"""shared/google_calendar.py helper 測試。

只測純函式（time/overlap/parsing），不碰真 API。
"""

from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo

import pytest

from shared.google_calendar import (
    CalendarEvent,
    _dt_to_rfc3339,
    _ensure_tz_iso,
    _overlaps,
    _parse_event,
    _parse_iso,
)

# ── _ensure_tz_iso ───────────────────────────────────────────────────


def test_ensure_tz_iso_naive_gets_taipei_offset():
    assert _ensure_tz_iso("2026-04-25T15:00:00") == "2026-04-25T15:00:00+08:00"


def test_ensure_tz_iso_with_offset_unchanged():
    assert _ensure_tz_iso("2026-04-25T15:00:00+08:00") == "2026-04-25T15:00:00+08:00"


def test_ensure_tz_iso_with_utc_z_unchanged():
    assert _ensure_tz_iso("2026-04-25T15:00:00Z") == "2026-04-25T15:00:00Z"


def test_ensure_tz_iso_empty_passthrough():
    assert _ensure_tz_iso("") == ""


def test_ensure_tz_iso_with_negative_offset():
    assert _ensure_tz_iso("2026-04-25T15:00:00-05:00") == "2026-04-25T15:00:00-05:00"


# ── _dt_to_rfc3339 ───────────────────────────────────────────────────


def test_dt_to_rfc3339_naive_gets_taipei():
    dt = datetime(2026, 4, 25, 15, 0, 0)  # naive
    result = _dt_to_rfc3339(dt)
    assert result.endswith("+08:00")
    assert "2026-04-25T15:00:00" in result


def test_dt_to_rfc3339_aware_preserves_tz():
    dt = datetime(2026, 4, 25, 15, 0, 0, tzinfo=ZoneInfo("UTC"))
    result = _dt_to_rfc3339(dt)
    # UTC datetime isoformat 可能是 +00:00 或不同形式
    parsed = datetime.fromisoformat(result)
    assert parsed.tzinfo is not None
    assert parsed.utcoffset().total_seconds() == 0


# ── _parse_iso ───────────────────────────────────────────────────────


def test_parse_iso_with_offset():
    dt = _parse_iso("2026-04-25T15:00:00+08:00")
    assert dt.year == 2026
    assert dt.tzinfo is not None


def test_parse_iso_with_z():
    dt = _parse_iso("2026-04-25T15:00:00Z")
    assert dt.utcoffset().total_seconds() == 0


# ── _overlaps ────────────────────────────────────────────────────────


def test_overlaps_true_partial():
    # 15:00-16:00 vs 15:30-16:30
    assert _overlaps(
        "2026-04-25T15:00:00+08:00",
        "2026-04-25T16:00:00+08:00",
        "2026-04-25T15:30:00+08:00",
        "2026-04-25T16:30:00+08:00",
    )


def test_overlaps_true_contained():
    assert _overlaps(
        "2026-04-25T15:00:00+08:00",
        "2026-04-25T17:00:00+08:00",
        "2026-04-25T15:30:00+08:00",
        "2026-04-25T16:00:00+08:00",
    )


def test_overlaps_false_adjacent_end_equals_start():
    """A 結束時間 = B 開始時間 → 不算重疊。"""
    assert not _overlaps(
        "2026-04-25T15:00:00+08:00",
        "2026-04-25T16:00:00+08:00",
        "2026-04-25T16:00:00+08:00",
        "2026-04-25T17:00:00+08:00",
    )


def test_overlaps_false_gap():
    assert not _overlaps(
        "2026-04-25T15:00:00+08:00",
        "2026-04-25T16:00:00+08:00",
        "2026-04-25T17:00:00+08:00",
        "2026-04-25T18:00:00+08:00",
    )


def test_overlaps_handles_different_timezones():
    # UTC 07:00-08:00 == Taipei 15:00-16:00
    assert _overlaps(
        "2026-04-25T07:00:00Z",
        "2026-04-25T08:00:00Z",
        "2026-04-25T15:30:00+08:00",
        "2026-04-25T16:30:00+08:00",
    )


# ── _parse_event ─────────────────────────────────────────────────────


def test_parse_event_timed():
    g = {
        "id": "evt123",
        "summary": "會議",
        "start": {"dateTime": "2026-04-25T15:00:00+08:00"},
        "end": {"dateTime": "2026-04-25T16:00:00+08:00"},
        "htmlLink": "https://calendar.google.com/evt123",
        "description": "議題 A",
    }
    event = _parse_event(g)
    assert event.id == "evt123"
    assert event.title == "會議"
    assert event.start == "2026-04-25T15:00:00+08:00"
    assert event.description == "議題 A"


def test_parse_event_all_day():
    g = {
        "id": "evt_day",
        "summary": "假日",
        "start": {"date": "2026-04-25"},
        "end": {"date": "2026-04-26"},
        "htmlLink": "https://calendar.google.com/evt_day",
    }
    event = _parse_event(g)
    assert event.start == "2026-04-25"
    assert event.end == "2026-04-26"
    assert event.description == ""


def test_parse_event_missing_summary():
    g = {"id": "evt_nosum", "start": {"date": "2026-04-25"}, "end": {"date": "2026-04-26"}}
    event = _parse_event(g)
    assert event.title == ""


# ── CalendarEvent dataclass ──────────────────────────────────────────


def test_calendar_event_dataclass():
    e = CalendarEvent(id="x", title="y", start="a", end="b", html_link="c", description="d")
    assert e.id == "x"


def test_calendar_event_default_description():
    e = CalendarEvent(id="x", title="y", start="a", end="b", html_link="c")
    assert e.description == ""


# ── Integration edge: _get_credentials with missing token ────────────


def test_get_credentials_missing_token_raises(monkeypatch, tmp_path):
    """無 token.json 時應拋 GoogleCalendarAuthError，不是 FileNotFoundError。"""
    from shared import google_calendar
    from shared.google_calendar import GoogleCalendarAuthError

    monkeypatch.setattr(google_calendar, "_TOKEN_PATH", tmp_path / "nonexistent.json")
    with pytest.raises(GoogleCalendarAuthError, match="Token 不存在"):
        google_calendar._get_credentials()
