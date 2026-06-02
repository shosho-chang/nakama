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
from shared.weekly_writer import (
    WeeklyWriteError,
    reschedule_task_block,
    schedule_task_block,
    unlink_calendar_block,
    unschedule_task_block,
)

logger = get_logger("nakama.calendar_scheduler")

# calendar_status values
CREATED = "created"  # event created/updated (or de-duped) + linked
CONFLICT = "conflict"  # time clash; event NOT created/moved (plan still written)
UNAVAILABLE = "unavailable"  # calendar API/auth down; plan written, no event change
FAILED = "failed"  # event created but link write-back failed → event rolled back

# cancel_status values (the two D9 cancel actions)
CANCELLED = "cancelled"  # vault cleared cleanly; Google event deleted (or none existed)
CANCEL_CAL_FAILED = "cancel_cal_failed"  # vault cleared, but the Google delete errored


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
    return _create_and_link(
        vault_root,
        task_slug,
        start=start,
        pomodoros=pomodoros,
        title=title,
        reason=reason,
        scheduled=scheduled,
        scheduled_end=scheduled_end,
        token=token,
        force=force,
    )


def _create_and_link(
    vault_root: Path,
    task_slug: str,
    *,
    start: datetime,
    pomodoros: int,
    title: str,
    reason: Optional[str],
    scheduled: str,
    scheduled_end: str,
    token: str,
    force: bool,
) -> ScheduleOutcome:
    """Best-effort calendar projection for a block whose vault state (plan[] +
    scheduled/scheduled_end) is **already written**. Creates the event (keyed for
    idempotency, D7) and links its id back via a single ``calendar_event_id`` write;
    a write-back failure rolls the event back (D7). Used by both
    :func:`schedule_block` and the unlinked fallback in :func:`reschedule_block`,
    so the relocate write is never duplicated (41d panel — Gemini §2)."""
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

    # link the event id back to the task (guarded by the post-write token)
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


@dataclass
class CancelOutcome:
    """Result of a D9 cancel action (移出行事曆 / 取消排程). The vault write always
    succeeded if this is returned; the Google delete is best-effort (its outcome is
    ``calendar_status``, never an exception)."""

    token: str
    calendar_status: str  # CANCELLED | CANCEL_CAL_FAILED
    event_id: Optional[str] = None  # the event we tried to delete ("" if none)


def reschedule_block(
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
    """Move an already-projected block to ``start`` (ADR-041 D8).

    Step 1 — authoritative vault relocate (``reschedule_task_block``): the plan[]
    entry moves and ``scheduled`` / ``scheduled_end`` are rewritten; the existing
    ``calendar_event_id`` is preserved. Raises propagate (nothing touched Google).

    Step 2 — best-effort PATCH of the **same** Google event (no new event → no
    orphan). ``update_event`` has no built-in conflict check, so unless ``force``
    we pre-check overlap, **excluding the event being moved** (else a small shift
    would clash with its own old slot). A clash returns ``CONFLICT`` with the event
    left at its old time (the plan is still moved — D2); a Google outage returns
    ``UNAVAILABLE``. NB: the event's idempotency key still encodes the *original*
    date — benign in v1 because a linked task never re-enters the create path (the
    backlog picker is hidden for it); reschedule/rollback locate by event id."""
    scheduled, scheduled_end, token, event_id = reschedule_task_block(
        vault_root,
        task_slug,
        start=start,
        pomodoros=pomodoros,
        reason=reason,
        expected_token=expected_token,
    )
    if not event_id:
        # Not actually linked (e.g. cleared in Obsidian between page-load and
        # submit). The relocate above already wrote plan[] + scheduled, so only the
        # calendar create+link remains — call _create_and_link directly (NOT
        # schedule_block, which would redo the vault write — 41d panel, Gemini §2).
        return _create_and_link(
            vault_root,
            task_slug,
            start=start,
            pomodoros=pomodoros,
            title=title,
            reason=reason,
            scheduled=scheduled,
            scheduled_end=scheduled_end,
            token=token,
            force=force,
        )

    if not force:
        try:
            conflicts = [
                c for c in google_calendar.find_conflicts(scheduled, scheduled_end)
                if c.id != event_id
            ]
        except Exception as exc:  # noqa: BLE001 — outage degrades, never blocks (D2)
            logger.warning("reschedule conflict pre-check unavailable for %s: %s", task_slug, exc)
            return ScheduleOutcome(scheduled, scheduled_end, token, UNAVAILABLE, event_id=event_id)
        if conflicts:
            return ScheduleOutcome(
                scheduled, scheduled_end, token, CONFLICT, event_id=event_id,
                conflicts=tuple(conflicts),
            )

    try:
        google_calendar.update_event(event_id, title=title, start=scheduled, end=scheduled_end)
    except Exception as exc:  # noqa: BLE001 — a Google failure degrades, never blocks the plan
        logger.warning("calendar reschedule unavailable for %s: %s", task_slug, exc)
        return ScheduleOutcome(scheduled, scheduled_end, token, UNAVAILABLE, event_id=event_id)
    return ScheduleOutcome(scheduled, scheduled_end, token, CREATED, event_id=event_id)


def _delete_best_effort(task_slug: str, event_id: str, token: str) -> CancelOutcome:
    """Delete the Google event if there is one; a failure degrades to
    ``CANCEL_CAL_FAILED`` (the vault is already cleared — D2)."""
    if not event_id:
        return CancelOutcome(token, CANCELLED)  # nothing was linked
    try:
        google_calendar.delete_event(event_id)
    except Exception as exc:  # noqa: BLE001 — vault already cleared; surface as status, never raise
        logger.warning("calendar delete failed for %s (event %s): %s", task_slug, event_id, exc)
        return CancelOutcome(token, CANCEL_CAL_FAILED, event_id=event_id)
    return CancelOutcome(token, CANCELLED, event_id=event_id)


def unlink_calendar(
    vault_root: Path,
    task_slug: str,
    *,
    expected_token: Optional[str] = None,
) -> CancelOutcome:
    """「移出行事曆」 (D9): clear the projection fields, **keep** plan[], delete the
    Google event (best-effort). The vault write is authoritative (raises
    propagate); the Google delete surfaces as ``calendar_status``."""
    event_id, token = unlink_calendar_block(vault_root, task_slug, expected_token=expected_token)
    return _delete_best_effort(task_slug, event_id, token)


def cancel_schedule(
    vault_root: Path,
    task_slug: str,
    *,
    expected_token: Optional[str] = None,
) -> CancelOutcome:
    """「取消排程」 (D9): drop the scheduled-date plan[] entry (unless it carries
    done work) **and** clear the projection fields, then delete the Google event
    (best-effort)."""
    event_id, token = unschedule_task_block(vault_root, task_slug, expected_token=expected_token)
    return _delete_best_effort(task_slug, event_id, token)
