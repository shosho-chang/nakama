"""Unit tests for `agents.brook.script_video.silence_detection` parser.

ADR-038 §D5 PR-C. Parser is pure — these tests use fixture stderr strings and
do NOT invoke real ffmpeg.
"""

from __future__ import annotations

import pytest

from agents.brook.script_video.silence_detection import (
    parse_silencedetect_stderr,
)

# Realistic ffmpeg silencedetect emission (formatting varies across versions;
# tests cover the canonical " | " separator form).
_FIXTURE_NORMAL = """\
[silencedetect @ 0x7f8a0d004400] silence_start: 5.123
[silencedetect @ 0x7f8a0d004400] silence_end: 7.890 | silence_duration: 2.767
[silencedetect @ 0x7f8a0d004400] silence_start: 15.000
[silencedetect @ 0x7f8a0d004400] silence_end: 16.500 | silence_duration: 1.500
"""


def test_parser_inverts_silences_into_speaking_spans():
    spans = parse_silencedetect_stderr(_FIXTURE_NORMAL, total_duration_s=30.0)
    assert spans == [
        (0.0, 5.123),
        (7.890, 15.000),
        (16.500, 30.0),
    ]


def test_parser_handles_empty_stderr():
    # No silences detected → single speaking span over the whole clip.
    spans = parse_silencedetect_stderr(
        "ffmpeg version 6.0\nno silencedetect lines\n", total_duration_s=12.5
    )
    assert spans == [(0.0, 12.5)]


def test_parser_handles_zero_duration():
    spans = parse_silencedetect_stderr(_FIXTURE_NORMAL, total_duration_s=0.0)
    assert spans == []


def test_parser_handles_negative_duration():
    spans = parse_silencedetect_stderr(_FIXTURE_NORMAL, total_duration_s=-1.0)
    assert spans == []


def test_silence_starts_at_zero():
    stderr = (
        "[silencedetect] silence_start: 0.000\n"
        "[silencedetect] silence_end: 2.000 | silence_duration: 2.000\n"
    )
    spans = parse_silencedetect_stderr(stderr, total_duration_s=10.0)
    # No speaking before 0; one span after.
    assert spans == [(2.0, 10.0)]


def test_trailing_silence_past_eof_is_handled():
    # silence_start emitted with no matching silence_end — ffmpeg behavior when
    # the clip ends mid-silence. Span should terminate at total_duration_s and
    # NOT produce a phantom speaking span after it.
    stderr = (
        "[silencedetect] silence_start: 3.000\n"
        "[silencedetect] silence_end: 4.500 | silence_duration: 1.500\n"
        "[silencedetect] silence_start: 9.500\n"
    )
    spans = parse_silencedetect_stderr(stderr, total_duration_s=10.0)
    assert spans == [(0.0, 3.0), (4.5, 9.5)]


def test_whole_clip_is_silent():
    stderr = (
        "[silencedetect] silence_start: 0.000\n"
        "[silencedetect] silence_end: 10.000 | silence_duration: 10.000\n"
    )
    spans = parse_silencedetect_stderr(stderr, total_duration_s=10.0)
    assert spans == []


def test_silences_clamped_to_clip_duration():
    # silence_end overshoots total — should be clamped.
    stderr = (
        "[silencedetect] silence_start: 3.000\n"
        "[silencedetect] silence_end: 15.000 | silence_duration: 12.000\n"
    )
    spans = parse_silencedetect_stderr(stderr, total_duration_s=10.0)
    assert spans == [(0.0, 3.0)]


def test_malformed_pair_is_skipped():
    # silence_end before silence_start (impossible in well-formed ffmpeg output,
    # but we want defensive behavior).
    stderr = (
        "[silencedetect] silence_start: 5.000\n"
        "[silencedetect] silence_end: 4.000 | silence_duration: -1.000\n"
        "[silencedetect] silence_start: 7.000\n"
        "[silencedetect] silence_end: 8.000 | silence_duration: 1.000\n"
    )
    spans = parse_silencedetect_stderr(stderr, total_duration_s=10.0)
    # First (5.0, 4.0) skipped; second pair honored.
    assert spans == [(0.0, 7.0), (8.0, 10.0)]


def test_decimal_precision_preserved():
    stderr = (
        "[silencedetect] silence_start: 1.234567\n"
        "[silencedetect] silence_end: 2.345678 | silence_duration: 1.111111\n"
    )
    spans = parse_silencedetect_stderr(stderr, total_duration_s=5.0)
    assert spans[0] == pytest.approx((0.0, 1.234567))
    assert spans[1] == pytest.approx((2.345678, 5.0))
