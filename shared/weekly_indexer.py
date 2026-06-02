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
from dataclasses import dataclass, field
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

# Task category model (ADR-039): a single frontmatter ``category:`` slug per task.
# Only ``work`` tracks pomodoros (🍅 = work hours, anti-burnout). The set is
# open-ended — unknown slugs render after the canonical order; a missing/blank
# ``category`` falls back to ``misc``. Stored value is an English slug; the UI
# renders the 中文 label from this map (rename display without touching data).
CATEGORY_LABELS = {"work": "工作", "health": "身心健康", "growth": "自我進修", "misc": "雜事"}
CATEGORY_ORDER = ("work", "health", "growth", "misc")
WORK_CATEGORY = "work"
DEFAULT_CATEGORY = "misc"

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

    @property
    def is_work(self) -> bool:
        return self.category == WORK_CATEGORY

    @property
    def category_label(self) -> str:
        return CATEGORY_LABELS.get(self.category, self.category)

    def is_on(self, d: date) -> bool:
        """True if this task is assigned to day ``d`` (plan entry or scheduled)."""
        if self.plan:
            return any(a.date == d for a in self.plan)
        return self.scheduled == d

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
    top3: tuple[str, ...] = ()
    next3: tuple[str, ...] = ()
    sections: dict[str, str] = field(default_factory=dict)  # heading -> prose
    notes: str = ""


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
    ufo_target: int  # weekly UFO goal (constant for now — ADR-039 E)
    top3: tuple[WeeklyTask, ...]  # this week's ≤3 weekly_priority tasks (hero strip)
    tasks: tuple[WeeklyTask, ...]  # tasks with allocation/scheduled in week
    today_tasks: tuple[WeeklyTask, ...]  # subset scheduled today (current week only)
    incomplete: tuple[WeeklyTask, ...]  # not done, due on/before week end
    by_project: dict[str, list[WeeklyTask]]
    planned_by_task: dict[str, int]  # slug -> planned 🍅 this week (work only)
    days: tuple[dict, ...]  # 5 day-cards Mon..Fri (the bullet section)
    day_headers: list[dict]  # 7 entries {zh, date, is_weekend, is_today} — editor day-select
    review: Optional[WeeklyReview]
    conflicts: tuple[ConflictFile, ...]
    prev_key: str
    next_key: str


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
                        done=_as_int(a.get("done")),
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
        return WeeklyReview(
            exists=True,
            status=str(fm.get("status") or "active"),
            top3=top3,
            next3=next3,
            sections=sections,
            notes=sections.get("隨手筆記", ""),
        )

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

        # 🍅 = work hours: only `work`-category tasks count toward planned/actual.
        planned = sum(t.planned_in(wk) for t in all_tasks if t.is_work)
        actual = weekly_actual(
            self._root,
            wk.start,
            wk.end,
            task_time_entries=[(t.slug, t.time_entries) for t in all_tasks if t.is_work],
        )
        rate = int(round(100 * actual.total_pomodoros / planned)) if planned else 0

        # 🤩 UFO = 75-min deep sessions logged this week (work tasks only).
        ufo_count = sum(t.deep_sessions_in(wk) for t in all_tasks if t.is_work)
        # 本週三大要事 — tasks flagged weekly_priority for this week (≤3, done→strikethrough)
        top3 = tuple(t for t in all_tasks if t.is_priority_for(wk))[:3]

        review = self.read_review(wk)

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
            ufo_target=UFO_WEEKLY_TARGET,
            top3=top3,
            tasks=tuple(in_week),
            today_tasks=today_tasks,
            incomplete=tuple(incomplete),
            by_project=by_project,
            planned_by_task={t.slug: t.planned_in(wk) for t in in_week},
            days=days,
            day_headers=day_headers,
            review=review,
            conflicts=tuple(self.list_conflicts()),
            prev_key=wk.shift(-1).file_key,
            next_key=wk.shift(1).file_key,
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
            by_cat: dict[str, list[WeeklyTask]] = {}
            for t in on:
                by_cat.setdefault(t.category, []).append(t)
            ordered = list(CATEGORY_ORDER) + [c for c in by_cat if c not in CATEGORY_ORDER]
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
                        "done": t.done,
                        "relative_path": t.relative_path,
                    }
                    for t in by_cat.get(slug, [])
                ]
                categories.append(
                    {
                        "slug": slug,
                        "label": CATEGORY_LABELS.get(slug, slug),
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
