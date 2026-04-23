"""SQLite advisory lock for single-machine concurrency control (ADR-005b §2.1).

Provides a context manager that acquires an exclusive named lock backed by a
dedicated `advisory_locks` table in the shared state.db.  Only one holder per
key is allowed at a time; other waiters raise `LockTimeoutError`.

Design rationale (from ADR-005b §2.1):
- Nakama is a single-worker setup (Phase 1).  The real race window is between
  `find_by_meta` and `create_post` inside Usopp — both happen on the Nakama
  side, so the lock belongs here, not in WordPress.
- SQLite `BEGIN IMMEDIATE` serialises writers at the WAL level; we rely on
  this for the UPSERT + DELETE lifecycle of the lock row.
- The `advisory_locks` table is created in the same state.db used by
  `approval_queue` and `publish_jobs`.  No new infrastructure required.

Usage::

    from shared.locks import advisory_lock, LockTimeoutError

    with advisory_lock(conn, key="usopp_draft_abc123", timeout_s=5.0):
        # critical section — only one holder at a time
        ...

    # or with explicit connection:
    from shared.state import _get_conn
    conn = _get_conn()
    with advisory_lock(conn, key="usopp_draft_abc123"):
        ...
"""

from __future__ import annotations

import sqlite3
import time
from contextlib import contextmanager
from datetime import datetime, timezone
from typing import Generator

from shared.log import get_logger

logger = get_logger("nakama.locks")

# Polling interval while waiting to acquire the lock (seconds).
_POLL_INTERVAL_S = 0.05

# Table DDL — created lazily on first use.
_DDL = """
CREATE TABLE IF NOT EXISTS advisory_locks (
    key         TEXT    NOT NULL,
    acquired_at TEXT    NOT NULL,
    holder_pid  INTEGER NOT NULL,
    PRIMARY KEY (key)
);
"""


def _ensure_schema(conn: sqlite3.Connection) -> None:
    """Create advisory_locks table if it doesn't exist (idempotent).

    Checks sqlite_master directly rather than using a cached flag — this
    is safe because CREATE TABLE IF NOT EXISTS is idempotent, and the check
    is only executed once per lock acquisition (not in a hot loop).
    """
    row = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name='advisory_locks'"
    ).fetchone()
    if row is None:
        conn.execute(_DDL)
        conn.commit()


class LockTimeoutError(TimeoutError):
    """Raised when `advisory_lock` cannot acquire the lock within `timeout_s`."""


@contextmanager
def advisory_lock(
    conn: sqlite3.Connection,
    key: str,
    *,
    timeout_s: float = 5.0,
) -> Generator[None, None, None]:
    """Acquire an exclusive advisory lock on `key`, held for the duration of the block.

    Args:
        conn:      SQLite connection to state.db (WAL mode assumed).
        key:       Lock name; use a namespaced string, e.g. "usopp_draft_<draft_id>".
        timeout_s: Maximum seconds to wait before raising LockTimeoutError.

    Raises:
        LockTimeoutError: Lock could not be acquired within timeout_s.

    The lock is released automatically on block exit (success or exception).
    Re-entrant use from the *same* connection is NOT supported — a second call
    with the same key from the same connection will deadlock (BEGIN IMMEDIATE
    blocks on itself).
    """
    import os

    _ensure_schema(conn)
    pid = os.getpid()
    deadline = time.monotonic() + timeout_s

    # Spin until we can INSERT the lock row (conflict = another holder).
    while True:
        now_iso = datetime.now(timezone.utc).isoformat()
        try:
            # BEGIN IMMEDIATE serialises concurrent writers at WAL level.
            # If another connection holds a write lock we get OperationalError
            # ("database is locked") which we treat the same as a row conflict.
            conn.execute("BEGIN IMMEDIATE")
            conn.execute(
                "INSERT INTO advisory_locks (key, acquired_at, holder_pid) VALUES (?, ?, ?)",
                (key, now_iso, pid),
            )
            conn.execute("COMMIT")
            break  # acquired
        except sqlite3.IntegrityError:
            # PRIMARY KEY conflict — another holder has the row.
            try:
                conn.execute("ROLLBACK")
            except sqlite3.OperationalError:
                pass
        except sqlite3.OperationalError as exc:
            # "database is locked" from BEGIN IMMEDIATE WAL contention.
            try:
                conn.execute("ROLLBACK")
            except sqlite3.OperationalError:
                pass
            # Only suppress locking errors; re-raise others immediately.
            if "locked" not in str(exc).lower():
                raise

        if time.monotonic() >= deadline:
            raise LockTimeoutError(f"Could not acquire advisory_lock({key!r}) within {timeout_s}s")
        time.sleep(_POLL_INTERVAL_S)

    logger.debug("advisory_lock acquired: key=%s pid=%s", key, pid)
    try:
        yield
    finally:
        # Release: DELETE the lock row unconditionally.
        try:
            conn.execute("BEGIN IMMEDIATE")
            conn.execute("DELETE FROM advisory_locks WHERE key = ?", (key,))
            conn.execute("COMMIT")
            logger.debug("advisory_lock released: key=%s pid=%s", key, pid)
        except sqlite3.OperationalError:
            # Best-effort release; if the DB is gone the lock will be stale
            # until the next process restarts.  Log but don't swallow any
            # original exception from the protected block.
            logger.warning("advisory_lock release failed for key=%s — DB may be locked", key)
            try:
                conn.execute("ROLLBACK")
            except sqlite3.OperationalError:
                pass
