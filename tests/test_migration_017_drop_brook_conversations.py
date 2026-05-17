"""Regression test for migrations/017_drop_brook_conversation_tables.sql.

PR-3 (ADR-027 §Decision 8) drops the SQLite tables that backed the
conversational `/brook/chat` flow. This test verifies the SQL file:

1. Drops both tables + the supporting index when applied to a fresh DB
   pre-seeded with the legacy schema.
2. The companion ``.down.sql`` re-creates the empty schema (round-trip
   reversibility check).

The migration is *idempotent* — re-running on an already-clean DB must not
error. SQLite ``DROP ... IF EXISTS`` already provides this guarantee; the
test asserts it explicitly so a future edit that removes ``IF EXISTS``
would be caught.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

_MIGRATIONS = Path(__file__).resolve().parent.parent / "migrations"
_UP_SQL = _MIGRATIONS / "017_drop_brook_conversation_tables.sql"
_DOWN_SQL = _MIGRATIONS / "017_drop_brook_conversation_tables.down.sql"


_LEGACY_DDL = """
CREATE TABLE IF NOT EXISTS brook_conversations (
    id          TEXT PRIMARY KEY,
    topic       TEXT NOT NULL,
    phase       TEXT NOT NULL DEFAULT 'outline',
    kb_context  TEXT NOT NULL DEFAULT '',
    created_at  TEXT NOT NULL,
    updated_at  TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS brook_messages (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    conversation_id TEXT NOT NULL,
    role            TEXT NOT NULL,
    content         TEXT NOT NULL,
    created_at      TEXT NOT NULL,
    FOREIGN KEY (conversation_id) REFERENCES brook_conversations(id)
);

CREATE INDEX IF NOT EXISTS idx_brook_messages_conv
    ON brook_messages(conversation_id, id);
"""


def _existing_tables(conn: sqlite3.Connection) -> set[str]:
    rows = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table'"
    ).fetchall()
    return {r[0] for r in rows}


def _existing_indexes(conn: sqlite3.Connection) -> set[str]:
    rows = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='index' AND name NOT LIKE 'sqlite_%'"
    ).fetchall()
    return {r[0] for r in rows}


@pytest.fixture
def db_with_legacy_brook_tables(tmp_path: Path) -> sqlite3.Connection:
    """Fresh SQLite seeded with the legacy brook_conversations + brook_messages
    schema (mirrors what compose.py's _init_brook_tables() would create)."""
    db_path = tmp_path / "nakama.sqlite"
    conn = sqlite3.connect(str(db_path))
    conn.executescript(_LEGACY_DDL)
    conn.commit()
    return conn


def test_up_migration_drops_both_tables_and_index(db_with_legacy_brook_tables):
    conn = db_with_legacy_brook_tables
    # Pre-condition: tables + index present
    assert {"brook_conversations", "brook_messages"}.issubset(_existing_tables(conn))
    assert "idx_brook_messages_conv" in _existing_indexes(conn)

    # Apply migration
    conn.executescript(_UP_SQL.read_text(encoding="utf-8"))
    conn.commit()

    tables = _existing_tables(conn)
    indexes = _existing_indexes(conn)
    assert "brook_conversations" not in tables
    assert "brook_messages" not in tables
    assert "idx_brook_messages_conv" not in indexes


def test_up_migration_is_idempotent(tmp_path):
    """Applying 017 on a DB that has no Brook tables must not error
    (handles VPS / fresh-install path)."""
    db_path = tmp_path / "fresh.sqlite"
    conn = sqlite3.connect(str(db_path))
    conn.executescript(_UP_SQL.read_text(encoding="utf-8"))
    conn.commit()
    assert "brook_conversations" not in _existing_tables(conn)


def test_down_migration_recreates_empty_schema(db_with_legacy_brook_tables):
    conn = db_with_legacy_brook_tables
    # Drop forward, then roll back.
    conn.executescript(_UP_SQL.read_text(encoding="utf-8"))
    conn.executescript(_DOWN_SQL.read_text(encoding="utf-8"))
    conn.commit()

    tables = _existing_tables(conn)
    indexes = _existing_indexes(conn)
    assert "brook_conversations" in tables
    assert "brook_messages" in tables
    assert "idx_brook_messages_conv" in indexes
    # Round-tripped tables are empty
    assert conn.execute("SELECT COUNT(*) FROM brook_conversations").fetchone()[0] == 0
    assert conn.execute("SELECT COUNT(*) FROM brook_messages").fetchone()[0] == 0
