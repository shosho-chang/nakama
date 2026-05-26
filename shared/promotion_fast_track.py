"""Confidence-based fast-track for ``EntityReviewItem`` (ADR-034 v2 PR2c).

Applies the ADR-034 v2 §D1 confidence fast-track to entity items
**after** the engine produces them and **before** the manifest is
persisted by ``PromotionReviewService``. Hybrid Entity gate semantics:

- ``canonical_match.confidence > 0.9`` AND ``match_basis != "none"``
  → auto-approve (``human_decision.decision="approve"``,
  ``decided_by="auto-fast-track"``). High-confidence cross-source
  disambig — UI review queue can hide these from the manual queue.
- ``canonical_match.confidence < 0.5`` AND ``match_basis != "none"``
  → auto-defer (``human_decision.decision="defer"``). Too uncertain
  even for queue review; surfaces in the deferred queue.
- ``0.5 ≤ confidence ≤ 0.9`` (with canonical_match) → no auto-decision
  (caller / human reviewer decides).
- ``canonical_match=None`` (e.g. ``create_entity`` items) → no
  auto-decision. Whether to create a new entity is a substantive review
  call, not a disambig confidence question.
- ``SourcePageReviewItem`` / ``ConceptReviewItem`` → pass through
  unchanged. Fast-track is Entity-scope per ADR-034 v2; Concept already
  has its own action policy producing recommendation= ``include`` /
  ``defer`` via the engine.

Threshold rationale (initial values; ADR-034 v2 §Neutral notes these are
guesses to be calibrated against manifest data):

- 0.9 high-water — concept_promotion_engine uses 0.90 for exact_alias
  auto-merge; same number here is one-knob consistency.
- 0.5 low-water — entity below this is too uncertain to act on; defer
  surfaces the disagreement to the reviewer's later-pass queue.
"""

from __future__ import annotations

from shared.log import get_logger
from shared.schemas.promotion_manifest import (
    ConceptReviewItem,
    EntityReviewItem,
    HumanDecision,
    PromotionManifest,
    SourcePageReviewItem,
    now_iso_utc,
)

_logger = get_logger("nakama.shared.promotion_fast_track")

# ── Thresholds (ADR-034 v2 §D1; initial values pending calibration) ─────────

FAST_TRACK_AUTO_APPROVE_THRESHOLD = 0.9
"""Strictly greater than this confidence → auto-approve."""

FAST_TRACK_AUTO_DEFER_THRESHOLD = 0.5
"""Strictly less than this confidence → auto-defer."""

AUTO_FAST_TRACK_DECIDER = "auto-fast-track"
"""``HumanDecision.decided_by`` value identifying the auto-applied
decision. Reviewer-facing surfaces (Bridge review queue, manifest audit
trail) MUST use this string to distinguish auto from manual decisions."""


def apply_entity_fast_track(manifest: PromotionManifest) -> int:
    """Mutate ``manifest.items`` in place, setting ``human_decision`` on
    eligible ``EntityReviewItem`` entries per the fast-track policy.

    Returns the number of items that received an auto-decision. Items
    that already carry a ``human_decision`` (e.g. re-runs preserving
    prior decisions) are left untouched — fast-track never overrides a
    recorded decision.

    Caller invariants:
    - manifest is mutable (PromotionManifest.items is not frozen).
    - manifest may contain mixed Source / Concept / Entity items; only
      Entity items are evaluated.
    - call site is post-engine, pre-persist (between engine.propose()
      and manifest_store.save()).
    """
    auto_decided = 0
    for item in manifest.items:
        decision = _evaluate_fast_track(item)
        if decision is None:
            continue
        item.human_decision = decision
        auto_decided += 1
    if auto_decided:
        _logger.info(
            "entity fast-track applied",
            extra={
                "category": "promotion_fast_track_applied",
                "source_id": manifest.source_id,
                "manifest_id": manifest.manifest_id,
                "auto_decided_count": auto_decided,
            },
        )
    return auto_decided


def _evaluate_fast_track(
    item: SourcePageReviewItem | ConceptReviewItem | EntityReviewItem,
) -> HumanDecision | None:
    """Return the fast-track decision for ``item``, or ``None`` if no
    auto-decision applies.

    Dispatch via ``match`` (ADR-034 v2 §D3). Default arm raises so
    register hygiene catches missing subtype arms loudly.
    """
    match item:
        case SourcePageReviewItem():
            return None
        case ConceptReviewItem():
            return None
        case EntityReviewItem(human_decision=hd) if hd is not None:
            # Never override a recorded decision — re-runs / human edits win.
            return None
        case EntityReviewItem(canonical_match=cm) if cm is None or cm.match_basis == "none":
            # create_entity (no match) — substantive review call,
            # not a confidence-disambig fast-track.
            return None
        case EntityReviewItem(canonical_match=cm) if (
            cm.confidence > FAST_TRACK_AUTO_APPROVE_THRESHOLD
        ):
            return HumanDecision(
                decision="approve",
                decided_at=now_iso_utc(),
                decided_by=AUTO_FAST_TRACK_DECIDER,
                note=(
                    f"auto-approved by fast-track: canonical_match.confidence="
                    f"{cm.confidence:.2f} > {FAST_TRACK_AUTO_APPROVE_THRESHOLD}"
                ),
            )
        case EntityReviewItem(canonical_match=cm) if (
            cm.confidence < FAST_TRACK_AUTO_DEFER_THRESHOLD
        ):
            return HumanDecision(
                decision="defer",
                decided_at=now_iso_utc(),
                decided_by=AUTO_FAST_TRACK_DECIDER,
                note=(
                    f"auto-deferred by fast-track: canonical_match.confidence="
                    f"{cm.confidence:.2f} < {FAST_TRACK_AUTO_DEFER_THRESHOLD}"
                ),
            )
        case EntityReviewItem():
            # 0.5 ≤ confidence ≤ 0.9 — queue normally, no auto-decision.
            return None
        case _:
            raise NotImplementedError(
                f"_evaluate_fast_track: no arm for ReviewItem subtype "
                f"{type(item).__name__!r}. Add a `case` per ADR-034 v2 §D3."
            )
