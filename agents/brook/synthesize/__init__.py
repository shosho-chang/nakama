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

from shared import brook_synthesize_store as _store
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
    REJECT_DISCOUNT_FACTOR,
)
from ._finalize import regenerate_outline_final
from ._outline import OutlineDraftError, draft_outline
from ._reject_discount import apply_reject_discount
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
    trending_angles: list[str] | None = None,
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
        trending_angles: Optional Zoro trending-angle strings (ADR-027
            §Decision 4). When supplied, the outline drafter is shown the
            angles and may use ones with strong evidence correspondence as
            section headings. Angles that no drafted section matches surface
            on the store as ``unmatched_trending_angles`` (warning channel
            for 修修; reverse signal for Robin discovery). Backwards
            compatible: when ``None`` or empty, prompt and behaviour are
            identical to the pre-ADR-027 baseline.

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

    # Reject-aware down-rank (issue #460, ADR-021 §4): when a prior store
    # exists for this slug, replay its `reject_evidence_entirely` actions as
    # a multiplicative discount on each rejected slug's chunk scores. We do
    # this *after* dedupe (so the discount sees the canonical rrf_score) and
    # *before* outline drafting (so the LLM sees a re-ranked pool). First-run
    # / no-prior-store path is a no-op.
    prior_user_actions: list = []
    if _store.exists(slug):
        try:
            prior_user_actions = list(_store.read(slug).user_actions)
        except Exception:  # pragma: no cover — corrupt store should not block
            logger.exception("synthesize.prior_actions_read_failed slug=%s", slug)
            prior_user_actions = []
    pool = apply_reject_discount(pool, prior_user_actions)

    outline = draft_outline(
        topic, keywords, pool, ask_fn=ask_fn, trending_angles=trending_angles
    )

    # ADR-027 §Decision 4: collect angles the outline drafter actually
    # matched (across all sections), then set-difference vs the input list.
    # Only input angles that no section claimed go into the warning bucket.
    # `trending_match` values that aren't in the input list (LLM noise) are
    # ignored here — we don't bounce the outline, but we also don't count
    # them as "matched input angles". See test
    # ``test_unmatched_ignores_llm_invented_angles``.
    input_angles = list(trending_angles or [])
    matched: set[str] = set()
    for section in outline:
        for angle in section.trending_match:
            if angle in input_angles:
                matched.add(angle)
    unmatched = [angle for angle in input_angles if angle not in matched]

    store = persist(
        slug=slug,
        topic=topic,
        keywords=keywords,
        evidence_pool=pool,
        outline_draft=outline,
        unmatched_trending_angles=unmatched,
    )

    logger.info(
        "synthesize.done slug=%s pool_sources=%d outline_sections=%d unmatched_angles=%d",
        slug,
        len(pool),
        len(outline),
        len(unmatched),
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
    "REJECT_DISCOUNT_FACTOR",
    "SynthesizeResult",
    "apply_reject_discount",
    "regenerate_outline_final",
    "synthesize",
]
