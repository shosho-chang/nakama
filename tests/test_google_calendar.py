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


def test_create_event_dedupes_on_idempotency_key(monkeypatch):
    """ADR-041 D7: when an event with the same idempotency key already exists,
    create_event returns it WITHOUT inserting a duplicate (no service call)."""
    from shared import google_calendar
    from shared.google_calendar import CalendarEvent

    existing = CalendarEvent(id="evt_x", title="t", start="s", end="e", html_link="h")
    monkeypatch.setattr(
        google_calendar, "find_event_by_idempotency_key", lambda key, **kw: existing
    )

    def _no_service():
        raise AssertionError("must not insert when an idempotent event already exists")

    monkeypatch.setattr(google_calendar, "_get_service", _no_service)
    out = google_calendar.create_event(
        title="t",
        start="2026-06-03T09:00:00",
        end="2026-06-03T11:00:00",
        idempotency_key="slug@2026-06-03",
    )
    assert out is existing


class _FakeService:
    """Minimal Google Calendar service double that records the inserted/patched body."""

    def __init__(self):
        self.inserted = None
        self.patched = None

    def events(self):
        return self

    def insert(self, *, calendarId, body):
        self.inserted = body
        return _FakeExec({"id": "evt_new", **body})

    def patch(self, *, calendarId, eventId, body):
        self.patched = body
        return _FakeExec({"id": eventId, **body})


class _FakeExec:
    def __init__(self, ret):
        self._ret = ret

    def execute(self):
        return self._ret


def test_create_event_all_day_uses_date_not_datetime(monkeypatch):
    """ADR-041 v3-E: a date-only start/end → an all-day Google event (start.date /
    end.date, no dateTime), and conflict detection is skipped (no time slot)."""
    from shared import google_calendar

    svc = _FakeService()
    monkeypatch.setattr(google_calendar, "_get_service", lambda: svc)
    monkeypatch.setattr(
        google_calendar,
        "find_conflicts",
        lambda s, e: (_ for _ in ()).throw(AssertionError("all-day must not conflict-check")),
    )
    google_calendar.create_event(title="整天任務", start="2026-06-05", end="2026-06-06")
    assert svc.inserted["start"] == {"date": "2026-06-05"}
    assert svc.inserted["end"] == {"date": "2026-06-06"}
    assert "dateTime" not in svc.inserted["start"]


def test_update_event_all_day_patches_date(monkeypatch):
    """v3-E: patching with a date-only start/end keeps the event all-day."""
    from shared import google_calendar

    svc = _FakeService()
    monkeypatch.setattr(google_calendar, "_get_service", lambda: svc)
    google_calendar.update_event("evt1", start="2026-06-07", end="2026-06-08")
    assert svc.patched["start"] == {"date": "2026-06-07"}
    assert svc.patched["end"] == {"date": "2026-06-08"}


def test_is_date_only():
    from shared.google_calendar import _is_date_only

    assert _is_date_only("2026-06-05") is True
    assert _is_date_only("2026-06-05T09:00:00") is False
    assert _is_date_only("2026-06-05T09:00:00+08:00") is False
    assert _is_date_only("") is False


def test_find_free_slots_gaps_ordered_by_proximity(monkeypatch):
    """v3-F: free-slot suggestions skip all-day events and order by closeness to the
    requested time. Busy 09–10 & 14–15 on 6/5 ⇒ gaps starting 08:00 / 10:00 / 15:00;
    nearest to a 14:00 request is the 15:00 gap."""
    from datetime import date

    from shared import google_calendar
    from shared.google_calendar import CalendarEvent

    evs = [
        CalendarEvent(
            id="a",
            title="x",
            start="2026-06-05T09:00:00+08:00",
            end="2026-06-05T10:00:00+08:00",
            html_link="h",
        ),
        CalendarEvent(
            id="b",
            title="y",
            start="2026-06-05T14:00:00+08:00",
            end="2026-06-05T15:00:00+08:00",
            html_link="h",
        ),
        CalendarEvent(
            id="c", title="整天", start="2026-06-05", end="2026-06-06", html_link="h"
        ),  # all-day → ignored
    ]
    monkeypatch.setattr(google_calendar, "list_events", lambda **kw: evs)
    slots = google_calendar.find_free_slots(
        date(2026, 6, 5), 60, near="2026-06-05T14:00:00+08:00", max_slots=3
    )
    assert slots[0] == ("2026-06-05T15:00:00+08:00", "2026-06-05T16:00:00+08:00")
    starts = {s for s, _ in slots}
    assert "2026-06-05T08:00:00+08:00" in starts and "2026-06-05T10:00:00+08:00" in starts
