"""Typed authorization and Ledger events for native Subtitle V2 resolution.

Native Full Audit discoveries are hypotheses, and an acceptance verdict is only
authorization.  This module supplies the deliberately separate contracts that
turn one exact authorization into a replayable decision event.  None of the
public request contracts accepts replacement text.

``confirm_original`` has an independent human-review path.  Rejecting a proposed
candidate never implies that the current spelling is correct; the original must
earn its own pronunciation receipt and, when audio cannot distinguish spelling,
complete active Reference authority.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from datetime import datetime
from typing import Annotated, Literal, TypeAlias, TypeVar

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    StringConstraints,
    ValidationError,
    field_validator,
    model_validator,
)

from shared.schemas.podcast_subtitles_v2 import (
    RecognitionEvidence,
    recognition_evidence_content_hash,
    recognition_evidence_set_hash,
)
from shared.schemas.podcast_subtitles_v2_audio_audit import AudioDiscoveredCandidateV2
from shared.schemas.podcast_subtitles_v2_text_audit import TextDiscoveredCandidateV2

from .adapters.reference import LocalReferenceRetriever, ReferenceExactLookupRequest
from .correction_acceptance import (
    CorrectionAcceptanceVerdictV2,
    HumanReviewerAttestationV2,
)
from .full_audit_attestation import (
    FullAuditAggregateAttestationV2,
    SelectiveAuditAggregateAttestationV3,
)
from .hashing import canonical_json_bytes, hash_object, sha256_bytes
from .reference_claims import (
    ReferenceAuthorityProofV2,
    ReferenceAuthorityTargetV2,
    ReferenceClaimScope,
    ReferenceEvidenceOmissionSpecV2,
    ReferenceExactClaimSpecV2,
    normalize_resolution_key,
    verify_reference_authority_proof,
)

Sha256 = Annotated[str, StringConstraints(pattern=r"^[0-9a-f]{64}$")]
CandidateDiscoveryV2: TypeAlias = TextDiscoveredCandidateV2 | AudioDiscoveredCandidateV2
NativeAuditAggregate: TypeAlias = (
    FullAuditAggregateAttestationV2 | SelectiveAuditAggregateAttestationV3
)
OriginalPronunciationRelationV2 = Literal[
    "pronunciations_distinct",
    "homophone_or_orthographically_indiscriminable",
]
OriginalConfirmationReasoningCodeV2 = Literal[
    "audible_pronunciation_matches_original_and_differs_candidate",
    "audio_matches_original_but_cannot_choose_orthography",
]
OriginalConfirmationActionV2 = Literal["confirm_original", "defer"]
OriginalConfirmationReasonV2 = Literal[
    "human_receipt_missing",
    "human_receipt_lineage_mismatch",
    "human_receipt_artifact_duplicated",
    "duplicate_human_reviewer",
    "human_quorum_not_met",
    "human_receipt_disagreement",
    "reference_proof_missing_for_orthography",
    "reference_scope_not_authorizable",
    "reference_coverage_incomplete",
    "reference_authoritative_original_literal_missing",
    "reference_conflict_unresolved",
    "all_required_evidence_confirms_exact_original",
]
NativeResolutionActionV2 = Literal[
    "accept_exact_candidate",
    "confirm_original",
    "reject_candidate",
    "defer",
]
NativeAuthorizationKindV2 = Literal[
    "correction_acceptance",
    "original_confirmation",
]
NativeResolveCheckpointStageV2 = Literal[
    "audit_basis_ready",
    "text_audit_ready",
    "audio_audit_ready",
]

_AUTHORISABLE_SCOPES: tuple[ReferenceClaimScope, ...] = (
    "literal_terminology",
    "owner_approved_glossary_spelling",
    "source_author",
    "source_title",
)
_ORIGINAL_REASON_ORDER: tuple[OriginalConfirmationReasonV2, ...] = (
    "human_receipt_missing",
    "human_receipt_lineage_mismatch",
    "human_receipt_artifact_duplicated",
    "duplicate_human_reviewer",
    "human_quorum_not_met",
    "human_receipt_disagreement",
    "reference_proof_missing_for_orthography",
    "reference_scope_not_authorizable",
    "reference_coverage_incomplete",
    "reference_authoritative_original_literal_missing",
    "reference_conflict_unresolved",
    "all_required_evidence_confirms_exact_original",
)
_UTC_TIMESTAMP = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$")
_GENERATION_ID = re.compile(r"^(?:[0-9a-f]{64}|generation-[0-9a-f]{64})$")
_NATIVE_EVENT_ID = re.compile(r"^native-resolution-[0-9a-f]{64}$")
_NATIVE_CHECKPOINT_ID = re.compile(r"^native-resolve-checkpoint-[0-9a-f]{64}$")
_ModelT = TypeVar("_ModelT", bound=BaseModel)


class NativeResolutionError(ValueError):
    """Native resolution bytes or frozen-parent lineage are invalid."""


class _StrictContract(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)


def _bounded(value: str, label: str, *, maximum: int = 16_384) -> str:
    if not value.strip():
        raise ValueError(f"{label} must not be blank")
    if len(value) > maximum:
        raise ValueError(f"{label} exceeds {maximum} Unicode scalars")
    if any(unicodedata.category(character) in {"Cc", "Cs"} for character in value):
        raise ValueError(f"{label} contains a forbidden control character")
    return value


def _validate_utc_timestamp(value: str) -> str:
    if not _UTC_TIMESTAMP.fullmatch(value):
        raise ValueError("reviewed_at_utc must be second-precision RFC 3339 UTC")
    try:
        datetime.strptime(value, "%Y-%m-%dT%H:%M:%SZ")
    except ValueError as exc:
        raise ValueError("reviewed_at_utc is not a real UTC timestamp") from exc
    return value


def _identity_payload(value: BaseModel) -> dict[str, object]:
    return value.model_dump(mode="json", exclude={"id", "content_hash"})


def _require_identity(value: BaseModel, identity: str, content_hash: str, label: str) -> None:
    expected = hash_object(_identity_payload(value))
    if identity != expected or content_hash != expected:
        raise ValueError(f"{label} identity/content hash mismatch")


def _seal(model: type[_ModelT], payload: dict[str, object]) -> _ModelT:
    digest = hash_object(payload)
    return model(**payload, id=digest, content_hash=digest)


def _seal_native_event(payload: dict[str, object]) -> "NativeCorrectionDecisionV2":
    digest = hash_object(payload)
    return NativeCorrectionDecisionV2(
        **payload,
        event_id=f"native-resolution-{digest}",
        content_hash=digest,
    )


def _validate_exact_model(value: object, model: type[_ModelT], label: str) -> _ModelT:
    if not isinstance(value, model):
        raise NativeResolutionError(f"{label} has the wrong artifact type")
    exact = canonical_json_bytes(value)
    try:
        replayed = model.model_validate_json(exact, strict=True)
    except (ValidationError, ValueError) as exc:
        raise NativeResolutionError(f"{label} is not an exact valid artifact") from exc
    if replayed != value:
        raise NativeResolutionError(f"{label} is not an exact valid artifact")
    return replayed


def _verify_canonical_bytes(exact_bytes: bytes, model: type[_ModelT], label: str) -> _ModelT:
    if type(exact_bytes) is not bytes:
        raise NativeResolutionError(f"{label} requires exact bytes")
    try:
        parsed = model.model_validate_json(exact_bytes, strict=True)
    except (ValidationError, ValueError) as exc:
        raise NativeResolutionError(f"{label} bytes violate strict v2 schema") from exc
    if canonical_json_bytes(parsed) != exact_bytes:
        raise NativeResolutionError(f"{label} bytes are not canonical")
    return parsed


def _recognition_registry(
    evidence: tuple[RecognitionEvidence, ...],
) -> tuple[tuple[str, ...], dict[str, object]]:
    hashes: list[str] = []
    registry: dict[str, object] = {}
    for index, item in enumerate(evidence):
        exact = _validate_exact_model(item, RecognitionEvidence, f"Recognition Evidence {index}")
        source_hash = recognition_evidence_content_hash(exact)
        if source_hash in hashes:
            raise NativeResolutionError("Recognition Evidence contains duplicate content")
        hashes.append(source_hash)
        for token in exact.tokens:
            registry[f"{source_hash}:{token.id}"] = token
    return tuple(sorted(hashes)), registry


class HumanOriginalConfirmationReceiptV2(_StrictContract):
    """Human pronunciation evidence for the existing literal, never mutation authority."""

    schema_version: Literal[2] = 2
    id: Sha256
    candidate_discovery_id: Sha256
    candidate_discovery_hash: Sha256
    full_audit_aggregate_id: Sha256
    full_audit_aggregate_hash: Sha256
    normalized_audio_hash: Sha256
    clip_lineage_kind: Literal["audio_audit_candidate_clip", "recognition_token_window"]
    clip_lineage_parent_id: Sha256
    clip_extraction_policy_hash: Sha256
    clip_sha256: Sha256
    clip_size_bytes: int = Field(gt=0)
    clip_start_ms: int = Field(ge=0)
    clip_end_ms: int = Field(gt=0)
    affected_token_ids: tuple[str, ...]
    cited_span_ids: tuple[str, ...]
    cited_recognition_evidence_ids: tuple[str, ...]
    original_text: str
    rejected_candidate_text: str
    pronunciation_outcome: Literal["compatible"] = "compatible"
    candidate_pronunciation_relation: OriginalPronunciationRelationV2
    reasoning_code: OriginalConfirmationReasoningCodeV2
    notes_digest: Sha256
    reviewer: HumanReviewerAttestationV2
    reviewed_at_utc: str
    authority: Literal["human_pronunciation_review_not_spelling_or_text_mutation_authority"] = (
        "human_pronunciation_review_not_spelling_or_text_mutation_authority"
    )
    content_hash: Sha256

    @field_validator("original_text", "rejected_candidate_text")
    @classmethod
    def _text_is_safe(cls, value: str, info: object) -> str:
        return _bounded(value, str(getattr(info, "field_name", "reviewed text")))

    @field_validator("affected_token_ids", "cited_span_ids", "cited_recognition_evidence_ids")
    @classmethod
    def _identifiers_are_exact(cls, values: tuple[str, ...], info: object) -> tuple[str, ...]:
        label = str(getattr(info, "field_name", "identifiers"))
        if len(set(values)) != len(values):
            raise ValueError(f"{label} must not contain duplicates")
        for value in values:
            _bounded(value, label, maximum=512)
        return values

    @field_validator("reviewed_at_utc")
    @classmethod
    def _timestamp_is_exact(cls, value: str) -> str:
        return _validate_utc_timestamp(value)

    @model_validator(mode="after")
    def _receipt_is_exact(self) -> "HumanOriginalConfirmationReceiptV2":
        if self.clip_end_ms <= self.clip_start_ms:
            raise ValueError("human original confirmation clip must have a positive interval")
        if not self.affected_token_ids or not self.cited_recognition_evidence_ids:
            raise ValueError(
                "human original confirmation requires exact token and Recognition Evidence IDs"
            )
        if self.original_text == self.rejected_candidate_text:
            raise ValueError("human original confirmation cannot review a no-op candidate")
        expected_reason = {
            "pronunciations_distinct": (
                "audible_pronunciation_matches_original_and_differs_candidate"
            ),
            "homophone_or_orthographically_indiscriminable": (
                "audio_matches_original_but_cannot_choose_orthography"
            ),
        }[self.candidate_pronunciation_relation]
        if self.reasoning_code != expected_reason:
            raise ValueError("original pronunciation relation contradicts reasoning code")
        _require_identity(self, self.id, self.content_hash, "Human original confirmation")
        return self


class OriginalConfirmationPolicyV2(_StrictContract):
    """Closed policy for proving the current literal rather than a candidate."""

    schema_version: Literal[2] = 2
    id: Sha256
    minimum_human_reviewers: int = Field(ge=1, le=3)
    require_human_pronunciation_for_every_confirmation: Literal[True] = True
    require_reference_when_audio_cannot_choose_orthography: Literal[True] = True
    require_complete_reference_coverage: Literal[True] = True
    require_active_authoritative_original_literal: Literal[True] = True
    authorisable_reference_scopes: tuple[ReferenceClaimScope, ...] = _AUTHORISABLE_SCOPES
    authority: Literal["policy_not_correction_decision"] = "policy_not_correction_decision"
    content_hash: Sha256

    @model_validator(mode="after")
    def _policy_is_closed(self) -> "OriginalConfirmationPolicyV2":
        if self.authorisable_reference_scopes != _AUTHORISABLE_SCOPES:
            raise ValueError("original confirmation cannot broaden Reference authority scopes")
        _require_identity(self, self.id, self.content_hash, "Original confirmation policy")
        return self


class OriginalConfirmationAuthorizationV2(_StrictContract):
    """Authorization to preserve exact original text; never a mutation itself."""

    schema_version: Literal[2] = 2
    id: Sha256
    action: OriginalConfirmationActionV2
    candidate_modality: Literal["text", "audio"]
    candidate_discovery_id: Sha256
    candidate_discovery_hash: Sha256
    full_audit_aggregate_id: Sha256
    full_audit_aggregate_hash: Sha256
    recognition_evidence_hashes: tuple[Sha256, ...]
    recognition_evidence_set_hash: Sha256
    normalized_audio_hash: Sha256
    resolution_key: str
    reference_scope: ReferenceClaimScope
    affected_token_ids: tuple[str, ...]
    cited_span_ids: tuple[str, ...]
    cited_recognition_evidence_ids: tuple[str, ...]
    original_text: str
    rejected_candidate_text: str
    orthography_requires_reference: bool
    human_original_receipt_ids: tuple[Sha256, ...]
    human_original_receipt_hashes: tuple[Sha256, ...]
    reference_proof_id: Sha256 | None
    reference_proof_hash: Sha256 | None
    reference_coverage_receipt_id: Sha256 | None
    reference_coverage_receipt_hash: Sha256 | None
    reference_conflict_set_id: Sha256 | None
    reference_conflict_set_hash: Sha256 | None
    selected_reference_claim_id: Sha256 | None
    selected_reference_claim_hash: Sha256 | None
    selected_reference_literal: str | None
    policy_id: Sha256
    policy_hash: Sha256
    reasons: tuple[OriginalConfirmationReasonV2, ...]
    authority: Literal["authorization_not_mutation"] = "authorization_not_mutation"
    content_hash: Sha256

    @field_validator("resolution_key")
    @classmethod
    def _resolution_key_is_canonical(cls, value: str) -> str:
        _bounded(value, "resolution_key", maximum=512)
        if value != normalize_resolution_key(value):
            raise ValueError("resolution_key must already be deterministically normalized")
        return value

    @field_validator("original_text", "rejected_candidate_text", "selected_reference_literal")
    @classmethod
    def _literal_is_safe(cls, value: str | None, info: object) -> str | None:
        if value is None:
            return None
        return _bounded(value, str(getattr(info, "field_name", "literal")))

    @field_validator("orthography_requires_reference", mode="before")
    @classmethod
    def _orthography_flag_is_exact(cls, value: object) -> object:
        if type(value) is not bool:
            raise ValueError("orthography_requires_reference requires exact bool")
        return value

    @model_validator(mode="after")
    def _authorization_is_exact(self) -> "OriginalConfirmationAuthorizationV2":
        if self.original_text == self.rejected_candidate_text:
            raise ValueError("original confirmation cannot authorize a no-op candidate")
        if (
            self.human_original_receipt_ids != tuple(sorted(self.human_original_receipt_ids))
            or self.human_original_receipt_hashes
            != tuple(sorted(self.human_original_receipt_hashes))
            or len(self.human_original_receipt_ids) != len(self.human_original_receipt_hashes)
        ):
            raise ValueError("human original receipt parents are not canonical")
        expected_reasons = tuple(
            reason for reason in _ORIGINAL_REASON_ORDER if reason in set(self.reasons)
        )
        if not self.reasons or len(set(self.reasons)) != len(self.reasons):
            raise ValueError("original confirmation requires unique typed reasons")
        if self.reasons != expected_reasons:
            raise ValueError("original confirmation reasons are not canonical")
        proof_fields = (
            self.reference_proof_id,
            self.reference_proof_hash,
            self.reference_coverage_receipt_id,
            self.reference_coverage_receipt_hash,
            self.reference_conflict_set_id,
            self.reference_conflict_set_hash,
        )
        if any(value is None for value in proof_fields) != all(
            value is None for value in proof_fields
        ):
            raise ValueError("Reference proof lineage must be wholly present or absent")
        selected = (
            self.selected_reference_claim_id,
            self.selected_reference_claim_hash,
            self.selected_reference_literal,
        )
        if any(value is None for value in selected) != all(value is None for value in selected):
            raise ValueError("selected Reference claim must be wholly present or absent")
        if self.action == "confirm_original":
            if self.reasons != ("all_required_evidence_confirms_exact_original",):
                raise ValueError("confirmed original must have only the success reason")
            if not self.human_original_receipt_ids:
                raise ValueError("confirmed original lacks human pronunciation receipts")
            if self.orthography_requires_reference:
                if any(value is None for value in proof_fields + selected):
                    raise ValueError(
                        "orthographically ambiguous original lacks Reference authority"
                    )
                if self.selected_reference_literal != self.original_text:
                    raise ValueError("selected Reference literal differs from exact original")
            elif any(value is not None for value in selected):
                raise ValueError(
                    "distinct pronunciation confirmation cannot claim spelling authority"
                )
        else:
            if "all_required_evidence_confirms_exact_original" in self.reasons:
                raise ValueError("deferred original confirmation cannot carry success authority")
            if any(value is not None for value in selected):
                raise ValueError("deferred original confirmation cannot select a Reference claim")
        _require_identity(self, self.id, self.content_hash, "Original confirmation authorization")
        return self


class NativeCorrectionDecisionV2(_StrictContract):
    """Replayable Ledger event derived from one exact authorization artifact."""

    schema_version: Literal[2] = 2
    event_id: str
    decision_kind: Literal["native_correction_v2"] = "native_correction_v2"
    episode_id: str
    generation_id: str
    action: NativeResolutionActionV2
    authorization_kind: NativeAuthorizationKindV2
    authorization_id: Sha256
    authorization_hash: Sha256
    authorization_action: Literal[
        "accept_exact_candidate",
        "confirm_original",
        "reject",
        "defer",
    ]
    candidate_modality: Literal["text", "audio"]
    candidate_discovery_id: Sha256
    candidate_discovery_hash: Sha256
    full_audit_aggregate_id: Sha256
    full_audit_aggregate_hash: Sha256
    normalized_audio_hash: Sha256
    reviewed_fingerprint: Sha256
    target_span_ids: tuple[str, ...]
    affected_token_ids: tuple[str, ...]
    cited_recognition_evidence_ids: tuple[str, ...]
    target_start_ms: int = Field(ge=0)
    target_end_ms: int = Field(gt=0)
    original_literal_sha256: Sha256
    candidate_literal_sha256: Sha256
    authorized_literal_sha256: Sha256 | None
    mutates_text: bool
    authority: Literal["typed_authorization_event_not_free_text"] = (
        "typed_authorization_event_not_free_text"
    )
    content_hash: Sha256

    @field_validator("episode_id", "generation_id")
    @classmethod
    def _identity_text_is_safe(cls, value: str, info: object) -> str:
        return _bounded(value, str(getattr(info, "field_name", "identity")), maximum=256)

    @field_validator("target_span_ids", "affected_token_ids", "cited_recognition_evidence_ids")
    @classmethod
    def _event_ids_are_exact(cls, values: tuple[str, ...], info: object) -> tuple[str, ...]:
        label = str(getattr(info, "field_name", "identifiers"))
        if not values or len(set(values)) != len(values):
            raise ValueError(f"{label} must be non-empty and unique")
        for value in values:
            _bounded(value, label, maximum=512)
        return values

    @field_validator("mutates_text", mode="before")
    @classmethod
    def _mutation_flag_is_exact(cls, value: object) -> object:
        if type(value) is not bool:
            raise ValueError("mutates_text requires exact bool")
        return value

    @model_validator(mode="after")
    def _event_is_exact(self) -> "NativeCorrectionDecisionV2":
        if self.target_end_ms <= self.target_start_ms:
            raise ValueError("native decision target interval must be positive")
        expected_mutation = self.action == "accept_exact_candidate"
        if self.mutates_text != expected_mutation:
            raise ValueError("native decision mutation flag contradicts action")
        if expected_mutation:
            if self.authorization_kind != "correction_acceptance":
                raise ValueError("candidate acceptance requires acceptance authorization")
            if self.authorized_literal_sha256 != self.candidate_literal_sha256:
                raise ValueError("candidate acceptance authorized literal hash drift")
        elif self.action == "confirm_original":
            if self.authorization_kind != "original_confirmation":
                raise ValueError("original confirmation requires its separate authorization")
            if self.authorized_literal_sha256 != self.original_literal_sha256:
                raise ValueError("original confirmation authorized literal hash drift")
        elif self.authorized_literal_sha256 is not None:
            raise ValueError("defer/reject cannot carry an authorized literal")
        expected_payload = self.model_dump(mode="json", exclude={"event_id", "content_hash"})
        expected_hash = hash_object(expected_payload)
        if (
            self.event_id != f"native-resolution-{expected_hash}"
            or self.content_hash != expected_hash
        ):
            raise ValueError("native correction decision identity/content hash mismatch")
        return self


class NativeResolveCheckpointV2(_StrictContract):
    """Immutable recovery boundary for one exact native child Full Audit."""

    schema_version: Literal[2] = 2
    id: str
    operation_key: Sha256
    episode_id: str
    stage: NativeResolveCheckpointStageV2
    parent_generation_id: str
    parent_manifest_hash: Sha256
    expected_active_generation_id: str
    expected_ledger_head: Sha256
    prepared_ledger_entry_hash: Sha256
    decision_event_id: str
    decision_content_hash: Sha256
    authorization_kind: NativeAuthorizationKindV2
    authorization_id: Sha256
    authorization_hash: Sha256
    audit_basis_generation_id: str
    audit_basis_transcript_hash: Sha256
    policy_hash: Sha256
    code_hash: Sha256
    reference_enrollment_hash: Sha256
    adapter_identities_hash: Sha256
    artifact_hashes: dict[str, Sha256]
    previous_checkpoint_id: str | None = None

    @field_validator("episode_id")
    @classmethod
    def _episode_is_safe(cls, value: str) -> str:
        return _bounded(value, "episode_id", maximum=256)

    @model_validator(mode="after")
    def _checkpoint_is_exact(self) -> "NativeResolveCheckpointV2":
        if not _NATIVE_CHECKPOINT_ID.fullmatch(self.id):
            raise ValueError("native resolve checkpoint ID is invalid")
        if any(
            not _GENERATION_ID.fullmatch(generation_id)
            for generation_id in (
                self.parent_generation_id,
                self.expected_active_generation_id,
                self.audit_basis_generation_id,
            )
        ):
            raise ValueError("native resolve checkpoint Generation ID is invalid")
        if self.parent_generation_id != self.expected_active_generation_id:
            raise ValueError("native resolve checkpoint active parent binding differs")
        if not _NATIVE_EVENT_ID.fullmatch(self.decision_event_id):
            raise ValueError("native resolve checkpoint Decision event ID is invalid")
        if self.decision_event_id != f"native-resolution-{self.decision_content_hash}":
            raise ValueError("native resolve checkpoint Decision identity differs")
        names = list(self.artifact_hashes)
        if not names or names != sorted(names):
            raise ValueError(
                "native resolve checkpoint artifact hashes must be non-empty and sorted"
            )
        if any(
            not name.startswith("native_resolve/")
            or name.startswith("/")
            or "\\" in name
            or any(part in {"", ".", ".."} for part in name.split("/"))
            for name in names
        ):
            raise ValueError("native resolve checkpoint artifact name is unsafe")
        if self.stage == "audit_basis_ready":
            if self.previous_checkpoint_id is not None:
                raise ValueError("audit basis checkpoint must not name a predecessor")
        elif self.previous_checkpoint_id is None:
            raise ValueError("first native resolve checkpoint must be audit_basis_ready")
        elif not _NATIVE_CHECKPOINT_ID.fullmatch(self.previous_checkpoint_id):
            raise ValueError("native resolve checkpoint predecessor ID is invalid")
        expected = "native-resolve-checkpoint-" + hash_object(
            self.model_dump(mode="json", exclude={"id"})
        )
        if self.id != expected:
            raise ValueError("native resolve checkpoint ID does not address its exact content")
        return self


def default_original_confirmation_policy(
    *, minimum_human_reviewers: int = 1
) -> OriginalConfirmationPolicyV2:
    payload: dict[str, object] = {
        "schema_version": 2,
        "minimum_human_reviewers": minimum_human_reviewers,
        "require_human_pronunciation_for_every_confirmation": True,
        "require_reference_when_audio_cannot_choose_orthography": True,
        "require_complete_reference_coverage": True,
        "require_active_authoritative_original_literal": True,
        "authorisable_reference_scopes": _AUTHORISABLE_SCOPES,
        "authority": "policy_not_correction_decision",
    }
    return _seal(OriginalConfirmationPolicyV2, payload)


def build_native_resolve_checkpoint(
    *,
    operation_key: str,
    episode_id: str,
    stage: NativeResolveCheckpointStageV2,
    parent_generation_id: str,
    parent_manifest_hash: str,
    expected_active_generation_id: str,
    expected_ledger_head: str,
    prepared_ledger_entry_hash: str,
    decision_event_id: str,
    decision_content_hash: str,
    authorization_kind: NativeAuthorizationKindV2,
    authorization_id: str,
    authorization_hash: str,
    audit_basis_generation_id: str,
    audit_basis_transcript_hash: str,
    policy_hash: str,
    code_hash: str,
    reference_enrollment_hash: str,
    adapter_identities_hash: str,
    artifact_hashes: dict[str, str],
    previous_checkpoint_id: str | None = None,
) -> NativeResolveCheckpointV2:
    """Build one native resolve checkpoint from its exact immutable identity."""

    if list(artifact_hashes) != sorted(artifact_hashes):
        raise ValueError("native resolve checkpoint artifact hashes must be sorted")
    payload: dict[str, object] = {
        "schema_version": 2,
        "operation_key": operation_key,
        "episode_id": episode_id,
        "stage": stage,
        "parent_generation_id": parent_generation_id,
        "parent_manifest_hash": parent_manifest_hash,
        "expected_active_generation_id": expected_active_generation_id,
        "expected_ledger_head": expected_ledger_head,
        "prepared_ledger_entry_hash": prepared_ledger_entry_hash,
        "decision_event_id": decision_event_id,
        "decision_content_hash": decision_content_hash,
        "authorization_kind": authorization_kind,
        "authorization_id": authorization_id,
        "authorization_hash": authorization_hash,
        "audit_basis_generation_id": audit_basis_generation_id,
        "audit_basis_transcript_hash": audit_basis_transcript_hash,
        "policy_hash": policy_hash,
        "code_hash": code_hash,
        "reference_enrollment_hash": reference_enrollment_hash,
        "adapter_identities_hash": adapter_identities_hash,
        "artifact_hashes": artifact_hashes,
        "previous_checkpoint_id": previous_checkpoint_id,
    }
    checkpoint_id = "native-resolve-checkpoint-" + hash_object(payload)
    return NativeResolveCheckpointV2(id=checkpoint_id, **payload)


def _original_receipt_binding_is_exact(
    receipt: HumanOriginalConfirmationReceiptV2,
    *,
    candidate: CandidateDiscoveryV2,
    aggregate: NativeAuditAggregate,
    recognition_registry: dict[str, object],
) -> bool:
    if (
        receipt.candidate_discovery_id != candidate.id
        or receipt.candidate_discovery_hash != candidate.content_hash
        or receipt.full_audit_aggregate_id != aggregate.id
        or receipt.full_audit_aggregate_hash != aggregate.content_hash
        or receipt.normalized_audio_hash != aggregate.normalized_audio_hash
        or receipt.affected_token_ids != candidate.affected_token_ids
        or receipt.cited_span_ids != candidate.cited_span_ids
        or receipt.cited_recognition_evidence_ids != candidate.cited_recognition_evidence_ids
        or receipt.original_text != candidate.observed_text
        or receipt.rejected_candidate_text != candidate.candidate_text
    ):
        return False
    if isinstance(candidate, AudioDiscoveredCandidateV2):
        if (
            receipt.clip_lineage_kind != "audio_audit_candidate_clip"
            or receipt.clip_lineage_parent_id != candidate.packet_id
            or receipt.clip_sha256 != candidate.clip_hash
        ):
            return False
    elif (
        receipt.clip_lineage_kind != "recognition_token_window"
        or receipt.clip_lineage_parent_id != candidate.id
    ):
        return False
    try:
        cited_tokens = tuple(
            recognition_registry[item] for item in candidate.cited_recognition_evidence_ids
        )
    except KeyError:
        return False
    return all(
        receipt.clip_start_ms <= int(getattr(token, "start_ms"))
        and int(getattr(token, "end_ms")) <= receipt.clip_end_ms
        for token in cited_tokens
    )


def _canonical_original_reasons(
    reasons: set[OriginalConfirmationReasonV2],
) -> tuple[OriginalConfirmationReasonV2, ...]:
    return tuple(reason for reason in _ORIGINAL_REASON_ORDER if reason in reasons)


def build_original_confirmation_authorization(
    *,
    candidate: CandidateDiscoveryV2,
    full_audit_aggregate: NativeAuditAggregate,
    recognition_evidence: tuple[RecognitionEvidence, ...],
    resolution_key: str,
    reference_scope: ReferenceClaimScope,
    reference_proof: ReferenceAuthorityProofV2 | None,
    human_original_receipts: tuple[HumanOriginalConfirmationReceiptV2, ...],
    policy: OriginalConfirmationPolicyV2,
) -> OriginalConfirmationAuthorizationV2:
    """Authorize preservation of exact original text from immutable parents."""

    if not isinstance(candidate, (TextDiscoveredCandidateV2, AudioDiscoveredCandidateV2)):
        raise NativeResolutionError("candidate must be a typed v2 discovery artifact")
    candidate = _validate_exact_model(candidate, type(candidate), "candidate discovery")
    if not isinstance(
        full_audit_aggregate,
        (FullAuditAggregateAttestationV2, SelectiveAuditAggregateAttestationV3),
    ):
        raise NativeResolutionError("Full Audit aggregate has an unsupported schema")
    aggregate = _validate_exact_model(
        full_audit_aggregate,
        type(full_audit_aggregate),
        "Full Audit aggregate",
    )
    policy = _validate_exact_model(
        policy, OriginalConfirmationPolicyV2, "original confirmation policy"
    )
    _bounded(resolution_key, "resolution_key", maximum=512)
    if resolution_key != normalize_resolution_key(resolution_key):
        raise NativeResolutionError("resolution_key is not canonical")
    modality: Literal["text", "audio"] = (
        "text" if isinstance(candidate, TextDiscoveredCandidateV2) else "audio"
    )
    discovery_ids = (
        aggregate.text_discovery_ids if modality == "text" else aggregate.audio_discovery_ids
    )
    if candidate.id not in discovery_ids or candidate.cell_id not in aggregate.all_cell_ids:
        raise NativeResolutionError("candidate is not a member of the Full Audit aggregate")

    evidence_hashes, recognition_registry = _recognition_registry(recognition_evidence)
    if (
        not evidence_hashes
        or evidence_hashes != aggregate.recognition_evidence_hashes
        or recognition_evidence_set_hash(recognition_evidence)
        != aggregate.audit_recognition_evidence_set_hash
        or any(
            item.normalized_audio_hash != aggregate.normalized_audio_hash
            for item in recognition_evidence
        )
        or not set(candidate.cited_recognition_evidence_ids) <= set(recognition_registry)
    ):
        raise NativeResolutionError("Recognition Evidence differs from Full Audit lineage")

    receipts = tuple(
        sorted(
            (
                _validate_exact_model(
                    item,
                    HumanOriginalConfirmationReceiptV2,
                    "human original confirmation receipt",
                )
                for item in human_original_receipts
            ),
            key=lambda item: item.id,
        )
    )
    exact_receipts = tuple(
        item
        for item in receipts
        if _original_receipt_binding_is_exact(
            item,
            candidate=candidate,
            aggregate=aggregate,
            recognition_registry=recognition_registry,
        )
    )
    reasons: set[OriginalConfirmationReasonV2] = set()
    if not receipts:
        reasons.add("human_receipt_missing")
    elif len(exact_receipts) != len(receipts):
        reasons.add("human_receipt_lineage_mismatch")
    if len({item.id for item in receipts}) != len(receipts):
        reasons.add("human_receipt_artifact_duplicated")
    reviewer_ids = tuple(item.reviewer.reviewer_id for item in exact_receipts)
    if len(set(reviewer_ids)) != len(reviewer_ids):
        reasons.add("duplicate_human_reviewer")
    if len(set(reviewer_ids)) < policy.minimum_human_reviewers:
        reasons.add("human_quorum_not_met")
    audio_lineages = {
        (
            item.clip_lineage_kind,
            item.clip_lineage_parent_id,
            item.clip_extraction_policy_hash,
            item.clip_sha256,
            item.clip_start_ms,
            item.clip_end_ms,
        )
        for item in exact_receipts
    }
    relations = {item.candidate_pronunciation_relation for item in exact_receipts}
    if len(audio_lineages) > 1 or len(relations) > 1:
        reasons.add("human_receipt_disagreement")
    orthography_requires_reference = relations == {"homophone_or_orthographically_indiscriminable"}

    proof: ReferenceAuthorityProofV2 | None = None
    selected_claim = None
    if reference_proof is not None:
        proof = _validate_exact_model(
            reference_proof,
            ReferenceAuthorityProofV2,
            "Reference authority proof",
        )
    if orthography_requires_reference:
        if proof is None:
            reasons.add("reference_proof_missing_for_orthography")
        else:
            if reference_scope not in policy.authorisable_reference_scopes:
                reasons.add("reference_scope_not_authorizable")
            if (
                not proof.coverage.selection_complete
                or proof.coverage.bounded_retrieval_complete is not True
                or proof.conflicts.unresolved_conditions
            ):
                reasons.add("reference_coverage_incomplete")
            scoped_claims = tuple(
                item
                for item in proof.claims
                if item.resolution_key == resolution_key and item.scope == reference_scope
            )
            exact_original_claims = tuple(
                item
                for item in scoped_claims
                if item.strength == "authoritative"
                and item.version_status == "active"
                and item.claimed_text == candidate.observed_text
            )
            if not exact_original_claims:
                reasons.add("reference_authoritative_original_literal_missing")
            relevant_conflicts = tuple(
                item
                for item in proof.conflicts.conflicts
                if item.resolution_key == resolution_key
                and item.overlapping_scope == reference_scope
            )
            if relevant_conflicts:
                reasons.add("reference_conflict_unresolved")
            if not reasons and exact_original_claims:
                selected_claim = exact_original_claims[0]

    if reasons:
        action: OriginalConfirmationActionV2 = "defer"
        selected_claim = None
        final_reasons = _canonical_original_reasons(reasons)
    else:
        action = "confirm_original"
        final_reasons = ("all_required_evidence_confirms_exact_original",)

    payload: dict[str, object] = {
        "schema_version": 2,
        "action": action,
        "candidate_modality": modality,
        "candidate_discovery_id": candidate.id,
        "candidate_discovery_hash": candidate.content_hash,
        "full_audit_aggregate_id": aggregate.id,
        "full_audit_aggregate_hash": aggregate.content_hash,
        "recognition_evidence_hashes": evidence_hashes,
        "recognition_evidence_set_hash": recognition_evidence_set_hash(recognition_evidence),
        "normalized_audio_hash": aggregate.normalized_audio_hash,
        "resolution_key": resolution_key,
        "reference_scope": reference_scope,
        "affected_token_ids": candidate.affected_token_ids,
        "cited_span_ids": candidate.cited_span_ids,
        "cited_recognition_evidence_ids": candidate.cited_recognition_evidence_ids,
        "original_text": candidate.observed_text,
        "rejected_candidate_text": candidate.candidate_text,
        "orthography_requires_reference": orthography_requires_reference,
        "human_original_receipt_ids": tuple(item.id for item in receipts),
        "human_original_receipt_hashes": tuple(sorted(item.content_hash for item in receipts)),
        "reference_proof_id": proof.id if proof is not None else None,
        "reference_proof_hash": proof.content_hash if proof is not None else None,
        "reference_coverage_receipt_id": proof.coverage.id if proof is not None else None,
        "reference_coverage_receipt_hash": (
            proof.coverage.content_hash if proof is not None else None
        ),
        "reference_conflict_set_id": proof.conflicts.id if proof is not None else None,
        "reference_conflict_set_hash": (
            proof.conflicts.content_hash if proof is not None else None
        ),
        "selected_reference_claim_id": (selected_claim.id if selected_claim is not None else None),
        "selected_reference_claim_hash": (
            selected_claim.content_hash if selected_claim is not None else None
        ),
        "selected_reference_literal": (
            selected_claim.claimed_text if selected_claim is not None else None
        ),
        "policy_id": policy.id,
        "policy_hash": policy.content_hash,
        "reasons": final_reasons,
        "authority": "authorization_not_mutation",
    }
    return _seal(OriginalConfirmationAuthorizationV2, payload)


def _authorization_candidate_binding(
    authorization: CorrectionAcceptanceVerdictV2 | OriginalConfirmationAuthorizationV2,
    candidate: CandidateDiscoveryV2,
) -> bool:
    common = (
        authorization.candidate_discovery_id == candidate.id
        and authorization.candidate_discovery_hash == candidate.content_hash
        and authorization.affected_token_ids == candidate.affected_token_ids
        and authorization.cited_span_ids == candidate.cited_span_ids
        and authorization.cited_recognition_evidence_ids == candidate.cited_recognition_evidence_ids
    )
    if isinstance(authorization, CorrectionAcceptanceVerdictV2):
        return (
            common
            and authorization.original_text == candidate.observed_text
            and authorization.candidate_text == candidate.candidate_text
        )
    return (
        common
        and authorization.original_text == candidate.observed_text
        and authorization.rejected_candidate_text == candidate.candidate_text
    )


def build_native_correction_decision(
    *,
    episode_id: str,
    parent_generation_id: str,
    reviewed_fingerprint: str,
    target_start_ms: int,
    target_end_ms: int,
    candidate: CandidateDiscoveryV2,
    authorization: CorrectionAcceptanceVerdictV2 | OriginalConfirmationAuthorizationV2,
) -> NativeCorrectionDecisionV2:
    """Derive the only Ledger event shape accepted by native resolution."""

    _bounded(episode_id, "episode_id", maximum=256)
    _bounded(parent_generation_id, "parent_generation_id", maximum=256)
    if not _GENERATION_ID.fullmatch(parent_generation_id):
        raise NativeResolutionError("parent_generation_id is not a valid Generation ID")
    if not re.fullmatch(r"[0-9a-f]{64}", reviewed_fingerprint):
        raise NativeResolutionError("reviewed_fingerprint must be lowercase SHA-256")
    if target_start_ms < 0 or target_end_ms <= target_start_ms:
        raise NativeResolutionError("native decision target interval must be positive")
    if not isinstance(candidate, (TextDiscoveredCandidateV2, AudioDiscoveredCandidateV2)):
        raise NativeResolutionError("candidate must be a typed v2 discovery artifact")
    candidate = _validate_exact_model(candidate, type(candidate), "candidate discovery")

    if isinstance(authorization, CorrectionAcceptanceVerdictV2):
        exact_authorization = _validate_exact_model(
            authorization,
            CorrectionAcceptanceVerdictV2,
            "Correction acceptance verdict",
        )
        authorization_kind: NativeAuthorizationKindV2 = "correction_acceptance"
        action: NativeResolutionActionV2 = {
            "accept_exact_candidate": "accept_exact_candidate",
            "defer": "defer",
            "reject": "reject_candidate",
        }[exact_authorization.action]
        authorization_action = exact_authorization.action
    elif isinstance(authorization, OriginalConfirmationAuthorizationV2):
        exact_authorization = _validate_exact_model(
            authorization,
            OriginalConfirmationAuthorizationV2,
            "Original confirmation authorization",
        )
        authorization_kind = "original_confirmation"
        action = exact_authorization.action
        authorization_action = exact_authorization.action
    else:
        raise NativeResolutionError("native decision requires a typed authorization artifact")
    if not _authorization_candidate_binding(exact_authorization, candidate):
        raise NativeResolutionError("authorization differs from exact candidate discovery")

    original_hash = sha256_bytes(candidate.observed_text.encode("utf-8"))
    candidate_hash = sha256_bytes(candidate.candidate_text.encode("utf-8"))
    authorized_hash = (
        candidate_hash
        if action == "accept_exact_candidate"
        else original_hash
        if action == "confirm_original"
        else None
    )
    payload: dict[str, object] = {
        "schema_version": 2,
        "decision_kind": "native_correction_v2",
        "episode_id": episode_id,
        "generation_id": parent_generation_id,
        "action": action,
        "authorization_kind": authorization_kind,
        "authorization_id": exact_authorization.id,
        "authorization_hash": exact_authorization.content_hash,
        "authorization_action": authorization_action,
        "candidate_modality": exact_authorization.candidate_modality,
        "candidate_discovery_id": candidate.id,
        "candidate_discovery_hash": candidate.content_hash,
        "full_audit_aggregate_id": exact_authorization.full_audit_aggregate_id,
        "full_audit_aggregate_hash": exact_authorization.full_audit_aggregate_hash,
        "normalized_audio_hash": exact_authorization.normalized_audio_hash,
        "reviewed_fingerprint": reviewed_fingerprint,
        "target_span_ids": candidate.cited_span_ids,
        "affected_token_ids": candidate.affected_token_ids,
        "cited_recognition_evidence_ids": candidate.cited_recognition_evidence_ids,
        "target_start_ms": target_start_ms,
        "target_end_ms": target_end_ms,
        "original_literal_sha256": original_hash,
        "candidate_literal_sha256": candidate_hash,
        "authorized_literal_sha256": authorized_hash,
        "mutates_text": action == "accept_exact_candidate",
        "authority": "typed_authorization_event_not_free_text",
    }
    return _seal_native_event(payload)


def human_original_confirmation_receipt_bytes(
    receipt: HumanOriginalConfirmationReceiptV2,
) -> bytes:
    return canonical_json_bytes(receipt)


def original_confirmation_policy_bytes(policy: OriginalConfirmationPolicyV2) -> bytes:
    return canonical_json_bytes(policy)


def original_confirmation_authorization_bytes(
    authorization: OriginalConfirmationAuthorizationV2,
) -> bytes:
    return canonical_json_bytes(authorization)


def native_correction_decision_bytes(decision: NativeCorrectionDecisionV2) -> bytes:
    return canonical_json_bytes(decision)


def verify_human_original_confirmation_receipt(
    exact_bytes: bytes,
) -> HumanOriginalConfirmationReceiptV2:
    return _verify_canonical_bytes(
        exact_bytes,
        HumanOriginalConfirmationReceiptV2,
        "Human original confirmation receipt",
    )


def verify_original_confirmation_policy(exact_bytes: bytes) -> OriginalConfirmationPolicyV2:
    return _verify_canonical_bytes(
        exact_bytes,
        OriginalConfirmationPolicyV2,
        "Original confirmation policy",
    )


def verify_original_confirmation_authorization(
    exact_bytes: bytes,
    *,
    candidate: CandidateDiscoveryV2,
    full_audit_aggregate: NativeAuditAggregate,
    recognition_evidence: tuple[RecognitionEvidence, ...],
    resolution_key: str,
    reference_scope: ReferenceClaimScope,
    reference_proof: ReferenceAuthorityProofV2 | None,
    human_original_receipts: tuple[HumanOriginalConfirmationReceiptV2, ...],
    policy: OriginalConfirmationPolicyV2,
) -> OriginalConfirmationAuthorizationV2:
    parsed = _verify_canonical_bytes(
        exact_bytes,
        OriginalConfirmationAuthorizationV2,
        "Original confirmation authorization",
    )
    rebuilt = build_original_confirmation_authorization(
        candidate=candidate,
        full_audit_aggregate=full_audit_aggregate,
        recognition_evidence=recognition_evidence,
        resolution_key=resolution_key,
        reference_scope=reference_scope,
        reference_proof=reference_proof,
        human_original_receipts=human_original_receipts,
        policy=policy,
    )
    if parsed != rebuilt or exact_bytes != canonical_json_bytes(rebuilt):
        raise NativeResolutionError(
            "Original confirmation authorization differs from frozen parents"
        )
    return rebuilt


def verify_native_correction_decision(
    exact_bytes: bytes,
    *,
    episode_id: str,
    parent_generation_id: str,
    reviewed_fingerprint: str,
    target_start_ms: int,
    target_end_ms: int,
    candidate: CandidateDiscoveryV2,
    authorization: CorrectionAcceptanceVerdictV2 | OriginalConfirmationAuthorizationV2,
) -> NativeCorrectionDecisionV2:
    parsed = _verify_canonical_bytes(
        exact_bytes,
        NativeCorrectionDecisionV2,
        "Native correction decision",
    )
    rebuilt = build_native_correction_decision(
        episode_id=episode_id,
        parent_generation_id=parent_generation_id,
        reviewed_fingerprint=reviewed_fingerprint,
        target_start_ms=target_start_ms,
        target_end_ms=target_end_ms,
        candidate=candidate,
        authorization=authorization,
    )
    if parsed != rebuilt or exact_bytes != canonical_json_bytes(rebuilt):
        raise NativeResolutionError("Native correction decision differs from frozen parents")
    return rebuilt


def verify_native_reference_authority_proof(
    exact_bytes: bytes,
    *,
    retriever: LocalReferenceRetriever,
    resolution_key: str,
    reference_scope: ReferenceClaimScope,
    stored_reference_evidence: tuple[object, ...],
) -> ReferenceAuthorityProofV2:
    """Rebuild a one-target proof from the Generation's exact stored references.

    A content-addressed proof is not sufficient by itself: this replay binds its
    claims back to the configured Reference index, immutable source bytes, and
    the complete Reference Evidence set stored in the parent Generation.
    """

    from shared.schemas.podcast_subtitles_v2 import ReferenceEvidence

    parsed = _verify_canonical_bytes(
        exact_bytes,
        ReferenceAuthorityProofV2,
        "Reference authority proof",
    )
    if not isinstance(retriever, LocalReferenceRetriever):
        raise NativeResolutionError(
            "native Reference authority replay requires LocalReferenceRetriever"
        )
    target = ReferenceAuthorityTargetV2(
        resolution_key=resolution_key,
        scopes=(reference_scope,),
    )
    if parsed.coverage.targets_hash != hash_object((target,)):
        raise NativeResolutionError("Reference proof targets differ from native resolution")
    try:
        evidence = tuple(
            _validate_exact_model(item, ReferenceEvidence, "stored Reference Evidence")
            for item in stored_reference_evidence
        )
    except NativeResolutionError:
        raise
    evidence_by_id = {item.id: item for item in evidence}
    if (
        len(evidence_by_id) != len(evidence)
        or tuple(sorted(evidence_by_id)) != parsed.coverage.examined_evidence_ids
    ):
        raise NativeResolutionError(
            "Reference proof Evidence coverage differs from stored Generation"
        )

    exact_retriever_evidence: list[ReferenceEvidence] = []
    for stored in evidence:
        exact = retriever.lookup_exact(
            (
                ReferenceExactLookupRequest(
                    source_id=stored.artifact.source_id,
                    extraction_block_index=stored.extraction_block_index,
                    excerpt_start=stored.excerpt_start,
                    excerpt_end=stored.excerpt_end,
                ),
            )
        )
        if len(exact) != 1:
            raise NativeResolutionError(
                "stored Reference locator did not reproduce exactly one Retriever Evidence"
            )
        reproduced = exact[0]
        expected_source_uri = (
            f"generation-artifact://reference_sources/{stored.artifact.digest.sha256}.bin"
        )
        expected_extraction_uri = (
            "generation-artifact://reference_extractions/"
            f"{stored.artifact.extracted_text.sha256}.json"
        )
        if (
            stored.artifact.digest.uri != expected_source_uri
            or stored.artifact.extracted_text.uri != expected_extraction_uri
        ):
            raise NativeResolutionError(
                "stored Reference Evidence has a non-canonical portable Artifact URI"
            )
        portable_reproduced = reproduced.model_copy(
            update={
                "artifact": reproduced.artifact.model_copy(
                    update={
                        "digest": reproduced.artifact.digest.model_copy(
                            update={"uri": expected_source_uri}
                        ),
                        "extracted_text": reproduced.artifact.extracted_text.model_copy(
                            update={"uri": expected_extraction_uri}
                        ),
                    }
                )
            }
        )
        if portable_reproduced != stored:
            raise NativeResolutionError(
                "stored Reference Evidence differs from exact Retriever lookup"
            )
        exact_retriever_evidence.append(reproduced)

    specs: list[ReferenceExactClaimSpecV2] = []
    for claim in parsed.claims:
        if claim.resolution_key != resolution_key or claim.scope != reference_scope:
            raise NativeResolutionError("Reference proof claim is outside native target scope")
        try:
            source = evidence_by_id[claim.evidence_id]
        except KeyError as exc:
            raise NativeResolutionError(
                "Reference proof claim names Evidence outside stored Generation"
            ) from exc
        claim_start: int | None = None
        claim_end: int | None = None
        if claim.origin == "exact_extracted_evidence":
            claim_start = source.excerpt.find(claim.claimed_text)
            if claim_start < 0:
                raise NativeResolutionError(
                    "Reference proof literal is absent from stored Evidence excerpt"
                )
            claim_end = claim_start + len(claim.claimed_text)
        specs.append(
            ReferenceExactClaimSpecV2(
                resolution_key=resolution_key,
                scope=reference_scope,
                claimed_text=claim.claimed_text,
                origin=claim.origin,
                evidence_id=claim.evidence_id,
                claim_start=claim_start,
                claim_end=claim_end,
            )
        )

    omission_scopes: dict[tuple[str, str], set[ReferenceClaimScope]] = {}
    for omission in parsed.coverage.omissions:
        if omission.kind != "evidence":
            continue
        if (
            omission.reason != "no_exact_claim_for_target"
            or omission.resolution_key != resolution_key
            or omission.scope != reference_scope
        ):
            raise NativeResolutionError(
                "Reference proof Evidence omission differs from native target scope"
            )
        evidence_id = omission.omitted_id.split("|", maxsplit=1)[0]
        if (
            evidence_id not in evidence_by_id
            or omission.omitted_id != f"{evidence_id}|{resolution_key}|{reference_scope}"
        ):
            raise NativeResolutionError("Reference proof Evidence omission ID is invalid")
        omission_scopes.setdefault((evidence_id, resolution_key), set()).add(reference_scope)
    omission_specs = tuple(
        ReferenceEvidenceOmissionSpecV2(
            evidence_id=evidence_id,
            resolution_key=key,
            scopes=tuple(sorted(scopes)),
            reason="no_exact_claim_for_target",
        )
        for (evidence_id, key), scopes in sorted(omission_scopes.items())
    )
    bounded_ids = (
        parsed.coverage.bounded_retrieval_evidence_ids
        if parsed.coverage.bounded_retrieval_assessed
        else None
    )
    try:
        verified = verify_reference_authority_proof(
            exact_bytes,
            retriever=retriever,
            targets=(target,),
            evidence=tuple(exact_retriever_evidence),
            claim_specs=tuple(specs),
            evidence_omissions=omission_specs,
            bounded_retrieval_evidence_ids=bounded_ids,
        )
    except ValueError as exc:
        raise NativeResolutionError(
            "Reference authority proof differs from stored immutable parents"
        ) from exc
    return verified


@dataclass(frozen=True, slots=True)
class ResolveNativeRequest:
    """Exact-byte request for native resolution; intentionally has no text input."""

    generation_id: str
    correction_acceptance_verdict: bytes | None = None
    original_confirmation_authorization: bytes | None = None
    correction_acceptance_policy: bytes | None = None
    original_confirmation_policy: bytes | None = None
    human_audio_receipts: tuple[bytes, ...] = ()
    human_reference_adjudication: bytes | None = None
    human_original_confirmation_receipts: tuple[bytes, ...] = ()
    reference_authority_proof: bytes | None = None

    def __post_init__(self) -> None:
        if not self.generation_id.strip() or not _GENERATION_ID.fullmatch(self.generation_id):
            raise ValueError("native resolve requires a valid Generation ID")
        branches = (
            self.correction_acceptance_verdict is not None,
            self.original_confirmation_authorization is not None,
        )
        if sum(branches) != 1:
            raise ValueError("native resolve requires exactly one authorization branch")
        for label, value in (
            ("correction_acceptance_verdict", self.correction_acceptance_verdict),
            ("original_confirmation_authorization", self.original_confirmation_authorization),
            ("correction_acceptance_policy", self.correction_acceptance_policy),
            ("original_confirmation_policy", self.original_confirmation_policy),
            ("human_reference_adjudication", self.human_reference_adjudication),
            ("reference_authority_proof", self.reference_authority_proof),
        ):
            if value is not None and type(value) is not bytes:
                raise TypeError(f"{label} requires exact bytes")
        for label, values in (
            ("human_audio_receipts", self.human_audio_receipts),
            ("human_original_confirmation_receipts", self.human_original_confirmation_receipts),
        ):
            if type(values) is not tuple or any(type(value) is not bytes for value in values):
                raise TypeError(f"{label} requires a tuple of exact bytes")
            if len({sha256_bytes(value) for value in values}) != len(values):
                raise ValueError(f"{label} contains duplicate exact artifacts")
        if self.correction_acceptance_verdict is not None:
            if self.correction_acceptance_policy is None:
                raise ValueError("acceptance verdict requires its exact policy bytes")
            if (
                self.original_confirmation_policy is not None
                or self.human_original_confirmation_receipts
            ):
                raise ValueError("acceptance request cannot mix original-confirmation parents")
        else:
            if self.original_confirmation_policy is None:
                raise ValueError("original confirmation requires its exact policy bytes")
            if (
                self.correction_acceptance_policy is not None
                or self.human_audio_receipts
                or self.human_reference_adjudication is not None
            ):
                raise ValueError("original confirmation cannot mix acceptance parents")


__all__ = [
    "HumanOriginalConfirmationReceiptV2",
    "NativeAuthorizationKindV2",
    "NativeCorrectionDecisionV2",
    "NativeResolveCheckpointStageV2",
    "NativeResolveCheckpointV2",
    "NativeResolutionActionV2",
    "NativeResolutionError",
    "OriginalConfirmationActionV2",
    "OriginalConfirmationAuthorizationV2",
    "OriginalConfirmationPolicyV2",
    "OriginalConfirmationReasonV2",
    "OriginalConfirmationReasoningCodeV2",
    "OriginalPronunciationRelationV2",
    "ResolveNativeRequest",
    "build_native_correction_decision",
    "build_native_resolve_checkpoint",
    "build_original_confirmation_authorization",
    "default_original_confirmation_policy",
    "human_original_confirmation_receipt_bytes",
    "native_correction_decision_bytes",
    "original_confirmation_authorization_bytes",
    "original_confirmation_policy_bytes",
    "verify_human_original_confirmation_receipt",
    "verify_native_correction_decision",
    "verify_native_reference_authority_proof",
    "verify_original_confirmation_authorization",
    "verify_original_confirmation_policy",
]
