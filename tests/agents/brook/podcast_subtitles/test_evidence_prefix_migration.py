"""Operator-level tracers for append-only evidence-prefix migration."""

from __future__ import annotations

from pathlib import Path

import pytest

from agents.brook.podcast_subtitles.errors import (
    ArtifactHashMismatchError,
    GenerationIsolationError,
)
from agents.brook.podcast_subtitles.module import (
    CreateRequest,
    EvidencePrefixMigrationRequest,
    Interrupted,
)
from tests.agents.brook.podcast_subtitles.test_full_audit_module_integration import (
    _native_module,
)


class _StopAfterEvidence(RuntimeError):
    pass


def _install_adapter_drift(
    module: object,
    monkeypatch: pytest.MonkeyPatch,
    *,
    code_hash: str,
) -> None:
    current_identities = module._create_checkpoint_adapter_identities

    def drifted(**kwargs: object):
        identities = current_identities(**kwargs)
        return (
            *identities[:-1],
            identities[-1].model_copy(update={"code_hash": code_hash}),
        )

    monkeypatch.setattr(module, "_create_checkpoint_adapter_identities", drifted)


def _seed_pre_cutover_evidence_ready(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    *,
    code_version: str = "pre-selective-audio-v3",
) -> tuple[Path, Path, CreateRequest, str]:
    episode_root = tmp_path / "episode"
    workspace = tmp_path / "workspace"
    legacy, source, _, _ = _native_module(
        episode_root,
        workspace=workspace,
        selective_audio=False,
        code_version=code_version,
    )
    request = CreateRequest(episode_id="episode-evidence-migration", source_audio=source)

    def stop_after_evidence(**_kwargs: object) -> None:
        raise _StopAfterEvidence

    monkeypatch.setattr(
        legacy,
        "_commit_native_audit_basis_checkpoint",
        stop_after_evidence,
    )
    with pytest.raises(_StopAfterEvidence):
        legacy.create(request)
    stored = legacy.store.load_latest_create_checkpoint()
    assert stored is not None
    assert stored.checkpoint.stage == "evidence_ready"
    return episode_root, workspace, request, stored.checkpoint.id


def test_verified_evidence_prefix_migration_is_append_only_idempotent_and_resumable(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    episode_root, workspace, create_request, old_checkpoint_id = _seed_pre_cutover_evidence_ready(
        tmp_path, monkeypatch, code_version="selective-audio-v3"
    )
    module, _, normalizer, recognizers = _native_module(
        episode_root,
        workspace=workspace,
        code_version="selective-audio-v3",
    )
    request = EvidencePrefixMigrationRequest(
        create_request=create_request,
        expected_checkpoint_id=old_checkpoint_id,
        operator="operator:test",
        migrated_at_utc="2026-08-20T01:02:03Z",
    )

    migrated = module.migrate_evidence_prefix(request)

    assert migrated.old_checkpoint_id == old_checkpoint_id
    assert migrated.new_checkpoint_id != old_checkpoint_id
    assert migrated.receipt.reason_code == "selective_audio_v3_cutover"
    assert migrated.receipt.old_operation_key != migrated.receipt.new_operation_key
    assert migrated.receipt.old_policy_hash != migrated.receipt.new_policy_hash
    assert migrated.receipt.old_code_hash == migrated.receipt.new_code_hash
    assert (
        migrated.receipt.old_adapter_identity_set_hash
        == migrated.receipt.new_adapter_identity_set_hash
    )
    stored = module.store.load_latest_create_checkpoint()
    assert stored is not None
    assert stored.checkpoint.id == migrated.new_checkpoint_id
    assert stored.checkpoint.stage == "evidence_ready"
    assert stored.checkpoint.previous_checkpoint_id == old_checkpoint_id
    assert normalizer.calls == 0
    assert tuple(item.calls for item in recognizers) == (0, 0)
    assert not tuple(module.store.audio_staging_dir.iterdir())

    checkpoint_count = len(tuple(module.store.create_checkpoints_dir.iterdir()))
    assert module.migrate_evidence_prefix(request) == migrated
    assert len(tuple(module.store.create_checkpoints_dir.iterdir())) == checkpoint_count

    resumed = module.create(create_request)
    assert isinstance(resumed, Interrupted)
    resumed_checkpoint = module.store.load_latest_create_checkpoint()
    assert resumed_checkpoint is not None
    assert resumed_checkpoint.checkpoint.stage == "native_audit_basis_ready"
    assert normalizer.calls == 0
    assert tuple(item.calls for item in recognizers) == (0, 0)


def test_migration_rebinds_a_real_current_adapter_identity_drift(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    episode_root, workspace, create_request, old_checkpoint_id = _seed_pre_cutover_evidence_ready(
        tmp_path, monkeypatch
    )
    module, _, normalizer, recognizers = _native_module(
        episode_root, workspace=workspace, code_version="selective-audio-v3"
    )
    current_identities = module._create_checkpoint_adapter_identities

    def drifted_current_identities(**kwargs: object):
        identities = current_identities(**kwargs)
        return (
            identities[0].model_copy(update={"code_hash": "f" * 64}),
            *identities[1:],
        )

    monkeypatch.setattr(
        module,
        "_create_checkpoint_adapter_identities",
        drifted_current_identities,
    )

    migrated = module.migrate_evidence_prefix(
        EvidencePrefixMigrationRequest(
            create_request=create_request,
            expected_checkpoint_id=old_checkpoint_id,
            operator="operator:test",
            migrated_at_utc="2026-08-20T01:02:03Z",
        )
    )

    assert (
        migrated.receipt.old_adapter_identity_set_hash
        != migrated.receipt.new_adapter_identity_set_hash
    )
    assert normalizer.calls == 0
    assert tuple(item.calls for item in recognizers) == (0, 0)


def test_migration_refreshes_an_already_migrated_active_checkpoint_append_only(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    episode_root, workspace, create_request, original_checkpoint_id = (
        _seed_pre_cutover_evidence_ready(tmp_path, monkeypatch)
    )
    first_module, _, first_normalizer, first_recognizers = _native_module(
        episode_root, workspace=workspace, code_version="selective-audio-v3"
    )
    first = first_module.migrate_evidence_prefix(
        EvidencePrefixMigrationRequest(
            create_request=create_request,
            expected_checkpoint_id=original_checkpoint_id,
            operator="operator:first-cutover",
            migrated_at_utc="2026-08-20T01:02:03Z",
        )
    )
    first_stored = first_module.store.load_create_checkpoint_id(first.new_checkpoint_id)
    first_artifacts = {
        name: (first_stored.directory / name).read_bytes()
        for name in first_stored.checkpoint.artifact_hashes
    }

    second_module, _, second_normalizer, second_recognizers = _native_module(
        episode_root, workspace=workspace, code_version="selective-audio-v3"
    )
    current_identities = second_module._create_checkpoint_adapter_identities

    def batch_reference_identities(**kwargs: object):
        identities = current_identities(**kwargs)
        return (
            *identities[:-1],
            identities[-1].model_copy(update={"code_hash": "e" * 64}),
        )

    monkeypatch.setattr(
        second_module,
        "_create_checkpoint_adapter_identities",
        batch_reference_identities,
    )
    refresh_request = EvidencePrefixMigrationRequest(
        create_request=create_request,
        expected_checkpoint_id=original_checkpoint_id,
        operator="operator:batch-reference-cutover",
        migrated_at_utc="2026-08-20T02:03:04Z",
    )

    second = second_module.migrate_evidence_prefix(refresh_request)

    assert second.old_checkpoint_id == first.new_checkpoint_id
    assert second.new_checkpoint_id not in {original_checkpoint_id, first.new_checkpoint_id}
    second_stored = second_module.store.load_create_checkpoint_id(second.new_checkpoint_id)
    assert second_stored.checkpoint.previous_checkpoint_id == first.new_checkpoint_id
    for name, payload in first_artifacts.items():
        assert (second_stored.directory / name).read_bytes() == payload
    added = set(second_stored.checkpoint.artifact_hashes) - set(
        first_stored.checkpoint.artifact_hashes
    )
    assert len(added) == 1
    assert next(iter(added)).startswith("checkpoint/evidence_prefix_migrations/")
    assert first_normalizer.calls == second_normalizer.calls == 0
    assert tuple(item.calls for item in first_recognizers) == (0, 0)
    assert tuple(item.calls for item in second_recognizers) == (0, 0)

    checkpoint_count = len(tuple(second_module.store.create_checkpoints_dir.iterdir()))
    assert second_module.migrate_evidence_prefix(refresh_request) == second
    assert len(tuple(second_module.store.create_checkpoints_dir.iterdir())) == checkpoint_count


def test_three_migration_edges_verify_and_resume_from_the_latest_identity(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    episode_root, workspace, create_request, original_checkpoint_id = (
        _seed_pre_cutover_evidence_ready(tmp_path, monkeypatch)
    )
    first_module, _, _, _ = _native_module(
        episode_root, workspace=workspace, code_version="selective-audio-v3"
    )
    first = first_module.migrate_evidence_prefix(
        EvidencePrefixMigrationRequest(
            create_request=create_request,
            expected_checkpoint_id=original_checkpoint_id,
            operator="operator:first",
            migrated_at_utc="2026-08-20T01:00:00Z",
        )
    )

    second_module, _, _, _ = _native_module(
        episode_root, workspace=workspace, code_version="selective-audio-v3"
    )
    _install_adapter_drift(second_module, monkeypatch, code_hash="e" * 64)
    second = second_module.migrate_evidence_prefix(
        EvidencePrefixMigrationRequest(
            create_request=create_request,
            expected_checkpoint_id=original_checkpoint_id,
            operator="operator:second",
            migrated_at_utc="2026-08-20T02:00:00Z",
        )
    )

    third_module, _, normalizer, recognizers = _native_module(
        episode_root, workspace=workspace, code_version="selective-audio-v3"
    )
    _install_adapter_drift(third_module, monkeypatch, code_hash="d" * 64)
    third_request = EvidencePrefixMigrationRequest(
        create_request=create_request,
        expected_checkpoint_id=original_checkpoint_id,
        operator="operator:third",
        migrated_at_utc="2026-08-20T03:00:00Z",
    )
    third = third_module.migrate_evidence_prefix(third_request)

    assert third.receipt.sequence == 3
    assert third.old_checkpoint_id == second.new_checkpoint_id
    assert third.receipt.previous_migration_receipt_id == second.receipt.id
    latest = third_module.store.load_create_checkpoint_id(third.new_checkpoint_id)
    migration_names = {
        name for name in latest.checkpoint.artifact_hashes if "evidence_prefix_migration" in name
    }
    assert len(migration_names) == 3
    assert first.new_checkpoint_id != second.new_checkpoint_id != third.new_checkpoint_id

    checkpoint_count = len(tuple(third_module.store.create_checkpoints_dir.iterdir()))
    assert third_module.migrate_evidence_prefix(third_request) == third
    assert len(tuple(third_module.store.create_checkpoints_dir.iterdir())) == checkpoint_count
    resumed = third_module.create(create_request)
    assert isinstance(resumed, Interrupted)
    resumed_checkpoint = third_module.store.load_latest_create_checkpoint()
    assert resumed_checkpoint is not None
    assert resumed_checkpoint.checkpoint.stage == "native_audit_basis_ready"
    assert normalizer.calls == 0
    assert tuple(item.calls for item in recognizers) == (0, 0)


def test_migration_noop_identity_fails_closed_before_provider_calls(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    episode_root, workspace, create_request, checkpoint_id = _seed_pre_cutover_evidence_ready(
        tmp_path, monkeypatch
    )
    unchanged, _, normalizer, recognizers = _native_module(
        episode_root,
        workspace=workspace,
        selective_audio=False,
        code_version="pre-selective-audio-v3",
    )

    with pytest.raises((ValueError, GenerationIsolationError)):
        unchanged.migrate_evidence_prefix(
            EvidencePrefixMigrationRequest(
                create_request=create_request,
                expected_checkpoint_id=checkpoint_id,
                operator="operator:noop",
                migrated_at_utc="2026-08-20T01:00:00Z",
            )
        )

    latest = unchanged.store.load_latest_create_checkpoint()
    assert latest is not None
    assert latest.checkpoint.id == checkpoint_id
    assert normalizer.calls == 0
    assert tuple(item.calls for item in recognizers) == (0, 0)


@pytest.mark.parametrize("damage", ["tamper", "missing"])
def test_migration_rejects_changed_legacy_evidence_bytes_before_provider_calls(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    damage: str,
) -> None:
    episode_root, workspace, create_request, old_checkpoint_id = _seed_pre_cutover_evidence_ready(
        tmp_path, monkeypatch
    )
    module, _, normalizer, recognizers = _native_module(
        episode_root, workspace=workspace, code_version="selective-audio-v3"
    )
    stored = module.store.load_create_checkpoint_id(old_checkpoint_id)
    artifact_name = next(iter(stored.checkpoint.artifact_hashes))
    artifact_path = stored.directory / artifact_name
    if damage == "tamper":
        artifact_path.write_bytes(artifact_path.read_bytes() + b"tampered")
    else:
        artifact_path.unlink()

    with pytest.raises((ArtifactHashMismatchError, GenerationIsolationError)):
        module.migrate_evidence_prefix(
            EvidencePrefixMigrationRequest(
                create_request=create_request,
                expected_checkpoint_id=old_checkpoint_id,
                operator="operator:test",
                migrated_at_utc="2026-08-20T01:02:03Z",
            )
        )

    assert normalizer.calls == 0
    assert tuple(item.calls for item in recognizers) == (0, 0)


def test_migration_rejects_wrong_checkpoint_and_episode_before_provider_calls(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    episode_root, workspace, create_request, old_checkpoint_id = _seed_pre_cutover_evidence_ready(
        tmp_path, monkeypatch
    )
    module, _, normalizer, recognizers = _native_module(
        episode_root, workspace=workspace, code_version="selective-audio-v3"
    )
    wrong_checkpoint = "create-checkpoint-" + "0" * 64
    with pytest.raises(GenerationIsolationError, match="expected checkpoint"):
        module.migrate_evidence_prefix(
            EvidencePrefixMigrationRequest(
                create_request=create_request,
                expected_checkpoint_id=wrong_checkpoint,
                operator="operator:test",
                migrated_at_utc="2026-08-20T01:02:03Z",
            )
        )
    wrong_episode = CreateRequest(
        episode_id="another-episode", source_audio=create_request.source_audio
    )
    with pytest.raises(GenerationIsolationError, match="crossed episode"):
        module.migrate_evidence_prefix(
            EvidencePrefixMigrationRequest(
                create_request=wrong_episode,
                expected_checkpoint_id=old_checkpoint_id,
                operator="operator:test",
                migrated_at_utc="2026-08-20T01:02:03Z",
            )
        )
    assert normalizer.calls == 0
    assert tuple(item.calls for item in recognizers) == (0, 0)


def test_migration_rejects_changed_source_without_creating_a_staging_copy(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    episode_root, workspace, create_request, old_checkpoint_id = _seed_pre_cutover_evidence_ready(
        tmp_path, monkeypatch
    )
    create_request.source_audio.write_bytes(b"changed after evidence capture")
    module, _, normalizer, recognizers = _native_module(
        episode_root, workspace=workspace, code_version="selective-audio-v3"
    )

    with pytest.raises(GenerationIsolationError, match="differs from the migrated"):
        module.migrate_evidence_prefix(
            EvidencePrefixMigrationRequest(
                create_request=create_request,
                expected_checkpoint_id=old_checkpoint_id,
                operator="operator:test",
                migrated_at_utc="2026-08-20T01:02:03Z",
            )
        )

    assert not tuple(module.store.audio_staging_dir.iterdir())
    assert normalizer.calls == 0
    assert tuple(item.calls for item in recognizers) == (0, 0)


def test_migration_rejects_old_audit_basis_instead_of_evidence_prefix(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    episode_root, workspace, create_request, _ = _seed_pre_cutover_evidence_ready(
        tmp_path, monkeypatch
    )
    legacy, _, _, _ = _native_module(
        episode_root,
        workspace=workspace,
        selective_audio=False,
        code_version="pre-selective-audio-v3",
    )
    resumed = legacy.create(create_request)
    assert isinstance(resumed, Interrupted)
    basis = legacy.store.load_latest_create_checkpoint()
    assert basis is not None
    assert basis.checkpoint.stage == "native_audit_basis_ready"
    module, _, normalizer, recognizers = _native_module(
        episode_root, workspace=workspace, code_version="selective-audio-v3"
    )

    with pytest.raises(GenerationIsolationError, match="requires an evidence_ready"):
        module.migrate_evidence_prefix(
            EvidencePrefixMigrationRequest(
                create_request=create_request,
                expected_checkpoint_id=basis.checkpoint.id,
                operator="operator:test",
                migrated_at_utc="2026-08-20T01:02:03Z",
            )
        )

    assert normalizer.calls == 0
    assert tuple(item.calls for item in recognizers) == (0, 0)


def test_migration_receipt_tamper_fails_closed_before_provider_calls(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    episode_root, workspace, create_request, old_checkpoint_id = _seed_pre_cutover_evidence_ready(
        tmp_path, monkeypatch
    )
    module, _, normalizer, recognizers = _native_module(
        episode_root, workspace=workspace, code_version="selective-audio-v3"
    )
    outcome = module.migrate_evidence_prefix(
        EvidencePrefixMigrationRequest(
            create_request=create_request,
            expected_checkpoint_id=old_checkpoint_id,
            operator="operator:test",
            migrated_at_utc="2026-08-20T01:02:03Z",
        )
    )
    stored = module.store.load_create_checkpoint_id(outcome.new_checkpoint_id)
    receipt_path = stored.directory / "checkpoint/evidence_prefix_migration_v1.json"
    receipt_path.write_bytes(receipt_path.read_bytes() + b"tampered")

    with pytest.raises(ArtifactHashMismatchError):
        module.create(create_request)

    assert normalizer.calls == 0
    assert tuple(item.calls for item in recognizers) == (0, 0)
