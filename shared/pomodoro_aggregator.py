"""Pomodoro aggregator — unified "actual 🍅" read over two vault session logs.

ADR-039 D3. "Actual pomodoros" is NOT stored; it is computed on read from the
two timestamped session logs the vault already produces:

  1. Obsidian TaskNotes pomodoro  → ``Journals/Daily/{YYYY-MM-DD}.md`` frontmatter
     ``pomodoros: [{startTime, endTime, plannedDuration, type, taskPath,
     completed, activePeriods}]`` (config ``pomodoroStorageLocation: daily-notes``).
  2. Bridge Web timer / +1🍅      → ``TaskNotes/Tasks/*.md`` frontmatter
     ``timeEntries: [{startTime, endTime}]`` (ADR-031 D6).

The two logs are written by two different timers; in normal use they never
record the same physical session. But "two stores" is NOT proof of "two
sessions" (ADR-039 D3, Codex §3): a session could in principle be logged to
both. So we normalise every session to an interval, **merge overlapping
intervals per task** (union duration, not sum), and only then convert minutes
→ 🍅. Exact-duplicate intervals collapse for free; genuine overlaps are merged
and flagged via :attr:`WeeklyActual.overlap_warnings` rather than double-counted.

Time semantics (ADR-039 D3 / panel #5):
  - count only ``type == "work"`` && ``completed`` from daily ``pomodoros[]``;
  - use ``activePeriods`` for duration when present (pauses must not inflate);
  - assign a session to a day/week by its **``endTime`` in Asia/Taipei**
    (NOT the daily-note filename — sessions can cross midnight);
  - 1 🍅 = 25 min (TaskNotes config authority); the week total floors
    ``total_minutes // 25``.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any, Iterable, Optional
from zoneinfo import ZoneInfo

import yaml

TAIPEI = ZoneInfo("Asia/Taipei")
POMODORO_MINUTES = 25  # TaskNotes config authority (ADR-039 D3)

_DAILY_DIR = "Journals/Daily"
_FRONTMATTER_RE = re.compile(r"^---\n(.*?)\n---\n?(.*)$", re.DOTALL)


@dataclass(frozen=True)
class Interval:
    """One counted focus session, normalised across both source logs."""

    task_key: str  # task slug from taskPath / task file stem; "" when unattributed
    start: datetime  # Asia/Taipei
    end: datetime  # Asia/Taipei
    source: str  # "daily" | "timeEntries"

    @property
    def minutes(self) -> float:
        return max(0.0, (self.end - self.start).total_seconds() / 60.0)


@dataclass(frozen=True)
class WeeklyActual:
    total_pomodoros: int
    total_minutes: float
    by_day: dict[str, int]  # ISO date -> 🍅 (floored per day)
    by_task: dict[str, int]  # task_key -> 🍅 (floored per task)
    overlap_warnings: tuple[str, ...] = ()


# ── timestamp parsing ────────────────────────────────────────────────────────


def parse_dt(value: object) -> Optional[datetime]:
    """Parse an ISO-8601 timestamp into an Asia/Taipei-aware datetime.

    Handles trailing ``Z`` (UTC), explicit offsets (``+08:00``), and naive
    timestamps (assumed already Asia/Taipei — matches how TaskNotes writes
    local naive ``scheduled`` values). Returns None on anything unparseable.
    """
    if isinstance(value, datetime):
        dt = value
    elif isinstance(value, str):
        s = value.strip()
        if not s:
            return None
        if s.endswith("Z"):
            s = s[:-1] + "+00:00"
        try:
            dt = datetime.fromisoformat(s)
        except ValueError:
            return None
    else:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=TAIPEI)
    return dt.astimezone(TAIPEI)


def _session_minutes(session: dict[str, Any], start: datetime, end: datetime) -> float:
    """Active minutes for a daily ``pomodoros[]`` entry.

    Prefer summing ``activePeriods`` (pause-aware); fall back to the
    start→end envelope when absent.
    """
    periods = session.get("activePeriods")
    if isinstance(periods, list) and periods:
        total = 0.0
        for p in periods:
            if not isinstance(p, dict):
                continue
            ps, pe = parse_dt(p.get("startTime")), parse_dt(p.get("endTime"))
            if ps and pe and pe > ps:
                total += (pe - ps).total_seconds() / 60.0
        if total > 0:
            return total
    return max(0.0, (end - start).total_seconds() / 60.0)


# ── interval collection ──────────────────────────────────────────────────────


def _read_frontmatter(path: Path) -> dict[str, Any]:
    try:
        raw = path.read_text(encoding="utf-8")
    except OSError:
        return {}
    m = _FRONTMATTER_RE.match(raw)
    if not m:
        return {}
    try:
        fm = yaml.safe_load(m.group(1)) or {}
    except yaml.YAMLError:
        return {}
    return fm if isinstance(fm, dict) else {}


def _task_key_from_path(task_path: object) -> str:
    """Derive a task slug from a ``taskPath`` like ``TaskNotes/Tasks/X.md``."""
    if not isinstance(task_path, str) or not task_path:
        return ""
    return Path(task_path).stem


def collect_daily_intervals(
    vault_root: Path,
    start: date,
    end: date,
    *,
    allowed_task_keys: Optional[set[str]] = None,
) -> list[Interval]:
    """Work sessions from ``Journals/Daily/*`` whose endTime falls in [start, end].

    ``allowed_task_keys`` (ADR-040 A2 / Codex §3): when given, keep only sessions
    whose ``taskPath`` resolves to a slug **in the set** — so a TaskNotes pomodoro
    attached to a non-``work`` task can't inflate the weekly work🍅. Sessions with
    a missing/unresolvable ``taskPath`` are dropped under filtering (they can't be
    confirmed work, and an unattributed ``""`` bucket would also merge unsafely
    against current-slug timeEntries — Codex §3 renamed/missing-taskPath dupes).
    """
    out: list[Interval] = []
    daily_dir = Path(vault_root) / _DAILY_DIR
    if not daily_dir.exists():
        return out
    # Scan the window's own daily notes plus one neighbour each side (a session
    # can be filed in an adjacent note yet end inside the window after TZ shift).
    for delta in range((end - start).days + 3):
        d = start - timedelta(days=1) + timedelta(days=delta)
        fm = _read_frontmatter(daily_dir / f"{d.isoformat()}.md")
        out.extend(_intervals_from_sessions(fm.get("pomodoros"), start, end, allowed_task_keys))
    return out


def _intervals_from_sessions(
    sessions: object,
    start: Optional[date],
    end: Optional[date],
    allowed_task_keys: Optional[set[str]],
) -> list[Interval]:
    """Normalise a daily-note ``pomodoros`` list into counted work Intervals.

    ``start``/``end`` bound by endTime when both given (None = unbounded, used by
    the all-time per-task scan). ``allowed_task_keys`` filters by resolved slug.
    """
    out: list[Interval] = []
    if not isinstance(sessions, list):
        return out
    for s in sessions:
        if not isinstance(s, dict):
            continue
        if s.get("type") != "work" or not s.get("completed"):
            continue
        task_key = _task_key_from_path(s.get("taskPath"))
        if allowed_task_keys is not None and task_key not in allowed_task_keys:
            continue
        st, en = parse_dt(s.get("startTime")), parse_dt(s.get("endTime"))
        if not st or not en or en <= st:
            continue
        if start is not None and end is not None and not (start <= en.date() <= end):
            continue
        mins = _session_minutes(s, st, en)
        # Represent the counted duration as a [start, start+mins] envelope so
        # overlap-merge stays consistent with timeEntries intervals.
        out.append(
            Interval(
                task_key=task_key,
                start=st,
                end=st + timedelta(minutes=mins),
                source="daily",
            )
        )
    return out


def collect_timeentry_intervals(
    tasks: Iterable[tuple[str, list]], start: date, end: date
) -> list[Interval]:
    """Sessions from task ``timeEntries[]``. ``tasks`` = ``[(task_key, entries)]``."""
    out: list[Interval] = []
    for task_key, entries in tasks:
        if not isinstance(entries, list):
            continue
        for e in entries:
            if not isinstance(e, dict):
                continue
            st, en = parse_dt(e.get("startTime")), parse_dt(e.get("endTime"))
            if not st or not en or en <= st:
                continue
            if not (start <= en.date() <= end):
                continue
            out.append(Interval(task_key=task_key, start=st, end=en, source="timeEntries"))
    return out


def _merge_per_task(intervals: list[Interval]) -> tuple[list[Interval], list[str]]:
    """Merge overlapping intervals **within the same task** (union, not sum).

    Returns merged intervals + human-readable overlap warnings (ADR-039 D3:
    overlaps are merged + flagged, never silently summed).
    """
    by_task: dict[str, list[Interval]] = {}
    for iv in intervals:
        by_task.setdefault(iv.task_key, []).append(iv)

    merged: list[Interval] = []
    warnings: list[str] = []
    for task_key, ivs in by_task.items():
        ivs.sort(key=lambda x: x.start)
        cur = ivs[0]
        for nxt in ivs[1:]:
            if nxt.start < cur.end:  # overlap within same task
                if cur.source != nxt.source:
                    warnings.append(
                        f"{task_key or '(未指派)'}: 兩個 timer 的 session 重疊"
                        f"（{nxt.start:%m-%d %H:%M}），已合併不重複計"
                    )
                cur = Interval(
                    task_key=task_key,
                    start=cur.start,
                    end=max(cur.end, nxt.end),
                    source=cur.source,
                )
            else:
                merged.append(cur)
                cur = nxt
        merged.append(cur)
    return merged, warnings


def summarize(intervals: list[Interval]) -> WeeklyActual:
    merged, warnings = _merge_per_task(intervals)
    minutes_by_day: dict[str, float] = {}
    minutes_by_task: dict[str, float] = {}
    total_minutes = 0.0
    for iv in merged:
        m = iv.minutes
        total_minutes += m
        day = iv.end.date().isoformat()
        minutes_by_day[day] = minutes_by_day.get(day, 0.0) + m
        minutes_by_task[iv.task_key] = minutes_by_task.get(iv.task_key, 0.0) + m
    return WeeklyActual(
        total_pomodoros=int(total_minutes // POMODORO_MINUTES),
        total_minutes=total_minutes,
        by_day={d: int(m // POMODORO_MINUTES) for d, m in minutes_by_day.items()},
        by_task={t: int(m // POMODORO_MINUTES) for t, m in minutes_by_task.items()},
        overlap_warnings=tuple(warnings),
    )


def weekly_actual(
    vault_root: Path,
    start: date,
    end: date,
    *,
    task_time_entries: Iterable[tuple[str, list]] = (),
    work_task_keys: Optional[set[str]] = None,
) -> WeeklyActual:
    """Compute the week's actual 🍅 = union of daily ``pomodoros[]`` and task
    ``timeEntries[]``, filtered to [start, end], merged per task, minutes ÷ 25.

    ``work_task_keys`` scopes the daily side to ``work``-category tasks (ADR-040
    A2 — keeps a non-work timer out of the work🍅 hero); the timeEntries side is
    already pre-filtered by the caller passing only work tasks in
    ``task_time_entries``.
    """
    intervals = collect_daily_intervals(vault_root, start, end, allowed_task_keys=work_task_keys)
    intervals += collect_timeentry_intervals(task_time_entries, start, end)
    return summarize(intervals)


def task_actual(vault_root: Path, task_slug: str, time_entries: list) -> WeeklyActual:
    """All-time actual 🍅 for ONE task — the D3 **union** of every daily
    ``pomodoros[]`` session attributed to it (Obsidian TaskNotes timer) and its
    own ``timeEntries[]`` (Bridge web timer), merged so a session logged to both
    stores isn't double-counted (ADR-040 A2 / Codex §3 — the task-page accuracy
    must not be timeEntries-only).

    Cold path (task-detail page only): scans every ``Journals/Daily/*.md`` once,
    skipping Syncthing conflict copies. No date window — accuracy is all-time.
    """
    intervals: list[Interval] = []
    daily_dir = Path(vault_root) / _DAILY_DIR
    if daily_dir.exists():
        for p in sorted(daily_dir.glob("*.md")):
            if ".sync-conflict-" in p.name:
                continue
            fm = _read_frontmatter(p)
            intervals.extend(_intervals_from_sessions(fm.get("pomodoros"), None, None, {task_slug}))
    intervals += collect_timeentry_intervals([(task_slug, time_entries)], date.min, date.max)
    return summarize(intervals)
