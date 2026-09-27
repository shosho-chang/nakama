from __future__ import annotations

import json
from pathlib import Path

import pytest
from pydantic import ValidationError

import agents.brook.podcast_subtitles.store as checkpoint_store_module
from agents.brook.podcast_subtitles.checkpoint import (
    CheckpointAdapterIdentity,
    build_create_checkpoint,
)
from agents.brook.podcast_subtitles.errors import (
    ArtifactHashMismatchError,
    GenerationIsolationError,
)
from agents.brook.podcast_subtitles.hashing import hash_object, sha256_bytes
from agents.brook.podcast_subtitles.store import GenerationStore
from shared.schemas.podcast_subtitles_v2 import ArtifactDigest


def _digest(label: str, payload: bytes) -> ArtifactDigest:
    return ArtifactDigest(
        uri=f"generation-artifact://checkpoint/{label}",
        sha256=sha256_bytes(payload),
        size_bytes=len(payload),
    )


def _identities(config: str = "v1") -> tuple[CheckpointAdapterIdentity, ...]:
    return (
        CheckpointAdapterIdentity(
            role="normalizer",
            ordinal=0,
            adapter_name="fixture-normalizer",
            adapter_version="1",
            config_hash=hash_object({"config": config}),
            code_hash=hash_object({"code": "normalizer"}),
            runtime_hash=hash_object({"runtime": "python"}),
            execution_mode="fixture",
        ),
        CheckpointAdapterIdentity(
            role="recognizer",
            ordinal=0,
            adapter_name="fixture-recognizer",
            adapter_version="1",
            config_hash=hash_object({"config": "recognizer"}),
            code_hash=hash_object({"code": "recognizer"}),
            runtime_hash=hash_object({"runtime": "python"}),
            execution_mode="fixture",
        ),
    )


def _checkpoint(
    *,
    stage: str = "evidence_ready",
    artifacts: dict[str, bytes] | None = None,
    operation_key: str | None = None,
    previous_checkpoint_id: str | None = None,
    generation_id: str | None = None,
    complete_binding: bool = True,
):
    payloads = artifacts or {"state.json": b'{"stage":"evidence"}'}
    source = b"source"
    normalized = b"normalized"
    started = _started_checkpoint(
        artifacts={"checkpoint/create_intent.json": b'{"operation":"started"}'},
        operation_key=operation_key,
    )
    return build_create_checkpoint(
        operation_key=operation_key or hash_object({"operation": "episode"}),
        episode_id="episode-checkpoint",
        stage=stage,  # type: ignore[arg-type]
        invocation_id="recognition-fixture",
        source=_digest("source", source),
        normalized=_digest("normalized", normalized),
        normalization_receipt_hash=hash_object({"receipt": 1}),
        input_binding_hash=hash_object({"input": 1}),
        policy_hash=hash_object({"policy": 1}),
        code_hash=hash_object({"code": 1}),
        reference_enrollment_hash=hash_object(()),
        speaker_track_binding_hash=hash_object(()),
        adapter_identities=_identities(),
        artifact_hashes={name: sha256_bytes(value) for name, value in sorted(payloads.items())},
        previous_checkpoint_id=previous_checkpoint_id or started.id,
        generation_id=generation_id,
        generation_artifact_set_hash=("1" * 64 if generation_id and complete_binding else None),
        generation_record_hash=("2" * 64 if generation_id and complete_binding else None),
        generation_manifest_hash=("3" * 64 if generation_id and complete_binding else None),
        generation_transcript_hash=("4" * 64 if generation_id and complete_binding else None),
        generation_outcome=("accepted" if generation_id and complete_binding else None),
    )


def _started_checkpoint(
    *,
    artifacts: dict[str, bytes],
    operation_key: str | None = None,
    expected_active_generation_id: str | None = None,
):
    source = b"source"
    return build_create_checkpoint(
        operation_key=operation_key or hash_object({"operation": "episode"}),
        episode_id="episode-checkpoint",
        stage="started",
        invocation_id=None,
        source=_digest("source", source),
        normalized=None,
        normalization_receipt_hash=None,
        input_binding_hash=hash_object({"input": 1}),
        policy_hash=hash_object({"policy": 1}),
        code_hash=hash_object({"code": 1}),
        reference_enrollment_hash=hash_object(()),
        speaker_track_binding_hash=hash_object(()),
        adapter_identities=_identities(),
        artifact_hashes={name: sha256_bytes(value) for name, value in sorted(artifacts.items())},
        expected_active_generation_id=expected_active_generation_id,
    )


def _commit_started(store: GenerationStore, checkpoint) -> None:
    payloads = {"checkpoint/create_intent.json": b'{"operation":"started"}'}
    started = _started_checkpoint(
        artifacts=payloads,
        operation_key=checkpoint.operation_key,
    )
    assert checkpoint.previous_checkpoint_id == started.id
    store.commit_create_checkpoint(started, artifacts=payloads)


def test_create_checkpoint_round_trips_and_reverifies_in_fresh_process(
    tmp_path: Path,
) -> None:
    artifacts = {
        "evidence.json": b'[{"id":"evidence"}]',
        "raw/one.json": b'{"provider":"exact"}',
    }
    checkpoint = _checkpoint(artifacts=artifacts)
    first = GenerationStore(tmp_path)

    _commit_started(first, checkpoint)
    stored = first.commit_create_checkpoint(checkpoint, artifacts=artifacts)
    fresh = GenerationStore(tmp_path)
    loaded = fresh.load_create_checkpoint(expected_operation_key=checkpoint.operation_key)

    assert loaded is not None
    assert loaded.checkpoint == checkpoint
    assert (
        fresh.read_create_checkpoint_artifact(loaded, "raw/one.json") == artifacts["raw/one.json"]
    )
    assert stored.directory == loaded.directory


def test_verified_store_session_does_not_repeat_full_stage_hashing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    artifacts = {f"raw/{index}.json": f'{{"index":{index}}}'.encode() for index in range(12)}
    checkpoint = _checkpoint(artifacts=artifacts)
    writer = GenerationStore(tmp_path)
    _commit_started(writer, checkpoint)
    writer.commit_create_checkpoint(checkpoint, artifacts=artifacts)
    calls: list[Path] = []
    real_hash_file = checkpoint_store_module.hash_file

    def counted(path: Path) -> str:
        calls.append(path)
        return real_hash_file(path)

    monkeypatch.setattr(checkpoint_store_module, "hash_file", counted)
    reader = GenerationStore(tmp_path)
    loaded = reader.load_create_checkpoint(expected_operation_key=checkpoint.operation_key)
    assert loaded is not None
    full_open_calls = len(calls)
    assert full_open_calls == len(artifacts) + 1

    for name, payload in artifacts.items():
        assert reader.read_create_checkpoint_artifact(loaded, name) == payload

    # Each read re-hashes only the small checkpoint record; the requested bytes
    # are hashed from the payload after the read and never trigger a stage scan.
    assert len(calls) == full_open_calls + len(artifacts)


def test_verified_store_session_rejects_requested_artifact_and_record_tamper(
    tmp_path: Path,
) -> None:
    artifacts = {"raw/one.json": b'{"one":1}', "raw/two.json": b'{"two":2}'}
    checkpoint = _checkpoint(artifacts=artifacts)
    store = GenerationStore(tmp_path)
    _commit_started(store, checkpoint)
    loaded = store.commit_create_checkpoint(checkpoint, artifacts=artifacts)

    (loaded.directory / "raw/one.json").write_bytes(b'{"one":"tampered"}')
    with pytest.raises(ArtifactHashMismatchError, match="changed after verification"):
        store.read_create_checkpoint_artifact(loaded, "raw/one.json")

    (loaded.directory / "raw/one.json").write_bytes(artifacts["raw/one.json"])
    (loaded.directory / "raw/two.json").write_bytes(b'{"two":"tampered"}')
    with pytest.raises(ArtifactHashMismatchError, match="artifact hash mismatch"):
        store.read_create_checkpoint_artifact(loaded, "raw/one.json")

    (loaded.directory / "raw/two.json").write_bytes(artifacts["raw/two.json"])
    loaded = store.load_create_checkpoint(expected_operation_key=checkpoint.operation_key)
    assert loaded is not None
    (loaded.directory / "checkpoint.json").write_bytes(b"{}")
    with pytest.raises(ArtifactHashMismatchError, match="record changed"):
        store.read_create_checkpoint_artifact(loaded, "raw/two.json")


def test_fresh_store_rejects_non_requested_artifact_tamper(tmp_path: Path) -> None:
    artifacts = {"raw/one.json": b'{"one":1}', "raw/two.json": b'{"two":2}'}
    checkpoint = _checkpoint(artifacts=artifacts)
    store = GenerationStore(tmp_path)
    _commit_started(store, checkpoint)
    loaded = store.commit_create_checkpoint(checkpoint, artifacts=artifacts)

    (loaded.directory / "raw/two.json").write_bytes(b'{"two":"tampered"}')
    with pytest.raises(ArtifactHashMismatchError, match="artifact hash mismatch"):
        GenerationStore(tmp_path).load_create_checkpoint(
            expected_operation_key=checkpoint.operation_key
        )


def test_create_checkpoint_rejects_input_or_adapter_binding_drift(tmp_path: Path) -> None:
    checkpoint = _checkpoint()
    store = GenerationStore(tmp_path)
    _commit_started(store, checkpoint)
    store.commit_create_checkpoint(
        checkpoint,
        artifacts={"state.json": b'{"stage":"evidence"}'},
    )

    with pytest.raises(GenerationIsolationError, match="different source"):
        store.load_create_checkpoint(
            expected_operation_key=hash_object({"operation": "changed-config"})
        )


def test_create_checkpoint_rejects_artifact_and_record_tamper(tmp_path: Path) -> None:
    artifacts = {"state.json": b'{"stage":"evidence"}'}
    checkpoint = _checkpoint(artifacts=artifacts)
    store = GenerationStore(tmp_path)
    _commit_started(store, checkpoint)
    stored = store.commit_create_checkpoint(checkpoint, artifacts=artifacts)

    (stored.directory / "state.json").write_bytes(b'{"stage":"tampered"}')
    with pytest.raises(ArtifactHashMismatchError, match="artifact hash mismatch"):
        GenerationStore(tmp_path).load_create_checkpoint(
            expected_operation_key=checkpoint.operation_key
        )


def test_create_checkpoint_pointer_advances_only_on_exact_stage_lineage(
    tmp_path: Path,
) -> None:
    artifacts = {"state.json": b'{"stage":"evidence"}'}
    first = _checkpoint(artifacts=artifacts)
    store = GenerationStore(tmp_path)
    _commit_started(store, first)
    store.commit_create_checkpoint(first, artifacts=artifacts)
    second_artifacts = {"state.json": b'{"stage":"correction"}'}
    second = _checkpoint(
        stage="correction_ready",
        artifacts=second_artifacts,
        previous_checkpoint_id=first.id,
    )
    store.commit_create_checkpoint(second, artifacts=second_artifacts)

    fork = _checkpoint(
        stage="audio_audit_ready",
        artifacts={"state.json": b'{"stage":"audio"}'},
        previous_checkpoint_id=first.id,
    )
    with pytest.raises(GenerationIsolationError, match="lineage forked"):
        store.commit_create_checkpoint(
            fork,
            artifacts={"state.json": b'{"stage":"audio"}'},
        )


def test_complete_checkpoint_requires_generation_and_id_covers_all_fields() -> None:
    with pytest.raises(ValidationError, match="complete Generation binding"):
        _checkpoint(stage="complete")

    complete = _checkpoint(
        stage="complete",
        generation_id="generation-" + "a" * 64,
    )
    payload = complete.model_dump(mode="json")
    payload["policy_hash"] = "b" * 64
    with pytest.raises(ValidationError, match="does not address"):
        type(complete).model_validate_json(json.dumps(payload))


def test_started_checkpoint_captures_active_parent_before_external_work() -> None:
    payload = b'{"operation":"started"}'
    started = _started_checkpoint(
        artifacts={"checkpoint/create_intent.json": payload},
        expected_active_generation_id="generation-" + "a" * 64,
    )

    assert started.stage == "started"
    assert started.expected_active_generation_id == "generation-" + "a" * 64
    assert started.previous_checkpoint_id is None
    assert started.generation_id is None


def test_complete_checkpoint_requires_exact_generation_storage_binding() -> None:
    with pytest.raises(ValidationError, match="complete Generation binding"):
        _checkpoint(
            stage="complete",
            generation_id="generation-" + "a" * 64,
            complete_binding=False,
        )


def test_pointer_tamper_is_rejected_before_checkpoint_use(tmp_path: Path) -> None:
    checkpoint = _checkpoint()
    store = GenerationStore(tmp_path)
    _commit_started(store, checkpoint)
    store.commit_create_checkpoint(
        checkpoint,
        artifacts={"state.json": b'{"stage":"evidence"}'},
    )
    pointer = json.loads(store.create_checkpoint_pointer_path.read_text(encoding="utf-8"))
    pointer["operation_key"] = "0" * 64
    store.create_checkpoint_pointer_path.write_text(json.dumps(pointer), encoding="utf-8")

    with pytest.raises(GenerationIsolationError, match="pointer hash mismatch"):
        store.load_create_checkpoint(expected_operation_key=checkpoint.operation_key)


def test_new_operation_can_start_only_after_terminal_checkpoint(
    tmp_path: Path,
) -> None:
    store = GenerationStore(tmp_path)
    evidence_artifacts = {"state.json": b'{"stage":"evidence"}'}
    evidence = _checkpoint(artifacts=evidence_artifacts)
    _commit_started(store, evidence)
    store.commit_create_checkpoint(evidence, artifacts=evidence_artifacts)
    correction_artifacts = {"state.json": b'{"stage":"correction"}'}
    correction = _checkpoint(
        stage="correction_ready",
        artifacts=correction_artifacts,
        previous_checkpoint_id=evidence.id,
    )
    store.commit_create_checkpoint(correction, artifacts=correction_artifacts)
    audio_artifacts = {"state.json": b'{"stage":"audio"}'}
    audio = _checkpoint(
        stage="audio_audit_ready",
        artifacts=audio_artifacts,
        previous_checkpoint_id=correction.id,
    )
    store.commit_create_checkpoint(audio, artifacts=audio_artifacts)
    complete_artifacts = {"state.json": b'{"stage":"complete"}'}
    complete = _checkpoint(
        stage="complete",
        artifacts=complete_artifacts,
        previous_checkpoint_id=audio.id,
        generation_id="generation-" + "a" * 64,
    )
    store.commit_create_checkpoint(complete, artifacts=complete_artifacts)
    active = store.commit(
        manifest={"stage": "active"},
        artifacts={"value.json": {"active": True}},
    )
    store.set_active(active.generation_id)
    next_artifacts = {"checkpoint/create_intent.json": b'{"operation":"started"}'}
    next_started = _started_checkpoint(
        artifacts=next_artifacts,
        operation_key=hash_object({"operation": "next"}),
        expected_active_generation_id=active.generation_id,
    )

    stored = store.commit_create_checkpoint(next_started, artifacts=next_artifacts)

    assert stored.checkpoint.expected_active_generation_id == active.generation_id
    assert store.load_latest_create_checkpoint() == stored


def test_new_operation_cannot_replace_an_incomplete_checkpoint(tmp_path: Path) -> None:
    store = GenerationStore(tmp_path)
    first_artifacts = {"checkpoint/create_intent.json": b'{"operation":"started"}'}
    first = _started_checkpoint(artifacts=first_artifacts)
    store.commit_create_checkpoint(first, artifacts=first_artifacts)
    second_artifacts = {"checkpoint/create_intent.json": b'{"operation":"second"}'}
    second = _started_checkpoint(
        artifacts=second_artifacts,
        operation_key=hash_object({"operation": "second"}),
    )

    with pytest.raises(GenerationIsolationError, match="lineage forked"):
        store.commit_create_checkpoint(second, artifacts=second_artifacts)


def test_checkpoint_store_rejects_skipped_stage_and_inherited_parent_drift(
    tmp_path: Path,
) -> None:
    store = GenerationStore(tmp_path)
    evidence_artifacts = {"state.json": b'{"stage":"evidence"}'}
    evidence = _checkpoint(artifacts=evidence_artifacts)
    _commit_started(store, evidence)
    store.commit_create_checkpoint(evidence, artifacts=evidence_artifacts)
    skipped_artifacts = {"state.json": b'{"stage":"audio"}'}
    skipped = _checkpoint(
        stage="audio_audit_ready",
        artifacts=skipped_artifacts,
        previous_checkpoint_id=evidence.id,
    )
    with pytest.raises(GenerationIsolationError, match="not contiguous"):
        store.commit_create_checkpoint(skipped, artifacts=skipped_artifacts)

    drifted_artifacts = {"state.json": b'{"stage":"correction"}'}
    drifted = build_create_checkpoint(
        operation_key=evidence.operation_key,
        episode_id=evidence.episode_id,
        stage="correction_ready",
        invocation_id=evidence.invocation_id,
        source=evidence.source,
        normalized=evidence.normalized,
        normalization_receipt_hash=evidence.normalization_receipt_hash,
        input_binding_hash=evidence.input_binding_hash,
        policy_hash="f" * 64,
        code_hash=evidence.code_hash,
        reference_enrollment_hash=evidence.reference_enrollment_hash,
        speaker_track_binding_hash=evidence.speaker_track_binding_hash,
        adapter_identities=evidence.adapter_identities,
        artifact_hashes={"state.json": sha256_bytes(drifted_artifacts["state.json"])},
        expected_active_generation_id=evidence.expected_active_generation_id,
        previous_checkpoint_id=evidence.id,
    )
    with pytest.raises(GenerationIsolationError, match="inherited identity"):
        store.commit_create_checkpoint(drifted, artifacts=drifted_artifacts)
