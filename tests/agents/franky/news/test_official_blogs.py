"""Tests for agents/franky/news/official_blogs.py — RSS source layer."""

from __future__ import annotations

import calendar
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import MagicMock  # noqa: F401  (used by future smoke tests)

from agents.franky.news import official_blogs as ob


def _make_entry(
    *,
    title: str = "Claude 4.7 release",
    link: str = "https://www.anthropic.com/news/claude-4-7",
    entry_id: str | None = "https://www.anthropic.com/news/claude-4-7",
    summary: str = "<p>1M context now <strong>available</strong>.</p>",
    published_dt: datetime | None = None,
):
    """Build a feedparser-like entry SimpleNamespace."""
    if published_dt is None:
        published_dt = datetime.now(timezone.utc)
    parsed_struct = published_dt.utctimetuple()
    return SimpleNamespace(
        title=title,
        link=link,
        id=entry_id,
        summary=summary,
        published=published_dt.isoformat(),
        published_parsed=parsed_struct,
    )


# ---------- load_feeds ----------------------------------------------------------


def test_load_feeds_happy(tmp_path):
    cfg = tmp_path / "ai_news.yaml"
    cfg.write_text(
        """\
feeds:
  - name: anthropic_news
    url: https://www.anthropic.com/news/rss.xml
    publisher: Anthropic
  - name: openai_news
    url: https://openai.com/news/rss.xml
    publisher: OpenAI
""",
        encoding="utf-8",
    )
    feeds = ob.load_feeds(cfg)
    assert len(feeds) == 2
    assert feeds[0].name == "anthropic_news"
    assert feeds[0].publisher == "Anthropic"


def test_load_feeds_missing_file_returns_empty(tmp_path):
    feeds = ob.load_feeds(tmp_path / "nonexistent.yaml")
    assert feeds == []


def test_load_feeds_skips_malformed(tmp_path):
    cfg = tmp_path / "bad.yaml"
    cfg.write_text(
        """\
feeds:
  - name: ok
    url: https://example.com/feed
    publisher: OK
  - name: ""
    url: https://no-name.example.com/feed
  - name: no_url
    url: ""
    publisher: NoURL
""",
        encoding="utf-8",
    )
    feeds = ob.load_feeds(cfg)
    assert len(feeds) == 1
    assert feeds[0].name == "ok"


def test_load_feeds_publisher_defaults_to_name(tmp_path):
    cfg = tmp_path / "no_pub.yaml"
    cfg.write_text(
        """\
feeds:
  - name: solo
    url: https://example.com/feed
""",
        encoding="utf-8",
    )
    feeds = ob.load_feeds(cfg)
    assert feeds[0].publisher == "solo"


# ---------- _entry_to_candidate ------------------------------------------------


def test_entry_to_candidate_strips_html_and_decodes_entities():
    feed = ob.FeedConfig(name="x", url="https://example.com/feed", publisher="X")
    entry = _make_entry(summary="<p>Hello&nbsp;<em>world</em> &amp; you</p>")
    cand = ob._entry_to_candidate(entry, feed)
    assert cand is not None
    assert "Hello" in cand["summary"]
    assert "<p>" not in cand["summary"]
    assert "&amp;" not in cand["summary"]


def test_entry_to_candidate_uses_calendar_timegm_for_utc():
    """Regression: mktime would interpret struct_time as local TZ — wrong."""
    feed = ob.FeedConfig(name="x", url="https://example.com/feed", publisher="X")
    fixed = datetime(2026, 4, 26, 12, 0, 0, tzinfo=timezone.utc)
    entry = _make_entry(published_dt=fixed)
    cand = ob._entry_to_candidate(entry, feed)
    expected_ts = float(calendar.timegm(fixed.utctimetuple()))
    assert cand["published_ts"] == expected_ts


def test_entry_to_candidate_falls_back_to_sha256_when_no_id():
    feed = ob.FeedConfig(name="x", url="https://example.com/feed", publisher="X")
    entry = _make_entry(entry_id=None)
    # SimpleNamespace.id default is missing — getattr fallback path
    delattr(entry, "id")
    cand = ob._entry_to_candidate(entry, feed)
    assert cand is not None
    assert len(cand["item_id"]) == 16  # sha256[:16]


def test_entry_to_candidate_skips_when_title_or_link_missing():
    feed = ob.FeedConfig(name="x", url="https://example.com/feed", publisher="X")
    entry = _make_entry(title="")
    assert ob._entry_to_candidate(entry, feed) is None
    entry = _make_entry(link="")
    assert ob._entry_to_candidate(entry, feed) is None


def test_entry_to_candidate_caps_summary_length():
    feed = ob.FeedConfig(name="x", url="https://example.com/feed", publisher="X")
    entry = _make_entry(summary="x" * 5000)
    cand = ob._entry_to_candidate(entry, feed)
    assert len(cand["summary"]) == 1500


# ---------- gather_candidates --------------------------------------------------


def test_gather_filters_old_entries(monkeypatch):
    """24h cutoff drops entries older than cutoff."""
    feed = ob.FeedConfig(name="x", url="https://example.com/feed", publisher="X")
    now = datetime(2026, 4, 26, 12, 0, 0, tzinfo=timezone.utc)
    fresh = _make_entry(
        link="https://example.com/fresh",
        entry_id="fresh",
        published_dt=now,
    )
    stale = _make_entry(
        link="https://example.com/stale",
        entry_id="stale",
        published_dt=datetime(2026, 4, 24, 12, 0, 0, tzinfo=timezone.utc),  # 48h old
    )
    monkeypatch.setattr(ob, "_fetch_feed", lambda f: [fresh, stale])
    cands = ob.gather_candidates([feed], now=now, max_age_hours=24)
    assert len(cands) == 1
    assert cands[0]["item_id"] == "fresh"


def test_gather_dedup_via_is_seen(monkeypatch):
    feed = ob.FeedConfig(name="x", url="https://example.com/feed", publisher="X")
    now = datetime(2026, 4, 26, 12, 0, 0, tzinfo=timezone.utc)
    e1 = _make_entry(link="https://example.com/a", entry_id="a", published_dt=now)
    e2 = _make_entry(link="https://example.com/b", entry_id="b", published_dt=now)
    monkeypatch.setattr(ob, "_fetch_feed", lambda f: [e1, e2])
    # patch the is_seen used inside official_blogs (not shared.state directly)
    monkeypatch.setattr(ob, "is_seen", lambda src, item_id: item_id == "a")
    cands = ob.gather_candidates([feed], now=now)
    ids = [c["item_id"] for c in cands]
    assert ids == ["b"]


def test_gather_skip_seen_false_keeps_all(monkeypatch):
    feed = ob.FeedConfig(name="x", url="https://example.com/feed", publisher="X")
    now = datetime(2026, 4, 26, 12, 0, 0, tzinfo=timezone.utc)
    e = _make_entry(link="https://example.com/x", entry_id="x", published_dt=now)
    monkeypatch.setattr(ob, "_fetch_feed", lambda f: [e])
    monkeypatch.setattr(ob, "is_seen", lambda src, item_id: True)  # always seen
    cands = ob.gather_candidates([feed], now=now, skip_seen=False)
    assert len(cands) == 1


def test_gather_sorts_newest_first(monkeypatch):
    feed = ob.FeedConfig(name="x", url="https://example.com/feed", publisher="X")
    now = datetime(2026, 4, 26, 12, 0, 0, tzinfo=timezone.utc)
    older = _make_entry(
        link="https://example.com/older",
        entry_id="older",
        published_dt=datetime(2026, 4, 26, 6, 0, 0, tzinfo=timezone.utc),
    )
    newer = _make_entry(
        link="https://example.com/newer",
        entry_id="newer",
        published_dt=datetime(2026, 4, 26, 11, 0, 0, tzinfo=timezone.utc),
    )
    monkeypatch.setattr(ob, "_fetch_feed", lambda f: [older, newer])
    cands = ob.gather_candidates([feed], now=now)
    assert [c["item_id"] for c in cands] == ["newer", "older"]


def test_gather_continues_when_one_feed_fails(monkeypatch):
    """One feed raising should not block other feeds."""
    feed_bad = ob.FeedConfig(name="bad", url="https://bad.example.com/feed", publisher="Bad")
    feed_good = ob.FeedConfig(name="good", url="https://good.example.com/feed", publisher="Good")
    now = datetime(2026, 4, 26, 12, 0, 0, tzinfo=timezone.utc)
    good_entry = _make_entry(link="https://good.example.com/a", entry_id="g", published_dt=now)

    def _fake_fetch(feed):
        if feed.name == "bad":
            return []  # _fetch_feed catches its own exception → returns []
        return [good_entry]

    monkeypatch.setattr(ob, "_fetch_feed", _fake_fetch)
    cands = ob.gather_candidates([feed_bad, feed_good], now=now)
    assert len(cands) == 1
    assert cands[0]["item_id"] == "g"


def test_fetch_feed_swallows_exceptions(monkeypatch):
    """_fetch_feed should never raise, even on network error."""
    feed = ob.FeedConfig(name="x", url="https://broken.example.com/feed", publisher="X")
    fake_parser = MagicMock(side_effect=RuntimeError("network down"))
    monkeypatch.setattr(ob, "feedparser", SimpleNamespace(parse=fake_parser))
    result = ob._fetch_feed(feed)
    assert result == []


def test_fetch_feed_drops_when_bozo_with_no_entries(monkeypatch):
    feed = ob.FeedConfig(name="x", url="https://example.com/feed", publisher="X")
    fake_parsed = SimpleNamespace(bozo=True, bozo_exception=Exception("malformed"), entries=[])
    monkeypatch.setattr(ob, "feedparser", SimpleNamespace(parse=lambda url: fake_parsed))
    assert ob._fetch_feed(feed) == []


def test_fetch_feed_keeps_entries_when_bozo_but_entries_present(monkeypatch):
    feed = ob.FeedConfig(name="x", url="https://example.com/feed", publisher="X")
    e = _make_entry()
    fake_parsed = SimpleNamespace(bozo=True, bozo_exception=Exception("warning"), entries=[e])
    monkeypatch.setattr(ob, "feedparser", SimpleNamespace(parse=lambda url: fake_parsed))
    assert ob._fetch_feed(feed) == [e]
