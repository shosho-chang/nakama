"""Sandboxed file blob loader (ADR-024 Slice 10 / issue #540 — N518a).

Production implementation of the ``BlobLoader`` Protocol consumed by
``shared.promotion_preflight`` (#511) and ``shared.source_map_builder``
(#513). Both upstream services declare a ``BlobLoader = Callable[[str],
bytes]`` alias and read variant bytes through it; this module provides the
production class.

Hard invariants (per N518 brief §4 + W7/W8):

- The loader receives ``SourceVariant.path`` strings — vault-relative POSIX
  paths like ``"data/books/{id}/original.epub"`` or ``"Inbox/kb/foo.md"``.
  These are NOT ``source_id`` values; the caller (preflight/builder) has
  already resolved a ReadingSource and is passing one of its variant paths.
- The loader rejects path traversal (``..`` segments) and absolute paths
  with ``ValueError`` BEFORE touching the filesystem. Resolution against
  ``vault_root`` is a second-line check that confirms the resolved path
  stays inside the vault — caught by ``Path.relative_to`` raising
  ``ValueError``.
- Documented IO errors (missing file, permission) propagate as ``OSError``
  / ``FileNotFoundError`` per the upstream ``_LOADER_FAILURES`` tuple in
  #513 (``OSError, FileNotFoundError, KeyError, ValueError``). The loader
  never silently swallows.

The loader does NOT take ``os.getenv`` — env reads belong to the
``thousand_sunny.app`` startup helper, which constructs this loader with a
resolved ``vault_root: Path`` (per N518 brief boundary 5).
"""

from __future__ import annotations

from pathlib import Path, PurePosixPath


class VaultBlobLoader:
    """Sandboxed reader for ``vault_root``-relative file paths.

    Construct once with the absolute vault root; subsequent ``__call__`` /
    ``load`` invocations accept a vault-relative POSIX path (e.g.
    ``"data/books/abc/original.epub"``) and return the file bytes.

    Path safety:

    - rejects absolute paths (anything ``Path`` considers absolute).
    - rejects any path containing a ``..`` segment.
    - resolves the candidate against ``vault_root`` and re-checks the
      resolved location is inside the vault — defends against symlink
      escapes and OS-specific path tricks.

    Failure modes:

    - ``ValueError`` for unsafe paths (traversal / outside vault / absolute).
    - ``FileNotFoundError`` (subclass of ``OSError``) when the resolved file
      does not exist.
    - ``OSError`` for other IO failures (permission, disk).
    - ``IsADirectoryError`` (subclass of ``OSError``) when the path resolves
      to a directory rather than a file.
    """

    def __init__(self, vault_root: Path) -> None:
        # Resolve once at construction so symlinks / case-folding on the root
        # itself can't shift between invocations. Existence is NOT enforced
        # here — tests construct loaders against tmp_path that may not yet
        # contain the variant; ``load()`` surfaces missing files instead.
        self._vault_root = Path(vault_root).resolve()

    def __call__(self, path: str) -> bytes:
        """Convenience adapter so the class satisfies the upstream
        ``BlobLoader = Callable[[str], bytes]`` alias directly."""
        return self.load(path)

    def load(self, path: str) -> bytes:
        """Return the bytes of the vault-relative file at ``path``.

        Raises:
            ValueError: when ``path`` is unsafe (absolute, contains ``..``,
                or resolves outside ``vault_root``).
            FileNotFoundError: when no file exists at the resolved location.
            IsADirectoryError: when the resolved path is a directory.
            OSError: for other filesystem failures (permission, IO).
        """
        self._reject_unsafe(path)
        # ``Path(path)`` works for both POSIX-style separators and the
        # current OS; ``vault_root / candidate`` joins them. ``resolve()``
        # canonicalizes symlinks so the relative-to check below catches
        # symlink escapes (``Inbox/escape -> /etc/passwd``).
        target = (self._vault_root / path).resolve()
        try:
            target.relative_to(self._vault_root)
        except ValueError as exc:
            raise ValueError(f"path resolves outside vault_root: {path!r} → {target}") from exc
        # ``read_bytes`` raises FileNotFoundError / IsADirectoryError /
        # PermissionError naturally; per the upstream contract we want IO
        # failures to propagate as OSError (with FileNotFoundError as the
        # specific subclass for missing-file). NEVER catch with bare
        # ``except Exception``.
        return target.read_bytes()

    def _reject_unsafe(self, path: str) -> None:
        """Reject obviously unsafe inputs before touching the filesystem.

        Rejects:
        - empty / whitespace-only strings.
        - absolute paths (POSIX ``/foo`` or Windows ``C:\\foo``). On Windows
          ``Path("/foo").is_absolute()`` is False but ``PurePosixPath`` flags
          ``/foo`` as absolute — we use both checks so the loader behaves
          consistently regardless of host OS.
        - any path containing a ``..`` segment.
        """
        if not path or not path.strip():
            raise ValueError(f"empty path: {path!r}")
        if Path(path).is_absolute() or PurePosixPath(path).is_absolute():
            raise ValueError(f"absolute paths not permitted: {path!r}")
        # Use PurePosixPath part-segmentation so backslash-bearing inputs are
        # also caught; the alternative ``"../" in path`` is too narrow on
        # Windows where separators may be backslash. parts handles both.
        parts = Path(path).parts
        for part in parts:
            if part == "..":
                raise ValueError(f"path traversal not permitted: {path!r}")
        # Also check the POSIX form for foreign callers (the upstream paths
        # always use ``/`` per #509 N3 contract).
        for part in PurePosixPath(path).parts:
            if part == "..":
                raise ValueError(f"path traversal not permitted: {path!r}")
