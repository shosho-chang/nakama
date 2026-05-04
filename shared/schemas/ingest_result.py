"""URL ingest pipeline output schema.

Used by ``agents.robin.url_dispatcher.URLDispatcher.dispatch()`` to convey
the outcome of a single URL fetch attempt to ``agents.robin.inbox_writer.InboxWriter``
and any downstream caller (Reader UI status column, BackgroundTask logger).

The ``fulltext_layer`` enum lists every layer the dispatcher can route through.
Slice 1 only emits ``readability`` / ``firecrawl``; the academic_* values are
reserved for Slice 2 which will wire ``agents.robin.pubmed_fulltext.fetch_fulltext``
plus arxiv / biorxiv handlers behind the same ``IngestResult`` contract.
Listing them all up-front prevents a Slice 2 schema migration from breaking
already-written ``Inbox/kb/{slug}.md`` frontmatter or forcing a callers-rewrite.

Aligns with ``docs/principles/schemas.md``:
- ``extra="forbid"`` strict
- ``Literal`` over str enums
- value-object style; mutated fields stay shallow
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

# ── Status / layer literals ──────────────────────────────────────────────────
#
# Slice 1 emits ``ready`` (markdown >= 200 chars) or ``failed`` (under threshold
# or fetch error). Slice 2+ may add intermediate states only by extending this
# Literal — InboxWriter / reader template both branch on the literal directly.

IngestStatus = Literal["ready", "failed"]

# ``readability`` covers both Trafilatura and readability-lxml — the Slice 1
# dispatcher delegates to ``shared.web_scraper.scrape_url`` which already runs
# trafilatura → readability-lxml as a single "local readability layer". The
# distinction (which sub-engine actually won) is not surfaced; if Slice 2 wants
# per-engine telemetry, it can expand this Literal without breaking writes.
IngestFullTextLayer = Literal[
    "readability",
    "firecrawl",
    "academic_pmc",
    "academic_europe_pmc",
    "academic_unpaywall",
    "academic_publisher_html",
    "academic_arxiv",
    "academic_biorxiv",
]


class IngestResult(BaseModel):
    """Single URL ingest attempt result.

    Carries enough metadata for ``InboxWriter`` to write the file plus the
    UI status column to render the right icon. Image support is stubbed for
    Slice 1 — ``image_paths`` is always ``[]`` until Slice 3 wires
    ``download_markdown_images``.
    """

    model_config = ConfigDict(extra="forbid")

    status: IngestStatus
    fulltext_layer: IngestFullTextLayer
    fulltext_source: str
    """Display label shown in inbox row / reader header (e.g. "Readability",
    "Firecrawl", "Europe PMC"). Free-form string — UI does not parse it."""

    markdown: str
    """Cleaned article body. Empty string when ``status == 'failed'``."""

    image_paths: list[str] = Field(default_factory=list)
    """Vault-relative image paths. Slice 1 always ``[]`` (Slice 3 will populate)."""

    title: str
    original_url: str
    """The exact URL the user pasted. Inbox repeat-detection looks this up
    in existing ``Inbox/kb/*.md`` frontmatter — do NOT normalise."""

    error: str | None = None
    """Short technical message when ``status == 'failed'`` (e.g. "Firecrawl
    API call failed: 502"). ``None`` on success."""

    note: str | None = None
    """Human-readable hint for the UI. Used for the Slice 1 < 200-char hard
    block: ``"抓取結果太短，疑似 bot 擋頁"``. ``None`` on success."""
