"""第 1 類章節卡：交件規則（插入點、標題、字級、收據的插入資訊、重渲判斷）。

不真的開 hyperframes。
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from agents.brook.youtube_video.visuals import chapter_card as cc
from agents.brook.youtube_video.visuals import delivery


def _plan(title: str = "到底該怎麼花錢？", before_cue: int = 4) -> dict:
    cues = [
        {"id": i, "start": i * 2.0, "end": i * 2.0 + 1.8, "text": f"第 {i} 句"}
        for i in range(1, 11)
    ]
    row = {
        "id": "row-a",
        "origin": "auto",
        "kind": "insert",
        "category": "chapter_card",
        "start_cue": None,
        "end_cue": None,
        "before_cue": before_cue,
        "insert_sec": 2,
        "layout": "full",
        "description": "",
        "dp_instruction": "",
        "onscreen_text": title,
        "source": "",
        "status": "approved",
        "note": "",
        "program_state": None,
    }
    return {"schema": "nakama.youtube_visual_plan.v1", "cues": cues, "rows": [row]}


class FakeRunner:
    def __init__(self):
        self.calls: list[list[str]] = []

    def __call__(self, argv: list[str], cwd: Path) -> None:
        self.calls.append(argv)
        if argv[0] == "ffmpeg":
            Path(argv[-1]).write_bytes(b"mov")


def test_inserted_before_its_cue():
    plan = _plan(before_cue=4)
    variables, start = cc.card_variables(plan, plan["rows"][0])
    assert start == 8.0
    assert variables == {"title": "到底該怎麼花錢？", "font_px": 136}


def test_long_title_shrinks_then_is_refused():
    assert cc.font_px_for("到底該怎麼花錢？") == 136
    assert cc.font_px_for("一二三四五六七八九十一二") < 136  # 12 個全形字縮小
    with pytest.raises(delivery.DeliveryError, match="太長"):
        cc.font_px_for("一二三四五六七八九十一二三四五")


@pytest.mark.parametrize("title, message", [("", "空的"), ("第一\n第二", "一行")])
def test_title_rules(title, message):
    plan = _plan(title=title)
    with pytest.raises(delivery.DeliveryError, match=message):
        cc.card_variables(plan, plan["rows"][0])


def test_overlay_row_is_refused():
    plan = _plan()
    plan["rows"][0]["category"] = "concept_title"
    with pytest.raises(delivery.DeliveryError, match="不是章節卡"):
        cc.card_variables(plan, plan["rows"][0])


def test_deliver_writes_receipt_with_insert_overlap_then_reuses(tmp_path):
    plan = _plan()
    runner = FakeRunner()
    receipt, rendered = cc.deliver_row(tmp_path, plan, plan["rows"][0], runner=runner)
    assert rendered
    saved = json.loads(delivery.asset_paths(tmp_path, "row-a").receipt.read_text(encoding="utf-8"))
    assert saved == receipt
    assert saved["kind"] == "insert" and saved["component"] == "chapter_card"
    assert saved["start_sec"] == 8.0 and saved["duration_sec"] == 2.0
    # 前 1.7 秒把錄影往後推，最後 0.3 秒疊在下一段錄影上
    assert saved["insert_sec"] == 1.7 and saved["overlap_next_sec"] == 0.3
    render, trim = runner.calls
    assert render[render.index("-c") + 1] == "compositions/chapter_card.html"
    assert render[render.index("--format") + 1] == "mov"
    assert trim[trim.index("-t") + 1] == "2.000"
    _, again = cc.deliver_row(tmp_path, plan, plan["rows"][0], runner=runner)
    assert not again and len(runner.calls) == 2


def test_only_approved_chapter_cards_are_built():
    plan = _plan()
    plan["rows"] += [
        dict(plan["rows"][0], id="row-b", status=None),
        dict(plan["rows"][0], id="row-c", category="concept_title", kind="overlay"),
    ]
    assert [row["id"] for row in cc.approved_rows(plan)] == ["row-a"]


def test_insert_point_only_for_inserts():
    plan = _plan()
    with pytest.raises(delivery.DeliveryError, match="只有插入類"):
        delivery.insert_point(plan, dict(plan["rows"][0], kind="overlay"))
