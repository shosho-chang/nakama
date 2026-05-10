"""Registry-backed ``ReadingSourceLister`` adapter (ADR-024 Slice 10 /
N518a).

Production implementation of the ``ReadingSourceLister`` Protocol declared
in ``shared.promotion_review_service`` (#516). Walks the ``books`` table
plus the vault's ``Inbox/kb/`` directory and resolves each candidate via
the injected ``ReadingSourceRegistry`` (#509). Inbox originals and their
``-bilingual`` siblings collapse to a single ``ReadingSource`` per the
``InboxKey`` invariants.

Hard invariants (per N518 brief §4):

- Every candidate goes through ``ReadingSourceRegistry.resolve(key)`` —
  the lister NEVER constructs a ``ReadingSource`` itself. This keeps
  evidence-track / variant policy centralized in #509.
- Unsafe paths (symlinks pointing outside the vault, ``..`` segments)
  are skipped + logged. No silent crash on malformed inbox files.
- Original + bilingual sibling pairs collapse via #509 ``_logical_original``
  semantics: both register under ``inbox:{logical_original_path}``, so
  walking the directory and resolving each unique logical original yields
  one entry per logical source.
- Bilingual-only sources still surface (``has_evidence_track=False``) —
  the registry already marks them with ``evidence_reason="bilingual_only_inbox"``;
  the lister surfaces them so the review UI can show a missing-evidence
  banner. Filtering by preflight action lives downstream in
  ``PromotionReviewService.list_pending``.

Boundary 5 (W6 / brief §6): NO ``os.getenv`` here. The constructor takes
already-resolved paths. The lister does NOT read frontmatter — that is
the registry's job during ``resolve()``.
"""

from __future__ import annotations

from pathlib import Path

from shared.log import get_logger
from shared.reading_source_registry import (
    BookKey,
    InboxKey,
    ReadingSourceRegistry,
)
from shared.schemas.reading_source import ReadingSource

_logger = get_logger("nakama.shared.reading_source_lister")

# Documented failure modes for cross-service / filesystem calls. Narrow
# tuple per #511 F5 lesson — programmer errors propagate.
_LISTER_FAILURES: tuple[type[BaseException], ...] = (OSError, ValueError)
"""Per-candidate enumeration may raise OSError (filesystem access during
``iterdir``, ``is_file``, ``is_symlink``) or ValueError (registry's
inbox path-traversal guard for malformed keys). Skipped + logged."""


# Mirror #509 ``_BILINGUAL_SUFFIX`` — kept here as a private constant so we
# don't reach into the registry's private namespace from this adapter. The
# value is a stable contract per the #509 schema.
_BILINGUAL_SUFFIX = "-bilingual.md"
_INBOX_SUBDIR = ("Inbox", "kb")


class RegistryReadingSourceLister:
    """Walks vault candidate sources and yields ``ReadingSource`` entries.

    Constructed with:

    - ``registry``: a ``ReadingSourceRegistry`` to resolve each candidate.
    - ``inbox_root``: absolute path to ``{vault}/Inbox/kb`` — the directory
      to walk for ``InboxKey`` candidates.
    - ``books_root``: absolute path to ``{vault}/data/books`` — the
      directory to walk for ``BookKey`` candidates. The lister enumerates
      child directories (one per ``book_id``) rather than touching the
      ``books`` SQLite table directly; that keeps the adapter usable in
      tests that don't initialize the DB. Each candidate is then resolved
      through the registry, which DOES consult the DB if available — book
      directories without a matching DB row will simply resolve to
      ``None`` and be skipped.
    """

    def __init__(
        self,
        *,
        registry: ReadingSourceRegistry,
        inbox_root: Path,
        books_root: Path,
    ) -> None:
        self._registry = registry
        self._inbox_root = Path(inbox_root)
        self._books_root = Path(books_root)

    # ── Public API ────────────────────────────────────────────────────────

    def list_sources(self) -> list[ReadingSource]:
        """Return all candidate Reading Sources for review listing.

        Order: books first (sorted by ``book_id``), then inbox documents
        (sorted by ``logical_original_path``). Within each kind the order
        is deterministic so list-view rendering is stable.
        """
        sources: list[ReadingSource] = []
        sources.extend(self._list_books())
        sources.extend(self._list_inbox())
        return sources

    # ── Books enumeration ─────────────────────────────────────────────────

    def _list_books(self) -> list[ReadingSource]:
        if not self._books_root.is_dir():
            return []
        out: list[ReadingSource] = []
        try:
            entries = sorted(self._books_root.iterdir(), key=lambda p: p.name)
        except _LISTER_FAILURES as exc:
            _logger.warning(
                "books root iterdir failed",
                extra={
                    "category": "reading_source_lister_books_iter_failed",
                    "books_root": str(self._books_root),
                    "error": str(exc),
                },
            )
            return []
        for entry in entries:
            if not _is_safe_dir(entry):
                _logger.warning(
                    "skipped unsafe book directory",
                    extra={
                        "category": "reading_source_lister_books_unsafe",
                        "path": str(entry),
                    },
                )
                continue
            book_id = entry.name
            rs = self._safe_resolve_book(book_id)
            if rs is not None:
                out.append(rs)
        return out

    def _safe_resolve_book(self, book_id: str) -> ReadingSource | None:
        try:
            return self._registry.resolve(BookKey(book_id=book_id))
        except _LISTER_FAILURES as exc:
            _logger.warning(
                "book resolve failed",
                extra={
                    "category": "reading_source_lister_book_resolve_failed",
                    "book_id": book_id,
                    "error": str(exc),
                },
            )
            return None

    # ── Inbox enumeration ─────────────────────────────────────────────────

    def _list_inbox(self) -> list[ReadingSource]:
        if not self._inbox_root.is_dir():
            return []
        try:
            entries = sorted(self._inbox_root.iterdir(), key=lambda p: p.name)
        except _LISTER_FAILURES as exc:
            _logger.warning(
                "inbox root iterdir failed",
                extra={
                    "category": "reading_source_lister_inbox_iter_failed",
                    "inbox_root": str(self._inbox_root),
                    "error": str(exc),
                },
            )
            return []

        # Collapse bilingual siblings: walk every ``.md`` file and project
        # to the logical original path (strip ``-bilingual.md`` if
        # present). De-duplicate so each logical source resolves once.
        seen_logical: set[str] = set()
        candidate_relpaths: list[str] = []
        for entry in entries:
            if not _is_safe_inbox_md(entry):
                _logger.warning(
                    "skipped unsafe inbox path",
                    extra={
                        "category": "reading_source_lister_inbox_unsafe",
                        "path": str(entry),
                    },
                )
                continue
            relative_path = _vault_relative_inbox_path(entry)
            logical = _logical_original_md(relative_path)
            if logical in seen_logical:
                continue
            seen_logical.add(logical)
            # Pass the actual on-disk path through to registry so it sees
            # the same key shape /books/foo.md or /books/foo-bilingual.md
            # the user-facing fixture exposes; #509 collapses internally.
            candidate_relpaths.append(relative_path)

        out: list[ReadingSource] = []
        for relative_path in candidate_relpaths:
            rs = self._safe_resolve_inbox(relative_path)
            if rs is not None:
                out.append(rs)

        # Sort by source_id so siblings collapse cleanly + order is stable.
        out.sort(key=lambda rs: rs.source_id)
        return out

    def _safe_resolve_inbox(self, relative_path: str) -> ReadingSource | None:
        try:
            return self._registry.resolve(InboxKey(relative_path=relative_path))
        except _LISTER_FAILURES as exc:
            _logger.warning(
                "inbox resolve failed",
                extra={
                    "category": "reading_source_lister_inbox_resolve_failed",
                    "relative_path": relative_path,
                    "error": str(exc),
                },
            )
            return None


# ── Helpers ──────────────────────────────────────────────────────────────


def _is_safe_dir(path: Path) -> bool:
    """Skip non-directory and symlink-pointing entries.

    Raw filesystem inspection can raise OSError (permissions / broken
    symlinks); we treat those as 'unsafe' and skip rather than crash.
    """
    try:
        if path.is_symlink():
            return False
        return path.is_dir()
    except OSError:
        return False


def _is_safe_inbox_md(path: Path) -> bool:
    """Skip non-files, non-``.md``, symlinks, and dotfiles.

    Inbox enumeration only cares about plain ``.md`` files; everything
    else (subdirectories, ``.tmp``, broken symlinks) is ignored.
    """
    try:
        if path.is_symlink():
            return False
        if not path.is_file():
            return False
        if path.suffix != ".md":
            return False
        if path.name.startswith("."):
            return False
        return True
    except OSError:
        return False


def _vault_relative_inbox_path(path: Path) -> str:
    """Compose the vault-relative POSIX path the registry expects.

    The registry's ``InboxKey.relative_path`` lives at ``Inbox/kb/{name}``
    — we re-assemble that prefix from constants here rather than computing
    a difference against the vault root, which keeps the adapter agnostic
    to the absolute vault path provided at construction.
    """
    return "/".join((*_INBOX_SUBDIR, path.name))


def _logical_original_md(relative_path: str) -> str:
    """Strip ``-bilingual.md`` to project to the logical original path.

    Mirrors ``shared.reading_source_registry._logical_original`` but kept
    private here so the adapter doesn't reach into the registry module's
    private namespace.
    """
    if relative_path.endswith(_BILINGUAL_SUFFIX):
        return relative_path[: -len(_BILINGUAL_SUFFIX)] + ".md"
    return relative_path
