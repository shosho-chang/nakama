"""Reject-aware ranker — multiplicative discount, no hide (issue #460, ADR-021 §4).

When Brook re-runs synthesize for a Project, the user's prior reject actions
should bias the evidence pool *down* but not *out*. Gemini push-back during
ADR-021 review: a permanent "naughty list" prevents serendipitous rediscovery
when the user's understanding of the topic shifts. Solution — multiplicative
discount on the per-chunk ``rrf_score`` proportional to how many times the
slug was rejected wholesale:

    discounted_rrf = base_rrf * (REJECT_DISCOUNT_FACTOR ** reject_count)

Counts only ``action == "reject_evidence_entirely"``. Per-section rejects
(``reject_from_section``) are *not* aggregated here — that grain says "this
evidence is wrong for that one section", not "I don't trust this source"; it
must not bleed into a global down-rank (AC bullet #3).

After discounting, chunks are re-sorted within each pool item and items are
re-sorted by best chunk score, mirroring the invariants that
:func:`agents.brook.synthesize._search.gather_evidence` produces. Pool items
with no chunks are kept in place — no chunks means nothing to discount.

Empty ``user_actions`` (first run, or no rejects so far) is a no-op return
of the input pool — callers can wire this in unconditionally.
"""

from __future__ import annotations

from collections import Counter
from typing import Any

from shared.log import get_logger
from shared.schemas.brook_synthesize import EvidencePoolItem, UserAction

from ._constants import REJECT_DISCOUNT_FACTOR

logger = get_logger("nakama.brook.synthesize.reject_discount")


_GLOBAL_REJECT = "reject_evidence_entirely"


def _count_global_rejects(user_actions: list[UserAction | dict]) -> Counter[str]:
    """Tally ``reject_evidence_entirely`` actions per evidence slug.

    Accepts either validated :class:`UserAction` instances or the raw dicts
    that the store-as-JSON path may surface — both shapes are common at the
    synthesize seam, and we don't want callers wrapping their list before
    calling us.
    """
    counts: Counter[str] = Counter()
    for action in user_actions:
        if isinstance(action, UserAction):
            kind = action.action
            slug = action.evidence_slug
        else:
            kind = action.get("action")
            slug = action.get("evidence_slug")
        if kind == _GLOBAL_REJECT and slug:
            counts[slug] += 1
    return counts


def apply_reject_discount(
    pool: list[EvidencePoolItem],
    user_actions: list[UserAction | dict],
    *,
    discount_factor: float = REJECT_DISCOUNT_FACTOR,
) -> list[EvidencePoolItem]:
    """Return a new pool with rejected slugs' chunks down-weighted and re-ranked.

    Args:
        pool: Output of :func:`gather_evidence` — one item per source.
        user_actions: ``BrookSynthesizeStore.user_actions`` from a prior run
            (empty list on first run).
        discount_factor: Override for tests / future tuning. Production
            callers should leave this at the
            :data:`REJECT_DISCOUNT_FACTOR` default so behaviour stays
            consistent across re-runs.

    Returns:
        A *new* list of new :class:`EvidencePoolItem` objects with chunks
        carrying discounted ``rrf_score`` and ordered by it. The input
        ``pool`` is not mutated. Items not affected by any reject are still
        wrapped in a new instance for symmetry but their chunk scores are
        identical to the input.
    """
    reject_counts = _count_global_rejects(user_actions)
    if not reject_counts:
        return list(pool)

    if discount_factor < 0 or discount_factor > 1:
        # Defensive — at >1 we'd *boost* rejects, at <0 we'd flip signs and
        # break the sort. Loud failure is better than a silent re-ranking bug.
        raise ValueError(f"discount_factor must be in [0, 1], got {discount_factor!r}")

    discounted_pool: list[EvidencePoolItem] = []
    affected = 0
    for item in pool:
        n_reject = reject_counts.get(item.slug, 0)
        if n_reject <= 0:
            discounted_pool.append(item.model_copy())
            continue

        affected += 1
        multiplier = discount_factor**n_reject
        new_chunks: list[dict[str, Any]] = []
        for chunk in item.chunks:
            # chunks is `list[Any]` per schema — production fills dicts; we
            # mirror that without coercing because tests / future callers
            # might use SimpleNamespace-likes. Skip if not a dict-like.
            if isinstance(chunk, dict):
                new_chunk = dict(chunk)
                base = float(new_chunk.get("rrf_score", 0.0))
                new_chunk["rrf_score"] = base * multiplier
                new_chunks.append(new_chunk)
            else:
                # Unknown shape — leave untouched; we have no contract to mutate.
                new_chunks.append(chunk)

        # Re-sort chunks within this item by the discounted score
        new_chunks.sort(key=lambda c: -(c.get("rrf_score", 0.0) if isinstance(c, dict) else 0.0))
        # Annotate hit_reason so the Web UI can show "downranked: 2 prior rejects"
        existing_reason = item.hit_reason
        downrank_note = f"downranked: {n_reject} prior reject{'s' if n_reject > 1 else ''}"
        merged_reason = f"{existing_reason}; {downrank_note}" if existing_reason else downrank_note

        discounted_pool.append(
            EvidencePoolItem(
                slug=item.slug,
                chunks=new_chunks,
                hit_reason=merged_reason,
            )
        )

    # Re-sort the pool by each item's best (post-discount) chunk score so a
    # heavily-rejected slug sinks but is not removed.
    def _best_score(item: EvidencePoolItem) -> float:
        best = 0.0
        for chunk in item.chunks:
            if isinstance(chunk, dict):
                score = float(chunk.get("rrf_score", 0.0))
                if score > best:
                    best = score
        return best

    discounted_pool.sort(key=lambda it: -_best_score(it))

    logger.info(
        "synthesize.reject_discount slugs_affected=%d unique_rejected=%d factor=%.3f",
        affected,
        len(reject_counts),
        discount_factor,
    )
    return discounted_pool


__all__ = ["apply_reject_discount"]
