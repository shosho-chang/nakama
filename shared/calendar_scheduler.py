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
from datetime import date, datetime
from pathlib import Path
from typing import Optional

from shared import google_calendar
from shared.google_calendar import CalendarEvent
from shared.log import get_logger
from shared.weekly_writer import (
    WeeklyWriteError,
    read_entry_event_id,
    remove_plan_entry,
    reschedule_task_block,
    schedule_task_block,
    task_file_token,
    unlink_calendar_block,
    unschedule_task_block,
    upsert_plan_entry,
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


# ── v3 per-entry scheduling (ADR-041 v3) ───────────────────────────────────────
# One Google event per plan[] entry, keyed {slug}@{date}. schedule_entry is the
# merged 「排入」 path: vault-first upsert, then either reschedule the SAME event
# in place (preserve identity — V3a) if the entry is already linked, or find-or-
# create. cancel/unlink act on one entry. Vault writes are authoritative; Google
# is best-effort (status, never an exception — D2).


def schedule_entry(
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
    """v3 merged 「排入」: write the timed ``plan[]`` entry for ``start``'s date, then
    project it. If that entry is **already linked**, PATCH the same event in place
    (preserve identity, re-key for the date — V3a); else find-or-create keyed
    ``{slug}@{date}`` (per-entry orphan guard via the date-scoped key — V3b). The
    vault upsert is authoritative; the Google step degrades to a status (D2)."""
    day = start.date()
    existing = read_entry_event_id(vault_root, task_slug, day)  # "" if new/unlinked
    start_iso, end_iso, token = upsert_plan_entry(
        vault_root,
        task_slug,
        day=day,
        pomodoros=pomodoros,
        start=start,
        reason=reason,
        expected_token=expected_token,
    )
    idem = f"{task_slug}@{day.isoformat()}"

    if existing:  # reschedule the SAME event in place (V3a — no delete+recreate)
        if not force:
            try:
                clash = [
                    c
                    for c in google_calendar.find_conflicts(start_iso, end_iso)
                    if c.id != existing
                ]
            except Exception as exc:  # noqa: BLE001
                logger.warning(
                    "schedule_entry conflict pre-check unavailable %s: %s", task_slug, exc
                )
                return ScheduleOutcome(start_iso, end_iso, token, UNAVAILABLE, event_id=existing)
            if clash:
                return ScheduleOutcome(
                    start_iso, end_iso, token, CONFLICT, event_id=existing, conflicts=tuple(clash)
                )
        try:
            google_calendar.update_event(
                existing, title=title, start=start_iso, end=end_iso, idempotency_key=idem
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning("schedule_entry update unavailable %s: %s", task_slug, exc)
            return ScheduleOutcome(start_iso, end_iso, token, UNAVAILABLE, event_id=existing)
        return ScheduleOutcome(start_iso, end_iso, token, CREATED, event_id=existing)

    # new → find-or-create keyed by the day
    try:
        result = google_calendar.create_event(
            title=title,
            start=start_iso,
            end=end_iso,
            check_conflict=not force,
            idempotency_key=idem,
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning("schedule_entry create unavailable %s: %s", task_slug, exc)
        return ScheduleOutcome(start_iso, end_iso, token, UNAVAILABLE)
    if isinstance(result, list):
        return ScheduleOutcome(start_iso, end_iso, token, CONFLICT, conflicts=tuple(result))
    try:  # write-back the event id onto this entry (start=None keeps the time just written)
        _, _, token = upsert_plan_entry(
            vault_root,
            task_slug,
            day=day,
            pomodoros=pomodoros,
            reason=reason,
            calendar_event_id=result.id,
            expected_token=token,
        )
    except WeeklyWriteError as exc:
        logger.warning(
            "schedule_entry write-back failed %s; rolling back %s: %s", task_slug, result.id, exc
        )
        try:
            google_calendar.delete_event(result.id)
        except Exception:  # noqa: BLE001
            logger.exception("rollback delete_event(%s) failed — orphan remains", result.id)
        return ScheduleOutcome(start_iso, end_iso, token, FAILED)
    return ScheduleOutcome(start_iso, end_iso, token, CREATED, event_id=result.id)


def cancel_entry(
    vault_root: Path,
    task_slug: str,
    *,
    day: date,
    drop_plan: bool,
    expected_token: Optional[str] = None,
) -> CancelOutcome:
    """Per-entry cancel (V3d). ``drop_plan=False`` = 「移出行事曆」 (clear the entry's
    start/end/event_id, keep the entry); ``drop_plan=True`` = 「取消這天的安排」 (delete
    the event AND drop the entry, `done`-safe). Deletes the Google event best-effort."""
    event_id = read_entry_event_id(vault_root, task_slug, day)
    if drop_plan:
        removed = remove_plan_entry(vault_root, task_slug, day)  # done-safe
        if not removed:  # done-protected → degrade to unlink (keep history, clear projection)
            token = _clear_entry_projection(
                vault_root, task_slug, day, expected_token=expected_token
            )
        else:
            token = task_file_token(vault_root, task_slug)
    else:
        token = _clear_entry_projection(vault_root, task_slug, day, expected_token=expected_token)
    return _delete_best_effort(task_slug, event_id, token)


def _clear_entry_projection(
    vault_root: Path, task_slug: str, day: date, *, expected_token: Optional[str]
) -> str:
    """Clear start/end/calendar_event_id on the entry for ``day``, keep the entry."""
    from shared.weekly_writer import _plan_list, _read_task, _task_path, _write_task

    path = _task_path(vault_root, task_slug)
    fm, body = _read_task(path)
    for e in _plan_list(fm):
        if e.get("date") and str(e["date"])[:10] == day.isoformat():
            for k in ("start", "end", "calendar_event_id"):
                e.pop(k, None)
    fm["plan"] = _plan_list(fm)
    _write_task(path, fm, body)
    return task_file_token(vault_root, task_slug)


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
                c
                for c in google_calendar.find_conflicts(scheduled, scheduled_end)
                if c.id != event_id
            ]
        except Exception as exc:  # noqa: BLE001 — outage degrades, never blocks (D2)
            logger.warning("reschedule conflict pre-check unavailable for %s: %s", task_slug, exc)
            return ScheduleOutcome(scheduled, scheduled_end, token, UNAVAILABLE, event_id=event_id)
        if conflicts:
            return ScheduleOutcome(
                scheduled,
                scheduled_end,
                token,
                CONFLICT,
                event_id=event_id,
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
