"""Tests for agents.brook.fb_renderer.FBRenderer.

Covers:
- Protocol conformance: FBRenderer satisfies ChannelRenderer at runtime
- LLM mock test: Stage1Result → 4 ChannelArtifact (one per FB_TONALS)
- Deterministic FB_TONALS ordering (regardless of thread completion order)
- Tonal differentiation: 4 separate LLM calls with distinct tonal directives
- fb-{tonal}.md filename via fb_filename() helper (no hardcoded literals)
- Word count warning per variant
- Defensive Stage 1 dict access (typed errors on missing/empty fields)
- Profile loading fallback + override
- Plain text output (no markdown headers / frontmatter / hashtags)
"""

from __future__ import annotations

import logging
from unittest.mock import patch

import pytest

from agents.brook.fb_renderer import _TONAL_DIRECTIVES, FBRenderer
from agents.brook.repurpose_engine import (
    FB_TONALS,
    ChannelArtifact,
    ChannelRenderer,
    EpisodeMetadata,
    Stage1Result,
    fb_filename,
)
from agents.brook.style_profile_loader import StyleProfile

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

_STAGE1_DATA = {
    "hooks": [
        "EP99｜周郁凱：「首次揭露！獨家懶人成功術」",
        "他被退學的高中生，後來史丹佛和 Google 搶著上他的課",
        "從遊戲化專家到烏克蘭總統的諮詢顧問",
    ],
    "identity_sketch": (
        "周郁凱是享譽全球的遊戲化大師，創造了八角框架，"
        "被三千六百多篇學術論文引用，影響了健康、教育、公共政策等領域。"
    ),
    "origin": (
        "高中時代電玩重度成癮，大一玩星海爭霸玩到被退學。"
        "在某個關鍵時刻，他覺醒了，決定不再沉迷虛擬世界。"
    ),
    "turning_point": ("他開始把人生當成遊戲來破關，這個 mindset shift 帶他從魯蛇變成創業家。"),
    "rebirth": ("他發布八角框架後成立顧問公司，十年來成為遊戲化領域頂尖的存在。"),
    "present_action": (
        "他在台灣開了線上課程，使出渾身解數毫不藏私分享。"
        "本集分享他受邀到烏克蘭，討論如何用八角框架幫助士兵和國家重建。"
    ),
    "ending_direction": (
        "留白於「人生這場遊戲，你要當 NPC 還是一級玩家？」邀請讀者進入 podcast 聽完整故事。"
    ),
    "quotes": [
        {
            "text": "我們都拒絕成為每天日復一日做著自己討厭的事情的非玩家角色",
            "timestamp": "00:15:30",
            "speaker": "周郁凱",
        },
        {
            "text": "八角框架可以應用在幾乎所有產業和情境，能讓人類對所有事情產生動力",
            "timestamp": "00:42:18",
            "speaker": "周郁凱",
        },
        {
            "text": "我去到烏克蘭，他們問我能不能用遊戲化幫助國家重建",
            "timestamp": "01:05:45",
            "speaker": "周郁凱",
        },
        {
            "text": "原子習慣是 50 級魔法，鉤癮效應是 60 級，八角框架是 80 級的頂級魔法",
            "timestamp": "01:20:12",
            "speaker": "張修修",
        },
        {
            "text": "我這次是完全不藏私的全盤托出",
            "timestamp": "01:32:08",
            "speaker": "周郁凱",
        },
    ],
    "title_candidates": [
        "🧠不正常人類研究所 EP99｜周郁凱：八角框架創造者，烏克蘭總統的遊戲化諮詢",
        "🧠不正常人類研究所 EP99｜周郁凱：從電玩成癮被退學，到史丹佛 Google 搶上課",
        "🧠不正常人類研究所 EP99｜周郁凱：用遊戲化重建國家、人生破關的硬核哲學",
    ],
    "meta_description": (
        "遊戲化大師周郁凱，從電玩成癮被退學的魯蛇，"
        "到創造影響三千六百多篇論文的八角框架，本集分享他受邀到烏克蘭重建國家的故事。"
        "立即收聽不正常人類研究所 EP99！"
    ),
    "episode_type": "narrative_journey",
}


def _stub_profile(
    *,
    body: str = "stub fb-post profile",
    word_count_min: int = 800,
    word_count_max: int = 3500,
    primary_category: str = "fb-post",
    tags: tuple[str, ...] = ("facebook", "podcast", "interview"),
) -> StyleProfile:
    return StyleProfile(
        profile_id="fb-post@0.1.0-test",
        category="fb-post",
        primary_category=primary_category,
        body=body,
        word_count_min=word_count_min,
        word_count_max=word_count_max,
        forbid_emoji=False,
        default_tag_hints=tags,
        detect_keywords=(),
    )


def _make_stage1_result(data: dict | None = None) -> Stage1Result:
    return Stage1Result(
        data=data if data is not None else _STAGE1_DATA, source_repr="<srt 200 chars>"
    )


def _make_metadata(podcast_url: str = "https://example.com/ep99") -> EpisodeMetadata:
    return EpisodeMetadata(
        slug="yu-kai-chou-ep99",
        host="張修修",
        extra={"guest": "周郁凱", "podcast_episode_url": podcast_url},
    )


def _renderer(**profile_kwargs) -> FBRenderer:
    return FBRenderer(style_profile=_stub_profile(**profile_kwargs))


# Static fake LLM body — long enough to clear the 800-char floor without
# triggering word-count warnings during default tests (1000-char filler).
_FAKE_FB_BODY = "🧠 EP99 周郁凱訪談筆記。" + ("這是測試用的 FB 貼文內容。" * 80)


# ---------------------------------------------------------------------------
# Protocol conformance
# ---------------------------------------------------------------------------


def test_fb_renderer_satisfies_channel_renderer_protocol():
    assert isinstance(_renderer(), ChannelRenderer)


# ---------------------------------------------------------------------------
# 4-artifact output structure
# ---------------------------------------------------------------------------


def test_render_returns_four_artifacts_one_per_tonal():
    with patch("agents.brook.fb_renderer.ask_multi", return_value=_FAKE_FB_BODY):
        artifacts = _renderer().render(_make_stage1_result(), _make_metadata())

    assert len(artifacts) == len(FB_TONALS) == 4
    for artifact in artifacts:
        assert isinstance(artifact, ChannelArtifact)


def test_render_artifacts_use_fb_filename_helper():
    """Filenames must come from fb_filename(tonal), not hardcoded strings."""
    with patch("agents.brook.fb_renderer.ask_multi", return_value=_FAKE_FB_BODY):
        artifacts = _renderer().render(_make_stage1_result(), _make_metadata())

    filenames = {a.filename for a in artifacts}
    expected = {fb_filename(t) for t in FB_TONALS}
    assert filenames == expected


def test_render_artifacts_in_deterministic_fb_tonals_order():
    """Output order tracks FB_TONALS regardless of thread completion order."""
    with patch("agents.brook.fb_renderer.ask_multi", return_value=_FAKE_FB_BODY):
        artifacts = _renderer().render(_make_stage1_result(), _make_metadata())

    assert [a.filename for a in artifacts] == [fb_filename(t) for t in FB_TONALS]


def test_render_artifact_channel_names_match_fb_tonal_pattern():
    with patch("agents.brook.fb_renderer.ask_multi", return_value=_FAKE_FB_BODY):
        artifacts = _renderer().render(_make_stage1_result(), _make_metadata())

    channels = {a.channel for a in artifacts}
    expected = {f"fb-{t}" for t in FB_TONALS}
    assert channels == expected


# ---------------------------------------------------------------------------
# 4 distinct LLM calls + tonal differentiation
# ---------------------------------------------------------------------------


def test_render_makes_four_separate_llm_calls():
    """One ask_multi call per tonal — not a shared call with branching output."""
    with patch("agents.brook.fb_renderer.ask_multi", return_value=_FAKE_FB_BODY) as mock_llm:
        _renderer().render(_make_stage1_result(), _make_metadata())

    assert mock_llm.call_count == 4


def test_render_tonal_directive_appears_in_each_prompt():
    """Each LLM call's user prompt must contain its tonal-specific directive.

    This is the heart of "4 variants 可區別" — without distinct directives,
    all 4 outputs would be identical despite separate calls. We verify the
    directive substring (e.g. "tonal=light") rather than full content to
    avoid coupling to wording details.
    """
    with patch("agents.brook.fb_renderer.ask_multi", return_value=_FAKE_FB_BODY) as mock_llm:
        _renderer().render(_make_stage1_result(), _make_metadata())

    # Collect all 4 user prompts and verify each carries its tonal marker.
    user_prompts = [call.args[0][0]["content"] for call in mock_llm.call_args_list]
    tonal_markers_seen = set()
    for prompt in user_prompts:
        for tonal in FB_TONALS:
            if f"tonal={tonal}" in prompt:
                tonal_markers_seen.add(tonal)
                break
    assert tonal_markers_seen == set(FB_TONALS), (
        f"missing tonal markers: {set(FB_TONALS) - tonal_markers_seen} — "
        f"each LLM call must carry its tonal directive"
    )


def test_tonal_directives_are_actually_different():
    """Sanity: the 4 tonal directives are non-trivially distinct strings."""
    bodies = list(_TONAL_DIRECTIVES.values())
    # All 4 distinct
    assert len(set(bodies)) == 4, "tonal directives must all be distinct"
    # Each is non-trivial length
    for tonal, directive in _TONAL_DIRECTIVES.items():
        assert len(directive) > 100, f"tonal {tonal} directive is suspiciously short"


# ---------------------------------------------------------------------------
# Model selection
# ---------------------------------------------------------------------------


def test_render_passes_model_sonnet_46_by_default():
    with patch("agents.brook.fb_renderer.ask_multi", return_value=_FAKE_FB_BODY) as mock_llm:
        _renderer().render(_make_stage1_result(), _make_metadata())

    for call in mock_llm.call_args_list:
        assert call.kwargs.get("model") == "claude-sonnet-4-6"


def test_render_accepts_model_override():
    renderer = FBRenderer(style_profile=_stub_profile(), model="claude-opus-4-7")
    with patch("agents.brook.fb_renderer.ask_multi", return_value=_FAKE_FB_BODY) as mock_llm:
        renderer.render(_make_stage1_result(), _make_metadata())

    for call in mock_llm.call_args_list:
        assert call.kwargs.get("model") == "claude-opus-4-7"


# ---------------------------------------------------------------------------
# Output content shape (plain text, not markdown)
# ---------------------------------------------------------------------------


def test_render_artifact_content_includes_llm_body():
    """Each artifact's content is the LLM body + trailing newline (no frontmatter)."""
    with patch("agents.brook.fb_renderer.ask_multi", return_value=_FAKE_FB_BODY):
        artifacts = _renderer().render(_make_stage1_result(), _make_metadata())

    for artifact in artifacts:
        # Content should NOT start with YAML frontmatter (FB doesn't use it)
        assert not artifact.content.startswith("---"), (
            f"FB artifact unexpectedly has frontmatter: {artifact.content[:50]!r}"
        )
        # Content should end with single trailing newline
        assert artifact.content.endswith("\n"), "FB artifact missing trailing newline"
        # Content should be the LLM body (rstripped + \n)
        assert artifact.content == _FAKE_FB_BODY.rstrip() + "\n"


def test_render_passes_podcast_url_into_prompt():
    url = "https://example.com/ep77"
    with patch("agents.brook.fb_renderer.ask_multi", return_value=_FAKE_FB_BODY) as mock_llm:
        _renderer().render(_make_stage1_result(), _make_metadata(podcast_url=url))

    for call in mock_llm.call_args_list:
        prompt = call.args[0][0]["content"]
        assert url in prompt, "podcast URL must reach the prompt for CTA section"


def test_render_passes_profile_body_into_prompt():
    """fb-post.md profile body is injected into each prompt (LLM has style guide)."""
    profile = _stub_profile(body="UNIQUE_PROFILE_MARKER_xyz123")
    renderer = FBRenderer(style_profile=profile)
    with patch("agents.brook.fb_renderer.ask_multi", return_value=_FAKE_FB_BODY) as mock_llm:
        renderer.render(_make_stage1_result(), _make_metadata())

    for call in mock_llm.call_args_list:
        prompt = call.args[0][0]["content"]
        assert "UNIQUE_PROFILE_MARKER_xyz123" in prompt


# ---------------------------------------------------------------------------
# Defensive Stage 1 dict access
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "missing_key", ["identity_sketch", "origin", "turning_point", "rebirth", "quotes"]
)
def test_render_raises_on_missing_required_field(missing_key):
    bad_data = {k: v for k, v in _STAGE1_DATA.items() if k != missing_key}
    with pytest.raises(ValueError, match=f"missing required field {missing_key!r}"):
        _renderer().render(_make_stage1_result(bad_data), _make_metadata())


@pytest.mark.parametrize("empty_key", ["identity_sketch", "origin"])
def test_render_raises_on_empty_required_field(empty_key):
    bad_data = {**_STAGE1_DATA, empty_key: ""}
    with pytest.raises(ValueError, match=f"\\[{empty_key!r}\\] is empty"):
        _renderer().render(_make_stage1_result(bad_data), _make_metadata())


def test_render_fails_fast_before_spinning_up_threads():
    """Validation must happen BEFORE submitting LLM calls, not in the thread pool.

    Otherwise the 4 wasted LLM calls cost real money on a fixable upstream
    error. We assert this by checking ask_multi is never called.
    """
    bad_data = {k: v for k, v in _STAGE1_DATA.items() if k != "origin"}
    with patch("agents.brook.fb_renderer.ask_multi") as mock_llm:
        with pytest.raises(ValueError):
            _renderer().render(_make_stage1_result(bad_data), _make_metadata())

    assert mock_llm.call_count == 0, (
        "FBRenderer should validate Stage 1 before calling LLM, "
        "to avoid 4× wasted LLM cost on upstream schema bugs"
    )


# ---------------------------------------------------------------------------
# Word count warning
# ---------------------------------------------------------------------------


def test_render_warns_when_body_below_min(caplog):
    short_body = "太短" * 10  # 20 chars, below 800 min
    with patch("agents.brook.fb_renderer.ask_multi", return_value=short_body):
        with caplog.at_level(logging.WARNING, logger="nakama.brook.fb_renderer"):
            _renderer(word_count_min=800).render(_make_stage1_result(), _make_metadata())

    # All 4 tonals are below min, so 4 warnings expected
    warnings = [r for r in caplog.records if r.levelno == logging.WARNING]
    assert len(warnings) == 4, f"expected 4 word-count warnings, got {len(warnings)}"
    for record in warnings:
        assert "below minimum" in record.getMessage()


def test_render_warns_when_body_above_max(caplog):
    long_body = "太長" * 2000  # 4000 chars, above 3500 max
    with patch("agents.brook.fb_renderer.ask_multi", return_value=long_body):
        with caplog.at_level(logging.WARNING, logger="nakama.brook.fb_renderer"):
            _renderer(word_count_max=3500).render(_make_stage1_result(), _make_metadata())

    warnings = [r for r in caplog.records if r.levelno == logging.WARNING]
    assert len(warnings) == 4
    for record in warnings:
        assert "exceeds maximum" in record.getMessage()


def test_render_warning_includes_tonal_label(caplog):
    """Warning message must identify which tonal was off-target (debug aid)."""
    short_body = "x" * 10
    with patch("agents.brook.fb_renderer.ask_multi", return_value=short_body):
        with caplog.at_level(logging.WARNING, logger="nakama.brook.fb_renderer"):
            _renderer(word_count_min=800).render(_make_stage1_result(), _make_metadata())

    warning_text = " ".join(r.getMessage() for r in caplog.records)
    for tonal in FB_TONALS:
        assert f"tonal={tonal}" in warning_text, (
            f"warning must mention tonal={tonal} for debugging which variant misbehaved"
        )


# ---------------------------------------------------------------------------
# Profile loading default
# ---------------------------------------------------------------------------


def test_render_loads_fb_post_profile_by_default():
    """When no style_profile passed, FBRenderer loads fb-post via style_profile_loader."""
    with patch("agents.brook.fb_renderer.load_style_profile") as mock_loader:
        mock_loader.return_value = _stub_profile()
        FBRenderer()

    mock_loader.assert_called_once_with("fb-post")


# ---------------------------------------------------------------------------
# Sub-scenario routing (Line 1 always 訪談宣傳)
# ---------------------------------------------------------------------------


def test_render_prompt_targets_interview_subscenario():
    """Line 1 podcast EP repurpose always goes through 子場景 A 訪談宣傳.

    Profile body contains 3 子場景 (A 訪談 / B 個人 / C 嘉賓側寫); the prompt
    must explicitly direct the LLM to A so it doesn't drift to B/C.
    """
    with patch("agents.brook.fb_renderer.ask_multi", return_value=_FAKE_FB_BODY) as mock_llm:
        _renderer().render(_make_stage1_result(), _make_metadata())

    for call in mock_llm.call_args_list:
        prompt = call.args[0][0]["content"]
        assert "子場景 A 訪談宣傳" in prompt, (
            "prompt must explicitly route to 子場景 A 訪談宣傳 for Line 1 podcast"
        )
