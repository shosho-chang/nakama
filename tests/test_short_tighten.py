"""run_short_tighten 純函數測試（短片 jump-cut 緊湊化）。"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

from run_short_tighten import _keep_segments, _strip_cut_word


def _cut(t0, t1, keep=True):
    return {"t0": t0, "t1": t1, "keep": keep}


def test_keep_segments_basic_complement():
    segs = _keep_segments(0.0, 10.0, [_cut(3.0, 4.0), _cut(7.0, 7.5)])
    assert segs == [(0.0, 3.0), (4.0, 7.0), (7.5, 10.0)]


def test_keep_segments_cut_at_clip_start():
    # 開頭口吃「那、那」整刀（punch-S1 案例）：不留 0 長度開頭段
    segs = _keep_segments(625.188, 692.508, [_cut(625.188, 626.129)])
    assert segs[0] == (626.129, 692.508)
    assert len(segs) == 1


def test_keep_segments_short_island_absorbed():
    # 兩刀之間 0.085s 孤島（pause 刀 + filler 刀相鄰）→ 併成一刀，不閃屏
    segs = _keep_segments(0.0, 10.0, [_cut(2.0, 2.5), _cut(2.585, 3.3)])
    assert segs == [(0.0, 2.0), (3.3, 10.0)]


def test_keep_segments_ignores_unkept_and_null():
    segs = _keep_segments(0.0, 10.0, [_cut(3.0, 4.0, keep=False), _cut(5.0, 6.0, keep=None)])
    assert segs == [(0.0, 10.0)]


def test_strip_cut_word_leading():
    # 「那 現在的這個時代」開頭贅詞 → 移除 + 空格收乾淨
    assert _strip_cut_word("那 現在的這個時代喔", "那", 0.0) == "現在的這個時代喔"


def test_strip_cut_word_picks_nearest_occurrence():
    # 文字有兩個「那」，剪點在後段 → 移除後面那個
    text = "那一旦習慣了 那它其實"
    assert _strip_cut_word(text, "那", 0.9) == "那一旦習慣了 它其實"
    assert _strip_cut_word(text, "那", 0.0) == "一旦習慣了 那它其實"


def test_strip_cut_word_absent_is_noop():
    assert _strip_cut_word("完全無關的句子", "那", 0.5) == "完全無關的句子"
