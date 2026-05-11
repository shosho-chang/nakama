"""Tests for ``shared.reading_source_lister.RegistryReadingSourceLister``
(ADR-024 Slice 10 / N518a).

Brief §5 AT8-AT12:

- AT8  Books candidate (``data/books/foo/{original.epub,bilingual.epub}``).
- AT9  Inbox original-only.
- AT10 Inbox original + bilingual sibling collapsed to one entry.
- AT11 Inbox bilingual-only marked missing-evidence.
- AT12 Unsafe paths (symlinks pointing outside / dotfiles / non-md) skipped.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

from shared.reading_source_lister import RegistryReadingSourceLister
from shared.reading_source_registry import ReadingSourceRegistry
from shared.schemas.books import Book

# ── Fixtures ─────────────────────────────────────────────────────────────────


@pytest.fixture
def vault(tmp_path: Path) -> Path:
    inbox = tmp_path / "Inbox" / "kb"
    inbox.mkdir(parents=True)
    books = tmp_path / "data" / "books"
    books.mkdir(parents=True)
    return tmp_path


@pytest.fixture
def books_dir(vault: Path, monkeypatch) -> Path:
    """Reroute ``data/books/`` (used by ``shared.book_storage``) to the
    same dir the lister will enumerate."""
    target = vault / "data" / "books"
    monkeypatch.setenv("NAKAMA_BOOKS_DIR", str(target))
    return target


@pytest.fixture
def registry(vault: Path, books_dir: Path) -> ReadingSourceRegistry:
    return ReadingSourceRegistry(vault_root=vault)


@pytest.fixture
def lister(registry: ReadingSourceRegistry, vault: Path) -> RegistryReadingSourceLister:
    return RegistryReadingSourceLister(
        registry=registry,
        inbox_root=vault / "Inbox" / "kb",
        books_root=vault / "data" / "books",
    )


# ── Helpers ──────────────────────────────────────────────────────────────────


def _make_book(book_id: str, has_original: bool = True) -> Book:
    return Book(
        book_id=book_id,
        title="Sample",
        author="Anon",
        lang_pair="en-zh",
        genre=None,
        isbn="9780000000001",
        published_year=2024,
        has_original=has_original,
        book_version_hash="a" * 64,
        created_at="2026-05-05T00:00:00+00:00",
    )


def _store_book(book_id: str, *, has_original: bool = True, language: str = "en") -> Book:
    from shared.book_storage import insert_book, store_book_files
    from tests.shared._epub_fixtures import EPUBSpec, make_epub_blob

    bilingual_blob = make_epub_blob(EPUBSpec(language=language))
    original_blob = make_epub_blob(EPUBSpec(language=language)) if has_original else None
    store_book_files(book_id, bilingual=bilingual_blob, original=original_blob)
    book = _make_book(book_id=book_id, has_original=has_original)
    insert_book(book)
    return book


def _write_inbox(vault: Path, name: str, *, body: str = "body", lang: str = "en") -> Path:
    """Write a minimal inbox markdown file with frontmatter + body."""
    fm = f"---\ntitle: {name}\nlang: {lang}\n---\n{body}\n"
    path = vault / "Inbox" / "kb" / name
    path.write_text(fm, encoding="utf-8")
    return path


# ── AT8 — books candidate ────────────────────────────────────────────────────


def test_at8_lister_books_candidate(
    lister: RegistryReadingSourceLister,
    vault: Path,
    books_dir: Path,
):
    """A book with both original + bilingual blob present yields one
    ``ReadingSource`` (registry decides variant shape)."""
    _store_book("alpha-book", has_original=True, language="en")

    sources = lister.list_sources()
    book_sources = [rs for rs in sources if rs.kind == "ebook"]

    assert len(book_sources) == 1
    rs = book_sources[0]
    assert rs.source_id == "ebook:alpha-book"
    assert rs.has_evidence_track is True


# ── AT9 — inbox original only ───────────────────────────────────────────────


def test_at9_lister_inbox_original_only(lister: RegistryReadingSourceLister, vault: Path):
    _write_inbox(vault, "foo.md", lang="en")

    sources = lister.list_sources()
    inbox_sources = [rs for rs in sources if rs.kind == "inbox_document"]

    assert len(inbox_sources) == 1
    rs = inbox_sources[0]
    assert rs.source_id == "inbox:Inbox/kb/foo.md"
    assert rs.has_evidence_track is True
    assert rs.evidence_reason is None


# ── AT10 — inbox original + bilingual sibling collapsed ─────────────────────


def test_at10_lister_inbox_original_plus_bilingual_collapsed(
    lister: RegistryReadingSourceLister, vault: Path
):
    _write_inbox(vault, "foo.md", lang="en")
    # Bilingual sibling: same ``-bilingual.md`` suffix the registry expects.
    _write_inbox(vault, "foo-bilingual.md", lang="en")

    sources = lister.list_sources()
    inbox_sources = [rs for rs in sources if rs.kind == "inbox_document"]

    # Both files collapse into one logical source per #509 ``InboxKey``
    # invariants — the registry's ``_logical_original`` strips the
    # ``-bilingual.md`` suffix, so both file walks resolve to the same id.
    assert len(inbox_sources) == 1
    rs = inbox_sources[0]
    assert rs.source_id == "inbox:Inbox/kb/foo.md"
    assert rs.has_evidence_track is True
    # Two variants: the original (markdown) + the bilingual display sibling.
    roles = {v.role for v in rs.variants}
    assert roles == {"original", "display"}


# ── AT11 — inbox bilingual-only marked missing-evidence ─────────────────────


def test_at11_lister_inbox_bilingual_only_missing_evidence(
    lister: RegistryReadingSourceLister, vault: Path
):
    """Only ``foo-bilingual.md`` exists — no original sibling. The
    registry marks ``has_evidence_track=False`` with
    ``evidence_reason="bilingual_only_inbox"``."""
    _write_inbox(vault, "foo-bilingual.md", lang="en")

    sources = lister.list_sources()
    inbox_sources = [rs for rs in sources if rs.kind == "inbox_document"]

    assert len(inbox_sources) == 1
    rs = inbox_sources[0]
    # Source id projects to the logical original even though only the
    # bilingual exists on disk (#509 NB3 — source_id is logical identity,
    # not a filesystem lookup key).
    assert rs.source_id == "inbox:Inbox/kb/foo.md"
    assert rs.has_evidence_track is False
    assert rs.evidence_reason == "bilingual_only_inbox"


# ── AT12 — unsafe paths skipped ─────────────────────────────────────────────


def test_at12_lister_skips_unsafe_paths_dotfiles_and_non_md(
    lister: RegistryReadingSourceLister, vault: Path
):
    """Dotfiles and non-``.md`` files are silently ignored."""
    # Dotfile (``.DS_Store``-style) should be skipped.
    (vault / "Inbox" / "kb" / ".hidden.md").write_text("---\n---\n", encoding="utf-8")
    # Non-md extension.
    (vault / "Inbox" / "kb" / "notes.txt").write_text("plain text", encoding="utf-8")
    # Subdirectory shouldn't surface as a candidate either.
    (vault / "Inbox" / "kb" / "subdir").mkdir()
    # One real file so we can assert the lister still works.
    _write_inbox(vault, "real.md", lang="en")

    sources = lister.list_sources()
    inbox_sources = [rs for rs in sources if rs.kind == "inbox_document"]
    assert len(inbox_sources) == 1
    assert inbox_sources[0].source_id == "inbox:Inbox/kb/real.md"


@pytest.mark.skipif(
    sys.platform == "win32",
    reason="Symlink creation requires admin on Windows.",
)
def test_lister_skips_symlinks(lister: RegistryReadingSourceLister, vault: Path, tmp_path: Path):
    """A symlink in the inbox directory is skipped (per the brief — unsafe)."""
    # Real file outside the vault.
    target = tmp_path / "outside.md"
    target.write_text("---\n---\nbody", encoding="utf-8")
    # Symlink in the inbox.
    link = vault / "Inbox" / "kb" / "linked.md"
    os.symlink(target, link)
    # Plus one real file.
    _write_inbox(vault, "real.md", lang="en")

    sources = lister.list_sources()
    inbox_sources = [rs for rs in sources if rs.kind == "inbox_document"]
    assert len(inbox_sources) == 1
    assert inbox_sources[0].source_id == "inbox:Inbox/kb/real.md"


# ── Empty roots ─────────────────────────────────────────────────────────────


def test_lister_returns_empty_when_both_roots_missing(tmp_path: Path):
    """Construction does not require existing roots; empty list is fine."""
    registry = ReadingSourceRegistry(vault_root=tmp_path)
    lister = RegistryReadingSourceLister(
        registry=registry,
        inbox_root=tmp_path / "missing_inbox",
        books_root=tmp_path / "missing_books",
    )
    assert lister.list_sources() == []


def test_lister_does_not_import_fastapi():
    import shared.reading_source_lister as mod

    forbidden = {"fastapi", "anthropic"}
    for name in vars(mod):
        assert name not in forbidden, f"forbidden import surfaced: {name}"
