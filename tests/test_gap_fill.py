"""run_gap_fill 純函數測試（偵測邏輯，不碰 GPU）。"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

from run_gap_fill import MIN_GAP_SEC, _parse_srt, _ts, detect_gaps


def test_detect_gaps_basic():
    cues = [(0.0, 5.0, "a"), (5.5, 10.0, "b"), (20.0, 25.0, "c")]
    assert detect_gaps(cues, 25.0) == [(10.0, 20.0)]


def test_detect_gaps_head_and_tail():
    cues = [(10.0, 12.0, "a")]
    assert detect_gaps(cues, 30.0) == [(0.0, 10.0), (12.0, 30.0)]


def test_detect_gaps_threshold():
    cues = [(0.0, 5.0, "a"), (5.0 + MIN_GAP_SEC - 0.1, 9.0, "b")]
    assert detect_gaps(cues, 9.0) == []


def test_detect_gaps_overlapping_cues():
    # cue 時間重疊（speaker split 後可能出現）不可誤報為洞
    cues = [(0.0, 8.0, "a"), (3.0, 5.0, "b"), (8.2, 12.0, "c")]
    assert detect_gaps(cues, 12.0) == []


def test_parse_and_format_roundtrip():
    srt = "1\n00:42:13,897 --> 00:42:29,759\n測試字幕\n"
    cues = _parse_srt(srt)
    assert len(cues) == 1
    assert abs(cues[0][0] - 2533.897) < 0.001
    assert _ts(cues[0][0]) == "00:42:13,897"
