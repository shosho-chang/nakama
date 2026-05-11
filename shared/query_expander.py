"""Bilingual query expansion for ADR-020 S7.

expand_query(query, *, vault_path, _read_concept_fn):
  Detects [[wikilink]] targets in the query, reads each concept page's
  ``en_source_terms`` frontmatter, and returns the original query plus
  one extra query string per English synonym.

rrf_merge(results_lists, *, k, top_n):
  Reciprocal Rank Fusion over multiple ranked retrieval result lists.
  Returns at most ``top_n`` RankedResult objects sorted by RRF score.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Callable

import yaml

from shared.log import get_logger
from shared.reranker import RankedResult

logger = get_logger("nakama.shared.query_expander")

_RE_WIKILINK = re.compile(r"\[\[([^\]|]+)(?:\|[^\]]+)?\]\]")


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def extract_wikilinks(query: str) -> list[str]:
    """Return wikilink targets found in *query* (alias portion stripped)."""
    return [m.group(1) for m in _RE_WIKILINK.finditer(query)]


def expand_query(
    query: str,
    *,
    vault_path: Path,
    _read_concept_fn: Callable[[Path], str | None] | None = None,
) -> list[str]:
    """Expand a query using concept page ``en_source_terms``.

    Args:
        query:             User query, may contain ``[[wikilink]]`` targets.
        vault_path:        Root path of the Obsidian vault.
        _read_concept_fn:  Injectable for testing; defaults to reading files
                           from ``{vault_path}/KB/Wiki/{target}.md``.

    Returns:
        ``[original_query] + [en_source_term, ...]``.  Returns the
        original query as a single-element list when no expansions found.
    """
    wikilinks = extract_wikilinks(query)
    if not wikilinks:
        return [query]

    read_fn = _read_concept_fn if _read_concept_fn is not None else _default_read_concept

    extra: list[str] = []
    for target in wikilinks:
        concept_path = vault_path / "KB" / "Wiki" / f"{target}.md"
        content = read_fn(concept_path)
        if content is None:
            logger.debug("concept page not found: %s", concept_path)
            continue
        terms = _parse_en_source_terms(content)
        extra.extend(terms)

    if not extra:
        return [query]

    return [query] + extra


def rrf_merge(
    results_lists: list[list[RankedResult]],
    *,
    k: int = 60,
    top_n: int = 10,
) -> list[RankedResult]:
    """Merge multiple ranked lists via Reciprocal Rank Fusion.

    Args:
        results_lists:  Each inner list is a ranked retrieval result set.
        k:              RRF constant (default 60).
        top_n:          Maximum results to return.

    Returns:
        Merged, de-duplicated list of at most ``top_n`` RankedResult
        objects; ``score`` field holds the RRF score.
    """
    if not results_lists:
        return []

    rrf_scores: dict[str, float] = {}
    chunk_by_id: dict[str, RankedResult] = {}

    for results in results_lists:
        for rank, item in enumerate(results, start=1):
            rrf_scores[item.chunk_id] = rrf_scores.get(item.chunk_id, 0.0) + 1.0 / (k + rank)
            chunk_by_id[item.chunk_id] = item

    sorted_ids = sorted(rrf_scores, key=lambda cid: rrf_scores[cid], reverse=True)

    return [
        RankedResult(
            chunk_id=cid,
            text=chunk_by_id[cid].text,
            score=rrf_scores[cid],
        )
        for cid in sorted_ids[:top_n]
    ]


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _parse_en_source_terms(content: str) -> list[str]:
    """Extract ``en_source_terms`` list from YAML frontmatter."""
    if not content.startswith("---"):
        return []
    end = content.find("\n---", 3)
    if end == -1:
        return []
    fm_text = content[3:end]
    try:
        fm = yaml.safe_load(fm_text)
    except Exception:
        return []
    if not isinstance(fm, dict):
        return []
    terms = fm.get("en_source_terms", [])
    if not isinstance(terms, list):
        return []
    return [t for t in terms if isinstance(t, str)]


def _default_read_concept(path: Path) -> str | None:
    if not path.exists():
        return None
    return path.read_text(encoding="utf-8")
