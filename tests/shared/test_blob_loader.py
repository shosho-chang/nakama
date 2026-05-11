"""Tests for ``shared.blob_loader.VaultBlobLoader`` (ADR-024 Slice 10 / N518a).

Brief §5 AT1-AT3 + extra coverage:

- AT1 ``loader("data/x.txt")`` returns bytes.
- AT2 ``loader("../etc/passwd")`` raises ``ValueError``.
- AT3 absolute path outside vault → ``ValueError``.
- Extras: missing file → FileNotFoundError; empty path → ValueError;
  resolves through symlinks back inside vault.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

from shared.blob_loader import VaultBlobLoader


@pytest.fixture
def vault(tmp_path: Path) -> Path:
    """Tmp vault root with one file ready for happy-path reads."""
    (tmp_path / "data").mkdir()
    (tmp_path / "data" / "x.txt").write_bytes(b"hello vault")
    return tmp_path


# ── AT1 — happy path ─────────────────────────────────────────────────────────


def test_at1_vault_blob_loader_reads_within_vault(vault: Path):
    loader = VaultBlobLoader(vault_root=vault)
    assert loader("data/x.txt") == b"hello vault"


def test_loader_callable_alias_matches_load_method(vault: Path):
    """``__call__`` and ``load`` produce identical results — the class
    satisfies the ``BlobLoader = Callable[[str], bytes]`` alias directly."""
    loader = VaultBlobLoader(vault_root=vault)
    assert loader("data/x.txt") == loader.load("data/x.txt")


# ── AT2 — path traversal ─────────────────────────────────────────────────────


def test_at2_vault_blob_loader_rejects_traversal(vault: Path):
    loader = VaultBlobLoader(vault_root=vault)
    with pytest.raises(ValueError, match="path traversal"):
        loader("../etc/passwd")


def test_traversal_via_nested_segment_rejected(vault: Path):
    """``foo/../../etc`` still escapes — the ``..`` check is per-segment."""
    loader = VaultBlobLoader(vault_root=vault)
    with pytest.raises(ValueError, match="path traversal"):
        loader("foo/../../etc/passwd")


@pytest.mark.skipif(
    sys.platform != "win32",
    reason="Backslash is a regular filename character on POSIX, not a path "
    "separator. Backslash-based traversal is a Windows-specific attack vector; "
    "on POSIX ``..\\\\etc\\\\passwd`` is a single filename segment with no "
    "literal ``..`` part, and the impl correctly does not reject it.",
)
def test_traversal_via_backslash_rejected(vault: Path):
    """Windows-style backslash traversal is rejected on Windows hosts where
    ``Path.parts`` parses backslash as a separator and surfaces a literal
    ``..`` part token. POSIX is exempt — see the ``skipif`` reason."""
    loader = VaultBlobLoader(vault_root=vault)
    with pytest.raises(ValueError):
        loader("..\\etc\\passwd")


# ── AT3 — outside-vault paths ────────────────────────────────────────────────


def test_at3_vault_blob_loader_rejects_outside_vault_absolute(vault: Path, tmp_path: Path):
    """Absolute paths are rejected at the first guard, before resolution."""
    loader = VaultBlobLoader(vault_root=vault)
    outside = tmp_path / "outside.txt"
    outside.write_bytes(b"nope")
    with pytest.raises(ValueError, match="absolute paths not permitted"):
        loader(str(outside))


def test_loader_rejects_posix_absolute_on_any_host(vault: Path):
    """A POSIX-style absolute path is rejected even on Windows hosts where
    ``Path("/foo").is_absolute()`` is False — the secondary
    ``PurePosixPath`` check catches it."""
    loader = VaultBlobLoader(vault_root=vault)
    with pytest.raises(ValueError, match="absolute paths not permitted"):
        loader("/etc/passwd")


@pytest.mark.skipif(
    sys.platform == "win32",
    reason="Symlink creation requires admin on Windows; covered via `..` check.",
)
def test_loader_rejects_symlink_escaping_vault(
    vault: Path, tmp_path_factory: pytest.TempPathFactory
):
    """A symlink whose target lives outside the vault must be rejected.

    The first-line ``..`` guard doesn't catch this — the literal path
    ``data/escape.txt`` is clean. The second-line check (``relative_to``
    against the resolved vault root) catches it because ``resolve()``
    follows the symlink first.

    The target lives in a sibling tmp dir created via ``tmp_path_factory``
    so it is genuinely outside ``vault_root``. Using the per-test
    ``tmp_path`` would not work because the ``vault`` fixture returns
    ``tmp_path`` itself — any file under ``tmp_path`` would still be
    inside the vault and the resolved symlink would NOT escape.
    """
    outside_root = tmp_path_factory.mktemp("symlink_outside")
    target_outside = outside_root / "secret.txt"
    target_outside.write_bytes(b"top secret")
    link = vault / "data" / "escape.txt"
    os.symlink(target_outside, link)

    loader = VaultBlobLoader(vault_root=vault)
    with pytest.raises(ValueError, match="resolves outside vault_root"):
        loader("data/escape.txt")


# ── Empty / whitespace inputs ───────────────────────────────────────────────


def test_loader_rejects_empty_path(vault: Path):
    loader = VaultBlobLoader(vault_root=vault)
    with pytest.raises(ValueError, match="empty path"):
        loader("")


def test_loader_rejects_whitespace_path(vault: Path):
    loader = VaultBlobLoader(vault_root=vault)
    with pytest.raises(ValueError, match="empty path"):
        loader("   ")


# ── Missing file ─────────────────────────────────────────────────────────────


def test_loader_missing_file_raises_filenotfound(vault: Path):
    """Missing file under the vault is an IO error, not a path-safety error."""
    loader = VaultBlobLoader(vault_root=vault)
    with pytest.raises(FileNotFoundError):
        loader("data/does_not_exist.txt")


def test_loader_directory_target_raises_oserror(vault: Path):
    """Targeting a directory raises ``IsADirectoryError`` (OSError subclass)."""
    loader = VaultBlobLoader(vault_root=vault)
    with pytest.raises(OSError):
        loader("data")


# ── Vault root edge cases ───────────────────────────────────────────────────


def test_loader_construction_does_not_require_existing_vault(tmp_path: Path):
    """Construction resolves the vault root but does NOT enforce existence —
    tests + fresh checkouts construct loaders before populating the vault."""
    nonexistent = tmp_path / "future_vault"
    # Should not raise — construction is best-effort path resolution.
    loader = VaultBlobLoader(vault_root=nonexistent)
    assert loader is not None


# ── F06 — two-root dispatch (vault + books) ─────────────────────────────────


@pytest.fixture
def vault_and_books(tmp_path: Path) -> tuple[Path, Path]:
    """Vault + an out-of-vault books root, populated for two-root tests.

    Mirrors production layout where books live outside the Obsidian vault
    (``shared.book_storage.books_root()``) — vault has Inbox content,
    books root has ``{book_id}/original.epub``.
    """
    vault = tmp_path / "vault"
    (vault / "Inbox" / "kb").mkdir(parents=True)
    (vault / "Inbox" / "kb" / "doc.md").write_bytes(b"vault inbox doc")

    books = tmp_path / "books_outside_vault"
    (books / "abc").mkdir(parents=True)
    (books / "abc" / "original.epub").write_bytes(b"epub bytes")
    return vault, books


def test_books_root_dispatch_reads_outside_vault(vault_and_books: tuple[Path, Path]):
    """``data/books/{id}/file`` resolves to ``books_root``, not vault."""
    vault, books = vault_and_books
    loader = VaultBlobLoader(vault_root=vault, books_root=books)
    assert loader("data/books/abc/original.epub") == b"epub bytes"


def test_non_books_path_still_routes_to_vault(vault_and_books: tuple[Path, Path]):
    """Vault paths keep going to vault even when books_root is configured."""
    vault, books = vault_and_books
    loader = VaultBlobLoader(vault_root=vault, books_root=books)
    assert loader("Inbox/kb/doc.md") == b"vault inbox doc"


def test_books_root_strips_data_books_prefix(vault_and_books: tuple[Path, Path]):
    """The ``data/books/`` prefix is stripped before joining — without
    the strip we would resolve ``{books}/data/books/abc/original.epub``
    and miss the file. Regression guard for the F06 fix shape."""
    vault, books = vault_and_books
    # If the strip were missing, the file would only be found under
    # ``{books}/data/books/abc/...`` — set up that wrong layout to make
    # sure the loader does NOT pick it up.
    wrong_layout = books / "data" / "books" / "abc"
    wrong_layout.mkdir(parents=True)
    (wrong_layout / "original.epub").write_bytes(b"WRONG bytes")

    loader = VaultBlobLoader(vault_root=vault, books_root=books)
    # Should read the correctly-laid-out file at {books}/abc/original.epub,
    # NOT the bait file at {books}/data/books/abc/original.epub.
    assert loader("data/books/abc/original.epub") == b"epub bytes"


def test_books_root_rejects_traversal_inside_books_root(
    vault_and_books: tuple[Path, Path],
):
    """``..`` inside a books-prefixed path is rejected the same as vault paths."""
    vault, books = vault_and_books
    loader = VaultBlobLoader(vault_root=vault, books_root=books)
    with pytest.raises(ValueError, match="path traversal"):
        loader("data/books/../escape.txt")


def test_books_root_missing_file_raises_filenotfound(
    vault_and_books: tuple[Path, Path],
):
    """Missing book file under books_root raises FileNotFoundError, not vault error."""
    vault, books = vault_and_books
    loader = VaultBlobLoader(vault_root=vault, books_root=books)
    with pytest.raises(FileNotFoundError):
        loader("data/books/abc/missing.epub")


def test_books_root_error_message_names_books_root_not_vault(
    vault_and_books: tuple[Path, Path], tmp_path_factory: pytest.TempPathFactory
):
    """Out-of-books-root resolution error message names ``books_root``
    so operators can tell which root the path escaped — not generic
    "outside vault_root" which would be misleading."""
    if sys.platform == "win32":
        pytest.skip("Symlink creation requires admin on Windows")
    vault, books = vault_and_books
    outside = tmp_path_factory.mktemp("outside_books")
    target = outside / "secret.epub"
    target.write_bytes(b"sneaky")
    link = books / "abc" / "escape.epub"
    os.symlink(target, link)

    loader = VaultBlobLoader(vault_root=vault, books_root=books)
    with pytest.raises(ValueError, match="books_root"):
        loader("data/books/abc/escape.epub")


def test_data_prefix_alone_does_not_dispatch_to_books(tmp_path: Path):
    """A path starting with ``data/`` but NOT ``data/books/`` keeps going
    to the vault — guards against accidental over-matching of the prefix
    if any other ``data/`` subtree ever lands in the vault."""
    vault = tmp_path / "vault"
    (vault / "data").mkdir(parents=True)
    (vault / "data" / "x.txt").write_bytes(b"vault data x")
    books = tmp_path / "books"
    books.mkdir()

    loader = VaultBlobLoader(vault_root=vault, books_root=books)
    assert loader("data/x.txt") == b"vault data x"


def test_no_books_root_falls_back_to_vault(tmp_path: Path):
    """``books_root=None`` (legacy single-root construction) keeps all
    paths resolving against the vault — preserves pre-F06 behavior so
    existing tests / call-sites that don't exercise books are unaffected."""
    vault = tmp_path / "vault"
    (vault / "data" / "books" / "abc").mkdir(parents=True)
    (vault / "data" / "books" / "abc" / "original.epub").write_bytes(b"under vault")

    loader = VaultBlobLoader(vault_root=vault)  # no books_root
    assert loader("data/books/abc/original.epub") == b"under vault"
