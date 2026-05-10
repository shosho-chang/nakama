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
