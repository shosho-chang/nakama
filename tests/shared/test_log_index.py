"""Tests for shared/log_index.py."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from shared.log_index import LogIndex, LogStats


@pytest.fixture
def idx(tmp_path):
    return LogIndex.from_path(tmp_path / "logs.db")


def _ts(seconds_ago: int = 0) -> datetime:
    return datetime.now(timezone.utc) - timedelta(seconds=seconds_ago)


def test_insert_and_search_basic(idx: LogIndex):
    idx.insert(ts=_ts(60), level="INFO", logger="nakama.test", msg="hello world", extra={})
    idx.insert(ts=_ts(30), level="ERROR", logger="nakama.test", msg="db connection lost", extra={"err": "timeout"})

    hits = idx.search("hello", limit=10)
    assert len(hits) == 1
    assert hits[0].msg == "hello world"
    assert hits[0].level == "INFO"
    assert "<mark>hello</mark>" in hits[0].snippet
    assert hits[0].extra == {}


def test_search_empty_query_browses_recent(idx: LogIndex):
    idx.insert(ts=_ts(120), level="INFO", logger="a", msg="oldest", extra={})
    idx.insert(ts=_ts(60), level="INFO", logger="a", msg="middle", extra={})
    idx.insert(ts=_ts(10), level="INFO", logger="a", msg="newest", extra={})

    hits = idx.search("", limit=10)
    assert [h.msg for h in hits] == ["newest", "middle", "oldest"]


def test_search_level_filter(idx: LogIndex):
    idx.insert(ts=_ts(60), level="INFO", logger="a", msg="info", extra={})
    idx.insert(ts=_ts(30), level="ERROR", logger="a", msg="error", extra={})
    idx.insert(ts=_ts(10), level="WARNING", logger="a", msg="warning", extra={})

    err_hits = idx.search("", level="ERROR")
    assert len(err_hits) == 1
    assert err_hits[0].msg == "error"

    err_hits_lower = idx.search("", level="error")
    assert len(err_hits_lower) == 1


def test_search_logger_prefix_filter(idx: LogIndex):
    idx.insert(ts=_ts(60), level="INFO", logger="nakama.franky.health", msg="franky health", extra={})
    idx.insert(ts=_ts(30), level="INFO", logger="nakama.robin.scout", msg="robin scout", extra={})
    idx.insert(ts=_ts(10), level="INFO", logger="nakama.franky.news", msg="franky news", extra={})

    franky_hits = idx.search("", logger_prefix="nakama.franky")
    assert len(franky_hits) == 2
    assert {h.logger for h in franky_hits} == {"nakama.franky.health", "nakama.franky.news"}


def test_search_time_range(idx: LogIndex):
    now = datetime.now(timezone.utc)
    idx.insert(ts=now - timedelta(hours=3), level="INFO", logger="a", msg="three hours ago", extra={})
    idx.insert(ts=now - timedelta(hours=1), level="INFO", logger="a", msg="one hour ago", extra={})
    idx.insert(ts=now - timedelta(minutes=5), level="INFO", logger="a", msg="five min ago", extra={})

    hits = idx.search("", since=now - timedelta(hours=2))
    assert len(hits) == 2
    assert "three hours ago" not in [h.msg for h in hits]

    hits = idx.search("", until=now - timedelta(hours=2))
    assert len(hits) == 1
    assert hits[0].msg == "three hours ago"


def test_search_bad_fts5_syntax_returns_empty(idx: LogIndex):
    idx.insert(ts=_ts(10), level="INFO", logger="a", msg="hello", extra={})
    # Unbalanced quote — FTS5 raises OperationalError; we soft-fail to [].
    hits = idx.search('"unbalanced')
    assert hits == []


def test_search_snippet_html_escaped(idx: LogIndex):
    idx.insert(
        ts=_ts(10),
        level="INFO",
        logger="a",
        msg="literal <script>alert(1)</script> tag",
        extra={},
    )
    hits = idx.search("script")
    assert len(hits) == 1
    # `<script>` must NOT survive raw — html.escape() turns < into &lt;.
    # FTS5 wraps the matched token (`script`) in <mark>, so the actual snippet
    # reads `&lt;<mark>script</mark>&gt;` — escape works around the mark.
    assert "<script>" not in hits[0].snippet
    assert "&lt;" in hits[0].snippet
    assert "&gt;" in hits[0].snippet
    assert "<mark>script</mark>" in hits[0].snippet


def test_search_combined_filters(idx: LogIndex):
    idx.insert(ts=_ts(60), level="ERROR", logger="nakama.franky", msg="db connection refused", extra={})
    idx.insert(ts=_ts(30), level="INFO", logger="nakama.franky", msg="db connection ok", extra={})
    idx.insert(ts=_ts(10), level="ERROR", logger="nakama.robin", msg="db connection refused", extra={})

    hits = idx.search("connection", level="ERROR", logger_prefix="nakama.franky")
    assert len(hits) == 1
    assert hits[0].msg == "db connection refused"
    assert hits[0].logger == "nakama.franky"


def test_insert_naive_datetime_raises(idx: LogIndex):
    with pytest.raises(ValueError, match="timezone-aware"):
        idx.insert(ts=datetime(2026, 4, 26, 12, 0, 0), level="INFO", logger="a", msg="x", extra={})


def test_extra_json_round_trip(idx: LogIndex):
    idx.insert(
        ts=_ts(10),
        level="INFO",
        logger="a",
        msg="task done",
        extra={"job": "nakama-backup", "duration_ms": 1234, "ok": True},
    )
    hits = idx.search("done")
    assert hits[0].extra == {"job": "nakama-backup", "duration_ms": 1234, "ok": True}


def test_stats_empty(idx: LogIndex):
    s = idx.stats()
    assert s == LogStats(total=0)


def test_stats_populated(idx: LogIndex):
    idx.insert(ts=_ts(60), level="INFO", logger="a", msg="x", extra={})
    idx.insert(ts=_ts(30), level="INFO", logger="a", msg="y", extra={})
    idx.insert(ts=_ts(10), level="ERROR", logger="a", msg="z", extra={})

    s = idx.stats()
    assert s.total == 3
    assert s.by_level == {"INFO": 2, "ERROR": 1}
    assert s.oldest_ts is not None
    assert s.newest_ts is not None
    assert s.oldest_ts <= s.newest_ts


def test_cleanup_deletes_old_rows(idx: LogIndex):
    now = datetime.now(timezone.utc)
    idx.insert(ts=now - timedelta(days=40), level="INFO", logger="a", msg="ancient", extra={})
    idx.insert(ts=now - timedelta(days=20), level="INFO", logger="a", msg="recent", extra={})
    idx.insert(ts=now - timedelta(hours=1), level="INFO", logger="a", msg="just now", extra={})

    deleted = idx.cleanup(older_than=timedelta(days=30))
    assert deleted == 1
    assert idx.stats().total == 2

    remaining = idx.search("")
    assert {h.msg for h in remaining} == {"recent", "just now"}


def test_cleanup_also_removes_from_fts(idx: LogIndex):
    """After delete, FTS5 should not return the deleted row (logs_ad trigger)."""
    now = datetime.now(timezone.utc)
    idx.insert(ts=now - timedelta(days=40), level="INFO", logger="a", msg="ancient secret", extra={})
    idx.insert(ts=now - timedelta(hours=1), level="INFO", logger="a", msg="fresh secret", extra={})

    pre = idx.search("secret")
    assert len(pre) == 2

    idx.cleanup(older_than=timedelta(days=30))
    post = idx.search("secret")
    assert len(post) == 1
    assert post[0].msg == "fresh secret"


def test_limit_and_offset(idx: LogIndex):
    for i in range(10):
        idx.insert(ts=_ts(60 - i), level="INFO", logger="a", msg=f"row {i}", extra={})

    page1 = idx.search("", limit=3, offset=0)
    page2 = idx.search("", limit=3, offset=3)
    assert len(page1) == 3
    assert len(page2) == 3
    assert {h.msg for h in page1} & {h.msg for h in page2} == set()
