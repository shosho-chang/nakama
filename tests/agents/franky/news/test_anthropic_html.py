"""Tests for agents/franky/news/anthropic_html.py — Anthropic /news scraper."""

from __future__ import annotations

from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from agents.franky.news import anthropic_html as ah

# Minimal HTML fixture mirroring the live anthropic.com/news structure observed
# 2026-04-26: <a href="/news/SLUG"> wrapping <h2>, <time>, <p>. Class names
# are hashed CSS Modules — parser must rely on tag/structure, not class.
FIXTURE_HTML = """
<html>
<body>
  <main>
    <a href="/news/claude-opus-4-7" class="FeaturedGrid-module-scss-module__W1FydW__content">
      <h2 class="headline-4">Introducing Claude Opus 4.7</h2>
      <div class="meta">
        <span class="caption bold">Product</span>
        <time class="caption bold">Apr 16, 2026</time>
      </div>
      <p class="body-3 serif">Our latest Opus model brings stronger performance.</p>
    </a>
    <a href="/news/claude-design" class="grid-content">
      <h2>Claude Design Anthropic Labs</h2>
      <div>
        <span>Announcement</span>
        <time>Apr 17, 2026</time>
      </div>
      <p>A new design system for Claude apps.</p>
    </a>
    <a href="/news/old-news" class="grid-content">
      <h2>Old News Item</h2>
      <div><time>Mar 1, 2026</time></div>
      <p>Stale news from over a month ago.</p>
    </a>
    <a href="/news/missing-time" class="grid-content">
      <h2>Article Without Time</h2>
      <p>No time element in this anchor.</p>
    </a>
    <a href="/news/no-title" class="grid-content">
      <span>short</span>
    </a>
    <a href="/about">Not a /news/ link</a>
    <a href="/news/claude-opus-4-7" class="duplicate-card">
      <h2>Introducing Claude Opus 4.7</h2>
      <time>Apr 16, 2026</time>
    </a>
  </main>
</body>
</html>
"""


@pytest.fixture(autouse=True)
def _no_dedupe(monkeypatch):
    """Default: nothing is_seen, so cutoff/age tests don't accidentally filter."""
    monkeypatch.setattr(ah, "is_seen", lambda src, item_id: False)


# ---------------------------------------------------------------------------
# _parse_articles
# ---------------------------------------------------------------------------


def test_parse_articles_extracts_basic_fields():
    parsed = ah._parse_articles(FIXTURE_HTML)
    titles = [p["title"] for p in parsed]
    assert "Introducing Claude Opus 4.7" in titles
    assert "Claude Design Anthropic Labs" in titles


def test_parse_articles_skips_anchors_without_extractable_title():
    """An anchor with no heading and only short spans yields no title → drop."""
    parsed = ah._parse_articles(FIXTURE_HTML)
    slugs = [p["slug"] for p in parsed]
    assert "no-title" not in slugs


def test_parse_articles_keeps_anchors_without_time_with_zero_ts():
    """Articles missing <time> still emit; cutoff path will drop them when ts=0."""
    parsed = ah._parse_articles(FIXTURE_HTML)
    no_time = [p for p in parsed if p["slug"] == "missing-time"]
    assert len(no_time) == 1
    assert no_time[0]["published_ts"] == 0.0


def test_parse_articles_skips_non_news_hrefs():
    parsed = ah._parse_articles(FIXTURE_HTML)
    slugs = [p["slug"] for p in parsed]
    assert "about" not in slugs
    # And no slug from /about ever leaks through
    for p in parsed:
        assert "/" not in p["slug"]


def test_parse_articles_resolves_visible_date_to_utc():
    parsed = ah._parse_articles(FIXTURE_HTML)
    apr16 = next(p for p in parsed if p["slug"] == "claude-opus-4-7")
    # "Apr 16, 2026" → 2026-04-16 00:00 UTC
    expected = datetime(2026, 4, 16, tzinfo=timezone.utc).timestamp()
    assert apr16["published_ts"] == expected


def test_parse_articles_emits_duplicate_anchors_at_parse_layer():
    """gather_candidates dedupes by slug; _parse_articles itself does not."""
    parsed = ah._parse_articles(FIXTURE_HTML)
    opus_count = sum(1 for p in parsed if p["slug"] == "claude-opus-4-7")
    assert opus_count == 2


def test_parse_articles_handles_publication_list_style():
    """PublicationList row has no heading tag — title lives in a <span>.

    Live anthropic.com/news shows three card layouts; missing this one means
    only the hero card extracts, dropping ~12 of 13 articles (regression risk
    found 2026-04-26 — see _extract_title docstring)."""
    pubs_html = """
    <ul>
      <li>
        <a href="/news/election-safeguards">
          <div>
            <time>Apr 24, 2026</time>
            <span>Announcements</span>
          </div>
          <span class="hashed_title body-3">An update on our election safeguards</span>
        </a>
      </li>
      <li>
        <a href="/news/h4-side-card">
          <div><time>Apr 17, 2026</time></div>
          <h4>Sidebar Card With H4</h4>
          <p>Body text.</p>
        </a>
      </li>
    </ul>
    """
    parsed = ah._parse_articles(pubs_html)
    titles = {p["slug"]: p["title"] for p in parsed}
    assert titles["election-safeguards"] == "An update on our election safeguards"
    assert titles["h4-side-card"] == "Sidebar Card With H4"


def test_parse_articles_skips_publication_list_when_no_substantive_span():
    """If only short category labels exist, no title is extractable — drop it."""
    short_only_html = """
    <a href="/news/no-title-here">
      <time>Apr 24, 2026</time>
      <span>Product</span>
    </a>
    """
    parsed = ah._parse_articles(short_only_html)
    assert parsed == []


# ---------------------------------------------------------------------------
# gather_candidates
# ---------------------------------------------------------------------------


def test_gather_filters_old_articles():
    now = datetime(2026, 4, 17, 12, 0, tzinfo=timezone.utc)
    cands = ah.gather_candidates(now=now, html_override=FIXTURE_HTML)
    slugs = [c["item_id"] for c in cands]
    # "Apr 17" is fresh (~12h), "Apr 16" is fresh (~36h... wait, max_age_hours
    # default 24 → drops Apr 16). So only Apr 17 stays.
    assert "anthropic-news-claude-design" in slugs
    assert "anthropic-news-claude-opus-4-7" not in slugs
    assert "anthropic-news-old-news" not in slugs


def test_gather_dedupes_duplicate_slugs():
    """Duplicate anchor occurrences for the same slug must not produce duplicate candidates."""
    now = datetime(2026, 4, 17, 12, 0, tzinfo=timezone.utc)
    # Loosen window to keep Opus too — both Apr 16 + Apr 17 fall inside 48h.
    cands = ah.gather_candidates(now=now, max_age_hours=48, html_override=FIXTURE_HTML)
    item_ids = [c["item_id"] for c in cands]
    assert len(item_ids) == len(set(item_ids))
    assert "anthropic-news-claude-opus-4-7" in item_ids


def test_gather_skips_seen_when_skip_seen_true(monkeypatch):
    now = datetime(2026, 4, 17, 12, 0, tzinfo=timezone.utc)
    monkeypatch.setattr(
        ah, "is_seen", lambda src, item_id: item_id == "anthropic-news-claude-design"
    )
    cands = ah.gather_candidates(
        now=now, max_age_hours=48, html_override=FIXTURE_HTML, skip_seen=True
    )
    item_ids = [c["item_id"] for c in cands]
    assert "anthropic-news-claude-design" not in item_ids


def test_gather_skip_seen_false_keeps_all(monkeypatch):
    now = datetime(2026, 4, 17, 12, 0, tzinfo=timezone.utc)
    monkeypatch.setattr(ah, "is_seen", lambda src, item_id: True)  # all seen
    cands = ah.gather_candidates(
        now=now, max_age_hours=48, html_override=FIXTURE_HTML, skip_seen=False
    )
    assert len(cands) >= 2  # Apr 16 + Apr 17 within 48h window


def test_gather_sorts_newest_first():
    now = datetime(2026, 4, 18, 0, 0, tzinfo=timezone.utc)
    cands = ah.gather_candidates(now=now, max_age_hours=72, html_override=FIXTURE_HTML)
    timestamps = [c["published_ts"] for c in cands]
    assert timestamps == sorted(timestamps, reverse=True)


def test_gather_keeps_articles_with_zero_ts_through_cutoff():
    """ts=0 (date parse failure) bypasses cutoff — same as official_blogs.
    Reasoning: a parse failure shouldn't silently drop a fresh post."""
    now = datetime(2026, 4, 17, 12, 0, tzinfo=timezone.utc)
    cands = ah.gather_candidates(now=now, html_override=FIXTURE_HTML)
    missing = [c for c in cands if c["item_id"] == "anthropic-news-missing-time"]
    assert len(missing) == 1
    assert missing[0]["published_ts"] == 0.0
    assert missing[0]["age_hours"] == 0.0


def test_gather_returns_candidate_schema_compatible_with_official_blogs():
    """Candidate dict keys must match official_blogs candidate schema for merging."""
    now = datetime(2026, 4, 17, 12, 0, tzinfo=timezone.utc)
    cands = ah.gather_candidates(now=now, html_override=FIXTURE_HTML)
    assert cands, "fixture should yield at least one candidate"
    cand = cands[0]
    expected_keys = {
        "item_id",
        "title",
        "publisher",
        "feed_name",
        "url",
        "summary",
        "published",
        "published_ts",
        "age_hours",
    }
    assert set(cand.keys()) == expected_keys
    assert cand["publisher"] == "Anthropic"
    assert cand["feed_name"] == "anthropic_news_html"
    assert cand["url"].startswith("https://www.anthropic.com/news/")


def test_gather_age_hours_computed_when_published_ts_present():
    now = datetime(2026, 4, 17, 12, 0, tzinfo=timezone.utc)
    cands = ah.gather_candidates(now=now, html_override=FIXTURE_HTML)
    design = next(c for c in cands if c["item_id"] == "anthropic-news-claude-design")
    # Apr 17 00:00 UTC → 12h before Apr 17 12:00
    assert 11.5 <= design["age_hours"] <= 12.5


def test_gather_caps_summary_length():
    long_html = (
        '<a href="/news/long"><h2>Long</h2><time>Apr 17, 2026</time><p>' + ("x" * 5000) + "</p></a>"
    )
    now = datetime(2026, 4, 17, 12, 0, tzinfo=timezone.utc)
    cands = ah.gather_candidates(now=now, html_override=long_html)
    assert cands[0]["summary"] == "x" * 1500


def test_gather_returns_empty_when_no_html():
    """Fetch failure path: html_override="" simulates _fetch_html returning None."""
    now = datetime(2026, 4, 17, 12, 0, tzinfo=timezone.utc)
    # Empty string is falsy → same path as None
    cands = ah.gather_candidates(now=now, html_override="")
    assert cands == []


# ---------------------------------------------------------------------------
# _fetch_html network behavior
# ---------------------------------------------------------------------------


def test_fetch_html_returns_none_on_http_error(monkeypatch):
    fake_resp = MagicMock()
    fake_resp.raise_for_status.side_effect = RuntimeError("503")
    monkeypatch.setattr(ah.httpx, "get", lambda *a, **kw: fake_resp)
    assert ah._fetch_html() is None


def test_fetch_html_returns_text_on_success(monkeypatch):
    fake_resp = SimpleNamespace(
        text="<html>OK</html>",
        raise_for_status=lambda: None,
    )
    monkeypatch.setattr(ah.httpx, "get", lambda *a, **kw: fake_resp)
    assert ah._fetch_html() == "<html>OK</html>"


def test_fetch_html_swallows_network_exceptions(monkeypatch):
    def _raise(*a, **kw):
        raise OSError("network down")

    monkeypatch.setattr(ah.httpx, "get", _raise)
    assert ah._fetch_html() is None
