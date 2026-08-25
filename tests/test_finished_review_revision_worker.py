"""Public lifecycle contract for the finished-cut desktop revision worker."""

from __future__ import annotations

import hashlib
import json
import shutil
import subprocess
from copy import deepcopy
from pathlib import Path
from types import SimpleNamespace

import pytest

from scripts.finished_review_watcher import (
    _authoritative_output_verifier,
    _ResolveTimelineTransaction,
    _run_visual_pipeline_revision,
    _trusted_apply_revision,
    _verify_revision_output_acceptance,
    dispatch_revision_agent,
    pending_revision_jobs,
    prepare_trusted_asset_handoff,
    reconcile_missing_revision_job,
    recover_running_revision_job,
    retry_failed_revision_job,
    run_revision_job,
)


@pytest.fixture(autouse=True)
def _stub_authoritative_visual_producer(monkeypatch):
    monkeypatch.setattr(
        "scripts.finished_review_watcher._run_visual_pipeline_revision",
        _fixture_visual_pipeline_runner,
    )
    monkeypatch.setattr(
        "agents.brook.script_video.highlight_broll.verify_visual_recipe_lineage",
        lambda *_args, **_kwargs: ({"content_hash": "f" * 64}, []),
    )
    master = SimpleNamespace(identity=lambda: {"content_hash": "a" * 64})
    monkeypatch.setattr("scripts.run_short_broll._open_editorial_master", lambda _root: master)
    monkeypatch.setattr(
        "scripts.run_short_broll._load_winner",
        lambda *_args, **_kwargs: ({"format": "long"}, {"rank": 1}),
    )


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _write_acquisition_source(root: Path) -> tuple[Path, dict]:
    root.mkdir(parents=True)
    sources = {}
    for index in range(3):
        slug = f"trusted-stock-{index}"
        filename = f"{slug}.mp4"
        media = root / filename
        media.write_bytes(f"fixture-video-{index}".encode())
        sources[slug] = {
            "filename": filename,
            "bytes": media.stat().st_size,
            "sha256": _sha(media),
            "provenance": {
                "source_url": f"https://example.test/video/{index}",
                "acquired_at": "2026-08-22T04:48:53.1292202Z",
                "license_url": "https://example.test/license",
            },
        }
    manifest = root / "trusted_asset_sources.json"
    manifest.write_text(json.dumps(sources), encoding="utf-8")
    return manifest, sources


def _write_queued_job(
    root: Path,
    *,
    manifest_schema: str = "nakama.finished_cut_review_manifest.v2",
    episode_dir: Path | None = None,
) -> tuple[Path, Path, Path]:
    episode = episode_dir or root / "20260805 林之晨"
    review = episode / "highlights" / "review"
    cut_dir = review / "value-L01"
    cut_dir.mkdir(parents=True)
    preview = cut_dir / "長1_preview.mp4"
    preview.write_bytes(b"approved-old-preview")
    subtitles = cut_dir / "subs.srt"
    subtitles.write_text("1\n00:00:00,000 --> 00:00:01,000\n舊字幕\n", encoding="utf-8")
    (cut_dir / "events.json").write_text(
        json.dumps({"timeline": "長1 - value-L01（緊·導播）", "events": []}),
        encoding="utf-8",
    )
    tighten = episode / "highlights" / "tighten"
    tighten.mkdir(parents=True)
    (tighten / "value-L01_titles.json").write_text('{"old": true}', encoding="utf-8")
    visuals = episode / "highlights" / "stills" / "value-L01-visuals"
    visuals.mkdir(parents=True)
    (visuals / "old.png").write_bytes(b"old-visual")
    assets = episode / "assets" / "broll"
    assets.mkdir(parents=True)
    (assets / "existing.mp4").write_bytes(b"existing-asset")
    manifest = review / "finished_review_manifest_20260822.json"
    manifest_payload = {
        "schema": manifest_schema,
        "episode_id": episode.name,
        "stage": 5,
        "gate": {"kind": "finished_cut_review", "status": "ready_for_review"},
        "feedback_contract": {
            "review_lanes": ["hero_title"],
            "component_actions": {"hero_title": ["edit_text"]},
            "gate_actions": ["request_changes", "approve_cut", "approve_all"],
        },
        "cuts": [
            {
                "cut_id": "value-L01",
                "title": "old",
                "format": "long",
                "artifacts": {
                    "preview": {
                        "path": str(preview),
                        "bytes": preview.stat().st_size,
                        "sha256": _sha(preview),
                        "duration_seconds": 60.0,
                    },
                    "subtitles": {
                        "path": str(subtitles),
                        "bytes": subtitles.stat().st_size,
                        "sha256": _sha(subtitles),
                    },
                },
                "components": [
                    {
                        "component_id": "value-L01-hero-001",
                        "lane": "hero_title",
                        "t0": 2.0,
                        "t1": 4.0,
                        "text": "舊文字",
                    }
                ],
            }
        ],
    }
    manifest.write_text(json.dumps(manifest_payload, ensure_ascii=False), encoding="utf-8")
    from scripts.build_finished_review_manifest import identity_registry_source_sha256

    source_registry_sha256 = identity_registry_source_sha256(episode, manifest)
    audit = {
        "schema": "nakama.finished_cut_review_feedback.v1",
        "episode_id": episode.name,
        "revisions": [
            {
                "revision": 1,
                "decision": "draft",
                "manifest_sha256": _sha(manifest),
                "preview_sha256": {"value-L01": _sha(preview)},
                "component_feedback": [
                    {
                        "cut_id": "value-L01",
                        "component_id": "value-L01-hero-001",
                        "lane": "hero_title",
                        "action": "edit_text",
                        "replacement": "新文字\n分兩行",
                        "comment": "句子要完整",
                    }
                ],
                "overall_feedback": {"value-L01": "整體節奏壓短。\n保留開場。"},
                "revision_job": {
                    "contract": "finished-cut-revision-job-v1",
                    "request_id": "finished-revision-abc123",
                    "status": "queued",
                    "attempt": 0,
                    "review_format": "long",
                    "manifest_filename": manifest.name,
                    "source_manifest_sha256": _sha(manifest),
                    "source_registry_sha256": source_registry_sha256,
                    "source_preview_sha256": {"value-L01": _sha(preview)},
                    "requested_cut_ids": ["value-L01"],
                    "component_feedback": [
                        {
                            "cut_id": "value-L01",
                            "component_id": "value-L01-hero-001",
                            "action": "edit_text",
                            "replacement": "新文字\n分兩行",
                        }
                    ],
                    "overall_feedback": {"value-L01": "整體節奏壓短。\n保留開場。"},
                    "requested_at": "2026-08-22T04:00:00+00:00",
                    "started_at": None,
                    "finished_at": None,
                    "result_receipt": None,
                    "error": None,
                },
            }
        ],
    }
    feedback = review / "finished_review_feedback.v1.json"
    feedback.write_text(json.dumps(audit, ensure_ascii=False), encoding="utf-8")
    return episode, manifest, feedback


def _successful_agent(context: dict) -> subprocess.CompletedProcess[str]:
    output_tighten = Path(context["output_root"]) / "tighten"
    output_tighten.mkdir(parents=True, exist_ok=True)
    return subprocess.CompletedProcess(["fixture-agent"], 0, "done", "")


def _fixture_visual_pipeline_runner(context: dict) -> dict:
    output_tighten = Path(context["output_root"]) / "tighten"
    output_tighten.mkdir(parents=True, exist_ok=True)
    (output_tighten / "value-L01_broll.json").write_text(
        json.dumps({"items": []}), encoding="utf-8"
    )
    (output_tighten / "value-L01_titles.json").write_text(
        json.dumps(
            {
                "titles": [],
                "cards": [{"type": "hero", "title": "新文字\n分兩行"}],
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    return {
        "status": "ready_to_materialize",
        "cuts": [{"cut_id": "value-L01", "content_hash": "f" * 64}],
    }


def _fixture_verifier(context: dict) -> dict:
    manifest = Path(context["manifest_path"])
    payload = json.loads(manifest.read_text(encoding="utf-8"))
    preview = Path(payload["cuts"][0]["artifacts"]["preview"]["path"])
    payload["cuts"][0]["title"] = "revised"
    payload["cuts"][0]["components"][0]["text"] = "新文字\n分兩行"
    payload["cuts"][0]["artifacts"]["preview"].update(
        {"bytes": preview.stat().st_size, "sha256": _sha(preview)}
    )
    subtitles = Path(payload["cuts"][0]["artifacts"]["subtitles"]["path"])
    payload["cuts"][0]["artifacts"]["subtitles"].update(
        {"bytes": subtitles.stat().st_size, "sha256": _sha(subtitles)}
    )
    manifest.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    return {"verified": True, "approved": False}


def _fixture_trusted_apply(context: dict) -> dict:
    recipe = Path(context["episode_dir"]) / "highlights/tighten/value-L01_titles.json"
    assert json.loads(recipe.read_text(encoding="utf-8"))["cards"][0]["title"] == (
        "新文字\n分兩行"
    )
    manifest = Path(context["manifest_path"])
    payload = json.loads(manifest.read_text(encoding="utf-8"))
    preview = Path(payload["cuts"][0]["artifacts"]["preview"]["path"])
    preview.write_bytes(
        f"trusted-render-with-new-hero-{context['request_id']}".encode()
    )
    verification = context["output_verifier"](context)
    return {
        "status": "trusted_apply_succeeded",
        "operations": [
            {"operation": "run_short_titles.apply"},
            {"operation": "run_short_review.build_packet"},
        ],
        "timeline_transaction": {"committed": True},
        "authoritative_verification": verification,
        "output_acceptance": _verify_revision_output_acceptance(
            context, verification
        ),
    }


def test_worker_picks_up_once_and_leaves_a_result_receipt(tmp_path):
    _, manifest, feedback = _write_queued_job(tmp_path)
    jobs = pending_revision_jobs(tmp_path)
    assert len(jobs) == 1

    trusted_calls: list[str] = []

    def trusted(context: dict) -> dict:
        trusted_calls.append(context["request_id"])
        return _fixture_trusted_apply(context)

    assert run_revision_job(
        jobs[0],
        agent_runner=_successful_agent,
        output_verifier=_fixture_verifier,
        trusted_apply=trusted,
    )
    assert trusted_calls == ["finished-revision-abc123"]

    audit = json.loads(feedback.read_text(encoding="utf-8"))
    job = audit["revisions"][0]["revision_job"]
    assert job["status"] == "succeeded"
    assert job["output_manifest_sha256"] == _sha(manifest)
    receipt = feedback.parent / job["result_receipt"]
    assert receipt.is_file()
    result = json.loads(receipt.read_text(encoding="utf-8"))
    assert result["contract"] == "finished-cut-revision-result-v1"
    assert result["approved"] is False
    assert result["output_manifest_sha256"] == _sha(manifest)
    assert result["trusted_apply"]["timeline_transaction"]["committed"] is True
    assert result["trusted_apply"]["operations"][0]["operation"] == (
        "run_short_titles.apply"
    )
    request_path = feedback.parent / "revisions/finished-revision-abc123/request.json"
    saved_request = json.loads(request_path.read_text(encoding="utf-8"))
    assert saved_request["allowed_changes"]["requested_cut_ids"] == ["value-L01"]
    assert saved_request["allowed_changes"]["resolve_timelines"] == {
        "value-L01": "長1 - value-L01（緊·導播）"
    }
    assert saved_request["allowed_changes"]["new_broll_assets_policy"] == (
        "new-files-only-no-overwrite"
    )
    assert saved_request["allowed_changes"]["trusted_asset_sources_contract"][
        "staged_asset_path"
    ] == "output/assets/broll/<filename>"
    assert len(
        saved_request["allowed_changes"]["trusted_asset_sources_sha256"]
    ) == 64
    assert saved_request["pre_snapshot"]["assets/broll/existing.mp4"]["sha256"] == _sha(
        feedback.parents[2] / "assets/broll/existing.mp4"
    )
    assert pending_revision_jobs(tmp_path) == []

    # Idempotent pickup: a completed job can never dispatch again.
    assert not run_revision_job(
        jobs[0], agent_runner=lambda _: (_ for _ in ()).throw(AssertionError())
    )


def test_queued_legacy_v1_source_bootstraps_distinct_v2_output_before_resolve(
    monkeypatch, tmp_path
):
    episode, legacy_manifest, feedback = _write_queued_job(
        tmp_path, manifest_schema="nakama.finished_cut_review_manifest.v1"
    )
    legacy_bytes = legacy_manifest.read_bytes()

    class Master:
        def identity(self):
            return {"contract": "podcast-editorial-master-v1", "content_hash": "a" * 64}

    monkeypatch.setattr(
        "scripts.build_finished_review_manifest._open_master", lambda _episode: Master()
    )
    monkeypatch.setattr(
        "scripts.build_finished_review_manifest._approved_inventory",
        lambda _episode, _identity: {"value-L01": "long"},
    )
    calls: list[str] = []

    def agent(context: dict) -> subprocess.CompletedProcess[str]:
        calls.append("agent-plan")
        assert context["source_preflight"]["schema"].endswith(".v1")
        assert context["source_preflight"]["editorial_master_lineage"] == Master().identity()
        return _successful_agent(context)

    def trusted_v2_producer(context: dict) -> dict:
        calls.append("trusted-v2-producer")
        output_manifest = legacy_manifest.parent / "finished_review_manifest_current.json"
        payload = json.loads(legacy_manifest.read_text(encoding="utf-8"))
        payload["schema"] = "nakama.finished_cut_review_manifest.v2"
        payload["editorial_master_lineage"] = Master().identity()
        output_manifest.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
        output_context = {**context, "manifest_path": str(output_manifest)}
        return _fixture_trusted_apply(output_context)

    assert run_revision_job(
        pending_revision_jobs(tmp_path)[0],
        agent_runner=agent,
        output_verifier=_fixture_verifier,
        trusted_apply=trusted_v2_producer,
    )
    assert calls == ["agent-plan", "trusted-v2-producer"]
    assert legacy_manifest.read_bytes() == legacy_bytes
    output_manifest = legacy_manifest.parent / "finished_review_manifest_current.json"
    assert json.loads(output_manifest.read_text(encoding="utf-8"))["schema"].endswith(".v2")
    saved = json.loads(feedback.read_text(encoding="utf-8"))["revisions"][-1][
        "revision_job"
    ]
    assert saved["status"] == "succeeded"


def test_default_output_producer_passes_request_bound_identity_transition(
    monkeypatch, tmp_path
):
    episode, manifest, _feedback = _write_queued_job(tmp_path)
    request = {
        "review_format": "long",
        "requested_cut_ids": ["value-L01"],
        "source_manifest_sha256": _sha(manifest),
        "source_registry_sha256": "b" * 64,
        "source_preview_sha256": {"value-L01": "c" * 64},
        "component_feedback": [
            {
                "cut_id": "value-L01",
                "component_id": "value-L01-b-roll-001",
                "action": "remove",
            }
        ],
    }
    captured = {}

    def build(_episode, **kwargs):
        captured.update(kwargs)
        return manifest

    monkeypatch.setattr("scripts.build_finished_review_manifest.build_manifest", build)
    monkeypatch.setattr(
        "scripts.build_finished_review_manifest.verify_finished_review_cut",
        lambda *_args, **kwargs: {
            "cut_id": "value-L01",
            "manifest_sha256": _sha(manifest),
            "preview_sha256": "d" * 64,
            "identity_transition": kwargs["identity_transition"],
        },
    )

    result = _authoritative_output_verifier(
        {
            "request_id": "finished-revision-bound-identity",
            "episode_dir": str(episode),
            "request": request,
        }
    )

    expected = {
        "request_id": "finished-revision-bound-identity",
        "source_manifest_sha256": _sha(manifest),
        "source_registry_sha256": "b" * 64,
        "feedback_rows": request["component_feedback"],
    }
    assert captured["identity_transition"] == expected
    assert result["cut_results"][0]["identity_transition"] == expected


def test_legacy_v1_source_never_allows_v1_revision_output(monkeypatch, tmp_path):
    episode, legacy_manifest, feedback = _write_queued_job(
        tmp_path, manifest_schema="nakama.finished_cut_review_manifest.v1"
    )
    legacy_bytes = legacy_manifest.read_bytes()

    class Master:
        def identity(self):
            return {"contract": "podcast-editorial-master-v1", "content_hash": "a" * 64}

    monkeypatch.setattr(
        "scripts.build_finished_review_manifest._open_master", lambda _episode: Master()
    )
    monkeypatch.setattr(
        "scripts.build_finished_review_manifest._approved_inventory",
        lambda _episode, _identity: {"value-L01": "long"},
    )

    assert not run_revision_job(
        pending_revision_jobs(tmp_path)[0],
        agent_runner=_successful_agent,
        output_verifier=_fixture_verifier,
        trusted_apply=_fixture_trusted_apply,
    )
    saved = json.loads(feedback.read_text(encoding="utf-8"))["revisions"][-1][
        "revision_job"
    ]
    assert saved["status"] == "failed"
    assert "output manifest schema is invalid" in saved["error"]
    assert legacy_manifest.read_bytes() == legacy_bytes


def test_missing_queue_bound_identity_registry_fails_before_agent_or_resolve(tmp_path):
    _episode, _manifest, feedback = _write_queued_job(tmp_path)
    audit = json.loads(feedback.read_text(encoding="utf-8"))
    del audit["revisions"][-1]["revision_job"]["source_registry_sha256"]
    feedback.write_text(json.dumps(audit, ensure_ascii=False), encoding="utf-8")
    calls = []

    assert not run_revision_job(
        pending_revision_jobs(tmp_path)[0],
        agent_runner=lambda _context: calls.append("agent"),
        trusted_apply=lambda _context: calls.append("resolve"),
    )
    assert calls == []
    saved = json.loads(feedback.read_text(encoding="utf-8"))["revisions"][-1][
        "revision_job"
    ]
    assert saved["status"] == "failed"
    assert saved["attempt"] == 0
    assert "queue-bound identity registry" in saved["error"]


def test_public_revision_job_runs_request_bound_visual_producer_before_trusted_apply(
    tmp_path,
):
    _, _manifest, _feedback = _write_queued_job(tmp_path)
    calls: list[str] = []

    def agent(context: dict) -> subprocess.CompletedProcess[str]:
        calls.append("generic-agent")
        return _successful_agent(context)

    def producer(context: dict) -> dict:
        calls.append("visual-producer")
        request_path = Path(context["request_path"])
        assert request_path.is_file()
        assert json.loads(request_path.read_text(encoding="utf-8"))["request_id"] == (
            context["request_id"]
        )
        return _fixture_visual_pipeline_runner(context)

    def trusted(context: dict) -> dict:
        calls.append("trusted-apply")
        assert context["visual_pipeline"]["status"] == "ready_to_materialize"
        return _fixture_trusted_apply(context)

    assert run_revision_job(
        pending_revision_jobs(tmp_path)[0],
        agent_runner=agent,
        visual_pipeline_runner=producer,
        output_verifier=_fixture_verifier,
        trusted_apply=trusted,
    )
    assert calls == ["generic-agent", "visual-producer", "trusted-apply"]


def test_finished_revision_has_dedicated_python310_supervisor_not_render_watcher():
    repo = Path(__file__).parents[1]
    startup = (repo / "scripts/start_thousand_sunny.ps1").read_text(encoding="utf-8")
    render_watcher = (repo / "scripts/render_watcher.py").read_text(encoding="utf-8")

    assert r"C:\Users\Shosho\AppData\Local\Programs\Python\Python310\python.exe" in startup
    assert "scripts/finished_review_watcher.py" in startup
    assert "finished-review-watcher.out.log" in startup
    assert "run_finished_revision_queue_once" not in render_watcher
    assert "pending_finished_revision_jobs" not in render_watcher


def test_explicit_reconcile_marks_legacy_feedback_awaiting_assets_without_touching_dry_run(
    tmp_path,
):
    episode, _, feedback = _write_queued_job(tmp_path)
    audit = json.loads(feedback.read_text(encoding="utf-8"))
    del audit["revisions"][-1]["revision_job"]
    feedback.write_text(json.dumps(audit, ensure_ascii=False), encoding="utf-8")

    preview = reconcile_missing_revision_job(
        tmp_path, episode_id=episode.name, apply=False
    )
    assert preview["status"] == "awaiting_stock_assets"
    unchanged = json.loads(feedback.read_text(encoding="utf-8"))
    assert "revision_job" not in unchanged["revisions"][-1]

    applied = reconcile_missing_revision_job(
        tmp_path, episode_id=episode.name, apply=True
    )
    assert applied["status"] == "awaiting_stock_assets"
    saved = json.loads(feedback.read_text(encoding="utf-8"))
    job = saved["revisions"][-1]["revision_job"]
    assert job["request_id"] == applied["request_id"]
    assert job["requested_cut_ids"] == ["value-L01"]
    assert pending_revision_jobs(tmp_path) == []


def test_reconcile_acquisition_handoff_queues_and_reuses_assets_on_next_revision(
    monkeypatch, tmp_path
):
    episode, manifest, feedback = _write_queued_job(tmp_path)
    audit = json.loads(feedback.read_text(encoding="utf-8"))
    del audit["revisions"][-1]["revision_job"]
    feedback.write_text(json.dumps(audit, ensure_ascii=False), encoding="utf-8")
    acquisition, sources = _write_acquisition_source(tmp_path / "acquisition")
    monkeypatch.setattr(
        "agents.brook.script_video.highlight_broll.probe_stock_video",
        lambda _path: {"duration_seconds": 2.0, "video_streams": [{"codec": "h264"}]},
    )

    awaiting = reconcile_missing_revision_job(
        tmp_path, episode_id=episode.name, apply=False
    )
    preview = reconcile_missing_revision_job(
        tmp_path,
        episode_id=episode.name,
        apply=False,
        trusted_asset_sources=acquisition,
    )
    assert awaiting["status"] == "awaiting_stock_assets"
    assert preview["status"] == "would_queue"
    assert preview["request_id"] != awaiting["request_id"]
    assert not (episode / "highlights/revision-inputs/current.json").exists()

    applied = reconcile_missing_revision_job(
        tmp_path,
        episode_id=episode.name,
        apply=True,
        trusted_asset_sources=acquisition,
    )
    assert applied["status"] == "queued"
    saved = json.loads(feedback.read_text(encoding="utf-8"))
    job = saved["revisions"][-1]["revision_job"]
    assert job["trusted_asset_sources"] == sources
    assert job["trusted_asset_sources_sha256"] == hashlib.sha256(
        json.dumps(
            sources, ensure_ascii=False, sort_keys=True, separators=(",", ":")
        ).encode()
    ).hexdigest()
    assert job["trusted_asset_handoff"]["root"].startswith(
        "highlights/revision-inputs/"
    )

    def agent(context: dict) -> subprocess.CompletedProcess[str]:
        _successful_agent(context)
        output = Path(context["output_root"])
        for _slug, source in sources.items():
            staged = output / "assets/broll" / source["filename"]
            promoted = episode / "assets/broll" / source["filename"]
            assert staged.is_file() or promoted.is_file()
        return subprocess.CompletedProcess(["fixture-agent"], 0, "done", "")

    assert run_revision_job(
        pending_revision_jobs(tmp_path)[0],
        agent_runner=agent,
        output_verifier=_fixture_verifier,
        trusted_apply=_fixture_trusted_apply,
    )

    pointer = episode / "highlights/revision-inputs/current.json"
    pointer_payload = pointer.read_bytes()
    first_saved = json.loads(feedback.read_text(encoding="utf-8"))
    first_job = first_saved["revisions"][-1]["revision_job"]
    assert first_job["status"] == "succeeded"
    first_receipt = json.loads(
        (episode / "highlights/review" / first_job["result_receipt"]).read_text(
            encoding="utf-8"
        )
    )
    assert sorted(first_receipt["promoted_broll_assets"]) == sorted(
        f"assets/broll/{source['filename']}" for source in sources.values()
    )
    assert all("\\" not in path for path in first_receipt["promoted_broll_assets"])

    second_revision = deepcopy(first_saved["revisions"][-1])
    second_revision["revision"] = 2
    second_revision["manifest_sha256"] = _sha(manifest)
    preview_path = Path(
        json.loads(manifest.read_text(encoding="utf-8"))["cuts"][0]["artifacts"][
            "preview"
        ]["path"]
    )
    second_revision["preview_sha256"] = {"value-L01": _sha(preview_path)}
    second_job = deepcopy(second_revision["revision_job"])
    from scripts.build_finished_review_manifest import identity_registry_source_sha256

    second_job["source_registry_sha256"] = identity_registry_source_sha256(
        episode, manifest
    )
    second_job.update(
        {
            "request_id": "finished-revision-second456",
            "status": "queued",
            "attempt": 0,
            "source_manifest_sha256": _sha(manifest),
            "source_preview_sha256": {"value-L01": _sha(preview_path)},
            "started_at": None,
            "finished_at": None,
            "result_receipt": None,
            "error": None,
        }
    )
    second_revision["revision_job"] = second_job
    first_saved["revisions"].append(second_revision)
    feedback.write_text(json.dumps(first_saved, ensure_ascii=False), encoding="utf-8")

    assert run_revision_job(
        pending_revision_jobs(tmp_path)[0],
        agent_runner=agent,
        output_verifier=_fixture_verifier,
        trusted_apply=_fixture_trusted_apply,
    )
    second_saved = json.loads(feedback.read_text(encoding="utf-8"))
    saved_second_job = second_saved["revisions"][-1]["revision_job"]
    assert saved_second_job["status"] == "succeeded"
    second_receipt = json.loads(
        (episode / "highlights/review" / saved_second_job["result_receipt"]).read_text(
            encoding="utf-8"
        )
    )
    assert second_receipt["promoted_broll_assets"] == []
    assert second_receipt["trusted_handoff_assets"] == []
    assert pointer.read_bytes() == pointer_payload


@pytest.mark.parametrize("failure", ["tamper", "foreign", "missing"])
def test_acquisition_handoff_rejects_tamper_foreign_path_and_missing_media(
    monkeypatch, tmp_path, failure
):
    episode, _manifest, _feedback = _write_queued_job(tmp_path)
    acquisition, sources = _write_acquisition_source(tmp_path / "acquisition")
    monkeypatch.setattr(
        "agents.brook.script_video.highlight_broll.probe_stock_video",
        lambda _path: {"duration_seconds": 2.0, "video_streams": [{}]},
    )
    first = next(iter(sources.values()))
    if failure == "tamper":
        (acquisition.parent / first["filename"]).write_bytes(b"tampered")
    elif failure == "foreign":
        first["filename"] = "../foreign.mp4"
        acquisition.write_text(json.dumps(sources), encoding="utf-8")
    else:
        (acquisition.parent / first["filename"]).unlink()

    with pytest.raises(RuntimeError):
        prepare_trusted_asset_handoff(episode, acquisition, apply=False)


def test_failed_worker_restores_previous_preview_and_manifest_but_keeps_feedback(tmp_path):
    _, manifest, feedback = _write_queued_job(tmp_path)
    original_manifest = manifest.read_bytes()
    original = json.loads(manifest.read_text(encoding="utf-8"))
    preview = Path(original["cuts"][0]["artifacts"]["preview"]["path"])
    original_preview = preview.read_bytes()

    def broken_agent(context: dict) -> subprocess.CompletedProcess[str]:
        Path(context["manifest_path"]).write_text("broken", encoding="utf-8")
        preview.write_bytes(b"broken-preview")
        return subprocess.CompletedProcess(["fixture-agent"], 1, "", "render failed")

    assert not run_revision_job(pending_revision_jobs(tmp_path)[0], agent_runner=broken_agent)

    assert manifest.read_bytes() == original_manifest
    assert preview.read_bytes() == original_preview
    audit = json.loads(feedback.read_text(encoding="utf-8"))
    revision = audit["revisions"][0]
    assert revision["overall_feedback"]["value-L01"].endswith("保留開場。")
    assert revision["revision_job"]["status"] == "failed"
    assert "render failed" in revision["revision_job"]["error"]


def test_verifier_failure_rolls_back_promoted_sidefiles_recipes_visuals_and_assets(tmp_path):
    episode, manifest, feedback = _write_queued_job(tmp_path)
    review_cut = episode / "highlights" / "review" / "value-L01"
    tighten = episode / "highlights" / "tighten" / "value-L01_titles.json"
    visuals = episode / "highlights" / "stills" / "value-L01-visuals"
    assets = episode / "assets" / "broll"
    originals = {
        "manifest": manifest.read_bytes(),
        "preview": (review_cut / "長1_preview.mp4").read_bytes(),
        "recipe": tighten.read_bytes(),
        "visual": (visuals / "old.png").read_bytes(),
        "asset": (assets / "existing.mp4").read_bytes(),
    }
    audit = json.loads(feedback.read_text(encoding="utf-8"))
    audit["revisions"][0]["revision_job"]["trusted_asset_sources"] = {
        "job-new": {
            "filename": "job-new.mp4",
            "bytes": len(b"new-asset"),
            "sha256": hashlib.sha256(b"new-asset").hexdigest(),
            "provenance": {
                "source_url": "https://example.test/video/42",
                "acquired_at": "2026-08-22T12:00:00+08:00",
                "license_id": "fixture-license",
            },
        }
    }
    feedback.write_text(json.dumps(audit, ensure_ascii=False), encoding="utf-8")

    def agent(context: dict) -> subprocess.CompletedProcess[str]:
        output = Path(context["output_root"])
        (output / "tighten").mkdir()
        (output / "tighten" / "value-L01_titles.json").write_text(
            '{"new": true}', encoding="utf-8"
        )
        output_assets = output / "assets" / "broll"
        output_assets.mkdir(parents=True)
        (output_assets / "job-new.mp4").write_bytes(b"new-asset")
        return subprocess.CompletedProcess(["fixture-agent"], 0, "done", "")

    def trusted(context: dict) -> dict:
        (review_cut / "長1_preview.mp4").write_bytes(b"new-preview")
        (review_cut / "new-sidefile.png").write_bytes(b"new-sidefile")
        (visuals / "new.png").write_bytes(b"new-visual")
        (episode / "highlights/materialization").mkdir(parents=True)
        (episode / "highlights/materialization/value-L01.json").write_text(
            '{"new":true}', encoding="utf-8"
        )
        (episode / "highlights/tighten/value-L01_broll_materialization.json").write_text(
            '{"new":true}', encoding="utf-8"
        )
        (episode / "highlights/review/finished_review_component_identity.v2.json").write_text(
            '{"new":true}', encoding="utf-8"
        )
        return _fixture_trusted_apply(context)

    assert not run_revision_job(
        pending_revision_jobs(tmp_path)[0],
        agent_runner=agent,
        output_verifier=lambda _: (_ for _ in ()).throw(RuntimeError("fresh verifier failed")),
        trusted_apply=trusted,
    )
    assert manifest.read_bytes() == originals["manifest"]
    assert (review_cut / "長1_preview.mp4").read_bytes() == originals["preview"]
    assert not (review_cut / "new-sidefile.png").exists()
    assert tighten.read_bytes() == originals["recipe"]
    assert (visuals / "old.png").read_bytes() == originals["visual"]
    assert not (visuals / "new.png").exists()
    assert (assets / "existing.mp4").read_bytes() == originals["asset"]
    assert not (assets / "job-new.mp4").exists()
    assert not (episode / "highlights/materialization/value-L01.json").exists()
    assert not (
        episode / "highlights/review/finished_review_component_identity.v2.json"
    ).exists()


def test_resolve_abi_system_exit_is_contained_and_rolls_back_promoted_inputs(
    monkeypatch, tmp_path
):
    episode, manifest, feedback = _write_queued_job(tmp_path)
    old_manifest = manifest.read_bytes()
    old_recipe = (episode / "highlights/tighten/value-L01_titles.json").read_bytes()
    monkeypatch.setattr(
        "scripts.finished_review_watcher._ResolveTimelineTransaction.begin",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(SystemExit(1)),
    )

    assert not run_revision_job(
        pending_revision_jobs(tmp_path)[0],
        agent_runner=_successful_agent,
        output_verifier=_fixture_verifier,
    )
    saved = json.loads(feedback.read_text(encoding="utf-8"))["revisions"][-1][
        "revision_job"
    ]
    assert saved["status"] == "failed"
    assert "SystemExit" in saved["error"] or "Resolve" in saved["error"]
    assert manifest.read_bytes() == old_manifest
    assert (episode / "highlights/tighten/value-L01_titles.json").read_bytes() == old_recipe


def test_failed_cleanly_rolled_back_job_can_be_explicitly_retried(tmp_path):
    episode, _manifest, feedback = _write_queued_job(tmp_path)

    def failed_agent(_context: dict) -> subprocess.CompletedProcess[str]:
        return subprocess.CompletedProcess(["fixture-agent"], 2, "", "CLI parser failed")

    assert not run_revision_job(
        pending_revision_jobs(tmp_path)[0], agent_runner=failed_agent
    )
    failed = json.loads(feedback.read_text(encoding="utf-8"))["revisions"][-1][
        "revision_job"
    ]
    assert failed["status"] == "failed"
    request_id = failed["request_id"]

    dry_run = retry_failed_revision_job(
        tmp_path, episode_id=episode.name, request_id=request_id, apply=False
    )
    assert dry_run["status"] == "would_retry"
    assert pending_revision_jobs(tmp_path) == []

    applied = retry_failed_revision_job(
        tmp_path, episode_id=episode.name, request_id=request_id, apply=True
    )
    assert applied["status"] == "queued"
    queued = pending_revision_jobs(tmp_path)
    assert len(queued) == 1
    assert queued[0]["request_id"] == request_id

    assert run_revision_job(
        queued[0],
        agent_runner=_successful_agent,
        output_verifier=_fixture_verifier,
        trusted_apply=_fixture_trusted_apply,
    )
    saved = json.loads(feedback.read_text(encoding="utf-8"))["revisions"][-1][
        "revision_job"
    ]
    assert saved["status"] == "succeeded"
    assert saved["attempt"] == 2
    assert saved["previous_result_receipts"]
    assert saved["result_receipt"].startswith(f"revisions/{request_id}/attempts/2/")


def test_legacy_v1_preflight_attempt_zero_can_retry_without_fabricated_receipt(
    monkeypatch, tmp_path
):
    episode, _manifest, feedback = _write_queued_job(
        tmp_path, manifest_schema="nakama.finished_cut_review_manifest.v1"
    )

    class Master:
        def identity(self):
            return {"contract": "podcast-editorial-master-v1", "content_hash": "a" * 64}

    monkeypatch.setattr(
        "scripts.build_finished_review_manifest._open_master", lambda _episode: Master()
    )
    monkeypatch.setattr(
        "scripts.build_finished_review_manifest._approved_inventory",
        lambda _episode, _identity: {"value-L01": "long"},
    )
    audit = json.loads(feedback.read_text(encoding="utf-8"))
    job = audit["revisions"][-1]["revision_job"]
    job.update(
        {
            "status": "failed",
            "attempt": 0,
            "result_receipt": None,
            "error": "revision output manifest schema is invalid",
        }
    )
    feedback.write_text(json.dumps(audit, ensure_ascii=False), encoding="utf-8")

    dry = retry_failed_revision_job(
        tmp_path,
        episode_id=episode.name,
        request_id=job["request_id"],
        apply=False,
    )
    assert dry["status"] == "would_retry"
    assert dry["previous_attempt"] == 0
    assert dry["previous_result_receipt"] is None
    assert dry["retry_proof"] == "preflight_failed_before_backup_or_agent"
    assert dry["rollback_verified"] is True
    assert retry_failed_revision_job(
        tmp_path,
        episode_id=episode.name,
        request_id=job["request_id"],
        apply=True,
    )["status"] == "queued"
    queued = pending_revision_jobs(tmp_path)
    assert len(queued) == 1
    assert queued[0]["job"]["attempt"] == 0


def test_failed_job_without_queue_bound_registry_is_permanently_non_retryable(tmp_path):
    episode, _manifest, feedback = _write_queued_job(tmp_path)
    audit = json.loads(feedback.read_text(encoding="utf-8"))
    job = audit["revisions"][-1]["revision_job"]
    job.update({"status": "failed", "attempt": 0, "result_receipt": None})
    del job["source_registry_sha256"]
    before = json.dumps(audit, ensure_ascii=False)
    feedback.write_text(before, encoding="utf-8")

    for apply in (False, True):
        with pytest.raises(RuntimeError, match="permanently non-retryable"):
            retry_failed_revision_job(
                tmp_path,
                episode_id=episode.name,
                request_id=job["request_id"],
                apply=apply,
            )
        assert feedback.read_text(encoding="utf-8") == before


def test_retry_resumes_same_immutable_visual_request_but_isolates_attempt_files(tmp_path):
    episode, _manifest, feedback = _write_queued_job(tmp_path)
    visual_paths = []
    agent_paths = []

    def agent(context):
        agent_paths.append(Path(context["request_path"]))
        return _successful_agent(context)

    def visual(context):
        visual_path = Path(context["visual_request_path"])
        visual_paths.append(visual_path)
        if len(visual_paths) == 1:
            pending = episode / "highlights/visual-pipeline/value-L01/PENDING.fixture"
            pending.parent.mkdir(parents=True, exist_ok=True)
            pending.write_text(_sha(visual_path), encoding="utf-8")
            raise RuntimeError("old visual CLI failed after PENDING")
        pending = episode / "highlights/visual-pipeline/value-L01/PENDING.fixture"
        assert pending.read_text(encoding="utf-8") == _sha(visual_path)
        return _fixture_visual_pipeline_runner(context)

    work = pending_revision_jobs(tmp_path)[0]
    assert not run_revision_job(work, agent_runner=agent, visual_pipeline_runner=visual)
    failed = json.loads(feedback.read_text(encoding="utf-8"))["revisions"][-1][
        "revision_job"
    ]
    retry_failed_revision_job(
        tmp_path, episode_id=episode.name, request_id=failed["request_id"], apply=True
    )
    assert run_revision_job(
        pending_revision_jobs(tmp_path)[0],
        agent_runner=agent,
        visual_pipeline_runner=visual,
        output_verifier=_fixture_verifier,
        trusted_apply=_fixture_trusted_apply,
    )
    assert visual_paths == [visual_paths[0], visual_paths[0]]
    assert visual_paths[0] == feedback.parent / "revisions" / failed["request_id"] / "request.json"
    assert agent_paths[0] == visual_paths[0]
    assert agent_paths[1] == visual_paths[0].parent / "attempts/2/request.json"
    assert agent_paths[0].parent != agent_paths[1].parent


def test_explicit_retry_migrates_prepatch_visual_request_only_after_real_pending_lineage(
    monkeypatch, tmp_path
):
    from agents.brook.script_video.highlight_visual_pipeline import (
        init_visual_work_packet,
        visual_pipeline_status,
    )
    from tests.brook.script_video.test_highlight_visual_pipeline import _episode

    episode, master = _episode(tmp_path)
    _episode, _manifest, feedback = _write_queued_job(
        tmp_path, episode_dir=episode
    )
    monkeypatch.setattr("scripts.run_short_broll._open_editorial_master", lambda _: master)
    initialized = []

    def real_pending_then_fail(context):
        work = init_visual_work_packet(
            episode,
            cut_id="value-L01",
            revision_request=Path(context["visual_request_path"]),
            editorial_master=master,
        )
        initialized.append(work.document["revision_id"])
        raise RuntimeError("pre-patch visual CLI failed after PENDING")

    assert not run_revision_job(
        pending_revision_jobs(tmp_path)[0],
        agent_runner=_successful_agent,
        visual_pipeline_runner=real_pending_then_fail,
    )
    assert visual_pipeline_status(
        episode, cut_id="value-L01", editorial_master=master
    )["status"] == "awaiting_director"

    audit = json.loads(feedback.read_text(encoding="utf-8"))
    job = audit["revisions"][-1]["revision_job"]
    del job["visual_request_sha256"]
    feedback.write_text(json.dumps(audit, ensure_ascii=False), encoding="utf-8")
    before = feedback.read_bytes()

    dry = retry_failed_revision_job(
        tmp_path,
        episode_id=episode.name,
        request_id=job["request_id"],
        apply=False,
    )
    assert dry["status"] == "would_bind_visual_request"
    assert dry["visual_request_migration"]["pending_revisions"] == {
        "value-L01": initialized[0]
    }
    assert feedback.read_bytes() == before

    pending_path = episode / "highlights/visual-pipeline/value-L01/PENDING.json"
    pristine_pending = pending_path.read_bytes()
    tampered_pending = json.loads(pristine_pending.decode("utf-8"))
    tampered_pending["content_hash"] = "0" * 64
    pending_path.write_text(json.dumps(tampered_pending), encoding="utf-8")
    with pytest.raises(RuntimeError, match="PENDING lineage"):
        retry_failed_revision_job(
            tmp_path,
            episode_id=episode.name,
            request_id=job["request_id"],
            apply=True,
        )
    assert feedback.read_bytes() == before
    pending_path.write_bytes(pristine_pending)

    applied = retry_failed_revision_job(
        tmp_path,
        episode_id=episode.name,
        request_id=job["request_id"],
        apply=True,
    )
    assert applied["status"] == "queued"
    saved = json.loads(feedback.read_text(encoding="utf-8"))["revisions"][-1][
        "revision_job"
    ]
    assert saved["visual_request_sha256"] == _sha(
        feedback.parent / "revisions" / job["request_id"] / "request.json"
    )
    assert saved["visual_request_migration"]["contract"] == (
        "finished-cut-visual-request-retry-migration-v1"
    )


def test_retry_rejects_tampered_root_visual_request_before_agent_or_resolve(tmp_path):
    episode, _manifest, feedback = _write_queued_job(tmp_path)

    def pending_then_fail(context):
        assert Path(context["visual_request_path"]) == Path(context["request_path"])
        raise RuntimeError("visual pending then old CLI failed")

    assert not run_revision_job(
        pending_revision_jobs(tmp_path)[0],
        agent_runner=_successful_agent,
        visual_pipeline_runner=pending_then_fail,
    )
    failed = json.loads(feedback.read_text(encoding="utf-8"))["revisions"][-1][
        "revision_job"
    ]
    root_request = feedback.parent / "revisions" / failed["request_id"] / "request.json"
    retry_failed_revision_job(
        tmp_path, episode_id=episode.name, request_id=failed["request_id"], apply=True
    )
    root_request.write_text('{"tampered":true}', encoding="utf-8")
    calls = []

    assert not run_revision_job(
        pending_revision_jobs(tmp_path)[0],
        agent_runner=lambda _context: calls.append("agent"),
        visual_pipeline_runner=lambda _context: calls.append("visual"),
        trusted_apply=lambda _context: calls.append("resolve"),
    )
    assert calls == []
    saved = json.loads(feedback.read_text(encoding="utf-8"))["revisions"][-1][
        "revision_job"
    ]
    assert "immutable visual revision request" in saved["error"]


def test_default_visual_runner_uses_root_request_not_attempt_request(monkeypatch, tmp_path):
    root_request = tmp_path / "request.json"
    attempt_request = tmp_path / "attempts/2/request.json"
    root_request.write_text('{"root":true}', encoding="utf-8")
    attempt_request.parent.mkdir(parents=True)
    attempt_request.write_text('{"attempt":2}', encoding="utf-8")
    seen = {}
    selection = SimpleNamespace(
        materializations=(),
        lineage=lambda: {"revision_id": "same-revision", "content_hash": "a" * 64},
    )

    def run(_episode, **kwargs):
        seen.update(kwargs)
        return selection

    monkeypatch.setattr(
        "scripts.podcast_highlight_visual_orchestrator.run_visual_pipeline", run
    )
    monkeypatch.setattr("scripts.run_short_broll._open_editorial_master", lambda _: object())
    monkeypatch.setattr(
        "scripts.run_short_broll.emit_audited_recipe",
        lambda *_args, output_dir, **_kwargs: output_dir / "value-L01_broll.json",
    )
    monkeypatch.setattr(
        "scripts.run_short_titles.emit_audited_recipe",
        lambda *_args, output_dir, **_kwargs: output_dir / "value-L01_titles.json",
    )

    result = _run_visual_pipeline_revision(
        {
            "episode_dir": str(tmp_path),
            "output_root": str(tmp_path / "output"),
            "request_path": str(attempt_request),
            "visual_request_path": str(root_request),
            "request": {"requested_cut_ids": ["value-L01"]},
        }
    )

    assert seen["revision_request"] == root_request
    assert seen["resume"] is True
    assert result["cuts"][0]["revision_id"] == "same-revision"


def test_failed_job_retry_rejects_artifact_drift_without_requeueing(tmp_path):
    episode, manifest, feedback = _write_queued_job(tmp_path)

    def failed_agent(_context: dict) -> subprocess.CompletedProcess[str]:
        return subprocess.CompletedProcess(["fixture-agent"], 2, "", "CLI parser failed")

    assert not run_revision_job(
        pending_revision_jobs(tmp_path)[0], agent_runner=failed_agent
    )
    manifest_payload = json.loads(manifest.read_text(encoding="utf-8"))
    Path(manifest_payload["cuts"][0]["artifacts"]["preview"]["path"]).write_bytes(
        b"drifted-after-clean-rollback"
    )
    failed = json.loads(feedback.read_text(encoding="utf-8"))["revisions"][-1][
        "revision_job"
    ]

    with pytest.raises(RuntimeError, match="rollback is not clean"):
        retry_failed_revision_job(
            tmp_path,
            episode_id=episode.name,
            request_id=failed["request_id"],
            apply=True,
        )
    saved = json.loads(feedback.read_text(encoding="utf-8"))["revisions"][-1][
        "revision_job"
    ]
    assert saved["status"] == "failed"
    assert pending_revision_jobs(tmp_path) == []


def test_orphan_running_recovery_restores_partial_promotion_before_attempt_three(tmp_path):
    episode, _manifest, feedback = _write_queued_job(tmp_path)
    old_recipe = (episode / "highlights/tighten/value-L01_titles.json").read_bytes()

    def failed_agent(_context: dict) -> subprocess.CompletedProcess[str]:
        return subprocess.CompletedProcess(["fixture-agent"], 2, "", "first failure")

    assert not run_revision_job(
        pending_revision_jobs(tmp_path)[0], agent_runner=failed_agent
    )
    first_failed = json.loads(feedback.read_text(encoding="utf-8"))["revisions"][-1][
        "revision_job"
    ]
    request_id = first_failed["request_id"]
    assert retry_failed_revision_job(
        tmp_path, episode_id=episode.name, request_id=request_id, apply=True
    )["status"] == "queued"

    request_root = episode / "highlights/review/revisions" / request_id
    attempt_two = request_root / "attempts/2"
    attempt_two.mkdir(parents=True)
    shutil.copytree(request_root / "before", attempt_two / "before")
    shutil.copy2(request_root / "request.json", attempt_two / "request.json")
    (attempt_two / "output").mkdir()
    (attempt_two / "agent.stdout.log").write_text("staged\n", encoding="utf-8")
    (attempt_two / "agent.stderr.log").write_text("", encoding="utf-8")
    (episode / "highlights/tighten/value-L01_titles.json").write_text(
        '{"partially_promoted": true}', encoding="utf-8"
    )
    audit = json.loads(feedback.read_text(encoding="utf-8"))
    job = audit["revisions"][-1]["revision_job"]
    job.update(
        {
            "status": "running",
            "attempt": 2,
            "worker_pid": 999_999_999,
            "worker_session_id": "orphan-session",
            "result_receipt": None,
        }
    )
    feedback.write_text(json.dumps(audit, ensure_ascii=False), encoding="utf-8")

    with pytest.raises(RuntimeError, match="still active"):
        recover_running_revision_job(
            tmp_path,
            episode_id=episode.name,
            request_id=request_id,
            apply=True,
            process_probe=lambda _pid, _session: True,
            resolve_probe=lambda *_args: {"revision_timelines": []},
        )
    with pytest.raises(RuntimeError, match="Timelines still exist"):
        recover_running_revision_job(
            tmp_path,
            episode_id=episode.name,
            request_id=request_id,
            apply=True,
            process_probe=lambda _pid, _session: False,
            resolve_probe=lambda *_args: {
                "revision_timelines": ["derived__revision_work__abc123"]
            },
        )

    dry = recover_running_revision_job(
        tmp_path,
        episode_id=episode.name,
        request_id=request_id,
        apply=False,
        process_probe=lambda _pid, _session: False,
        resolve_probe=lambda *_args: {"revision_timelines": []},
    )
    assert dry["status"] == "would_recover"
    assert dry["current_matches_before"] is False
    assert (episode / "highlights/tighten/value-L01_titles.json").read_bytes() != old_recipe

    recovered = recover_running_revision_job(
        tmp_path,
        episode_id=episode.name,
        request_id=request_id,
        apply=True,
        process_probe=lambda _pid, _session: False,
        resolve_probe=lambda *_args: {"revision_timelines": []},
    )
    assert recovered["status"] == "failed"
    assert (episode / "highlights/tighten/value-L01_titles.json").read_bytes() == old_recipe
    saved = json.loads(feedback.read_text(encoding="utf-8"))["revisions"][-1][
        "revision_job"
    ]
    assert saved["status"] == "failed"
    assert "recovery.json" in saved["result_receipt"]
    assert retry_failed_revision_job(
        tmp_path, episode_id=episode.name, request_id=request_id, apply=True
    )["status"] == "queued"
    queued = pending_revision_jobs(tmp_path)[0]
    assert queued["job"]["attempt"] == 2
    assert not (
        episode / "highlights/tighten/value-L01_broll_materialization.json"
    ).exists()


def test_agent_command_writes_only_episode_local_job_output(monkeypatch, tmp_path):
    job_dir = tmp_path / "episode/highlights/review/revisions/request-1"
    job_dir.mkdir(parents=True)
    request = job_dir / "request.json"
    request.write_text("{}", encoding="utf-8")
    captured: dict = {}

    monkeypatch.setattr(
        "scripts.finished_review_watcher._codex_command", lambda: "codex-fixture"
    )

    def fake_run(command, **kwargs):
        captured["command"] = command
        captured["cwd"] = kwargs["cwd"]
        captured["prompt"] = kwargs["input"]
        return subprocess.CompletedProcess(command, 0, "", "")

    monkeypatch.setattr("scripts.finished_review_watcher.subprocess.run", fake_run)
    dispatch_revision_agent(
        {
            "request_path": str(request),
            "job_dir": str(job_dir),
            "output_root": str(job_dir / "output"),
            "episode_dir": "G:/Footages/episode",
            "review_dir": "G:/Footages/episode/highlights/review",
            "manifest_path": "G:/Footages/episode/highlights/review/manifest.json",
            "allowed_changes": {"requested_cut_ids": ["value-L01"]},
        }
    )

    assert "--add-dir" not in captured["command"]
    assert "--approve-for-me" not in captured["command"]
    assert "--dangerously-bypass-approvals-and-sandbox" not in captured["command"]
    assert captured["command"][captured["command"].index("--sandbox") + 1] == "workspace-write"
    assert Path(captured["cwd"]) == job_dir / "output"
    assert str(job_dir / "output") in captured["prompt"]
    assert "Editorial Master" in captured["prompt"]
    assert "其他 cut" in captured["prompt"]


def test_default_verifier_rejects_forged_three_broll_events_without_plan_receipt(
    monkeypatch, tmp_path
):
    episode, manifest, feedback = _write_queued_job(tmp_path)
    review_cut = episode / "highlights" / "review" / "value-L01"
    old_preview = (review_cut / "長1_preview.mp4").read_bytes()
    old_manifest = manifest.read_bytes()
    master_identity = {"contract": "podcast-editorial-master-v1", "content_hash": "a" * 64}

    class Master:
        def identity(self):
            return master_identity

    monkeypatch.setattr(
        "scripts.build_finished_review_manifest._open_master", lambda _: Master()
    )
    monkeypatch.setattr(
        "scripts.build_finished_review_manifest._approved_inventory",
        lambda _episode, _identity: {"value-L01": "long"},
    )

    def forged_agent(context: dict) -> subprocess.CompletedProcess[str]:
        output = Path(context["output_root"])
        output_cut = output / "review" / "value-L01"
        output_cut.mkdir(parents=True)
        (output_cut / "長1_preview.mp4").write_bytes(b"forged-new-preview")
        (output_cut / "subs.srt").write_text(
            "1\n00:00:00,000 --> 00:00:01,000\n偽造\n", encoding="utf-8"
        )
        events = [
            {
                "type": "video",
                "asset_category": "stock_video",
                "slug": f"forged-{index}",
                "t0": 5.0 + index * 10,
                "t1": 8.0 + index * 10,
            }
            for index in range(3)
        ]
        (output_cut / "events.json").write_text(
            json.dumps(
                {
                    "timeline": "長1 - value-L01（緊·導播）",
                    "duration_sec": 60.0,
                    "preview": "長1_preview.mp4",
                    "editorial_master_lineage": master_identity,
                    "stock_video_lineage": {
                        "contract": "podcast-long-highlight-stock-video-v1",
                        "cut_id": "value-L01",
                        "content_hash": "f" * 64,
                        "stock_video_count": 3,
                    },
                    "events": events,
                }
            ),
            encoding="utf-8",
        )
        assets = output / "assets" / "broll"
        assets.mkdir(parents=True)
        for index in range(3):
            (assets / f"forged-{index}.mp4").write_bytes(f"asset-{index}".encode())
        return subprocess.CompletedProcess(["fixture-agent"], 0, "done", "")

    assert not run_revision_job(
        pending_revision_jobs(tmp_path)[0], agent_runner=forged_agent
    )
    assert manifest.read_bytes() == old_manifest
    assert (review_cut / "長1_preview.mp4").read_bytes() == old_preview
    assert not any((episode / "assets/broll").glob("forged-*.mp4"))
    audit = json.loads(feedback.read_text(encoding="utf-8"))
    job = audit["revisions"][-1]["revision_job"]
    assert job["status"] == "failed"
    assert job["error"] == "agent output contains forbidden paths: review"


def test_agent_self_reported_materialization_receipt_is_rejected(tmp_path):
    _write_queued_job(tmp_path)
    trusted_calls: list[str] = []

    def agent(context: dict) -> subprocess.CompletedProcess[str]:
        tighten = Path(context["output_root"]) / "tighten"
        tighten.mkdir(parents=True)
        (tighten / "value-L01_broll_materialization.json").write_text(
            '{"status":"success"}', encoding="utf-8"
        )
        return subprocess.CompletedProcess(["fixture-agent"], 0, "done", "")

    assert not run_revision_job(
        pending_revision_jobs(tmp_path)[0],
        agent_runner=agent,
        trusted_apply=lambda context: trusted_calls.append(context["request_id"]),
    )
    assert trusted_calls == []


def test_agent_new_video_without_request_bound_provenance_is_rejected(tmp_path):
    _write_queued_job(tmp_path)
    trusted_calls: list[str] = []

    def agent(context: dict) -> subprocess.CompletedProcess[str]:
        assets = Path(context["output_root"]) / "assets/broll"
        assets.mkdir(parents=True)
        (assets / "self-reported.mp4").write_bytes(b"not-authoritative")
        return subprocess.CompletedProcess(["fixture-agent"], 0, "done", "")

    assert not run_revision_job(
        pending_revision_jobs(tmp_path)[0],
        agent_runner=agent,
        trusted_apply=lambda context: trusted_calls.append(context["request_id"]),
    )
    assert trusted_calls == []


def test_timeline_transaction_rolls_back_partial_mutation_to_original_uid(
    monkeypatch, tmp_path
):
    class Timeline:
        counter = 0

        def __init__(self, name: str, uid: str | None = None):
            Timeline.counter += 1
            self.name = name
            self.uid = uid or f"uid-{Timeline.counter}"
            self.project = None

        def GetName(self):
            return self.name

        def SetName(self, name):
            self.name = name
            return True

        def GetUniqueId(self):
            return self.uid

        def DuplicateTimeline(self, name):
            duplicate = Timeline(name)
            duplicate.project = self.project
            self.project.timelines.append(duplicate)
            return duplicate

    class Pool:
        def __init__(self, project):
            self.project = project

        def DeleteTimelines(self, timelines):
            self.project.timelines = [
                timeline for timeline in self.project.timelines if timeline not in timelines
            ]
            return True

    class Project:
        def __init__(self):
            original = Timeline("長1 - value-L01（緊·導播）", "original-uid")
            original.project = self
            self.timelines = [original]
            self.pool = Pool(self)

        def GetName(self):
            return "20260805 林之晨"

        def GetTimelineCount(self):
            return len(self.timelines)

        def GetTimelineByIndex(self, index):
            return self.timelines[index - 1]

        def GetMediaPool(self):
            return self.pool

    class Manager:
        def __init__(self, project):
            self.project = project

        def GetCurrentProject(self):
            return self.project

        def LoadProject(self, _name):
            return self.project

        def SaveProject(self):
            return True

    project = Project()
    manager = Manager(project)
    resolve = type("Resolve", (), {"GetProjectManager": lambda _self: manager})()
    monkeypatch.setattr(
        "scripts.build_resolve_project.connect_resolve", lambda: resolve
    )
    episode = tmp_path / "20260805 林之晨"
    transaction = _ResolveTimelineTransaction.begin(
        episode,
        {"value-L01": "長1 - value-L01（緊·導播）"},
        "finished-revision-abc123",
    )
    working = next(
        timeline
        for timeline in project.timelines
        if timeline.GetName() == "長1 - value-L01（緊·導播）"
    )
    working.mutated = True
    receipt = transaction.rollback()
    canonical = [
        timeline
        for timeline in project.timelines
        if timeline.GetName() == "長1 - value-L01（緊·導播）"
    ]
    assert receipt["rolled_back"] is True
    assert len(canonical) == 1
    assert canonical[0].GetUniqueId() == "original-uid"
    assert not hasattr(canonical[0], "mutated")

    save_calls = 0

    def save_with_abi_exit():
        nonlocal save_calls
        save_calls += 1
        if save_calls == 1:
            raise SystemExit(77)
        return True

    manager.SaveProject = save_with_abi_exit
    with pytest.raises(SystemExit, match="77"):
        _ResolveTimelineTransaction.begin(
            episode,
            {"value-L01": "長1 - value-L01（緊·導播）"},
            "finished-revision-systemexit77",
        )
    canonical = [
        timeline
        for timeline in project.timelines
        if timeline.GetName() == "長1 - value-L01（緊·導播）"
    ]
    assert len(canonical) == 1
    assert canonical[0].GetUniqueId() == "original-uid"
    assert all("__revision_" not in timeline.GetName() for timeline in project.timelines)


def test_default_trusted_apply_calls_existing_pipeline_before_commit(monkeypatch, tmp_path):
    episode = tmp_path / "20260805 林之晨"
    calls: list[str] = []

    class Transaction:
        def prepare(self):
            calls.append("prepare")
            return {"prepared": True}

        def rollback(self):
            calls.append("rollback")
            return {"rolled_back": True}

    monkeypatch.setattr(
        _ResolveTimelineTransaction,
        "begin",
        lambda *_args, **_kwargs: Transaction(),
    )
    monkeypatch.setattr(
        "scripts.run_short_director.direct",
        lambda *_args: calls.append("director") or {"status": "directed"},
    )
    monkeypatch.setattr(
        "scripts.run_short_broll.validate_plan",
        lambda *_args: calls.append("broll-preflight") or {"status": "plan-valid"},
    )
    monkeypatch.setattr(
        "scripts.run_short_titles.validate_plan",
        lambda *_args: calls.append("titles-preflight") or {"status": "plan-valid"},
    )
    monkeypatch.setattr(
        "scripts.run_short_broll.apply",
        lambda *_args: calls.append("broll") or {"status": "brolled"},
    )
    monkeypatch.setattr(
        "scripts.run_short_titles.apply",
        lambda *_args: calls.append("titles") or {"status": "titled"},
    )
    monkeypatch.setattr(
        "scripts.run_short_review.build_packet",
        lambda *_args, **_kwargs: calls.append("review") or {"status": "ready"},
    )
    monkeypatch.setattr(
        "scripts.finished_review_watcher._verify_revision_output_acceptance",
        lambda *_args: calls.append("accept")
        or {"manifest_sha256": "b" * 64, "preview_sha256": {"value-L01": "c" * 64}},
    )

    result = _trusted_apply_revision(
        {
            "episode_dir": str(episode),
            "request_id": "finished-revision-abc123",
            "request": {"requested_cut_ids": ["value-L01"]},
            "allowed_changes": {
                "resolve_timelines": {
                    "value-L01": "長1 - value-L01（緊·導播）"
                }
            },
            "output_verifier": lambda _context: calls.append("verify")
            or {
                "approved": False,
                "manifest_path": str(
                    episode / "highlights/review/finished_review_manifest_current.json"
                ),
            },
        }
    )

    assert calls == [
        "broll-preflight",
        "titles-preflight",
        "director",
        "broll",
        "titles",
        "review",
        "verify",
        "accept",
        "prepare",
    ]
    assert result["status"] == "trusted_apply_succeeded"


def test_visual_preflight_failure_opens_no_resolve_transaction(monkeypatch, tmp_path):
    episode = tmp_path / "20260805 林之晨"
    transaction_calls: list[str] = []
    monkeypatch.setattr(
        "scripts.run_short_broll.validate_plan",
        lambda *_args: (_ for _ in ()).throw(SystemExit("Semantic Audit missing")),
    )
    monkeypatch.setattr(
        "scripts.run_short_titles.validate_plan",
        lambda *_args: (_ for _ in ()).throw(AssertionError("must stop at B-roll gate")),
    )
    monkeypatch.setattr(
        _ResolveTimelineTransaction,
        "begin",
        lambda *_args, **_kwargs: transaction_calls.append("begin"),
    )

    with pytest.raises(RuntimeError, match="Semantic Audit missing"):
        _trusted_apply_revision(
            {
                "episode_dir": str(episode),
                "request_id": "finished-revision-no-audit",
                "request": {"requested_cut_ids": ["value-L01"]},
                "allowed_changes": {
                    "resolve_timelines": {"value-L01": "長1 - value-L01（緊·導播）"}
                },
                "output_verifier": lambda _context: {},
            }
        )
    assert transaction_calls == []


def test_unchanged_output_rolls_back_before_timeline_commit(monkeypatch, tmp_path):
    episode, manifest, _feedback = _write_queued_job(tmp_path)
    calls: list[str] = []

    class Transaction:
        def prepare(self):
            calls.append("prepare")
            return {"prepared": True}

        def rollback(self):
            calls.append("rollback")
            return {"rolled_back": True, "restored_uid": "original-uid"}

    monkeypatch.setattr(
        _ResolveTimelineTransaction,
        "begin",
        lambda *_args, **_kwargs: Transaction(),
    )
    monkeypatch.setattr("scripts.run_short_director.direct", lambda *_args: {})
    monkeypatch.setattr("scripts.run_short_broll.validate_plan", lambda *_args: {})
    monkeypatch.setattr("scripts.run_short_titles.validate_plan", lambda *_args: {})
    monkeypatch.setattr("scripts.run_short_broll.apply", lambda *_args: {})
    monkeypatch.setattr("scripts.run_short_titles.apply", lambda *_args: {})
    monkeypatch.setattr(
        "scripts.run_short_review.build_packet", lambda *_args, **_kwargs: {}
    )
    payload = json.loads(manifest.read_text(encoding="utf-8"))
    preview = Path(payload["cuts"][0]["artifacts"]["preview"]["path"])
    context = {
        "episode_dir": str(episode),
        "review_dir": str(manifest.parent),
        "manifest_path": str(manifest),
        "request_id": "finished-revision-abc123",
        "request": {
            "requested_cut_ids": ["value-L01"],
            "source_manifest_sha256": _sha(manifest),
            "source_preview_sha256": {"value-L01": _sha(preview)},
        },
        "allowed_changes": {
            "resolve_timelines": {"value-L01": "長1 - value-L01（緊·導播）"}
        },
        "output_verifier": lambda _context: {
            "approved": False,
            "manifest_path": str(manifest),
        },
    }

    with pytest.raises(RuntimeError, match="did not rebuild"):
        _trusted_apply_revision(context)
    assert calls == ["rollback"]


def test_job_status_io_failure_rolls_back_prepared_timeline_and_filesystem(
    monkeypatch, tmp_path
):
    episode, manifest, feedback = _write_queued_job(tmp_path)
    original_manifest = manifest.read_bytes()
    preview = Path(
        json.loads(manifest.read_text(encoding="utf-8"))["cuts"][0]["artifacts"][
            "preview"
        ]["path"]
    )
    original_preview = preview.read_bytes()
    transaction = _ResolveTimelineTransaction(None, None, [])
    calls: list[str] = []
    transaction.rollback = lambda: calls.append("rollback") or {"rolled_back": True}
    transaction.finalize = lambda: calls.append("finalize") or {"committed": True}

    def trusted(context: dict) -> dict:
        result = _fixture_trusted_apply(context)
        result["timeline_transaction"] = {"prepared": True}
        result["_timeline_transaction_handle"] = transaction
        return result

    from scripts import finished_review_watcher as watcher

    real_update = watcher._update_job

    def fail_success_update(work, patch, **kwargs):
        if patch.get("status") == "succeeded":
            raise OSError("injected job status fsync failure")
        return real_update(work, patch, **kwargs)

    monkeypatch.setattr(watcher, "_update_job", fail_success_update)
    assert not run_revision_job(
        pending_revision_jobs(tmp_path)[0],
        agent_runner=_successful_agent,
        output_verifier=_fixture_verifier,
        trusted_apply=trusted,
    )
    assert calls == ["rollback"]
    assert manifest.read_bytes() == original_manifest
    assert preview.read_bytes() == original_preview
    audit = json.loads(feedback.read_text(encoding="utf-8"))
    assert audit["revisions"][0]["revision_job"]["status"] == "failed"
    assert "fsync failure" in audit["revisions"][0]["revision_job"]["error"]


def test_finalize_system_exit_preserves_durable_success_and_new_output(tmp_path):
    episode, manifest, feedback = _write_queued_job(tmp_path)
    original_manifest = manifest.read_bytes()
    preview = Path(
        json.loads(manifest.read_text(encoding="utf-8"))["cuts"][0]["artifacts"][
            "preview"
        ]["path"]
    )
    original_preview = preview.read_bytes()
    transaction = _ResolveTimelineTransaction(None, None, [])
    calls: list[str] = []
    transaction.rollback = lambda: calls.append("rollback") or {"rolled_back": True}

    def finalize():
        calls.append("finalize")
        raise SystemExit(91)

    transaction.finalize = finalize

    def trusted(context: dict) -> dict:
        result = _fixture_trusted_apply(context)
        result["timeline_transaction"] = {"prepared": True}
        result["_timeline_transaction_handle"] = transaction
        return result

    assert run_revision_job(
        pending_revision_jobs(tmp_path)[0],
        agent_runner=_successful_agent,
        output_verifier=_fixture_verifier,
        trusted_apply=trusted,
    )
    assert calls == ["finalize"]
    assert manifest.read_bytes() != original_manifest
    assert preview.read_bytes() != original_preview
    audit = json.loads(feedback.read_text(encoding="utf-8"))
    job = audit["revisions"][0]["revision_job"]
    assert job["status"] == "succeeded"
    assert job["output_manifest_sha256"] == _sha(manifest)
    receipt = json.loads(
        (episode / "highlights/review" / job["result_receipt"]).read_text(
            encoding="utf-8"
        )
    )
    cleanup = receipt["trusted_apply"]["timeline_transaction"]
    assert cleanup["backup_cleanup_pending"] is True
    assert "SystemExit: 91" in cleanup["backup_cleanup_error"]


def test_existing_broll_asset_drift_is_rejected_before_trusted_apply(tmp_path):
    episode, _manifest, feedback = _write_queued_job(tmp_path)
    provenance = {
        "source_url": "https://example.test/existing",
        "acquired_at": "2026-08-22T12:00:00+08:00",
        "license_id": "fixture-license",
    }
    existing = episode / "assets/broll/existing.mp4"
    audit = json.loads(feedback.read_text(encoding="utf-8"))
    audit["revisions"][0]["revision_job"]["trusted_asset_sources"] = {
        "existing": {
            "filename": existing.name,
            "bytes": existing.stat().st_size,
            "sha256": _sha(existing),
            "provenance": provenance,
        }
    }
    feedback.write_text(json.dumps(audit, ensure_ascii=False), encoding="utf-8")
    existing.write_bytes(b"drifted-after-request")
    trusted_calls: list[str] = []

    def agent(context: dict) -> subprocess.CompletedProcess[str]:
        tighten = Path(context["output_root"]) / "tighten"
        tighten.mkdir(parents=True)
        (tighten / "value-L01_broll.json").write_text(
            json.dumps(
                {
                    "items": [
                        {
                            "kind": "video",
                            "slug": "existing",
                            "t0": 1.0,
                            "t1": 2.0,
                            "provenance": provenance,
                        }
                    ]
                }
            ),
            encoding="utf-8",
        )
        return subprocess.CompletedProcess(["fixture-agent"], 0, "done", "")

    assert not run_revision_job(
        pending_revision_jobs(tmp_path)[0],
        agent_runner=agent,
        trusted_apply=lambda context: trusted_calls.append(context["request_id"]),
    )
    assert trusted_calls == []


def test_three_request_bound_assets_do_not_authorize_generic_agent_broll_plan(
    monkeypatch, tmp_path
):
    episode, _manifest, feedback = _write_queued_job(tmp_path)
    (episode / "highlights/tighten/value-L01_broll.json").write_text(
        '{"items":[]}', encoding="utf-8"
    )
    provenance_rows = {
        f"stock-{index}": {
            "source_url": f"https://example.test/stock/{index}",
            "acquired_at": "2026-08-22T12:00:00+08:00",
            "license_id": f"fixture-{index}",
        }
        for index in range(3)
    }
    sources = {}
    for slug, provenance in provenance_rows.items():
        raw = f"video-{slug}".encode()
        sources[slug] = {
            "filename": f"{slug}.mp4",
            "bytes": len(raw),
            "sha256": hashlib.sha256(raw).hexdigest(),
            "provenance": provenance,
        }
    audit = json.loads(feedback.read_text(encoding="utf-8"))
    audit["revisions"][0]["revision_job"]["trusted_asset_sources"] = sources
    feedback.write_text(json.dumps(audit, ensure_ascii=False), encoding="utf-8")
    monkeypatch.setattr(
        "agents.brook.script_video.highlight_broll.probe_stock_video",
        lambda _path: {"duration_seconds": 1.0, "video_streams": [{}]},
    )
    trusted_calls: list[str] = []

    def agent(context: dict) -> subprocess.CompletedProcess[str]:
        _successful_agent(context)
        output = Path(context["output_root"])
        assets = output / "assets/broll"
        assets.mkdir(parents=True)
        items = []
        for index, (slug, provenance) in enumerate(provenance_rows.items()):
            (assets / f"{slug}.mp4").write_bytes(f"video-{slug}".encode())
            items.append(
                {
                    "kind": "video",
                    "slug": slug,
                    "t0": float(index * 3),
                    "t1": float(index * 3 + 2),
                    "provenance": provenance,
                }
            )
        (output / "tighten/value-L01_broll.json").write_text(
            json.dumps({"items": items}), encoding="utf-8"
        )
        return subprocess.CompletedProcess(["fixture-agent"], 0, "done", "")

    def trusted(context: dict) -> dict:
        trusted_calls.append(context["request_id"])
        return _fixture_trusted_apply(context)

    assert not run_revision_job(
        pending_revision_jobs(tmp_path)[0],
        agent_runner=agent,
        output_verifier=_fixture_verifier,
        trusted_apply=trusted,
    )
    assert trusted_calls == []
    failed = json.loads(feedback.read_text(encoding="utf-8"))["revisions"][-1][
        "revision_job"
    ]
    assert failed["status"] == "failed"
    assert "generic revision agent authored semantic visual recipes" in failed["error"]
