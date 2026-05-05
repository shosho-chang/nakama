"""URLDispatcher tests (Slice 1 + Slice 2, issues #352 / #353).

Scope (per PRD §Testing Decisions / "只測外部行為"):

Slice 1:
- ``dispatch(url)`` routes general URL into the readability path and produces
  a ``status='ready'`` ``IngestResult`` with the markdown intact.
- < 200-char hard block produces ``status='failed'`` + the "疑似 bot 擋頁"
  note (acceptance #4).
- Scrape exceptions become ``status='failed'`` + an ``error`` field, never
  raised to the caller (BackgroundTask must not crash on a single bad URL).
- Empty / whitespace URL raises ``ValueError``.
- Title is extracted from ``# heading`` when present, else falls back to
  netloc + path.

Slice 2:
- Academic URL pattern detection (PubMed / DOI / arXiv / bioRxiv / medRxiv /
  publisher domain) routes correctly.
- DOI → PMID reverse-lookup (mocked NCBI esearch).
- PubMed URL dispatches via configured fetch_fulltext_fn.
- arXiv / bioRxiv / medRxiv dispatch via their respective APIs (mocked).
- Without fetch_fulltext_fn configured, academic pubmed/doi/publisher URLs
  fall through to the readability path.
- OA layers (pmc / europe_pmc / unpaywall) carry ✓ in display label;
  general scrape layers carry ⚠️.
"""

from __future__ import annotations

from pathlib import Path

import httpx
import pytest

from agents.robin.url_dispatcher import (
    MIN_CONTENT_CHARS,
    URLDispatcher,
    URLDispatcherConfig,
    _detect_academic_source,
    _parse_arxiv_atom,
)
from shared.schemas.ingest_result import IngestResult


def _make(scrape_fn):
    """Construct a URLDispatcher wired only to the given scrape function.

    Slice 1 helper — Slice 2+ tests will pass full ``URLDispatcherConfig``
    with fetch_fulltext_fn / email / etc. directly.
    """
    return URLDispatcher(URLDispatcherConfig(scrape_url_fn=scrape_fn))


# ── Happy path ───────────────────────────────────────────────────────────────


def test_dispatch_general_url_routes_to_readability():
    """Slice 1: every non-academic URL goes through the readability layer."""
    big_md = "# Article Title\n\n" + ("body line.\n" * 80)
    dispatcher = _make(lambda _url: big_md)

    result = dispatcher.dispatch("https://example.com/post")

    assert isinstance(result, IngestResult)
    assert result.status == "ready"
    assert result.fulltext_layer == "readability"
    assert result.fulltext_source == "Readability ⚠️"
    assert result.markdown == big_md
    assert result.original_url == "https://example.com/post"
    assert result.error is None
    assert result.note is None


def test_dispatch_extracts_title_from_first_h1():
    big_body = "body line.\n" * 80
    md = f"# Awesome Title\n\n{big_body}"
    dispatcher = _make(lambda _url: md)

    result = dispatcher.dispatch("https://example.com/post")

    assert result.title == "Awesome Title"


def test_dispatch_falls_back_to_url_when_no_h1():
    md = "no heading just text.\n" * 80
    dispatcher = _make(lambda _url: md)

    result = dispatcher.dispatch("https://example.com/foo/bar")

    # Falls back to ``netloc + path`` (stripped of leading slash).
    assert result.title == "example.com/foo/bar"


# ── < 200-char hard block ────────────────────────────────────────────────────


def test_dispatch_short_content_marked_failed():
    """Acceptance #4: under-threshold output → status=failed + bot-blocked note."""
    short_md = "tiny"
    dispatcher = _make(lambda _url: short_md)

    result = dispatcher.dispatch("https://example.com/blocked")

    assert result.status == "failed"
    assert result.markdown == ""  # don't echo the bot-blocked chrome
    assert result.note == "抓取結果太短，疑似 bot 擋頁"
    assert result.error is None


def test_dispatch_threshold_boundary():
    """Exactly MIN_CONTENT_CHARS is accepted; one below is rejected."""
    accept_md = "x" * MIN_CONTENT_CHARS
    reject_md = "x" * (MIN_CONTENT_CHARS - 1)

    accept = _make(lambda _url: accept_md).dispatch("https://example.com/a")
    reject = _make(lambda _url: reject_md).dispatch("https://example.com/b")

    assert accept.status == "ready"
    assert reject.status == "failed"


# ── Scrape exception handling ────────────────────────────────────────────────


def test_dispatch_scraper_exception_caught():
    """A scraper raising must NOT bubble to the BackgroundTask caller."""

    def boom(_url: str) -> str:
        raise RuntimeError("connection refused")

    dispatcher = _make(boom)
    result = dispatcher.dispatch("https://unreachable.example.com/x")

    assert result.status == "failed"
    assert "RuntimeError" in (result.error or "")
    assert "connection refused" in (result.error or "")
    assert result.original_url == "https://unreachable.example.com/x"
    # Pre-route exception → ``unknown`` layer (not a misleading ``readability``
    # label that suggests a layer actually ran).
    assert result.fulltext_layer == "unknown"
    assert result.fulltext_source == "(未知)"


# ── Input validation ─────────────────────────────────────────────────────────


@pytest.mark.parametrize("bad", ["", "   ", "\n", None])
def test_dispatch_empty_url_raises(bad):
    """Empty / whitespace URLs are caller bugs — surface immediately."""
    dispatcher = _make(lambda _u: "should not be called")
    with pytest.raises((ValueError, TypeError)):
        dispatcher.dispatch(bad)


# ── Default scrape_fn lazy import ───────────────────────────────────────────


def test_default_scrape_fn_uses_shared_web_scraper(monkeypatch):
    """No-arg constructor pulls in ``shared.web_scraper.scrape_url``."""
    calls = {}

    def fake(url: str) -> str:
        calls["url"] = url
        return "# T\n\n" + ("text\n" * 80)

    # Patch the symbol the dispatcher imports lazily — patch the source module
    # binding (lazy import means the dispatcher never holds a captured ref).
    monkeypatch.setattr("shared.web_scraper.scrape_url", fake)

    dispatcher = URLDispatcher()
    result = dispatcher.dispatch("https://example.com/page")

    assert calls["url"] == "https://example.com/page"
    assert result.status == "ready"


# ── Slice 2: academic pattern detection ────────────────────────────────────


@pytest.mark.parametrize(
    "url,exp_kind,exp_id",
    [
        ("https://pubmed.ncbi.nlm.nih.gov/12345678/", "pubmed", "12345678"),
        ("https://pubmed.ncbi.nlm.nih.gov/12345678", "pubmed", "12345678"),
        ("https://doi.org/10.1016/j.cell.2024.01.001", "doi", "10.1016/j.cell.2024.01.001"),
        (
            "https://dx.doi.org/10.1038/s41586-024-00001-0",
            "doi",
            "10.1038/s41586-024-00001-0",
        ),
        ("https://arxiv.org/abs/2301.12345", "arxiv", "2301.12345"),
        ("https://arxiv.org/pdf/2301.12345v2.pdf", "arxiv", "2301.12345"),
        (
            "https://www.biorxiv.org/content/10.1101/2024.01.01.000001v1",
            "biorxiv",
            "10.1101/2024.01.01.000001",
        ),
        (
            "https://www.medrxiv.org/content/10.1101/2024.01.01.24300001v2.full",
            "medrxiv",
            "10.1101/2024.01.01.24300001",
        ),
    ],
)
def test_detect_academic_source_patterns(url, exp_kind, exp_id):
    result = _detect_academic_source(url)
    assert result is not None, f"Expected academic source for {url!r}"
    kind, ident = result
    assert kind == exp_kind
    assert ident == exp_id


@pytest.mark.parametrize(
    "url",
    [
        "https://example.com/article/123",
        "https://blog.example.com/2024/01/post",
        "https://medium.com/@author/post",
        "https://news.google.com/article/abc",
    ],
)
def test_detect_academic_source_non_academic_returns_none(url):
    assert _detect_academic_source(url) is None


def test_detect_publisher_domain_without_doi_returns_publisher():
    """bmj.com subdomain without DOI in path → ('publisher', url)."""
    url = "https://bmjmedicine.bmj.com/content/5/1/e001513"
    result = _detect_academic_source(url)
    assert result is not None
    assert result[0] == "publisher"


def test_detect_publisher_domain_with_doi_in_path_returns_doi():
    """Publisher URL that embeds a DOI in the path → ('doi', doi_str)."""
    url = "https://link.springer.com/article/10.1007/s00125-024-06100-3"
    result = _detect_academic_source(url)
    assert result is not None
    assert result[0] == "doi"
    assert result[1].startswith("10.")


# ── Slice 2: DOI → PMID lookup ───────────────────────────────────────────────


def _make_fulltext_dispatcher(tmp_path: Path, fetch_fn=None) -> URLDispatcher:
    """Helper: dispatcher wired with fetch_fulltext_fn and tmp_path attachments."""
    return URLDispatcher(
        URLDispatcherConfig(
            fetch_fulltext_fn=fetch_fn or (lambda *a, **kw: {}),
            email="test@example.com",
            fulltext_attachments_abs_dir=tmp_path,
            fulltext_vault_relative_prefix="KB/Attachments/pubmed",
        )
    )


def test_doi_to_pmid_returns_first_hit(monkeypatch, tmp_path):
    import agents.robin.url_dispatcher as mod

    def fake_get(url, *, params=None, headers=None, timeout=None, **kw):
        class R:
            def raise_for_status(self):
                pass

            def json(self):
                return {"esearchresult": {"idlist": ["39999999"]}}

        return R()

    monkeypatch.setattr(mod.httpx, "get", fake_get)
    dispatcher = _make_fulltext_dispatcher(tmp_path)
    pmid = dispatcher._doi_to_pmid("10.1016/j.cell.2024.01.001")
    assert pmid == "39999999"


def test_doi_to_pmid_no_email_returns_none():
    dispatcher = URLDispatcher(URLDispatcherConfig())
    assert dispatcher._doi_to_pmid("10.1016/j.cell.2024.01.001") is None


def test_doi_to_pmid_empty_idlist_returns_none(monkeypatch, tmp_path):
    import agents.robin.url_dispatcher as mod

    def fake_get(url, *, params=None, headers=None, timeout=None, **kw):
        class R:
            def raise_for_status(self):
                pass

            def json(self):
                return {"esearchresult": {"idlist": []}}

        return R()

    monkeypatch.setattr(mod.httpx, "get", fake_get)
    dispatcher = _make_fulltext_dispatcher(tmp_path)
    assert dispatcher._doi_to_pmid("10.9999/notinpubmed") is None


def test_doi_to_pmid_http_error_returns_none(monkeypatch, tmp_path):
    import agents.robin.url_dispatcher as mod

    def fake_get(url, *, params=None, headers=None, timeout=None, **kw):
        raise httpx.ConnectError("refused")

    monkeypatch.setattr(mod.httpx, "get", fake_get)
    dispatcher = _make_fulltext_dispatcher(tmp_path)
    assert dispatcher._doi_to_pmid("10.1016/j.cell.2024.01.001") is None


# ── Slice 2: PubMed URL dispatch via fetch_fulltext ──────────────────────────


def test_dispatch_pubmed_url_calls_fetch_fulltext(tmp_path):
    """PubMed URL extracts PMID directly and routes to fetch_fulltext."""
    md_file = tmp_path / "12345678.md"
    md_file.write_text("# BMJ Medicine Full Text\n\n" + "content.\n" * 80, encoding="utf-8")

    ft_calls: list[str] = []

    def fake_ft(pmid, *, attachments_abs_dir, vault_relative_prefix, email, ncbi_api_key=None):
        ft_calls.append(pmid)
        return {"status": "oa_html", "source": "publisher_html", "html_relpath": "...", "note": ""}

    dispatcher = _make_fulltext_dispatcher(tmp_path, fetch_fn=fake_ft)
    result = dispatcher.dispatch("https://pubmed.ncbi.nlm.nih.gov/12345678/")

    assert ft_calls == ["12345678"]
    assert result.status == "ready"
    assert result.fulltext_layer == "publisher_html"
    assert "Publisher HTML" in result.fulltext_source


def test_dispatch_pubmed_url_not_found_marks_failed(tmp_path):
    """fetch_fulltext returning not_found → status='failed' with note."""

    def fake_ft(pmid, *, attachments_abs_dir, vault_relative_prefix, email, ncbi_api_key=None):
        return {"status": "not_found", "source": None, "note": "no OA version"}

    dispatcher = _make_fulltext_dispatcher(tmp_path, fetch_fn=fake_ft)
    result = dispatcher.dispatch("https://pubmed.ncbi.nlm.nih.gov/99999999/")

    assert result.status == "failed"
    assert result.fulltext_layer == "unknown"
    assert "no OA version" in (result.note or "")


def test_dispatch_pubmed_without_fulltext_fn_falls_through_to_readability():
    """No fetch_fulltext_fn → PubMed URL falls through to general readability."""
    big_md = "# Title\n\n" + ("text\n" * 80)
    dispatcher = URLDispatcher(URLDispatcherConfig(scrape_url_fn=lambda _: big_md))
    result = dispatcher.dispatch("https://pubmed.ncbi.nlm.nih.gov/12345678/")
    assert result.status == "ready"
    assert result.fulltext_layer == "readability"


def test_dispatch_fetch_fulltext_exception_returns_failed(tmp_path):
    """fetch_fulltext raising → status='failed', exception message in error."""

    def boom(*a, **kw):
        raise RuntimeError("network timeout")

    dispatcher = _make_fulltext_dispatcher(tmp_path, fetch_fn=boom)
    result = dispatcher.dispatch("https://pubmed.ncbi.nlm.nih.gov/12345678/")

    assert result.status == "failed"
    assert "RuntimeError" in (result.error or "")


# ── Slice 2: DOI URL dispatch ─────────────────────────────────────────────────


def test_dispatch_doi_url_resolves_pmid_then_dispatches(monkeypatch, tmp_path):
    """doi.org URL → esearch PMID → fetch_fulltext."""
    import agents.robin.url_dispatcher as mod

    def fake_get(url, *, params=None, headers=None, timeout=None, **kw):
        class R:
            def raise_for_status(self):
                pass

            def json(self):
                return {"esearchresult": {"idlist": ["39999999"]}}

        return R()

    monkeypatch.setattr(mod.httpx, "get", fake_get)

    md_file = tmp_path / "39999999.md"
    md_file.write_text("# OA Title\n\n" + "content.\n" * 80, encoding="utf-8")

    def fake_ft(pmid, *, attachments_abs_dir, vault_relative_prefix, email, ncbi_api_key=None):
        return {"status": "oa_html", "source": "europe_pmc", "html_relpath": "...", "note": ""}

    dispatcher = _make_fulltext_dispatcher(tmp_path, fetch_fn=fake_ft)
    result = dispatcher.dispatch("https://doi.org/10.1136/bmjmed-001513")

    assert result.status == "ready"
    assert result.fulltext_layer == "europe_pmc"
    assert "OA from Europe PMC" in result.fulltext_source


def test_dispatch_doi_pmid_not_found_falls_through_to_readability(monkeypatch, tmp_path):
    """doi.org URL → esearch returns empty → fall through to readability."""
    import agents.robin.url_dispatcher as mod

    big_md = "# Fallback\n\n" + ("text.\n" * 80)

    def fake_get(url, *, params=None, headers=None, timeout=None, **kw):
        class R:
            def raise_for_status(self):
                pass

            def json(self):
                return {"esearchresult": {"idlist": []}}

        return R()

    monkeypatch.setattr(mod.httpx, "get", fake_get)

    dispatcher = URLDispatcher(
        URLDispatcherConfig(
            fetch_fulltext_fn=lambda *a, **kw: {},
            scrape_url_fn=lambda _: big_md,
            email="test@example.com",
            fulltext_attachments_abs_dir=tmp_path,
            fulltext_vault_relative_prefix="KB/Attachments/pubmed",
        )
    )
    result = dispatcher.dispatch("https://doi.org/10.9999/not-in-pubmed")

    assert result.status == "ready"
    assert result.fulltext_layer == "readability"


# ── Slice 2: arXiv dispatch ───────────────────────────────────────────────────

_ARXIV_ATOM_SAMPLE = """\
<?xml version="1.0" encoding="UTF-8"?>
<feed xmlns="http://www.w3.org/2005/Atom">
  <title type="html">ArXiv Query Interface</title>
  <entry>
    <id>http://arxiv.org/abs/2301.12345v1</id>
    <title>Deep Learning for Health Outcomes</title>
    <summary>  We present a comprehensive study of deep learning
approaches for predicting health outcomes from clinical records.
Our method achieves state-of-the-art performance on multiple benchmarks.
The proposed architecture combines attention mechanisms with novel
regularization techniques suitable for high-dimensional clinical data.
We validate on three large cohort studies with over 100,000 patients.
    </summary>
  </entry>
</feed>"""


def test_dispatch_arxiv_url_routes_to_arxiv_api(monkeypatch):
    import agents.robin.url_dispatcher as mod

    def fake_get(url, *, params=None, timeout=None, **kw):
        class R:
            text = _ARXIV_ATOM_SAMPLE

            def raise_for_status(self):
                pass

        return R()

    monkeypatch.setattr(mod.httpx, "get", fake_get)
    result = URLDispatcher(URLDispatcherConfig()).dispatch("https://arxiv.org/abs/2301.12345")

    assert result.status == "ready"
    assert result.fulltext_layer == "arxiv"
    assert "arXiv" in result.fulltext_source
    assert "Deep Learning" in result.title
    assert "deep learning" in result.markdown.lower()


def test_dispatch_arxiv_api_failure_returns_failed(monkeypatch):
    import agents.robin.url_dispatcher as mod

    def fake_get(url, *, params=None, timeout=None, **kw):
        raise httpx.ConnectError("timeout")

    monkeypatch.setattr(mod.httpx, "get", fake_get)
    result = URLDispatcher(URLDispatcherConfig()).dispatch("https://arxiv.org/abs/0000.00000")

    assert result.status == "failed"


def test_dispatch_arxiv_pdf_url_detects_id(monkeypatch):
    """arxiv.org/pdf/{id}v2.pdf → same routing as abs URL."""
    import agents.robin.url_dispatcher as mod

    def fake_get(url, *, params=None, timeout=None, **kw):
        class R:
            text = _ARXIV_ATOM_SAMPLE

            def raise_for_status(self):
                pass

        return R()

    monkeypatch.setattr(mod.httpx, "get", fake_get)
    result = URLDispatcher(URLDispatcherConfig()).dispatch("https://arxiv.org/pdf/2301.12345v2.pdf")

    assert result.status == "ready"
    assert result.fulltext_layer == "arxiv"


# ── Slice 2: bioRxiv / medRxiv dispatch ──────────────────────────────────────

_BIORXIV_JSON_SAMPLE = {
    "messages": [{"status": "ok"}],
    "collection": [
        {
            "doi": "10.1101/2024.01.01.000001",
            "title": "Exercise Impacts on Sleep Quality",
            "abstract": (
                "Physical exercise has been widely studied for its effects on human health. "
                "In this study we investigate the relationship between exercise intensity and "
                "sleep quality using actigraphy in a randomized controlled trial of 500 "
                "participants over 12 weeks. Results show significant improvements in sleep "
                "onset latency and total sleep time with moderate-intensity aerobic exercise."
            ),
            "date": "2024-01-01",
        }
    ],
}


def test_dispatch_biorxiv_url_routes_to_biorxiv_api(monkeypatch):
    import agents.robin.url_dispatcher as mod

    def fake_get(url, *, timeout=None, **kw):
        class R:
            def raise_for_status(self):
                pass

            def json(self):
                return _BIORXIV_JSON_SAMPLE

        return R()

    monkeypatch.setattr(mod.httpx, "get", fake_get)
    result = URLDispatcher(URLDispatcherConfig()).dispatch(
        "https://www.biorxiv.org/content/10.1101/2024.01.01.000001v1"
    )

    assert result.status == "ready"
    assert result.fulltext_layer == "biorxiv"
    assert "bioRxiv" in result.fulltext_source
    assert "Exercise" in result.title


def test_dispatch_medrxiv_url_uses_biorxiv_layer(monkeypatch):
    """medRxiv uses biorxiv layer but medrxiv display label."""
    import agents.robin.url_dispatcher as mod

    def fake_get(url, *, timeout=None, **kw):
        class R:
            def raise_for_status(self):
                pass

            def json(self):
                return _BIORXIV_JSON_SAMPLE

        return R()

    monkeypatch.setattr(mod.httpx, "get", fake_get)
    result = URLDispatcher(URLDispatcherConfig()).dispatch(
        "https://www.medrxiv.org/content/10.1101/2024.01.01.24300001v1"
    )

    assert result.status == "ready"
    assert result.fulltext_layer == "biorxiv"
    assert "medRxiv" in result.fulltext_source


def test_dispatch_biorxiv_api_failure_returns_failed(monkeypatch):
    import agents.robin.url_dispatcher as mod

    def fake_get(url, *, timeout=None, **kw):
        raise httpx.ConnectError("refused")

    monkeypatch.setattr(mod.httpx, "get", fake_get)
    result = URLDispatcher(URLDispatcherConfig()).dispatch(
        "https://www.biorxiv.org/content/10.1101/2024.01.01.000001v1"
    )

    assert result.status == "failed"


# ── Slice 2: oa_html read from disk ──────────────────────────────────────────


def test_dispatch_fulltext_oa_html_reads_md_file(tmp_path):
    """oa_html result: dispatcher reads {pmid}.md from attachments_abs_dir."""
    md_file = tmp_path / "12345.md"
    md_content = "# Europe PMC Full Text\n\n" + "paragraph.\n" * 80
    md_file.write_text(md_content, encoding="utf-8")

    def fake_ft(pmid, *, attachments_abs_dir, vault_relative_prefix, email, ncbi_api_key=None):
        return {
            "status": "oa_html",
            "source": "europe_pmc",
            "html_relpath": f"KB/Attachments/pubmed/{pmid}.md",
            "note": "",
        }

    dispatcher = _make_fulltext_dispatcher(tmp_path, fetch_fn=fake_ft)
    result = dispatcher.dispatch("https://pubmed.ncbi.nlm.nih.gov/12345/")

    assert result.status == "ready"
    assert result.markdown == md_content
    assert result.fulltext_layer == "europe_pmc"
    assert "OA from Europe PMC" in result.fulltext_source


def test_dispatch_fulltext_oa_html_missing_md_marks_failed(tmp_path):
    """oa_html but .md file not written → status='failed'."""

    def fake_ft(pmid, *, attachments_abs_dir, vault_relative_prefix, email, ncbi_api_key=None):
        return {"status": "oa_html", "source": "europe_pmc", "html_relpath": "...", "note": ""}

    dispatcher = _make_fulltext_dispatcher(tmp_path, fetch_fn=fake_ft)
    result = dispatcher.dispatch("https://pubmed.ncbi.nlm.nih.gov/99999/")

    assert result.status == "failed"


# ── Slice 2: display label quality signals ───────────────────────────────────


def test_readability_layer_has_warning_indicator():
    """General scrape path label carries ⚠️ (uncertain quality)."""
    big_md = "# T\n\n" + ("text\n" * 80)
    result = URLDispatcher(URLDispatcherConfig(scrape_url_fn=lambda _: big_md)).dispatch(
        "https://example.com/post"
    )
    assert "⚠️" in result.fulltext_source


def test_oa_layers_have_checkmark_indicator():
    """OA layer display labels carry ✓."""
    import agents.robin.url_dispatcher as _mod

    assert "✓" in _mod._LAYER_DISPLAY["pmc"]
    assert "✓" in _mod._LAYER_DISPLAY["europe_pmc"]
    assert "✓" in _mod._LAYER_DISPLAY["unpaywall"]


# ── _parse_arxiv_atom ─────────────────────────────────────────────────────────


def test_parse_arxiv_atom_extracts_title_and_summary():
    title, abstract = _parse_arxiv_atom(_ARXIV_ATOM_SAMPLE)
    assert title == "Deep Learning for Health Outcomes"
    assert "deep learning" in abstract.lower()


def test_parse_arxiv_atom_empty_xml_returns_empty_strings():
    assert _parse_arxiv_atom("<feed></feed>") == ("", "")


# ── Bug #(Lancet repro): publisher DOI extract failure → Firecrawl fallback ──
#
# Reproduced live 2026-05-04 against
#   https://www.thelancet.com/journals/eclinm/article/PIIS2589-5370(25)00676-5/fulltext
# Before fix: httpx with Nakama-Robin UA was bot-blocked → silent DEBUG log →
# fall through to readability → user got page chrome ("Skip to Main Content"
# / "ADVERTISEMENT") labelled "Readability ⚠️" but flagged as ready.
#
# After fix: httpx failure is WARN, Firecrawl raw-HTML fallback is attempted,
# the meta-tag is re-parsed from Firecrawl's anti-bot HTML, and the DOI
# resolves correctly.

_LANCET_FULL_HTML = (
    "<html><head>"
    '<meta name="citation_title" content="Some article">'
    '<meta name="citation_doi" content="10.1016/j.eclinm.2025.103193">'
    "</head><body>article body</body></html>"
)


def _publisher_dispatcher() -> URLDispatcher:
    """Dispatcher with email set so _extract_doi_from_html builds a UA."""
    return URLDispatcher(URLDispatcherConfig(email="test@example.com"))


def test_extract_doi_httpx_connect_error_warns_and_falls_back_to_firecrawl(monkeypatch, caplog):
    """httpx ConnectError → WARN logged + Firecrawl fallback attempted + DOI extracted."""
    import logging

    import agents.robin.url_dispatcher as mod

    def boom_get(url, *, headers=None, timeout=None, follow_redirects=None, **kw):
        raise httpx.ConnectError("blocked by cloudflare")

    fc_calls: list[str] = []

    def fake_firecrawl(url):
        fc_calls.append(url)
        return _LANCET_FULL_HTML

    monkeypatch.setattr(mod.httpx, "get", boom_get)
    monkeypatch.setattr("shared.web_scraper.fetch_html_via_firecrawl", fake_firecrawl)

    dispatcher = _publisher_dispatcher()
    with caplog.at_level(logging.WARNING, logger="nakama.robin.url_dispatcher"):
        doi = dispatcher._extract_doi_from_html(
            "https://www.thelancet.com/journals/eclinm/article/PIIS2589-5370(25)00676-5/fulltext"
        )

    assert doi == "10.1016/j.eclinm.2025.103193"
    assert fc_calls == [
        "https://www.thelancet.com/journals/eclinm/article/PIIS2589-5370(25)00676-5/fulltext"
    ]
    # WARN should mention the httpx failure (not silent DEBUG anymore).
    warning_text = " ".join(rec.message for rec in caplog.records if rec.levelname == "WARNING")
    assert "httpx" in warning_text.lower() or "publisher" in warning_text.lower()


def test_extract_doi_httpx_returns_chrome_only_html_falls_back_to_firecrawl(monkeypatch, caplog):
    """httpx 200 but no citation_doi tag → WARN logged + Firecrawl fallback used."""
    import logging

    import agents.robin.url_dispatcher as mod

    chrome_only = (
        "<html><head><title>Skip to Main Content</title></head>"
        "<body><div>ADVERTISEMENT</div></body></html>"
    )

    def fake_get(url, *, headers=None, timeout=None, follow_redirects=None, **kw):
        class R:
            text = chrome_only

            def raise_for_status(self):
                pass

        return R()

    fc_calls: list[str] = []

    def fake_firecrawl(url):
        fc_calls.append(url)
        return _LANCET_FULL_HTML

    monkeypatch.setattr(mod.httpx, "get", fake_get)
    monkeypatch.setattr("shared.web_scraper.fetch_html_via_firecrawl", fake_firecrawl)

    dispatcher = _publisher_dispatcher()
    with caplog.at_level(logging.WARNING, logger="nakama.robin.url_dispatcher"):
        doi = dispatcher._extract_doi_from_html(
            "https://www.thelancet.com/journals/eclinm/article/PIIS2589-5370(25)00676-5/fulltext"
        )

    assert doi == "10.1016/j.eclinm.2025.103193"
    assert len(fc_calls) == 1
    warning_text = " ".join(rec.message for rec in caplog.records if rec.levelname == "WARNING")
    assert "absent" in warning_text.lower() or "meta" in warning_text.lower()


def test_extract_doi_firecrawl_html_parsed_correctly(monkeypatch):
    """Firecrawl-returned HTML with citation_doi meta tag → DOI extracted correctly."""
    import agents.robin.url_dispatcher as mod

    def boom_get(url, **kw):
        raise httpx.ConnectError("blocked")

    monkeypatch.setattr(mod.httpx, "get", boom_get)
    monkeypatch.setattr(
        "shared.web_scraper.fetch_html_via_firecrawl",
        lambda _url: _LANCET_FULL_HTML,
    )

    dispatcher = _publisher_dispatcher()
    doi = dispatcher._extract_doi_from_html("https://www.thelancet.com/article/x")

    assert doi == "10.1016/j.eclinm.2025.103193"


def test_extract_doi_both_httpx_and_firecrawl_fail_returns_none_with_warn(monkeypatch, caplog):
    """Both layers fail → WARN logged, returns None (preserves fall-through)."""
    import logging

    import agents.robin.url_dispatcher as mod

    def boom_get(url, **kw):
        raise httpx.ConnectError("blocked")

    def boom_firecrawl(url):
        raise RuntimeError("FIRECRAWL_API_KEY 未設定")

    monkeypatch.setattr(mod.httpx, "get", boom_get)
    monkeypatch.setattr("shared.web_scraper.fetch_html_via_firecrawl", boom_firecrawl)

    dispatcher = _publisher_dispatcher()
    with caplog.at_level(logging.WARNING, logger="nakama.robin.url_dispatcher"):
        doi = dispatcher._extract_doi_from_html("https://www.thelancet.com/article/x")

    assert doi is None
    warnings = [rec for rec in caplog.records if rec.levelname == "WARNING"]
    assert len(warnings) >= 2  # httpx warn + firecrawl warn
    combined = " ".join(rec.message for rec in warnings).lower()
    assert "firecrawl" in combined


# ── Slice 1 #389 — Zotero URI dispatch ──────────────────────────────────────


_ZOTERO_HTML_FIXTURE = """\
<!DOCTYPE html>
<html>
<head><title>Zotero Test Paper</title></head>
<body>
  <article>
    <h1>Zotero Test Paper</h1>
    <p>This is a sufficient body of text to clear Trafilatura's minimum
    extraction heuristic so the pipeline returns clean markdown rather than
    None. Padding sentences ensure we cross the 200-character threshold the
    dispatcher enforces against bot-blocked pages, even after Trafilatura's
    own filtering. Each paragraph keeps adding mass.</p>
    <p>The second paragraph extends with biological context. Cellular
    respiration, oxidative phosphorylation, mitochondrial dynamics — the
    point is just to push body length past Trafilatura's lower bound and
    the dispatcher's MIN_CONTENT_CHARS threshold.</p>
  </article>
</body>
</html>
"""


def test_dispatch_zotero_uri_routes_to_sync_and_returns_ready(tmp_path: Path):
    """zotero:// URI is dispatched into ``zotero_sync.sync_zotero_item``."""
    from tests.agents.robin._zotero_fixture import (
        add_html_snapshot,
        add_journal_article,
        init_zotero_lib,
    )

    fixture = init_zotero_lib(tmp_path / "Zotero")
    parent_id = add_journal_article(fixture, item_key="ZOTERO12", title="Zotero Test Paper")
    add_html_snapshot(
        fixture,
        parent_item_id=parent_id,
        attachment_key="HTML0001",
        body=_ZOTERO_HTML_FIXTURE,
    )

    config = URLDispatcherConfig(
        zotero_root=fixture.zotero_root,
        vault_root=tmp_path / "vault",
    )
    dispatcher = URLDispatcher(config)

    result = dispatcher.dispatch("zotero://select/library/items/ZOTERO12")

    assert result.status == "ready"
    assert result.fulltext_layer == "zotero_html_snapshot"
    assert result.zotero_item_key == "ZOTERO12"
    assert result.attachment_type == "text/html"
    assert "Zotero Test Paper" in result.title


def test_dispatch_zotero_uri_without_config_returns_failed():
    """zotero:// URI with no zotero_root configured → failed with helpful note."""
    dispatcher = URLDispatcher()  # default config — no Zotero paths

    result = dispatcher.dispatch("zotero://select/library/items/ABC12345")

    assert result.status == "failed"
    assert result.fulltext_layer == "unknown"
    assert "Zotero 未配置" in (result.note or "")


def test_dispatch_zotero_uri_unknown_item_returns_failed(tmp_path: Path):
    """Item key not in Zotero library → failed (don't propagate KeyError)."""
    from tests.agents.robin._zotero_fixture import init_zotero_lib

    fixture = init_zotero_lib(tmp_path / "Zotero")
    config = URLDispatcherConfig(
        zotero_root=fixture.zotero_root,
        vault_root=tmp_path / "vault",
    )
    dispatcher = URLDispatcher(config)

    result = dispatcher.dispatch("zotero://select/library/items/MISSING1")

    assert result.status == "failed"
    assert "找不到" in (result.note or "")


def test_dispatch_zotero_uri_no_attachment_returns_failed(tmp_path: Path):
    """Item exists but has no HTML/PDF attachment → failed (placeholder note)."""
    from tests.agents.robin._zotero_fixture import (
        add_journal_article,
        init_zotero_lib,
    )

    fixture = init_zotero_lib(tmp_path / "Zotero")
    add_journal_article(fixture, item_key="ZOTERO12", title="Citation only")

    config = URLDispatcherConfig(
        zotero_root=fixture.zotero_root,
        vault_root=tmp_path / "vault",
    )
    dispatcher = URLDispatcher(config)

    result = dispatcher.dispatch("zotero://select/library/items/ZOTERO12")

    assert result.status == "failed"
    assert "attachment" in (result.note or "").lower() or "snapshot" in (result.note or "")


def test_dispatch_non_zotero_url_falls_through_to_default_path():
    """https:// URLs are unaffected by the Zotero dispatch branch."""
    big_md = "# Real Article\n\n" + ("body line.\n" * 80)
    config = URLDispatcherConfig(scrape_url_fn=lambda _u: big_md)
    dispatcher = URLDispatcher(config)

    result = dispatcher.dispatch("https://example.com/article")

    assert result.status == "ready"
    assert result.fulltext_layer == "readability"
    assert result.zotero_item_key is None
