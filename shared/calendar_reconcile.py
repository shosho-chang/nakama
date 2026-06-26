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

from dataclasses import dataclass, field
from pathlib import Path

from shared import google_calendar
from shared.log import get_logger
from shared.weekly_indexer import WeeklyIndexer
from shared.weekly_writer import upsert_plan_entry

logger = get_logger("nakama.calendar_reconcile")


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
