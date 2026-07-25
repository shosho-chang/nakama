"""Tests for agents.robin.promotion.video_source_map_builder (ADR-035 §D6 / PR3a-ii)."""

from __future__ import annotations

import pytest

from agents.robin.promotion.video_source_map_builder import (
    _format_locator,
    _parse_t_locator_pair,
    build_video_source_map,
)
from shared.schemas.annotations import (
    AnnotationSetV3,
    AnnotationV3,
    HighlightV3,
)
from shared.schemas.reading_source import ReadingSource, SourceVariant


def _video_source(
    *,
    video_id: str = "abcDEF12345",
    primary_lang: str = "en",
    has_evidence_track: bool = True,
) -> ReadingSource:
    variants = (
        [
            SourceVariant(
                role="original",
                format="vtt",
                lang=primary_lang,
                path=f"Watchlist/youtube/{video_id}/transcript.vtt",
            )
        ]
        if has_evidence_track
        else [
            SourceVariant(
                role="display",
                format="vtt",
                lang="bilingual",
                path=f"Watchlist/youtube/{video_id}/transcript-bilingual.vtt",
            )
        ]
    )
    return ReadingSource(
        schema_version=2,
        source_id=f"youtube:{video_id}",
        annotation_key=f"youtube_{video_id}",
        kind="youtube_video",
        title="Test Episode",
        author="Test Channel",
        primary_lang=primary_lang,
        has_evidence_track=has_evidence_track,
        evidence_reason=None if has_evidence_track else "no_original_uploaded",
        variants=variants,
        metadata={"video_id": video_id},
        cast=["Host", "Guest"],
    )


def _highlight(cue_start: float, text: str, *, speaker: str = "") -> HighlightV3:
    return HighlightV3(
        cfi=f"t={cue_start}",
        text_excerpt=text,
        text=text,
        speaker=speaker,
    )


def _annotation(cue_start: float, excerpt: str, note: str, *, speaker: str = "") -> AnnotationV3:
    return AnnotationV3(
        cfi=f"t={cue_start}",
        text_excerpt=excerpt,
        note=note,
        speaker=speaker,
    )


def _ann_set(items, *, slug: str = "youtube_abcDEF12345") -> AnnotationSetV3:
    return AnnotationSetV3(slug=slug, base="watchlist", items=items)


# ── Entry-gate contracts ──────────────────────────────────────────────────


def test_build_video_source_map_rejects_non_video_kind():
    rs = ReadingSource(
        schema_version=1,
        source_id="ebook:abc",
        annotation_key="abc",
        kind="ebook",
        title="X",
        primary_lang="en",
        has_evidence_track=True,
        variants=[
            SourceVariant(
                role="original", format="epub", lang="en", path="data/books/abc/original.epub"
            )
        ],
    )
    with pytest.raises(ValueError, match="kind='youtube_video'"):
        build_video_source_map(rs, _ann_set([]))


def test_build_video_source_map_rejects_has_evidence_track_false():
    rs = _video_source(has_evidence_track=False)
    with pytest.raises(ValueError, match="has_evidence_track=True"):
        build_video_source_map(rs, _ann_set([]))


# ── Happy paths ──────────────────────────────────────────────────────────


def test_build_video_source_map_emits_one_item_per_annotation_as_evidence():
    rs = _video_source()
    ann_set = _ann_set(
        [
            _highlight(15.0, "We start with sleep."),
            _annotation(120.5, "REM matters.", "Compare to Walker book.", speaker="Host"),
            _highlight(305.75, "Caffeine half-life is 6 hours.", speaker="Guest"),
        ]
    )
    result = build_video_source_map(rs, ann_set)
    assert result.error is None
    assert result.schema_version == 2
    assert len(result.items) == 1
    item = result.items[0]
    assert item.chapter_ref == "whole"
    assert item.recommendation == "include"
    assert item.confidence == pytest.approx(1.0)
    assert item.target_kb_path == "KB/Wiki/Sources/abcDEF12345/whole.md"
    assert [a.locator for a in item.evidence] == ["t=15", "t=120.5", "t=305.75"]
    assert all(a.kind == "timestamp_range" for a in item.evidence)
    assert all(a.source_path == rs.variants[0].path for a in item.evidence)
    assert item.evidence[1].excerpt == "REM matters."
    assert all(a.confidence == pytest.approx(1.0) for a in item.evidence)


def test_build_video_source_map_orders_evidence_by_cue_start():
    rs = _video_source()
    ann_set = _ann_set(
        [
            _highlight(305.0, "third"),
            _highlight(15.0, "first"),
            _highlight(120.0, "second"),
        ]
    )
    result = build_video_source_map(rs, ann_set)
    assert [a.locator for a in result.items[0].evidence] == ["t=15", "t=120", "t=305"]


def test_build_video_source_map_preserves_locator_end_when_present():
    rs = _video_source()
    item = AnnotationV3(
        cfi="t=15.0-19.25",
        text_excerpt="Cue text.",
        note="A note.",
    )
    result = build_video_source_map(rs, _ann_set([item]))
    assert result.items[0].evidence[0].locator == "t=15-19.25"


# ── Empty / drift edge cases ─────────────────────────────────────────────


def test_build_video_source_map_no_annotations_defers_with_low_signal():
    rs = _video_source()
    result = build_video_source_map(rs, _ann_set([]))
    assert result.error is None
    assert len(result.items) == 1
    item = result.items[0]
    assert item.recommendation == "defer"
    assert item.evidence == []
    assert any(r.code == "low_signal_count" for r in item.risk)
    assert item.confidence == pytest.approx(0.5)


def test_build_video_source_map_drops_annotations_without_timestamp_locators():
    rs = _video_source()
    items = [
        _highlight(15.0, "valid"),
        AnnotationV3(cfi=None, text_excerpt="orphan", note="legacy"),
        AnnotationV3(cfi="epubcfi(/6/4)", text_excerpt="wrong-kind", note="x"),
    ]
    result = build_video_source_map(rs, _ann_set(items))
    assert [a.locator for a in result.items[0].evidence] == ["t=15"]


def test_build_video_source_map_dedupes_within_50ms_bucket():
    rs = _video_source()
    items = [
        _highlight(15.00, "first save"),
        _annotation(15.02, "first save", "upgraded note"),
        _highlight(15.10, "drifted past tolerance"),
    ]
    result = build_video_source_map(rs, _ann_set(items))
    locators = [a.locator for a in result.items[0].evidence]
    # 15.00 and 15.02 share the same 50ms bucket; 15.10 lands in a
    # different bucket so it survives.
    assert locators == ["t=15", "t=15.1"]


def test_build_video_source_map_truncates_long_excerpts():
    rs = _video_source()
    long_text = "x" * 1000
    result = build_video_source_map(
        rs,
        _ann_set([_highlight(10.0, long_text)]),
        max_excerpt_chars=200,
    )
    excerpt = result.items[0].evidence[0].excerpt
    assert len(excerpt) == 200
    assert excerpt.endswith("…")


# ── No-store path ────────────────────────────────────────────────────────


def test_build_video_source_map_no_annotation_set_loads_from_store(monkeypatch):
    rs = _video_source()
    captured: dict[str, str] = {}

    class _FakeStore:
        def load(self, slug: str):
            captured["slug"] = slug
            return _ann_set([_highlight(7.0, "from store")], slug=slug)

    import agents.robin.promotion.video_source_map_builder as mod

    monkeypatch.setattr(mod, "get_annotation_store", lambda: _FakeStore())
    result = build_video_source_map(rs)
    assert captured["slug"] == rs.annotation_key
    assert [a.locator for a in result.items[0].evidence] == ["t=7"]


def test_build_video_source_map_no_annotation_set_and_store_empty_defers(monkeypatch):
    rs = _video_source()

    class _EmptyStore:
        def load(self, slug: str):
            return None

    import agents.robin.promotion.video_source_map_builder as mod

    monkeypatch.setattr(mod, "get_annotation_store", lambda: _EmptyStore())
    result = build_video_source_map(rs)
    assert result.items[0].recommendation == "defer"
    assert any(r.code == "low_signal_count" for r in result.items[0].risk)


# ── Unit tests on helpers ────────────────────────────────────────────────


def test_parse_t_locator_pair_handles_start_only_and_range():
    assert _parse_t_locator_pair("t=12.5") == (12.5, None)
    assert _parse_t_locator_pair("t=15.0-19.25") == (15.0, 19.25)
    assert _parse_t_locator_pair(None) is None
    assert _parse_t_locator_pair("epubcfi(/6/4)") is None


def test_format_locator_collapses_integer_floats():
    assert _format_locator(15.0, None) == "t=15"
    assert _format_locator(15.5, None) == "t=15.5"
    assert _format_locator(15.0, 19.0) == "t=15-19"
    assert _format_locator(15.0, 15.0) == "t=15"  # end <= start collapses


# ── SourceMapBuilder dispatch still refuses youtube_video ────────────────


def test_source_map_builder_still_refuses_youtube_video():
    from agents.robin.promotion.source_map_builder import SourceMapBuilder
    from shared.schemas.source_map import ClaimExtractionResult

    class _Loader:
        def __call__(self, path: str) -> bytes:
            return b""

    class _Extractor:
        def extract(self, chapter_text, chapter_title, primary_lang):
            return ClaimExtractionResult(extraction_confidence=0.0)

    builder = SourceMapBuilder(blob_loader=_Loader())
    rs = _video_source()
    result = builder.build(rs, _Extractor())
    assert result.items == []
    assert result.error is not None
    assert "video_source_map_builder" in result.error
