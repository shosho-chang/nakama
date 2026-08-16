from __future__ import annotations

import argparse
import json
from pathlib import Path
from unittest.mock import Mock

import pytest
import yaml

from agents.brook.podcast_subtitles.handoff import open_verified_projection
from agents.brook.podcast_subtitles.module import (
    AcceptedGeneration,
    CreateRequest,
    NeedsReview,
    ProjectRequest,
    ResolveRequest,
)
from agents.brook.podcast_subtitles.profiles import HORIZONTAL_16X9
from agents.brook.script_video import pipeline
from agents.brook.script_video.srt_flattener import flatten_cues, parse_srt
from tests.agents.brook.podcast_subtitles.test_module import (
    _decision_for_created,
    _module,
)


def _project_fixture(root: Path, *, episode_id: str):
    module, source = _module(root)
    created = module.create(CreateRequest(episode_id=episode_id, source_audio=source))
    assert isinstance(created, NeedsReview)
    accepted = module.resolve(
        ResolveRequest(
            created.generation_id,
            (_decision_for_created(created, event_id="accept-script-video"),),
        )
    )
    assert isinstance(accepted, AcceptedGeneration)
    projected = module.project(ProjectRequest(accepted.generation_id, HORIZONTAL_16X9))
    return module, accepted, projected


def _fixture_factory(context):
    return _module(context.episode_root)[0]


def _real_fixture_loader(**kwargs):
    return open_verified_projection(**kwargs, factory=_fixture_factory)


def _episode_binding(root: Path, projected, accepted) -> dict:
    return {
        "schema_version": 1,
        "episode_root": str(root),
        "projection_id": projected.projection_id,
        "generation_id": accepted.generation_id,
        "projection_manifest_sha256": projected.manifest_sha256,
    }


def _write_script_episode(
    ep_dir: Path,
    *,
    episode_id: str,
    binding: dict | None,
) -> None:
    ep_dir.mkdir(parents=True)
    payload: dict = {"id": episode_id, "title": "Verified handoff fixture"}
    if binding is not None:
        payload["subtitle_v2_handoff"] = binding
    (ep_dir / "episode.yaml").write_text(
        yaml.safe_dump(payload, allow_unicode=True, sort_keys=False),
        encoding="utf-8",
    )


def _canned_storyboard_for_srt(srt_bytes: bytes) -> str:
    flat_text, _char_to_time, _ranges = flatten_cues(
        parse_srt(srt_bytes.decode("utf-8"))
    )
    anchor = flat_text[: min(12, len(flat_text))]
    beats = [
        {
            "beat_id": 1,
            "start_quote": anchor,
            "end_quote": anchor,
            "broll_decision": "none",
            "layout": "full_aroll",
            "broll": None,
            "status": {
                "text_approved": False,
                "render_status": "pending",
                "visual_approved": False,
            },
            "user_notes": [],
        }
    ]
    return "```yaml\n" + yaml.safe_dump(beats, allow_unicode=True) + "```"


def test_plan_uses_real_disk_verifier_and_records_exact_storyboard_provenance(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    episode_id = "episode-stage5-plan"
    subtitle_root = tmp_path / "subtitle"
    _module_instance, accepted, projected = _project_fixture(
        subtitle_root,
        episode_id=episode_id,
    )
    ep_dir = tmp_path / "script-video" / episode_id
    _write_script_episode(
        ep_dir,
        episode_id=episode_id,
        binding=_episode_binding(subtitle_root, projected, accepted),
    )
    assert not (ep_dir / "transcript.srt").exists()

    monkeypatch.setattr(pipeline, "_DATA_ROOT", ep_dir.parent)
    monkeypatch.setattr(
        "agents.brook.script_video.subtitle_handoff.open_verified_projection",
        _real_fixture_loader,
    )
    monkeypatch.setattr(
        "agents.brook.script_video.planner.ask",
        Mock(return_value=_canned_storyboard_for_srt(projected.srt_bytes)),
    )

    assert pipeline._cmd_plan(argparse.Namespace(episode=episode_id)) == 0

    provenance = json.loads(
        (ep_dir / "storyboard.provenance.json").read_text(encoding="utf-8")
    )
    assert provenance == {
        "schema_version": 1,
        "episode_id": episode_id,
        "projection_id": projected.projection_id,
        "generation_id": accepted.generation_id,
        "projection_sha256": projected.projection_sha256,
        "projection_manifest_sha256": projected.manifest_sha256,
        "quality_report_sha256": projected.quality_report_sha256,
        "srt_sha256": projected.srt_sha256,
        "canonical_hash": projected.manifest.canonical_hash,
        "profile_hash": projected.manifest.profile_hash,
        "token_sequence_hash": projected.manifest.token_sequence_hash,
        "output_hash": projected.manifest.output_hash,
    }


def test_bare_transcript_only_fails_before_verifier_and_planner(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    episode_id = "episode-bare-srt"
    ep_dir = tmp_path / episode_id
    _write_script_episode(ep_dir, episode_id=episode_id, binding=None)
    (ep_dir / "transcript.srt").write_text("legacy, unverified", encoding="utf-8")
    verifier = Mock(side_effect=AssertionError("verifier must not run without binding"))
    planner = Mock(side_effect=AssertionError("planner must not run without verification"))
    monkeypatch.setattr(pipeline, "_DATA_ROOT", tmp_path)
    monkeypatch.setattr(
        "agents.brook.script_video.subtitle_handoff.open_verified_projection",
        verifier,
    )
    monkeypatch.setattr("agents.brook.script_video.planner.plan_episode", planner)

    with pytest.raises(SystemExit, match="subtitle_v2_handoff"):
        pipeline._cmd_plan(argparse.Namespace(episode=episode_id))

    verifier.assert_not_called()
    planner.assert_not_called()


def test_tampered_verified_srt_fails_before_planner(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    episode_id = "episode-tampered-srt"
    subtitle_root = tmp_path / "subtitle"
    module, accepted, projected = _project_fixture(subtitle_root, episode_id=episode_id)
    ep_dir = tmp_path / "script-video" / episode_id
    _write_script_episode(
        ep_dir,
        episode_id=episode_id,
        binding=_episode_binding(subtitle_root, projected, accepted),
    )
    srt_path = module.store.root / "projections" / projected.projection_id / "transcript.srt"
    srt_path.write_bytes(srt_path.read_bytes() + b"\nforged\n")

    planner = Mock(side_effect=AssertionError("planner must not see tampered SRT"))
    monkeypatch.setattr(pipeline, "_DATA_ROOT", ep_dir.parent)
    monkeypatch.setattr(
        "agents.brook.script_video.subtitle_handoff.open_verified_projection",
        _real_fixture_loader,
    )
    monkeypatch.setattr("agents.brook.script_video.planner.plan_episode", planner)

    with pytest.raises(Exception, match="artifact hash mismatch"):
        pipeline._cmd_plan(argparse.Namespace(episode=episode_id))
    planner.assert_not_called()


@pytest.mark.parametrize(
    ("binding_field", "replacement"),
    (
        ("generation_id", "generation-" + "0" * 64),
        ("projection_manifest_sha256", "0" * 64),
    ),
)
def test_wrong_generation_or_manifest_binding_fails_before_planner(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    binding_field: str,
    replacement: str,
) -> None:
    episode_id = f"episode-wrong-{binding_field}"
    subtitle_root = tmp_path / "subtitle"
    _module_instance, accepted, projected = _project_fixture(
        subtitle_root,
        episode_id=episode_id,
    )
    binding = _episode_binding(subtitle_root, projected, accepted)
    binding[binding_field] = replacement
    ep_dir = tmp_path / "script-video" / episode_id
    _write_script_episode(ep_dir, episode_id=episode_id, binding=binding)

    planner = Mock(side_effect=AssertionError("planner must not see wrong lineage"))
    monkeypatch.setattr(pipeline, "_DATA_ROOT", ep_dir.parent)
    monkeypatch.setattr(
        "agents.brook.script_video.subtitle_handoff.open_verified_projection",
        _real_fixture_loader,
    )
    monkeypatch.setattr("agents.brook.script_video.planner.plan_episode", planner)

    with pytest.raises(Exception):
        pipeline._cmd_plan(argparse.Namespace(episode=episode_id))
    planner.assert_not_called()


def test_projection_episode_must_equal_script_video_episode_binding(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    subtitle_root = tmp_path / "subtitle"
    _module_instance, accepted, projected = _project_fixture(
        subtitle_root,
        episode_id="source-episode",
    )
    ep_dir = tmp_path / "script-video" / "consumer-episode"
    _write_script_episode(
        ep_dir,
        episode_id="consumer-episode",
        binding=_episode_binding(subtitle_root, projected, accepted),
    )
    planner = Mock(side_effect=AssertionError("planner must not see cross-episode SRT"))
    monkeypatch.setattr(pipeline, "_DATA_ROOT", ep_dir.parent)
    monkeypatch.setattr(
        "agents.brook.script_video.subtitle_handoff.open_verified_projection",
        _real_fixture_loader,
    )
    monkeypatch.setattr("agents.brook.script_video.planner.plan_episode", planner)

    with pytest.raises(Exception, match="episode"):
        pipeline._cmd_plan(argparse.Namespace(episode="consumer-episode"))
    planner.assert_not_called()


def test_emit_reverifies_handoff_and_rejects_storyboard_provenance_drift(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    episode_id = "episode-emit-drift"
    subtitle_root = tmp_path / "subtitle"
    _module_instance, accepted, projected = _project_fixture(
        subtitle_root,
        episode_id=episode_id,
    )
    ep_dir = tmp_path / "script-video" / episode_id
    _write_script_episode(
        ep_dir,
        episode_id=episode_id,
        binding=_episode_binding(subtitle_root, projected, accepted),
    )
    monkeypatch.setattr(pipeline, "_DATA_ROOT", ep_dir.parent)
    monkeypatch.setattr(
        "agents.brook.script_video.subtitle_handoff.open_verified_projection",
        _real_fixture_loader,
    )
    monkeypatch.setattr(
        "agents.brook.script_video.planner.ask",
        Mock(return_value=_canned_storyboard_for_srt(projected.srt_bytes)),
    )
    assert pipeline._cmd_plan(argparse.Namespace(episode=episode_id)) == 0

    provenance_path = ep_dir / "storyboard.provenance.json"
    provenance = json.loads(provenance_path.read_text(encoding="utf-8"))
    provenance["srt_sha256"] = "0" * 64
    provenance_path.write_text(json.dumps(provenance), encoding="utf-8")
    emitter = Mock(side_effect=AssertionError("emit must not run after provenance drift"))
    monkeypatch.setattr("agents.brook.script_video.fcpxml_emitter.emit", emitter)

    with pytest.raises(Exception, match="provenance"):
        pipeline._cmd_emit(argparse.Namespace(episode=episode_id, fcpxml_version="1.10"))
    emitter.assert_not_called()
