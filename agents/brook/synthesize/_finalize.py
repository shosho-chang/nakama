"""Outline finalize — regenerate ``outline_final`` from cached evidence + user_actions
(issue #462, ADR-021 §3 Step 4).

After the user finishes reviewing the draft outline (issue #458), they hit
"finalize" and Brook re-derives the outline. ADR-021 §3 says:

    finalize → Brook 重新 generate outline（廣搜結果 cached，不重撈）

So this module is *not* a re-run of synthesize. It:

1. Reads the existing :class:`BrookSynthesizeStore` for ``slug`` (must exist
   — the route bootstraps nothing).
2. Applies the multiplicative reject discount to the cached evidence pool
   based on ``reject_evidence_entirely`` actions (same routine the next
   synthesize re-run would use, so finalize and re-run agree on ranking).
3. Filters out per-section rejects (``reject_from_section``) — those slugs
   are removed *only from the section the user rejected them from*; they
   stay in the pool for other sections.
4. Calls the same outline drafter (LLM) the draft pass used, so the output
   shape and contract are identical (5–7 sections, ≥2 refs each, refs
   resolve to slugs in the discounted pool).
5. Writes the result to ``outline_final`` via
   :func:`shared.brook_synthesize_store.update_outline_final`.

KB广搜 is *not* re-run — that is the whole point of caching the
``evidence_pool`` server-side (ADR-021 §4).
"""

from __future__ import annotations

from collections import defaultdict

from shared import brook_synthesize_store as _store
from shared.log import get_logger
from shared.schemas.brook_synthesize import (
    BrookSynthesizeStore,
    OutlineSection,
    UserAction,
)

from ._outline import AskFn, draft_outline
from ._reject_discount import apply_reject_discount

logger = get_logger("nakama.brook.synthesize.finalize")


_PER_SECTION_REJECT = "reject_from_section"


def _per_section_rejects(user_actions: list[UserAction]) -> dict[int, set[str]]:
    """Build ``section -> {evidence_slug}`` map of per-section rejects.

    Only ``reject_from_section`` actions count here. Skips actions with
    ``section is None`` defensively (the schema allows it but the action
    wouldn't make sense without a section).
    """
    out: dict[int, set[str]] = defaultdict(set)
    for a in user_actions:
        if a.action == _PER_SECTION_REJECT and a.section is not None:
            out[a.section].add(a.evidence_slug)
    return out


def regenerate_outline_final(
    slug: str,
    *,
    ask_fn: AskFn | None = None,
) -> BrookSynthesizeStore:
    """Regenerate ``outline_final`` for ``slug`` using cached evidence + user_actions.

    This is the finalize path (issue #462). It does *not* re-run KB
    retrieval; it re-uses the cached ``evidence_pool`` written by Brook
    synthesize (#459) and re-ranks via the standard reject discount.

    Args:
        slug: Project slug; the store must already exist.
        ask_fn: Optional LLM callable override (``(prompt, **kw) -> str``)
            for tests.

    Returns:
        The updated :class:`BrookSynthesizeStore` (with new
        ``outline_final``) as written to disk.

    Raises:
        StoreNotFoundError: when no synthesize has run yet for this slug.
        OutlineDraftError: when the LLM contract is violated (re-raised
            from :func:`draft_outline`).
        ValueError: when the cached pool is empty after discounting.
    """
    store = _store.read(slug)

    # 1. Apply the global-reject discount over the cached pool. This ranks
    #    rejected slugs *down* but keeps them visible — matches the next
    #    re-run's ranking exactly.
    discounted_pool = apply_reject_discount(
        list(store.evidence_pool),
        list(store.user_actions),
    )
    if not discounted_pool:
        raise ValueError(
            f"finalize: evidence pool is empty for slug={slug!r}; "
            "nothing to draft an outline from"
        )

    # 2. Re-draft the outline against the discounted pool. We pass the
    #    already-stored topic/keywords so the LLM has the same framing the
    #    draft pass used.
    sections = draft_outline(
        store.topic,
        list(store.keywords),
        discounted_pool,
        ask_fn=ask_fn,
    )

    # 3. Honour per-section rejects: any (section, slug) pair the user
    #    explicitly rejected during review is removed from that section's
    #    evidence_refs. We do *not* re-pad to MIN_REFS_PER_SECTION here —
    #    the user's explicit reject overrides the contract; the section's
    #    ref count may legitimately drop below the minimum after this step.
    per_sec = _per_section_rejects(list(store.user_actions))
    if per_sec:
        filtered: list[OutlineSection] = []
        for sec in sections:
            drop = per_sec.get(sec.section, set())
            if not drop:
                filtered.append(sec)
                continue
            kept_refs = [r for r in sec.evidence_refs if r not in drop]
            filtered.append(
                OutlineSection(
                    section=sec.section,
                    heading=sec.heading,
                    evidence_refs=kept_refs,
                )
            )
        sections = filtered

    # 4. Persist as outline_final. Re-uses the route's mutate path so the
    #    on-disk shape stays canonical and updated_at gets bumped.
    updated = _store.update_outline_final(slug, sections)
    logger.info(
        "synthesize.finalize slug=%s sections=%d per_section_rejects=%d",
        slug,
        len(sections),
        sum(len(v) for v in per_sec.values()),
    )
    return updated


__all__ = ["regenerate_outline_final"]
