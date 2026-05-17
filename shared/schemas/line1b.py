"""Typed Pydantic v2 schema for Brook Line 1b Stage 1 extractor output.

Line 1b = interview SRT + closed-pool research_pack (訪前研究包) →
single LLM call produces a canonical brief consumed by all 3 channel
renderers (blog / fb / ig). See ADR-027 §Decision 5.

Why typed (not ADR-014 untyped `data: dict`)?
- ADR-014 left Stage 1 shape open because Line 1 / Line 2 / Line 3 narrative
  skeletons differ. Within a single line, typed contracts are still preferred.
- ADR-027 §Decision 5: "Line1bStage1Result 採 typed pydantic ... 1b 新合約應
  fail loudly. ADR-014「untyped」原意是 cross-line schema 不該硬統一,
  single-line 內 typed 不違反精神."

Citation discipline (ADR-027 §Decision 6, layer 3):
- Each `NarrativeSegment.text` SHOULD end with `[source: <slug>]` or
  `[transcript@HH:MM]` markers.
- Post-process scans for the presence of these markers; missing ones are
  flagged via `NarrativeSegment.warning = "⚠️ no_citation"` (reminder, not
  hard-fail — see `feedback_redline_self_discipline_not_enforcement`).
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

NoCitationWarning = Literal["⚠️ no_citation"]


class Quote(BaseModel):
    """A verbatim quote from the transcript with timestamp + speaker."""

    model_config = ConfigDict(extra="forbid")

    text: str = Field(min_length=1)
    timestamp: str = Field(min_length=1, description="HH:MM:SS within the transcript")
    speaker: str = Field(min_length=1)
    original_language: str | None = Field(
        default=None,
        description=(
            "ISO 639-1 code if the quote was translated (e.g. 'en' for an "
            "English source rendered into 修修-voice 中文). None = no translation."
        ),
    )
    original_text: str | None = Field(
        default=None,
        description=(
            "Original-language verbatim, preserved when `text` is a translation. "
            "Per ADR-027 §Decision 9: 「保留原英文 quote 在 evidence」"
        ),
    )


class BookContextItem(BaseModel):
    """A reference to a book (or article) from the closed research_pack pool."""

    model_config = ConfigDict(extra="forbid")

    slug: str = Field(min_length=1, description="KB path of the source, e.g. 'KB/Wiki/Sources/book-x'")
    title: str = Field(min_length=1)
    author: str = ""
    note: str = ""
    """Why this source matters for the interview brief (1-2 sentences)."""


class CrossRef(BaseModel):
    """A link between a transcript point and a research_pack source.

    Used by renderers to surface 'guest said X, this echoes book Y' moments
    without re-deriving them from raw transcript + pack.
    """

    model_config = ConfigDict(extra="forbid")

    transcript_anchor: str = Field(
        min_length=1,
        description="Short quote / paraphrase from transcript (≤80 chars).",
    )
    transcript_timestamp: str = Field(min_length=1)
    source_slug: str = Field(min_length=1, description="KB path of the cited source.")
    relation: str = Field(
        min_length=1,
        description="One sentence describing the link (echo / contrast / extends / contradicts).",
    )


class NarrativeSegment(BaseModel):
    """One narrative beat of the brief, derived from transcript and/or research_pack.

    `text` is expected to end with a citation marker:
        - `[source: <slug>]` for research_pack-derived
        - `[transcript@HH:MM]` for transcript-derived
    If neither is present, `warning` is set to ⚠️ no_citation by post-process.
    """

    model_config = ConfigDict(extra="forbid")

    text: str = Field(min_length=1)
    citations: list[str] = Field(
        default_factory=list,
        description="Extracted citation tokens (source slugs or transcript timestamps).",
    )
    warning: NoCitationWarning | None = None


class Line1bStage1Result(BaseModel):
    """Canonical typed output of `Line1bExtractor.extract()`.

    Consumed by all three ChannelRenderers (blog / fb / ig) via the shared
    `brief` field. Per ADR-027 §Decision 5: multi-channel voice consistency
    requires a single Stage-1 brief, NOT three independent renderings.
    """

    model_config = ConfigDict(extra="forbid")

    narrative_segments: list[NarrativeSegment] = Field(min_length=1)
    quotes: list[Quote] = Field(min_length=1)
    titles: list[str] = Field(min_length=3, max_length=8)
    book_context: list[BookContextItem] = Field(default_factory=list)
    cross_refs: list[CrossRef] = Field(default_factory=list)
    brief: str = Field(
        min_length=1,
        description=(
            "Single canonical brief in 修修-voice 繁中. All three channel renderers "
            "consume this as their shared input under Line 1b mode."
        ),
    )

    @property
    def has_warnings(self) -> bool:
        """True if any narrative_segment was flagged (no citation, etc.)."""
        return any(seg.warning is not None for seg in self.narrative_segments)

    @model_validator(mode="after")
    def _at_least_one_research_or_transcript(self) -> Line1bStage1Result:
        # An empty book_context + empty cross_refs is allowed (e.g. interview
        # with no pre-read research). Do not enforce — left to caller.
        return self
