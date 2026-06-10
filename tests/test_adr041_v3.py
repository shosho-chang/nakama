"""ADR-041 v3 (multi-block projection) — per-entry writer + scheduler + indexer.

Google Calendar fully mocked (no live API). Covers the panel-driven contracts:
per-entry timed plan[] writes with +08:00 offset, done-safe removal, legacy fold,
reschedule-in-place (not delete+recreate), day-wide idempotency lookup, dual-read.
"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path

import pytest
import yaml

from shared import calendar_scheduler as cs
from shared import google_calendar
from shared.calendar_scheduler import CONFLICT, CREATED, UNAVAILABLE, cancel_entry, schedule_entry
from shared.google_calendar import CalendarEvent, _day_window_from_key
from shared.weekly_indexer import WeeklyIndexer
from shared.weekly_writer import (
    WeekendReasonRequired,
    migrate_legacy_projection,
    read_entry_event_id,
    remove_plan_entry,
    upsert_plan_entry,
)

TASKS_DIR = "TaskNotes/Tasks"
SLUG = "肌酸的妙用 - Pre-production"
WED = datetime(2026, 6, 3, 9, 0, 0)  # Wed (weekday)
SAT = datetime(2026, 6, 6, 9, 0, 0)  # Sat (weekend)


@pytest.fixture
def vault(tmp_path: Path) -> Path:
    d = tmp_path / TASKS_DIR
    d.mkdir(parents=True)
    fm = {"title": SLUG, "status": "to-do", "預估🍅": 6, "tags": ["task"]}
    (d / f"{SLUG}.md").write_text(
        f"---\n{yaml.safe_dump(fm, allow_unicode=True, sort_keys=False)}---\n\nbody\n",
        encoding="utf-8",
    )
    return tmp_path


def _fm(vault: Path, slug: str = SLUG) -> dict:
    raw = (vault / TASKS_DIR / f"{slug}.md").read_text(encoding="utf-8")
    return yaml.safe_load(raw.split("---", 2)[1])


def _entry(vault: Path, date_iso: str, slug: str = SLUG) -> dict:
    return next(e for e in _fm(vault, slug)["plan"] if str(e["date"])[:10] == date_iso)


def _ev(eid="evt_1"):
    return CalendarEvent(id=eid, title=SLUG, start="x", end="y", html_link="http://h")


# ── upsert_plan_entry ──────────────────────────────────────────────────────────


class TestUpsertPlanEntry:
    def test_plan_only_no_time(self, vault):
        s, e, _ = upsert_plan_entry(vault, SLUG, day=WED.date(), pomodoros=4)
        assert (s, e) == ("", "")
        entry = _entry(vault, "2026-06-03")
        assert entry["pomodoros"] == 4 and "start" not in entry

    def test_timed_writes_offset_and_derived_end(self, vault):
        s, e, _ = upsert_plan_entry(vault, SLUG, day=WED.date(), pomodoros=4, start=WED)
        # +08:00 offset, end = start + 4×30 = 120 min → 11:00
        assert s == "2026-06-03T09:00:00+08:00"
        assert e == "2026-06-03T11:00:00+08:00"
        entry = _entry(vault, "2026-06-03")
        assert entry["start"] == s and entry["end"] == e

    def test_all_day_writes_date_only_span(self, vault):
        # v3-E: blank-time 排入 → a date-only span (start = day, end = next day, exclusive)
        s, e, _ = upsert_plan_entry(vault, SLUG, day=WED.date(), pomodoros=4, all_day=True)
        assert s == "2026-06-03" and e == "2026-06-04"
        entry = _entry(vault, "2026-06-03")
        assert entry["start"] == "2026-06-03" and entry["end"] == "2026-06-04"

    def test_preserves_done_on_upsert(self, vault):
        upsert_plan_entry(vault, SLUG, day=WED.date(), pomodoros=2)
        # mark it done in the file, then re-upsert
        fm = _fm(vault)
        fm["plan"][0]["done"] = 1
        (vault / TASKS_DIR / f"{SLUG}.md").write_text(
            f"---\n{yaml.safe_dump(fm, allow_unicode=True, sort_keys=False)}---\n\nbody\n",
            encoding="utf-8",
        )
        upsert_plan_entry(vault, SLUG, day=WED.date(), pomodoros=5)
        assert _entry(vault, "2026-06-03")["done"] == 1

    def test_plan_only_edit_keeps_existing_projection(self, vault):
        upsert_plan_entry(vault, SLUG, day=WED.date(), pomodoros=4, start=WED)
        upsert_plan_entry(vault, SLUG, day=WED.date(), pomodoros=4, calendar_event_id="evt_x")
        # a later plan-only re-pomodoro must NOT silently drop the start/event link
        upsert_plan_entry(vault, SLUG, day=WED.date(), pomodoros=6)
        entry = _entry(vault, "2026-06-03")
        assert entry["start"] == "2026-06-03T09:00:00+08:00"
        assert entry["calendar_event_id"] == "evt_x"
        assert entry["pomodoros"] == 6

    def test_weekend_requires_reason(self, vault):
        with pytest.raises(WeekendReasonRequired):
            upsert_plan_entry(vault, SLUG, day=SAT.date(), pomodoros=2, start=SAT)

    def test_read_entry_event_id(self, vault):
        assert read_entry_event_id(vault, SLUG, WED.date()) == ""
        upsert_plan_entry(vault, SLUG, day=WED.date(), pomodoros=2, calendar_event_id="evt_9")
        assert read_entry_event_id(vault, SLUG, WED.date()) == "evt_9"


# ── remove_plan_entry done-safety ────────────────────────────────────────────


class TestRemoveDoneSafe:
    def test_removes_undone_entry(self, vault):
        upsert_plan_entry(vault, SLUG, day=WED.date(), pomodoros=2)
        assert remove_plan_entry(vault, SLUG, WED.date()) is True
        assert _fm(vault).get("plan", []) == []

    def test_keeps_done_entry(self, vault):
        upsert_plan_entry(vault, SLUG, day=WED.date(), pomodoros=2)
        fm = _fm(vault)
        fm["plan"][0]["done"] = 1
        (vault / TASKS_DIR / f"{SLUG}.md").write_text(
            f"---\n{yaml.safe_dump(fm, allow_unicode=True, sort_keys=False)}---\n\nbody\n",
            encoding="utf-8",
        )
        assert remove_plan_entry(vault, SLUG, WED.date()) is False  # protected
        assert len(_fm(vault)["plan"]) == 1
        assert remove_plan_entry(vault, SLUG, WED.date(), force=True) is True  # force overrides


# ── migrate_legacy_projection ────────────────────────────────────────────────


class TestMigrateLegacy:
    def _legacy(self, vault, with_plan_entry: bool):
        fm = {
            "title": SLUG,
            "status": "to-do",
            "預估🍅": 6,
            "scheduled": "2026-06-03T09:00:00",
            "scheduled_end": "2026-06-03T11:00:00",
            "calendar_event_id": "evt_legacy",
        }
        if with_plan_entry:
            fm["plan"] = [{"date": "2026-06-03", "pomodoros": 4, "done": 1, "reason": "x"}]
        (vault / TASKS_DIR / f"{SLUG}.md").write_text(
            f"---\n{yaml.safe_dump(fm, allow_unicode=True, sort_keys=False)}---\n\nbody\n",
            encoding="utf-8",
        )

    def test_folds_into_existing_entry_clears_task_level(self, vault):
        self._legacy(vault, with_plan_entry=True)
        migrate_legacy_projection(vault, SLUG)
        fm = _fm(vault)
        assert "scheduled" not in fm and "calendar_event_id" not in fm
        entry = _entry(vault, "2026-06-03")
        assert entry["calendar_event_id"] == "evt_legacy"
        assert entry["start"] == "2026-06-03T09:00:00+08:00"
        assert entry["done"] == 1 and entry["reason"] == "x"  # preserved

    def test_synthesizes_entry_when_none(self, vault):
        self._legacy(vault, with_plan_entry=False)
        migrate_legacy_projection(vault, SLUG)
        entry = _entry(vault, "2026-06-03")
        assert entry["calendar_event_id"] == "evt_legacy"
        assert entry["start"].endswith("+08:00")

    def test_idempotent_noop_without_legacy(self, vault):
        upsert_plan_entry(vault, SLUG, day=WED.date(), pomodoros=2)
        migrate_legacy_projection(vault, SLUG)  # no legacy keys → no-op
        assert "scheduled" not in _fm(vault)


# ── google_calendar day-window key ───────────────────────────────────────────


class TestDayWindowKey:
    def test_full_day_window(self):
        win = _day_window_from_key("肌酸的妙用 - Pre-production@2026-06-03")
        assert win is not None
        lo, hi = win
        assert lo.isoformat() == "2026-06-03T00:00:00+08:00"
        assert hi.isoformat() == "2026-06-04T00:00:00+08:00"

    def test_undatable_key_none(self):
        assert _day_window_from_key("no-date-here") is None


# ── schedule_entry (mocked GCal) ─────────────────────────────────────────────


class TestScheduleEntry:
    def test_create_links_event(self, vault, monkeypatch):
        seen = {}
        monkeypatch.setattr(
            google_calendar, "create_event", lambda **kw: (seen.update(kw), _ev())[1]
        )
        out = schedule_entry(vault, SLUG, start=WED, pomodoros=4, title=SLUG)
        assert out.calendar_status == CREATED and out.event_id == "evt_1"
        assert seen["idempotency_key"] == f"{SLUG}@2026-06-03"
        entry = _entry(vault, "2026-06-03")
        assert entry["calendar_event_id"] == "evt_1"
        assert entry["start"] == "2026-06-03T09:00:00+08:00"

    def test_same_day_time_change_updates_in_place_not_create(self, vault, monkeypatch):
        # An already-linked entry, re-scheduled to a new TIME on the same day, must
        # PATCH the same event in place (preserve identity) — never create a 2nd one.
        # (A different DAY is a new block — multi-block is additive — so that goes the
        # create path; the explicit per-entry relocate lands in v3-C.)
        upsert_plan_entry(
            vault, SLUG, day=WED.date(), pomodoros=4, start=WED, calendar_event_id="evt_live"
        )
        calls = {"create": 0, "update": 0}
        monkeypatch.setattr(google_calendar, "find_conflicts", lambda s, e: [])
        monkeypatch.setattr(
            google_calendar,
            "create_event",
            lambda **kw: calls.__setitem__("create", calls["create"] + 1),
        )

        def fake_update(eid, **kw):
            calls["update"] += 1
            assert eid == "evt_live"
            assert kw["idempotency_key"] == f"{SLUG}@2026-06-03"  # same day → same key
            return _ev("evt_live")

        monkeypatch.setattr(google_calendar, "update_event", fake_update)
        out = schedule_entry(
            vault, SLUG, start=datetime(2026, 6, 3, 14, 0, 0), pomodoros=2, title=SLUG
        )
        assert out.calendar_status == CREATED and out.event_id == "evt_live"
        assert calls["update"] == 1 and calls["create"] == 0  # in place, no new event
        assert _entry(vault, "2026-06-03")["start"] == "2026-06-03T14:00:00+08:00"

    def test_all_day_creates_event_passes_date_only(self, vault, monkeypatch):
        # v3-E: an all-day 排入 projects a date-only event (no time slot, no conflict
        # pre-check in the create path) and links it.
        seen = {}
        monkeypatch.setattr(
            google_calendar, "create_event", lambda **kw: (seen.update(kw), _ev())[1]
        )
        out = schedule_entry(vault, SLUG, start=WED, pomodoros=4, title=SLUG, all_day=True)
        assert out.calendar_status == CREATED and out.event_id == "evt_1"
        assert seen["start"] == "2026-06-03" and seen["end"] == "2026-06-04"
        assert _entry(vault, "2026-06-03")["calendar_event_id"] == "evt_1"

    def test_conflict_precheck_writes_nothing(self, vault, monkeypatch):
        # v3-I.4 (修修): a clash is detected BEFORE the vault write, so a conflict the
        # user cancels leaves NO plan entry (was: vault-first wrote the plan anyway).
        monkeypatch.setattr(google_calendar, "find_conflicts", lambda s, e: [_ev("a"), _ev("b")])
        called = []
        monkeypatch.setattr(google_calendar, "create_event", lambda **kw: called.append(kw))
        out = schedule_entry(vault, SLUG, start=WED, pomodoros=4, title=SLUG)
        assert out.calendar_status == CONFLICT and out.event_id is None
        assert len(out.conflicts) == 2
        assert called == []  # never reached create_event — bailed at the pre-check
        with pytest.raises((KeyError, StopIteration)):  # no plan entry written for that date
            _entry(vault, "2026-06-03")

    def test_outage_degrades(self, vault, monkeypatch):
        def boom(**kw):
            raise RuntimeError("api down")

        monkeypatch.setattr(google_calendar, "create_event", boom)
        out = schedule_entry(vault, SLUG, start=WED, pomodoros=4, title=SLUG)
        assert out.calendar_status == UNAVAILABLE
        assert _entry(vault, "2026-06-03")["start"]  # plan stands


# ── cancel_entry (mocked GCal) ───────────────────────────────────────────────


class TestCancelEntry:
    def test_drop_plan_removes_entry_and_deletes_event(self, vault, monkeypatch):
        upsert_plan_entry(
            vault, SLUG, day=WED.date(), pomodoros=4, start=WED, calendar_event_id="evt_1"
        )
        deleted = []
        monkeypatch.setattr(google_calendar, "delete_event", lambda eid: deleted.append(eid))
        out = cancel_entry(vault, SLUG, day=WED.date(), drop_plan=True)
        assert out.calendar_status == cs.CANCELLED and deleted == ["evt_1"]
        assert _fm(vault).get("plan", []) == []

    def test_unlink_keeps_entry_clears_projection(self, vault, monkeypatch):
        upsert_plan_entry(
            vault, SLUG, day=WED.date(), pomodoros=4, start=WED, calendar_event_id="evt_1"
        )
        monkeypatch.setattr(google_calendar, "delete_event", lambda eid: None)
        cancel_entry(vault, SLUG, day=WED.date(), drop_plan=False)
        entry = _entry(vault, "2026-06-03")
        assert "calendar_event_id" not in entry and "start" not in entry
        assert entry["pomodoros"] == 4  # entry kept

    def test_drop_plan_done_safe_degrades_to_unlink(self, vault, monkeypatch):
        upsert_plan_entry(
            vault, SLUG, day=WED.date(), pomodoros=4, start=WED, calendar_event_id="evt_1"
        )
        fm = _fm(vault)
        fm["plan"][0]["done"] = 2
        (vault / TASKS_DIR / f"{SLUG}.md").write_text(
            f"---\n{yaml.safe_dump(fm, allow_unicode=True, sort_keys=False)}---\n\nbody\n",
            encoding="utf-8",
        )
        monkeypatch.setattr(google_calendar, "delete_event", lambda eid: None)
        cancel_entry(vault, SLUG, day=WED.date(), drop_plan=True)
        # done entry NOT dropped; projection cleared, history kept
        entry = _entry(vault, "2026-06-03")
        assert entry["done"] == 2 and "calendar_event_id" not in entry


# ── indexer per-entry + dual-read ────────────────────────────────────────────


class TestIndexerPerEntry:
    def test_parses_entry_projection(self, vault):
        upsert_plan_entry(
            vault, SLUG, day=WED.date(), pomodoros=4, start=WED, calendar_event_id="evt_1"
        )
        t = WeeklyIndexer(vault).find_task(SLUG)
        a = next(a for a in t.plan if a.date.isoformat() == "2026-06-03")
        assert a.is_linked and a.event_id == "evt_1"
        assert a.time_label == "09:00"

    def test_all_day_entry_reads_整天_label(self, vault, monkeypatch):
        # v3-E: a date-only (all-day) projection reads "整天" on the chip, and is linked.
        monkeypatch.setattr(google_calendar, "create_event", lambda **kw: _ev("evt_ad"))
        schedule_entry(vault, SLUG, start=WED, pomodoros=2, title=SLUG, all_day=True)
        t = WeeklyIndexer(vault).find_task(SLUG)
        a = next(a for a in t.plan if a.date.isoformat() == "2026-06-03")
        assert a.is_linked and a.event_id == "evt_ad"
        assert a.time_label == "整天"

    def test_dual_read_folds_legacy_for_display(self, vault):
        fm = {
            "title": SLUG,
            "status": "to-do",
            "預估🍅": 6,
            "scheduled": "2026-06-03T09:00:00",
            "scheduled_end": "2026-06-03T11:00:00",
            "calendar_event_id": "evt_legacy",
            "plan": [{"date": "2026-06-03", "pomodoros": 4}],
        }
        (vault / TASKS_DIR / f"{SLUG}.md").write_text(
            f"---\n{yaml.safe_dump(fm, allow_unicode=True, sort_keys=False)}---\n\nbody\n",
            encoding="utf-8",
        )
        t = WeeklyIndexer(vault).find_task(SLUG)
        a = next(a for a in t.plan if a.date.isoformat() == "2026-06-03")
        assert a.is_linked and a.event_id == "evt_legacy"  # surfaced onto the entry (read-only)
