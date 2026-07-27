"""Tests for shared.cam_validate — camera/speaker cross-validation helper (ADR-054 A8)."""

from __future__ import annotations

import pytest

from shared.cam_validate import validate_cam_speaker


def _words(*intervals: tuple[float, float]) -> list[dict]:
    return [{"start": s, "end": e, "word": "x"} for s, e in intervals]


def test_happy_path_expected_speaker_dominates():
    words = _words((0, 1), (1, 2), (2, 3), (3, 4), (4, 5))
    speakers = [0, 0, 0, 1, 0]  # 4/5 = 80% speaker0
    validate_cam_speaker(words, speakers, (0.0, 5.0), expected_speaker=0, threshold=0.6)


def test_fail_loud_when_fraction_below_threshold():
    words = _words((0, 1), (1, 2), (2, 3), (3, 4), (4, 5))
    speakers = [1, 1, 1, 0, 1]  # only 1/5 = 20% speaker0
    with pytest.raises(ValueError, match="director.json cam mapping"):
        validate_cam_speaker(words, speakers, (0.0, 5.0), expected_speaker=0, threshold=0.6)


def test_fail_message_includes_fraction_and_threshold():
    words = _words((0, 1), (1, 2))
    speakers = [1, 1]  # 0% speaker0
    with pytest.raises(ValueError, match="60%"):
        validate_cam_speaker(words, speakers, (0.0, 2.0), expected_speaker=0, threshold=0.6)


def test_window_filters_words_outside_range():
    # Words at 0-5s and 20-25s; window is (0, 10)
    words = _words((0, 1), (1, 2), (2, 3), (20, 21), (21, 22))
    # speaker0 for first 3, speaker1 for last 2 — but last 2 are outside window
    speakers = [0, 0, 0, 1, 1]
    validate_cam_speaker(words, speakers, (0.0, 10.0), expected_speaker=0, threshold=0.9)


def test_no_words_in_window_passes():
    words = _words((30, 31), (32, 33))
    speakers = [1, 1]
    validate_cam_speaker(words, speakers, (0.0, 5.0), expected_speaker=0)


def test_all_unassigned_speakers_passes():
    words = _words((0, 1), (1, 2))
    speakers = [None, None]
    validate_cam_speaker(words, speakers, (0.0, 5.0), expected_speaker=0)


def test_mismatched_lengths_raises():
    words = _words((0, 1), (1, 2))
    with pytest.raises(ValueError, match="same length"):
        validate_cam_speaker(words, [0], (0.0, 5.0), expected_speaker=0)


def test_custom_threshold():
    words = _words((0, 1), (1, 2), (2, 3))
    speakers = [0, 0, 1]  # 2/3 = 66.7%
    # threshold=0.7 → should fail (66.7% < 70%)
    with pytest.raises(ValueError):
        validate_cam_speaker(words, speakers, (0.0, 5.0), expected_speaker=0, threshold=0.7)
    # threshold=0.5 → should pass (66.7% >= 50%)
    validate_cam_speaker(words, speakers, (0.0, 5.0), expected_speaker=0, threshold=0.5)
