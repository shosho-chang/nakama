"""Adapter writing the synthesize result via :mod:`shared.brook_synthesize_store`.

Brook synthesize (#459) is the *creator* of the per-slug store — the API
route refuses to materialise an empty store on POST. This module wraps the
write so the public ``synthesize()`` entry point does not have to know
whether a store already exists for the slug (e.g. because of a re-run).

Re-run policy: ADR-021 §3 says re-running synthesize should overwrite the
evidence pool + outline_draft but **not** discard the user's review history
(``user_actions``) or finalised outline (``outline_final``). We honour that
here by reading the existing store, merging the new pool/draft on top, and
calling ``write`` (full-replace, but with preserved fields).
"""

from __future__ import annotations

from shared import brook_synthesize_store as _store
from shared.log import get_logger
from shared.schemas.brook_synthesize import (
    BrookSynthesizeStore,
    EvidencePoolItem,
    OutlineSection,
)

logger = get_logger("nakama.brook.synthesize.store_writer")


def persist(
    *,
    slug: str,
    topic: str,
    keywords: list[str],
    evidence_pool: list[EvidencePoolItem],
    outline_draft: list[OutlineSection],
    unmatched_trending_angles: list[str] | None = None,
) -> BrookSynthesizeStore:
    """Create-or-overwrite the store for ``slug``, preserving review state.

    On first run, calls ``store.create``. On re-runs, calls ``store.write``
    after copying ``user_actions`` and ``outline_final`` from the existing
    store. ``unmatched_trending_angles`` is always overwritten (it is a
    derived warning recomputed from the freshly-drafted outline; preserving
    a stale value across re-runs would mislead 修修).
    """
    unmatched = list(unmatched_trending_angles or [])
    if _store.exists(slug):
        existing = _store.read(slug)
        next_store = existing.model_copy(
            update={
                "topic": topic,
                "keywords": list(keywords),
                "evidence_pool": list(evidence_pool),
                "outline_draft": list(outline_draft),
                "unmatched_trending_angles": unmatched,
                # user_actions + outline_final preserved via model_copy
            }
        )
        result = _store.write(next_store)
        logger.info(
            "synthesize.persist re-run slug=%s preserved_actions=%d preserved_final=%d unmatched_angles=%d",
            slug,
            len(existing.user_actions),
            len(existing.outline_final),
            len(unmatched),
        )
        return result

    fresh = BrookSynthesizeStore(
        project_slug=slug,
        topic=topic,
        keywords=list(keywords),
        evidence_pool=list(evidence_pool),
        outline_draft=list(outline_draft),
        unmatched_trending_angles=unmatched,
    )
    result = _store.create(fresh)
    logger.info(
        "synthesize.persist created slug=%s unmatched_angles=%d", slug, len(unmatched)
    )
    return result


__all__ = ["persist"]
