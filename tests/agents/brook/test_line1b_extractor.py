"""Tests for agents.brook.line1b_extractor.Line1bExtractor + Line1bStage1Result.

Covers ADR-027 §Decision 5, 6, 9:
- Closed-pool filter at retrieval layer (closed_pool_search): tested in
  tests/shared/test_closed_pool.py.
- Bilingual: English source + Chinese transcript → brief is Chinese and the
  original English quote is preserved in `quotes[].original_text`.
- Citation post-process: a narrative segment without a citation marker
  receives warning=⚠️ no_citation; `has_warnings` reflects this.
- Fail loudly: invalid JSON / missing required field raises ValueError.
- Schema round-trip: dump → validate is identity.
- Protocol conformance with Stage1Extractor.
- Adapter: Line1bStage1Result → legacy Stage1Result shape contains brief
  in every narrative slot and the typed payload under `line1b`.
"""

from __future__ import annotations

import json
from unittest.mock import patch

import pytest
from pydantic import ValidationError

from agents.brook.line1b_extractor import (
    Line1bExtractor,
    ResearchPackChunk,
    _post_process_citations,
)
from agents.brook.line1b_renderer_adapter import to_legacy_stage1
from agents.brook.repurpose_engine import EpisodeMetadata, Stage1Extractor, Stage1Result
from shared.schemas.line1b import Line1bStage1Result, NarrativeSegment, Quote

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


_FIXTURE_SRT = """\
1
00:00:00,000 --> 00:00:04,000
[SPEAKER_00] 今天邀請到 Dr Smith 來跟我們談睡眠科學

2
00:00:04,100 --> 00:00:10,000
[SPEAKER_01] Thanks for having me. Sleep is the bedrock of all recovery.

3
00:00:10,100 --> 00:00:20,000
[SPEAKER_00] 你在書裡提到 deep sleep 對 muscle recovery 影響很大，能說明嗎？

4
00:00:20,100 --> 00:00:35,000
[SPEAKER_01] In our 2022 study, deep sleep below four percent of total
sleep predicted poor recovery.
"""

_PACK_EN = [
    ResearchPackChunk(
        slug="KB/Wiki/Sources/why-we-sleep",
        title="Why We Sleep",
        heading="Chapter 5 — Recovery",
        text=(
            "Deep sleep is the primary substrate for physical recovery. "
            "Athletes with deep sleep below 4% show 60% slower recovery times."
        ),
        language="en",
    ),
]


def _make_valid_typed_payload() -> dict:
    """Build a Line1bStage1Result-shaped dict that validates."""
    return {
        "narrative_segments": [
            {
                "text": (
                    "Dr Smith 在訪談裡強調，深度睡眠是身體修復的主要場域。 "
                    "[source: KB/Wiki/Sources/why-we-sleep][translated_from: en]"
                ),
                "citations": ["KB/Wiki/Sources/why-we-sleep"],
                "warning": None,
            },
            {
                "text": (
                    "修修問到 deep sleep 與肌肉修復的關係，Dr Smith 引用 2022 年研究："
                    "深度睡眠低於 4% 與恢復力顯著相關。 [transcript@00:20]"
                ),
                "citations": ["transcript@00:20"],
                "warning": None,
            },
        ],
        "quotes": [
            {
                "text": "睡眠是所有修復的根基。",
                "timestamp": "00:00:04",
                "speaker": "Dr Smith",
                "original_language": "en",
                "original_text": "Sleep is the bedrock of all recovery.",
            },
        ],
        "titles": [
            "🧠不正常人類研究所 EP?｜Dr Smith：睡眠是所有修復的根基",
            "🧠不正常人類研究所 EP?｜深度睡眠 4% 法則 — Dr Smith 訪談",
            "🧠不正常人類研究所 EP?｜為什麼你需要睡眠 — 跟 Why We Sleep 作者談科學",
        ],
        "book_context": [
            {
                "slug": "KB/Wiki/Sources/why-we-sleep",
                "title": "Why We Sleep",
                "author": "Dr Smith",
                "note": "本集訪談前讀的主要書目，整段討論圍繞第 5 章 recovery 框架。",
            }
        ],
        "cross_refs": [
            {
                "transcript_anchor": "deep sleep 對 muscle recovery 影響很大",
                "transcript_timestamp": "00:00:10",
                "source_slug": "KB/Wiki/Sources/why-we-sleep",
                "relation": "Dr Smith 口頭引用了書中第 5 章 4% 門檻的數據",
            }
        ],
        "brief": (
            "本集訪談 Dr Smith，圍繞《Why We Sleep》第 5 章 deep sleep 與 recovery 的核心論點。"
            "Dr Smith 用一句「sleep is the bedrock of all recovery」開場，並引用 2022 年研究："
            "深度睡眠低於總睡眠 4% 時，恢復力下降 60%。"
            "修修在訪談中追問實作層面，把抽象科學接上聽眾日常。"
            "[source: KB/Wiki/Sources/why-we-sleep][transcript@00:20]"
        ),
    }


def _ctor(prompt: str = "STUB PROMPT — material_list={material_list}") -> Line1bExtractor:
    return Line1bExtractor(
        research_pack=_PACK_EN,
        style_profile_body="stub style profile",
        transcript_slug="KB/Wiki/Sources/transcript-2026-05-20-dr-smith",
        prompt_template=prompt,
    )


# ---------------------------------------------------------------------------
# Protocol conformance
# ---------------------------------------------------------------------------


def test_line1b_extractor_satisfies_stage1_extractor_protocol():
    assert isinstance(_ctor(), Stage1Extractor)


# ---------------------------------------------------------------------------
# Happy path + bilingual evidence preservation
# ---------------------------------------------------------------------------


def test_bilingual_english_source_chinese_brief_with_original_quote_preserved():
    """English research_pack + Chinese transcript → brief in Chinese AND
    original English quote present in `quotes[].original_text` (ADR-027 §9)."""
    extractor = _ctor()
    meta = EpisodeMetadata(slug="dr-smith-sleep", host="張修修", extra={"guest": "Dr Smith"})
    llm_response = json.dumps(_make_valid_typed_payload(), ensure_ascii=False)

    with patch("agents.brook.line1b_extractor.ask_multi", return_value=llm_response) as mock_llm:
        result = extractor.extract(_FIXTURE_SRT, meta)

    assert isinstance(result, Stage1Result)
    assert mock_llm.call_count == 1

    typed = Line1bExtractor.parse_result(result)
    # brief is in Chinese (heuristic: contains CJK chars)
    assert any("一" <= c <= "鿿" for c in typed.brief)
    # Original English quote preserved
    assert typed.quotes[0].original_language == "en"
    assert "bedrock of all recovery" in (typed.quotes[0].original_text or "")
    # Translated Chinese form is in `text`
    assert typed.quotes[0].text == "睡眠是所有修復的根基。"


def test_extract_passes_material_list_into_system_prompt():
    """The system prompt must substitute {material_list} with pack + transcript slugs."""
    extractor = _ctor()
    meta = EpisodeMetadata(slug="dr-smith-sleep", host="張修修", extra={"guest": "Dr Smith"})
    llm_response = json.dumps(_make_valid_typed_payload(), ensure_ascii=False)

    with patch("agents.brook.line1b_extractor.ask_multi", return_value=llm_response) as mock_llm:
        extractor.extract(_FIXTURE_SRT, meta)

    _, kwargs = mock_llm.call_args
    system_str = kwargs["system"]
    assert "KB/Wiki/Sources/why-we-sleep" in system_str
    assert "KB/Wiki/Sources/transcript-2026-05-20-dr-smith" in system_str
    assert "{material_list}" not in system_str  # template var must be substituted


# ---------------------------------------------------------------------------
# Citation post-process (Layer 3 reminder)
# ---------------------------------------------------------------------------


def test_no_citation_segment_gets_flagged_with_warning():
    """A narrative_segment without [source: ...] or [transcript@...] gets ⚠️."""
    payload = _make_valid_typed_payload()
    # Strip the citation marker from segment 0
    payload["narrative_segments"].append(
        {
            "text": "這段沒有 citation marker，純自由發揮。",
            "citations": [],
            "warning": None,
        }
    )
    extractor = _ctor()
    meta = EpisodeMetadata(slug="x", host="張修修", extra={"guest": "G"})
    llm_response = json.dumps(payload, ensure_ascii=False)

    with patch("agents.brook.line1b_extractor.ask_multi", return_value=llm_response):
        result = extractor.extract(_FIXTURE_SRT, meta)

    typed = Line1bExtractor.parse_result(result)
    assert typed.has_warnings is True
    last_seg = typed.narrative_segments[-1]
    assert last_seg.warning == "⚠️ no_citation"
    # Segments with citation markers are untouched
    assert typed.narrative_segments[0].warning is None
    assert typed.narrative_segments[1].warning is None


def test_post_process_citations_unit():
    """Direct unit test of the helper — segment with citation untouched, without flagged."""
    typed = Line1bStage1Result(
        narrative_segments=[
            NarrativeSegment(text="有 citation [source: a]", citations=["a"]),
            NarrativeSegment(text="沒 citation", citations=[]),
            NarrativeSegment(
                text="transcript citation [transcript@00:01]",
                citations=["transcript@00:01"],
            ),
        ],
        quotes=[Quote(text="x", timestamp="00:00:00", speaker="g")],
        titles=["a", "b", "c"],
        brief="brief text",
    )
    out = _post_process_citations(typed)
    assert out.narrative_segments[0].warning is None
    assert out.narrative_segments[1].warning == "⚠️ no_citation"
    assert out.narrative_segments[2].warning is None
    assert out.has_warnings is True


# ---------------------------------------------------------------------------
# Fail loudly: invalid JSON / missing field
# ---------------------------------------------------------------------------


def test_invalid_json_raises_after_retry():
    extractor = _ctor()
    meta = EpisodeMetadata(slug="x", host="張修修", extra={"guest": "G"})

    with patch("agents.brook.line1b_extractor.ask_multi", return_value="not json at all{"):
        with pytest.raises(ValueError, match="Line 1b Stage 1 extraction failed"):
            extractor.extract(_FIXTURE_SRT, meta)


def test_missing_required_field_raises_after_retry():
    extractor = _ctor()
    meta = EpisodeMetadata(slug="x", host="張修修", extra={"guest": "G"})

    payload = _make_valid_typed_payload()
    del payload["brief"]  # required field
    bad = json.dumps(payload, ensure_ascii=False)

    with patch("agents.brook.line1b_extractor.ask_multi", return_value=bad):
        with pytest.raises(ValueError, match="Line 1b Stage 1 extraction failed"):
            extractor.extract(_FIXTURE_SRT, meta)


def test_retry_succeeds_when_second_attempt_returns_valid_json():
    """First attempt invalid → corrective retry → success on attempt 2."""
    extractor = _ctor()
    meta = EpisodeMetadata(slug="x", host="張修修", extra={"guest": "G"})
    good = json.dumps(_make_valid_typed_payload(), ensure_ascii=False)

    responses = iter(["not json", good])

    def fake_ask(*_a, **_kw):
        return next(responses)

    with patch("agents.brook.line1b_extractor.ask_multi", side_effect=fake_ask) as mock_llm:
        result = extractor.extract(_FIXTURE_SRT, meta)
    assert mock_llm.call_count == 2
    typed = Line1bExtractor.parse_result(result)
    assert typed.brief


# ---------------------------------------------------------------------------
# Schema round-trip
# ---------------------------------------------------------------------------


def test_line1b_stage1_result_round_trip_is_identity():
    typed = Line1bStage1Result.model_validate(_make_valid_typed_payload())
    dumped = typed.model_dump_json()
    re_loaded = Line1bStage1Result.model_validate_json(dumped)
    assert re_loaded == typed


def test_line1b_stage1_result_rejects_extra_top_level_keys():
    """extra='forbid' guards against silent contract drift."""
    payload = _make_valid_typed_payload()
    payload["bogus_field"] = "evil"
    with pytest.raises(ValidationError):
        Line1bStage1Result.model_validate(payload)


def test_titles_min_three_enforced():
    payload = _make_valid_typed_payload()
    payload["titles"] = ["only one"]
    with pytest.raises(ValidationError):
        Line1bStage1Result.model_validate(payload)


# ---------------------------------------------------------------------------
# Adapter: legacy Stage1Result shape for existing renderers
# ---------------------------------------------------------------------------


def test_adapter_packs_brief_into_legacy_narrative_fields():
    typed = Line1bStage1Result.model_validate(_make_valid_typed_payload())
    legacy = to_legacy_stage1(typed)

    assert isinstance(legacy, Stage1Result)
    d = legacy.data
    # All 6 narrative slots carry the brief (Stage-2 LLM splits it)
    for key in (
        "identity_sketch",
        "origin",
        "turning_point",
        "rebirth",
        "present_action",
        "ending_direction",
    ):
        assert d[key] == typed.brief
    # Title candidates passed through
    assert d["title_candidates"] == list(typed.titles)
    # Episode type defaults to narrative_journey (IG card-count routing depends on this)
    assert d["episode_type"] == "narrative_journey"
    # At least 3 hooks (Stage1Schema min_length=3 must hold for legacy renderer)
    assert len(d["hooks"]) >= 3
    # meta_description in the 80-200 char range (legacy schema bound)
    assert 80 <= len(d["meta_description"]) <= 200
    # Original English text preserved in legacy quotes (verbatim wins)
    assert d["quotes"][0]["text"] == "Sleep is the bedrock of all recovery."
    # Typed payload available under `line1b` for future explicit branches
    assert d["line1b"]["brief"] == typed.brief


def test_adapter_legacy_data_carries_keys_required_by_blog_renderer():
    """BlogRenderer reads `title_candidates`, `meta_description` from data —
    adapter must provide both non-empty so the renderer's _require_stage1_field
    guard passes."""
    typed = Line1bStage1Result.model_validate(_make_valid_typed_payload())
    legacy = to_legacy_stage1(typed)
    d = legacy.data
    # Keys BlogRenderer._require_stage1_field asks for:
    assert d.get("title_candidates")
    assert d.get("meta_description")
    # IGRenderer episode_type routing key:
    assert d.get("episode_type") in {"narrative_journey", "myth_busting", "framework", "listicle"}


def test_adapter_override_episode_type():
    typed = Line1bStage1Result.model_validate(_make_valid_typed_payload())
    legacy = to_legacy_stage1(typed, episode_type="framework")
    assert legacy.data["episode_type"] == "framework"
