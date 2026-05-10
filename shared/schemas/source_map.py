"""Source Map Builder schema (ADR-024 Slice 5 / issue #513).

Pure pydantic value-objects describing the deterministic claim-dense source map
emitted by ``shared.source_map_builder.SourceMapBuilder.build()`` for one
normalized Reading Source (#509). Builder output (``SourceMapBuildResult``) is
consumed by downstream slices (#514 concept promotion, #515 commit gate, #516
review UI) which wrap ``items`` into a ``PromotionManifest`` (#512) with
``RecommenderMetadata``.

Slice 5 is schema-only here. State transition logic, persistence, hashing,
manifest assembly, KB commit, and concept canonicalization live in #514 / #515.
LLM-backed claim extraction lives outside this slice — Slice 5 ships only the
``ClaimExtractor`` Protocol + a deterministic fixture-friendly contract.

Closed-set extension protocol (mirrors #509 N6 / #511 / #512 contract):
every ``Literal`` enum is frozen for ``schema_version=1``. Adding a new
member requires (a) bumping ``schema_version`` on ``SourceMapBuildResult``,
(b) updating this docstring + the value-object docstring, (c) updating
downstream policy in #514 / #515 / #516. Silent extension is forbidden.

Hard invariants enforced by the builder (see ``SourceMapBuilder`` docstring):

- B1 ``has_evidence_track=False`` ⇒ ``ValueError`` at ``build()`` entry.
- B3 ``EvidenceAnchor.excerpt`` length ≤ ``max_excerpt_chars`` (caller default 800).
- B4 Sum of all emitted excerpt chars ≤ 30% of inspected chapter chars.
- B5 ``SourcePageReviewItem.chapter_ref`` unique within ``items``.
- B6 Extractor failure (narrow exception tuple) → ``items=[]`` + ``error=...``.

Hard invariant enforced on this schema (Pydantic ``model_validator``):

- ``error is not None`` ⇒ ``items == []``.
  Builder failures MUST surface as empty items + error message; downstream
  slices (#514-#517) MUST NOT consume an error+non-empty-items combination.
  Mirrors the F1-analog fix on ``PreflightReport`` (#511).
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from shared.schemas.promotion_manifest import (
    RiskFlag,
    SourcePageReviewItem,
)


class ChapterCandidate(BaseModel):
    """Internal builder intermediate — what the builder identified before
    extraction. Carried into ``ClaimExtractor.extract`` input.

    Frozen value-object; the builder discards ``chapter_text`` after extraction
    so downstream callers never see raw chapter bytes (claim-dense, not mirror).
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    chapter_ref: str
    """Free-form locator owned by the builder.

    Closed-shape conventions for ``schema_version=1``:

    - ebook chapters       → ``"ch-{i}"`` (1-based spine index).
    - inbox H1/H2 sections → ``"sec-{i}"`` (1-based heading order).
    - single-page short    → ``"whole"``.
    - long-source overview → ``"index"``.

    Schema treats the string as opaque; downstream slices may re-parse but
    MUST NOT enforce a different convention without bumping schema_version.
    """

    chapter_title: str
    chapter_text: str
    """Raw chapter text. Held only on the candidate during extraction; NEVER
    persisted into ``SourceMapBuildResult`` (claim-dense invariant)."""

    char_count: int = Field(ge=0)
    word_count: int = Field(ge=0)


class QuoteAnchor(BaseModel):
    """One short-quote anchor returned by ``ClaimExtractor``. Becomes an
    ``EvidenceAnchor`` (kind=``chapter_quote``) on the emitted review item.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    excerpt: str
    """≤ ``max_excerpt_chars`` (caller default 800). Builder truncates before
    embedding so this length is the post-truncation cap."""

    locator: str
    """Format depends on chapter source.

    - ebook chapter → EPUB CFI string (or fallback ``ch-{i}#0``).
    - markdown      → ``L{start}-L{end}`` line range.

    Schema treats the string as opaque; #515 owns concrete CFI semantics."""

    confidence: float = Field(ge=0.0, le=1.0)


class ClaimExtractionResult(BaseModel):
    """One ``ClaimExtractor.extract()`` return value.

    Frozen value-object. Empty lists are legitimate (signals low extraction
    yield → builder emits ``low_signal_count`` risk per Brief §4.2).
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    claims: list[str] = Field(default_factory=list)
    """Each claim ≤ 200 chars (caller invariant; not enforced at schema level
    — extractor owns it). Builder uses ``claims[0]`` for ``reason`` synthesis."""

    key_numbers: list[str] = Field(default_factory=list)
    """e.g. ``"7.5 mmol/L"``. Empty if none. ≤ 50 chars each (caller invariant)."""

    figure_summaries: list[str] = Field(default_factory=list)
    table_summaries: list[str] = Field(default_factory=list)

    short_quotes: list[QuoteAnchor] = Field(default_factory=list)
    """Quote excerpts with locator strings. The builder converts each to an
    ``EvidenceAnchor(kind="chapter_quote", ...)`` on the review item.

    A ``recommendation="include"`` on the emitted review item requires
    ``len(short_quotes) >= 1`` (per #512 V1). When empty, the builder emits
    ``recommendation="defer"`` instead."""

    extraction_confidence: float = Field(ge=0.0, le=1.0)


class SourceMapBuildResult(BaseModel):
    """Builder output. Caller wraps ``items`` into a ``PromotionManifest``
    with ``RecommenderMetadata`` + manifest-level ids (out of scope here).

    Frozen value-object — emit a new result on re-run; do not mutate.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal[1] = 1
    """First field on the result — closed-set extension protocol marker.
    Adding any new Literal member or invariant requires bumping this and
    updating downstream consumers (#514 / #515 / #516)."""

    source_id: str
    """Mirrors ``ReadingSource.source_id``. Transport string only — the
    builder NEVER parses it (per #509 N3 contract)."""

    primary_lang: str
    """Mirrors ``ReadingSource.primary_lang`` (BCP-47 short tag)."""

    has_evidence_track: bool
    """Mirrors ``ReadingSource.has_evidence_track``. Builder enforces
    ``True`` at ``build()`` entry (B1); persisted on the result for
    downstream parity with #511 / #512 contracts."""

    chapters_inspected: int = Field(ge=0)
    """Number of ``ChapterCandidate`` objects passed to the extractor.
    May exceed ``len(items)`` when the builder emits an ``index`` overview
    in addition to per-chapter items (long source layout)."""

    items: list[SourcePageReviewItem] = Field(default_factory=list)
    """Ordered. Long-source layout: ``index`` overview first, then per
    chapter. Short-source layout: a single ``whole`` consolidated item.

    On builder failure (extractor exception, blob unreadable, etc.),
    ``items`` is empty and ``error`` is set; caller routes to ``defer``."""

    risks: list[RiskFlag] = Field(default_factory=list)
    """Build-level risks (e.g. ``weak_toc`` when the ebook has no chapters
    detected, ``ocr_artifact`` when text extraction surfaces noise). Distinct
    from per-item ``SourcePageReviewItem.risk``; caller decides how to
    surface (typically: aggregate to manifest-level review banner)."""

    error: str | None = None
    """``None`` on success. Set to a short, code-prefixed reason (e.g.
    ``"extractor_failed: ValueError(...)"``) when the builder's narrow
    exception tuple caught a documented failure. On error, ``items=[]``
    and the caller is responsible for routing to ``defer`` (mirrors #511
    inspector_error policy)."""

    @model_validator(mode="after")
    def _hard_invariant_error_implies_empty_items(self) -> SourceMapBuildResult:
        if self.error is not None and self.items:
            raise ValueError(
                f"error is not None requires items=[]; got {len(self.items)} "
                f"item(s) with error={self.error!r}. Builder failures must "
                f"surface as empty items + error per Brief §6 / B6; downstream "
                f"slices (#514-#517) MUST NOT consume an error+non-empty-items "
                f"combination. Mirrors #511 F1 inspector_error/defer pattern."
            )
        return self
