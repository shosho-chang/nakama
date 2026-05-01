"""Tests for agents.brook.ig_renderer.IGRenderer.

Covers:
- Protocol conformance: IGRenderer satisfies ChannelRenderer at runtime
- Single ChannelArtifact with IG_FILENAME + channel="ig"
- episode_type routing to 4 sub-template card counts (5/7/5/10)
- LLM mock test with valid cards JSON per episode_type
- JSON parsing failure → ValueError with raw context
- Schema validation:
  - card count mismatch
  - role sequence mismatch
  - missing required fields per card
  - cover headline > 10 字
  - middle headline > 12 字
  - body > 80 字
- Total char count soft warning (outside [150, 300])
- Defensive Stage 1 dict access (typed errors on missing/empty fields)
- Profile loading default + override
- Sub-template / EPISODE_TYPE_CARD_COUNT 1:1 alignment drift guard
- Markdown code-fence stripping in LLM output
"""

from __future__ import annotations

import json
import logging
from unittest.mock import patch

import pytest

from agents.brook.ig_renderer import (
    _SUB_TEMPLATE_DIRECTIVES,
    EPISODE_TYPE_CARD_COUNT,
    IGRenderer,
)
from agents.brook.repurpose_engine import (
    IG_FILENAME,
    ChannelArtifact,
    ChannelRenderer,
    EpisodeMetadata,
    Stage1Result,
)
from agents.brook.style_profile_loader import StyleProfile

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

_BASE_STAGE1 = {
    "hooks": [
        "30 歲，她裸辭去考心理諮商",
        "從中華電信穩定生活到創辦諮商所",
        "她的人生轉折給每個卡關的人新答案",
    ],
    "identity_sketch": "周慕姿是知名心理諮商師，著有《情緒勒索》《關係黑洞》等暢銷書。",
    "origin": "她政大新聞畢業，進入中華電信基金會工作，過著看似一帆風順的生活。",
    "turning_point": "30 歲那年她裸辭，連年終都不領，去考心理諮商所。",
    "rebirth": "她現在創辦了心理諮商所，著作暢銷，主持 Podcast 還是樂團主唱。",
    "present_action": "本集她分享 30 歲做這個重大決定背後的動機，以及內耗如何發生。",
    "ending_direction": "留白於：「人生這場遊戲，你要當 NPC 還是一級玩家？」",
    "quotes": [
        {"text": "我 30 歲前都在虛度光陰", "timestamp": "00:10:30", "speaker": "周慕姿"},
        {"text": "覺得自己不夠好，是內耗的根源", "timestamp": "00:25:18", "speaker": "周慕姿"},
        {"text": "認識自己是放下內耗的第一步", "timestamp": "00:42:45", "speaker": "周慕姿"},
        {"text": "高功能型內耗最容易被忽略", "timestamp": "01:05:12", "speaker": "周慕姿"},
        {"text": "做諮商是極度被低估的工具", "timestamp": "01:20:08", "speaker": "周慕姿"},
    ],
    "title_candidates": [
        "🧠不正常人類研究所 EP97｜周慕姿：30 歲裸辭去做諮商，從內耗到自由",
        "🧠不正常人類研究所 EP97｜周慕姿：高功能型內耗的解方",
        "🧠不正常人類研究所 EP97｜周慕姿：我 30 歲前都在虛度光陰",
    ],
    "meta_description": (
        "心理諮商師周慕姿 30 歲裸辭去考諮商所，從中華電信員工到暢銷作家。"
        "本集分享內耗如何發生、如何用心理諮商釋放高功能型內耗。"
    ),
    "episode_type": "narrative_journey",
}


def _stage1_with_type(episode_type: str) -> dict:
    """Return a fresh Stage 1 dict with the given episode_type."""
    return {**_BASE_STAGE1, "episode_type": episode_type}


def _stub_profile(
    *,
    body: str = "stub ig-carousel profile",
    word_count_min: int = 150,
    word_count_max: int = 300,
) -> StyleProfile:
    return StyleProfile(
        profile_id="ig-carousel@0.1.0-test",
        category="ig-carousel",
        primary_category="ig-carousel",
        body=body,
        word_count_min=word_count_min,
        word_count_max=word_count_max,
        forbid_emoji=False,
        default_tag_hints=("instagram", "carousel"),
        detect_keywords=(),
    )


def _make_stage1_result(data: dict | None = None) -> Stage1Result:
    return Stage1Result(
        data=data if data is not None else dict(_BASE_STAGE1), source_repr="<srt 200 chars>"
    )


def _make_metadata(podcast_url: str = "https://example.com/ep97") -> EpisodeMetadata:
    return EpisodeMetadata(
        slug="zhou-mu-zi-ep97",
        host="張修修",
        extra={"guest": "周慕姿", "podcast_episode_url": podcast_url},
    )


def _renderer(**profile_kwargs) -> IGRenderer:
    return IGRenderer(style_profile=_stub_profile(**profile_kwargs))


def _build_valid_cards(episode_type: str) -> dict:
    """Construct a syntactically valid cards payload for the given episode_type.

    Headlines + bodies are sized within hard limits so validation passes.
    """
    n = EPISODE_TYPE_CARD_COUNT[episode_type]
    cards = []
    for i in range(1, n + 1):
        head_len = 8 if i == 1 else 10  # cover ≤10, middle ≤12
        body_len = 0 if i == 1 else 25  # cover often empty body
        cards.append(
            {
                "role": f"C{i}",
                "headline": "測" * head_len,
                "body": "說明文字" * (body_len // 4),
                "char_count": head_len + body_len,
            }
        )
    total = sum(c["char_count"] for c in cards)
    # Adjust last card body so total is in [150, 300] band for narrative_journey
    # (5 cards × ~30 = 150). Add filler to reach 200 mid-band.
    if total < 200:
        deficit = 200 - total
        last = cards[-1]
        last["body"] = last["body"] + ("補" * deficit)
        last["char_count"] += deficit
        total = sum(c["char_count"] for c in cards)
    return {
        "episode_type": episode_type,
        "card_count": n,
        "cards": cards,
        "total_char_count": total,
    }


# ---------------------------------------------------------------------------
# Protocol conformance
# ---------------------------------------------------------------------------


def test_ig_renderer_satisfies_channel_renderer_protocol():
    assert isinstance(_renderer(), ChannelRenderer)


# ---------------------------------------------------------------------------
# Single artifact output
# ---------------------------------------------------------------------------


def test_render_returns_single_artifact():
    payload = _build_valid_cards("narrative_journey")
    with patch(
        "agents.brook.ig_renderer.ask_multi",
        return_value=json.dumps(payload, ensure_ascii=False),
    ):
        artifacts = _renderer().render(_make_stage1_result(), _make_metadata())

    assert len(artifacts) == 1
    assert isinstance(artifacts[0], ChannelArtifact)


def test_render_uses_ig_filename_constant():
    payload = _build_valid_cards("narrative_journey")
    with patch(
        "agents.brook.ig_renderer.ask_multi",
        return_value=json.dumps(payload, ensure_ascii=False),
    ):
        artifact = _renderer().render(_make_stage1_result(), _make_metadata())[0]

    assert artifact.filename == IG_FILENAME, (
        "IGRenderer must use IG_FILENAME constant, not hardcoded string"
    )
    assert artifact.channel == "ig"


def test_render_artifact_content_is_indented_json():
    payload = _build_valid_cards("narrative_journey")
    with patch(
        "agents.brook.ig_renderer.ask_multi",
        return_value=json.dumps(payload, ensure_ascii=False),
    ):
        artifact = _renderer().render(_make_stage1_result(), _make_metadata())[0]

    # Round-trip parse to confirm valid JSON
    parsed = json.loads(artifact.content)
    assert parsed["episode_type"] == "narrative_journey"
    assert parsed["card_count"] == 5
    assert artifact.content.endswith("\n"), "trailing newline expected"
    assert "  " in artifact.content, "expected indent=2 formatted JSON"


# ---------------------------------------------------------------------------
# episode_type routing
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "episode_type,expected_count",
    [
        ("narrative_journey", 5),
        ("myth_busting", 7),
        ("framework", 5),
        ("listicle", 10),
    ],
)
def test_render_routes_episode_type_to_expected_card_count(episode_type, expected_count):
    payload = _build_valid_cards(episode_type)
    stage1_data = _stage1_with_type(episode_type)
    with patch(
        "agents.brook.ig_renderer.ask_multi",
        return_value=json.dumps(payload, ensure_ascii=False),
    ):
        artifact = _renderer().render(_make_stage1_result(stage1_data), _make_metadata())[0]

    parsed = json.loads(artifact.content)
    assert parsed["episode_type"] == episode_type
    assert parsed["card_count"] == expected_count
    assert len(parsed["cards"]) == expected_count


def test_render_invalid_episode_type_raises():
    bad_data = _stage1_with_type("invalid_type")
    with pytest.raises(ValueError, match="not in"):
        _renderer().render(_make_stage1_result(bad_data), _make_metadata())


def test_episode_type_card_count_covers_all_sub_template_directives():
    """`EPISODE_TYPE_CARD_COUNT` keys must equal `_SUB_TEMPLATE_DIRECTIVES` keys.

    Drift guard: a new episode_type in one but not the other would either
    produce an unrouted card-count (if missing in EPISODE_TYPE_CARD_COUNT)
    or an unrouted directive (if missing in _SUB_TEMPLATE_DIRECTIVES) —
    both manifest as KeyError at request time. Catch at unit-test time.
    """
    assert set(EPISODE_TYPE_CARD_COUNT.keys()) == set(_SUB_TEMPLATE_DIRECTIVES.keys()), (
        f"EPISODE_TYPE_CARD_COUNT={set(EPISODE_TYPE_CARD_COUNT)} vs "
        f"_SUB_TEMPLATE_DIRECTIVES={set(_SUB_TEMPLATE_DIRECTIVES)} drift"
    )


def test_sub_template_directive_card_count_matches_constant():
    """Each directive's `{type} N 卡` claim must match EPISODE_TYPE_CARD_COUNT.

    Stronger drift guard than key-set equality: catches edits like changing
    `narrative_journey 5 卡` → `narrative_journey 6 卡` in the directive
    text without updating EPISODE_TYPE_CARD_COUNT[narrative_journey]=5.
    Such drift would silently produce LLM outputs whose card count fails
    post-validation (raise) — catch at unit-test time instead.
    """
    import re

    pattern = re.compile(r"\*\*(\w+)\s+(\d+)\s+卡")
    for episode_type, directive in _SUB_TEMPLATE_DIRECTIVES.items():
        match = pattern.search(directive)
        assert match is not None, (
            f"directive for {episode_type} missing canonical `**{{type}} N 卡**` header"
        )
        type_name, claimed_count = match.group(1), int(match.group(2))
        assert type_name == episode_type, (
            f"directive header type {type_name!r} ≠ dict key {episode_type!r}"
        )
        assert claimed_count == EPISODE_TYPE_CARD_COUNT[episode_type], (
            f"directive for {episode_type} claims {claimed_count} 卡 but "
            f"EPISODE_TYPE_CARD_COUNT[{episode_type!r}]="
            f"{EPISODE_TYPE_CARD_COUNT[episode_type]} — drift"
        )


# ---------------------------------------------------------------------------
# Prompt content
# ---------------------------------------------------------------------------


def test_render_passes_profile_body_and_directive_into_prompt():
    payload = _build_valid_cards("narrative_journey")
    profile = _stub_profile(body="UNIQUE_PROFILE_MARKER_xyz123")
    renderer = IGRenderer(style_profile=profile)
    with patch(
        "agents.brook.ig_renderer.ask_multi",
        return_value=json.dumps(payload, ensure_ascii=False),
    ) as mock_llm:
        renderer.render(_make_stage1_result(), _make_metadata())

    prompt = mock_llm.call_args.args[0][0]["content"]
    assert "UNIQUE_PROFILE_MARKER_xyz123" in prompt, "profile body must be injected"
    assert "narrative_journey 5 卡" in prompt, "sub-template directive must be present"


@pytest.mark.parametrize("episode_type", list(EPISODE_TYPE_CARD_COUNT.keys()))
def test_render_injects_correct_sub_template_for_episode_type(episode_type):
    payload = _build_valid_cards(episode_type)
    stage1_data = _stage1_with_type(episode_type)
    with patch(
        "agents.brook.ig_renderer.ask_multi",
        return_value=json.dumps(payload, ensure_ascii=False),
    ) as mock_llm:
        _renderer().render(_make_stage1_result(stage1_data), _make_metadata())

    prompt = mock_llm.call_args.args[0][0]["content"]
    expected_count = EPISODE_TYPE_CARD_COUNT[episode_type]
    assert f"{episode_type} {expected_count} 卡" in prompt, (
        f"prompt must specify {episode_type} {expected_count} 卡 layout"
    )


def test_render_prompt_includes_podcast_url():
    payload = _build_valid_cards("narrative_journey")
    url = "https://example.com/ep77"
    with patch(
        "agents.brook.ig_renderer.ask_multi",
        return_value=json.dumps(payload, ensure_ascii=False),
    ) as mock_llm:
        _renderer().render(_make_stage1_result(), _make_metadata(podcast_url=url))

    prompt = mock_llm.call_args.args[0][0]["content"]
    assert url in prompt


def test_render_prompt_forbids_url_hallucination():
    """Anti-hallucination: prompt must explicitly forbid fabricating URLs."""
    payload = _build_valid_cards("narrative_journey")
    with patch(
        "agents.brook.ig_renderer.ask_multi",
        return_value=json.dumps(payload, ensure_ascii=False),
    ) as mock_llm:
        _renderer().render(_make_stage1_result(), _make_metadata())

    prompt = mock_llm.call_args.args[0][0]["content"]
    assert "不要捏造" in prompt or "不可虛構" in prompt or "禁止捏造" in prompt


# ---------------------------------------------------------------------------
# Model selection
# ---------------------------------------------------------------------------


def test_render_passes_model_sonnet_46_by_default():
    payload = _build_valid_cards("narrative_journey")
    with patch(
        "agents.brook.ig_renderer.ask_multi",
        return_value=json.dumps(payload, ensure_ascii=False),
    ) as mock_llm:
        _renderer().render(_make_stage1_result(), _make_metadata())

    assert mock_llm.call_args.kwargs.get("model") == "claude-sonnet-4-6"


def test_render_accepts_model_override():
    payload = _build_valid_cards("narrative_journey")
    renderer = IGRenderer(style_profile=_stub_profile(), model="claude-opus-4-7")
    with patch(
        "agents.brook.ig_renderer.ask_multi",
        return_value=json.dumps(payload, ensure_ascii=False),
    ) as mock_llm:
        renderer.render(_make_stage1_result(), _make_metadata())

    assert mock_llm.call_args.kwargs.get("model") == "claude-opus-4-7"


# ---------------------------------------------------------------------------
# JSON parsing
# ---------------------------------------------------------------------------


def test_render_strips_markdown_fence_around_json():
    """LLMs sometimes wrap JSON in ```json ... ``` fences despite prompt asking otherwise."""
    payload = _build_valid_cards("narrative_journey")
    fenced = f"```json\n{json.dumps(payload, ensure_ascii=False)}\n```"
    with patch("agents.brook.ig_renderer.ask_multi", return_value=fenced):
        artifact = _renderer().render(_make_stage1_result(), _make_metadata())[0]

    parsed = json.loads(artifact.content)
    assert parsed["episode_type"] == "narrative_journey"


def test_render_invalid_json_raises_with_raw_context():
    """Bad JSON must surface a ValueError including a snippet of the raw output."""
    with patch("agents.brook.ig_renderer.ask_multi", return_value="not even close to json {{"):
        with pytest.raises(ValueError, match="not valid JSON"):
            _renderer().render(_make_stage1_result(), _make_metadata())


# ---------------------------------------------------------------------------
# Schema validation
# ---------------------------------------------------------------------------


def test_render_card_count_mismatch_raises():
    """Cards length ≠ expected_card_count must raise."""
    payload = _build_valid_cards("narrative_journey")
    payload["cards"].pop()  # 5 → 4
    payload["card_count"] = 4
    with patch(
        "agents.brook.ig_renderer.ask_multi",
        return_value=json.dumps(payload, ensure_ascii=False),
    ):
        with pytest.raises(ValueError, match="cards length 4 ≠ expected 5"):
            _renderer().render(_make_stage1_result(), _make_metadata())


def test_render_card_count_field_mismatch_raises():
    """payload card_count field declares N but actual cards length differs."""
    payload = _build_valid_cards("narrative_journey")
    payload["card_count"] = 99  # cards is still 5
    with patch(
        "agents.brook.ig_renderer.ask_multi",
        return_value=json.dumps(payload, ensure_ascii=False),
    ):
        with pytest.raises(ValueError, match="card_count="):
            _renderer().render(_make_stage1_result(), _make_metadata())


def test_render_role_sequence_mismatch_raises():
    """cards[i].role must be C{i+1} in order."""
    payload = _build_valid_cards("narrative_journey")
    payload["cards"][2]["role"] = "C99"
    with patch(
        "agents.brook.ig_renderer.ask_multi",
        return_value=json.dumps(payload, ensure_ascii=False),
    ):
        with pytest.raises(ValueError, match="role='C99' ≠ 'C3'"):
            _renderer().render(_make_stage1_result(), _make_metadata())


def test_render_cover_headline_too_long_raises():
    """C1 headline > 10 字 must raise."""
    payload = _build_valid_cards("narrative_journey")
    payload["cards"][0]["headline"] = "封面太長過字數限制超過十個字"  # 14 字
    with patch(
        "agents.brook.ig_renderer.ask_multi",
        return_value=json.dumps(payload, ensure_ascii=False),
    ):
        with pytest.raises(ValueError, match="C1 headline.*exceeds limit 10"):
            _renderer().render(_make_stage1_result(), _make_metadata())


def test_render_middle_headline_too_long_raises():
    """C2-CN headline > 12 字 must raise."""
    payload = _build_valid_cards("narrative_journey")
    payload["cards"][1]["headline"] = "中段卡標題超過十二個字限制應該失敗"  # 17 字
    with patch(
        "agents.brook.ig_renderer.ask_multi",
        return_value=json.dumps(payload, ensure_ascii=False),
    ):
        with pytest.raises(ValueError, match="C2 headline.*exceeds limit 12"):
            _renderer().render(_make_stage1_result(), _make_metadata())


def test_render_card_body_too_long_raises():
    """body > 80 字 must raise."""
    payload = _build_valid_cards("narrative_journey")
    payload["cards"][2]["body"] = "x" * 100  # 100 chars > 80
    with patch(
        "agents.brook.ig_renderer.ask_multi",
        return_value=json.dumps(payload, ensure_ascii=False),
    ):
        with pytest.raises(ValueError, match="body.*exceeds limit 80"):
            _renderer().render(_make_stage1_result(), _make_metadata())


def test_render_missing_card_field_raises():
    payload = _build_valid_cards("narrative_journey")
    del payload["cards"][1]["body"]
    with patch(
        "agents.brook.ig_renderer.ask_multi",
        return_value=json.dumps(payload, ensure_ascii=False),
    ):
        with pytest.raises(ValueError, match="missing required field 'body'"):
            _renderer().render(_make_stage1_result(), _make_metadata())


def test_render_total_char_count_outside_band_warns_not_raises(caplog):
    """total_char_count outside [150, 300] is a soft warning, not a raise."""
    payload = _build_valid_cards("narrative_journey")
    payload["total_char_count"] = 50  # below 150
    with patch(
        "agents.brook.ig_renderer.ask_multi",
        return_value=json.dumps(payload, ensure_ascii=False),
    ):
        with caplog.at_level(logging.WARNING, logger="nakama.brook.ig_renderer"):
            artifact = _renderer().render(_make_stage1_result(), _make_metadata())[0]

    # No exception; artifact still produced
    assert artifact.filename == IG_FILENAME
    warnings = [r for r in caplog.records if "total_char_count" in r.getMessage()]
    assert len(warnings) >= 1, "expected total_char_count band warning"


def test_render_total_char_count_missing_warns_not_raises(caplog):
    """Missing `total_char_count` field warns instead of raising.

    Strictness symmetry: out-of-band → warn (already), missing → warn (this).
    Per-card hard limits already catch runaway content; treating missing
    field as fail-close was a strictness asymmetry from the first commit.
    """
    payload = _build_valid_cards("narrative_journey")
    del payload["total_char_count"]
    with patch(
        "agents.brook.ig_renderer.ask_multi",
        return_value=json.dumps(payload, ensure_ascii=False),
    ):
        with caplog.at_level(logging.WARNING, logger="nakama.brook.ig_renderer"):
            artifact = _renderer().render(_make_stage1_result(), _make_metadata())[0]

    assert artifact.filename == IG_FILENAME
    msgs = [r.getMessage() for r in caplog.records]
    assert any("total_char_count missing" in m for m in msgs), (
        f"expected missing-total warning, got {msgs}"
    )


def test_render_total_char_count_bool_warns_not_raises(caplog):
    """`total_char_count: true` (bool) must not pass int check (Python quirk)."""
    payload = _build_valid_cards("narrative_journey")
    payload["total_char_count"] = True  # would pass isinstance(int) without bool guard
    with patch(
        "agents.brook.ig_renderer.ask_multi",
        return_value=json.dumps(payload, ensure_ascii=False),
    ):
        with caplog.at_level(logging.WARNING, logger="nakama.brook.ig_renderer"):
            artifact = _renderer().render(_make_stage1_result(), _make_metadata())[0]

    assert artifact.filename == IG_FILENAME
    msgs = [r.getMessage() for r in caplog.records]
    assert any("not an integer" in m for m in msgs), (
        f"expected not-integer warning for bool total, got {msgs}"
    )


def test_render_card_count_missing_field_raises_distinct_message(caplog):
    """Missing `card_count` field reports differently from a wrong value."""
    payload = _build_valid_cards("narrative_journey")
    del payload["card_count"]
    with patch(
        "agents.brook.ig_renderer.ask_multi",
        return_value=json.dumps(payload, ensure_ascii=False),
    ):
        with pytest.raises(ValueError, match="missing 'card_count' field"):
            _renderer().render(_make_stage1_result(), _make_metadata())


def test_render_warns_when_card_char_count_lies(caplog):
    """LLM-claimed `char_count` ≠ actual headline+body length triggers warning."""
    payload = _build_valid_cards("narrative_journey")
    # Truthful headline+body length is in `char_count`; lie by inflating.
    payload["cards"][2]["char_count"] = 999
    with patch(
        "agents.brook.ig_renderer.ask_multi",
        return_value=json.dumps(payload, ensure_ascii=False),
    ):
        with caplog.at_level(logging.WARNING, logger="nakama.brook.ig_renderer"):
            artifact = _renderer().render(_make_stage1_result(), _make_metadata())[0]

    assert artifact.filename == IG_FILENAME
    msgs = [r.getMessage() for r in caplog.records]
    assert any("char_count=999" in m and "≠ actual" in m for m in msgs), (
        f"expected char_count drift warning, got {msgs}"
    )


def test_render_episode_type_payload_mismatch_raises():
    """payload's episode_type must match Stage 1's episode_type."""
    payload = _build_valid_cards("narrative_journey")
    payload["episode_type"] = "myth_busting"  # but Stage 1 says narrative_journey
    with patch(
        "agents.brook.ig_renderer.ask_multi",
        return_value=json.dumps(payload, ensure_ascii=False),
    ):
        with pytest.raises(ValueError, match="payload episode_type"):
            _renderer().render(_make_stage1_result(), _make_metadata())


# ---------------------------------------------------------------------------
# Defensive Stage 1 dict access
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "missing_key",
    [
        "episode_type",
        "identity_sketch",
        "origin",
        "turning_point",
        "rebirth",
        "quotes",
        "present_action",
    ],
)
def test_render_raises_on_missing_required_field(missing_key):
    bad_data = {k: v for k, v in _BASE_STAGE1.items() if k != missing_key}
    with pytest.raises(ValueError, match=f"missing required field {missing_key!r}"):
        _renderer().render(_make_stage1_result(bad_data), _make_metadata())


def test_render_fails_fast_before_llm_call():
    """Validation must happen BEFORE submitting LLM call to avoid wasted cost."""
    bad_data = {k: v for k, v in _BASE_STAGE1.items() if k != "origin"}
    with patch("agents.brook.ig_renderer.ask_multi") as mock_llm:
        with pytest.raises(ValueError):
            _renderer().render(_make_stage1_result(bad_data), _make_metadata())

    assert mock_llm.call_count == 0


# ---------------------------------------------------------------------------
# Profile loading default
# ---------------------------------------------------------------------------


def test_render_loads_ig_carousel_profile_by_default():
    with patch("agents.brook.ig_renderer.load_style_profile") as mock_loader:
        mock_loader.return_value = _stub_profile()
        IGRenderer()

    mock_loader.assert_called_once_with("ig-carousel")
