"""Phase 2 concept dispatch wrapper for ADR-020 textbook ingest v3.

Wraps ``shared.kb_writer.upsert_concept_page`` with:
- Per-concept advisory lock (via ``shared.locks.advisory_lock``)
- ADR-020 v3 frontmatter fields: ``en_source_terms``, ``maturity_level``,
  ``high_value_signals``
- Hard invariant checks (placeholder stub detection, L3 body word count)

The 4-action dispatch routing (create / update_merge / update_conflict / noop)
is unchanged in ``upsert_concept_page`` (per Codex audit §2 — pipeline bypass
was the bug, not the routing logic).  This module is the new caller that
activates the existing dispatcher for Phase 2 in-chapter concept aggregation.
"""

from __future__ import annotations

import re
import sqlite3
from pathlib import Path
from typing import Literal

import yaml

from shared.kb_writer import upsert_concept_page
from shared.locks import advisory_lock
from shared.log import get_logger

logger = get_logger("nakama.shared.concept_dispatch")

# ADR-020 §Phase 2 hard min for L3 active concept bodies.
_L3_BODY_MIN_WORDS = 200

# Verbatim strings that indicate a phase-b-reconciliation-style placeholder stub.
# Any of these appearing in the body = ingest fail (0 tolerance per ADR-020).
_PLACEHOLDER_PATTERNS = (
    "Will be enriched",
    "phase-b-reconciliation",
    "Stub — auto-created by Phase B",
)

# YAML frontmatter parse — mirrors _FRONTMATTER_RE in kb_writer.py.
_FM_RE = re.compile(r"^---\n(.*?)\n---\n", re.DOTALL)


class IngestFailError(RuntimeError):
    """Raised when a hard ADR-020 invariant is violated.

    The chapter ingest must be aborted and the slug reported for human
    intervention when this error is raised.
    """


def dispatch_concept(
    slug: str,
    action: Literal["create", "update_merge", "update_conflict", "noop"],
    source_link: str,
    *,
    en_source_terms: list[str] | None = None,
    maturity_level: str | None = None,
    high_value_signals: list[str] | None = None,
    lock_conn: sqlite3.Connection | None = None,
    lock_timeout_s: float = 30.0,
    **kwargs,
) -> Path:
    """Dispatch a concept page write with lock, v3 fields, and invariant checks.

    Args:
        slug:             Concept page slug (filename without ``.md``).
        action:           One of ``create`` / ``update_merge`` / ``update_conflict``
                          / ``noop``.  Passed unchanged to ``upsert_concept_page``.
        source_link:      Wikilink to the source chapter, e.g.
                          ``"[[Sources/Books/bse-2024/ch1]]"``.
        en_source_terms:  English terms from the current chapter that map to this
                          concept (e.g. ``["gut microbiota", "intestinal flora"]``).
                          Deduped and merged into the ``en_source_terms`` frontmatter
                          list on every action.
        maturity_level:   ``"L1"`` / ``"L2"`` / ``"L3"`` per ADR-020 Maturity Model.
                          Written to frontmatter on ``create``; not overwritten on
                          update paths (caller sets maturity once at creation).
        high_value_signals: List of classifier signals that caused L2 promotion
                          (e.g. ``["section_heading", "bolded_define"]``).
                          Written to frontmatter on ``create`` only.
        lock_conn:        SQLite connection for per-concept advisory lock.  When
                          provided, the lock key ``"concept_{slug}"`` is held for the
                          duration of the write.  Pass ``None`` (default) to skip
                          locking (e.g. single-chapter sequential ingest).
        lock_timeout_s:   Advisory lock timeout.  Defaults to 30 s (longer than
                          typical update_merge LLM call).
        **kwargs:         Forwarded verbatim to ``upsert_concept_page``.

    Returns:
        Path to the written concept page.

    Raises:
        IngestFailError:  Hard invariant violated (placeholder stub body or L3
                          active with body word count < 200).
        LockTimeoutError: Could not acquire the per-concept lock within
                          ``lock_timeout_s`` seconds.
    """
    lock_key = f"concept_{slug}"

    def _run() -> Path:
        path = upsert_concept_page(slug, action, source_link, **kwargs)
        _patch_v3_frontmatter(
            path,
            en_source_terms=en_source_terms or [],
            maturity_level=maturity_level,
            high_value_signals=high_value_signals,
        )
        _check_hard_invariants(path, maturity_level=maturity_level)
        return path

    if lock_conn is not None:
        with advisory_lock(lock_conn, key=lock_key, timeout_s=lock_timeout_s):
            return _run()
    return _run()


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _patch_v3_frontmatter(
    path: Path,
    *,
    en_source_terms: list[str],
    maturity_level: str | None,
    high_value_signals: list[str] | None,
) -> None:
    """Read-modify-write: add ADR-020 v3 fields to the concept page frontmatter.

    This is a separate pass after ``upsert_concept_page`` so the existing v2
    dispatcher is not changed.  Fields patched:
    - ``en_source_terms``: dedup-merged on every call
    - ``maturity_level``: written only when not already present
    - ``high_value_signals``: written only on first write (absent = not patched)
    - ``schema_version``: upgraded from 2 → 3
    """
    raw = path.read_text(encoding="utf-8")
    m = _FM_RE.match(raw)
    if not m:
        logger.warning("No frontmatter in concept page %s — skipping v3 patch", path)
        return

    fm: dict = yaml.safe_load(m.group(1)) or {}
    body = raw[m.end() :]

    changed = False

    # --- en_source_terms (dedup merge) ---
    if en_source_terms:
        existing: list[str] = list(fm.get("en_source_terms") or [])
        for term in en_source_terms:
            if term not in existing:
                existing.append(term)
        fm["en_source_terms"] = existing
        changed = True

    # --- maturity_level (set once on create; don't overwrite on update paths) ---
    if maturity_level is not None and "maturity_level" not in fm:
        fm["maturity_level"] = maturity_level
        changed = True

    # --- high_value_signals (create-time only) ---
    if high_value_signals and "high_value_signals" not in fm:
        fm["high_value_signals"] = list(high_value_signals)
        changed = True

    # --- schema_version bump ---
    if fm.get("schema_version") != 3:
        fm["schema_version"] = 3
        changed = True

    if not changed:
        return

    fm_str = yaml.dump(
        fm,
        allow_unicode=True,
        default_flow_style=False,
        sort_keys=False,
        width=10**9,
    ).strip()
    path.write_text(f"---\n{fm_str}\n---\n{body}", encoding="utf-8")


def _check_hard_invariants(path: Path, *, maturity_level: str | None) -> None:
    """Raise IngestFailError if a hard ADR-020 invariant is violated.

    Invariants checked (per ADR-020 §Phase 2):
    1. No placeholder stub body (``"Will be enriched later"`` / Phase B stub text).
    2. L3 active concept body word count ≥ 200.
    """
    raw = path.read_text(encoding="utf-8")
    m = _FM_RE.match(raw)
    body = raw[m.end() :] if m else raw

    # 1. Placeholder stub check (0 tolerance per ADR-020)
    for pattern in _PLACEHOLDER_PATTERNS:
        if pattern in body:
            raise IngestFailError(
                f"placeholder stub detected in '{path.name}': "
                f"body contains '{pattern}'. "
                "ADR-020 mandates 0 phase-b-style stubs."
            )

    # 2. L3 body word count
    if maturity_level == "L3":
        word_count = len(body.split())
        if word_count < _L3_BODY_MIN_WORDS:
            raise IngestFailError(
                f"L3 active concept '{path.stem}' has body word count {word_count} "
                f"< {_L3_BODY_MIN_WORDS} (ADR-020 hard min). "
                "Ingest aborted — report this slug for human intervention."
            )
