"""Filesystem-backed ``KBConceptIndex`` adapter (ADR-024 Slice 10 / N518a-b).

Production implementation of the ``KBConceptIndex`` Protocol declared in
``shared.concept_promotion_engine`` (#514). Scans ``KB/Wiki/Concepts/``
markdown pages, parses each page's frontmatter (``name`` /
``aliases`` / ``languages``), and serves the protocol's two read methods
(``lookup(alias)`` and ``aliases_starting_with(prefix)``) against the
parsed entries.

Hard invariants (per N518 brief §4):

- Missing or non-directory ``concepts_root`` → empty list, no exception.
  This is the bootstrap case for a fresh vault (no concepts yet).
- Malformed frontmatter is logged + skipped — the index returns the
  partial list rather than failing the whole scan. Mirrors the
  registry's NB1 unified failure policy.
- Bare ``except Exception`` is forbidden (#511 F5 lesson) — narrow the
  catch to documented ``yaml.YAMLError`` / ``OSError`` / ``ValueError``.

Cache invalidation strategy (N518b carry-over C1):

The index lazy-scans on first call. Subsequent calls re-stat the
``concepts_root`` directory's mtime and rescan only when it changes.
Trade-off: picks up new / deleted concept files at the cost of one
``stat()`` per query. The overhead is negligible compared to the alias
matching loop, and avoiding stale state in long-running uvicorn processes
is more important than shaving a microsecond. Modifying an existing
concept file's content (without changing the parent directory mtime)
will NOT invalidate the cache — that's an accepted limitation for the
shape of N518's promotion workflow (concepts grow append-only during a
review session). Callers needing strong freshness can construct a new
index instance.
"""

from __future__ import annotations

from pathlib import Path

import yaml

from shared.log import get_logger
from shared.schemas.concept_promotion import KBConceptEntry

_logger = get_logger("nakama.shared.kb_concept_index_default")


# Documented failure modes for filesystem + yaml parse calls. Narrow tuple
# per #511 F5 lesson — programmer errors propagate.
_PARSE_FAILURES: tuple[type[BaseException], ...] = (
    OSError,
    yaml.YAMLError,
    ValueError,
    UnicodeDecodeError,
)


class VaultKBConceptIndex:
    """Read-only index of ``KB/Wiki/Concepts/*.md`` pages.

    Protocol contract (#514 ``KBConceptIndex``):

    - ``lookup(alias)`` returns the entry whose ``aliases`` list contains
      ``alias`` (case-insensitive). The page's canonical_label / name is
      also matched. Returns ``None`` when no entry matches.
    - ``aliases_starting_with(prefix)`` returns a flat list of every
      indexed alias that starts with ``prefix`` (case-insensitive).
      Order is stable but unspecified by the Protocol.

    Non-Protocol convenience method:

    - ``list_entries()`` returns the full list of parsed entries — useful
      for diagnostics and admin tooling. Not consumed by the engine.

    Construction takes ``concepts_root: Path`` — typically
    ``{vault}/KB/Wiki/Concepts``. The index is constructed once per
    application startup and shared across requests.
    """

    def __init__(self, concepts_root: Path) -> None:
        self._concepts_root = Path(concepts_root)
        self._entries_cache: list[KBConceptEntry] | None = None
        self._alias_lookup_cache: dict[str, KBConceptEntry] | None = None
        # Last observed mtime of ``concepts_root``. The cache is invalidated
        # when this changes (file added / removed in the directory). See
        # N518b C1 — keeps long-running uvicorn processes from serving
        # stale concept lists after a manual KB edit.
        self._cached_mtime_ns: int | None = None

    # ── Protocol API ──────────────────────────────────────────────────────

    def lookup(self, alias: str) -> KBConceptEntry | None:
        """Return the KB concept entry whose canonical_label or aliases
        contains ``alias`` (case-insensitive).

        Returns ``None`` when no entry matches. Empty / whitespace-only
        ``alias`` also returns ``None`` rather than raising — the matcher
        Protocol may legitimately probe with derived labels that turn out
        empty after normalization.
        """
        if not alias or not alias.strip():
            return None
        norm = alias.strip().casefold()
        return self._alias_lookup().get(norm)

    def aliases_starting_with(self, prefix: str) -> list[str]:
        """Return all indexed aliases starting with ``prefix``
        (case-insensitive). Empty prefix returns all aliases.
        """
        norm_prefix = prefix.strip().casefold() if prefix else ""
        out: list[str] = []
        for entry in self._entries():
            for alias in (entry.canonical_label, *entry.aliases):
                if not alias:
                    continue
                if alias.casefold().startswith(norm_prefix):
                    out.append(alias)
        # Stable sort for deterministic output.
        out.sort()
        return out

    # ── Convenience ───────────────────────────────────────────────────────

    def list_entries(self) -> list[KBConceptEntry]:
        """Return all parsed concept entries. Diagnostic helper; not on
        the Protocol surface."""
        return list(self._entries())

    # ── Internal scan + cache ─────────────────────────────────────────────

    def _entries(self) -> list[KBConceptEntry]:
        # mtime-based invalidation (N518b C1). Callers in long-running
        # processes (uvicorn) get fresh state when the directory changes;
        # short-lived per-test instances pay one stat() and stay cached.
        current_mtime = self._current_mtime_ns()
        if self._entries_cache is None or current_mtime != self._cached_mtime_ns:
            self._entries_cache = self._scan()
            self._alias_lookup_cache = None  # force rebuild on next lookup
            self._cached_mtime_ns = current_mtime
        return self._entries_cache

    def _alias_lookup(self) -> dict[str, KBConceptEntry]:
        # Touch _entries() first so mtime invalidation runs before we read
        # the alias cache.
        entries = self._entries()
        if self._alias_lookup_cache is None:
            mapping: dict[str, KBConceptEntry] = {}
            for entry in entries:
                # Index canonical_label too — engines commonly search by
                # the page's primary name in addition to declared aliases.
                for alias in (entry.canonical_label, *entry.aliases):
                    if not alias:
                        continue
                    norm = alias.strip().casefold()
                    if not norm:
                        continue
                    # First-write wins so ordering is deterministic
                    # against scan order (which is already sorted).
                    mapping.setdefault(norm, entry)
            self._alias_lookup_cache = mapping
        return self._alias_lookup_cache

    def _current_mtime_ns(self) -> int | None:
        """Read ``concepts_root.stat().st_mtime_ns`` defensively.

        Returns ``None`` when the directory is missing or stat() raises
        ``OSError`` — that's the same signal as "no entries", and we
        treat ``None != None`` as False so a missing-then-still-missing
        directory doesn't trigger a redundant rescan."""
        try:
            return self._concepts_root.stat().st_mtime_ns
        except OSError:
            # Missing dir / permission denied / broken symlink. The scan
            # itself handles these gracefully (returns []); we just want
            # to remember "we tried and there was nothing" without
            # forcing endless rescans.
            return None

    def _scan(self) -> list[KBConceptEntry]:
        if not self._concepts_root.is_dir():
            # Empty / missing root is the bootstrap case for a fresh
            # vault. Return [] cleanly — no warning, this is normal.
            return []

        try:
            entries = sorted(
                self._concepts_root.iterdir(),
                key=lambda p: p.name.casefold(),
            )
        except OSError as exc:
            _logger.warning(
                "concepts root iterdir failed",
                extra={
                    "category": "kb_concept_index_iter_failed",
                    "concepts_root": str(self._concepts_root),
                    "error": str(exc),
                },
            )
            return []

        out: list[KBConceptEntry] = []
        for entry in entries:
            if not _is_concept_md(entry):
                continue
            parsed = self._parse_entry(entry)
            if parsed is not None:
                out.append(parsed)
        return out

    def _parse_entry(self, path: Path) -> KBConceptEntry | None:
        try:
            content = path.read_text(encoding="utf-8")
        except _PARSE_FAILURES as exc:
            _logger.warning(
                "concept page read failed",
                extra={
                    "category": "kb_concept_index_read_failed",
                    "path": str(path),
                    "error": str(exc),
                },
            )
            return None

        frontmatter = _extract_strict_frontmatter(content)
        if frontmatter is None:
            _logger.warning(
                "concept page frontmatter parse failed; skipped",
                extra={
                    "category": "kb_concept_index_frontmatter_parse_failed",
                    "path": str(path),
                },
            )
            return None

        name = frontmatter.get("name")
        if not isinstance(name, str) or not name.strip():
            # No canonical name → fall back to file stem so the entry is
            # still queryable; warn so the page can be fixed.
            _logger.warning(
                "concept page missing 'name' frontmatter; using filename stem",
                extra={
                    "category": "kb_concept_index_missing_name",
                    "path": str(path),
                },
            )
            name = path.stem

        aliases_raw = frontmatter.get("aliases", [])
        aliases = _normalize_string_list(aliases_raw)
        languages_raw = frontmatter.get("languages", [])
        languages = _normalize_string_list(languages_raw)

        # ``concept_path`` is a vault-relative POSIX path; we don't have
        # the vault root here so we expose the absolute path. Downstream
        # consumers (commit service / matcher) treat it as opaque per the
        # KBConceptEntry schema. The frontmatter parse layer is best-effort.
        try:
            return KBConceptEntry(
                concept_path=path.as_posix(),
                canonical_label=name.strip(),
                aliases=aliases,
                languages=languages,
            )
        except ValueError as exc:
            # Pydantic validation failure (e.g. extra=forbid violation).
            _logger.warning(
                "concept page schema validation failed; skipped",
                extra={
                    "category": "kb_concept_index_schema_failed",
                    "path": str(path),
                    "error": str(exc),
                },
            )
            return None


# ── Helpers ──────────────────────────────────────────────────────────────


def _is_concept_md(path: Path) -> bool:
    """Skip non-files, dotfiles, non-``.md``, and symlinks."""
    try:
        if path.is_symlink():
            return False
        if not path.is_file():
            return False
        if path.suffix.lower() != ".md":
            return False
        if path.name.startswith("."):
            return False
        return True
    except OSError:
        return False


def _extract_strict_frontmatter(content: str) -> dict | None:
    """Extract YAML frontmatter from a markdown page, returning ``None`` on
    parse failure rather than silently swallowing.

    Mirrors ``shared.reading_source_registry._strict_parse_frontmatter`` —
    we intentionally avoid ``shared.utils.extract_frontmatter`` because it
    swallows ``yaml.YAMLError`` (per #511 F6 lesson).

    Returns ``{}`` for pages with no frontmatter fence (empty parse, valid
    page just without metadata) and ``None`` only when the YAML body is
    syntactically broken.
    """
    if not content.startswith("---"):
        return {}
    parts = content.split("---", 2)
    if len(parts) < 3:
        return {}
    try:
        fm = yaml.safe_load(parts[1])
    except yaml.YAMLError:
        return None
    if fm is None:
        return {}
    if not isinstance(fm, dict):
        return None
    return fm


def _normalize_string_list(value: object) -> list[str]:
    """Coerce a frontmatter scalar / list into a clean ``list[str]``.

    YAML may return:
    - ``None`` (missing key) → ``[]``
    - a single scalar → ``[scalar]``
    - a list of scalars → strip + drop empties

    Non-string elements are coerced via ``str()``; empty / whitespace-only
    elements are dropped.
    """
    if value is None:
        return []
    if isinstance(value, str):
        stripped = value.strip()
        return [stripped] if stripped else []
    if isinstance(value, list):
        out: list[str] = []
        for item in value:
            if item is None:
                continue
            text = str(item).strip()
            if text:
                out.append(text)
        return out
    # Other scalar types — coerce to string.
    text = str(value).strip()
    return [text] if text else []
