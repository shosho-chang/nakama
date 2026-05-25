"""9 scenarios per ADR-032 §Acceptance Criteria — chinese_normalizer PR-2."""

from agents.foundry.chinese_normalizer import (
    enforce_taiwan_quotes,
    normalize_numbers,
    normalize_punctuation,
    smart_join_cues,
)

# ── normalize_punctuation ────────────────────────────────────────────────────


def test_normalize_punctuation_full_width_comma_unchanged():
    """Full-width ， already canonical; half-width , promoted (space preserved)."""
    result = normalize_punctuation("你好，world, test")
    assert result == "你好，world， test"


def test_normalize_punctuation_question_mark_half_to_full():
    """Half-width ? and full-width ？ both end up as ？."""
    result = normalize_punctuation("真的? 真的？")
    assert result == "真的？ 真的？"


# ── normalize_numbers ────────────────────────────────────────────────────────


def test_normalize_numbers_chinese_wan_qian():
    """一萬一千 → 11000 (scenario 3)."""
    assert normalize_numbers("研究追蹤一萬一千名受試者") == "研究追蹤11000名受試者"


def test_normalize_numbers_comma_separated():
    """11,000 → 11000 (scenario 4)."""
    assert normalize_numbers("共11,000名參與者") == "共11000名參與者"


def test_normalize_numbers_chinese_decimal_wan():
    """一·一萬 → 11000 (scenario 5)."""
    assert normalize_numbers("共一·一萬名參與者") == "共11000名參與者"


def test_normalize_numbers_already_canonical():
    """Pure 11000 stays unchanged (scenario 6)."""
    assert normalize_numbers("共11000名參與者") == "共11000名參與者"


# ── smart_join_cues ──────────────────────────────────────────────────────────


def test_smart_join_cues_mid_sentence_uses_full_width_space():
    """Cues split mid-sentence (no 。？！ at end) joined with full-width space (scenario 7)."""
    result = smart_join_cues(["你好", "世界"])
    assert result == "你好　世界"


def test_smart_join_cues_sentence_end_no_separator():
    """Cues ending in 。 kept as contiguous sentences without extra separator (scenario 8)."""
    result = smart_join_cues(["你好。", "世界。"])
    assert result == "你好。世界。"


# ── enforce_taiwan_quotes ────────────────────────────────────────────────────


def test_enforce_taiwan_quotes_western_to_taiwan(caplog):
    """Western " " rewritten to 「 」 with warning logged (scenario 9)."""
    import logging

    with caplog.at_level(logging.WARNING, logger="agents.foundry.chinese_normalizer"):
        result = enforce_taiwan_quotes("“hello”")  # "hello"
    assert result == "「hello」"
    assert caplog.records, "expected warning log for replaced quotes"
