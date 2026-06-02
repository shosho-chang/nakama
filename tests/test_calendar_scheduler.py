"""Tests for shared.calendar_scheduler (ADR-041 41b) — Google Calendar fully mocked;
no live API. Verifies the vault-first / calendar-best-effort contract (D1/D2/D7)."""

from __future__ import annotations

from datetime import datetime
from pathlib import Path

import pytest
import yaml

from shared import calendar_scheduler as cs
from shared import google_calendar
from shared.calendar_scheduler import CONFLICT, CREATED, FAILED, UNAVAILABLE, schedule_block
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


@pytest.fixture
def vault(tmp_path: Path) -> Path:
    return tmp_path
