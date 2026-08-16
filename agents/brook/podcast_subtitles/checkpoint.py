"""Typed, content-addressed recovery state for ``PodcastSubtitleV2.create``.

A Create Checkpoint is deliberately not a Generation and never becomes
transcript truth.  It only proves that an expensive prefix of ``create`` was
completed for one exact request/Adapter binding, and addresses every byte
needed to replay and independently verify that prefix in a fresh process.
"""

from __future__ import annotations

import re
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from shared.schemas.podcast_subtitles_v2 import ArtifactDigest

from .hashing import hash_object

CheckpointStage = Literal[
    "started",
    "evidence_ready",
    "native_audit_basis_ready",
    "native_text_audit_ready",
    "native_audio_audit_ready",
    "correction_ready",
    "audio_audit_ready",
    "complete",
]

_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
_CHECKPOINT_ID_RE = re.compile(r"^create-checkpoint-[0-9a-f]{64}$")
_GENERATION_ID_RE = re.compile(r"^(?:[0-9a-f]{64}|generation-[0-9a-f]{64})$")


class _CheckpointContract(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)


class CheckpointAdapterIdentity(_CheckpointContract):
    """One measured executable Adapter identity bound to a create attempt."""

    schema_version: Literal[1] = 1
    role: str
    ordinal: int = Field(ge=0)
    adapter_name: str
    adapter_version: str
    config_hash: str
    code_hash: str
    runtime_hash: str
    execution_mode: Literal["fixture", "local", "paid_api", "subscription", "other"]

    @field_validator("role", "adapter_name", "adapter_version")
    @classmethod
    def _required_text(cls, value: str) -> str:
        if not value.strip() or value != value.strip():
            raise ValueError("checkpoint Adapter identity text must be non-blank and trimmed")
        return value

    @field_validator("config_hash", "code_hash", "runtime_hash")
    @classmethod
    def _required_hash(cls, value: str) -> str:
        if not _SHA256_RE.fullmatch(value):
            raise ValueError("checkpoint Adapter identity hashes must be lowercase SHA-256")
        return value

    @property
    def content_hash(self) -> str:
        return hash_object(self)


def checkpoint_content_identity(checkpoint: "CreateCheckpoint") -> dict[str, object]:
    """Return the exact payload addressed by ``checkpoint.id``."""

    return checkpoint.model_dump(mode="json", exclude={"id"}, exclude_none=False)


class CreateCheckpoint(_CheckpointContract):
    """Authenticated recovery boundary after one completed create stage."""

    schema_version: Literal[2] = 2
    id: str
    operation_key: str
    episode_id: str
    stage: CheckpointStage
    invocation_id: str | None
    source: ArtifactDigest
    normalized: ArtifactDigest | None
    normalization_receipt_hash: str | None
    input_binding_hash: str
    policy_hash: str
    code_hash: str
    reference_enrollment_hash: str
    speaker_track_binding_hash: str
    adapter_identities: tuple[CheckpointAdapterIdentity, ...]
    artifact_hashes: dict[str, str]
    expected_active_generation_id: str | None = None
    previous_checkpoint_id: str | None = None
    generation_id: str | None = None
    generation_artifact_set_hash: str | None = None
    generation_record_hash: str | None = None
    generation_manifest_hash: str | None = None
    generation_transcript_hash: str | None = None
    generation_outcome: Literal["accepted", "needs_review"] | None = None

    @field_validator("operation_key", "input_binding_hash")
    @classmethod
    def _identity_hash(cls, value: str) -> str:
        if not _SHA256_RE.fullmatch(value):
            raise ValueError("checkpoint identity must be lowercase SHA-256")
        return value

    @field_validator("normalization_receipt_hash")
    @classmethod
    def _optional_identity_hash(cls, value: str | None) -> str | None:
        if value is not None and not _SHA256_RE.fullmatch(value):
            raise ValueError("checkpoint identity must be lowercase SHA-256")
        return value

    @field_validator(
        "policy_hash",
        "code_hash",
        "reference_enrollment_hash",
        "speaker_track_binding_hash",
    )
    @classmethod
    def _binding_hash(cls, value: str) -> str:
        if not _SHA256_RE.fullmatch(value):
            raise ValueError("checkpoint binding must be lowercase SHA-256")
        return value

    @field_validator("episode_id")
    @classmethod
    def _required_text(cls, value: str) -> str:
        if not value.strip() or value != value.strip():
            raise ValueError("checkpoint text identity must be non-blank and trimmed")
        return value

    @field_validator("invocation_id")
    @classmethod
    def _optional_required_text(cls, value: str | None) -> str | None:
        if value is not None and (not value.strip() or value != value.strip()):
            raise ValueError("checkpoint text identity must be non-blank and trimmed")
        return value

    @field_validator(
        "generation_artifact_set_hash",
        "generation_record_hash",
        "generation_manifest_hash",
        "generation_transcript_hash",
    )
    @classmethod
    def _optional_generation_hash(cls, value: str | None) -> str | None:
        if value is not None and not _SHA256_RE.fullmatch(value):
            raise ValueError("complete Generation hashes must be lowercase SHA-256")
        return value

    @model_validator(mode="after")
    def _closed_identity(self) -> "CreateCheckpoint":
        if not self.adapter_identities:
            raise ValueError("Create Checkpoint requires every configured Adapter identity")
        keys = [(item.role, item.ordinal) for item in self.adapter_identities]
        if keys != sorted(keys) or len(set(keys)) != len(keys):
            raise ValueError("checkpoint Adapter identities must be unique and sorted")
        artifact_names = list(self.artifact_hashes)
        if artifact_names != sorted(artifact_names) or not artifact_names:
            raise ValueError("checkpoint artifact hashes must be non-empty and sorted")
        if any(not _SHA256_RE.fullmatch(value) for value in self.artifact_hashes.values()):
            raise ValueError("checkpoint artifact hashes must be lowercase SHA-256")
        if self.previous_checkpoint_id is not None and not _CHECKPOINT_ID_RE.fullmatch(
            self.previous_checkpoint_id
        ):
            raise ValueError("previous_checkpoint_id is invalid")
        for generation_id in (self.expected_active_generation_id, self.generation_id):
            if generation_id is not None and not _GENERATION_ID_RE.fullmatch(generation_id):
                raise ValueError("checkpoint Generation ID is invalid")
        if self.stage == "started":
            if (
                self.invocation_id is not None
                or self.normalized is not None
                or self.normalization_receipt_hash is not None
                or self.previous_checkpoint_id is not None
            ):
                raise ValueError("started Create Checkpoint cannot claim completed normalization")
        elif (
            self.invocation_id is None
            or self.normalized is None
            or self.normalization_receipt_hash is None
            or self.previous_checkpoint_id is None
        ):
            raise ValueError("post-start Create Checkpoint requires normalization and predecessor")
        complete_binding = (
            self.generation_id,
            self.generation_artifact_set_hash,
            self.generation_record_hash,
            self.generation_manifest_hash,
            self.generation_transcript_hash,
            self.generation_outcome,
        )
        if self.stage == "complete":
            if any(value is None for value in complete_binding):
                raise ValueError("complete Generation binding is incomplete")
        elif any(value is not None for value in complete_binding):
            raise ValueError("only a complete Create Checkpoint binds a Generation")
        expected = "create-checkpoint-" + hash_object(checkpoint_content_identity(self))
        if self.id != expected:
            raise ValueError("Create Checkpoint ID does not address its exact content")
        return self


def build_create_checkpoint(
    *,
    operation_key: str,
    episode_id: str,
    stage: CheckpointStage,
    invocation_id: str | None,
    source: ArtifactDigest,
    normalized: ArtifactDigest | None,
    normalization_receipt_hash: str | None,
    input_binding_hash: str,
    policy_hash: str,
    code_hash: str,
    reference_enrollment_hash: str,
    speaker_track_binding_hash: str,
    adapter_identities: tuple[CheckpointAdapterIdentity, ...],
    artifact_hashes: dict[str, str],
    expected_active_generation_id: str | None = None,
    previous_checkpoint_id: str | None = None,
    generation_id: str | None = None,
    generation_artifact_set_hash: str | None = None,
    generation_record_hash: str | None = None,
    generation_manifest_hash: str | None = None,
    generation_transcript_hash: str | None = None,
    generation_outcome: Literal["accepted", "needs_review"] | None = None,
) -> CreateCheckpoint:
    """Construct a checkpoint only through its canonical content identity."""

    payload = {
        "schema_version": 2,
        "operation_key": operation_key,
        "episode_id": episode_id,
        "stage": stage,
        "invocation_id": invocation_id,
        "source": source,
        "normalized": normalized,
        "normalization_receipt_hash": normalization_receipt_hash,
        "input_binding_hash": input_binding_hash,
        "policy_hash": policy_hash,
        "code_hash": code_hash,
        "reference_enrollment_hash": reference_enrollment_hash,
        "speaker_track_binding_hash": speaker_track_binding_hash,
        "adapter_identities": adapter_identities,
        "artifact_hashes": dict(sorted(artifact_hashes.items())),
        "expected_active_generation_id": expected_active_generation_id,
        "previous_checkpoint_id": previous_checkpoint_id,
        "generation_id": generation_id,
        "generation_artifact_set_hash": generation_artifact_set_hash,
        "generation_record_hash": generation_record_hash,
        "generation_manifest_hash": generation_manifest_hash,
        "generation_transcript_hash": generation_transcript_hash,
        "generation_outcome": generation_outcome,
    }
    checkpoint_id = "create-checkpoint-" + hash_object(payload)
    return CreateCheckpoint(id=checkpoint_id, **payload)


__all__ = [
    "CheckpointAdapterIdentity",
    "CheckpointStage",
    "CreateCheckpoint",
    "build_create_checkpoint",
    "checkpoint_content_identity",
]
