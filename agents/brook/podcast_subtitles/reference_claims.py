"""Complete, deterministic authority-claim and conflict proofs.

The contracts here discover exact enrolled claims and preserve counterclaims.
They cannot create a Correction Proposal or Decision, judge audio, mutate a
Canonical Transcript, or decide which conflicting claim is true.
"""

from __future__ import annotations

import re
import unicodedata
from itertools import combinations
from typing import Annotated, Literal

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
    ReferenceArtifact,
    ReferenceAuthorityVersionRef,
    ReferenceEvidence,
    ReferenceLocator,
    reference_evidence_set_hash,
)

from .adapters.reference import LocalReferenceRetriever, reference_evidence_id
from .hashing import canonical_json_bytes, hash_object
from .ports import AdapterIntegrityError

Sha256 = Annotated[str, StringConstraints(pattern=r"^[0-9a-f]{64}$")]
ReferenceClaimScope = Literal[
    "source_title",
    "source_author",
    "literal_terminology",
    "verbatim_source_text",
    "owner_approved_glossary_spelling",
]
ReferenceClaimOrigin = Literal[
    "enrolled_source_title",
    "enrolled_source_author",
    "exact_extracted_evidence",
]
ReferenceClaimStrength = Literal["authoritative", "curated", "contextual"]
ReferenceProofAuthority = Literal["not_correction_decision_not_audio_verdict"]

_AUTHORITY_LIMIT = "not_correction_decision_not_audio_verdict"
_WHITESPACE = re.compile(r"\s+")


class ReferenceClaimError(ValueError):
    """An authority claim proof is incomplete, forged, or non-canonical."""


class _StrictContract(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)


def normalize_reference_literal(value: str) -> str:
    """Conservative equivalence only: Unicode NFKC, case-fold, whitespace."""

    normalized = unicodedata.normalize("NFKC", value).casefold()
    return _WHITESPACE.sub(" ", normalized).strip()


def normalize_resolution_key(value: str) -> str:
    return normalize_reference_literal(value)


def _bounded(value: str, label: str, *, maximum: int = 1024) -> str:
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


class ReferenceAuthorityTargetV2(_StrictContract):
    schema_version: Literal[2] = 2
    resolution_key: str
    scopes: tuple[ReferenceClaimScope, ...]

    @field_validator("resolution_key")
    @classmethod
    def _canonical_key(cls, value: str) -> str:
        _bounded(value, "resolution_key", maximum=512)
        if value != normalize_resolution_key(value):
            raise ValueError("resolution_key must already be deterministically normalized")
        return value

    @model_validator(mode="after")
    def _scope_set_is_canonical(self) -> ReferenceAuthorityTargetV2:
        if not self.scopes:
            raise ValueError("Reference authority target requires at least one scope")
        if len(set(self.scopes)) != len(self.scopes) or self.scopes != tuple(
            sorted(self.scopes)
        ):
            raise ValueError("Reference authority target scopes must be unique and ordered")
        return self


class ReferenceExactClaimSpecV2(_StrictContract):
    """Caller-declared exact claim; no model-generated/free-text origin exists."""

    schema_version: Literal[2] = 2
    resolution_key: str
    scope: ReferenceClaimScope
    claimed_text: str
    origin: ReferenceClaimOrigin
    evidence_id: str
    claim_start: int | None = Field(default=None, ge=0)
    claim_end: int | None = Field(default=None, gt=0)

    @field_validator("resolution_key")
    @classmethod
    def _canonical_key(cls, value: str) -> str:
        _bounded(value, "resolution_key", maximum=512)
        if value != normalize_resolution_key(value):
            raise ValueError("resolution_key must already be deterministically normalized")
        return value

    @field_validator("claimed_text", "evidence_id")
    @classmethod
    def _text_is_bounded(cls, value: str, info: object) -> str:
        return _bounded(
            value,
            getattr(info, "field_name", "value"),
            maximum=4096 if getattr(info, "field_name", "") == "claimed_text" else 256,
        )

    @model_validator(mode="after")
    def _origin_has_exact_shape(self) -> ReferenceExactClaimSpecV2:
        if self.origin == "exact_extracted_evidence":
            if self.claim_start is None or self.claim_end is None:
                raise ValueError("exact extracted claim requires claim_start and claim_end")
            if self.claim_end <= self.claim_start:
                raise ValueError("exact extracted claim range must be non-empty")
        elif self.claim_start is not None or self.claim_end is not None:
            raise ValueError("enrolled metadata claim cannot carry excerpt-relative offsets")
        if self.origin == "enrolled_source_title" and self.scope != "source_title":
            raise ValueError("enrolled source title claim requires source_title scope")
        if self.origin == "enrolled_source_author" and self.scope != "source_author":
            raise ValueError("enrolled source author claim requires source_author scope")
        return self


class ReferenceEvidenceOmissionSpecV2(_StrictContract):
    """Explicit target-bound statement that examined Evidence contains no exact claim."""

    schema_version: Literal[2] = 2
    evidence_id: str
    resolution_key: str
    scopes: tuple[ReferenceClaimScope, ...]
    reason: Literal["no_exact_claim_for_target"]

    @field_validator("evidence_id", "resolution_key")
    @classmethod
    def _bounded_fields(cls, value: str, info: object) -> str:
        field_name = getattr(info, "field_name", "value")
        _bounded(value, field_name, maximum=512 if field_name == "resolution_key" else 256)
        if field_name == "resolution_key" and value != normalize_resolution_key(value):
            raise ValueError("resolution_key must already be deterministically normalized")
        return value

    @model_validator(mode="after")
    def _scope_set_is_canonical(self) -> ReferenceEvidenceOmissionSpecV2:
        if not self.scopes:
            raise ValueError("Reference Evidence omission requires target scopes")
        if len(set(self.scopes)) != len(self.scopes) or self.scopes != tuple(
            sorted(self.scopes)
        ):
            raise ValueError("Reference Evidence omission scopes must be unique and ordered")
        return self


class ReferenceClaimV2(_StrictContract):
    schema_version: Literal[2] = 2
    id: Sha256
    resolution_key: str
    scope: ReferenceClaimScope
    claimed_text: str
    normalized_claimed_text: str
    origin: ReferenceClaimOrigin
    evidence_id: str
    source_id: str
    source_artifact_sha256: Sha256
    extracted_text_sha256: Sha256
    locator: ReferenceLocator
    locator_hash: Sha256
    extraction_block_index: int = Field(ge=0)
    extraction_block_hash: Sha256
    excerpt_start: int = Field(ge=0)
    excerpt_end: int = Field(gt=0)
    excerpt_hash: Sha256
    authority_descriptor_hash: Sha256
    logical_source_id: str
    version_id: str
    version_status: Literal["draft", "active", "superseded"]
    trust_tier: Literal["authoritative", "curated", "contextual"]
    role: Literal[
        "published_author_book",
        "owner_final_report",
        "owner_approved_outline_glossary",
        "curated_reference",
        "contextual_reference",
    ]
    supersedes: tuple[ReferenceAuthorityVersionRef, ...]
    supersedes_lineage_hash: Sha256
    strength: ReferenceClaimStrength
    authority: ReferenceProofAuthority = _AUTHORITY_LIMIT
    content_hash: Sha256

    @model_validator(mode="after")
    def _is_content_addressed(self) -> ReferenceClaimV2:
        if self.resolution_key != normalize_resolution_key(self.resolution_key):
            raise ValueError("Reference claim resolution_key is not canonical")
        if self.normalized_claimed_text != normalize_reference_literal(self.claimed_text):
            raise ValueError("Reference claim deterministic text normalization mismatch")
        if self.locator_hash != hash_object(self.locator):
            raise ValueError("Reference claim locator hash mismatch")
        if self.supersedes_lineage_hash != hash_object(self.supersedes):
            raise ValueError("Reference claim supersedes lineage hash mismatch")
        _require_identity(self, self.id, self.content_hash, "Reference claim")
        return self


class ReferenceAuthorityVersionCoverageV2(_StrictContract):
    schema_version: Literal[2] = 2
    source_id: str
    logical_source_id: str
    version_id: str
    version_status: Literal["draft", "active", "superseded"]
    descriptor_hash: Sha256
    artifact_sha256: Sha256
    disposition: Literal["eligible_active", "excluded_draft", "excluded_superseded"]


class ReferenceCoverageOmissionV2(_StrictContract):
    schema_version: Literal[2] = 2
    kind: Literal["source_version", "evidence", "claim_spec"]
    omitted_id: str
    resolution_key: str | None = None
    scope: ReferenceClaimScope | None = None
    reason: Literal[
        "draft_version",
        "superseded_version",
        "missing_exact_evidence",
        "missing_typed_claim_or_omission",
        "no_exact_claim_for_target",
    ]
    blocking: bool

    @model_validator(mode="after")
    def _target_binding_is_exact(self) -> ReferenceCoverageOmissionV2:
        if self.kind == "evidence":
            if self.resolution_key is None or self.scope is None:
                raise ValueError("Reference Evidence omission requires resolution key and scope")
        elif self.resolution_key is not None or self.scope is not None:
            raise ValueError("source/version omission cannot carry a resolution target")
        return self


class ReferenceAuthorityCoverageReceiptV2(_StrictContract):
    schema_version: Literal[2] = 2
    id: Sha256
    targets_hash: Sha256
    index_hash: Sha256
    retriever_version: str
    retriever_config_hash: Sha256
    retriever_code_hash: Sha256
    retriever_runtime_hash: Sha256
    artifact_set_hash: Sha256
    authority_descriptor_set_hash: Sha256
    version_coverage_hash: Sha256
    evidence_set_hash: Sha256
    claim_spec_set_hash: Sha256
    evidence_omission_spec_set_hash: Sha256
    claim_set_hash: Sha256
    version_coverage: tuple[ReferenceAuthorityVersionCoverageV2, ...]
    eligible_logical_version_ids: tuple[str, ...]
    examined_evidence_ids: tuple[str, ...]
    examined_claim_ids: tuple[Sha256, ...]
    omissions: tuple[ReferenceCoverageOmissionV2, ...] = ()
    selection_complete: bool
    bounded_retrieval_assessed: bool
    bounded_retrieval_evidence_ids: tuple[str, ...]
    bounded_retrieval_omitted_claim_ids: tuple[Sha256, ...]
    bounded_retrieval_complete: bool | None
    authority: ReferenceProofAuthority = _AUTHORITY_LIMIT
    content_hash: Sha256

    @model_validator(mode="after")
    def _is_complete_and_content_addressed(self) -> ReferenceAuthorityCoverageReceiptV2:
        for label, values in (
            ("eligible logical/version IDs", self.eligible_logical_version_ids),
            ("examined evidence IDs", self.examined_evidence_ids),
            ("examined claim IDs", self.examined_claim_ids),
            ("bounded retrieval Evidence IDs", self.bounded_retrieval_evidence_ids),
            ("bounded retrieval omitted claim IDs", self.bounded_retrieval_omitted_claim_ids),
        ):
            if len(set(values)) != len(values) or values != tuple(sorted(values)):
                raise ValueError(f"Reference coverage {label} must be unique and ordered")
        version_keys = [
            (item.logical_source_id, item.version_id, item.source_id)
            for item in self.version_coverage
        ]
        if len(set(version_keys)) != len(version_keys) or version_keys != sorted(version_keys):
            raise ValueError("Reference coverage versions must be unique and ordered")
        if self.version_coverage_hash != hash_object(self.version_coverage):
            raise ValueError("Reference coverage version coverage hash mismatch")
        expected_eligible = tuple(
            sorted(
                f"{item.logical_source_id}@{item.version_id}"
                for item in self.version_coverage
                if item.disposition == "eligible_active"
            )
        )
        if self.eligible_logical_version_ids != expected_eligible:
            raise ValueError("Reference coverage eligible version IDs are incomplete")
        omission_keys = [
            (
                item.kind,
                item.omitted_id,
                item.resolution_key or "",
                item.scope or "",
                item.reason,
            )
            for item in self.omissions
        ]
        if len(set(omission_keys)) != len(omission_keys) or omission_keys != sorted(
            omission_keys
        ):
            raise ValueError("Reference coverage omissions must be unique and ordered")
        expected_selection = not any(item.blocking for item in self.omissions)
        if self.selection_complete != expected_selection:
            raise ValueError("Reference coverage selection_complete contradicts omissions")
        if self.bounded_retrieval_assessed:
            expected_bounded = not self.bounded_retrieval_omitted_claim_ids
            if self.bounded_retrieval_complete != expected_bounded:
                raise ValueError("Reference coverage bounded retrieval completeness lie")
        elif self.bounded_retrieval_complete is not None:
            raise ValueError("unassessed bounded retrieval cannot claim completeness")
        _require_identity(self, self.id, self.content_hash, "Reference coverage receipt")
        return self


class ReferenceConflictV2(_StrictContract):
    schema_version: Literal[2] = 2
    id: Sha256
    resolution_key: str
    overlapping_scope: ReferenceClaimScope
    claim_ids: tuple[Sha256, Sha256]
    normalized_claimed_texts: tuple[str, str]
    kind: Literal[
        "authoritative_authoritative",
        "authoritative_contextual_counterclaim",
        "authority_counterclaim",
        "contextual_counterclaim",
    ]
    severity: Literal["blocking", "unresolved"]
    explicit_supersedes: Literal[False] = False
    authority: ReferenceProofAuthority = _AUTHORITY_LIMIT
    content_hash: Sha256

    @model_validator(mode="after")
    def _is_content_addressed(self) -> ReferenceConflictV2:
        if (
            self.claim_ids != tuple(sorted(self.claim_ids))
            or self.claim_ids[0] == self.claim_ids[1]
        ):
            raise ValueError("Reference conflict claim IDs must be distinct and ordered")
        if self.normalized_claimed_texts[0] == self.normalized_claimed_texts[1]:
            raise ValueError("equivalent deterministic text cannot form a Reference conflict")
        _require_identity(self, self.id, self.content_hash, "Reference conflict")
        return self


class ReferenceAuthorityUnresolvedConditionV2(_StrictContract):
    schema_version: Literal[2] = 2
    code: Literal["coverage_incomplete", "bounded_retrieval_not_authority_complete"]
    related_ids: tuple[str, ...]
    severity: Literal["blocking"] = "blocking"


class ReferenceConflictSetV2(_StrictContract):
    schema_version: Literal[2] = 2
    id: Sha256
    claim_set_hash: Sha256
    coverage_receipt_id: Sha256
    coverage_receipt_hash: Sha256
    conflicts: tuple[ReferenceConflictV2, ...]
    unresolved_conditions: tuple[ReferenceAuthorityUnresolvedConditionV2, ...]
    blocking: bool
    authority: ReferenceProofAuthority = _AUTHORITY_LIMIT
    content_hash: Sha256

    @model_validator(mode="after")
    def _is_content_addressed(self) -> ReferenceConflictSetV2:
        conflict_ids = tuple(item.id for item in self.conflicts)
        if len(set(conflict_ids)) != len(conflict_ids) or conflict_ids != tuple(
            sorted(conflict_ids)
        ):
            raise ValueError("Reference conflicts must be unique and ordered")
        condition_keys = tuple(
            (item.code, item.related_ids) for item in self.unresolved_conditions
        )
        if len(set(condition_keys)) != len(condition_keys) or condition_keys != tuple(
            sorted(condition_keys)
        ):
            raise ValueError("Reference unresolved conditions must be unique and ordered")
        expected_blocking = bool(
            any(item.severity == "blocking" for item in self.conflicts)
            or self.unresolved_conditions
        )
        if self.blocking != expected_blocking:
            raise ValueError("Reference conflict-set blocking flag is inconsistent")
        _require_identity(self, self.id, self.content_hash, "Reference conflict set")
        return self


class ReferenceAuthorityProofV2(_StrictContract):
    schema_version: Literal[2] = 2
    id: Sha256
    claims: tuple[ReferenceClaimV2, ...]
    coverage: ReferenceAuthorityCoverageReceiptV2
    conflicts: ReferenceConflictSetV2
    authority: ReferenceProofAuthority = _AUTHORITY_LIMIT
    content_hash: Sha256

    @model_validator(mode="after")
    def _is_content_addressed(self) -> ReferenceAuthorityProofV2:
        claim_ids = tuple(item.id for item in self.claims)
        if len(set(claim_ids)) != len(claim_ids) or claim_ids != tuple(sorted(claim_ids)):
            raise ValueError("Reference proof claims must be unique and ordered")
        if self.coverage.claim_set_hash != hash_object(self.claims):
            raise ValueError("Reference proof coverage differs from claims")
        if self.coverage.examined_claim_ids != claim_ids:
            raise ValueError("Reference proof coverage does not enumerate every claim")
        if self.conflicts.claim_set_hash != hash_object(self.claims):
            raise ValueError("Reference proof conflict set differs from claims")
        if (
            self.conflicts.coverage_receipt_id != self.coverage.id
            or self.conflicts.coverage_receipt_hash != self.coverage.content_hash
        ):
            raise ValueError("Reference proof conflict set differs from coverage")
        claim_by_id = {item.id: item for item in self.claims}
        for conflict in self.conflicts.conflicts:
            try:
                left, right = (claim_by_id[item] for item in conflict.claim_ids)
            except KeyError as exc:
                raise ValueError("Reference conflict names a claim outside the proof") from exc
            if (
                left.resolution_key != right.resolution_key
                or conflict.resolution_key != left.resolution_key
                or left.scope != right.scope
                or conflict.overlapping_scope != left.scope
                or conflict.normalized_claimed_texts
                != (left.normalized_claimed_text, right.normalized_claimed_text)
            ):
                raise ValueError("Reference conflict differs from its exact claims")
        expected_condition_codes: list[str] = []
        if not self.coverage.selection_complete:
            expected_condition_codes.append("coverage_incomplete")
        if self.coverage.bounded_retrieval_complete is False:
            expected_condition_codes.append("bounded_retrieval_not_authority_complete")
        if [item.code for item in self.conflicts.unresolved_conditions] != sorted(
            expected_condition_codes
        ):
            raise ValueError("Reference unresolved conditions differ from coverage")
        _require_identity(self, self.id, self.content_hash, "Reference authority proof")
        return self


def _seal(model: type[_StrictContract], payload: dict[str, object]) -> _StrictContract:
    digest = hash_object(payload)
    return model(**payload, id=digest, content_hash=digest)


def _validate_exact_artifact(value: ReferenceArtifact) -> ReferenceArtifact:
    reparsed = ReferenceArtifact.model_validate_json(canonical_json_bytes(value), strict=True)
    if reparsed != value:
        raise ReferenceClaimError("Reference Artifact is not an exact valid v2 artifact")
    return value


def _validate_exact_evidence(value: ReferenceEvidence) -> ReferenceEvidence:
    reparsed = ReferenceEvidence.model_validate_json(canonical_json_bytes(value), strict=True)
    if reparsed != value:
        raise ReferenceClaimError("Reference Evidence is not an exact valid v2 artifact")
    expected_id = reference_evidence_id(
        artifact=value.artifact,
        locator=value.locator,
        extraction_block_index=value.extraction_block_index,
        extraction_block_hash=value.extraction_block_hash,
        excerpt_start=value.excerpt_start,
        excerpt_end=value.excerpt_end,
        excerpt_hash=value.excerpt_hash,
    )
    if value.id != expected_id:
        raise ReferenceClaimError("Reference Evidence ID differs from its exact locator/excerpt")
    return value


def _claim_strength(artifact: ReferenceArtifact, scope: ReferenceClaimScope) -> str:
    authority = artifact.authority
    if authority.version_status != "active" or scope not in authority.allowed_scopes:
        return "contextual"
    if authority.trust_tier == "authoritative":
        return "authoritative"
    if authority.trust_tier == "curated":
        return "curated"
    return "contextual"


def _build_claim(spec: ReferenceExactClaimSpecV2, evidence: ReferenceEvidence) -> ReferenceClaimV2:
    artifact = evidence.artifact
    if spec.origin == "exact_extracted_evidence":
        assert spec.claim_start is not None and spec.claim_end is not None
        if spec.claim_end > len(evidence.excerpt) or (
            evidence.excerpt[spec.claim_start : spec.claim_end] != spec.claimed_text
        ):
            raise ReferenceClaimError("typed exact claim is not a member of its Evidence excerpt")
    elif spec.origin == "enrolled_source_title":
        if spec.claimed_text != artifact.title:
            raise ReferenceClaimError("source-title claim differs from enrolled title")
    elif artifact.author is None or spec.claimed_text != artifact.author:
        raise ReferenceClaimError("source-author claim differs from enrolled author")
    authority = artifact.authority
    payload: dict[str, object] = {
        "schema_version": 2,
        "resolution_key": spec.resolution_key,
        "scope": spec.scope,
        "claimed_text": spec.claimed_text,
        "normalized_claimed_text": normalize_reference_literal(spec.claimed_text),
        "origin": spec.origin,
        "evidence_id": evidence.id,
        "source_id": artifact.source_id,
        "source_artifact_sha256": artifact.digest.sha256,
        "extracted_text_sha256": artifact.extracted_text.sha256,
        "locator": evidence.locator,
        "locator_hash": hash_object(evidence.locator),
        "extraction_block_index": evidence.extraction_block_index,
        "extraction_block_hash": evidence.extraction_block_hash,
        "excerpt_start": evidence.excerpt_start,
        "excerpt_end": evidence.excerpt_end,
        "excerpt_hash": evidence.excerpt_hash,
        "authority_descriptor_hash": authority.content_hash,
        "logical_source_id": authority.logical_source_id,
        "version_id": authority.version_id,
        "version_status": authority.version_status,
        "trust_tier": authority.trust_tier,
        "role": authority.role,
        "supersedes": authority.supersedes,
        "supersedes_lineage_hash": hash_object(authority.supersedes),
        "strength": _claim_strength(artifact, spec.scope),
        "authority": _AUTHORITY_LIMIT,
    }
    return _seal(ReferenceClaimV2, payload)  # type: ignore[return-value]


def _explicitly_supersedes(left: ReferenceClaimV2, right: ReferenceClaimV2) -> bool:
    if left.logical_source_id != right.logical_source_id:
        return False
    return any(
        item.logical_source_id == right.logical_source_id
        and item.version_id == right.version_id
        for item in left.supersedes
    ) or any(
        item.logical_source_id == left.logical_source_id
        and item.version_id == left.version_id
        for item in right.supersedes
    )


def _build_conflict(left: ReferenceClaimV2, right: ReferenceClaimV2) -> ReferenceConflictV2:
    ordered = tuple(sorted((left, right), key=lambda item: item.id))
    strengths = {item.strength for item in ordered}
    if strengths == {"authoritative"}:
        kind = "authoritative_authoritative"
        severity = "blocking"
    elif "authoritative" in strengths and "contextual" in strengths:
        kind = "authoritative_contextual_counterclaim"
        severity = "blocking"
    elif "authoritative" in strengths:
        kind = "authority_counterclaim"
        severity = "blocking"
    else:
        kind = "contextual_counterclaim"
        severity = "unresolved"
    payload: dict[str, object] = {
        "schema_version": 2,
        "resolution_key": left.resolution_key,
        "overlapping_scope": left.scope,
        "claim_ids": tuple(item.id for item in ordered),
        "normalized_claimed_texts": tuple(item.normalized_claimed_text for item in ordered),
        "kind": kind,
        "severity": severity,
        "explicit_supersedes": False,
        "authority": _AUTHORITY_LIMIT,
    }
    return _seal(ReferenceConflictV2, payload)  # type: ignore[return-value]


def build_reference_authority_proof(
    *,
    retriever: LocalReferenceRetriever,
    targets: tuple[ReferenceAuthorityTargetV2, ...],
    evidence: tuple[ReferenceEvidence, ...],
    claim_specs: tuple[ReferenceExactClaimSpecV2, ...],
    evidence_omissions: tuple[ReferenceEvidenceOmissionSpecV2, ...] = (),
    bounded_retrieval_evidence_ids: tuple[str, ...] | None = None,
) -> ReferenceAuthorityProofV2:
    """Build complete claims/coverage/conflicts from exact enrolled parents."""

    try:
        index = retriever.verify_index()
    except AdapterIntegrityError as exc:
        raise ReferenceClaimError("Reference complete lookup index integrity failed") from exc
    retriever_version = retriever.adapter_version
    if not targets:
        raise ReferenceClaimError("Reference authority proof requires explicit targets")
    target_keys = [(item.resolution_key, item.scopes) for item in targets]
    if len(set(target_keys)) != len(target_keys) or target_keys != sorted(target_keys):
        raise ReferenceClaimError("Reference authority targets must be unique and ordered")
    artifacts = tuple(_validate_exact_artifact(item) for item in index.artifacts)
    artifact_keys = [item.source_id for item in artifacts]
    if len(set(artifact_keys)) != len(artifact_keys) or artifact_keys != sorted(artifact_keys):
        raise ReferenceClaimError("Reference index artifacts must be unique and ordered")
    artifact_by_source = {item.source_id: item for item in artifacts}
    validated_evidence = tuple(_validate_exact_evidence(item) for item in evidence)
    evidence_by_id: dict[str, ReferenceEvidence] = {}
    for item in validated_evidence:
        if item.id in evidence_by_id:
            raise ReferenceClaimError("complete Reference lookup contains duplicate Evidence IDs")
        try:
            enrolled = artifact_by_source[item.artifact.source_id]
        except KeyError as exc:
            raise ReferenceClaimError(
                "Reference Evidence source is not in the exact index"
            ) from exc
        if enrolled != item.artifact:
            raise ReferenceClaimError("Reference Evidence Artifact differs from exact index")
        try:
            retriever.verify(item)
        except (AdapterIntegrityError, ValueError) as exc:
            raise ReferenceClaimError(
                "Reference Evidence lacks exact enrolled extraction membership"
            ) from exc
        evidence_by_id[item.id] = item

    target_scopes = {
        item.resolution_key: frozenset(item.scopes) for item in targets
    }
    spec_keys = [
        (
            item.resolution_key,
            item.scope,
            item.evidence_id,
            item.origin,
            item.claim_start,
            item.claim_end,
            item.claimed_text,
        )
        for item in claim_specs
    ]
    if len(set(spec_keys)) != len(spec_keys):
        raise ReferenceClaimError("typed exact Reference claim specs must be unique")
    omission_spec_keys = [
        (item.evidence_id, item.resolution_key, item.scopes, item.reason)
        for item in evidence_omissions
    ]
    if len(set(omission_spec_keys)) != len(omission_spec_keys):
        raise ReferenceClaimError("Reference Evidence omission specs must be unique")
    omission_by_target: dict[
        tuple[str, str], ReferenceEvidenceOmissionSpecV2
    ] = {}
    for omission in evidence_omissions:
        if omission.evidence_id not in evidence_by_id:
            raise ReferenceClaimError("Reference Evidence omission names unknown Evidence")
        expected_scopes = target_scopes.get(omission.resolution_key)
        if expected_scopes is None or not frozenset(omission.scopes) <= expected_scopes:
            raise ReferenceClaimError("Reference Evidence omission differs from target scope")
        omission_key = (omission.evidence_id, omission.resolution_key)
        if omission_key in omission_by_target:
            raise ReferenceClaimError(
                "Reference Evidence has duplicate target-bound omission specs"
            )
        omission_by_target[omission_key] = omission
    claims: list[ReferenceClaimV2] = []
    for spec in claim_specs:
        if spec.resolution_key not in target_scopes or spec.scope not in target_scopes[
            spec.resolution_key
        ]:
            raise ReferenceClaimError("Reference claim spec is outside explicit target scope")
        try:
            item = evidence_by_id[spec.evidence_id]
        except KeyError as exc:
            raise ReferenceClaimError("Reference claim spec names non-enrolled Evidence") from exc
        if item.artifact.authority.version_status == "active":
            claims.append(_build_claim(spec, item))
    claims_tuple = tuple(sorted(claims, key=lambda item: item.id))

    claimed_cells = {
        (item.evidence_id, item.resolution_key, item.scope) for item in claim_specs
    }
    omitted_cells = {
        (item.evidence_id, item.resolution_key, scope)
        for item in evidence_omissions
        for scope in item.scopes
    }
    if claimed_cells & omitted_cells:
        raise ReferenceClaimError(
            "Reference Evidence target/scope cannot be both claimed and omitted"
        )
    omissions: list[ReferenceCoverageOmissionV2] = []
    version_coverage: list[ReferenceAuthorityVersionCoverageV2] = []
    for artifact in artifacts:
        authority = artifact.authority
        disposition = (
            "eligible_active"
            if authority.version_status == "active"
            else "excluded_draft"
            if authority.version_status == "draft"
            else "excluded_superseded"
        )
        version_coverage.append(
            ReferenceAuthorityVersionCoverageV2(
                source_id=artifact.source_id,
                logical_source_id=authority.logical_source_id,
                version_id=authority.version_id,
                version_status=authority.version_status,
                descriptor_hash=authority.content_hash,
                artifact_sha256=artifact.digest.sha256,
                disposition=disposition,
            )
        )
        if authority.version_status != "active":
            omissions.append(
                ReferenceCoverageOmissionV2(
                    kind="source_version",
                    omitted_id=f"{authority.logical_source_id}@{authority.version_id}",
                    reason=(
                        "draft_version"
                        if authority.version_status == "draft"
                        else "superseded_version"
                    ),
                    blocking=False,
                )
            )
            continue
        source_evidence = [
            item for item in validated_evidence if item.artifact.source_id == artifact.source_id
        ]
        if not source_evidence:
            omissions.append(
                ReferenceCoverageOmissionV2(
                    kind="source_version",
                    omitted_id=f"{authority.logical_source_id}@{authority.version_id}",
                    reason="missing_exact_evidence",
                    blocking=True,
                )
            )
        for item in source_evidence:
            for target in targets:
                for scope in target.scopes:
                    cell = (item.id, target.resolution_key, scope)
                    if cell in claimed_cells:
                        continue
                    declared = cell in omitted_cells
                    omissions.append(
                        ReferenceCoverageOmissionV2(
                            kind="evidence",
                            omitted_id=f"{item.id}|{target.resolution_key}|{scope}",
                            resolution_key=target.resolution_key,
                            scope=scope,
                            reason=(
                                "no_exact_claim_for_target"
                                if declared
                                else "missing_typed_claim_or_omission"
                            ),
                            blocking=not declared,
                        )
                    )

    version_tuple = tuple(
        sorted(
            version_coverage,
            key=lambda item: (item.logical_source_id, item.version_id, item.source_id),
        )
    )
    omissions_tuple = tuple(
        sorted(
            omissions,
            key=lambda item: (
                item.kind,
                item.omitted_id,
                item.resolution_key or "",
                item.scope or "",
                item.reason,
            ),
        )
    )
    examined_evidence_ids = tuple(sorted(evidence_by_id))
    examined_claim_ids = tuple(item.id for item in claims_tuple)
    claim_set_hash = hash_object(claims_tuple)
    bounded_assessed = bounded_retrieval_evidence_ids is not None
    bounded_ids = tuple(sorted(bounded_retrieval_evidence_ids or ()))
    if len(set(bounded_ids)) != len(bounded_ids) or not set(bounded_ids) <= set(
        evidence_by_id
    ):
        raise ReferenceClaimError("bounded retrieval Evidence IDs are duplicate or unknown")
    bounded_omitted_claim_ids = tuple(
        sorted(item.id for item in claims_tuple if item.evidence_id not in bounded_ids)
    ) if bounded_assessed else ()
    coverage_payload: dict[str, object] = {
        "schema_version": 2,
        "targets_hash": hash_object(targets),
        "index_hash": index.index_hash,
        "retriever_version": retriever_version,
        "retriever_config_hash": index.retriever_config_hash,
        "retriever_code_hash": index.retriever_code_hash,
        "retriever_runtime_hash": index.retriever_runtime_hash,
        "artifact_set_hash": hash_object(artifacts),
        "authority_descriptor_set_hash": hash_object(
            tuple(item.authority for item in artifacts)
        ),
        "version_coverage_hash": hash_object(version_tuple),
        "evidence_set_hash": reference_evidence_set_hash(validated_evidence),
        "claim_spec_set_hash": hash_object(tuple(sorted(spec_keys))),
        "evidence_omission_spec_set_hash": hash_object(
            tuple(sorted(omission_spec_keys))
        ),
        "claim_set_hash": claim_set_hash,
        "version_coverage": version_tuple,
        "eligible_logical_version_ids": tuple(
            sorted(
                f"{item.logical_source_id}@{item.version_id}"
                for item in version_tuple
                if item.disposition == "eligible_active"
            )
        ),
        "examined_evidence_ids": examined_evidence_ids,
        "examined_claim_ids": examined_claim_ids,
        "omissions": omissions_tuple,
        "selection_complete": not any(item.blocking for item in omissions_tuple),
        "bounded_retrieval_assessed": bounded_assessed,
        "bounded_retrieval_evidence_ids": bounded_ids,
        "bounded_retrieval_omitted_claim_ids": bounded_omitted_claim_ids,
        "bounded_retrieval_complete": (
            not bounded_omitted_claim_ids if bounded_assessed else None
        ),
        "authority": _AUTHORITY_LIMIT,
    }
    coverage = _seal(
        ReferenceAuthorityCoverageReceiptV2,
        coverage_payload,
    )
    assert isinstance(coverage, ReferenceAuthorityCoverageReceiptV2)

    conflicts: list[ReferenceConflictV2] = []
    for left, right in combinations(claims_tuple, 2):
        if (
            left.version_status != "active"
            or right.version_status != "active"
            or left.resolution_key != right.resolution_key
            or left.scope != right.scope
            or left.normalized_claimed_text == right.normalized_claimed_text
            or _explicitly_supersedes(left, right)
        ):
            continue
        conflicts.append(_build_conflict(left, right))
    conflicts_tuple = tuple(sorted(conflicts, key=lambda item: item.id))
    conditions: list[ReferenceAuthorityUnresolvedConditionV2] = []
    if not coverage.selection_complete:
        conditions.append(
            ReferenceAuthorityUnresolvedConditionV2(
                code="coverage_incomplete",
                related_ids=tuple(item.omitted_id for item in coverage.omissions if item.blocking),
            )
        )
    if coverage.bounded_retrieval_complete is False:
        conditions.append(
            ReferenceAuthorityUnresolvedConditionV2(
                code="bounded_retrieval_not_authority_complete",
                related_ids=coverage.bounded_retrieval_omitted_claim_ids,
            )
        )
    conditions_tuple = tuple(sorted(conditions, key=lambda item: (item.code, item.related_ids)))
    conflict_payload: dict[str, object] = {
        "schema_version": 2,
        "claim_set_hash": claim_set_hash,
        "coverage_receipt_id": coverage.id,
        "coverage_receipt_hash": coverage.content_hash,
        "conflicts": conflicts_tuple,
        "unresolved_conditions": conditions_tuple,
        "blocking": bool(
            any(item.severity == "blocking" for item in conflicts_tuple)
            or conditions_tuple
        ),
        "authority": _AUTHORITY_LIMIT,
    }
    conflict_set = _seal(ReferenceConflictSetV2, conflict_payload)
    assert isinstance(conflict_set, ReferenceConflictSetV2)
    proof_payload: dict[str, object] = {
        "schema_version": 2,
        "claims": claims_tuple,
        "coverage": coverage,
        "conflicts": conflict_set,
        "authority": _AUTHORITY_LIMIT,
    }
    proof = _seal(ReferenceAuthorityProofV2, proof_payload)
    assert isinstance(proof, ReferenceAuthorityProofV2)
    return proof


def reference_authority_proof_bytes(proof: ReferenceAuthorityProofV2) -> bytes:
    """Return the only canonical byte representation of a proof."""

    return canonical_json_bytes(proof)


def verify_reference_authority_proof(
    exact_bytes: bytes,
    *,
    retriever: LocalReferenceRetriever,
    targets: tuple[ReferenceAuthorityTargetV2, ...],
    evidence: tuple[ReferenceEvidence, ...],
    claim_specs: tuple[ReferenceExactClaimSpecV2, ...],
    evidence_omissions: tuple[ReferenceEvidenceOmissionSpecV2, ...] = (),
    bounded_retrieval_evidence_ids: tuple[str, ...] | None = None,
) -> ReferenceAuthorityProofV2:
    """Rebuild from exact enrolled parents and reject any coordinated drift."""

    if type(exact_bytes) is not bytes:
        raise ReferenceClaimError("Reference authority proof requires exact bytes")
    try:
        parsed = ReferenceAuthorityProofV2.model_validate_json(exact_bytes, strict=True)
    except (ValidationError, ValueError) as exc:
        raise ReferenceClaimError(
            "Reference authority proof bytes violate strict v2 schema"
        ) from exc
    if canonical_json_bytes(parsed) != exact_bytes:
        raise ReferenceClaimError("Reference authority proof bytes are not canonical")
    rebuilt = build_reference_authority_proof(
        retriever=retriever,
        targets=targets,
        evidence=evidence,
        claim_specs=claim_specs,
        evidence_omissions=evidence_omissions,
        bounded_retrieval_evidence_ids=bounded_retrieval_evidence_ids,
    )
    if parsed != rebuilt or exact_bytes != canonical_json_bytes(rebuilt):
        raise ReferenceClaimError("Reference authority proof differs from frozen parents")
    return rebuilt


__all__ = [
    "ReferenceAuthorityCoverageReceiptV2",
    "ReferenceAuthorityProofV2",
    "ReferenceAuthorityTargetV2",
    "ReferenceAuthorityUnresolvedConditionV2",
    "ReferenceAuthorityVersionCoverageV2",
    "ReferenceClaimError",
    "ReferenceClaimV2",
    "ReferenceConflictSetV2",
    "ReferenceConflictV2",
    "ReferenceCoverageOmissionV2",
    "ReferenceEvidenceOmissionSpecV2",
    "ReferenceExactClaimSpecV2",
    "build_reference_authority_proof",
    "normalize_reference_literal",
    "normalize_resolution_key",
    "reference_authority_proof_bytes",
    "verify_reference_authority_proof",
]
