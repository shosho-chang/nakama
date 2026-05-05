"""Behavior tests for ``shared.book_queue`` (Slice 4A).

The queue is a thin wrapper over ``state.db`` ``book_ingest_queue``. Public API:

- ``enqueue(book_id)`` — insert a new row (status="queued"), or no-op if a
  non-terminal row already exists for that book (idempotent).
- ``next_queued() -> book_id | None`` — FIFO pop-the-oldest-queued (does NOT
  mutate row; status stays "queued" until the skill calls ``mark_status``).
- ``mark_status(book_id, status, *, chapters_done=None, error=None)`` — write
  the new status; bumps ``started_at`` on first ``ingesting``, ``completed_at``
  on terminal ``ingested`` / ``failed`` / ``partial``.

Status FSM:

    queued → ingesting → {ingested, partial, failed}

Re-enqueue while ``status == queued | ingesting`` is a no-op (idempotent).
Re-enqueue after a terminal status DOES create a fresh queue row (so the
user can retry an ingest after a failure).
"""

from __future__ import annotations

import pytest

book_queue = pytest.importorskip(
    "shared.book_queue",
    reason="shared.book_queue is the production module Slice 4A must create",
)
schemas = pytest.importorskip(
    "shared.schemas.books",
    reason="shared.schemas.books extension is the production module Slice 4A must update",
)

enqueue = book_queue.enqueue
next_queued = book_queue.next_queued
mark_status = book_queue.mark_status
QueueStatusError = book_queue.QueueStatusError


# ---------------------------------------------------------------------------
# Fixture: a real book row exists so FK constraint passes
# ---------------------------------------------------------------------------


@pytest.fixture
def book(monkeypatch, tmp_path):
    monkeypatch.setenv("NAKAMA_BOOKS_DIR", str(tmp_path / "books"))
    from shared.book_storage import insert_book
    from shared.schemas.books import Book

    b = Book(
        book_id="alpha",
        title="Alpha Book",
        author=None,
        lang_pair="en-zh",
        genre=None,
        isbn=None,
        published_year=None,
        has_original=True,
        book_version_hash="a" * 64,
        created_at="2026-05-05T00:00:00+00:00",
    )
    insert_book(b)
    return b


# ---------------------------------------------------------------------------
# enqueue
# ---------------------------------------------------------------------------


def test_enqueue_creates_queued_row(book):
    enqueue("alpha")
    assert next_queued() == "alpha"


def test_enqueue_orphan_book_id_raises(book):
    """FK book_ingest_queue.book_id → books.book_id must fire on orphan insert."""
    import sqlite3

    with pytest.raises(sqlite3.IntegrityError):
        enqueue("ghost-book")


def test_enqueue_double_is_idempotent_when_queued(book):
    enqueue("alpha")
    enqueue("alpha")  # second call must NOT create a duplicate row
    # next_queued returns "alpha" once; subsequent call yields None
    assert next_queued() == "alpha"
    mark_status("alpha", "ingesting")
    assert next_queued() is None  # only ingesting / no other queued


def test_enqueue_idempotent_while_ingesting(book):
    enqueue("alpha")
    mark_status("alpha", "ingesting")
    enqueue("alpha")  # should NOT spawn a second row
    # Still no NEW queued rows
    assert next_queued() is None


def test_enqueue_after_terminal_creates_new_row(book):
    enqueue("alpha")
    mark_status("alpha", "ingesting")
    mark_status("alpha", "failed", error="boom")
    enqueue("alpha")  # retry path — fresh queued row
    assert next_queued() == "alpha"


# ---------------------------------------------------------------------------
# next_queued — FIFO
# ---------------------------------------------------------------------------


def test_next_queued_returns_none_when_empty(book):
    assert next_queued() is None


def test_next_queued_fifo_order(book):
    from shared.book_storage import insert_book
    from shared.schemas.books import Book

    insert_book(
        Book(
            book_id="beta",
            title="Beta",
            author=None,
            lang_pair="en-zh",
            genre=None,
            isbn=None,
            published_year=None,
            has_original=True,
            book_version_hash="b" * 64,
            created_at="2026-05-05T00:01:00+00:00",
        )
    )
    enqueue("alpha")
    enqueue("beta")
    assert next_queued() == "alpha"
    mark_status("alpha", "ingesting")
    assert next_queued() == "beta"


# ---------------------------------------------------------------------------
# mark_status — FSM + side effects
# ---------------------------------------------------------------------------


def test_mark_status_sets_started_at_on_first_ingesting(book):
    from shared.state import _get_conn

    enqueue("alpha")
    mark_status("alpha", "ingesting")
    row = (
        _get_conn()
        .execute("SELECT started_at FROM book_ingest_queue WHERE book_id=?", ("alpha",))
        .fetchone()
    )
    assert row["started_at"] is not None


def test_mark_status_sets_completed_at_on_ingested(book):
    from shared.state import _get_conn

    enqueue("alpha")
    mark_status("alpha", "ingesting")
    mark_status("alpha", "ingested", chapters_done=5)
    row = (
        _get_conn()
        .execute(
            "SELECT completed_at, chapters_done FROM book_ingest_queue WHERE book_id=?",
            ("alpha",),
        )
        .fetchone()
    )
    assert row["completed_at"] is not None
    assert row["chapters_done"] == 5


def test_mark_status_writes_error_on_failed(book):
    from shared.state import _get_conn

    enqueue("alpha")
    mark_status("alpha", "ingesting")
    mark_status("alpha", "failed", error="parse_book threw on chapter 3")
    row = (
        _get_conn()
        .execute("SELECT status, error FROM book_ingest_queue WHERE book_id=?", ("alpha",))
        .fetchone()
    )
    assert row["status"] == "failed"
    assert "chapter 3" in row["error"]


def test_mark_status_invalid_status_raises(book):
    enqueue("alpha")
    with pytest.raises(QueueStatusError):
        mark_status("alpha", "totally-bogus")


def test_mark_status_unknown_book_raises(book):
    with pytest.raises(LookupError):
        mark_status("never-enqueued", "ingesting")


# ---------------------------------------------------------------------------
# Schema — BookIngestQueueEntry
# ---------------------------------------------------------------------------


def test_book_ingest_queue_entry_round_trip():
    Entry = schemas.BookIngestQueueEntry
    e = Entry(
        book_id="alpha",
        status="queued",
        requested_at="2026-05-05T00:00:00Z",
        started_at=None,
        completed_at=None,
        chapters_done=0,
        error=None,
    )
    assert Entry(**e.model_dump()) == e


def test_book_ingest_queue_entry_extra_forbid():
    Entry = schemas.BookIngestQueueEntry
    from pydantic import ValidationError

    with pytest.raises(ValidationError):
        Entry(
            book_id="alpha",
            status="queued",
            requested_at="2026-05-05T00:00:00Z",
            started_at=None,
            completed_at=None,
            chapters_done=0,
            error=None,
            future_field="oops",  # type: ignore[call-arg]
        )


def test_book_ingest_queue_table_columns():
    from shared.state import _get_conn

    cols = {
        row[1] for row in _get_conn().execute("PRAGMA table_info(book_ingest_queue)").fetchall()
    }
    expected = {
        "book_id",
        "status",
        "requested_at",
        "started_at",
        "completed_at",
        "chapters_done",
        "error",
    }
    assert expected.issubset(cols), f"missing: {expected - cols}"
