"""Tests for shared/locks.py — advisory_lock().

Test coverage:
- Basic acquire + release (context manager)
- LockTimeoutError raised when lock is already held
- Two-worker concurrency test: same key, only one wins at a time
- Different keys can be held concurrently (no cross-key interference)
- Lock is released on exception inside the block (finally clause)
- Schema initialisation is idempotent (safe to call _ensure_schema multiple times)
"""

from __future__ import annotations

import sqlite3
import threading
import time
from pathlib import Path

import pytest

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def conn(tmp_path: Path) -> sqlite3.Connection:
    """Fresh in-process SQLite connection per test."""
    db = tmp_path / "locks_test.db"
    c = sqlite3.connect(str(db), check_same_thread=False)
    c.row_factory = sqlite3.Row
    c.execute("PRAGMA journal_mode=WAL")
    yield c
    c.close()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _has_lock_row(conn: sqlite3.Connection, key: str) -> bool:
    """Return True if `advisory_locks` table has a row for `key`."""
    row = conn.execute("SELECT 1 FROM advisory_locks WHERE key = ?", (key,)).fetchone()
    return row is not None


# ---------------------------------------------------------------------------
# Tests: basic acquire + release
# ---------------------------------------------------------------------------


def test_basic_acquire_release(conn):
    from shared.locks import advisory_lock

    with advisory_lock(conn, key="test_key"):
        assert _has_lock_row(conn, "test_key"), "Lock row should exist inside block"

    assert not _has_lock_row(conn, "test_key"), "Lock row should be deleted after block"


def test_acquire_twice_sequential(conn):
    """Same key can be acquired in two sequential blocks (not concurrent)."""
    from shared.locks import advisory_lock

    with advisory_lock(conn, key="seq_key"):
        pass  # released

    with advisory_lock(conn, key="seq_key"):
        pass  # should succeed immediately

    assert not _has_lock_row(conn, "seq_key")


# ---------------------------------------------------------------------------
# Tests: timeout when lock is held
# ---------------------------------------------------------------------------


def test_lock_timeout_when_held(tmp_path: Path):
    """LockTimeoutError raised when another holder doesn't release within timeout_s."""
    from shared.locks import LockTimeoutError, advisory_lock

    db = tmp_path / "timeout_test.db"

    # Use two separate connections to simulate two "processes" within the same test
    conn_a = sqlite3.connect(str(db), check_same_thread=False)
    conn_a.execute("PRAGMA journal_mode=WAL")
    conn_b = sqlite3.connect(str(db), check_same_thread=False)
    conn_b.execute("PRAGMA journal_mode=WAL")

    ready = threading.Event()
    hold_lock = threading.Event()
    released = threading.Event()

    def holder():
        with advisory_lock(conn_a, key="contested_key", timeout_s=5.0):
            ready.set()  # signal: lock is held
            hold_lock.wait(timeout=3.0)  # wait for test to try acquiring
        released.set()

    t = threading.Thread(target=holder, daemon=True)
    t.start()
    ready.wait(timeout=5.0)

    # Now try to acquire with a short timeout → should fail
    with pytest.raises(LockTimeoutError):
        with advisory_lock(conn_b, key="contested_key", timeout_s=0.2):
            pass

    hold_lock.set()
    t.join(timeout=5.0)

    conn_a.close()
    conn_b.close()


# ---------------------------------------------------------------------------
# Tests: lock released on exception inside block
# ---------------------------------------------------------------------------


def test_lock_released_on_exception(conn):
    """Lock row must be removed even when the protected block raises."""
    from shared.locks import advisory_lock

    with pytest.raises(ValueError, match="oops"):
        with advisory_lock(conn, key="exc_key"):
            raise ValueError("oops")

    assert not _has_lock_row(conn, "exc_key"), "Lock must be released after exception"


# ---------------------------------------------------------------------------
# Tests: different keys are independent
# ---------------------------------------------------------------------------


def test_different_keys_independent(tmp_path: Path):
    """Two threads can hold locks on different keys simultaneously.

    Connections are opened before threads start to avoid PRAGMA WAL racing
    with BEGIN IMMEDIATE from the other connection.
    """
    from shared.locks import advisory_lock

    db = tmp_path / "multi_key.db"
    # Pre-create connections with WAL and SQLite busy-timeout
    conn_a = sqlite3.connect(str(db), check_same_thread=False, timeout=10.0)
    conn_a.execute("PRAGMA journal_mode=WAL")
    conn_b = sqlite3.connect(str(db), check_same_thread=False, timeout=10.0)
    conn_b.execute("PRAGMA journal_mode=WAL")

    both_holding = threading.Event()
    done_a = threading.Event()
    errors: list[Exception] = []

    def thread_a():
        try:
            with advisory_lock(conn_a, key="key_alpha", timeout_s=5.0):
                both_holding.wait(timeout=3.0)
        except Exception as exc:
            errors.append(exc)
        finally:
            done_a.set()
            conn_a.close()

    def thread_b():
        try:
            with advisory_lock(conn_b, key="key_beta", timeout_s=5.0):
                both_holding.set()
                done_a.wait(timeout=5.0)
        except Exception as exc:
            errors.append(exc)
        finally:
            conn_b.close()

    t_a = threading.Thread(target=thread_a, daemon=True)
    t_b = threading.Thread(target=thread_b, daemon=True)
    t_a.start()
    t_b.start()
    t_a.join(timeout=10.0)
    t_b.join(timeout=10.0)

    assert not errors, f"Thread errors: {errors}"
    assert not t_a.is_alive(), "Thread A should have completed"
    assert not t_b.is_alive(), "Thread B should have completed"


# ---------------------------------------------------------------------------
# Tests: two-worker concurrency — same key, serialised access
# ---------------------------------------------------------------------------


def test_two_workers_serialised_on_same_key(tmp_path: Path):
    """Two workers competing for same key must not overlap (mutual exclusion).

    We record entry/exit timestamps for both workers and verify they don't
    overlap.  ADR-005b §2.1: prevents duplicate WP post creation.

    Connections are opened *before* threads start (with SQLite timeout) to
    avoid PRAGMA journal_mode=WAL racing with another connection's transaction.
    """
    from shared.locks import advisory_lock

    db = tmp_path / "concurrent.db"
    results: list[tuple[float, float, int]] = []  # (enter_ts, exit_ts, worker_id)

    ts_lock = threading.Lock()

    # Create both connections before spawning threads so PRAGMA WAL doesn't
    # race with an active BEGIN IMMEDIATE transaction from the other thread.
    conns = [sqlite3.connect(str(db), check_same_thread=False, timeout=15.0) for _ in range(2)]
    for c in conns:
        c.execute("PRAGMA journal_mode=WAL")

    errors: list[Exception] = []

    def worker(worker_id: int, c: sqlite3.Connection) -> None:
        try:
            with advisory_lock(c, key="shared_draft_key", timeout_s=10.0):
                enter_ts = time.monotonic()
                time.sleep(0.05)  # simulate work inside critical section
                exit_ts = time.monotonic()
                with ts_lock:
                    results.append((enter_ts, exit_ts, worker_id))
        except Exception as exc:
            errors.append(exc)
        finally:
            c.close()

    threads = [threading.Thread(target=worker, args=(i, conns[i]), daemon=True) for i in range(2)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=20.0)

    assert not errors, f"Worker errors: {errors}"
    assert len(results) == 2, "Both workers should have completed"

    # Sort by enter time
    results.sort(key=lambda r: r[0])
    w1_enter, w1_exit, _ = results[0]
    w2_enter, w2_exit, _ = results[1]

    # Worker 2 must not have entered before Worker 1 exited
    assert w2_enter >= w1_exit - 0.01, (
        f"Overlap detected: w1=({w1_enter:.3f},{w1_exit:.3f}) w2=({w2_enter:.3f},{w2_exit:.3f})"
    )


# ---------------------------------------------------------------------------
# Tests: schema initialisation is idempotent
# ---------------------------------------------------------------------------


def test_ensure_schema_idempotent(conn):
    """_ensure_schema can be called multiple times without error."""
    from shared.locks import _ensure_schema

    _ensure_schema(conn)
    _ensure_schema(conn)  # second call should be no-op
    _ensure_schema(conn)  # third call too

    # Table must exist
    row = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name='advisory_locks'"
    ).fetchone()
    assert row is not None
