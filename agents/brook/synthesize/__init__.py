"""Brook synthesize — multi-query 廣搜 + outline drafter (issue #459, ADR-021 §3).

Public API: :func:`synthesize`. Sub-modules (`_search`, `_outline`,
`_store_writer`, `_constants`) are private and may be reorganised without
notice; downstream code should depend only on this package's surface.

Pipeline:

    topic + keywords
        → multi-query hybrid search (zh-topic + en-keywords lanes)
        → dedupe by (source_path, chunk_id) → group by source
        → evidence_pool : list[EvidencePoolItem]
        → outline drafter (LLM, 5–7 sections, each cites ≥2 source slugs)
        → outline_draft : list[OutlineSection]
        → persist via shared.brook_synthesize_store

The frozen defaults (``BROOK_SYNTHESIZE_TOP_K = 15``, ``ENGINE = "hybrid"``)
come from the #457 mini-bench HITL freeze (ADR-021 §3, 2026-05-07).
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass

from shared.log import get_logger
from shared.schemas.brook_synthesize import (
    BrookSynthesizeStore,
    EvidencePoolItem,
    OutlineSection,
)

from ._constants import (
    BROOK_SYNTHESIZE_ENGINE,
    BROOK_SYNTHESIZE_TOP_K,
    MULTI_QUERY,
    OUTLINE_MAX_SECTIONS,
    OUTLINE_MIN_REFS_PER_SECTION,
    OUTLINE_MIN_SECTIONS,
)
from ._outline import OutlineDraftError, draft_outline
from ._search import gather_evidence
from ._store_writer import persist

logger = get_logger("nakama.brook.synthesize")


@dataclass(frozen=True)
class SynthesizeResult:
    """Returned to the caller (CLI / Sunny route).

    The ``store`` field is the freshly-written
    :class:`BrookSynthesizeStore` — useful so the route can return it as
    JSON without re-reading the file.
    """

    slug: str
    evidence_pool: list[EvidencePoolItem]
    outline_draft: list[OutlineSection]
    store: BrookSynthesizeStore


def synthesize(
    slug: str,
    topic: str,
    keywords: list[str],
    *,
    db: sqlite3.Connection | None = None,
    ask_fn=None,
) -> SynthesizeResult:
    """Run Brook synthesize end-to-end and persist the result.

    Args:
        slug: Project slug. Must match ``[A-Za-z0-9_-]+`` (validated by the
            store layer; we let it surface).
        topic: Trad-Chinese one-sentence topic from the Project page.
        keywords: Zoro-supplied keyword list (mostly English).
        db: Optional sqlite connection override — tests inject an in-memory
            KB index here. Production lets ``kb_hybrid_search`` resolve the
            module-level connection.
        ask_fn: Optional LLM callable override (``(prompt, **kw) -> str``).
            Tests pass a stub; production uses ``shared.llm.ask`` via the
            router.

    Raises:
        ValueError: empty topic+keywords.
        OutlineDraftError: LLM contract violation (see ``_outline``).
        StoreAlreadyExistsError: only when a previous bug created a store
            for this slug *with no user_actions* and we hit a race; in
            normal operation the re-run path preserves and overwrites.
    """
    logger.info(
        "synthesize.start slug=%s topic=%r keywords=%d top_k=%d engine=%s multi_query=%s",
        slug,
        topic,
        len(keywords),
        BROOK_SYNTHESIZE_TOP_K,
        BROOK_SYNTHESIZE_ENGINE,
        MULTI_QUERY,
    )

    pool = gather_evidence(
        topic,
        keywords,
        top_k=BROOK_SYNTHESIZE_TOP_K,
        engine=BROOK_SYNTHESIZE_ENGINE,
        db=db,
    )
    outline = draft_outline(topic, keywords, pool, ask_fn=ask_fn)
    store = persist(
        slug=slug,
        topic=topic,
        keywords=keywords,
        evidence_pool=pool,
        outline_draft=outline,
    )

    logger.info(
        "synthesize.done slug=%s pool_sources=%d outline_sections=%d",
        slug,
        len(pool),
        len(outline),
    )
    return SynthesizeResult(
        slug=slug,
        evidence_pool=pool,
        outline_draft=outline,
        store=store,
    )


__all__ = [
    "BROOK_SYNTHESIZE_ENGINE",
    "BROOK_SYNTHESIZE_TOP_K",
    "MULTI_QUERY",
    "OUTLINE_MAX_SECTIONS",
    "OUTLINE_MIN_REFS_PER_SECTION",
    "OUTLINE_MIN_SECTIONS",
    "OutlineDraftError",
    "SynthesizeResult",
    "synthesize",
]
