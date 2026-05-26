"""Tests for ``shared.promotion_fast_track`` (ADR-034 v2 PR2c).

Covers:
- > 0.9 + match_basis != "none" → auto-approve
- < 0.5 + match_basis != "none" → auto-defer
- 0.5–0.9 (inclusive both ends) → no auto-decision
- canonical_match=None → no auto-decision (create_entity case)
- match_basis="none" → no auto-decision (no actual disambig signal)
- existing human_decision → never overridden
- SourcePage / Concept items pass through untouched
- Mixed manifest: only Entity items affected; return count accurate
- Default arm raises (register hygiene)
"""

from __future__ import annotations

import pytest

from shared.promotion_fast_track import (
    AUTO_FAST_TRACK_DECIDER,
    _evaluate_fast_track,
    apply_entity_fast_track,
)
from shared.schemas.promotion_manifest import (
    CanonicalMatch,
    ConceptReviewItem,
    EntityCanonicalMatch,
    EntityReviewItem,
    EvidenceAnchor,
    HumanDecision,
    PersonMetadata,
    PromotionManifest,
    RecommenderMetadata,
    SourcePageReviewItem,
)


def _evidence() -> list[EvidenceAnchor]:
    return [
        EvidenceAnchor(
            kind="external_ref",
            source_path="00:12:34",
            locator="00:12:34",
            excerpt="x",
            confidence=0.85,
        )
    ]


def _manifest(items: list) -> PromotionManifest:
    return PromotionManifest(
        schema_version=2,
        manifest_id="m-1",
        source_id="podcast:ep-1",
        created_at="2026-05-27T00:00:00Z",
        status="needs_review",
        recommender=RecommenderMetadata(
            model_name="claude-opus-4-7",
            model_version="2026-04",
            recommended_at="2026-05-27T00:00:00Z",
        ),
        items=items,
        commit_batches=[],
    )


def _entity(
    *,
    confidence: float,
    match_basis: str = "exact_alias",
    canonical: bool = True,
    decision: HumanDecision | None = None,
) -> EntityReviewItem:
    cm = None
    if canonical:
        # When match_basis is "none", V10 invariant forbids matched_entity_path.
        if match_basis == "none":
            cm = EntityCanonicalMatch(
                match_basis="none",
                matched_entity_path=None,
                confidence=confidence,
            )
        else:
            cm = EntityCanonicalMatch(
                match_basis=match_basis,  # type: ignore[arg-type]
                matched_entity_path="KB/Wiki/Entities/People/X.md",
                confidence=confidence,
            )
    return EntityReviewItem(
        item_id=f"ent-{confidence}",
        recommendation="include",
        action="update_merge_entity" if canonical and match_basis != "none" else "create_entity",
        reason="r",
        evidence=_evidence(),
        risk=[],
        confidence=confidence,
        source_importance=0.9,
        reader_salience=0.5,
        entity_label="Andrew Huberman",
        metadata=PersonMetadata(),
        canonical_match=cm,
        human_decision=decision,
    )


def _source_page() -> SourcePageReviewItem:
    return SourcePageReviewItem(
        item_id="src-1",
        recommendation="include",
        action="create",
        reason="r",
        evidence=_evidence(),
        risk=[],
        confidence=0.95,
        source_importance=0.9,
        reader_salience=0.5,
        target_kb_path="KB/Wiki/Sources/x/ch1.md",
    )


def _concept() -> ConceptReviewItem:
    return ConceptReviewItem(
        item_id="concept-1",
        recommendation="include",
        action="create_global_concept",
        reason="r",
        evidence=_evidence(),
        risk=[],
        confidence=0.95,
        source_importance=0.9,
        reader_salience=0.5,
        concept_label="ATP",
        evidence_language="en",
        canonical_match=CanonicalMatch(
            match_basis="exact_alias",
            matched_concept_path="KB/Wiki/Concepts/ATP.md",
            confidence=0.95,
        ),
    )


# ── Auto-approve path (> 0.9) ───────────────────────────────────────────────


def test_high_confidence_above_threshold_auto_approves() -> None:
    item = _entity(confidence=0.95)
    decision = _evaluate_fast_track(item)
    assert decision is not None
    assert decision.decision == "approve"
    assert decision.decided_by == AUTO_FAST_TRACK_DECIDER


def test_threshold_boundary_exactly_at_high_does_not_auto_approve() -> None:
    """Strictly greater than 0.9 — exactly 0.9 stays in the manual queue."""
    item = _entity(confidence=0.9)
    assert _evaluate_fast_track(item) is None


# ── Auto-defer path (< 0.5) ─────────────────────────────────────────────────


def test_low_confidence_below_threshold_auto_defers() -> None:
    item = _entity(confidence=0.3)
    decision = _evaluate_fast_track(item)
    assert decision is not None
    assert decision.decision == "defer"
    assert decision.decided_by == AUTO_FAST_TRACK_DECIDER


def test_threshold_boundary_exactly_at_low_does_not_auto_defer() -> None:
    """Strictly less than 0.5 — exactly 0.5 stays in the manual queue."""
    item = _entity(confidence=0.5)
    assert _evaluate_fast_track(item) is None


# ── Middle band (0.5–0.9) ───────────────────────────────────────────────────


def test_middle_band_no_auto_decision() -> None:
    for conf in (0.5, 0.65, 0.75, 0.85, 0.9):
        assert _evaluate_fast_track(_entity(confidence=conf)) is None, conf


# ── Edge cases ──────────────────────────────────────────────────────────────


def test_no_canonical_match_no_auto_decision() -> None:
    """create_entity items (canonical_match=None) — substantive review call."""
    item = _entity(confidence=0.99, canonical=False)
    assert _evaluate_fast_track(item) is None


def test_match_basis_none_no_auto_decision() -> None:
    """match_basis='none' — no actual cross-source signal even though
    canonical_match object exists."""
    item = _entity(confidence=0.99, match_basis="none")
    assert _evaluate_fast_track(item) is None


def test_existing_human_decision_never_overridden() -> None:
    """Re-runs / manual edits preserved — fast-track never overwrites."""
    existing = HumanDecision(
        decision="reject",
        decided_at="2026-05-26T00:00:00Z",
        decided_by="shosho",
    )
    item = _entity(confidence=0.99, decision=existing)
    assert _evaluate_fast_track(item) is None


def test_source_page_passes_through() -> None:
    assert _evaluate_fast_track(_source_page()) is None


def test_concept_item_passes_through() -> None:
    assert _evaluate_fast_track(_concept()) is None


def test_unknown_item_kind_raises() -> None:
    """ADR-034 v2 §D3 — `case _: raise` enforces register hygiene."""

    class _FakeItem:
        item_id = "fake"
        human_decision = None

    with pytest.raises(NotImplementedError, match="_FakeItem"):
        _evaluate_fast_track(_FakeItem())  # type: ignore[arg-type]


# ── Manifest-level integration ──────────────────────────────────────────────


def test_apply_entity_fast_track_mutates_items_in_place() -> None:
    high = _entity(confidence=0.95)
    low = _entity(confidence=0.3)
    mid = _entity(confidence=0.7)
    manifest = _manifest([_source_page(), high, low, mid, _concept()])
    count = apply_entity_fast_track(manifest)
    assert count == 2
    assert high.human_decision is not None
    assert high.human_decision.decision == "approve"
    assert low.human_decision is not None
    assert low.human_decision.decision == "defer"
    assert mid.human_decision is None
    # Non-entity items untouched
    assert manifest.items[0].human_decision is None
    assert manifest.items[-1].human_decision is None


def test_apply_entity_fast_track_returns_zero_for_concept_only_manifest() -> None:
    """Source/Concept-only manifests (current start_review path) → no-op."""
    manifest = _manifest([_source_page(), _concept()])
    assert apply_entity_fast_track(manifest) == 0


def test_apply_entity_fast_track_skips_items_with_existing_decision() -> None:
    existing = HumanDecision(
        decision="reject",
        decided_at="2026-05-26T00:00:00Z",
        decided_by="shosho",
    )
    item = _entity(confidence=0.99, decision=existing)
    manifest = _manifest([item])
    count = apply_entity_fast_track(manifest)
    assert count == 0
    assert item.human_decision is existing  # untouched reference
