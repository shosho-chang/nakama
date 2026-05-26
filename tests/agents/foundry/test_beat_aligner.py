"""Tests for beat_aligner PR-2."""

import pytest

from agents.foundry.beat_aligner import AnchorNotFoundError, align_beat
from agents.foundry.srt_flattener import SRTCue, flatten_cues


def _make_char_to_time(text: str, start_time: float = 0.0) -> dict[int, float]:
    return {i: start_time for i in range(len(text))}


def test_happy_path_anchor_found_returns_timing():
    """Anchor found verbatim → timing returned with correct start time."""
    flat_text = "研究追蹤11000名受試者，今天天氣很好。"
    char_to_time = _make_char_to_time(flat_text, 2.5)
    beat = {"start_quote": "研究追蹤", "end_quote": "受試者"}

    result = align_beat(beat, flat_text, char_to_time)

    assert result["timing"]["start"] == 2.5
    assert result["timing"]["duration"] >= 0.0
    assert "srt_line_ids" in result
    assert result["srt_line_ids"] == []  # no srt_line_id_to_char_range supplied


def test_miss_path_raises_anchor_not_found_error():
    """start_quote not in text → AnchorNotFoundError (not None, not warning)."""
    flat_text = "你好世界"
    char_to_time = _make_char_to_time(flat_text)
    beat = {"start_quote": "不存在的錨點", "end_quote": "世界"}

    with pytest.raises(AnchorNotFoundError) as exc_info:
        align_beat(beat, flat_text, char_to_time)

    assert exc_info.value.beat is beat
    assert exc_info.value.flat_text == flat_text


def test_miss_on_end_quote_also_raises():
    """end_quote not in text → AnchorNotFoundError."""
    flat_text = "你好世界"
    char_to_time = _make_char_to_time(flat_text)
    beat = {"start_quote": "你好", "end_quote": "不存在"}

    with pytest.raises(AnchorNotFoundError):
        align_beat(beat, flat_text, char_to_time)


def test_srt_line_ids_populated_for_multi_cue_beat():
    """Beat spanning two cues includes both cue IDs in srt_line_ids."""
    cues = [
        SRTCue(1, 0.0, 2.0, "你好"),
        SRTCue(2, 3.0, 5.0, "世界"),
    ]
    flat_text, char_to_time, srt_line_id_to_char_range = flatten_cues(cues)
    # flat_text = "你好　世界" (sep at pos 2)

    beat = {"start_quote": "你好", "end_quote": "世界"}
    result = align_beat(beat, flat_text, char_to_time, srt_line_id_to_char_range)

    assert 1 in result["srt_line_ids"]
    assert 2 in result["srt_line_ids"]


def test_srt_line_ids_single_cue_beat():
    """Beat fully within one cue → only that cue ID in srt_line_ids."""
    cues = [
        SRTCue(1, 0.0, 2.0, "你好世界"),
        SRTCue(2, 3.0, 5.0, "再見"),
    ]
    flat_text, char_to_time, srt_line_id_to_char_range = flatten_cues(cues)

    beat = {"start_quote": "你好", "end_quote": "世界"}
    result = align_beat(beat, flat_text, char_to_time, srt_line_id_to_char_range)

    assert result["srt_line_ids"] == [1]


def test_diagnostic_fuzzy_still_raises_on_miss():
    """diagnostic_fuzzy=True logs candidates but still raises AnchorNotFoundError."""
    flat_text = "你好世界"
    char_to_time = _make_char_to_time(flat_text)
    beat = {"start_quote": "你哈", "end_quote": "世界"}  # typo in start_quote

    with pytest.raises(AnchorNotFoundError):
        align_beat(beat, flat_text, char_to_time, diagnostic_fuzzy=True)
