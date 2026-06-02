"""Calendar-block scheduling orchestrator (ADR-041 Slice 41b).

Composes the authoritative vault write (``weekly_writer.schedule_task_block`` — the
source of truth, D1) with a **best-effort** Google Calendar projection (D2): the
plan write must succeed, the calendar event must not block it. On a calendar outage
the block is still planned (status ``unavailable``); on a time clash the event is not
created (status ``conflict``); on a successful create the event id is written back to
the task, and if *that* write fails the event is rolled back to avoid an orphan
(mirrors Nami's create-with-rollback, ``gateway/handlers/nami.py:1393-1400``).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Optional

from shared import google_calendar
from shared.google_calendar import CalendarEvent
from shared.log import get_logger
from shared.weekly_writer import WeeklyWriteError, schedule_task_block

logger = get_logger("nakama.calendar_scheduler")

# calendar_status values
CREATED = "created"  # event created (or de-duped) + linked
CONFLICT = "conflict"  # time clash; event NOT created (plan still written)
UNAVAILABLE = "unavailable"  # calendar API/auth down; plan written, no event
FAILED = "failed"  # event created but link write-back failed → event rolled back


@dataclass
class ScheduleOutcome:
    """Result of one schedule action. The vault write always succeeded if this is
    returned (calendar failures raise nothing — they surface as ``calendar_status``)."""

    scheduled: str
    scheduled_end: str
    token: str
    calendar_status: str
    event_id: Optional[str] = None
    conflicts: tuple[CalendarEvent, ...] = field(default_factory=tuple)


def schedule_block(
    vault_root: Path,
    task_slug: str,
    *,
    start: datetime,
    pomodoros: int,
    title: str,
    reason: Optional[str] = None,
    expected_token: Optional[str] = None,
    force: bool = False,
) -> ScheduleOutcome:
    """Schedule ``task_slug`` at ``start`` for ``pomodoros`` (1🍅 = 30 min block).

    Step 1 — authoritative vault write (``schedule_task_block``): plan[] + scheduled/
    scheduled_end. Raises (``TaskNotFoundError`` / ``WeekendReasonRequired`` /
    ``WeeklyConflictError``) propagate; nothing was sent to the calendar yet.

    Step 2 — best-effort calendar projection, keyed by ``{slug}@{date}`` for
    idempotency (D7). Returns a :class:`ScheduleOutcome`; the calendar status is data,
    never an exception (D2 — a Google outage must not block planning)."""
    scheduled, scheduled_end, token = schedule_task_block(
        vault_root,
        task_slug,
        start=start,
        pomodoros=pomodoros,
        reason=reason,
        expected_token=expected_token,
    )

    idem = f"{task_slug}@{start.date().isoformat()}"
    try:
        result = google_calendar.create_event(
            title=title,
            start=scheduled,
            end=scheduled_end,
            check_conflict=not force,
            idempotency_key=idem,
        )
    except Exception as exc:  # noqa: BLE001 — any calendar failure degrades, never blocks the plan
        logger.warning("calendar projection unavailable for %s: %s", task_slug, exc)
        return ScheduleOutcome(scheduled, scheduled_end, token, UNAVAILABLE)

    if isinstance(result, list):  # time clash; event not created
        return ScheduleOutcome(scheduled, scheduled_end, token, CONFLICT, conflicts=tuple(result))

    # link the event id back to the task (guarded by the post-step-1 token)
    try:
        _, _, token = schedule_task_block(
            vault_root,
            task_slug,
            start=start,
            pomodoros=pomodoros,
            reason=reason,
            calendar_event_id=result.id,
            expected_token=token,
        )
    except WeeklyWriteError as exc:
        logger.warning(
            "event-id write-back failed for %s; rolling back event %s: %s",
            task_slug,
            result.id,
            exc,
        )
        try:
            google_calendar.delete_event(result.id)
        except Exception:  # noqa: BLE001
            logger.exception("rollback delete_event(%s) failed — orphan event remains", result.id)
        return ScheduleOutcome(scheduled, scheduled_end, token, FAILED)

    return ScheduleOutcome(scheduled, scheduled_end, token, CREATED, event_id=result.id)
