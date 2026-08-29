"""Tests for shared.weekly_writer — plan[] add/remove, sync_scheduled, constraints."""

from __future__ import annotations

import re
from datetime import date, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest
import yaml

from shared.weekly_writer import (
    CALENDAR_BLOCK_MINUTES_PER_POMODORO,
    TaskNotFoundError,
    WeekendReasonRequired,
    WeeklyConflictError,
    WeeklyWriteError,
    _warn_if_sync_conflict_sibling,
    add_plan_entry,
    log_time_entry,
    read_task_body,
    read_task_schedule,
    remove_plan_entry,
    reschedule_task_block,
    schedule_task_block,
    set_plan_entry_done,
    set_task_done,
    sync_scheduled_to_next_plan,
    task_file_token,
    unlink_calendar_block,
    unschedule_task_block,
    weekly_file_token,
    write_task_body,
    write_weekly,
)

TAIPEI = ZoneInfo("Asia/Taipei")

TASKS_DIR = "TaskNotes/Tasks"
WEEKLY_DIR = "Journals/Weekly"
WK = "2026-05-31"  # a real start-Sunday (W23)
WK_START = date(2026, 5, 31)
WK_END = date(2026, 6, 6)


def _read_weekly_fm(vault: Path, file_key: str = WK) -> dict:
    return _read_fm(vault / WEEKLY_DIR / f"{file_key}.md")


def _read_weekly_body(vault: Path, file_key: str = WK) -> str:
    return _read_body(vault / WEEKLY_DIR / f"{file_key}.md")


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

    def test_manual_blocks_stack_non_overlapping(self, vault):
        """Three +1 clicks at ~the same instant must stack into 3 non-overlapping
        blocks — else the per-task union merge collapses them to one 🍅 (regression:
        知識衛星試錄課程影片, where 12:50:01/03/11 all overlapped → counted as 1)."""
        from shared.pomodoro_aggregator import task_actual

        slug = "test-task"
        p = _make_task(vault, slug, {"title": "Test"})
        now = datetime(2026, 6, 16, 13, 15, tzinfo=TAIPEI)
        for _ in range(3):  # rapid manual clicks, all ending "now"
            log_time_entry(vault, slug, now - timedelta(minutes=25), now, manual=True)

        entries = _read_fm(p)["timeEntries"]
        assert len(entries) == 3
        spans = sorted(
            (datetime.fromisoformat(e["startTime"]), datetime.fromisoformat(e["endTime"]))
            for e in entries
        )
        for (_s1, e1), (s2, _e2) in zip(spans, spans[1:]):
            assert e1 <= s2  # no overlap
        # union (the task-page count) now agrees with the raw sum: 3 🍅
        assert task_actual(vault, slug, entries).total_pomodoros == 3

    def test_manual_block_with_no_prior_entries_keeps_window(self, vault):
        """First manual click with nothing logged that day keeps its [start, end]."""
        slug = "test-task"
        p = _make_task(vault, slug, {"title": "Test"})
        now = datetime(2026, 6, 16, 13, 15, tzinfo=TAIPEI)
        log_time_entry(vault, slug, now - timedelta(minutes=25), now, manual=True)
        e = _read_fm(p)["timeEntries"][0]
        assert "2026-06-16T12:50:00" in e["startTime"]
        assert "2026-06-16T13:15:00" in e["endTime"]


# ── write_weekly (ADR-040 Slice 2 — Journals/Weekly 🟡) ────────────────────────


class TestWriteWeekly:
    def test_creates_from_template(self, vault):
        write_weekly(vault, WK, start_date=WK_START, end_date=WK_END)
        fm = _read_weekly_fm(vault)
        assert _date_isoformat(fm["start_date"]) == "2026-05-31"
        assert _date_isoformat(fm["end_date"]) == "2026-06-06"
        assert fm["status"] == "planning"
        assert fm["top3"] == [] and fm["next3"] == []
        assert fm["targets"] == {}  # A3: not auto-filled
        body = _read_weekly_body(vault)
        assert "## ✨ Highlight" in body
        assert "## 隨手筆記" in body

    def test_create_requires_dates(self, vault):
        with pytest.raises(WeeklyWriteError):
            write_weekly(vault, WK, frontmatter={"status": "active"})

    def test_persists_top3_and_targets_intent(self, vault):
        write_weekly(vault, WK, start_date=WK_START, end_date=WK_END)
        write_weekly(
            vault,
            WK,
            frontmatter={
                "top3": ["[[肌酸的妙用]]", "[[電子報]]"],
                "targets": {"pomodoro": 35, "ufo": 5},
            },
        )
        fm = _read_weekly_fm(vault)
        assert fm["top3"] == ["[[肌酸的妙用]]", "[[電子報]]"]
        assert fm["targets"] == {"pomodoro": 35, "ufo": 5}

    def test_targets_merge_not_replace(self, vault):
        """PR#811 audit B2: a partial targets payload merges into the stored
        dict — saving only ufo must not drop a stored pomodoro goal."""
        write_weekly(vault, WK, start_date=WK_START, end_date=WK_END)
        write_weekly(vault, WK, frontmatter={"targets": {"pomodoro": 30}})
        write_weekly(vault, WK, frontmatter={"targets": {"ufo": 4}})
        assert _read_weekly_fm(vault)["targets"] == {"pomodoro": 30, "ufo": 4}


class TestWriteTaskBody:
    """ADR-040 A7 (Slice W) — the scoped task note-body writing surface."""

    _FM = {"title": "測試任務", "status": "to-do", "預估🍅": 6, "tags": ["task"]}

    def test_replaces_body_preserves_frontmatter(self, vault):
        _make_task(vault, "測試任務", self._FM, body="舊內文")
        write_task_body(vault, "測試任務", "# 本週電子報\n\n第一段草稿。")
        path = vault / TASKS_DIR / "測試任務.md"
        assert "# 本週電子報" in _read_body(path)
        assert "舊內文" not in _read_body(path)
        fm = _read_fm(path)  # frontmatter untouched (body-only — A7)
        assert fm["title"] == "測試任務" and fm["預估🍅"] == 6 and fm["tags"] == ["task"]

    def test_read_task_body_roundtrips(self, vault):
        _make_task(vault, "測試任務", self._FM, body="原始草稿內容")
        assert "原始草稿內容" in read_task_body(vault, "測試任務")

    def test_read_missing_task_raises(self, vault):
        with pytest.raises(WeeklyWriteError):
            read_task_body(vault, "不存在")

    def test_body_if_match_conflict(self, vault):
        _make_task(vault, "測試任務", self._FM, body="a")
        token = task_file_token(vault, "測試任務")
        write_task_body(vault, "測試任務", "b")  # someone else writes in between
        with pytest.raises(WeeklyConflictError):
            write_task_body(vault, "測試任務", "c", expected_token=token)

    def test_token_changes_with_content(self, vault):
        _make_task(vault, "測試任務", self._FM, body="a")
        t0 = task_file_token(vault, "測試任務")
        new = write_task_body(vault, "測試任務", "different", expected_token=t0)
        assert new != t0  # content hash advanced

    # ── PR#812 panel regressions — byte-splice frontmatter preservation ──────
    def test_frontmatter_preserved_byte_for_byte(self, vault):
        """Codex+Gemini: a body save must keep the frontmatter prefix verbatim —
        comments, quoting, and key order survive (no PyYAML re-serialisation)."""
        d = vault / TASKS_DIR
        d.mkdir(parents=True, exist_ok=True)
        original = '---\ntitle: 測試任務  # 不要動我\nstatus: "to-do"\n預估🍅: 6\n---\n\n舊內文\n'
        path = d / "測試任務.md"
        path.write_text(original, encoding="utf-8")
        write_task_body(vault, "測試任務", "新內文")
        raw = path.read_text(encoding="utf-8")
        assert raw.startswith(
            '---\ntitle: 測試任務  # 不要動我\nstatus: "to-do"\n預估🍅: 6\n---\n'
        )  # comment + quotes intact
        assert "新內文" in raw and "舊內文" not in raw

    def test_crlf_frontmatter_preserved(self, vault):
        """A CRLF-line-ending task file keeps its CRLF frontmatter — the old
        LF-only path would have dropped the whole frontmatter (Gemini cascade)."""
        d = vault / TASKS_DIR
        d.mkdir(parents=True, exist_ok=True)
        path = d / "測試任務.md"
        path.write_bytes("---\r\ntitle: 測試任務\r\n---\r\nold\r\n".encode("utf-8"))
        write_task_body(vault, "測試任務", "new body")
        raw = path.read_text(encoding="utf-8")
        assert "title: 測試任務" in raw  # frontmatter NOT lost
        assert "new body" in raw

    def test_no_frontmatter_fails_closed(self, vault):
        """A file with no parseable frontmatter must NOT be reinterpreted as body
        and clobbered — it raises instead (the catastrophic-loss guard)."""
        d = vault / TASKS_DIR
        d.mkdir(parents=True, exist_ok=True)
        path = d / "測試任務.md"
        path.write_text("just a plain note, no frontmatter\n", encoding="utf-8")
        with pytest.raises(WeeklyWriteError):
            write_task_body(vault, "測試任務", "x")
        assert "just a plain note" in path.read_text(encoding="utf-8")  # untouched

    def test_body_verbatim_roundtrip(self, vault):
        """The body is stored verbatim (leading/trailing blank lines preserved),
        only a single trailing newline is ensured."""
        _make_task(vault, "測試任務", self._FM, body="x")
        body = "\n# 標題\n\n段落\n\n- a\n- b\n"
        write_task_body(vault, "測試任務", body)
        assert read_task_body(vault, "測試任務") == body

    def test_rejects_non_allowlisted_frontmatter(self, vault):
        write_weekly(vault, WK, start_date=WK_START, end_date=WK_END)
        with pytest.raises(WeeklyWriteError):
            write_weekly(vault, WK, frontmatter={"pomodoros_cache": 12})

    def test_rejects_unknown_status(self, vault):
        write_weekly(vault, WK, start_date=WK_START, end_date=WK_END)
        with pytest.raises(WeeklyWriteError):
            write_weekly(vault, WK, frontmatter={"status": "bogus"})

    def test_writes_prose_sections_verbatim(self, vault):
        write_weekly(vault, WK, start_date=WK_START, end_date=WK_END)
        write_weekly(
            vault,
            WK,
            sections={"✨ Highlight": "出了第一支影片 🎉", "隨手筆記": "下週想試 75 分 block。"},
        )
        body = _read_weekly_body(vault)
        assert "出了第一支影片 🎉" in body
        assert "下週想試 75 分 block。" in body
        # idempotent replace — re-writing the same heading doesn't duplicate it
        write_weekly(vault, WK, sections={"✨ Highlight": "改寫過的 highlight"})
        body2 = _read_weekly_body(vault)
        assert body2.count("## ✨ Highlight") == 1
        assert "改寫過的 highlight" in body2
        assert "出了第一支影片" not in body2

    def test_multi_section_write_preserves_all_headings_in_order(self, vault):
        """Regression: a greedy ``\\s*\\n`` heading match used to swallow the blank
        line and replace the NEXT heading — dropping 😔 Lowlight and reordering
        🙏 感恩. All five sections must survive in template order."""
        write_weekly(vault, WK, start_date=WK_START, end_date=WK_END)
        write_weekly(
            vault,
            WK,
            sections={
                "✨ Highlight": "出片了",
                "😔 Lowlight": "",  # empty section must not vanish
                "📚 學到的東西": "",
                "🙏 感恩": "謝謝團隊",
                "隨手筆記": "備註",
            },
        )
        body = _read_weekly_body(vault)
        assert re.findall(r"^## .*$", body, re.MULTILINE) == [
            "## ✨ Highlight",
            "## 😔 Lowlight",
            "## 📚 學到的東西",
            "## 🙏 感恩",
            "## 隨手筆記",
        ]
        assert "出片了" in body and "謝謝團隊" in body and "備註" in body

    def test_inserts_missing_section_heading(self, vault):
        # create a file with no body sections, then write to a brand-new heading
        path = vault / WEEKLY_DIR / f"{WK}.md"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("---\nstatus: active\n---\n\nbody\n", encoding="utf-8")
        write_weekly(vault, WK, sections={"隨手筆記": "新增的筆記"})
        body = _read_weekly_body(vault)
        assert "## 隨手筆記" in body and "新增的筆記" in body

    def test_preserves_hand_written_frontmatter_and_body(self, vault):
        path = vault / WEEKLY_DIR / f"{WK}.md"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            "---\nstatus: active\nweek: 2026-W23\nmy_field: keep\n---\n\n"
            "## ✨ Highlight\n\nold\n\n## 自己加的段落\n\n手寫內容\n",
            encoding="utf-8",
        )
        write_weekly(vault, WK, sections={"✨ Highlight": "new"})
        fm = _read_weekly_fm(vault)
        body = _read_weekly_body(vault)
        assert fm["week"] == "2026-W23"  # non-allowlisted key untouched
        assert fm["my_field"] == "keep"
        assert "手寫內容" in body  # 修修's own section survives
        assert "## 自己加的段落" in body

    def test_if_match_conflict(self, vault):
        write_weekly(vault, WK, start_date=WK_START, end_date=WK_END)
        token = weekly_file_token(vault, WK)
        # someone else writes in between → token goes stale
        write_weekly(vault, WK, frontmatter={"status": "active"})
        with pytest.raises(WeeklyConflictError):
            write_weekly(vault, WK, frontmatter={"status": "reviewed"}, expected_token=token)

    def test_if_match_expected_absent_then_created_conflict(self, vault):
        # page loaded with no file (token ""), but the file appears before the write
        write_weekly(vault, WK, start_date=WK_START, end_date=WK_END)
        with pytest.raises(WeeklyConflictError):
            write_weekly(vault, WK, start_date=WK_START, end_date=WK_END, expected_token="")

    def test_if_match_matching_token_succeeds(self, vault):
        write_weekly(vault, WK, start_date=WK_START, end_date=WK_END)
        token = weekly_file_token(vault, WK)
        new_token = write_weekly(vault, WK, frontmatter={"status": "active"}, expected_token=token)
        assert new_token != token  # content changed → hash changed
        assert _read_weekly_fm(vault)["status"] == "active"

    def test_token_empty_when_absent(self, vault):
        assert weekly_file_token(vault, WK) == ""

    def test_atomic_leaves_no_tmp(self, vault):
        write_weekly(vault, WK, start_date=WK_START, end_date=WK_END)
        d = vault / WEEKLY_DIR
        assert list(d.glob("*.tmp")) == []


class TestScheduleTaskBlock:
    """ADR-041 41a — authoritative vault write for a scheduled calendar block."""

    _FM = {"title": "測試任務", "status": "to-do", "預估🍅": 6, "tags": ["task"]}

    def test_writes_plan_and_projection_fields(self, vault):
        _make_task(vault, "測試任務", self._FM)
        start = datetime(2026, 6, 3, 9, 0, 0)  # Wed (weekday)
        sched, sched_end, tok = schedule_task_block(vault, "測試任務", start=start, pomodoros=4)
        fm = _read_fm(vault / TASKS_DIR / "測試任務.md")
        # authoritative plan[] (D1)
        assert fm["plan"][0]["date"] == "2026-06-03"
        assert fm["plan"][0]["pomodoros"] == 4
        # projection fields (D2) — end = start + 4 × 30 min = 11:00
        assert sched == "2026-06-03T09:00:00"
        assert sched_end == "2026-06-03T11:00:00"
        assert _date_isoformat(fm["scheduled"]).startswith("2026-06-03T09:00")
        assert _date_isoformat(fm["scheduled_end"]).startswith("2026-06-03T11:00")
        assert "calendar_event_id" not in fm  # not provided → not written
        assert tok == task_file_token(vault, "測試任務")

    def test_block_length_is_30_min_per_pomodoro(self, vault):
        assert CALENDAR_BLOCK_MINUTES_PER_POMODORO == 30
        _make_task(vault, "測試任務", self._FM)
        _, sched_end, _ = schedule_task_block(
            vault, "測試任務", start=datetime(2026, 6, 3, 14, 0), pomodoros=1
        )
        assert sched_end == "2026-06-03T14:30:00"  # 1🍅 = 30 min wall-clock

    def test_calendar_event_id_written_when_given(self, vault):
        _make_task(vault, "測試任務", self._FM)
        schedule_task_block(
            vault,
            "測試任務",
            start=datetime(2026, 6, 3, 9, 0),
            pomodoros=2,
            calendar_event_id="evt_abc123",
        )
        fm = _read_fm(vault / TASKS_DIR / "測試任務.md")
        assert fm["calendar_event_id"] == "evt_abc123"

    def test_preserves_done_on_same_date_upsert(self, vault):
        existing = [{"date": "2026-06-03", "pomodoros": 2, "done": 1}]
        _make_task(vault, "測試任務", {**self._FM, "plan": existing})
        schedule_task_block(vault, "測試任務", start=datetime(2026, 6, 3, 9, 0), pomodoros=4)
        fm = _read_fm(vault / TASKS_DIR / "測試任務.md")
        assert len(fm["plan"]) == 1
        assert fm["plan"][0]["pomodoros"] == 4
        assert fm["plan"][0]["done"] == 1  # preserved

    def test_other_frontmatter_preserved(self, vault):
        _make_task(vault, "測試任務", {**self._FM, "custom": "keep"})
        schedule_task_block(vault, "測試任務", start=datetime(2026, 6, 3, 9, 0), pomodoros=2)
        fm = _read_fm(vault / TASKS_DIR / "測試任務.md")
        # 預估🍅 is NOT "untouched frontmatter" — scheduling syncs it to the plan sum
        # (修修 option 1). Non-scheduling keys (custom/title) stay put.
        assert fm["custom"] == "keep" and fm["title"] == "測試任務"

    def test_estimate_syncs_to_scheduled_plan(self, vault):
        """修修 option 1: scheduling writes 預估🍅 = sum(plan pomodoros), so the task
        page (預估🍅) and dashboard/bullet (plan sum) can't diverge. _FM starts at 6;
        scheduling a 3🍅 block drops the estimate to 3 (regression: 寫電子報 showed 4 vs 3)."""
        _make_task(vault, "測試任務", self._FM)  # 預估🍅: 6
        schedule_task_block(vault, "測試任務", start=datetime(2026, 6, 3, 9, 0), pomodoros=3)
        fm = _read_fm(vault / TASKS_DIR / "測試任務.md")
        assert fm["plan"][0]["pomodoros"] == 3
        assert fm["預估🍅"] == 3

    def test_weekend_without_reason_raises(self, vault):
        _make_task(vault, "測試任務", self._FM)
        with pytest.raises(WeekendReasonRequired):
            # Sat 6/6 — weekend block needs a reason
            schedule_task_block(vault, "測試任務", start=datetime(2026, 6, 6, 9, 0), pomodoros=2)

    def test_weekend_with_reason_ok(self, vault):
        _make_task(vault, "測試任務", self._FM)
        schedule_task_block(
            vault, "測試任務", start=datetime(2026, 6, 6, 9, 0), pomodoros=2, reason="截稿衝刺"
        )
        fm = _read_fm(vault / TASKS_DIR / "測試任務.md")
        assert fm["plan"][0]["reason"] == "截稿衝刺"

    def test_pomodoros_below_one_raises(self, vault):
        _make_task(vault, "測試任務", self._FM)
        with pytest.raises(WeeklyWriteError):
            schedule_task_block(vault, "測試任務", start=datetime(2026, 6, 3, 9, 0), pomodoros=0)

    def test_missing_task_raises(self, vault):
        with pytest.raises(TaskNotFoundError):
            schedule_task_block(vault, "不存在", start=datetime(2026, 6, 3, 9, 0), pomodoros=2)

    def test_if_match_conflict(self, vault):
        _make_task(vault, "測試任務", self._FM)
        token = task_file_token(vault, "測試任務")
        schedule_task_block(vault, "測試任務", start=datetime(2026, 6, 3, 9, 0), pomodoros=2)
        with pytest.raises(WeeklyConflictError):
            schedule_task_block(
                vault,
                "測試任務",
                start=datetime(2026, 6, 4, 9, 0),
                pomodoros=2,
                expected_token=token,
            )


class TestRescheduleTaskBlock:
    """ADR-041 41d D8 — relocate a scheduled block; keep calendar_event_id."""

    _LINKED = {
        "title": "測試任務",
        "status": "to-do",
        "預估🍅": 6,
        "scheduled": "2026-06-03T09:00:00",
        "scheduled_end": "2026-06-03T10:00:00",
        "calendar_event_id": "evt_live",
        "plan": [{"date": "2026-06-03", "pomodoros": 2}],
        "tags": ["task"],
    }

    def test_moves_plan_entry_and_rewrites_projection(self, vault):
        _make_task(vault, "測試任務", self._LINKED)
        sched, sched_end, tok, eid = reschedule_task_block(
            vault, "測試任務", start=datetime(2026, 6, 4, 14, 0), pomodoros=3
        )
        fm = _read_fm(vault / TASKS_DIR / "測試任務.md")
        # old date's entry dropped, new one added — no orphan (D8)
        dates = [e["date"] for e in fm["plan"]]
        assert dates == ["2026-06-04"]
        assert fm["plan"][0]["pomodoros"] == 3
        assert sched == "2026-06-04T14:00:00"
        assert sched_end == "2026-06-04T15:30:00"  # 3🍅 × 30 min
        assert eid == "evt_live"  # event id preserved (caller PATCHes it)
        assert fm["calendar_event_id"] == "evt_live"
        assert tok == task_file_token(vault, "測試任務")

    def test_same_date_time_change_keeps_single_entry(self, vault):
        _make_task(vault, "測試任務", self._LINKED)
        reschedule_task_block(vault, "測試任務", start=datetime(2026, 6, 3, 15, 0), pomodoros=2)
        fm = _read_fm(vault / TASKS_DIR / "測試任務.md")
        assert len(fm["plan"]) == 1 and fm["plan"][0]["date"] == "2026-06-03"
        assert _date_isoformat(fm["scheduled"]).startswith("2026-06-03T15:00")

    def test_old_entry_with_done_work_is_preserved(self, vault):
        fm0 = {**self._LINKED, "plan": [{"date": "2026-06-03", "pomodoros": 2, "done": 1}]}
        _make_task(vault, "測試任務", fm0)
        reschedule_task_block(vault, "測試任務", start=datetime(2026, 6, 5, 9, 0), pomodoros=2)
        fm = _read_fm(vault / TASKS_DIR / "測試任務.md")
        dates = sorted(e["date"] for e in fm["plan"])
        assert dates == ["2026-06-03", "2026-06-05"]  # done-work history kept

    def test_unlinked_task_returns_empty_event_id(self, vault):
        fm0 = {"title": "測試任務", "status": "to-do", "預估🍅": 4, "tags": ["task"]}
        _make_task(vault, "測試任務", fm0)
        _, _, _, eid = reschedule_task_block(
            vault, "測試任務", start=datetime(2026, 6, 4, 9, 0), pomodoros=2
        )
        assert eid == ""  # not linked — orchestrator degrades to a fresh create

    def test_weekend_without_reason_raises(self, vault):
        _make_task(vault, "測試任務", self._LINKED)
        with pytest.raises(WeekendReasonRequired):
            reschedule_task_block(vault, "測試任務", start=datetime(2026, 6, 6, 9, 0), pomodoros=2)

    def test_if_match_conflict(self, vault):
        _make_task(vault, "測試任務", self._LINKED)
        with pytest.raises(WeeklyConflictError):
            reschedule_task_block(
                vault,
                "測試任務",
                start=datetime(2026, 6, 4, 9, 0),
                pomodoros=2,
                expected_token="stale",
            )


class TestUnlinkCalendarBlock:
    """ADR-041 41d D9 — 移出行事曆: clear projection, KEEP plan[]."""

    _LINKED = {
        "title": "測試任務",
        "status": "to-do",
        "預估🍅": 6,
        "scheduled": "2026-06-03T09:00:00",
        "scheduled_end": "2026-06-03T10:00:00",
        "calendar_event_id": "evt_live",
        "plan": [{"date": "2026-06-03", "pomodoros": 2}],
        "tags": ["task"],
    }

    def test_clears_projection_keeps_plan(self, vault):
        _make_task(vault, "測試任務", self._LINKED)
        old_eid, tok = unlink_calendar_block(vault, "測試任務")
        fm = _read_fm(vault / TASKS_DIR / "測試任務.md")
        assert old_eid == "evt_live"
        assert "scheduled" not in fm and "scheduled_end" not in fm
        assert "calendar_event_id" not in fm
        assert fm["plan"] == [{"date": "2026-06-03", "pomodoros": 2}]  # KEPT (D9)
        assert tok == task_file_token(vault, "測試任務")

    def test_missing_task_raises(self, vault):
        with pytest.raises(TaskNotFoundError):
            unlink_calendar_block(vault, "不存在")

    def test_if_match_conflict(self, vault):
        _make_task(vault, "測試任務", self._LINKED)
        with pytest.raises(WeeklyConflictError):
            unlink_calendar_block(vault, "測試任務", expected_token="stale")


class TestUnscheduleTaskBlock:
    """ADR-041 41d D9 — 取消排程: drop the scheduled-date plan[] entry + clear."""

    _LINKED = {
        "title": "測試任務",
        "status": "to-do",
        "預估🍅": 6,
        "scheduled": "2026-06-03T09:00:00",
        "scheduled_end": "2026-06-03T10:00:00",
        "calendar_event_id": "evt_live",
        "plan": [
            {"date": "2026-06-03", "pomodoros": 2},
            {"date": "2026-06-05", "pomodoros": 1},
        ],
        "tags": ["task"],
    }

    def test_drops_scheduled_date_entry_and_clears(self, vault):
        _make_task(vault, "測試任務", self._LINKED)
        old_eid, _ = unschedule_task_block(vault, "測試任務")
        fm = _read_fm(vault / TASKS_DIR / "測試任務.md")
        assert old_eid == "evt_live"
        assert [e["date"] for e in fm["plan"]] == ["2026-06-05"]  # only 6/3 dropped
        assert "scheduled" not in fm and "calendar_event_id" not in fm

    def test_keeps_done_work_entry(self, vault):
        fm0 = {
            **self._LINKED,
            "plan": [{"date": "2026-06-03", "pomodoros": 2, "done": 1}],
        }
        _make_task(vault, "測試任務", fm0)
        unschedule_task_block(vault, "測試任務")
        fm = _read_fm(vault / TASKS_DIR / "測試任務.md")
        assert [e["date"] for e in fm["plan"]] == ["2026-06-03"]  # done → preserved
        assert "scheduled" not in fm  # but the projection is still cleared

    def test_if_match_conflict(self, vault):
        _make_task(vault, "測試任務", self._LINKED)
        with pytest.raises(WeeklyConflictError):
            unschedule_task_block(vault, "測試任務", expected_token="stale")


class TestReadTaskSchedule:
    def test_returns_raw_projection_fields(self, vault):
        _make_task(
            vault,
            "測試任務",
            {
                "title": "測試任務",
                "scheduled": "2026-06-03T09:00:00",
                "scheduled_end": "2026-06-03T10:00:00",
                "calendar_event_id": "evt_live",
                "tags": ["task"],
            },
        )
        sched = read_task_schedule(vault, "測試任務")
        assert sched["scheduled"].startswith("2026-06-03T09:00")
        assert sched["calendar_event_id"] == "evt_live"

    def test_missing_task_returns_none(self, vault):
        assert read_task_schedule(vault, "不存在") is None

    def test_unlinked_task_blank_event_id(self, vault):
        _make_task(vault, "測試任務", {"title": "測試任務", "tags": ["task"]})
        sched = read_task_schedule(vault, "測試任務")
        assert sched["calendar_event_id"] == ""


# ── set_task_done (#2 dashboard checkbox) ──────────────────────────────────────


class TestSetTaskDone:
    def test_mark_done_sets_status(self, vault):
        p = _make_task(vault, "t1", {"title": "T", "status": "to-do"})
        out = set_task_done(vault, "t1", True)
        assert out == "done"
        assert _read_fm(p)["status"] == "done"

    def test_unmark_reopens_to_do(self, vault):
        p = _make_task(vault, "t1", {"title": "T", "status": "done"})
        out = set_task_done(vault, "t1", False)
        assert out == "to-do"
        assert _read_fm(p)["status"] == "to-do"

    def test_unmark_clears_truthy_checkmark(self, vault):
        # the indexer treats ✅:true as done too — un-marking must clear it
        p = _make_task(vault, "t1", {"title": "T", "status": "done", "✅": True})
        set_task_done(vault, "t1", False)
        fm = _read_fm(p)
        assert fm["status"] == "to-do"
        assert fm["✅"] is False

    def test_mark_done_preserves_plan_and_body(self, vault):
        p = _make_task(
            vault,
            "t1",
            {"title": "T", "status": "to-do", "plan": [{"date": "2026-06-01", "pomodoros": 2}]},
            body="draft notes",
        )
        set_task_done(vault, "t1", True)
        fm = _read_fm(p)
        # 修修 2026-08-29: the whole-task checkbox also ticks its day slices (so the
        # daily cards agree) — the entry's own data is otherwise untouched.
        assert fm["plan"] == [{"date": "2026-06-01", "pomodoros": 2, "done": True}]
        assert "draft notes" in _read_body(p)

    def test_unmark_clears_day_slices(self, vault):
        p = _make_task(
            vault,
            "t1",
            {
                "title": "T",
                "status": "done",
                "plan": [
                    {"date": "2026-06-01", "pomodoros": 2, "done": True},
                    {"date": "2026-06-02", "pomodoros": 1, "done": True},
                ],
            },
        )
        set_task_done(vault, "t1", False)
        assert [e.get("done") for e in _read_fm(p)["plan"]] == [None, None]

    def test_missing_task_raises(self, vault):
        with pytest.raises(TaskNotFoundError):
            set_task_done(vault, "nope", True)


class TestSetPlanEntryDoneRollup:
    """修修 2026-08-29: 日卡打勾要跟任務列表同步 — the day slices roll up to the
    task's own status (all days done ⇒ task done; any day re-opened ⇒ task open)."""

    def test_only_day_done_marks_task_done(self, vault):
        p = _make_task(
            vault,
            "t1",
            {"title": "T", "status": "to-do", "plan": [{"date": "2026-06-01", "pomodoros": 2}]},
        )
        set_plan_entry_done(vault, "t1", date(2026, 6, 1), True)
        fm = _read_fm(p)
        assert fm["plan"][0]["done"] is True
        assert fm["status"] == "done"

    def test_partial_days_keep_task_open(self, vault):
        p = _make_task(
            vault,
            "t1",
            {
                "title": "T",
                "status": "to-do",
                "plan": [
                    {"date": "2026-06-01", "pomodoros": 2},
                    {"date": "2026-06-02", "pomodoros": 2},
                ],
            },
        )
        set_plan_entry_done(vault, "t1", date(2026, 6, 1), True)
        assert _read_fm(p)["status"] == "to-do"
        set_plan_entry_done(vault, "t1", date(2026, 6, 2), True)
        assert _read_fm(p)["status"] == "done"
        set_plan_entry_done(vault, "t1", date(2026, 6, 1), False)
        assert _read_fm(p)["status"] == "to-do"

    def test_rollup_clears_truthy_checkmark(self, vault):
        p = _make_task(
            vault,
            "t1",
            {
                "title": "T",
                "status": "done",
                "✅": True,
                "plan": [{"date": "2026-06-01", "pomodoros": 2, "done": True}],
            },
        )
        set_plan_entry_done(vault, "t1", date(2026, 6, 1), False)
        fm = _read_fm(p)
        assert fm["status"] == "to-do" and fm["✅"] is False

    def test_task_without_plan_keeps_status(self, vault):
        p = _make_task(vault, "t1", {"title": "T", "status": "to-do"})
        set_plan_entry_done(vault, "t1", date(2026, 6, 1), True)
        assert _read_fm(p)["status"] == "to-do"  # nothing to roll up


class TestWarnIfSyncConflictSibling:
    """2026-08-10 事故：Obsidian Sync 有時會把剛寫入的檔案改名成
    ``*.sync-conflict-*.md``，寫入本身仍回報成功。這個 helper 在同一次寫入
    後立刻檢查有沒有這種 sibling 冒出來，發現了就記 warning log（不擋、不拋）。"""

    def test_logs_warning_when_conflict_sibling_exists(self, vault, caplog):
        p = vault / TASKS_DIR / "寫電子報.md"
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text("---\ntitle: 寫電子報\n---\n", encoding="utf-8")
        (p.parent / "寫電子報.sync-conflict-20260617-160416-YJZV5NL.md").write_text(
            "---\ntitle: 寫電子報\n---\n", encoding="utf-8"
        )
        with caplog.at_level("WARNING", logger="nakama.weekly_writer"):
            _warn_if_sync_conflict_sibling(p)
        assert "sync-conflict sibling" in caplog.text

    def test_no_warning_when_clean(self, vault, caplog):
        p = vault / TASKS_DIR / "寫稿.md"
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text("---\ntitle: 寫稿\n---\n", encoding="utf-8")
        with caplog.at_level("WARNING", logger="nakama.weekly_writer"):
            _warn_if_sync_conflict_sibling(p)
        assert caplog.text == ""
