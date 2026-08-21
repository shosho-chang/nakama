"""Typed, content-addressed recovery state for ``PodcastSubtitleV2.create``.

A Create Checkpoint is deliberately not a Generation and never becomes
transcript truth.  It only proves that an expensive prefix of ``create`` was
completed for one exact request/Adapter binding, and addresses every byte
needed to replay and independently verify that prefix in a fresh process.
"""

from __future__ import annotations

import re
from datetime import datetime, timezone
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
EVIDENCE_PREFIX_MIGRATION_ARTIFACT = (
    "checkpoint/evidence_prefix_migration_v1.json"
)
EVIDENCE_PREFIX_MIGRATION_LEDGER_PREFIX = "checkpoint/evidence_prefix_migrations/"
NATIVE_AUDIT_BASIS_MIGRATION_PREFIX = "checkpoint/native_audit_basis_migrations/"


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


class EvidencePrefixMigrationReceiptV1(_CheckpointContract):
    """Authorization to rebind one verified evidence prefix to V3 policy identity."""

    schema_version: Literal[1] = 1
    id: str
    episode_id: str
    old_checkpoint_id: str
    old_checkpoint_record_hash: str
    old_operation_key: str
    new_operation_key: str
    old_input_binding_hash: str
    new_input_binding_hash: str
    old_policy_hash: str
    new_policy_hash: str
    old_code_hash: str
    new_code_hash: str
    old_adapter_identity_set_hash: str
    new_adapter_identity_set_hash: str
    evidence_artifact_set_hash: str
    reason_code: Literal["selective_audio_v3_cutover"] = "selective_audio_v3_cutover"
    migrated_at_utc: str
    operator: str
    authority: Literal[
        "evidence_prefix_rebind_only_no_evidence_mutation_no_audit_or_release_approval"
    ] = "evidence_prefix_rebind_only_no_evidence_mutation_no_audit_or_release_approval"
    content_hash: str

    @field_validator(
        "old_checkpoint_record_hash",
        "old_operation_key",
        "new_operation_key",
        "old_input_binding_hash",
        "new_input_binding_hash",
        "old_policy_hash",
        "new_policy_hash",
        "old_code_hash",
        "new_code_hash",
        "old_adapter_identity_set_hash",
        "new_adapter_identity_set_hash",
        "evidence_artifact_set_hash",
    )
    @classmethod
    def _migration_hash(cls, value: str) -> str:
        if not _SHA256_RE.fullmatch(value):
            raise ValueError("evidence-prefix migration hashes must be lowercase SHA-256")
        return value

    @field_validator("episode_id", "operator")
    @classmethod
    def _migration_text(cls, value: str) -> str:
        if not value.strip() or value != value.strip():
            raise ValueError("evidence-prefix migration text must be non-blank and trimmed")
        return value

    @field_validator("old_checkpoint_id")
    @classmethod
    def _old_checkpoint_id(cls, value: str) -> str:
        if not _CHECKPOINT_ID_RE.fullmatch(value):
            raise ValueError("evidence-prefix migration checkpoint ID is invalid")
        return value

    @field_validator("migrated_at_utc")
    @classmethod
    def _utc_timestamp(cls, value: str) -> str:
        try:
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError as exc:
            raise ValueError("migration timestamp must be ISO-8601 UTC") from exc
        if parsed.tzinfo is None or parsed.utcoffset() != timezone.utc.utcoffset(parsed):
            raise ValueError("migration timestamp must be ISO-8601 UTC")
        canonical = parsed.astimezone(timezone.utc).isoformat(timespec="seconds").replace(
            "+00:00", "Z"
        )
        if value != canonical:
            raise ValueError("migration timestamp must use canonical second-precision UTC")
        return value

    @model_validator(mode="after")
    def _migration_identity(self) -> "EvidencePrefixMigrationReceiptV1":
        if (
            self.old_operation_key == self.new_operation_key
            or self.old_input_binding_hash == self.new_input_binding_hash
            or self.old_policy_hash == self.new_policy_hash
        ):
            raise ValueError("evidence-prefix migration must bind a real cutover identity change")
        expected = hash_object(self.model_dump(mode="json", exclude={"id", "content_hash"}))
        if self.id != expected or self.content_hash != expected:
            raise ValueError("evidence-prefix migration identity/content hash mismatch")
        return self


class EvidencePrefixMigrationReceiptV2(_CheckpointContract):
    """One append-only identity refresh in an evidence-prefix migration ledger."""

    schema_version: Literal[2] = 2
    id: str
    sequence: int
    previous_migration_receipt_id: str
    episode_id: str
    old_checkpoint_id: str
    old_checkpoint_record_hash: str
    old_operation_key: str
    new_operation_key: str
    old_input_binding_hash: str
    new_input_binding_hash: str
    old_policy_hash: str
    new_policy_hash: str
    old_code_hash: str
    new_code_hash: str
    old_adapter_identity_set_hash: str
    new_adapter_identity_set_hash: str
    predecessor_artifact_set_hash: str
    evidence_artifact_set_hash: str
    reason_code: Literal["evidence_prefix_identity_refresh"] = (
        "evidence_prefix_identity_refresh"
    )
    migrated_at_utc: str
    operator: str
    authority: Literal[
        "evidence_prefix_rebind_only_no_evidence_mutation_no_audit_or_release_approval"
    ] = "evidence_prefix_rebind_only_no_evidence_mutation_no_audit_or_release_approval"
    content_hash: str

    @field_validator(
        "previous_migration_receipt_id",
        "old_checkpoint_record_hash",
        "old_operation_key",
        "new_operation_key",
        "old_input_binding_hash",
        "new_input_binding_hash",
        "old_policy_hash",
        "new_policy_hash",
        "old_code_hash",
        "new_code_hash",
        "old_adapter_identity_set_hash",
        "new_adapter_identity_set_hash",
        "predecessor_artifact_set_hash",
        "evidence_artifact_set_hash",
    )
    @classmethod
    def _refresh_hash(cls, value: str) -> str:
        if not _SHA256_RE.fullmatch(value):
            raise ValueError("evidence-prefix refresh hashes must be lowercase SHA-256")
        return value

    @field_validator("episode_id", "operator")
    @classmethod
    def _refresh_text(cls, value: str) -> str:
        return EvidencePrefixMigrationReceiptV1._migration_text(value)

    @field_validator("old_checkpoint_id")
    @classmethod
    def _refresh_checkpoint_id(cls, value: str) -> str:
        return EvidencePrefixMigrationReceiptV1._old_checkpoint_id(value)

    @field_validator("migrated_at_utc")
    @classmethod
    def _refresh_timestamp(cls, value: str) -> str:
        return EvidencePrefixMigrationReceiptV1._utc_timestamp(value)

    @field_validator("sequence")
    @classmethod
    def _refresh_sequence(cls, value: int) -> int:
        if value < 2:
            raise ValueError("evidence-prefix refresh sequence must start at two")
        return value

    @model_validator(mode="after")
    def _refresh_identity(self) -> "EvidencePrefixMigrationReceiptV2":
        if (
            self.old_operation_key == self.new_operation_key
            or self.old_input_binding_hash == self.new_input_binding_hash
            or (
                self.old_policy_hash == self.new_policy_hash
                and self.old_code_hash == self.new_code_hash
                and self.old_adapter_identity_set_hash
                == self.new_adapter_identity_set_hash
            )
        ):
            raise ValueError("evidence-prefix refresh must bind a real identity change")
        expected = hash_object(self.model_dump(mode="json", exclude={"id", "content_hash"}))
        if self.id != expected or self.content_hash != expected:
            raise ValueError("evidence-prefix refresh identity/content hash mismatch")
        return self


class NativeAuditBasisMigrationReceiptV1(_CheckpointContract):
    """Authorize one code-only identity refresh of an immutable audit basis."""

    schema_version: Literal[1] = 1
    id: str
    episode_id: str
    old_checkpoint_id: str
    old_checkpoint_record_hash: str
    old_operation_key: str
    new_operation_key: str
    old_input_binding_hash: str
    new_input_binding_hash: str
    old_policy_hash: str
    new_policy_hash: str
    old_code_hash: str
    new_code_hash: str
    old_adapter_identity_set_hash: str
    new_adapter_identity_set_hash: str
    predecessor_artifact_set_hash: str
    basis_artifact_set_hash: str
    reason_code: Literal["native_audit_basis_code_identity_refresh"] = (
        "native_audit_basis_code_identity_refresh"
    )
    migrated_at_utc: str
    operator: str
    authority: Literal[
        "basis_code_rebind_only_no_artifact_mutation_no_audit_or_release_approval"
    ] = "basis_code_rebind_only_no_artifact_mutation_no_audit_or_release_approval"
    content_hash: str

    @field_validator(
        "old_checkpoint_record_hash",
        "old_operation_key",
        "new_operation_key",
        "old_input_binding_hash",
        "new_input_binding_hash",
        "old_policy_hash",
        "new_policy_hash",
        "old_code_hash",
        "new_code_hash",
        "old_adapter_identity_set_hash",
        "new_adapter_identity_set_hash",
        "predecessor_artifact_set_hash",
        "basis_artifact_set_hash",
    )
    @classmethod
    def _basis_hash(cls, value: str) -> str:
        return EvidencePrefixMigrationReceiptV1._migration_hash(value)

    @field_validator("episode_id", "operator")
    @classmethod
    def _basis_text(cls, value: str) -> str:
        return EvidencePrefixMigrationReceiptV1._migration_text(value)

    @field_validator("old_checkpoint_id")
    @classmethod
    def _basis_checkpoint_id(cls, value: str) -> str:
        return EvidencePrefixMigrationReceiptV1._old_checkpoint_id(value)

    @field_validator("migrated_at_utc")
    @classmethod
    def _basis_timestamp(cls, value: str) -> str:
        return EvidencePrefixMigrationReceiptV1._utc_timestamp(value)

    @model_validator(mode="after")
    def _basis_identity(self) -> "NativeAuditBasisMigrationReceiptV1":
        if (
            self.old_code_hash == self.new_code_hash
            or self.old_operation_key == self.new_operation_key
            or self.old_input_binding_hash != self.new_input_binding_hash
            or self.old_policy_hash != self.new_policy_hash
            or self.old_adapter_identity_set_hash != self.new_adapter_identity_set_hash
        ):
            raise ValueError("native audit basis migration must be a code-only identity refresh")
        expected = hash_object(self.model_dump(mode="json", exclude={"id", "content_hash"}))
        if self.id != expected or self.content_hash != expected:
            raise ValueError("native audit basis migration identity/content hash mismatch")
        return self


def native_audit_basis_migration_artifact(receipt_id: str) -> str:
    if not _SHA256_RE.fullmatch(receipt_id):
        raise ValueError("native audit basis migration receipt ID is invalid")
    return f"{NATIVE_AUDIT_BASIS_MIGRATION_PREFIX}{receipt_id}.json"


def is_native_audit_basis_migration_artifact(name: str) -> bool:
    if not name.startswith(NATIVE_AUDIT_BASIS_MIGRATION_PREFIX) or not name.endswith(".json"):
        return False
    receipt_id = name[len(NATIVE_AUDIT_BASIS_MIGRATION_PREFIX) : -len(".json")]
    return bool(_SHA256_RE.fullmatch(receipt_id))


def build_native_audit_basis_migration_receipt(
    **values: object,
) -> NativeAuditBasisMigrationReceiptV1:
    payload = {
        "schema_version": 1,
        **values,
        "reason_code": "native_audit_basis_code_identity_refresh",
        "authority": (
            "basis_code_rebind_only_no_artifact_mutation_no_audit_or_release_approval"
        ),
    }
    digest = hash_object(payload)
    return NativeAuditBasisMigrationReceiptV1(
        **payload,
        id=digest,
        content_hash=digest,
    )


def evidence_prefix_migration_artifact(receipt_id: str) -> str:
    """Return the content-addressed artifact name for a V2 ledger receipt."""

    if not _SHA256_RE.fullmatch(receipt_id):
        raise ValueError("evidence-prefix migration receipt ID is invalid")
    return f"{EVIDENCE_PREFIX_MIGRATION_LEDGER_PREFIX}{receipt_id}.json"


def is_evidence_prefix_migration_artifact(name: str) -> bool:
    if name == EVIDENCE_PREFIX_MIGRATION_ARTIFACT:
        return True
    if not name.startswith(EVIDENCE_PREFIX_MIGRATION_LEDGER_PREFIX) or not name.endswith(
        ".json"
    ):
        return False
    receipt_id = name[len(EVIDENCE_PREFIX_MIGRATION_LEDGER_PREFIX) : -len(".json")]
    return bool(_SHA256_RE.fullmatch(receipt_id))


def build_evidence_prefix_migration_refresh_receipt(
    *,
    sequence: int,
    previous_migration_receipt_id: str,
    episode_id: str,
    old_checkpoint_id: str,
    old_checkpoint_record_hash: str,
    old_operation_key: str,
    new_operation_key: str,
    old_input_binding_hash: str,
    new_input_binding_hash: str,
    old_policy_hash: str,
    new_policy_hash: str,
    old_code_hash: str,
    new_code_hash: str,
    old_adapter_identity_set_hash: str,
    new_adapter_identity_set_hash: str,
    predecessor_artifact_set_hash: str,
    evidence_artifact_set_hash: str,
    migrated_at_utc: str,
    operator: str,
) -> EvidencePrefixMigrationReceiptV2:
    payload = {
        "schema_version": 2,
        "sequence": sequence,
        "previous_migration_receipt_id": previous_migration_receipt_id,
        "episode_id": episode_id,
        "old_checkpoint_id": old_checkpoint_id,
        "old_checkpoint_record_hash": old_checkpoint_record_hash,
        "old_operation_key": old_operation_key,
        "new_operation_key": new_operation_key,
        "old_input_binding_hash": old_input_binding_hash,
        "new_input_binding_hash": new_input_binding_hash,
        "old_policy_hash": old_policy_hash,
        "new_policy_hash": new_policy_hash,
        "old_code_hash": old_code_hash,
        "new_code_hash": new_code_hash,
        "old_adapter_identity_set_hash": old_adapter_identity_set_hash,
        "new_adapter_identity_set_hash": new_adapter_identity_set_hash,
        "predecessor_artifact_set_hash": predecessor_artifact_set_hash,
        "evidence_artifact_set_hash": evidence_artifact_set_hash,
        "reason_code": "evidence_prefix_identity_refresh",
        "migrated_at_utc": migrated_at_utc,
        "operator": operator,
        "authority": (
            "evidence_prefix_rebind_only_no_evidence_mutation_no_audit_or_release_approval"
        ),
    }
    digest = hash_object(payload)
    return EvidencePrefixMigrationReceiptV2(**payload, id=digest, content_hash=digest)


def build_evidence_prefix_migration_receipt(
    *,
    episode_id: str,
    old_checkpoint_id: str,
    old_checkpoint_record_hash: str,
    old_operation_key: str,
    new_operation_key: str,
    old_input_binding_hash: str,
    new_input_binding_hash: str,
    old_policy_hash: str,
    new_policy_hash: str,
    old_code_hash: str,
    new_code_hash: str,
    old_adapter_identity_set_hash: str,
    new_adapter_identity_set_hash: str,
    evidence_artifact_set_hash: str,
    migrated_at_utc: str,
    operator: str,
) -> EvidencePrefixMigrationReceiptV1:
    payload = {
        "schema_version": 1,
        "episode_id": episode_id,
        "old_checkpoint_id": old_checkpoint_id,
        "old_checkpoint_record_hash": old_checkpoint_record_hash,
        "old_operation_key": old_operation_key,
        "new_operation_key": new_operation_key,
        "old_input_binding_hash": old_input_binding_hash,
        "new_input_binding_hash": new_input_binding_hash,
        "old_policy_hash": old_policy_hash,
        "new_policy_hash": new_policy_hash,
        "old_code_hash": old_code_hash,
        "new_code_hash": new_code_hash,
        "old_adapter_identity_set_hash": old_adapter_identity_set_hash,
        "new_adapter_identity_set_hash": new_adapter_identity_set_hash,
        "evidence_artifact_set_hash": evidence_artifact_set_hash,
        "reason_code": "selective_audio_v3_cutover",
        "migrated_at_utc": migrated_at_utc,
        "operator": operator,
        "authority": (
            "evidence_prefix_rebind_only_no_evidence_mutation_no_audit_or_release_approval"
        ),
    }
    digest = hash_object(payload)
    return EvidencePrefixMigrationReceiptV1(
        **payload,
        id=digest,
        content_hash=digest,
    )


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
    "EVIDENCE_PREFIX_MIGRATION_ARTIFACT",
    "EVIDENCE_PREFIX_MIGRATION_LEDGER_PREFIX",
    "EvidencePrefixMigrationReceiptV1",
    "EvidencePrefixMigrationReceiptV2",
    "NativeAuditBasisMigrationReceiptV1",
    "build_create_checkpoint",
    "build_evidence_prefix_migration_receipt",
    "build_evidence_prefix_migration_refresh_receipt",
    "build_native_audit_basis_migration_receipt",
    "checkpoint_content_identity",
    "evidence_prefix_migration_artifact",
    "is_evidence_prefix_migration_artifact",
    "is_native_audit_basis_migration_artifact",
    "native_audit_basis_migration_artifact",
]
