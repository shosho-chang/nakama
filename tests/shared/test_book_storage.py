"""Behavior tests for ``shared.book_storage`` + ``shared.schemas.books``
(Slice 1C).

Two halves to the contract:

1. Filesystem side — bilingual.epub (required) and original.epub (optional)
   round-trip through ``store_book_files`` / ``read_book_blob``. ``book_id`` is
   the path component, so it MUST reject anything that could escape
   ``data/books/`` (path traversal, separators, control chars, empty).

2. SQLite side — ``insert_book`` / ``get_book`` / ``list_books`` over a
   ``books`` table that ``shared.state._init_tables`` provisions. The
   ``isolated_db`` autouse fixture (see ``tests/conftest.py``) reroutes the
   connection to a tmp DB.

The schema lives in ``shared/schemas/books.py``; ``Book`` is persisted, so
``schema_version`` + ``extra="forbid"`` are mandatory per project schemas
discipline.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from tests.shared._epub_fixtures import epub_clean, epub_with_cover

storage = pytest.importorskip(
    "shared.book_storage",
    reason="shared.book_storage is the production module Step 1C must create",
)
books_schema = pytest.importorskip(
    "shared.schemas.books",
    reason="shared.schemas.books is created in Step 1C",
)

store_book_files = storage.store_book_files
read_book_blob = storage.read_book_blob
insert_book = storage.insert_book
get_book = storage.get_book
list_books = storage.list_books
BookStorageError = storage.BookStorageError

Book = books_schema.Book


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def books_dir(tmp_path: Path, monkeypatch) -> Path:
    """Reroute ``data/books/`` to a tmp dir for the duration of one test."""
    target = tmp_path / "books"
    monkeypatch.setenv("NAKAMA_BOOKS_DIR", str(target))
    return target


def _make_book(book_id: str = "kahneman-thinking-fast-and-slow", **overrides) -> Book:
    defaults = dict(
        book_id=book_id,
        title="Thinking, Fast and Slow",
        author="Daniel Kahneman",
        lang_pair="en-zh",
        genre=None,
        isbn="9780374533557",
        published_year=2011,
        has_original=True,
        book_version_hash="a" * 64,
        created_at="2026-05-05T00:00:00+00:00",
    )
    defaults.update(overrides)
    return Book(**defaults)


# ---------------------------------------------------------------------------
# Tracer bullet — store + read round-trip.
# ---------------------------------------------------------------------------


def test_store_and_read_bilingual_blob_roundtrip(books_dir: Path):
    blob = epub_clean()
    store_book_files("alpha-book", bilingual=blob)
    assert read_book_blob("alpha-book", lang="bilingual") == blob


# ---------------------------------------------------------------------------
# Optional original.epub side — only present when caller passes it.
# ---------------------------------------------------------------------------


def test_store_with_original_makes_both_readable(books_dir: Path):
    bilingual = epub_clean()
    original = epub_with_cover()
    store_book_files("beta-book", bilingual=bilingual, original=original)
    assert read_book_blob("beta-book", lang="bilingual") == bilingual
    assert read_book_blob("beta-book", lang="en") == original


def test_store_without_original_makes_en_unreadable(books_dir: Path):
    store_book_files("gamma-book", bilingual=epub_clean())
    with pytest.raises(FileNotFoundError):
        read_book_blob("gamma-book", lang="en")


def test_read_bilingual_for_missing_book_raises(books_dir: Path):
    with pytest.raises(FileNotFoundError):
        read_book_blob("never-stored", lang="bilingual")


# ---------------------------------------------------------------------------
# Idempotency — re-storing the same book overwrites cleanly, no exception.
# ---------------------------------------------------------------------------


def test_store_is_idempotent_on_identical_bytes(books_dir: Path):
    blob = epub_clean()
    store_book_files("idempotent-book", bilingual=blob)
    store_book_files("idempotent-book", bilingual=blob)
    assert read_book_blob("idempotent-book", lang="bilingual") == blob


def test_store_overwrites_when_bytes_change(books_dir: Path):
    store_book_files("rewrite-book", bilingual=epub_clean())
    store_book_files("rewrite-book", bilingual=epub_with_cover())
    assert read_book_blob("rewrite-book", lang="bilingual") == epub_with_cover()


# ---------------------------------------------------------------------------
# Path traversal defense — book_id is a path component, treat as untrusted.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "evil_id",
    [
        "../etc",
        "../../foo",
        "..",
        "foo/..",
        "foo/bar",  # forward slash separator
        "foo\\bar",  # backslash separator (Windows)
        "",  # empty
        ".",  # current dir
        ".hidden",  # leading dot
        "with\x00null",  # NUL byte
        "a" * 300,  # absurdly long
    ],
)
def test_store_rejects_evil_book_id(books_dir: Path, evil_id: str):
    with pytest.raises(BookStorageError):
        store_book_files(evil_id, bilingual=epub_clean())


@pytest.mark.parametrize("evil_id", ["../secret", "foo/bar", "..\\win"])
def test_read_rejects_evil_book_id(books_dir: Path, evil_id: str):
    with pytest.raises(BookStorageError):
        read_book_blob(evil_id, lang="bilingual")


def test_evil_book_id_does_not_create_anything_on_disk(books_dir: Path):
    """Path-traversal reject must happen BEFORE any filesystem touch — otherwise
    a partial write could leak outside ``data/books/``."""
    with pytest.raises(BookStorageError):
        store_book_files("../escape", bilingual=epub_clean())
    # No file named "escape" anywhere reachable from cwd or the books dir.
    assert not (books_dir.parent / "escape").exists()
    if books_dir.exists():
        assert not any("escape" in p.name for p in books_dir.rglob("*"))


# ---------------------------------------------------------------------------
# Book pydantic schema — extra="forbid" + schema_version pinned.
# ---------------------------------------------------------------------------


def test_book_schema_forbids_unknown_fields():
    from pydantic import ValidationError

    with pytest.raises(ValidationError):
        Book(
            book_id="x",
            title="t",
            author=None,
            lang_pair="en-zh",
            genre=None,
            isbn=None,
            published_year=None,
            has_original=False,
            book_version_hash="b" * 64,
            created_at="2026-05-05T00:00:00+00:00",
            future_field="oops",  # type: ignore[call-arg]
        )


def test_book_schema_version_is_pinned():
    book = _make_book()
    # Pydantic dumps schema_version even if defaulted; persisted shape must
    # carry it so future migrations can dispatch.
    dumped = book.model_dump()
    assert dumped["schema_version"] == 1


# ---------------------------------------------------------------------------
# DB layer — insert_book / get_book / list_books.
# ---------------------------------------------------------------------------


def test_insert_then_get_book_roundtrip():
    book = _make_book()
    insert_book(book)
    fetched = get_book(book.book_id)
    assert fetched is not None
    assert fetched.book_id == book.book_id
    assert fetched.title == book.title
    assert fetched.author == book.author
    assert fetched.lang_pair == book.lang_pair
    assert fetched.has_original is True
    assert fetched.book_version_hash == book.book_version_hash


def test_get_book_returns_none_when_missing():
    assert get_book("never-inserted") is None


def test_list_books_orders_by_created_at_desc():
    insert_book(_make_book(book_id="oldest", created_at="2026-01-01T00:00:00+00:00"))
    insert_book(_make_book(book_id="middle", created_at="2026-03-01T00:00:00+00:00"))
    insert_book(_make_book(book_id="newest", created_at="2026-05-01T00:00:00+00:00"))
    rows = list_books()
    assert [b.book_id for b in rows] == ["newest", "middle", "oldest"]


def test_insert_duplicate_book_id_replaces_or_raises():
    """Either upsert semantics (INSERT OR REPLACE) or raise — not silent
    duplicate. If the implementation chooses to raise, list_books must still
    show exactly one row; if upsert, the second insert must update fields."""
    insert_book(_make_book(book_id="dup", title="first"))
    try:
        insert_book(_make_book(book_id="dup", title="second"))
    except Exception:
        # Raise variant — DB still has exactly one "dup" row from the first insert.
        rows = [b for b in list_books() if b.book_id == "dup"]
        assert len(rows) == 1
        assert rows[0].title == "first"
        return
    # Upsert variant — DB has one "dup" row carrying the latest values.
    rows = [b for b in list_books() if b.book_id == "dup"]
    assert len(rows) == 1
    assert rows[0].title == "second"


# ---------------------------------------------------------------------------
# Migration — books table is provisioned by shared.state._init_tables.
# ---------------------------------------------------------------------------


def test_books_table_columns_match_schema():
    """Pin the column set so future migrations have to update the schema in
    lockstep. The ``isolated_db`` autouse fixture has already pointed
    ``shared.state`` at a tmp DB; calling _get_conn forces _init_tables to run."""
    from shared.state import _get_conn

    conn = _get_conn()
    cols = {row[1] for row in conn.execute("PRAGMA table_info(books)").fetchall()}
    expected = {
        "book_id",
        "title",
        "author",
        "lang_pair",
        "genre",
        "isbn",
        "published_year",
        "has_original",
        "book_version_hash",
        "created_at",
    }
    assert expected.issubset(cols), f"missing columns: {expected - cols}"


def test_books_table_book_id_is_primary_key():
    from shared.state import _get_conn

    conn = _get_conn()
    rows = conn.execute("PRAGMA table_info(books)").fetchall()
    pk_cols = {row[1] for row in rows if row[5]}  # row[5] = pk flag
    assert pk_cols == {"book_id"}
