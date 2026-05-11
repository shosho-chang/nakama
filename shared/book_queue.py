"""Queue management for background book ingestion (Slice 4A).

Status FSM:  queued → ingesting → {ingested, partial, failed}

Re-enqueue while status is queued or ingesting is a no-op (idempotent).
Re-enqueue after a terminal status replaces the old row with a fresh queued
row (retry path) — history is intentionally not preserved.
"""

from __future__ import annotations

import threading
from datetime import datetime, timezone

from shared.state import _get_conn

VALID_STATUSES = ("queued", "ingesting", "ingested", "partial", "failed")
TERMINAL_STATUSES = ("ingested", "partial", "failed")

_lock = threading.Lock()


class QueueStatusError(ValueError):
    """Raised when an unknown status is passed to mark_status."""


def enqueue(book_id: str) -> None:
    """Insert a queued row for book_id.

    No-op if a non-terminal row already exists.  After a terminal status a
    fresh queued row replaces the old one (retry path).
    """
    now = datetime.now(timezone.utc).isoformat()
    with _lock:
        conn = _get_conn()
        with conn:
            row = conn.execute(
                "SELECT status FROM book_ingest_queue WHERE book_id = ?",
                (book_id,),
            ).fetchone()
            if row is not None and row["status"] not in TERMINAL_STATUSES:
                return
            conn.execute(
                """INSERT OR REPLACE INTO book_ingest_queue
                   (book_id, status, requested_at, started_at, completed_at,
                    chapters_done, error)
                   VALUES (?, 'queued', ?, NULL, NULL, 0, NULL)""",
                (book_id, now),
            )


def cancel(book_id: str) -> bool:
    """Delete the queue row for ``book_id`` if its status is ``queued``.

    Returns True if a row was removed; False if no row exists or the entry has
    already started ingesting (which we cannot abort safely from the API).
    """
    with _lock:
        conn = _get_conn()
        with conn:
            cur = conn.execute(
                "DELETE FROM book_ingest_queue WHERE book_id = ? AND status = 'queued'",
                (book_id,),
            )
            return cur.rowcount > 0


def delete_queue_row(book_id: str) -> None:
    """Remove any queue row for ``book_id`` regardless of status.

    Used during full book deletion — bypasses the ``cancel()`` safety check
    because the book itself is going away.
    """
    with _lock:
        conn = _get_conn()
        with conn:
            conn.execute("DELETE FROM book_ingest_queue WHERE book_id = ?", (book_id,))


def next_queued() -> str | None:
    """Return the oldest book_id with status='queued', or None if empty.

    Does NOT mutate the row — caller decides when to mark_status('ingesting').
    """
    with _lock:
        conn = _get_conn()
        row = conn.execute(
            "SELECT book_id FROM book_ingest_queue"
            " WHERE status = 'queued'"
            " ORDER BY requested_at ASC"
            " LIMIT 1"
        ).fetchone()
        return row["book_id"] if row else None


def mark_status(
    book_id: str,
    status: str,
    *,
    chapters_done: int | None = None,
    error: str | None = None,
) -> None:
    """Update status for the queue row of book_id.

    Sets started_at on first 'ingesting'; sets completed_at on terminal
    statuses; bumps chapters_done if provided; writes error if provided.

    Raises:
        QueueStatusError: unknown status value.
        LookupError: no queue row exists for book_id.
    """
    if status not in VALID_STATUSES:
        raise QueueStatusError(f"Unknown status: {status!r}")
    now = datetime.now(timezone.utc).isoformat()
    with _lock:
        conn = _get_conn()
        with conn:
            row = conn.execute(
                "SELECT started_at FROM book_ingest_queue WHERE book_id = ?",
                (book_id,),
            ).fetchone()
            if row is None:
                raise LookupError(f"No queue row for book_id={book_id!r}")

            updates = ["status = ?"]
            params: list = [status]

            if status == "ingesting" and row["started_at"] is None:
                updates.append("started_at = ?")
                params.append(now)

            if status in TERMINAL_STATUSES:
                updates.append("completed_at = ?")
                params.append(now)

            if chapters_done is not None:
                updates.append("chapters_done = ?")
                params.append(chapters_done)

            if error is not None:
                updates.append("error = ?")
                params.append(error)

            params.append(book_id)
            conn.execute(
                f"UPDATE book_ingest_queue SET {', '.join(updates)} WHERE book_id = ?",
                params,
            )
