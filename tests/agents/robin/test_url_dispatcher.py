"""URLDispatcher tests (Slice 1, issue #352).

Scope (per PRD §Testing Decisions / "只測外部行為"):

- ``dispatch(url)`` routes general URL into the readability path and produces
  a ``status='ready'`` ``IngestResult`` with the markdown intact.
- < 200-char hard block produces ``status='failed'`` + the "疑似 bot 擋頁"
  note (acceptance #4).
- Scrape exceptions become ``status='failed'`` + an ``error`` field, never
  raised to the caller (BackgroundTask must not crash on a single bad URL).
- Empty / whitespace URL raises ``ValueError``.
- Title is extracted from ``# heading`` when present, else falls back to
  netloc + path.

Slice 2 will add academic-source path tests (PMC / Europe PMC / Unpaywall /
publisher HTML / arXiv / bioRxiv) when the dispatcher gains URL pattern
detection — those tests will live here once the layers are wired.
"""

from __future__ import annotations

import pytest

from agents.robin.url_dispatcher import MIN_CONTENT_CHARS, URLDispatcher, URLDispatcherConfig
from shared.schemas.ingest_result import IngestResult


def _make(scrape_fn):
    """Construct a URLDispatcher wired only to the given scrape function.

    Slice 1 helper — Slice 2+ tests will pass full ``URLDispatcherConfig``
    with fetch_fulltext_fn / email / etc. directly.
    """
    return URLDispatcher(URLDispatcherConfig(scrape_url_fn=scrape_fn))


# ── Happy path ───────────────────────────────────────────────────────────────


def test_dispatch_general_url_routes_to_readability():
    """Slice 1: every URL goes through the readability layer."""
    big_md = "# Article Title\n\n" + ("body line.\n" * 80)
    dispatcher = _make(lambda _url: big_md)

    result = dispatcher.dispatch("https://example.com/post")

    assert isinstance(result, IngestResult)
    assert result.status == "ready"
    assert result.fulltext_layer == "readability"
    assert result.fulltext_source == "Readability"
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
