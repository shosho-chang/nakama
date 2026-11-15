"""第 2 類重點概念 title：交件規則（時間、形狀、字數、收據、重渲判斷）。不真的開 hyperframes。"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from agents.brook.youtube_video.visuals import concept_title as ct
from agents.brook.youtube_video.visuals import delivery


def _plan(text: str = "萬分之一法則", start_cue: int = 3, end_cue: int = 4) -> dict:
    cues = [
        {"id": i, "start": i * 2.0, "end": i * 2.0 + 1.8, "text": f"第 {i} 句"}
        for i in range(1, 11)
    ]
    row = {
        "id": "row-a",
        "origin": "director",
        "kind": "overlay",
        "category": "concept_title",
        "start_cue": start_cue,
        "end_cue": end_cue,
        "before_cue": None,
        "insert_sec": None,
        "layout": "overlay",
        "description": "",
        "dp_instruction": "",
        "onscreen_text": text,
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


def test_window_starts_at_first_cue_and_is_clamped():
    plan = _plan(start_cue=3, end_cue=4)  # 6.0 → 9.8，共 3.8 秒
    variables, start, seconds = ct.card_variables(plan, plan["rows"][0])
    assert start == 6.0 and seconds == 3.8
    assert variables["show_sec"] == 3.8
    assert ct.show_seconds(0.6) == ct.MIN_SEC
    assert ct.show_seconds(12.0) == ct.MAX_SEC


def test_shape_is_one_of_four_and_stable_per_row():
    shapes = {ct.shape_for(f"row-{i}") for i in range(200)}
    assert shapes == {1, 2, 3, 4}
    assert ct.shape_for("row-a") == ct.shape_for("row-a")


@pytest.mark.parametrize(
    "text, message",
    [("", "空的"), ("萬分之一\n法則", "一行"), ("這是一個非常非常非常長的重點概念名稱", "太長")],
)
def test_text_rules(text, message):
    plan = _plan(text=text)
    with pytest.raises(delivery.DeliveryError, match=message):
        ct.card_variables(plan, plan["rows"][0])


def test_mixed_width_counts_half_width_narrower():
    assert ct.text_width_units("1% 法則") < ct.text_width_units("百分之一法則")


def test_deliver_writes_media_and_receipt_then_reuses(tmp_path):
    plan = _plan()
    runner = FakeRunner()
    receipt, rendered = ct.deliver_row(tmp_path, plan, plan["rows"][0], runner=runner)
    assert rendered
    paths = delivery.asset_paths(tmp_path, "row-a")
    assert paths.media.is_file()
    saved = json.loads(paths.receipt.read_text(encoding="utf-8"))
    assert saved == receipt
    assert saved["schema"] == delivery.RECEIPT_SCHEMA
    assert saved["start_sec"] == 6.0 and saved["duration_sec"] == 3.8
    assert saved["fps"] == "30000/1001"
    render, trim = runner.calls
    assert "--fps" in render and render[render.index("--fps") + 1] == "30000/1001"
    assert trim[trim.index("-t") + 1] == "3.800"
    # 暫存檔都清掉
    assert sorted(p.name for p in paths.media.parent.iterdir()) == ["row-a.json", "row-a.mov"]

    _, again = ct.deliver_row(tmp_path, plan, plan["rows"][0], runner=runner)
    assert not again and len(runner.calls) == 2


def test_text_change_triggers_rerender(tmp_path):
    plan = _plan()
    runner = FakeRunner()
    ct.deliver_row(tmp_path, plan, plan["rows"][0], runner=runner)
    plan["rows"][0]["onscreen_text"] = "百分之一法則"
    receipt, rendered = ct.deliver_row(tmp_path, plan, plan["rows"][0], runner=runner)
    assert rendered and receipt["variables"]["text"] == "百分之一法則"


def test_only_approved_concept_titles_are_built():
    plan = _plan()
    other = dict(plan["rows"][0], id="row-b", status=None)
    book = dict(plan["rows"][0], id="row-c", category="book_cover")
    plan["rows"] += [other, book]
    assert [row["id"] for row in ct.approved_rows(plan)] == ["row-a"]


def test_wrong_category_is_refused():
    plan = _plan()
    plan["rows"][0]["category"] = "book_cover"
    with pytest.raises(delivery.DeliveryError, match="不是重點概念"):
        ct.card_variables(plan, plan["rows"][0])
