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


# ── ADR-035 §D6 / PR3a-iii — video source body template ──────────────────────


def _make_video_manifest(video_id: str = "abcDEF12345") -> PromotionManifest:
    return PromotionManifest(
        manifest_id="m-video",
        source_id=f"youtube:{video_id}",
        created_at="2026-05-29T00:00:00Z",
        status="needs_review",
        recommender=RecommenderMetadata(
            model_name="claude-opus-4-7",
            model_version="2026-04",
            recommended_at="2026-05-29T00:00:00Z",
        ),
        items=[],
        commit_batches=[],
    )


def _video_evidence() -> list[EvidenceAnchor]:
    return [
        EvidenceAnchor(
            kind="timestamp_range",
            source_path="Watchlist/youtube/abcDEF12345/transcript.vtt",
            locator="t=15",
            excerpt="We start with sleep.",
            confidence=1.0,
        ),
        EvidenceAnchor(
            kind="timestamp_range",
            source_path="Watchlist/youtube/abcDEF12345/transcript.vtt",
            locator="t=125.5-130.0",
            excerpt="REM matters.",
            confidence=1.0,
        ),
        EvidenceAnchor(
            kind="timestamp_range",
            source_path="Watchlist/youtube/abcDEF12345/transcript.vtt",
            locator="t=3725",
            excerpt="Caffeine half-life is 6 hours.",
            confidence=1.0,
        ),
    ]


def _make_video_source_item() -> SourcePageReviewItem:
    return SourcePageReviewItem(
        item_id="abcDEF12345::whole",
        recommendation="include",
        action="create",
        reason="Test Episode: 3 annotations",
        evidence=_video_evidence(),
        risk=[],
        confidence=1.0,
        source_importance=0.5,
        reader_salience=0.0,
        target_kb_path="KB/Wiki/Sources/abcDEF12345/whole.md",
        chapter_ref="whole",
    )


def test_render_source_page_video_emits_annotations_section() -> None:
    item = _make_video_source_item()
    manifest = _make_video_manifest()
    out = render_source_page(item, manifest)
    assert "## Annotations" in out
    assert "## Evidence" not in out


def test_render_source_page_video_formats_mm_ss_and_hh_mm_ss() -> None:
    item = _make_video_source_item()
    manifest = _make_video_manifest()
    out = render_source_page(item, manifest)
    assert "**[00:15]**" in out
    assert "**[02:05]**" in out
    assert "**[01:02:05]**" in out


def test_render_source_page_video_includes_watch_url_with_int_seconds() -> None:
    item = _make_video_source_item()
    manifest = _make_video_manifest()
    out = render_source_page(item, manifest)
    assert "[Watch on YouTube](https://youtu.be/abcDEF12345?t=15)" in out
    assert "[Watch on YouTube](https://youtu.be/abcDEF12345?t=125)" in out
    assert "[Watch on YouTube](https://youtu.be/abcDEF12345?t=3725)" in out


def test_render_source_page_video_preserves_caller_order() -> None:
    item = _make_video_source_item()
    manifest = _make_video_manifest()
    out = render_source_page(item, manifest)
    idx_first = out.index("**[00:15]**")
    idx_second = out.index("**[02:05]**")
    idx_third = out.index("**[01:02:05]**")
    assert idx_first < idx_second < idx_third


def test_render_source_page_video_byte_identical_across_runs() -> None:
    item = _make_video_source_item()
    manifest = _make_video_manifest()
    assert render_source_page(item, manifest) == render_source_page(item, manifest)


def test_render_source_page_video_with_zero_anchors_omits_annotations_section() -> None:
    item = SourcePageReviewItem(
        item_id="abcDEF12345::whole",
        recommendation="defer",
        action="create",
        reason="Test Episode: low signal",
        evidence=[],
        risk=[],
        confidence=0.5,
        source_importance=0.5,
        reader_salience=0.0,
        target_kb_path="KB/Wiki/Sources/abcDEF12345/whole.md",
        chapter_ref="whole",
    )
    manifest = _make_video_manifest()
    out = render_source_page(item, manifest)
    assert "## Annotations" not in out
    assert "## Evidence" not in out


def test_render_source_page_non_video_unchanged() -> None:
    """Ebook sources keep generic Reason/Evidence/Risks layout."""
    item = _make_source()
    manifest = _make_manifest()
    out = render_source_page(item, manifest)
    assert "## Annotations" not in out
    assert "## Evidence" in out
    assert "Watch on YouTube" not in out


def test_render_source_page_video_mixed_anchors_splits_sections() -> None:
    """If a non-timestamp anchor lands on a video item, it falls through
    to ## Evidence below ## Annotations (defensive — should not happen
    in practice today)."""
    item = SourcePageReviewItem(
        item_id="abcDEF12345::whole",
        recommendation="include",
        action="create",
        reason="Test Episode: mixed",
        evidence=[
            EvidenceAnchor(
                kind="timestamp_range",
                source_path="Watchlist/youtube/abcDEF12345/transcript.vtt",
                locator="t=15",
                excerpt="cue",
                confidence=1.0,
            ),
            EvidenceAnchor(
                kind="external_ref",
                source_path="https://example.com/study",
                locator="https://example.com/study",
                excerpt="referenced paper",
                confidence=0.8,
            ),
        ],
        risk=[],
        confidence=1.0,
        source_importance=0.5,
        reader_salience=0.0,
        target_kb_path="KB/Wiki/Sources/abcDEF12345/whole.md",
        chapter_ref="whole",
    )
    manifest = _make_video_manifest()
    out = render_source_page(item, manifest)
    assert "## Annotations" in out
    assert "## Evidence" in out
    assert out.index("## Annotations") < out.index("## Evidence")
