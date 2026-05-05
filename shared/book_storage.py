"""Filesystem + SQLite persistence for translated EPUB books.

Filesystem layout (rooted at ``NAKAMA_BOOKS_DIR``, fallback ``data/books/``):

    data/books/{book_id}/bilingual.epub
    data/books/{book_id}/original.epub   (only if has_original=True)

SQLite table: ``books`` — provisioned by ``shared.state._init_tables``.
"""

from __future__ import annotations

import os
import sqlite3
from pathlib import Path
from typing import Literal

from shared.schemas.books import Book
from shared.state import _get_conn

_DEFAULT_BOOKS_DIR = "data/books"
_MAX_BOOK_ID_LEN = 200


class BookStorageError(ValueError):
    """Raised for invalid book_id or storage failures."""


# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------


def _check_book_id(book_id: str) -> None:
    if not book_id:
        raise BookStorageError("book_id must not be empty")
    if len(book_id) > _MAX_BOOK_ID_LEN:
        raise BookStorageError(f"book_id too long ({len(book_id)} chars)")
    if "\x00" in book_id:
        raise BookStorageError("book_id contains NUL byte")
    if "/" in book_id or "\\" in book_id:
        raise BookStorageError("book_id contains path separator")
    if book_id.startswith("."):
        raise BookStorageError("book_id starts with dot")
    if ".." in book_id:
        raise BookStorageError("book_id contains parent-traversal sequence")


def _books_root() -> Path:
    return Path(os.environ.get("NAKAMA_BOOKS_DIR", _DEFAULT_BOOKS_DIR))


# ---------------------------------------------------------------------------
# Filesystem API
# ---------------------------------------------------------------------------


def store_book_files(
    book_id: str,
    *,
    bilingual: bytes,
    original: bytes | None = None,
) -> None:
    """Write bilingual.epub (and optionally original.epub) under data/books/{book_id}/."""
    _check_book_id(book_id)
    book_dir = _books_root() / book_id
    book_dir.mkdir(parents=True, exist_ok=True)
    (book_dir / "bilingual.epub").write_bytes(bilingual)
    if original is not None:
        (book_dir / "original.epub").write_bytes(original)


def read_book_blob(book_id: str, *, lang: Literal["bilingual", "en"]) -> bytes:
    """Read and return bilingual.epub or original.epub bytes.

    Raises FileNotFoundError if the file does not exist.
    Raises BookStorageError for invalid book_id.
    """
    _check_book_id(book_id)
    filename = "bilingual.epub" if lang == "bilingual" else "original.epub"
    path = _books_root() / book_id / filename
    if not path.exists():
        raise FileNotFoundError(f"Book file not found: {path}")
    return path.read_bytes()


# ---------------------------------------------------------------------------
# SQLite API
# ---------------------------------------------------------------------------


def insert_book(book: Book) -> None:
    """Upsert a Book record into the books table (INSERT OR REPLACE)."""
    conn = _get_conn()
    conn.execute(
        """INSERT OR REPLACE INTO books
           (book_id, title, author, lang_pair, genre, isbn, published_year,
            has_original, book_version_hash, created_at)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (
            book.book_id,
            book.title,
            book.author,
            book.lang_pair,
            book.genre,
            book.isbn,
            book.published_year,
            1 if book.has_original else 0,
            book.book_version_hash,
            book.created_at,
        ),
    )
    conn.commit()


def get_book(book_id: str) -> Book | None:
    """Fetch one Book by book_id; returns None if not found."""
    conn = _get_conn()
    row = conn.execute("SELECT * FROM books WHERE book_id = ?", (book_id,)).fetchone()
    if row is None:
        return None
    return _row_to_book(row)


def list_books() -> list[Book]:
    """Return all books ordered by created_at DESC."""
    conn = _get_conn()
    rows = conn.execute("SELECT * FROM books ORDER BY created_at DESC").fetchall()
    return [_row_to_book(row) for row in rows]


def _row_to_book(row: sqlite3.Row) -> Book:
    return Book(
        book_id=row["book_id"],
        title=row["title"],
        author=row["author"],
        lang_pair=row["lang_pair"],
        genre=row["genre"],
        isbn=row["isbn"],
        published_year=row["published_year"],
        has_original=bool(row["has_original"]),
        book_version_hash=row["book_version_hash"],
        created_at=row["created_at"],
    )
