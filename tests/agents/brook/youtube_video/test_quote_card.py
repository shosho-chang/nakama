"""第 8 類金句卡：交件規則（時間、分行、出處、收據、重渲判斷）。不真的開 hyperframes。"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from agents.brook.youtube_video.visuals import delivery
from agents.brook.youtube_video.visuals import quote_card as qc


def _plan(
    text: str = "致富跟你聰不聰明無關，跟你的行為有關。",
    source: str = "Morgan Housel《致富心態》",
    start_cue: int = 3,
    end_cue: int = 5,
) -> dict:
    cues = [
        {"id": i, "start": i * 2.0, "end": i * 2.0 + 1.8, "text": f"第 {i} 句"}
        for i in range(1, 11)
    ]
    row = {
        "id": "row-a",
        "origin": "director",
        "kind": "overlay",
        "category": "quote_card",
        "start_cue": start_cue,
        "end_cue": end_cue,
        "before_cue": None,
        "insert_sec": None,
        "layout": "full",
        "description": "",
        "dp_instruction": "",
        "onscreen_text": text,
        "source": source,
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
    plan = _plan(start_cue=3, end_cue=5)  # 6.0 → 11.8，共 5.8 秒
    variables, start, seconds = qc.card_variables(plan, plan["rows"][0])
    assert start == 6.0 and seconds == 5.8 and variables["show_sec"] == 5.8
    assert qc.show_seconds(1.0) == qc.MIN_SEC
    assert qc.show_seconds(30.0) == qc.MAX_SEC


def test_long_sentence_breaks_after_punctuation():
    plan = _plan()
    variables, _, _ = qc.card_variables(plan, plan["rows"][0])
    assert variables["lines"] == "致富跟你聰不聰明無關，\n跟你的行為有關。"
    assert variables["who"] == "Morgan Housel《致富心態》"


def test_own_line_breaks_are_kept():
    plan = _plan(text="錢\n是一種工具")
    variables, _, _ = qc.card_variables(plan, plan["rows"][0])
    assert variables["lines"] == "錢\n是一種工具"


def test_punctuation_never_starts_a_line():
    lines = qc.wrap_line("一二三四五六七八九十一二三四五。後面")
    assert lines == ["一二三四五六七八九十一二三四五。", "後面"]


def test_too_many_lines_is_refused():
    plan = _plan(text="一二三四五六七八九十" * 5)
    with pytest.raises(delivery.DeliveryError, match="太長"):
        qc.card_variables(plan, plan["rows"][0])


@pytest.mark.parametrize(
    "source, message",
    [("https://example.com/x", "網址"), ("甲\n乙", "一行")],
)
def test_attribution_rules(source, message):
    plan = _plan(source=source)
    with pytest.raises(delivery.DeliveryError, match=message):
        qc.card_variables(plan, plan["rows"][0])


def test_attribution_may_be_empty():
    plan = _plan(source="")
    variables, _, _ = qc.card_variables(plan, plan["rows"][0])
    assert variables["who"] == ""


def test_empty_quote_is_refused():
    plan = _plan(text="  ")
    with pytest.raises(delivery.DeliveryError, match="空的"):
        qc.card_variables(plan, plan["rows"][0])


def test_deliver_writes_receipt_then_reuses(tmp_path):
    plan = _plan()
    runner = FakeRunner()
    receipt, rendered = qc.deliver_row(tmp_path, plan, plan["rows"][0], runner=runner)
    assert rendered
    saved = json.loads(delivery.asset_paths(tmp_path, "row-a").receipt.read_text(encoding="utf-8"))
    assert saved == receipt
    assert saved["component"] == "quote_dim" and saved["category"] == "quote_card"
    assert saved["start_sec"] == 6.0 and saved["duration_sec"] == 5.8
    render, trim = runner.calls
    assert render[render.index("-c") + 1] == "compositions/quote_dim.html"
    assert trim[trim.index("-t") + 1] == "5.800"
    _, again = qc.deliver_row(tmp_path, plan, plan["rows"][0], runner=runner)
    assert not again and len(runner.calls) == 2
    plan["rows"][0]["source"] = "Morgan Housel"
    _, changed = qc.deliver_row(tmp_path, plan, plan["rows"][0], runner=runner)
    assert changed and len(runner.calls) == 4


def test_only_approved_quote_cards_are_built():
    plan = _plan()
    plan["rows"] += [
        dict(plan["rows"][0], id="row-b", status=None),
        dict(plan["rows"][0], id="row-c", category="concept_title"),
    ]
    assert [row["id"] for row in qc.approved_rows(plan)] == ["row-a"]
