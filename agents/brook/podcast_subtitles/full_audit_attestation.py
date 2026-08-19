"""Content-addressed aggregate for native Text and Audio Full Audit.

The aggregate proves exact execution coverage over one frozen AuditPlan.  It
cannot express a Correction Proposal, Decision, arbitration verdict,
Canonical mutation, or release approval.
"""

from __future__ import annotations

from typing import Annotated, Literal

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    StringConstraints,
    ValidationError,
    model_validator,
)

from shared.schemas.podcast_subtitles_v2 import AuditPlan, CandidateGroupSet, CandidateSignalSet
from shared.schemas.podcast_subtitles_v2_audio_audit import AudioAuditExecutionRecordV2
from shared.schemas.podcast_subtitles_v2_correction import CorrectionAuditExecutionPlanV2
from shared.schemas.podcast_subtitles_v2_text_audit import TextAuditExecutionRecordV2

from .hashing import canonical_json_bytes, hash_object, sha256_bytes

Sha256 = Annotated[str, StringConstraints(pattern=r"^[0-9a-f]{64}$")]


class FullAuditAttestationError(ValueError):
    """Raised when full-audit parents or aggregate bytes are not exact."""


class FullAuditSourceArtifactV2(BaseModel):
    """Digest of one complete immutable parent object."""

    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    schema_version: Literal[2] = 2
    kind: Literal[
        "audit_plan",
        "candidate_signal_set",
        "candidate_group_set",
        "correction_audit_execution_plan",
        "text_audit_execution_record",
        "audio_audit_execution_record",
    ]
    artifact_sha256: Sha256
    object_content_hash: Sha256
    size_bytes: int = Field(ge=2)


class FullAuditAggregateAttestationV2(BaseModel):
    """Execution-completeness proof without any text-mutation authority."""

    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    schema_version: Literal[2] = 2
    id: Sha256
    episode_id: str
    generation_id: str
    audit_plan_hash: Sha256
    audit_plan_content_hash: Sha256
    audit_input_hash: Sha256
    audit_policy_hash: Sha256
    canonical_transcript_hash: Sha256
    canonical_content_hash: Sha256
    recognition_evidence_hashes: tuple[Sha256, ...]
    audit_recognition_evidence_set_hash: Sha256
    execution_recognition_evidence_artifact_hash: Sha256
    reference_evidence_hash: Sha256
    reference_retrieval_receipt_hashes: tuple[Sha256, ...]
    audit_reference_retrieval_receipt_set_hash: Sha256
    execution_reference_retrieval_receipt_artifact_hash: Sha256
    speech_coverage_receipt_hash: Sha256 | None
    boundary_constraint_receipt_hash: Sha256 | None
    seam_evidence_hash: Sha256 | None
    normalized_audio_hash: Sha256
    topology_policy_hash: Sha256
    candidate_signal_set_hash: Sha256
    candidate_signal_set_content_hash: Sha256
    candidate_group_set_hash: Sha256
    candidate_group_set_content_hash: Sha256
    execution_plan_id: Sha256
    execution_plan_content_hash: Sha256
    text_record_id: Sha256
    text_record_content_hash: Sha256
    text_policy_hash: Sha256
    text_adapter_identity_hash: Sha256
    audio_record_id: Sha256
    audio_record_content_hash: Sha256
    audio_policy_hash: Sha256
    audio_adapter_identity_hash: Sha256
    all_cell_ids: tuple[Sha256, ...]
    required_cell_ids: tuple[Sha256, ...]
    text_disposition_ids: tuple[Sha256, ...]
    audio_disposition_ids: tuple[Sha256, ...]
    text_discovery_ids: tuple[Sha256, ...]
    audio_discovery_ids: tuple[Sha256, ...]
    source_artifacts: tuple[FullAuditSourceArtifactV2, ...]
    execution_scope: Literal["all_plan_cells_both_modalities_dispositioned"] = (
        "all_plan_cells_both_modalities_dispositioned"
    )
    authority: Literal[
        "no_text_mutation_no_correction_decision_no_arbitration_no_release_approval"
    ] = "no_text_mutation_no_correction_decision_no_arbitration_no_release_approval"
    content_hash: Sha256

    @model_validator(mode="after")
    def _is_content_addressed(self) -> FullAuditAggregateAttestationV2:
        if not self.all_cell_ids or len(set(self.all_cell_ids)) != len(self.all_cell_ids):
            raise ValueError("full-audit aggregate requires unique ordered AuditPlan cells")
        if len(set(self.required_cell_ids)) != len(self.required_cell_ids) or not set(
            self.required_cell_ids
        ) <= set(self.all_cell_ids):
            raise ValueError("full-audit aggregate required cells are not an exact subset")
        if len({item.kind for item in self.source_artifacts}) != len(self.source_artifacts):
            raise ValueError("full-audit aggregate source artifact kinds must be unique")
        expected_kinds = (
            "audit_plan",
            "candidate_signal_set",
            "candidate_group_set",
            "correction_audit_execution_plan",
            "text_audit_execution_record",
            "audio_audit_execution_record",
        )
        if tuple(item.kind for item in self.source_artifacts) != expected_kinds:
            raise ValueError("full-audit aggregate source artifact order or coverage drift")
        expected = hash_object(self.model_dump(mode="json", exclude={"id", "content_hash"}))
        if self.id != expected or self.content_hash != expected:
            raise ValueError("full-audit aggregate identity/content hash mismatch")
        return self


def _validate_exact_model(value: BaseModel, model: type[BaseModel], label: str) -> None:
    try:
        validated = model.model_validate_json(canonical_json_bytes(value), strict=True)
    except ValidationError as exc:
        raise FullAuditAttestationError(f"{label} is not an exact valid artifact") from exc
    if validated != value:
        raise FullAuditAttestationError(f"{label} is not an exact valid artifact")


def _source(kind: str, value: BaseModel, content_hash: str) -> FullAuditSourceArtifactV2:
    exact = canonical_json_bytes(value)
    return FullAuditSourceArtifactV2(
        kind=kind,
        artifact_sha256=sha256_bytes(exact),
        object_content_hash=content_hash,
        size_bytes=len(exact),
    )


def _verify_record_raw_artifact_digests(
    *,
    text_record: TextAuditExecutionRecordV2,
    text_request_bytes: tuple[bytes, ...] | None,
    text_response_bytes: tuple[bytes, ...] | None,
    audio_record: AudioAuditExecutionRecordV2,
    audio_request_bytes: tuple[bytes, ...] | None,
    audio_response_bytes: tuple[bytes, ...] | None,
    audio_clip_bytes: tuple[bytes, ...] | None,
) -> None:
    supplied_text = (text_request_bytes, text_response_bytes)
    if any(value is None for value in supplied_text) != all(
        value is None for value in supplied_text
    ):
        raise FullAuditAttestationError("text raw artifact byte sets are all-or-none")
    if text_request_bytes is not None and text_response_bytes is not None:
        if len(text_request_bytes) != len(text_record.response_receipts) or len(
            text_response_bytes
        ) != len(text_record.response_receipts):
            raise FullAuditAttestationError("text raw artifact bytes lack complete record coverage")
        for receipt, request, response in zip(
            text_record.response_receipts,
            text_request_bytes,
            text_response_bytes,
            strict=True,
        ):
            if type(request) is not bytes or type(response) is not bytes:
                raise FullAuditAttestationError("text raw artifacts require exact bytes")
            if (
                receipt.request_artifact.sha256 != sha256_bytes(request)
                or receipt.request_artifact.size_bytes != len(request)
                or receipt.raw_response.sha256 != sha256_bytes(response)
                or receipt.raw_response.size_bytes != len(response)
            ):
                raise FullAuditAttestationError(
                    "text raw artifact bytes differ from record digests"
                )

    supplied_audio = (audio_request_bytes, audio_response_bytes, audio_clip_bytes)
    if any(value is None for value in supplied_audio) != all(
        value is None for value in supplied_audio
    ):
        raise FullAuditAttestationError("audio raw artifact byte sets are all-or-none")
    if (
        audio_request_bytes is not None
        and audio_response_bytes is not None
        and audio_clip_bytes is not None
    ):
        if any(
            len(value) != len(audio_record.response_receipts)
            for value in (audio_request_bytes, audio_response_bytes, audio_clip_bytes)
        ):
            raise FullAuditAttestationError(
                "audio raw artifact bytes lack complete record coverage"
            )
        for receipt, request, response, clip in zip(
            audio_record.response_receipts,
            audio_request_bytes,
            audio_response_bytes,
            audio_clip_bytes,
            strict=True,
        ):
            if any(type(value) is not bytes for value in (request, response, clip)):
                raise FullAuditAttestationError("audio raw artifacts require exact bytes")
            if (
                receipt.request_artifact.sha256 != sha256_bytes(request)
                or receipt.request_artifact.size_bytes != len(request)
                or receipt.raw_response.sha256 != sha256_bytes(response)
                or receipt.raw_response.size_bytes != len(response)
                or receipt.clip_artifact.sha256 != sha256_bytes(clip)
                or receipt.clip_artifact.size_bytes != len(clip)
            ):
                raise FullAuditAttestationError(
                    "audio raw artifact bytes differ from record digests"
                )


def _validate_parents(
    *,
    audit_plan: AuditPlan,
    candidate_signal_set: CandidateSignalSet,
    candidate_group_set: CandidateGroupSet,
    execution_plan: CorrectionAuditExecutionPlanV2,
    text_record: TextAuditExecutionRecordV2,
    audio_record: AudioAuditExecutionRecordV2,
) -> None:
    for value, model, label in (
        (audit_plan, AuditPlan, "AuditPlan"),
        (candidate_signal_set, CandidateSignalSet, "CandidateSignalSet"),
        (candidate_group_set, CandidateGroupSet, "CandidateGroupSet"),
        (execution_plan, CorrectionAuditExecutionPlanV2, "CorrectionAuditExecutionPlanV2"),
        (text_record, TextAuditExecutionRecordV2, "TextAuditExecutionRecordV2"),
        (audio_record, AudioAuditExecutionRecordV2, "AudioAuditExecutionRecordV2"),
    ):
        _validate_exact_model(value, model, label)

    audit_hash = sha256_bytes(canonical_json_bytes(audit_plan))
    signal_hash = sha256_bytes(canonical_json_bytes(candidate_signal_set))
    group_hash = sha256_bytes(canonical_json_bytes(candidate_group_set))
    if (
        candidate_signal_set.audit_plan_hash != audit_hash
        or candidate_signal_set.audit_plan_content_hash != audit_plan.content_hash
        or candidate_signal_set.audit_input_hash != audit_plan.inputs.content_hash
    ):
        raise FullAuditAttestationError("CandidateSignalSet differs from exact AuditPlan")
    if (
        candidate_group_set.signal_set_hash != signal_hash
        or candidate_group_set.signal_set_content_hash != candidate_signal_set.content_hash
        or candidate_group_set.audit_plan_hash != audit_hash
        or candidate_group_set.audit_plan_content_hash != audit_plan.content_hash
        or candidate_group_set.audit_input_hash != audit_plan.inputs.content_hash
    ):
        raise FullAuditAttestationError("CandidateGroupSet differs from exact signals/plan")

    exact_cells = tuple(item.id for item in audit_plan.cells)
    exact_required = tuple(item.id for item in audit_plan.cells if item.applicability == "required")
    if (
        execution_plan.episode_id != audit_plan.episode_id
        or execution_plan.generation_id != audit_plan.generation_id
        or execution_plan.audit_plan_hash != audit_hash
        or execution_plan.audit_plan_content_hash != audit_plan.content_hash
        or execution_plan.audit_input_hash != audit_plan.inputs.content_hash
        or execution_plan.candidate_signal_set_hash != signal_hash
        or execution_plan.candidate_signal_set_content_hash != candidate_signal_set.content_hash
        or execution_plan.candidate_group_set_hash != group_hash
        or execution_plan.candidate_group_set_content_hash != candidate_group_set.content_hash
        or execution_plan.canonical_transcript_hash != audit_plan.inputs.canonical_transcript_hash
        or execution_plan.canonical_content_hash != audit_plan.inputs.canonical_content_hash
        or execution_plan.normalized_audio_hash != audit_plan.inputs.normalized_audio_hash
        or execution_plan.all_cell_ids != exact_cells
        or execution_plan.required_cell_ids != exact_required
    ):
        raise FullAuditAttestationError("execution plan differs from frozen audit parents")

    for label, record, disposition_set in (
        ("text", text_record, text_record.text_disposition_set),
        ("audio", audio_record, audio_record.audio_disposition_set),
    ):
        if (
            record.execution_plan_id != execution_plan.id
            or record.execution_plan_content_hash != execution_plan.content_hash
            or record.audit_plan_hash != audit_hash
            or record.audit_plan_content_hash != audit_plan.content_hash
            or disposition_set.all_cell_ids != exact_cells
            or tuple(item.cell_id for item in disposition_set.dispositions) != exact_cells
        ):
            raise FullAuditAttestationError(f"{label} execution record lineage/coverage drift")
        for cell, disposition in zip(audit_plan.cells, disposition_set.dispositions, strict=True):
            if cell.applicability == "required":
                expected_source = (
                    "provider_text_assessment" if label == "text" else "provider_audio_assessment"
                )
                if (
                    disposition.source != expected_source
                    or disposition.assessment_id is None
                    or disposition.packet_id is None
                    or disposition.status.startswith("inherited_")
                ):
                    raise FullAuditAttestationError(
                        f"required cell lacks provider {label} assessment"
                    )
            else:
                expected_status = (
                    "inherited_not_applicable"
                    if cell.applicability == "not_applicable"
                    else "inherited_unavailable"
                )
                if (
                    disposition.source != "inherited_audit_plan_applicability"
                    or disposition.status != expected_status
                    or disposition.assessment_id is not None
                    or disposition.packet_id is not None
                ):
                    raise FullAuditAttestationError(
                        f"non-required cell has invalid {label} disposition authority"
                    )


def build_full_audit_aggregate(
    *,
    audit_plan: AuditPlan,
    candidate_signal_set: CandidateSignalSet,
    candidate_group_set: CandidateGroupSet,
    execution_plan: CorrectionAuditExecutionPlanV2,
    text_record: TextAuditExecutionRecordV2,
    audio_record: AudioAuditExecutionRecordV2,
    text_request_bytes: tuple[bytes, ...] | None = None,
    text_response_bytes: tuple[bytes, ...] | None = None,
    audio_request_bytes: tuple[bytes, ...] | None = None,
    audio_response_bytes: tuple[bytes, ...] | None = None,
    audio_clip_bytes: tuple[bytes, ...] | None = None,
) -> FullAuditAggregateAttestationV2:
    """Build the unique aggregate for six exact frozen parent artifacts."""

    _validate_parents(
        audit_plan=audit_plan,
        candidate_signal_set=candidate_signal_set,
        candidate_group_set=candidate_group_set,
        execution_plan=execution_plan,
        text_record=text_record,
        audio_record=audio_record,
    )
    _verify_record_raw_artifact_digests(
        text_record=text_record,
        text_request_bytes=text_request_bytes,
        text_response_bytes=text_response_bytes,
        audio_record=audio_record,
        audio_request_bytes=audio_request_bytes,
        audio_response_bytes=audio_response_bytes,
        audio_clip_bytes=audio_clip_bytes,
    )
    payload = {
        "schema_version": 2,
        "episode_id": audit_plan.episode_id,
        "generation_id": audit_plan.generation_id,
        "audit_plan_hash": sha256_bytes(canonical_json_bytes(audit_plan)),
        "audit_plan_content_hash": audit_plan.content_hash,
        "audit_input_hash": audit_plan.inputs.content_hash,
        "audit_policy_hash": audit_plan.policy_hash,
        "canonical_transcript_hash": audit_plan.inputs.canonical_transcript_hash,
        "canonical_content_hash": audit_plan.inputs.canonical_content_hash,
        "recognition_evidence_hashes": audit_plan.inputs.recognition_evidence_hashes,
        "audit_recognition_evidence_set_hash": audit_plan.inputs.recognition_evidence_set_hash,
        "execution_recognition_evidence_artifact_hash": (
            execution_plan.recognition_evidence_set_hash
        ),
        "reference_evidence_hash": audit_plan.inputs.reference_evidence_hash,
        "reference_retrieval_receipt_hashes": (
            audit_plan.inputs.reference_retrieval_receipt_hashes
        ),
        "audit_reference_retrieval_receipt_set_hash": (
            audit_plan.inputs.reference_retrieval_receipt_set_hash
        ),
        "execution_reference_retrieval_receipt_artifact_hash": (
            execution_plan.reference_retrieval_receipt_set_hash
        ),
        "speech_coverage_receipt_hash": audit_plan.inputs.speech_coverage_receipt_hash,
        "boundary_constraint_receipt_hash": (audit_plan.inputs.boundary_constraint_receipt_hash),
        "seam_evidence_hash": audit_plan.inputs.seam_evidence_hash,
        "normalized_audio_hash": audit_plan.inputs.normalized_audio_hash,
        "topology_policy_hash": execution_plan.policy_hash,
        "candidate_signal_set_hash": sha256_bytes(canonical_json_bytes(candidate_signal_set)),
        "candidate_signal_set_content_hash": candidate_signal_set.content_hash,
        "candidate_group_set_hash": sha256_bytes(canonical_json_bytes(candidate_group_set)),
        "candidate_group_set_content_hash": candidate_group_set.content_hash,
        "execution_plan_id": execution_plan.id,
        "execution_plan_content_hash": execution_plan.content_hash,
        "text_record_id": text_record.id,
        "text_record_content_hash": text_record.content_hash,
        "text_policy_hash": text_record.policy_hash,
        "text_adapter_identity_hash": text_record.adapter_identity_hash,
        "audio_record_id": audio_record.id,
        "audio_record_content_hash": audio_record.content_hash,
        "audio_policy_hash": audio_record.policy_hash,
        "audio_adapter_identity_hash": audio_record.adapter_identity_hash,
        "all_cell_ids": execution_plan.all_cell_ids,
        "required_cell_ids": execution_plan.required_cell_ids,
        "text_disposition_ids": tuple(
            item.id for item in text_record.text_disposition_set.dispositions
        ),
        "audio_disposition_ids": tuple(
            item.id for item in audio_record.audio_disposition_set.dispositions
        ),
        "text_discovery_ids": tuple(
            item.id for item in text_record.candidate_discovery_set.candidates
        ),
        "audio_discovery_ids": tuple(
            item.id for item in audio_record.candidate_discovery_set.candidates
        ),
        "source_artifacts": (
            _source("audit_plan", audit_plan, audit_plan.content_hash),
            _source(
                "candidate_signal_set",
                candidate_signal_set,
                candidate_signal_set.content_hash,
            ),
            _source("candidate_group_set", candidate_group_set, candidate_group_set.content_hash),
            _source("correction_audit_execution_plan", execution_plan, execution_plan.content_hash),
            _source("text_audit_execution_record", text_record, text_record.content_hash),
            _source("audio_audit_execution_record", audio_record, audio_record.content_hash),
        ),
        "execution_scope": "all_plan_cells_both_modalities_dispositioned",
        "authority": ("no_text_mutation_no_correction_decision_no_arbitration_no_release_approval"),
    }
    digest = hash_object(payload)
    return FullAuditAggregateAttestationV2(**payload, id=digest, content_hash=digest)


def full_audit_aggregate_bytes(aggregate: FullAuditAggregateAttestationV2) -> bytes:
    return canonical_json_bytes(aggregate)


def full_audit_aggregate_digest(aggregate: FullAuditAggregateAttestationV2) -> str:
    """Return the SHA-256 digest of the exact canonical aggregate artifact bytes."""

    return sha256_bytes(full_audit_aggregate_bytes(aggregate))


def verify_full_audit_aggregate(
    exact_bytes: bytes,
    *,
    audit_plan: AuditPlan,
    candidate_signal_set: CandidateSignalSet,
    candidate_group_set: CandidateGroupSet,
    execution_plan: CorrectionAuditExecutionPlanV2,
    text_record: TextAuditExecutionRecordV2,
    audio_record: AudioAuditExecutionRecordV2,
    text_request_bytes: tuple[bytes, ...] | None = None,
    text_response_bytes: tuple[bytes, ...] | None = None,
    audio_request_bytes: tuple[bytes, ...] | None = None,
    audio_response_bytes: tuple[bytes, ...] | None = None,
    audio_clip_bytes: tuple[bytes, ...] | None = None,
) -> FullAuditAggregateAttestationV2:
    """Rebuild from exact parents and compare exact aggregate bytes."""

    if type(exact_bytes) is not bytes:
        raise FullAuditAttestationError("full-audit aggregate requires exact bytes")
    try:
        parsed = FullAuditAggregateAttestationV2.model_validate_json(exact_bytes, strict=True)
    except (ValidationError, ValueError) as exc:
        raise FullAuditAttestationError("full-audit aggregate bytes violate strict schema") from exc
    if canonical_json_bytes(parsed) != exact_bytes:
        raise FullAuditAttestationError("full-audit aggregate bytes are not canonical")
    rebuilt = build_full_audit_aggregate(
        audit_plan=audit_plan,
        candidate_signal_set=candidate_signal_set,
        candidate_group_set=candidate_group_set,
        execution_plan=execution_plan,
        text_record=text_record,
        audio_record=audio_record,
        text_request_bytes=text_request_bytes,
        text_response_bytes=text_response_bytes,
        audio_request_bytes=audio_request_bytes,
        audio_response_bytes=audio_response_bytes,
        audio_clip_bytes=audio_clip_bytes,
    )
    if parsed != rebuilt or exact_bytes != canonical_json_bytes(rebuilt):
        raise FullAuditAttestationError("full-audit aggregate differs from frozen parents")
    return rebuilt


__all__ = [
    "FullAuditAggregateAttestationV2",
    "FullAuditAttestationError",
    "FullAuditSourceArtifactV2",
    "build_full_audit_aggregate",
    "full_audit_aggregate_bytes",
    "full_audit_aggregate_digest",
    "verify_full_audit_aggregate",
]
