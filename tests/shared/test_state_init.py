"""Regression tests for shared.state._init_tables migration ordering.

Specifically guards against the bug where CREATE INDEX statements that depend
on columns added by ALTER TABLE migrations (e.g. r2_backup_checks.prefix from
migration 008) were executed BEFORE those migrations ran. On a pre-migration
SQLite DB this caused the first DB access after server restart to die with
``sqlite3.OperationalError: no such column: prefix``.

These tests build the *old* schema explicitly, then call _init_tables and
assert that both the migrated column AND the index that references it exist.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path


def _make_pre_migration_db(db_path: Path) -> None:
    """Create a state.db with the OLD r2_backup_checks schema (no `prefix`).

    Mirrors migrations/004_r2_backup_checks.sql verbatim — pre-migration-008
    state. Also drops the `idx_r2_backup_prefix_time` index that does not yet
    exist on a pre-migration DB.
    """
    conn = sqlite3.connect(str(db_path))
    conn.executescript(
        """
        CREATE TABLE r2_backup_checks (
            id                  INTEGER PRIMARY KEY AUTOINCREMENT,
            checked_at          TEXT NOT NULL,
            latest_object_key   TEXT,
            latest_object_size  INTEGER,
            latest_object_mtime TEXT,
            status              TEXT NOT NULL
                                CHECK (status IN ('ok', 'stale', 'missing', 'too_small')),
            detail              TEXT
        );

        CREATE INDEX idx_r2_backup_time
            ON r2_backup_checks(checked_at DESC);
        """
    )
    conn.commit()
    conn.close()


def _column_exists(conn: sqlite3.Connection, table: str, column: str) -> bool:
    rows = conn.execute(f"PRAGMA table_info({table})").fetchall()
    return any(row[1] == column for row in rows)


def _index_exists(conn: sqlite3.Connection, index: str) -> bool:
    row = conn.execute(
        "SELECT name FROM sqlite_master WHERE type = 'index' AND name = ?",
        (index,),
    ).fetchone()
    return row is not None


def test_init_tables_migrates_pre_existing_db_without_prefix_column(
    tmp_path: Path, monkeypatch
) -> None:
    """Reproduces the bug: pre-migration DB → _init_tables must succeed AND
    add the `prefix` column AND create the dependent index.

    Before the fix, _init_tables raised
    ``sqlite3.OperationalError: no such column: prefix`` because the
    CREATE INDEX statement referencing `prefix` ran inside the same
    executescript() block that creates the table — but the ALTER TABLE
    migration that adds `prefix` only runs after the script completes.
    """
    db_path = tmp_path / "state.db"
    _make_pre_migration_db(db_path)

    # Sanity: our pre-migration DB really does lack the column.
    pre_conn = sqlite3.connect(str(db_path))
    try:
        assert not _column_exists(pre_conn, "r2_backup_checks", "prefix"), (
            "fixture must start without prefix column"
        )
        assert not _index_exists(pre_conn, "idx_r2_backup_prefix_time"), (
            "fixture must start without the prefix-dependent index"
        )
    finally:
        pre_conn.close()

    # Drive _init_tables via the real public surface (_get_conn). Reset the
    # module-level cache and point get_db_path at our pre-migration file.
    import shared.state as state

    if state._conn is not None:
        state._conn.close()
        state._conn = None
    monkeypatch.setattr(state, "get_db_path", lambda: db_path)

    try:
        state._get_conn()  # this is what dies on the bug
    finally:
        # Make sure subsequent tests still get their own isolated_db fixture.
        if state._conn is not None:
            state._conn.close()
            state._conn = None

    # Reopen with a fresh connection to introspect what was persisted.
    check_conn = sqlite3.connect(str(db_path))
    try:
        assert _column_exists(check_conn, "r2_backup_checks", "prefix"), (
            "ALTER TABLE migration must add `prefix` column to r2_backup_checks"
        )
        assert _index_exists(check_conn, "idx_r2_backup_prefix_time"), (
            "CREATE INDEX must run AFTER the ALTER TABLE that adds `prefix`"
        )
    finally:
        check_conn.close()


def test_init_tables_is_idempotent_on_already_migrated_db(tmp_path: Path, monkeypatch) -> None:
    """Calling _init_tables twice in a row must not raise (real-world
    behavior — every server restart re-enters _init_tables)."""
    db_path = tmp_path / "state.db"
    _make_pre_migration_db(db_path)

    import shared.state as state

    if state._conn is not None:
        state._conn.close()
        state._conn = None
    monkeypatch.setattr(state, "get_db_path", lambda: db_path)

    try:
        # First call: runs ALTER TABLE
        state._get_conn()
        # Force re-init by closing and clearing
        state._conn.close()
        state._conn = None
        # Second call: ALTER TABLE should silently no-op via try/except, and
        # CREATE INDEX should be idempotent
        state._get_conn()
    finally:
        if state._conn is not None:
            state._conn.close()
            state._conn = None
