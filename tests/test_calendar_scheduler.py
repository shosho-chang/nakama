"""Tests for shared.calendar_scheduler (ADR-041 41b) — Google Calendar fully mocked;
no live API. Verifies the vault-first / calendar-best-effort contract (D1/D2/D7)."""

from __future__ import annotations

from datetime import datetime
from pathlib import Path

import pytest
import yaml

from shared import calendar_scheduler as cs
from shared import google_calendar
from shared.calendar_scheduler import (
    CANCEL_CAL_FAILED,
    CANCELLED,
    CONFLICT,
    CREATED,
    FAILED,
    UNAVAILABLE,
    cancel_schedule,
    reschedule_block,
    schedule_block,
    unlink_calendar,
)
from shared.google_calendar import CalendarEvent
from shared.weekly_writer import WeeklyWriteError

TASKS_DIR = "TaskNotes/Tasks"
SLUG = "寫當週電子報"


def _make_task(vault: Path) -> None:
    d = vault / TASKS_DIR
    d.mkdir(parents=True, exist_ok=True)
    fm = {"title": SLUG, "status": "to-do", "預估🍅": 4, "tags": ["task"]}
    (d / f"{SLUG}.md").write_text(
        f"---\n{yaml.safe_dump(fm, allow_unicode=True, sort_keys=False)}---\n\nbody\n",
        encoding="utf-8",
    )


def _fm(vault: Path) -> dict:
    raw = (vault / TASKS_DIR / f"{SLUG}.md").read_text(encoding="utf-8")
    return yaml.safe_load(raw.split("---", 2)[1])


def _event(eid="evt_1"):
    return CalendarEvent(id=eid, title=SLUG, start="x", end="y", html_link="http://h")


WED_9AM = datetime(2026, 6, 3, 9, 0, 0)  # weekday


def test_created_path_links_event(vault, monkeypatch):
    _make_task(vault)
    seen = {}

    def fake_create(**kw):
        seen.update(kw)
        return _event("evt_42")

    monkeypatch.setattr(google_calendar, "create_event", fake_create)
    out = schedule_block(vault, SLUG, start=WED_9AM, pomodoros=4, title=SLUG)

    assert out.calendar_status == CREATED
    assert out.event_id == "evt_42"
    fm = _fm(vault)
    assert fm["plan"][0]["pomodoros"] == 4  # authoritative plan (D1)
    assert fm["calendar_event_id"] == "evt_42"  # linked
    assert str(fm["scheduled"]).startswith("2026-06-03T09:00")
    assert seen["idempotency_key"] == f"{SLUG}@2026-06-03"  # D7


def test_conflict_path_writes_plan_but_no_event(vault, monkeypatch):
    _make_task(vault)
    monkeypatch.setattr(google_calendar, "create_event", lambda **kw: [_event("other")])
    out = schedule_block(vault, SLUG, start=WED_9AM, pomodoros=2, title=SLUG)

    assert out.calendar_status == CONFLICT
    assert out.event_id is None
    assert len(out.conflicts) == 1
    fm = _fm(vault)
    assert fm["plan"][0]["pomodoros"] == 2  # plan still written (D2)
    assert "calendar_event_id" not in fm


def test_calendar_outage_degrades(vault, monkeypatch):
    _make_task(vault)

    def boom(**kw):
        raise RuntimeError("API down")

    monkeypatch.setattr(google_calendar, "create_event", boom)
    out = schedule_block(vault, SLUG, start=WED_9AM, pomodoros=2, title=SLUG)

    assert out.calendar_status == UNAVAILABLE  # never raises (D2)
    fm = _fm(vault)
    assert fm["plan"][0]["pomodoros"] == 2  # plan stands
    assert "calendar_event_id" not in fm


def test_writeback_failure_rolls_back_event(vault, monkeypatch):
    _make_task(vault)
    monkeypatch.setattr(google_calendar, "create_event", lambda **kw: _event("evt_orphan"))
    deleted = []
    monkeypatch.setattr(google_calendar, "delete_event", lambda eid: deleted.append(eid))

    # make the SECOND schedule_task_block (the event-id write-back) fail
    calls = {"n": 0}
    real = cs.schedule_task_block

    def flaky(*a, **k):
        calls["n"] += 1
        if calls["n"] == 2:
            raise WeeklyWriteError("disk gone on write-back")
        return real(*a, **k)

    monkeypatch.setattr(cs, "schedule_task_block", flaky)
    out = schedule_block(vault, SLUG, start=WED_9AM, pomodoros=2, title=SLUG)

    assert out.calendar_status == FAILED
    assert deleted == ["evt_orphan"]  # orphan event rolled back


def test_force_skips_conflict_check(vault, monkeypatch):
    _make_task(vault)
    seen = {}
    monkeypatch.setattr(
        google_calendar, "create_event", lambda **kw: (seen.update(kw), _event())[1]
    )
    schedule_block(vault, SLUG, start=WED_9AM, pomodoros=2, title=SLUG, force=True)
    assert seen["check_conflict"] is False


def _make_linked_task(vault: Path, eid="evt_live") -> None:
    """A task already projected onto the calendar (scheduled Wed 6/3 09:00, 2🍅)."""
    d = vault / TASKS_DIR
    d.mkdir(parents=True, exist_ok=True)
    fm = {
        "title": SLUG,
        "status": "to-do",
        "預估🍅": 4,
        "scheduled": "2026-06-03T09:00:00",
        "scheduled_end": "2026-06-03T10:00:00",
        "calendar_event_id": eid,
        "plan": [{"date": "2026-06-03", "pomodoros": 2}],
        "tags": ["task"],
    }
    (d / f"{SLUG}.md").write_text(
        f"---\n{yaml.safe_dump(fm, allow_unicode=True, sort_keys=False)}---\n\nbody\n",
        encoding="utf-8",
    )


# ── reschedule_block (D8) ───────────────────────────────────────────────────


def test_reschedule_patches_same_event(vault, monkeypatch):
    _make_linked_task(vault)
    seen = {}

    def fake_update(event_id, **kw):
        seen["event_id"] = event_id
        seen.update(kw)
        return _event(event_id)

    monkeypatch.setattr(google_calendar, "find_conflicts", lambda s, e: [])
    monkeypatch.setattr(google_calendar, "update_event", fake_update)
    out = reschedule_block(vault, SLUG, start=datetime(2026, 6, 4, 14, 0), pomodoros=3, title=SLUG)
    assert out.calendar_status == CREATED
    assert out.event_id == "evt_live"
    assert seen["event_id"] == "evt_live"  # PATCHed the SAME event (no orphan, D8)
    assert seen["start"] == "2026-06-04T14:00:00"
    fm = _fm(vault)
    assert [e["date"] for e in fm["plan"]] == ["2026-06-04"]  # plan moved
    assert fm["calendar_event_id"] == "evt_live"


def test_reschedule_conflict_excludes_own_event(vault, monkeypatch):
    """A small shift overlaps the block's OWN old slot — that self-match must be
    filtered out, so it does NOT count as a conflict."""
    _make_linked_task(vault)
    monkeypatch.setattr(google_calendar, "find_conflicts", lambda s, e: [_event("evt_live")])
    updated = {"n": 0}
    monkeypatch.setattr(
        google_calendar,
        "update_event",
        lambda eid, **kw: (updated.__setitem__("n", 1), _event(eid))[1],
    )
    out = reschedule_block(vault, SLUG, start=datetime(2026, 6, 3, 9, 15), pomodoros=2, title=SLUG)
    assert out.calendar_status == CREATED  # own event filtered → no conflict
    assert updated["n"] == 1


def test_reschedule_real_conflict_blocks_move(vault, monkeypatch):
    _make_linked_task(vault)
    monkeypatch.setattr(google_calendar, "find_conflicts", lambda s, e: [_event("someone_else")])
    updated = {"n": 0}
    monkeypatch.setattr(
        google_calendar,
        "update_event",
        lambda eid, **kw: (updated.__setitem__("n", 1), _event(eid))[1],
    )
    out = reschedule_block(vault, SLUG, start=datetime(2026, 6, 4, 9, 0), pomodoros=2, title=SLUG)
    assert out.calendar_status == CONFLICT
    assert updated["n"] == 0  # event NOT moved
    fm = _fm(vault)
    assert [e["date"] for e in fm["plan"]] == ["2026-06-04"]  # but plan still moved (D2)


def test_reschedule_force_skips_conflict_check(vault, monkeypatch):
    _make_linked_task(vault)
    checked = {"n": 0}
    monkeypatch.setattr(
        google_calendar,
        "find_conflicts",
        lambda s, e: (checked.__setitem__("n", 1), [])[1],
    )
    monkeypatch.setattr(google_calendar, "update_event", lambda eid, **kw: _event(eid))
    out = reschedule_block(
        vault, SLUG, start=datetime(2026, 6, 4, 9, 0), pomodoros=2, title=SLUG, force=True
    )
    assert out.calendar_status == CREATED
    assert checked["n"] == 0  # no pre-check when forced


def test_reschedule_calendar_outage_degrades(vault, monkeypatch):
    _make_linked_task(vault)
    monkeypatch.setattr(google_calendar, "find_conflicts", lambda s, e: [])

    def boom(eid, **kw):
        raise RuntimeError("API down")

    monkeypatch.setattr(google_calendar, "update_event", boom)
    out = reschedule_block(vault, SLUG, start=datetime(2026, 6, 4, 9, 0), pomodoros=2, title=SLUG)
    assert out.calendar_status == UNAVAILABLE  # never raises (D2)
    fm = _fm(vault)
    assert [e["date"] for e in fm["plan"]] == ["2026-06-04"]  # plan moved regardless


def test_reschedule_unlinked_falls_back_to_create(vault, monkeypatch):
    _make_task(vault)  # no calendar_event_id
    monkeypatch.setattr(google_calendar, "create_event", lambda **kw: _event("evt_new"))
    out = reschedule_block(vault, SLUG, start=datetime(2026, 6, 4, 9, 0), pomodoros=2, title=SLUG)
    assert out.calendar_status == CREATED
    assert out.event_id == "evt_new"
    assert _fm(vault)["calendar_event_id"] == "evt_new"


# ── cancel actions (D9) ─────────────────────────────────────────────────────


def test_unlink_clears_projection_keeps_plan_and_deletes_event(vault, monkeypatch):
    _make_linked_task(vault)
    deleted = []
    monkeypatch.setattr(google_calendar, "delete_event", lambda eid: deleted.append(eid))
    out = unlink_calendar(vault, SLUG)
    assert out.calendar_status == CANCELLED
    assert deleted == ["evt_live"]
    fm = _fm(vault)
    assert "calendar_event_id" not in fm and "scheduled" not in fm
    assert fm["plan"] == [{"date": "2026-06-03", "pomodoros": 2}]  # KEPT (D9)


def test_cancel_schedule_drops_plan_and_deletes_event(vault, monkeypatch):
    _make_linked_task(vault)
    deleted = []
    monkeypatch.setattr(google_calendar, "delete_event", lambda eid: deleted.append(eid))
    out = cancel_schedule(vault, SLUG)
    assert out.calendar_status == CANCELLED
    assert deleted == ["evt_live"]
    fm = _fm(vault)
    assert not fm.get("plan")  # the scheduled-date entry dropped (D9)
    assert "calendar_event_id" not in fm


def test_cancel_calendar_delete_failure_degrades(vault, monkeypatch):
    _make_linked_task(vault)

    def boom(eid):
        raise RuntimeError("API down")

    monkeypatch.setattr(google_calendar, "delete_event", boom)
    out = unlink_calendar(vault, SLUG)
    assert out.calendar_status == CANCEL_CAL_FAILED  # vault cleared, delete failed
    fm = _fm(vault)
    assert "calendar_event_id" not in fm  # vault still authoritatively cleared


@pytest.fixture
def vault(tmp_path: Path) -> Path:
    return tmp_path
