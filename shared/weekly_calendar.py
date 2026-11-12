"""Week time-grid model for the Bridge 任務 → 行事曆 tab (修修 2026-11-12).

Pure data: turns a :class:`~shared.weekly_indexer.WeeklyView` into the seven day
columns the calendar renders — timed blocks positioned by minute-of-day (with
side-by-side lanes when blocks overlap), date-only entries in the 整天 row, and
the 還沒排時間 tray. No I/O, so the layout rules are unit-testable without a vault.

Source of truth stays the task ``plan[]`` (ADR-041 v3): one entry per task per day,
``start``/``end`` ISO-with-offset for a timed block, a date-only ``start`` for an
all-day event, neither for a legacy plan-only entry (shown in 整天 too).
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import date, datetime, timedelta
from typing import Optional

from shared.weekly_indexer import TAIPEI, WeeklyTask, WeeklyView
from shared.weekly_writer import CALENDAR_BLOCK_MINUTES_PER_POMODORO

# Visible hour window. 06:00 so the Monday-morning 電子報 block is on screen
# (修修 2026-11-12); widened automatically when a block falls outside it.
DEFAULT_HOUR_START = 6
DEFAULT_HOUR_END = 22

_ZH_WEEKDAYS = ("日", "一", "二", "三", "四", "五", "六")
_DAY_MINUTES = 24 * 60


@dataclass(frozen=True)
class CalBlock:
    """One timed plan entry, positioned inside its day column."""

    slug: str
    title: str
    date: str  # ISO date of the plan entry (the drag source date)
    start_min: int  # minutes after midnight (Asia/Taipei)
    end_min: int
    pomodoros: int
    done: bool
    important: bool  # 本週重要任務 → 深橘
    lane: int = 0  # 0-based column inside an overlap cluster
    lanes: int = 1  # how many side-by-side columns the cluster needs

    @property
    def start_label(self) -> str:
        return _hhmm(self.start_min)

    @property
    def end_label(self) -> str:
        return _hhmm(self.end_min)


@dataclass(frozen=True)
class CalChip:
    """A date-only entry (all-day event or plan-only) in the 整天 row."""

    slug: str
    title: str
    date: str
    pomodoros: int
    done: bool
    important: bool
    draggable: bool  # False for a bare task-level ``scheduled`` (no plan entry to move)


@dataclass(frozen=True)
class CalDay:
    date: str
    weekday_zh: str
    day: int
    is_today: bool
    is_weekend: bool
    chips: tuple[CalChip, ...]
    blocks: tuple[CalBlock, ...]


@dataclass(frozen=True)
class CalTrayItem:
    slug: str
    title: str
    pomodoros: int  # length a drop creates (the task estimate, clamped 1–20)


@dataclass(frozen=True)
class WeekCalendar:
    days: tuple[CalDay, ...]
    hour_start: int
    hour_end: int
    tray: tuple[CalTrayItem, ...]
    today: str  # ISO — "" when today is outside this week

    @property
    def hours(self) -> list[int]:
        return list(range(self.hour_start, self.hour_end + 1))


def _hhmm(minutes: int) -> str:
    minutes = max(0, min(_DAY_MINUTES, minutes))
    return f"{minutes // 60:02d}:{minutes % 60:02d}"


def _minute_of_day(iso: str, day: date) -> Optional[int]:
    """Minutes after midnight on ``day`` for an ISO datetime; clamps a value on an
    adjacent day to the day's edge. None when unparseable / date-only."""
    if not iso or "T" not in iso:
        return None
    try:
        dt = datetime.fromisoformat(iso)
    except ValueError:
        return None
    if dt.tzinfo is not None:
        dt = dt.astimezone(TAIPEI)
    if dt.date() < day:
        return 0
    if dt.date() > day:
        return _DAY_MINUTES
    return dt.hour * 60 + dt.minute


def _assign_lanes(blocks: list[CalBlock]) -> list[CalBlock]:
    """Greedy interval partitioning: overlapping blocks sit side by side. Each
    connected overlap cluster shares one ``lanes`` count so widths line up."""
    ordered = sorted(blocks, key=lambda b: (b.start_min, -b.end_min, b.title))
    out: list[CalBlock] = []
    cluster: list[tuple[CalBlock, int]] = []
    lane_ends: list[int] = []
    cluster_end = -1

    def flush() -> None:
        n = max(lane for _, lane in cluster) + 1
        out.extend(replace(b, lane=lane, lanes=n) for b, lane in cluster)

    for b in ordered:
        if cluster and b.start_min >= cluster_end:
            flush()
            cluster, lane_ends, cluster_end = [], [], -1
        for i, end in enumerate(lane_ends):
            if end <= b.start_min:
                lane_ends[i] = b.end_min
                cluster.append((b, i))
                break
        else:
            lane_ends.append(b.end_min)
            cluster.append((b, len(lane_ends) - 1))
        cluster_end = max(cluster_end, b.end_min)
    if cluster:
        flush()
    return out


def _day_items(
    t: WeeklyTask, d: date, important: frozenset
) -> tuple[Optional[CalBlock], Optional[CalChip]]:
    entry = t.entry_on(d)
    is_important = t.slug in important
    if entry is None:
        if not t.plan and t.scheduled == d:  # bare scheduled-only task (no plan entry)
            chip = CalChip(
                t.slug, t.name, d.isoformat(), t.est_pomodoros, t.done, is_important, False
            )
            return None, chip
        return None, None
    done = bool(entry.done) or t.done
    start = _minute_of_day(entry.start, d)
    if start is None:  # all-day event or legacy plan-only entry
        chip = CalChip(t.slug, t.name, d.isoformat(), entry.pomodoros, done, is_important, True)
        return None, chip
    end = _minute_of_day(entry.end, d)
    if end is None or end <= start:
        end = start + max(1, entry.pomodoros) * CALENDAR_BLOCK_MINUTES_PER_POMODORO
    end = min(end, _DAY_MINUTES)
    block = CalBlock(
        slug=t.slug,
        title=t.name,
        date=d.isoformat(),
        start_min=start,
        end_min=max(end, start + 15),
        pomodoros=entry.pomodoros,
        done=done,
        important=is_important,
    )
    return block, None


def build_week_calendar(view: WeeklyView) -> WeekCalendar:
    wk = view.week
    today = view.today_iso
    days: list[CalDay] = []
    earliest, latest = DEFAULT_HOUR_START * 60, DEFAULT_HOUR_END * 60
    for i in range(7):
        d = wk.start + timedelta(days=i)
        blocks: list[CalBlock] = []
        chips: list[CalChip] = []
        for t in view.tasks:
            block, chip = _day_items(t, d, view.important_slugs)
            if block is not None:
                blocks.append(block)
                earliest = min(earliest, block.start_min)
                latest = max(latest, block.end_min)
            if chip is not None:
                chips.append(chip)
        chips.sort(key=lambda c: (c.done, not c.important, c.title))
        days.append(
            CalDay(
                date=d.isoformat(),
                weekday_zh=_ZH_WEEKDAYS[i],
                day=d.day,
                is_today=d.isoformat() == today,
                is_weekend=i in (0, 6),
                chips=tuple(chips),
                blocks=tuple(_assign_lanes(blocks)),
            )
        )
    hour_start = earliest // 60
    hour_end = min(24, -(-latest // 60))  # ceil
    tray = tuple(
        CalTrayItem(t.slug, t.name, min(20, max(1, t.est_pomodoros or 2)))
        for t in view.all_unscheduled
    )
    in_week = any(day.is_today for day in days)
    return WeekCalendar(
        days=tuple(days),
        hour_start=hour_start,
        hour_end=hour_end,
        tray=tray,
        today=today if in_week else "",
    )
