"""Tests for agents.brook.blog_renderer.BlogRenderer.

Covers:
- Protocol conformance: BlogRenderer satisfies ChannelRenderer at runtime
- LLM mock test: Stage1Result → ChannelArtifact with blog.md
- Artifact filename and channel name
- Frontmatter completeness: all required keys present
- Body has ≥3 H2 sections (narrative chapter titles)
- Body has ≥1 blockquote (受訪者引述)
- Podcast link appears in artifact content
- Word count warning when outside 988-3954 range
- Golden output structure test: curated Stage 1 JSON → 8 H2 / frontmatter / blockquote
"""

from __future__ import annotations

import logging
import re
from unittest.mock import patch

from agents.brook.blog_renderer import BlogRenderer
from agents.brook.repurpose_engine import (
    ChannelArtifact,
    ChannelRenderer,
    EpisodeMetadata,
    Stage1Result,
)

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

# Golden blog body returned by mocked LLM — 8 H2 sections, blockquotes, podcast link
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

> 「在生命最後的時候，人最需要做的就是這四件事。」——朱為民

這四道，不是儀式，而是一種對話的勇氣。

## 『你終究會發現，每個人都連結在一起。』

這是朱為民在節目裡說的話，也是他多年行醫後最深的感悟。

## 從診間到舞台，他選擇大聲說出「死亡」

> 「我發現很多人在面對死亡的時候都沒有準備，所以我決定要專注在這個領域。」——朱為民

他的 TEDxTaipei 演講、他的著作、他的診間——每一個場合，都是一次對死亡禁忌的正面挑戰。

## 讓善終變成選擇，而不是意外

朱為民持續透過演講、書寫和診間工作，推廣善終觀念，讓更多人在清醒有尊嚴的狀態下告別。

> 「善終就是在清醒有尊嚴的狀態下離開，還能夠跟最重要的人好好道別。」——朱為民

這句話，讓我重新想了好久。

## 如果你也沒想過死，你真的活著嗎？

> 「如果你沒有好好思考過死亡，你就沒有辦法真正地活著。」——朱為民

死亡不是禁忌。是邀請。邀請你好好想想，你真正在乎的是什麼。

## 未完待續的故事

朱為民的故事，還在進行中。每一個他陪伴走過最後一程的病人，都讓他更確信一件事——

我們每個人，都連結在一起。

如果你想聽完他完整的故事，就來聽這集吧。

> 🎙️ 收聽本集 → https://example.com/ep67
"""


def _make_stage1_result() -> Stage1Result:
    return Stage1Result(data=_STAGE1_DATA, source_repr="test SRT[:200]")


def _make_metadata(podcast_url: str = "https://example.com/ep67") -> EpisodeMetadata:
    return EpisodeMetadata(
        slug="dr-chu-ep67",
        host="張修修",
        extra={"guest": "朱為民", "podcast_episode_url": podcast_url},
    )


# ---------------------------------------------------------------------------
# Protocol conformance
# ---------------------------------------------------------------------------


def test_blog_renderer_satisfies_channel_renderer_protocol():
    assert isinstance(BlogRenderer(people_md="stub"), ChannelRenderer)


# ---------------------------------------------------------------------------
# LLM mock test — basic happy path
# ---------------------------------------------------------------------------


def test_render_returns_single_artifact():
    renderer = BlogRenderer(people_md="stub profile")
    stage1 = _make_stage1_result()
    meta = _make_metadata()

    with patch("agents.brook.blog_renderer.ask_multi", return_value=_GOLDEN_BLOG_BODY):
        artifacts = renderer.render(stage1, meta)

    assert isinstance(artifacts, list)
    assert len(artifacts) == 1
    assert isinstance(artifacts[0], ChannelArtifact)


def test_render_artifact_filename_and_channel():
    renderer = BlogRenderer(people_md="stub")
    with patch("agents.brook.blog_renderer.ask_multi", return_value=_GOLDEN_BLOG_BODY):
        artifacts = renderer.render(_make_stage1_result(), _make_metadata())

    artifact = artifacts[0]
    assert artifact.filename == "blog.md"
    assert artifact.channel == "blog"


def test_render_passes_model_sonnet_46():
    renderer = BlogRenderer(people_md="stub")
    with patch("agents.brook.blog_renderer.ask_multi", return_value=_GOLDEN_BLOG_BODY) as mock_llm:
        renderer.render(_make_stage1_result(), _make_metadata())

    call_kwargs = mock_llm.call_args
    assert call_kwargs.kwargs.get("model") == "claude-sonnet-4-6"


# ---------------------------------------------------------------------------
# Frontmatter
# ---------------------------------------------------------------------------


def _parse_frontmatter(content: str) -> dict:
    """Extract YAML frontmatter keys from blog.md content."""
    m = re.match(r"^---\n(.*?)\n---\n", content, re.DOTALL)
    assert m, f"frontmatter block not found in:\n{content[:200]}"
    import yaml

    return yaml.safe_load(m.group(1)) or {}


def test_render_frontmatter_has_all_required_keys():
    renderer = BlogRenderer(people_md="stub")
    with patch("agents.brook.blog_renderer.ask_multi", return_value=_GOLDEN_BLOG_BODY):
        artifacts = renderer.render(_make_stage1_result(), _make_metadata())

    fm = _parse_frontmatter(artifacts[0].content)
    for key in ("title", "meta_description", "category", "tags", "podcast_episode_url"):
        assert key in fm, f"frontmatter missing key: {key}"


def test_render_frontmatter_category_is_people():
    renderer = BlogRenderer(people_md="stub")
    with patch("agents.brook.blog_renderer.ask_multi", return_value=_GOLDEN_BLOG_BODY):
        artifacts = renderer.render(_make_stage1_result(), _make_metadata())

    fm = _parse_frontmatter(artifacts[0].content)
    assert fm["category"] == "people"


def test_render_frontmatter_title_from_stage1():
    renderer = BlogRenderer(people_md="stub")
    with patch("agents.brook.blog_renderer.ask_multi", return_value=_GOLDEN_BLOG_BODY):
        artifacts = renderer.render(_make_stage1_result(), _make_metadata())

    fm = _parse_frontmatter(artifacts[0].content)
    assert fm["title"] == _STAGE1_DATA["title_candidates"][0]


def test_render_frontmatter_meta_description_from_stage1():
    renderer = BlogRenderer(people_md="stub")
    with patch("agents.brook.blog_renderer.ask_multi", return_value=_GOLDEN_BLOG_BODY):
        artifacts = renderer.render(_make_stage1_result(), _make_metadata())

    fm = _parse_frontmatter(artifacts[0].content)
    assert fm["meta_description"] == _STAGE1_DATA["meta_description"]


def test_render_frontmatter_podcast_url_from_metadata():
    renderer = BlogRenderer(people_md="stub")
    url = "https://example.com/ep99"
    with patch("agents.brook.blog_renderer.ask_multi", return_value=_GOLDEN_BLOG_BODY):
        artifacts = renderer.render(_make_stage1_result(), _make_metadata(podcast_url=url))

    fm = _parse_frontmatter(artifacts[0].content)
    assert fm["podcast_episode_url"] == url


# ---------------------------------------------------------------------------
# Body structure
# ---------------------------------------------------------------------------


def _extract_body(content: str) -> str:
    """Return the markdown body after the YAML frontmatter block."""
    m = re.match(r"^---\n.*?\n---\n(.*)", content, re.DOTALL)
    assert m, "could not extract body from content"
    return m.group(1)


def test_render_body_has_h2_sections():
    """Blog body must have ≥3 H2 sections."""
    renderer = BlogRenderer(people_md="stub")
    with patch("agents.brook.blog_renderer.ask_multi", return_value=_GOLDEN_BLOG_BODY):
        artifacts = renderer.render(_make_stage1_result(), _make_metadata())

    body = _extract_body(artifacts[0].content)
    h2_sections = re.findall(r"^##\s+.+", body, re.MULTILINE)
    assert len(h2_sections) >= 3, f"expected ≥3 H2 sections, got {len(h2_sections)}"


def test_render_body_has_blockquote():
    """Blog body must have ≥1 blockquote (受訪者引述)."""
    renderer = BlogRenderer(people_md="stub")
    with patch("agents.brook.blog_renderer.ask_multi", return_value=_GOLDEN_BLOG_BODY):
        artifacts = renderer.render(_make_stage1_result(), _make_metadata())

    body = _extract_body(artifacts[0].content)
    blockquotes = re.findall(r"^>", body, re.MULTILINE)
    assert len(blockquotes) >= 1, "expected ≥1 blockquote in body"


def test_render_content_has_podcast_link():
    """Artifact content must include the podcast episode URL somewhere."""
    renderer = BlogRenderer(people_md="stub")
    url = "https://example.com/ep67"
    with patch("agents.brook.blog_renderer.ask_multi", return_value=_GOLDEN_BLOG_BODY):
        artifacts = renderer.render(_make_stage1_result(), _make_metadata(podcast_url=url))

    assert url in artifacts[0].content


# ---------------------------------------------------------------------------
# Word count warning
# ---------------------------------------------------------------------------


def test_render_warns_when_body_too_short(caplog):
    """Body < 988 chars → log warning."""
    renderer = BlogRenderer(people_md="stub")
    short_body = "很短" * 10  # 20 chars — too short
    with patch("agents.brook.blog_renderer.ask_multi", return_value=short_body):
        with caplog.at_level(logging.WARNING, logger="nakama.brook.blog_renderer"):
            renderer.render(_make_stage1_result(), _make_metadata())

    assert any("word count" in r.message.lower() or "字數" in r.message for r in caplog.records)


def test_render_warns_when_body_too_long(caplog):
    """Body > 3954 chars → log warning."""
    renderer = BlogRenderer(people_md="stub")
    long_body = "這是一個很長的段落。" * 500  # 5000 chars — too long
    with patch("agents.brook.blog_renderer.ask_multi", return_value=long_body):
        with caplog.at_level(logging.WARNING, logger="nakama.brook.blog_renderer"):
            renderer.render(_make_stage1_result(), _make_metadata())

    assert any("word count" in r.message.lower() or "字數" in r.message for r in caplog.records)


def test_render_no_warning_for_normal_length(caplog):
    """Body in 988-3954 range → no word count warning."""
    renderer = BlogRenderer(people_md="stub")
    # _GOLDEN_BLOG_BODY is ~1200 chars — within range
    with patch("agents.brook.blog_renderer.ask_multi", return_value=_GOLDEN_BLOG_BODY):
        with caplog.at_level(logging.WARNING, logger="nakama.brook.blog_renderer"):
            renderer.render(_make_stage1_result(), _make_metadata())

    word_count_warns = [
        r for r in caplog.records if "word count" in r.message.lower() or "字數" in r.message
    ]
    assert not word_count_warns


# ---------------------------------------------------------------------------
# Golden output structure test
# ---------------------------------------------------------------------------


def test_golden_output_8_h2_sections():
    """Curated Stage 1 JSON → golden blog body has 8 H2 sections."""
    renderer = BlogRenderer(people_md="stub profile")
    with patch("agents.brook.blog_renderer.ask_multi", return_value=_GOLDEN_BLOG_BODY):
        artifacts = renderer.render(_make_stage1_result(), _make_metadata())

    body = _extract_body(artifacts[0].content)
    h2_sections = re.findall(r"^##\s+.+", body, re.MULTILINE)
    assert len(h2_sections) >= 8, (
        f"golden body should have ≥8 H2 sections, got {len(h2_sections)}: " + str(h2_sections)
    )


def test_golden_output_has_gold_quote_h2():
    """Blog body has a gold quote H2 matching pattern ## 『...』."""
    renderer = BlogRenderer(people_md="stub")
    with patch("agents.brook.blog_renderer.ask_multi", return_value=_GOLDEN_BLOG_BODY):
        artifacts = renderer.render(_make_stage1_result(), _make_metadata())

    body = _extract_body(artifacts[0].content)
    gold_h2 = re.findall(r"^##\s+『.*?』", body, re.MULTILINE)
    assert gold_h2, "expected gold quote H2 (## 『...』) in body"


def test_golden_output_has_blockquote():
    """Golden body has ≥1 blockquote."""
    renderer = BlogRenderer(people_md="stub")
    with patch("agents.brook.blog_renderer.ask_multi", return_value=_GOLDEN_BLOG_BODY):
        artifacts = renderer.render(_make_stage1_result(), _make_metadata())

    body = _extract_body(artifacts[0].content)
    blockquotes = re.findall(r"^>", body, re.MULTILINE)
    assert len(blockquotes) >= 1


def test_golden_output_frontmatter_complete():
    """Golden output has complete frontmatter with all required keys."""
    renderer = BlogRenderer(people_md="stub")
    with patch("agents.brook.blog_renderer.ask_multi", return_value=_GOLDEN_BLOG_BODY):
        artifacts = renderer.render(_make_stage1_result(), _make_metadata())

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
    """Stage 1 JSON must appear in the LLM messages."""
    renderer = BlogRenderer(people_md="stub")
    with patch("agents.brook.blog_renderer.ask_multi", return_value=_GOLDEN_BLOG_BODY) as mock_llm:
        renderer.render(_make_stage1_result(), _make_metadata())

    messages = mock_llm.call_args.args[0]
    combined = " ".join(m["content"] for m in messages)
    assert "identity_sketch" in combined or "身份速寫" in combined
    assert "turning_point" in combined or "轉折" in combined
