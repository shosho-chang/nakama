"""Typed, replayable Phase A release evaluation for native Full Audit.

The Full Audit aggregate proves execution coverage only.  This module consumes
that aggregate together with its exact audit records and Canonical risk state,
then produces either a separate release attestation or a deterministic
``review_required`` evaluation.  It has no text-mutation seam and cannot mark
an issue resolved beyond its closed policy.
"""

from __future__ import annotations

from typing import Annotated, Literal, TypeAlias, TypeVar

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    StringConstraints,
    TypeAdapter,
    ValidationError,
    field_validator,
    model_validator,
)

from shared.schemas.podcast_subtitles_v2 import (
    AcceptancePolicySnapshot,
    AuditApplicability,
    AuditPlan,
    BoundaryAuditCategory,
    CanonicalTranscript,
    GenerationWarning,
    IssueStatus,
    ReviewIssue,
    RiskSeverity,
    SpanAuditCategory,
    SpeechCoverageReceipt,
    speech_coverage_receipt_content_hash,
)
from shared.schemas.podcast_subtitles_v2_audio_audit import (
    AudioAuditExecutionRecordV2,
    AudioDispositionStatusV2,
)
from shared.schemas.podcast_subtitles_v2_text_audit import (
    TextAuditExecutionRecordV2,
    TextDispositionStatusV2,
)

from .canonical import CanonicalBuildResult
from .full_audit_attestation import FullAuditAggregateAttestationV2
from .hashing import canonical_json_bytes, hash_object, sha256_bytes
from .risk import RiskRecord

Sha256 = Annotated[str, StringConstraints(pattern=r"^[0-9a-f]{64}$")]
_ModelT = TypeVar("_ModelT", bound=BaseModel)

NativeReleaseSystemGateV2 = Literal[
    "ledger_lineage",
    "speech_coverage",
    "reference_retrieval",
    "text_discoveries",
    "audio_discoveries",
]
NativeReleaseSystemCodeV2 = Literal[
    "ledger_current",
    "ledger_stale",
    "speech_coverage_verified",
    "speech_coverage_absent",
    "speech_coverage_failed",
    "speech_coverage_not_verified",
    "reference_retrieval_completed",
    "reference_sources_not_enrolled",
    "reference_retrieval_failed",
    "reference_retrieval_incomplete",
    "no_text_discoveries",
    "text_discoveries_present",
    "no_audio_discoveries",
    "audio_discoveries_present",
]
NativeReleaseCellPassBasisV2 = Literal[
    "inherited_not_applicable",
    "text_and_exact_audio_clean",
    "text_requires_audio_exact_same_cell_audio_clean",
]
NativeReleaseCellBlockReasonV2 = Literal[
    "inherited_unavailable",
    "text_finding",
    "text_unresolved_insufficient_evidence",
    "text_requires_audio_without_exact_audio_clean",
    "audio_finding",
    "audio_unresolved_insufficient_evidence",
    "audio_conflict",
]
NativeReleaseIssueDecisionV2 = Literal[
    "visible_unresolved_info",
    "visible_unresolved_low_permitted_by_acceptance_policy",
    "blocked_unresolved_low_by_acceptance_policy",
    "blocked_unresolved_material",
    "blocked_closed_policy_issue_code",
    "blocked_resolved_without_verified_typed_event",
    "blocked_waiver_unsupported_phase_a",
]
NativeReleaseWarningDecisionV2 = Literal[
    "release_gate_satisfied_by_complete_clean_exact_audio_audit",
    "blocked_without_complete_clean_exact_audio_audit",
]

_SYSTEM_GATE_ORDER: tuple[NativeReleaseSystemGateV2, ...] = (
    "ledger_lineage",
    "speech_coverage",
    "reference_retrieval",
    "text_discoveries",
    "audio_discoveries",
)
_BLOCKING_SEVERITIES: tuple[RiskSeverity, ...] = (
    "medium",
    "high",
    "blocking",
)
_ALWAYS_BLOCK_ISSUE_CODES = (
    "native_full_audit_requires_resolution",
    "orthographic_multi_option_ambiguity",
    "recognition_coverage_disagreement",
    "recognition_disagreement",
    "reference_retrieval_failed",
    "reference_retrieval_incomplete",
)
_CELL_BLOCK_REASON_ORDER: tuple[NativeReleaseCellBlockReasonV2, ...] = (
    "inherited_unavailable",
    "text_finding",
    "text_unresolved_insufficient_evidence",
    "text_requires_audio_without_exact_audio_clean",
    "audio_finding",
    "audio_unresolved_insufficient_evidence",
    "audio_conflict",
)
_AGGREGATE_AUTHORITY = "no_text_mutation_no_correction_decision_no_arbitration_no_release_approval"
_EVALUATION_AUTHORITY = (
    "native_release_evaluation_no_text_mutation_no_issue_resolution_beyond_closed_policy"
)


class NativeReleaseError(ValueError):
    """Release inputs or replay bytes are not exact and fail closed."""


class _StrictContract(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)


def _identity_payload(value: BaseModel) -> dict[str, object]:
    return value.model_dump(mode="json", exclude={"id", "content_hash"})


def _require_identity(value: BaseModel, identity: str, content_hash: str, label: str) -> None:
    expected = hash_object(_identity_payload(value))
    if identity != expected or content_hash != expected:
        raise ValueError(f"{label} identity/content hash mismatch")


def _seal(model: type[_ModelT], payload: dict[str, object]) -> _ModelT:
    digest = hash_object(payload)
    return model(**payload, id=digest, content_hash=digest)


def _validate_exact_model(value: object, model: type[_ModelT], label: str) -> _ModelT:
    if not isinstance(value, model):
        raise NativeReleaseError(f"{label} has the wrong artifact type")
    exact = canonical_json_bytes(value)
    try:
        replayed = model.model_validate_json(exact, strict=True)
    except (ValidationError, ValueError) as exc:
        raise NativeReleaseError(f"{label} is not an exact valid artifact") from exc
    if replayed != value:
        raise NativeReleaseError(f"{label} is not an exact valid artifact")
    return replayed


class NativeReleasePolicyV2(_StrictContract):
    """Closed policy for Phase A release classification, never issue authority."""

    schema_version: Literal[2] = 2
    id: Sha256
    acceptance_policy: AcceptancePolicySnapshot
    acceptance_policy_hash: Sha256
    blocking_issue_severities: tuple[RiskSeverity, ...] = _BLOCKING_SEVERITIES
    always_block_issue_codes: tuple[str, ...] = _ALWAYS_BLOCK_ISSUE_CODES
    require_current_ledger_head: Literal[True] = True
    require_verified_independent_speech_coverage: Literal[True] = True
    require_complete_or_not_enrolled_reference_retrieval: Literal[True] = True
    require_every_audit_cell_disposition: Literal[True] = True
    require_zero_text_and_audio_discoveries: Literal[True] = True
    require_clean_exact_audio_for_confidence_warning: Literal[True] = True
    aggregate_authority: Literal["execution_coverage_only_never_release_approval"] = (
        "execution_coverage_only_never_release_approval"
    )
    authority: Literal["closed_release_policy_no_text_mutation_no_issue_resolution_authority"] = (
        "closed_release_policy_no_text_mutation_no_issue_resolution_authority"
    )
    content_hash: Sha256

    @model_validator(mode="after")
    def _policy_is_closed(self) -> NativeReleasePolicyV2:
        if self.acceptance_policy_hash != hash_object(self.acceptance_policy):
            raise ValueError("native release acceptance policy hash mismatch")
        if self.blocking_issue_severities != _BLOCKING_SEVERITIES:
            raise ValueError("native release policy cannot redefine material severity")
        if self.always_block_issue_codes != _ALWAYS_BLOCK_ISSUE_CODES:
            raise ValueError("native release policy cannot weaken closed issue blockers")
        _require_identity(self, self.id, self.content_hash, "Native release policy")
        return self


class NativeReleaseSystemDispositionV2(_StrictContract):
    schema_version: Literal[2] = 2
    id: Sha256
    ordinal: int = Field(ge=0)
    gate: NativeReleaseSystemGateV2
    code: NativeReleaseSystemCodeV2
    evidence_hashes: tuple[Sha256, ...] = ()
    blocks_release: bool
    authority: Literal["gate_classification_only_no_text_mutation_no_issue_resolution"] = (
        "gate_classification_only_no_text_mutation_no_issue_resolution"
    )
    content_hash: Sha256

    @field_validator("blocks_release", mode="before")
    @classmethod
    def _block_flag_is_exact(cls, value: object) -> object:
        if type(value) is not bool:
            raise ValueError("system disposition blocks_release requires exact bool")
        return value

    @model_validator(mode="after")
    def _system_disposition_is_exact(self) -> NativeReleaseSystemDispositionV2:
        if tuple(sorted(set(self.evidence_hashes))) != self.evidence_hashes:
            raise ValueError("system disposition Evidence hashes must be sorted and unique")
        allowed = {
            "ledger_lineage": {"ledger_current", "ledger_stale"},
            "speech_coverage": {
                "speech_coverage_verified",
                "speech_coverage_absent",
                "speech_coverage_failed",
                "speech_coverage_not_verified",
            },
            "reference_retrieval": {
                "reference_retrieval_completed",
                "reference_sources_not_enrolled",
                "reference_retrieval_failed",
                "reference_retrieval_incomplete",
            },
            "text_discoveries": {"no_text_discoveries", "text_discoveries_present"},
            "audio_discoveries": {"no_audio_discoveries", "audio_discoveries_present"},
        }
        if self.code not in allowed[self.gate]:
            raise ValueError("system disposition code differs from its gate")
        passing = {
            "ledger_current",
            "speech_coverage_verified",
            "reference_retrieval_completed",
            "reference_sources_not_enrolled",
            "no_text_discoveries",
            "no_audio_discoveries",
        }
        if self.blocks_release != (self.code not in passing):
            raise ValueError("system disposition block flag contradicts code")
        _require_identity(self, self.id, self.content_hash, "Native release system disposition")
        return self


class NativeReleaseCellDispositionV2(_StrictContract):
    schema_version: Literal[2] = 2
    id: Sha256
    ordinal: int = Field(ge=0)
    cell_id: Sha256
    target_id: Sha256
    category: SpanAuditCategory | BoundaryAuditCategory
    applicability: AuditApplicability
    text_disposition_id: Sha256
    text_disposition_hash: Sha256
    text_status: TextDispositionStatusV2
    audio_disposition_id: Sha256
    audio_disposition_hash: Sha256
    audio_status: AudioDispositionStatusV2
    pass_basis: NativeReleaseCellPassBasisV2 | None = None
    blocking_reasons: tuple[NativeReleaseCellBlockReasonV2, ...] = ()
    blocks_release: bool
    authority: Literal["cell_classification_only_no_text_mutation_no_issue_resolution"] = (
        "cell_classification_only_no_text_mutation_no_issue_resolution"
    )
    content_hash: Sha256

    @field_validator("blocks_release", mode="before")
    @classmethod
    def _block_flag_is_exact(cls, value: object) -> object:
        if type(value) is not bool:
            raise ValueError("cell disposition blocks_release requires exact bool")
        return value

    @model_validator(mode="after")
    def _cell_disposition_is_exact(self) -> NativeReleaseCellDispositionV2:
        expected_reasons = tuple(
            reason for reason in _CELL_BLOCK_REASON_ORDER if reason in set(self.blocking_reasons)
        )
        if self.blocking_reasons != expected_reasons or len(set(self.blocking_reasons)) != len(
            self.blocking_reasons
        ):
            raise ValueError("cell release blockers are not canonical")
        if self.blocks_release:
            if self.pass_basis is not None or not self.blocking_reasons:
                raise ValueError("blocked cell requires typed blockers and no pass basis")
        elif self.pass_basis is None or self.blocking_reasons:
            raise ValueError("passing cell requires one pass basis and no blockers")
        if self.pass_basis == "inherited_not_applicable" and (
            self.applicability != "not_applicable"
            or self.text_status != "inherited_not_applicable"
            or self.audio_status != "inherited_not_applicable"
        ):
            raise ValueError("not-applicable cell pass basis contradicts exact dispositions")
        if self.pass_basis == "text_and_exact_audio_clean" and (
            self.applicability != "required"
            or self.text_status != "verified_clean_within_text_evidence"
            or self.audio_status != "verified_clean_from_exact_audio"
        ):
            raise ValueError("clean cell pass basis contradicts exact dispositions")
        if self.pass_basis == "text_requires_audio_exact_same_cell_audio_clean" and (
            self.applicability != "required"
            or self.text_status != "unresolved_requires_audio"
            or self.audio_status != "verified_clean_from_exact_audio"
        ):
            raise ValueError("audio-confirmed text cell pass basis is not exact")
        _require_identity(self, self.id, self.content_hash, "Native release cell disposition")
        return self


class NativeReleaseIssueDispositionV2(_StrictContract):
    schema_version: Literal[2] = 2
    id: Sha256
    ordinal: int = Field(ge=0)
    issue_id: str
    issue_hash: Sha256
    risk_record_hash: Sha256
    issue_code: str
    severity: RiskSeverity
    source_status: IssueStatus
    resolution_event_id: str | None = None
    decision: NativeReleaseIssueDecisionV2
    blocks_release: bool
    authority: Literal["issue_classification_only_no_issue_resolution_no_text_mutation"] = (
        "issue_classification_only_no_issue_resolution_no_text_mutation"
    )
    content_hash: Sha256

    @field_validator("issue_id", "issue_code")
    @classmethod
    def _text_is_bounded(cls, value: str) -> str:
        if not value.strip() or len(value) > 1_024:
            raise ValueError("issue disposition identity/code must be nonblank and bounded")
        return value

    @field_validator("blocks_release", mode="before")
    @classmethod
    def _block_flag_is_exact(cls, value: object) -> object:
        if type(value) is not bool:
            raise ValueError("issue disposition blocks_release requires exact bool")
        return value

    @model_validator(mode="after")
    def _issue_disposition_is_exact(self) -> NativeReleaseIssueDispositionV2:
        passing = {
            "visible_unresolved_info",
            "visible_unresolved_low_permitted_by_acceptance_policy",
        }
        if self.blocks_release != (self.decision not in passing):
            raise ValueError("issue disposition block flag contradicts decision")
        if (self.decision == "blocked_resolved_without_verified_typed_event") != (
            self.source_status == "resolved"
        ):
            raise ValueError("resolved issue requires a fail-closed Phase A disposition")
        _require_identity(self, self.id, self.content_hash, "Native release issue disposition")
        return self


class NativeReleaseWarningDispositionV2(_StrictContract):
    schema_version: Literal[2] = 2
    id: Sha256
    ordinal: int = Field(ge=0)
    warning_id: str
    warning_hash: Sha256
    warning_code: Literal["recognizer_confidence_unavailable"]
    source_status: Literal["requires_full_audit", "mitigated"]
    audio_record_id: Sha256
    audio_record_hash: Sha256
    full_audit_aggregate_id: Sha256
    decision: NativeReleaseWarningDecisionV2
    blocks_release: bool
    authority: Literal["warning_classification_only_no_issue_resolution_no_text_mutation"] = (
        "warning_classification_only_no_issue_resolution_no_text_mutation"
    )
    content_hash: Sha256

    @field_validator("blocks_release", mode="before")
    @classmethod
    def _block_flag_is_exact(cls, value: object) -> object:
        if type(value) is not bool:
            raise ValueError("warning disposition blocks_release requires exact bool")
        return value

    @model_validator(mode="after")
    def _warning_disposition_is_exact(self) -> NativeReleaseWarningDispositionV2:
        expected = self.decision == "blocked_without_complete_clean_exact_audio_audit"
        if self.blocks_release != expected:
            raise ValueError("warning disposition block flag contradicts decision")
        _require_identity(self, self.id, self.content_hash, "Native release warning disposition")
        return self


class _NativeReleaseResultBase(_StrictContract):
    schema_version: Literal[2] = 2
    id: Sha256
    release_status: Literal["approved", "review_required"]
    result_scope: Literal["release_approval", "review_required"]
    episode_id: str
    generation_id: str
    canonical_build_result_hash: Sha256
    canonical_transcript_hash: Sha256
    canonical_content_hash: Sha256
    ledger_hash: Sha256
    observed_ledger_head: Sha256
    acceptance_policy_hash: Sha256
    native_release_policy_id: Sha256
    native_release_policy_hash: Sha256
    audit_plan_hash: Sha256
    audit_plan_content_hash: Sha256
    text_record_id: Sha256
    text_record_hash: Sha256
    audio_record_id: Sha256
    audio_record_hash: Sha256
    full_audit_aggregate_id: Sha256
    full_audit_aggregate_hash: Sha256
    full_audit_aggregate_artifact_hash: Sha256
    full_audit_aggregate_authority: Literal[
        "no_text_mutation_no_correction_decision_no_arbitration_no_release_approval"
    ]
    speech_coverage_receipt_hash: Sha256 | None
    risk_record_set_hash: Sha256
    generation_warning_set_hash: Sha256
    system_dispositions: tuple[NativeReleaseSystemDispositionV2, ...]
    cell_dispositions: tuple[NativeReleaseCellDispositionV2, ...]
    issue_dispositions: tuple[NativeReleaseIssueDispositionV2, ...]
    warning_dispositions: tuple[NativeReleaseWarningDispositionV2, ...]
    blocking_disposition_ids: tuple[Sha256, ...]
    authority: Literal[
        "native_release_evaluation_no_text_mutation_no_issue_resolution_beyond_closed_policy"
    ] = _EVALUATION_AUTHORITY
    content_hash: Sha256

    @field_validator("episode_id", "generation_id")
    @classmethod
    def _identity_is_bounded(cls, value: str) -> str:
        if not value.strip() or len(value) > 256:
            raise ValueError("release identity must be nonblank and bounded")
        return value

    @model_validator(mode="after")
    def _result_is_exact(self) -> _NativeReleaseResultBase:
        groups = (
            self.system_dispositions,
            self.cell_dispositions,
            self.issue_dispositions,
            self.warning_dispositions,
        )
        for label, group in zip(("system", "cell", "issue", "warning"), groups, strict=True):
            if tuple(item.ordinal for item in group) != tuple(range(len(group))):
                raise ValueError(f"native release {label} dispositions are not ordered")
            if len({item.id for item in group}) != len(group):
                raise ValueError(f"native release {label} dispositions repeat identities")
        if tuple(item.gate for item in self.system_dispositions) != _SYSTEM_GATE_ORDER:
            raise ValueError("native release system gate coverage/order drift")
        expected_blockers = tuple(
            item.id for group in groups for item in group if item.blocks_release
        )
        if self.blocking_disposition_ids != expected_blockers or len(
            set(self.blocking_disposition_ids)
        ) != len(self.blocking_disposition_ids):
            raise ValueError("native release blocking disposition set is not exact")
        if self.release_status == "approved":
            if self.result_scope != "release_approval" or self.blocking_disposition_ids:
                raise ValueError("approved native release carries blocking evidence")
        elif self.result_scope != "review_required" or not self.blocking_disposition_ids:
            raise ValueError("review-required native release lacks blocking evidence")
        _require_identity(self, self.id, self.content_hash, "Native release result")
        return self


class NativeReleaseAttestationV2(_NativeReleaseResultBase):
    """Separate approval artifact; the Full Audit aggregate remains non-authoritative."""

    release_status: Literal["approved"] = "approved"
    result_scope: Literal["release_approval"] = "release_approval"


class NativeReleaseReviewRequiredV2(_NativeReleaseResultBase):
    """Deterministic explanation of every blocker without mutating Canonical truth."""

    release_status: Literal["review_required"] = "review_required"
    result_scope: Literal["review_required"] = "review_required"


NativeReleaseResultV2: TypeAlias = NativeReleaseAttestationV2 | NativeReleaseReviewRequiredV2
_RESULT_ADAPTER = TypeAdapter(
    Annotated[NativeReleaseResultV2, Field(discriminator="release_status")]
)


def build_native_release_policy(
    acceptance_policy: AcceptancePolicySnapshot,
) -> NativeReleasePolicyV2:
    exact = _validate_exact_model(
        acceptance_policy,
        AcceptancePolicySnapshot,
        "Acceptance Policy Snapshot",
    )
    payload: dict[str, object] = {
        "schema_version": 2,
        "acceptance_policy": exact,
        "acceptance_policy_hash": hash_object(exact),
        "blocking_issue_severities": _BLOCKING_SEVERITIES,
        "always_block_issue_codes": _ALWAYS_BLOCK_ISSUE_CODES,
        "require_current_ledger_head": True,
        "require_verified_independent_speech_coverage": True,
        "require_complete_or_not_enrolled_reference_retrieval": True,
        "require_every_audit_cell_disposition": True,
        "require_zero_text_and_audio_discoveries": True,
        "require_clean_exact_audio_for_confidence_warning": True,
        "aggregate_authority": "execution_coverage_only_never_release_approval",
        "authority": "closed_release_policy_no_text_mutation_no_issue_resolution_authority",
    }
    return _seal(NativeReleasePolicyV2, payload)


def _validate_canonical_result(result: CanonicalBuildResult) -> CanonicalBuildResult:
    if type(result) is not CanonicalBuildResult:
        raise NativeReleaseError("native release requires an exact CanonicalBuildResult")
    transcript = _validate_exact_model(
        result.transcript,
        CanonicalTranscript,
        "Canonical Transcript",
    )
    if (
        result.reference_evidence_hash != transcript.reference_evidence_hash
        or result.generation_warnings != transcript.generation_warnings
        or tuple(item.issue for item in result.risks) != transcript.review_issues
        or result.outcome != ("accepted" if transcript.status == "accepted" else "needs_review")
    ):
        raise NativeReleaseError("CanonicalBuildResult differs from its exact Transcript")
    if len({item.issue.id for item in result.risks}) != len(result.risks):
        raise NativeReleaseError("CanonicalBuildResult repeats a RiskRecord issue")
    for ordinal, risk in enumerate(result.risks):
        if type(risk) is not RiskRecord:
            raise NativeReleaseError("CanonicalBuildResult contains a non-exact RiskRecord")
        _validate_exact_model(risk.issue, ReviewIssue, f"Review Issue {ordinal}")
        for label, values in (
            ("audio_span_ids", risk.audio_span_ids),
            ("evidence_ids", risk.evidence_ids),
            ("supporting_reference_ids", risk.supporting_reference_ids),
            ("conflicting_reference_ids", risk.conflicting_reference_ids),
        ):
            if len(set(values)) != len(values):
                raise NativeReleaseError(f"RiskRecord {label} contains duplicates")
    return result


def _source_matches(
    aggregate: FullAuditAggregateAttestationV2,
    *,
    kind: str,
    artifact: BaseModel,
    content_hash: str,
) -> bool:
    source = next((item for item in aggregate.source_artifacts if item.kind == kind), None)
    exact = canonical_json_bytes(artifact)
    return source is not None and (
        source.artifact_sha256 == sha256_bytes(exact)
        and source.object_content_hash == content_hash
        and source.size_bytes == len(exact)
    )


def _validate_lineage(
    *,
    canonical_result: CanonicalBuildResult,
    audit_plan: AuditPlan,
    text_record: TextAuditExecutionRecordV2,
    audio_record: AudioAuditExecutionRecordV2,
    aggregate: FullAuditAggregateAttestationV2,
    acceptance_policy: AcceptancePolicySnapshot,
    speech_coverage_receipt: SpeechCoverageReceipt | None,
    policy: NativeReleasePolicyV2,
) -> str | None:
    transcript = canonical_result.transcript
    audit_hash = sha256_bytes(canonical_json_bytes(audit_plan))
    transcript_hash = hash_object(transcript)
    exact_cells = tuple(item.id for item in audit_plan.cells)
    required_cells = tuple(item.id for item in audit_plan.cells if item.applicability == "required")
    text_dispositions = text_record.text_disposition_set.dispositions
    audio_dispositions = audio_record.audio_disposition_set.dispositions
    if acceptance_policy != transcript.acceptance_policy or policy.acceptance_policy != (
        acceptance_policy
    ):
        raise NativeReleaseError("native release Acceptance Policy lineage differs")
    if (
        audit_plan.episode_id != transcript.episode_id
        or audit_plan.generation_id != transcript.generation_id
        or audit_plan.inputs.canonical_transcript_hash != transcript_hash
        or audit_plan.inputs.canonical_content_hash != transcript.content_hash
        or audit_plan.inputs.normalized_audio_hash != transcript.normalized_audio_hash
        or audit_plan.inputs.recognition_evidence_set_hash != transcript.evidence_hash
        or audit_plan.inputs.reference_evidence_hash != transcript.reference_evidence_hash
    ):
        raise NativeReleaseError("AuditPlan crossed exact Canonical Generation lineage")
    for label, record, dispositions in (
        ("text", text_record, text_dispositions),
        ("audio", audio_record, audio_dispositions),
    ):
        if (
            record.audit_plan_hash != audit_hash
            or record.audit_plan_content_hash != audit_plan.content_hash
            or record.execution_plan_id != aggregate.execution_plan_id
            or record.execution_plan_content_hash != aggregate.execution_plan_content_hash
            or tuple(item.cell_id for item in dispositions) != exact_cells
        ):
            raise NativeReleaseError(f"{label} audit record crossed exact AuditPlan lineage")
    for cell, text, audio in zip(
        audit_plan.cells, text_dispositions, audio_dispositions, strict=True
    ):
        if (
            text.target_id != cell.target_id
            or audio.target_id != cell.target_id
            or text.category != cell.category
            or audio.category != cell.category
            or text.applicability_reason != cell.applicability_reason
            or audio.applicability_reason != cell.applicability_reason
        ):
            raise NativeReleaseError("audit cell disposition identity differs from AuditPlan")
        if cell.applicability == "required":
            if (
                text.source != "provider_text_assessment"
                or audio.source != "provider_audio_assessment"
                or text.assessment_id is None
                or audio.assessment_id is None
                or text.status.startswith("inherited_")
                or audio.status.startswith("inherited_")
            ):
                raise NativeReleaseError("required audit cell lacks exact provider dispositions")
        else:
            expected = (
                "inherited_not_applicable"
                if cell.applicability == "not_applicable"
                else "inherited_unavailable"
            )
            if text.status != expected or audio.status != expected:
                raise NativeReleaseError("non-required audit cell applicability was overwritten")
    if (
        aggregate.episode_id != transcript.episode_id
        or aggregate.generation_id != transcript.generation_id
        or aggregate.audit_plan_hash != audit_hash
        or aggregate.audit_plan_content_hash != audit_plan.content_hash
        or aggregate.audit_input_hash != audit_plan.inputs.content_hash
        or aggregate.audit_policy_hash != audit_plan.policy_hash
        or aggregate.canonical_transcript_hash != transcript_hash
        or aggregate.canonical_content_hash != transcript.content_hash
        or aggregate.normalized_audio_hash != transcript.normalized_audio_hash
        or aggregate.recognition_evidence_hashes != audit_plan.inputs.recognition_evidence_hashes
        or aggregate.audit_recognition_evidence_set_hash
        != audit_plan.inputs.recognition_evidence_set_hash
        or aggregate.reference_evidence_hash != transcript.reference_evidence_hash
        or aggregate.boundary_constraint_receipt_hash
        != audit_plan.inputs.boundary_constraint_receipt_hash
        or aggregate.seam_evidence_hash != audit_plan.inputs.seam_evidence_hash
        or aggregate.text_record_id != text_record.id
        or aggregate.text_record_content_hash != text_record.content_hash
        or aggregate.text_policy_hash != text_record.policy_hash
        or aggregate.text_adapter_identity_hash != text_record.adapter_identity_hash
        or aggregate.audio_record_id != audio_record.id
        or aggregate.audio_record_content_hash != audio_record.content_hash
        or aggregate.audio_policy_hash != audio_record.policy_hash
        or aggregate.audio_adapter_identity_hash != audio_record.adapter_identity_hash
        or aggregate.all_cell_ids != exact_cells
        or aggregate.required_cell_ids != required_cells
        or aggregate.text_disposition_ids != tuple(item.id for item in text_dispositions)
        or aggregate.audio_disposition_ids != tuple(item.id for item in audio_dispositions)
        or aggregate.text_discovery_ids
        != tuple(item.id for item in text_record.candidate_discovery_set.candidates)
        or aggregate.audio_discovery_ids
        != tuple(item.id for item in audio_record.candidate_discovery_set.candidates)
        or aggregate.speech_coverage_receipt_hash != audit_plan.inputs.speech_coverage_receipt_hash
        or aggregate.reference_retrieval_receipt_hashes
        != audit_plan.inputs.reference_retrieval_receipt_hashes
        or aggregate.audit_reference_retrieval_receipt_set_hash
        != audit_plan.inputs.reference_retrieval_receipt_set_hash
        or aggregate.authority != _AGGREGATE_AUTHORITY
        or not _source_matches(
            aggregate,
            kind="audit_plan",
            artifact=audit_plan,
            content_hash=audit_plan.content_hash,
        )
        or not _source_matches(
            aggregate,
            kind="text_audit_execution_record",
            artifact=text_record,
            content_hash=text_record.content_hash,
        )
        or not _source_matches(
            aggregate,
            kind="audio_audit_execution_record",
            artifact=audio_record,
            content_hash=audio_record.content_hash,
        )
    ):
        raise NativeReleaseError("Full Audit aggregate differs from exact release parents")

    if speech_coverage_receipt is None:
        if audit_plan.inputs.speech_coverage_status != "absent":
            return None
        if audit_plan.inputs.speech_coverage_receipt_hash is not None:
            raise NativeReleaseError("absent speech coverage carries an artifact hash")
        return None
    receipt = _validate_exact_model(
        speech_coverage_receipt,
        SpeechCoverageReceipt,
        "Speech Coverage Receipt",
    )
    coverage_hash = speech_coverage_receipt_content_hash(receipt)
    if (
        receipt.episode_id != transcript.episode_id
        or receipt.normalized_audio_hash != transcript.normalized_audio_hash
        or receipt.recognition_evidence_hash != transcript.evidence_hash
        or audit_plan.inputs.speech_coverage_receipt_hash != coverage_hash
        or aggregate.speech_coverage_receipt_hash != coverage_hash
    ):
        raise NativeReleaseError("Speech Coverage Receipt crossed exact release lineage")
    if transcript.verified_speech_coverage_receipt_hash not in {None, coverage_hash}:
        raise NativeReleaseError("Canonical verified speech coverage binding drifted")
    return coverage_hash


def _system_dispositions(
    *,
    canonical_result: CanonicalBuildResult,
    audit_plan: AuditPlan,
    text_record: TextAuditExecutionRecordV2,
    audio_record: AudioAuditExecutionRecordV2,
    speech_coverage_receipt: SpeechCoverageReceipt | None,
    coverage_hash: str | None,
    current_ledger_hash: str,
) -> tuple[NativeReleaseSystemDispositionV2, ...]:
    transcript = canonical_result.transcript
    if current_ledger_hash == transcript.ledger_hash:
        ledger_code = "ledger_current"
    else:
        ledger_code = "ledger_stale"
    coverage_code: NativeReleaseSystemCodeV2
    if speech_coverage_receipt is None:
        coverage_code = "speech_coverage_absent"
    elif speech_coverage_receipt.status == "failed":
        coverage_code = "speech_coverage_failed"
    elif (
        speech_coverage_receipt.passed
        and not speech_coverage_receipt.uncovered_intervals
        and coverage_hash is not None
        and transcript.verified_speech_coverage_receipt_hash == coverage_hash
        and audit_plan.inputs.speech_coverage_status == "completed"
    ):
        coverage_code = "speech_coverage_verified"
    else:
        coverage_code = "speech_coverage_not_verified"
    reference_code = {
        "not_enrolled": "reference_sources_not_enrolled",
        "completed": "reference_retrieval_completed",
        "failed": "reference_retrieval_failed",
        "incomplete": "reference_retrieval_incomplete",
    }[audit_plan.inputs.reference_status]
    text_ids = tuple(item.id for item in text_record.candidate_discovery_set.candidates)
    audio_ids = tuple(item.id for item in audio_record.candidate_discovery_set.candidates)
    specifications = (
        (
            "ledger_lineage",
            ledger_code,
            tuple(sorted({transcript.ledger_hash, current_ledger_hash})),
        ),
        (
            "speech_coverage",
            coverage_code,
            (coverage_hash,) if coverage_hash is not None else (),
        ),
        (
            "reference_retrieval",
            reference_code,
            tuple(sorted(audit_plan.inputs.reference_retrieval_receipt_hashes)),
        ),
        (
            "text_discoveries",
            "text_discoveries_present" if text_ids else "no_text_discoveries",
            tuple(sorted(text_ids)),
        ),
        (
            "audio_discoveries",
            "audio_discoveries_present" if audio_ids else "no_audio_discoveries",
            tuple(sorted(audio_ids)),
        ),
    )
    dispositions: list[NativeReleaseSystemDispositionV2] = []
    passing = {
        "ledger_current",
        "speech_coverage_verified",
        "reference_retrieval_completed",
        "reference_sources_not_enrolled",
        "no_text_discoveries",
        "no_audio_discoveries",
    }
    for ordinal, (gate, code, evidence_hashes) in enumerate(specifications):
        dispositions.append(
            _seal(
                NativeReleaseSystemDispositionV2,
                {
                    "schema_version": 2,
                    "ordinal": ordinal,
                    "gate": gate,
                    "code": code,
                    "evidence_hashes": evidence_hashes,
                    "blocks_release": code not in passing,
                    "authority": ("gate_classification_only_no_text_mutation_no_issue_resolution"),
                },
            )
        )
    return tuple(dispositions)


def _cell_dispositions(
    audit_plan: AuditPlan,
    text_record: TextAuditExecutionRecordV2,
    audio_record: AudioAuditExecutionRecordV2,
) -> tuple[NativeReleaseCellDispositionV2, ...]:
    results: list[NativeReleaseCellDispositionV2] = []
    for ordinal, (cell, text, audio) in enumerate(
        zip(
            audit_plan.cells,
            text_record.text_disposition_set.dispositions,
            audio_record.audio_disposition_set.dispositions,
            strict=True,
        )
    ):
        reasons: set[NativeReleaseCellBlockReasonV2] = set()
        pass_basis: NativeReleaseCellPassBasisV2 | None = None
        if cell.applicability == "not_applicable":
            pass_basis = "inherited_not_applicable"
        elif cell.applicability == "unavailable":
            reasons.add("inherited_unavailable")
        else:
            if text.status == "finding":
                reasons.add("text_finding")
            elif text.status == "unresolved_insufficient_evidence":
                reasons.add("text_unresolved_insufficient_evidence")
            if audio.status == "finding":
                reasons.add("audio_finding")
            elif audio.status == "unresolved_insufficient_evidence":
                reasons.add("audio_unresolved_insufficient_evidence")
            elif audio.status == "conflict":
                reasons.add("audio_conflict")
            if text.status == "unresolved_requires_audio" and (
                audio.status != "verified_clean_from_exact_audio"
            ):
                reasons.add("text_requires_audio_without_exact_audio_clean")
            if not reasons:
                pass_basis = (
                    "text_requires_audio_exact_same_cell_audio_clean"
                    if text.status == "unresolved_requires_audio"
                    else "text_and_exact_audio_clean"
                )
        ordered_reasons = tuple(reason for reason in _CELL_BLOCK_REASON_ORDER if reason in reasons)
        results.append(
            _seal(
                NativeReleaseCellDispositionV2,
                {
                    "schema_version": 2,
                    "ordinal": ordinal,
                    "cell_id": cell.id,
                    "target_id": cell.target_id,
                    "category": cell.category,
                    "applicability": cell.applicability,
                    "text_disposition_id": text.id,
                    "text_disposition_hash": text.content_hash,
                    "text_status": text.status,
                    "audio_disposition_id": audio.id,
                    "audio_disposition_hash": audio.content_hash,
                    "audio_status": audio.status,
                    "pass_basis": pass_basis,
                    "blocking_reasons": ordered_reasons,
                    "blocks_release": bool(ordered_reasons),
                    "authority": ("cell_classification_only_no_text_mutation_no_issue_resolution"),
                },
            )
        )
    return tuple(results)


def _issue_dispositions(
    canonical_result: CanonicalBuildResult,
    policy: NativeReleasePolicyV2,
) -> tuple[NativeReleaseIssueDispositionV2, ...]:
    results: list[NativeReleaseIssueDispositionV2] = []
    for ordinal, risk in enumerate(canonical_result.risks):
        issue = risk.issue
        decision: NativeReleaseIssueDecisionV2
        if issue.status == "resolved":
            decision = "blocked_resolved_without_verified_typed_event"
        elif issue.status == "waived":
            decision = "blocked_waiver_unsupported_phase_a"
        elif issue.code in policy.always_block_issue_codes:
            decision = "blocked_closed_policy_issue_code"
        elif issue.severity in policy.blocking_issue_severities:
            decision = "blocked_unresolved_material"
        elif issue.severity == "low":
            decision = (
                "visible_unresolved_low_permitted_by_acceptance_policy"
                if policy.acceptance_policy.permit_unresolved_low_risk
                else "blocked_unresolved_low_by_acceptance_policy"
            )
        else:
            decision = "visible_unresolved_info"
        passing = {
            "visible_unresolved_info",
            "visible_unresolved_low_permitted_by_acceptance_policy",
        }
        results.append(
            _seal(
                NativeReleaseIssueDispositionV2,
                {
                    "schema_version": 2,
                    "ordinal": ordinal,
                    "issue_id": issue.id,
                    "issue_hash": hash_object(issue),
                    "risk_record_hash": hash_object(risk),
                    "issue_code": issue.code,
                    "severity": issue.severity,
                    "source_status": issue.status,
                    "resolution_event_id": risk.resolution_event_id,
                    "decision": decision,
                    "blocks_release": decision not in passing,
                    "authority": ("issue_classification_only_no_issue_resolution_no_text_mutation"),
                },
            )
        )
    return tuple(results)


def _warning_dispositions(
    warnings: tuple[GenerationWarning, ...],
    *,
    audit_plan: AuditPlan,
    audio_record: AudioAuditExecutionRecordV2,
    aggregate: FullAuditAggregateAttestationV2,
) -> tuple[NativeReleaseWarningDispositionV2, ...]:
    audio_complete_clean = not audio_record.candidate_discovery_set.candidates and all(
        (
            cell.applicability == "required"
            and disposition.status == "verified_clean_from_exact_audio"
        )
        or (
            cell.applicability == "not_applicable"
            and disposition.status == "inherited_not_applicable"
        )
        for cell, disposition in zip(
            audit_plan.cells,
            audio_record.audio_disposition_set.dispositions,
            strict=True,
        )
    )
    results: list[NativeReleaseWarningDispositionV2] = []
    for ordinal, warning in enumerate(warnings):
        decision: NativeReleaseWarningDecisionV2 = (
            "release_gate_satisfied_by_complete_clean_exact_audio_audit"
            if audio_complete_clean
            else "blocked_without_complete_clean_exact_audio_audit"
        )
        results.append(
            _seal(
                NativeReleaseWarningDispositionV2,
                {
                    "schema_version": 2,
                    "ordinal": ordinal,
                    "warning_id": warning.id,
                    "warning_hash": hash_object(warning),
                    "warning_code": warning.code,
                    "source_status": warning.status,
                    "audio_record_id": audio_record.id,
                    "audio_record_hash": audio_record.content_hash,
                    "full_audit_aggregate_id": aggregate.id,
                    "decision": decision,
                    "blocks_release": decision
                    == "blocked_without_complete_clean_exact_audio_audit",
                    "authority": (
                        "warning_classification_only_no_issue_resolution_no_text_mutation"
                    ),
                },
            )
        )
    return tuple(results)


def evaluate_native_release(
    *,
    canonical_result: CanonicalBuildResult,
    audit_plan: AuditPlan,
    text_record: TextAuditExecutionRecordV2,
    audio_record: AudioAuditExecutionRecordV2,
    full_audit_aggregate: FullAuditAggregateAttestationV2,
    acceptance_policy: AcceptancePolicySnapshot,
    speech_coverage_receipt: SpeechCoverageReceipt | None,
    current_ledger_hash: str,
    policy: NativeReleasePolicyV2,
) -> NativeReleaseResultV2:
    """Evaluate exact parents without changing text, issues, warnings, or Ledger."""

    result = _validate_canonical_result(canonical_result)
    plan = _validate_exact_model(audit_plan, AuditPlan, "AuditPlan")
    text = _validate_exact_model(
        text_record,
        TextAuditExecutionRecordV2,
        "Text Audit Execution Record",
    )
    audio = _validate_exact_model(
        audio_record,
        AudioAuditExecutionRecordV2,
        "Audio Audit Execution Record",
    )
    aggregate = _validate_exact_model(
        full_audit_aggregate,
        FullAuditAggregateAttestationV2,
        "Full Audit aggregate",
    )
    acceptance = _validate_exact_model(
        acceptance_policy,
        AcceptancePolicySnapshot,
        "Acceptance Policy Snapshot",
    )
    exact_policy = _validate_exact_model(
        policy,
        NativeReleasePolicyV2,
        "Native Release Policy",
    )
    if (
        not isinstance(current_ledger_hash, str)
        or len(current_ledger_hash) != 64
        or any(character not in "0123456789abcdef" for character in current_ledger_hash)
    ):
        raise NativeReleaseError("current Ledger head requires lowercase SHA-256")
    coverage_hash = _validate_lineage(
        canonical_result=result,
        audit_plan=plan,
        text_record=text,
        audio_record=audio,
        aggregate=aggregate,
        acceptance_policy=acceptance,
        speech_coverage_receipt=speech_coverage_receipt,
        policy=exact_policy,
    )
    system = _system_dispositions(
        canonical_result=result,
        audit_plan=plan,
        text_record=text,
        audio_record=audio,
        speech_coverage_receipt=speech_coverage_receipt,
        coverage_hash=coverage_hash,
        current_ledger_hash=current_ledger_hash,
    )
    cells = _cell_dispositions(plan, text, audio)
    issues = _issue_dispositions(result, exact_policy)
    warnings = _warning_dispositions(
        result.generation_warnings,
        audit_plan=plan,
        audio_record=audio,
        aggregate=aggregate,
    )
    groups = (system, cells, issues, warnings)
    blocking = tuple(item.id for group in groups for item in group if item.blocks_release)
    payload: dict[str, object] = {
        "schema_version": 2,
        "release_status": "review_required" if blocking else "approved",
        "result_scope": "review_required" if blocking else "release_approval",
        "episode_id": result.transcript.episode_id,
        "generation_id": result.transcript.generation_id,
        "canonical_build_result_hash": hash_object(result),
        "canonical_transcript_hash": hash_object(result.transcript),
        "canonical_content_hash": result.transcript.content_hash,
        "ledger_hash": result.transcript.ledger_hash,
        "observed_ledger_head": current_ledger_hash,
        "acceptance_policy_hash": hash_object(acceptance),
        "native_release_policy_id": exact_policy.id,
        "native_release_policy_hash": exact_policy.content_hash,
        "audit_plan_hash": sha256_bytes(canonical_json_bytes(plan)),
        "audit_plan_content_hash": plan.content_hash,
        "text_record_id": text.id,
        "text_record_hash": text.content_hash,
        "audio_record_id": audio.id,
        "audio_record_hash": audio.content_hash,
        "full_audit_aggregate_id": aggregate.id,
        "full_audit_aggregate_hash": aggregate.content_hash,
        "full_audit_aggregate_artifact_hash": sha256_bytes(canonical_json_bytes(aggregate)),
        "full_audit_aggregate_authority": aggregate.authority,
        "speech_coverage_receipt_hash": coverage_hash,
        "risk_record_set_hash": hash_object(result.risks),
        "generation_warning_set_hash": hash_object(result.generation_warnings),
        "system_dispositions": system,
        "cell_dispositions": cells,
        "issue_dispositions": issues,
        "warning_dispositions": warnings,
        "blocking_disposition_ids": blocking,
        "authority": _EVALUATION_AUTHORITY,
    }
    model: type[NativeReleaseAttestationV2] | type[NativeReleaseReviewRequiredV2] = (
        NativeReleaseReviewRequiredV2 if blocking else NativeReleaseAttestationV2
    )
    return _seal(model, payload)


def native_release_policy_bytes(policy: NativeReleasePolicyV2) -> bytes:
    exact = _validate_exact_model(policy, NativeReleasePolicyV2, "Native Release Policy")
    return canonical_json_bytes(exact)


def native_release_result_bytes(result: NativeReleaseResultV2) -> bytes:
    model = (
        NativeReleaseAttestationV2
        if isinstance(result, NativeReleaseAttestationV2)
        else NativeReleaseReviewRequiredV2
    )
    exact = _validate_exact_model(result, model, "Native Release Result")
    return canonical_json_bytes(exact)


def verify_native_release_policy(exact_bytes: bytes) -> NativeReleasePolicyV2:
    if type(exact_bytes) is not bytes:
        raise NativeReleaseError("Native Release Policy requires exact bytes")
    try:
        parsed = NativeReleasePolicyV2.model_validate_json(exact_bytes, strict=True)
    except (ValidationError, ValueError) as exc:
        raise NativeReleaseError("Native Release Policy bytes violate strict schema") from exc
    if canonical_json_bytes(parsed) != exact_bytes:
        raise NativeReleaseError("Native Release Policy bytes are not canonical")
    rebuilt = build_native_release_policy(parsed.acceptance_policy)
    if parsed != rebuilt:
        raise NativeReleaseError("Native Release Policy differs from closed policy builder")
    return rebuilt


def verify_native_release_result(
    exact_bytes: bytes,
    *,
    canonical_result: CanonicalBuildResult,
    audit_plan: AuditPlan,
    text_record: TextAuditExecutionRecordV2,
    audio_record: AudioAuditExecutionRecordV2,
    full_audit_aggregate: FullAuditAggregateAttestationV2,
    acceptance_policy: AcceptancePolicySnapshot,
    speech_coverage_receipt: SpeechCoverageReceipt | None,
    current_ledger_hash: str,
    policy: NativeReleasePolicyV2,
) -> NativeReleaseResultV2:
    if type(exact_bytes) is not bytes:
        raise NativeReleaseError("Native Release Result requires exact bytes")
    try:
        parsed = _RESULT_ADAPTER.validate_json(exact_bytes, strict=True)
    except (ValidationError, ValueError) as exc:
        raise NativeReleaseError("Native Release Result bytes violate strict schema") from exc
    if canonical_json_bytes(parsed) != exact_bytes:
        raise NativeReleaseError("Native Release Result bytes are not canonical")
    rebuilt = evaluate_native_release(
        canonical_result=canonical_result,
        audit_plan=audit_plan,
        text_record=text_record,
        audio_record=audio_record,
        full_audit_aggregate=full_audit_aggregate,
        acceptance_policy=acceptance_policy,
        speech_coverage_receipt=speech_coverage_receipt,
        current_ledger_hash=current_ledger_hash,
        policy=policy,
    )
    if parsed != rebuilt or exact_bytes != canonical_json_bytes(rebuilt):
        raise NativeReleaseError("Native Release Result differs from exact frozen parents")
    return rebuilt


__all__ = [
    "NativeReleaseAttestationV2",
    "NativeReleaseCellDispositionV2",
    "NativeReleaseError",
    "NativeReleaseIssueDispositionV2",
    "NativeReleasePolicyV2",
    "NativeReleaseResultV2",
    "NativeReleaseReviewRequiredV2",
    "NativeReleaseSystemDispositionV2",
    "NativeReleaseWarningDispositionV2",
    "build_native_release_policy",
    "evaluate_native_release",
    "native_release_policy_bytes",
    "native_release_result_bytes",
    "verify_native_release_policy",
    "verify_native_release_result",
]
