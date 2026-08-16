"""Fail-closed authorization for one exact Subtitle V2 correction candidate.

Discovery artifacts only propose text.  This module can authorize the exact
candidate text when immutable Full Audit, Recognition Evidence, human audio
review, and scoped Reference authority all agree.  Its verdict is evidence for
a later stage; it has no authority to mutate a Canonical Transcript.

Human receipts are deliberately *not* created here.  They must arrive from an
authenticated human-review surface and can only be parsed and verified here.
"""

from __future__ import annotations

import re
import unicodedata
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

from .full_audit_attestation import FullAuditAggregateAttestationV2
from .hashing import canonical_json_bytes, hash_object
from .reference_claims import (
    ReferenceAuthorityProofV2,
    ReferenceClaimScope,
    ReferenceClaimV2,
    normalize_resolution_key,
)

Sha256 = Annotated[str, StringConstraints(pattern=r"^[0-9a-f]{64}$")]
CandidateDiscoveryV2: TypeAlias = TextDiscoveredCandidateV2 | AudioDiscoveredCandidateV2
CorrectionAcceptanceActionV2 = Literal["accept_exact_candidate", "defer", "reject"]
PronunciationOutcomeV2 = Literal["compatible", "incompatible", "indeterminate"]
HumanAttestationMethodV2 = Literal[
    "authenticated_ui_confirmation",
    "detached_signature",
    "supervised_in_person_confirmation",
]
AudioReviewReasoningCodeV2 = Literal[
    "audible_pronunciation_matches_exact_candidate",
    "audible_pronunciation_conflicts_exact_candidate",
    "audio_cannot_discriminate_spelling",
]
ReferenceAdjudicationReasoningCodeV2 = Literal[
    "authoritative_literal_selected_after_source_review",
    "conflicting_authority_requires_defer",
    "insufficient_scope_or_authority_requires_defer",
]
CorrectionAcceptanceReasonV2 = Literal[
    "all_required_evidence_authorizes_exact_candidate",
    "audio_receipt_missing",
    "audio_receipt_lineage_mismatch",
    "audio_indiscriminable",
    "audio_incompatible",
    "human_audio_quorum_not_met",
    "duplicate_human_audio_reviewer",
    "reference_proof_missing",
    "reference_scope_not_authorizable",
    "reference_coverage_incomplete",
    "reference_authoritative_literal_missing",
    "reference_literal_mismatch",
    "reference_counterclaim_unresolved",
    "reference_adjudication_missing",
    "reference_adjudication_deferred",
    "reference_adjudication_invalid",
    "reference_reviewer_not_independent",
]

_AUTHORITY_LIMIT = "authorization_not_mutation"
_UTC_TIMESTAMP = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$")
_AUTHORISABLE_SCOPES: tuple[ReferenceClaimScope, ...] = (
    "literal_terminology",
    "owner_approved_glossary_spelling",
    "source_author",
    "source_title",
)
_REASON_ORDER: tuple[CorrectionAcceptanceReasonV2, ...] = (
    "audio_receipt_missing",
    "audio_receipt_lineage_mismatch",
    "audio_indiscriminable",
    "audio_incompatible",
    "human_audio_quorum_not_met",
    "duplicate_human_audio_reviewer",
    "reference_proof_missing",
    "reference_scope_not_authorizable",
    "reference_coverage_incomplete",
    "reference_authoritative_literal_missing",
    "reference_literal_mismatch",
    "reference_counterclaim_unresolved",
    "reference_adjudication_missing",
    "reference_adjudication_deferred",
    "reference_adjudication_invalid",
    "reference_reviewer_not_independent",
    "all_required_evidence_authorizes_exact_candidate",
)
_ModelT = TypeVar("_ModelT", bound=BaseModel)


class CorrectionAcceptanceError(ValueError):
    """Frozen acceptance parents are malformed, stale, or non-canonical."""


class _StrictContract(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)


def _bounded(value: str, label: str, *, maximum: int = 4_096) -> str:
    if not value.strip():
        raise ValueError(f"{label} must not be blank")
    if len(value) > maximum:
        raise ValueError(f"{label} exceeds {maximum} Unicode scalars")
    if any(unicodedata.category(character) in {"Cc", "Cs"} for character in value):
        raise ValueError(f"{label} contains a forbidden control character")
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


def _validate_exact_model(value: object, model: type[_ModelT], label: str) -> _ModelT:
    if not isinstance(value, model):
        raise CorrectionAcceptanceError(f"{label} has the wrong artifact type")
    exact = canonical_json_bytes(value)
    try:
        replayed = model.model_validate_json(exact, strict=True)
    except (ValidationError, ValueError) as exc:
        raise CorrectionAcceptanceError(f"{label} is not an exact valid artifact") from exc
    if replayed != value:
        raise CorrectionAcceptanceError(f"{label} is not an exact valid artifact")
    return replayed


def _validate_utc_timestamp(value: str) -> str:
    if not _UTC_TIMESTAMP.fullmatch(value):
        raise ValueError("reviewed_at_utc must be second-precision RFC 3339 UTC")
    try:
        datetime.strptime(value, "%Y-%m-%dT%H:%M:%SZ")
    except ValueError as exc:
        raise ValueError("reviewed_at_utc is not a real UTC timestamp") from exc
    return value


class HumanReviewerAttestationV2(_StrictContract):
    """Authenticated human identity; model/provider actors cannot be represented."""

    schema_version: Literal[2] = 2
    actor_kind: Literal["human"] = "human"
    reviewer_id: str
    identity_provider: str
    identity_record_sha256: Sha256
    attestation_method: HumanAttestationMethodV2
    attestation_record_sha256: Sha256

    @field_validator("reviewer_id", "identity_provider")
    @classmethod
    def _identity_text_is_safe(cls, value: str, info: object) -> str:
        return _bounded(value, str(getattr(info, "field_name", "identity")), maximum=256)


class HumanAudioReviewReceiptV2(_StrictContract):
    """A human's judgment about pronunciation in one exact immutable clip."""

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
    recognized_text: str
    candidate_text: str
    pronunciation_outcome: PronunciationOutcomeV2
    discriminable: bool
    reasoning_code: AudioReviewReasoningCodeV2
    notes_digest: Sha256
    reviewer: HumanReviewerAttestationV2
    reviewed_at_utc: str
    authority: Literal["human_pronunciation_review_not_spelling_authority"] = (
        "human_pronunciation_review_not_spelling_authority"
    )
    content_hash: Sha256

    @field_validator("recognized_text", "candidate_text")
    @classmethod
    def _reviewed_text_is_safe(cls, value: str, info: object) -> str:
        return _bounded(value, str(getattr(info, "field_name", "reviewed text")), maximum=16_384)

    @field_validator("affected_token_ids", "cited_span_ids", "cited_recognition_evidence_ids")
    @classmethod
    def _ids_are_nonblank_and_unique(
        cls, values: tuple[str, ...], info: object
    ) -> tuple[str, ...]:
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
    def _receipt_is_exact(self) -> HumanAudioReviewReceiptV2:
        if self.clip_end_ms <= self.clip_start_ms:
            raise ValueError("human audio review clip must have a positive interval")
        if not self.affected_token_ids or not self.cited_recognition_evidence_ids:
            raise ValueError("human audio review requires exact token and Recognition Evidence IDs")
        expected_shape = {
            "compatible": (True, "audible_pronunciation_matches_exact_candidate"),
            "incompatible": (True, "audible_pronunciation_conflicts_exact_candidate"),
            "indeterminate": (False, "audio_cannot_discriminate_spelling"),
        }[self.pronunciation_outcome]
        if (self.discriminable, self.reasoning_code) != expected_shape:
            raise ValueError("human audio outcome contradicts discriminability/reasoning")
        _require_identity(self, self.id, self.content_hash, "Human audio review receipt")
        return self


class HumanReferenceAdjudicationReceiptV2(_StrictContract):
    """Human resolution of named Reference conflicts, never of what was spoken."""

    schema_version: Literal[2] = 2
    id: Sha256
    candidate_discovery_id: Sha256
    candidate_discovery_hash: Sha256
    reference_proof_id: Sha256
    reference_proof_hash: Sha256
    coverage_receipt_id: Sha256
    coverage_receipt_hash: Sha256
    conflict_set_id: Sha256
    conflict_set_hash: Sha256
    resolution_key: str
    scope: ReferenceClaimScope
    candidate_text: str
    conflict_ids: tuple[Sha256, ...]
    decision: Literal["choose_authoritative_literal", "defer"]
    chosen_claim_id: Sha256 | None = None
    chosen_literal: str | None = None
    reasoning_code: ReferenceAdjudicationReasoningCodeV2
    reviewer: HumanReviewerAttestationV2
    reviewed_at_utc: str
    authority: Literal["reference_conflict_adjudication_not_audio_or_text_mutation"] = (
        "reference_conflict_adjudication_not_audio_or_text_mutation"
    )
    content_hash: Sha256

    @field_validator("resolution_key")
    @classmethod
    def _resolution_key_is_canonical(cls, value: str) -> str:
        _bounded(value, "resolution_key", maximum=512)
        if value != normalize_resolution_key(value):
            raise ValueError("resolution_key must already be deterministically normalized")
        return value

    @field_validator("candidate_text")
    @classmethod
    def _candidate_is_safe(cls, value: str) -> str:
        return _bounded(value, "candidate_text", maximum=16_384)

    @field_validator("chosen_literal")
    @classmethod
    def _chosen_literal_is_safe(cls, value: str | None) -> str | None:
        return None if value is None else _bounded(value, "chosen_literal", maximum=16_384)

    @field_validator("reviewed_at_utc")
    @classmethod
    def _timestamp_is_exact(cls, value: str) -> str:
        return _validate_utc_timestamp(value)

    @model_validator(mode="after")
    def _receipt_is_exact(self) -> HumanReferenceAdjudicationReceiptV2:
        if (
            not self.conflict_ids
            or len(set(self.conflict_ids)) != len(self.conflict_ids)
            or self.conflict_ids != tuple(sorted(self.conflict_ids))
        ):
            raise ValueError("Reference adjudication requires unique ordered conflict IDs")
        if self.decision == "choose_authoritative_literal":
            if self.chosen_claim_id is None or self.chosen_literal is None:
                raise ValueError("Reference choice requires an exact claim ID and literal")
            if self.reasoning_code != "authoritative_literal_selected_after_source_review":
                raise ValueError(
                    "Reference choice requires the authoritative-literal reasoning code"
                )
        elif self.chosen_claim_id is not None or self.chosen_literal is not None:
            raise ValueError("deferred Reference adjudication cannot choose a claim or literal")
        elif self.reasoning_code == "authoritative_literal_selected_after_source_review":
            raise ValueError("deferred Reference adjudication cannot claim a selected authority")
        _require_identity(self, self.id, self.content_hash, "Human Reference adjudication")
        return self


class CorrectionAcceptancePolicyV2(_StrictContract):
    """Non-weakenable authority boundaries plus an explicit human quorum."""

    schema_version: Literal[2] = 2
    id: Sha256
    minimum_human_audio_reviewers: int = Field(ge=1, le=3)
    require_human_audio_for_every_discovery: Literal[True] = True
    require_exact_candidate_literal: Literal[True] = True
    require_complete_reference_coverage: Literal[True] = True
    require_authoritative_active_reference: Literal[True] = True
    require_conflict_adjudicator_independence: Literal[True] = True
    authorisable_reference_scopes: tuple[ReferenceClaimScope, ...] = _AUTHORISABLE_SCOPES
    authority: Literal["policy_not_correction_decision"] = "policy_not_correction_decision"
    content_hash: Sha256

    @model_validator(mode="after")
    def _policy_is_exact(self) -> CorrectionAcceptancePolicyV2:
        if self.authorisable_reference_scopes != _AUTHORISABLE_SCOPES:
            raise ValueError("Correction acceptance cannot broaden Reference authority scopes")
        _require_identity(self, self.id, self.content_hash, "Correction acceptance policy")
        return self


class CorrectionAcceptanceVerdictV2(_StrictContract):
    """Authorization receipt for an exact candidate; never a transcript mutation."""

    schema_version: Literal[2] = 2
    id: Sha256
    action: CorrectionAcceptanceActionV2
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
    audio_window_start_ms: int | None = Field(default=None, ge=0)
    audio_window_end_ms: int | None = Field(default=None, gt=0)
    original_text: str
    candidate_text: str
    reference_proof_id: Sha256 | None
    reference_proof_hash: Sha256 | None
    reference_coverage_receipt_id: Sha256 | None
    reference_coverage_receipt_hash: Sha256 | None
    reference_conflict_set_id: Sha256 | None
    reference_conflict_set_hash: Sha256 | None
    selected_reference_claim_id: Sha256 | None
    selected_reference_claim_hash: Sha256 | None
    selected_reference_literal: str | None
    human_audio_review_receipt_ids: tuple[Sha256, ...]
    human_audio_review_receipt_hashes: tuple[Sha256, ...]
    human_reference_adjudication_receipt_id: Sha256 | None
    human_reference_adjudication_receipt_hash: Sha256 | None
    policy_id: Sha256
    policy_hash: Sha256
    reasons: tuple[CorrectionAcceptanceReasonV2, ...]
    authority: Literal["authorization_not_mutation"] = _AUTHORITY_LIMIT
    content_hash: Sha256

    @model_validator(mode="after")
    def _verdict_is_exact(self) -> CorrectionAcceptanceVerdictV2:
        if self.original_text == self.candidate_text:
            raise ValueError("Correction acceptance cannot represent a no-op")
        if (self.audio_window_start_ms is None) != (self.audio_window_end_ms is None):
            raise ValueError("audio review window must be wholly present or absent")
        if (
            self.audio_window_start_ms is not None
            and self.audio_window_end_ms is not None
            and self.audio_window_end_ms <= self.audio_window_start_ms
        ):
            raise ValueError("audio review window must be positive")
        if self.human_audio_review_receipt_ids != tuple(
            sorted(self.human_audio_review_receipt_ids)
        ) or self.human_audio_review_receipt_hashes != tuple(
            sorted(self.human_audio_review_receipt_hashes)
        ):
            raise ValueError("human audio receipt parents must use canonical order")
        if len(self.human_audio_review_receipt_ids) != len(
            self.human_audio_review_receipt_hashes
        ):
            raise ValueError("human audio receipt IDs and hashes differ")
        expected_reason_order = tuple(
            reason for reason in _REASON_ORDER if reason in set(self.reasons)
        )
        if not self.reasons or len(set(self.reasons)) != len(self.reasons):
            raise ValueError("Correction acceptance requires unique typed reasons")
        if self.reasons != expected_reason_order:
            raise ValueError("Correction acceptance reasons are not canonical")
        selected = (
            self.selected_reference_claim_id,
            self.selected_reference_claim_hash,
            self.selected_reference_literal,
        )
        if self.action == "accept_exact_candidate":
            if self.reasons != ("all_required_evidence_authorizes_exact_candidate",):
                raise ValueError("accepted correction must have only the success reason")
            if any(value is None for value in selected) or self.audio_window_start_ms is None:
                raise ValueError("accepted correction lacks exact audio/Reference authorization")
            if self.selected_reference_literal != self.candidate_text:
                raise ValueError("accepted Reference literal differs from exact candidate")
        elif any(value is not None for value in selected):
            raise ValueError("non-accepted verdict cannot expose a selected authority claim")
        if self.action == "reject" and "audio_incompatible" not in self.reasons:
            raise ValueError("reject is reserved for human-confirmed audio incompatibility")
        _require_identity(self, self.id, self.content_hash, "Correction acceptance verdict")
        return self


def default_correction_acceptance_policy() -> CorrectionAcceptancePolicyV2:
    payload: dict[str, object] = {
        "schema_version": 2,
        "minimum_human_audio_reviewers": 1,
        "require_human_audio_for_every_discovery": True,
        "require_exact_candidate_literal": True,
        "require_complete_reference_coverage": True,
        "require_authoritative_active_reference": True,
        "require_conflict_adjudicator_independence": True,
        "authorisable_reference_scopes": _AUTHORISABLE_SCOPES,
        "authority": "policy_not_correction_decision",
    }
    return _seal(CorrectionAcceptancePolicyV2, payload)


def _recognition_registry(
    evidence: tuple[RecognitionEvidence, ...],
) -> tuple[tuple[Sha256, ...], dict[str, object]]:
    hashes: list[str] = []
    registry: dict[str, object] = {}
    for index, item in enumerate(evidence):
        exact = _validate_exact_model(item, RecognitionEvidence, f"Recognition Evidence {index}")
        source_hash = recognition_evidence_content_hash(exact)
        if source_hash in hashes:
            raise CorrectionAcceptanceError("Recognition Evidence contains duplicate content")
        hashes.append(source_hash)
        for token in exact.tokens:
            registry[f"{source_hash}:{token.id}"] = token
    return tuple(sorted(hashes)), registry


def _audio_receipt_binding_is_exact(
    receipt: HumanAudioReviewReceiptV2,
    *,
    candidate: CandidateDiscoveryV2,
    aggregate: FullAuditAggregateAttestationV2,
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
        or receipt.cited_recognition_evidence_ids
        != candidate.cited_recognition_evidence_ids
        or receipt.recognized_text != candidate.observed_text
        or receipt.candidate_text != candidate.candidate_text
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


def _reference_receipt_binding_is_exact(
    receipt: HumanReferenceAdjudicationReceiptV2,
    *,
    candidate: CandidateDiscoveryV2,
    proof: ReferenceAuthorityProofV2,
    resolution_key: str,
    reference_scope: ReferenceClaimScope,
    relevant_conflict_ids: tuple[str, ...],
    exact_authoritative_claims: tuple[ReferenceClaimV2, ...],
) -> bool:
    claim_by_id = {item.id: item for item in exact_authoritative_claims}
    if (
        receipt.candidate_discovery_id != candidate.id
        or receipt.candidate_discovery_hash != candidate.content_hash
        or receipt.reference_proof_id != proof.id
        or receipt.reference_proof_hash != proof.content_hash
        or receipt.coverage_receipt_id != proof.coverage.id
        or receipt.coverage_receipt_hash != proof.coverage.content_hash
        or receipt.conflict_set_id != proof.conflicts.id
        or receipt.conflict_set_hash != proof.conflicts.content_hash
        or receipt.resolution_key != resolution_key
        or receipt.scope != reference_scope
        or receipt.candidate_text != candidate.candidate_text
        or receipt.conflict_ids != relevant_conflict_ids
    ):
        return False
    if receipt.decision == "defer":
        return True
    if receipt.chosen_claim_id is None:
        return False
    chosen = claim_by_id.get(receipt.chosen_claim_id)
    if chosen is None or receipt.chosen_literal != chosen.claimed_text:
        return False
    conflict_by_id = {item.id: item for item in proof.conflicts.conflicts}
    return all(
        receipt.chosen_claim_id in conflict_by_id[item].claim_ids
        for item in receipt.conflict_ids
    )


def _canonical_reasons(
    reasons: set[CorrectionAcceptanceReasonV2],
) -> tuple[CorrectionAcceptanceReasonV2, ...]:
    return tuple(reason for reason in _REASON_ORDER if reason in reasons)


def build_correction_acceptance_verdict(
    *,
    candidate: CandidateDiscoveryV2,
    full_audit_aggregate: FullAuditAggregateAttestationV2,
    recognition_evidence: tuple[RecognitionEvidence, ...],
    resolution_key: str,
    reference_scope: ReferenceClaimScope,
    reference_proof: ReferenceAuthorityProofV2 | None,
    human_audio_receipts: tuple[HumanAudioReviewReceiptV2, ...],
    human_reference_adjudication: HumanReferenceAdjudicationReceiptV2 | None,
    policy: CorrectionAcceptancePolicyV2,
) -> CorrectionAcceptanceVerdictV2:
    """Build one deterministic decision from exact immutable parents.

    ``candidate.candidate_text`` is the only replacement text accepted by this
    API.  There is intentionally no free-text/manual replacement parameter.
    """

    if not isinstance(candidate, (TextDiscoveredCandidateV2, AudioDiscoveredCandidateV2)):
        raise CorrectionAcceptanceError("candidate must be a typed v2 discovery artifact")
    candidate = _validate_exact_model(candidate, type(candidate), "candidate discovery")
    aggregate = _validate_exact_model(
        full_audit_aggregate,
        FullAuditAggregateAttestationV2,
        "Full Audit aggregate",
    )
    policy = _validate_exact_model(policy, CorrectionAcceptancePolicyV2, "acceptance policy")
    _bounded(resolution_key, "resolution_key", maximum=512)
    if resolution_key != normalize_resolution_key(resolution_key):
        raise CorrectionAcceptanceError("resolution_key is not canonical")
    modality: Literal["text", "audio"] = (
        "text" if isinstance(candidate, TextDiscoveredCandidateV2) else "audio"
    )
    discovery_ids = (
        aggregate.text_discovery_ids if modality == "text" else aggregate.audio_discovery_ids
    )
    if candidate.id not in discovery_ids or candidate.cell_id not in aggregate.all_cell_ids:
        raise CorrectionAcceptanceError("candidate is not a member of the Full Audit aggregate")

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
    ):
        raise CorrectionAcceptanceError("Recognition Evidence differs from Full Audit lineage")
    if not set(candidate.cited_recognition_evidence_ids) <= set(recognition_registry):
        raise CorrectionAcceptanceError("candidate cites unknown Recognition Evidence tokens")

    receipts = tuple(
        sorted(
            (
                _validate_exact_model(item, HumanAudioReviewReceiptV2, "human audio receipt")
                for item in human_audio_receipts
            ),
            key=lambda item: item.id,
        )
    )
    if len({item.id for item in receipts}) != len(receipts):
        raise CorrectionAcceptanceError("human audio receipt artifact is duplicated")
    reasons: set[CorrectionAcceptanceReasonV2] = set()
    exact_audio_receipts = tuple(
        item
        for item in receipts
        if _audio_receipt_binding_is_exact(
            item,
            candidate=candidate,
            aggregate=aggregate,
            recognition_registry=recognition_registry,
        )
    )
    if not receipts:
        reasons.add("audio_receipt_missing")
    elif len(exact_audio_receipts) != len(receipts):
        reasons.add("audio_receipt_lineage_mismatch")
    else:
        reviewer_ids = tuple(item.reviewer.reviewer_id for item in exact_audio_receipts)
        if len(set(reviewer_ids)) != len(reviewer_ids):
            reasons.add("duplicate_human_audio_reviewer")
        if len(set(reviewer_ids)) < policy.minimum_human_audio_reviewers:
            reasons.add("human_audio_quorum_not_met")
        if any(item.pronunciation_outcome == "incompatible" for item in exact_audio_receipts):
            reasons.add("audio_incompatible")
        elif any(item.pronunciation_outcome == "indeterminate" for item in exact_audio_receipts):
            reasons.add("audio_indiscriminable")
        elif len(
            {
                (
                    item.clip_lineage_kind,
                    item.clip_lineage_parent_id,
                    item.clip_extraction_policy_hash,
                    item.clip_sha256,
                    item.clip_start_ms,
                    item.clip_end_ms,
                )
                for item in exact_audio_receipts
            }
        ) != 1:
            reasons.add("audio_receipt_lineage_mismatch")

    proof: ReferenceAuthorityProofV2 | None = None
    selected_claim: ReferenceClaimV2 | None = None
    reference_receipt: HumanReferenceAdjudicationReceiptV2 | None = None
    if reference_proof is None:
        reasons.add("reference_proof_missing")
    else:
        proof = _validate_exact_model(
            reference_proof,
            ReferenceAuthorityProofV2,
            "Reference authority proof",
        )
        if reference_scope not in policy.authorisable_reference_scopes:
            reasons.add("reference_scope_not_authorizable")
        if (
            not proof.coverage.selection_complete
            or proof.conflicts.unresolved_conditions
        ):
            reasons.add("reference_coverage_incomplete")
        scoped_claims = tuple(
            item
            for item in proof.claims
            if item.resolution_key == resolution_key and item.scope == reference_scope
        )
        exact_authoritative = tuple(
            item
            for item in scoped_claims
            if item.strength == "authoritative"
            and item.version_status == "active"
            and item.claimed_text == candidate.candidate_text
        )
        if not exact_authoritative:
            if any(
                item.strength == "authoritative" and item.version_status == "active"
                for item in scoped_claims
            ):
                reasons.add("reference_literal_mismatch")
            else:
                reasons.add("reference_authoritative_literal_missing")
        relevant_conflicts = tuple(
            item
            for item in proof.conflicts.conflicts
            if item.resolution_key == resolution_key and item.overlapping_scope == reference_scope
        )
        relevant_conflict_ids = tuple(item.id for item in relevant_conflicts)
        exact_literal_variants = tuple(
            item
            for item in scoped_claims
            if item.strength == "authoritative"
            and item.version_status == "active"
            and item.normalized_claimed_text
            in {claim.normalized_claimed_text for claim in exact_authoritative}
            and item.claimed_text != candidate.candidate_text
        )
        if exact_literal_variants and not relevant_conflicts:
            reasons.add("reference_counterclaim_unresolved")
        if relevant_conflicts:
            if human_reference_adjudication is None:
                reasons.add("reference_adjudication_missing")
            else:
                reference_receipt = _validate_exact_model(
                    human_reference_adjudication,
                    HumanReferenceAdjudicationReceiptV2,
                    "human Reference adjudication",
                )
                if not _reference_receipt_binding_is_exact(
                    reference_receipt,
                    candidate=candidate,
                    proof=proof,
                    resolution_key=resolution_key,
                    reference_scope=reference_scope,
                    relevant_conflict_ids=relevant_conflict_ids,
                    exact_authoritative_claims=exact_authoritative,
                ):
                    reasons.add("reference_adjudication_invalid")
                elif reference_receipt.decision == "defer":
                    reasons.add("reference_adjudication_deferred")
                elif reference_receipt.reviewer.reviewer_id in {
                    item.reviewer.reviewer_id for item in exact_audio_receipts
                }:
                    reasons.add("reference_reviewer_not_independent")
                else:
                    selected_claim = next(
                        item
                        for item in exact_authoritative
                        if item.id == reference_receipt.chosen_claim_id
                    )
        elif human_reference_adjudication is not None:
            reference_receipt = _validate_exact_model(
                human_reference_adjudication,
                HumanReferenceAdjudicationReceiptV2,
                "human Reference adjudication",
            )
            reasons.add("reference_adjudication_invalid")
        elif exact_authoritative:
            selected_claim = exact_authoritative[0]

    if reasons:
        selected_claim = None
        action: CorrectionAcceptanceActionV2 = (
            "reject" if "audio_incompatible" in reasons else "defer"
        )
        final_reasons = _canonical_reasons(reasons)
    else:
        if selected_claim is None:
            raise CorrectionAcceptanceError(
                "accepted candidate lacks a deterministic Reference claim"
            )
        action = "accept_exact_candidate"
        final_reasons = ("all_required_evidence_authorizes_exact_candidate",)

    compatible_receipts = tuple(
        item
        for item in exact_audio_receipts
        if item.pronunciation_outcome == "compatible"
    )
    audio_window = (
        (compatible_receipts[0].clip_start_ms, compatible_receipts[0].clip_end_ms)
        if compatible_receipts
        else (None, None)
    )
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
        "audio_window_start_ms": audio_window[0],
        "audio_window_end_ms": audio_window[1],
        "original_text": candidate.observed_text,
        "candidate_text": candidate.candidate_text,
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
        "selected_reference_claim_id": (
            selected_claim.id if selected_claim is not None else None
        ),
        "selected_reference_claim_hash": (
            selected_claim.content_hash if selected_claim is not None else None
        ),
        "selected_reference_literal": (
            selected_claim.claimed_text if selected_claim is not None else None
        ),
        "human_audio_review_receipt_ids": tuple(item.id for item in receipts),
        "human_audio_review_receipt_hashes": tuple(
            sorted(item.content_hash for item in receipts)
        ),
        "human_reference_adjudication_receipt_id": (
            reference_receipt.id if reference_receipt is not None else None
        ),
        "human_reference_adjudication_receipt_hash": (
            reference_receipt.content_hash if reference_receipt is not None else None
        ),
        "policy_id": policy.id,
        "policy_hash": policy.content_hash,
        "reasons": final_reasons,
        "authority": _AUTHORITY_LIMIT,
    }
    return _seal(CorrectionAcceptanceVerdictV2, payload)


def human_audio_review_receipt_bytes(receipt: HumanAudioReviewReceiptV2) -> bytes:
    return canonical_json_bytes(receipt)


def human_reference_adjudication_receipt_bytes(
    receipt: HumanReferenceAdjudicationReceiptV2,
) -> bytes:
    return canonical_json_bytes(receipt)


def correction_acceptance_policy_bytes(policy: CorrectionAcceptancePolicyV2) -> bytes:
    return canonical_json_bytes(policy)


def correction_acceptance_verdict_bytes(verdict: CorrectionAcceptanceVerdictV2) -> bytes:
    return canonical_json_bytes(verdict)


def _verify_canonical_bytes(exact_bytes: bytes, model: type[_ModelT], label: str) -> _ModelT:
    if type(exact_bytes) is not bytes:
        raise CorrectionAcceptanceError(f"{label} requires exact bytes")
    try:
        parsed = model.model_validate_json(exact_bytes, strict=True)
    except (ValidationError, ValueError) as exc:
        raise CorrectionAcceptanceError(f"{label} bytes violate strict v2 schema") from exc
    if canonical_json_bytes(parsed) != exact_bytes:
        raise CorrectionAcceptanceError(f"{label} bytes are not canonical")
    return parsed


def verify_human_audio_review_receipt(exact_bytes: bytes) -> HumanAudioReviewReceiptV2:
    return _verify_canonical_bytes(
        exact_bytes,
        HumanAudioReviewReceiptV2,
        "Human audio review receipt",
    )


def verify_human_reference_adjudication_receipt(
    exact_bytes: bytes,
) -> HumanReferenceAdjudicationReceiptV2:
    return _verify_canonical_bytes(
        exact_bytes,
        HumanReferenceAdjudicationReceiptV2,
        "Human Reference adjudication receipt",
    )


def verify_correction_acceptance_policy(exact_bytes: bytes) -> CorrectionAcceptancePolicyV2:
    return _verify_canonical_bytes(
        exact_bytes,
        CorrectionAcceptancePolicyV2,
        "Correction acceptance policy",
    )


def verify_correction_acceptance_verdict(
    exact_bytes: bytes,
    *,
    candidate: CandidateDiscoveryV2,
    full_audit_aggregate: FullAuditAggregateAttestationV2,
    recognition_evidence: tuple[RecognitionEvidence, ...],
    resolution_key: str,
    reference_scope: ReferenceClaimScope,
    reference_proof: ReferenceAuthorityProofV2 | None,
    human_audio_receipts: tuple[HumanAudioReviewReceiptV2, ...],
    human_reference_adjudication: HumanReferenceAdjudicationReceiptV2 | None,
    policy: CorrectionAcceptancePolicyV2,
) -> CorrectionAcceptanceVerdictV2:
    parsed = _verify_canonical_bytes(
        exact_bytes,
        CorrectionAcceptanceVerdictV2,
        "Correction acceptance verdict",
    )
    rebuilt = build_correction_acceptance_verdict(
        candidate=candidate,
        full_audit_aggregate=full_audit_aggregate,
        recognition_evidence=recognition_evidence,
        resolution_key=resolution_key,
        reference_scope=reference_scope,
        reference_proof=reference_proof,
        human_audio_receipts=human_audio_receipts,
        human_reference_adjudication=human_reference_adjudication,
        policy=policy,
    )
    if parsed != rebuilt or exact_bytes != canonical_json_bytes(rebuilt):
        raise CorrectionAcceptanceError("Correction acceptance verdict differs from frozen parents")
    return rebuilt


__all__ = [
    "AudioReviewReasoningCodeV2",
    "CandidateDiscoveryV2",
    "CorrectionAcceptanceActionV2",
    "CorrectionAcceptanceError",
    "CorrectionAcceptancePolicyV2",
    "CorrectionAcceptanceReasonV2",
    "CorrectionAcceptanceVerdictV2",
    "HumanAudioReviewReceiptV2",
    "HumanReferenceAdjudicationReceiptV2",
    "HumanReviewerAttestationV2",
    "PronunciationOutcomeV2",
    "ReferenceAdjudicationReasoningCodeV2",
    "build_correction_acceptance_verdict",
    "correction_acceptance_policy_bytes",
    "correction_acceptance_verdict_bytes",
    "default_correction_acceptance_policy",
    "human_audio_review_receipt_bytes",
    "human_reference_adjudication_receipt_bytes",
    "verify_correction_acceptance_policy",
    "verify_correction_acceptance_verdict",
    "verify_human_audio_review_receipt",
    "verify_human_reference_adjudication_receipt",
]
