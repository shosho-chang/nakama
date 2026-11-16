"""第 18 類重點條列：交件規則（條列、每條出現時間、尺寸上限、收據、重渲判斷）。

不真的開 hyperframes。
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from agents.brook.youtube_video.visuals import delivery
from agents.brook.youtube_video.visuals import reframe as rf
from agents.brook.youtube_video.visuals import side_bullets as sb

# 《財富階梯》總結段的字幕（剪好的版本，秒數從 0 起算）
SUMMARY = [
    ("再幫大家整理一下幾個重點", 0.0),
    ("第一個", 2.07),
    ("花錢要看自己在哪個階層", 2.9),
    ("用萬分之一法則", 5.17),
    ("第二個", 8.21),
    ("拿時間換錢的工作要參考１％法則", 8.74),
    ("第三個", 14.31),
    ("投資跟學習都要趁早", 15.12),
    ("第四", 22.56),
    ("善用內容跟AI", 23.12),
    ("第五", 27.33),
    ("利用內容找到對的受眾", 27.86),
    ("AI Agent建立起服務他們的事業", 30.63),
]
ITEMS = "\n".join(
    [
        "花錢看階層，用萬分之一法則",
        "時間換錢，參考 1% 法則",
        "投資與學習都要趁早",
        "善用內容與 AI 兩大槓桿",
        "用內容找受眾，用 AI Agent 建事業",
    ]
)


def _plan(texts=SUMMARY, items: str = ITEMS, end_gap: float = 3.4) -> dict:
    cues = []
    for i, (text, start) in enumerate(texts, start=1):
        nxt = texts[i][1] if i < len(texts) else start + end_gap
        cues.append({"id": i, "start": 100.0 + start, "end": 100.0 + nxt, "text": text})
    row = {
        "id": "row-a",
        "origin": "director",
        "kind": "overlay",
        "category": "side_bullets",
        "start_cue": 1,
        "end_cue": len(cues),
        "before_cue": None,
        "insert_sec": None,
        "layout": "overlay",
        "description": "",
        "dp_instruction": "",
        "onscreen_text": items,
        "source": "",
        "status": "approved",
        "note": "",
        "program_state": None,
    }
    return {"schema": "nakama.youtube_visual_plan.v1", "cues": cues, "rows": [row]}


class FakeRunner:
    """記下呼叫；遇到 ffmpeg 就產出交件檔（代表截斷完成）。"""

    def __init__(self):
        self.calls: list[list[str]] = []

    def __call__(self, argv: list[str], cwd: Path) -> None:
        self.calls.append(argv)
        if argv[0] == "ffmpeg":
            Path(argv[-1]).write_bytes(b"mov")


def test_items_appear_when_each_ordinal_is_spoken():
    plan = _plan()
    variables, start, seconds, method = sb.card_variables(plan, plan["rows"][0])
    assert method == "ordinal"
    assert variables["times"] == "2.07,8.21,14.31,22.56,27.33"
    assert start == 100.0 and seconds == pytest.approx(34.03)
    assert variables["items"].split("\n")[0] == "花錢看階層，用萬分之一法則"
    assert variables["title"] == "重點整理"


def test_without_ordinals_items_spread_over_cues():
    texts = [(f"第 {chr(0x7532 + i)} 句", i * 2.0) for i in range(9)]  # 沒有「第一」……
    plan = _plan(texts=texts, items="甲\n乙\n丙")
    variables, _, _, method = sb.card_variables(plan, plan["rows"][0])
    assert method == "even"
    assert variables["times"] == "0.6,6,12"  # 第 0、3、6 句；第一條等側欄滑到位


def test_ordinal_does_not_match_a_longer_number():
    cues = [{"text": "第十一個"}, {"text": "第一個"}]
    assert sb.ordinal_cues(cues, 1) == [1]
    assert sb.ordinal_cues([{"text": "第1點"}, {"text": "第２點"}], 2) == [0, 1]


def test_leading_numbers_are_dropped():
    plan = _plan(items="1. 甲\n2、乙\n- 丙\n・丁\n\n五")
    variables, _, _, _ = sb.card_variables(plan, plan["rows"][0])
    assert variables["items"] == "甲\n乙\n丙\n丁\n五"


@pytest.mark.parametrize(
    "items, message",
    [
        ("  ", "空的"),
        ("\n".join("甲乙" for _ in range(7)), "上限 6 條"),
        ("一二三四五六七八九十" * 3, "太長"),
        ("\n".join("一二三四五六七八九十一二三四五" for _ in range(6)), "太高"),
    ],
)
def test_bad_lists_are_refused(items, message):
    plan = _plan(items=items)
    with pytest.raises(delivery.DeliveryError, match=message):
        sb.card_variables(plan, plan["rows"][0])


def test_last_item_needs_time_on_screen():
    texts = [("開場", 0.0), ("第一", 1.0), ("第二", 3.5)]
    plan = _plan(texts=texts, items="甲\n乙", end_gap=0.5)
    with pytest.raises(delivery.DeliveryError, match="往後拉"):
        sb.card_variables(plan, plan["rows"][0])


def test_too_short_or_too_long_is_refused():
    plan = _plan(texts=[("第一", 0.0)], items="甲", end_gap=2.0)
    with pytest.raises(delivery.DeliveryError, match="至少"):
        sb.card_variables(plan, plan["rows"][0])
    plan = _plan(texts=[("第一", 0.0)], items="甲", end_gap=61.0)
    with pytest.raises(delivery.DeliveryError, match="上限 60"):
        sb.card_variables(plan, plan["rows"][0])


def test_deliver_writes_receipt_then_reuses(tmp_path):
    plan = _plan()
    runner = FakeRunner()
    receipt, rendered = sb.deliver_row(tmp_path, plan, plan["rows"][0], runner=runner)
    assert rendered
    saved = json.loads(delivery.asset_paths(tmp_path, "row-a").receipt.read_text(encoding="utf-8"))
    assert saved == receipt
    assert saved["component"] == "side_bullets" and saved["category"] == "side_bullets"
    assert saved["start_sec"] == 100.0 and saved["duration_sec"] == pytest.approx(34.03)
    assert saved["item_timing"] == "ordinal"
    render, trim = runner.calls
    assert render[render.index("-c") + 1] == "compositions/side_bullets.html"
    assert trim[trim.index("-t") + 1] == "34.030"
    _, again = sb.deliver_row(tmp_path, plan, plan["rows"][0], runner=runner)
    assert not again and len(runner.calls) == 2
    plan["rows"][0]["onscreen_text"] = ITEMS.replace("趁早", "越早越好")
    _, changed = sb.deliver_row(tmp_path, plan, plan["rows"][0], runner=runner)
    assert changed and len(runner.calls) == 4
    # 畫面搬移把臉讓到左 1/3，跟側欄同一段時間
    (move,) = rf.moves_for_receipts(tmp_path)
    assert move.kind == "left" and move.start == 100.0 and move.end == pytest.approx(134.03)


def test_only_approved_side_bullets_are_built():
    plan = _plan()
    plan["rows"] += [
        dict(plan["rows"][0], id="row-b", status=None),
        dict(plan["rows"][0], id="row-c", category="concept_title"),
    ]
    assert [row["id"] for row in sb.approved_rows(plan)] == ["row-a"]
