"""Entity Promotion schemas (ADR-034 v2 PR2b-ii).

Pure pydantic value-objects describing the deterministic entity promotion
engine's intermediates and final result. Engine output
(:class:`EntityPromotionResult`) is consumed by downstream slices (#515
commit gate, promotion_review_service fast-track) which wrap ``items``
into a :class:`shared.schemas.promotion_manifest.PromotionManifest` with
:class:`RecommenderMetadata`.

Parallels :mod:`shared.schemas.concept_promotion` (ADR-024 Slice 6) —
same value-object shape, same F1-analog invariant
(``error is not None`` ⇒ ``items == []``), Entity-specific fields
(``entity_type`` discriminator, ``metadata`` payload).

Closed-set extension protocol (mirrors #509 N6 / #511 / #512 / #513):
every ``Literal`` enum is frozen for ``schema_version=1`` of this result
(which corresponds to PromotionManifest ``schema_version=2`` per ADR-034
v2 V12). Adding a new entity_type (Place / Product) requires (a) adding
the Metadata class in :mod:`shared.schemas.promotion_manifest`,
(b) extending :data:`shared.schemas.promotion_manifest.EntityType`,
(c) bumping PromotionManifest ``schema_version``.

Hard invariants enforced by the engine (see ``EntityPromotionEngine``
docstring):

- E1 Every emitted ``EntityReviewItem`` is per the promotion_manifest
     schema (V1 invariant inherited — ``include`` requires evidence).
- E2 ``update_conflict_entity`` items have ``recommendation="defer"``.
- E3 ``create_entity`` items have ≥1 ``EvidenceAnchor`` AND
     ``confidence ≥ min_global_confidence``.
- E4 ``EntityReviewItem.metadata.entity_type`` matches
     ``EntityCandidate.entity_type`` (engine never crosses variants).
- E5 Engine NEVER imports ``shared.book_storage`` / ``fastapi`` /
     ``thousand_sunny.*`` / ``agents.*`` / LLM clients (T11 / T12
     subprocess gates).
- E6 On matcher exception (narrow tuple): set ``result.error``, return
     whatever items completed (degraded to ``items=[]`` per E7).
- E7 ``error is not None`` ⇒ ``items == []`` (F1-analog).
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from shared.schemas.promotion_manifest import (
    EntityCanonicalMatch,
    EntityMetadata,
    EntityReviewItem,
    EntityType,
    RiskFlag,
)


class EntityCandidate(BaseModel):
    """Engine input — one entity surface form extracted from source material.

    Frozen value-object. Caller (future YouTube/Podcast reader, LLM-backed
    NER service, textbook ingest entity extractor) constructs the list
    and passes it to ``EntityPromotionEngine.propose``. Engine never
    mutates candidates.

    Distinct from :class:`shared.schemas.concept_promotion.ConceptCandidate`
    because entity extraction is fundamentally LLM/NER-shaped and the
    engine boundary forbids LLM imports — the engine takes pre-classified
    candidates rather than extracting them from raw source maps. The
    ``entity_type`` discriminator tells the engine which metadata variant
    to attach when building the ``EntityReviewItem``.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    candidate_id: str
    """Caller-assigned stable id within one ``propose()`` run. Schema
    treats as opaque string."""

    entity_type: EntityType
    """``"person"`` or ``"organization"`` — matches
    :data:`shared.schemas.promotion_manifest.EntityType`. Engine routes
    to the matching :class:`PersonMetadata` / :class:`OrganizationMetadata`
    variant when building the review item."""

    label: str
    """Canonical surface form (``"Andrew Huberman"``, ``"Stanford
    University"``). Empty / whitespace-only labels route to ``exclude``."""

    aliases: list[str] = Field(default_factory=list)
    """Other surface forms found in source (``["Dr. Huberman", "Andrew
    D. Huberman"]``). Caller-supplied — engine does not de-duplicate."""

    evidence_language: str | None = None
    """BCP-47 short tag (``"en"`` / ``"zh-Hant"`` / ``None``). Useful
    when same entity is referenced in multiple languages."""

    source_refs: list[str] = Field(default_factory=list)
    """Stable references to where this entity appeared (chapter_ref,
    podcast timestamp range, video segment id — caller-defined transport
    string). ``len(source_refs)`` is the recurrence signal."""

    raw_quotes: list[str] = Field(default_factory=list)
    """≤ 3 short excerpts used to seed the ``EvidenceAnchor`` list on
    the eventual ``EntityReviewItem``. Caller controls cap."""

    metadata: EntityMetadata
    """Variant-specific metadata payload (Person fields / Organization
    fields). ``metadata.entity_type`` must equal ``self.entity_type``
    (enforced by validator)."""

    @model_validator(mode="after")
    def _validate_entity_type_consistency(self) -> EntityCandidate:
        # E4 invariant — engine cannot cross variants between candidate
        # and metadata.
        if self.metadata.entity_type != self.entity_type:
            raise ValueError(
                f"EntityCandidate.entity_type={self.entity_type!r} differs "
                f"from metadata.entity_type={self.metadata.entity_type!r} "
                f"(candidate_id={self.candidate_id!r})"
            )
        return self


class KBEntityEntry(BaseModel):
    """One existing global KB entity entry — minimal projection used by
    the engine when calling ``KBEntityIndex.lookup``.

    Frozen value-object. Schema does not parse ``entity_path`` for vault
    semantics (caller's responsibility downstream)."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    entity_path: str
    """``"KB/Wiki/Entities/People/Andrew Huberman.md"`` /
    ``"KB/Wiki/Entities/Organizations/Stanford University.md"``.
    Opaque transport string from the engine's perspective."""

    entity_type: EntityType
    """Must match the kind of folder the entity lives in. Index reader
    enforces this when scanning ``KB/Wiki/Entities/``."""

    canonical_label: str
    """Frontmatter ``entity_label`` from the existing page."""

    aliases: list[str] = Field(default_factory=list)
    """Frontmatter ``aliases`` list. Used by deterministic alias matching."""

    languages: list[str] = Field(default_factory=list)
    """BCP-47 short tags this entity page covers."""


class EntityMatchOutcome(BaseModel):
    """``EntityMatcher.match()`` return value.

    Frozen value-object — matchers never mutate post-construction.
    Engine relies on ``EntityCanonicalMatch.match_basis`` + ``confidence``
    + ``conflict_signals`` to choose an action.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    canonical_match: EntityCanonicalMatch
    """``match_basis``, ``confidence``, ``matched_entity_path``."""

    conflict_signals: list[str] = Field(default_factory=list)
    """Free-form notes on disagreement (``"affiliation diverges"``,
    ``"birth_year mismatch"``). Non-empty list with
    ``match_basis="exact_alias"`` routes to ``update_conflict_entity``."""


class EntityPromotionResult(BaseModel):
    """Engine output. Caller wraps ``items`` into a ``PromotionManifest``
    with ``schema_version=2``.

    Frozen value-object — emit a new result on re-run; do not mutate.

    Hard invariant (E7): ``error is not None`` ⇒ ``items == []``. Engine
    failures MUST surface as empty items + error; downstream slices MUST
    NOT consume an error+non-empty-items combination. Mirrors #511 F1 /
    #513 ``SourceMapBuildResult`` / concept_promotion F1-analog patterns.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal[1] = 1
    """Closed-set extension protocol marker for this result schema.
    Distinct from ``PromotionManifest.schema_version`` (which is 2 for
    entity-bearing manifests per ADR-034 v2 V12). Bump this when any
    field shape or invariant changes in this module."""

    source_id: str
    """Mirrors ``ReadingSource.source_id``. Opaque transport string."""

    primary_lang: str
    """Mirrors ``ReadingSource.primary_lang`` (BCP-47 short tag)."""

    candidates_received: int = Field(ge=0)
    """Number of ``EntityCandidate`` objects the caller passed in.
    May exceed ``len(items)`` because blank-label candidates are
    excluded but still counted in this field (parallels concept's
    ``candidates_extracted``).
    """

    items: list[EntityReviewItem] = Field(default_factory=list)
    """Ordered list of ``EntityReviewItem`` entries. Caller may re-sort.
    On engine failure ``items=[]`` and ``error`` is set."""

    risks: list[RiskFlag] = Field(default_factory=list)
    """Engine-level risks (e.g. ``cross_lingual_uncertain`` aggregated
    across items). Distinct from per-item ``EntityReviewItem.risk``."""

    error: str | None = None
    """``None`` on success. Code-prefixed reason
    (``"matcher_failed: ValueError(...)"``) when the engine's narrow
    exception tuple caught a documented failure. On error, ``items=[]``."""

    @model_validator(mode="after")
    def _hard_invariant_error_implies_empty_items(self) -> EntityPromotionResult:
        # E7 / F1-analog: error+items combination is forbidden.
        if self.error is not None and self.items:
            raise ValueError(
                f"error is not None requires items=[]; got {len(self.items)} "
                f"item(s) with error={self.error!r}. Engine failures must "
                f"surface as empty items + error per E7."
            )
        return self
