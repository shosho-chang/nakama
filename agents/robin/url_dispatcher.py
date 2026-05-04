"""URL → IngestResult dispatcher (PRD docs/plans/2026-05-04-stage-1-ingest-unify.md).

``URLDispatcher.dispatch(url)`` is the single entry-point the
``/scrape-translate`` BackgroundTask hits. It owns:

- Layer routing (which scraper to call for what URL).
- Catching scraper exceptions and turning them into ``status='failed'``
  ``IngestResult`` rather than letting them crash the BackgroundTask.
- < 200-char hard-block (the "疑似 bot 擋頁" UI hint specified by issue #352).
- Image fetch hook (Slice 4): when ``image_downloader_fn`` is configured,
  external image URLs in the scraped markdown are downloaded to vault and
  the markdown body is rewritten to vault-relative paths before the
  ``IngestResult`` is constructed (single-shot, no post-build mutation —
  honours ``docs/principles/schemas.md`` value-object semantics).

Slice 1 scope: readability + firecrawl fallback only via
``shared.web_scraper.scrape_url`` (trafilatura → readability → firecrawl).
``shared.web_scraper`` does not surface which sub-layer actually won, so
Slice 1 always emits ``fulltext_layer="readability"`` for non-empty results;
the schema reserves ``firecrawl`` for when a future slice splits the layer
signal out of ``shared.web_scraper``. Pre-route exceptions emit
``fulltext_layer="unknown"`` so a failed scrape isn't mislabelled as a
specific working layer.

Slice 2 adds academic source detection — URL pattern matching, PMID/DOI
extraction, DOI → PMID reverse lookup (NCBI esearch), and routing into
``agents.robin.pubmed_fulltext.fetch_fulltext`` for the 5-layer OA fallback
(PMC → Europe PMC → Unpaywall → publisher HTML). arXiv and bioRxiv/medRxiv
preprints route to their respective APIs instead of ``fetch_fulltext``.
``URLDispatcherConfig`` exposes the injection points (fetch_fulltext_fn /
fulltext_attachments_abs_dir / fulltext_vault_relative_prefix / email / ncbi_api_key) for the
router to wire. When ``fetch_fulltext_fn`` is not configured, academic
pubmed/doi/publisher patterns fall through to readability; arXiv/bioRxiv still
route via their own lightweight APIs since those need no injection.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable
from urllib.parse import urlparse

import httpx

from shared.log import get_logger
from shared.schemas.ingest_result import IngestFullTextLayer, IngestResult

logger = get_logger("nakama.robin.url_dispatcher")

# < 200 字硬擋 threshold (PRD §Solution / issue #352 acceptance #4).
MIN_CONTENT_CHARS = 200

# ── Academic URL patterns ─────────────────────────────────────────────────────

_PUBMED_URL_RE = re.compile(r"pubmed\.ncbi\.nlm\.nih\.gov/(\d+)/?$", re.IGNORECASE)
_DOI_URL_RE = re.compile(r"(?:dx\.)?doi\.org/(10\.\d{4,}/[^\s?&#]+)", re.IGNORECASE)
# DOI embedded in URL path (e.g. Springer: /article/10.1007/s00125-024-06100-3)
_DOI_IN_PATH_RE = re.compile(r"/(10\.\d{4,}/[^\s?&#/\"']{4,})")
_ARXIV_URL_RE = re.compile(
    r"arxiv\.org/(?:abs|pdf|html)/([^\s/?#]+?)(?:v\d+)?(?:\.pdf)?$",
    re.IGNORECASE,
)
_BIORXIV_URL_RE = re.compile(
    r"biorxiv\.org/content/(10\.\d{4,}/[^\s?&#]+?)(?:v\d+)?(?:\.full(?:\.pdf)?|\.pdf)?(?:[?#].*)?$",
    re.IGNORECASE,
)
_MEDRXIV_URL_RE = re.compile(
    r"medrxiv\.org/content/(10\.\d{4,}/[^\s?&#]+?)(?:v\d+)?(?:\.full(?:\.pdf)?|\.pdf)?(?:[?#].*)?$",
    re.IGNORECASE,
)

# Two-step citation_doi meta-tag extraction: locate the tag, then pull content=.
_META_CITATION_DOI_RE = re.compile(
    r"<meta\s[^>]{0,300}citation_doi[^>]{0,300}>",
    re.IGNORECASE | re.DOTALL,
)
_CONTENT_ATTR_RE = re.compile(r'\bcontent\s*=\s*["\']([^"\']+)["\']', re.IGNORECASE)

# Known academic publisher hostnames (subdomain-aware via _is_publisher_domain).
_PUBLISHER_DOMAINS: frozenset[str] = frozenset(
    [
        "thelancet.com",
        "bmj.com",
        "nature.com",
        "cell.com",
        "nejm.org",
        "science.org",
        "plos.org",
        "link.springer.com",
        "onlinelibrary.wiley.com",
        "jamanetwork.com",
        "academic.oup.com",
        "sciencedirect.com",
    ]
)

# External API endpoints
_NCBI_ESEARCH = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/esearch.fcgi"
_ARXIV_API = "https://export.arxiv.org/api/query"
_BIORXIV_API = "https://api.biorxiv.org/details"

# Display labels — OA sources get ✓ (quality signal); scrapers get ⚠️.
# Kept here so Slice 2 can extend alongside new literal values.
_LAYER_DISPLAY: dict[IngestFullTextLayer, str] = {
    "readability": "Readability ⚠️",
    "firecrawl": "Firecrawl ⚠️",
    "pmc": "OA from PMC ✓",
    "europe_pmc": "OA from Europe PMC ✓",
    "unpaywall": "OA from Unpaywall ✓",
    "publisher_html": "Publisher HTML ⚠️",
    "arxiv": "arXiv Preprint",
    "biorxiv": "bioRxiv Preprint",
    "unknown": "(未知)",
}

# arXiv Atom entry parser
_ARXIV_ENTRY_RE = re.compile(r"<entry>(.*?)</entry>", re.DOTALL)


def _is_publisher_domain(hostname: str) -> bool:
    """True when hostname is (or is a subdomain of) a known academic publisher."""
    return any(hostname == d or hostname.endswith("." + d) for d in _PUBLISHER_DOMAINS)


def _detect_academic_source(url: str) -> tuple[str, str] | None:
    """Classify a URL as an academic source and extract its key identifier.

    Returns one of:
      ``("pubmed", pmid)``      — PubMed article page
      ``("doi", doi_str)``      — doi.org redirector or DOI embedded in URL path
      ``("arxiv", arxiv_id)``   — arXiv abstract / PDF
      ``("biorxiv", doi_str)``  — bioRxiv preprint
      ``("medrxiv", doi_str)``  — medRxiv preprint
      ``("publisher", url)``    — known publisher domain; no DOI in URL path
      ``None``                  — not an academic source
    """
    parsed = urlparse(url)
    hostname = (parsed.hostname or "").lower()

    # PubMed article page
    m = _PUBMED_URL_RE.search(url)
    if m:
        return ("pubmed", m.group(1))

    # doi.org / dx.doi.org redirector
    if "doi.org" in hostname:
        m = _DOI_URL_RE.search(url)
        if m:
            return ("doi", m.group(1).rstrip(".,;)/>"))

    # arXiv
    if "arxiv.org" in hostname:
        m = _ARXIV_URL_RE.search(url)
        if m:
            return ("arxiv", m.group(1))

    # bioRxiv
    if "biorxiv.org" in hostname:
        m = _BIORXIV_URL_RE.search(url)
        if m:
            return ("biorxiv", m.group(1).rstrip(".,;)/>"))

    # medRxiv
    if "medrxiv.org" in hostname:
        m = _MEDRXIV_URL_RE.search(url)
        if m:
            return ("medrxiv", m.group(1).rstrip(".,;)/>"))

    # Publisher URL: DOI in path wins; otherwise needs HTML meta-tag fetch.
    if _is_publisher_domain(hostname):
        m = _DOI_IN_PATH_RE.search(url)
        if m:
            return ("doi", m.group(1).rstrip(".,;)/>"))
        return ("publisher", url)

    return None


def _parse_arxiv_atom(xml: str) -> tuple[str, str]:
    """Extract ``(title, abstract)`` from an arXiv Atom API response."""
    m = _ARXIV_ENTRY_RE.search(xml)
    if not m:
        return ("", "")
    entry = m.group(1)
    title_m = re.search(r"<title>(.*?)</title>", entry, re.DOTALL)
    summary_m = re.search(r"<summary>(.*?)</summary>", entry, re.DOTALL)
    title = title_m.group(1).strip().replace("\n", " ") if title_m else ""
    abstract = summary_m.group(1).strip() if summary_m else ""
    return (title, abstract)


def _title_from_url(url: str) -> str:
    """Best-effort title from URL for placeholder + failed cases."""
    parsed = urlparse(url)
    return f"{parsed.netloc}{parsed.path}".strip("/") or url


@dataclass(frozen=True)
class URLDispatcherConfig:
    """Constructor-injected config for ``URLDispatcher``.

    Slice 1 only uses ``scrape_url_fn``. Slice 2 wires the academic 5-layer
    via ``fetch_fulltext_fn`` plus its required ``fulltext_attachments_abs_dir`` /
    ``fulltext_vault_relative_prefix`` / ``email`` / ``ncbi_api_key`` (matching
    the ``agents.robin.pubmed_fulltext.fetch_fulltext`` signature). Slice 4 wires
    ``image_downloader_fn`` plus ``image_attachments_abs_dir`` /
    ``image_vault_relative_prefix`` for per-URL ``KB/Attachments/inbox/{slug}/``
    image fetch — separate slot from fetch_fulltext's PDF dir to avoid path
    collision when a URL routes through both pipelines.

    Constructor injection (over reading ``shared.config`` inside ``dispatch()``)
    keeps the dispatcher decoupled from robin agent globals and makes it
    open-source-ready (``feedback_open_source_ready``): no hardcoded personal
    email, swappable backends, no implicit vault-path coupling.
    """

    scrape_url_fn: Callable[[str], str] | None = None
    fetch_fulltext_fn: Callable[..., Any] | None = None
    image_downloader_fn: Callable[..., Any] | None = None

    # fetch_fulltext_fn writes PDFs / OA HTML md here (e.g. ``KB/Attachments/pubmed``).
    # Slice 2 wires this; Slice 4 image fetch ignores it.
    fulltext_attachments_abs_dir: Path | None = None
    fulltext_vault_relative_prefix: str | None = None

    # image_downloader_fn writes per-URL images here (e.g. ``KB/Attachments/inbox/{slug}/``).
    # Slice 4 wires this; Slice 2 fetch_fulltext ignores it.
    image_attachments_abs_dir: Path | None = None
    image_vault_relative_prefix: str | None = None

    email: str | None = None
    ncbi_api_key: str | None = None


class URLDispatcher:
    """Route a URL into the right scraping layer and return an ``IngestResult``.

    Slice 1: readability + firecrawl (both via ``shared.web_scraper.scrape_url``).
    Slice 2: academic source detection → 5-layer OA fallback / preprint APIs.

    The dispatcher swallows scraper / API exceptions and converts them into
    ``status='failed'`` results — the BackgroundTask caller never sees a raised
    exception, so the inbox placeholder always gets updated to its final state.
    """

    def __init__(self, config: URLDispatcherConfig | None = None) -> None:
        self._config = config or URLDispatcherConfig()

    def dispatch(self, url: str) -> IngestResult:
        """Fetch ``url``, return an ``IngestResult``.

        Academic source detection (Slice 2) runs first; arXiv/bioRxiv always
        route via their own APIs. pubmed/doi/publisher routing only activates
        when ``fetch_fulltext_fn`` is configured; otherwise falls through to the
        general readability path below.
        """
        if not url or not url.strip():
            raise ValueError("url must be non-empty")

        # ── Academic routing (Slice 2) ────────────────────────────────────────
        academic = _detect_academic_source(url)
        if academic is not None:
            kind, identifier = academic
            if kind == "arxiv":
                return self._dispatch_arxiv(identifier, url)
            if kind in ("biorxiv", "medrxiv"):
                return self._dispatch_biorxiv(identifier, kind, url)
            # pubmed / doi / publisher — only when fetch_fulltext_fn is wired
            if self._config.fetch_fulltext_fn is not None:
                pmid = self._resolve_pmid(kind, identifier)
                if pmid is not None:
                    return self._dispatch_via_fulltext(pmid, url)

        # ── General readability path (Slice 1) ────────────────────────────────
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

        # Slice 4: image fetch + markdown rewrite. Runs only when the caller
        # injected ``image_downloader_fn`` AND its required attachments paths
        # (the router does; unit tests for the dispatcher's layer logic don't
        # need to). We compute the rewritten markdown + image_paths first and
        # then construct ``IngestResult`` once — no post-construction mutation,
        # so ``IngestResult`` stays a value-object per ``docs/principles/schemas.md``.
        rewritten_markdown, image_paths = self._maybe_download_images(markdown, url)

        return IngestResult(
            status="ready",
            fulltext_layer=layer,
            fulltext_source=_LAYER_DISPLAY[layer],
            markdown=rewritten_markdown,
            image_paths=image_paths,
            title=title,
            original_url=url,
            error=None,
            note=None,
        )

    def _maybe_download_images(self, markdown: str, url: str) -> tuple[str, list[str]]:
        """Run the configured image downloader, or return inputs unchanged.

        Returns a ``(markdown, image_paths)`` pair so the caller can build the
        IngestResult once. When the downloader is not configured, or required
        config fields (``image_attachments_abs_dir`` / ``image_vault_relative_prefix``)
        are missing, this is a no-op — the original markdown flows through with
        an empty ``image_paths`` list (Slice 1 baseline behaviour).

        Downloader exceptions are caught + logged but do NOT fail the dispatch
        — image-fetch failure should degrade to "no images, original markdown"
        rather than tag the whole ingest as failed (single bad image must not
        kill an otherwise-good article — same defence philosophy as the scrape
        exception handler above).
        """
        downloader = self._config.image_downloader_fn
        attachments_abs_dir = self._config.image_attachments_abs_dir
        vault_relative_prefix = self._config.image_vault_relative_prefix
        if downloader is None or attachments_abs_dir is None or vault_relative_prefix is None:
            return markdown, []

        try:
            rewritten, paths = downloader(
                markdown,
                attachments_abs_dir,
                vault_relative_prefix,
            )
        except Exception as exc:  # noqa: BLE001 — degrade, don't fail
            logger.warning(
                "image downloader failed (url=%s): %s — keeping original markdown",
                url,
                exc,
            )
            return markdown, []

        # Defensive: a misbehaving downloader could return non-list paths.
        # Normalise so downstream Pydantic validation doesn't blow up.
        if not isinstance(paths, list):
            logger.warning(
                "image downloader returned non-list image_paths (%s); coercing to []",
                type(paths).__name__,
            )
            paths = []
        return rewritten, paths

    # ── Slice 2 academic handlers ─────────────────────────────────────────────

    def _resolve_pmid(self, kind: str, identifier: str) -> str | None:
        """Resolve (kind, identifier) → PMID, or None on failure.

        "pubmed": identifier is already the PMID.
        "doi": reverse-lookup via NCBI esearch.
        "publisher": fetch page HTML, extract citation_doi, then esearch.
        """
        if kind == "pubmed":
            return identifier
        if kind == "doi":
            return self._doi_to_pmid(identifier)
        if kind == "publisher":
            doi = self._extract_doi_from_html(identifier)
            return self._doi_to_pmid(doi) if doi else None
        return None

    def _doi_to_pmid(self, doi: str) -> str | None:
        """Reverse-lookup PMID for a DOI via NCBI esearch. Returns None on any failure."""
        cfg = self._config
        if not cfg.email:
            return None
        params: dict[str, str] = {
            "db": "pubmed",
            "term": f"{doi}[doi]",
            "retmode": "json",
            "retmax": "1",
        }
        if cfg.ncbi_api_key:
            params["api_key"] = cfg.ncbi_api_key
        try:
            r = httpx.get(
                _NCBI_ESEARCH,
                params=params,
                headers={"User-Agent": f"Nakama-Robin/1.0 (+{cfg.email})"},
                timeout=15.0,
            )
            r.raise_for_status()
            ids = r.json().get("esearchresult", {}).get("idlist", [])
            return ids[0] if ids else None
        except Exception as exc:
            logger.warning("DOI→PMID lookup failed (%s): %s", doi, exc)
            return None

    def _extract_doi_from_html(self, url: str, *, timeout: float = 15.0) -> str | None:
        """Fetch publisher page and extract DOI from ``citation_doi`` meta tag.

        Two-stage fetch:
          1. plain httpx with our UA — fast, no quota cost
          2. Firecrawl raw HTML fallback — bypasses bot detection / cloudflare
             (Lancet, NEJM, Cell etc. block plain httpx with a custom UA)

        Failures at every stage are logged at WARNING (not DEBUG) so the
        operator sees publisher detection→DOI extract failures in production
        logs. Without this, the dispatcher silently falls through to the
        readability scraper and the user gets page chrome labelled "ready".
        """
        cfg = self._config
        ua = f"Nakama-Robin/1.0 (+{cfg.email})" if cfg.email else "Nakama-Robin/1.0"
        html: str | None = None
        try:
            r = httpx.get(
                url,
                headers={"User-Agent": ua},
                timeout=timeout,
                follow_redirects=True,
            )
            r.raise_for_status()
            html = r.text
        except Exception as exc:
            logger.warning(
                "Publisher meta-tag fetch via httpx failed (%s): %s — trying Firecrawl",
                url,
                exc,
            )

        doi = self._parse_citation_doi(html) if html else None
        if doi:
            return doi

        if html is not None:
            # httpx succeeded but no citation_doi meta tag in HTML — likely the
            # publisher gated the article behind JS and httpx got chrome only.
            logger.warning(
                "Publisher meta-tag absent in httpx HTML (%s) — trying Firecrawl",
                url,
            )

        # Firecrawl fallback. Will raise if FIRECRAWL_API_KEY missing or quota
        # exhausted; treat any failure as "no DOI" — caller falls through to
        # readability path with the existing log signal.
        try:
            from shared.web_scraper import fetch_html_via_firecrawl

            logger.info("Publisher meta-tag fallback to Firecrawl: %s", url)
            fc_html = fetch_html_via_firecrawl(url)
        except Exception as exc:
            logger.warning(
                "Publisher meta-tag Firecrawl fallback failed (%s): %s — no DOI extracted",
                url,
                exc,
            )
            return None

        doi = self._parse_citation_doi(fc_html)
        if doi:
            return doi

        logger.warning(
            "Publisher meta-tag absent in both httpx and Firecrawl HTML (%s) — no DOI extracted",
            url,
        )
        return None

    @staticmethod
    def _parse_citation_doi(html: str | None) -> str | None:
        """Pull the ``content`` value out of a ``<meta name="citation_doi">`` tag."""
        if not html:
            return None
        m_tag = _META_CITATION_DOI_RE.search(html)
        if not m_tag:
            return None
        m_val = _CONTENT_ATTR_RE.search(m_tag.group(0))
        if not m_val:
            return None
        doi = m_val.group(1).strip()
        return doi if doi.startswith("10.") else None

    def _fulltext_to_markdown(self, ft: Any, pmid: str) -> str:
        """Convert a FullTextResult's downloaded files to markdown content."""
        cfg = self._config
        if cfg.fulltext_attachments_abs_dir is None:
            return ""
        status = ft.get("status")
        if status == "oa_html":
            md_path = cfg.fulltext_attachments_abs_dir / f"{pmid}.md"
            if md_path.exists():
                try:
                    return md_path.read_text(encoding="utf-8")
                except OSError:
                    pass
        elif status == "oa_downloaded":
            pdf_path = cfg.fulltext_attachments_abs_dir / f"{pmid}.pdf"
            if pdf_path.exists():
                try:
                    from shared.pdf_parser import parse_pdf  # lazy — heavy dep

                    return parse_pdf(pdf_path)
                except Exception as exc:
                    logger.warning("PDF parse failed (pmid=%s): %s", pmid, exc)
        return ""

    def _dispatch_via_fulltext(self, pmid: str, original_url: str) -> IngestResult:
        """Route a PMID through the 5-layer OA fallback engine."""
        cfg = self._config
        try:
            ft = cfg.fetch_fulltext_fn(
                pmid,
                attachments_abs_dir=cfg.fulltext_attachments_abs_dir,
                vault_relative_prefix=cfg.fulltext_vault_relative_prefix or "",
                email=cfg.email,
                ncbi_api_key=cfg.ncbi_api_key,
            )
        except Exception as exc:
            logger.warning("fetch_fulltext failed (pmid=%s): %s", pmid, exc)
            return IngestResult(
                status="failed",
                fulltext_layer="unknown",
                fulltext_source=_LAYER_DISPLAY["unknown"],
                markdown="",
                title=_title_from_url(original_url),
                original_url=original_url,
                error=f"{type(exc).__name__}: {exc}",
                note=None,
            )

        ft_status = ft.get("status")
        if ft_status in ("needs_manual", "not_found"):
            return IngestResult(
                status="failed",
                fulltext_layer="unknown",
                fulltext_source=_LAYER_DISPLAY["unknown"],
                markdown="",
                title=_title_from_url(original_url),
                original_url=original_url,
                error=None,
                note=ft.get("note") or "無法取得 OA 全文，請手動下載",
            )

        source = ft.get("source")
        layer: IngestFullTextLayer = source if source in _LAYER_DISPLAY else "unknown"
        markdown = self._fulltext_to_markdown(ft, pmid)

        if not markdown or len(markdown) < MIN_CONTENT_CHARS:
            return IngestResult(
                status="failed",
                fulltext_layer=layer,
                fulltext_source=_LAYER_DISPLAY[layer],
                markdown="",
                title=_title_from_url(original_url),
                original_url=original_url,
                error=None,
                note="全文下載後內容太短或無法解析",
            )

        title = self._title_from_markdown(markdown) or _title_from_url(original_url)
        return IngestResult(
            status="ready",
            fulltext_layer=layer,
            fulltext_source=_LAYER_DISPLAY[layer],
            markdown=markdown,
            title=title,
            original_url=original_url,
            error=None,
            note=None,
        )

    def _dispatch_arxiv(self, arxiv_id: str, original_url: str) -> IngestResult:
        """Fetch arXiv paper metadata via Atom API and build markdown."""
        try:
            r = httpx.get(
                _ARXIV_API,
                params={"id_list": arxiv_id, "max_results": "1"},
                timeout=15.0,
            )
            r.raise_for_status()
        except Exception as exc:
            logger.warning("arXiv API failed (%s): %s", arxiv_id, exc)
            return IngestResult(
                status="failed",
                fulltext_layer="unknown",
                fulltext_source=_LAYER_DISPLAY["unknown"],
                markdown="",
                title=_title_from_url(original_url),
                original_url=original_url,
                error=f"{type(exc).__name__}: {exc}",
                note=None,
            )

        title, abstract = _parse_arxiv_atom(r.text)
        if not abstract:
            return IngestResult(
                status="failed",
                fulltext_layer="arxiv",
                fulltext_source=_LAYER_DISPLAY["arxiv"],
                markdown="",
                title=title or _title_from_url(original_url),
                original_url=original_url,
                error=None,
                note="arXiv API 回傳空摘要",
            )

        markdown = (f"# {title}\n\n" if title else "") + abstract + "\n"
        if len(markdown) < MIN_CONTENT_CHARS:
            return IngestResult(
                status="failed",
                fulltext_layer="arxiv",
                fulltext_source=_LAYER_DISPLAY["arxiv"],
                markdown="",
                title=title or _title_from_url(original_url),
                original_url=original_url,
                error=None,
                note="arXiv 摘要太短",
            )

        return IngestResult(
            status="ready",
            fulltext_layer="arxiv",
            fulltext_source=_LAYER_DISPLAY["arxiv"],
            markdown=markdown,
            title=title or _title_from_url(original_url),
            original_url=original_url,
            error=None,
            note=None,
        )

    def _dispatch_biorxiv(self, doi: str, server: str, original_url: str) -> IngestResult:
        """Fetch bioRxiv / medRxiv paper via biorxiv.org API."""
        display = "medRxiv Preprint" if server == "medrxiv" else _LAYER_DISPLAY["biorxiv"]
        try:
            r = httpx.get(f"{_BIORXIV_API}/{server}/{doi}", timeout=15.0)
            r.raise_for_status()
            data = r.json()
        except Exception as exc:
            logger.warning("bioRxiv API failed (%s/%s): %s", server, doi, exc)
            return IngestResult(
                status="failed",
                fulltext_layer="unknown",
                fulltext_source=_LAYER_DISPLAY["unknown"],
                markdown="",
                title=_title_from_url(original_url),
                original_url=original_url,
                error=f"{type(exc).__name__}: {exc}",
                note=None,
            )

        collection = data.get("collection", [])
        if not collection:
            return IngestResult(
                status="failed",
                fulltext_layer="biorxiv",
                fulltext_source=display,
                markdown="",
                title=_title_from_url(original_url),
                original_url=original_url,
                error=None,
                note="bioRxiv API 未找到論文",
            )

        paper = collection[-1]
        paper_title = str(paper.get("title", "")).strip()
        abstract = str(paper.get("abstract", "")).strip()
        markdown = (f"# {paper_title}\n\n" if paper_title else "") + abstract + "\n"

        if len(markdown) < MIN_CONTENT_CHARS:
            return IngestResult(
                status="failed",
                fulltext_layer="biorxiv",
                fulltext_source=display,
                markdown="",
                title=paper_title or _title_from_url(original_url),
                original_url=original_url,
                error=None,
                note="bioRxiv 摘要太短",
            )

        return IngestResult(
            status="ready",
            fulltext_layer="biorxiv",
            fulltext_source=display,
            markdown=markdown,
            title=paper_title or _title_from_url(original_url),
            original_url=original_url,
            error=None,
            note=None,
        )

    # ── General helpers ───────────────────────────────────────────────────────

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
        """General readability path always returns ``readability``.

        ``shared.web_scraper.scrape_url`` runs trafilatura → readability →
        firecrawl internally with no per-engine telemetry exposed. We label
        everything under the "Readability" umbrella; the ⚠️ indicator in
        ``_LAYER_DISPLAY`` signals that quality is uncertain vs OA layers.
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
