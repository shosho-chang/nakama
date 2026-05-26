"""ADR-033 D3 — parse 5-line thumbnail idea block.

The parser is the seam between LLM brainstorm output (free Markdown) and
the render endpoint (structured composition variables). Panel P3 + Codex §4
flagged that 修修's edits will break naive regex — these tests lock the
permissive parsing contract.
"""

from __future__ import annotations

import pytest

from shared.cutout_library import EmotionLookupError
from shared.thumbnail_idea import (
    IdeaParseError,
    ParsedIdea,
    parse_idea,
    parse_ideas_batch,
)

SAMPLE_FULL = """\
大字：妙用解密
我的表情：surprised
視覺：左罐右腦黃閃電
數字/圖示：⚡
背景：實驗室白底
"""

SAMPLE_ZH_EMOTION = """\
大字：來得及嗎
我的表情：驚訝
視覺：65 歲老人 + MRI 機器
數字/圖示：65
背景：醫院走廊
"""

SAMPLE_ALIAS_EMOTION = """\
大字：每天 5g
我的表情：驚喜
視覺：湯匙 + 化學分子式
數字/圖示：5g
背景：實驗室
"""

SAMPLE_ASCII_COLONS = """\
大字: hello
我的表情: thoughtful
視覺: a brain glows
數字/圖示: 3
背景: dark space
"""

SAMPLE_MISSING_DECORATION = """\
大字：簡單就好
我的表情：思考
視覺：純色背景 + 大字
數字/圖示：無
背景：純黑漸層
"""

SAMPLE_NO_DECORATION_LINE = """\
大字：簡單就好
我的表情：思考
視覺：純色背景 + 大字
背景：純黑漸層
"""

SAMPLE_PROSE_INTERLEAVED = """\
（這是 LLM 的解釋文字，可以保留）

大字：妙用解密
這個構想想要強調的是反差感。
我的表情：surprised
視覺：左罐右腦黃閃電
數字/圖示：⚡
背景：實驗室白底

備註：可以調暗一點。
"""

SAMPLE_BATCH = """\
這是 3 個候選的縮圖 idea：

Idea 1
大字：妙用解密
我的表情：surprised
視覺：左罐右腦黃閃電
數字/圖示：⚡
背景：實驗室白底

Idea 2
大字：來得及嗎
我的表情：驚訝
視覺：65 歲老人 + MRI
數字/圖示：65
背景：醫院走廊

Idea 3
大字：每天 5g
我的表情：thoughtful
視覺：湯匙 + 分子式
數字/圖示：5g
背景：實驗室桌面
"""

SAMPLE_BATCH_DASH_SEPARATOR = """\
大字：a
我的表情：surprised
視覺：a
背景：b

---

大字：c
我的表情：thoughtful
視覺：d
背景：e
"""


def test_parse_canonical_full_block():
    parsed = parse_idea(SAMPLE_FULL)
    assert parsed == ParsedIdea(
        hook="妙用解密",
        emotion_key="surprised",
        emotion_input="surprised",
        visual="左罐右腦黃閃電",
        decoration="⚡",
        bg="實驗室白底",
    )


def test_parse_zh_emotion_resolves_to_english_key():
    parsed = parse_idea(SAMPLE_ZH_EMOTION)
    assert parsed.emotion_key == "surprised"
    assert parsed.emotion_input == "驚訝"


def test_parse_alias_emotion_resolves():
    parsed = parse_idea(SAMPLE_ALIAS_EMOTION)
    assert parsed.emotion_key == "surprised"
    assert parsed.emotion_input == "驚喜"


def test_parse_accepts_ascii_colon():
    parsed = parse_idea(SAMPLE_ASCII_COLONS)
    assert parsed.hook == "hello"
    assert parsed.emotion_key == "thoughtful"
    assert parsed.bg == "dark space"


def test_parse_treats_decoration_无_as_empty():
    parsed = parse_idea(SAMPLE_MISSING_DECORATION)
    assert parsed.decoration == ""


def test_parse_missing_decoration_line_ok():
    """decoration is optional — missing line yields empty string."""
    parsed = parse_idea(SAMPLE_NO_DECORATION_LINE)
    assert parsed.decoration == ""
    assert parsed.bg == "純黑漸層"


def test_parse_tolerates_prose_between_labels():
    parsed = parse_idea(SAMPLE_PROSE_INTERLEAVED)
    assert parsed.hook == "妙用解密"
    assert parsed.bg == "實驗室白底"


def test_parse_missing_hook_raises_with_missing_list():
    bad = """\
我的表情：surprised
視覺：foo
數字/圖示：bar
背景：baz
"""
    with pytest.raises(IdeaParseError) as exc:
        parse_idea(bad)
    assert "hook" in exc.value.missing
    # Other required labels present, so they shouldn't appear
    assert "emotion" not in exc.value.missing
    assert exc.value.raw == bad


def test_parse_missing_multiple_required():
    with pytest.raises(IdeaParseError) as exc:
        parse_idea("just some prose with nothing structured")
    assert set(exc.value.missing) == {"hook", "emotion", "visual", "bg"}


def test_parse_unknown_emotion_bubbles_emotion_lookup_error():
    bad_emotion = """\
大字：x
我的表情：迷茫
視覺：a
背景：b
"""
    with pytest.raises(EmotionLookupError):
        parse_idea(bad_emotion)


def test_parse_batch_idea_n_heading_separator():
    parsed = parse_ideas_batch(SAMPLE_BATCH)
    assert len(parsed) == 3
    assert parsed[0].hook == "妙用解密"
    assert parsed[1].hook == "來得及嗎"
    assert parsed[2].hook == "每天 5g"
    assert parsed[2].emotion_key == "thoughtful"


def test_parse_batch_dash_separator():
    parsed = parse_ideas_batch(SAMPLE_BATCH_DASH_SEPARATOR)
    assert len(parsed) == 2


def test_parse_batch_discards_leading_preamble():
    """LLM might write a sentence before the first 'Idea 1' marker — discard it."""
    with_preamble = "Here are 3 ideas for you:\n\n" + SAMPLE_BATCH
    parsed = parse_ideas_batch(with_preamble)
    assert len(parsed) == 3


def test_parse_batch_single_block_no_separator():
    """A single idea with no separators returns 1 parsed item."""
    parsed = parse_ideas_batch(SAMPLE_FULL)
    assert len(parsed) == 1
