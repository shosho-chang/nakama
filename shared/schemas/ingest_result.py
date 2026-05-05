"""URL ingest pipeline output schema.

Used by ``agents.robin.url_dispatcher.URLDispatcher.dispatch()`` to convey
the outcome of a single URL fetch attempt to ``agents.robin.inbox_writer.InboxWriter``
and any downstream caller (Reader UI status column, BackgroundTask logger).

The ``fulltext_layer`` enum lists every layer the dispatcher can route through.
Slice 1 emits ``readability`` (general path) or ``unknown`` (pre-route exception).
Slice 2 will emit ``pmc`` / ``europe_pmc`` / ``unpaywall`` / ``publisher_html``
(via ``agents.robin.pubmed_fulltext.fetch_fulltext`` reuse) plus ``arxiv`` /
``biorxiv`` (preprint API). Layer names are bare (no ``academic_`` prefix) to
align with ``pubmed_fulltext.fetch_fulltext`` ``source`` return values
(``pmc`` / ``europe_pmc`` / ``unpaywall``) so the Slice 2 adapter can pass
them through verbatim.

Listing every value up-front prevents a Slice 2 schema migration from breaking
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
# Lifecycle states an inbox row passes through:
#   processing → ready → translated   (happy path through Slice 1+3)
#   processing → failed                (scrape error / < 200 char / BG crash)
#
# ``processing`` is written synchronously by the placeholder writer before the
# BackgroundTask runs — InboxWriter must accept it on the way in even though
# URLDispatcher.dispatch() never returns it. ``translated`` is reserved for
# Slice 3's ``/translate`` endpoint (writes ``{slug}-bilingual.md`` and updates
# the original frontmatter to track that the bilingual variant exists).

IngestStatus = Literal["processing", "ready", "translated", "failed"]

# Layer values align with ``pubmed_fulltext.fetch_fulltext`` ``source`` return
# values so the Slice 2 adapter can pass them through unchanged. ``readability``
# covers both Trafilatura and readability-lxml — the Slice 1 dispatcher
# delegates to ``shared.web_scraper.scrape_url`` which runs them in sequence
# as one "local readability layer" without surfacing which sub-engine won.
# ``unknown`` is for the pre-route exception path (no layer ran successfully).
# Slice 2 will introduce per-layer telemetry by widening this Literal.
IngestFullTextLayer = Literal[
    "readability",
    "firecrawl",
    "pmc",
    "europe_pmc",
    "unpaywall",
    "publisher_html",
    "arxiv",
    "biorxiv",
    "zotero_html_snapshot",
    "zotero_pdf",
    "unknown",
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

    # ── Zotero-specific (None for non-Zotero ingests) ────────────────────────
    #
    # Populated by ``agents.robin.zotero_sync.sync_zotero_item`` so the
    # downstream ``InboxWriter`` can emit Zotero-aware frontmatter
    # (``zotero_item_key`` / ``zotero_attachment_path`` / ``attachment_type``)
    # without changing its public signature. Future per-source ingest paths
    # (EPUB / podcast) can add their own optional fields the same way.
    zotero_item_key: str | None = None
    zotero_attachment_path: str | None = None
    attachment_type: Literal["text/html", "application/pdf"] | None = None
