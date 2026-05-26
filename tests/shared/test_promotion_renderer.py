"""Tests for shared.promotion_renderer — `render_review_item` ADR-034 v2 §D3.

Verifies:
- ``render_review_item`` dispatches to ``render_source_page`` for
  ``SourcePageReviewItem`` byte-identically.
- ``render_review_item`` dispatches to ``render_concept_page`` for
  ``ConceptReviewItem`` byte-identically.
- Default arm raises ``NotImplementedError`` for unknown subtype.
"""

from __future__ import annotations

import hashlib

import pytest

from shared.promotion_renderer import (
    render_concept_page,
    render_review_item,
    render_source_page,
)
from shared.schemas.promotion_manifest import (
    CanonicalMatch,
    ConceptReviewItem,
    EvidenceAnchor,
    HumanDecision,
    PromotionManifest,
    RecommenderMetadata,
    SourcePageReviewItem,
)


def _evidence() -> list[EvidenceAnchor]:
    return [
        EvidenceAnchor(
            kind="chapter_quote",
            source_path="data/books/bse/original.epub",
            locator="epubcfi(/6/4[ch1]!/4/2/16/1:0,/6/4[ch1]!/4/2/16/1:200)",
            excerpt="ATP hydrolysis releases energy.",
            confidence=0.92,
        )
    ]


def _make_manifest() -> PromotionManifest:
    return PromotionManifest(
        manifest_id="m-1",
        source_id="ebook:bse",
        created_at="2026-05-26T00:00:00Z",
        status="needs_review",
        recommender=RecommenderMetadata(
            model_name="claude-opus-4-7",
            model_version="2026-04",
            recommended_at="2026-05-26T00:00:00Z",
        ),
        items=[],
        commit_batches=[],
    )


def _approved_decision() -> HumanDecision:
    return HumanDecision(
        decision="approve",
        decided_at="2026-05-26T00:00:00Z",
        decided_by="tester",
    )


def _make_source() -> SourcePageReviewItem:
    return SourcePageReviewItem(
        item_id="src-page-1",
        recommendation="include",
        action="create",
        reason="claim-dense chapter on ATP",
        evidence=_evidence(),
        risk=[],
        confidence=0.93,
        source_importance=0.85,
        reader_salience=0.7,
        target_kb_path="KB/Wiki/Sources/bse/ch1.md",
        chapter_ref="Ch1: Energy systems",
        human_decision=_approved_decision(),
    )


def _make_concept() -> ConceptReviewItem:
    return ConceptReviewItem(
        item_id="concept-atp",
        recommendation="include",
        action="create_global_concept",
        reason="recurrent across multiple chapters",
        evidence=_evidence(),
        risk=[],
        confidence=0.95,
        source_importance=0.9,
        reader_salience=0.8,
        concept_label="ATP",
        evidence_language="en",
        canonical_match=CanonicalMatch(
            match_basis="exact_alias",
            matched_concept_path="KB/Wiki/Concepts/ATP.md",
            confidence=0.98,
        ),
        human_decision=_approved_decision(),
    )


def test_render_review_item_dispatches_source_page_byte_identical() -> None:
    item = _make_source()
    manifest = _make_manifest()
    via_dispatch = render_review_item(item, manifest)
    via_direct = render_source_page(item, manifest)
    assert via_dispatch == via_direct
    assert (
        hashlib.sha256(via_dispatch.encode("utf-8")).hexdigest()
        == hashlib.sha256(via_direct.encode("utf-8")).hexdigest()
    )


def test_render_review_item_dispatches_concept_byte_identical() -> None:
    item = _make_concept()
    manifest = _make_manifest()
    via_dispatch = render_review_item(item, manifest)
    via_direct = render_concept_page(item, manifest)
    assert via_dispatch == via_direct


def test_render_review_item_dispatches_entity_byte_identical() -> None:
    """PR2b — render_review_item dispatches Entity to render_entity_page
    byte-identically (parallels source / concept dispatch contract)."""
    from shared.promotion_renderer import render_entity_page
    from shared.schemas.promotion_manifest import EntityReviewItem, PersonMetadata

    item = EntityReviewItem(
        item_id="ent-1",
        recommendation="include",
        action="create_entity",
        reason="recurring authority",
        evidence=_evidence(),
        risk=[],
        confidence=0.9,
        source_importance=0.9,
        reader_salience=0.5,
        entity_label="Andrew Huberman",
        metadata=PersonMetadata(affiliation="Stanford", role="Professor"),
    )
    manifest = _make_manifest()
    via_dispatch = render_review_item(item, manifest)
    via_direct = render_entity_page(item, manifest)
    assert via_dispatch == via_direct
    assert (
        hashlib.sha256(via_dispatch.encode("utf-8")).hexdigest()
        == hashlib.sha256(via_direct.encode("utf-8")).hexdigest()
    )


def test_render_review_item_default_arm_raises() -> None:
    """ADR-034 v2 §D3 — `case _: raise` enforces register hygiene."""
    manifest = _make_manifest()

    class _FakeReviewItem:
        item_id = "fake"

    with pytest.raises(NotImplementedError, match="_FakeReviewItem"):
        render_review_item(_FakeReviewItem(), manifest)  # type: ignore[arg-type]
