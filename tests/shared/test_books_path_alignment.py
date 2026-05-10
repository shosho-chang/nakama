"""Integration test for the F06 fix — lister + loader + book_storage all
agree on the books root, regardless of where it lives relative to the
vault (2026-05-10).

Background: pre-fix wiring computed ``books_root = vault_root /
"data/books"`` while ``book_storage`` already used ``cwd`` (or
``NAKAMA_BOOKS_DIR``) — so the lister enumerated one directory and
``store_book_files`` wrote to another, making books invisible in the
promotion-review list view. F06 in ``qa/findings.md`` (commit
``bc1e27d``) describes the failure mode in detail.

This test pins the contract by exercising the full surface in one
process:

1. Books live **outside the vault** (``books_outside_vault``).
2. ``book_storage.store_book_files`` writes there (because
   ``NAKAMA_BOOKS_DIR`` points at it).
3. ``book_storage.books_root()`` returns the same path.
4. ``RegistryReadingSourceLister`` constructed with that root surfaces
   the book in ``list_sources()``.
5. ``VaultBlobLoader`` constructed with that ``books_root`` reads the
   stored EPUB bytes via the ``data/books/{id}/...`` path string the
   registry emits — without ever touching the vault tree.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from shared import book_storage
from shared.blob_loader import VaultBlobLoader
from shared.reading_source_lister import RegistryReadingSourceLister
from shared.reading_source_registry import ReadingSourceRegistry
from shared.schemas.books import Book
from tests.shared._epub_fixtures import epub_clean


@pytest.fixture
def two_root_layout(tmp_path: Path, monkeypatch) -> tuple[Path, Path, str]:
    """Set up a vault root + a separate books root, with one book stored.

    Returns ``(vault_root, books_root, book_id)``. The ``books`` SQLite
    table is provisioned via ``conftest.isolated_db`` so the registry's
    ``get_book`` lookup works without a real app boot.
    """
    vault = tmp_path / "Shosho LifeOS"
    (vault / "Inbox" / "kb").mkdir(parents=True)
    (vault / "KB" / "Wiki" / "Concepts").mkdir(parents=True)

    # Books deliberately live OUTSIDE the vault — exactly the
    # production layout that pre-fix wiring missed.
    books = tmp_path / "books_outside_vault"
    books.mkdir()
    monkeypatch.setenv("NAKAMA_BOOKS_DIR", str(books))

    book_id = "alignment-test-book"
    book_storage.store_book_files(
        book_id,
        bilingual=epub_clean(),
    )
    book_storage.insert_book(
        Book(
            book_id=book_id,
            title="Alignment Test",
            author="QA",
            lang_pair="en→zh-Hant",
            genre=None,
            isbn=None,
            published_year=None,
            has_original=False,
            book_version_hash="x" * 64,
            created_at="2026-05-10T12:00:00+08:00",
        )
    )

    return vault, books, book_id


def test_book_storage_root_lives_outside_vault(
    two_root_layout: tuple[Path, Path, str],
):
    """Sanity: with ``NAKAMA_BOOKS_DIR`` pointing outside the vault,
    ``books_root()`` reflects that — books are NOT under the vault tree.
    Pinning this so a future "default to vault-relative" regression
    fails loudly here rather than silently in promotion review."""
    vault, books, book_id = two_root_layout
    assert book_storage.books_root().resolve() == books.resolve()
    # And the actual EPUB was written to the books root, not the vault.
    expected_blob = books / book_id / "bilingual.epub"
    assert expected_blob.exists()
    assert not (vault / "data" / "books" / book_id / "bilingual.epub").exists()


def test_lister_surfaces_book_when_books_root_is_outside_vault(
    two_root_layout: tuple[Path, Path, str],
):
    """The lister must see the book even though books live outside the
    vault — the F06 failure was that the lister walked
    ``vault/data/books`` (which doesn't exist) instead of the actual
    books root, so list view was always empty for ebooks."""
    vault, books, book_id = two_root_layout

    registry = ReadingSourceRegistry(vault_root=vault)
    lister = RegistryReadingSourceLister(
        registry=registry,
        inbox_root=vault / "Inbox" / "kb",
        books_root=book_storage.books_root(),  # ← THE F06 fix
    )

    sources = lister.list_sources()
    surfaced_ids = [rs.source_id for rs in sources]
    assert f"ebook:{book_id}" in surfaced_ids


def test_blob_loader_reads_books_via_data_books_path_string(
    two_root_layout: tuple[Path, Path, str],
):
    """The registry emits ``data/books/{id}/bilingual.epub`` as the
    ``SourceVariant.path`` string. The blob loader must resolve that
    against ``books_root`` (NOT vault_root) and return real bytes —
    this is the second half of the F06 fix that the lister test
    above doesn't cover."""
    vault, books, book_id = two_root_layout

    loader = VaultBlobLoader(
        vault_root=vault,
        books_root=book_storage.books_root(),
    )

    # Match the path string ReadingSourceRegistry constructs (see
    # shared/reading_source_registry.py:189 / 198 — has_original=False
    # produces a single ``bilingual.epub`` variant).
    blob_bytes = loader(f"data/books/{book_id}/bilingual.epub")
    assert blob_bytes, "loader returned empty bytes — books_root dispatch missed"

    # And the bytes match what book_storage actually stored.
    on_disk = (books / book_id / "bilingual.epub").read_bytes()
    assert blob_bytes == on_disk


def test_registry_resolves_ebook_when_books_live_outside_vault(
    two_root_layout: tuple[Path, Path, str],
):
    """The registry uses ``book_storage.read_book_blob`` internally to
    extract metadata — that path was already cwd-relative pre-fix, so
    this should keep passing post-fix. Pinned here so future "make
    the registry vault-aware for books" refactors trip this guard."""
    from shared.reading_source_registry import BookKey

    vault, books, book_id = two_root_layout
    registry = ReadingSourceRegistry(vault_root=vault)
    rs = registry.resolve(BookKey(book_id=book_id))
    assert rs is not None
    assert rs.source_id == f"ebook:{book_id}"
    # Variant path is the logical ``data/books/...`` string — the loader
    # is responsible for routing it to the right root.
    assert any(v.path == f"data/books/{book_id}/bilingual.epub" for v in rs.variants)
