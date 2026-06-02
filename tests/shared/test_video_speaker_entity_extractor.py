"""Tests for shared.video_speaker_entity_extractor (ADR-035 PR4)."""

from __future__ import annotations

from shared.schemas.annotations import (
    AnnotationSetV3,
    AnnotationV3,
    HighlightV3,
    ReflectionV3,
)
from shared.schemas.promotion_manifest import PersonMetadata
from shared.schemas.reading_source import ReadingSource, SourceVariant
from shared.schemas.source_map import SourceMapBuildResult
from shared.video_speaker_entity_extractor import VideoSpeakerEntityExtractor


def _video_source(video_id: str = "abcDEF12345") -> ReadingSource:
    return ReadingSource(
        schema_version=2,
        source_id=f"youtube:{video_id}",
        annotation_key=f"youtube_{video_id}",
        kind="youtube_video",
        title="Test Episode",
        author="Test Channel",
        primary_lang="en",
        has_evidence_track=True,
        evidence_reason=None,
        variants=[
            SourceVariant(
                role="original",
                format="vtt",
                lang="en",
                path=f"Watchlist/youtube/{video_id}/transcript.vtt",
            )
        ],
        cast=["Host", "Guest"],
    )


def _empty_source_map(source_id: str) -> SourceMapBuildResult:
    return SourceMapBuildResult(
        schema_version=2,
        source_id=source_id,
        primary_lang="en",
        has_evidence_track=True,
        chapters_inspected=1,
        items=[],
        risks=[],
        error=None,
    )


class _FakeStore:
    def __init__(self, by_slug: dict[str, object]) -> None:
        self._by_slug = by_slug

    def load(self, slug: str):
        return self._by_slug.get(slug)


def _highlight(start: float, text: str, speaker: str = "") -> HighlightV3:
    return HighlightV3(cfi=f"t={start}", text_excerpt=text, text=text, speaker=speaker)


def _annotation(start: float, excerpt: str, note: str, speaker: str = "") -> AnnotationV3:
    return AnnotationV3(cfi=f"t={start}", text_excerpt=excerpt, note=note, speaker=speaker)


# ── Entry-gate ────────────────────────────────────────────────────────────


def test_extractor_returns_empty_for_non_video_kind():
    rs = ReadingSource(
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
    store = _FakeStore({})
    extractor = VideoSpeakerEntityExtractor(annotation_store=store)
    assert extractor.extract(rs, _empty_source_map(rs.source_id)) == []


def test_extractor_returns_empty_when_no_annotation_set():
    rs = _video_source()
    extractor = VideoSpeakerEntityExtractor(annotation_store=_FakeStore({}))
    assert extractor.extract(rs, _empty_source_map(rs.source_id)) == []


# ── Happy path ────────────────────────────────────────────────────────────


def test_extractor_emits_one_candidate_per_distinct_speaker():
    rs = _video_source()
    ann_set = AnnotationSetV3(
        slug=rs.annotation_key,
        base="watchlist",
        items=[
            _highlight(15.0, "First Host cue.", speaker="Host"),
            _annotation(120.0, "Guest excerpt.", "Guest note.", speaker="Guest"),
            _highlight(305.0, "Second Host cue.", speaker="Host"),
            _annotation(400.0, "Unlabelled cue.", "no speaker chip"),
        ],
    )
    store = _FakeStore({rs.annotation_key: ann_set})
    extractor = VideoSpeakerEntityExtractor(annotation_store=store)
    candidates = extractor.extract(rs, _empty_source_map(rs.source_id))

    assert [c.label for c in candidates] == ["Host", "Guest"]
    assert all(c.entity_type == "person" for c in candidates)
    assert all(isinstance(c.metadata, PersonMetadata) for c in candidates)
    assert all(c.evidence_language == "en" for c in candidates)
    # Host appears at t=15 + t=305 (recurrence signal preserved).
    host = candidates[0]
    assert host.source_refs == ["t=15", "t=305"]
    assert host.raw_quotes == ["First Host cue.", "Second Host cue."]
    # Guest appears once.
    guest = candidates[1]
    assert guest.source_refs == ["t=120"]
    assert guest.raw_quotes == ["Guest excerpt."]


def test_extractor_strips_speaker_whitespace_and_groups_case_sensitive():
    rs = _video_source()
    ann_set = AnnotationSetV3(
        slug=rs.annotation_key,
        items=[
            _highlight(10.0, "a", speaker="  Host  "),
            _highlight(20.0, "b", speaker="Host"),
            _highlight(30.0, "c", speaker="host"),
        ],
    )
    extractor = VideoSpeakerEntityExtractor(
        annotation_store=_FakeStore({rs.annotation_key: ann_set})
    )
    candidates = extractor.extract(rs, _empty_source_map(rs.source_id))

    # "  Host  " (stripped) merges with "Host"; "host" (lowercase) is a
    # distinct candidate — speakers come from a curated cast list so
    # case differences are meaningful (different roster entries).
    assert [c.label for c in candidates] == ["Host", "host"]
    assert candidates[0].source_refs == ["t=10", "t=20"]


def test_extractor_caps_raw_quotes_at_three_per_speaker():
    rs = _video_source()
    ann_set = AnnotationSetV3(
        slug=rs.annotation_key,
        items=[_highlight(float(i), f"cue {i}", speaker="Host") for i in range(1, 6)],
    )
    extractor = VideoSpeakerEntityExtractor(
        annotation_store=_FakeStore({rs.annotation_key: ann_set})
    )
    candidates = extractor.extract(rs, _empty_source_map(rs.source_id))
    host = candidates[0]
    # 5 annotations → all 5 timestamps preserved (recurrence signal),
    # but only first 3 cue excerpts kept in raw_quotes (caller cap).
    assert host.source_refs == ["t=1", "t=2", "t=3", "t=4", "t=5"]
    assert host.raw_quotes == ["cue 1", "cue 2", "cue 3"]


def test_extractor_skips_annotations_with_unparseable_cfi():
    rs = _video_source()
    ann_set = AnnotationSetV3(
        slug=rs.annotation_key,
        items=[
            _highlight(15.0, "valid", speaker="Host"),
            AnnotationV3(cfi=None, text_excerpt="orphan", note="legacy", speaker="Host"),
            AnnotationV3(cfi="epubcfi(/6/4)", text_excerpt="wrong-kind", note="x", speaker="Host"),
        ],
    )
    extractor = VideoSpeakerEntityExtractor(
        annotation_store=_FakeStore({rs.annotation_key: ann_set})
    )
    candidates = extractor.extract(rs, _empty_source_map(rs.source_id))
    # All three annotations contribute to raw_quotes (excerpt is the
    # body, not the locator), but only the t=15 locator survives in
    # source_refs.
    host = candidates[0]
    assert host.source_refs == ["t=15"]
    assert len(host.raw_quotes) == 3


def test_extractor_returns_empty_when_all_annotations_have_empty_speaker():
    rs = _video_source()
    ann_set = AnnotationSetV3(
        slug=rs.annotation_key,
        items=[
            _highlight(15.0, "cue 1"),
            _annotation(120.0, "cue 2 excerpt", "note"),
        ],
    )
    extractor = VideoSpeakerEntityExtractor(
        annotation_store=_FakeStore({rs.annotation_key: ann_set})
    )
    assert extractor.extract(rs, _empty_source_map(rs.source_id)) == []


def test_extractor_handles_reflection_items_silently():
    """ReflectionV3 has no ``speaker`` field today — extractor must
    treat the missing attribute as empty speaker and skip the item
    without crashing."""
    rs = _video_source()
    ann_set = AnnotationSetV3(
        slug=rs.annotation_key,
        items=[
            _highlight(15.0, "labelled", speaker="Host"),
            ReflectionV3(body="chapter-level reflection"),
        ],
    )
    extractor = VideoSpeakerEntityExtractor(
        annotation_store=_FakeStore({rs.annotation_key: ann_set})
    )
    candidates = extractor.extract(rs, _empty_source_map(rs.source_id))
    assert [c.label for c in candidates] == ["Host"]


def test_extractor_default_uses_module_singleton_store(monkeypatch):
    rs = _video_source()
    ann_set = AnnotationSetV3(
        slug=rs.annotation_key,
        items=[_highlight(7.0, "from singleton", speaker="Host")],
    )

    class _Singleton:
        def load(self, slug):
            return ann_set if slug == rs.annotation_key else None

    import shared.video_speaker_entity_extractor as mod

    monkeypatch.setattr(mod, "get_annotation_store", lambda: _Singleton())
    extractor = VideoSpeakerEntityExtractor()  # no store injected
    candidates = extractor.extract(rs, _empty_source_map(rs.source_id))
    assert candidates[0].label == "Host"


def test_extractor_emits_candidate_id_in_stable_order():
    rs = _video_source()
    ann_set = AnnotationSetV3(
        slug=rs.annotation_key,
        items=[
            _highlight(10.0, "a", speaker="Guest"),
            _highlight(20.0, "b", speaker="Host"),
            _highlight(30.0, "c", speaker="Producer"),
        ],
    )
    extractor = VideoSpeakerEntityExtractor(
        annotation_store=_FakeStore({rs.annotation_key: ann_set})
    )
    candidates = extractor.extract(rs, _empty_source_map(rs.source_id))
    # Order is first-appearance, not alphabetical.
    assert [c.label for c in candidates] == ["Guest", "Host", "Producer"]
    assert [c.candidate_id for c in candidates] == [
        "speaker-00",
        "speaker-01",
        "speaker-02",
    ]


def test_extractor_ignores_non_v3_annotation_set():
    """Pre-v3 sets cannot exist for a video source in practice but the
    extractor defends by treating them as empty (parallels
    video_source_map_builder._coerce_to_v3)."""
    rs = _video_source()

    class _LegacySet:
        slug = rs.annotation_key
        items: list = []

    extractor = VideoSpeakerEntityExtractor(
        annotation_store=_FakeStore({rs.annotation_key: _LegacySet()})
    )
    assert extractor.extract(rs, _empty_source_map(rs.source_id)) == []
