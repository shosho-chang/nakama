"""Backfill — re-link existing Google Calendar events onto tasks whose vault
record lost the link (修修 回饋 item 4, option b「連舊任務一起修」).

A task created via Nami's Slack ``create_task`` wrote only a ``scheduled`` date —
no ``plan[]`` entry carrying a ``calendar_event_id``. If that task's day was
nonetheless projected to Google (the event carries the
``lifeos_task = {slug}@{date}`` idempotency key — e.g. via ``schedule_entry`` or
Nami's ``create_calendar_event``), the weekly dashboard still shows it un-linked
because ``is_linked`` reads only the ``plan[]`` entry's ``calendar_event_id``.

This reconciler finds the keyed event by ``find_event_by_idempotency_key`` and
writes its id back into the ``plan[]`` entry. **Link only — it never creates an
event** (creating is the forward-fix's job in ``_tool_create_task``). Idempotent:
re-running re-links the same events. Events with no ``lifeos_task`` key (e.g.
hand-made in Google Calendar) cannot be matched and are reported as ``missing``.
"""

from __future__ import annotations

import unicodedata
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any, Callable, Iterable, Optional
from zoneinfo import ZoneInfo

from shared import google_calendar
from shared.log import get_logger
from shared.weekly_indexer import WeeklyIndexer
from shared.weekly_writer import TASKS_DIR, _read_task, _write_task, upsert_plan_entry

logger = get_logger("nakama.calendar_reconcile")

TAIPEI = ZoneInfo("Asia/Taipei")


@dataclass
class ReconcileReport:
    """Outcome of a reconcile pass. Tuples are display-ready."""

    linked: list[tuple[str, str, str]] = field(default_factory=list)  # (slug, date, event_id)
    missing: list[tuple[str, str]] = field(default_factory=list)  # (slug, date) — no keyed event
    errors: list[tuple[str, str]] = field(default_factory=list)  # (slug, error)
    dry_run: bool = False


def _scheduled_already_linked(task) -> bool:
    """True iff a ``plan[]`` entry on the task's scheduled date already carries an
    event id — nothing to reconcile."""
    return any(getattr(a, "event_id", "") and a.date == task.scheduled for a in task.plan)


def reconcile_scheduled_tasks(vault_root: Path, *, dry_run: bool = False) -> ReconcileReport:
    """Link existing keyed GCal events onto scheduled-but-unlinked tasks.

    Candidate = has a ``scheduled`` date AND no ``plan[]`` entry on that date is
    already linked. For each, look up ``{slug}@{date}`` in Google Calendar; if an
    event is found, write its id into the plan entry (unless ``dry_run``).
    """
    report = ReconcileReport(dry_run=dry_run)
    indexer = WeeklyIndexer(vault_root)
    for task in indexer.read_tasks():
        if not task.scheduled or _scheduled_already_linked(task):
            continue
        key = f"{task.slug}@{task.scheduled.isoformat()}"
        try:
            event = google_calendar.find_event_by_idempotency_key(key)
        except Exception as exc:  # noqa: BLE001 — GCal down / auth expired → report, keep going
            logger.warning("reconcile lookup failed %s: %s", task.slug, exc)
            report.errors.append((task.slug, str(exc)))
            continue
        if event is None:
            report.missing.append((task.slug, task.scheduled.isoformat()))
            continue
        if not dry_run:
            try:
                upsert_plan_entry(
                    vault_root,
                    task.slug,
                    day=task.scheduled,
                    pomodoros=task.est_pomodoros or 1,
                    calendar_event_id=event.id,
                )
            except Exception as exc:  # noqa: BLE001 — one bad write shouldn't sink the batch
                logger.warning("reconcile write-back failed %s: %s", task.slug, exc)
                report.errors.append((task.slug, str(exc)))
                continue
        report.linked.append((task.slug, task.scheduled.isoformat(), event.id))
    return report


# ── Drift sweep (N543) ────────────────────────────────────────────────────────
# reconcile_scheduled_tasks (above) only links events that carry the exact
# ``{slug}@{date}`` idempotency key — so an event whose key/title doesn't match the
# task slug (e.g. event「知識衛星｜正課拍攝」vs task「…0724」) is reported as *missing* and
# the drift is never healed. The sweep below is the wider safety net修修 asked for: it
# matches by (title, date), classifies every kind of task↔calendar drift, and auto-links
# only the unambiguous cases — everything else is REPORTED (never silently "fixed").


def _norm(title: str | None) -> str:
    return unicodedata.normalize("NFC", (title or "").strip())


def _dt(iso: str) -> Optional[datetime]:
    """A timed ISO boundary → datetime; None for blank / date-only / unparseable."""
    if not iso or "T" not in iso:
        return None
    try:
        return datetime.fromisoformat(iso)
    except ValueError:
        return None


@dataclass(frozen=True)
class TaskSlot:
    """One timed scheduling intent from a task (plan[] entry, or bare ``scheduled``)."""

    slug: str
    title: str
    date_iso: str
    start_iso: str
    end_iso: str
    event_id: str


@dataclass(frozen=True)
class Drift:
    kind: str  # unlinked | no_event | time_mismatch | dangling | orphan_event
    date_iso: str
    title: str
    detail: str
    task_slug: Optional[str] = None
    event_id: Optional[str] = None
    repairable: bool = False


@dataclass
class DriftReport:
    drifts: list[Drift] = field(default_factory=list)
    repaired: list[str] = field(default_factory=list)
    checked_tasks: int = 0
    checked_events: int = 0

    @property
    def clean(self) -> bool:
        return not self.drifts and not self.repaired

    def slack_text(self) -> str:
        """Compact Slack body — empty string when there is nothing worth pinging about."""
        if self.clean:
            return ""
        lines = ["*📅 任務 ↔ 行事曆對齊檢查*"]
        if self.repaired:
            lines.append(f"\n🔧 已自動連結 {len(self.repaired)} 筆：")
            lines += [f"  • {r}" for r in self.repaired]
        if self.drifts:
            lines.append(f"\n⚠️ 需要你確認 {len(self.drifts)} 筆：")
            lines += [f"  • [{d.kind}] {d.date_iso} {d.title} — {d.detail}" for d in self.drifts]
        return "\n".join(lines)


def _iter_task_slots(vault_root: Path) -> Iterable[TaskSlot]:
    """Every timed scheduling intent across not-done task files. Reads raw frontmatter so a
    bare ``scheduled: <datetime>`` with no plan[] (the 0724/0731 shape) is still seen."""
    tasks_dir = Path(vault_root) / TASKS_DIR
    if not tasks_dir.is_dir():
        return
    for path in sorted(tasks_dir.glob("*.md")):
        try:
            fm, _ = _read_task(path)
        except Exception:  # noqa: BLE001 — one malformed file must not abort the sweep
            logger.warning("calendar_reconcile: could not read %s", path.name)
            continue
        if not isinstance(fm, dict):
            continue
        if str(fm.get("status") or "to-do") == "done" or fm.get("✅") is True:
            continue
        slug = path.stem
        title = _norm(str(fm.get("title") or slug))

        plan = fm.get("plan")
        emitted = False
        if isinstance(plan, list):
            for e in plan:
                if not isinstance(e, dict):
                    continue
                start = str(e.get("start") or "")
                if "T" not in start:  # only TIMED blocks are calendar-eligible
                    continue
                emitted = True
                yield TaskSlot(
                    slug,
                    title,
                    str(e.get("date") or start[:10]),
                    start,
                    str(e.get("end") or ""),
                    str(e.get("calendar_event_id") or e.get("event_id") or "").strip(),
                )
        if not emitted:  # legacy bare `scheduled: <datetime>`
            sched = str(fm.get("scheduled") or "")
            if "T" in sched:
                yield TaskSlot(
                    slug,
                    title,
                    sched[:10],
                    sched,
                    str(fm.get("scheduled_end") or ""),
                    str(fm.get("calendar_event_id") or "").strip(),
                )


def audit(
    vault_root: Path,
    events: list,
    *,
    today: Optional[date] = None,
    window_days: int = 120,
) -> DriftReport:
    """Pure drift computation given ``events`` (a list of ``google_calendar.CalendarEvent``).
    Only slots dated within ``[today, today+window_days]`` are checked, so an event outside
    the fetched window is never mistaken for a dangling link."""
    today = today or datetime.now(TAIPEI).date()
    horizon = today + timedelta(days=window_days)
    events_by_id = {e.id: e for e in events}
    events_by_key: dict[tuple[str, str], Any] = {}
    for e in events:
        events_by_key.setdefault((_norm(e.title), str(e.start)[:10]), e)

    report = DriftReport(checked_events=len(events))
    task_titles: set[str] = set()
    linked_ids: set[str] = set()
    seen: set[str] = set()

    for slot in _iter_task_slots(vault_root):
        seen.add(slot.slug)
        task_titles.add(slot.title)
        try:
            d = date.fromisoformat(slot.date_iso[:10])
        except ValueError:
            continue
        in_window = today <= d <= horizon

        if slot.event_id:
            linked_ids.add(slot.event_id)
            ev = events_by_id.get(slot.event_id)
            if ev is None:
                if in_window:
                    report.drifts.append(
                        Drift(
                            "dangling",
                            slot.date_iso,
                            slot.title,
                            "任務連到的 Google 事件已不存在（可能被刪）",
                            task_slug=slot.slug,
                            event_id=slot.event_id,
                        )
                    )
                continue
            if _dt(slot.start_iso) != _dt(ev.start) or (
                slot.end_iso and _dt(slot.end_iso) != _dt(ev.end)
            ):
                report.drifts.append(
                    Drift(
                        "time_mismatch",
                        slot.date_iso,
                        slot.title,
                        f"任務 {slot.start_iso[11:16]}–{slot.end_iso[11:16]} ≠ "
                        f"行事曆 {str(ev.start)[11:16]}–{str(ev.end)[11:16]}",
                        task_slug=slot.slug,
                        event_id=slot.event_id,
                    )
                )
            continue

        match = events_by_key.get((slot.title, slot.date_iso[:10]))
        if match is not None:
            linked_ids.add(match.id)  # claimed by this slot → not an orphan
            report.drifts.append(
                Drift(
                    "unlinked",
                    slot.date_iso,
                    slot.title,
                    f"任務已排程但沒連到行事曆事件 {match.id[:10]}…（可自動連結）",
                    task_slug=slot.slug,
                    event_id=match.id,
                    repairable=True,
                )
            )
        else:
            report.drifts.append(
                Drift(
                    "no_event",
                    slot.date_iso,
                    slot.title,
                    "任務已排程但行事曆上找不到對應事件",
                    task_slug=slot.slug,
                )
            )

    report.checked_tasks = len(seen)

    # orphan events: a task-looking event (its title is the stem of some task title) that no
    # task references — e.g. the event was created but its task never was. Report-only.
    for e in events:
        if e.id in linked_ids:
            continue
        etitle = _norm(e.title)
        if any(t == etitle or t.startswith(etitle + " ") for t in task_titles):
            report.drifts.append(
                Drift(
                    "orphan_event",
                    str(e.start)[:10],
                    e.title,
                    "行事曆有此事件，但沒有任務連到它（任務可能沒建成功）",
                    event_id=e.id,
                )
            )
    return report


def _link_slot(vault_root: Path, slug: str, date_iso: str, event_id: str) -> bool:
    """Write ``calendar_event_id`` onto the task's timed plan entry for ``date_iso`` (or
    promote a bare ``scheduled`` into a linked plan entry). Returns True on a real change."""
    path = Path(vault_root) / TASKS_DIR / f"{slug}.md"
    if not path.exists():
        return False
    fm, body = _read_task(path)
    plan = fm.get("plan")
    plan = [e for e in plan if isinstance(e, dict)] if isinstance(plan, list) else []
    for e in plan:
        if str(e.get("date"))[:10] == date_iso[:10] and "T" in str(e.get("start") or ""):
            if e.get("calendar_event_id") == event_id:
                return False
            e["calendar_event_id"] = event_id
            fm["plan"] = plan
            _write_task(path, fm, body)
            return True
    sched = str(fm.get("scheduled") or "")
    if "T" in sched and sched[:10] == date_iso[:10]:
        plan.append(
            {
                "date": sched[:10],
                "pomodoros": int(fm.get("預估🍅") or 0) or 1,
                "start": sched,
                "end": str(fm.get("scheduled_end") or ""),
                "calendar_event_id": event_id,
            }
        )
        fm["plan"] = plan
        for k in ("scheduled", "scheduled_end", "calendar_event_id"):
            fm.pop(k, None)
        _write_task(path, fm, body)
        return True
    return False


def sweep(
    vault_root: Path,
    *,
    repair: bool = True,
    today: Optional[date] = None,
    window_days: int = 120,
    list_events_fn: Optional[Callable] = None,
) -> DriftReport:
    """Fetch the live calendar, audit for drift, and (when ``repair``) auto-link the
    unambiguous ``unlinked`` cases. Ambiguous drift (mismatch / dangling / orphan) is only
    reported. This is what the daily Franky job runs."""
    today = today or datetime.now(TAIPEI).date()
    if list_events_fn is None:
        list_events_fn = google_calendar.list_events
    events = list_events_fn(
        time_min=datetime.combine(today, datetime.min.time(), TAIPEI),
        time_max=datetime.combine(today + timedelta(days=window_days), datetime.min.time(), TAIPEI),
        max_results=250,
    )
    report = audit(vault_root, events, today=today, window_days=window_days)
    if not repair:
        return report

    remaining: list[Drift] = []
    for d in report.drifts:
        if d.kind == "unlinked" and d.repairable and d.task_slug and d.event_id:
            try:
                if _link_slot(vault_root, d.task_slug, d.date_iso, d.event_id):
                    report.repaired.append(f"{d.date_iso} {d.title} → {d.event_id[:10]}…")
                    continue
            except Exception:  # noqa: BLE001 — a bad file shouldn't abort the run
                logger.warning("calendar_reconcile: repair failed for %s", d.task_slug)
        remaining.append(d)
    report.drifts = remaining
    return report
