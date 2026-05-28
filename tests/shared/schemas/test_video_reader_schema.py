"""Tests for ADR-035 video reader schema bumps (PR1a).

Covers:
- ``EvidenceAnchorKind`` accepts ``timestamp_range``
- PromotionManifest V13 — timestamp_range anchor requires schema_version >= 2
- ``ReadingSource.SourceKind`` accepts ``youtube_video``
- ``VariantFormat`` accepts ``vtt``
- ReadingSource V14 — youtube_video kind requires schema_version >= 2
- Backward compat — existing v=1 ReadingSource / PromotionManifest unaffected
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from shared.schemas.promotion_manifest import (
    EvidenceAnchor,
    HumanDecision,
    PromotionManifest,
    RecommenderMetadata,
    SourcePageReviewItem,
)
from shared.schemas.reading_source import ReadingSource, SourceVariant

# ── EvidenceAnchor / V13 ──────────────────────────────────────────────────


def _timestamp_anchor() -> EvidenceAnchor:
    return EvidenceAnchor(
        kind="timestamp_range",
        source_path="Watchlist/youtube/dQw4w9WgXcQ/transcript.vtt",
        locator="t=123.4-145.7",
        excerpt="Huberman talks about dopamine baseline regulation",
        confidence=0.9,
    )


def _v1_manifest_kwargs() -> dict:
    return dict(
        manifest_id="m-1",
        source_id="youtube:dQw4w9WgXcQ",
        created_at="2026-05-27T00:00:00Z",
        status="needs_review",
        recommender=RecommenderMetadata(
            model_name="claude-opus-4-7",
            model_version="2026-04",
            recommended_at="2026-05-27T00:00:00Z",
        ),
        items=[],
        commit_batches=[],
    )


def _src_item_with_timestamp_anchor() -> SourcePageReviewItem:
    return SourcePageReviewItem(
        item_id="src-yt-1",
        recommendation="include",
        action="create",
        reason="cue range carrying core claim",
        evidence=[_timestamp_anchor()],
        risk=[],
        confidence=0.9,
        source_importance=0.8,
        reader_salience=0.7,
    )


def test_evidence_anchor_accepts_timestamp_range() -> None:
    """Schema bump: timestamp_range is a valid EvidenceAnchorKind."""
    anchor = _timestamp_anchor()
    assert anchor.kind == "timestamp_range"
    assert anchor.locator.startswith("t=")


def test_evidence_anchor_point_locator_ok() -> None:
    """Single-point locator (no end) is valid per ADR-035 §D5."""
    anchor = EvidenceAnchor(
        kind="timestamp_range",
        source_path="Watchlist/youtube/abc/transcript.vtt",
        locator="t=42.5",
        excerpt="quote",
        confidence=0.8,
    )
    assert anchor.locator == "t=42.5"


def test_v13_timestamp_anchor_rejected_in_schema_v1() -> None:
    """V13: timestamp_range anchor in v=1 manifest must raise."""
    with pytest.raises(ValidationError, match="schema_version >= 2"):
        PromotionManifest(
            schema_version=1,
            **{**_v1_manifest_kwargs(), "items": [_src_item_with_timestamp_anchor()]},
        )


def test_v13_timestamp_anchor_accepted_in_schema_v2() -> None:
    """V13: timestamp_range anchor accepted when schema_version=2."""
    manifest = PromotionManifest(
        schema_version=2,
        **{**_v1_manifest_kwargs(), "items": [_src_item_with_timestamp_anchor()]},
    )
    assert manifest.schema_version == 2
    assert manifest.items[0].evidence[0].kind == "timestamp_range"


def test_v13_v1_manifest_without_timestamp_still_valid() -> None:
    """Backward compat — existing v=1 manifests without timestamp anchor unaffected."""
    chapter_item = SourcePageReviewItem(
        item_id="src-1",
        recommendation="include",
        action="create",
        reason="r",
        evidence=[
            EvidenceAnchor(
                kind="chapter_quote",
                source_path="data/books/x/original.epub",
                locator="epubcfi(/6/4!/4/2)",
                excerpt="q",
                confidence=0.8,
            )
        ],
        risk=[],
        confidence=0.8,
        source_importance=0.8,
        reader_salience=0.5,
        human_decision=HumanDecision(
            decision="approve",
            decided_at="2026-05-27T00:00:00Z",
            decided_by="t",
        ),
    )
    manifest = PromotionManifest(
        schema_version=1,
        **{**_v1_manifest_kwargs(), "items": [chapter_item]},
    )
    assert manifest.schema_version == 1


# ── ReadingSource / V14 ────────────────────────────────────────────────────


def _yt_variant() -> SourceVariant:
    return SourceVariant(
        role="original",
        format="vtt",
        lang="en",
        path="Watchlist/youtube/dQw4w9WgXcQ/transcript.vtt",
    )


def _yt_source_kwargs() -> dict:
    return dict(
        source_id="youtube:dQw4w9WgXcQ",
        annotation_key="youtube_dQw4w9WgXcQ",
        kind="youtube_video",
        title="Andrew Huberman on Dopamine",
        primary_lang="en",
        has_evidence_track=True,
        variants=[_yt_variant()],
        metadata={
            "video_id": "dQw4w9WgXcQ",
            "channel": "Huberman Lab",
            "duration_s": "5400",
            "url": "https://youtube.com/watch?v=dQw4w9WgXcQ",
            "cast": '["host", "Andrew Huberman"]',
        },
    )


def test_source_kind_accepts_youtube_video() -> None:
    """SourceKind enum extended with youtube_video (schema_version=2)."""
    src = ReadingSource(schema_version=2, **_yt_source_kwargs())
    assert src.kind == "youtube_video"
    assert src.schema_version == 2


def test_variant_format_accepts_vtt() -> None:
    variant = SourceVariant(
        role="original",
        format="vtt",
        lang="en",
        path="Watchlist/youtube/x/transcript.vtt",
    )
    assert variant.format == "vtt"


def test_v14_youtube_video_rejected_in_schema_v1() -> None:
    """V14: youtube_video kind in v=1 ReadingSource must raise."""
    with pytest.raises(ValidationError, match="schema_version >= 2"):
        ReadingSource(schema_version=1, **_yt_source_kwargs())


def test_v14_existing_ebook_source_still_v1() -> None:
    """Backward compat — ebook ReadingSource still loads as v=1."""
    src = ReadingSource(
        source_id="ebook:abc123",
        annotation_key="abc123",
        kind="ebook",
        title="Why We Sleep",
        primary_lang="en",
        has_evidence_track=True,
        variants=[
            SourceVariant(
                role="original",
                format="epub",
                lang="en",
                path="data/books/abc123/original.epub",
            )
        ],
        metadata={"isbn": "9781501144318"},
    )
    assert src.schema_version == 1
    assert src.kind == "ebook"


def test_v14_inbox_document_still_v1() -> None:
    src = ReadingSource(
        source_id="inbox:Inbox/web/foo.md",
        annotation_key="foo",
        kind="inbox_document",
        title="Foo",
        primary_lang="en",
        has_evidence_track=True,
        variants=[
            SourceVariant(
                role="original",
                format="markdown",
                lang="en",
                path="Inbox/web/foo.md",
            )
        ],
    )
    assert src.schema_version == 1
    assert src.kind == "inbox_document"


# ── SourceMapBuildResult B7 (ADR-035 §D5) ─────────────────────────────────


def test_source_map_build_result_b7_rejects_timestamp_in_v1() -> None:
    """SourceMapBuildResult B7 — timestamp_range anchor on item requires v >= 2."""
    from shared.schemas.source_map import SourceMapBuildResult

    item = _src_item_with_timestamp_anchor()
    with pytest.raises(ValidationError, match="schema_version >= 2"):
        SourceMapBuildResult(
            schema_version=1,
            source_id="youtube:dQw4w9WgXcQ",
            primary_lang="en",
            has_evidence_track=True,
            chapters_inspected=1,
            items=[item],
        )


def test_source_map_build_result_b7_accepts_timestamp_in_v2() -> None:
    from shared.schemas.source_map import SourceMapBuildResult

    result = SourceMapBuildResult(
        schema_version=2,
        source_id="youtube:dQw4w9WgXcQ",
        primary_lang="en",
        has_evidence_track=True,
        chapters_inspected=1,
        items=[_src_item_with_timestamp_anchor()],
    )
    assert result.schema_version == 2


def test_source_map_build_result_existing_v1_unchanged() -> None:
    """Backward compat — existing chapter_quote-only builders still v=1."""
    from shared.schemas.source_map import SourceMapBuildResult

    chapter_item = SourcePageReviewItem(
        item_id="src-1",
        recommendation="include",
        action="create",
        reason="r",
        evidence=[
            EvidenceAnchor(
                kind="chapter_quote",
                source_path="data/books/x/original.epub",
                locator="epubcfi(/6/4!/4/2)",
                excerpt="q",
                confidence=0.8,
            )
        ],
        risk=[],
        confidence=0.8,
        source_importance=0.8,
        reader_salience=0.5,
    )
    result = SourceMapBuildResult(
        source_id="ebook:abc",
        primary_lang="en",
        has_evidence_track=True,
        chapters_inspected=1,
        items=[chapter_item],
    )
    assert result.schema_version == 1


# ── PromotionReviewService schema_version derivation (#1 fix) ──────────────


def test_compose_manifest_derives_v2_for_timestamp_only_items() -> None:
    """Regression — schema_version factory must consider timestamp_range
    anchors, not just entity items, or V13 will reject every video manifest.
    """
    from shared.promotion_review_service import PromotionReviewService

    # Construct a minimal service for the private helper; deps unused by _compose_manifest.
    svc = PromotionReviewService.__new__(PromotionReviewService)
    svc._recommender_model_name = "claude-opus-4-7"
    svc._recommender_model_version = "2026-04"

    manifest = svc._compose_manifest(
        source_id="youtube:dQw4w9WgXcQ",
        source_page_items=[_src_item_with_timestamp_anchor()],
        concept_items=[],
        entity_items=[],
    )
    assert manifest.schema_version == 2, (
        "manifest with timestamp_range anchor must auto-bump to v=2 "
        "(otherwise V13 raises ValidationError)"
    )


def test_compose_manifest_stays_v1_for_legacy_chapter_items() -> None:
    """Backward compat — concept/source-only manifests with chapter_quote
    anchors still ship as v=1.
    """
    from shared.promotion_review_service import PromotionReviewService

    svc = PromotionReviewService.__new__(PromotionReviewService)
    svc._recommender_model_name = "claude-opus-4-7"
    svc._recommender_model_version = "2026-04"

    chapter_item = SourcePageReviewItem(
        item_id="src-1",
        recommendation="include",
        action="create",
        reason="r",
        evidence=[
            EvidenceAnchor(
                kind="chapter_quote",
                source_path="data/books/x/original.epub",
                locator="epubcfi(/6/4!/4/2)",
                excerpt="q",
                confidence=0.8,
            )
        ],
        risk=[],
        confidence=0.8,
        source_importance=0.8,
        reader_salience=0.5,
    )
    manifest = svc._compose_manifest(
        source_id="ebook:abc",
        source_page_items=[chapter_item],
        concept_items=[],
        entity_items=[],
    )
    assert manifest.schema_version == 1
