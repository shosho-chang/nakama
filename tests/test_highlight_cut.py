"""run_highlight_cut 純函數測試（variant 分組——去重移到評分後的機制）。"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

from run_highlight_cut import _variant_groups


def _c(cid, fmt, s, e):
    return {"id": cid, "format": fmt, "t_start": s, "t_end": e}


def test_overlapping_same_format_grouped():
    # story-L3(1015-1695) 包住 util-L2(1203-1695) → 同群組（2026-07-26 教訓案例）
    cands = [_c("L3", "long", 1015, 1695), _c("L2", "long", 1203, 1695), _c("LX", "long", 0, 500)]
    g = _variant_groups(cands)
    assert g["L3"] == g["L2"]
    assert g["LX"] != g["L3"]


def test_cross_format_never_grouped():
    # 長短片同範圍是不同產品，不歸同群
    cands = [_c("L1", "long", 100, 700), _c("S1", "short", 100, 180)]
    g = _variant_groups(cands)
    assert g["L1"] != g["S1"]


def test_chain_grouping_transitive():
    # A-B 重疊、B-C 重疊 → 三者同群（連通分量）
    cands = [_c("A", "short", 0, 100), _c("B", "short", 40, 140), _c("C", "short", 80, 180)]
    g = _variant_groups(cands)
    assert g["A"] == g["B"] == g["C"]


def test_small_overlap_not_grouped():
    # 重疊 40%（相對較短者）→ 不同群
    cands = [_c("A", "short", 0, 100), _c("B", "short", 65, 200)]
    g = _variant_groups(cands)
    assert g["A"] != g["B"]
