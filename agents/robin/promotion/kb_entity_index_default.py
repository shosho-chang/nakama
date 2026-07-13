"""Filesystem-backed ``KBEntityIndex`` adapter (ADR-034 v2 PR3 — parallel
to ``agents.robin.promotion.kb_concept_index_default``).

Production implementation of the ``KBEntityIndex`` Protocol declared in
:mod:`agents.robin.promotion.entity_promotion_engine`. Scans both
``KB/Wiki/Entities/People/`` and ``KB/Wiki/Entities/Organizations/``
markdown pages, parses each page's frontmatter (``entity_label`` /
``aliases`` / ``languages`` / ``entity_kind``), and serves the
Protocol's two read methods (``lookup`` / ``aliases_starting_with``).

Diverges from concept index in one place: scans **two** subdirectories
(``People`` + ``Organizations``) and tags each entry with its
``entity_type`` from the parent directory name (or frontmatter
``entity_kind``, whichever is set — frontmatter wins, directory is the
fallback). Pages whose frontmatter ``entity_kind`` disagrees with the
parent directory are logged + skipped (data inconsistency).

Hard invariants (mirror :mod:`agents.robin.promotion.kb_concept_index_default`):

- Missing or non-directory ``entities_root`` → empty list, no exception.
  Bootstrap case for fresh vault.
- Missing People or Organizations subdir is fine (one may exist before
  the other).
- Malformed frontmatter / schema validation failure → logged + skipped;
  scan returns partial list.
- Bare ``except Exception`` forbidden (#511 F5) — narrow catches.

Cache invalidation: same lazy mtime-based strategy. Watches BOTH
subdirectories — when either dir's mtime changes, rescan both.
"""

from __future__ import annotations

from pathlib import Path

import yaml

from shared.log import get_logger
from shared.schemas.entity_promotion import KBEntityEntry
from shared.schemas.promotion_manifest import EntityType

_logger = get_logger("nakama.agents.robin.promotion.kb_entity_index_default")


_PARSE_FAILURES: tuple[type[BaseException], ...] = (
    OSError,
    yaml.YAMLError,
    ValueError,
    UnicodeDecodeError,
)


# Directory name → EntityType. Adding a new entity_type requires
# extending :data:`shared.schemas.promotion_manifest.EntityType` (closed
# Literal) and adding the subdir name here.
_DIR_TO_TYPE: dict[str, EntityType] = {
    "People": "person",
    "Organizations": "organization",
}


class VaultKBEntityIndex:
    """Read-only index of ``KB/Wiki/Entities/{People,Organizations}/*.md``.

    Protocol contract (entity_promotion_engine ``KBEntityIndex``):

    - ``lookup(alias)`` returns the entry whose ``aliases`` (or
      canonical_label) contains ``alias`` (case-insensitive). Returns
      ``None`` when no match.
    - ``aliases_starting_with(prefix)`` returns sorted aliases starting
      with ``prefix`` (case-insensitive). Empty prefix returns all.

    Non-Protocol convenience:

    - ``list_entries()`` returns the full parsed entry list.

    Construction: ``entities_root: Path`` is typically
    ``{vault}/KB/Wiki/Entities``. The two subdirectories ``People`` and
    ``Organizations`` live underneath.
    """

    def __init__(self, entities_root: Path) -> None:
        self._entities_root = Path(entities_root)
        self._entries_cache: list[KBEntityEntry] | None = None
        self._alias_lookup_cache: dict[str, KBEntityEntry] | None = None
        # Track each subdir's mtime independently so a change in one
        # subdir invalidates the cache.
        self._cached_mtimes_ns: tuple[int | None, ...] | None = None

    # ── Protocol API ──────────────────────────────────────────────────────

    def lookup(self, alias: str) -> KBEntityEntry | None:
        if not alias or not alias.strip():
            return None
        norm = alias.strip().casefold()
        return self._alias_lookup().get(norm)

    def aliases_starting_with(self, prefix: str) -> list[str]:
        norm_prefix = prefix.strip().casefold() if prefix else ""
        out: list[str] = []
        for entry in self._entries():
            for alias in (entry.canonical_label, *entry.aliases):
                if not alias:
                    continue
                if alias.casefold().startswith(norm_prefix):
                    out.append(alias)
        out.sort()
        return out

    # ── Convenience ───────────────────────────────────────────────────────

    def list_entries(self) -> list[KBEntityEntry]:
        return list(self._entries())

    # ── Internal scan + cache ─────────────────────────────────────────────

    def _entries(self) -> list[KBEntityEntry]:
        current_mtimes = self._current_mtimes_ns()
        if self._entries_cache is None or current_mtimes != self._cached_mtimes_ns:
            self._entries_cache = self._scan()
            self._alias_lookup_cache = None
            self._cached_mtimes_ns = current_mtimes
        return self._entries_cache

    def _alias_lookup(self) -> dict[str, KBEntityEntry]:
        entries = self._entries()
        if self._alias_lookup_cache is None:
            mapping: dict[str, KBEntityEntry] = {}
            for entry in entries:
                for alias in (entry.canonical_label, *entry.aliases):
                    if not alias:
                        continue
                    norm = alias.strip().casefold()
                    if not norm:
                        continue
                    mapping.setdefault(norm, entry)
            self._alias_lookup_cache = mapping
        return self._alias_lookup_cache

    def _current_mtimes_ns(self) -> tuple[int | None, ...]:
        """Read each subdirectory's mtime. Tuple shape is stable
        (People-first, Organizations-second) so cache invalidation
        compares element-wise."""
        return tuple(self._subdir_mtime(name) for name in _DIR_TO_TYPE)

    def _subdir_mtime(self, subdir_name: str) -> int | None:
        try:
            return (self._entities_root / subdir_name).stat().st_mtime_ns
        except OSError:
            return None

    def _scan(self) -> list[KBEntityEntry]:
        if not self._entities_root.is_dir():
            return []
        out: list[KBEntityEntry] = []
        # Stable subdir traversal order so output is deterministic.
        for subdir_name, expected_type in _DIR_TO_TYPE.items():
            subdir = self._entities_root / subdir_name
            if not subdir.is_dir():
                continue
            try:
                children = sorted(subdir.iterdir(), key=lambda p: p.name.casefold())
            except OSError as exc:
                _logger.warning(
                    "entities subdir iterdir failed",
                    extra={
                        "category": "kb_entity_index_iter_failed",
                        "subdir": str(subdir),
                        "error": str(exc),
                    },
                )
                continue
            for entry_path in children:
                if not _is_entity_md(entry_path):
                    continue
                parsed = self._parse_entry(entry_path, expected_type)
                if parsed is not None:
                    out.append(parsed)
        return out

    def _parse_entry(self, path: Path, expected_type: EntityType) -> KBEntityEntry | None:
        try:
            content = path.read_text(encoding="utf-8")
        except _PARSE_FAILURES as exc:
            _logger.warning(
                "entity page read failed",
                extra={
                    "category": "kb_entity_index_read_failed",
                    "path": str(path),
                    "error": str(exc),
                },
            )
            return None

        frontmatter = _extract_strict_frontmatter(content)
        if frontmatter is None:
            _logger.warning(
                "entity page frontmatter parse failed; skipped",
                extra={
                    "category": "kb_entity_index_frontmatter_parse_failed",
                    "path": str(path),
                },
            )
            return None

        # Frontmatter ``entity_kind`` wins; falls back to subdir-derived
        # expected_type. When both present but disagree, log + skip.
        fm_kind = frontmatter.get("entity_kind")
        if isinstance(fm_kind, str) and fm_kind.strip():
            kind_str = fm_kind.strip()
            if kind_str != expected_type:
                _logger.warning(
                    "entity page entity_kind mismatch with parent dir; skipped",
                    extra={
                        "category": "kb_entity_index_kind_mismatch",
                        "path": str(path),
                        "frontmatter_kind": kind_str,
                        "expected_kind": expected_type,
                    },
                )
                return None
            resolved_type: EntityType = expected_type
        else:
            resolved_type = expected_type

        name = frontmatter.get("entity_label")
        if not isinstance(name, str) or not name.strip():
            _logger.warning(
                "entity page missing 'entity_label' frontmatter; using filename stem",
                extra={
                    "category": "kb_entity_index_missing_label",
                    "path": str(path),
                },
            )
            name = path.stem

        aliases = _normalize_string_list(frontmatter.get("aliases", []))
        languages = _normalize_string_list(frontmatter.get("languages", []))

        try:
            return KBEntityEntry(
                entity_path=path.as_posix(),
                entity_type=resolved_type,
                canonical_label=name.strip(),
                aliases=aliases,
                languages=languages,
            )
        except ValueError as exc:
            _logger.warning(
                "entity page schema validation failed; skipped",
                extra={
                    "category": "kb_entity_index_schema_failed",
                    "path": str(path),
                    "error": str(exc),
                },
            )
            return None


# ── Helpers (mirrors kb_concept_index_default) ──────────────────────────────


def _is_entity_md(path: Path) -> bool:
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
    text = str(value).strip()
    return [text] if text else []
