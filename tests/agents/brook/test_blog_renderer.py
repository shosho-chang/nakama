"""Tests for agents.brook.blog_renderer.BlogRenderer.

Covers:
- Protocol conformance: BlogRenderer satisfies ChannelRenderer at runtime
- LLM mock test: Stage1Result → ChannelArtifact with BLOG_FILENAME
- Frontmatter completeness + scalar safety (round-trip with hostile chars)
- Body has ≥3 H2 sections + ≥1 blockquote + podcast link
- Word count warning matches StyleProfile bounds (config/style-profiles/people.yaml)
- Defensive Stage 1 dict access (typed errors on missing/empty fields)
- Golden output structure: 8 H2 / frontmatter / blockquote / trailing newline
"""

from __future__ import annotations

import logging
import re
from unittest.mock import patch

import pytest
import yaml

from agents.brook.blog_renderer import BlogRenderer
from agents.brook.repurpose_engine import (
    BLOG_FILENAME,
    ChannelArtifact,
    ChannelRenderer,
    EpisodeMetadata,
    Stage1Result,
)
from agents.brook.style_profile_loader import StyleProfile

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

_STAGE1_DATA = {
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
        "🧠不正常人類研究所 EP67｜朱為民：安寧醫師教你如何好好告別，才能真正地活著",
        "🧠不正常人類研究所 EP67｜朱為民醫師：四道人生，善終是一種選擇",
        "🧠不正常人類研究所 EP67｜朱為民：每個人都連結在一起，死亡不是終點",
    ],
    "meta_description": (
        "台中榮總安寧醫師朱為民，因父親驟逝踏入生死醫學，用「四道人生」陪伴無數人好好道別。"
        "本集不正常人類研究所，修修與朱醫師聊善終、死亡禁忌與真正活著的意義。立即收聽！"
    ),
    "episode_type": "narrative_journey",
}

_GOLDEN_BLOG_BODY = """\
『死亡』是一件華人社會避之唯恐不及的話題。孔子說：「未知生，焉知死」，但我始終認為，若沒有去好好了解並且思考「死亡」這件事情，我就不能真正的「活著」。

今天的來賓——台中榮總家庭醫學科主治醫師朱為民醫師，他不但是 TEDxTaipei 的講者，
還是一位知名作家，更是台灣安寧緩和醫學的推動者。他到底是怎麼走上這條路的？

## 從醫學院到病榻旁，死亡成為他的老師

朱為民在醫學院求學期間，父親突然過世了。那一次猝不及防的死亡，讓他意識到生命的脆弱與無常。

那段與死亡相遇的日子，讓他開始深刻思考一件事：為什麼我們從來不談「死」？

## 那一刻，他做了一個讓人意外的選擇

目睹身邊許多病人和家屬在面對死亡時毫無準備，朱為民做了一個決定——把自己的職業生涯，投入到幫助人好好道別的工作上。

他加入台中榮總的安寧緩和團隊，從此走進了很多家庭最難開口的時刻。

## 四道人生：道謝、道歉、道愛、道別

他把多年的臨床觀察整理成「四道人生」，一個幫助病人與家屬走過生命終點的具體工具。

>「在生命最後的時候，人最需要做的就是這四件事。」（EP67 朱為民）

這四道，不是儀式，而是一種對話的勇氣。

## 『你終究會發現，每個人都連結在一起。』

這是朱為民在節目裡說的話，也是他多年行醫後最深的感悟。

## 從診間到舞台，他選擇大聲說出「死亡」

>「我發現很多人在面對死亡的時候都沒有準備，所以我決定要專注在這個領域。」（EP67 朱為民）

他的 TEDxTaipei 演講、他的著作、他的診間——每一個場合，都是一次對死亡禁忌的正面挑戰。

## 讓善終變成選擇，而不是意外

朱為民持續透過演講、書寫和診間工作，推廣善終觀念，讓更多人在清醒有尊嚴的狀態下告別。

>「善終就是在清醒有尊嚴的狀態下離開，還能夠跟最重要的人好好道別。」（EP67 朱為民）

這句話，讓我重新想了好久。

## 如果你也沒想過死，你真的活著嗎？

>「如果你沒有好好思考過死亡，你就沒有辦法真正地活著。」（EP67 朱為民）

死亡不是禁忌。是邀請。邀請你好好想想，你真正在乎的是什麼。

## 未完待續的故事

朱為民的故事，還在進行中。每一個他陪伴走過最後一程的病人，都讓他更確信一件事——

我們每個人，都連結在一起。

如果你想聽完他完整的故事，就來聽這集吧。

> 🎙️ 收聽本集 → https://example.com/ep67
"""


def _stub_profile(
    *,
    body: str = "stub profile",
    word_count_min: int = 1000,
    word_count_max: int = 4000,
    primary_category: str = "people",
    tags: tuple[str, ...] = ("people", "podcast", "interview"),
) -> StyleProfile:
    return StyleProfile(
        profile_id="people@0.1.0-test",
        category="people",
        primary_category=primary_category,
        body=body,
        word_count_min=word_count_min,
        word_count_max=word_count_max,
        forbid_emoji=False,
        default_tag_hints=tags,
        detect_keywords=("podcast", "EP"),
    )


def _make_stage1_result(data: dict | None = None) -> Stage1Result:
    return Stage1Result(
        data=data if data is not None else _STAGE1_DATA, source_repr="<srt 200 chars>"
    )


def _make_metadata(podcast_url: str = "https://example.com/ep67") -> EpisodeMetadata:
    return EpisodeMetadata(
        slug="dr-chu-ep67",
        host="張修修",
        extra={"guest": "朱為民", "podcast_episode_url": podcast_url},
    )


def _renderer() -> BlogRenderer:
    return BlogRenderer(style_profile=_stub_profile())


# ---------------------------------------------------------------------------
# Protocol conformance
# ---------------------------------------------------------------------------


def test_blog_renderer_satisfies_channel_renderer_protocol():
    assert isinstance(_renderer(), ChannelRenderer)


# ---------------------------------------------------------------------------
# LLM mock test — basic happy path
# ---------------------------------------------------------------------------


def test_render_returns_single_artifact():
    with patch("agents.brook.blog_renderer.ask_multi", return_value=_GOLDEN_BLOG_BODY):
        artifacts = _renderer().render(_make_stage1_result(), _make_metadata())

    assert isinstance(artifacts, list)
    assert len(artifacts) == 1
    assert isinstance(artifacts[0], ChannelArtifact)


def test_render_artifact_uses_BLOG_FILENAME_constant():
    with patch("agents.brook.blog_renderer.ask_multi", return_value=_GOLDEN_BLOG_BODY):
        artifacts = _renderer().render(_make_stage1_result(), _make_metadata())

    artifact = artifacts[0]
    assert artifact.filename == BLOG_FILENAME, (
        "BlogRenderer must use BLOG_FILENAME constant, not hardcoded 'blog.md'"
    )
    assert artifact.channel == "blog"


def test_render_passes_model_sonnet_46_by_default():
    with patch("agents.brook.blog_renderer.ask_multi", return_value=_GOLDEN_BLOG_BODY) as mock_llm:
        _renderer().render(_make_stage1_result(), _make_metadata())

    call_kwargs = mock_llm.call_args
    assert call_kwargs.kwargs.get("model") == "claude-sonnet-4-6"


def test_render_accepts_model_override():
    """Caller can inject a different model for testing or A/B."""
    renderer = BlogRenderer(style_profile=_stub_profile(), model="claude-opus-4-7")
    with patch("agents.brook.blog_renderer.ask_multi", return_value=_GOLDEN_BLOG_BODY) as mock_llm:
        renderer.render(_make_stage1_result(), _make_metadata())

    assert mock_llm.call_args.kwargs.get("model") == "claude-opus-4-7"


# ---------------------------------------------------------------------------
# Frontmatter
# ---------------------------------------------------------------------------


def _parse_frontmatter(content: str) -> dict:
    """Extract YAML frontmatter via yaml.safe_load round-trip."""
    m = re.match(r"^---\n(.*?)\n---\n", content, re.DOTALL)
    assert m, f"frontmatter block not found in:\n{content[:200]}"
    return yaml.safe_load(m.group(1)) or {}


def test_render_frontmatter_has_all_required_keys():
    with patch("agents.brook.blog_renderer.ask_multi", return_value=_GOLDEN_BLOG_BODY):
        artifacts = _renderer().render(_make_stage1_result(), _make_metadata())

    fm = _parse_frontmatter(artifacts[0].content)
    for key in ("title", "meta_description", "category", "tags", "podcast_episode_url"):
        assert key in fm, f"frontmatter missing key: {key}"


def test_render_frontmatter_category_from_profile():
    """category sourced from StyleProfile.primary_category, not hardcoded."""
    profile = _stub_profile(primary_category="custom-cat")
    renderer = BlogRenderer(style_profile=profile)
    with patch("agents.brook.blog_renderer.ask_multi", return_value=_GOLDEN_BLOG_BODY):
        artifacts = renderer.render(_make_stage1_result(), _make_metadata())

    fm = _parse_frontmatter(artifacts[0].content)
    assert fm["category"] == "custom-cat"


def test_render_frontmatter_tags_from_profile():
    """tags sourced from StyleProfile.default_tag_hints (no hardcoded ['people','podcast'])."""
    profile = _stub_profile(tags=("alpha", "beta", "gamma"))
    renderer = BlogRenderer(style_profile=profile)
    with patch("agents.brook.blog_renderer.ask_multi", return_value=_GOLDEN_BLOG_BODY):
        artifacts = renderer.render(_make_stage1_result(), _make_metadata())

    fm = _parse_frontmatter(artifacts[0].content)
    assert fm["tags"] == ["alpha", "beta", "gamma"]


def test_render_frontmatter_title_from_stage1():
    with patch("agents.brook.blog_renderer.ask_multi", return_value=_GOLDEN_BLOG_BODY):
        artifacts = _renderer().render(_make_stage1_result(), _make_metadata())

    fm = _parse_frontmatter(artifacts[0].content)
    assert fm["title"] == _STAGE1_DATA["title_candidates"][0]


def test_render_frontmatter_meta_description_from_stage1():
    with patch("agents.brook.blog_renderer.ask_multi", return_value=_GOLDEN_BLOG_BODY):
        artifacts = _renderer().render(_make_stage1_result(), _make_metadata())

    fm = _parse_frontmatter(artifacts[0].content)
    assert fm["meta_description"] == _STAGE1_DATA["meta_description"]


def test_render_frontmatter_podcast_url_from_metadata():
    url = "https://example.com/ep99"
    with patch("agents.brook.blog_renderer.ask_multi", return_value=_GOLDEN_BLOG_BODY):
        artifacts = _renderer().render(_make_stage1_result(), _make_metadata(podcast_url=url))

    fm = _parse_frontmatter(artifacts[0].content)
    assert fm["podcast_episode_url"] == url


# ---------------------------------------------------------------------------
# YAML scalar safety (per feedback_yaml_scalar_safety.md)
# ---------------------------------------------------------------------------


def test_render_frontmatter_round_trips_hostile_title():
    """Title with `:`, `"`, `『』`, em-dash should round-trip via yaml.safe_load."""
    hostile_title = '朱醫師: "活著"的意義 — 一個『不正常』的選擇'
    data = {**_STAGE1_DATA, "title_candidates": [hostile_title, "filler 2", "filler 3"]}
    with patch("agents.brook.blog_renderer.ask_multi", return_value=_GOLDEN_BLOG_BODY):
        artifacts = _renderer().render(_make_stage1_result(data), _make_metadata())

    fm = _parse_frontmatter(artifacts[0].content)
    assert fm["title"] == hostile_title


def test_render_frontmatter_collapses_newlines_in_meta_description():
    """meta_description with `\\n` must be collapsed (per feedback_yaml_scalar_safety.md)."""
    multi_line_desc = '第一行\n第二行：含冒號\r\n第三行 "含引號" 收尾'
    data = {**_STAGE1_DATA, "meta_description": multi_line_desc}
    with patch("agents.brook.blog_renderer.ask_multi", return_value=_GOLDEN_BLOG_BODY):
        artifacts = _renderer().render(_make_stage1_result(data), _make_metadata())

    fm = _parse_frontmatter(artifacts[0].content)
    # All newlines collapsed to single spaces
    assert "\n" not in fm["meta_description"]
    assert "\r" not in fm["meta_description"]
    # Content semantically preserved
    assert "第一行" in fm["meta_description"]
    assert "第二行" in fm["meta_description"]
    assert "第三行" in fm["meta_description"]


# ---------------------------------------------------------------------------
# Defensive Stage 1 dict access
# ---------------------------------------------------------------------------


def test_render_raises_typed_error_when_title_candidates_missing():
    data = {k: v for k, v in _STAGE1_DATA.items() if k != "title_candidates"}
    with pytest.raises(ValueError, match="title_candidates"):
        with patch("agents.brook.blog_renderer.ask_multi", return_value=_GOLDEN_BLOG_BODY):
            _renderer().render(_make_stage1_result(data), _make_metadata())


def test_render_raises_typed_error_when_title_candidates_empty():
    data = {**_STAGE1_DATA, "title_candidates": []}
    with pytest.raises(ValueError, match="title_candidates"):
        with patch("agents.brook.blog_renderer.ask_multi", return_value=_GOLDEN_BLOG_BODY):
            _renderer().render(_make_stage1_result(data), _make_metadata())


def test_render_raises_typed_error_when_meta_description_missing():
    data = {k: v for k, v in _STAGE1_DATA.items() if k != "meta_description"}
    with pytest.raises(ValueError, match="meta_description"):
        with patch("agents.brook.blog_renderer.ask_multi", return_value=_GOLDEN_BLOG_BODY):
            _renderer().render(_make_stage1_result(data), _make_metadata())


# ---------------------------------------------------------------------------
# Body structure
# ---------------------------------------------------------------------------


def _extract_body(content: str) -> str:
    m = re.match(r"^---\n.*?\n---\n(.*)", content, re.DOTALL)
    assert m, "could not extract body from content"
    return m.group(1)


def test_render_body_has_h2_sections():
    with patch("agents.brook.blog_renderer.ask_multi", return_value=_GOLDEN_BLOG_BODY):
        artifacts = _renderer().render(_make_stage1_result(), _make_metadata())

    body = _extract_body(artifacts[0].content)
    h2_sections = re.findall(r"^##\s+.+", body, re.MULTILINE)
    assert len(h2_sections) >= 3


def test_render_body_has_blockquote():
    with patch("agents.brook.blog_renderer.ask_multi", return_value=_GOLDEN_BLOG_BODY):
        artifacts = _renderer().render(_make_stage1_result(), _make_metadata())

    body = _extract_body(artifacts[0].content)
    blockquotes = re.findall(r"^>", body, re.MULTILINE)
    assert len(blockquotes) >= 1


def test_render_content_has_podcast_link():
    url = "https://example.com/ep67"
    with patch("agents.brook.blog_renderer.ask_multi", return_value=_GOLDEN_BLOG_BODY):
        artifacts = _renderer().render(_make_stage1_result(), _make_metadata(podcast_url=url))

    assert url in artifacts[0].content


def test_render_content_ends_with_trailing_newline():
    """blog.md should end with `\\n` per POSIX text-file convention."""
    with patch("agents.brook.blog_renderer.ask_multi", return_value="lorem ipsum dolor"):
        artifacts = _renderer().render(_make_stage1_result(), _make_metadata())

    assert artifacts[0].content.endswith("\n")


# ---------------------------------------------------------------------------
# Word count warning — bounds from StyleProfile
# ---------------------------------------------------------------------------


def test_render_warns_when_body_below_profile_minimum(caplog):
    """Body shorter than profile.word_count_min → warning."""
    profile = _stub_profile(word_count_min=1000, word_count_max=4000)
    renderer = BlogRenderer(style_profile=profile)
    short_body = "很短" * 10  # 20 chars
    with patch("agents.brook.blog_renderer.ask_multi", return_value=short_body):
        with caplog.at_level(logging.WARNING, logger="nakama.brook.blog_renderer"):
            renderer.render(_make_stage1_result(), _make_metadata())

    assert any("word count" in r.message.lower() or "字數" in r.message for r in caplog.records)


def test_render_warns_when_body_above_profile_maximum(caplog):
    """Body longer than profile.word_count_max → warning."""
    profile = _stub_profile(word_count_min=1000, word_count_max=4000)
    renderer = BlogRenderer(style_profile=profile)
    long_body = "這是一個很長的段落。" * 500  # ~5000 chars
    with patch("agents.brook.blog_renderer.ask_multi", return_value=long_body):
        with caplog.at_level(logging.WARNING, logger="nakama.brook.blog_renderer"):
            renderer.render(_make_stage1_result(), _make_metadata())

    assert any("word count" in r.message.lower() or "字數" in r.message for r in caplog.records)


def test_render_no_warning_for_normal_length(caplog):
    """Body in range → no word count warning."""
    profile = _stub_profile(word_count_min=500, word_count_max=4000)
    renderer = BlogRenderer(style_profile=profile)
    with patch("agents.brook.blog_renderer.ask_multi", return_value=_GOLDEN_BLOG_BODY):
        with caplog.at_level(logging.WARNING, logger="nakama.brook.blog_renderer"):
            renderer.render(_make_stage1_result(), _make_metadata())

    word_count_warns = [
        r for r in caplog.records if "word count" in r.message.lower() or "字數" in r.message
    ]
    assert not word_count_warns


def test_word_count_bounds_pulled_from_profile_not_hardcoded():
    """Profile bounds must drive the validator — change profile → bounds change."""
    tight_profile = _stub_profile(word_count_min=10000, word_count_max=20000)
    renderer = BlogRenderer(style_profile=tight_profile)
    # _GOLDEN_BLOG_BODY is ~1200 chars — far below tight_profile.min=10000
    import logging as _logging

    caplog_handler = _logging.getLogger("nakama.brook.blog_renderer")
    triggered = []
    handler = _logging.Handler()
    handler.emit = lambda record: triggered.append(record.getMessage())
    caplog_handler.addHandler(handler)
    try:
        with patch("agents.brook.blog_renderer.ask_multi", return_value=_GOLDEN_BLOG_BODY):
            renderer.render(_make_stage1_result(), _make_metadata())
    finally:
        caplog_handler.removeHandler(handler)

    assert any("10000" in m for m in triggered), (
        "warning must reference profile-derived minimum 10000, not a hardcoded value"
    )


# ---------------------------------------------------------------------------
# Golden output structure
# ---------------------------------------------------------------------------


def test_golden_output_8_h2_sections():
    with patch("agents.brook.blog_renderer.ask_multi", return_value=_GOLDEN_BLOG_BODY):
        artifacts = _renderer().render(_make_stage1_result(), _make_metadata())

    body = _extract_body(artifacts[0].content)
    h2_sections = re.findall(r"^##\s+.+", body, re.MULTILINE)
    assert len(h2_sections) >= 8


def test_golden_output_has_gold_quote_h2():
    with patch("agents.brook.blog_renderer.ask_multi", return_value=_GOLDEN_BLOG_BODY):
        artifacts = _renderer().render(_make_stage1_result(), _make_metadata())

    body = _extract_body(artifacts[0].content)
    gold_h2 = re.findall(r"^##\s+『.*?』", body, re.MULTILINE)
    assert gold_h2


def test_golden_output_has_blockquote():
    with patch("agents.brook.blog_renderer.ask_multi", return_value=_GOLDEN_BLOG_BODY):
        artifacts = _renderer().render(_make_stage1_result(), _make_metadata())

    body = _extract_body(artifacts[0].content)
    blockquotes = re.findall(r"^>", body, re.MULTILINE)
    assert len(blockquotes) >= 1


def test_golden_output_frontmatter_complete():
    with patch("agents.brook.blog_renderer.ask_multi", return_value=_GOLDEN_BLOG_BODY):
        artifacts = _renderer().render(_make_stage1_result(), _make_metadata())

    fm = _parse_frontmatter(artifacts[0].content)
    assert fm["title"]
    assert fm["meta_description"]
    assert fm["category"]
    assert fm["tags"]
    assert "podcast_episode_url" in fm


# ---------------------------------------------------------------------------
# Stage 1 data injected into LLM prompt
# ---------------------------------------------------------------------------


def test_stage1_data_in_llm_prompt():
    with patch("agents.brook.blog_renderer.ask_multi", return_value=_GOLDEN_BLOG_BODY) as mock_llm:
        _renderer().render(_make_stage1_result(), _make_metadata())

    messages = mock_llm.call_args.args[0]
    combined = " ".join(m["content"] for m in messages)
    assert "identity_sketch" in combined or "身份速寫" in combined
    assert "turning_point" in combined or "轉折" in combined


def test_blockquote_format_instruction_in_prompt():
    """Prompt must teach the LLM the people.md blockquote convention `（EP## 姓名）`."""
    with patch("agents.brook.blog_renderer.ask_multi", return_value=_GOLDEN_BLOG_BODY) as mock_llm:
        _renderer().render(_make_stage1_result(), _make_metadata())

    messages = mock_llm.call_args.args[0]
    combined = " ".join(m["content"] for m in messages)
    assert "EP##" in combined or "EP" in combined
    assert "（" in combined  # full-width parens in convention example
