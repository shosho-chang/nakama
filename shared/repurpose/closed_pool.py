"""Closed-pool KB retrieval wrapper for Brook Line 1b (and future closed-pool flows).

Purpose (ADR-027 §Decision 6, Layer 1)
---------------------------------------
Wrap :func:`shared.kb_hybrid_search.search` so that retrieval is restricted
to a caller-supplied set of slugs (typically the Line 1b research_pack ∪
{transcript_slug}).

This is a **reminder, not enforcement.** It does NOT prevent the downstream
LLM from leaking its parametric memory about the same topic. What it DOES
prevent is the system accidentally surfacing chunks from unrelated KB pages
during normal retrieval — i.e. it keeps the *system's behaviour* aligned
with 修修's intent, while leaving the parametric-memory red line as a
matter of 修修 self-discipline + prompt + post-process ⚠️ markers.

See: `memory/claude/feedback_redline_self_discipline_not_enforcement.md`.

Explicit cut: no transitive backlink traversal
----------------------------------------------
``shared.kb_hybrid_search.search`` supports a ``wikilink`` lane that
expands 1-hop neighbours via the ``kb_wikilinks`` table. ``closed_pool_search``
**deliberately omits** that lane — we filter to ``allowed_slugs`` *after*
retrieval too, so even if a future caller passed ``lanes=("...", "wikilink")``
we would still strip neighbour chunks. Adding transitive expansion would
defeat the closed-pool intent (ADR-027 §Decision 6 explicit cut).

A KB chunk may carry ``mentioned_in:`` backlinks to outside slugs in its
text body — that is acceptable, the LLM sees the chunk text as-is. What we
guarantee is that retrieval never *returns chunks belonging to* outside slugs.
"""

from __future__ import annotations

import sqlite3
from collections.abc import Iterable

from shared.kb_hybrid_search import SearchHit, search
from shared.log import get_logger

logger = get_logger("nakama.shared.repurpose.closed_pool")


# Lanes intentionally fixed to BM25 + dense vec only — never `wikilink`.
# See module docstring for rationale.
_CLOSED_POOL_LANES: tuple[str, ...] = ("bm25", "vec")


def closed_pool_search(
    query: str,
    *,
    allowed_slugs: Iterable[str],
    top_k: int = 10,
    db: sqlite3.Connection | None = None,
) -> list[SearchHit]:
    """Run KB hybrid search and drop any hit whose path is not in `allowed_slugs`.

    Args:
        query: Free-text query forwarded to ``kb_hybrid_search.search``.
        allowed_slugs: KB paths (e.g. ``"KB/Wiki/Sources/article-x"``) that the
            caller authorises retrieval to surface. Typically the Line 1b
            ``research_pack ∪ {transcript_slug}``. An empty / falsy set yields
            an empty result (defensive: avoid full-corpus leak by mistake).
        top_k: Max results returned AFTER filtering. We over-fetch internally
            (``top_k * 4``, capped at 60) so the pool-restricted result still
            comes back densely populated when many hits are outside the pool.
        db: Optional sqlite3.Connection override (for tests).

    Returns:
        ``list[SearchHit]`` ordered by RRF score, length ≤ ``top_k``.

    Notes:
        - This wrapper does NOT do transitive backlink traversal. A KB chunk
          inside ``allowed_slugs`` may *contain text* that references outside
          slugs; we surface that chunk as-is but never follow the link to
          fetch the outside chunk.
        - This is a reminder + audit trail, not enforcement. LLM parametric
          memory leak is out of scope; see module docstring.
    """
    allowed = {s for s in allowed_slugs if s}
    if not allowed:
        logger.warning(
            "closed_pool_search called with empty allowed_slugs — returning []. "
            "This is defensive; if you intended an open search, call "
            "shared.kb_hybrid_search.search directly."
        )
        return []

    # Over-fetch so that filtering to the closed pool still leaves enough hits.
    # Capped at 60 to keep RRF candidate pools sane.
    over_fetch = min(max(top_k * 4, top_k), 60)
    raw_hits = search(query, top_k=over_fetch, lanes=_CLOSED_POOL_LANES, db=db)

    filtered: list[SearchHit] = []
    dropped_outside = 0
    for hit in raw_hits:
        if hit.path in allowed:
            filtered.append(hit)
            if len(filtered) >= top_k:
                break
        else:
            dropped_outside += 1

    if dropped_outside:
        logger.info(
            "closed_pool_search query=%r returned=%d dropped_outside=%d allowed=%d",
            query,
            len(filtered),
            dropped_outside,
            len(allowed),
        )
    return filtered
