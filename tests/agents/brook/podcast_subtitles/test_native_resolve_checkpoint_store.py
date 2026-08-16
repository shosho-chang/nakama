from __future__ import annotations

from pathlib import Path

import pytest

from agents.brook.podcast_subtitles.errors import (
    ArtifactHashMismatchError,
    GenerationIsolationError,
)
from agents.brook.podcast_subtitles.hashing import sha256_bytes
from agents.brook.podcast_subtitles.native_resolution import (
    NativeResolveCheckpointV2,
    build_native_resolve_checkpoint,
)
from agents.brook.podcast_subtitles.store import GenerationStore


def _checkpoint(
    *,
    stage: str = "audit_basis_ready",
    previous: NativeResolveCheckpointV2 | None = None,
    operation_key: str = "1" * 64,
    artifacts: dict[str, bytes] | None = None,
    **updates: str,
) -> tuple[NativeResolveCheckpointV2, dict[str, bytes]]:
    payloads = artifacts or {f"native_resolve/{stage}.json": stage.encode()}
    values: dict[str, object] = {
        "operation_key": operation_key,
        "episode_id": "episode-1",
        "stage": stage,
        "parent_generation_id": "generation-" + "2" * 64,
        "parent_manifest_hash": "3" * 64,
        "expected_active_generation_id": "generation-" + "2" * 64,
        "expected_ledger_head": "4" * 64,
        "prepared_ledger_entry_hash": "5" * 64,
        "decision_event_id": "native-resolution-" + "6" * 64,
        "decision_content_hash": "6" * 64,
        "authorization_kind": "correction_acceptance",
        "authorization_id": "7" * 64,
        "authorization_hash": "7" * 64,
        "audit_basis_generation_id": "generation-" + "8" * 64,
        "audit_basis_transcript_hash": "9" * 64,
        "policy_hash": "a" * 64,
        "code_hash": "b" * 64,
        "reference_enrollment_hash": "c" * 64,
        "adapter_identities_hash": "d" * 64,
        "artifact_hashes": {name: sha256_bytes(value) for name, value in sorted(payloads.items())},
        "previous_checkpoint_id": previous.id if previous is not None else None,
    }
    values.update(updates)
    return build_native_resolve_checkpoint(**values), payloads  # type: ignore[arg-type]


def test_native_resolve_checkpoint_round_trip_across_fresh_store(tmp_path: Path) -> None:
    store = GenerationStore(tmp_path)
    basis, artifacts = _checkpoint()

    stored = store.commit_native_resolve_checkpoint(basis, artifacts=artifacts)
    reopened = GenerationStore(tmp_path).load_native_resolve_checkpoint(
        expected_operation_key=basis.operation_key
    )

    assert reopened is not None
    assert reopened.checkpoint == basis
    assert stored.checkpoint == basis
    assert (
        store.read_native_resolve_checkpoint_artifact(
            stored, "native_resolve/audit_basis_ready.json"
        )
        == b"audit_basis_ready"
    )


def test_native_resolve_checkpoint_advances_contiguously_and_idempotently(
    tmp_path: Path,
) -> None:
    store = GenerationStore(tmp_path)
    basis, basis_artifacts = _checkpoint()
    store.commit_native_resolve_checkpoint(basis, artifacts=basis_artifacts)
    repeated = store.commit_native_resolve_checkpoint(basis, artifacts=basis_artifacts)
    assert repeated.checkpoint == basis
    text, text_artifacts = _checkpoint(stage="text_audit_ready", previous=basis)
    store.commit_native_resolve_checkpoint(text, artifacts=text_artifacts)
    audio, audio_artifacts = _checkpoint(stage="audio_audit_ready", previous=text)
    store.commit_native_resolve_checkpoint(audio, artifacts=audio_artifacts)

    latest = GenerationStore(tmp_path).load_native_resolve_checkpoint(
        expected_operation_key=basis.operation_key
    )

    assert latest is not None
    assert latest.checkpoint == audio
    assert store.load_native_resolve_checkpoint_id(text.id).checkpoint == text


def test_native_resolve_checkpoint_rejects_skipped_stage_and_identity_drift(
    tmp_path: Path,
) -> None:
    store = GenerationStore(tmp_path)
    basis, artifacts = _checkpoint()
    store.commit_native_resolve_checkpoint(basis, artifacts=artifacts)
    skipped, skipped_artifacts = _checkpoint(stage="audio_audit_ready", previous=basis)
    with pytest.raises(GenerationIsolationError, match="not contiguous"):
        store.commit_native_resolve_checkpoint(skipped, artifacts=skipped_artifacts)

    drifted, drifted_artifacts = _checkpoint(
        stage="text_audit_ready",
        previous=basis,
        code_hash="e" * 64,
    )
    with pytest.raises(GenerationIsolationError, match="inherited identity"):
        store.commit_native_resolve_checkpoint(drifted, artifacts=drifted_artifacts)


def test_native_resolve_operations_have_independent_authenticated_pointers(
    tmp_path: Path,
) -> None:
    store = GenerationStore(tmp_path)
    first, first_artifacts = _checkpoint(operation_key="1" * 64)
    second, second_artifacts = _checkpoint(
        operation_key="f" * 64,
        decision_event_id="native-resolution-" + "0" * 64,
        decision_content_hash="0" * 64,
    )

    store.commit_native_resolve_checkpoint(first, artifacts=first_artifacts)
    store.commit_native_resolve_checkpoint(second, artifacts=second_artifacts)

    assert (
        store.load_native_resolve_checkpoint(expected_operation_key=first.operation_key).checkpoint
        == first
    )
    assert (
        store.load_native_resolve_checkpoint(expected_operation_key=second.operation_key).checkpoint
        == second
    )


def test_native_resolve_checkpoint_tamper_fails_closed(tmp_path: Path) -> None:
    store = GenerationStore(tmp_path)
    basis, artifacts = _checkpoint()
    stored = store.commit_native_resolve_checkpoint(basis, artifacts=artifacts)
    artifact_path = stored.directory / "native_resolve" / "audit_basis_ready.json"
    artifact_path.write_bytes(b"tampered")

    with pytest.raises(ArtifactHashMismatchError, match="artifact hash mismatch"):
        GenerationStore(tmp_path).load_native_resolve_checkpoint(
            expected_operation_key=basis.operation_key
        )
