"""Multi-query 廣搜 → evidence pool (ADR-021 §3 transition).

Brook's bilingual problem: Project topic is trad-Chinese, Zoro keywords are
mostly English. ADR-021 §3 settled on a *multi-query* transition — run the
zh topic and the en keywords through hybrid search separately, then dedupe
on the chunk granularity. Once ADR-022 multilingual embeddings ship, Brook
folds back to single-query (tracked in #452) and this module's caller flips
:data:`agents.brook.synthesize._constants.MULTI_QUERY` to ``False``.

Output shape conforms to
:class:`shared.schemas.brook_synthesize.EvidencePoolItem` — one item per
*source* (we use ``KBHit.path`` as the evidence slug because it is already
the unique source identifier in the KB and what the Web UI links to). Each
item's ``chunks`` list carries the per-chunk hit details Brook needs at
outline time (heading, chunk_id, rrf_score) without pinning the chunk shape
in the schema (ADR-021 §2 amendment — schema leaves ``chunks: list[Any]``).
"""

from __future__ import annotations

import sqlite3
from typing import Any

from shared import kb_hybrid_search
from shared.log import get_logger
from shared.schemas.brook_synthesize import EvidencePoolItem

from ._constants import BROOK_SYNTHESIZE_ENGINE, BROOK_SYNTHESIZE_TOP_K, MULTI_QUERY

logger = get_logger("nakama.brook.synthesize.search")


# Engine → kb_hybrid_search.search lanes mapping. Kept here so ADR-022 can
# add a "multilingual_dense" engine without touching call sites.
_ENGINE_LANES: dict[str, tuple[str, ...]] = {
    "hybrid": ("bm25", "vec"),
}


def _build_queries(topic: str, keywords: list[str]) -> list[tuple[str, str]]:
    """Return (lane_name, query) tuples for the multi-query fan-out.

    ``lane_name`` is informational only — used in ``hit_reason`` so the Web
    UI can show "matched zh-topic" / "matched en-keywords" provenance.

    Empty inputs are dropped (an all-empty `keywords` list yields a single
    zh-topic query — never an empty list).
    """
    queries: list[tuple[str, str]] = []
    if topic.strip():
        queries.append(("zh-topic", topic.strip()))
    if MULTI_QUERY and keywords:
        en_query = " ".join(k.strip() for k in keywords if k.strip())
        if en_query:
            queries.append(("en-keywords", en_query))
    if not queries:
        # Guard: caller passed all-blank input. Surface as ValueError rather
        # than silently issuing a no-op search.
        raise ValueError("synthesize requires non-empty topic or keywords")
    return queries


def _engine_lanes(engine: str) -> tuple[str, ...]:
    try:
        return _ENGINE_LANES[engine]
    except KeyError as exc:
        raise ValueError(
            f"unsupported BROOK_SYNTHESIZE_ENGINE={engine!r}; known: {sorted(_ENGINE_LANES)}"
        ) from exc


def gather_evidence(
    topic: str,
    keywords: list[str],
    *,
    top_k: int = BROOK_SYNTHESIZE_TOP_K,
    engine: str = BROOK_SYNTHESIZE_ENGINE,
    db: sqlite3.Connection | None = None,
) -> list[EvidencePoolItem]:
    """Fan out the multi-query search and collapse hits into the evidence pool.

    Dedupe key is ``(source_path, chunk_id)`` — best ``rrf_score`` wins.
    Items are grouped by ``source_path`` (= evidence slug). Each item's
    ``chunks`` list is sorted by ``rrf_score`` desc; items themselves are
    sorted by their *best* chunk's ``rrf_score`` desc, so the most relevant
    source is first.

    ``hit_reason`` records which query lane(s) surfaced the source — useful
    provenance in the Web UI without leaking ranker internals.
    """
    queries = _build_queries(topic, keywords)
    lanes = _engine_lanes(engine)

    # (path, chunk_id) → best chunk dict
    best_chunk: dict[tuple[str, int], dict[str, Any]] = {}
    # path → set of query lanes that hit it
    path_lanes: dict[str, set[str]] = {}

    for lane_name, query in queries:
        hits = kb_hybrid_search.search(query, top_k=top_k, lanes=lanes, db=db)
        logger.info(
            "synthesize.search lane=%s query=%r hits=%d",
            lane_name,
            query,
            len(hits),
        )
        for hit in hits:
            key = (hit.path, hit.chunk_id)
            existing = best_chunk.get(key)
            if existing is None or hit.rrf_score > existing["rrf_score"]:
                best_chunk[key] = {
                    "chunk_id": hit.chunk_id,
                    "heading": hit.heading,
                    "page_title": hit.page_title,
                    "chunk_text": hit.chunk_text,
                    "rrf_score": hit.rrf_score,
                    "lane_ranks": dict(hit.lane_ranks),
                }
            path_lanes.setdefault(hit.path, set()).add(lane_name)

    # Group by source path
    by_path: dict[str, list[dict[str, Any]]] = {}
    for (path, _chunk_id), chunk in best_chunk.items():
        by_path.setdefault(path, []).append(chunk)

    pool: list[EvidencePoolItem] = []
    for path, chunks in by_path.items():
        chunks.sort(key=lambda c: -c["rrf_score"])
        lanes_hit = sorted(path_lanes.get(path, set()))
        hit_reason = "matched " + " + ".join(lanes_hit) if lanes_hit else ""
        pool.append(
            EvidencePoolItem(
                slug=path,
                chunks=chunks,
                hit_reason=hit_reason,
            )
        )

    pool.sort(key=lambda item: -max((c["rrf_score"] for c in item.chunks), default=0.0))
    logger.info(
        "synthesize.search pool sources=%d chunks=%d",
        len(pool),
        sum(len(item.chunks) for item in pool),
    )
    return pool


__all__ = ["gather_evidence"]
