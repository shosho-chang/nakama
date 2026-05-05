"""Behavior tests for ``book_progress`` table migration + ``BookProgress`` schema.

Slice 3A scope:

- ``shared/state.py::_init_tables`` provisions the ``book_progress`` table with
  ``book_id`` PK + FK→books + the per-position columns spelled out in #381.
- ``shared/schemas/books.py`` exports ``BookProgress`` (pydantic, extra="forbid")
  with the same column shape so the API layer can serialize/parse it.

Tests touch the DB through ``shared.state._get_conn`` (autoused by
``tests/conftest.py::isolated_db``).
"""

from __future__ import annotations

import sqlite3

import pytest


def test_book_progress_table_exists():
    from shared.state import _get_conn

    conn = _get_conn()
    rows = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name='book_progress'"
    ).fetchall()
    assert len(rows) == 1


def test_book_progress_columns_match_schema():
    from shared.state import _get_conn

    conn = _get_conn()
    cols = {row[1] for row in conn.execute("PRAGMA table_info(book_progress)").fetchall()}
    expected = {
        "book_id",
        "last_cfi",
        "last_chapter_ref",
        "last_spread_idx",
        "percent",
        "total_reading_seconds",
        "updated_at",
    }
    assert expected.issubset(cols), f"missing columns: {expected - cols}"


def test_book_progress_book_id_is_primary_key():
    from shared.state import _get_conn

    conn = _get_conn()
    rows = conn.execute("PRAGMA table_info(book_progress)").fetchall()
    pk_cols = {row[1] for row in rows if row[5]}
    assert pk_cols == {"book_id"}


def test_book_progress_foreign_key_to_books():
    """FK on book_id → books.book_id, ON DELETE CASCADE so progress dies with the book."""
    from shared.state import _get_conn

    conn = _get_conn()
    fks = conn.execute("PRAGMA foreign_key_list(book_progress)").fetchall()
    assert any(fk[2] == "books" and fk[3] == "book_id" and fk[4] == "book_id" for fk in fks), (
        f"no FK book_progress.book_id → books.book_id; got {fks}"
    )


def test_book_progress_fk_blocks_orphan_insert():
    """Inserting a row whose book_id has no matching books row must raise."""
    from shared.state import _get_conn

    conn = _get_conn()
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute(
            """INSERT INTO book_progress
               (book_id, last_cfi, last_chapter_ref, last_spread_idx, percent,
                total_reading_seconds, updated_at)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            ("ghost-book", "epubcfi(/6/2!/4/2:0)", "ch1", 0, 0.0, 0, "2026-05-05T00:00:00Z"),
        )
        conn.commit()


def test_book_progress_init_is_idempotent():
    """Calling _init_tables twice must not raise — the migration uses
    CREATE TABLE IF NOT EXISTS / equivalent."""
    from shared.state import _get_conn, _init_tables

    conn = _get_conn()
    _init_tables(conn)
    _init_tables(conn)


def test_book_progress_schema_round_trip():
    schemas = pytest.importorskip(
        "shared.schemas.books",
        reason="shared.schemas.books extension is the production module Slice 3A must update",
    )
    BookProgress = schemas.BookProgress

    p = BookProgress(
        book_id="alpha",
        last_cfi="epubcfi(/6/4!/4/2:0)",
        last_chapter_ref="ch01.xhtml",
        last_spread_idx=3,
        percent=0.42,
        total_reading_seconds=120,
        updated_at="2026-05-05T00:00:00Z",
    )
    assert BookProgress(**p.model_dump()) == p


def test_book_progress_schema_forbids_extra_fields():
    schemas = pytest.importorskip("shared.schemas.books")
    BookProgress = schemas.BookProgress

    from pydantic import ValidationError

    with pytest.raises(ValidationError):
        BookProgress(
            book_id="alpha",
            last_cfi="epubcfi(/6/4!/4/2:0)",
            last_chapter_ref="ch01.xhtml",
            last_spread_idx=3,
            percent=0.42,
            total_reading_seconds=120,
            updated_at="2026-05-05T00:00:00Z",
            future_field="oops",  # type: ignore[call-arg]
        )


def test_book_progress_schema_optional_fields():
    """A freshly-opened book may have no CFI yet; schema should accept None
    on the optional fields so the GET endpoint can return a meaningful empty
    state when no row exists."""
    schemas = pytest.importorskip("shared.schemas.books")
    BookProgress = schemas.BookProgress

    p = BookProgress(
        book_id="never-opened",
        last_cfi=None,
        last_chapter_ref=None,
        last_spread_idx=0,
        percent=0.0,
        total_reading_seconds=0,
        updated_at="2026-05-05T00:00:00Z",
    )
    assert p.last_cfi is None
    assert p.last_chapter_ref is None
