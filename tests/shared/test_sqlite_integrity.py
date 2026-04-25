"""Tests for shared/sqlite_integrity.py."""

from __future__ import annotations

import sqlite3
from pathlib import Path

from shared.sqlite_integrity import verify_db


def _make_state_db(path: Path, n_rows: int = 3) -> None:
    conn = sqlite3.connect(str(path))
    conn.execute("CREATE TABLE agent_memory (id INTEGER PRIMARY KEY, content TEXT)")
    conn.execute("CREATE TABLE approval_queue (id INTEGER PRIMARY KEY, status TEXT)")
    for i in range(n_rows):
        conn.execute("INSERT INTO agent_memory (content) VALUES (?)", (f"mem{i}",))
        conn.execute("INSERT INTO approval_queue (status) VALUES (?)", ("approved",))
    conn.commit()
    conn.close()


def _make_corrupt_db(path: Path) -> None:
    """Header looks like SQLite, body is garbage."""
    path.write_bytes(b"SQLite format 3\x00" + b"\xff" * 1024)


def test_verify_db_returns_ok_for_valid_db(tmp_path):
    db = tmp_path / "state.db"
    _make_state_db(db, n_rows=5)

    ok, n_tables, n_rows = verify_db(db)

    assert ok is True
    assert n_tables == 2
    assert n_rows == 10


def test_verify_db_handles_zero_byte_file(tmp_path):
    db = tmp_path / "nakama.db"
    db.touch()

    ok, n_tables, n_rows = verify_db(db)

    assert ok is True
    assert n_tables == 0
    assert n_rows == 0


def test_verify_db_returns_false_for_corrupt_db_without_raising(tmp_path):
    db = tmp_path / "corrupt.db"
    _make_corrupt_db(db)

    ok, _, _ = verify_db(db)
    # Critical contract: must NOT raise sqlite3.DatabaseError to caller —
    # operators see a clean (False, ...) report instead of stack trace.
    assert ok is False


def test_verify_db_counts_only_user_tables_not_sqlite_internal(tmp_path):
    db = tmp_path / "state.db"
    conn = sqlite3.connect(str(db))
    conn.execute("CREATE TABLE my_table (id INTEGER PRIMARY KEY)")
    conn.commit()
    conn.close()

    ok, n_tables, n_rows = verify_db(db)

    assert ok is True
    # Just the user table — sqlite_master / sqlite_sequence are filtered out
    assert n_tables == 1
    assert n_rows == 0
