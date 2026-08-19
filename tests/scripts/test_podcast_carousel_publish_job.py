from __future__ import annotations

import hashlib
import json
import subprocess
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from pydantic import ValidationError

from scripts.podcast_carousel_publish_job import (
    PublishJobTransitionError,
    checkpoint_publish_target,
    claim_publish_job,
    complete_publish_job,
    create_or_get_publish_job,
    fail_publish_job,
    list_publish_jobs,
    load_publish_job,
    progress_publish_job,
    publish_job_path,
    published_publish_platforms,
    republish_required_platforms,
    retire_unsafe_legacy_publish_job,
    start_publish_target,
    supersede_queued_publish_job,
    unfinished_publish_platforms,
)
from shared.schemas.carousel_publish import (
    CarouselPublishAsset,
    CarouselPublishPlatformResult,
    CarouselPublishTarget,
)
from shared.schemas.podcast_carousel import receipt_for

NOW = datetime(2020, 8, 19, 3, 0, tzinfo=UTC)
MANIFEST_BYTES = b'{"revision":"r016"}\n'
SHA = hashlib.sha256(MANIFEST_BYTES).hexdigest()


def _legacy_fingerprint(caption: str, platforms: list[str]) -> str:
    payload = {
        "source_revision": "r016",
        "source_manifest_sha256": SHA,
        "caption": caption.strip(),
        "platforms": sorted(platforms),
    }
    encoded = json.dumps(payload, ensure_ascii=False, separators=(",", ":"), sort_keys=True)
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def _asset(package_root: Path) -> CarouselPublishAsset:
    image_path = package_root / "revisions" / "r016" / "pages" / "01.png"
    image_path.parent.mkdir(parents=True, exist_ok=True)
    if not image_path.exists():
        image_path.write_bytes(b"reviewed-carousel-image")
    return CarouselPublishAsset(
        page_id="cover",
        page_number=1,
        image=receipt_for(image_path),
    )


def _write_approval_audit(package_root: Path) -> None:
    path = package_root / "review_feedback.v1.json"
    if path.exists():
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(
            {
                "episode_id": "ep120",
                "revisions": [
                    {
                        "revision_number": 3,
                        "created_at": (NOW - timedelta(minutes=1)).isoformat(),
                        "carousel_revision": "r016",
                        "manifest_sha256": SHA,
                        "decision": "approved",
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    manifest_path = package_root / "revisions" / "r016" / "review_manifest.v1.json"
    manifest_path.parent.mkdir(parents=True, exist_ok=True)
    manifest_path.write_bytes(MANIFEST_BYTES)
    (package_root / "current.json").write_text(
        json.dumps(
            {
                "revision": "r016",
                "manifest": str(manifest_path.resolve()),
                "manifest_sha256": SHA,
            }
        ),
        encoding="utf-8",
    )


def _browser_target(platform: str = "instagram") -> CarouselPublishTarget:
    if platform == "youtube_community":
        return CarouselPublishTarget(
            platform=platform,
            strategy="agent_browser_manual",
            configuration_state="manual_only",
            required_executor_capabilities=["browser_session"],
            note="No supported Community publish API.",
        )
    return CarouselPublishTarget(
        platform=platform,
        strategy="agent_browser",
        configuration_state="agent_browser_required",
        required_executor_capabilities=["browser_session"],
        note="Use an authenticated agent browser.",
    )


def _meta_target() -> CarouselPublishTarget:
    return CarouselPublishTarget(
        platform="instagram",
        strategy="meta_api",
        configuration_state="configured",
        required_executor_capabilities=["meta_api"],
        note="Meta transport configured.",
    )


def _queued(package_root: Path, *, suffix: str = "1", targets=None):
    _write_approval_audit(package_root)
    job, created = create_or_get_publish_job(
        package_root=package_root,
        episode_id="ep120",
        source_revision="r016",
        source_manifest_sha256=SHA,
        source_publish_compatibility="api_compatible",
        approval_revision_number=3,
        approved_at=NOW - timedelta(minutes=1),
        caption=f"Approved caption {suffix}",
        assets=[_asset(package_root)],
        targets=targets or [_browser_target()],
        now=NOW,
        job_id="pj-" + suffix * 32,
    )
    assert created is True
    return job


def _bound_result(path: Path, result: CarouselPublishPlatformResult):
    state = next(
        state for state in load_publish_job(path).target_states if state.platform == result.platform
    )
    return result.model_copy(
        update={
            "idempotency_key": state.idempotency_key,
            "attempt_id": state.attempt_id,
        }
    )


def test_claim_uses_materialized_release_bundle_after_episode_assets_change(tmp_path: Path):
    job = _queued(tmp_path)
    source_asset = tmp_path / "revisions" / "r016" / "pages" / "01.png"
    source_asset.write_bytes(b"changed-after-job-creation")

    claimed = claim_publish_job(
        publish_job_path(tmp_path, job.job_id),
        executor="codex",
        executor_id="worker-1",
        executor_capabilities=["browser_session"],
        claim_token="claim-token-1234",
        now=NOW,
    )

    assert claimed.status == "claimed"
    assert Path(claimed.assets[0].image.path).read_bytes() == b"reviewed-carousel-image"
    assert Path(claimed.assets[0].image.path) != source_asset


def test_create_rejects_current_manifest_bytes_that_do_not_match_source_hash(tmp_path: Path):
    _write_approval_audit(tmp_path)
    manifest_path = tmp_path / "revisions" / "r016" / "review_manifest.v1.json"
    manifest_path.write_bytes(b'{"revision":"r016","mutated":true}\n')

    with pytest.raises(PublishJobTransitionError, match="source manifest.*hash"):
        create_or_get_publish_job(
            package_root=tmp_path,
            episode_id="ep120",
            source_revision="r016",
            source_manifest_sha256=SHA,
            source_publish_compatibility="api_compatible",
            approval_revision_number=3,
            approved_at=NOW - timedelta(minutes=1),
            caption="Approved caption",
            assets=[_asset(tmp_path)],
            targets=[_browser_target()],
            now=NOW,
            job_id="pj-" + "9" * 32,
        )


def test_claim_rejects_job_after_current_release_advances(tmp_path: Path):
    job = _queued(tmp_path)
    current_path = tmp_path / "current.json"
    current = json.loads(current_path.read_text(encoding="utf-8"))
    current.update({"revision": "r017", "manifest_sha256": "b" * 64})
    current_path.write_text(json.dumps(current), encoding="utf-8")

    with pytest.raises(PublishJobTransitionError, match="no longer matches"):
        claim_publish_job(
            publish_job_path(tmp_path, job.job_id),
            executor="codex",
            executor_id="worker-1",
            executor_capabilities=["browser_session"],
            claim_token="claim-token-1234",
            now=NOW,
        )


def test_checkpoint_rejects_receipt_from_unrelated_attempt(tmp_path: Path):
    job = _queued(tmp_path)
    path = publish_job_path(tmp_path, job.job_id)
    claim_publish_job(
        path,
        executor="codex",
        executor_id="worker-1",
        executor_capabilities=["browser_session"],
        claim_token="claim-token-1234",
        now=NOW,
    )
    started = start_publish_target(
        path,
        claim_token="claim-token-1234",
        platform="instagram",
        now=NOW + timedelta(seconds=1),
    )
    state = started.target_states[0]
    unrelated = CarouselPublishPlatformResult(
        platform="instagram",
        strategy="agent_browser",
        status="published",
        receipt_id="external-post-123",
        idempotency_key="f" * 64,
        attempt_id="pa-" + "f" * 32,
        completed_at=NOW + timedelta(seconds=2),
    )

    with pytest.raises(PublishJobTransitionError, match="attempt binding"):
        checkpoint_publish_target(
            path,
            claim_token="claim-token-1234",
            result=unrelated,
            now=NOW + timedelta(seconds=2),
        )
    assert state.attempt_id is not None


def test_same_request_after_supersession_creates_queued_lineage(tmp_path: Path):
    first = _queued(tmp_path)
    supersede_queued_publish_job(
        publish_job_path(tmp_path, first.job_id),
        reason="release re-approved after correction",
        now=NOW + timedelta(seconds=1),
    )

    replacement, created = create_or_get_publish_job(
        package_root=tmp_path,
        episode_id="ep120",
        source_revision="r016",
        source_manifest_sha256=SHA,
        source_publish_compatibility="api_compatible",
        approval_revision_number=3,
        approved_at=NOW - timedelta(minutes=1),
        caption="Approved caption 1",
        assets=[_asset(tmp_path)],
        targets=[_browser_target()],
        now=NOW + timedelta(seconds=2),
        job_id="pj-" + "2" * 32,
    )

    assert created is True
    assert replacement.status == "queued"
    assert replacement.retry_of_job_id == first.job_id


def test_fail_rejects_uncertain_in_progress_target(tmp_path: Path):
    job = _queued(tmp_path)
    path = publish_job_path(tmp_path, job.job_id)
    claim_publish_job(
        path,
        executor="codex",
        executor_id="worker-1",
        executor_capabilities=["browser_session"],
        claim_token="claim-token-1234",
        now=NOW,
    )
    start_publish_target(
        path,
        claim_token="claim-token-1234",
        platform="instagram",
        now=NOW + timedelta(seconds=1),
    )

    with pytest.raises(PublishJobTransitionError, match="reconcile"):
        fail_publish_job(
            path,
            claim_token="claim-token-1234",
            error="browser response was ambiguous",
            now=NOW + timedelta(seconds=2),
        )


def _write_legacy_job(package_root: Path, *, status: str, suffix: str) -> Path:
    _write_approval_audit(package_root)
    caption = f"Legacy caption {suffix}"
    asset = _asset(package_root)
    created_at = NOW.isoformat()
    payload = {
        "schema_name": "nakama.podcast_carousel_publish_job.v1",
        "job_id": "pj-" + suffix * 32,
        "episode_id": "ep120",
        "source_revision": "r016",
        "source_manifest_sha256": SHA,
        "approval_revision_number": 3,
        "approved_at": (NOW - timedelta(minutes=1)).isoformat(),
        "request_fingerprint": _legacy_fingerprint(caption, ["instagram"]),
        "caption": caption,
        "assets": [
            {
                "page_id": "cover",
                "page_number": 1,
                "image": {
                    "path": asset.image.path,
                    "bytes": asset.image.bytes,
                    "sha256": asset.image.sha256,
                },
            }
        ],
        "targets": [
            {
                "platform": "instagram",
                "strategy": "agent_browser",
                "configuration_state": "agent_browser_required",
                "required_executor_capabilities": ["browser_session"],
                "note": "Legacy browser target.",
            }
        ],
        "status": status,
        "created_at": created_at,
        "updated_at": created_at,
        "claim": None,
        "progress": [],
        "results": [],
        "error": None,
    }
    if status == "failed":
        payload["claim"] = {
            "executor": "codex",
            "executor_id": "legacy-worker",
            "executor_capabilities": ["browser_session"],
            "claim_token": "claim-legacy-0001",
            "claimed_at": created_at,
            "lease_seconds": 1800,
            "lease_expires_at": (NOW + timedelta(minutes=30)).isoformat(),
        }
        payload["error"] = "legacy browser failed"
    path = publish_job_path(package_root, payload["job_id"])
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload), encoding="utf-8")
    return path


def _rewrite_legacy_as_manual_meta(path: Path, *, status: str = "queued") -> None:
    payload = json.loads(path.read_text(encoding="utf-8"))
    first_asset = payload["assets"][0]
    payload["assets"] = [
        {
            **first_asset,
            "page_id": f"page-{page_number}",
            "page_number": page_number,
        }
        for page_number in range(1, 12)
    ]
    payload["targets"][0].update(
        {
            "strategy": "meta_api",
            "configuration_state": "configured",
            "required_executor_capabilities": ["meta_api"],
            "note": "Legacy Meta target.",
        }
    )
    if status == "in_progress":
        payload.update(
            {
                "status": "in_progress",
                "updated_at": (NOW + timedelta(seconds=1)).isoformat(),
                "claim": {
                    "executor": "codex",
                    "executor_id": "legacy-worker",
                    "executor_capabilities": ["meta_api"],
                    "claim_token": "claim-legacy-0001",
                    "claimed_at": NOW.isoformat(),
                    "lease_seconds": 1800,
                    "lease_expires_at": (NOW + timedelta(minutes=30)).isoformat(),
                },
                "progress": [
                    {
                        "sequence": 1,
                        "step": "legacy_meta_publish",
                        "progress_percent": 90,
                        "message": "legacy executor reported progress",
                        "recorded_at": (NOW + timedelta(seconds=1)).isoformat(),
                    }
                ],
            }
        )
    path.write_text(json.dumps(payload), encoding="utf-8")


def test_create_is_idempotent_for_revision_caption_and_platform_set(tmp_path: Path):
    package = tmp_path / "ig-carousel"
    first = _queued(
        package,
        targets=[_browser_target("youtube_community"), _browser_target("instagram")],
    )
    duplicate, created = create_or_get_publish_job(
        package_root=package,
        episode_id="ep120",
        source_revision="r016",
        source_manifest_sha256=SHA,
        source_publish_compatibility="api_compatible",
        approval_revision_number=3,
        approved_at=NOW - timedelta(minutes=1),
        caption="Approved caption 1",
        assets=[_asset(package)],
        targets=[_browser_target("instagram"), _browser_target("youtube_community")],
        now=NOW + timedelta(seconds=1),
        job_id="pj-" + "2" * 32,
    )

    assert created is False
    assert duplicate.job_id == first.job_id
    assert [job.job_id for job in list_publish_jobs(package)] == [first.job_id]


def test_create_rejects_overlapping_active_job_with_different_fingerprint(
    tmp_path: Path,
):
    package = tmp_path / "ig-carousel"
    first = _queued(package)

    with pytest.raises(PublishJobTransitionError, match="active publish job"):
        create_or_get_publish_job(
            package_root=package,
            episode_id="ep120",
            source_revision="r016",
            source_manifest_sha256=SHA,
            source_publish_compatibility="api_compatible",
            approval_revision_number=3,
            approved_at=NOW - timedelta(minutes=1),
            caption="Different caption",
            assets=[_asset(package)],
            targets=[_browser_target()],
            now=NOW + timedelta(seconds=1),
            job_id="pj-" + "2" * 32,
        )

    assert [job.job_id for job in list_publish_jobs(package)] == [first.job_id]


def test_active_overlap_is_scoped_to_the_same_source_revision_and_manifest(
    tmp_path: Path,
):
    package = tmp_path / "ig-carousel"
    first = _queued(package)
    newer_manifest = package / "revisions" / "r017" / "review_manifest.v1.json"
    newer_manifest.parent.mkdir(parents=True, exist_ok=True)
    newer_manifest.write_bytes(b'{"revision":"r017"}\n')
    newer_sha = receipt_for(newer_manifest).sha256
    current_path = package / "current.json"
    current = json.loads(current_path.read_text(encoding="utf-8"))
    current.update(
        {
            "revision": "r017",
            "manifest": str(newer_manifest.resolve()),
            "manifest_sha256": newer_sha,
        }
    )
    current_path.write_text(json.dumps(current), encoding="utf-8")
    newer, created = create_or_get_publish_job(
        package_root=package,
        episode_id="ep120",
        source_revision="r017",
        source_manifest_sha256=newer_sha,
        source_publish_compatibility="api_compatible",
        approval_revision_number=4,
        approved_at=NOW,
        caption="Newly approved revision",
        assets=[_asset(package)],
        targets=[_browser_target()],
        now=NOW + timedelta(seconds=1),
        job_id="pj-" + "2" * 32,
    )

    assert created is True
    assert newer.job_id != first.job_id


def test_republish_after_success_requires_explicit_confirmation(tmp_path: Path):
    package = tmp_path / "ig-carousel"
    queued = _queued(package)
    path = publish_job_path(package, queued.job_id)
    claim_publish_job(
        path,
        executor="codex",
        executor_id="worker-1",
        executor_capabilities=["browser_session"],
        claim_token="claim-republish-0001",
        now=NOW + timedelta(seconds=1),
    )
    progress_publish_job(
        path,
        claim_token="claim-republish-0001",
        step="published",
        progress_percent=100,
        now=NOW + timedelta(seconds=2),
    )
    start_publish_target(
        path,
        claim_token="claim-republish-0001",
        platform="instagram",
        now=NOW + timedelta(seconds=2),
    )
    result = CarouselPublishPlatformResult(
        platform="instagram",
        strategy="agent_browser",
        status="published",
        receipt_id="ig-published-once",
        completed_at=NOW + timedelta(seconds=3),
    )
    result = _bound_result(path, result)
    checkpoint_publish_target(
        path,
        claim_token="claim-republish-0001",
        result=result,
        now=NOW + timedelta(seconds=3),
    )
    complete_publish_job(
        path,
        claim_token="claim-republish-0001",
        results=[result],
        now=NOW + timedelta(seconds=3),
    )
    assert (
        republish_required_platforms(
            package_root=package,
            source_revision="r016",
            source_manifest_sha256=SHA,
            source_publish_compatibility="api_compatible",
            caption="Approved caption 1",
            targets=[_browser_target()],
        )
        == []
    )
    assert republish_required_platforms(
        package_root=package,
        source_revision="r016",
        source_manifest_sha256=SHA,
        source_publish_compatibility="api_compatible",
        caption="Intentionally republished caption",
        targets=[_browser_target()],
    ) == ["instagram"]
    kwargs = {
        "package_root": package,
        "episode_id": "ep120",
        "source_revision": "r016",
        "source_manifest_sha256": SHA,
        "source_publish_compatibility": "api_compatible",
        "approval_revision_number": 3,
        "approved_at": NOW - timedelta(minutes=1),
        "caption": "Intentionally republished caption",
        "assets": [_asset(package)],
        "targets": [_browser_target()],
        "now": NOW + timedelta(seconds=4),
        "job_id": "pj-" + "2" * 32,
    }

    with pytest.raises(PublishJobTransitionError, match="explicit confirmation"):
        create_or_get_publish_job(**kwargs)
    republish, created = create_or_get_publish_job(
        **kwargs,
        allow_republish=True,
    )

    assert created is True
    assert republish.status == "queued"


def test_partial_failure_requires_confirmation_before_republishing_published_target(
    tmp_path: Path,
):
    package = tmp_path / "ig-carousel"
    targets = [_browser_target("instagram"), _browser_target("youtube_community")]
    queued = _queued(package, targets=targets)
    path = publish_job_path(package, queued.job_id)
    claim_publish_job(
        path,
        executor="codex",
        executor_id="worker-1",
        executor_capabilities=["browser_session"],
        claim_token="claim-partial-republish-0001",
        now=NOW + timedelta(seconds=1),
    )
    results = [
        CarouselPublishPlatformResult(
            platform="instagram",
            strategy="agent_browser",
            status="published",
            receipt_id="ig-partial-published",
            completed_at=NOW + timedelta(seconds=2),
        ),
        CarouselPublishPlatformResult(
            platform="youtube_community",
            strategy="agent_browser_manual",
            status="failed",
            error="manual confirmation failed",
            completed_at=NOW + timedelta(seconds=3),
        ),
    ]
    for index, result in enumerate(results):
        start_publish_target(
            path,
            claim_token="claim-partial-republish-0001",
            platform=result.platform,
            now=result.completed_at,
        )
        result = _bound_result(path, result)
        results[index] = result
        checkpoint_publish_target(
            path,
            claim_token="claim-partial-republish-0001",
            result=result,
            now=result.completed_at,
        )
    failed = complete_publish_job(
        path,
        claim_token="claim-partial-republish-0001",
        results=results,
        now=NOW + timedelta(seconds=4),
    )
    assert failed.status == "failed"
    assert (
        republish_required_platforms(
            package_root=package,
            source_revision="r016",
            source_manifest_sha256=SHA,
            source_publish_compatibility="api_compatible",
            caption="Approved caption 1",
            targets=targets,
        )
        == []
    )

    kwargs = {
        "package_root": package,
        "episode_id": "ep120",
        "source_revision": "r016",
        "source_manifest_sha256": SHA,
        "source_publish_compatibility": "api_compatible",
        "approval_revision_number": 3,
        "approved_at": NOW - timedelta(minutes=1),
        "caption": "A different Instagram caption",
        "assets": failed.assets,
        "targets": [_browser_target("instagram")],
        "now": NOW + timedelta(seconds=5),
        "job_id": "pj-" + "2" * 32,
    }
    with pytest.raises(PublishJobTransitionError, match="explicit confirmation"):
        create_or_get_publish_job(**kwargs)

    republish, created = create_or_get_publish_job(**kwargs, allow_republish=True)
    assert created is True
    assert republish.status == "queued"


def test_handwritten_legacy_v1_loads_lists_and_preserves_idempotency_and_retry(
    tmp_path: Path,
):
    active_package = tmp_path / "active" / "ig-carousel"
    active_path = _write_legacy_job(active_package, status="queued", suffix="4")

    legacy = load_publish_job(active_path)
    listed = list_publish_jobs(active_package)
    cli = subprocess.run(
        [
            sys.executable,
            str(Path("scripts/podcast_carousel_publish_job.py").resolve()),
            "list",
            str(active_package),
        ],
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=False,
    )
    duplicate, created = create_or_get_publish_job(
        package_root=active_package,
        episode_id="ep120",
        source_revision="r016",
        source_manifest_sha256=SHA,
        source_publish_compatibility="api_compatible",
        approval_revision_number=3,
        approved_at=NOW - timedelta(minutes=1),
        caption="Legacy caption 4",
        assets=[_asset(active_package)],
        targets=[_browser_target()],
        now=NOW + timedelta(seconds=1),
        job_id="pj-" + "5" * 32,
    )

    assert legacy.source_publish_compatibility is None
    assert [job.job_id for job in listed] == [legacy.job_id]
    assert cli.returncode == 0, cli.stderr
    assert json.loads(cli.stdout)[0]["job_id"] == legacy.job_id
    assert created is False
    assert duplicate.job_id == legacy.job_id

    failed_package = tmp_path / "failed" / "ig-carousel"
    failed_path = _write_legacy_job(failed_package, status="failed", suffix="6")
    failed = load_publish_job(failed_path)
    retry, retry_created = create_or_get_publish_job(
        package_root=failed_package,
        episode_id="ep120",
        source_revision="r016",
        source_manifest_sha256=SHA,
        source_publish_compatibility="api_compatible",
        approval_revision_number=3,
        approved_at=NOW - timedelta(minutes=1),
        caption="Legacy caption 6",
        assets=[_asset(failed_package)],
        targets=[_browser_target()],
        now=NOW + timedelta(seconds=1),
        job_id="pj-" + "7" * 32,
    )

    assert failed.source_publish_compatibility is None
    assert retry_created is True
    assert retry.retry_of_job_id == failed.job_id
    assert retry.source_publish_compatibility == "api_compatible"


def test_legacy_manual_only_meta_job_loads_but_cannot_be_claimed(tmp_path: Path):
    package = tmp_path / "ig-carousel"
    path = _write_legacy_job(package, status="queued", suffix="9")
    _rewrite_legacy_as_manual_meta(path)

    legacy = load_publish_job(path)
    assert legacy.source_publish_compatibility is None
    with pytest.raises(PublishJobTransitionError, match="legacy manual_only"):
        claim_publish_job(
            path,
            executor="codex",
            executor_id="worker-1",
            executor_capabilities=["meta_api"],
            now=NOW + timedelta(seconds=1),
        )
    assert load_publish_job(path).status == "queued"

    replacement, created = create_or_get_publish_job(
        package_root=package,
        episode_id="ep120",
        source_revision="r016",
        source_manifest_sha256=SHA,
        source_publish_compatibility="manual_only",
        approval_revision_number=3,
        approved_at=NOW - timedelta(minutes=1),
        caption="Legacy caption 9",
        assets=legacy.assets,
        targets=[_browser_target()],
        now=NOW + timedelta(seconds=2),
        job_id="pj-" + "a" * 32,
    )
    duplicate, duplicate_created = create_or_get_publish_job(
        package_root=package,
        episode_id="ep120",
        source_revision="r016",
        source_manifest_sha256=SHA,
        source_publish_compatibility="manual_only",
        approval_revision_number=3,
        approved_at=NOW - timedelta(minutes=1),
        caption="Legacy caption 9",
        assets=legacy.assets,
        targets=[_browser_target()],
        now=NOW + timedelta(seconds=3),
        job_id="pj-" + "b" * 32,
    )

    assert created is True
    assert replacement.retry_of_job_id == legacy.job_id
    assert replacement.targets[0].strategy == "agent_browser"
    assert replacement.source_publish_compatibility == "manual_only"
    assert duplicate_created is False
    assert duplicate.job_id == replacement.job_id


def test_legacy_manual_meta_in_progress_job_cannot_complete(tmp_path: Path):
    package = tmp_path / "ig-carousel"
    path = _write_legacy_job(package, status="queued", suffix="c")
    _rewrite_legacy_as_manual_meta(path, status="in_progress")

    with pytest.raises(PublishJobTransitionError, match="manual_only publish job"):
        complete_publish_job(
            path,
            claim_token="claim-legacy-0001",
            results=[
                CarouselPublishPlatformResult(
                    platform="instagram",
                    strategy="meta_api",
                    status="published",
                    receipt_id="legacy-meta-receipt",
                    completed_at=NOW + timedelta(seconds=2),
                )
            ],
            now=NOW + timedelta(seconds=2),
        )
    assert load_publish_job(path).status == "in_progress"


def test_manual_only_job_schema_rejects_meta_api_even_if_target_is_configured(tmp_path: Path):
    package = tmp_path / "ig-carousel"
    with pytest.raises(ValidationError, match="manual_only carousel cannot use Meta API"):
        create_or_get_publish_job(
            package_root=package,
            episode_id="ep120",
            source_revision="r016",
            source_manifest_sha256=SHA,
            source_publish_compatibility="manual_only",
            approval_revision_number=3,
            approved_at=NOW - timedelta(minutes=1),
            caption="Manual carousel",
            assets=[_asset(package)],
            targets=[_meta_target()],
            now=NOW,
        )


def test_failed_request_creates_one_auditable_retry_job(tmp_path: Path):
    package = tmp_path / "ig-carousel"
    first = _queued(package)
    first_path = publish_job_path(package, first.job_id)
    claim_publish_job(
        first_path,
        executor="codex",
        executor_id="worker-1",
        executor_capabilities=["browser_session"],
        claim_token="claim-browser-0001",
        now=NOW + timedelta(seconds=1),
    )
    fail_publish_job(
        first_path,
        claim_token="claim-browser-0001",
        error="browser session expired",
        now=NOW + timedelta(seconds=2),
    )

    retry, created = create_or_get_publish_job(
        package_root=package,
        episode_id="ep120",
        source_revision="r016",
        source_manifest_sha256=SHA,
        source_publish_compatibility="api_compatible",
        approval_revision_number=3,
        approved_at=NOW - timedelta(minutes=1),
        caption="Approved caption 1",
        assets=[_asset(package)],
        targets=[_browser_target()],
        now=NOW + timedelta(seconds=3),
        job_id="pj-" + "2" * 32,
    )
    duplicate, duplicate_created = create_or_get_publish_job(
        package_root=package,
        episode_id="ep120",
        source_revision="r016",
        source_manifest_sha256=SHA,
        source_publish_compatibility="api_compatible",
        approval_revision_number=3,
        approved_at=NOW - timedelta(minutes=1),
        caption="Approved caption 1",
        assets=[_asset(package)],
        targets=[_browser_target()],
        now=NOW + timedelta(seconds=4),
        job_id="pj-" + "3" * 32,
    )

    assert created is True
    assert retry.status == "queued"
    assert retry.job_id != first.job_id
    assert retry.retry_of_job_id == first.job_id
    assert duplicate_created is False
    assert duplicate.job_id == retry.job_id


@pytest.mark.parametrize("executor", ["codex", "claude_code"])
def test_codex_and_claude_code_can_claim_with_required_capability(
    tmp_path: Path,
    executor: str,
):
    package = tmp_path / executor / "ig-carousel"
    queued = _queued(package)
    path = publish_job_path(package, queued.job_id)

    claimed = claim_publish_job(
        path,
        executor=executor,
        executor_id=f"{executor}-worker",
        executor_capabilities=["browser_session"],
        claim_token=f"claim-{executor}-0001",
        now=NOW + timedelta(seconds=1),
    )

    assert claimed.status == "claimed"
    assert claimed.claim is not None
    assert claimed.claim.executor == executor


def test_claim_rejects_missing_capability_and_active_lease(tmp_path: Path):
    package = tmp_path / "ig-carousel"
    queued = _queued(package)
    path = publish_job_path(package, queued.job_id)

    with pytest.raises(PublishJobTransitionError, match="lacks required"):
        claim_publish_job(
            path,
            executor="codex",
            executor_id="worker-1",
            executor_capabilities=["meta_api"],
            now=NOW + timedelta(seconds=1),
        )
    claim_publish_job(
        path,
        executor="codex",
        executor_id="worker-1",
        executor_capabilities=["browser_session"],
        claim_token="claim-browser-0001",
        lease_seconds=60,
        now=NOW + timedelta(seconds=1),
    )
    with pytest.raises(PublishJobTransitionError, match="lease is still active"):
        claim_publish_job(
            path,
            executor="claude_code",
            executor_id="worker-2",
            executor_capabilities=["browser_session"],
            now=NOW + timedelta(seconds=30),
        )


def test_claim_rejects_approval_revoked_by_later_draft(tmp_path: Path):
    package = tmp_path / "ig-carousel"
    queued = _queued(package)
    audit_path = package / "review_feedback.v1.json"
    audit = json.loads(audit_path.read_text(encoding="utf-8"))
    audit["revisions"].append(
        {
            "revision_number": 4,
            "created_at": NOW.isoformat(),
            "carousel_revision": "r016",
            "manifest_sha256": SHA,
            "decision": "draft",
        }
    )
    audit_path.write_text(json.dumps(audit), encoding="utf-8")

    with pytest.raises(PublishJobTransitionError, match="approval is no longer current"):
        claim_publish_job(
            publish_job_path(package, queued.job_id),
            executor="codex",
            executor_id="worker-1",
            executor_capabilities=["browser_session"],
            now=NOW + timedelta(seconds=1),
        )
    assert load_publish_job(publish_job_path(package, queued.job_id)).status == "queued"


def test_stale_lock_files_do_not_permanently_block_claim(tmp_path: Path):
    package = tmp_path / "ig-carousel"
    queued = _queued(package)
    path = publish_job_path(package, queued.job_id)
    path.with_suffix(path.suffix + ".lock").write_text(
        '{"pid":999999,"acquired_at":"2000-01-01T00:00:00Z"}',
        encoding="utf-8",
    )
    (package / ".publish-release.lock").write_text(
        '{"pid":999999,"acquired_at":"2000-01-01T00:00:00Z"}',
        encoding="utf-8",
    )

    claimed = claim_publish_job(
        path,
        executor="codex",
        executor_id="worker-1",
        executor_capabilities=["browser_session"],
        now=NOW + timedelta(seconds=1),
    )

    assert claimed.status == "claimed"


def test_partial_checkpoint_survives_crash_reclaim_and_failed_retry(tmp_path: Path):
    package = tmp_path / "ig-carousel"
    targets = [_browser_target("instagram"), _browser_target("youtube_community")]
    queued = _queued(package, targets=targets)
    path = publish_job_path(package, queued.job_id)
    claim_publish_job(
        path,
        executor="codex",
        executor_id="worker-1",
        executor_capabilities=["browser_session"],
        claim_token="claim-partial-0001",
        lease_seconds=60,
        now=NOW + timedelta(seconds=1),
    )
    started = start_publish_target(
        path,
        claim_token="claim-partial-0001",
        platform="instagram",
        now=NOW + timedelta(seconds=2),
    )
    instagram_key = next(
        state.idempotency_key for state in started.target_states if state.platform == "instagram"
    )
    checkpointed = checkpoint_publish_target(
        path,
        claim_token="claim-partial-0001",
        result=_bound_result(
            path,
            CarouselPublishPlatformResult(
                platform="instagram",
                strategy="agent_browser",
                status="published",
                receipt_id="ig-checkpoint-120",
                permalink="https://www.instagram.com/p/checkpoint/",
                completed_at=NOW + timedelta(seconds=3),
            ),
        ),
        now=NOW + timedelta(seconds=3),
    )
    assert unfinished_publish_platforms(checkpointed) == ["youtube_community"]

    reclaimed = claim_publish_job(
        path,
        executor="claude_code",
        executor_id="worker-2",
        executor_capabilities=["browser_session"],
        claim_token="claim-partial-0002",
        now=NOW + timedelta(seconds=63),
    )
    assert reclaimed.results[0].receipt_id == "ig-checkpoint-120"
    with pytest.raises(PublishJobTransitionError, match="already checkpointed"):
        start_publish_target(
            path,
            claim_token="claim-partial-0002",
            platform="instagram",
            now=NOW + timedelta(seconds=64),
        )
    failed = fail_publish_job(
        path,
        claim_token="claim-partial-0002",
        error="browser crashed before YouTube",
        now=NOW + timedelta(seconds=64),
    )

    retry, created = create_or_get_publish_job(
        package_root=package,
        episode_id="ep120",
        source_revision="r016",
        source_manifest_sha256=SHA,
        source_publish_compatibility="api_compatible",
        approval_revision_number=3,
        approved_at=NOW - timedelta(minutes=1),
        caption="Approved caption 1",
        assets=failed.assets,
        targets=[_meta_target(), _browser_target("youtube_community")],
        now=NOW + timedelta(seconds=65),
        job_id="pj-" + "d" * 32,
    )

    assert created is True
    assert retry.retry_of_job_id == failed.job_id
    assert retry.results[0].receipt_id == "ig-checkpoint-120"
    assert unfinished_publish_platforms(retry) == ["youtube_community"]
    carried = next(state for state in retry.target_states if state.platform == "instagram")
    assert carried.status == "published"
    assert carried.idempotency_key == instagram_key

    retry_path = publish_job_path(package, retry.job_id)
    claim_publish_job(
        retry_path,
        executor="codex",
        executor_id="worker-3",
        executor_capabilities=["browser_session"],
        claim_token="claim-partial-0003",
        now=NOW + timedelta(seconds=66),
    )
    failed_again = fail_publish_job(
        retry_path,
        claim_token="claim-partial-0003",
        error="browser unavailable before unfinished target",
        now=NOW + timedelta(seconds=67),
    )
    third, third_created = create_or_get_publish_job(
        package_root=package,
        episode_id="ep120",
        source_revision="r016",
        source_manifest_sha256=SHA,
        source_publish_compatibility="api_compatible",
        approval_revision_number=3,
        approved_at=NOW - timedelta(minutes=1),
        caption="Approved caption 1",
        assets=failed_again.assets,
        targets=targets,
        now=NOW + timedelta(seconds=68),
        job_id="pj-" + "e" * 32,
    )
    assert third_created is True
    assert third.retry_of_job_id == retry.job_id
    assert published_publish_platforms(third) == ["instagram"]


def test_reclaimed_in_progress_target_requires_reconciliation_not_restart(tmp_path: Path):
    package = tmp_path / "ig-carousel"
    queued = _queued(package)
    path = publish_job_path(package, queued.job_id)
    claim_publish_job(
        path,
        executor="codex",
        executor_id="worker-1",
        executor_capabilities=["browser_session"],
        claim_token="claim-uncertain-0001",
        lease_seconds=60,
        now=NOW + timedelta(seconds=1),
    )
    started = start_publish_target(
        path,
        claim_token="claim-uncertain-0001",
        platform="instagram",
        now=NOW + timedelta(seconds=2),
    )
    assert started.target_states[0].attempt_count == 1
    claim_publish_job(
        path,
        executor="claude_code",
        executor_id="worker-2",
        executor_capabilities=["browser_session"],
        claim_token="claim-uncertain-0002",
        now=NOW + timedelta(seconds=62),
    )

    with pytest.raises(PublishJobTransitionError, match="uncertain.*reconcile"):
        start_publish_target(
            path,
            claim_token="claim-uncertain-0002",
            platform="instagram",
            now=NOW + timedelta(seconds=63),
        )

    reconciled = checkpoint_publish_target(
        path,
        claim_token="claim-uncertain-0002",
        result=_bound_result(
            path,
            CarouselPublishPlatformResult(
                platform="instagram",
                strategy="agent_browser",
                status="published",
                receipt_id="reconciled-external-post",
                completed_at=NOW + timedelta(seconds=63),
            ),
        ),
        now=NOW + timedelta(seconds=63),
    )
    assert reconciled.target_states[0].attempt_count == 1
    assert reconciled.target_states[0].status == "published"


def test_failed_target_checkpoint_cannot_be_cleared_by_restart(tmp_path: Path):
    package = tmp_path / "ig-carousel"
    queued = _queued(package)
    path = publish_job_path(package, queued.job_id)
    claim_publish_job(
        path,
        executor="codex",
        executor_id="worker-1",
        executor_capabilities=["browser_session"],
        claim_token="claim-failed-target-0001",
        now=NOW + timedelta(seconds=1),
    )
    start_publish_target(
        path,
        claim_token="claim-failed-target-0001",
        platform="instagram",
        now=NOW + timedelta(seconds=2),
    )
    failed_result = CarouselPublishPlatformResult(
        platform="instagram",
        strategy="agent_browser",
        status="failed",
        error="browser returned an ambiguous failure",
        completed_at=NOW + timedelta(seconds=3),
    )
    failed_result = _bound_result(path, failed_result)
    checkpoint_publish_target(
        path,
        claim_token="claim-failed-target-0001",
        result=failed_result,
        now=NOW + timedelta(seconds=3),
    )

    with pytest.raises(PublishJobTransitionError, match="failed checkpoint"):
        start_publish_target(
            path,
            claim_token="claim-failed-target-0001",
            platform="instagram",
            now=NOW + timedelta(seconds=4),
        )

    preserved = load_publish_job(path).target_states[0]
    assert preserved.status == "failed"
    assert preserved.checkpoint == failed_result
    assert preserved.attempt_count == 1


def test_retry_claim_requires_capabilities_only_for_unfinished_targets(tmp_path: Path):
    package = tmp_path / "ig-carousel"
    targets = [_meta_target(), _browser_target("youtube_community")]
    queued = _queued(package, targets=targets)
    path = publish_job_path(package, queued.job_id)
    claim_publish_job(
        path,
        executor="codex",
        executor_id="worker-1",
        executor_capabilities=["browser_session", "meta_api"],
        claim_token="claim-mixed-0001",
        now=NOW + timedelta(seconds=1),
    )
    start_publish_target(
        path,
        claim_token="claim-mixed-0001",
        platform="instagram",
        now=NOW + timedelta(seconds=2),
    )
    checkpoint_publish_target(
        path,
        claim_token="claim-mixed-0001",
        result=_bound_result(
            path,
            CarouselPublishPlatformResult(
                platform="instagram",
                strategy="meta_api",
                status="published",
                receipt_id="ig-meta-receipt",
                completed_at=NOW + timedelta(seconds=3),
            ),
        ),
        now=NOW + timedelta(seconds=3),
    )
    failed = fail_publish_job(
        path,
        claim_token="claim-mixed-0001",
        error="browser unavailable",
        now=NOW + timedelta(seconds=4),
    )
    retry, created = create_or_get_publish_job(
        package_root=package,
        episode_id="ep120",
        source_revision="r016",
        source_manifest_sha256=SHA,
        source_publish_compatibility="api_compatible",
        approval_revision_number=3,
        approved_at=NOW - timedelta(minutes=1),
        caption="Approved caption 1",
        assets=failed.assets,
        targets=targets,
        now=NOW + timedelta(seconds=5),
        job_id="pj-" + "e" * 32,
    )

    assert created is True
    assert unfinished_publish_platforms(retry) == ["youtube_community"]
    claimed = claim_publish_job(
        publish_job_path(package, retry.job_id),
        executor="claude_code",
        executor_id="browser-worker",
        executor_capabilities=["browser_session"],
        now=NOW + timedelta(seconds=6),
    )
    assert claimed.status == "claimed"
    assert claimed.results[0].receipt_id == "ig-meta-receipt"


@pytest.mark.parametrize("drift", ["missing", "size", "hash"])
def test_claim_revalidates_every_asset_receipt(tmp_path: Path, drift: str):
    package = tmp_path / drift / "ig-carousel"
    queued = _queued(package)
    path = publish_job_path(package, queued.job_id)
    image_path = Path(queued.assets[0].image.path)
    if drift == "missing":
        image_path.rename(image_path.with_suffix(".moved"))
    elif drift == "size":
        image_path.write_bytes(image_path.read_bytes() + b"-drift")
    else:
        image_path.write_bytes(b"x" * queued.assets[0].image.bytes)

    with pytest.raises(PublishJobTransitionError, match="asset integrity"):
        claim_publish_job(
            path,
            executor="codex",
            executor_id="worker-1",
            executor_capabilities=["browser_session"],
            now=NOW + timedelta(seconds=1),
        )
    assert load_publish_job(path).status == "queued"


def test_expired_lease_can_be_reclaimed_but_old_token_cannot_progress(tmp_path: Path):
    package = tmp_path / "ig-carousel"
    queued = _queued(package)
    path = publish_job_path(package, queued.job_id)
    claim_publish_job(
        path,
        executor="codex",
        executor_id="worker-1",
        executor_capabilities=["browser_session"],
        claim_token="claim-browser-0001",
        lease_seconds=60,
        now=NOW + timedelta(seconds=1),
    )

    reclaimed = claim_publish_job(
        path,
        executor="claude_code",
        executor_id="worker-2",
        executor_capabilities=["browser_session"],
        claim_token="claim-browser-0002",
        now=NOW + timedelta(seconds=61),
    )

    assert reclaimed.claim is not None
    assert reclaimed.claim.executor == "claude_code"
    with pytest.raises(PublishJobTransitionError, match="claim token mismatch"):
        progress_publish_job(
            path,
            claim_token="claim-browser-0001",
            step="stale_worker",
            progress_percent=25,
            now=NOW + timedelta(seconds=62),
        )


def test_mixed_completion_is_failed_and_retry_carries_published_target(tmp_path: Path):
    package = tmp_path / "ig-carousel"
    targets = [_browser_target("instagram"), _browser_target("youtube_community")]
    queued = _queued(package, targets=targets)
    path = publish_job_path(package, queued.job_id)
    claim_publish_job(
        path,
        executor="codex",
        executor_id="worker-1",
        executor_capabilities=["browser_session"],
        claim_token="claim-browser-0001",
        now=NOW + timedelta(seconds=1),
    )
    active = progress_publish_job(
        path,
        claim_token="claim-browser-0001",
        step="open_platforms",
        progress_percent=50,
        message="Authenticated browser sessions ready",
        now=NOW + timedelta(seconds=2),
    )
    assert active.status == "in_progress"

    instagram_result = CarouselPublishPlatformResult(
        platform="instagram",
        strategy="agent_browser",
        status="published",
        receipt_id="ig-receipt-120",
        permalink="https://www.instagram.com/p/example/",
        completed_at=NOW + timedelta(seconds=3),
    )
    youtube_result = CarouselPublishPlatformResult(
        platform="youtube_community",
        strategy="agent_browser_manual",
        status="failed",
        error="human confirmation was not granted",
        completed_at=NOW + timedelta(seconds=3),
    )
    for platform, result in (
        ("instagram", instagram_result),
        ("youtube_community", youtube_result),
    ):
        start_publish_target(
            path,
            claim_token="claim-browser-0001",
            platform=platform,
            now=NOW + timedelta(seconds=3),
        )
        result = _bound_result(path, result)
        if platform == "instagram":
            instagram_result = result
        else:
            youtube_result = result
        checkpoint_publish_target(
            path,
            claim_token="claim-browser-0001",
            result=result,
            now=NOW + timedelta(seconds=3),
        )

    failed = complete_publish_job(
        path,
        claim_token="claim-browser-0001",
        results=[youtube_result, instagram_result],
        now=NOW + timedelta(seconds=4),
    )

    assert failed.status == "failed"
    assert failed.error == "one or more publish targets failed: youtube_community"
    assert [result.platform for result in failed.results] == [
        "instagram",
        "youtube_community",
    ]
    assert failed.results[0].permalink == "https://www.instagram.com/p/example/"
    assert failed.results[1].error == "human confirmation was not granted"

    retry, created = create_or_get_publish_job(
        package_root=package,
        episode_id="ep120",
        source_revision="r016",
        source_manifest_sha256=SHA,
        source_publish_compatibility="api_compatible",
        approval_revision_number=3,
        approved_at=NOW - timedelta(minutes=1),
        caption="Approved caption 1",
        assets=failed.assets,
        targets=targets,
        now=NOW + timedelta(seconds=5),
        job_id="pj-" + "f" * 32,
    )
    assert created is True
    assert retry.results[0].receipt_id == "ig-receipt-120"
    assert unfinished_publish_platforms(retry) == ["youtube_community"]
    assert retry.targets[0].strategy == "agent_browser"
    assert retry.target_states[0].strategy == "agent_browser"


def test_complete_revalidates_asset_receipts_after_claim(tmp_path: Path):
    package = tmp_path / "ig-carousel"
    queued = _queued(package)
    path = publish_job_path(package, queued.job_id)
    claim_publish_job(
        path,
        executor="codex",
        executor_id="worker-1",
        executor_capabilities=["browser_session"],
        claim_token="claim-browser-0001",
        now=NOW + timedelta(seconds=1),
    )
    progress_publish_job(
        path,
        claim_token="claim-browser-0001",
        step="browser_publish",
        progress_percent=100,
        now=NOW + timedelta(seconds=2),
    )
    image_path = Path(queued.assets[0].image.path)
    image_path.write_bytes(b"x" * queued.assets[0].image.bytes)

    with pytest.raises(PublishJobTransitionError, match="asset integrity"):
        complete_publish_job(
            path,
            claim_token="claim-browser-0001",
            results=[
                CarouselPublishPlatformResult(
                    platform="instagram",
                    strategy="agent_browser",
                    status="published",
                    receipt_id="browser-receipt-1",
                    completed_at=NOW + timedelta(seconds=3),
                )
            ],
            now=NOW + timedelta(seconds=4),
        )
    assert load_publish_job(path).status == "in_progress"


def test_complete_requires_per_target_checkpoints(tmp_path: Path):
    package = tmp_path / "ig-carousel"
    queued = _queued(package)
    path = publish_job_path(package, queued.job_id)
    claim_publish_job(
        path,
        executor="codex",
        executor_id="worker-1",
        executor_capabilities=["browser_session"],
        claim_token="claim-browser-0001",
        now=NOW + timedelta(seconds=1),
    )
    progress_publish_job(
        path,
        claim_token="claim-browser-0001",
        step="ready",
        progress_percent=10,
        now=NOW + timedelta(seconds=2),
    )

    with pytest.raises(PublishJobTransitionError, match="checkpointed before completion"):
        complete_publish_job(
            path,
            claim_token="claim-browser-0001",
            results=[
                CarouselPublishPlatformResult(
                    platform="instagram",
                    strategy="agent_browser",
                    status="published",
                    receipt_id="uncheckpointed-receipt",
                    completed_at=NOW + timedelta(seconds=3),
                )
            ],
            now=NOW + timedelta(seconds=3),
        )


def test_legacy_youtube_job_over_ten_images_is_readable_but_not_executable(tmp_path: Path):
    package = tmp_path / "ig-carousel"
    path = _write_legacy_job(package, status="queued", suffix="7")
    payload = json.loads(path.read_text(encoding="utf-8"))
    first_asset = payload["assets"][0]
    payload["assets"] = [
        {
            **first_asset,
            "page_id": f"page-{page_number}",
            "page_number": page_number,
        }
        for page_number in range(1, 12)
    ]
    payload["targets"] = [
        {
            "platform": "youtube_community",
            "strategy": "agent_browser_manual",
            "configuration_state": "manual_only",
            "required_executor_capabilities": ["browser_session"],
            "note": "Legacy YouTube browser target.",
        }
    ]
    payload["request_fingerprint"] = _legacy_fingerprint(payload["caption"], ["youtube_community"])
    path.write_text(json.dumps(payload), encoding="utf-8")

    assert load_publish_job(path).source_publish_compatibility is None
    assert list_publish_jobs(package)[0].targets[0].platform == "youtube_community"
    with pytest.raises(PublishJobTransitionError, match="at most 10"):
        claim_publish_job(
            path,
            executor="codex",
            executor_id="legacy-worker",
            executor_capabilities=["browser_session"],
            claim_token="claim-legacy-youtube-0001",
            now=NOW + timedelta(seconds=1),
        )
    assert load_publish_job(path).status == "queued"


def test_claim_migrates_legacy_empty_target_states_and_completion_stays_fail_closed(
    tmp_path: Path,
):
    package = tmp_path / "ig-carousel"
    path = _write_legacy_job(package, status="queued", suffix="8")
    with pytest.raises(PublishJobTransitionError, match="immutable release bundle"):
        claim_publish_job(
            path,
            executor="codex",
            executor_id="legacy-worker",
            executor_capabilities=["browser_session"],
            claim_token="claim-legacy-checkpoint-0001",
            now=NOW + timedelta(seconds=1),
        )
    assert load_publish_job(path).status == "queued"


def test_expired_unsafe_legacy_job_can_be_retired_without_execution(tmp_path: Path):
    package = tmp_path / "ig-carousel"
    path = _write_legacy_job(package, status="queued", suffix="a")
    _rewrite_legacy_as_manual_meta(path, status="in_progress")

    retired = retire_unsafe_legacy_publish_job(
        path,
        reason="unsafe legacy strategy cannot execute",
        now=NOW + timedelta(minutes=31),
    )

    assert retired.status == "superseded"
    assert retired.claim is None
    assert retired.superseded_reason == "unsafe legacy strategy cannot execute"


def test_invalid_terminal_transitions_fail_closed(tmp_path: Path):
    package = tmp_path / "ig-carousel"
    queued = _queued(package)
    path = publish_job_path(package, queued.job_id)
    with pytest.raises(PublishJobTransitionError, match="cannot complete"):
        complete_publish_job(path, claim_token="claim-browser-0001", results=[], now=NOW)
    claim_publish_job(
        path,
        executor="claude_code",
        executor_id="worker-2",
        executor_capabilities=["browser_session"],
        claim_token="claim-browser-0002",
        now=NOW + timedelta(seconds=1),
    )
    failed = fail_publish_job(
        path,
        claim_token="claim-browser-0002",
        error="browser session unavailable",
        now=NOW + timedelta(seconds=2),
    )
    assert failed.status == "failed"
    assert load_publish_job(path).error == "browser session unavailable"


def test_cli_list_claim_progress_and_complete_only_mutate_local_json(tmp_path: Path):
    package = tmp_path / "ig-carousel"
    queued = _queued(package)
    path = publish_job_path(package, queued.job_id)
    script = Path("scripts/podcast_carousel_publish_job.py").resolve()
    result_path = tmp_path / "results.json"
    result_path.write_text(
        json.dumps(
            [
                {
                    "platform": "instagram",
                    "strategy": "agent_browser",
                    "status": "published",
                    "receipt_id": "browser-receipt-1",
                    "completed_at": (NOW + timedelta(seconds=3)).isoformat(),
                }
            ]
        ),
        encoding="utf-8",
    )
    checkpoint_path = tmp_path / "checkpoint.json"
    checkpoint_path.write_text(
        json.dumps(json.loads(result_path.read_text(encoding="utf-8"))[0]),
        encoding="utf-8",
    )
    commands = [
        ["list", str(package)],
        [
            "claim",
            str(path),
            "--executor",
            "codex",
            "--executor-id",
            "cli-worker",
            "--capability",
            "browser_session",
            "--claim-token",
            "claim-cli-0001",
        ],
        [
            "start-target",
            str(path),
            "--claim-token",
            "claim-cli-0001",
            "--platform",
            "instagram",
        ],
        [
            "checkpoint",
            str(path),
            "--claim-token",
            "claim-cli-0001",
            "--result-json",
            str(checkpoint_path),
        ],
        [
            "progress",
            str(path),
            "--claim-token",
            "claim-cli-0001",
            "--step",
            "browser_publish",
            "--percent",
            "100",
        ],
        [
            "complete",
            str(path),
            "--claim-token",
            "claim-cli-0001",
            "--results-json",
            str(result_path),
        ],
    ]
    for command in commands:
        if command[0] == "checkpoint":
            state = load_publish_job(path).target_states[0]
            payload = json.loads(checkpoint_path.read_text(encoding="utf-8"))
            payload.update(
                {
                    "idempotency_key": state.idempotency_key,
                    "attempt_id": state.attempt_id,
                }
            )
            checkpoint_path.write_text(json.dumps(payload), encoding="utf-8")
            result_path.write_text(json.dumps([payload]), encoding="utf-8")
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
    assert load_publish_job(path).status == "completed"
    assert not (package / "published.json").exists()
