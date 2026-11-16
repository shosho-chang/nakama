"""稿上 `###` 小標題遮罩：交件規則（起點、標題、字級、收據、重渲判斷）。

不真的開 hyperframes。
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from agents.brook.youtube_video.visuals import delivery
from agents.brook.youtube_video.visuals import section_title as st


def _plan(title: str = "第五階：住宅自由", start_cue: int = 3, end_cue: int = 3) -> dict:
    cues = [
        {"id": i, "start": i * 2.0, "end": i * 2.0 + 1.8, "text": f"第 {i} 句"}
        for i in range(1, 11)
    ]
    row = {
        "id": "row-a",
        "origin": "auto",
        "kind": "overlay",
        "category": "section_title",
        "start_cue": start_cue,
        "end_cue": end_cue,
        "before_cue": None,
        "insert_sec": None,
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
    """記下呼叫；render 時在 -o 產出檔案（代表渲完）。"""

    def __init__(self):
        self.calls: list[list[str]] = []

    def __call__(self, argv: list[str], cwd: Path) -> None:
        self.calls.append(argv)
        Path(argv[argv.index("-o") + 1]).write_bytes(b"mov")


def test_starts_at_first_cue_with_default_size():
    plan = _plan(start_cue=3, end_cue=3)
    variables, start = st.card_variables(plan, plan["rows"][0])
    assert start == 6.0
    assert variables == {"title": "第五階：住宅自由", "font_px": 96}


def test_long_title_shrinks_then_is_refused():
    assert st.font_px_for("第五階：住宅自由") == 96
    assert st.font_px_for("一二三四五六七八九十一二三四五六") < 96  # 16 個全形字縮小
    with pytest.raises(delivery.DeliveryError, match="太長"):
        st.font_px_for("一二三四五六七八九十一二三四五六七八九十")


@pytest.mark.parametrize("title, message", [("", "空的"), ("第一\n第二", "一行")])
def test_title_rules(title, message):
    plan = _plan(title=title)
    with pytest.raises(delivery.DeliveryError, match=message):
        st.card_variables(plan, plan["rows"][0])


def test_other_category_is_refused():
    plan = _plan()
    plan["rows"][0]["category"] = "concept_title"
    with pytest.raises(delivery.DeliveryError, match="不是小標題遮罩"):
        st.card_variables(plan, plan["rows"][0])


def test_deliver_writes_receipt_then_reuses(tmp_path):
    plan = _plan()
    runner = FakeRunner()
    receipt, rendered = st.deliver_row(tmp_path, plan, plan["rows"][0], runner=runner)
    assert rendered
    paths = delivery.asset_paths(tmp_path, "row-a")
    assert paths.media.is_file()
    assert not list(paths.media.parent.glob("*.full.mov"))
    saved = json.loads(paths.receipt.read_text(encoding="utf-8"))
    assert saved == receipt
    assert saved["kind"] == "overlay" and saved["component"] == "section_title"
    assert saved["start_sec"] == 6.0 and saved["duration_sec"] == 2.5
    (render,) = runner.calls
    assert render[render.index("-c") + 1] == "compositions/section_title.html"
    assert render[render.index("--format") + 1] == "mov"
    _, again = st.deliver_row(tmp_path, plan, plan["rows"][0], runner=runner)
    assert not again and len(runner.calls) == 1


def test_changed_title_is_rerendered(tmp_path):
    plan = _plan()
    runner = FakeRunner()
    st.deliver_row(tmp_path, plan, plan["rows"][0], runner=runner)
    plan["rows"][0]["onscreen_text"] = "第六階：時間自由"
    _, rendered = st.deliver_row(tmp_path, plan, plan["rows"][0], runner=runner)
    assert rendered and len(runner.calls) == 2


def test_only_approved_section_titles_are_built():
    plan = _plan()
    plan["rows"] += [
        dict(plan["rows"][0], id="row-b", status=None),
        dict(plan["rows"][0], id="row-c", category="concept_title", layout="overlay"),
    ]
    assert [row["id"] for row in st.approved_rows(plan)] == ["row-a"]
