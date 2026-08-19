from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import pytest

from agents.brook.podcast_subtitles.errors import GenerationIsolationError
from agents.brook.script_video.subtitle_handoff import (
    Stage5SubtitleArtifactConflictError,
    Stage5SubtitleContractError,
    Stage5SubtitleRequest,
    open_stage5_subtitle,
    select_stage5_subtitle,
)
from tests.agents.brook.podcast_subtitles.test_verified_projection_handoff import (
    _fixture_factory,
    _project_fixture,
)


def test_formal_stage5_handoff_materializes_exact_verified_srt_and_lineage(
    tmp_path: Path,
) -> None:
    _module, accepted, projected = _project_fixture(tmp_path, episode_id="episode-stage5")

    handoff = open_stage5_subtitle(
        episode_root=tmp_path,
        projection_id=projected.projection_id,
        expected_episode_id="episode-stage5",
        expected_generation_id=accepted.generation_id,
        expected_manifest_sha256=projected.manifest_sha256,
        factory=_fixture_factory,
    )

    assert handoff.srt_path.read_bytes() == handoff.verified.srt_bytes
    assert handoff.srt_path.read_bytes() == projected.srt_bytes
    assert json.loads(handoff.receipt_path.read_text(encoding="utf-8")) == {
        "schema_version": 1,
        "episode_id": "episode-stage5",
        "generation_id": accepted.generation_id,
        "manifest_sha256": projected.manifest_sha256,
        "projection_id": projected.projection_id,
        "srt_path": str(handoff.srt_path),
        "srt_sha256": handoff.verified.srt_sha256,
    }


def test_same_verified_projection_handoff_is_idempotent(tmp_path: Path) -> None:
    _module, accepted, projected = _project_fixture(tmp_path, episode_id="episode-stage5")
    request = {
        "episode_root": tmp_path,
        "projection_id": projected.projection_id,
        "expected_episode_id": "episode-stage5",
        "expected_generation_id": accepted.generation_id,
        "expected_manifest_sha256": projected.manifest_sha256,
        "factory": _fixture_factory,
    }
    first = open_stage5_subtitle(**request)
    fixed_ns = 1_700_000_000_000_000_000
    os.utime(first.srt_path, ns=(fixed_ns, fixed_ns))
    os.utime(first.receipt_path, ns=(fixed_ns, fixed_ns))

    second = open_stage5_subtitle(**request)

    assert second.srt_path.stat().st_mtime_ns == fixed_ns
    assert second.receipt_path.stat().st_mtime_ns == fixed_ns


@pytest.mark.parametrize("artifact", ("srt", "receipt"))
def test_verified_projection_handoff_rejects_existing_conflicting_bytes(
    tmp_path: Path,
    artifact: str,
) -> None:
    _module, accepted, projected = _project_fixture(tmp_path, episode_id="episode-stage5")
    request = {
        "episode_root": tmp_path,
        "projection_id": projected.projection_id,
        "expected_episode_id": "episode-stage5",
        "expected_generation_id": accepted.generation_id,
        "expected_manifest_sha256": projected.manifest_sha256,
        "factory": _fixture_factory,
    }
    first = open_stage5_subtitle(**request)
    target = first.srt_path if artifact == "srt" else first.receipt_path
    conflicting = b"conflicting immutable artifact\n"
    target.write_bytes(conflicting)

    with pytest.raises(Stage5SubtitleArtifactConflictError, match="immutable"):
        open_stage5_subtitle(**request)

    assert target.read_bytes() == conflicting


def test_bare_episode_transcript_requires_explicit_legacy_mode(tmp_path: Path) -> None:
    bare_srt = tmp_path / "transcript.srt"
    bare_srt.write_bytes(b"bare legacy subtitle\n")

    with pytest.raises(Stage5SubtitleContractError, match="projection_id"):
        select_stage5_subtitle(episode_root=tmp_path)

    selected = select_stage5_subtitle(episode_root=tmp_path, legacy_v1=True)

    assert selected.mode == "legacy-v1"
    assert selected.srt_path == bare_srt
    assert selected.handoff is None


def test_resolve_build_rejects_wrong_projection_binding_before_media_or_resolve(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _module, accepted, projected = _project_fixture(tmp_path, episode_id="episode-stage5")
    scripts = Path(__file__).resolve().parents[3] / "scripts"
    monkeypatch.syspath_prepend(str(scripts))
    sys.modules.pop("build_resolve_project", None)
    import build_resolve_project

    def forbidden(*_args, **_kwargs):
        raise AssertionError("Resolve/media work started before subtitle verification")

    monkeypatch.setattr(build_resolve_project, "find_main_video", forbidden)
    monkeypatch.setattr(build_resolve_project, "connect_resolve", forbidden)

    with pytest.raises(GenerationIsolationError):
        build_resolve_project.build_project(
            tmp_path,
            subtitle_request=Stage5SubtitleRequest(
                projection_id=projected.projection_id,
                expected_episode_id="episode-stage5",
                expected_generation_id=accepted.generation_id,
                expected_manifest_sha256="0" * 64,
            ),
            verifier_factory=_fixture_factory,
        )


def test_resolve_cli_exposes_formal_identity_and_explicit_legacy_switch(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    scripts = Path(__file__).resolve().parents[3] / "scripts"
    monkeypatch.syspath_prepend(str(scripts))
    sys.modules.pop("build_resolve_project", None)
    import build_resolve_project

    formal = build_resolve_project._parse_args(
        [
            str(tmp_path),
            "--projection-id",
            "projection-123",
            "--expected-episode-id",
            "episode-stage5",
            "--expected-generation-id",
            "generation-123",
            "--expected-manifest-sha256",
            "a" * 64,
        ]
    )
    legacy = build_resolve_project._parse_args([str(tmp_path), "--legacy-v1"])

    assert formal.subtitle_request == Stage5SubtitleRequest(
        projection_id="projection-123",
        expected_episode_id="episode-stage5",
        expected_generation_id="generation-123",
        expected_manifest_sha256="a" * 64,
    )
    assert legacy.subtitle_request == Stage5SubtitleRequest(legacy_v1=True)


def test_highlight_validation_rejects_wrong_binding_before_candidate_mutation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _module, accepted, projected = _project_fixture(tmp_path, episode_id="episode-stage5")
    candidates = tmp_path / "highlights" / "candidates.json"
    candidates.parent.mkdir()
    original = b'{"candidates": []}\n'
    candidates.write_bytes(original)
    scripts = Path(__file__).resolve().parents[3] / "scripts"
    monkeypatch.syspath_prepend(str(scripts))
    sys.modules.pop("run_highlight_cut", None)
    import run_highlight_cut

    with pytest.raises(GenerationIsolationError):
        run_highlight_cut.validate(
            tmp_path,
            subtitle_request=Stage5SubtitleRequest(
                projection_id=projected.projection_id,
                expected_episode_id="wrong-episode",
                expected_generation_id=accepted.generation_id,
                expected_manifest_sha256=projected.manifest_sha256,
            ),
            verifier_factory=_fixture_factory,
        )

    assert candidates.read_bytes() == original


def test_resolve_dry_run_records_verified_projection_lineage_without_resolve(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _module, accepted, projected = _project_fixture(tmp_path, episode_id="episode-stage5")
    scripts = Path(__file__).resolve().parents[3] / "scripts"
    monkeypatch.syspath_prepend(str(scripts))
    sys.modules.pop("build_resolve_project", None)
    import build_resolve_project

    video = tmp_path / "program.mp4"
    video.write_bytes(b"test")
    monkeypatch.setattr(build_resolve_project, "find_main_video", lambda *_args: video)
    monkeypatch.setattr(
        build_resolve_project,
        "_probe",
        lambda _path: {"fps": 30.0, "width": 1920, "height": 1080, "duration": 1.0},
    )
    monkeypatch.setattr(
        build_resolve_project,
        "connect_resolve",
        lambda: (_ for _ in ()).throw(AssertionError("dry-run touched Resolve")),
    )

    plan = build_resolve_project.build_project(
        tmp_path,
        dry_run=True,
        subtitle_request=Stage5SubtitleRequest(
            projection_id=projected.projection_id,
            expected_episode_id="episode-stage5",
            expected_generation_id=accepted.generation_id,
            expected_manifest_sha256=projected.manifest_sha256,
        ),
        verifier_factory=_fixture_factory,
    )

    assert plan["subtitle_mode"] == "verified-v2"
    assert plan["projection_id"] == projected.projection_id
    assert plan["generation_id"] == accepted.generation_id
    assert plan["episode_id"] == "episode-stage5"
    assert plan["projection_manifest_sha256"] == projected.manifest_sha256
    assert Path(plan["subtitle_handoff_receipt"]).is_file()
