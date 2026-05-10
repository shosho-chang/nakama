"""Sandboxed file blob loader (ADR-024 Slice 10 / issue #540 — N518a).

Production implementation of the ``BlobLoader`` Protocol consumed by
``shared.promotion_preflight`` (#511) and ``shared.source_map_builder``
(#513). Both upstream services declare a ``BlobLoader = Callable[[str],
bytes]`` alias and read variant bytes through it; this module provides the
production class.

Hard invariants (per N518 brief §4 + W7/W8):

- The loader receives ``SourceVariant.path`` strings — POSIX paths like
  ``"data/books/{id}/original.epub"`` or ``"Inbox/kb/foo.md"``. These are
  NOT ``source_id`` values; the caller (preflight/builder) has already
  resolved a ReadingSource and is passing one of its variant paths.
- Two roots, two destinations (F06 fix, 2026-05-10):
    - ``data/books/...`` paths resolve against ``books_root``
      (``shared.book_storage.books_root()`` — cwd-relative or
      ``NAKAMA_BOOKS_DIR``). EPUB binaries live outside the vault so
      they don't participate in Obsidian sync.
    - All other paths (``Inbox/kb/...``, ``KB/Wiki/...``, etc.) resolve
      against ``vault_root``.
  ``books_root`` is optional — if omitted, ``data/books/...`` falls back
  to ``vault_root`` (preserves pre-F06 behavior for tests that don't
  exercise the books surface).
- The loader rejects path traversal (``..`` segments) and absolute paths
  with ``ValueError`` BEFORE touching the filesystem. Resolution against
  the appropriate root is a second-line check that confirms the resolved
  path stays inside it — caught by ``Path.relative_to`` raising
  ``ValueError``.
- Documented IO errors (missing file, permission) propagate as ``OSError``
  / ``FileNotFoundError`` per the upstream ``_LOADER_FAILURES`` tuple in
  #513 (``OSError, FileNotFoundError, KeyError, ValueError``). The loader
  never silently swallows.

The loader does NOT take ``os.getenv`` — env reads belong to the
``thousand_sunny.app`` startup helper, which constructs this loader with
resolved ``vault_root`` and ``books_root`` paths (per N518 brief
boundary 5).
"""

from __future__ import annotations

from pathlib import Path, PurePosixPath

# Logical prefix that signals "this path lives in the books root, not the
# vault root". Kept in sync with the prefix that
# ``shared.reading_source_registry`` uses when constructing
# ``SourceVariant.path`` strings for ebooks.
_BOOKS_PREFIX_PARTS: tuple[str, ...] = ("data", "books")


class VaultBlobLoader:
    """Sandboxed reader for vault-relative + books-relative file paths.

    Construct once with the resolved vault root and (optionally) the
    resolved books root; subsequent ``__call__`` / ``load`` invocations
    accept a logical POSIX path (e.g. ``"Inbox/kb/foo.md"`` for the vault
    or ``"data/books/abc/original.epub"`` for the books root) and return
    the file bytes.

    Path safety:

    - rejects absolute paths (anything ``Path`` considers absolute).
    - rejects any path containing a ``..`` segment.
    - resolves the candidate against the appropriate root (books_root for
      ``data/books/...``, vault_root otherwise) and re-checks the
      resolved location is inside that root — defends against symlink
      escapes and OS-specific path tricks.

    Failure modes:

    - ``ValueError`` for unsafe paths (traversal / outside the resolved
      root / absolute).
    - ``FileNotFoundError`` (subclass of ``OSError``) when the resolved file
      does not exist.
    - ``OSError`` for other IO failures (permission, disk).
    - ``IsADirectoryError`` (subclass of ``OSError``) when the path resolves
      to a directory rather than a file.
    """

    def __init__(self, vault_root: Path, books_root: Path | None = None) -> None:
        # Resolve once at construction so symlinks / case-folding on the
        # root itself can't shift between invocations. Existence is NOT
        # enforced here — tests construct loaders against tmp_path that
        # may not yet contain the variant; ``load()`` surfaces missing
        # files instead.
        self._vault_root = Path(vault_root).resolve()
        # Optional second root for ``data/books/...`` paths. None preserves
        # pre-F06 behavior of resolving everything against the vault.
        self._books_root: Path | None = (
            Path(books_root).resolve() if books_root is not None else None
        )

    def __call__(self, path: str) -> bytes:
        """Convenience adapter so the class satisfies the upstream
        ``BlobLoader = Callable[[str], bytes]`` alias directly."""
        return self.load(path)

    def load(self, path: str) -> bytes:
        """Return the bytes of the logical file at ``path``.

        Dispatches by path prefix: ``data/books/...`` goes to
        ``books_root`` (when configured), everything else to
        ``vault_root``.

        Raises:
            ValueError: when ``path`` is unsafe (absolute, contains ``..``,
                or resolves outside the chosen root).
            FileNotFoundError: when no file exists at the resolved location.
            IsADirectoryError: when the resolved path is a directory.
            OSError: for other filesystem failures (permission, IO).
        """
        self._reject_unsafe(path)
        root, sub, label = self._dispatch_root(path)
        # ``Path(sub)`` works for both POSIX-style separators and the
        # current OS; ``root / sub`` joins them. ``resolve()``
        # canonicalizes symlinks so the relative-to check below catches
        # symlink escapes (``Inbox/escape -> /etc/passwd``).
        target = (root / sub).resolve()
        try:
            target.relative_to(root)
        except ValueError as exc:
            raise ValueError(f"path resolves outside {label}: {path!r} → {target}") from exc
        # ``read_bytes`` raises FileNotFoundError / IsADirectoryError /
        # PermissionError naturally; per the upstream contract we want IO
        # failures to propagate as OSError (with FileNotFoundError as the
        # specific subclass for missing-file). NEVER catch with bare
        # ``except Exception``.
        return target.read_bytes()

    def _dispatch_root(self, path: str) -> tuple[Path, str, str]:
        """Pick the root + the path-relative-to-root + label for errors.

        ``data/books/...`` paths route to ``books_root`` when configured;
        the ``data/books/`` prefix is **stripped** before joining because
        ``books_root`` *is* the directory that directly contains
        ``{book_id}/...`` (matching ``shared.book_storage.books_root()``,
        whose default is itself ``data/books``). Without the strip we
        would resolve ``{books_root}/data/books/{book_id}/...`` and miss.

        When ``books_root`` is ``None`` (legacy single-root construction)
        all paths fall back to ``vault_root`` un-stripped so the loader
        retains its pre-F06 behavior for tests / call-sites that don't
        exercise the books surface.

        Returns ``(root, path_relative_to_root, label)``.
        """
        if self._books_root is None:
            return self._vault_root, path, "vault_root"
        # Use POSIX parts because the upstream contract (#509 N3) is
        # POSIX paths. ``PurePosixPath`` parses with ``/`` and never
        # treats ``\`` as a separator, which is what we want — a literal
        # backslash in a POSIX-shaped contract should fall through to
        # the unsafe-input guard or the vault branch, not silently
        # match the books prefix.
        posix_parts = PurePosixPath(path).parts
        if posix_parts[: len(_BOOKS_PREFIX_PARTS)] == _BOOKS_PREFIX_PARTS:
            sub = "/".join(posix_parts[len(_BOOKS_PREFIX_PARTS) :])
            return self._books_root, sub, "books_root"
        return self._vault_root, path, "vault_root"

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
