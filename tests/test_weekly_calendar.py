"""shared.weekly_calendar — the 任務 → 行事曆 tab's week time-grid model (修修 2026-11-12)."""

from __future__ import annotations

from datetime import date
from pathlib import Path

import pytest

import shared.weekly_indexer as wi
from shared.weekly_calendar import CalBlock, _assign_lanes, build_week_calendar

WEEK = date(2026, 5, 31)  # Sun 5/31 .. Sat 6/6


def _task(vault: Path, name: str, fm: str) -> None:
    d = vault / "TaskNotes" / "Tasks"
    d.mkdir(parents=True, exist_ok=True)
    (d / f"{name}.md").write_text(
        f"---\ntitle: {name}\nstatus: to-do\n{fm}tags:\n  - task\n---\n", encoding="utf-8"
    )


@pytest.fixture
def cal(tmp_path, monkeypatch):
    monkeypatch.setattr(wi, "today_taipei", lambda: date(2026, 6, 3))
    # timed, written by our writer (ISO with T)
    _task(
        tmp_path,
        "寫稿",
        "plan:\n  - date: 2026-06-03\n    pomodoros: 2\n"
        "    start: '2026-06-03T09:00:00+08:00'\n    end: '2026-06-03T10:00:00+08:00'\n",
    )
    # timed, after a yaml round-trip: unquoted → datetime → must still be timed
    _task(
        tmp_path,
        "剪片",
        "plan:\n  - date: 2026-06-03\n    pomodoros: 2\n"
        "    start: 2026-06-03 09:30:00+08:00\n    end: 2026-06-03 10:30:00+08:00\n",
    )
    # early block widens the visible window
    _task(
        tmp_path,
        "晨跑",
        "plan:\n  - date: 2026-06-04\n    pomodoros: 1\n"
        "    start: '2026-06-04T05:00:00+08:00'\n    end: '2026-06-04T05:30:00+08:00'\n"
        "    done: true\n",
    )
    # all-day event (date-only start)
    _task(
        tmp_path,
        "報稅",
        "plan:\n  - date: 2026-06-04\n    pomodoros: 1\n"
        "    start: '2026-06-04'\n    end: '2026-06-05'\n",
    )
    # bare scheduled-only (no plan entry) → chip that cannot be dragged
    _task(tmp_path, "回信", "scheduled: 2026-06-05\n")
    # 本週重要 via the transitional weekly_priority flag
    _task(
        tmp_path,
        "錄訪談",
        "weekly_priority: 2026-05-31\nplan:\n  - date: 2026-06-05\n    pomodoros: 4\n"
        "    start: '2026-06-05T10:00:00+08:00'\n    end: '2026-06-05T12:00:00+08:00'\n",
    )
    # nothing scheduled → tray
    _task(tmp_path, "更新作品集", "預估🍅: 3\n")
    view = wi.WeeklyIndexer(tmp_path).view(wi.week_for_date(WEEK))
    return build_week_calendar(view)


def _day(cal, iso):
    return next(d for d in cal.days if d.date == iso)


def test_seven_days_sunday_first_with_today(cal):
    assert [d.date for d in cal.days][0] == "2026-05-31"
    assert len(cal.days) == 7
    assert cal.today == "2026-06-03"
    assert _day(cal, "2026-06-03").is_today
    assert [d.is_weekend for d in cal.days] == [True, False, False, False, False, False, True]


def test_timed_blocks_positioned_by_minute_including_space_separated_yaml(cal):
    wed = _day(cal, "2026-06-03")
    by = {b.title: b for b in wed.blocks}
    assert (by["寫稿"].start_min, by["寫稿"].end_min) == (9 * 60, 10 * 60)
    assert (by["剪片"].start_min, by["剪片"].end_min) == (9 * 60 + 30, 10 * 60 + 30)
    assert wed.chips == ()


def test_overlapping_blocks_share_lanes(cal):
    by = {b.title: b for b in _day(cal, "2026-06-03").blocks}
    assert by["寫稿"].lanes == by["剪片"].lanes == 2
    assert {by["寫稿"].lane, by["剪片"].lane} == {0, 1}


def test_all_day_and_scheduled_only_go_to_the_all_day_row(cal):
    thu = _day(cal, "2026-06-04")
    assert [c.title for c in thu.chips] == ["報稅"]
    assert thu.chips[0].draggable is True
    fri = _day(cal, "2026-06-05")
    assert [(c.title, c.draggable) for c in fri.chips] == [("回信", False)]


def test_done_and_important_flags(cal):
    early = _day(cal, "2026-06-04").blocks[0]
    assert early.title == "晨跑" and early.done is True
    rec = _day(cal, "2026-06-05").blocks[0]
    assert rec.title == "錄訪談" and rec.important is True


def test_hour_window_widens_for_early_block(cal):
    assert cal.hour_start == 5  # 05:00 晨跑 < default 06:00
    assert cal.hour_end == 22


def test_unscheduled_tasks_fill_the_tray(cal):
    tray = {t.title: t for t in cal.tray}
    assert "更新作品集" in tray
    assert tray["更新作品集"].pomodoros == 3


def test_today_blank_when_outside_week(tmp_path, monkeypatch):
    monkeypatch.setattr(wi, "today_taipei", lambda: date(2026, 7, 1))
    (tmp_path / "TaskNotes" / "Tasks").mkdir(parents=True)
    view = wi.WeeklyIndexer(tmp_path).view(wi.week_for_date(WEEK))
    c = build_week_calendar(view)
    assert c.today == ""
    assert (c.hour_start, c.hour_end) == (6, 22)


def _b(title, s, e):
    return CalBlock(title, title, "2026-06-03", s, e, 1, False, False)


def test_lanes_reset_between_separate_clusters():
    out = {
        b.title: b for b in _assign_lanes([_b("a", 60, 120), _b("b", 90, 150), _b("c", 200, 260)])
    }
    assert out["a"].lanes == out["b"].lanes == 2
    assert out["c"].lanes == 1 and out["c"].lane == 0


def test_back_to_back_blocks_do_not_overlap():
    out = _assign_lanes([_b("a", 60, 120), _b("b", 120, 180)])
    assert all(b.lanes == 1 and b.lane == 0 for b in out)
