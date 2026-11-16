"""第 7 類文件 highlighter：用字找位置、每步時間、說明檔錯誤、收據與重渲判斷。

用一份臨時手寫的小 PDF（Helvetica 英數字）跑真的 PDFium 找字；不真的開 hyperframes。
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from PIL import Image

from agents.brook.youtube_video.visuals import delivery
from agents.brook.youtube_video.visuals import doc_highlight as dh

# 表格：一列一行，數字靠右對齊（右緣 x=230、290、350），欄寬 60pt
ROWS = [  # (標籤, y 基線, 三格)
    ("Header", 260, ("1", "2", "3")),
    ("Assets(B)", 220, ("222", "390", "684")),
    ("Total", 180, ("77", "493", "5,133")),
    ("Other", 140, ("77", "12", "8")),
]


def _pdf_bytes() -> bytes:
    def right(text: str, edge: float) -> float:
        return edge - 6.67 * len(text)  # Helvetica 12pt 的數字寬約 6.67pt

    ops = ["BT /F1 12 Tf 150 290 Td (Group) Tj ET"]
    for label, y, cells in ROWS:
        ops.append(f"BT /F1 12 Tf 20 {y} Td ({label}) Tj ET")
        for text, edge in zip(cells, (230, 290, 350), strict=True):
            ops.append(f"BT /F1 12 Tf {right(text, edge):.2f} {y} Td ({text}) Tj ET")
    stream = "\n".join(ops).encode("latin-1")
    objects = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 400 320] /Contents 4 0 R"
        b" /Resources << /Font << /F1 5 0 R >> >> >>",
        b"<< /Length %d >>\nstream\n" % len(stream) + stream + b"\nendstream",
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
    ]
    out = bytearray(b"%PDF-1.4\n")
    offsets = []
    for i, body in enumerate(objects, start=1):
        offsets.append(len(out))
        out += b"%d 0 obj\n" % i + body + b"\nendobj\n"
    xref = len(out)
    out += b"xref\n0 %d\n0000000000 65535 f \n" % (len(objects) + 1)
    for off in offsets:
        out += b"%010d 00000 n \n" % off
    out += b"trailer\n<< /Size %d /Root 1 0 R >>\nstartxref\n%d\n%%%%EOF\n" % (
        len(objects) + 1,
        xref,
    )
    return bytes(out)


def _episode(tmp_path: Path, steps: list, *, source: str = "主計總處〈測試報告〉表6") -> dict:
    cues = [
        {"id": i, "start": 100.0 + i * 2.0, "end": 100.0 + i * 2.0 + 2.0, "text": f"第 {i} 句"}
        for i in range(1, 11)
    ]
    row = {
        "id": "row-a",
        "origin": "director",
        "kind": "overlay",
        "category": "doc_highlight",
        "start_cue": 2,
        "end_cue": 7,
        "before_cue": None,
        "insert_sec": None,
        "layout": "full",
        "description": "",
        "dp_instruction": "測試報告第 1 頁",
        "onscreen_text": "",
        "source": source,
        "status": "approved",
        "note": "",
        "program_state": None,
    }
    folder = dh.sources_dir(tmp_path)
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "report.pdf").write_bytes(_pdf_bytes())
    spec = {"document": "report.pdf", "page": 1, "crop": None, "steps": steps}
    (folder / "row-a.json").write_text(json.dumps(spec, ensure_ascii=False), encoding="utf-8")
    return {"schema": "nakama.youtube_visual_plan.v1", "cues": cues, "rows": [row]}


def _doc(tmp_path: Path) -> dh.PdfDocument:
    path = tmp_path / "t.pdf"
    path.write_bytes(_pdf_bytes())
    return dh.PdfDocument(path, 1)


def test_text_is_found_where_it_is_printed(tmp_path):
    x0, y0, x1, y1 = dh.find_text(_doc(tmp_path), {"text": "222"})
    assert 209 < x0 < 211 and 229 < x1 < 231
    assert 320 - 220 - 12 < y0 < y1 < 320 - 220 + 4  # 左上原點：基線 220 → y≈100


def test_row_picks_the_occurrence_on_that_line(tmp_path):
    doc = _doc(tmp_path)
    total = dh.find_text(doc, {"text": "77", "row": "Total"})
    other = dh.find_text(doc, {"text": "77", "row": "Other"})
    assert total[1] < other[1]  # Total 那一列在上面
    assert dh.find_text(doc, {"text": "77", "nth": 2}) == other


def test_column_band_is_one_column_wide(tmp_path):
    doc = _doc(tmp_path)
    (x0, y0, x1, y1), kind = dh.resolve_mark(
        doc, {"column": {"text": "5,133", "row": "Total"}, "top": {"text": "Group"}}
    )
    cell = dh.find_text(doc, {"text": "5,133", "row": "Total"})
    pitch = cell[2] - dh.find_text(doc, {"text": "493", "row": "Total"})[2]  # 相鄰格右緣距離
    assert kind == "column" and 50 < pitch < 65
    assert x1 > cell[2] and x0 < cell[0]
    assert 0.9 * pitch < x1 - x0 < pitch  # 一欄寬，不吃到隔壁欄
    assert y0 < 320 - 260 and y1 > 320 - 180  # 從 Group 下緣劃到 Total 那一列


def test_layout_times_names_and_crop(tmp_path):
    label = {"text": "Total"}
    steps = [
        {"cue": 2, "marks": [{"text": "222"}]},
        {"cue": 4, "marks": [label, {"text": "77", "row": "Total"}]},
        {"cue": 5, "marks": [label, {"text": "5,133", "row": "Total"}]},
    ]
    plan = _episode(tmp_path, steps)
    variables, start, seconds, layout = dh.card_variables(tmp_path, plan, plan["rows"][0])
    assert start == 104.0 and seconds == 12.0
    assert [t for t, _ in layout.steps] == [0.8, 4.0, 6.0]  # 第一步等紙浮上來
    assert layout.steps[1][1][0] == layout.steps[2][1][0]  # 同一個標籤跨步驟沿用、不重劃
    assert len(layout.marks) == 4
    for x, y, w, h, _ in layout.marks.values():
        assert 0 <= x and x + w <= layout.image.width and 0 <= y and y + h <= layout.image.height
    assert 2000 <= layout.image.width <= 2600  # 裁切後渲到約 2400px 寬，推近不糊
    assert variables["img"].startswith("data:image/png;base64,")
    assert variables["source"] == "主計總處〈測試報告〉表6"


@pytest.mark.parametrize(
    "steps, message",
    [
        ([{"cue": 9, "marks": [{"text": "222"}]}], "不在這一列"),
        ([{"cue": 3, "marks": [{"text": "999"}]}], "找不到「999」"),
        ([{"cue": 3, "marks": [{"text": "77", "nth": 5}]}], "沒有第 5 處"),
        (
            [{"cue": 4, "marks": [{"text": "222"}]}, {"cue": 3, "marks": [{"text": "390"}]}],
            "之後",
        ),
        ([{"cue": 3, "marks": [{"what": "x"}]}], "看不懂"),
    ],
)
def test_bad_specs_are_refused(tmp_path, steps, message):
    plan = _episode(tmp_path, steps)
    with pytest.raises(delivery.DeliveryError, match=message):
        dh.card_variables(tmp_path, plan, plan["rows"][0])


def test_last_step_needs_time_on_screen(tmp_path, monkeypatch):
    monkeypatch.setattr(dh, "LAST_HOLD", 3.0)
    plan = _episode(tmp_path, [{"cue": 7, "marks": [{"text": "222"}]}])
    with pytest.raises(delivery.DeliveryError, match="整張只停"):
        dh.card_variables(tmp_path, plan, plan["rows"][0])


@pytest.mark.parametrize(
    "source, message", [("", "空的"), ("https://x.y/z", "網址"), ("甲\n乙", "一行")]
)
def test_source_rules(tmp_path, source, message):
    plan = _episode(tmp_path, [{"cue": 3, "marks": [{"text": "222"}]}], source=source)
    with pytest.raises(delivery.DeliveryError, match=message):
        dh.card_variables(tmp_path, plan, plan["rows"][0])


def test_missing_spec_and_image_without_text(tmp_path):
    plan = _episode(tmp_path, [{"cue": 3, "marks": [{"text": "222"}]}])
    dh.spec_path(tmp_path, "row-a").unlink()
    with pytest.raises(delivery.DeliveryError, match="還沒有文件說明檔"):
        dh.card_variables(tmp_path, plan, plan["rows"][0])
    Image.new("RGB", (800, 400), "white").save(dh.sources_dir(tmp_path) / "shot.png")
    spec = {"document": "shot.png", "steps": [{"cue": 3, "marks": [{"text": "222"}]}]}
    dh.spec_path(tmp_path, "row-a").write_text(json.dumps(spec), encoding="utf-8")
    with pytest.raises(delivery.DeliveryError, match="rect"):
        dh.card_variables(tmp_path, plan, plan["rows"][0])
    spec["steps"] = [{"cue": 3, "marks": [{"rect": [100, 50, 300, 90]}]}]
    dh.spec_path(tmp_path, "row-a").write_text(json.dumps(spec), encoding="utf-8")
    _, _, _, layout = dh.card_variables(tmp_path, plan, plan["rows"][0])
    (x, y, w, h, kind) = next(iter(layout.marks.values()))
    assert kind == "text" and w > 0 and h > 0


def test_check_image_and_deliver(tmp_path):
    plan = _episode(tmp_path, [{"cue": 3, "marks": [{"text": "222"}]}])
    _, _, _, layout = dh.card_variables(tmp_path, plan, plan["rows"][0])
    assert dh.write_check(tmp_path, "row-a", layout).is_file()

    calls: list[list[str]] = []

    def runner(argv, cwd):
        calls.append(argv)
        if argv[0] == "ffmpeg":
            Path(argv[-1]).write_bytes(b"mov")

    receipt, rendered = dh.deliver_row(tmp_path, plan, plan["rows"][0], runner=runner)
    assert rendered and receipt["category"] == "doc_highlight"
    assert receipt["variables"]["img"].startswith("<") and receipt["duration_sec"] == 12.0
    assert calls[0][calls[0].index("-c") + 1] == "compositions/doc_highlight.html"
    assert calls[1][calls[1].index("-t") + 1] == "12.000"
    _, again = dh.deliver_row(tmp_path, plan, plan["rows"][0], runner=runner)
    assert not again and len(calls) == 2


def test_only_approved_doc_highlights_are_built():
    rows = [
        {"id": "a", "category": "doc_highlight", "status": "approved"},
        {"id": "b", "category": "doc_highlight", "status": None},
        {"id": "c", "category": "quote_card", "status": "approved"},
    ]
    assert [r["id"] for r in dh.approved_rows({"rows": rows})] == ["a"]
