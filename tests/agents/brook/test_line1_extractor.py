"""Tests for agents.brook.line1_extractor.Line1Extractor.

Covers:
- Protocol conformance: Line1Extractor satisfies Stage1Extractor at runtime
- LLM mock test: valid JSON → Stage1Result with correct schema
- Retry on bad JSON: first call returns invalid JSON, second returns valid
- Retry on schema mismatch: first call returns JSON missing required fields
- Guest=None logs warning and still calls LLM
- Speaker attribution: quotes contain real names, not [SPEAKER_XX]
- Golden fixture round-trip: fixture SRT → schema passes, 8 segments, ≥5 quotes, ≥3 titles
"""

from __future__ import annotations

import json
import logging
from unittest.mock import patch

import pytest

from agents.brook.line1_extractor import Line1Extractor, _extract_json
from agents.brook.repurpose_engine import EpisodeMetadata, Stage1Extractor, Stage1Result

# ---------------------------------------------------------------------------
# Shared fixtures
# ---------------------------------------------------------------------------

_FIXTURE_SRT = """\
1
00:00:00,000 --> 00:00:03,500
[SPEAKER_00] 今天很高興邀請到朱為民醫師來跟我們聊聊善終這個話題

2
00:00:03,600 --> 00:00:08,200
[SPEAKER_01] 謝謝修修的邀請，我覺得這個話題很重要，很多人都忽視了

3
00:00:08,300 --> 00:00:14,500
[SPEAKER_00] 醫師，你是怎麼走上安寧緩和醫學這條路的？

4
00:00:14,600 --> 00:00:25,800
[SPEAKER_01] 其實我父親在我念醫學院的時候過世了，那個經歷讓我開始思考死亡這件事

5
00:00:25,900 --> 00:00:35,000
[SPEAKER_01] 我發現很多人在面對死亡的時候都沒有準備，所以我決定要專注在這個領域

6
00:00:35,100 --> 00:00:42,500
[SPEAKER_00] 那麼什麼是善終？你怎麼定義它？

7
00:00:42,600 --> 00:00:55,800
[SPEAKER_01] 善終就是在清醒有尊嚴的狀態下離開，還能夠跟最重要的人好好道別

8
00:00:55,900 --> 00:01:08,200
[SPEAKER_01] 你終究會發現，每個人都連結在一起，死亡不是終點，而是一種轉化

9
00:01:08,300 --> 00:01:15,700
[SPEAKER_00] 你提到道謝、道歉、道愛、道別，能說說這四道人生嗎？

10
00:01:15,800 --> 00:01:28,900
[SPEAKER_01] 這四道是我從研究中整理出來的，在生命最後的時候，人最需要做的就是這四件事

11
00:01:29,000 --> 00:01:45,200
[SPEAKER_01] 很多人覺得死亡是禁忌，但我認為，如果你沒有好好思考過死亡，你就沒有辦法真正地活著

12
00:01:45,300 --> 00:02:00,000
[SPEAKER_00] 醫師，謝謝你今天的分享，讓我們對善終有了全新的認識
"""

_VALID_STAGE1 = {
    "hooks": [
        "你有想過，在台灣最有名的安寧醫師，是因為父親去世才走上這條路的嗎？",
        "『死亡』是一件華人社會避之唯恐不及的話題——但朱為民醫師偏偏選擇正面迎擊。",
        "他每天陪伴病人走完最後一程，但他真正想告訴你的，是怎麼活著。",
    ],
    "identity_sketch": (
        "朱為民醫師是台中榮總家庭醫學科主治醫師，也是台灣安寧緩和醫學的推動者。"
        "他不僅是 TEDxTaipei 講者，更是暢銷書作家。"
    ),
    "origin": (
        "朱為民在醫學院求學期間，父親突然過世。那段與死亡猝不及防的相遇，讓他開始深刻思考生死議題。"
    ),
    "turning_point": (
        "目睹身邊許多人在面對死亡時毫無準備，朱為民決定投身安寧緩和醫學，協助人們好好道別。"
    ),
    "rebirth": (
        "他將多年臨床觀察整理成「四道人生」——道謝、道歉、道愛、道別，"
        "成為幫助病人與家屬走過生命終點的具體工具。"
    ),
    "present_action": (
        "朱為民持續透過演講、書寫和診間工作，推廣善終觀念，讓更多人在清醒有尊嚴的狀態下告別。"
    ),
    "ending_direction": (
        "留白於「你終究會發現，每個人都連結在一起」——"
        "邀請讀者帶著這份好奇，進入 podcast 聽完整故事。"
    ),
    "quotes": [
        {
            "text": "善終就是在清醒有尊嚴的狀態下離開，還能夠跟最重要的人好好道別",
            "timestamp": "00:42:36",
            "speaker": "朱為民",
        },
        {
            "text": "你終究會發現，每個人都連結在一起，死亡不是終點，而是一種轉化",
            "timestamp": "00:55:54",
            "speaker": "朱為民",
        },
        {
            "text": "如果你沒有好好思考過死亡，你就沒有辦法真正地活著",
            "timestamp": "01:29:00",
            "speaker": "朱為民",
        },
        {
            "text": "在生命最後的時候，人最需要做的就是這四件事",
            "timestamp": "01:15:48",
            "speaker": "朱為民",
        },
        {
            "text": "我發現很多人在面對死亡的時候都沒有準備，所以我決定要專注在這個領域",
            "timestamp": "00:25:54",
            "speaker": "朱為民",
        },
    ],
    "title_candidates": [
        "🧠不正常人類研究所 EP?｜朱為民：安寧醫師教你如何好好告別，才能真正地活著",
        "🧠不正常人類研究所 EP?｜朱為民醫師：四道人生道謝道歉道愛道別，善終是一種選擇",
        "🧠不正常人類研究所 EP?｜朱為民：每個人都連結在一起，死亡不是終點",
    ],
    "meta_description": (
        "台中榮總安寧醫師朱為民，因父親驟逝踏入生死醫學，用「四道人生」陪伴無數人好好道別。"
        "本集不正常人類研究所，修修與朱醫師聊善終、死亡禁忌與真正活著的意義。立即收聽！"
    ),
    "episode_type": "narrative_journey",
}


def _fake_ask_multi(valid_json: dict):
    """Return a patch target that returns the given JSON as a string."""
    return json.dumps(valid_json, ensure_ascii=False)


# ---------------------------------------------------------------------------
# Protocol conformance
# ---------------------------------------------------------------------------


def test_line1_extractor_satisfies_stage1_extractor_protocol():
    assert isinstance(Line1Extractor(people_md="stub"), Stage1Extractor)


# ---------------------------------------------------------------------------
# _extract_json helper
# ---------------------------------------------------------------------------


def test_extract_json_strips_json_fence():
    raw = '```json\n{"key": "value"}\n```'
    assert _extract_json(raw) == '{"key": "value"}'


def test_extract_json_strips_plain_fence():
    raw = '```\n{"key": "value"}\n```'
    assert _extract_json(raw) == '{"key": "value"}'


def test_extract_json_passthrough_when_no_fence():
    raw = '{"key": "value"}'
    assert _extract_json(raw) == '{"key": "value"}'


def test_extract_json_prefers_last_fenced_block():
    """When LLM emits multiple fences, last is the real answer (not first)."""
    raw = (
        "Here's an example schema:\n"
        '```json\n{"example": true}\n```\n'
        "And the actual answer:\n"
        '```json\n{"actual": "answer"}\n```\n'
    )
    assert _extract_json(raw) == '{"actual": "answer"}'


# ---------------------------------------------------------------------------
# LLM mock test — basic happy path
# ---------------------------------------------------------------------------


def test_extract_returns_stage1_result_on_valid_json():
    extractor = Line1Extractor(people_md="stub profile")
    meta = EpisodeMetadata(slug="dr-chu", host="張修修", extra={"guest": "朱為民"})
    llm_response = json.dumps(_VALID_STAGE1, ensure_ascii=False)

    with patch("agents.brook.line1_extractor.ask_multi", return_value=llm_response) as mock_llm:
        result = extractor.extract(_FIXTURE_SRT, meta)

    assert isinstance(result, Stage1Result)
    assert mock_llm.call_count == 1

    data = result.data
    assert data["episode_type"] == "narrative_journey"
    assert len(data["hooks"]) >= 3
    assert len(data["quotes"]) >= 5
    assert len(data["title_candidates"]) >= 3
    # source_repr is a length-only summary (no raw SRT bytes — PII-safe per review)
    assert result.source_repr.startswith("<srt ") and "chars>" in result.source_repr


def test_extract_passes_model_sonnet_46():
    """LLM call must use claude-sonnet-4-6 explicitly."""
    extractor = Line1Extractor(people_md="stub")
    meta = EpisodeMetadata(slug="ep1", host="張修修", extra={"guest": "Guest"})
    llm_response = json.dumps(_VALID_STAGE1, ensure_ascii=False)

    with patch("agents.brook.line1_extractor.ask_multi", return_value=llm_response) as mock_llm:
        extractor.extract("srt text", meta)

    call_kwargs = mock_llm.call_args
    assert call_kwargs.kwargs.get("model") == "claude-sonnet-4-6"


def test_extract_passes_system_prompt():
    extractor = Line1Extractor(people_md="stub")
    meta = EpisodeMetadata(slug="ep1", host="張修修", extra={"guest": "Guest"})
    llm_response = json.dumps(_VALID_STAGE1, ensure_ascii=False)

    with patch("agents.brook.line1_extractor.ask_multi", return_value=llm_response) as mock_llm:
        extractor.extract("srt text", meta)

    call_kwargs = mock_llm.call_args
    assert call_kwargs.kwargs.get("system")


# ---------------------------------------------------------------------------
# Retry logic
# ---------------------------------------------------------------------------


def test_extract_retries_once_on_invalid_json():
    """First call returns non-JSON; second call returns valid JSON."""
    extractor = Line1Extractor(people_md="stub")
    meta = EpisodeMetadata(slug="ep1", host="張修修", extra={"guest": "朱為民"})
    valid_response = json.dumps(_VALID_STAGE1, ensure_ascii=False)

    responses = ["this is not JSON at all", valid_response]
    with patch("agents.brook.line1_extractor.ask_multi", side_effect=responses) as mock_llm:
        result = extractor.extract("srt text", meta)

    assert mock_llm.call_count == 2
    assert result.data["episode_type"] == "narrative_journey"


def test_extract_retries_once_on_schema_mismatch():
    """First call returns JSON missing required fields; second returns valid JSON."""
    extractor = Line1Extractor(people_md="stub")
    meta = EpisodeMetadata(slug="ep1", host="張修修", extra={"guest": "朱為民"})
    bad_response = json.dumps({"hooks": ["only one hook"], "episode_type": "listicle"})
    valid_response = json.dumps(_VALID_STAGE1, ensure_ascii=False)

    with patch(
        "agents.brook.line1_extractor.ask_multi", side_effect=[bad_response, valid_response]
    ) as mock_llm:
        result = extractor.extract("srt text", meta)

    assert mock_llm.call_count == 2
    assert result.data["episode_type"] == "narrative_journey"


def test_extract_raises_after_two_failures():
    """Both calls fail validation → ValueError raised."""
    extractor = Line1Extractor(people_md="stub")
    meta = EpisodeMetadata(slug="ep1", host="張修修", extra={"guest": "朱為民"})
    bad_response = '{"not": "valid schema"}'

    with patch("agents.brook.line1_extractor.ask_multi", return_value=bad_response):
        with pytest.raises(ValueError, match="Stage 1 extraction failed"):
            extractor.extract("srt text", meta)


def test_extract_retry_prompt_includes_corrective_note():
    """On retry, an extra user message must tell the LLM what failed previously.

    Resending the same prompt verbatim wastes a round-trip on deterministic LLM
    errors. Reviewer asked for an explicit corrective note.
    """
    extractor = Line1Extractor(people_md="stub")
    meta = EpisodeMetadata(slug="ep1", host="張修修", extra={"guest": "Guest"})
    bad_response = '{"hooks": ["only one"], "episode_type": "listicle"}'
    valid_response = json.dumps(_VALID_STAGE1, ensure_ascii=False)

    with patch(
        "agents.brook.line1_extractor.ask_multi", side_effect=[bad_response, valid_response]
    ) as mock_llm:
        extractor.extract("srt text", meta)

    assert mock_llm.call_count == 2
    second_messages = mock_llm.call_args_list[1].args[0]
    second_combined = " ".join(m["content"] for m in second_messages)
    # Corrective note keywords
    assert "schema" in second_combined or "驗證" in second_combined or "錯誤" in second_combined


def test_extract_rejects_invalid_episode_type():
    """episode_type must be one of the four enum values; bad value triggers retry."""
    extractor = Line1Extractor(people_md="stub")
    meta = EpisodeMetadata(slug="ep1", host="張修修", extra={"guest": "Guest"})
    bad = {**_VALID_STAGE1, "episode_type": "off-grid-mystery"}
    bad_response = json.dumps(bad, ensure_ascii=False)
    valid_response = json.dumps(_VALID_STAGE1, ensure_ascii=False)

    with patch(
        "agents.brook.line1_extractor.ask_multi", side_effect=[bad_response, valid_response]
    ) as mock_llm:
        result = extractor.extract("srt", meta)

    assert mock_llm.call_count == 2
    assert result.data["episode_type"] == "narrative_journey"


def test_extract_rejects_meta_description_too_short():
    """meta_description below 80 chars → ValidationError → retry."""
    extractor = Line1Extractor(people_md="stub")
    meta = EpisodeMetadata(slug="ep1", host="張修修", extra={"guest": "Guest"})
    bad = {**_VALID_STAGE1, "meta_description": "太短了"}
    bad_response = json.dumps(bad, ensure_ascii=False)
    valid_response = json.dumps(_VALID_STAGE1, ensure_ascii=False)

    with patch(
        "agents.brook.line1_extractor.ask_multi", side_effect=[bad_response, valid_response]
    ) as mock_llm:
        result = extractor.extract("srt", meta)

    assert mock_llm.call_count == 2
    assert len(result.data["meta_description"]) >= 80


def test_extract_accepts_non_narrative_episode_type():
    """Schema accepts each of the four episode types — coverage gap fix."""
    extractor = Line1Extractor(people_md="stub")
    meta = EpisodeMetadata(slug="ep1", host="張修修", extra={"guest": "Guest"})
    for episode_type in ("narrative_journey", "myth_busting", "framework", "listicle"):
        data = {**_VALID_STAGE1, "episode_type": episode_type}
        with patch(
            "agents.brook.line1_extractor.ask_multi",
            return_value=json.dumps(data, ensure_ascii=False),
        ):
            result = extractor.extract("srt", meta)
        assert result.data["episode_type"] == episode_type


def test_extract_prompt_defines_each_episode_type():
    """Prompt must include 1-line gloss per episode_type so LLM can route correctly."""
    extractor = Line1Extractor(people_md="stub")
    meta = EpisodeMetadata(slug="ep1", host="張修修", extra={"guest": "Guest"})
    valid_response = json.dumps(_VALID_STAGE1, ensure_ascii=False)

    with patch("agents.brook.line1_extractor.ask_multi", return_value=valid_response) as mock_llm:
        extractor.extract("srt", meta)

    messages = mock_llm.call_args_list[0].args[0]
    combined = " ".join(m["content"] for m in messages)
    # Each enum name appears AND has accompanying gloss text
    for type_name in ("narrative_journey", "myth_busting", "framework", "listicle"):
        assert type_name in combined, f"missing episode_type {type_name} in prompt"
    # Gloss keywords
    assert "敘事弧" in combined or "人生敘事" in combined  # narrative_journey gloss
    assert "迷思" in combined or "誤解" in combined  # myth_busting gloss
    assert "方法論" in combined or "步驟" in combined  # framework gloss
    assert "清單" in combined or "列舉" in combined  # listicle gloss


# ---------------------------------------------------------------------------
# Guest=None warning
# ---------------------------------------------------------------------------


def test_extract_warns_when_guest_is_none(caplog):
    extractor = Line1Extractor(people_md="stub")
    meta = EpisodeMetadata(slug="ep1", host="張修修")  # no guest in extra
    llm_response = json.dumps(_VALID_STAGE1, ensure_ascii=False)

    with patch("agents.brook.line1_extractor.ask_multi", return_value=llm_response):
        with caplog.at_level(logging.WARNING, logger="nakama.brook.line1_extractor"):
            result = extractor.extract("srt text", meta)

    assert result is not None
    assert any("guest" in record.message.lower() for record in caplog.records)


# ---------------------------------------------------------------------------
# Golden fixture round-trip
# ---------------------------------------------------------------------------


def test_golden_fixture_schema_passes():
    """Fixture SRT → all 8 narrative segments present + quotes ≥5 + titles ≥3."""
    extractor = Line1Extractor(people_md="stub profile text")
    meta = EpisodeMetadata(slug="dr-chu-ep67", host="張修修", extra={"guest": "朱為民"})
    llm_response = json.dumps(_VALID_STAGE1, ensure_ascii=False)

    with patch("agents.brook.line1_extractor.ask_multi", return_value=llm_response):
        result = extractor.extract(_FIXTURE_SRT, meta)

    data = result.data

    # 8-segment coverage
    for segment in (
        "hooks",
        "identity_sketch",
        "origin",
        "turning_point",
        "rebirth",
        "present_action",
        "ending_direction",
    ):
        assert segment in data, f"missing segment: {segment}"
        assert data[segment], f"empty segment: {segment}"

    # Quotes ≥5
    assert len(data["quotes"]) >= 5
    for q in data["quotes"]:
        assert q["text"]
        assert q["timestamp"]
        assert q["speaker"]

    # Titles ≥3
    assert len(data["title_candidates"]) >= 3

    # episode_type valid
    valid_types = {"narrative_journey", "myth_busting", "framework", "listicle"}
    assert data["episode_type"] in valid_types

    # meta_description present
    assert data["meta_description"]


def test_golden_fixture_srt_text_in_prompt():
    """SRT text must appear in the LLM messages (not silently dropped)."""
    extractor = Line1Extractor(people_md="stub")
    meta = EpisodeMetadata(slug="ep1", host="張修修", extra={"guest": "朱為民"})
    llm_response = json.dumps(_VALID_STAGE1, ensure_ascii=False)

    with patch("agents.brook.line1_extractor.ask_multi", return_value=llm_response) as mock_llm:
        extractor.extract(_FIXTURE_SRT, meta)

    messages = mock_llm.call_args.args[0]
    combined = " ".join(m["content"] for m in messages)
    # Both SRT structure (SPEAKER labels) AND content must carry through
    assert "SPEAKER_00" in combined
    assert "善終" in combined


def test_speaker_mapping_in_prompt():
    """Host + guest names appear in the LLM messages for attribution."""
    extractor = Line1Extractor(people_md="stub")
    meta = EpisodeMetadata(slug="ep1", host="張修修", extra={"guest": "朱為民"})
    llm_response = json.dumps(_VALID_STAGE1, ensure_ascii=False)

    with patch("agents.brook.line1_extractor.ask_multi", return_value=llm_response) as mock_llm:
        extractor.extract("srt text", meta)

    messages = mock_llm.call_args.args[0]
    combined = " ".join(m["content"] for m in messages)
    assert "張修修" in combined
    assert "朱為民" in combined


# ---------------------------------------------------------------------------
# JSON wrapped in markdown fences
# ---------------------------------------------------------------------------


def test_extract_handles_json_wrapped_in_markdown_fence():
    """LLM sometimes wraps output in ```json ... ``` — must be stripped."""
    extractor = Line1Extractor(people_md="stub")
    meta = EpisodeMetadata(slug="ep1", host="張修修", extra={"guest": "Guest"})
    fenced = f"```json\n{json.dumps(_VALID_STAGE1, ensure_ascii=False)}\n```"

    with patch("agents.brook.line1_extractor.ask_multi", return_value=fenced):
        result = extractor.extract("srt text", meta)

    assert result.data["episode_type"] == "narrative_journey"
