from __future__ import annotations

import json
import subprocess
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from pydantic import ValidationError

import scripts.podcast_carousel_correction_job as correction_module
from agents.brook.podcast_carousel_panel import PanelResult, PanelReview, PanelSynthesis
from agents.brook.podcast_carousel_render import _content_sha
from scripts.podcast_carousel_correction_job import (
    CorrectionJobTransitionError,
    claim_job,
    complete_job,
    correction_job_path,
    create_queued_job,
    fail_job,
    load_job,
    progress_job,
)
from shared.schemas.podcast_carousel import (
    CarouselCopyEdit,
    CarouselCorrectionItem,
    CarouselReviewManifestV1,
    CarouselReviewPage,
    PageFitDiagnostic,
    PodcastCarouselCopySpecV1,
    receipt_for,
)

SHA = "a" * 64
NOW = datetime(2026, 8, 19, 1, 0, tzinfo=UTC)
REAL_TRUSTED_RERENDER_HASHES = correction_module._trusted_rerender_hashes


@pytest.fixture(autouse=True)
def _stub_deterministic_rerender(monkeypatch):
    def trusted_hashes(*, result_manifest, page_ids, **_kwargs):
        pages = {page.page_id: page.image.sha256 for page in result_manifest.pages}
        return {page_id: pages[page_id] for page_id in page_ids}

    monkeypatch.setattr(correction_module, "_trusted_rerender_hashes", trusted_hashes)


def _write_revision(
    package_root: Path,
    revision: str,
    *,
    make_current: bool,
    copy_updates: dict[str, dict[str, object]] | None = None,
) -> tuple[Path, CarouselReviewManifestV1]:
    cutouts_dir = package_root.parent / "packaging" / "cutouts"
    cutouts_dir.mkdir(parents=True, exist_ok=True)
    for cutout_name in ("guest.png", "host.png", "other-guest.png"):
        cutout_path = cutouts_dir / cutout_name
        if not cutout_path.exists():
            cutout_path.write_bytes(f"stable:{cutout_name}".encode())
    evidence = {
        "evidence_id": "ev-1",
        "source_path": "transcript.md",
        "source_sha256": "e" * 64,
        "speaker": "來賓",
        "text": "演算法會隱藏失敗。",
        "t0": 1,
        "t1": 2,
    }
    spec = PodcastCarouselCopySpecV1.model_validate(
        {
            "episode_id": "ep120",
            "revision": revision,
            "episode": {
                "number": 121,
                "topic": "演算法與失敗",
                "guest_name": "來賓",
                "guest_title": "研究者",
            },
            "pages": [
                {
                    "page_id": "cover",
                    "role": "cover",
                    "headline": "演算法隱藏失敗",
                    "emphasis": "隱藏失敗",
                    "guest_name": "來賓",
                    "guest_title": "研究者",
                    "cutout": "guest.png",
                    "evidence": [evidence],
                },
                {
                    "page_id": "hook",
                    "role": "hook",
                    "question": "為什麼只看到成功？",
                    "emphasis": "只看到成功",
                    "bridge": "答案藏在演算法裡。",
                    "evidence": [evidence],
                },
                {
                    "page_id": "point-algorithm",
                    "role": "point",
                    "headline": "演算法會隱藏失敗",
                    "emphasis": "隱藏失敗",
                    "body": "表現不好的內容會沉下去。",
                    "evidence": [evidence],
                },
                {
                    "page_id": "quote",
                    "role": "quote",
                    "variant": "A",
                    "text": "演算法會隱藏失敗。",
                    "emphasis": "隱藏失敗",
                    "guest_name": "來賓",
                    "guest_cutout": "guest.png",
                    "evidence": [evidence],
                },
                {
                    "page_id": "cta",
                    "role": "cta",
                    "episode_topic": "演算法與失敗",
                    "emphasis": "演算法",
                    "evidence": [evidence],
                },
            ],
            "publish_compatibility": "api_compatible",
        }
    )
    if copy_updates:
        payload = spec.model_dump(mode="json")
        for page in payload["pages"]:
            page.update(copy_updates.get(page["page_id"], {}))
        spec = PodcastCarouselCopySpecV1.model_validate(payload)
    revision_dir = package_root / "revisions" / revision
    pages_dir = revision_dir / "pages"
    pages_dir.mkdir(parents=True, exist_ok=True)
    copy_path = revision_dir / "copy_spec.v1.json"
    copy_path.write_text(spec.model_dump_json(indent=2), encoding="utf-8")
    render_input_path = revision_dir / "render_input.html"
    render_input_path.write_text(
        f"<html>canonical render input {revision}</html>", encoding="utf-8"
    )
    review_pages = []
    for index, page in enumerate(spec.pages, start=1):
        image_path = pages_dir / f"{index:02d}.png"
        image_path.write_bytes(f"{revision}:{page.page_id}:png".encode())
        review_pages.append(
            CarouselReviewPage(
                page_id=page.page_id,
                page_number=index,
                role=page.role,
                content_sha256=_content_sha(spec, index - 1, "f" * 64, cutouts_dir),
                image=receipt_for(image_path),
                fit=PageFitDiagnostic(status="fit"),
                copy_page=page,
            )
        )
    manifest = CarouselReviewManifestV1(
        episode_id=spec.episode_id,
        revision=revision,
        copy_spec=receipt_for(copy_path),
        render_input=receipt_for(render_input_path),
        template={
            "root": str((package_root / "template").resolve()),
            "sha256": "f" * 64,
        },
        publish_compatibility=spec.publish_compatibility,
        pages=review_pages,
    )
    manifest_path = revision_dir / "review_manifest.v1.json"
    manifest_path.write_text(manifest.model_dump_json(indent=2), encoding="utf-8")
    if make_current:
        (package_root / "current.json").write_text(
            json.dumps(
                {
                    "episode_id": spec.episode_id,
                    "revision": revision,
                    "manifest": str(manifest_path.resolve()),
                    "manifest_sha256": receipt_for(manifest_path).sha256,
                }
            ),
            encoding="utf-8",
        )
    return manifest_path, manifest


def _completion_fixture(
    package_root: Path,
    *,
    copy_updates: dict[str, dict[str, object]] | None = None,
):
    if copy_updates is None:
        copy_updates = {"cover": {"headline": "演算法隱藏失敗的新版本"}}
    manifest_path, _ = _write_revision(
        package_root,
        "r002",
        make_current=True,
        copy_updates=copy_updates,
    )
    panel_dir = package_root / "editorial" / "r002"
    reviews_dir = panel_dir / "reviews"
    reviews_dir.mkdir(parents=True, exist_ok=True)
    reviews = {}
    reviewer_artifacts = []
    for lens in ("ig_audience", "episode_editorial", "brand_evidence"):
        review = PanelReview(lens=lens, verdict="pass")
        review_path = reviews_dir / f"{lens}.json"
        review_path.write_text(review.model_dump_json(indent=2), encoding="utf-8")
        reviews[lens] = review
        reviewer_artifacts.append((lens, f"subagent-{lens}", review_path))
    panel = PanelResult(
        episode_id="ep120",
        revision="r002",
        status="converged",
        reviews=reviews,
        verified_findings=[],
        verification_rejections=[],
        synthesis=PanelSynthesis(
            accepted_finding_ids=[],
            revision_instructions=[],
        ),
    )
    panel_path = panel_dir / "panel_result.v1.json"
    panel_path.write_text(panel.model_dump_json(indent=2), encoding="utf-8")
    return {
        "result_manifest_path": manifest_path,
        "panel_result_path": panel_path,
        "reviewer_artifacts": reviewer_artifacts,
    }


def _queued(package_root: Path):
    manifest_path, manifest = _write_revision(package_root, "r001", make_current=True)
    return create_queued_job(
        package_root=package_root,
        episode_id="ep120",
        source_revision="r001",
        source_manifest_sha256=receipt_for(manifest_path).sha256,
        feedback_items=[
            CarouselCorrectionItem(
                page_id="cover",
                artifact_sha256=manifest.pages[0].image.sha256,
                feedback="放大來賓",
            )
        ],
        now=NOW,
        job_id="cj-" + "1" * 32,
    )


def _active_feedback_job(package_root: Path):
    queued = _queued(package_root)
    path = correction_job_path(package_root, queued.job_id)
    claim_job(
        path,
        executor="codex",
        executor_id="worker-integrity",
        claim_token="claim-integrity-1",
        now=NOW + timedelta(seconds=1),
    )
    progress_job(
        path,
        claim_token="claim-integrity-1",
        step="render_candidate",
        progress_percent=50,
        now=NOW + timedelta(seconds=2),
    )
    return path


def _active_structured_job(package_root: Path):
    manifest_path, manifest = _write_revision(package_root, "r001", make_current=True)
    queued = create_queued_job(
        package_root=package_root,
        episode_id="ep120",
        source_revision="r001",
        source_manifest_sha256=receipt_for(manifest_path).sha256,
        copy_edits=[
            CarouselCopyEdit(
                page_id="cover",
                role="cover",
                artifact_sha256=manifest.pages[0].image.sha256,
                fields={"headline": "隱藏失敗的新標題"},
            )
        ],
        now=NOW,
        job_id="cj-" + "3" * 32,
    )
    path = correction_job_path(package_root, queued.job_id)
    claim_job(
        path,
        executor="codex",
        executor_id="worker-structured-integrity",
        claim_token="claim-structured-integrity",
        now=NOW + timedelta(seconds=1),
    )
    progress_job(
        path,
        claim_token="claim-structured-integrity",
        step="render_candidate",
        progress_percent=50,
        now=NOW + timedelta(seconds=2),
    )
    return path


def test_state_machine_prevents_double_claim_and_invalid_transitions(tmp_path: Path):
    package = tmp_path / "ig-carousel"
    queued = _queued(package)
    path = correction_job_path(package, queued.job_id)

    with pytest.raises(CorrectionJobTransitionError, match="cannot complete"):
        complete_job(
            path,
            claim_token="claim-00000001",
            result_manifest_path=Path("missing-manifest"),
            panel_result_path=Path("missing-panel"),
            reviewer_artifacts=[],
            now=NOW,
        )

    claimed = claim_job(
        path,
        executor="codex",
        executor_id="worker-1",
        claim_token="claim-00000001",
        now=NOW + timedelta(seconds=1),
    )
    assert claimed.status == "claimed"
    with pytest.raises(CorrectionJobTransitionError, match="cannot claim"):
        claim_job(
            path,
            executor="claude_code",
            executor_id="worker-2",
            claim_token="claim-00000002",
            now=NOW + timedelta(seconds=2),
        )

    active = progress_job(
        path,
        claim_token="claim-00000001",
        step="rewrite_copy",
        progress_percent=40,
        message="修正文案",
        now=NOW + timedelta(seconds=3),
    )
    assert active.status == "in_progress"
    assert active.progress[0].sequence == 1
    with pytest.raises(CorrectionJobTransitionError, match="cannot decrease"):
        progress_job(
            path,
            claim_token="claim-00000001",
            step="render",
            progress_percent=20,
            now=NOW + timedelta(seconds=4),
        )

    for index, review in enumerate(active.required_reviews, start=1):
        progress_job(
            path,
            claim_token="claim-00000001",
            step=f"review:{review}",
            progress_percent=40 + index * 15,
            now=NOW + timedelta(seconds=4 + index),
        )

    completion = _completion_fixture(
        package,
        copy_updates={"cover": {"headline": "隱藏失敗的新標題"}},
    )
    completed = complete_job(
        path,
        claim_token="claim-00000001",
        **completion,
        now=NOW + timedelta(seconds=8),
    )
    assert completed.status == "completed"
    assert completed.result_revision == "r002"
    assert completed.completion_evidence is not None
    assert {item.lens for item in completed.completion_evidence.reviewers} == set(
        completed.required_reviews
    )
    with pytest.raises(CorrectionJobTransitionError, match="cannot record progress"):
        progress_job(
            path,
            claim_token="claim-00000001",
            step="late_write",
            progress_percent=100,
        )
    assert not list(path.parent.glob("*.lock"))
    assert not list(path.parent.glob("*.tmp"))


def test_expired_claim_can_be_reclaimed_and_invalidates_old_token(tmp_path: Path):
    package = tmp_path / "ig-carousel"
    queued = _queued(package)
    path = correction_job_path(package, queued.job_id)

    first = claim_job(
        path,
        executor="codex",
        executor_id="worker-1",
        claim_token="claim-lease-0001",
        lease_seconds=60,
        now=NOW,
    )
    assert first.claim is not None
    assert first.claim.lease_expires_at == NOW + timedelta(seconds=60)

    with pytest.raises(CorrectionJobTransitionError, match="lease is still active"):
        claim_job(
            path,
            executor="claude_code",
            executor_id="worker-2",
            claim_token="claim-lease-0002",
            now=NOW + timedelta(seconds=59),
        )

    reclaimed = claim_job(
        path,
        executor="claude_code",
        executor_id="worker-2",
        claim_token="claim-lease-0002",
        lease_seconds=120,
        now=NOW + timedelta(seconds=60),
    )
    assert reclaimed.status == "claimed"
    assert reclaimed.claim is not None
    assert reclaimed.claim.executor_id == "worker-2"
    assert reclaimed.claim.lease_expires_at == NOW + timedelta(seconds=180)

    with pytest.raises(CorrectionJobTransitionError, match="claim token mismatch"):
        progress_job(
            path,
            claim_token="claim-lease-0001",
            step="stale_worker",
            progress_percent=10,
            now=NOW + timedelta(seconds=61),
        )


def test_progress_renews_lease_and_expired_worker_cannot_mutate(tmp_path: Path):
    package = tmp_path / "ig-carousel"
    queued = _queued(package)
    path = correction_job_path(package, queued.job_id)
    claim_job(
        path,
        executor="codex",
        executor_id="worker-1",
        claim_token="claim-renew-0001",
        lease_seconds=60,
        now=NOW,
    )

    active = progress_job(
        path,
        claim_token="claim-renew-0001",
        step="rewrite",
        progress_percent=25,
        now=NOW + timedelta(seconds=30),
    )
    assert active.claim is not None
    assert active.claim.lease_expires_at == NOW + timedelta(seconds=90)

    with pytest.raises(CorrectionJobTransitionError, match="lease has expired"):
        progress_job(
            path,
            claim_token="claim-renew-0001",
            step="late_heartbeat",
            progress_percent=50,
            now=NOW + timedelta(seconds=90),
        )

    with pytest.raises(CorrectionJobTransitionError, match="lease has expired"):
        complete_job(
            path,
            claim_token="claim-renew-0001",
            result_manifest_path=Path("missing-manifest"),
            panel_result_path=Path("missing-panel"),
            reviewer_artifacts=[],
            now=NOW + timedelta(seconds=90),
        )

    reclaimed = claim_job(
        path,
        executor="claude_code",
        executor_id="worker-2",
        claim_token="claim-renew-0002",
        lease_seconds=60,
        now=NOW + timedelta(seconds=90),
    )
    assert reclaimed.status == "in_progress"
    assert len(reclaimed.progress) == 1
    assert reclaimed.claim is not None
    assert reclaimed.claim.claim_token == "claim-renew-0002"
    with pytest.raises(CorrectionJobTransitionError, match="claim token mismatch"):
        fail_job(
            path,
            claim_token="claim-renew-0001",
            error="stale worker",
            now=NOW + timedelta(seconds=91),
        )


def test_executor_metadata_is_platform_neutral_and_fail_closed(tmp_path: Path):
    package = tmp_path / "ig-carousel"
    queued = _queued(package)
    path = correction_job_path(package, queued.job_id)

    with pytest.raises(ValidationError, match="executor"):
        claim_job(
            path,
            executor="anthropic_api",
            executor_id="provider-specific",
            claim_token="claim-00000003",
            now=NOW + timedelta(seconds=1),
        )
    assert load_job(path).status == "queued"

    claim_job(
        path,
        executor="claude_code",
        executor_id="desktop-worker",
        claim_token="claim-00000004",
        now=NOW + timedelta(seconds=2),
    )
    failed = fail_job(
        path,
        claim_token="claim-00000004",
        error="transcript evidence mismatch",
        now=NOW + timedelta(seconds=3),
    )
    assert failed.status == "failed"
    assert failed.error == "transcript evidence mismatch"
    with pytest.raises(CorrectionJobTransitionError, match="cannot fail"):
        fail_job(path, claim_token="claim-00000004", error="again")


def test_cli_claim_progress_rejects_unattested_fake_completion(tmp_path: Path):
    package = tmp_path / "ig-carousel"
    queued = _queued(package)
    path = correction_job_path(package, queued.job_id)
    script = Path("scripts/podcast_carousel_correction_job.py").resolve()

    commands = [
        [
            "claim",
            str(path),
            "--executor",
            "codex",
            "--executor-id",
            "afk-worker",
            "--claim-token",
            "claim-cli-0001",
        ],
        [
            "progress",
            str(path),
            "--claim-token",
            "claim-cli-0001",
            "--step",
            "copy_and_render",
            "--percent",
            "100",
        ],
        *[
            [
                "progress",
                str(path),
                "--claim-token",
                "claim-cli-0001",
                "--step",
                f"review:{review}",
                "--percent",
                "100",
            ]
            for review in queued.required_reviews
        ],
    ]
    for command in commands:
        completed = subprocess.run(
            [sys.executable, str(script), *command],
            cwd=Path(__file__).resolve().parents[2],
            capture_output=True,
            text=True,
            encoding="utf-8",
            check=False,
        )
        assert completed.returncode == 0, completed.stderr
        json.loads(completed.stdout)

    completion = _completion_fixture(package)
    complete_command = [
        "complete",
        str(path),
        "--claim-token",
        "claim-cli-0001",
        "--result-manifest",
        str(completion["result_manifest_path"]),
        "--panel-result",
        str(completion["panel_result_path"]),
    ]
    for lens, reviewer_id, review_path in completion["reviewer_artifacts"]:
        complete_command.extend(["--reviewer-receipt", f"{lens}={reviewer_id}={review_path}"])
    completed = subprocess.run(
        [sys.executable, str(script), *complete_command],
        cwd=Path(__file__).resolve().parents[2],
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=False,
    )
    assert completed.returncode != 0
    assert "deterministic carousel rerender attestation failed" in completed.stderr

    stored = load_job(path)
    assert stored.status == "in_progress"
    assert stored.claim is not None
    assert stored.claim.executor == "codex"
    assert stored.result_revision is None


def test_progress_review_strings_cannot_replace_panel_receipts(tmp_path: Path):
    package = tmp_path / "ig-carousel"
    manifest_path, manifest = _write_revision(package, "r001", make_current=True)
    queued = create_queued_job(
        package_root=package,
        episode_id="ep120",
        source_revision="r001",
        source_manifest_sha256=receipt_for(manifest_path).sha256,
        copy_edits=[
            CarouselCopyEdit(
                page_id="cover",
                role="cover",
                artifact_sha256=manifest.pages[0].image.sha256,
                fields={"headline": "隱藏失敗的新標題"},
            )
        ],
        now=NOW,
        job_id="cj-" + "2" * 32,
    )
    path = correction_job_path(package, queued.job_id)
    claim_job(
        path,
        executor="claude_code",
        executor_id="worker-structured",
        claim_token="claim-structured-1",
        now=NOW + timedelta(seconds=1),
    )
    progress_job(
        path,
        claim_token="claim-structured-1",
        step="apply_structured_edits",
        progress_percent=30,
        now=NOW + timedelta(seconds=2),
    )
    completion = _completion_fixture(
        package,
        copy_updates={"cover": {"headline": "隱藏失敗的新標題"}},
    )
    with pytest.raises(CorrectionJobTransitionError, match="exactly one artifact"):
        complete_job(
            path,
            claim_token="claim-structured-1",
            result_manifest_path=completion["result_manifest_path"],
            panel_result_path=completion["panel_result_path"],
            reviewer_artifacts=[],
            now=NOW + timedelta(seconds=3),
        )
    for index, review in enumerate(queued.required_reviews, start=1):
        progress_job(
            path,
            claim_token="claim-structured-1",
            step=f"review:{review}",
            progress_percent=30 + index * 20,
            now=NOW + timedelta(seconds=3 + index),
        )
    completed = complete_job(
        path,
        claim_token="claim-structured-1",
        **completion,
        now=NOW + timedelta(seconds=7),
    )
    assert completed.status == "completed"


def test_claim_fails_when_source_is_not_current_or_page_receipt_drifted(tmp_path: Path):
    stale_package = tmp_path / "stale" / "ig-carousel"
    stale = _queued(stale_package)
    stale_path = correction_job_path(stale_package, stale.job_id)
    _write_revision(stale_package, "r002", make_current=True)
    with pytest.raises(CorrectionJobTransitionError, match="no longer the current"):
        claim_job(
            stale_path,
            executor="codex",
            executor_id="worker-stale",
            claim_token="claim-source-stale",
            now=NOW + timedelta(seconds=1),
        )

    drift_package = tmp_path / "drift" / "ig-carousel"
    drift = _queued(drift_package)
    drift_path = correction_job_path(drift_package, drift.job_id)
    source_manifest = CarouselReviewManifestV1.model_validate_json(
        (drift_package / "revisions/r001/review_manifest.v1.json").read_text(encoding="utf-8")
    )
    Path(source_manifest.pages[0].image.path).write_bytes(b"tampered page")
    with pytest.raises(CorrectionJobTransitionError, match="page artifact receipt changed"):
        claim_job(
            drift_path,
            executor="codex",
            executor_id="worker-drift",
            claim_token="claim-source-drift",
            now=NOW + timedelta(seconds=1),
        )


def test_completion_rejects_missing_duplicate_or_nonconverged_panel_receipts(tmp_path: Path):
    package = tmp_path / "ig-carousel"
    path = _active_feedback_job(package)
    completion = _completion_fixture(package)
    artifacts = completion["reviewer_artifacts"]

    with pytest.raises(CorrectionJobTransitionError, match="exactly one artifact"):
        complete_job(
            path,
            claim_token="claim-integrity-1",
            result_manifest_path=completion["result_manifest_path"],
            panel_result_path=completion["panel_result_path"],
            reviewer_artifacts=artifacts[:2],
            now=NOW + timedelta(seconds=3),
        )
    with pytest.raises(CorrectionJobTransitionError, match="exactly one artifact"):
        complete_job(
            path,
            claim_token="claim-integrity-1",
            result_manifest_path=completion["result_manifest_path"],
            panel_result_path=completion["panel_result_path"],
            reviewer_artifacts=[artifacts[0], artifacts[0], artifacts[2]],
            now=NOW + timedelta(seconds=3),
        )
    duplicate_identity = [
        (lens, "same-subagent", review_path) for lens, _, review_path in artifacts
    ]
    with pytest.raises(CorrectionJobTransitionError, match="identities must be unique"):
        complete_job(
            path,
            claim_token="claim-integrity-1",
            result_manifest_path=completion["result_manifest_path"],
            panel_result_path=completion["panel_result_path"],
            reviewer_artifacts=duplicate_identity,
            now=NOW + timedelta(seconds=3),
        )

    reviews = {
        lens: PanelReview.model_validate_json(review_path.read_text(encoding="utf-8"))
        for lens, _, review_path in artifacts
    }
    blocked = PanelResult(
        episode_id="ep120",
        revision="r002",
        status="blocked",
        reviews=reviews,
        verified_findings=[],
        verification_rejections=[],
        synthesis=PanelSynthesis(
            accepted_finding_ids=[],
            revision_instructions=[],
            blockers=["需要人工確認"],
        ),
    )
    completion["panel_result_path"].write_text(blocked.model_dump_json(), encoding="utf-8")
    with pytest.raises(CorrectionJobTransitionError, match="not validly converged"):
        complete_job(
            path,
            claim_token="claim-integrity-1",
            **completion,
            now=NOW + timedelta(seconds=3),
        )


def test_completion_rejects_panel_that_omits_reviewer_findings_from_verification(
    tmp_path: Path,
):
    package = tmp_path / "ig-carousel"
    path = _active_feedback_job(package)
    completion = _completion_fixture(package)
    reviews = {}
    for lens, _, review_path in completion["reviewer_artifacts"]:
        review = {
            "lens": lens,
            "verdict": "revise",
            "findings": [
                {
                    "finding_id": f"{lens}-01",
                    "severity": "medium",
                    "page_id": None,
                    "claim": "reviewer found an editorial issue",
                    "page_copy_quote": None,
                    "evidence_ids": ["ev-1"],
                    "suggested_change": "revise the unsupported copy",
                }
            ],
        }
        review_path.write_text(json.dumps(review), encoding="utf-8")
        reviews[lens] = review
    forged_panel = {
        "episode_id": "ep120",
        "revision": "r002",
        "status": "converged",
        "reviews": reviews,
        "verified_findings": [],
        "verification_rejections": [],
        "synthesis": {
            "accepted_finding_ids": [],
            "rejected": [],
            "revision_instructions": [],
            "blockers": [],
        },
    }
    completion["panel_result_path"].write_text(json.dumps(forged_panel), encoding="utf-8")

    with pytest.raises(CorrectionJobTransitionError, match="not validly converged"):
        complete_job(
            path,
            claim_token="claim-integrity-1",
            **completion,
            now=NOW + timedelta(seconds=3),
        )


@pytest.mark.parametrize("tamper", ["manifest", "page"])
def test_completion_rejects_missing_or_tampered_result_artifacts(tmp_path: Path, tamper: str):
    package = tmp_path / tamper / "ig-carousel"
    path = _active_feedback_job(package)
    completion = _completion_fixture(package)
    if tamper == "manifest":
        completion["result_manifest_path"].write_text("{}", encoding="utf-8")
    else:
        manifest = CarouselReviewManifestV1.model_validate_json(
            completion["result_manifest_path"].read_text(encoding="utf-8")
        )
        Path(manifest.pages[0].image.path).write_bytes(b"tampered result page")
    with pytest.raises(CorrectionJobTransitionError, match="changed"):
        complete_job(
            path,
            claim_token="claim-integrity-1",
            **completion,
            now=NOW + timedelta(seconds=3),
        )

    missing_package = tmp_path / f"missing-{tamper}" / "ig-carousel"
    missing_path = _active_feedback_job(missing_package)
    valid = _completion_fixture(missing_package)
    with pytest.raises(CorrectionJobTransitionError, match="missing or outside"):
        complete_job(
            missing_path,
            claim_token="claim-integrity-1",
            result_manifest_path=missing_package / "revisions/r999/missing.json",
            panel_result_path=valid["panel_result_path"],
            reviewer_artifacts=valid["reviewer_artifacts"],
            now=NOW + timedelta(seconds=3),
        )


@pytest.mark.parametrize(
    "extra_change",
    [
        {},
        {"cutout": "other-guest.png"},
        {"guest_title": "未授權的新職稱"},
        {
            "evidence": [
                {
                    "evidence_id": "ev-2",
                    "source_path": "transcript.md",
                    "source_sha256": "e" * 64,
                    "speaker": "來賓",
                    "text": "被偷換的證據。",
                    "t0": 2,
                    "t1": 3,
                    "contiguous": True,
                }
            ]
        },
    ],
)
def test_structured_completion_requires_exact_requested_copy_changes(
    tmp_path: Path,
    extra_change: dict[str, object],
):
    package = tmp_path / "ig-carousel"
    path = _active_structured_job(package)
    update = dict(extra_change)
    if extra_change:
        update["headline"] = "隱藏失敗的新標題"
    completion = _completion_fixture(
        package,
        copy_updates={"cover": update} if update else {},
    )
    with pytest.raises(CorrectionJobTransitionError, match="exactly apply"):
        complete_job(
            path,
            claim_token="claim-structured-integrity",
            **completion,
            now=NOW + timedelta(seconds=3),
        )


def test_structured_completion_recomputes_canonical_page_content_hash(tmp_path: Path):
    package = tmp_path / "ig-carousel"
    path = _active_structured_job(package)
    completion = _completion_fixture(
        package,
        copy_updates={"cover": {"headline": "隱藏失敗的新標題"}},
    )
    manifest_path = completion["result_manifest_path"]
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["pages"][0]["content_sha256"] = "0" * 64
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    current_path = package / "current.json"
    current = json.loads(current_path.read_text(encoding="utf-8"))
    current["manifest_sha256"] = receipt_for(manifest_path).sha256
    current_path.write_text(json.dumps(current), encoding="utf-8")

    with pytest.raises(CorrectionJobTransitionError, match="canonical content hash"):
        complete_job(
            path,
            claim_token="claim-structured-integrity",
            **completion,
            now=NOW + timedelta(seconds=3),
        )


def test_structured_completion_rejects_png_not_matching_deterministic_rerender(
    tmp_path: Path, monkeypatch
):
    package = tmp_path / "ig-carousel"
    path = _active_structured_job(package)
    completion = _completion_fixture(
        package,
        copy_updates={"cover": {"headline": "隱藏失敗的新標題"}},
    )
    manifest_path = completion["result_manifest_path"]
    manifest = CarouselReviewManifestV1.model_validate_json(
        manifest_path.read_text(encoding="utf-8")
    )
    expected_cover_sha = manifest.pages[0].image.sha256
    cover_path = Path(manifest.pages[0].image.path)
    cover_path.write_bytes(b"arbitrary changed PNG")
    payload = manifest.model_dump(mode="json")
    payload["pages"][0]["image"] = receipt_for(cover_path).model_dump(mode="json")
    manifest_path.write_text(json.dumps(payload), encoding="utf-8")
    current_path = package / "current.json"
    current = json.loads(current_path.read_text(encoding="utf-8"))
    current["manifest_sha256"] = receipt_for(manifest_path).sha256
    current_path.write_text(json.dumps(current), encoding="utf-8")
    monkeypatch.setattr(
        correction_module,
        "_trusted_rerender_hashes",
        lambda **_kwargs: {"cover": expected_cover_sha},
    )

    with pytest.raises(CorrectionJobTransitionError, match="deterministic rerender"):
        complete_job(
            path,
            claim_token="claim-structured-integrity",
            **completion,
            now=NOW + timedelta(seconds=3),
        )


def test_structured_completion_rejects_replaced_self_consistent_template_tree(
    tmp_path: Path,
):
    package = tmp_path / "ig-carousel"
    path = _active_structured_job(package)
    requested_headline = load_job(path).copy_edits[0].fields["headline"]
    completion = _completion_fixture(
        package,
        copy_updates={"cover": {"headline": requested_headline}},
    )
    manifest_path = completion["result_manifest_path"]
    manifest = CarouselReviewManifestV1.model_validate_json(
        manifest_path.read_text(encoding="utf-8")
    )
    spec = PodcastCarouselCopySpecV1.model_validate_json(
        Path(manifest.copy_spec.path).read_text(encoding="utf-8")
    )

    replacement_root = package / "templates" / "executor-replacement"
    replacement_root.mkdir(parents=True)
    replacement_template = replacement_root / "PodcastCarouselRender.html"
    replacement_template.write_text("executor-controlled template", encoding="utf-8")
    replacement_sha = correction_module._digest_files(
        [(replacement_template.name, replacement_template)]
    )
    render_input_path = Path(manifest.render_input.path)
    render_input_path.write_text(
        "executor-controlled render rebuilt from replacement template", encoding="utf-8"
    )
    cover_path = Path(manifest.pages[0].image.path)
    cover_path.write_bytes(b"executor-controlled deterministic replacement PNG")

    payload = manifest.model_dump(mode="json")
    payload["template"]["root"] = str(replacement_root.resolve())
    payload["template"]["sha256"] = replacement_sha
    payload["render_input"] = receipt_for(render_input_path).model_dump(mode="json")
    payload["pages"][0]["image"] = receipt_for(cover_path).model_dump(mode="json")
    payload["pages"][0]["content_sha256"] = _content_sha(
        spec,
        0,
        replacement_sha,
        package.parent / "packaging" / "cutouts",
    )
    manifest_path.write_text(json.dumps(payload), encoding="utf-8")
    current_path = package / "current.json"
    current = json.loads(current_path.read_text(encoding="utf-8"))
    current["manifest_sha256"] = receipt_for(manifest_path).sha256
    current_path.write_text(json.dumps(current), encoding="utf-8")

    with pytest.raises(CorrectionJobTransitionError, match="template identity"):
        complete_job(
            path,
            claim_token="claim-structured-integrity",
            **completion,
            now=NOW + timedelta(seconds=3),
        )


def test_trusted_rerender_rejects_self_reported_template_digest(tmp_path: Path):
    package = tmp_path / "ig-carousel"
    completion = _completion_fixture(package)
    template_root = package / "template"
    template_root.mkdir(parents=True)
    (template_root / "untrusted.html").write_text("forged", encoding="utf-8")
    manifest = CarouselReviewManifestV1.model_validate_json(
        completion["result_manifest_path"].read_text(encoding="utf-8")
    )
    spec = PodcastCarouselCopySpecV1.model_validate_json(
        Path(manifest.copy_spec.path).read_text(encoding="utf-8")
    )

    with pytest.raises(CorrectionJobTransitionError, match="template snapshot tree changed"):
        REAL_TRUSTED_RERENDER_HASHES(
            result_spec=spec,
            source_template=manifest.template,
            result_manifest=manifest,
            package_root=package,
            page_ids={"cover"},
        )


def test_quote_geometry_only_job_still_runs_the_exact_diff(tmp_path):
    """守衛漏掉 `quote_layout_overrides` 時，只調金句幾何的單會整個跳過比對。

    後果不是「少驗一項」：exact diff 是「沿用 panel、不重跑三個 lens」的唯一
    授權依據。跳過它，結果 spec 就能夾帶任何一張卡的文案改動而完成
    （2026-09-03 review 抓到）。
    """
    import inspect as _inspect

    from scripts import podcast_carousel_correction_job as mod

    source = _inspect.getsource(mod._assert_structured_edits_applied)
    guard = source[: source.index("expected = ")]
    assert "quote_layout_overrides" in guard, "早退守衛必須涵蓋金句幾何"
    # 四種結構化編輯任一存在就要往下走
    for field in (
        "copy_edits",
        "layout_overrides",
        "quote_layout_overrides",
        "text_layout_overrides",
    ):
        assert field in guard


def test_feedback_driven_job_cannot_inherit_the_panel():
    """自由文字的修改意見是 agent 照意圖重寫文案——那正是三個 lens 存在的理由。

    讓它也能宣告沿用，等於自己簽自己的審查。
    """
    import inspect as _inspect

    from scripts import podcast_carousel_correction_job as mod

    source = _inspect.getsource(mod._verify_completion_evidence)
    branch = source[source.index("inherits_panel = ") :]
    assert "job.feedback_items" in branch
    assert "cannot inherit the source panel" in branch
    # 檢查必須在「沿用」真的成立之前
    assert branch.index("job.feedback_items") < branch.index("_verify_inherited_panel_completion")


def test_expired_lease_no_longer_blocks_new_submissions(tmp_path, monkeypatch):
    """認領後行程死掉／租約過期時，工作會永遠停在 claimed。

    `fail_job` 自己也要驗租約，所以連標記失敗都做不到——那個 revision 從此
    送不出任何新修改，而 Review Gate 上沒有任何控制項能解開
    （2026-09-03 review 抓到）。租約過期的認領本來就允許被接手，這裡同判準。
    """
    import inspect as _inspect

    from scripts import podcast_carousel_correction_job as mod

    source = _inspect.getsource(mod.create_queued_job)
    active = source[source.index("active = [") :][:600]
    assert "lease_expires_at" in active, "進行中要看租約還在不在，不能只看 status"
    assert 'existing.status == "queued"' in active
