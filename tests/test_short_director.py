"""run_short_director 純函數測試（雙機位導播 shot 規劃 + panel 幾何）。"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

from run_short_director import (
    DEFAULT_CFG,
    FIT,
    TILT_SCALE,
    _pan,
    _panel_props,
    build_shots,
)


def _words(*runs):
    """runs = (start, end, spk, n_words) → 均分 n 個詞。"""
    out = []
    for s, e, spk, n in runs:
        step = (e - s) / n
        for i in range(n):
            out.append((s + i * step, s + (i + 1) * step, spk))
    return out


def test_shots_switch_on_speaker_change():
    words = _words((0, 5, 1, 10), (5, 10, 0, 10))
    shots = build_shots([(0.0, 10.0)], words, DEFAULT_CFG)
    assert [s["spk"] for s in shots] == [1, 0]
    assert shots[0]["e"] == shots[1]["s"]  # 邊界相接無縫


def test_shots_short_interjection_absorbed():
    # 0.5s 的附和（min_shot=1.0）不切鏡
    words = _words((0, 4, 1, 8), (4, 4.5, 0, 2), (4.5, 10, 1, 10))
    shots = build_shots([(0.0, 10.0)], words, DEFAULT_CFG)
    assert [s["spk"] for s in shots] == [1]


def test_shots_zoom_alternates_within_speaker():
    # 兩個保留段、同說話者 → 交替 base/punch
    words = _words((0, 5, 1, 10), (6, 11, 1, 10))
    shots = build_shots([(0.0, 5.0), (6.0, 11.0)], words, DEFAULT_CFG)
    assert [s["zoom"] for s in shots] == [DEFAULT_CFG["zoom_base"], DEFAULT_CFG["zoom_punch"]]


def test_shots_zoom_resets_on_speaker_change():
    words = _words((0, 5, 1, 10), (5, 10, 0, 10))
    shots = build_shots([(0.0, 10.0)], words, DEFAULT_CFG)
    assert all(s["zoom"] == DEFAULT_CFG["zoom_base"] for s in shots)


def test_pan_centers_face():
    # 臉在源片中心右側 → 影像要往左移（Pan 負）
    assert _pan(DEFAULT_CFG, 1, 3.2) < 0  # face_x 1165 > 960
    assert _pan(DEFAULT_CFG, 0, 3.2) > 0  # face_x 880 < 960


def test_panel_fills_half_frame():
    # 窗框放大後恰為 1080×960 半屏（Resolve 語意見 script 常數註解）
    p = _panel_props(DEFAULT_CFG, 1, top=True)
    win_w_fit = 1080 / p["ZoomX"]
    win_h_fit = win_w_fit * (720 / 810)
    assert abs(win_w_fit * p["ZoomX"] - 1080) < 1e-6
    assert abs(win_h_fit * p["ZoomX"] - 960) < 1e-6
    # crop 總和 = fit 畫布減窗框
    assert abs((p["CropLeft"] + p["CropRight"]) - (1920 - 810) * FIT) < 1e-6
    assert abs((p["CropTop"] + p["CropBottom"]) - (1080 - 720) * FIT) < 1e-6


def test_panel_top_bottom_tilt_signs():
    top = _panel_props(DEFAULT_CFG, 1, top=True)
    bottom = _panel_props(DEFAULT_CFG, 0, top=False)
    # Tilt 正值向上：上 panel 要向上移 → Tilt 正；下 panel 反之
    assert top["Tilt"] > 0 > bottom["Tilt"]
    # TILT_SCALE 換算後的螢幕位移量：上下各 ~480±窗框補償
    assert abs(top["Tilt"] * TILT_SCALE - 360) < 5
    assert abs(bottom["Tilt"] * TILT_SCALE + 600) < 5
