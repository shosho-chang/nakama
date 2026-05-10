"""Concept Promotion schemas (ADR-024 Slice 6 / issue #514).

Pure pydantic value-objects describing the deterministic concept promotion
engine's intermediates and final result. Engine output (``ConceptPromotionResult``)
is consumed by downstream slices (#515 commit gate, #516 review UI) which
wrap ``items`` into a ``PromotionManifest`` (#512) with ``RecommenderMetadata``.

Slice 6 is engine + schema only. State transition logic, persistence,
manifest assembly, KB commit, and Review UI live in #515 / #516. LLM-backed
match logic lives outside this slice — Slice 6 ships only the
``ConceptMatcher`` Protocol + a deterministic fixture-friendly contract
plus this schema module's value-objects.

Closed-set extension protocol (mirrors #509 N6 / #511 / #512 / #513 contract):
every ``Literal`` enum is frozen for ``schema_version=1``. Adding a new
member requires (a) bumping ``schema_version`` on ``ConceptPromotionResult``,
(b) updating this docstring + the value-object docstring, (c) updating
downstream policy in #515 / #516. Silent extension is forbidden.

Hard invariants enforced by the engine (see ``ConceptPromotionEngine`` docstring):

- C1 Every emitted ``ConceptReviewItem`` is per #512 schema (V1 invariant inherited).
- C2 ``update_conflict_global`` items have ``recommendation="defer"``.
- C3 ``keep_source_local`` items have ``recommendation in {"include", "defer"}``.
- C4 ``create_global_concept`` items have ≥1 ``EvidenceAnchor`` AND
     ``confidence ≥ min_global_confidence``.
- C5 ``ConceptReviewItem.evidence_language`` derived from candidate.evidence_language;
     non-null when source is monolingual.
- C6 Engine NEVER imports ``shared.book_storage`` / ``fastapi`` / ``thousand_sunny.*``
     / ``agents.*`` / LLM clients (T11 / T12 subprocess gates).
- C7 On matcher exception (narrow tuple): set ``result.error``, return whatever
     items completed.

Hard invariant enforced on this schema (Pydantic ``model_validator``):

- ``error is not None`` ⇒ ``items == []``.
  Engine failures MUST surface as empty items + error message; downstream
  slices (#515-#517) MUST NOT consume an error+non-empty-items combination.
  Mirrors the F1-analog fix on ``PreflightReport`` (#511) and ``SourceMapBuildResult`` (#513).
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from shared.schemas.promotion_manifest import (
    CanonicalMatch,
    ConceptReviewItem,
    RiskFlag,
)


class ConceptCandidate(BaseModel):
    """Internal engine intermediate — extracted from ``SourceMapBuildResult.items``.

    Carried into ``ConceptMatcher.match()`` input. Frozen value-object; the
    engine does not mutate candidates after extraction.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    candidate_id: str
    """Engine-assigned stable id within one ``propose()`` run. Format:
    ``"cand_{normalized_label}"``. Schema treats the string as opaque."""

    label: str
    """Canonical surface form for the concept candidate, derived from the
    leading text of the first evidence excerpt that produced this candidate.
    May be the canonical English term (``HRV``) or Chinese term (``心率變異``)
    depending on source language. Empty / whitespace-only labels route to
    ``exclude`` action per Brief §4.2 row 8."""

    aliases: list[str] = Field(default_factory=list)
    """Other surface forms found in source. Currently empty in the V1
    deterministic extractor; future LLM-backed extractors may populate."""

    evidence_language: str
    """BCP-47 short tag — derived from ``ReadingSource.primary_lang`` for
    monolingual sources. Stays consistent across all candidates from the same
    source map. Future cross-lingual extraction may set per-candidate."""

    chapter_refs: list[str] = Field(default_factory=list)
    """``SourcePageReviewItem.chapter_ref`` values where this candidate
    appeared. ``len(chapter_refs)`` is the recurrence signal used by Brief
    §4.2 row 1 / row 6 / row 7. The engine de-duplicates entries before
    storing here."""

    raw_quotes: list[str] = Field(default_factory=list)
    """≤ 3 short excerpts (engine truncates) used to seed ``EvidenceAnchor``
    list on the eventual ``ConceptReviewItem``. Drawn from the
    ``EvidenceAnchor.excerpt`` strings of the items that produced this
    candidate."""


class KBConceptEntry(BaseModel):
    """One existing global KB concept entry — minimal projection used by
    the engine when calling ``KBConceptIndex.lookup``.

    Frozen value-object. Schema does not parse ``concept_path`` for vault
    semantics — that's #515's job."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    concept_path: str
    """e.g. ``KB/Wiki/Concepts/HRV.md``. Schema treats as opaque transport
    string."""

    canonical_label: str
    """Frontmatter ``name`` or page title."""

    aliases: list[str] = Field(default_factory=list)
    """Frontmatter ``aliases`` list. Used by deterministic alias matching."""

    languages: list[str] = Field(default_factory=list)
    """BCP-47 short tags this concept page covers. Used by cross-lingual
    decision (e.g. ``["en", "zh-Hant"]`` ⇒ same page can absorb either
    language)."""


class MatchOutcome(BaseModel):
    """``ConceptMatcher.match()`` return value.

    Frozen value-object — matchers never mutate post-construction. The
    engine relies on ``CanonicalMatch.match_basis`` and ``confidence`` plus
    ``conflict_signals`` to choose an action per Brief §4.2.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    canonical_match: CanonicalMatch
    """Includes ``match_basis``, ``confidence``, ``matched_concept_path``.
    Reuses #512 schema unchanged."""

    conflict_signals: list[str] = Field(default_factory=list)
    """Free-form notes on disagreement (e.g. ``"definition diverges"``,
    ``"aliases overlap but languages differ"``). Non-empty list with
    ``match_basis="exact_alias"`` routes to ``update_conflict_global``
    per Brief §4.2 row 3."""


class ConceptPromotionResult(BaseModel):
    """Engine output. Caller wraps ``items`` into a ``PromotionManifest``.

    Frozen value-object — emit a new result on re-run; do not mutate.

    Hard invariant: ``error is not None`` ⇒ ``items == []``. Engine failures
    MUST surface as empty items + error per Brief §4.3 C7; downstream
    slices (#515-#517) MUST NOT consume an error+non-empty-items
    combination. Mirrors #511 F1 / #513 ``SourceMapBuildResult`` pattern.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal[1] = 1
    """First field on the result — closed-set extension protocol marker.
    Adding any new Literal member or invariant requires bumping this and
    updating downstream consumers (#515 / #516)."""

    source_id: str
    """Mirrors ``ReadingSource.source_id`` / ``SourceMapBuildResult.source_id``.
    Transport string only — engine NEVER parses (per #509 N3 contract)."""

    primary_lang: str
    """Mirrors ``ReadingSource.primary_lang`` (BCP-47 short tag)."""

    candidates_extracted: int = Field(ge=0)
    """Number of ``ConceptCandidate`` objects derived from the source map
    before action policy applied. May exceed ``len(items)`` because
    blank-label candidates are dropped by row 8 (``exclude``) but still
    counted in this field."""

    items: list[ConceptReviewItem] = Field(default_factory=list)
    """Ordered list of #512 ``ConceptReviewItem`` entries. Caller may
    re-sort. On engine failure (matcher exception, etc.) ``items=[]`` and
    ``error`` is set."""

    risks: list[RiskFlag] = Field(default_factory=list)
    """Engine-level risks (e.g. ``cross_lingual_uncertain`` aggregated
    across items). Distinct from per-item ``ConceptReviewItem.risk``;
    caller decides how to surface (typically: aggregate to manifest-level
    review banner)."""

    error: str | None = None
    """``None`` on success. Set to a short, code-prefixed reason (e.g.
    ``"matcher_failed: ValueError(...)"``) when the engine's narrow
    exception tuple caught a documented failure. On error, ``items=[]``
    and the caller is responsible for routing to ``defer`` (mirrors #511
    inspector_error / #513 extractor_failed policy)."""

    @model_validator(mode="after")
    def _hard_invariant_error_implies_empty_items(self) -> ConceptPromotionResult:
        # F1-analog from #511 / #513: error+items combination is forbidden.
        if self.error is not None and self.items:
            raise ValueError(
                f"error is not None requires items=[]; got {len(self.items)} "
                f"item(s) with error={self.error!r}. Engine failures must "
                f"surface as empty items + error per Brief §4.3 C7; downstream "
                f"slices (#515-#517) MUST NOT consume an error+non-empty-items "
                f"combination. Mirrors #511 F1 inspector_error / #513 "
                f"SourceMapBuildResult patterns."
            )
        return self
