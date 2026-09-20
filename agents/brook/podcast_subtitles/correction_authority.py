"""Closed authority modes for exact Podcast Subtitle V2 correction candidates.

This is a pure domain slice.  It decides whether already-discovered candidate
text is *authorized*; it never writes a Correction Decision or mutates a
Canonical Transcript.  Audio remains primary evidence for what was spoken,
while Reference Evidence can only authorize an exact spelling/literal within
its declared scope.

The v2 human audio receipt records whether a candidate is pronunciation-
compatible, but it cannot prove that the original and candidate are audibly
different.  ``HumanPronunciationRelationReceiptV3`` closes that gap by binding
one authenticated human judgment to the exact pair and clip.  This module does
not create human receipts; it only parses and verifies receipts produced by an
authenticated review surface.
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

from .correction_acceptance import (
    HumanReferenceAdjudicationReceiptV2,
    HumanReviewerAttestationV2,
)
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
CorrectionAuthorityModeV3 = Literal[
    "audio_discriminable_lexical",
    "orthographic_homophone_or_entity",
    "reference_only",
]
CorrectionAuthorityActionV3 = Literal["authorize_exact_candidate", "defer", "reject"]
ReferenceLookupStatusV3 = Literal["not_enrolled", "completed", "failed", "incomplete"]
PronunciationCompatibilityV3 = Literal["compatible", "incompatible", "indeterminate"]
PronunciationPairDiscriminabilityV3 = Literal[
    "audibly_distinct",
    "homophonous_or_indiscriminable",
    "indeterminate",
]
PronunciationRelationReasonV3 = Literal[
    "candidate_matches_original_does_not",
    "original_matches_candidate_does_not",
    "candidate_and_original_both_match_same_pronunciation",
    "audio_insufficient_to_compare_pair",
]
CorrectionAuthorityReasonV3 = Literal[
    "audio_discriminable_exact_candidate_authorized",
    "orthographic_exact_reference_literal_authorized",
    "reference_only_never_authorizes_text_mutation",
    "human_audio_receipt_missing",
    "human_audio_receipt_lineage_mismatch",
    "human_audio_quorum_not_met",
    "duplicate_human_audio_reviewer",
    "human_audio_reviewers_disagree",
    "audio_candidate_incompatible",
    "audio_pair_indeterminate",
    "audio_pair_not_discriminable",
    "reference_lookup_not_enrolled",
    "reference_lookup_failed",
    "reference_lookup_incomplete",
    "reference_proof_missing_or_mismatched",
    "reference_coverage_incomplete",
    "reference_authoritative_literal_missing",
    "reference_literal_mismatch",
    "reference_counterclaim_blocks_audio_lexical",
    "reference_conflict_blocks_audio_lexical",
    "reference_adjudication_missing",
    "reference_adjudication_deferred",
    "reference_adjudication_invalid",
    "reference_reviewer_not_independent",
]

_AUTHORITY_MODES: tuple[CorrectionAuthorityModeV3, ...] = (
    "audio_discriminable_lexical",
    "orthographic_homophone_or_entity",
    "reference_only",
)
_AUTHORISABLE_SCOPES: tuple[ReferenceClaimScope, ...] = (
    "literal_terminology",
    "owner_approved_glossary_spelling",
    "source_author",
    "source_title",
)
_REASON_ORDER: tuple[CorrectionAuthorityReasonV3, ...] = (
    "human_audio_receipt_missing",
    "human_audio_receipt_lineage_mismatch",
    "human_audio_quorum_not_met",
    "duplicate_human_audio_reviewer",
    "human_audio_reviewers_disagree",
    "audio_candidate_incompatible",
    "audio_pair_indeterminate",
    "audio_pair_not_discriminable",
    "reference_lookup_not_enrolled",
    "reference_lookup_failed",
    "reference_lookup_incomplete",
    "reference_proof_missing_or_mismatched",
    "reference_coverage_incomplete",
    "reference_authoritative_literal_missing",
    "reference_literal_mismatch",
    "reference_counterclaim_blocks_audio_lexical",
    "reference_conflict_blocks_audio_lexical",
    "reference_adjudication_missing",
    "reference_adjudication_deferred",
    "reference_adjudication_invalid",
    "reference_reviewer_not_independent",
    "reference_only_never_authorizes_text_mutation",
    "audio_discriminable_exact_candidate_authorized",
    "orthographic_exact_reference_literal_authorized",
)
_AUTHORITY_LIMIT = "authorization_not_mutation"
_UTC_TIMESTAMP = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$")
_ModelT = TypeVar("_ModelT", bound=BaseModel)


class CorrectionAuthorityError(ValueError):
    """An authority parent is malformed, stale, reordered, or non-canonical."""


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
        raise CorrectionAuthorityError(f"{label} has the wrong artifact type")
    exact = canonical_json_bytes(value)
    try:
        replayed = model.model_validate_json(exact, strict=True)
    except (ValidationError, ValueError) as exc:
        raise CorrectionAuthorityError(f"{label} is not an exact valid artifact") from exc
    if replayed != value:
        raise CorrectionAuthorityError(f"{label} is not an exact valid artifact")
    return replayed


def _validate_utc_timestamp(value: str) -> str:
    if not _UTC_TIMESTAMP.fullmatch(value):
        raise ValueError("reviewed_at_utc must be second-precision RFC 3339 UTC")
    try:
        datetime.strptime(value, "%Y-%m-%dT%H:%M:%SZ")
    except ValueError as exc:
        raise ValueError("reviewed_at_utc is not a real UTC timestamp") from exc
    return value


class ArtifactBindingV3(_StrictContract):
    """One exact ID-to-content-hash pair; parallel unpaired arrays are forbidden."""

    schema_version: Literal[3] = 3
    id: Sha256
    content_hash: Sha256


class HumanPronunciationRelationReceiptV3(_StrictContract):
    """Authenticated judgment of both candidate and original against one clip."""

    schema_version: Literal[3] = 3
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
    candidate_text: str
    original_pronunciation: PronunciationCompatibilityV3
    candidate_pronunciation: PronunciationCompatibilityV3
    pair_discriminability: PronunciationPairDiscriminabilityV3
    reasoning_code: PronunciationRelationReasonV3
    notes_digest: Sha256
    reviewer: HumanReviewerAttestationV2
    reviewed_at_utc: str
    authority: Literal["human_pronunciation_relation_not_spelling_authority"] = (
        "human_pronunciation_relation_not_spelling_authority"
    )
    content_hash: Sha256

    @field_validator("original_text", "candidate_text")
    @classmethod
    def _text_is_safe(cls, value: str, info: object) -> str:
        return _bounded(value, str(getattr(info, "field_name", "reviewed text")), maximum=16_384)

    @field_validator("affected_token_ids", "cited_span_ids", "cited_recognition_evidence_ids")
    @classmethod
    def _ids_are_unique(cls, values: tuple[str, ...], info: object) -> tuple[str, ...]:
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
    def _relation_is_closed_and_content_addressed(self) -> HumanPronunciationRelationReceiptV3:
        if self.clip_end_ms <= self.clip_start_ms:
            raise ValueError("pronunciation review clip must have a positive interval")
        if not self.affected_token_ids or not self.cited_recognition_evidence_ids:
            raise ValueError("pronunciation review requires exact token and Recognition IDs")
        if self.original_text == self.candidate_text:
            raise ValueError("pronunciation relation cannot represent a no-op")
        exact_shape = {
            "candidate_matches_original_does_not": (
                "incompatible",
                "compatible",
                "audibly_distinct",
            ),
            "original_matches_candidate_does_not": (
                "compatible",
                "incompatible",
                "audibly_distinct",
            ),
            "candidate_and_original_both_match_same_pronunciation": (
                "compatible",
                "compatible",
                "homophonous_or_indiscriminable",
            ),
            "audio_insufficient_to_compare_pair": (
                "indeterminate",
                "indeterminate",
                "indeterminate",
            ),
        }[self.reasoning_code]
        if (
            self.original_pronunciation,
            self.candidate_pronunciation,
            self.pair_discriminability,
        ) != exact_shape:
            raise ValueError("pronunciation relation contradicts its closed reasoning code")
        _require_identity(self, self.id, self.content_hash, "Human pronunciation relation")
        return self


class CorrectionAuthorityPolicyV3(_StrictContract):
    """Frozen non-weakenable policy for the three closed authority modes."""

    schema_version: Literal[3] = 3
    id: Sha256
    minimum_human_audio_reviewers: int = Field(ge=2, le=3)
    authority_modes: tuple[CorrectionAuthorityModeV3, ...] = _AUTHORITY_MODES
    require_exact_discovered_candidate: Literal[True] = True
    require_explicit_reference_lookup_state: Literal[True] = True
    require_complete_lookup_when_sources_enrolled: Literal[True] = True
    require_candidate_only_audio_for_discriminable_lexical: Literal[True] = True
    require_active_authoritative_literal_for_orthographic: Literal[True] = True
    require_reference_adjudicator_independence: Literal[True] = True
    authorisable_reference_scopes: tuple[ReferenceClaimScope, ...] = _AUTHORISABLE_SCOPES
    reference_only_action: Literal["defer"] = "defer"
    authority: Literal["policy_not_correction_decision"] = "policy_not_correction_decision"
    content_hash: Sha256

    @model_validator(mode="after")
    def _policy_is_closed(self) -> CorrectionAuthorityPolicyV3:
        if self.authority_modes != _AUTHORITY_MODES:
            raise ValueError("Correction authority modes cannot be broadened or reordered")
        if self.authorisable_reference_scopes != _AUTHORISABLE_SCOPES:
            raise ValueError("Correction authority cannot broaden Reference scopes")
        _require_identity(self, self.id, self.content_hash, "Correction authority policy")
        return self


class StoredReferenceEnrollmentV3(_StrictContract):
    """One immutable source identity reconstructed from stored Generation artifacts."""

    schema_version: Literal[3] = 3
    source_id: str
    logical_source_id: str
    version_id: str
    version_status: Literal["draft", "active", "superseded"]
    source_artifact_hash: Sha256
    authority_descriptor_hash: Sha256

    @field_validator("source_id", "logical_source_id", "version_id")
    @classmethod
    def _identity_is_safe(cls, value: str, info: object) -> str:
        return _bounded(value, str(getattr(info, "field_name", "source identity")), maximum=512)


class StoredReferenceEnrollmentSnapshotV3(_StrictContract):
    """Phase-B trust root rebuilt by the stored-Generation loader.

    The authority API deliberately does not accept ``references_enrolled`` or
    caller-supplied source IDs.  Before calling it, Phase B must load the exact
    Generation manifest and stored Reference artifacts, verify their hashes,
    then materialize this content-addressed snapshot.  This pure domain module
    can replay and bind the snapshot; it cannot substitute a caller assertion
    for that storage reconstruction.  Module/store integration is intentionally
    outside this vertical slice.
    """

    schema_version: Literal[3] = 3
    id: Sha256
    generation_id: str
    full_audit_aggregate_id: Sha256
    full_audit_aggregate_hash: Sha256
    generation_manifest_hash: Sha256
    reference_operator_bundle_hash: Sha256
    enrollment_manifest_hash: Sha256
    enrollments: tuple[StoredReferenceEnrollmentV3, ...]
    enrolled_logical_version_ids: tuple[str, ...]
    enrollment_set_hash: Sha256
    reconstruction: Literal[
        "rebuilt_from_stored_generation_artifacts_not_caller_declaration"
    ] = "rebuilt_from_stored_generation_artifacts_not_caller_declaration"
    authority: Literal["stored_lineage_fact_not_reference_or_audio_verdict"] = (
        "stored_lineage_fact_not_reference_or_audio_verdict"
    )
    content_hash: Sha256

    @field_validator("generation_id")
    @classmethod
    def _generation_is_safe(cls, value: str) -> str:
        return _bounded(value, "generation_id", maximum=512)

    @model_validator(mode="after")
    def _snapshot_is_exact(self) -> StoredReferenceEnrollmentSnapshotV3:
        keys = tuple(
            (item.logical_source_id, item.version_id, item.source_id)
            for item in self.enrollments
        )
        if len(set(keys)) != len(keys) or keys != tuple(sorted(keys)):
            raise ValueError("stored Reference enrollments must be unique and ordered")
        expected_ids = tuple(
            sorted(f"{item.logical_source_id}@{item.version_id}" for item in self.enrollments)
        )
        if self.enrolled_logical_version_ids != expected_ids:
            raise ValueError("stored Reference enrolled version IDs differ from artifacts")
        if self.enrollment_set_hash != hash_object(self.enrollments):
            raise ValueError("stored Reference enrollment set hash mismatch")
        _require_identity(self, self.id, self.content_hash, "Stored Reference enrollment snapshot")
        return self


class ReferenceLookupStateV3(_StrictContract):
    """Explicit, content-addressed result of authority lookup for one candidate."""

    schema_version: Literal[3] = 3
    id: Sha256
    candidate_discovery_id: Sha256
    candidate_discovery_hash: Sha256
    full_audit_aggregate_id: Sha256
    full_audit_aggregate_hash: Sha256
    generation_id: str
    normalized_audio_hash: Sha256
    resolution_key: str
    reference_scope: ReferenceClaimScope
    lookup_target_hash: Sha256
    stored_enrollment_snapshot_id: Sha256
    stored_enrollment_snapshot_hash: Sha256
    generation_manifest_hash: Sha256
    reference_operator_bundle_hash: Sha256
    enrollment_manifest_hash: Sha256
    enrolled_logical_version_ids: tuple[str, ...]
    enrollment_set_hash: Sha256
    lookup_receipt_set_hash: Sha256
    status: ReferenceLookupStatusV3
    reference_proof_id: Sha256 | None = None
    reference_proof_hash: Sha256 | None = None
    failure_artifact_hash: Sha256 | None = None
    authority: Literal["lookup_state_not_reference_or_audio_verdict"] = (
        "lookup_state_not_reference_or_audio_verdict"
    )
    content_hash: Sha256

    @field_validator("generation_id")
    @classmethod
    def _generation_is_safe(cls, value: str) -> str:
        return _bounded(value, "generation_id", maximum=512)

    @field_validator("resolution_key")
    @classmethod
    def _resolution_key_is_canonical(cls, value: str) -> str:
        _bounded(value, "resolution_key", maximum=512)
        if value != normalize_resolution_key(value):
            raise ValueError("resolution_key must already be normalized")
        return value

    @field_validator("enrolled_logical_version_ids")
    @classmethod
    def _enrollments_are_canonical(cls, values: tuple[str, ...]) -> tuple[str, ...]:
        if len(set(values)) != len(values) or values != tuple(sorted(values)):
            raise ValueError("enrolled logical/version IDs must be unique and ordered")
        for value in values:
            _bounded(value, "enrolled logical/version ID", maximum=512)
        return values

    @model_validator(mode="after")
    def _state_is_exact(self) -> ReferenceLookupStateV3:
        expected_target = hash_object(
            {
                "candidate_discovery_id": self.candidate_discovery_id,
                "candidate_discovery_hash": self.candidate_discovery_hash,
                "full_audit_aggregate_id": self.full_audit_aggregate_id,
                "full_audit_aggregate_hash": self.full_audit_aggregate_hash,
                "generation_id": self.generation_id,
                "normalized_audio_hash": self.normalized_audio_hash,
                "resolution_key": self.resolution_key,
                "reference_scope": self.reference_scope,
            }
        )
        if self.lookup_target_hash != expected_target:
            raise ValueError("Reference lookup target hash mismatch")
        if self.enrollment_set_hash != hash_object(self.enrolled_logical_version_ids):
            raise ValueError("Reference lookup enrollment set hash mismatch")
        proof_pair = (self.reference_proof_id, self.reference_proof_hash)
        if (proof_pair[0] is None) != (proof_pair[1] is None):
            raise ValueError("Reference lookup proof ID/hash must be wholly present or absent")
        if self.status == "not_enrolled":
            if self.enrolled_logical_version_ids or any(value is not None for value in proof_pair):
                raise ValueError("not_enrolled lookup cannot name enrollments or a proof")
            if self.failure_artifact_hash is not None:
                raise ValueError("not_enrolled lookup cannot carry a failure artifact")
        elif self.status == "completed":
            if not self.enrolled_logical_version_ids or proof_pair[0] is None:
                raise ValueError("completed lookup requires enrollments and an exact proof")
            if self.failure_artifact_hash is not None:
                raise ValueError("completed lookup cannot carry a failure artifact")
        elif self.status == "failed":
            if (
                not self.enrolled_logical_version_ids
                or proof_pair[0] is not None
                or self.failure_artifact_hash is None
            ):
                raise ValueError("failed lookup requires enrollments and only a failure artifact")
        elif (
            not self.enrolled_logical_version_ids
            or proof_pair[0] is None
            or self.failure_artifact_hash is not None
        ):
            raise ValueError("incomplete lookup requires enrollments and an incomplete proof")
        _require_identity(self, self.id, self.content_hash, "Reference lookup state")
        return self


class CorrectionAuthorityVerdictV3(_StrictContract):
    """Content-addressed authorization only; it has no mutation authority."""

    schema_version: Literal[3] = 3
    id: Sha256
    action: CorrectionAuthorityActionV3
    authority_mode: CorrectionAuthorityModeV3
    candidate_modality: Literal["text", "audio"]
    candidate_discovery_id: Sha256
    candidate_discovery_hash: Sha256
    full_audit_aggregate_id: Sha256
    full_audit_aggregate_hash: Sha256
    generation_id: str
    normalized_audio_hash: Sha256
    recognition_evidence_hashes: tuple[Sha256, ...]
    recognition_evidence_set_hash: Sha256
    resolution_key: str
    reference_scope: ReferenceClaimScope
    affected_token_ids: tuple[str, ...]
    cited_span_ids: tuple[str, ...]
    cited_recognition_evidence_ids: tuple[str, ...]
    original_text: str
    candidate_text: str
    reference_lookup_state: ArtifactBindingV3
    reference_lookup_status: ReferenceLookupStatusV3
    reference_proof: ArtifactBindingV3 | None
    reference_coverage_receipt: ArtifactBindingV3 | None
    reference_conflict_set: ArtifactBindingV3 | None
    selected_reference_claim: ArtifactBindingV3 | None
    selected_reference_literal: str | None
    human_pronunciation_receipts: tuple[ArtifactBindingV3, ...]
    human_reference_adjudication: ArtifactBindingV3 | None
    audio_window_start_ms: int | None = Field(default=None, ge=0)
    audio_window_end_ms: int | None = Field(default=None, gt=0)
    policy: ArtifactBindingV3
    reasons: tuple[CorrectionAuthorityReasonV3, ...]
    authority: Literal["authorization_not_mutation"] = _AUTHORITY_LIMIT
    content_hash: Sha256

    @field_validator("original_text", "candidate_text")
    @classmethod
    def _verdict_text_is_safe(cls, value: str, info: object) -> str:
        return _bounded(value, str(getattr(info, "field_name", "verdict text")), maximum=16_384)

    @model_validator(mode="after")
    def _verdict_is_exact(self) -> CorrectionAuthorityVerdictV3:
        if self.original_text == self.candidate_text:
            raise ValueError("Correction authority verdict cannot represent a no-op")
        if (self.audio_window_start_ms is None) != (self.audio_window_end_ms is None):
            raise ValueError("audio window must be wholly present or absent")
        if (
            self.audio_window_start_ms is not None
            and self.audio_window_end_ms is not None
            and self.audio_window_end_ms <= self.audio_window_start_ms
        ):
            raise ValueError("audio window must be positive")
        receipt_ids = tuple(item.id for item in self.human_pronunciation_receipts)
        if len(set(receipt_ids)) != len(receipt_ids) or receipt_ids != tuple(sorted(receipt_ids)):
            raise ValueError("pronunciation receipt bindings must be unique and ordered")
        if not self.reasons or len(set(self.reasons)) != len(self.reasons):
            raise ValueError("Correction authority verdict requires unique typed reasons")
        expected_reasons = tuple(reason for reason in _REASON_ORDER if reason in set(self.reasons))
        if self.reasons != expected_reasons:
            raise ValueError("Correction authority reasons are not in canonical order")
        selected_pair = (self.selected_reference_claim, self.selected_reference_literal)
        if (selected_pair[0] is None) != (selected_pair[1] is None):
            raise ValueError("selected Reference claim/literal must be wholly present or absent")
        if self.action == "authorize_exact_candidate":
            expected_success = {
                "audio_discriminable_lexical": (
                    "audio_discriminable_exact_candidate_authorized",
                ),
                "orthographic_homophone_or_entity": (
                    "orthographic_exact_reference_literal_authorized",
                ),
                "reference_only": (),
            }[self.authority_mode]
            if not expected_success or self.reasons != expected_success:
                raise ValueError("authorized verdict has the wrong closed success reason")
            if not self.human_pronunciation_receipts or self.audio_window_start_ms is None:
                raise ValueError("authorized verdict lacks exact human audio lineage")
            if self.authority_mode == "orthographic_homophone_or_entity":
                if (
                    self.reference_lookup_status != "completed"
                    or self.selected_reference_claim is None
                    or self.selected_reference_literal != self.candidate_text
                ):
                    raise ValueError("orthographic authorization lacks exact Reference authority")
            elif self.selected_reference_claim is not None:
                raise ValueError(
                    "audio-discriminable authorization must not imply spelling authority"
                )
        elif self.selected_reference_claim is not None:
            raise ValueError("non-authorized verdict cannot expose a selected Reference claim")
        if self.action == "reject" and "audio_candidate_incompatible" not in self.reasons:
            raise ValueError("reject is reserved for unanimous candidate-incompatible audio")
        _require_identity(self, self.id, self.content_hash, "Correction authority verdict")
        return self


def default_correction_authority_policy() -> CorrectionAuthorityPolicyV3:
    return _seal(
        CorrectionAuthorityPolicyV3,
        {
            "schema_version": 3,
            "minimum_human_audio_reviewers": 2,
            "authority_modes": _AUTHORITY_MODES,
            "require_exact_discovered_candidate": True,
            "require_explicit_reference_lookup_state": True,
            "require_complete_lookup_when_sources_enrolled": True,
            "require_candidate_only_audio_for_discriminable_lexical": True,
            "require_active_authoritative_literal_for_orthographic": True,
            "require_reference_adjudicator_independence": True,
            "authorisable_reference_scopes": _AUTHORISABLE_SCOPES,
            "reference_only_action": "defer",
            "authority": "policy_not_correction_decision",
        },
    )


def _candidate_modality(candidate: CandidateDiscoveryV2) -> Literal["text", "audio"]:
    return "text" if isinstance(candidate, TextDiscoveredCandidateV2) else "audio"


def _validate_candidate_and_aggregate(
    candidate: CandidateDiscoveryV2,
    aggregate: FullAuditAggregateAttestationV2,
) -> tuple[CandidateDiscoveryV2, FullAuditAggregateAttestationV2]:
    if not isinstance(candidate, (TextDiscoveredCandidateV2, AudioDiscoveredCandidateV2)):
        raise CorrectionAuthorityError("candidate must be a typed v2 discovery artifact")
    candidate = _validate_exact_model(candidate, type(candidate), "candidate discovery")
    aggregate = _validate_exact_model(
        aggregate,
        FullAuditAggregateAttestationV2,
        "Full Audit aggregate",
    )
    discovery_ids = (
        aggregate.text_discovery_ids
        if isinstance(candidate, TextDiscoveredCandidateV2)
        else aggregate.audio_discovery_ids
    )
    if candidate.id not in discovery_ids or candidate.cell_id not in aggregate.all_cell_ids:
        raise CorrectionAuthorityError("candidate is not a member of the Full Audit aggregate")
    return candidate, aggregate


def _validate_reference_proof(
    proof: ReferenceAuthorityProofV2 | None,
) -> ReferenceAuthorityProofV2 | None:
    if proof is None:
        return None
    return _validate_exact_model(proof, ReferenceAuthorityProofV2, "Reference authority proof")


def _proof_matches_stored_enrollment(
    proof: ReferenceAuthorityProofV2,
    snapshot: StoredReferenceEnrollmentSnapshotV3,
) -> bool:
    expected = tuple(
        (
            item.source_id,
            item.logical_source_id,
            item.version_id,
            item.version_status,
            item.source_artifact_hash,
            item.authority_descriptor_hash,
        )
        for item in snapshot.enrollments
    )
    actual = tuple(
        (
            item.source_id,
            item.logical_source_id,
            item.version_id,
            item.version_status,
            item.artifact_sha256,
            item.descriptor_hash,
        )
        for item in proof.coverage.version_coverage
    )
    return actual == expected


def build_reference_lookup_state(
    *,
    candidate: CandidateDiscoveryV2,
    full_audit_aggregate: FullAuditAggregateAttestationV2,
    resolution_key: str,
    reference_scope: ReferenceClaimScope,
    stored_enrollment_snapshot: StoredReferenceEnrollmentSnapshotV3,
    lookup_receipt_set_hash: Sha256,
    status: ReferenceLookupStatusV3,
    reference_proof: ReferenceAuthorityProofV2 | None = None,
    failure_artifact_hash: Sha256 | None = None,
) -> ReferenceLookupStateV3:
    """Build a machine receipt; this function never fabricates human evidence."""

    candidate, aggregate = _validate_candidate_and_aggregate(
        candidate,
        full_audit_aggregate,
    )
    _bounded(resolution_key, "resolution_key", maximum=512)
    if resolution_key != normalize_resolution_key(resolution_key):
        raise CorrectionAuthorityError("resolution_key is not canonical")
    snapshot = _validate_exact_model(
        stored_enrollment_snapshot,
        StoredReferenceEnrollmentSnapshotV3,
        "stored Reference enrollment snapshot",
    )
    if (
        snapshot.generation_id != aggregate.generation_id
        or snapshot.full_audit_aggregate_id != aggregate.id
        or snapshot.full_audit_aggregate_hash != aggregate.content_hash
    ):
        raise CorrectionAuthorityError(
            "stored Reference enrollment snapshot crossed Generation/Full Audit lineage"
        )
    if status not in {"not_enrolled", "completed", "failed", "incomplete"}:
        raise CorrectionAuthorityError("unknown Reference lookup status")
    proof = _validate_reference_proof(reference_proof)
    if proof is not None and not _proof_matches_stored_enrollment(proof, snapshot):
        raise CorrectionAuthorityError(
            "Reference proof coverage differs from stored Generation enrollments"
        )
    if status == "completed":
        if proof is None:
            raise CorrectionAuthorityError("completed Reference lookup requires an exact proof")
        if (
            not proof.coverage.selection_complete
            or not proof.coverage.bounded_retrieval_assessed
            or proof.coverage.bounded_retrieval_complete is not True
            or proof.conflicts.unresolved_conditions
        ):
            raise CorrectionAuthorityError("completed Reference lookup proof is incomplete")
    elif status == "incomplete":
        if proof is None:
            raise CorrectionAuthorityError("incomplete Reference lookup requires its partial proof")
        if (
            proof.coverage.selection_complete
            and proof.coverage.bounded_retrieval_assessed
            and proof.coverage.bounded_retrieval_complete is True
            and not proof.conflicts.unresolved_conditions
        ):
            raise CorrectionAuthorityError("incomplete lookup cannot bind a complete proof")
    elif proof is not None:
        raise CorrectionAuthorityError(f"{status} Reference lookup cannot bind a proof")
    enrollment_ids = snapshot.enrolled_logical_version_ids
    target_payload = {
        "candidate_discovery_id": candidate.id,
        "candidate_discovery_hash": candidate.content_hash,
        "full_audit_aggregate_id": aggregate.id,
        "full_audit_aggregate_hash": aggregate.content_hash,
        "generation_id": aggregate.generation_id,
        "normalized_audio_hash": aggregate.normalized_audio_hash,
        "resolution_key": resolution_key,
        "reference_scope": reference_scope,
    }
    payload: dict[str, object] = {
        "schema_version": 3,
        **target_payload,
        "lookup_target_hash": hash_object(target_payload),
        "stored_enrollment_snapshot_id": snapshot.id,
        "stored_enrollment_snapshot_hash": snapshot.content_hash,
        "generation_manifest_hash": snapshot.generation_manifest_hash,
        "reference_operator_bundle_hash": snapshot.reference_operator_bundle_hash,
        "enrollment_manifest_hash": snapshot.enrollment_manifest_hash,
        "enrolled_logical_version_ids": enrollment_ids,
        "enrollment_set_hash": hash_object(enrollment_ids),
        "lookup_receipt_set_hash": lookup_receipt_set_hash,
        "status": status,
        "reference_proof_id": proof.id if proof is not None else None,
        "reference_proof_hash": proof.content_hash if proof is not None else None,
        "failure_artifact_hash": failure_artifact_hash,
        "authority": "lookup_state_not_reference_or_audio_verdict",
    }
    try:
        return _seal(ReferenceLookupStateV3, payload)
    except (ValidationError, ValueError) as exc:
        raise CorrectionAuthorityError("Reference lookup state is internally inconsistent") from exc


def _recognition_registry(
    evidence: tuple[RecognitionEvidence, ...],
) -> tuple[tuple[Sha256, ...], dict[str, object]]:
    hashes: list[str] = []
    registry: dict[str, object] = {}
    for index, item in enumerate(evidence):
        exact = _validate_exact_model(item, RecognitionEvidence, f"Recognition Evidence {index}")
        source_hash = recognition_evidence_content_hash(exact)
        if source_hash in hashes:
            raise CorrectionAuthorityError("Recognition Evidence contains duplicate content")
        hashes.append(source_hash)
        for token in exact.tokens:
            key = f"{source_hash}:{token.id}"
            if key in registry:
                raise CorrectionAuthorityError("Recognition token registry contains a duplicate ID")
            registry[key] = token
    return tuple(sorted(hashes)), registry


def _pronunciation_receipt_binding_is_exact(
    receipt: HumanPronunciationRelationReceiptV3,
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
        or receipt.original_text != candidate.observed_text
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


def build_human_pronunciation_relation_receipt(
    *,
    candidate: CandidateDiscoveryV2,
    full_audit_aggregate: FullAuditAggregateAttestationV2,
    recognition_evidence: tuple[RecognitionEvidence, ...],
    clip_extraction_policy_hash: Sha256,
    clip_sha256: Sha256,
    clip_size_bytes: int,
    clip_start_ms: int,
    clip_end_ms: int,
    reasoning_code: PronunciationRelationReasonV3,
    notes_digest: Sha256,
    reviewer: HumanReviewerAttestationV2,
    reviewed_at_utc: str,
) -> HumanPronunciationRelationReceiptV3:
    """Seal an authenticated human review without caller-supplied conclusions.

    The caller supplies one closed reasoning code selected by a human review
    surface.  Original/candidate text, clip lineage, pronunciation outcomes and
    pair discriminability are derived here; contradictory combinations are not
    representable.  This builder authenticates no person and performs no audio
    inference—the ``reviewer`` attestation and review event must already exist.
    """

    candidate, aggregate = _validate_candidate_and_aggregate(
        candidate,
        full_audit_aggregate,
    )
    if reasoning_code not in {
        "candidate_matches_original_does_not",
        "original_matches_candidate_does_not",
        "candidate_and_original_both_match_same_pronunciation",
        "audio_insufficient_to_compare_pair",
    }:
        raise CorrectionAuthorityError("unknown pronunciation relation reasoning code")
    reviewer = _validate_exact_model(
        reviewer,
        HumanReviewerAttestationV2,
        "human reviewer attestation",
    )
    evidence_hashes, recognition_registry = _recognition_registry(recognition_evidence)
    if (
        evidence_hashes != aggregate.recognition_evidence_hashes
        or recognition_evidence_set_hash(recognition_evidence)
        != aggregate.audit_recognition_evidence_set_hash
        or any(
            item.normalized_audio_hash != aggregate.normalized_audio_hash
            for item in recognition_evidence
        )
        or not set(candidate.cited_recognition_evidence_ids) <= set(recognition_registry)
    ):
        raise CorrectionAuthorityError("Recognition Evidence differs from Full Audit lineage")
    if isinstance(candidate, AudioDiscoveredCandidateV2):
        clip_lineage_kind = "audio_audit_candidate_clip"
        clip_lineage_parent_id = candidate.packet_id
        if clip_sha256 != candidate.clip_hash:
            raise CorrectionAuthorityError("audio candidate human review clip hash drift")
    else:
        clip_lineage_kind = "recognition_token_window"
        clip_lineage_parent_id = candidate.id
    relation = {
        "candidate_matches_original_does_not": (
            "incompatible",
            "compatible",
            "audibly_distinct",
        ),
        "original_matches_candidate_does_not": (
            "compatible",
            "incompatible",
            "audibly_distinct",
        ),
        "candidate_and_original_both_match_same_pronunciation": (
            "compatible",
            "compatible",
            "homophonous_or_indiscriminable",
        ),
        "audio_insufficient_to_compare_pair": (
            "indeterminate",
            "indeterminate",
            "indeterminate",
        ),
    }[reasoning_code]
    payload: dict[str, object] = {
        "schema_version": 3,
        "candidate_discovery_id": candidate.id,
        "candidate_discovery_hash": candidate.content_hash,
        "full_audit_aggregate_id": aggregate.id,
        "full_audit_aggregate_hash": aggregate.content_hash,
        "normalized_audio_hash": aggregate.normalized_audio_hash,
        "clip_lineage_kind": clip_lineage_kind,
        "clip_lineage_parent_id": clip_lineage_parent_id,
        "clip_extraction_policy_hash": clip_extraction_policy_hash,
        "clip_sha256": clip_sha256,
        "clip_size_bytes": clip_size_bytes,
        "clip_start_ms": clip_start_ms,
        "clip_end_ms": clip_end_ms,
        "affected_token_ids": candidate.affected_token_ids,
        "cited_span_ids": candidate.cited_span_ids,
        "cited_recognition_evidence_ids": candidate.cited_recognition_evidence_ids,
        "original_text": candidate.observed_text,
        "candidate_text": candidate.candidate_text,
        "original_pronunciation": relation[0],
        "candidate_pronunciation": relation[1],
        "pair_discriminability": relation[2],
        "reasoning_code": reasoning_code,
        "notes_digest": notes_digest,
        "reviewer": reviewer,
        "reviewed_at_utc": reviewed_at_utc,
        "authority": "human_pronunciation_relation_not_spelling_authority",
    }
    try:
        receipt = _seal(HumanPronunciationRelationReceiptV3, payload)
    except (ValidationError, ValueError) as exc:
        raise CorrectionAuthorityError("human pronunciation receipt is inconsistent") from exc
    if not _pronunciation_receipt_binding_is_exact(
        receipt,
        candidate=candidate,
        aggregate=aggregate,
        recognition_registry=recognition_registry,
    ):
        raise CorrectionAuthorityError("human pronunciation receipt clip/Recognition lineage drift")
    return receipt


def _relevant_reference_material(
    proof: ReferenceAuthorityProofV2,
    *,
    resolution_key: str,
    reference_scope: ReferenceClaimScope,
    candidate_text: str,
) -> tuple[
    tuple[ReferenceClaimV2, ...],
    tuple[ReferenceClaimV2, ...],
    tuple[ReferenceClaimV2, ...],
    tuple[object, ...],
]:
    scoped = tuple(
        item
        for item in proof.claims
        if item.resolution_key == resolution_key and item.scope == reference_scope
    )
    exact = tuple(
        item
        for item in scoped
        if item.strength == "authoritative"
        and item.version_status == "active"
        and item.claimed_text == candidate_text
    )
    counterclaims = tuple(
        item
        for item in scoped
        if item.strength == "authoritative"
        and item.version_status == "active"
        and item.claimed_text != candidate_text
    )
    conflicts = tuple(
        item
        for item in proof.conflicts.conflicts
        if item.resolution_key == resolution_key and item.overlapping_scope == reference_scope
    )
    return scoped, exact, counterclaims, conflicts


def _reference_adjudication_is_exact(
    receipt: HumanReferenceAdjudicationReceiptV2,
    *,
    candidate: CandidateDiscoveryV2,
    proof: ReferenceAuthorityProofV2,
    resolution_key: str,
    reference_scope: ReferenceClaimScope,
    relevant_conflict_ids: tuple[str, ...],
    exact_claims: tuple[ReferenceClaimV2, ...],
) -> bool:
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
    exact_by_id = {item.id: item for item in exact_claims}
    if receipt.chosen_claim_id is None:
        return False
    chosen = exact_by_id.get(receipt.chosen_claim_id)
    if chosen is None or receipt.chosen_literal != chosen.claimed_text:
        return False
    conflict_by_id = {item.id: item for item in proof.conflicts.conflicts}
    return all(
        receipt.chosen_claim_id in conflict_by_id[conflict_id].claim_ids
        for conflict_id in receipt.conflict_ids
    )


def _canonical_reasons(
    reasons: set[CorrectionAuthorityReasonV3],
) -> tuple[CorrectionAuthorityReasonV3, ...]:
    return tuple(reason for reason in _REASON_ORDER if reason in reasons)


def build_correction_authority_verdict(
    *,
    candidate: CandidateDiscoveryV2,
    full_audit_aggregate: FullAuditAggregateAttestationV2,
    recognition_evidence: tuple[RecognitionEvidence, ...],
    authority_mode: CorrectionAuthorityModeV3,
    resolution_key: str,
    reference_scope: ReferenceClaimScope,
    stored_enrollment_snapshot: StoredReferenceEnrollmentSnapshotV3,
    reference_lookup_state: ReferenceLookupStateV3,
    reference_proof: ReferenceAuthorityProofV2 | None,
    human_pronunciation_receipts: tuple[HumanPronunciationRelationReceiptV3, ...],
    human_reference_adjudication: HumanReferenceAdjudicationReceiptV2 | None,
    policy: CorrectionAuthorityPolicyV3,
) -> CorrectionAuthorityVerdictV3:
    """Authorize only the exact stored candidate under one closed authority mode."""

    if authority_mode not in _AUTHORITY_MODES:
        raise CorrectionAuthorityError("unknown correction authority mode")
    candidate, aggregate = _validate_candidate_and_aggregate(
        candidate,
        full_audit_aggregate,
    )
    policy = _validate_exact_model(policy, CorrectionAuthorityPolicyV3, "authority policy")
    _bounded(resolution_key, "resolution_key", maximum=512)
    if resolution_key != normalize_resolution_key(resolution_key):
        raise CorrectionAuthorityError("resolution_key is not canonical")
    if reference_scope not in policy.authorisable_reference_scopes:
        raise CorrectionAuthorityError("reference scope is not authorisable")

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
        raise CorrectionAuthorityError("Recognition Evidence differs from Full Audit lineage")

    proof = _validate_reference_proof(reference_proof)
    snapshot = _validate_exact_model(
        stored_enrollment_snapshot,
        StoredReferenceEnrollmentSnapshotV3,
        "stored Reference enrollment snapshot",
    )
    lookup = _validate_exact_model(
        reference_lookup_state,
        ReferenceLookupStateV3,
        "Reference lookup state",
    )
    if (
        snapshot.generation_id != aggregate.generation_id
        or snapshot.full_audit_aggregate_id != aggregate.id
        or snapshot.full_audit_aggregate_hash != aggregate.content_hash
        or lookup.stored_enrollment_snapshot_id != snapshot.id
        or lookup.stored_enrollment_snapshot_hash != snapshot.content_hash
        or lookup.generation_manifest_hash != snapshot.generation_manifest_hash
        or lookup.reference_operator_bundle_hash != snapshot.reference_operator_bundle_hash
        or lookup.enrollment_manifest_hash != snapshot.enrollment_manifest_hash
        or lookup.enrolled_logical_version_ids
        != snapshot.enrolled_logical_version_ids
        or lookup.candidate_discovery_id != candidate.id
        or lookup.candidate_discovery_hash != candidate.content_hash
        or lookup.full_audit_aggregate_id != aggregate.id
        or lookup.full_audit_aggregate_hash != aggregate.content_hash
        or lookup.generation_id != aggregate.generation_id
        or lookup.normalized_audio_hash != aggregate.normalized_audio_hash
        or lookup.resolution_key != resolution_key
        or lookup.reference_scope != reference_scope
    ):
        raise CorrectionAuthorityError(
            "Reference lookup state differs from stored enrollment/candidate lineage"
        )
    expected_proof_pair = (
        (proof.id, proof.content_hash) if proof is not None else (None, None)
    )
    if (lookup.reference_proof_id, lookup.reference_proof_hash) != expected_proof_pair:
        raise CorrectionAuthorityError("Reference lookup state differs from supplied proof")
    expected_lookup = build_reference_lookup_state(
        candidate=candidate,
        full_audit_aggregate=aggregate,
        resolution_key=resolution_key,
        reference_scope=reference_scope,
        stored_enrollment_snapshot=snapshot,
        lookup_receipt_set_hash=lookup.lookup_receipt_set_hash,
        status=lookup.status,
        reference_proof=proof,
        failure_artifact_hash=lookup.failure_artifact_hash,
    )
    if expected_lookup != lookup:
        raise CorrectionAuthorityError("Reference lookup state differs from frozen parents")
    if lookup.status == "completed" and (
        proof is None
        or not proof.coverage.selection_complete
        or not proof.coverage.bounded_retrieval_assessed
        or proof.coverage.bounded_retrieval_complete is not True
        or proof.conflicts.unresolved_conditions
    ):
        raise CorrectionAuthorityError("completed Reference lookup is not complete on replay")
    if lookup.status == "incomplete" and proof is None:
        raise CorrectionAuthorityError("incomplete Reference lookup lost its partial proof")

    receipts = tuple(
        _validate_exact_model(
            item,
            HumanPronunciationRelationReceiptV3,
            "human pronunciation receipt",
        )
        for item in human_pronunciation_receipts
    )
    receipt_ids = tuple(item.id for item in receipts)
    if len(set(receipt_ids)) != len(receipt_ids):
        raise CorrectionAuthorityError("human pronunciation receipt artifact is duplicated")
    if receipt_ids != tuple(sorted(receipt_ids)):
        raise CorrectionAuthorityError("human pronunciation receipts are reordered")
    exact_receipts = tuple(
        item
        for item in receipts
        if _pronunciation_receipt_binding_is_exact(
            item,
            candidate=candidate,
            aggregate=aggregate,
            recognition_registry=recognition_registry,
        )
    )
    reasons: set[CorrectionAuthorityReasonV3] = set()
    if not receipts:
        reasons.add("human_audio_receipt_missing")
    elif len(exact_receipts) != len(receipts):
        reasons.add("human_audio_receipt_lineage_mismatch")
    else:
        reviewer_ids = tuple(item.reviewer.reviewer_id for item in exact_receipts)
        if len(set(reviewer_ids)) != len(reviewer_ids):
            reasons.add("duplicate_human_audio_reviewer")
        if len(set(reviewer_ids)) < policy.minimum_human_audio_reviewers:
            reasons.add("human_audio_quorum_not_met")
        if len({item.reasoning_code for item in exact_receipts}) != 1:
            reasons.add("human_audio_reviewers_disagree")
        if len(
            {
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
        ) != 1:
            reasons.add("human_audio_receipt_lineage_mismatch")
        relation_codes = {item.reasoning_code for item in exact_receipts}
        if relation_codes == {"original_matches_candidate_does_not"}:
            reasons.add("audio_candidate_incompatible")
        elif relation_codes == {"audio_insufficient_to_compare_pair"}:
            reasons.add("audio_pair_indeterminate")
        elif relation_codes == {"candidate_and_original_both_match_same_pronunciation"}:
            reasons.add("audio_pair_not_discriminable")

    reference_receipt: HumanReferenceAdjudicationReceiptV2 | None = None
    if human_reference_adjudication is not None:
        reference_receipt = _validate_exact_model(
            human_reference_adjudication,
            HumanReferenceAdjudicationReceiptV2,
            "human Reference adjudication",
        )

    selected_claim: ReferenceClaimV2 | None = None
    if authority_mode == "reference_only":
        reasons.add("reference_only_never_authorizes_text_mutation")
    elif lookup.status == "failed":
        reasons.add("reference_lookup_failed")
    elif lookup.status == "incomplete":
        reasons.add("reference_lookup_incomplete")
    elif authority_mode == "orthographic_homophone_or_entity" and lookup.status == "not_enrolled":
        reasons.add("reference_lookup_not_enrolled")

    if authority_mode != "reference_only" and lookup.status == "completed":
        if proof is None:
            reasons.add("reference_proof_missing_or_mismatched")
        elif (
            not proof.coverage.selection_complete
            or not proof.coverage.bounded_retrieval_assessed
            or proof.coverage.bounded_retrieval_complete is not True
            or proof.conflicts.unresolved_conditions
        ):
            reasons.add("reference_coverage_incomplete")
        else:
            _scoped, exact_claims, counterclaims, conflicts = _relevant_reference_material(
                proof,
                resolution_key=resolution_key,
                reference_scope=reference_scope,
                candidate_text=candidate.candidate_text,
            )
            if authority_mode == "audio_discriminable_lexical":
                if counterclaims:
                    reasons.add("reference_counterclaim_blocks_audio_lexical")
                if conflicts:
                    reasons.add("reference_conflict_blocks_audio_lexical")
            else:
                if not exact_claims:
                    if counterclaims:
                        reasons.add("reference_literal_mismatch")
                    else:
                        reasons.add("reference_authoritative_literal_missing")
                relevant_conflict_ids = tuple(item.id for item in conflicts)
                if conflicts:
                    if reference_receipt is None:
                        reasons.add("reference_adjudication_missing")
                    elif not _reference_adjudication_is_exact(
                        reference_receipt,
                        candidate=candidate,
                        proof=proof,
                        resolution_key=resolution_key,
                        reference_scope=reference_scope,
                        relevant_conflict_ids=relevant_conflict_ids,
                        exact_claims=exact_claims,
                    ):
                        reasons.add("reference_adjudication_invalid")
                    elif reference_receipt.decision == "defer":
                        reasons.add("reference_adjudication_deferred")
                    elif reference_receipt.reviewer.reviewer_id in {
                        item.reviewer.reviewer_id for item in exact_receipts
                    }:
                        reasons.add("reference_reviewer_not_independent")
                    else:
                        selected_claim = next(
                            item
                            for item in exact_claims
                            if item.id == reference_receipt.chosen_claim_id
                        )
                elif reference_receipt is not None:
                    reasons.add("reference_adjudication_invalid")
                elif exact_claims:
                    selected_claim = exact_claims[0]

    if reference_receipt is not None and (
        authority_mode != "orthographic_homophone_or_entity" or lookup.status != "completed"
    ):
        reasons.add("reference_adjudication_invalid")

    # Audio pair meaning depends on the selected authority mode.  Homophone
    # indistinguishability is expected (and safe) only when exact Reference
    # spelling authority is also required.
    if authority_mode == "orthographic_homophone_or_entity":
        reasons.discard("audio_pair_not_discriminable")
    if authority_mode == "audio_discriminable_lexical" and lookup.status == "not_enrolled":
        # Explicit no-enrollment is a valid completed authority search for this
        # mode; failed/incomplete searches remain blocking above.
        pass

    blocking_reasons = set(reasons)
    if not blocking_reasons:
        if authority_mode == "audio_discriminable_lexical":
            reasons = {"audio_discriminable_exact_candidate_authorized"}
            action: CorrectionAuthorityActionV3 = "authorize_exact_candidate"
        elif authority_mode == "orthographic_homophone_or_entity":
            if selected_claim is None:
                raise CorrectionAuthorityError("orthographic authorization lacks a selected claim")
            reasons = {"orthographic_exact_reference_literal_authorized"}
            action = "authorize_exact_candidate"
        else:  # unreachable due explicit reference-only reason
            action = "defer"
    elif reasons == {"audio_candidate_incompatible"}:
        action = "reject"
        selected_claim = None
    else:
        action = "defer"
        selected_claim = None

    exact_clip_receipts = exact_receipts if len(exact_receipts) == len(receipts) else ()
    audio_window = (
        (exact_clip_receipts[0].clip_start_ms, exact_clip_receipts[0].clip_end_ms)
        if exact_clip_receipts
        else (None, None)
    )
    proof_binding = (
        ArtifactBindingV3(id=proof.id, content_hash=proof.content_hash)
        if proof is not None
        else None
    )
    payload: dict[str, object] = {
        "schema_version": 3,
        "action": action,
        "authority_mode": authority_mode,
        "candidate_modality": _candidate_modality(candidate),
        "candidate_discovery_id": candidate.id,
        "candidate_discovery_hash": candidate.content_hash,
        "full_audit_aggregate_id": aggregate.id,
        "full_audit_aggregate_hash": aggregate.content_hash,
        "generation_id": aggregate.generation_id,
        "normalized_audio_hash": aggregate.normalized_audio_hash,
        "recognition_evidence_hashes": evidence_hashes,
        "recognition_evidence_set_hash": recognition_evidence_set_hash(recognition_evidence),
        "resolution_key": resolution_key,
        "reference_scope": reference_scope,
        "affected_token_ids": candidate.affected_token_ids,
        "cited_span_ids": candidate.cited_span_ids,
        "cited_recognition_evidence_ids": candidate.cited_recognition_evidence_ids,
        "original_text": candidate.observed_text,
        "candidate_text": candidate.candidate_text,
        "reference_lookup_state": ArtifactBindingV3(
            id=lookup.id,
            content_hash=lookup.content_hash,
        ),
        "reference_lookup_status": lookup.status,
        "reference_proof": proof_binding,
        "reference_coverage_receipt": (
            ArtifactBindingV3(id=proof.coverage.id, content_hash=proof.coverage.content_hash)
            if proof is not None
            else None
        ),
        "reference_conflict_set": (
            ArtifactBindingV3(id=proof.conflicts.id, content_hash=proof.conflicts.content_hash)
            if proof is not None
            else None
        ),
        "selected_reference_claim": (
            ArtifactBindingV3(id=selected_claim.id, content_hash=selected_claim.content_hash)
            if selected_claim is not None
            else None
        ),
        "selected_reference_literal": (
            selected_claim.claimed_text if selected_claim is not None else None
        ),
        "human_pronunciation_receipts": tuple(
            ArtifactBindingV3(id=item.id, content_hash=item.content_hash) for item in receipts
        ),
        "human_reference_adjudication": (
            ArtifactBindingV3(id=reference_receipt.id, content_hash=reference_receipt.content_hash)
            if reference_receipt is not None
            else None
        ),
        "audio_window_start_ms": audio_window[0],
        "audio_window_end_ms": audio_window[1],
        "policy": ArtifactBindingV3(id=policy.id, content_hash=policy.content_hash),
        "reasons": _canonical_reasons(reasons),
        "authority": _AUTHORITY_LIMIT,
    }
    return _seal(CorrectionAuthorityVerdictV3, payload)


def pronunciation_relation_receipt_bytes(
    receipt: HumanPronunciationRelationReceiptV3,
) -> bytes:
    return canonical_json_bytes(receipt)


def correction_authority_policy_bytes(policy: CorrectionAuthorityPolicyV3) -> bytes:
    return canonical_json_bytes(policy)


def reference_lookup_state_bytes(state: ReferenceLookupStateV3) -> bytes:
    return canonical_json_bytes(state)


def stored_reference_enrollment_snapshot_bytes(
    snapshot: StoredReferenceEnrollmentSnapshotV3,
) -> bytes:
    return canonical_json_bytes(snapshot)


def correction_authority_verdict_bytes(verdict: CorrectionAuthorityVerdictV3) -> bytes:
    return canonical_json_bytes(verdict)


def _verify_canonical_bytes(exact_bytes: bytes, model: type[_ModelT], label: str) -> _ModelT:
    if type(exact_bytes) is not bytes:
        raise CorrectionAuthorityError(f"{label} requires exact bytes")
    try:
        parsed = model.model_validate_json(exact_bytes, strict=True)
    except (ValidationError, ValueError) as exc:
        raise CorrectionAuthorityError(f"{label} bytes violate strict schema") from exc
    if canonical_json_bytes(parsed) != exact_bytes:
        raise CorrectionAuthorityError(f"{label} bytes are not canonical")
    return parsed


def verify_pronunciation_relation_receipt(
    exact_bytes: bytes,
    *,
    candidate: CandidateDiscoveryV2,
    full_audit_aggregate: FullAuditAggregateAttestationV2,
    recognition_evidence: tuple[RecognitionEvidence, ...],
) -> HumanPronunciationRelationReceiptV3:
    parsed = _verify_canonical_bytes(
        exact_bytes,
        HumanPronunciationRelationReceiptV3,
        "Human pronunciation relation receipt",
    )
    rebuilt = build_human_pronunciation_relation_receipt(
        candidate=candidate,
        full_audit_aggregate=full_audit_aggregate,
        recognition_evidence=recognition_evidence,
        clip_extraction_policy_hash=parsed.clip_extraction_policy_hash,
        clip_sha256=parsed.clip_sha256,
        clip_size_bytes=parsed.clip_size_bytes,
        clip_start_ms=parsed.clip_start_ms,
        clip_end_ms=parsed.clip_end_ms,
        reasoning_code=parsed.reasoning_code,
        notes_digest=parsed.notes_digest,
        reviewer=parsed.reviewer,
        reviewed_at_utc=parsed.reviewed_at_utc,
    )
    if parsed != rebuilt or exact_bytes != canonical_json_bytes(rebuilt):
        raise CorrectionAuthorityError(
            "Human pronunciation relation receipt differs from frozen parents"
        )
    return rebuilt


def verify_correction_authority_policy(exact_bytes: bytes) -> CorrectionAuthorityPolicyV3:
    return _verify_canonical_bytes(
        exact_bytes,
        CorrectionAuthorityPolicyV3,
        "Correction authority policy",
    )


def verify_stored_reference_enrollment_snapshot(
    exact_bytes: bytes,
) -> StoredReferenceEnrollmentSnapshotV3:
    """Parse the Phase-B snapshot; trusted storage must reconstruct its parents."""

    return _verify_canonical_bytes(
        exact_bytes,
        StoredReferenceEnrollmentSnapshotV3,
        "Stored Reference enrollment snapshot",
    )


def verify_reference_lookup_state(
    exact_bytes: bytes,
    *,
    candidate: CandidateDiscoveryV2,
    full_audit_aggregate: FullAuditAggregateAttestationV2,
    stored_enrollment_snapshot: StoredReferenceEnrollmentSnapshotV3,
    reference_proof: ReferenceAuthorityProofV2 | None,
) -> ReferenceLookupStateV3:
    parsed = _verify_canonical_bytes(
        exact_bytes,
        ReferenceLookupStateV3,
        "Reference lookup state",
    )
    rebuilt = build_reference_lookup_state(
        candidate=candidate,
        full_audit_aggregate=full_audit_aggregate,
        resolution_key=parsed.resolution_key,
        reference_scope=parsed.reference_scope,
        stored_enrollment_snapshot=stored_enrollment_snapshot,
        lookup_receipt_set_hash=parsed.lookup_receipt_set_hash,
        status=parsed.status,
        reference_proof=reference_proof,
        failure_artifact_hash=parsed.failure_artifact_hash,
    )
    if parsed != rebuilt or exact_bytes != canonical_json_bytes(rebuilt):
        raise CorrectionAuthorityError("Reference lookup state differs from frozen parents")
    return rebuilt


def verify_correction_authority_verdict(
    exact_bytes: bytes,
    *,
    candidate: CandidateDiscoveryV2,
    full_audit_aggregate: FullAuditAggregateAttestationV2,
    recognition_evidence: tuple[RecognitionEvidence, ...],
    authority_mode: CorrectionAuthorityModeV3,
    resolution_key: str,
    reference_scope: ReferenceClaimScope,
    stored_enrollment_snapshot: StoredReferenceEnrollmentSnapshotV3,
    reference_lookup_state: ReferenceLookupStateV3,
    reference_proof: ReferenceAuthorityProofV2 | None,
    human_pronunciation_receipts: tuple[HumanPronunciationRelationReceiptV3, ...],
    human_reference_adjudication: HumanReferenceAdjudicationReceiptV2 | None,
    policy: CorrectionAuthorityPolicyV3,
) -> CorrectionAuthorityVerdictV3:
    parsed = _verify_canonical_bytes(
        exact_bytes,
        CorrectionAuthorityVerdictV3,
        "Correction authority verdict",
    )
    rebuilt = build_correction_authority_verdict(
        candidate=candidate,
        full_audit_aggregate=full_audit_aggregate,
        recognition_evidence=recognition_evidence,
        authority_mode=authority_mode,
        resolution_key=resolution_key,
        reference_scope=reference_scope,
        stored_enrollment_snapshot=stored_enrollment_snapshot,
        reference_lookup_state=reference_lookup_state,
        reference_proof=reference_proof,
        human_pronunciation_receipts=human_pronunciation_receipts,
        human_reference_adjudication=human_reference_adjudication,
        policy=policy,
    )
    if parsed != rebuilt or exact_bytes != canonical_json_bytes(rebuilt):
        raise CorrectionAuthorityError("Correction authority verdict differs from frozen parents")
    return rebuilt


__all__ = [
    "ArtifactBindingV3",
    "CandidateDiscoveryV2",
    "CorrectionAuthorityActionV3",
    "CorrectionAuthorityError",
    "CorrectionAuthorityModeV3",
    "CorrectionAuthorityPolicyV3",
    "CorrectionAuthorityReasonV3",
    "CorrectionAuthorityVerdictV3",
    "HumanPronunciationRelationReceiptV3",
    "PronunciationCompatibilityV3",
    "PronunciationPairDiscriminabilityV3",
    "PronunciationRelationReasonV3",
    "ReferenceLookupStateV3",
    "ReferenceLookupStatusV3",
    "StoredReferenceEnrollmentSnapshotV3",
    "StoredReferenceEnrollmentV3",
    "build_correction_authority_verdict",
    "build_human_pronunciation_relation_receipt",
    "build_reference_lookup_state",
    "correction_authority_policy_bytes",
    "correction_authority_verdict_bytes",
    "default_correction_authority_policy",
    "pronunciation_relation_receipt_bytes",
    "reference_lookup_state_bytes",
    "stored_reference_enrollment_snapshot_bytes",
    "verify_correction_authority_policy",
    "verify_correction_authority_verdict",
    "verify_pronunciation_relation_receipt",
    "verify_reference_lookup_state",
    "verify_stored_reference_enrollment_snapshot",
]
