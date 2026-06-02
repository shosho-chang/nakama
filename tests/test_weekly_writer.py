"""Tests for shared.weekly_writer — plan[] add/remove, sync_scheduled, constraints."""

from __future__ import annotations

import re
from datetime import date, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest
import yaml

from shared.weekly_writer import (
    WeekendReasonRequired,
    WeeklyWriteError,
    add_plan_entry,
    log_time_entry,
    remove_plan_entry,
    sync_scheduled_to_next_plan,
)

TAIPEI = ZoneInfo("Asia/Taipei")

TASKS_DIR = "TaskNotes/Tasks"


# ── helpers ───────────────────────────────────────────────────────────────────


def _make_task(vault: Path, slug: str, fm: dict, body: str = "") -> Path:
    d = vault / TASKS_DIR
    d.mkdir(parents=True, exist_ok=True)
    fm_str = yaml.safe_dump(fm, allow_unicode=True, sort_keys=False)
    content = f"---\n{fm_str}---\n"
    if body:
        content += f"\n{body}\n"
    path = d / f"{slug}.md"
    path.write_text(content, encoding="utf-8")
    return path


_FM_RE = re.compile(r"^---\n(.*?)\n---\n?(.*)$", re.DOTALL)


def _read_fm(path: Path) -> dict:
    raw = path.read_text(encoding="utf-8")
    m = _FM_RE.match(raw)
    assert m, f"no frontmatter in {path}"
    return yaml.safe_load(m.group(1)) or {}


def _read_body(path: Path) -> str:
    raw = path.read_text(encoding="utf-8")
    m = _FM_RE.match(raw)
    return m.group(2) if m else raw


def _date_isoformat(v) -> str:
    """Normalise a yaml-parsed date (may be date obj or str) to ISO string."""
    return v.isoformat() if hasattr(v, "isoformat") else str(v)


@pytest.fixture
def vault(tmp_path: Path) -> Path:
    return tmp_path


# ── add_plan_entry ────────────────────────────────────────────────────────────


class TestAddPlanEntry:
    def test_adds_entry_to_empty_plan(self, vault):
        slug = "test-task"
        p = _make_task(vault, slug, {"title": "Test", "status": "to-do"})
        add_plan_entry(vault, slug, date(2026, 6, 1), pomodoros=2)
        fm = _read_fm(p)
        assert len(fm["plan"]) == 1
        assert fm["plan"][0]["pomodoros"] == 2

    def test_adds_entry_when_plan_key_absent(self, vault):
        slug = "test-task"
        p = _make_task(vault, slug, {"title": "Test"})
        add_plan_entry(vault, slug, date(2026, 6, 2), pomodoros=1)
        fm = _read_fm(p)
        assert "plan" in fm
        assert fm["plan"][0]["pomodoros"] == 1

    def test_adds_entry_with_reason(self, vault):
        slug = "test-task"
        p = _make_task(vault, slug, {"title": "Test"})
        add_plan_entry(vault, slug, date(2026, 6, 1), pomodoros=3, reason="prep for launch")
        fm = _read_fm(p)
        assert fm["plan"][0]["reason"] == "prep for launch"

    def test_updates_existing_entry_for_same_date(self, vault):
        slug = "test-task"
        existing = [{"date": "2026-06-01", "pomodoros": 2, "done": 1}]
        p = _make_task(vault, slug, {"title": "Test", "plan": existing})
        add_plan_entry(vault, slug, date(2026, 6, 1), pomodoros=4)
        fm = _read_fm(p)
        assert len(fm["plan"]) == 1
        assert fm["plan"][0]["pomodoros"] == 4
        assert fm["plan"][0].get("done") == 1  # done field preserved

    def test_appends_entry_for_new_date(self, vault):
        slug = "test-task"
        existing = [{"date": "2026-06-01", "pomodoros": 2}]
        p = _make_task(vault, slug, {"title": "Test", "plan": existing})
        add_plan_entry(vault, slug, date(2026, 6, 3), pomodoros=1)
        fm = _read_fm(p)
        assert len(fm["plan"]) == 2

    def test_saturday_without_reason_raises(self, vault):
        slug = "test-task"
        _make_task(vault, slug, {"title": "Test"})
        with pytest.raises(WeekendReasonRequired):
            add_plan_entry(vault, slug, date(2026, 6, 6), pomodoros=2)  # Sat

    def test_sunday_without_reason_raises(self, vault):
        slug = "test-task"
        _make_task(vault, slug, {"title": "Test"})
        with pytest.raises(WeekendReasonRequired):
            add_plan_entry(vault, slug, date(2026, 5, 31), pomodoros=1)  # Sun

    def test_saturday_with_reason_succeeds(self, vault):
        slug = "test-task"
        p = _make_task(vault, slug, {"title": "Test"})
        add_plan_entry(vault, slug, date(2026, 6, 6), pomodoros=2, reason="截稿衝刺")
        fm = _read_fm(p)
        assert fm["plan"][0]["reason"] == "截稿衝刺"

    def test_sunday_with_reason_succeeds(self, vault):
        slug = "test-task"
        p = _make_task(vault, slug, {"title": "Test"})
        add_plan_entry(vault, slug, date(2026, 5, 31), pomodoros=2, reason="緊急截稿")
        fm = _read_fm(p)
        assert fm["plan"][0]["reason"] == "緊急截稿"

    def test_monday_no_reason_required(self, vault):
        slug = "test-task"
        p = _make_task(vault, slug, {"title": "Test"})
        add_plan_entry(vault, slug, date(2026, 6, 1), pomodoros=2)
        fm = _read_fm(p)
        assert len(fm["plan"]) == 1
        assert "reason" not in fm["plan"][0]

    def test_raises_if_task_missing(self, vault):
        with pytest.raises(WeeklyWriteError):
            add_plan_entry(vault, "no-such-task", date(2026, 6, 1), pomodoros=2)

    def test_scheduled_not_modified(self, vault):
        slug = "test-task"
        p = _make_task(vault, slug, {"title": "Test", "scheduled": "2026-05-01T10:00:00"})
        add_plan_entry(vault, slug, date(2026, 6, 3), pomodoros=2)
        fm = _read_fm(p)
        sched = fm.get("scheduled")
        assert sched is not None
        assert "2026-05-01" in _date_isoformat(sched)

    def test_unknown_keys_preserved(self, vault):
        slug = "test-task"
        p = _make_task(
            vault,
            slug,
            {"title": "Test", "custom_field": "preserved", "tags": ["a", "b"]},
        )
        add_plan_entry(vault, slug, date(2026, 6, 2), pomodoros=1)
        fm = _read_fm(p)
        assert fm["custom_field"] == "preserved"
        assert fm["tags"] == ["a", "b"]

    def test_body_preserved(self, vault):
        slug = "test-task"
        p = _make_task(vault, slug, {"title": "Test"}, body="## Notes\n\nSome body content.")
        add_plan_entry(vault, slug, date(2026, 6, 2), pomodoros=1)
        body = _read_body(p)
        assert "Some body content." in body

    def test_atomic_write_leaves_no_tmp_files(self, vault):
        slug = "test-task"
        p = _make_task(vault, slug, {"title": "Test"})
        add_plan_entry(vault, slug, date(2026, 6, 2), pomodoros=1)
        tmp_files = list(p.parent.glob("*.tmp"))
        assert tmp_files == []
        fm = _read_fm(p)
        assert "plan" in fm

    def test_weekend_raises_before_touching_file(self, vault):
        """WeekendReasonRequired must be raised before any write."""
        slug = "test-task"
        p = _make_task(vault, slug, {"title": "Test"})
        original_content = p.read_text(encoding="utf-8")
        with pytest.raises(WeekendReasonRequired):
            add_plan_entry(vault, slug, date(2026, 5, 31), pomodoros=1)
        assert p.read_text(encoding="utf-8") == original_content


# ── remove_plan_entry ─────────────────────────────────────────────────────────


class TestRemovePlanEntry:
    def test_removes_existing_entry(self, vault):
        slug = "test-task"
        existing = [
            {"date": "2026-06-01", "pomodoros": 2},
            {"date": "2026-06-02", "pomodoros": 3},
        ]
        p = _make_task(vault, slug, {"title": "Test", "plan": existing})
        result = remove_plan_entry(vault, slug, date(2026, 6, 1))
        assert result is True
        fm = _read_fm(p)
        assert len(fm["plan"]) == 1
        assert _date_isoformat(fm["plan"][0]["date"]) == "2026-06-02"

    def test_returns_false_when_no_matching_entry(self, vault):
        slug = "test-task"
        _make_task(vault, slug, {"title": "Test", "plan": [{"date": "2026-06-01", "pomodoros": 2}]})
        result = remove_plan_entry(vault, slug, date(2026, 6, 5))
        assert result is False

    def test_returns_false_on_empty_plan(self, vault):
        slug = "test-task"
        _make_task(vault, slug, {"title": "Test"})
        result = remove_plan_entry(vault, slug, date(2026, 6, 1))
        assert result is False

    def test_removes_all_entries_leaving_empty_list(self, vault):
        slug = "test-task"
        existing = [{"date": "2026-06-01", "pomodoros": 2}]
        p = _make_task(vault, slug, {"title": "Test", "plan": existing})
        remove_plan_entry(vault, slug, date(2026, 6, 1))
        fm = _read_fm(p)
        assert fm["plan"] == []

    def test_scheduled_not_modified(self, vault):
        slug = "test-task"
        existing_plan = [{"date": "2026-06-01", "pomodoros": 2}]
        p = _make_task(
            vault,
            slug,
            {"title": "Test", "scheduled": "2026-06-01", "plan": existing_plan},
        )
        remove_plan_entry(vault, slug, date(2026, 6, 1))
        fm = _read_fm(p)
        assert fm.get("scheduled") is not None

    def test_unknown_keys_preserved(self, vault):
        slug = "test-task"
        existing = [{"date": "2026-06-01", "pomodoros": 2}]
        p = _make_task(vault, slug, {"title": "Test", "extra": "data", "plan": existing})
        remove_plan_entry(vault, slug, date(2026, 6, 1))
        fm = _read_fm(p)
        assert fm["extra"] == "data"

    def test_raises_if_task_missing(self, vault):
        with pytest.raises(WeeklyWriteError):
            remove_plan_entry(vault, "no-such-task", date(2026, 6, 1))

    def test_file_not_written_when_no_entry(self, vault):
        """File should remain unchanged when there's nothing to remove."""
        slug = "test-task"
        p = _make_task(vault, slug, {"title": "Test"})
        original = p.read_text(encoding="utf-8")
        remove_plan_entry(vault, slug, date(2026, 6, 1))
        assert p.read_text(encoding="utf-8") == original


# ── sync_scheduled_to_next_plan ───────────────────────────────────────────────


class TestSyncScheduledToNextPlan:
    def test_sets_scheduled_to_earliest_future_date(self, vault):
        slug = "test-task"
        existing = [
            {"date": "2026-06-05", "pomodoros": 1},
            {"date": "2026-06-03", "pomodoros": 2},
        ]
        p = _make_task(vault, slug, {"title": "Test", "plan": existing})
        result = sync_scheduled_to_next_plan(vault, slug, today=date(2026, 6, 1))
        assert result == date(2026, 6, 3)
        fm = _read_fm(p)
        assert _date_isoformat(fm["scheduled"]) == "2026-06-03"

    def test_returns_none_when_all_entries_past(self, vault):
        slug = "test-task"
        existing = [{"date": "2026-05-01", "pomodoros": 2}]
        _make_task(vault, slug, {"title": "Test", "scheduled": "2026-05-01", "plan": existing})
        result = sync_scheduled_to_next_plan(vault, slug, today=date(2026, 6, 1))
        assert result is None

    def test_returns_none_when_plan_empty(self, vault):
        slug = "test-task"
        _make_task(vault, slug, {"title": "Test"})
        result = sync_scheduled_to_next_plan(vault, slug, today=date(2026, 6, 1))
        assert result is None

    def test_today_counts_as_future(self, vault):
        slug = "test-task"
        existing = [{"date": "2026-06-01", "pomodoros": 2}]
        _make_task(vault, slug, {"title": "Test", "plan": existing})
        result = sync_scheduled_to_next_plan(vault, slug, today=date(2026, 6, 1))
        assert result == date(2026, 6, 1)

    def test_does_not_write_when_no_future_entries(self, vault):
        """No write should occur when returning None (file unchanged)."""
        slug = "test-task"
        p = _make_task(vault, slug, {"title": "Test"})
        original = p.read_text(encoding="utf-8")
        sync_scheduled_to_next_plan(vault, slug, today=date(2026, 6, 1))
        assert p.read_text(encoding="utf-8") == original

    def test_unknown_keys_preserved_after_sync(self, vault):
        slug = "test-task"
        existing = [{"date": "2026-06-03", "pomodoros": 2}]
        p = _make_task(vault, slug, {"title": "Test", "tags": ["x"], "plan": existing})
        sync_scheduled_to_next_plan(vault, slug, today=date(2026, 6, 1))
        fm = _read_fm(p)
        assert fm["tags"] == ["x"]

    def test_add_plan_does_not_change_scheduled(self, vault):
        """Integration: add_plan_entry never touches scheduled; sync does."""
        slug = "test-task"
        p = _make_task(vault, slug, {"title": "Test", "scheduled": "2026-05-01"})
        add_plan_entry(vault, slug, date(2026, 6, 5), pomodoros=2)
        fm_mid = _read_fm(p)
        # scheduled still the original value after add
        assert "2026-05-01" in _date_isoformat(fm_mid["scheduled"])
        # now sync
        sync_scheduled_to_next_plan(vault, slug, today=date(2026, 6, 1))
        fm_final = _read_fm(p)
        assert _date_isoformat(fm_final["scheduled"]) == "2026-06-05"

    def test_raises_if_task_missing(self, vault):
        with pytest.raises(WeeklyWriteError):
            sync_scheduled_to_next_plan(vault, "no-such-task", today=date(2026, 6, 1))


# ── log_time_entry (ADR-039 E) ────────────────────────────────────────────────


class TestLogTimeEntry:
    def _span(self, minutes: int) -> tuple[datetime, datetime]:
        start = datetime(2026, 6, 1, 9, 0, tzinfo=TAIPEI)
        return start, start + timedelta(minutes=minutes)

    def test_appends_to_empty(self, vault):
        slug = "test-task"
        p = _make_task(vault, slug, {"title": "Test"})
        start, end = self._span(25)
        log_time_entry(vault, slug, start, end)
        fm = _read_fm(p)
        assert len(fm["timeEntries"]) == 1
        e = fm["timeEntries"][0]
        assert e["mode"] == "pomodoro"
        assert "2026-06-01T09:00:00" in e["startTime"]
        assert "2026-06-01T09:25:00" in e["endTime"]

    def test_evidence_fields_recorded(self, vault):
        """ADR-040 A2: each entry stores planned/actual minutes + completed."""
        slug = "test-task"
        p = _make_task(vault, slug, {"title": "Test"})
        start, end = self._span(18)  # finished a 25-min block early at 18 min
        log_time_entry(vault, slug, start, end, mode="pomodoro")
        e = _read_fm(p)["timeEntries"][0]
        assert e["planned_minutes"] == 25
        assert e["actual_minutes"] == 18
        assert e["completed"] is True
        assert "manual" not in e  # timer evidence, not a manual claim

    def test_planned_minutes_override_and_manual_flag(self, vault):
        slug = "test-task"
        p = _make_task(vault, slug, {"title": "Test"})
        start, end = self._span(75)
        log_time_entry(vault, slug, start, end, mode="deep", planned_minutes=75, manual=True)
        e = _read_fm(p)["timeEntries"][0]
        assert e["planned_minutes"] == 75
        assert e["actual_minutes"] == 75
        assert e["manual"] is True

    def test_not_completed_flag(self, vault):
        slug = "test-task"
        p = _make_task(vault, slug, {"title": "Test"})
        start, end = self._span(40)
        log_time_entry(vault, slug, start, end, mode="deep", completed=False)
        assert _read_fm(p)["timeEntries"][0]["completed"] is False

    def test_appends_to_existing(self, vault):
        slug = "test-task"
        existing = [
            {"startTime": "2026-05-30T10:00:00+08:00", "endTime": "2026-05-30T10:25:00+08:00"}
        ]
        p = _make_task(vault, slug, {"title": "Test", "timeEntries": existing})
        start, end = self._span(25)
        log_time_entry(vault, slug, start, end)
        fm = _read_fm(p)
        assert len(fm["timeEntries"]) == 2

    def test_deep_mode_recorded(self, vault):
        slug = "test-task"
        p = _make_task(vault, slug, {"title": "Test"})
        start, end = self._span(75)
        log_time_entry(vault, slug, start, end, mode="deep")
        fm = _read_fm(p)
        assert fm["timeEntries"][0]["mode"] == "deep"

    def test_unknown_mode_raises(self, vault):
        slug = "test-task"
        _make_task(vault, slug, {"title": "Test"})
        start, end = self._span(25)
        with pytest.raises(WeeklyWriteError):
            log_time_entry(vault, slug, start, end, mode="bogus")

    def test_non_positive_span_raises(self, vault):
        slug = "test-task"
        _make_task(vault, slug, {"title": "Test"})
        start, _ = self._span(25)
        with pytest.raises(WeeklyWriteError):
            log_time_entry(vault, slug, start, start)

    def test_absurd_span_raises(self, vault):
        slug = "test-task"
        _make_task(vault, slug, {"title": "Test"})
        start, end = self._span(7 * 60)  # 7h > 6h cap
        with pytest.raises(WeeklyWriteError):
            log_time_entry(vault, slug, start, end)

    def test_other_keys_and_body_preserved(self, vault):
        slug = "test-task"
        p = _make_task(
            vault, slug, {"title": "Test", "預估🍅": 3, "custom": "keep"}, body="## Notes\n\nbody."
        )
        start, end = self._span(25)
        log_time_entry(vault, slug, start, end)
        fm = _read_fm(p)
        assert fm["預估🍅"] == 3
        assert fm["custom"] == "keep"
        assert "body." in _read_body(p)

    def test_atomic_leaves_no_tmp(self, vault):
        slug = "test-task"
        p = _make_task(vault, slug, {"title": "Test"})
        start, end = self._span(25)
        log_time_entry(vault, slug, start, end)
        assert list(p.parent.glob("*.tmp")) == []

    def test_raises_if_task_missing(self, vault):
        start, end = self._span(25)
        with pytest.raises(WeeklyWriteError):
            log_time_entry(vault, "no-such-task", start, end)
