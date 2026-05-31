"""Tests for shared.pomodoro_aggregator — the ADR-039 D3 union read.

Covers the panel-hardened semantics: type==work && completed filter,
activePeriods duration, endTime→Asia/Taipei week assignment, the Sat→Sun
boundary, and same-task overlap merge (union, not sum + warning).
"""

from __future__ import annotations

from datetime import date
from pathlib import Path

from shared.pomodoro_aggregator import (
    parse_dt,
    weekly_actual,
)

TAIPEI_OFFSET = "+08:00"


def _daily(vault: Path, day: str, pomodoros: list[dict]) -> None:
    import yaml

    d = vault / "Journals" / "Daily"
    d.mkdir(parents=True, exist_ok=True)
    fm = yaml.safe_dump({"date": day, "pomodoros": pomodoros}, allow_unicode=True)
    (d / f"{day}.md").write_text(f"---\n{fm}---\n\nbody\n", encoding="utf-8")


def _work(start: str, end: str, *, task: str = "T", completed: bool = True, active=None) -> dict:
    s = {
        "startTime": start,
        "endTime": end,
        "type": "work",
        "completed": completed,
        "taskPath": f"TaskNotes/Tasks/{task}.md",
    }
    if active is not None:
        s["activePeriods"] = active
    return s


def test_parse_dt_handles_z_offset_and_naive():
    assert parse_dt("2026-04-05T07:18:04Z") is not None
    assert parse_dt("2026-04-05T15:43:05+08:00").hour == 15
    # naive is assumed Asia/Taipei
    naive = parse_dt("2026-05-25T07:48:16")
    assert naive is not None and naive.utcoffset().total_seconds() == 8 * 3600
    assert parse_dt("") is None
    assert parse_dt(None) is None


def test_daily_work_sessions_counted_breaks_excluded(tmp_path):
    _daily(
        tmp_path,
        "2026-06-01",
        [
            _work(f"2026-06-01T09:00:00{TAIPEI_OFFSET}", f"2026-06-01T09:25:00{TAIPEI_OFFSET}"),
            _work(f"2026-06-01T09:30:00{TAIPEI_OFFSET}", f"2026-06-01T09:55:00{TAIPEI_OFFSET}"),
            # a short-break must NOT count
            {
                "startTime": f"2026-06-01T09:25:00{TAIPEI_OFFSET}",
                "endTime": f"2026-06-01T09:30:00{TAIPEI_OFFSET}",
                "type": "short-break",
                "completed": True,
                "taskPath": "TaskNotes/Tasks/T.md",
            },
            # incomplete work must NOT count
            _work(
                f"2026-06-01T10:00:00{TAIPEI_OFFSET}",
                f"2026-06-01T10:25:00{TAIPEI_OFFSET}",
                completed=False,
            ),
        ],
    )
    wa = weekly_actual(tmp_path, date(2026, 5, 31), date(2026, 6, 6))
    assert wa.total_pomodoros == 2  # 2 × 25min work; break + incomplete excluded
    assert wa.by_day["2026-06-01"] == 2


def test_activeperiods_used_for_duration(tmp_path):
    # 60-min envelope but only 25 active min (one pause) → 1 🍅, not 2.
    _daily(
        tmp_path,
        "2026-06-02",
        [
            _work(
                f"2026-06-02T09:00:00{TAIPEI_OFFSET}",
                f"2026-06-02T10:00:00{TAIPEI_OFFSET}",
                active=[
                    {
                        "startTime": f"2026-06-02T09:00:00{TAIPEI_OFFSET}",
                        "endTime": f"2026-06-02T09:25:00{TAIPEI_OFFSET}",
                    },
                ],
            )
        ],
    )
    wa = weekly_actual(tmp_path, date(2026, 5, 31), date(2026, 6, 6))
    assert wa.total_pomodoros == 1


def test_timeentries_folded_in_and_attributed(tmp_path):
    # No daily sessions; one 50-min Bridge timeEntry on task X → 2 🍅.
    wa = weekly_actual(
        tmp_path,
        date(2026, 5, 31),
        date(2026, 6, 6),
        task_time_entries=[
            (
                "肌酸 - Pre",
                [
                    {
                        "startTime": f"2026-06-03T14:00:00{TAIPEI_OFFSET}",
                        "endTime": f"2026-06-03T14:50:00{TAIPEI_OFFSET}",
                    }
                ],
            )
        ],
    )
    assert wa.total_pomodoros == 2
    assert wa.by_task["肌酸 - Pre"] == 2
    assert wa.by_day["2026-06-03"] == 2


def test_out_of_range_session_excluded_by_endtime(tmp_path):
    _daily(
        tmp_path,
        "2026-05-30",
        [_work(f"2026-05-30T09:00:00{TAIPEI_OFFSET}", f"2026-05-30T09:25:00{TAIPEI_OFFSET}")],
    )
    wa = weekly_actual(tmp_path, date(2026, 5, 31), date(2026, 6, 6))
    assert wa.total_pomodoros == 0  # 5/30 is the prior week (Sat)


def test_sat_to_sun_boundary_counts_by_endtime(tmp_path):
    # Session starts 23:50 Sat 6/6, ends 00:15 Sun 6/7 → belongs to the NEXT
    # week (endTime in Asia/Taipei), so excluded from 5/31–6/6.
    _daily(
        tmp_path,
        "2026-06-06",
        [_work(f"2026-06-06T23:50:00{TAIPEI_OFFSET}", f"2026-06-07T00:15:00{TAIPEI_OFFSET}")],
    )
    wa = weekly_actual(tmp_path, date(2026, 5, 31), date(2026, 6, 6))
    assert wa.total_pomodoros == 0


def test_same_task_overlap_merged_not_summed(tmp_path):
    # Same physical session logged by BOTH timers (daily + timeEntries),
    # overlapping → merge to union (25 min = 1 🍅), with a warning. Not 2.
    _daily(
        tmp_path,
        "2026-06-01",
        [
            _work(
                f"2026-06-01T09:00:00{TAIPEI_OFFSET}",
                f"2026-06-01T09:25:00{TAIPEI_OFFSET}",
                task="T",
            )
        ],
    )
    wa = weekly_actual(
        tmp_path,
        date(2026, 5, 31),
        date(2026, 6, 6),
        task_time_entries=[
            (
                "T",
                [
                    {
                        "startTime": f"2026-06-01T09:05:00{TAIPEI_OFFSET}",
                        "endTime": f"2026-06-01T09:30:00{TAIPEI_OFFSET}",
                    }
                ],
            )
        ],
    )
    assert wa.total_pomodoros == 1  # union 09:00–09:30 = 30min → 1, not 2
    assert wa.overlap_warnings  # flagged
