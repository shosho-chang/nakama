"""Closed, immutable contracts for Podcast Subtitle V2.

The V2 pipeline deliberately separates recognition evidence, the canonical
transcript, semantic grouping, and rendered subtitle projections.  Rendered
cue numbers and whitespace are never persistent identity or transcript truth.
Canonical integrity is the ordered sequence of ``(token_id, lexeme)`` pairs.

Closed-set extension protocol
-----------------------------
Every ``Literal`` in this module is closed for ``schema_version=1``.  Adding a
member, changing a field's meaning, or weakening an invariant requires a
schema-version bump and an explicit downstream migration.  Silent extension
is forbidden.

This module is pure Pydantic: no ASR, GPU, LLM, storage, or domain service
imports are permitted.
"""

from __future__ import annotations

import hashlib
import json
import re
import unicodedata
from datetime import date, timezone
from typing import Annotated, Literal, TypeAlias

from pydantic import (
    AwareDatetime,
    BaseModel,
    ConfigDict,
    Field,
    StrictBool,
    StringConstraints,
    field_validator,
    model_validator,
)

SchemaVersion = Literal[1]
Sha256 = Annotated[str, StringConstraints(pattern=r"^[0-9a-f]{64}$")]

TranscriptStatus = Literal["draft", "accepted"]
ReceiptStatus = Literal["draft", "accepted"]
IssueStatus = Literal["unresolved", "resolved", "waived"]
GenerationWarningStatus = Literal["requires_full_audit", "mitigated"]
RiskSeverity = Literal["info", "low", "medium", "high", "blocking"]
RiskKind = Literal["text", "timing", "speaker", "semantic", "projection", "provenance"]
DecisionAction = Literal[
    "confirm_original",
    "replace",
    "accept_candidate",
    "reject_candidate",
    "defer",
]
DecisionActorKind = Literal["human", "model", "rule"]
SemanticUnitKind = Literal[
    "token",
    "term",
    "name",
    "phrase",
    "clause",
    "sentence",
    "speaker_turn",
    "boundary_pair",
]
SemanticBoundaryRelation = Literal["forbidden", "discouraged", "preferred", "neutral"]
BoundaryChannelRelation = Literal[
    "mandatory",
    "forbidden",
    "discouraged",
    "preferred",
    "neutral",
    "uncertain",
]
ProtectedSemanticKind = Literal[
    "name",
    "term",
    "book_title",
    "report_title",
    "code_switch",
    "url",
]
ProtectedSemanticSource = Literal[
    "policy_vocabulary",
    "retrieved_reference_metadata",
]
BoundaryReasonCode = Literal[
    "speaker_change",
    "semantic_hard_unit",
    "semantic_soft_crossing",
    "semantic_unit_end",
    "protected_name",
    "protected_term",
    "protected_book_title",
    "protected_report_title",
    "protected_code_switch",
    "protected_url",
    "numeric_decimal",
    "numeric_percent",
    "numeric_range",
    "numeric_date_time",
    "numeric_version",
    "numeric_unit",
    "classifier_phrase",
    "bounded_preposition_structure",
    "closed_class_no_orphan",
    "negation_no_orphan",
    "connector_no_orphan",
    "paired_punctuation",
    "punctuation_opener",
    "punctuation_closer",
    "filler_no_independent_cue",
    "self_repair_cohesion",
    "asr_timestamp_gap_not_prosody",
    "unmatched_punctuation",
    "no_semantic_signal",
]
FindingSeverity = Literal["info", "warning", "error", "blocking"]
GenerationStatus = Literal["draft", "running", "complete", "failed"]
CorrectionMode = Literal["full_audit", "targeted_review"]
AudioAuditStatus = Literal["confirmed", "finding", "unresolved"]
ArbitrationReceiptStatus = Literal["accepted", "rejected", "unresolved"]
NormalizationOutcome = Literal["completed", "failed", "unknown"]
NormalizationProductionSource = Literal["created", "reused", "legacy_unknown"]
NormalizationSourceBindingMethod = Literal[
    "upload_in_current_request",
    "provider_checksum",
    "verified_upstream_handoff",
    "heuristic_filename_duration",
    "legacy_unknown",
]
NormalizationAlignmentMethod = Literal[
    "cross_correlation",
    "identity",
    "fixed_seconds",
    "not_requested",
    "legacy_unverified",
]
NormalizationParameterScope = Literal["algorithm", "output", "adapter"]
ReferenceKind = Literal[
    "book",
    "research_report",
    "interview_outline",
    "transcript",
    "knowledge_base",
    "other",
]
ReferenceTrustTier = Literal["authoritative", "curated", "contextual"]
ReferenceAuthorityRole = Literal[
    "published_author_book",
    "owner_final_report",
    "owner_approved_outline_glossary",
    "curated_reference",
    "contextual_reference",
]
ReferenceAuthorityScope = Literal[
    "source_title",
    "source_author",
    "literal_terminology",
    "verbatim_source_text",
    "owner_approved_glossary_spelling",
]
ReferenceAuthorityPrincipalKind = Literal[
    "person",
    "organization",
    "publication",
    "report",
    "episode",
    "other",
]
ReferenceAuthorityVersionStatus = Literal["draft", "active", "superseded"]
ReferenceAuthorityReleaseStatus = Literal[
    "unreleased",
    "published",
    "final",
    "approved",
    "not_applicable",
]
ReferenceAuthorityAttestationProvenance = Literal[
    "none",
    "publisher_record",
    "author_record",
    "owner_record",
    "owner_approval_record",
]
ReferenceLocatorKind = Literal[
    "page",
    "chapter",
    "section",
    "heading",
    "paragraph",
    "timestamp",
    "uri_fragment",
    "other",
]
ReferenceRetrievalStatus = Literal["completed", "failed"]
ReferenceQueryContextAlgorithm = Literal["canonical_adjacent_context"]
ReferenceQueryContextAlgorithmVersion = Literal["unicode-scalar-v1"]
ReferenceRetrievalSupportKind = Literal[
    "candidate_term_exact",
    "query_exact",
    "lexical",
    "phonetic_exact",
    "phonetic_near",
]
ReferenceSourceFormat = Literal["markdown", "text", "pdf", "docx", "epub"]
ReferenceOffsetUnit = Literal["unicode_scalar_v1"]
SpeechCoverageStatus = Literal["completed", "failed"]
SpeechActivityMethod = Literal[
    "ffmpeg_silencedetect_complement_v1",
    "fixture_intervals_v1",
]
DecisionEvidenceBasis = Literal[
    "audio",
    "audio_and_reference",
    "reference_only",
    "administrative",
]
CanonicalTimingBasis = Literal[
    "recognition",
    "coarse_span",
    "forced_alignment",
    "human_alignment",
]
CanonicalSpanAlignment = Literal["lexeme_exact", "coarse"]
ReplacementAlignmentMethod = Literal["forced_alignment", "human_alignment"]
SpanAuditCategory = Literal[
    "lexical_fidelity",
    "reference_name_term",
    "numeric_expression",
    "negation_polarity",
    "latin_code_switch",
    "acronym_initialism",
    "repetition_disfluency",
    "confidence_integrity",
    "speaker_identity",
]
BoundaryAuditCategory = Literal[
    "cross_span_fidelity",
    "chunk_seam",
    "speech_coverage",
    "speaker_transition",
    "repetition_self_repair",
]
AuditTargetKind = Literal["span", "boundary"]
AuditApplicability = Literal["required", "not_applicable", "unavailable"]
AuditApplicabilityReason = Literal[
    "baseline_required",
    "pattern_present",
    "pattern_absent",
    "no_sources_enrolled",
    "no_reference_support",
    "verified_clean",
    "reference_completed",
    "upstream_missing",
    "upstream_failed",
    "endpoint_not_applicable",
    "no_matching_seam",
    "explicit_evidence_available",
]
AuditReferenceInputStatus = Literal[
    "not_enrolled",
    "completed",
    "failed",
    "incomplete",
]
AuditCoverageInputStatus = Literal["completed", "failed", "absent"]
AuditSeamInputStatus = Literal["unchunked", "complete", "conflicted", "absent"]
AuditBoundaryInputStatus = Literal["verified", "absent"]
AuditMetadataInputStatus = Literal["complete", "partial", "absent"]
CandidateSignalAuthority = Literal["review_trigger_only"]
CandidateSignalEvidenceKind = Literal[
    "deterministic_text",
    "recognition_metadata",
    "reference_evidence",
    "speech_coverage",
    "semantic_constraint",
    "seam_observation",
    "speaker_metadata",
]
CandidateSourceBindingKind = Literal[
    "canonical_transcript",
    "recognition_evidence",
    "recognition_raw_artifact",
    "recognition_evidence_set",
    "reference_retrieval_receipt_set",
    "reference_retrieval_receipt",
    "reference_source_artifact",
    "reference_excerpt",
    "speech_coverage_receipt",
    "speech_coverage_raw_artifact",
    "boundary_constraint_receipt",
    "seam_evidence",
    "seam_observation",
]
CandidateSignalCode = Literal[
    "reference_candidate_term_exact",
    "reference_phonetic_support_without_exact_candidate",
    "reference_lexical_support_without_exact_candidate",
    "reference_retrieval_failed",
    "reference_retrieval_incomplete",
    "numeric_arabic_digits_detected",
    "numeric_han_number_detected",
    "numeric_decimal_detected",
    "numeric_percent_detected",
    "numeric_range_detected",
    "numeric_date_time_detected",
    "numeric_version_detected",
    "numeric_unit_or_classifier_detected",
    "negation_detected",
    "latin_code_switch_detected",
    "acronym_initialism_detected",
    "repetition_detected",
    "filler_detected",
    "self_repair_detected",
    "recognition_confidence_missing",
    "recognition_low_confidence",
    "speaker_identity_missing",
    "speaker_identity_mixed",
    "speaker_identity_disagreement",
    "speaker_change_detected",
    "semantic_boundary_uncertain",
    "semantic_boundary_forbidden",
    "chunk_seam_duplicate",
    "chunk_seam_conflict",
    "chunk_seam_unavailable",
    "speech_coverage_uncovered_inside_span",
    "speech_coverage_uncovered_at_boundary",
    "speech_coverage_unavailable",
    "speaker_transition_ambiguous",
    "cross_span_repetition_detected",
    "cross_span_self_repair_detected",
]
CORRECTION_AUDIT_SPAN_CATEGORIES: tuple[SpanAuditCategory, ...] = (
    "lexical_fidelity",
    "reference_name_term",
    "numeric_expression",
    "negation_polarity",
    "latin_code_switch",
    "acronym_initialism",
    "repetition_disfluency",
    "confidence_integrity",
    "speaker_identity",
)
CORRECTION_AUDIT_BOUNDARY_CATEGORIES: tuple[BoundaryAuditCategory, ...] = (
    "cross_span_fidelity",
    "chunk_seam",
    "speech_coverage",
    "speaker_transition",
    "repetition_self_repair",
)
ParameterScalar: TypeAlias = str | int | float | bool | None


class _Contract(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


def _ordered_token_pairs(
    tokens: tuple[CanonicalToken, ...] | list[CanonicalToken],
) -> list[list[str]]:
    return [[token.id, token.text] for token in tokens]


def canonical_content_hash(tokens: tuple[CanonicalToken, ...] | list[CanonicalToken]) -> str:
    """Hash canonical truth without rendered whitespace, cues, or timestamps."""

    payload = json.dumps(
        _ordered_token_pairs(tokens), ensure_ascii=False, separators=(",", ":")
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def token_sequence_hash(token_ids: tuple[str, ...] | list[str]) -> str:
    """Hash an ordered token-id projection, independent of cue numbering."""

    payload = json.dumps(list(token_ids), ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def _hash_json(payload: object) -> str:
    encoded = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _validate_nonblank(value: str, field_name: str) -> str:
    if not value.strip():
        raise ValueError(f"{field_name} must not be blank")
    return value


_BIDI_CONTROL_CHARACTERS = frozenset(
    {
        "\u061c",
        "\u200e",
        "\u200f",
        "\u202a",
        "\u202b",
        "\u202c",
        "\u202d",
        "\u202e",
        "\u2066",
        "\u2067",
        "\u2068",
        "\u2069",
    }
)


def _validate_bounded_metadata(value: str, field_name: str, *, maximum: int) -> str:
    value = _validate_nonblank(value, field_name)
    if len(value) > maximum:
        raise ValueError(f"{field_name} exceeds {maximum} Unicode scalars")
    if any(
        unicodedata.category(character) in {"Cc", "Cs"} or character in _BIDI_CONTROL_CHARACTERS
        for character in value
    ):
        raise ValueError(f"{field_name} contains forbidden control characters")
    return value


def _validate_subtitle_text(value: str, field_name: str) -> str:
    """Reject hidden physical lines and directional/control injection.

    Subtitle lexemes may contain ordinary Unicode spaces, but one logical token
    or rendered line must never smuggle CR/LF, line/paragraph separators,
    surrogates, or formatting controls into the physical SRT representation.
    """

    value = _validate_nonblank(value, field_name)
    forbidden = {
        character
        for character in value
        if unicodedata.category(character) in {"Cc", "Cf", "Cs", "Zl", "Zp"}
    }
    if forbidden:
        points = ", ".join(f"U+{ord(character):04X}" for character in sorted(forbidden))
        raise ValueError(f"{field_name} contains forbidden subtitle controls: {points}")
    return value


def _validate_timed_tokens(tokens: tuple[EvidenceToken, ...]) -> None:
    seen: set[str] = set()
    previous_end = 0
    for index, token in enumerate(tokens):
        if token.id in seen:
            raise ValueError(f"duplicate token id: {token.id!r}")
        seen.add(token.id)
        if index and token.start_ms < previous_end:
            raise ValueError(
                f"tokens must be monotonic and non-overlapping: {token.id!r} "
                f"starts at {token.start_ms} before prior end {previous_end}"
            )
        previous_end = token.end_ms


class ArtifactDigest(_Contract):
    """Content-addressed immutable artifact reference."""

    schema_version: SchemaVersion = 1
    uri: str
    sha256: Sha256
    size_bytes: int = Field(ge=0)

    @field_validator("uri")
    @classmethod
    def _uri_not_blank(cls, value: str) -> str:
        return _validate_nonblank(value, "uri")


class NormalizationParameter(_Contract):
    """One exact scalar setting submitted to a normalization provider."""

    schema_version: SchemaVersion = 1
    scope: NormalizationParameterScope
    name: str
    value: ParameterScalar

    @field_validator("name")
    @classmethod
    def _name_not_blank(cls, value: str) -> str:
        return _validate_nonblank(value, "name")


class NormalizationMetric(_Contract):
    """One immutable measurement used to verify the normalized audio clock."""

    schema_version: SchemaVersion = 1
    name: str
    value: float = Field(allow_inf_nan=False)
    unit: str

    @field_validator("name", "unit")
    @classmethod
    def _metric_text_not_blank(cls, value: str, info: object) -> str:
        return _validate_nonblank(value, getattr(info, "field_name", "value"))


def normalization_settings_hash(
    parameters: tuple[NormalizationParameter, ...] | list[NormalizationParameter],
    *,
    preset: str | None,
) -> str:
    """Hash the settings the provider actually used, never current defaults."""

    ordered = sorted(
        (
            {
                "scope": parameter.scope,
                "name": parameter.name,
                "value": parameter.value,
            }
            for parameter in parameters
        ),
        key=lambda item: (item["scope"], item["name"]),
    )
    return _hash_json({"parameters": ordered, "preset": preset})


def normalization_receipt_content_hash(receipt: NormalizationReceipt) -> str:
    """Hash normalization lineage while excluding machine-local artifact URIs."""

    return _hash_json(
        {
            "schema_version": receipt.schema_version,
            "status": receipt.status,
            "provider": receipt.provider,
            "production_id": receipt.production_id,
            "production_source": receipt.production_source,
            "source_identity_verified": receipt.source_identity_verified,
            "source_binding_method": receipt.source_binding_method,
            "provider_outcome": receipt.provider_outcome,
            "provider_status_code": receipt.provider_status_code,
            "provider_status": receipt.provider_status,
            "source": {
                "sha256": receipt.source.sha256,
                "size_bytes": receipt.source.size_bytes,
            },
            "normalized": {
                "sha256": receipt.normalized.sha256,
                "size_bytes": receipt.normalized.size_bytes,
            },
            "source_duration_ms": receipt.source_duration_ms,
            "normalized_duration_ms": receipt.normalized_duration_ms,
            "request_started_at": (
                receipt.request_started_at.isoformat()
                if receipt.request_started_at is not None
                else None
            ),
            "completed_at": (
                receipt.completed_at.isoformat() if receipt.completed_at is not None else None
            ),
            "provider_created_at": (
                receipt.provider_created_at.isoformat()
                if receipt.provider_created_at is not None
                else None
            ),
            "provider_completed_at": (
                receipt.provider_completed_at.isoformat()
                if receipt.provider_completed_at is not None
                else None
            ),
            "requested_parameters": [
                parameter.model_dump(mode="json") for parameter in receipt.requested_parameters
            ],
            "submitted_parameters": (
                [parameter.model_dump(mode="json") for parameter in receipt.submitted_parameters]
                if receipt.submitted_parameters is not None
                else None
            ),
            "preset": receipt.preset,
            "settings_hash": receipt.settings_hash,
            "reuse_reason": receipt.reuse_reason,
            "original_production_id": receipt.original_production_id,
            "clock_map": receipt.clock_map.model_dump(mode="json"),
            "alignment_method": receipt.alignment_method,
            "alignment_metrics": [
                metric.model_dump(mode="json") for metric in receipt.alignment_metrics
            ],
            "alignment_failure_reason": receipt.alignment_failure_reason,
        }
    )


class ReferenceAuthorityPrincipal(_Contract):
    """Stable identity plus the exact human-readable metadata it binds."""

    schema_version: SchemaVersion = 1
    kind: ReferenceAuthorityPrincipalKind
    stable_id: str
    display_name: str

    @field_validator("stable_id", "display_name")
    @classmethod
    def _principal_metadata(cls, value: str, info: object) -> str:
        return _validate_bounded_metadata(
            value,
            getattr(info, "field_name", "value"),
            maximum=512,
        )


class ReferenceAuthorityVersionRef(_Contract):
    """One exact predecessor in a logical source's version lineage."""

    schema_version: SchemaVersion = 1
    logical_source_id: str
    version_id: str

    @field_validator("logical_source_id", "version_id")
    @classmethod
    def _version_ref_metadata(cls, value: str, info: object) -> str:
        return _validate_bounded_metadata(
            value,
            getattr(info, "field_name", "value"),
            maximum=256,
        )


class ReferenceAuthorityAttestation(_Contract):
    """Typed provenance for an operator's authority confirmation claim."""

    schema_version: SchemaVersion = 1
    confirmed: StrictBool
    provenance: ReferenceAuthorityAttestationProvenance
    attestor: ReferenceAuthorityPrincipal | None
    record_sha256: Sha256 | None

    @model_validator(mode="after")
    def _confirmation_has_exact_provenance(self) -> ReferenceAuthorityAttestation:
        if self.confirmed:
            if self.provenance == "none" or self.attestor is None or self.record_sha256 is None:
                raise ValueError(
                    "confirmed Reference authority requires typed provenance, attestor, "
                    "and record_sha256"
                )
        elif (
            self.provenance != "none" or self.attestor is not None or self.record_sha256 is not None
        ):
            raise ValueError("unconfirmed Reference authority cannot carry attestation provenance")
        return self


class ReferenceAuthorityDescriptor(_Contract):
    """Content-addressed, scope-limited authority for one immutable source version."""

    schema_version: SchemaVersion = 1
    logical_source_id: str
    version_id: str
    version_status: ReferenceAuthorityVersionStatus
    release_status: ReferenceAuthorityReleaseStatus
    supersedes: tuple[ReferenceAuthorityVersionRef, ...] = ()
    source_kind: ReferenceKind
    trust_tier: ReferenceTrustTier
    role: ReferenceAuthorityRole
    subject: ReferenceAuthorityPrincipal
    owner: ReferenceAuthorityPrincipal | None
    allowed_scopes: tuple[ReferenceAuthorityScope, ...]
    attestation: ReferenceAuthorityAttestation
    content_hash: Sha256 | None = None

    @field_validator("logical_source_id", "version_id")
    @classmethod
    def _authority_metadata(cls, value: str, info: object) -> str:
        return _validate_bounded_metadata(
            value,
            getattr(info, "field_name", "value"),
            maximum=256,
        )

    @model_validator(mode="after")
    def _closed_authority_matrix(self) -> ReferenceAuthorityDescriptor:
        if self.source_kind == "transcript":
            raise ValueError(
                "transcript belongs to Recognition Evidence and cannot be Reference authority"
            )
        predecessor_keys = [(item.logical_source_id, item.version_id) for item in self.supersedes]
        if len(set(predecessor_keys)) != len(predecessor_keys):
            raise ValueError("Reference authority supersedes entries must be unique")
        if predecessor_keys != sorted(predecessor_keys):
            raise ValueError("Reference authority supersedes entries must be canonically ordered")
        for logical_source_id, version_id in predecessor_keys:
            if logical_source_id != self.logical_source_id:
                raise ValueError("Reference authority cannot supersede another logical source")
            if version_id == self.version_id:
                raise ValueError("Reference authority cannot supersede itself")

        book_scopes: tuple[ReferenceAuthorityScope, ...] = (
            "source_title",
            "source_author",
            "literal_terminology",
            "verbatim_source_text",
        )
        report_scopes: tuple[ReferenceAuthorityScope, ...] = (
            "source_title",
            "literal_terminology",
            "verbatim_source_text",
        )
        outline_scopes: tuple[ReferenceAuthorityScope, ...] = ("owner_approved_glossary_spelling",)
        if self.role == "published_author_book":
            if (
                self.source_kind != "book"
                or self.trust_tier != "authoritative"
                or self.version_status != "active"
                or self.release_status != "published"
                or self.subject.kind != "publication"
                or self.owner is None
                or self.allowed_scopes != book_scopes
                or not self.attestation.confirmed
                or self.attestation.provenance not in {"publisher_record", "author_record"}
            ):
                raise ValueError("published_author_book authority matrix mismatch")
            if (
                self.attestation.provenance == "author_record"
                and self.attestation.attestor != self.owner
            ):
                raise ValueError("author_record attestor must equal the bound book owner")
        elif self.role == "owner_final_report":
            if (
                self.source_kind != "research_report"
                or self.trust_tier != "authoritative"
                or self.version_status != "active"
                or self.release_status != "final"
                or self.subject.kind != "report"
                or self.owner is None
                or self.allowed_scopes != report_scopes
                or not self.attestation.confirmed
                or self.attestation.provenance != "owner_record"
                or self.attestation.attestor != self.owner
            ):
                raise ValueError("owner_final_report authority matrix mismatch")
        elif self.role == "owner_approved_outline_glossary":
            if (
                self.source_kind != "interview_outline"
                or self.trust_tier != "curated"
                or self.version_status != "active"
                or self.release_status != "approved"
                or self.subject.kind != "episode"
                or self.owner is None
                or self.allowed_scopes != outline_scopes
                or not self.attestation.confirmed
                or self.attestation.provenance != "owner_approval_record"
                or self.attestation.attestor != self.owner
            ):
                raise ValueError("owner_approved_outline_glossary authority matrix mismatch")
        elif self.role == "curated_reference":
            if (
                self.trust_tier != "curated"
                or self.source_kind == "interview_outline"
                or self.allowed_scopes
                or self.attestation.confirmed
            ):
                raise ValueError(
                    "curated_reference cannot carry mutation authority; interview_outline "
                    "defaults to contextual_reference unless owner-approved glossary scope "
                    "is explicitly attested"
                )
        elif self.role == "contextual_reference":
            if self.trust_tier != "contextual" or self.allowed_scopes or self.attestation.confirmed:
                raise ValueError("contextual_reference cannot carry mutation authority")

        if self.version_status != "active" and (
            self.trust_tier != "contextual"
            or self.role != "contextual_reference"
            or self.allowed_scopes
            or self.attestation.confirmed
        ):
            raise ValueError(
                "draft or superseded Reference versions cannot masquerade as active authority"
            )

        payload = self.model_dump(mode="json", exclude={"content_hash"})
        expected_hash = _hash_json(payload)
        if self.content_hash is None:
            object.__setattr__(self, "content_hash", expected_hash)
        elif self.content_hash != expected_hash:
            raise ValueError("Reference authority descriptor content_hash mismatch")
        return self


def validate_reference_authority_binding(
    authority: ReferenceAuthorityDescriptor,
    *,
    kind: ReferenceKind,
    title: str,
    author: str | None,
    publisher: str | None,
    version: str,
    trust_tier: ReferenceTrustTier,
) -> None:
    """Fail closed when source metadata and its authority descriptor diverge."""

    # ``model_copy(update=...)`` intentionally skips Pydantic validation.  Reparse
    # the complete descriptor at every boundary so an in-memory forged object is
    # no weaker than JSON replay.
    reparsed = ReferenceAuthorityDescriptor.model_validate(
        authority.model_dump(mode="json", exclude_none=False)
    )
    if reparsed != authority:
        raise ValueError("Reference authority descriptor is not canonically valid")
    if authority.source_kind != kind:
        raise ValueError("Reference authority source_kind differs from source metadata")
    if authority.trust_tier != trust_tier:
        raise ValueError("Reference authority trust_tier differs from source metadata")
    if authority.version_id != version:
        raise ValueError("Reference authority version_id differs from source metadata")
    if authority.subject.display_name != title:
        raise ValueError("Reference authority subject differs from source title")
    if author is not None:
        if authority.owner is None or authority.owner.display_name != author:
            raise ValueError("Reference authority owner differs from source author")
    elif authority.role in {
        "published_author_book",
        "owner_final_report",
        "owner_approved_outline_glossary",
    }:
        raise ValueError("authority-bearing Reference source requires author/owner metadata")
    if authority.role == "published_author_book" and authority.attestation.provenance == (
        "publisher_record"
    ):
        if publisher is None or authority.attestation.attestor is None:
            raise ValueError("publisher-attested book authority requires publisher metadata")
        if authority.attestation.attestor.display_name != publisher:
            raise ValueError("book authority attestor differs from source publisher")


class ReferenceArtifact(_Contract):
    """Immutable binary source plus its deterministic extracted-text snapshot."""

    schema_version: Literal[2] = 2
    source_id: str
    kind: ReferenceKind
    source_format: ReferenceSourceFormat
    digest: ArtifactDigest
    extracted_text: ArtifactDigest
    extractor_name: str
    extractor_version: str
    extractor_config_hash: Sha256
    extractor_code_hash: Sha256
    extractor_runtime_hash: Sha256
    offset_unit: ReferenceOffsetUnit
    extraction_block_count: int = Field(gt=0)
    title: str
    author: str | None = None
    publisher: str | None = None
    version: str
    document_date: str = "undated"
    enrollment_manifest_sha256: Sha256 | None = None
    trust_tier: ReferenceTrustTier
    authority: ReferenceAuthorityDescriptor

    @field_validator(
        "source_id",
        "extractor_name",
        "extractor_version",
        "title",
        "version",
        "document_date",
    )
    @classmethod
    def _required_metadata(cls, value: str, info: object) -> str:
        field_name = getattr(info, "field_name", "value")
        maximum = 512 if field_name == "title" else 256
        normalized = _validate_bounded_metadata(value, field_name, maximum=maximum)
        if field_name == "document_date" and normalized != "undated":
            try:
                parsed = date.fromisoformat(normalized)
            except ValueError as exc:
                raise ValueError("document_date must be YYYY-MM-DD or 'undated'") from exc
            if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", normalized) or (
                parsed.isoformat() != normalized
            ):
                raise ValueError("document_date must be YYYY-MM-DD or 'undated'")
        return normalized

    @field_validator("author", "publisher")
    @classmethod
    def _optional_metadata_not_blank(cls, value: str | None, info: object) -> str | None:
        if value is not None:
            return _validate_bounded_metadata(
                value,
                getattr(info, "field_name", "value"),
                maximum=512,
            )
        return value

    @model_validator(mode="after")
    def _authority_matches_artifact(self) -> ReferenceArtifact:
        validate_reference_authority_binding(
            self.authority,
            kind=self.kind,
            title=self.title,
            author=self.author,
            publisher=self.publisher,
            version=self.version,
            trust_tier=self.trust_tier,
        )
        return self


class ReferenceLocatorPart(_Contract):
    """One ordered component of a locator back to the exact source passage."""

    schema_version: SchemaVersion = 1
    kind: ReferenceLocatorKind
    value: str

    @field_validator("value")
    @classmethod
    def _value_not_blank(cls, value: str) -> str:
        return _validate_bounded_metadata(value, "value", maximum=1024)

    @model_validator(mode="after")
    def _reserved_range_syntax_is_forbidden(self) -> ReferenceLocatorPart:
        if self.kind == "other" and self.value.startswith("chars="):
            raise ValueError(
                "Reference Locator other:chars= is reserved; use excerpt_start/excerpt_end"
            )
        return self


class ReferenceLocator(_Contract):
    """Structured source locator, preserving chapter/page/paragraph hierarchy."""

    schema_version: SchemaVersion = 1
    parts: tuple[ReferenceLocatorPart, ...]

    @model_validator(mode="after")
    def _parts_are_nonempty_and_unique(self) -> ReferenceLocator:
        if not self.parts:
            raise ValueError("reference locator requires at least one part")
        keys = [(part.kind, part.value) for part in self.parts]
        if len(set(keys)) != len(keys):
            raise ValueError("reference locator parts must be unique")
        return self


class ReferenceTextBlock(_Contract):
    """One canonical block in a deterministic extracted-text snapshot."""

    schema_version: SchemaVersion = 1
    index: int = Field(ge=0)
    locator: ReferenceLocator
    text: str
    text_hash: Sha256

    @field_validator("text")
    @classmethod
    def _text_not_blank(cls, value: str) -> str:
        value = _validate_nonblank(value, "text")
        if len(value) > 1_000_000:
            raise ValueError("reference block text exceeds 1000000 Unicode scalars")
        if any(unicodedata.category(character) == "Cs" for character in value):
            raise ValueError("reference block text contains an unpaired surrogate")
        return value

    @model_validator(mode="after")
    def _text_matches_hash(self) -> ReferenceTextBlock:
        expected = hashlib.sha256(self.text.encode("utf-8")).hexdigest()
        if self.text_hash != expected:
            raise ValueError(
                f"reference text block hash mismatch: expected {expected}, got {self.text_hash}"
            )
        return self


class ReferenceExtractionSnapshot(_Contract):
    """Canonical parser output used to prove excerpt membership in a source."""

    schema_version: SchemaVersion = 1
    source_sha256: Sha256
    source_size_bytes: int = Field(ge=0)
    source_format: ReferenceSourceFormat
    decoded_text_sha256: Sha256 | None = None
    extractor_name: str
    extractor_version: str
    extractor_config_hash: Sha256
    extractor_code_hash: Sha256
    extractor_runtime_hash: Sha256
    offset_unit: ReferenceOffsetUnit
    blocks: tuple[ReferenceTextBlock, ...]

    @field_validator("extractor_name", "extractor_version")
    @classmethod
    def _extractor_text_not_blank(cls, value: str, info: object) -> str:
        return _validate_bounded_metadata(
            value,
            getattr(info, "field_name", "value"),
            maximum=256,
        )

    @model_validator(mode="after")
    def _blocks_are_nonempty_and_sequential(self) -> ReferenceExtractionSnapshot:
        if not self.blocks:
            raise ValueError("reference extraction snapshot requires blocks")
        indices = [block.index for block in self.blocks]
        if indices != list(range(len(self.blocks))):
            raise ValueError("reference extraction block indices must be sequential from zero")
        locators = [
            tuple((part.kind, part.value) for part in block.locator.parts) for block in self.blocks
        ]
        if len(set(locators)) != len(locators):
            raise ValueError("reference extraction block locators must be unique")
        if self.source_format in {"markdown", "text"} and self.decoded_text_sha256 is None:
            raise ValueError("text reference extraction requires decoded_text_sha256")
        if self.source_format not in {"markdown", "text"} and self.decoded_text_sha256 is not None:
            raise ValueError("binary reference extraction cannot declare decoded_text_sha256")
        return self


class ReferenceEvidence(_Contract):
    """Minimal verbatim passage; offsets are Unicode scalar indices (v1)."""

    schema_version: Literal[2] = 2
    id: str
    artifact: ReferenceArtifact
    locator: ReferenceLocator
    extraction_block_index: int = Field(ge=0)
    extraction_block_hash: Sha256
    excerpt_start: int = Field(ge=0)
    excerpt_end: int = Field(gt=0)
    excerpt: str
    excerpt_hash: Sha256

    @field_validator("id", "excerpt")
    @classmethod
    def _required_text(cls, value: str, info: object) -> str:
        field_name = getattr(info, "field_name", "value")
        maximum = 256 if field_name == "id" else 4096
        if field_name == "id":
            return _validate_bounded_metadata(value, field_name, maximum=maximum)
        value = _validate_nonblank(value, field_name)
        if len(value) > maximum:
            raise ValueError(f"{field_name} exceeds {maximum} Unicode scalars")
        if any(unicodedata.category(character) == "Cs" for character in value):
            raise ValueError(f"{field_name} contains an unpaired surrogate")
        return value

    @model_validator(mode="after")
    def _excerpt_matches_hash(self) -> ReferenceEvidence:
        if self.excerpt_end <= self.excerpt_start:
            raise ValueError("reference excerpt_end must be greater than excerpt_start")
        if self.excerpt_end - self.excerpt_start != len(self.excerpt):
            raise ValueError(
                "reference excerpt range length must equal the canonical excerpt length"
            )
        if self.extraction_block_index >= self.artifact.extraction_block_count:
            raise ValueError("reference extraction_block_index is outside the artifact")
        expected = hashlib.sha256(self.excerpt.encode("utf-8")).hexdigest()
        if self.excerpt_hash != expected:
            raise ValueError(
                f"reference excerpt hash mismatch: expected {expected}, got {self.excerpt_hash}"
            )
        return self


def reference_evidence_set_hash(
    evidence: tuple[ReferenceEvidence, ...] | list[ReferenceEvidence],
) -> str:
    """Hash a Reference Evidence set without mutable scores or caller ordering."""

    ids = [item.id for item in evidence]
    if len(set(ids)) != len(ids):
        raise ValueError("Reference Evidence set contains duplicate IDs")
    records = [
        {
            "id": item.id,
            "source_id": item.artifact.source_id,
            "source_kind": item.artifact.kind,
            "source_format": item.artifact.source_format,
            "source_sha256": item.artifact.digest.sha256,
            "source_size_bytes": item.artifact.digest.size_bytes,
            "source_version": item.artifact.version,
            "source_title": item.artifact.title,
            "source_author": item.artifact.author,
            "source_publisher": item.artifact.publisher,
            "extracted_text_sha256": item.artifact.extracted_text.sha256,
            "extracted_text_size_bytes": item.artifact.extracted_text.size_bytes,
            "extractor_name": item.artifact.extractor_name,
            "extractor_version": item.artifact.extractor_version,
            "extractor_config_hash": item.artifact.extractor_config_hash,
            "extractor_code_hash": item.artifact.extractor_code_hash,
            "extractor_runtime_hash": item.artifact.extractor_runtime_hash,
            "offset_unit": item.artifact.offset_unit,
            "extraction_block_count": item.artifact.extraction_block_count,
            "extraction_block_index": item.extraction_block_index,
            "extraction_block_hash": item.extraction_block_hash,
            "excerpt_start": item.excerpt_start,
            "excerpt_end": item.excerpt_end,
            "locator": [part.model_dump(mode="json") for part in item.locator.parts],
            "excerpt_hash": item.excerpt_hash,
            "trust_tier": item.artifact.trust_tier,
            "authority": item.artifact.authority.model_dump(mode="json"),
        }
        for item in evidence
    ]
    records.sort(
        key=lambda item: (
            item["id"],
            item["source_id"],
            item["source_sha256"],
            item["excerpt_hash"],
        )
    )
    return _hash_json(records)


EMPTY_REFERENCE_EVIDENCE_HASH = reference_evidence_set_hash(())


class ReferenceRetrievalPolicySnapshot(_Contract):
    """Complete, reversible policy used to build and execute one query set.

    The ordered vocabulary is intentionally stored rather than represented by
    only a hash.  A fresh process must be able to reconstruct every per-anchor
    request from the Canonical Transcript without consulting operator state.
    """

    schema_version: SchemaVersion = 1
    left_unicode_scalar_budget: int = Field(ge=0)
    right_unicode_scalar_budget: int = Field(ge=0)
    max_adjacent_spans_per_side: int = Field(ge=0)
    max_anchor_unicode_scalars: int = Field(ge=1)
    max_query_unicode_scalars: int = Field(ge=1)
    stop_at_known_speaker_change: bool
    max_adjacent_gap_ms: int = Field(ge=0)
    max_candidate_terms: int = Field(ge=1)
    max_results: int = Field(ge=1)
    retrievable_codes: tuple[str, ...]
    vocabulary: tuple[str, ...]

    @field_validator("retrievable_codes", "vocabulary")
    @classmethod
    def _policy_text_is_exact_and_unique(
        cls, value: tuple[str, ...], info: object
    ) -> tuple[str, ...]:
        label = getattr(info, "field_name", "values")
        folded: set[str] = set()
        for item in value:
            _validate_bounded_metadata(item, label, maximum=512)
            key = item.casefold()
            if key in folded:
                raise ValueError(f"{label} must be unique under case folding")
            folded.add(key)
        if label == "retrievable_codes" and not value:
            raise ValueError("retrievable_codes must not be empty")
        return value

    @model_validator(mode="after")
    def _query_budget_can_preserve_anchor_and_both_sides(
        self,
    ) -> ReferenceRetrievalPolicySnapshot:
        required = (
            self.max_anchor_unicode_scalars
            + self.left_unicode_scalar_budget
            + self.right_unicode_scalar_budget
        )
        if self.max_query_unicode_scalars < required:
            raise ValueError(
                "max_query_unicode_scalars must preserve the maximum anchor plus both sides"
            )
        return self


def reference_retrieval_policy_hash(policy: ReferenceRetrievalPolicySnapshot) -> str:
    """Hash the exact persisted retrieval policy with canonical JSON."""

    return _hash_json(policy.model_dump(mode="json"))


class ReferenceQueryContextSlice(_Contract):
    """One exact Unicode-scalar slice of a Canonical span."""

    schema_version: SchemaVersion = 1
    span_id: str
    token_ids: tuple[str, ...]
    span_text_hash: Sha256
    slice_start: int = Field(ge=0)
    slice_end: int = Field(gt=0)

    @field_validator("span_id")
    @classmethod
    def _span_id_not_blank(cls, value: str) -> str:
        return _validate_bounded_metadata(value, "span_id", maximum=256)

    @model_validator(mode="after")
    def _slice_is_nonempty(self) -> ReferenceQueryContextSlice:
        if self.slice_end <= self.slice_start:
            raise ValueError("Reference query slice must be non-empty")
        if not self.token_ids or len(set(self.token_ids)) != len(self.token_ids):
            raise ValueError("Reference query slice requires unique token_ids")
        return self


class ReferenceQueryContext(_Contract):
    """Persisted, mechanically reconstructable query plan for one anchor span."""

    schema_version: SchemaVersion = 1
    basis_content_hash: Sha256
    anchor_span_id: str
    anchor_query_start: int = Field(ge=0)
    anchor_query_end: int = Field(gt=0)
    slices: tuple[ReferenceQueryContextSlice, ...]
    exact_query: str
    algorithm: ReferenceQueryContextAlgorithm
    algorithm_version: ReferenceQueryContextAlgorithmVersion
    policy_hash: Sha256

    @field_validator("anchor_span_id")
    @classmethod
    def _anchor_span_id_not_blank(cls, value: str) -> str:
        return _validate_bounded_metadata(value, "anchor_span_id", maximum=256)

    @field_validator("exact_query")
    @classmethod
    def _query_is_safe(cls, value: str) -> str:
        return _validate_subtitle_text(value, "exact_query")

    @model_validator(mode="after")
    def _context_shape(self) -> ReferenceQueryContext:
        if not self.slices:
            raise ValueError("Reference query context requires slices")
        span_ids = tuple(item.span_id for item in self.slices)
        if len(set(span_ids)) != len(span_ids):
            raise ValueError("Reference query context cannot repeat a span slice")
        if span_ids.count(self.anchor_span_id) != 1:
            raise ValueError("Reference query context requires exactly one anchor slice")
        if self.anchor_query_end <= self.anchor_query_start:
            raise ValueError("Reference query anchor range must be non-empty")
        if self.anchor_query_end > len(self.exact_query):
            raise ValueError("Reference query anchor range exceeds exact_query")
        return self


class ReferenceRetrievalHit(_Contract):
    """Query-relative ranking metadata kept outside immutable Evidence."""

    schema_version: SchemaVersion = 1
    evidence_id: str
    rank: int = Field(ge=1)
    relevance: float = Field(ge=0.0, le=1.0, allow_inf_nan=False)
    query_support_start: int = Field(ge=0)
    query_support_end: int = Field(gt=0)
    support_kind: ReferenceRetrievalSupportKind
    candidate_term_index: int | None = Field(default=None, ge=0)

    @field_validator("evidence_id")
    @classmethod
    def _hit_evidence_id_not_blank(cls, value: str) -> str:
        return _validate_bounded_metadata(value, "evidence_id", maximum=256)

    @model_validator(mode="after")
    def _support_shape(self) -> ReferenceRetrievalHit:
        if self.query_support_end <= self.query_support_start:
            raise ValueError("Reference hit support range must be non-empty")
        if self.support_kind == "candidate_term_exact":
            if self.candidate_term_index is None:
                raise ValueError("candidate-term support requires candidate_term_index")
        elif self.candidate_term_index is not None:
            raise ValueError("only candidate-term support may name candidate_term_index")
        return self


class ReferenceRetrievalReceipt(_Contract):
    """Auditable retrieval result; never a synthesized, source-free answer."""

    schema_version: Literal[2] = 2
    query_id: str
    episode_id: str
    invocation_id: str
    audio_span_id: str
    query: str
    context: ReferenceQueryContext
    policy: ReferenceRetrievalPolicySnapshot
    candidate_terms: tuple[str, ...] = ()
    allowed_source_ids: tuple[str, ...] = ()
    retriever: str
    retriever_version: str
    retriever_config_hash: Sha256
    retriever_code_hash: Sha256
    retriever_runtime_hash: Sha256
    index_hash: Sha256
    query_plan_hash: Sha256
    candidate_passages_examined: int = Field(ge=0)
    max_results: int = Field(ge=1)
    status: ReferenceRetrievalStatus = "completed"
    hits: tuple[ReferenceRetrievalHit, ...] = ()
    evidence: tuple[ReferenceEvidence, ...] = ()
    failure_reason: str | None = None

    @field_validator(
        "query_id",
        "episode_id",
        "invocation_id",
        "audio_span_id",
        "query",
        "retriever",
        "retriever_version",
    )
    @classmethod
    def _receipt_text_not_blank(cls, value: str, info: object) -> str:
        return _validate_nonblank(value, getattr(info, "field_name", "value"))

    @field_validator("failure_reason")
    @classmethod
    def _failure_reason_is_bounded(cls, value: str | None) -> str | None:
        if value is None:
            return None
        return _validate_bounded_metadata(value, "failure_reason", maximum=1_024)

    @model_validator(mode="after")
    def _retrieval_invariants(self) -> ReferenceRetrievalReceipt:
        if self.context.policy_hash != reference_retrieval_policy_hash(self.policy):
            raise ValueError("Reference retrieval context policy hash mismatch")
        if self.audio_span_id != self.context.anchor_span_id:
            raise ValueError("Reference retrieval audio_span_id differs from query anchor")
        if self.query != self.context.exact_query:
            raise ValueError("Reference retrieval query differs from persisted context")
        if self.max_results != self.policy.max_results:
            raise ValueError("Reference retrieval max_results differs from policy")
        if len(self.candidate_terms) > self.policy.max_candidate_terms:
            raise ValueError("Reference retrieval candidate terms exceed policy")
        ids = [item.id for item in self.evidence]
        if len(set(ids)) != len(ids):
            raise ValueError("reference retrieval receipt contains duplicate evidence IDs")
        if len(set(self.allowed_source_ids)) != len(self.allowed_source_ids):
            raise ValueError("allowed_source_ids must be unique")
        if len(self.evidence) > self.max_results:
            raise ValueError("retrieval receipt evidence exceeds max_results")
        hit_ids = [item.evidence_id for item in self.hits]
        if len(set(hit_ids)) != len(hit_ids):
            raise ValueError("reference retrieval receipt contains duplicate hit Evidence IDs")
        if hit_ids != ids:
            raise ValueError("Reference retrieval hits must exactly order receipt Evidence")
        if [item.rank for item in self.hits] != list(range(1, len(self.hits) + 1)):
            raise ValueError("Reference retrieval hit ranks must be sequential from one")
        for hit in self.hits:
            if hit.query_support_end > len(self.query):
                raise ValueError("Reference retrieval hit support exceeds query")
            if not (
                hit.query_support_start < self.context.anchor_query_end
                and hit.query_support_end > self.context.anchor_query_start
            ):
                raise ValueError("Reference retrieval hit does not overlap its anchor")
            if hit.candidate_term_index is not None and hit.candidate_term_index >= len(
                self.candidate_terms
            ):
                raise ValueError("Reference retrieval hit candidate_term_index is invalid")
        disallowed = {
            item.artifact.source_id
            for item in self.evidence
            if self.allowed_source_ids and item.artifact.source_id not in self.allowed_source_ids
        }
        if disallowed:
            raise ValueError(
                f"retrieval receipt contains disallowed sources: {sorted(disallowed)!r}"
            )
        if self.status == "completed" and self.failure_reason is not None:
            raise ValueError("completed retrieval cannot have failure_reason")
        if self.status == "failed" and not (self.failure_reason or "").strip():
            raise ValueError("failed retrieval requires failure_reason")
        if self.status == "failed" and (self.evidence or self.hits):
            raise ValueError("failed retrieval cannot contain hits or Reference Evidence")
        return self


class AudioClockMap(_Contract):
    """Verified origin mapping from source audio to normalized audio."""

    schema_version: SchemaVersion = 1
    source_origin_ms: int = Field(ge=0)
    normalized_origin_ms: int = Field(ge=0)
    verified: bool
    drift_ms: float = Field(allow_inf_nan=False)


class NormalizationReceipt(_Contract):
    """Provider-neutral, replayable proof of one normalization execution."""

    schema_version: SchemaVersion = 1
    status: ReceiptStatus = "draft"
    provider: str
    production_id: str | None = None
    production_source: NormalizationProductionSource = "legacy_unknown"
    source_identity_verified: bool = False
    source_binding_method: NormalizationSourceBindingMethod = "legacy_unknown"
    provider_outcome: NormalizationOutcome = "unknown"
    provider_status_code: int | None = None
    provider_status: str | None = None
    source: ArtifactDigest
    normalized: ArtifactDigest
    source_duration_ms: int | None = Field(default=None, gt=0)
    normalized_duration_ms: int | None = Field(default=None, gt=0)
    request_started_at: AwareDatetime | None = None
    completed_at: AwareDatetime | None = None
    provider_created_at: AwareDatetime | None = None
    provider_completed_at: AwareDatetime | None = None
    requested_parameters: tuple[NormalizationParameter, ...] = ()
    submitted_parameters: tuple[NormalizationParameter, ...] | None = None
    preset: str | None = None
    settings_hash: Sha256
    reuse_reason: str | None = None
    original_production_id: str | None = None
    clock_map: AudioClockMap
    alignment_method: NormalizationAlignmentMethod = "legacy_unverified"
    alignment_metrics: tuple[NormalizationMetric, ...] = ()
    alignment_failure_reason: str | None = None

    @field_validator("provider")
    @classmethod
    def _provider_not_blank(cls, value: str) -> str:
        return _validate_nonblank(value, "provider")

    @field_validator(
        "production_id",
        "provider_status",
        "preset",
        "reuse_reason",
        "original_production_id",
        "alignment_failure_reason",
    )
    @classmethod
    def _optional_receipt_text_not_blank(cls, value: str | None, info: object) -> str | None:
        if value is not None:
            return _validate_nonblank(value, getattr(info, "field_name", "value"))
        return value

    @field_validator("provider_status_code", mode="before")
    @classmethod
    def _provider_status_code_is_an_exact_integer(cls, value: object) -> object:
        if value is not None and type(value) is not int:
            raise ValueError("provider_status_code must be an exact integer")
        return value

    @model_validator(mode="after")
    def _receipt_invariants(self) -> NormalizationReceipt:
        for label, parameters in (
            ("requested_parameters", self.requested_parameters),
            ("submitted_parameters", self.submitted_parameters or ()),
        ):
            keys = [(parameter.scope, parameter.name) for parameter in parameters]
            if len(set(keys)) != len(keys):
                raise ValueError(f"{label} contains duplicate scope/name pairs")
            if keys != sorted(keys):
                raise ValueError(f"{label} must be sorted by scope/name")

        metric_names = [metric.name for metric in self.alignment_metrics]
        if len(set(metric_names)) != len(metric_names):
            raise ValueError("alignment_metrics names must be unique")

        for field_name in (
            "request_started_at",
            "completed_at",
            "provider_created_at",
            "provider_completed_at",
        ):
            timestamp = getattr(self, field_name)
            if timestamp is not None and timestamp.utcoffset() != timezone.utc.utcoffset(timestamp):
                raise ValueError(f"{field_name} must use UTC")
        if (
            self.request_started_at is not None
            and self.completed_at is not None
            and self.completed_at < self.request_started_at
        ):
            raise ValueError("completed_at must not precede request_started_at")

        if self.production_source == "created":
            if self.reuse_reason is not None or self.original_production_id is not None:
                raise ValueError("created production cannot have reuse lineage")
        elif self.production_source == "reused":
            if not self.reuse_reason or not self.original_production_id:
                raise ValueError(
                    "reused production requires reuse_reason and original_production_id"
                )

        verified_binding_methods = {
            "upload_in_current_request",
            "provider_checksum",
            "verified_upstream_handoff",
        }
        if self.source_identity_verified != (
            self.source_binding_method in verified_binding_methods
        ):
            raise ValueError(
                "source_identity_verified must exactly match a cryptographic/current-upload "
                "source binding method"
            )
        if self.production_source == "created" and self.source_binding_method not in {
            "upload_in_current_request",
            "provider_checksum",
        }:
            raise ValueError(
                "created production requires current-upload or provider-checksum source binding"
            )

        if self.alignment_method in {"fixed_seconds", "not_requested", "legacy_unverified"}:
            if self.clock_map.verified:
                raise ValueError(
                    f"{self.alignment_method} alignment cannot claim a verified clock map"
                )
        if self.alignment_method in {"cross_correlation", "identity"}:
            if not self.clock_map.verified:
                raise ValueError(f"{self.alignment_method} alignment requires a verified clock map")

        if self.status == "accepted":
            if not self.source_identity_verified:
                raise ValueError("accepted normalization receipt requires verified source identity")
            if self.provider_outcome != "completed":
                raise ValueError("accepted normalization receipt requires completed outcome")
            if self.provider.casefold() == "auphonic":
                if self.provider_status_code != 3:
                    raise ValueError(
                        "accepted Auphonic normalization receipt requires provider status code 3"
                    )
                if self.provider_status is None:
                    raise ValueError(
                        "accepted Auphonic normalization receipt requires provider status"
                    )
            if not self.production_id:
                raise ValueError("accepted normalization receipt requires production_id")
            if self.production_source not in {"created", "reused"}:
                raise ValueError("accepted normalization receipt requires known production source")
            if self.submitted_parameters is None or not self.submitted_parameters:
                raise ValueError("accepted normalization receipt requires submitted parameters")
            expected_settings_hash = normalization_settings_hash(
                self.submitted_parameters,
                preset=self.preset,
            )
            if self.settings_hash != expected_settings_hash:
                raise ValueError(
                    "settings_hash does not match the submitted normalization parameters"
                )
            if self.source_duration_ms is None or self.normalized_duration_ms is None:
                raise ValueError("accepted normalization receipt requires audio durations")
            if self.request_started_at is None or self.completed_at is None:
                raise ValueError("accepted normalization receipt requires execution timestamps")
            if self.alignment_method not in {"cross_correlation", "identity"}:
                raise ValueError(
                    "accepted normalization receipt requires verified cross-correlation or identity"
                )
            if not self.clock_map.verified:
                raise ValueError("accepted normalization receipt requires a verified clock map")
        return self


class EvidenceToken(_Contract):
    """One immutable recognition hypothesis token on the normalized clock."""

    schema_version: SchemaVersion = 1
    id: str
    text: str
    start_ms: int = Field(ge=0)
    end_ms: int = Field(gt=0)
    confidence: float | None = Field(default=None, ge=0.0, le=1.0)
    speaker: str | None = None
    evidence_refs: tuple[str, ...] = ()

    @field_validator("id")
    @classmethod
    def _required_text(cls, value: str, info: object) -> str:
        return _validate_nonblank(value, getattr(info, "field_name", "value"))

    @field_validator("text")
    @classmethod
    def _safe_text(cls, value: str) -> str:
        return _validate_subtitle_text(value, "text")

    @field_validator("speaker")
    @classmethod
    def _optional_speaker_is_safe(cls, value: str | None) -> str | None:
        if value is None:
            return None
        return _validate_bounded_metadata(value, "speaker", maximum=256)

    @model_validator(mode="after")
    def _positive_range(self) -> EvidenceToken:
        if self.end_ms <= self.start_ms:
            raise ValueError("end_ms must be greater than start_ms")
        return self


class RecognitionEvidence(_Contract):
    """Complete, versioned output of one recognition adapter invocation."""

    schema_version: SchemaVersion = 1
    episode_id: str
    invocation_id: str
    adapter: str
    model: str
    language: str
    config_hash: Sha256
    raw_output: ArtifactDigest
    raw_output_hash: Sha256
    normalized_audio_hash: Sha256
    tokens: tuple[EvidenceToken, ...]

    @model_validator(mode="after")
    def _ordered_unique_tokens(self) -> RecognitionEvidence:
        for field_name in ("episode_id", "invocation_id", "adapter", "model", "language"):
            _validate_nonblank(getattr(self, field_name), field_name)
        if self.raw_output_hash != self.raw_output.sha256:
            raise ValueError("raw_output_hash must equal raw_output.sha256")
        if not self.tokens:
            raise ValueError("recognition evidence requires at least one token")
        _validate_timed_tokens(self.tokens)
        return self


class SpeechActivityInterval(_Contract):
    """One independent audio-activity observation on the normalized clock.

    The interval intentionally contains no text hypothesis.  It can establish
    that ASR coverage needs review, but it can never supply replacement words.
    """

    schema_version: SchemaVersion = 1
    id: str
    start_ms: int = Field(ge=0)
    end_ms: int = Field(gt=0)

    @field_validator("id")
    @classmethod
    def _interval_id_not_blank(cls, value: str) -> str:
        return _validate_nonblank(value, "speech activity interval id")

    @model_validator(mode="after")
    def _positive_interval(self) -> SpeechActivityInterval:
        if self.end_ms <= self.start_ms:
            raise ValueError("speech activity interval end_ms must exceed start_ms")
        return self


class SpeechCoverageReceipt(_Contract):
    """Replayable proof that independent audio activity was compared with ASR.

    ``raw_output`` addresses the exact analyzer output bundle.  Activity and
    uncovered intervals are derived claims and therefore must be reproduced by
    the configured Adapter before a stored generation can be trusted.
    """

    schema_version: SchemaVersion = 1
    status: SpeechCoverageStatus
    episode_id: str
    invocation_id: str
    analyzer: str
    analyzer_version: str
    analyzer_config_hash: Sha256
    analyzer_code_hash: Sha256
    analyzer_runtime_hash: Sha256
    method: SpeechActivityMethod
    normalized_audio_hash: Sha256
    normalized_audio_size_bytes: int = Field(gt=0)
    normalized_audio_duration_ms: int = Field(gt=0)
    recognition_evidence_hash: Sha256
    recognition_interval_hash: Sha256
    coverage_tolerance_ms: int = Field(ge=0)
    minimum_uncovered_ms: int = Field(gt=0)
    raw_output: ArtifactDigest
    raw_output_hash: Sha256
    activity_intervals: tuple[SpeechActivityInterval, ...] = ()
    uncovered_intervals: tuple[SpeechActivityInterval, ...] = ()
    passed: bool = False
    failure_reason: str | None = None

    @field_validator("episode_id", "invocation_id", "analyzer", "analyzer_version")
    @classmethod
    def _coverage_text_not_blank(cls, value: str, info: object) -> str:
        return _validate_nonblank(value, getattr(info, "field_name", "value"))

    @field_validator("failure_reason")
    @classmethod
    def _coverage_failure_not_blank(cls, value: str | None) -> str | None:
        if value is not None:
            return _validate_bounded_metadata(value, "failure_reason", maximum=2048)
        return value

    @model_validator(mode="after")
    def _coverage_invariants(self) -> SpeechCoverageReceipt:
        if self.raw_output_hash != self.raw_output.sha256:
            raise ValueError("speech coverage raw_output_hash must equal raw_output.sha256")

        for label, intervals in (
            ("activity_intervals", self.activity_intervals),
            ("uncovered_intervals", self.uncovered_intervals),
        ):
            ids = [interval.id for interval in intervals]
            if len(set(ids)) != len(ids):
                raise ValueError(f"{label} contains duplicate interval IDs")
            previous_end = 0
            for interval in intervals:
                if interval.start_ms < previous_end:
                    raise ValueError(f"{label} must be ordered and non-overlapping")
                if interval.end_ms > self.normalized_audio_duration_ms:
                    raise ValueError(f"{label} exceeds normalized audio duration")
                previous_end = interval.end_ms

        activity = self.activity_intervals
        for uncovered in self.uncovered_intervals:
            if not any(
                candidate.start_ms <= uncovered.start_ms and uncovered.end_ms <= candidate.end_ms
                for candidate in activity
            ):
                raise ValueError("uncovered interval must be contained in observed activity")

        if self.status == "completed":
            if self.failure_reason is not None:
                raise ValueError("completed speech coverage cannot have failure_reason")
            if self.passed != (not self.uncovered_intervals):
                raise ValueError("speech coverage passed must exactly reflect uncovered intervals")
        else:
            if self.failure_reason is None:
                raise ValueError("failed speech coverage requires failure_reason")
            if self.passed:
                raise ValueError("failed speech coverage cannot pass")
            if self.activity_intervals or self.uncovered_intervals:
                raise ValueError("failed speech coverage cannot claim derived intervals")
        return self


def speech_coverage_receipt_content_hash(receipt: SpeechCoverageReceipt) -> str:
    """Hash portable receipt semantics while excluding a machine-local URI."""

    payload = receipt.model_dump(mode="json")
    payload["raw_output"] = {
        "schema_version": receipt.raw_output.schema_version,
        "sha256": receipt.raw_output.sha256,
        "size_bytes": receipt.raw_output.size_bytes,
    }
    return _hash_json(payload)


def recognition_evidence_content_hash(evidence: RecognitionEvidence) -> str:
    """Hash one ASR result while excluding invocation and machine-local URI."""

    return _hash_json(
        {
            "schema_version": evidence.schema_version,
            "episode_id": evidence.episode_id,
            "adapter": evidence.adapter,
            "model": evidence.model,
            "language": evidence.language,
            "config_hash": evidence.config_hash,
            "raw_output": {
                "sha256": evidence.raw_output.sha256,
                "size_bytes": evidence.raw_output.size_bytes,
            },
            "normalized_audio_hash": evidence.normalized_audio_hash,
            "tokens": [token.model_dump(mode="json") for token in evidence.tokens],
        }
    )


def recognition_evidence_set_hash(
    evidence: tuple[RecognitionEvidence, ...] | list[RecognitionEvidence],
) -> str:
    """Hash an unordered collection of distinct recognition Adapter outputs."""

    hashes = sorted(recognition_evidence_content_hash(item) for item in evidence)
    if not hashes:
        raise ValueError("Recognition Evidence set must not be empty")
    if len(set(hashes)) != len(hashes):
        raise ValueError("Recognition Evidence set contains duplicate content")
    return _hash_json(hashes)


class CanonicalToken(_Contract):
    """Accepted lexeme; exact timing is optional and never inferred from text width.

    ``coarse_span`` lexemes intentionally carry no individual timestamps.  Their
    reviewed outer AudioSpan lives on :class:`CanonicalSpan`; lexeme edges remain
    valid semantic/display edges but are not cue boundaries.
    """

    schema_version: SchemaVersion = 1
    id: str
    text: str
    start_ms: int | None = Field(default=None, ge=0)
    end_ms: int | None = Field(default=None, gt=0)
    timing_basis: CanonicalTimingBasis = "recognition"
    alignment_evidence_ids: tuple[str, ...] = ()
    speaker: str | None = None
    evidence_ids: tuple[str, ...] = ()
    confidence: float | None = Field(default=None, ge=0.0, le=1.0)

    @field_validator("id")
    @classmethod
    def _required_text(cls, value: str, info: object) -> str:
        return _validate_nonblank(value, getattr(info, "field_name", "value"))

    @field_validator("text")
    @classmethod
    def _safe_text(cls, value: str) -> str:
        return _validate_subtitle_text(value, "text")

    @field_validator("speaker")
    @classmethod
    def _optional_speaker_is_safe(cls, value: str | None) -> str | None:
        if value is None:
            return None
        return _validate_bounded_metadata(value, "speaker", maximum=256)

    @model_validator(mode="after")
    def _timing_shape(self) -> CanonicalToken:
        if len(set(self.evidence_ids)) != len(self.evidence_ids):
            raise ValueError("evidence_ids must be unique")
        if len(set(self.alignment_evidence_ids)) != len(self.alignment_evidence_ids):
            raise ValueError("alignment_evidence_ids must be unique")
        if self.timing_basis == "coarse_span":
            if self.start_ms is not None or self.end_ms is not None:
                raise ValueError("coarse_span lexeme cannot claim individual timestamps")
            if self.alignment_evidence_ids:
                raise ValueError("coarse_span lexeme cannot claim alignment Evidence")
            return self
        if self.start_ms is None or self.end_ms is None:
            raise ValueError(f"{self.timing_basis} lexeme requires exact timestamps")
        if self.end_ms <= self.start_ms:
            raise ValueError("end_ms must be greater than start_ms")
        if self.timing_basis in {"forced_alignment", "human_alignment"} and not (
            self.alignment_evidence_ids
        ):
            raise ValueError(f"{self.timing_basis} lexeme requires alignment Evidence")
        return self


class CanonicalSpan(_Contract):
    """Stable review address and the exact outer AudioSpan for its lexemes."""

    schema_version: SchemaVersion = 1
    id: str
    token_ids: tuple[str, ...]
    start_ms: int = Field(ge=0)
    end_ms: int = Field(gt=0)
    alignment: CanonicalSpanAlignment = "lexeme_exact"
    alignment_evidence_ids: tuple[str, ...] = ()
    lineage: tuple[str, ...] = ()

    @model_validator(mode="after")
    def _nonempty_unique_tokens(self) -> CanonicalSpan:
        if not self.token_ids:
            raise ValueError("canonical span requires token_ids")
        if len(set(self.token_ids)) != len(self.token_ids):
            raise ValueError("canonical span token_ids must be unique")
        if self.end_ms <= self.start_ms:
            raise ValueError("canonical span end_ms must be greater than start_ms")
        if len(set(self.alignment_evidence_ids)) != len(self.alignment_evidence_ids):
            raise ValueError("canonical span alignment_evidence_ids must be unique")
        if self.alignment == "coarse" and self.alignment_evidence_ids:
            raise ValueError("coarse canonical span cannot claim alignment Evidence")
        if self.id in self.lineage:
            raise ValueError("canonical span cannot list itself in lineage")
        return self


class ReviewIssue(_Contract):
    """Open or resolved risk attached to stable canonical spans."""

    schema_version: SchemaVersion = 1
    id: str
    risk: RiskKind
    severity: RiskSeverity
    code: str
    span_ids: tuple[str, ...]
    audio_evidence_ids: tuple[str, ...] = ()
    reference_evidence_ids: tuple[str, ...] = ()
    candidates: tuple[str, ...] = ()
    status: IssueStatus = "unresolved"

    @model_validator(mode="after")
    def _evidence_ids_are_unique(self) -> ReviewIssue:
        if not self.span_ids:
            raise ValueError("review issue requires span_ids")
        if len(set(self.span_ids)) != len(self.span_ids):
            raise ValueError("review issue span_ids must be unique")
        for label, values in (
            ("audio_evidence_ids", self.audio_evidence_ids),
            ("reference_evidence_ids", self.reference_evidence_ids),
        ):
            if len(set(values)) != len(values):
                raise ValueError(f"{label} must be unique")
        for candidate in self.candidates:
            _validate_subtitle_text(candidate, "review issue candidate")
        return self


class GenerationWarning(_Contract):
    """Generation-wide provenance limitation without a fabricated span address."""

    schema_version: SchemaVersion = 1
    id: str
    code: Literal["recognizer_confidence_unavailable"]
    severity: Literal["medium"] = "medium"
    status: GenerationWarningStatus = "requires_full_audit"
    evidence_hashes: tuple[Sha256, ...]
    adapter_ids: tuple[str, ...]
    affected_token_count: int = Field(gt=0)
    mitigation_receipt_set_hash: Sha256 | None = None

    @model_validator(mode="after")
    def _warning_shape(self) -> GenerationWarning:
        if not self.evidence_hashes or len(set(self.evidence_hashes)) != len(self.evidence_hashes):
            raise ValueError("generation warning requires unique Evidence hashes")
        if not self.adapter_ids or any(not value.strip() for value in self.adapter_ids):
            raise ValueError("generation warning requires nonblank Adapter IDs")
        if len(set(self.adapter_ids)) != len(self.adapter_ids):
            raise ValueError("generation warning Adapter IDs must be unique")
        if self.status == "requires_full_audit" and self.mitigation_receipt_set_hash:
            raise ValueError("unmitigated warning cannot carry a mitigation receipt")
        if self.status == "mitigated" and self.mitigation_receipt_set_hash is None:
            raise ValueError("mitigated warning requires a receipt-set hash")
        return self


class CorrectionAuditReceipt(_Contract):
    """One strictly imported *text Corrector* response.

    This receipt proves that a bounded text work packet was executed.  It is
    deliberately not Audio Evidence and cannot satisfy the audio full-audit
    gate by itself.
    """

    schema_version: SchemaVersion = 1
    mode: Literal["full_audit"] = "full_audit"
    episode_id: str
    preaudit_generation_id: str
    preaudit_content_hash: Sha256
    evidence_hash: Sha256
    reference_evidence_hash: Sha256
    available_reference_evidence_ids: tuple[str, ...]
    available_reference_evidence_hash: Sha256
    presented_reference_evidence_ids: tuple[str, ...]
    presented_reference_evidence_hash: Sha256
    policy_hash: Sha256
    target_span_ids: tuple[str, ...]
    work_packet_id: str
    request: ArtifactDigest
    response: ArtifactDigest
    request_hash: Sha256
    response_hash: Sha256
    adapter_name: str
    adapter_version: str
    model: str
    model_version: str
    runtime_hash: Sha256
    adapter_code_hash: Sha256
    config_hash: Sha256
    execution_mode: Literal["fixture", "local", "paid_api", "subscription", "other"]
    adapter_identity_hash: Sha256
    proposal_ids: tuple[str, ...] = ()
    proposal_set_hash: Sha256

    @model_validator(mode="after")
    def _audit_receipt_shape(self) -> CorrectionAuditReceipt:
        for label, value in (
            ("episode_id", self.episode_id),
            ("preaudit_generation_id", self.preaudit_generation_id),
            ("work_packet_id", self.work_packet_id),
            ("adapter_name", self.adapter_name),
            ("adapter_version", self.adapter_version),
            ("model", self.model),
            ("model_version", self.model_version),
        ):
            if not value.strip():
                raise ValueError(f"audit receipt requires nonblank {label}")
        if not self.target_span_ids or len(set(self.target_span_ids)) != len(self.target_span_ids):
            raise ValueError("audit receipt requires unique target span IDs")
        if len(set(self.proposal_ids)) != len(self.proposal_ids):
            raise ValueError("audit receipt proposal IDs must be unique")
        if len(set(self.presented_reference_evidence_ids)) != len(
            self.presented_reference_evidence_ids
        ):
            raise ValueError("audit receipt presented Reference Evidence IDs must be unique")
        if len(set(self.available_reference_evidence_ids)) != len(
            self.available_reference_evidence_ids
        ):
            raise ValueError("audit receipt available Reference Evidence IDs must be unique")
        if not set(self.presented_reference_evidence_ids).issubset(
            self.available_reference_evidence_ids
        ):
            raise ValueError("presented Reference Evidence must be an available subset")
        for label, artifact, digest in (
            ("request", self.request, self.request_hash),
            ("response", self.response, self.response_hash),
        ):
            expected_prefix = f"generation-artifact://correction/{label}s/"
            if not artifact.uri.startswith(expected_prefix) or artifact.sha256 != digest:
                raise ValueError(
                    f"audit receipt {label} locator/hash does not bind a Correction artifact"
                )
        identity = {
            "adapter_name": self.adapter_name,
            "adapter_version": self.adapter_version,
            "model": self.model,
            "model_version": self.model_version,
            "runtime_hash": self.runtime_hash,
            "adapter_code_hash": self.adapter_code_hash,
            "config_hash": self.config_hash,
            "execution_mode": self.execution_mode,
        }
        if self.adapter_identity_hash != _hash_json(identity):
            raise ValueError("audit receipt Adapter identity hash mismatch")
        return self


def correction_audit_receipt_content_hash(receipt: CorrectionAuditReceipt) -> str:
    return _hash_json(receipt.model_dump(mode="json"))


def correction_audit_receipt_set_hash(
    receipts: tuple[CorrectionAuditReceipt, ...] | list[CorrectionAuditReceipt],
) -> str:
    hashes = [correction_audit_receipt_content_hash(receipt) for receipt in receipts]
    if not hashes:
        raise ValueError("full-audit receipt set must not be empty")
    if len(set(hashes)) != len(hashes):
        raise ValueError("full-audit receipt set contains duplicate receipts")
    return _hash_json(hashes)


class AudioAuditReceipt(_Contract):
    """Proof that one complete Canonical window was reviewed against audio.

    Request, response, and the exact clip delivered to the multimodal model
    are immutable Generation artifacts.  Their byte digests make a later
    restart able to reject edited prompts, model responses, or clips.
    """

    schema_version: SchemaVersion = 1
    id: str
    episode_id: str
    preaudit_generation_id: str
    preaudit_content_hash: Sha256
    evidence_hash: Sha256
    reference_evidence_hash: Sha256
    policy_hash: Sha256
    normalized_audio_hash: Sha256
    target_span_ids: tuple[str, ...]
    window_start_ms: int = Field(ge=0)
    window_end_ms: int = Field(gt=0)
    clip_start_ms: int = Field(ge=0)
    clip_end_ms: int = Field(gt=0)
    work_packet_id: str
    request: ArtifactDigest
    response: ArtifactDigest
    clip: ArtifactDigest
    adapter_name: str
    adapter_version: str
    model: str
    model_version: str
    runtime_hash: Sha256
    adapter_code_hash: Sha256
    config_hash: Sha256
    adapter_identity_hash: Sha256
    status: AudioAuditStatus
    confidence: float | None = Field(default=None, ge=0.0, le=1.0)
    rationale: str
    proposal_ids: tuple[str, ...] = ()
    proposal_set_hash: Sha256

    @field_validator(
        "id",
        "episode_id",
        "preaudit_generation_id",
        "work_packet_id",
        "adapter_name",
        "adapter_version",
        "model",
        "model_version",
        "rationale",
    )
    @classmethod
    def _required_metadata(cls, value: str, info: object) -> str:
        return _validate_nonblank(value, getattr(info, "field_name", "value"))

    @model_validator(mode="after")
    def _audio_audit_shape(self) -> AudioAuditReceipt:
        if not self.target_span_ids or len(set(self.target_span_ids)) != len(self.target_span_ids):
            raise ValueError("audio audit requires unique target span IDs")
        if self.window_end_ms <= self.window_start_ms:
            raise ValueError("audio audit window must have positive duration")
        if self.clip_end_ms <= self.clip_start_ms:
            raise ValueError("audio audit clip must have positive duration")
        if self.clip_start_ms > self.window_start_ms or self.clip_end_ms < self.window_end_ms:
            raise ValueError("audio audit clip must contain the complete Canonical window")
        if len(set(self.proposal_ids)) != len(self.proposal_ids):
            raise ValueError("audio audit proposal IDs must be unique")
        if self.status == "confirmed" and self.proposal_ids:
            raise ValueError("confirmed audio audit cannot carry proposals")
        if self.status == "finding" and not self.proposal_ids:
            raise ValueError("finding audio audit requires at least one proposal")
        if self.status == "unresolved" and self.proposal_ids:
            raise ValueError("unresolved audio audit cannot authorize proposals")
        if self.status in {"confirmed", "finding"} and self.confidence is None:
            raise ValueError("resolved audio audit requires confidence")
        if self.status in {"confirmed", "finding"} and self.confidence < 0.75:
            raise ValueError("low-confidence audio audit must remain unresolved")
        for label, artifact in (
            ("request", self.request),
            ("response", self.response),
            ("clip", self.clip),
        ):
            expected_prefix = f"generation-artifact://audio_audit/{label}s/"
            if not artifact.uri.startswith(expected_prefix):
                raise ValueError(f"audio audit {label} must use a portable Generation artifact URI")
        return self


def audio_audit_receipt_content_hash(receipt: AudioAuditReceipt) -> str:
    return _hash_json(receipt.model_dump(mode="json"))


def audio_audit_receipt_set_hash(
    receipts: tuple[AudioAuditReceipt, ...] | list[AudioAuditReceipt],
) -> str:
    hashes = [audio_audit_receipt_content_hash(receipt) for receipt in receipts]
    if not hashes:
        raise ValueError("audio full-audit receipt set must not be empty")
    if len(set(hashes)) != len(hashes):
        raise ValueError("audio full-audit receipt set contains duplicate receipts")
    return _hash_json(hashes)


class ArbitrationReceipt(_Contract):
    """Replayable audio verdict for exactly one stored Correction Proposal."""

    schema_version: SchemaVersion = 1
    id: str
    episode_id: str
    generation_id: str
    canonical_content_hash: Sha256
    evidence_hash: Sha256
    reference_evidence_hash: Sha256
    policy_hash: Sha256
    normalized_audio_hash: Sha256
    proposal_id: str
    proposal_hash: Sha256
    start_ms: int = Field(ge=0)
    end_ms: int = Field(gt=0)
    work_packet_id: str
    request: ArtifactDigest
    response: ArtifactDigest
    clip: ArtifactDigest
    adapter_name: str
    adapter_version: str
    model: str
    model_version: str
    runtime_hash: Sha256
    adapter_code_hash: Sha256
    config_hash: Sha256
    adapter_identity_hash: Sha256
    status: ArbitrationReceiptStatus
    selected_text: str | None = None
    confidence: float | None = Field(default=None, ge=0.0, le=1.0)
    rationale: str

    @field_validator(
        "id",
        "episode_id",
        "generation_id",
        "proposal_id",
        "work_packet_id",
        "adapter_name",
        "adapter_version",
        "model",
        "model_version",
        "rationale",
    )
    @classmethod
    def _required_arbitration_metadata(cls, value: str, info: object) -> str:
        return _validate_nonblank(value, getattr(info, "field_name", "value"))

    @field_validator("selected_text")
    @classmethod
    def _optional_selected_text(cls, value: str | None) -> str | None:
        if value is None:
            return None
        return _validate_subtitle_text(value, "selected_text")

    @model_validator(mode="after")
    def _arbitration_shape(self) -> ArbitrationReceipt:
        if self.end_ms <= self.start_ms:
            raise ValueError("arbitration range must have positive duration")
        if self.status == "unresolved":
            if self.selected_text is not None:
                raise ValueError("unresolved arbitration cannot select text")
        elif not (self.selected_text or "").strip():
            raise ValueError("resolved arbitration requires selected_text")
        if self.status != "unresolved" and self.confidence is None:
            raise ValueError("resolved arbitration requires confidence")
        for label, artifact in (
            ("request", self.request),
            ("response", self.response),
            ("clip", self.clip),
        ):
            expected_prefix = f"generation-artifact://arbitration/{label}s/"
            if not artifact.uri.startswith(expected_prefix):
                raise ValueError(f"arbitration {label} must use a portable Generation artifact URI")
        return self


class AcceptancePolicySnapshot(_Contract):
    """Versioned acceptance rule persisted with every Canonical Generation.

    ``policy_hash`` identifies the complete effective pipeline policy, but a
    hash cannot be reversed after restart.  This deliberately small snapshot
    carries the rule required to re-evaluate whether unresolved low-severity
    issues are allowed in an accepted transcript.
    """

    schema_version: SchemaVersion = 1
    permit_unresolved_low_risk: bool = True


class CanonicalTranscript(_Contract):
    """The sole text truth; accepted state is guarded by hard invariants."""

    schema_version: Literal[1, 2] = 1
    episode_id: str
    generation_id: str
    revision: int = Field(ge=1)
    status: TranscriptStatus = "draft"
    source_audio_hash: Sha256
    normalized_audio_hash: Sha256
    normalization_receipt_hash: Sha256
    evidence_hash: Sha256
    orthographic_projection_evidence_hash: Sha256 | None = Field(
        default=None,
        exclude_if=lambda value: value is None,
    )
    reference_evidence_hash: Sha256
    ledger_hash: Sha256
    policy_hash: Sha256
    acceptance_policy: AcceptancePolicySnapshot
    tokens: tuple[CanonicalToken, ...]
    spans: tuple[CanonicalSpan, ...] = ()
    review_issues: tuple[ReviewIssue, ...] = ()
    generation_warnings: tuple[GenerationWarning, ...] = ()
    full_audit_receipt_set_hash: Sha256 | None = None
    verified_speech_coverage_receipt_hash: Sha256 | None = None
    content_hash: Sha256

    @model_validator(mode="after")
    def _canonical_invariants(self) -> CanonicalTranscript:
        if self.schema_version == 1 and self.orthographic_projection_evidence_hash is not None:
            raise ValueError("Canonical schema v1 cannot claim Orthographic Projection Evidence")
        if self.schema_version == 2 and self.orthographic_projection_evidence_hash is None:
            raise ValueError("Canonical schema v2 requires Orthographic Projection Evidence")
        if not self.tokens:
            raise ValueError("canonical transcript requires at least one token")
        token_ids = [token.id for token in self.tokens]
        if len(set(token_ids)) != len(token_ids):
            raise ValueError("canonical token IDs must be unique")
        expected_hash = canonical_content_hash(self.tokens)
        if self.content_hash != expected_hash:
            raise ValueError(
                f"content_hash mismatch: expected {expected_hash}, got {self.content_hash}"
            )

        token_order = {token.id: index for index, token in enumerate(self.tokens)}
        token_by_id = {token.id: token for token in self.tokens}
        if not self.spans:
            raise ValueError("canonical transcript requires a complete span partition")
        span_ids: set[str] = set()
        flattened_span_tokens: list[str] = []
        previous_span_end = 0
        spans_overlap = False
        for span in self.spans:
            if span.id in span_ids:
                raise ValueError(f"duplicate canonical span id: {span.id!r}")
            span_ids.add(span.id)
            try:
                positions = [token_order[token_id] for token_id in span.token_ids]
            except KeyError as exc:
                raise ValueError(f"span references unknown token id: {exc.args[0]!r}") from exc
            if positions != list(range(positions[0], positions[-1] + 1)):
                raise ValueError(f"span {span.id!r} token_ids must be ordered and contiguous")
            if flattened_span_tokens and span.start_ms < previous_span_end:
                spans_overlap = True
            previous_span_end = span.end_ms
            span_tokens = tuple(token_by_id[token_id] for token_id in span.token_ids)
            if span.alignment == "coarse":
                if any(token.timing_basis != "coarse_span" for token in span_tokens):
                    raise ValueError(
                        f"coarse span {span.id!r} may contain only coarse_span lexemes"
                    )
            else:
                if any(token.timing_basis == "coarse_span" for token in span_tokens):
                    raise ValueError(f"lexeme_exact span {span.id!r} cannot contain coarse lexemes")
                previous_token_end: int | None = None
                for token in span_tokens:
                    if token.start_ms is None or token.end_ms is None:
                        raise ValueError(f"lexeme_exact span {span.id!r} requires timed lexemes")
                    if previous_token_end is not None and token.start_ms < previous_token_end:
                        raise ValueError(f"lexeme_exact span {span.id!r} token timing overlaps")
                    previous_token_end = token.end_ms
                if (
                    span_tokens[0].start_ms != span.start_ms
                    or span_tokens[-1].end_ms != span.end_ms
                ):
                    raise ValueError(
                        f"lexeme_exact span {span.id!r} outer timing must match its lexemes"
                    )
            flattened_span_tokens.extend(span.token_ids)

        canonical_token_ids = [token.id for token in self.tokens]
        if flattened_span_tokens != canonical_token_ids:
            raise ValueError(
                "canonical spans must form an ordered, non-overlapping, complete token partition"
            )
        if spans_overlap:
            raise ValueError("canonical spans must be monotonic and non-overlapping")

        issue_ids: set[str] = set()
        for issue in self.review_issues:
            if issue.id in issue_ids:
                raise ValueError(f"duplicate review issue id: {issue.id!r}")
            issue_ids.add(issue.id)
            unknown = set(issue.span_ids) - span_ids
            if unknown:
                raise ValueError(f"review issue references unknown span ids: {sorted(unknown)!r}")

        warning_ids = [warning.id for warning in self.generation_warnings]
        if len(set(warning_ids)) != len(warning_ids):
            raise ValueError("generation warning IDs must be unique")
        mitigation_hashes = {
            warning.mitigation_receipt_set_hash
            for warning in self.generation_warnings
            if warning.status == "mitigated"
        }
        if mitigation_hashes and mitigation_hashes != {self.full_audit_receipt_set_hash}:
            raise ValueError("warning mitigation must bind the transcript audit receipt set")
        # Audio full-audit is universal.  It may additionally mitigate a
        # recognizer-confidence warning, but its receipt set remains required
        # even when every Recognizer supplied token confidence.

        if self.status == "accepted":
            blocking_severities = {"medium", "high", "blocking"}
            if not self.acceptance_policy.permit_unresolved_low_risk:
                blocking_severities.add("low")
            unresolved = [
                issue.id
                for issue in self.review_issues
                if issue.status == "unresolved" and issue.severity in blocking_severities
            ]
            if unresolved:
                raise ValueError(
                    "accepted transcript cannot contain unresolved medium/high/blocking "
                    "or policy-blocking low "
                    f"issues: {unresolved!r}"
                )
            pending_warnings = [
                warning.id
                for warning in self.generation_warnings
                if warning.status == "requires_full_audit"
            ]
            if pending_warnings:
                raise ValueError(
                    "accepted transcript cannot contain an unmitigated generation warning: "
                    f"{pending_warnings!r}"
                )
            if self.full_audit_receipt_set_hash is None:
                raise ValueError(
                    "accepted transcript requires a complete audio full-audit receipt set"
                )
            if self.verified_speech_coverage_receipt_hash is None:
                raise ValueError(
                    "accepted transcript requires verified independent speech coverage"
                )
        return self


class ReplacementLexemeAlignment(_Contract):
    """Evidence-backed exact timing for one replacement lexeme.

    Absence of this contract means the replacement has only its reviewed outer
    AudioSpan.  It must never be filled with proportional or character-derived
    timestamps.
    """

    schema_version: SchemaVersion = 1
    lexeme: str
    start_ms: int = Field(ge=0)
    end_ms: int = Field(gt=0)
    method: ReplacementAlignmentMethod
    evidence_ids: tuple[str, ...]

    @field_validator("lexeme")
    @classmethod
    def _safe_lexeme(cls, value: str) -> str:
        return _validate_subtitle_text(value, "aligned replacement lexeme")

    @model_validator(mode="after")
    def _alignment_shape(self) -> ReplacementLexemeAlignment:
        if self.end_ms <= self.start_ms:
            raise ValueError("replacement alignment end_ms must be greater than start_ms")
        if not self.evidence_ids:
            raise ValueError("replacement alignment requires Evidence IDs")
        if len(set(self.evidence_ids)) != len(self.evidence_ids):
            raise ValueError("replacement alignment Evidence IDs must be unique")
        return self


class CorrectionDecision(_Contract):
    """Append-only review event addressed by stable spans and audio range."""

    schema_version: SchemaVersion = 1
    event_id: str
    episode_id: str
    generation_id: str
    target_span_ids: tuple[str, ...]
    target_start_ms: int = Field(ge=0)
    target_end_ms: int = Field(gt=0)
    evidence_fingerprint: Sha256
    proposal_ids: tuple[str, ...] = ()
    arbitration_receipt_ids: tuple[str, ...] = ()
    issue_ids: tuple[str, ...]
    audio_evidence_ids: tuple[str, ...] = ()
    reference_evidence_ids: tuple[str, ...] = ()
    evidence_basis: DecisionEvidenceBasis = "audio"
    action: DecisionAction
    replacement_text: str | None = None
    selected_candidate: str | None = None
    replacement_lexemes: tuple[str, ...] = ()
    # Reserved for the future typed ReplacementAlignmentReceipt feature.  A
    # generic Recognition/Audio Evidence ID cannot attest newly-created exact
    # lexeme boundaries, so schema_version=1 accepts only the empty value.
    replacement_alignment: tuple[ReplacementLexemeAlignment, ...] = ()
    actor_kind: DecisionActorKind
    actor: str
    rationale: str
    timestamp: AwareDatetime

    @field_validator("replacement_text", "selected_candidate")
    @classmethod
    def _optional_safe_subtitle_text(cls, value: str | None, info: object) -> str | None:
        if value is None:
            return None
        return _validate_subtitle_text(value, getattr(info, "field_name", "value"))

    @model_validator(mode="after")
    def _decision_shape(self) -> CorrectionDecision:
        if not self.target_span_ids:
            raise ValueError("correction decision requires target_span_ids")
        if len(set(self.target_span_ids)) != len(self.target_span_ids):
            raise ValueError("target_span_ids must be unique")
        if self.target_end_ms <= self.target_start_ms:
            raise ValueError("target_end_ms must be greater than target_start_ms")
        if self.action == "replace" and not (self.replacement_text or "").strip():
            raise ValueError("replace action requires replacement_text")
        if (
            self.action in {"accept_candidate", "reject_candidate"}
            and not (self.selected_candidate or "").strip()
        ):
            raise ValueError(f"{self.action} action requires selected_candidate")
        for lexeme in self.replacement_lexemes:
            _validate_subtitle_text(lexeme, "replacement_lexeme")
        for label, values in (
            ("proposal_ids", self.proposal_ids),
            ("arbitration_receipt_ids", self.arbitration_receipt_ids),
            ("issue_ids", self.issue_ids),
            ("audio_evidence_ids", self.audio_evidence_ids),
            ("reference_evidence_ids", self.reference_evidence_ids),
        ):
            if len(set(values)) != len(values):
                raise ValueError(f"{label} must be unique")
        if self.replacement_alignment:
            raise ValueError(
                "replacement_alignment is disabled until a typed "
                "ReplacementAlignmentReceipt contract is implemented"
            )
        if not self.issue_ids:
            raise ValueError("correction decision requires issue_ids")
        if self.action in {"accept_candidate", "reject_candidate"} and not self.proposal_ids:
            raise ValueError(f"{self.action} action requires proposal_ids")
        if self.action == "accept_candidate" and not self.arbitration_receipt_ids:
            raise ValueError("accept_candidate requires an audio Arbitration Receipt")
        if self.action != "accept_candidate" and self.arbitration_receipt_ids:
            raise ValueError(f"{self.action} action cannot carry Arbitration Receipt IDs")
        changes_text = self.action in {"replace", "accept_candidate"}
        if self.action == "replace" and self.selected_candidate is not None:
            raise ValueError("replace action cannot carry selected_candidate")
        if self.action != "replace" and self.replacement_text is not None:
            raise ValueError(f"{self.action} action cannot carry replacement_text")
        if self.action not in {"accept_candidate", "reject_candidate"} and (
            self.selected_candidate is not None
        ):
            raise ValueError(f"{self.action} action cannot carry selected_candidate")
        if not changes_text and (self.replacement_lexemes or self.replacement_alignment):
            raise ValueError(f"{self.action} action cannot carry replacement lexemes or alignment")
        if changes_text:
            if not self.replacement_lexemes:
                raise ValueError("text-changing decision requires replacement_lexemes")
            expected_text = (
                self.replacement_text if self.action == "replace" else self.selected_candidate
            )
            if "".join(self.replacement_lexemes) != expected_text:
                raise ValueError(
                    "replacement_lexemes must concatenate exactly to the replacement text"
                )
        if changes_text and not self.audio_evidence_ids:
            raise ValueError("text-changing decision requires audio_evidence_ids")
        if self.evidence_basis == "audio_and_reference" and not self.reference_evidence_ids:
            raise ValueError("reference-backed decision requires reference_evidence_ids")
        if self.evidence_basis == "audio" and self.reference_evidence_ids:
            raise ValueError(
                "decision carrying Reference Evidence must declare audio_and_reference "
                "or reference_only basis"
            )
        if self.evidence_basis == "reference_only" and changes_text:
            raise ValueError("Reference Evidence alone cannot authorize a text change")
        if self.evidence_basis == "reference_only" and self.action not in {
            "reject_candidate",
            "defer",
        }:
            raise ValueError(
                "Reference Evidence alone may only defer or reject a reference candidate"
            )
        if self.evidence_basis == "reference_only" and not self.reference_evidence_ids:
            raise ValueError("reference-only decision requires reference_evidence_ids")
        if self.evidence_basis == "administrative" and changes_text:
            raise ValueError("administrative decision cannot change canonical text")
        if self.action == "replace" and self.actor_kind != "human":
            raise ValueError("free-text replace is restricted to a human reviewer")
        if self.timestamp.utcoffset() != timezone.utc.utcoffset(self.timestamp):
            raise ValueError("timestamp must use UTC")
        return self


class _StrictAuditContract(_Contract):
    """Audit contracts reject Pydantic coercion as an integrity boundary."""

    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)


class CorrectionAuditPolicySnapshot(_StrictAuditContract):
    """Immutable policy for the dormant correction-quality audit contract.

    The category tuples are closed and ordered.  A policy change therefore
    cannot silently remove a cell from the complete audit matrix.
    """

    schema_version: SchemaVersion = 1
    policy_id: Literal["nakama-correction-audit"] = "nakama-correction-audit"
    policy_version: Literal[1] = 1
    language: Literal["zh-Hant-TW"] = "zh-Hant-TW"
    span_categories: tuple[SpanAuditCategory, ...] = CORRECTION_AUDIT_SPAN_CATEGORIES
    boundary_categories: tuple[BoundaryAuditCategory, ...] = CORRECTION_AUDIT_BOUNDARY_CATEGORIES
    low_confidence_threshold: float = Field(default=0.75, ge=0.0, le=1.0)
    seam_match_tolerance_ms: int = Field(default=50, ge=0, le=5_000)
    reference_requires_audio_confirmation: Literal[True] = True
    missing_confidence_mode: Literal["required_review_trigger"] = "required_review_trigger"
    missing_reference_mode: Literal["unavailable_fail_closed"] = "unavailable_fail_closed"
    rule_set_id: Literal["nakama-correction-audit-signals"] = "nakama-correction-audit-signals"
    rule_set_version: Literal[1] = 1
    rule_set_hash: Sha256
    detector_rule_set: Literal["nakama-correction-audit-signals-v1"] = (
        "nakama-correction-audit-signals-v1"
    )
    candidate_grouping: Literal["overlap-connected-components-v1"] = (
        "overlap-connected-components-v1"
    )

    @field_validator("reference_requires_audio_confirmation", mode="before")
    @classmethod
    def _audio_confirmation_literal_is_a_bool(cls, value: object) -> object:
        if type(value) is not bool:
            raise ValueError("reference_requires_audio_confirmation requires exact bool")
        return value

    @model_validator(mode="after")
    def _closed_category_matrix(self) -> CorrectionAuditPolicySnapshot:
        if self.span_categories != CORRECTION_AUDIT_SPAN_CATEGORIES:
            raise ValueError("correction audit span categories are a closed ordered set")
        if self.boundary_categories != CORRECTION_AUDIT_BOUNDARY_CATEGORIES:
            raise ValueError("correction audit boundary categories are a closed ordered set")
        expected_rule_hash = _hash_json(
            {
                "rule_set_id": self.rule_set_id,
                "rule_set_version": self.rule_set_version,
                "language": self.language,
                "span_categories": self.span_categories,
                "boundary_categories": self.boundary_categories,
                "low_confidence_threshold": self.low_confidence_threshold,
                "seam_match_tolerance_ms": self.seam_match_tolerance_ms,
                "reference_requires_audio_confirmation": (
                    self.reference_requires_audio_confirmation
                ),
                "missing_confidence_mode": self.missing_confidence_mode,
                "missing_reference_mode": self.missing_reference_mode,
                "detector_rule_set": self.detector_rule_set,
                "candidate_grouping": self.candidate_grouping,
            }
        )
        if self.rule_set_hash != expected_rule_hash:
            raise ValueError("correction audit policy rule_set_hash mismatch")
        return self


class RecognitionSeamObservation(_StrictAuditContract):
    """One typed recognizer seam observation; never Audio Evidence."""

    schema_version: SchemaVersion = 1
    id: str
    seam_ms: int = Field(ge=0)
    status: Literal["matched", "duplicate", "conflict"]
    raw_artifact_hash: Sha256
    left_chunk_id: str
    right_chunk_id: str
    left_chunk_receipt_hash: Sha256
    right_chunk_receipt_hash: Sha256
    observation_hash: Sha256

    @field_validator("id", "left_chunk_id", "right_chunk_id")
    @classmethod
    def _seam_id_is_bounded(cls, value: str) -> str:
        return _validate_bounded_metadata(value, "seam observation id", maximum=256)

    @model_validator(mode="after")
    def _seam_sources_are_distinct(self) -> RecognitionSeamObservation:
        if self.left_chunk_id == self.right_chunk_id:
            raise ValueError("seam observation requires two distinct chunk IDs")
        expected = _hash_json(self.model_dump(mode="json", exclude={"observation_hash"}))
        if self.observation_hash != expected:
            raise ValueError("seam observation hash mismatch")
        return self


class RecognitionSeamEvidence(_StrictAuditContract):
    """Minimal content-addressed wrapper around optional ASR seam observations."""

    schema_version: SchemaVersion = 1
    status: Literal["unchunked", "complete", "conflicted"]
    normalized_audio_hash: Sha256
    recognition_evidence_hashes: tuple[Sha256, ...]
    raw_artifact_hash: Sha256
    observations: tuple[RecognitionSeamObservation, ...] = ()
    failure_reason: str | None = None
    content_hash: Sha256

    @field_validator("failure_reason")
    @classmethod
    def _seam_failure_is_bounded(cls, value: str | None) -> str | None:
        if value is None:
            return None
        return _validate_bounded_metadata(value, "seam failure_reason", maximum=2_048)

    @model_validator(mode="after")
    def _seam_evidence_shape(self) -> RecognitionSeamEvidence:
        if not self.recognition_evidence_hashes:
            raise ValueError("seam evidence requires Recognition Evidence hashes")
        if tuple(sorted(self.recognition_evidence_hashes)) != self.recognition_evidence_hashes:
            raise ValueError("seam Recognition Evidence hashes must be sorted")
        if len(set(self.recognition_evidence_hashes)) != len(self.recognition_evidence_hashes):
            raise ValueError("seam Recognition Evidence hashes must be unique")
        ids = [item.id for item in self.observations]
        if len(set(ids)) != len(ids):
            raise ValueError("seam observations require unique IDs")
        if tuple(sorted(self.observations, key=lambda item: (item.seam_ms, item.id))) != (
            self.observations
        ):
            raise ValueError("seam observations must be ordered by time and ID")
        if any(item.raw_artifact_hash != self.raw_artifact_hash for item in self.observations):
            raise ValueError("seam observation raw artifact differs from its wrapper")
        conflicts = [item for item in self.observations if item.status != "matched"]
        if self.status == "unchunked" and self.observations:
            raise ValueError("unchunked seam evidence cannot claim seam observations")
        if self.status == "complete" and (
            not self.observations or any(item.status != "matched" for item in self.observations)
        ):
            raise ValueError("complete seam evidence requires only matched observations")
        if self.status == "conflicted" and not conflicts:
            raise ValueError("conflicted seam evidence requires a conflict observation")
        if self.failure_reason is not None and self.status != "conflicted":
            raise ValueError("only conflicted seam evidence may carry failure_reason")
        expected = _hash_json(self.model_dump(mode="json", exclude={"content_hash"}))
        if self.content_hash != expected:
            raise ValueError("seam evidence content_hash mismatch")
        return self


class AuditInputBindings(_StrictAuditContract):
    """Exact immutable upstream identities used to derive one audit plan."""

    schema_version: SchemaVersion = 1
    canonical_transcript_hash: Sha256
    canonical_content_hash: Sha256
    normalized_audio_hash: Sha256
    recognition_evidence_hashes: tuple[Sha256, ...]
    recognition_evidence_set_hash: Sha256
    confidence_status: AuditMetadataInputStatus
    speaker_status: AuditMetadataInputStatus
    reference_evidence_hash: Sha256
    reference_status: AuditReferenceInputStatus
    reference_retrieval_receipt_hashes: tuple[Sha256, ...] = ()
    reference_retrieval_receipt_set_hash: Sha256
    speech_coverage_status: AuditCoverageInputStatus
    speech_coverage_receipt_hash: Sha256 | None = None
    boundary_constraint_status: AuditBoundaryInputStatus
    boundary_constraint_receipt_hash: Sha256 | None = None
    seam_status: AuditSeamInputStatus
    seam_evidence_hash: Sha256 | None = None
    content_hash: Sha256

    @model_validator(mode="after")
    def _input_bindings_are_exact(self) -> AuditInputBindings:
        for label, values in (
            ("recognition_evidence_hashes", self.recognition_evidence_hashes),
            (
                "reference_retrieval_receipt_hashes",
                self.reference_retrieval_receipt_hashes,
            ),
        ):
            if tuple(sorted(values)) != values or len(set(values)) != len(values):
                raise ValueError(f"{label} must be sorted and unique")
        if not self.recognition_evidence_hashes:
            raise ValueError("audit input bindings require Recognition Evidence")
        if self.reference_status == "not_enrolled" and (self.reference_retrieval_receipt_hashes):
            raise ValueError("no-source reference state cannot carry retrieval receipts")
        if self.reference_status == "completed" and not (self.reference_retrieval_receipt_hashes):
            raise ValueError("completed reference state requires retrieval receipts")
        for status, artifact_hash, label in (
            (
                self.speech_coverage_status,
                self.speech_coverage_receipt_hash,
                "speech coverage",
            ),
            (self.seam_status, self.seam_evidence_hash, "seam evidence"),
        ):
            absent = status == "absent"
            if absent and artifact_hash is not None:
                raise ValueError(f"absent {label} cannot carry an artifact hash")
            if not absent and artifact_hash is None:
                raise ValueError(f"{status} {label} requires an artifact hash")
        if self.boundary_constraint_status == "absent" and (
            self.boundary_constraint_receipt_hash is not None
        ):
            raise ValueError("absent boundary constraint cannot carry a receipt hash")
        if self.boundary_constraint_status == "verified" and (
            self.boundary_constraint_receipt_hash is None
        ):
            raise ValueError("verified boundary constraint requires a receipt hash")
        expected = _hash_json(self.model_dump(mode="json", exclude={"content_hash"}))
        if self.content_hash != expected:
            raise ValueError("audit input bindings content_hash mismatch")
        return self


class SpanAuditTarget(_StrictAuditContract):
    """One stable Canonical span target in the complete audit matrix."""

    schema_version: SchemaVersion = 1
    id: Sha256
    basis_hash: Sha256
    ordinal: int = Field(ge=0)
    span_id: str
    token_ids: tuple[str, ...]
    token_start_index: int = Field(ge=0)
    token_end_index: int = Field(gt=0)
    observed_text: str
    observed_text_hash: Sha256
    start_ms: int = Field(ge=0)
    end_ms: int = Field(gt=0)

    @field_validator("span_id")
    @classmethod
    def _span_target_id_is_bounded(cls, value: str) -> str:
        return _validate_bounded_metadata(value, "span_id", maximum=256)

    @field_validator("observed_text")
    @classmethod
    def _span_target_text_is_safe(cls, value: str) -> str:
        return _validate_subtitle_text(value, "span audit observed_text")

    @model_validator(mode="after")
    def _span_target_is_content_addressed(self) -> SpanAuditTarget:
        if not self.token_ids or len(set(self.token_ids)) != len(self.token_ids):
            raise ValueError("span audit target requires unique ordered token IDs")
        if self.token_end_index - self.token_start_index != len(self.token_ids):
            raise ValueError("span audit target token range length mismatch")
        if self.end_ms <= self.start_ms:
            raise ValueError("span audit target must have positive duration")
        expected_text_hash = hashlib.sha256(self.observed_text.encode("utf-8")).hexdigest()
        if self.observed_text_hash != expected_text_hash:
            raise ValueError("span audit observed_text_hash mismatch")
        payload = self.model_dump(mode="json", exclude={"id"})
        if self.id != _hash_json(payload):
            raise ValueError("span audit target id mismatch")
        return self


class BoundaryAuditTarget(_StrictAuditContract):
    """One of exactly N+1 episode boundary/gap targets for N Canonical spans."""

    schema_version: SchemaVersion = 1
    id: Sha256
    basis_hash: Sha256
    ordinal: int = Field(ge=0)
    left_span_id: str | None = None
    right_span_id: str | None = None
    left_token_id: str | None = None
    right_token_id: str | None = None
    window_start_ms: int = Field(ge=0)
    window_end_ms: int = Field(ge=0)

    @model_validator(mode="after")
    def _boundary_target_is_content_addressed(self) -> BoundaryAuditTarget:
        if (self.left_span_id is None) != (self.left_token_id is None):
            raise ValueError("left boundary span/token identity must be present together")
        if (self.right_span_id is None) != (self.right_token_id is None):
            raise ValueError("right boundary span/token identity must be present together")
        if self.left_span_id is None and self.right_span_id is None:
            raise ValueError("boundary target requires at least one neighbouring span")
        if self.window_end_ms < self.window_start_ms:
            raise ValueError("boundary target window must not run backwards")
        payload = self.model_dump(mode="json", exclude={"id"})
        if self.id != _hash_json(payload):
            raise ValueError("boundary audit target id mismatch")
        return self


class AuditCell(_StrictAuditContract):
    """One deterministic category assessment slot, not an audit disposition."""

    schema_version: SchemaVersion = 1
    id: Sha256
    basis_hash: Sha256
    target_kind: AuditTargetKind
    target_id: Sha256
    category: SpanAuditCategory | BoundaryAuditCategory
    applicability: AuditApplicability
    applicability_reason: AuditApplicabilityReason

    @model_validator(mode="after")
    def _cell_is_typed_and_content_addressed(self) -> AuditCell:
        if self.target_kind == "span" and self.category not in (CORRECTION_AUDIT_SPAN_CATEGORIES):
            raise ValueError("span audit cell carries a boundary category")
        if self.target_kind == "boundary" and self.category not in (
            CORRECTION_AUDIT_BOUNDARY_CATEGORIES
        ):
            raise ValueError("boundary audit cell carries a span category")
        unavailable_reasons = {"upstream_missing", "upstream_failed"}
        not_applicable_reasons = {
            "pattern_absent",
            "no_sources_enrolled",
            "no_reference_support",
            "verified_clean",
            "endpoint_not_applicable",
            "no_matching_seam",
        }
        if self.applicability == "unavailable" and (
            self.applicability_reason not in unavailable_reasons
        ):
            raise ValueError("unavailable audit cell requires an upstream failure reason")
        if self.applicability == "not_applicable" and (
            self.applicability_reason not in not_applicable_reasons
        ):
            raise ValueError("not-applicable audit cell has an incompatible reason")
        if self.applicability == "required" and self.applicability_reason in (
            unavailable_reasons | not_applicable_reasons
        ):
            raise ValueError("required audit cell has an incompatible reason")
        payload = self.model_dump(mode="json", exclude={"id"})
        if self.id != _hash_json(payload):
            raise ValueError("audit cell id mismatch")
        return self


class AuditPlan(_StrictAuditContract):
    """Complete structural audit plan; it does not prove semantic recall."""

    schema_version: SchemaVersion = 1
    origin: Literal["native", "reconstructed"] = "native"
    episode_id: str
    generation_id: str
    policy: CorrectionAuditPolicySnapshot
    policy_hash: Sha256
    inputs: AuditInputBindings
    basis_hash: Sha256
    builder_id: Literal["nakama-correction-audit-plan"]
    builder_version: Literal[1]
    builder_code_hash: Sha256
    span_targets: tuple[SpanAuditTarget, ...]
    boundary_targets: tuple[BoundaryAuditTarget, ...]
    cells: tuple[AuditCell, ...]
    execution_status: Literal["dormant_not_executed"] = "dormant_not_executed"
    completeness_statement: Literal["structural_completeness_only_not_semantic_recall_proof"] = (
        "structural_completeness_only_not_semantic_recall_proof"
    )
    content_hash: Sha256

    @field_validator("episode_id", "generation_id")
    @classmethod
    def _audit_plan_identity_is_bounded(cls, value: str, info: object) -> str:
        return _validate_bounded_metadata(
            value, getattr(info, "field_name", "audit plan identity"), maximum=256
        )

    @model_validator(mode="after")
    def _audit_plan_is_complete(self) -> AuditPlan:
        if self.policy_hash != _hash_json(self.policy.model_dump(mode="json")):
            raise ValueError("audit plan policy_hash mismatch")
        expected_basis = _hash_json(
            {
                "episode_id": self.episode_id,
                "generation_id": self.generation_id,
                "canonical_transcript_hash": self.inputs.canonical_transcript_hash,
                "canonical_content_hash": self.inputs.canonical_content_hash,
                "normalized_audio_hash": self.inputs.normalized_audio_hash,
                "policy_hash": self.policy_hash,
                "input_binding_hash": self.inputs.content_hash,
                "builder_id": self.builder_id,
                "builder_version": self.builder_version,
                "builder_code_hash": self.builder_code_hash,
            }
        )
        if self.basis_hash != expected_basis:
            raise ValueError("audit plan basis_hash mismatch")
        if not self.span_targets:
            raise ValueError("audit plan requires Canonical span targets")
        if tuple(item.ordinal for item in self.span_targets) != tuple(
            range(len(self.span_targets))
        ):
            raise ValueError("span audit target ordinals must be complete and ordered")
        if any(item.basis_hash != self.basis_hash for item in self.span_targets):
            raise ValueError("span audit target basis differs from its AuditPlan")
        if len(self.boundary_targets) != len(self.span_targets) + 1:
            raise ValueError("N Canonical spans require exactly N+1 boundary targets")
        if tuple(item.ordinal for item in self.boundary_targets) != tuple(
            range(len(self.boundary_targets))
        ):
            raise ValueError("boundary audit target ordinals must be complete and ordered")
        if any(item.basis_hash != self.basis_hash for item in self.boundary_targets):
            raise ValueError("boundary audit target basis differs from its AuditPlan")
        for index, target in enumerate(self.boundary_targets):
            expected_left = None if index == 0 else self.span_targets[index - 1]
            expected_right = None if index == len(self.span_targets) else self.span_targets[index]
            if target.left_span_id != (
                expected_left.span_id if expected_left is not None else None
            ) or target.right_span_id != (
                expected_right.span_id if expected_right is not None else None
            ):
                raise ValueError("boundary target topology drifted from span order")
            if target.left_token_id != (
                expected_left.token_ids[-1] if expected_left is not None else None
            ) or target.right_token_id != (
                expected_right.token_ids[0] if expected_right is not None else None
            ):
                raise ValueError("boundary target token topology drifted from span order")
        expected_cells = tuple(
            ("span", target.id, category)
            for target in self.span_targets
            for category in self.policy.span_categories
        ) + tuple(
            ("boundary", target.id, category)
            for target in self.boundary_targets
            for category in self.policy.boundary_categories
        )
        actual_cells = tuple(
            (cell.target_kind, cell.target_id, cell.category) for cell in self.cells
        )
        if actual_cells != expected_cells:
            raise ValueError("audit cells must be the exact ordered Cartesian product")
        if any(cell.basis_hash != self.basis_hash for cell in self.cells):
            raise ValueError("audit cell basis differs from its AuditPlan")
        if len({cell.id for cell in self.cells}) != len(self.cells):
            raise ValueError("audit cells require unique content IDs")
        expected = _hash_json(self.model_dump(mode="json", exclude={"content_hash"}))
        if self.content_hash != expected:
            raise ValueError("audit plan content_hash mismatch")
        return self


class CandidateSourceBinding(_StrictAuditContract):
    """One mechanically paired source identity and content digest."""

    schema_version: SchemaVersion = 1
    kind: CandidateSourceBindingKind
    id: str
    content_hash: Sha256
    record_ids: tuple[str, ...]

    @field_validator("id")
    @classmethod
    def _source_binding_id_is_bounded(cls, value: str) -> str:
        return _validate_bounded_metadata(value, "candidate source binding id", maximum=512)

    @model_validator(mode="after")
    def _source_binding_records_are_exact(self) -> CandidateSourceBinding:
        if not self.record_ids:
            raise ValueError("candidate source binding requires record IDs")
        if tuple(sorted(self.record_ids)) != self.record_ids or len(set(self.record_ids)) != len(
            self.record_ids
        ):
            raise ValueError("candidate source binding record IDs must be sorted and unique")
        return self


class CandidateSignal(_StrictAuditContract):
    """Deterministic review trigger; never a Decision, Proposal, or Audio proof."""

    schema_version: SchemaVersion = 1
    id: Sha256
    audit_plan_hash: Sha256
    target_kind: AuditTargetKind
    target_id: Sha256
    audit_cell_id: Sha256
    category: SpanAuditCategory | BoundaryAuditCategory
    span_ids: tuple[str, ...]
    token_ids: tuple[str, ...]
    token_start_index: int = Field(ge=0)
    token_end_index: int = Field(gt=0)
    observed_text: str
    code: CandidateSignalCode
    authority: CandidateSignalAuthority = "review_trigger_only"
    generator_id: Literal["nakama-correction-candidate-signals"] = (
        "nakama-correction-candidate-signals"
    )
    generator_version: Literal[1] = 1
    generator_code_hash: Sha256
    rule_set_hash: Sha256
    evidence_kind: CandidateSignalEvidenceKind
    source_bindings: tuple[CandidateSourceBinding, ...]
    reference_evidence_ids: tuple[str, ...] = ()
    reference_support_kind: ReferenceRetrievalSupportKind | None = None
    reference_query_id: str | None = None
    reference_query_support_start: int | None = Field(default=None, ge=0)
    reference_query_support_end: int | None = Field(default=None, gt=0)
    candidate_text: str | None = None
    candidate_text_hash: Sha256 | None = None
    detail_hash: Sha256
    is_audio_evidence: Literal[False] = False
    proposal_created: Literal[False] = False

    @field_validator("is_audio_evidence", "proposal_created", mode="before")
    @classmethod
    def _false_signal_literals_are_bools(cls, value: object) -> object:
        if type(value) is not bool:
            raise ValueError("candidate signal authority flags require exact bool")
        return value

    @field_validator("observed_text", "candidate_text")
    @classmethod
    def _optional_signal_text_is_safe(cls, value: str | None, info: object) -> str | None:
        if value is None:
            return None
        return _validate_subtitle_text(value, getattr(info, "field_name", "candidate signal text"))

    @model_validator(mode="after")
    def _signal_authority_is_bounded(self) -> CandidateSignal:
        if self.target_kind == "span" and self.category not in (CORRECTION_AUDIT_SPAN_CATEGORIES):
            raise ValueError("span CandidateSignal carries a boundary category")
        if self.target_kind == "boundary" and self.category not in (
            CORRECTION_AUDIT_BOUNDARY_CATEGORIES
        ):
            raise ValueError("boundary CandidateSignal carries a span category")
        if not self.span_ids or len(set(self.span_ids)) != len(self.span_ids):
            raise ValueError("candidate signal requires unique Canonical span IDs")
        if not self.token_ids or len(set(self.token_ids)) != len(self.token_ids):
            raise ValueError("candidate signal requires unique Canonical token IDs")
        if self.token_end_index <= self.token_start_index:
            raise ValueError("candidate signal token range must be non-empty")
        if self.token_end_index - self.token_start_index != len(self.token_ids):
            raise ValueError("candidate signal token range length mismatch")
        if not self.source_bindings:
            raise ValueError("candidate signal requires typed source bindings")
        binding_order = tuple(
            sorted(
                self.source_bindings,
                key=lambda item: (item.kind, item.id, item.content_hash, item.record_ids),
            )
        )
        if binding_order != self.source_bindings:
            raise ValueError("candidate source bindings must be canonically ordered")
        binding_keys = [(item.kind, item.id, item.content_hash) for item in self.source_bindings]
        if len(set(binding_keys)) != len(binding_keys):
            raise ValueError("candidate source bindings must be unique")
        binding_kinds = {item.kind for item in self.source_bindings}
        allowed_by_evidence = {
            "deterministic_text": {"canonical_transcript"},
            "recognition_metadata": {
                "recognition_evidence",
                "recognition_raw_artifact",
                "canonical_transcript",
            },
            "reference_evidence": {
                "reference_retrieval_receipt_set",
                "reference_retrieval_receipt",
                "reference_source_artifact",
                "reference_excerpt",
            },
            "speech_coverage": {
                "speech_coverage_receipt",
                "speech_coverage_raw_artifact",
                "recognition_evidence_set",
            },
            "semantic_constraint": {"boundary_constraint_receipt"},
            "seam_observation": {
                "seam_evidence",
                "seam_observation",
                "recognition_raw_artifact",
                "recognition_evidence_set",
            },
            "speaker_metadata": {
                "canonical_transcript",
                "recognition_evidence",
                "recognition_raw_artifact",
            },
        }
        if not binding_kinds <= allowed_by_evidence[self.evidence_kind]:
            raise ValueError("candidate source binding kind contradicts evidence_kind")
        if self.evidence_kind == "deterministic_text" and binding_kinds != {"canonical_transcript"}:
            raise ValueError("deterministic text signal requires Canonical binding")
        if (
            self.evidence_kind == "recognition_metadata"
            and not {
                "recognition_evidence",
                "recognition_raw_artifact",
            }
            <= binding_kinds
        ):
            raise ValueError("recognition metadata requires Evidence and raw bindings")
        if self.evidence_kind == "semantic_constraint" and binding_kinds != {
            "boundary_constraint_receipt"
        }:
            raise ValueError("semantic constraint requires its exact receipt binding")
        if self.evidence_kind == "speech_coverage" and not (
            {"speech_coverage_receipt", "speech_coverage_raw_artifact"} & binding_kinds
            or binding_kinds == {"recognition_evidence_set"}
        ):
            raise ValueError("speech coverage signal lacks an admissible source binding")
        if self.evidence_kind == "seam_observation" and not (
            {"seam_evidence", "seam_observation"} & binding_kinds
            or binding_kinds == {"recognition_evidence_set"}
        ):
            raise ValueError("seam signal lacks an admissible seam source binding")
        if len(set(self.reference_evidence_ids)) != len(self.reference_evidence_ids):
            raise ValueError("candidate signal Reference Evidence IDs must be unique")
        if tuple(sorted(self.reference_evidence_ids)) != self.reference_evidence_ids:
            raise ValueError("candidate signal Reference Evidence IDs must be sorted")
        support_values = (
            self.reference_support_kind,
            self.reference_query_id,
            self.reference_query_support_start,
            self.reference_query_support_end,
        )
        if any(item is None for item in support_values) != all(
            item is None for item in support_values
        ):
            raise ValueError("reference support provenance fields are all-or-none")
        if self.reference_query_support_start is not None and (
            self.reference_query_support_end <= self.reference_query_support_start
        ):
            raise ValueError("reference query support range must be non-empty")
        reference_failure_codes = {
            "reference_retrieval_failed",
            "reference_retrieval_incomplete",
        }
        if self.evidence_kind == "reference_evidence":
            if self.code not in reference_failure_codes and (
                not self.reference_evidence_ids or self.reference_support_kind is None
            ):
                raise ValueError("reference signal requires exact support provenance")
            if self.code in reference_failure_codes and any(
                item is not None for item in support_values
            ):
                raise ValueError("reference retrieval failure cannot claim hit support")
            if self.code in reference_failure_codes:
                if not binding_kinds <= {
                    "reference_retrieval_receipt_set",
                    "reference_retrieval_receipt",
                }:
                    raise ValueError("reference failure cannot claim source/excerpt bindings")
            else:
                required_reference_kinds = {
                    "reference_retrieval_receipt",
                    "reference_source_artifact",
                    "reference_excerpt",
                }
                if not required_reference_kinds <= binding_kinds:
                    raise ValueError("reference hit requires receipt/source/excerpt bindings")
                excerpt_record_ids = {
                    record_id
                    for binding in self.source_bindings
                    if binding.kind == "reference_excerpt"
                    for record_id in binding.record_ids
                }
                if not set(self.reference_evidence_ids) <= excerpt_record_ids:
                    raise ValueError("Reference Evidence IDs are not covered by excerpt bindings")
        elif self.reference_evidence_ids or self.reference_support_kind is not None:
            raise ValueError("non-reference signal cannot claim Reference provenance")
        category_codes = {
            "lexical_fidelity": {"speech_coverage_uncovered_inside_span"},
            "reference_name_term": {
                "reference_candidate_term_exact",
                "reference_phonetic_support_without_exact_candidate",
                "reference_lexical_support_without_exact_candidate",
                "reference_retrieval_failed",
                "reference_retrieval_incomplete",
            },
            "numeric_expression": {
                "numeric_arabic_digits_detected",
                "numeric_han_number_detected",
                "numeric_decimal_detected",
                "numeric_percent_detected",
                "numeric_range_detected",
                "numeric_date_time_detected",
                "numeric_version_detected",
                "numeric_unit_or_classifier_detected",
            },
            "negation_polarity": {"negation_detected"},
            "latin_code_switch": {"latin_code_switch_detected"},
            "acronym_initialism": {"acronym_initialism_detected"},
            "repetition_disfluency": {
                "repetition_detected",
                "filler_detected",
                "self_repair_detected",
            },
            "confidence_integrity": {
                "recognition_confidence_missing",
                "recognition_low_confidence",
            },
            "speaker_identity": {
                "speaker_identity_missing",
                "speaker_identity_mixed",
                "speaker_identity_disagreement",
            },
            "cross_span_fidelity": {
                "semantic_boundary_uncertain",
                "semantic_boundary_forbidden",
            },
            "chunk_seam": {
                "chunk_seam_duplicate",
                "chunk_seam_conflict",
                "chunk_seam_unavailable",
            },
            "speech_coverage": {
                "speech_coverage_uncovered_at_boundary",
                "speech_coverage_unavailable",
            },
            "speaker_transition": {
                "speaker_change_detected",
                "speaker_transition_ambiguous",
            },
            "repetition_self_repair": {
                "cross_span_repetition_detected",
                "cross_span_self_repair_detected",
            },
        }
        if self.code not in category_codes[self.category]:
            raise ValueError("candidate signal code is incompatible with audit category")
        if self.candidate_text is None:
            if self.candidate_text_hash is not None:
                raise ValueError("signal without candidate text cannot claim its hash")
        else:
            if self.code != "reference_candidate_term_exact":
                raise ValueError("only exact reference-term signal may carry candidate text")
            if self.evidence_kind != "reference_evidence" or not (self.reference_evidence_ids):
                raise ValueError("candidate text requires exact Reference Evidence")
            expected_candidate_hash = hashlib.sha256(
                self.candidate_text.encode("utf-8")
            ).hexdigest()
            if self.candidate_text_hash != expected_candidate_hash:
                raise ValueError("candidate signal candidate_text_hash mismatch")
        payload = self.model_dump(mode="json", exclude={"id"})
        if self.id != _hash_json(payload):
            raise ValueError("candidate signal id mismatch")
        return self


class CandidateSignalSet(_StrictAuditContract):
    """Complete deterministic signal derivation for an exact AuditPlan."""

    schema_version: SchemaVersion = 1
    audit_plan_hash: Sha256
    audit_plan_content_hash: Sha256
    audit_input_hash: Sha256
    builder_id: Literal["nakama-correction-candidate-signals"]
    builder_version: Literal[1]
    builder_code_hash: Sha256
    rule_set_hash: Sha256
    authority: CandidateSignalAuthority = "review_trigger_only"
    derivation_status: Literal["complete_recomputation"] = "complete_recomputation"
    recall_statement: Literal["signals_are_not_semantic_recall_proof"] = (
        "signals_are_not_semantic_recall_proof"
    )
    signals: tuple[CandidateSignal, ...]
    content_hash: Sha256

    @model_validator(mode="after")
    def _signal_set_is_content_addressed(self) -> CandidateSignalSet:
        if tuple(sorted(self.signals, key=lambda item: item.id)) != self.signals:
            raise ValueError("candidate signals must be ordered by content ID")
        if len({item.id for item in self.signals}) != len(self.signals):
            raise ValueError("candidate signals require unique IDs")
        if any(item.audit_plan_hash != self.audit_plan_hash for item in self.signals):
            raise ValueError("candidate signal belongs to another AuditPlan")
        if any(
            item.generator_id != self.builder_id
            or item.generator_version != self.builder_version
            or item.generator_code_hash != self.builder_code_hash
            for item in self.signals
        ):
            raise ValueError("candidate signal generator identity drift")
        if any(item.rule_set_hash != self.rule_set_hash for item in self.signals):
            raise ValueError("candidate signal rule-set identity drift")
        expected = _hash_json(self.model_dump(mode="json", exclude={"content_hash"}))
        if self.content_hash != expected:
            raise ValueError("candidate signal set content_hash mismatch")
        return self


class CandidateOption(_StrictAuditContract):
    """One non-authoritative exact-text alternative retained for later audit."""

    schema_version: SchemaVersion = 1
    id: Sha256
    token_start_index: int = Field(ge=0)
    token_end_index: int = Field(gt=0)
    target_span_ids: tuple[str, ...]
    observed_text: str
    candidate_text: str
    candidate_text_hash: Sha256
    signal_ids: tuple[Sha256, ...]
    reference_evidence_ids: tuple[str, ...]
    authority: CandidateSignalAuthority = "review_trigger_only"
    requires_audio_confirmation: Literal[True] = True
    is_audio_evidence: Literal[False] = False
    proposal_created: Literal[False] = False

    @field_validator(
        "requires_audio_confirmation", "is_audio_evidence", "proposal_created", mode="before"
    )
    @classmethod
    def _candidate_option_literals_are_bools(cls, value: object) -> object:
        if type(value) is not bool:
            raise ValueError("candidate option authority flags require exact bool")
        return value

    @field_validator("observed_text", "candidate_text")
    @classmethod
    def _candidate_option_text_is_safe(cls, value: str, info: object) -> str:
        return _validate_subtitle_text(value, getattr(info, "field_name", "candidate option text"))

    @model_validator(mode="after")
    def _candidate_option_is_exact(self) -> CandidateOption:
        if self.token_end_index <= self.token_start_index:
            raise ValueError("candidate option token range must be non-empty")
        if not self.target_span_ids or len(set(self.target_span_ids)) != len(self.target_span_ids):
            raise ValueError("candidate option requires unique target span IDs")
        if not self.signal_ids or tuple(sorted(self.signal_ids)) != self.signal_ids:
            raise ValueError("candidate option signal IDs must be non-empty and sorted")
        if len(set(self.signal_ids)) != len(self.signal_ids):
            raise ValueError("candidate option signal IDs must be unique")
        if not self.reference_evidence_ids or len(set(self.reference_evidence_ids)) != len(
            self.reference_evidence_ids
        ):
            raise ValueError("candidate option requires unique Reference Evidence IDs")
        if tuple(sorted(self.reference_evidence_ids)) != self.reference_evidence_ids:
            raise ValueError("candidate option Reference Evidence IDs must be sorted")
        if self.observed_text == self.candidate_text:
            raise ValueError("candidate option cannot be an observed-text no-op")
        expected_candidate_hash = hashlib.sha256(self.candidate_text.encode("utf-8")).hexdigest()
        if self.candidate_text_hash != expected_candidate_hash:
            raise ValueError("candidate option candidate_text_hash mismatch")
        payload = self.model_dump(mode="json", exclude={"id"})
        if self.id != _hash_json(payload):
            raise ValueError("candidate option id mismatch")
        return self


class CandidateGroup(_StrictAuditContract):
    """One lossless signal component, including anomaly-only discovery work."""

    schema_version: SchemaVersion = 1
    id: Sha256
    component_index: int = Field(ge=0)
    overlap_component_id: Sha256
    token_start_index: int = Field(ge=0)
    token_end_index: int = Field(gt=0)
    target_span_ids: tuple[str, ...]
    observed_text: str
    signal_ids: tuple[Sha256, ...]
    options: tuple[CandidateOption, ...]
    discovery_required: bool

    @field_validator("observed_text")
    @classmethod
    def _group_observed_text_is_safe(cls, value: str) -> str:
        return _validate_subtitle_text(value, "candidate group observed_text")

    @model_validator(mode="after")
    def _candidate_group_is_content_addressed(self) -> CandidateGroup:
        if self.token_end_index <= self.token_start_index:
            raise ValueError("candidate group token range must be non-empty")
        for label, values in (
            ("target_span_ids", self.target_span_ids),
            ("signal_ids", self.signal_ids),
        ):
            if not values or len(set(values)) != len(values):
                raise ValueError(f"candidate group requires unique non-empty {label}")
        if tuple(sorted(self.signal_ids)) != self.signal_ids:
            raise ValueError("candidate group signal IDs must be sorted")
        if tuple(sorted(self.options, key=lambda item: item.id)) != self.options:
            raise ValueError("candidate group options must be ordered by content ID")
        option_signal_ids = {
            signal_id for option in self.options for signal_id in option.signal_ids
        }
        if option_signal_ids - set(self.signal_ids):
            raise ValueError("candidate group option refers to a signal outside its component")
        if not self.options and not self.discovery_required:
            raise ValueError("anomaly-only group must require candidate discovery")
        expected_overlap_id = _hash_json(
            {
                "token_start_index": self.token_start_index,
                "token_end_index": self.token_end_index,
                "signal_ids": self.signal_ids,
            }
        )
        if self.overlap_component_id != expected_overlap_id:
            raise ValueError("candidate group overlap_component_id mismatch")
        payload = self.model_dump(mode="json", exclude={"id"})
        if self.id != _hash_json(payload):
            raise ValueError("candidate group id mismatch")
        return self


class CandidateGroupSet(_StrictAuditContract):
    """Lossless overlap grouping; still dormant and creates no Proposal."""

    schema_version: SchemaVersion = 1
    signal_set_hash: Sha256
    signal_set_content_hash: Sha256
    audit_plan_hash: Sha256
    audit_plan_content_hash: Sha256
    audit_input_hash: Sha256
    grouping_algorithm: Literal["overlap-connected-components-v1"]
    builder_id: Literal["nakama-correction-candidate-groups"]
    builder_version: Literal[1]
    builder_code_hash: Sha256
    signal_ids: tuple[Sha256, ...]
    groups: tuple[CandidateGroup, ...]
    proposal_status: Literal["dormant_no_proposals_created"] = "dormant_no_proposals_created"
    content_hash: Sha256

    @model_validator(mode="after")
    def _group_set_is_lossless(self) -> CandidateGroupSet:
        if tuple(sorted(self.signal_ids)) != self.signal_ids or len(set(self.signal_ids)) != len(
            self.signal_ids
        ):
            raise ValueError("candidate group-set signal IDs must be sorted and unique")
        if tuple(item.component_index for item in self.groups) != tuple(range(len(self.groups))):
            raise ValueError("candidate group component indices must be complete")
        grouped_ids = [signal_id for group in self.groups for signal_id in group.signal_ids]
        if tuple(sorted(grouped_ids)) != self.signal_ids or len(grouped_ids) != len(
            set(grouped_ids)
        ):
            raise ValueError("candidate groups must retain every signal exactly once")
        if bool(self.signal_ids) != bool(self.groups):
            raise ValueError("candidate signals and groups must be empty together")
        expected = _hash_json(self.model_dump(mode="json", exclude={"content_hash"}))
        if self.content_hash != expected:
            raise ValueError("candidate group set content_hash mismatch")
        return self


class SemanticUnit(_Contract):
    """A semantic range with independent cue and physical-line constraints."""

    schema_version: Literal[2] = 2
    id: str
    token_ids: tuple[str, ...]
    kind: SemanticUnitKind
    strength: float = Field(ge=0.0, le=1.0)
    forbid_cue_breaks: bool = False
    forbid_line_breaks: bool = False
    cue_boundary_relation: SemanticBoundaryRelation | None = None
    line_boundary_relation: SemanticBoundaryRelation | None = None

    @model_validator(mode="after")
    def _unit_tokens(self) -> SemanticUnit:
        if not self.token_ids:
            raise ValueError("semantic unit requires token_ids")
        if len(set(self.token_ids)) != len(self.token_ids):
            raise ValueError("semantic unit token_ids must be unique")
        if self.kind == "boundary_pair":
            if len(self.token_ids) != 2:
                raise ValueError("semantic boundary pair must contain exactly two token IDs")
            if self.cue_boundary_relation is None or self.line_boundary_relation is None:
                raise ValueError("semantic boundary pair requires both channel relations")
            if self.forbid_cue_breaks != (self.cue_boundary_relation == "forbidden"):
                raise ValueError("cue boundary relation disagrees with hard cue constraint")
            if self.forbid_line_breaks != (self.line_boundary_relation == "forbidden"):
                raise ValueError("line boundary relation disagrees with hard line constraint")
            if (
                self.cue_boundary_relation != "neutral" or self.line_boundary_relation != "neutral"
            ) and self.strength <= 0:
                raise ValueError("non-neutral semantic boundary requires positive strength")
            if (
                self.cue_boundary_relation == "neutral"
                and self.line_boundary_relation == "neutral"
                and self.strength != 0
            ):
                raise ValueError("neutral semantic boundary must have zero strength")
        elif self.cue_boundary_relation is not None or self.line_boundary_relation is not None:
            raise ValueError("only semantic boundary pairs may carry boundary relations")
        return self


class DisplayMetricsIdentitySnapshot(_Contract):
    """Pinned Unicode approximation identity; explicitly not pixel shaping."""

    schema_version: Literal[2] = 2
    algorithm: Literal["nakama-unicode-display-metrics"]
    algorithm_version: Literal[1]
    unicode_version: str
    python_implementation: str
    python_version: str
    python_cache_tag: str
    east_asian_ambiguous_columns: float = Field(ge=0, allow_inf_nan=False)
    ascii_space_columns: float = Field(gt=0, allow_inf_nan=False)
    ascii_space_reading_units: float = Field(gt=0, allow_inf_nan=False)
    shaping_backend: Literal["none-not-pixel-exact"]
    runtime_identity_hash: Sha256

    @field_validator(
        "unicode_version",
        "python_implementation",
        "python_version",
        "python_cache_tag",
    )
    @classmethod
    def _identity_values_are_bounded(cls, value: str, info: object) -> str:
        return _validate_bounded_metadata(
            value,
            getattr(info, "field_name", "display metric identity"),
            maximum=128,
        )


class ProtectedTokenRange(_Contract):
    """Canonical-spoken token range protected by policy/reference metadata."""

    schema_version: Literal[2] = 2
    id: str
    token_ids: tuple[str, ...]
    canonical_text: str
    kind: ProtectedSemanticKind
    source: ProtectedSemanticSource
    source_value_hash: Sha256
    reference_evidence_ids: tuple[str, ...] = ()

    @field_validator("id")
    @classmethod
    def _range_id_is_bounded(cls, value: str) -> str:
        return _validate_bounded_metadata(value, "protected range id", maximum=256)

    @field_validator("canonical_text")
    @classmethod
    def _canonical_text_is_safe(cls, value: str) -> str:
        return _validate_subtitle_text(value, "protected canonical text")

    @model_validator(mode="after")
    def _protected_range_shape(self) -> ProtectedTokenRange:
        if not self.token_ids or len(set(self.token_ids)) != len(self.token_ids):
            raise ValueError("protected range requires unique ordered token IDs")
        if len(set(self.reference_evidence_ids)) != len(self.reference_evidence_ids):
            raise ValueError("protected range Reference Evidence IDs must be unique")
        if self.source == "retrieved_reference_metadata" and not self.reference_evidence_ids:
            raise ValueError("reference-derived protected range requires Reference Evidence")
        if self.source == "policy_vocabulary" and self.reference_evidence_ids:
            raise ValueError("policy-vocabulary protected range cannot claim Reference Evidence")
        return self


class BoundaryConstraintEdge(_Contract):
    """One complete, ordered assessment for an adjacent Canonical token edge."""

    schema_version: Literal[2] = 2
    edge_index: int = Field(ge=1)
    left_token_id: str
    right_token_id: str
    cue_relation: BoundaryChannelRelation
    line_relation: BoundaryChannelRelation
    semantic_strength: float = Field(ge=0, le=1, allow_inf_nan=False)
    # Only a separately stored/replayable acoustic-prosody artifact may fill
    # this channel.  Recognition token timestamps are alignment Evidence, not
    # pause Evidence, and therefore leave this value ``None``.
    verified_pause_strength: float | None = Field(default=None, ge=0, le=1, allow_inf_nan=False)
    reason_codes: tuple[BoundaryReasonCode, ...]
    material_uncertainty: bool = False

    @model_validator(mode="after")
    def _edge_shape(self) -> BoundaryConstraintEdge:
        if self.left_token_id == self.right_token_id:
            raise ValueError("boundary edge must join two different tokens")
        if not self.reason_codes or len(set(self.reason_codes)) != len(self.reason_codes):
            raise ValueError("boundary edge requires unique deterministic reason codes")
        uncertain = "uncertain" in {self.cue_relation, self.line_relation}
        if uncertain != self.material_uncertainty:
            raise ValueError("boundary material uncertainty must match its channel relation")
        return self


class BoundaryConstraintReceipt(_Contract):
    """Recomputable V2.1 relation for every adjacent Canonical token edge."""

    schema_version: Literal[2] = 2
    episode_id: str
    generation_id: str
    canonical_content_hash: Sha256
    token_ids: tuple[str, ...]
    policy_hash: Sha256
    profile_id: str
    profile_version: int = Field(ge=1)
    profile_hash: Sha256
    rule_set_id: Literal["nakama-zh-hant-boundaries"]
    rule_set_version: Literal[1]
    rule_set_hash: Sha256
    display_metrics: DisplayMetricsIdentitySnapshot
    protected_ranges: tuple[ProtectedTokenRange, ...]
    protected_range_set_hash: Sha256
    reference_evidence_ids: tuple[str, ...]
    reference_provenance_hash: Sha256
    semantic_units_hash: Sha256
    semantic_adapter_identity_hash: Sha256
    prosody_status: Literal["unavailable", "verified"]
    prosody_artifact_hash: Sha256 | None = None
    edges: tuple[BoundaryConstraintEdge, ...]
    content_hash: Sha256

    @field_validator("episode_id", "generation_id", "profile_id")
    @classmethod
    def _receipt_identity_is_bounded(cls, value: str, info: object) -> str:
        return _validate_bounded_metadata(
            value,
            getattr(info, "field_name", "boundary receipt identity"),
            maximum=256,
        )

    @model_validator(mode="after")
    def _receipt_is_complete_and_content_addressed(self) -> BoundaryConstraintReceipt:
        if not self.token_ids or len(set(self.token_ids)) != len(self.token_ids):
            raise ValueError("boundary receipt requires unique Canonical token IDs")
        expected_edges = tuple(range(1, len(self.token_ids)))
        if tuple(edge.edge_index for edge in self.edges) != expected_edges:
            raise ValueError("boundary receipt must assess every adjacent token edge exactly once")
        for edge in self.edges:
            if (
                edge.left_token_id != self.token_ids[edge.edge_index - 1]
                or edge.right_token_id != self.token_ids[edge.edge_index]
            ):
                raise ValueError("boundary edge drifted from Canonical token order")
        positions = {token_id: index for index, token_id in enumerate(self.token_ids)}
        range_ids: set[str] = set()
        referenced: set[str] = set()
        for protected in self.protected_ranges:
            if protected.id in range_ids:
                raise ValueError("boundary receipt contains duplicate protected range IDs")
            range_ids.add(protected.id)
            try:
                indices = [positions[token_id] for token_id in protected.token_ids]
            except KeyError as exc:
                raise ValueError(
                    f"protected range references unknown Canonical token {exc.args[0]!r}"
                ) from exc
            if indices != list(range(indices[0], indices[-1] + 1)):
                raise ValueError("protected range must be ordered and contiguous")
            referenced.update(protected.reference_evidence_ids)
        if len(set(self.reference_evidence_ids)) != len(self.reference_evidence_ids):
            raise ValueError("boundary receipt Reference Evidence IDs must be unique")
        if referenced != set(self.reference_evidence_ids):
            raise ValueError("boundary receipt Reference provenance is incomplete or excessive")
        if self.prosody_status == "unavailable" and self.prosody_artifact_hash is not None:
            raise ValueError("unavailable prosody cannot claim an artifact")
        if self.prosody_status == "verified" and self.prosody_artifact_hash is None:
            raise ValueError("verified prosody requires an artifact hash")
        pause_strengths = tuple(edge.verified_pause_strength for edge in self.edges)
        if self.prosody_status == "unavailable" and any(
            strength is not None for strength in pause_strengths
        ):
            raise ValueError("unavailable prosody cannot contribute edge pause strength")
        if self.prosody_status == "verified" and any(
            strength is None for strength in pause_strengths
        ):
            raise ValueError("verified prosody must assess every adjacent edge")
        expected_hash = _hash_json(self.model_dump(mode="json", exclude={"content_hash"}))
        if self.content_hash != expected_hash:
            raise ValueError(
                f"boundary receipt content_hash mismatch: expected {expected_hash}, "
                f"got {self.content_hash}"
            )
        return self


def boundary_constraint_receipt_hash(receipt: BoundaryConstraintReceipt) -> str:
    """Stable identity used by the projection sidecar and manifest."""

    return _hash_json(receipt.model_dump(mode="json"))


class ProjectionProfile(_Contract):
    """Versioned display constraints using columns and reading units, not scalars."""

    schema_version: Literal[2] = 2
    id: str
    profile_version: int = Field(ge=1)
    language: str = "zh-Hant-TW"
    max_lines: int = Field(ge=1, le=2)
    target_line_display_columns: float = Field(gt=0, allow_inf_nan=False)
    hard_line_display_columns: float = Field(gt=0, allow_inf_nan=False)
    # Cue density is independent of physical line width.  A two-line subtitle
    # normally carries more than one line's target without being forced to fill
    # the complete two-line hard capacity.
    target_cue_display_columns: float | None = Field(default=None, gt=0, allow_inf_nan=False)
    # Optional dead band around the cue-density target.  Width is a visual
    # preference, not a sentence boundary: cues inside this band carry no
    # quadratic density penalty, so semantic evidence decides their edges.
    minimum_preferred_cue_display_columns: float | None = Field(
        default=None, gt=0, allow_inf_nan=False
    )
    maximum_preferred_cue_display_columns: float | None = Field(
        default=None, gt=0, allow_inf_nan=False
    )
    cue_density_center_tiebreak_penalty: float = Field(default=0.0, ge=0, allow_inf_nan=False)
    min_cue_duration_ms: int = Field(gt=0)
    max_cue_duration_ms: int = Field(gt=0)
    max_reading_units_per_second: float = Field(gt=0, allow_inf_nan=False)
    max_intercue_gap_ms: int = Field(ge=0)
    pause_reward_saturation_ms: int = Field(gt=0)
    semantic_break_penalty: float = Field(ge=0)
    short_cue_penalty: float = Field(ge=0)
    long_cue_penalty: float = Field(default=4.0, ge=0, allow_inf_nan=False)
    cue_boundary_penalty: float = Field(default=0.5, ge=0, allow_inf_nan=False)
    second_line_penalty: float = Field(default=0.5, ge=0, allow_inf_nan=False)
    verified_pause_reward: float = Field(default=1.0, ge=0, allow_inf_nan=False)

    @model_validator(mode="after")
    def _coherent_constraints(self) -> ProjectionProfile:
        if self.hard_line_display_columns < self.target_line_display_columns:
            raise ValueError("hard_line_display_columns must be >= target_line_display_columns")
        if (
            self.target_cue_display_columns is not None
            and self.target_cue_display_columns > self.max_lines * self.hard_line_display_columns
        ):
            raise ValueError(
                "target_cue_display_columns must fit within the cue's hard line capacity"
            )
        preferred_minimum = self.minimum_preferred_cue_display_columns
        preferred_maximum = self.maximum_preferred_cue_display_columns
        if (preferred_minimum is None) != (preferred_maximum is None):
            raise ValueError("preferred cue display-column bounds must be configured together")
        if preferred_minimum is not None and preferred_maximum is not None:
            if preferred_minimum > preferred_maximum:
                raise ValueError("minimum_preferred_cue_display_columns must be <= maximum")
            if preferred_maximum > self.max_lines * self.hard_line_display_columns:
                raise ValueError("preferred cue display-column band must fit within hard capacity")
        if self.max_cue_duration_ms < self.min_cue_duration_ms:
            raise ValueError("max_cue_duration_ms must be >= min_cue_duration_ms")
        return self


class DisplayCue(_Contract):
    """Projection-scoped cue; ``id`` is not persistent transcript identity."""

    schema_version: SchemaVersion = 1
    id: str
    token_ids: tuple[str, ...]
    start_ms: int = Field(ge=0)
    end_ms: int = Field(gt=0)
    lines: tuple[str, ...]

    @model_validator(mode="after")
    def _cue_invariants(self) -> DisplayCue:
        if not self.token_ids:
            raise ValueError("display cue requires token_ids")
        if len(set(self.token_ids)) != len(self.token_ids):
            raise ValueError("display cue token_ids must be unique")
        if self.end_ms <= self.start_ms:
            raise ValueError("end_ms must be greater than start_ms")
        if not self.lines:
            raise ValueError("display cue requires non-blank rendered lines")
        for line in self.lines:
            _validate_subtitle_text(line, "display cue line")
        return self


class SubtitleProjection(_Contract):
    """Ordered display cues derived from one canonical transcript revision."""

    schema_version: SchemaVersion = 1
    episode_id: str
    generation_id: str
    canonical_content_hash: Sha256
    profile_id: str
    profile_version: int = Field(ge=1)
    cues: tuple[DisplayCue, ...]

    @model_validator(mode="after")
    def _projection_invariants(self) -> SubtitleProjection:
        seen_tokens: set[str] = set()
        seen_cues: set[str] = set()
        previous_start = -1
        for cue in self.cues:
            if cue.id in seen_cues:
                raise ValueError(f"duplicate display cue id: {cue.id!r}")
            seen_cues.add(cue.id)
            duplicate_tokens = seen_tokens.intersection(cue.token_ids)
            if duplicate_tokens:
                raise ValueError(
                    f"canonical token IDs may appear in only one cue: {sorted(duplicate_tokens)!r}"
                )
            seen_tokens.update(cue.token_ids)
            if cue.start_ms < previous_start:
                raise ValueError("display cues must be ordered by start_ms")
            previous_start = cue.start_ms
        return self


class ProjectionManifest(_Contract):
    """Content-addressed proof tying a projection to transcript and profile."""

    schema_version: SchemaVersion = 1
    episode_id: str
    generation_id: str
    canonical_hash: Sha256
    profile_hash: Sha256
    token_sequence_hash: Sha256
    output_hash: Sha256


class QualityFinding(_Contract):
    """Machine- or human-produced quality observation using stable IDs."""

    schema_version: SchemaVersion = 1
    id: str
    severity: FindingSeverity
    code: str
    message: str
    span_ids: tuple[str, ...] = ()
    cue_ids: tuple[str, ...] = ()


class QualityReport(_Contract):
    """Release gate; ``passed`` cannot coexist with error/blocking findings."""

    schema_version: SchemaVersion = 1
    episode_id: str
    generation_id: str
    passed: bool
    findings: tuple[QualityFinding, ...] = ()
    metrics: dict[str, float] = Field(default_factory=dict)

    @model_validator(mode="after")
    def _passed_has_no_blockers(self) -> QualityReport:
        blockers = [
            finding.id for finding in self.findings if finding.severity in {"error", "blocking"}
        ]
        if self.passed and blockers:
            raise ValueError(
                f"passed quality report cannot contain error/blocking findings: {blockers!r}"
            )
        return self


class GenerationManifest(_Contract):
    """Stage-level provenance; no stage is complete without addressed artifacts."""

    schema_version: SchemaVersion = 1
    id: str
    episode_id: str
    generation_id: str
    parent_manifest_hash: Sha256 | None = None
    input_artifact_hashes: dict[str, Sha256]
    code_hash: Sha256
    policy_hash: Sha256
    adapter_hash: Sha256
    stage_artifact_hashes: dict[str, Sha256]
    status: GenerationStatus = "draft"

    @model_validator(mode="after")
    def _complete_requires_artifacts(self) -> GenerationManifest:
        if not self.input_artifact_hashes:
            raise ValueError("generation manifest requires input_artifact_hashes")
        if self.status == "complete" and not self.stage_artifact_hashes:
            raise ValueError("complete generation manifest requires stage_artifact_hashes")
        return self


__all__ = [
    "AcceptancePolicySnapshot",
    "AuditApplicability",
    "AuditBoundaryInputStatus",
    "AuditCell",
    "AuditCoverageInputStatus",
    "AuditInputBindings",
    "AuditMetadataInputStatus",
    "AuditPlan",
    "AuditReferenceInputStatus",
    "AuditSeamInputStatus",
    "AuditTargetKind",
    "BoundaryChannelRelation",
    "BoundaryAuditCategory",
    "BoundaryAuditTarget",
    "BoundaryConstraintEdge",
    "BoundaryConstraintReceipt",
    "BoundaryReasonCode",
    "EMPTY_REFERENCE_EVIDENCE_HASH",
    "ArbitrationReceipt",
    "ArtifactDigest",
    "AudioAuditReceipt",
    "AudioClockMap",
    "CanonicalSpan",
    "CanonicalToken",
    "CanonicalTranscript",
    "CandidateGroup",
    "CandidateGroupSet",
    "CandidateOption",
    "CandidateSignal",
    "CandidateSignalAuthority",
    "CandidateSignalCode",
    "CandidateSignalEvidenceKind",
    "CandidateSignalSet",
    "CandidateSourceBinding",
    "CandidateSourceBindingKind",
    "CORRECTION_AUDIT_BOUNDARY_CATEGORIES",
    "CORRECTION_AUDIT_SPAN_CATEGORIES",
    "CorrectionAuditPolicySnapshot",
    "CorrectionAuditReceipt",
    "CorrectionDecision",
    "CorrectionMode",
    "DisplayCue",
    "DisplayMetricsIdentitySnapshot",
    "EvidenceToken",
    "GenerationManifest",
    "GenerationWarning",
    "NormalizationMetric",
    "NormalizationParameter",
    "NormalizationReceipt",
    "NormalizationSourceBindingMethod",
    "ProjectionManifest",
    "ProjectionProfile",
    "ProtectedSemanticKind",
    "ProtectedSemanticSource",
    "ProtectedTokenRange",
    "QualityFinding",
    "QualityReport",
    "RecognitionEvidence",
    "RecognitionSeamEvidence",
    "RecognitionSeamObservation",
    "ReferenceAuthorityAttestation",
    "ReferenceAuthorityDescriptor",
    "ReferenceAuthorityPrincipal",
    "ReferenceAuthorityVersionRef",
    "ReferenceArtifact",
    "ReferenceEvidence",
    "ReferenceExtractionSnapshot",
    "ReferenceLocator",
    "ReferenceLocatorPart",
    "ReferenceOffsetUnit",
    "ReferenceQueryContext",
    "ReferenceQueryContextSlice",
    "ReferenceRetrievalHit",
    "ReferenceRetrievalPolicySnapshot",
    "ReferenceRetrievalReceipt",
    "ReferenceSourceFormat",
    "ReferenceTextBlock",
    "ReplacementLexemeAlignment",
    "ReviewIssue",
    "SemanticBoundaryRelation",
    "SemanticUnit",
    "SpeechActivityInterval",
    "SpeechActivityMethod",
    "SpeechCoverageReceipt",
    "SpeechCoverageStatus",
    "SpanAuditCategory",
    "SpanAuditTarget",
    "SubtitleProjection",
    "canonical_content_hash",
    "audio_audit_receipt_content_hash",
    "audio_audit_receipt_set_hash",
    "boundary_constraint_receipt_hash",
    "correction_audit_receipt_content_hash",
    "correction_audit_receipt_set_hash",
    "normalization_receipt_content_hash",
    "normalization_settings_hash",
    "reference_evidence_set_hash",
    "reference_retrieval_policy_hash",
    "validate_reference_authority_binding",
    "recognition_evidence_content_hash",
    "recognition_evidence_set_hash",
    "speech_coverage_receipt_content_hash",
    "token_sequence_hash",
]
