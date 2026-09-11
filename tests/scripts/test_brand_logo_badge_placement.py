"""開場品牌 LOGO 卡的落點幾何（ADR-067 短片線）。

為什麼有這支測試：20260721 呂冠緯 三支短片的 `split_opener_sec` 都是 0.0，
沒有上下分割。兩個 seam anchor 都以接縫 y=960 為基準，於是同一個矩形落在滿版
談話鏡頭的臉正中央——實測蓋住主持人的嘴與下巴，正是他 2026-08-30 說的
「現在遮到我的頭太多了」。`free` anchor 是那次修正；這裡把它釘住。
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
_spec = importlib.util.spec_from_file_location(
    "build_brand_logo_badge", REPO_ROOT / "scripts" / "build_brand_logo_badge.py"
)
assert _spec and _spec.loader
badge = importlib.util.module_from_spec(_spec)
sys.modules["build_brand_logo_badge"] = badge
_spec.loader.exec_module(badge)

CARD_W, CARD_H = 440, 360


def test_seam_above_keeps_the_card_clear_of_the_seam():
    pad_x, pad_y = badge.card_placement("seam-above", CARD_W, CARD_H)
    assert pad_x == (badge.CANVAS_W - CARD_W) // 2
    assert pad_y + CARD_H == badge.SEAM_Y - badge.SEAM_GAP


def test_seam_center_straddles_the_seam():
    _, pad_y = badge.card_placement("seam-center", CARD_W, CARD_H)
    assert pad_y < badge.SEAM_Y < pad_y + CARD_H


def test_free_anchor_places_the_card_exactly_where_asked():
    assert badge.card_placement("free", CARD_W, CARD_H, card_x=580, card_y=200) == (580, 200)


def test_free_anchor_demands_both_coordinates():
    with pytest.raises(SystemExit, match="card-x"):
        badge.card_placement("free", CARD_W, CARD_H, card_x=580)


@pytest.mark.parametrize(
    "card_x,card_y",
    [
        (-1, 200),  # 左出界
        (700, 200),  # 右出界：700+440 > 1080
        (580, 1600),  # 下出界：1600+360 > 1920
    ],
)
def test_free_anchor_refuses_to_run_off_the_canvas(card_x, card_y):
    with pytest.raises(SystemExit, match="超出畫布"):
        badge.card_placement("free", CARD_W, CARD_H, card_x=card_x, card_y=card_y)


def test_seam_anchor_still_rejects_a_card_too_tall_for_the_top_half():
    with pytest.raises(SystemExit, match="放不進接縫上方"):
        badge.card_placement("seam-above", CARD_W, badge.SEAM_Y)
