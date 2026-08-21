"""Tracers for append-only native-audit-basis code migration."""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path

import pytest

from agents.brook.podcast_subtitles.errors import (
    ArtifactHashMismatchError,
    GenerationIsolationError,
)
from agents.brook.podcast_subtitles.facade import PodcastSubtitleFacade
from agents.brook.podcast_subtitles.module import (
    CreateRequest,
    Interrupted,
    NativeAuditBasisMigrationRequest,
    NeedsReview,
)
from tests.agents.brook.podcast_subtitles.test_full_audit_module_integration import (
    _native_module,
    audio_response_bytes,
    text_response_bytes,
)


def _seed_basis(tmp_path: Path) -> tuple[Path, Path, CreateRequest, str]:
    episode_root = tmp_path / "episode"
    workspace = tmp_path / "workspace"
    old, source, _, _ = _native_module(
        episode_root,
        workspace=workspace,
        code_version="basis-before-cache",
    )
    request = CreateRequest(episode_id="episode-basis-migration", source_audio=source)
    result = old.create(request)
    assert isinstance(result, Interrupted)
    stored = old.store.load_latest_create_checkpoint()
    assert stored is not None
    assert stored.checkpoint.stage == "native_audit_basis_ready"
    return episode_root, workspace, request, stored.checkpoint.id


def test_basis_code_migration_is_append_only_idempotent_and_resumable(
    tmp_path: Path,
) -> None:
    episode_root, workspace, create_request, old_id = _seed_basis(tmp_path)
    module, _, normalizer, recognizers = _native_module(
        episode_root,
        workspace=workspace,
        code_version="basis-with-cache",
    )
    request = NativeAuditBasisMigrationRequest(
        create_request=create_request,
        expected_checkpoint_id=old_id,
        operator="operator:test",
        migrated_at_utc="2026-08-20T02:03:04Z",
    )

    outcome = module.migrate_native_audit_basis(request)
    stored = module.store.load_latest_create_checkpoint()
    assert stored is not None
    assert stored.checkpoint.id == outcome.new_checkpoint_id
    assert stored.checkpoint.stage == "native_audit_basis_ready"
    assert stored.checkpoint.previous_checkpoint_id == old_id
    assert outcome.receipt.old_code_hash != outcome.receipt.new_code_hash
    assert (
        outcome.receipt.old_adapter_identity_set_hash
        == outcome.receipt.new_adapter_identity_set_hash
    )
    assert set(stored.checkpoint.artifact_hashes) == {
        *module.store.load_create_checkpoint_id(old_id).checkpoint.artifact_hashes,
        outcome.receipt_artifact_name,
    }
    assert normalizer.calls == 0
    assert tuple(item.calls for item in recognizers) == (0, 0)

    count = len(tuple(module.store.create_checkpoints_dir.iterdir()))
    assert module.migrate_native_audit_basis(request) == outcome
    assert len(tuple(module.store.create_checkpoints_dir.iterdir())) == count

    resumed = module.create(create_request)
    assert isinstance(resumed, Interrupted)
    assert normalizer.calls == 0
    assert tuple(item.calls for item in recognizers) == (0, 0)
    for request_path, response_path in zip(
        resumed.packet_paths, resumed.response_paths, strict=True
    ):
        response_path.parent.mkdir(parents=True, exist_ok=True)
        response_path.write_bytes(text_response_bytes(request_path.read_bytes()))

    after_text, _, text_normalizer, text_recognizers = _native_module(
        episode_root,
        workspace=workspace,
        code_version="basis-with-cache",
    )
    audio_pending = after_text.create(create_request)
    assert isinstance(audio_pending, Interrupted)
    assert after_text.store.load_latest_create_checkpoint().checkpoint.stage == (
        "native_text_audit_ready"
    )
    assert text_normalizer.calls == 0
    assert tuple(item.calls for item in text_recognizers) == (0, 0)
    for request_path, clip_path, response_path in zip(
        audio_pending.packet_paths,
        audio_pending.clip_paths,
        audio_pending.response_paths,
        strict=True,
    ):
        response_path.parent.mkdir(parents=True, exist_ok=True)
        response_path.write_bytes(
            audio_response_bytes(request_path.read_bytes(), clip_path.read_bytes())
        )

    after_audio, _, audio_normalizer, audio_recognizers = _native_module(
        episode_root,
        workspace=workspace,
        code_version="basis-with-cache",
    )
    completed = after_audio.create(create_request)
    assert isinstance(completed, NeedsReview)
    assert after_audio.store.load_latest_create_checkpoint().checkpoint.stage == "complete"
    assert audio_normalizer.calls == 0
    assert tuple(item.calls for item in audio_recognizers) == (0, 0)

    fresh, _, _, _ = _native_module(
        episode_root,
        workspace=workspace,
        code_version="basis-with-cache",
    )
    assert PodcastSubtitleFacade(fresh).status().state == "complete"


def test_basis_code_migration_rejects_adapter_drift_before_provider_calls(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    episode_root, workspace, create_request, old_id = _seed_basis(tmp_path)
    module, _, normalizer, recognizers = _native_module(
        episode_root,
        workspace=workspace,
        code_version="basis-with-cache",
    )
    current = module._create_checkpoint_adapter_identities

    def drifted(**kwargs: object):
        identities = current(**kwargs)
        return (identities[0].model_copy(update={"code_hash": "f" * 64}), *identities[1:])

    monkeypatch.setattr(module, "_create_checkpoint_adapter_identities", drifted)
    with pytest.raises(GenerationIsolationError, match="code-only"):
        module.migrate_native_audit_basis(
            NativeAuditBasisMigrationRequest(
                create_request=create_request,
                expected_checkpoint_id=old_id,
                operator="operator:test",
                migrated_at_utc="2026-08-20T02:03:04Z",
            )
        )
    assert normalizer.calls == 0
    assert tuple(item.calls for item in recognizers) == (0, 0)


def test_basis_code_migration_rejects_wrong_expected_checkpoint(tmp_path: Path) -> None:
    episode_root, workspace, create_request, _ = _seed_basis(tmp_path)
    module, _, normalizer, recognizers = _native_module(
        episode_root,
        workspace=workspace,
        code_version="basis-with-cache",
    )
    with pytest.raises(GenerationIsolationError, match="expected checkpoint"):
        module.migrate_native_audit_basis(
            NativeAuditBasisMigrationRequest(
                create_request=create_request,
                expected_checkpoint_id="create-checkpoint-" + "0" * 64,
                operator="operator:test",
                migrated_at_utc="2026-08-20T02:03:04Z",
            )
        )
    assert normalizer.calls == 0
    assert tuple(item.calls for item in recognizers) == (0, 0)


def test_basis_code_migration_rejects_episode_and_vocabulary_drift(tmp_path: Path) -> None:
    episode_root, workspace, create_request, old_id = _seed_basis(tmp_path)
    module, _, normalizer, recognizers = _native_module(
        episode_root,
        workspace=workspace,
        code_version="basis-with-cache",
    )
    common = {
        "expected_checkpoint_id": old_id,
        "operator": "operator:test",
        "migrated_at_utc": "2026-08-20T02:03:04Z",
    }
    with pytest.raises(GenerationIsolationError, match="episode lineage"):
        module.migrate_native_audit_basis(
            NativeAuditBasisMigrationRequest(
                create_request=replace(create_request, episode_id="wrong-episode"),
                **common,
            )
        )
    with pytest.raises(GenerationIsolationError, match="inputs drifted"):
        module.migrate_native_audit_basis(
            NativeAuditBasisMigrationRequest(
                create_request=replace(create_request, vocabulary=("changed",)),
                **common,
            )
        )
    assert normalizer.calls == 0
    assert tuple(item.calls for item in recognizers) == (0, 0)


def test_basis_migration_receipt_tamper_fails_before_provider_calls(tmp_path: Path) -> None:
    episode_root, workspace, create_request, old_id = _seed_basis(tmp_path)
    module, _, _, _ = _native_module(
        episode_root,
        workspace=workspace,
        code_version="basis-with-cache",
    )
    outcome = module.migrate_native_audit_basis(
        NativeAuditBasisMigrationRequest(
            create_request=create_request,
            expected_checkpoint_id=old_id,
            operator="operator:test",
            migrated_at_utc="2026-08-20T02:03:04Z",
        )
    )
    stored = module.store.load_create_checkpoint_id(outcome.new_checkpoint_id)
    receipt_path = stored.directory / outcome.receipt_artifact_name
    receipt_path.write_bytes(receipt_path.read_bytes() + b"tampered")

    fresh, _, normalizer, recognizers = _native_module(
        episode_root,
        workspace=workspace,
        code_version="basis-with-cache",
    )
    with pytest.raises(ArtifactHashMismatchError):
        fresh.create(create_request)
    assert normalizer.calls == 0
    assert tuple(item.calls for item in recognizers) == (0, 0)
