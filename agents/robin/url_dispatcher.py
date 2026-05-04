"""URL → IngestResult dispatcher (PRD docs/plans/2026-05-04-stage-1-ingest-unify.md).

``URLDispatcher.dispatch(url)`` is the single entry-point the
``/scrape-translate`` BackgroundTask hits. It owns:

- Layer routing (which scraper to call for what URL).
- Catching scraper exceptions and turning them into ``status='failed'``
  ``IngestResult`` rather than letting them crash the BackgroundTask.
- < 200-char hard-block (the "疑似 bot 擋頁" UI hint specified by issue #352).

Slice 1 scope: readability + firecrawl fallback only via
``shared.web_scraper.scrape_url`` (trafilatura → readability → firecrawl).
``shared.web_scraper`` does not surface which sub-layer actually won, so
Slice 1 always emits ``fulltext_layer="readability"`` for non-empty results;
the schema reserves ``firecrawl`` for when Slice 2 splits the layer signal
out of ``shared.web_scraper``. Pre-route exceptions emit
``fulltext_layer="unknown"`` so a failed scrape isn't mislabelled as a
specific working layer.

**Slice 2 will add academic source detection** — academic URL pattern matching,
PMID/DOI extraction, reverse lookup (DOI → efetch → PMID), and routing into
``agents.robin.pubmed_fulltext.fetch_fulltext`` for the 5-layer OA fallback.
arXiv / bioRxiv preprint handlers also land in Slice 2. ``URLDispatcherConfig``
already exposes the seven injection points Slice 2-4 need (fetch_fulltext_fn /
image_downloader_fn / attachments_abs_dir / vault_relative_prefix / email /
ncbi_api_key) so the constructor signature won't change.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable
from urllib.parse import urlparse

from shared.log import get_logger
from shared.schemas.ingest_result import IngestFullTextLayer, IngestResult

logger = get_logger("nakama.robin.url_dispatcher")

# < 200 字硬擋 threshold (PRD §Solution / issue #352 acceptance #4).
# Below this we mark the result failed with a "疑似 bot 擋頁" note so the UI
# row tells 修修 the page was likely chrome-only / blocked rather than a real
# article. The threshold is intentionally low — full-content classification
# is left to 修修's eyeball per PRD §Implementation Decisions.
MIN_CONTENT_CHARS = 200

# Display labels — kept here (not on the consumer side) so Slice 2 can
# extend the layer→label map alongside the new literal values.
_LAYER_DISPLAY: dict[IngestFullTextLayer, str] = {
    "readability": "Readability",
    "firecrawl": "Firecrawl",
    "pmc": "PubMed Central",
    "europe_pmc": "Europe PMC",
    "unpaywall": "Unpaywall",
    "publisher_html": "Publisher HTML",
    "arxiv": "arXiv",
    "biorxiv": "bioRxiv",
    "unknown": "(未知)",
}


@dataclass(frozen=True)
class URLDispatcherConfig:
    """Constructor-injected config for ``URLDispatcher``.

    Slice 1 only uses ``scrape_url_fn``. Slice 2 wires the academic 5-layer
    via ``fetch_fulltext_fn`` plus its required ``attachments_abs_dir`` /
    ``vault_relative_prefix`` / ``email`` / ``ncbi_api_key`` (matching the
    ``agents.robin.pubmed_fulltext.fetch_fulltext`` signature). Slice 4 wires
    ``image_downloader_fn`` for ``KB/Attachments/inbox/{slug}/`` image fetch.

    Constructor injection (over reading ``shared.config`` inside ``dispatch()``)
    keeps the dispatcher decoupled from robin agent globals and makes it
    open-source-ready (``feedback_open_source_ready``): no hardcoded personal
    email, swappable backends, no implicit vault-path coupling.
    """

    scrape_url_fn: Callable[[str], str] | None = None
    fetch_fulltext_fn: Callable[..., Any] | None = None
    image_downloader_fn: Callable[..., Any] | None = None

    attachments_abs_dir: Path | None = None
    vault_relative_prefix: str | None = None
    email: str | None = None
    ncbi_api_key: str | None = None


def _title_from_url(url: str) -> str:
    """Best-effort title from URL for placeholder + failed cases.

    InboxWriter overrides this with the first ``# heading`` line of the
    markdown when the dispatcher succeeds, but failed results still get a
    readable title (so the row in inbox view isn't blank).
    """
    parsed = urlparse(url)
    return f"{parsed.netloc}{parsed.path}".strip("/") or url


class URLDispatcher:
    """Route a URL into the right scraping layer and return an ``IngestResult``.

    Slice 1 implementation: readability + firecrawl (both via the existing
    ``shared.web_scraper.scrape_url`` chain). Academic fall-back layers are
    Slice 2.

    The dispatcher swallows scraper exceptions and converts them into
    ``status='failed'`` results — the BackgroundTask caller never sees a
    raised exception, so the inbox placeholder always gets updated to its
    final state (ready / failed) and the UI never gets stuck on 🔄.
    """

    def __init__(self, config: URLDispatcherConfig | None = None) -> None:
        """Inject ``URLDispatcherConfig``; defaults to all-None config.

        ``scrape_url_fn`` defaults to lazy-imported ``shared.web_scraper.scrape_url``
        when not injected, so Slice 1 callers can pass ``URLDispatcherConfig()``
        (or omit the argument entirely) and still get the production scraper.
        """
        self._config = config or URLDispatcherConfig()

    def dispatch(self, url: str) -> IngestResult:
        """Fetch ``url``, return an ``IngestResult``.

        Args:
            url: The URL the user pasted. Must be non-empty; URLDispatcher
                does NOT validate URL format beyond non-emptiness — let the
                caller's form validation handle that.

        Returns:
            ``IngestResult`` with ``status`` set to either ``ready`` (markdown
            >= 200 chars, written by ``InboxWriter``) or ``failed`` (scrape
            error, empty content, or < 200-char hard-block). Never raises
            on scraper failure — only ``ValueError`` for an empty URL.
        """
        if not url or not url.strip():
            raise ValueError("url must be non-empty")

        scrape_fn = self._config.scrape_url_fn or self._default_scrape

        try:
            markdown = scrape_fn(url)
        except Exception as exc:  # noqa: BLE001 — convert to failed result
            logger.warning("URL fetch failed (%s): %s", url, exc)
            return IngestResult(
                status="failed",
                fulltext_layer="unknown",
                fulltext_source=_LAYER_DISPLAY["unknown"],
                markdown="",
                title=_title_from_url(url),
                original_url=url,
                error=f"{type(exc).__name__}: {exc}",
                note=None,
            )

        layer = self._infer_layer(markdown)

        # < 200-char hard block (PRD §Solution bullet 3 / issue #352 #4).
        # Even when scrape didn't throw, treat under-threshold output as bot-blocked.
        if len(markdown) < MIN_CONTENT_CHARS:
            return IngestResult(
                status="failed",
                fulltext_layer=layer,
                fulltext_source=_LAYER_DISPLAY[layer],
                markdown="",
                title=_title_from_url(url),
                original_url=url,
                error=None,
                note="抓取結果太短，疑似 bot 擋頁",
            )

        title = self._title_from_markdown(markdown) or _title_from_url(url)

        return IngestResult(
            status="ready",
            fulltext_layer=layer,
            fulltext_source=_LAYER_DISPLAY[layer],
            markdown=markdown,
            title=title,
            original_url=url,
            error=None,
            note=None,
        )

    @staticmethod
    def _default_scrape(url: str) -> str:
        """Default scraper: existing 3-layer ``shared.web_scraper.scrape_url``.

        Lazy-imported so test patches against ``agents.robin.url_dispatcher``
        module bindings do not need to monkeypatch ``shared.web_scraper``
        in addition.
        """
        from shared.web_scraper import scrape_url

        return scrape_url(url)

    @staticmethod
    def _infer_layer(markdown: str) -> IngestFullTextLayer:
        """Slice 1 dispatcher always returns ``readability`` for non-empty output.

        ``shared.web_scraper.scrape_url`` already runs trafilatura → readability
        → firecrawl internally and returns one merged markdown blob with no
        layer signal. Surfacing per-engine telemetry would require refactoring
        ``shared.web_scraper`` (out of scope for Slice 1). We label everything
        under the "Readability" umbrella for now; Slice 2 will introduce
        per-layer reporting alongside academic detection.
        """
        return "readability"

    @staticmethod
    def _title_from_markdown(markdown: str) -> str | None:
        """First ``# heading`` line, stripped, or ``None`` if absent."""
        for line in markdown.splitlines():
            stripped = line.strip()
            if stripped.startswith("# "):
                return stripped[2:].strip() or None
        return None
