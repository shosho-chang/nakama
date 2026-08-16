from __future__ import annotations

import hashlib
from pathlib import Path

import pytest

from agents.brook.podcast_subtitles.errors import GenerationIsolationError, IntegrityError
from agents.brook.podcast_subtitles.handoff import open_verified_projection
from agents.brook.podcast_subtitles.module import (
    AcceptedGeneration,
    CreateRequest,
    NeedsReview,
    ProjectRequest,
    ResolveRequest,
)
from agents.brook.podcast_subtitles.profiles import HORIZONTAL_16X9
from tests.agents.brook.podcast_subtitles.test_module import (
    _decision_for_created,
    _module,
)


def _sha256(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def _project_fixture(root: Path, *, episode_id: str = "episode-stage5"):
    module, source = _module(root)
    created = module.create(CreateRequest(episode_id=episode_id, source_audio=source))
    assert isinstance(created, NeedsReview)
    accepted = module.resolve(
        ResolveRequest(
            created.generation_id,
            (_decision_for_created(created, event_id="accept-stage5"),),
        )
    )
    assert isinstance(accepted, AcceptedGeneration)
    projected = module.project(ProjectRequest(accepted.generation_id, HORIZONTAL_16X9))
    return module, accepted, projected


def _fixture_factory(context):
    return _module(context.episode_root)[0]


def test_public_loader_returns_exact_verified_bytes_from_fresh_module(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    module, accepted, projected = _project_fixture(tmp_path)
    projection_dir = module.store.root / "projections" / projected.projection_id

    reopened, _ = _module(tmp_path)

    def forbidden(*_args, **_kwargs):
        raise AssertionError("read-only handoff attempted new provider work")

    monkeypatch.setattr(reopened._normalizer, "normalize", forbidden)
    for recognizer in reopened._recognizers:
        monkeypatch.setattr(recognizer, "recognize", forbidden)
    monkeypatch.setattr(reopened._corrector, "propose_with_receipt", forbidden)
    monkeypatch.setattr(reopened._audio_auditor, "audit", forbidden)
    monkeypatch.setattr(reopened._semantic_analyzer, "partition_with_receipts", forbidden)

    loaded = reopened.load_verified_projection(
        projected.projection_id,
        expected_episode_id="episode-stage5",
        expected_generation_id=accepted.generation_id,
        expected_manifest_sha256=projected.manifest_sha256,
    )

    assert loaded.srt_bytes == (projection_dir / "transcript.srt").read_bytes()
    assert loaded.projection_bytes == (projection_dir / "projection.json").read_bytes()
    assert loaded.manifest_bytes == (projection_dir / "projection_manifest.json").read_bytes()
    assert loaded.quality_report_bytes == (projection_dir / "quality_report.json").read_bytes()
    assert loaded.srt_sha256 == _sha256(loaded.srt_bytes)
    assert loaded.manifest_sha256 == _sha256(loaded.manifest_bytes)
    assert loaded.quality_report.passed is True


def test_public_composition_loader_uses_full_disk_verifier(tmp_path: Path) -> None:
    _module_instance, accepted, projected = _project_fixture(tmp_path)

    loaded = open_verified_projection(
        episode_root=tmp_path,
        projection_id=projected.projection_id,
        expected_episode_id="episode-stage5",
        expected_generation_id=accepted.generation_id,
        expected_manifest_sha256=projected.manifest_sha256,
        factory=_fixture_factory,
    )

    assert loaded.projection_id == projected.projection_id
    assert loaded.generation_id == accepted.generation_id
    assert loaded.episode_id == "episode-stage5"


@pytest.mark.parametrize(
    ("scope", "artifact"),
    (
        ("projection", "transcript.srt"),
        ("projection", "transcript.tokens.json"),
        ("projection", "projection_record.json"),
        ("projection", "projection_manifest.json"),
        ("projection", "quality_report.json"),
        ("projection", "semantic_execution_receipts.json"),
        ("generation", "canonical_transcript.json"),
    ),
)
def test_public_loader_rejects_any_handoff_lineage_tamper(
    tmp_path: Path,
    scope: str,
    artifact: str,
) -> None:
    module, accepted, projected = _project_fixture(tmp_path)
    directory = (
        module.store.root / "projections" / projected.projection_id
        if scope == "projection"
        else module.store.generation_dir(accepted.generation_id)
    )
    target = directory / artifact
    target.write_bytes(target.read_bytes() + b" ")

    with pytest.raises(IntegrityError):
        reopened, _ = _module(tmp_path)
        reopened.load_verified_projection(
            projected.projection_id,
            expected_episode_id="episode-stage5",
            expected_generation_id=accepted.generation_id,
            expected_manifest_sha256=projected.manifest_sha256,
        )


def test_public_loader_rejects_wrong_episode_generation_and_manifest_binding(
    tmp_path: Path,
) -> None:
    _module_instance, accepted, projected = _project_fixture(tmp_path)
    reopened, _ = _module(tmp_path)

    cases = (
        {"expected_episode_id": "another-episode"},
        {"expected_generation_id": "generation-" + "0" * 64},
        {"expected_manifest_sha256": "0" * 64},
    )
    for overrides in cases:
        expected = {
            "expected_episode_id": "episode-stage5",
            "expected_generation_id": accepted.generation_id,
            "expected_manifest_sha256": projected.manifest_sha256,
        }
        expected.update(overrides)
        with pytest.raises(GenerationIsolationError):
            reopened.load_verified_projection(projected.projection_id, **expected)
