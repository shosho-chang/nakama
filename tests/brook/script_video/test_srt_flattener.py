"""Tests for srt_flattener PR-2."""

from agents.brook.script_video.srt_flattener import SRTCue, flatten_cues, parse_srt

SRT_FIXTURE = """\
1
00:00:01,000 --> 00:00:03,000
你好

2
00:00:04,000 --> 00:00:06,500
世界
"""


def test_parse_srt_returns_correct_cues():
    cues = parse_srt(SRT_FIXTURE)
    assert len(cues) == 2
    assert cues[0].index == 1
    assert cues[0].start == 1.0
    assert cues[0].end == 3.0
    assert cues[0].text == "你好"
    assert cues[1].index == 2
    assert cues[1].start == 4.0
    assert cues[1].end == 6.5


def test_char_to_time_round_trips():
    """Every char in flat_text maps to the correct cue start time."""
    cues = [
        SRTCue(1, 1.0, 2.0, "hello"),
        SRTCue(2, 5.0, 7.0, "world"),
    ]
    flat_text, char_to_time, _ = flatten_cues(cues)

    # "hello" does not end in 。？！ so a full-width space separator is inserted
    assert flat_text == "hello　world"

    # All 5 chars of "hello" map to cue 1 start time
    for i in range(5):
        assert char_to_time[i] == 1.0, f"char {i} should map to 1.0"

    # full-width space (pos 5) maps to cue 2 start time (separator attributed to next cue)
    assert char_to_time[5] == 5.0

    # All 5 chars of "world" map to cue 2 start time
    for i in range(6, 11):
        assert char_to_time[i] == 5.0, f"char {i} should map to 5.0"


def test_multi_cue_concatenation_preserves_timestamps():
    """srt_line_id_to_char_range correctly covers each cue's text slice."""
    cues = [
        SRTCue(1, 0.0, 2.0, "你好"),
        SRTCue(2, 3.0, 5.0, "世界"),
    ]
    flat_text, char_to_time, srt_line_id_to_char_range = flatten_cues(cues)

    # "你好" (2 chars): positions 0-1
    start1, end1 = srt_line_id_to_char_range[1]
    assert flat_text[start1:end1] == "你好"
    assert all(char_to_time[p] == 0.0 for p in range(start1, end1))

    # "世界" (2 chars): positions 3-4 (sep at pos 2)
    start2, end2 = srt_line_id_to_char_range[2]
    assert flat_text[start2:end2] == "世界"
    assert all(char_to_time[p] == 3.0 for p in range(start2, end2))


def test_sentence_boundary_no_separator():
    """Cues ending in 。 produce no separator between them."""
    cues = [
        SRTCue(1, 0.0, 1.0, "你好。"),
        SRTCue(2, 2.0, 3.0, "世界。"),
    ]
    flat_text, _, _ = flatten_cues(cues)
    assert flat_text == "你好。世界。"
    assert "　" not in flat_text
