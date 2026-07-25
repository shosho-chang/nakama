"""calendar_reconcile.sweep / audit — the N543 drift net (match by title+date, classify
every drift kind, auto-link only the unambiguous `unlinked` case)."""

from __future__ import annotations

from datetime import date
from pathlib import Path

import yaml

from shared import calendar_reconcile as cr
from shared.google_calendar import CalendarEvent

TODAY = date(2026, 7, 1)


def _task(vault: Path, fname: str, fm: dict) -> None:
    d = vault / "TaskNotes" / "Tasks"
    d.mkdir(parents=True, exist_ok=True)
    body = yaml.safe_dump(fm, allow_unicode=True, sort_keys=False)
    (d / fname).write_text(f"---\n{body}---\n\n", encoding="utf-8")


def _ev(eid: str, title: str, day: str, start="10:30:00", end="17:00:00") -> CalendarEvent:
    return CalendarEvent(
        id=eid,
        title=title,
        start=f"{day}T{start}+08:00",
        end=f"{day}T{end}+08:00",
        html_link="http://x",
    )


def _plan(day: str, event_id: str | None = None, start="10:30:00", end="17:00:00") -> dict:
    e = {"date": day, "pomodoros": 13, "start": f"{day}T{start}+08:00", "end": f"{day}T{end}+08:00"}
    if event_id:
        e["calendar_event_id"] = event_id
    return e


def _events_fn(events):
    return lambda **_: list(events)


def test_unlinked_is_auto_linked(tmp_path):
    """The 0724/0731 shape: task plan entry, no event_id, but a matching event exists →
    sweep writes the id back (repaired), leaving no drift."""
    _task(
        tmp_path,
        "正課 0724.md",
        {
            "title": "知識衛星｜正課拍攝",
            "status": "to-do",
            "category": "work",
            "plan": [_plan("2026-07-24")],
        },
    )
    ev = _ev("evt724", "知識衛星｜正課拍攝", "2026-07-24")
    rep = cr.sweep(tmp_path, today=TODAY, list_events_fn=_events_fn([ev]))
    assert len(rep.repaired) == 1 and not rep.drifts
    # the id was written into the plan entry
    fm = yaml.safe_load(
        (tmp_path / "TaskNotes/Tasks/正課 0724.md").read_text("utf-8").split("---")[1]
    )
    assert fm["plan"][0]["calendar_event_id"] == "evt724"


def test_bare_scheduled_is_promoted_and_linked(tmp_path):
    """A legacy bare `scheduled: <datetime>` (no plan[]) is seen, matched, and promoted to a
    linked plan entry — the exact repair done by hand for 0724."""
    _task(
        tmp_path,
        "正課 0731.md",
        {
            "title": "知識衛星｜正課拍攝",
            "status": "to-do",
            "category": "work",
            "scheduled": "2026-07-31T10:30:00",
        },
    )
    ev = _ev("evt731", "知識衛星｜正課拍攝", "2026-07-31")
    rep = cr.sweep(tmp_path, today=TODAY, list_events_fn=_events_fn([ev]))
    assert len(rep.repaired) == 1 and not rep.drifts
    fm = yaml.safe_load(
        (tmp_path / "TaskNotes/Tasks/正課 0731.md").read_text("utf-8").split("---")[1]
    )
    assert fm["plan"][0]["calendar_event_id"] == "evt731"
    assert "scheduled" not in fm


def test_clean_when_linked_and_matching(tmp_path):
    _task(
        tmp_path,
        "募資.md",
        {
            "title": "募資影片拍攝",
            "status": "to-do",
            "category": "work",
            "plan": [_plan("2026-07-14", "evt714", start="08:30:00")],
        },
    )
    ev = _ev("evt714", "募資影片拍攝", "2026-07-14", start="08:30:00")
    rep = cr.sweep(tmp_path, today=TODAY, list_events_fn=_events_fn([ev]))
    assert rep.clean and rep.checked_tasks == 1


def test_time_mismatch_reported_not_fixed(tmp_path):
    _task(
        tmp_path,
        "正課.md",
        {
            "title": "正課拍攝",
            "status": "to-do",
            "category": "work",
            "plan": [_plan("2026-07-10", "e10", end="18:00:00")],
        },
    )
    ev = _ev("e10", "正課拍攝", "2026-07-10", end="17:00:00")  # calendar says 17:00
    rep = cr.sweep(tmp_path, today=TODAY, list_events_fn=_events_fn([ev]))
    assert not rep.repaired
    assert [d.kind for d in rep.drifts] == ["time_mismatch"]


def test_dangling_link_reported(tmp_path):
    _task(
        tmp_path,
        "正課.md",
        {
            "title": "正課拍攝",
            "status": "to-do",
            "category": "work",
            "plan": [_plan("2026-07-10", "goneid")],
        },
    )
    rep = cr.sweep(tmp_path, today=TODAY, list_events_fn=_events_fn([]))  # event vanished
    assert [d.kind for d in rep.drifts] == ["dangling"]


def test_no_event_reported(tmp_path):
    _task(
        tmp_path,
        "正課.md",
        {"title": "正課拍攝", "status": "to-do", "category": "work", "plan": [_plan("2026-07-10")]},
    )
    rep = cr.sweep(tmp_path, today=TODAY, list_events_fn=_events_fn([]))
    assert [d.kind for d in rep.drifts] == ["no_event"]


def test_orphan_event_reported(tmp_path):
    """Event titled like a task (stem match) but no task links it — the 7/10 'task never
    created' shape."""
    _task(
        tmp_path,
        "正課 0724.md",
        {
            "title": "知識衛星｜正課拍攝 0724",
            "status": "to-do",
            "category": "work",
            "plan": [_plan("2026-07-24", "evt724")],
        },
    )
    events = [
        _ev("evt724", "知識衛星｜正課拍攝 0724", "2026-07-24"),
        _ev("orphan710", "知識衛星｜正課拍攝", "2026-07-10"),
    ]  # stem of the task title
    rep = cr.sweep(tmp_path, today=TODAY, list_events_fn=_events_fn(events))
    kinds = [d.kind for d in rep.drifts]
    assert "orphan_event" in kinds
    assert any(d.event_id == "orphan710" for d in rep.drifts)


def test_done_tasks_ignored(tmp_path):
    _task(
        tmp_path,
        "done.md",
        {"title": "正課拍攝", "status": "done", "category": "work", "plan": [_plan("2026-07-10")]},
    )
    rep = cr.sweep(tmp_path, today=TODAY, list_events_fn=_events_fn([]))
    assert rep.clean and rep.checked_tasks == 0


def test_slack_text_empty_when_clean(tmp_path):
    rep = cr.sweep(tmp_path, today=TODAY, list_events_fn=_events_fn([]))
    assert rep.slack_text() == ""
