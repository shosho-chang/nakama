"""Weekly indexer — assemble the LifeOS Weekly Dashboard view (ADR-039 Tier B).

FS-direct read (ADR-030 D2); vault is SoT; no DB mirror. Models the
``digest_indexer`` / ``project_indexer`` pattern.

Reads (all read-only for Slice 0):
  - ``TaskNotes/Tasks/*.md``       — tasks: 預估🍅, scheduled, plan[], timeEntries[], status
  - ``Journals/Daily/{date}.md``   — habits (exercise / meditation) + pomodoro sessions
  - ``Journals/Weekly/{sun}.md``   — weekly review (Slice 2 writes it; Slice 0 displays if present)

Week model (ADR-039 D2): **Sunday→Saturday**, US/epi numbering (week 1 contains
Jan 1). The label number matches 修修's spoken "W23". The file is keyed by the
start-Sunday date (``2026-05-31.md``), NOT ``W{n}`` — so it never collides with
the vault's ISO ``week:`` backlinks. ``year``/``week`` derive from the week-year,
not ``start.year`` (year-boundary safe, panel #7).
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, field, replace
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any, Optional
from zoneinfo import ZoneInfo

import yaml

from shared.pomodoro_aggregator import (
    POMODORO_MINUTES,
    WeeklyActual,
    parse_dt,
    weekly_actual,
)

TAIPEI = ZoneInfo("Asia/Taipei")

TASKS_DIR = "TaskNotes/Tasks"
DAILY_DIR = "Journals/Daily"
WEEKLY_DIR = "Journals/Weekly"
PROJECTS_DIR = "Projects"

# Task category model (ADR-039): a single frontmatter ``category:`` slug per task.
# Only ``work`` tracks pomodoros (🍅 = work hours, anti-burnout). The set is
# open-ended — unknown slugs render after the canonical order; a missing/blank
# ``category`` falls back to ``misc``. Stored value is an English slug; the UI
# renders the 中文 label from this map (rename display without touching data).
CATEGORY_LABELS = {"work": "工作", "health": "身心健康", "growth": "自我進修", "misc": "雜事"}
CATEGORY_ORDER = ("work", "health", "growth", "misc")
WORK_CATEGORY = "work"
DEFAULT_CATEGORY = "misc"

# Task priority (TaskNotes frontmatter `priority`). 3 stored values; the chip adds a
# 4th visual tier "First" (紫) for tasks in the week's 本週重要任務 list (weekly top3),
# computed at view time — not a stored priority value. (v3-I follow-up, 修修)
PRIORITY_LABELS = {"low": "Low", "normal": "Medium", "high": "High"}
PRIORITY_ORDER = ("low", "normal", "high")

UFO_WEEKLY_TARGET = 5  # planned super-focus (75-min) sessions per week (ADR-039 E)
UFO_MIN_MINUTES = 70  # a `deep` block counts as 1 UFO only if it ran ≥ this (ADR-040 A2)

_ZH_WEEKDAYS = ("日", "一", "二", "三", "四", "五", "六")  # Sun..Sat

_FRONTMATTER_RE = re.compile(r"^---\n(.*?)\n---\n?(.*)$", re.DOTALL)
_DATE_RE = re.compile(r"^(\d{4}-\d{2}-\d{2})\.md$")
_SYNC_CONFLICT_RE = re.compile(
    r"^(?P<key>\d{4}-\d{2}-\d{2})\.sync-conflict-"
    r"(?P<ts>\d{8}-\d{6})-(?P<device>[^.]+)\.md$"
)
_SKIP_FILENAME_RE = re.compile(r"^(?:\..*|.*\.sync-conflict-.*|.*\.tmp|Untitled.*)$")
_WIKILINK_RE = re.compile(r"\[\[([^\]|]+)(?:\|[^\]]+)?\]\]")


# ── Week math ────────────────────────────────────────────────────────────────


def today_taipei() -> date:
    return datetime.now(TAIPEI).date()


def _week1_sunday(year: int) -> date:
    """Sunday on/before Jan 1 of ``year`` (start of US week 1)."""
    jan1 = date(year, 1, 1)
    return jan1 - timedelta(days=(jan1.weekday() + 1) % 7)


@dataclass(frozen=True)
class WeekRef:
    year: int  # week-year (the year owning the Sun–Sat span)
    week: int  # US Sunday-start week number (week 1 contains Jan 1)
    start: date  # Sunday
    end: date  # Saturday

    @property
    def label(self) -> str:
        return f"W{self.week}"

    @property
    def file_key(self) -> str:
        return self.start.isoformat()

    @property
    def range_label(self) -> str:
        return f"{self.start.month}/{self.start.day} 日 – {self.end.month}/{self.end.day} 六"

    def shift(self, weeks: int) -> "WeekRef":
        return week_for_date(self.start + timedelta(weeks=weeks))

    def contains(self, d: date) -> bool:
        return self.start <= d <= self.end


def week_for_date(d: date) -> WeekRef:
    """Sunday-start, US-numbered week containing ``d`` (ADR-039 D2)."""
    this_sun = d - timedelta(days=(d.weekday() + 1) % 7)
    y = this_sun.year
    if this_sun >= _week1_sunday(y + 1):
        y += 1
    elif this_sun < _week1_sunday(y):
        y -= 1
    week = (this_sun - _week1_sunday(y)).days // 7 + 1
    return WeekRef(year=y, week=week, start=this_sun, end=this_sun + timedelta(days=6))


def week_from_key(key: str) -> Optional[WeekRef]:
    """Parse a ``YYYY-MM-DD`` start-Sunday key into a WeekRef (None if invalid)."""
    try:
        d = date.fromisoformat(key)
    except ValueError:
        return None
    wr = week_for_date(d)
    # Only accept keys that are actually a week-start Sunday.
    return wr if wr.start == d else None


# ── Coercion helpers ─────────────────────────────────────────────────────────


def _as_int(v: object) -> int:
    if isinstance(v, bool):
        return 0
    if isinstance(v, int):
        return v
    if isinstance(v, float):
        return int(v)
    if isinstance(v, str) and v.strip().lstrip("-").isdigit():
        return int(v.strip())
    return 0


def _as_date(v: object) -> Optional[date]:
    if isinstance(v, datetime):
        return v.astimezone(TAIPEI).date() if v.tzinfo else v.date()
    if isinstance(v, date):
        return v
    if isinstance(v, str) and v.strip():
        s = v.strip()
        try:
            return date.fromisoformat(s[:10])
        except ValueError:
            return None
    return None


def _strip_wikilink(v: object) -> str:
    if not isinstance(v, str):
        return ""
    m = _WIKILINK_RE.search(v)
    return m.group(1).strip() if m else v.strip()


def _link_key(v: str) -> str:
    """Normalise a wikilink target / filename to a canonical match key (PR#811
    audit §4): drop a ``#heading`` / ``^block`` suffix, take the last path
    segment (``[[Projects/專案]]`` → ``專案``), then NFC-normalise, trim, and map
    the full-width colon ``：`` → ASCII ``:`` (Obsidian/Windows store one or the
    other). NFC on both sides also reconciles macOS-NFD-synced filenames."""
    v = unicodedata.normalize("NFC", v).strip()
    v = re.split(r"[#^]", v, maxsplit=1)[0]  # drop heading / block ref
    v = v.replace("\\", "/").rsplit("/", 1)[-1]  # last path segment
    return v.strip().replace("：", ":")


def _entry_minutes(entry: dict) -> float:
    """Active minutes of a task ``timeEntries`` row (endTime − startTime)."""
    if not isinstance(entry, dict):
        return 0.0
    st, en = parse_dt(entry.get("startTime")), parse_dt(entry.get("endTime"))
    if not st or not en or en <= st:
        return 0.0
    return (en - st).total_seconds() / 60.0


def _entry_end_date(entry: dict) -> Optional[date]:
    if not isinstance(entry, dict):
        return None
    en = parse_dt(entry.get("endTime"))
    return en.date() if en else None


def _is_ufo(entry: dict) -> bool:
    """A timeEntries row counts as 1 UFO 🤩 iff it is a ``deep`` block that ran
    ≥ :data:`UFO_MIN_MINUTES` and was completed (ADR-040 A2 — derived, not a bare
    tag). ``completed`` absent → treated as completed (legacy / Obsidian-written
    entries predate the field)."""
    if not isinstance(entry, dict) or entry.get("mode") != "deep":
        return False
    if entry.get("completed") is False:
        return False
    return _entry_minutes(entry) >= UFO_MIN_MINUTES


# ── Task model ───────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class TaskAllocation:
    date: date
    pomodoros: int
    reason: str = ""
    done: int = 0
    # ADR-041 v3 — per-entry calendar projection (one Google event per plan entry).
    # ``start``/``end`` are ISO-8601 *with offset* (e.g. 2026-06-03T09:00:00+08:00),
    # "" when the entry is plan-only (not projected). ``event_id`` links the Google
    # event ("" when unprojected / projection pending).
    start: str = ""
    end: str = ""
    event_id: str = ""

    @property
    def is_linked(self) -> bool:
        """True iff this entry is projected to a live Google Calendar event."""
        return bool(self.event_id)

    @property
    def time_label(self) -> str:
        """``HH:MM`` for a timed entry, "整天" for an all-day entry (date-only start —
        ADR-041 v3-E), or "" when plan-only / unparseable."""
        if not self.start:
            return ""
        if "T" not in self.start:  # date-only ⇒ all-day event
            return "整天"
        try:
            return datetime.fromisoformat(self.start).strftime("%H:%M")
        except ValueError:
            return ""


@dataclass(frozen=True)
class WeeklyTask:
    slug: str  # file stem
    title: str
    name: str  # task portion (after "Project - "), else title
    project: str  # project name or ""
    category: str  # 工作 | 雜事
    est_pomodoros: int
    status: str  # to-do | doing | done | paused
    done: bool
    scheduled: Optional[date]
    plan: tuple[TaskAllocation, ...]
    time_entries: list  # raw, for aggregator
    relative_path: str
    weekly_priority: str = ""  # week file_key this task is a top-3 priority for ("" = none)
    calendar_event_id: str = ""  # set once projected to Google Calendar (41b); "" = not linked
    priority: str = "normal"  # task priority frontmatter: low | normal | high (TaskNotes)

    def planned_in(self, wk: WeekRef) -> int:
        if self.plan:
            return sum(a.pomodoros for a in self.plan if wk.contains(a.date))
        # Slice-0 fallback: no plan[] yet → whole estimate lands on `scheduled`.
        if self.scheduled and wk.contains(self.scheduled):
            return self.est_pomodoros
        return 0

    def dates_in(self, wk: WeekRef) -> list[date]:
        if self.plan:
            return [a.date for a in self.plan if wk.contains(a.date)]
        if self.scheduled and wk.contains(self.scheduled):
            return [self.scheduled]
        return []

    def pomodoros_on(self, d: date) -> int:
        if self.plan:
            return sum(a.pomodoros for a in self.plan if a.date == d)
        if self.scheduled == d:
            return self.est_pomodoros
        return 0

    def actual_pomodoros_on(self, d: date) -> int:
        """Actual 🍅 logged on day ``d`` from this task's own ``timeEntries[]``
        (entries whose endTime falls on ``d``). Per-day counterpart of
        :attr:`actual_pomodoros`; feeds the daily bullet's 預計/實際 readout (#4)."""
        mins = sum(_entry_minutes(e) for e in self.time_entries if _entry_end_date(e) == d)
        return int(mins // POMODORO_MINUTES)

    @property
    def is_work(self) -> bool:
        return self.category == WORK_CATEGORY

    @property
    def category_label(self) -> str:
        return CATEGORY_LABELS.get(self.category, self.category)

    @property
    def priority_label(self) -> str:
        return PRIORITY_LABELS.get(self.priority, "Medium")

    def is_on(self, d: date) -> bool:
        """True if this task is assigned to day ``d`` (plan entry or scheduled)."""
        if self.plan:
            return any(a.date == d for a in self.plan)
        return self.scheduled == d

    def schedule_dates(self) -> list[date]:
        """Every concrete date this task is placed on — ``plan[]`` entry dates plus a
        bare task-level ``scheduled`` — sorted ascending and de-duped. Empty list ⇒ the
        task has no time yet (the 全部 view buckets it under 還沒排定時間)."""
        ds = {a.date for a in self.plan}
        if self.scheduled is not None:
            ds.add(self.scheduled)
        return sorted(ds)

    def done_on(self, d: date) -> bool:
        """Per-DAY completion for the daily bullet (v3-I follow-up, 修修): the plan[]
        entry's own ``done`` flag for ``d``. A scheduled-only task (no plan entry) falls
        back to the task-level done. Distinct from ``done`` (whole-task completion)."""
        for a in self.plan:
            if a.date == d:
                return bool(a.done)
        return self.done

    def is_priority_for(self, wk: WeekRef) -> bool:
        return self.weekly_priority == wk.file_key

    # -- actual 🍅 from this task's own web-logged timeEntries[] (ADR-039 E) --
    @property
    def actual_minutes(self) -> float:
        return sum(_entry_minutes(e) for e in self.time_entries)

    @property
    def actual_pomodoros(self) -> int:
        return int(self.actual_minutes // POMODORO_MINUTES)

    @property
    def ufo_total(self) -> int:
        """All-time UFO (75-min ``deep``) sessions logged on this task — derived
        per :func:`_is_ufo` (≥70 min & completed), not a bare ``mode:deep`` tag."""
        return sum(1 for e in self.time_entries if _is_ufo(e))

    @property
    def accuracy_pct(self) -> int:
        """實際🍅 / 預估🍅 as a percentage (0 when no estimate)."""
        if not self.est_pomodoros:
            return 0
        return int(round(100 * self.actual_pomodoros / self.est_pomodoros))

    def deep_sessions_in(self, wk: WeekRef) -> int:
        """UFO 🤩 sessions (derived, :func:`_is_ufo`) ending inside ``wk``."""
        return sum(
            1
            for e in self.time_entries
            if _is_ufo(e) and (d := _entry_end_date(e)) is not None and wk.contains(d)
        )


@dataclass(frozen=True)
class DailyHabit:
    exercise_type: str = ""
    exercise_minutes: int = 0
    meditation_minutes: int = 0


@dataclass(frozen=True)
class WeeklyReview:
    exists: bool
    status: str  # planning | active | reviewed
    top3: tuple[str, ...] = ()  # raw wikilink targets (resolved into Top3Item by view)
    next3: tuple[str, ...] = ()
    targets: dict = field(default_factory=dict)  # {pomodoro, ufo} — 修修-set (A3)
    sections: dict[str, str] = field(default_factory=dict)  # heading -> prose
    notes: str = ""


@dataclass(frozen=True)
class Top3Item:
    """A resolved 本週三大要事 entry (ADR-040 A4). ``kind`` ∈ task | project |
    unresolved. A task is done by its own state; a project is done when every
    task in it is done (ratio = done/total). An unresolved wikilink surfaces
    visibly (never silently dropped — Gemini §1)."""

    raw: str  # the wikilink target text as 修修 wrote it
    kind: str
    title: str
    slug: str = ""  # task slug → task page link ("" for project/unresolved)
    done: bool = False
    ratio_done: int = 0
    ratio_total: int = 0

    @property
    def is_unresolved(self) -> bool:
        return self.kind == "unresolved"

    @property
    def ratio_label(self) -> str:
        return f"{self.ratio_done}/{self.ratio_total}" if self.kind == "project" else ""


@dataclass(frozen=True)
class ConflictFile:
    key: str
    relative_path: str
    timestamp: str
    device: str


@dataclass(frozen=True)
class WeeklyView:
    week: WeekRef
    is_current: bool
    mode: str  # "active" | "review"
    planned: int
    actual: WeeklyActual
    rate_pct: int
    ufo_count: int  # 75-min "deep" sessions logged this week (🤩)
    ufo_target: int  # weekly UFO goal — targets.ufo (A3) or the default constant
    ufo_target_raw: Optional[int]  # the *stored* targets.ufo, or None if unset —
    # the plan form binds to this so a blank field is never the machine default
    pomodoro_target: int  # weekly 🍅 goal — targets.pomodoro (A3); 0 = unset
    pomodoro_goal: int  # the hero denominator: pomodoro_target if set, else planned
    top3: tuple[Top3Item, ...]  # this week's resolved 本週重要任務 (hero strip; uncapped — 修修)
    top3_options: tuple[dict, ...]  # {group, value, label} for the 重要任務 dropdowns
    top3_values: tuple[str, ...]  # current selected option values (task slug | project raw)
    important_slugs: frozenset  # task slugs in 本週重要任務 → drives the "First" (紫) chip
    tasks: tuple[WeeklyTask, ...]  # tasks with allocation/scheduled in week
    today_tasks: tuple[WeeklyTask, ...]  # subset scheduled today (current week only)
    incomplete: tuple[WeeklyTask, ...]  # not done, due on/before week end
    # 全部 view (N541) — ALL not-done tasks split by scheduling status, not by project.
    # 1+2 are sorted nearest→furthest by date; 3 by name.
    all_this_week: tuple[WeeklyTask, ...]  # has a plan/scheduled date inside this week
    all_other_scheduled: tuple[WeeklyTask, ...]  # scheduled, but every date is outside this week
    all_unscheduled: tuple[WeeklyTask, ...]  # no plan/scheduled date yet
    backlog_count: int  # total open tasks in the backlog (across all three buckets)
    by_project: dict[str, list[WeeklyTask]]
    planned_by_task: dict[str, int]  # slug -> planned 🍅 this week (work only)
    days: tuple[dict, ...]  # 5 day-cards Mon..Fri (the bullet section)
    day_headers: list[dict]  # 7 entries {zh, date, is_weekend, is_today} — editor day-select
    review: Optional[WeeklyReview]
    conflicts: tuple[ConflictFile, ...]
    prev_key: str
    next_key: str
    today_iso: str  # today (Asia/Taipei) — default for the calendar scheduler picker (41c)


# ── Indexer ──────────────────────────────────────────────────────────────────


class WeeklyIndexer:
    def __init__(self, vault_root: Path) -> None:
        self._root = Path(vault_root)

    # -- tasks --
    def read_tasks(self) -> list[WeeklyTask]:
        out: list[WeeklyTask] = []
        d = self._root / TASKS_DIR
        if not d.exists():
            return out
        for p in sorted(d.iterdir()):
            if not p.is_file() or p.suffix != ".md" or _SKIP_FILENAME_RE.match(p.name):
                continue
            t = self._task_from_path(p)
            if t is not None:
                out.append(t)
        return out

    def find_task(self, slug: str) -> Optional[WeeklyTask]:
        """Load a single task by slug (file stem) for the task-detail page."""
        slug = unicodedata.normalize("NFC", slug)
        p = self._root / TASKS_DIR / f"{slug}.md"
        if not p.is_file():
            return None
        return self._task_from_path(p)

    def _task_from_path(self, path: Path) -> Optional[WeeklyTask]:
        try:
            raw = path.read_text(encoding="utf-8")
        except OSError:
            return None
        m = _FRONTMATTER_RE.match(raw)
        if not m:
            return None
        try:
            fm = yaml.safe_load(m.group(1)) or {}
        except yaml.YAMLError:
            return None
        if not isinstance(fm, dict):
            return None

        slug = unicodedata.normalize("NFC", path.stem)
        title = str(fm.get("title") or slug)

        projects = fm.get("projects")
        project = ""
        if isinstance(projects, list) and projects:
            project = _strip_wikilink(projects[0])
        elif isinstance(projects, str):
            project = _strip_wikilink(projects)

        name = title
        if project and title.startswith(project):
            name = title[len(project) :].lstrip(" -—–") or title

        est = _as_int(fm.get("預估🍅")) or _as_int(fm.get("🍅-Estimated"))
        if not est:
            te = _as_int(fm.get("timeEstimate"))
            est = round(te / 25) if te else 0

        status = str(fm.get("status") or "to-do")
        done = status == "done" or fm.get("✅") is True
        if done and status != "done":
            status = "done"

        plan_raw = fm.get("plan")
        plan: list[TaskAllocation] = []
        if isinstance(plan_raw, list):
            for a in plan_raw:
                if not isinstance(a, dict):
                    continue
                ad = _as_date(a.get("date"))
                if ad is None:
                    continue
                plan.append(
                    TaskAllocation(
                        date=ad,
                        pomodoros=_as_int(a.get("pomodoros")),
                        reason=str(a.get("reason") or ""),
                        # `done` is a YAML bool — _as_int treats bool as 0 (for pomodoros),
                        # so parse truthiness directly, else the daily cross-out never shows.
                        done=1 if a.get("done") else 0,
                        start=str(a.get("start") or ""),
                        end=str(a.get("end") or ""),
                        event_id=str(a.get("calendar_event_id") or a.get("event_id") or "").strip(),
                    )
                )

        time_entries = fm.get("timeEntries")
        if not isinstance(time_entries, list):
            time_entries = []

        cat_raw = fm.get("category")
        category = str(cat_raw).strip() if cat_raw is not None else ""
        if not category:
            category = DEFAULT_CATEGORY

        wp_raw = fm.get("weekly_priority")
        weekly_priority = _as_date(wp_raw).isoformat() if _as_date(wp_raw) else ""

        cal_event_id = str(fm.get("calendar_event_id") or "").strip()

        # ADR-041 v3 dual-read (V4): a legacy task-level projection (scheduled +
        # calendar_event_id — the v2 single-event store) is surfaced onto the
        # plan[] entry for DISPLAY so old links render under the per-entry model.
        # The writer migrates it for real on the next v3 write; here it is read-only.
        if cal_event_id and not any(a.start for a in plan):
            sched_raw = fm.get("scheduled")
            sched_d = _as_date(sched_raw)
            if sched_d is not None:
                start_iso = (
                    sched_raw.isoformat()
                    if isinstance(sched_raw, (date, datetime))
                    else str(sched_raw)
                )
                end_raw = fm.get("scheduled_end")
                end_iso = (
                    end_raw.isoformat()
                    if isinstance(end_raw, (date, datetime))
                    else str(end_raw or "")
                )
                idx = next((i for i, a in enumerate(plan) if a.date == sched_d), None)
                if idx is not None:
                    plan[idx] = replace(
                        plan[idx], start=start_iso, end=end_iso, event_id=cal_event_id
                    )
                else:  # legacy calendar task with no plan entry → synthesize one for display
                    plan.append(
                        TaskAllocation(
                            date=sched_d,
                            pomodoros=est or 1,
                            start=start_iso,
                            end=end_iso,
                            event_id=cal_event_id,
                        )
                    )

        # 預估🍅 fallback: a calendar-linked task (Nami's _write_calendar_linked_task
        # before it wrote 預估🍅) carries its estimate only on the plan[] entries.
        # Sum them so the task page shows a real 預估 instead of "-". An explicit
        # 預估🍅 in frontmatter always wins.
        if not est and plan:
            est = sum(a.pomodoros for a in plan)

        return WeeklyTask(
            slug=slug,
            title=title,
            name=name,
            project=project,
            category=category,
            est_pomodoros=est,
            status=status,
            done=done,
            scheduled=_as_date(fm.get("scheduled")),
            plan=tuple(plan),
            time_entries=time_entries,
            relative_path=f"{TASKS_DIR}/{path.name}",
            weekly_priority=weekly_priority,
            calendar_event_id=cal_event_id,
            priority=str(fm.get("priority") or "normal").strip().lower() or "normal",
        )

    # -- habits --
    def read_habits(self, wk: WeekRef) -> dict[date, DailyHabit]:
        out: dict[date, DailyHabit] = {}
        d = self._root / DAILY_DIR
        for i in range(7):
            day = wk.start + timedelta(days=i)
            p = d / f"{day.isoformat()}.md"
            if not p.exists():
                continue
            try:
                raw = p.read_text(encoding="utf-8")
            except OSError:
                continue
            m = _FRONTMATTER_RE.match(raw)
            if not m:
                continue
            try:
                fm = yaml.safe_load(m.group(1)) or {}
            except yaml.YAMLError:
                continue
            if not isinstance(fm, dict):
                continue
            out[day] = DailyHabit(
                exercise_type=str(fm.get("exercise_type") or ""),
                exercise_minutes=_as_int(fm.get("exercise_duration")),
                meditation_minutes=_as_int(fm.get("meditation_minutes")),
            )
        return out

    # -- weekly review file --
    def read_review(self, wk: WeekRef) -> Optional[WeeklyReview]:
        p = self._root / WEEKLY_DIR / f"{wk.file_key}.md"
        if not p.exists():
            return None
        try:
            raw = p.read_text(encoding="utf-8")
        except OSError:
            return None
        m = _FRONTMATTER_RE.match(raw)
        fm: dict[str, Any] = {}
        body = raw
        if m:
            try:
                fm = yaml.safe_load(m.group(1)) or {}
            except yaml.YAMLError:
                fm = {}
            body = m.group(2)
        if not isinstance(fm, dict):
            fm = {}
        sections: dict[str, str] = {}
        for part in re.split(r"^##\s+", body, flags=re.MULTILINE)[1:]:
            line, _, rest = part.partition("\n")
            sections[line.strip()] = rest.strip()
        top3 = tuple(_strip_wikilink(x) for x in (fm.get("top3") or []) if x)
        next3 = tuple(_strip_wikilink(x) for x in (fm.get("next3") or []) if x)
        targets_raw = fm.get("targets")
        targets: dict = {}
        if isinstance(targets_raw, dict):
            if (p := _as_int(targets_raw.get("pomodoro"))) > 0:
                targets["pomodoro"] = p
            if (u := _as_int(targets_raw.get("ufo"))) > 0:
                targets["ufo"] = u
        return WeeklyReview(
            exists=True,
            status=str(fm.get("status") or "active"),
            top3=top3,
            next3=next3,
            targets=targets,
            sections=sections,
            notes=sections.get("隨手筆記", ""),
        )

    def _top3_options(self, all_tasks: list[WeeklyTask]) -> list[dict]:
        """Candidate 三大要事 entries for the dropdowns (ADR-040 A4): open tasks +
        all projects. The ``value`` is what resolves via :meth:`_resolve_top3`
        (task slug → by_slug; project name → proj_tasks / Projects file)."""
        opts: list[dict] = [
            {"group": "任務", "value": t.slug, "label": t.name or t.title}
            for t in all_tasks
            if not t.done
        ]
        projects: set[str] = {t.project for t in all_tasks if t.project}
        pdir = self._root / PROJECTS_DIR
        if pdir.exists():
            for p in pdir.glob("*.md"):
                projects.add(unicodedata.normalize("NFC", p.stem))
        opts += [{"group": "專案", "value": name, "label": name} for name in sorted(projects)]
        return opts

    def _project_keys(self) -> set[str]:
        """Normalised match keys for every project file under ``Projects/``.
        Membership replaces the old per-target ``.exists()`` probe, which failed
        on a macOS-NFD-synced filename vs an NFC path on the Linux server
        (PR#811 audit §4 — `_link_key` NFC-normalises both sides here)."""
        pdir = self._root / PROJECTS_DIR
        if not pdir.exists():
            return set()
        return {_link_key(p.stem) for p in pdir.glob("*.md")}

    def _resolve_top3(
        self, raws: tuple[str, ...], all_tasks: list[WeeklyTask]
    ) -> tuple[Top3Item, ...]:
        """Resolve ≤3 wikilink targets to tasks or projects (ADR-040 A4).

        - **task** (slug or title match) → done by its own state.
        - **project** (a ``projects:`` value on some task, or a ``Projects/{name}.md``
          file) → done ratio = done tasks / total tasks in it; done iff all done.
        - otherwise **unresolved** — surfaced visibly, never dropped (Gemini §1).
        Matching is CJK-tolerant via :func:`_link_key` (NFC + full-width colon +
        path-segment + heading-strip).
        """
        by_slug = {_link_key(t.slug): t for t in all_tasks}
        by_title = {_link_key(t.title): t for t in all_tasks}
        proj_tasks: dict[str, list[WeeklyTask]] = {}
        for t in all_tasks:
            if t.project:
                proj_tasks.setdefault(_link_key(t.project), []).append(t)
        file_proj_keys = self._project_keys()

        items: list[Top3Item] = []
        for raw in raws[:3]:
            target = _strip_wikilink(raw)
            key = _link_key(target)
            task = by_slug.get(key) or by_title.get(key)
            if task is not None:
                items.append(
                    Top3Item(
                        raw=target, kind="task", title=task.title, slug=task.slug, done=task.done
                    )
                )
                continue
            ptasks = proj_tasks.get(key)
            if ptasks is not None or key in file_proj_keys:
                ptasks = ptasks or []
                total = len(ptasks)
                done_n = sum(1 for t in ptasks if t.done)
                items.append(
                    Top3Item(
                        raw=target,
                        kind="project",
                        title=key,
                        done=total > 0 and done_n == total,
                        ratio_done=done_n,
                        ratio_total=total,
                    )
                )
                continue
            items.append(Top3Item(raw=target, kind="unresolved", title=target))
        return tuple(items)

    def list_conflicts(self) -> list[ConflictFile]:
        out: list[ConflictFile] = []
        d = self._root / WEEKLY_DIR
        if not d.exists():
            return out
        for p in d.iterdir():
            m = _SYNC_CONFLICT_RE.match(p.name)
            if m:
                out.append(
                    ConflictFile(
                        key=m.group("key"),
                        relative_path=f"{WEEKLY_DIR}/{p.name}",
                        timestamp=m.group("ts"),
                        device=m.group("device"),
                    )
                )
        out.sort(key=lambda c: c.timestamp, reverse=True)
        return out

    # -- assembly --
    def view(self, wk: Optional[WeekRef] = None) -> WeeklyView:
        today = today_taipei()
        current = week_for_date(today)
        if wk is None:
            wk = current
        is_current = wk.start == current.start

        all_tasks = self.read_tasks()
        in_week = [
            t
            for t in all_tasks
            if t.planned_in(wk) > 0 or (t.scheduled is not None and wk.contains(t.scheduled))
        ]
        # incomplete = not done and due on/before this week's end (carry-forward pool)
        incomplete = [
            t for t in all_tasks if not t.done and t.scheduled is not None and t.scheduled <= wk.end
        ]
        # backlog (41c) = EVERY not-done task, regardless of scheduling. The 全部 view
        # (N541) splits it into three scheduling buckets instead of by project:
        #   1. 排定本週的       — has a plan/scheduled date inside this week
        #   2. 已排程，非本週   — scheduled, but every date falls outside this week
        #   3. 還沒排定時間     — no plan/scheduled date yet
        # Buckets 1+2 sort nearest→furthest by date (修修); 3 sorts by name.
        backlog = [t for t in all_tasks if not t.done]
        all_this_week: list[WeeklyTask] = []
        all_other_scheduled: list[WeeklyTask] = []
        all_unscheduled: list[WeeklyTask] = []
        for t in backlog:
            ds = t.schedule_dates()
            if not ds:
                all_unscheduled.append(t)
            elif any(wk.contains(d) for d in ds):
                all_this_week.append(t)
            else:
                all_other_scheduled.append(t)

        def _earliest_in_week(t: WeeklyTask) -> date:
            return min(d for d in t.schedule_dates() if wk.contains(d))

        all_this_week.sort(key=lambda t: (_earliest_in_week(t), t.name))
        all_other_scheduled.sort(key=lambda t: (t.schedule_dates()[0], t.name))
        all_unscheduled.sort(key=lambda t: t.name)

        # 🍅 = work hours: only `work`-category tasks count toward planned/actual.
        planned = sum(t.planned_in(wk) for t in all_tasks if t.is_work)
        work_slugs = {t.slug for t in all_tasks if t.is_work}
        actual = weekly_actual(
            self._root,
            wk.start,
            wk.end,
            task_time_entries=[(t.slug, t.time_entries) for t in all_tasks if t.is_work],
            work_task_keys=work_slugs,
        )
        # 🤩 UFO = 75-min deep sessions logged this week (work tasks only).
        ufo_count = sum(t.deep_sessions_in(wk) for t in all_tasks if t.is_work)

        review = self.read_review(wk)

        # 本週三大要事 (A4): canonical source = weekly-file top3 (wikilink → task|
        # project). Transitional fallback (A5): task-frontmatter weekly_priority
        # flags, used only while no weekly file / no top3 exists yet.
        # v3-I follow-up (修修): 本週重要任務 — no longer capped at 3 (a week can have >3).
        if review is not None and review.top3:
            top3 = self._resolve_top3(review.top3, all_tasks)
        else:
            top3 = tuple(
                Top3Item(raw=t.title, kind="task", title=t.title, slug=t.slug, done=t.done)
                for t in all_tasks
                if t.is_priority_for(wk)
            )

        # targets (A3): 修修-set weekly goals from the weekly file; UFO falls back
        # to the default constant, 🍅 goal falls back to the planned-sum.
        targets = review.targets if review is not None else {}
        _ufo_raw = targets.get("ufo")
        ufo_target_raw = _ufo_raw if isinstance(_ufo_raw, int) and _ufo_raw > 0 else None
        ufo_target = ufo_target_raw or UFO_WEEKLY_TARGET
        pomodoro_target = targets.get("pomodoro") or 0
        # 🍅 weekly goal = Σ this week's scheduled task pomodoros (auto-sum); an
        # explicit targets.pomodoro still wins if a file carries one (back-compat).
        pomodoro_goal = pomodoro_target or planned
        rate = int(round(100 * actual.total_pomodoros / pomodoro_goal)) if pomodoro_goal else 0

        # dropdown candidates + current selections (task slug | project name)
        top3_options = self._top3_options(all_tasks)
        top3_values = tuple((it.slug if it.kind == "task" else it.raw) for it in top3)

        # mode: review when looking at a past week that isn't yet reviewed
        mode = "active"
        if wk.start < current.start and (review is None or review.status != "reviewed"):
            mode = "review"

        by_project: dict[str, list[WeeklyTask]] = {}
        for t in in_week:
            by_project.setdefault(t.project or "（無專案）", []).append(t)

        day_headers = self._build_day_headers(wk, today)
        days = self._build_days(wk, in_week, today)
        today_tasks = tuple(t for t in in_week if today in t.dates_in(wk))

        return WeeklyView(
            week=wk,
            is_current=is_current,
            mode=mode,
            planned=planned,
            actual=actual,
            rate_pct=rate,
            ufo_count=ufo_count,
            ufo_target=ufo_target,
            ufo_target_raw=ufo_target_raw,
            pomodoro_target=pomodoro_target,
            pomodoro_goal=pomodoro_goal,
            top3=top3,
            top3_options=tuple(top3_options),
            top3_values=top3_values,
            important_slugs=frozenset(it.slug for it in top3 if it.kind == "task" and it.slug),
            tasks=tuple(in_week),
            today_tasks=today_tasks,
            incomplete=tuple(incomplete),
            all_this_week=tuple(all_this_week),
            all_other_scheduled=tuple(all_other_scheduled),
            all_unscheduled=tuple(all_unscheduled),
            backlog_count=len(backlog),
            by_project=by_project,
            planned_by_task={t.slug: t.planned_in(wk) for t in in_week},
            days=days,
            day_headers=day_headers,
            review=review,
            conflicts=tuple(self.list_conflicts()),
            prev_key=wk.shift(-1).file_key,
            next_key=wk.shift(1).file_key,
            today_iso=today.isoformat(),
        )

    def _build_day_headers(self, wk: WeekRef, today: date) -> list[dict]:
        """7 day descriptors (Sun..Sat) — feeds the plan-editor day <select>."""
        return [
            {
                "zh": _ZH_WEEKDAYS[i],
                "date": (wk.start + timedelta(days=i)).isoformat(),
                "is_weekend": i in (0, 6),  # Sun, Sat
                "is_today": (wk.start + timedelta(days=i)) == today,
            }
            for i in range(7)
        ]

    def _build_days(self, wk: WeekRef, tasks: list[WeeklyTask], today: date) -> tuple[dict, ...]:
        """5 day-cards (Mon..Fri). Each card groups that day's assigned tasks by
        category in canonical order (unknown slugs appended); only ``work`` items
        carry a 🍅 count. ``default_open`` marks today (or Monday if today ∉ week).
        """
        cards: list[dict] = []
        has_today = False
        for i in range(1, 6):  # Mon..Fri (0=Sun, 6=Sat in this Sunday-start model)
            d = wk.start + timedelta(days=i)
            on = [t for t in tasks if t.is_on(d)]
            work_pom = sum(t.pomodoros_on(d) for t in on if t.is_work)
            # weekend never reaches here (Mon-Fri only), so no D9 reason marker needed
            # v3-I follow-up (修修): the daily card has 3 columns — 工作(½) / 身心健康 / 其他.
            # 其他 folds 自我進修(growth) into 雜事(misc): both bucket under "misc" and the
            # column is labelled 其他. (The task-list category chip still shows the real
            # category.) Unknown categories also fall into 其他.
            DAILY_OTHER = "misc"
            by_cat: dict[str, list[WeeklyTask]] = {}
            for t in on:
                cat_key = t.category if t.category in ("work", "health") else DAILY_OTHER
                by_cat.setdefault(cat_key, []).append(t)
            ordered = ["work", "health", DAILY_OTHER]
            seen: set[str] = set()
            categories: list[dict] = []
            for slug in ordered:
                if slug in seen:
                    continue
                seen.add(slug)
                is_work = slug == WORK_CATEGORY
                items = [
                    {
                        "name": t.name,
                        "slug": t.slug,
                        "pomodoros": t.pomodoros_on(d) if is_work else 0,
                        "actual": t.actual_pomodoros_on(d) if is_work else 0,
                        # v3-I follow-up: daily done = the DAY's plan-entry done (per-day),
                        # NOT the whole-task done — the daily checkbox is a different action.
                        "done": t.done_on(d),
                        "relative_path": t.relative_path,
                        # v3-I follow-up: daily items also carry priority + project (修修)
                        "priority": t.priority,
                        "priority_label": t.priority_label,
                        "project": t.project,
                    }
                    for t in by_cat.get(slug, [])
                ]
                categories.append(
                    {
                        "slug": slug,
                        # the misc column is the merged 其他 bucket (misc + growth) — 修修
                        "label": "其他" if slug == DAILY_OTHER else CATEGORY_LABELS.get(slug, slug),
                        "is_work": is_work,
                        "items": items,
                    }
                )
            is_today = d == today
            has_today = has_today or is_today
            cards.append(
                {
                    "weekday_zh": _ZH_WEEKDAYS[i],
                    "date": d.isoformat(),
                    "md": f"{d.month}/{d.day}",
                    "is_today": is_today,
                    "work_pomodoros": work_pom,
                    "task_count": len(on),
                    "categories": categories,
                }
            )
        # default-open: today's card, else Monday (first) when today ∉ Mon–Fri
        for idx, c in enumerate(cards):
            c["default_open"] = c["is_today"] if has_today else (idx == 0)
        return tuple(cards)
