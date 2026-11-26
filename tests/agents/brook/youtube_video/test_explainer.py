"""第 5 類解釋動畫：圖表規格 → 交件規則。

時間換算、必填、數量上限、版面檢查擋下、收據、重渲判斷、畫面搬移。

不真的開 hyperframes、不開 Chrome（版面檢查用假的 checker）。
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from agents.brook.youtube_video import dp
from agents.brook.youtube_video import layout as lay
from agents.brook.youtube_video.visuals import delivery
from agents.brook.youtube_video.visuals import explainer as ex
from agents.brook.youtube_video.visuals import reframe as rf

# 謝伯讓那集「政府發米」段的字幕（秒數從 0 起算，掛在原始錄影的 100 秒）
CUES = [
    ("你是要一年拿 12 包", 0.0),
    ("還是說每個月拿一包", 2.2),
    ("理性的人應該會選擇", 5.1),
    ("每個月拿一包", 8.0),
    ("你不要等到一年後再拿 12 包", 10.4),
]


def _plan(layout: str = "full", end_gap: float = 4.0, source: str = "") -> dict:
    cues = []
    for i, (text, start) in enumerate(CUES, start=1):
        nxt = CUES[i][1] if i < len(CUES) else start + end_gap
        cues.append({"id": i, "start": 100.0 + start, "end": 100.0 + nxt, "text": text})
    row = {
        "id": "row-a",
        "origin": "director",
        "kind": "overlay",
        "category": "explainer",
        "start_cue": 1,
        "end_cue": len(cues),
        "before_cue": None,
        "insert_sec": None,
        "layout": layout,
        "description": "",
        "dp_instruction": "",
        "onscreen_text": "",
        "source": source,
        "status": "approved",
        "note": "",
        "program_state": None,
    }
    return {"schema": "nakama.youtube_visual_plan.v1", "cues": cues, "rows": [row]}


CONTRAST = {
    "chart": "contrast",
    "title": "政府發米",
    "columns": [
        {"label": "一年拿 12 包", "at": {"cue": 1, "offset": 0.9}, "items": []},
        {
            "label": "每個月拿一包",
            "at": {"cue": 2},
            "sub": "理性的人應該會選",
            "sub_at": {"cue": 3, "offset": 0.2},
            "items": [{"text": "每個月拿一包", "at": 8.4}],
        },
    ],
}


def _write_spec(episode: Path, spec: dict, row_id: str = "row-a") -> None:
    path = ex.spec_path(episode, row_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(spec, ensure_ascii=False), encoding="utf-8")


class FakeRunner:
    """記下呼叫；遇到 ffmpeg 就產出交件檔（代表截斷完成）。"""

    def __init__(self):
        self.calls: list[list[str]] = []

    def __call__(self, argv: list[str], cwd: Path) -> None:
        self.calls.append(argv)
        if argv[0] == "ffmpeg":
            Path(argv[-1]).write_bytes(b"mov")


def ok_checker(chart, spec):
    return {"at": spec["show_sec"] - 0.7, "outside": [], "overlap": []}


def test_cue_times_become_seconds_from_the_chart_start(tmp_path):
    _write_spec(tmp_path, CONTRAST)
    plan = _plan(source="謝伯讓 EP125")
    chart, spec, start, seconds = ex.chart_spec(tmp_path, plan, plan["rows"][0])
    assert chart == "contrast" and start == 100.0 and seconds == pytest.approx(14.4)
    left, right = spec["columns"]
    assert left["at"] == 0.9 and right["at"] == 2.2 and right["sub_at"] == 5.3
    assert right["items"][0]["at"] == 8.4  # 直接寫秒數的原樣
    assert spec["layout"] == "full" and spec["show_sec"] == pytest.approx(14.4)
    assert spec["source"] == "資料：謝伯讓 EP125" and "chart" not in spec


@pytest.mark.parametrize(
    ("change", "message"),
    [
        ({"chart": "map"}, "chart 要是"),
        ({"title": ""}, "要有標題"),
        ({"title": "這是一個超過十六個全形字的很長很長很長的標題"}, "太長"),
        ({"columns": CONTRAST["columns"][:1]}, "要 2–2 項"),
        (
            {"columns": [dict(CONTRAST["columns"][0], at={"cue": 99}), CONTRAST["columns"][1]]},
            "不在這一列蓋住的範圍裡",
        ),
        (
            {"columns": [dict(CONTRAST["columns"][0], at="開頭"), CONTRAST["columns"][1]]},
            "要是秒數",
        ),
        (
            {"columns": [CONTRAST["columns"][0], dict(CONTRAST["columns"][1], at=13.9)]},
            "最後一個元素至少要停",
        ),
        ({"columns": [{"label": "左"}, CONTRAST["columns"][1]]}, "缺 at"),
    ],
)
def test_bad_specs_are_refused(tmp_path, change, message):
    _write_spec(tmp_path, {**CONTRAST, **change})
    plan = _plan()
    with pytest.raises(ex.ExplainerError, match=message):
        ex.chart_spec(tmp_path, plan, plan["rows"][0])


def test_side_panel_limits_and_missing_inputs(tmp_path):
    plan = _plan(layout="overlay")
    with pytest.raises(ex.ExplainerError, match="還沒有圖表規格"):
        ex.chart_spec(tmp_path, plan, plan["rows"][0])
    donuts = [{"label": f"第{i}", "value": 10, "at": 1 + i} for i in range(3)]
    _write_spec(tmp_path, {"chart": "donut", "title": "三個環", "items": donuts})
    with pytest.raises(ex.ExplainerError, match="側欄.*1–2 項"):
        ex.chart_spec(tmp_path, plan, plan["rows"][0])
    long_title = "這是超過十個字的側欄標題"
    _write_spec(tmp_path, {"chart": "donut", "title": long_title, "items": donuts[:2]})
    with pytest.raises(ex.ExplainerError, match="側欄約 10"):
        ex.chart_spec(tmp_path, plan, plan["rows"][0])
    _write_spec(tmp_path, {"chart": "stat", "title": "大數字", "value": 62})
    with pytest.raises(ex.ExplainerError, match="stat 要有 at"):
        ex.chart_spec(tmp_path, plan, plan["rows"][0])
    plan["rows"][0]["layout"] = None
    with pytest.raises(ex.ExplainerError, match="還沒選版面"):
        ex.chart_spec(tmp_path, plan, plan["rows"][0])


def test_equation_rule_lines_only_need_a_time(tmp_path):
    lines = [
        {"label": "一個月拿", "text": "1 包", "at": 1.0},
        {"text": "÷ 30 天", "at": 2.0},
        {"rule": True, "at": 3.0},
        {"label": "每天拿", "text": "＝ 1/30 包", "at": 3.3},
    ]
    _write_spec(tmp_path, {"chart": "equation", "title": "把獎勵切小", "lines": lines})
    plan = _plan()
    chart, spec, _, _ = ex.chart_spec(tmp_path, plan, plan["rows"][0])
    assert chart == "equation" and len(spec["lines"]) == 4


def test_every_chart_has_its_page():
    for chart in ex.CHARTS:
        assert ex.chart_html(chart).is_file(), chart


def test_layout_problems_stop_the_render(tmp_path):
    _write_spec(tmp_path, CONTRAST)
    plan = _plan()
    runner = FakeRunner()

    def crowded(chart, spec):
        return {"outside": [{"text": "一年拿 12 包", "box": [120, 300, 600, 372]}],
                "overlap": [["每個月拿一包", "理性的人應該會選"]]}  # fmt: skip

    with pytest.raises(ex.ExplainerError, match="超出安全框.*疊在一起"):
        ex.deliver_row(tmp_path, plan, plan["rows"][0], runner=runner, checker=crowded)
    assert runner.calls == []
    _, _, problems = ex.check_row(tmp_path, plan, plan["rows"][0], checker=crowded)
    assert len(problems) == 2


def test_deliver_writes_receipt_then_reuses(tmp_path):
    _write_spec(tmp_path, CONTRAST)
    plan = _plan()
    runner = FakeRunner()
    receipt, rendered = ex.deliver_row(
        tmp_path, plan, plan["rows"][0], runner=runner, checker=ok_checker
    )
    assert rendered
    saved = json.loads(delivery.asset_paths(tmp_path, "row-a").receipt.read_text(encoding="utf-8"))
    assert saved == receipt
    assert saved["category"] == "explainer" and saved["component"] == "explainer/contrast"
    assert saved["chart"] == "contrast" and saved["layout"] == "full"
    assert saved["start_sec"] == 100.0 and saved["duration_sec"] == pytest.approx(14.4)
    assert json.loads(saved["variables"]["spec"])["columns"][1]["sub_at"] == 5.3
    render, trim = runner.calls
    assert render[render.index("-c") + 1] == "compositions/contrast.html"
    assert render[render.index("--fps") + 1] == delivery.FPS
    assert trim[trim.index("-t") + 1] == "14.400"
    _, again = ex.deliver_row(tmp_path, plan, plan["rows"][0], runner=runner, checker=ok_checker)
    assert not again and len(runner.calls) == 2
    _write_spec(tmp_path, {**CONTRAST, "title": "政府發米的兩種方式"})
    _, changed = ex.deliver_row(tmp_path, plan, plan["rows"][0], runner=runner, checker=ok_checker)
    assert changed and len(runner.calls) == 4
    # 滿版蓋住整個畫面：臉不用搬
    assert rf.moves_for_receipts(tmp_path) == []


def test_side_panel_moves_the_face_left(tmp_path):
    _write_spec(tmp_path, {**CONTRAST, "title": "政府發米"})
    plan = _plan(layout="overlay")
    ex.deliver_row(tmp_path, plan, plan["rows"][0], runner=FakeRunner(), checker=ok_checker)
    (move,) = rf.moves_for_receipts(tmp_path)
    assert move.kind == "left" and move.start == 100.0 and move.end == pytest.approx(114.4)
    (overlay,) = lay.load_layout(tmp_path, plan).overlays
    assert overlay.layout == "overlay"
    assert [m.kind for m in lay.moves(lay.load_layout(tmp_path, plan))] == ["left"]


def test_dp_routes_explainer_rows_here():
    assert dp.RENDERERS["explainer"] == ex.__name__
    plan = _plan()
    plan["rows"] += [
        dict(plan["rows"][0], id="row-b", status=None),
        dict(plan["rows"][0], id="row-c", category="side_bullets"),
    ]
    assert [row["id"] for row in ex.approved_rows(plan)] == ["row-a"]
