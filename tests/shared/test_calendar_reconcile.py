"""calendar_reconcile.reconcile_scheduled_tasks — backfill existing GCal events
onto scheduled-but-unlinked tasks (item 4, option b).

GCal + WeeklyIndexer + the vault write (upsert_plan_entry) are all stubbed; the
reconcile logic is exercised in isolation. The fake event is any object with an
``.id`` (reconcile only reads that).
"""

from __future__ import annotations

from datetime import date
from pathlib import Path
from types import SimpleNamespace

import shared.calendar_reconcile as cr


def _task(slug: str, scheduled, plan=()):
    return SimpleNamespace(slug=slug, scheduled=scheduled, plan=list(plan), est_pomodoros=4)


def _alloc(d: date, event_id: str = ""):
    return SimpleNamespace(date=d, event_id=event_id)


def _run(monkeypatch, tasks, find_fn, *, dry_run=False):
    """Run reconcile with stubbed indexer / GCal / writer. Returns (report, upserts)."""
    upserts: list[tuple] = []
    monkeypatch.setattr(cr, "WeeklyIndexer", lambda root: SimpleNamespace(read_tasks=lambda: tasks))
    monkeypatch.setattr(cr.google_calendar, "find_event_by_idempotency_key", find_fn)
    monkeypatch.setattr(
        cr,
        "upsert_plan_entry",
        lambda vault, slug, **kw: (
            upserts.append((slug, kw.get("day"), kw.get("calendar_event_id"))) or ("", "", "tok")
        ),
    )
    report = cr.reconcile_scheduled_tasks(Path("/vault"), dry_run=dry_run)
    return report, upserts


def test_links_existing_keyed_event(monkeypatch):
    """scheduled, unlinked, keyed event exists → linked + event id written back."""
    t = _task("拍攝0724", date(2026, 7, 24))
    report, upserts = _run(monkeypatch, [t], lambda key, **kw: SimpleNamespace(id="evt-1"))
    assert report.linked == [("拍攝0724", "2026-07-24", "evt-1")]
    assert upserts == [("拍攝0724", date(2026, 7, 24), "evt-1")]
    assert report.missing == [] and report.errors == []


def test_idempotency_key_format(monkeypatch):
    """The GCal lookup uses ``{slug}@{date}``."""
    seen = {}
    t = _task("知識衛星正課拍攝0731", date(2026, 7, 31))
    _run(monkeypatch, [t], lambda key, **kw: seen.update(key=key) or SimpleNamespace(id="e"))
    assert seen["key"] == "知識衛星正課拍攝0731@2026-07-31"


def test_missing_when_no_keyed_event(monkeypatch):
    """scheduled but no keyed event (never projected / hand-made) → missing, no write."""
    t = _task("無事件", date(2026, 7, 24))
    report, upserts = _run(monkeypatch, [t], lambda key, **kw: None)
    assert report.missing == [("無事件", "2026-07-24")]
    assert report.linked == [] and upserts == []


def test_skips_already_linked_scheduled_date(monkeypatch):
    """A plan entry already linked on the scheduled date → skip (never query GCal)."""
    t = _task("已連動", date(2026, 7, 24), plan=[_alloc(date(2026, 7, 24), event_id="evt-x")])
    queried: list[str] = []
    report, upserts = _run(monkeypatch, [t], lambda key, **kw: queried.append(key) or None)
    assert report.linked == [] and report.missing == [] and report.errors == []
    assert queried == []  # short-circuited before the GCal call


def test_unscheduled_task_ignored(monkeypatch):
    t = _task("沒排程", None)
    report, upserts = _run(monkeypatch, [t], lambda key, **kw: SimpleNamespace(id="e"))
    assert report.linked == [] and report.missing == [] and upserts == []


def test_dry_run_reports_but_does_not_write(monkeypatch):
    t = _task("拍攝0731", date(2026, 7, 31))
    report, upserts = _run(
        monkeypatch, [t], lambda key, **kw: SimpleNamespace(id="evt-2"), dry_run=True
    )
    assert report.linked == [("拍攝0731", "2026-07-31", "evt-2")]
    assert upserts == []  # dry-run wrote nothing
    assert report.dry_run is True


def test_gcal_error_recorded_and_batch_continues(monkeypatch):
    """A GCal failure on one task is recorded; the rest of the batch still runs."""
    good = _task("好", date(2026, 7, 24))
    bad = _task("壞", date(2026, 7, 25))

    def find_fn(key, **kw):
        if key.startswith("壞"):
            raise RuntimeError("auth expired")
        return SimpleNamespace(id="evt-ok")

    report, upserts = _run(monkeypatch, [bad, good], find_fn)
    assert report.errors == [("壞", "auth expired")]
    assert report.linked == [("好", "2026-07-24", "evt-ok")]
    assert upserts == [("好", date(2026, 7, 24), "evt-ok")]
