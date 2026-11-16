"""第 4 類書封：交件規則（找書封、時間、版面、收據、重渲判斷）。不真的開 hyperframes。"""

from __future__ import annotations

import base64
import json
from pathlib import Path

import pytest
from PIL import Image

from agents.brook.youtube_video.visuals import book_cover as bc
from agents.brook.youtube_video.visuals import delivery


def _cover(path: Path, size=(310, 400), color=(240, 240, 240)) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    Image.new("RGB", size, color).save(path)
    return path


@pytest.fixture
def library(tmp_path) -> Path:
    root = tmp_path / "books"
    _cover(root / "財富階梯" / "cover.jpg")
    return root


def _plan(source: str = "財富階梯", start_cue: int = 3, end_cue: int = 4) -> dict:
    cues = [
        {"id": i, "start": i * 2.0, "end": i * 2.0 + 1.8, "text": f"第 {i} 句"}
        for i in range(1, 11)
    ]
    row = {
        "id": "row-a",
        "origin": "director",
        "kind": "overlay",
        "category": "book_cover",
        "start_cue": start_cue,
        "end_cue": end_cue,
        "before_cue": None,
        "insert_sec": None,
        "layout": "overlay",
        "description": "",
        "dp_instruction": "",
        "onscreen_text": "",
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


def test_cover_from_library_by_book_title(library):
    plan = _plan()
    variables, start, seconds = bc.card_variables(plan, plan["rows"][0], library=library)
    assert start == 6.0 and seconds == 3.8
    assert variables["show_sec"] == 3.8 and variables["side"] == "right"
    assert variables["cover_ratio"] == round(310 / 400, 4)
    head, payload = variables["cover"].split(",", 1)
    assert head == "data:image/jpeg;base64"
    assert base64.b64decode(payload) == (library / "財富階梯" / "cover.jpg").read_bytes()


def test_cover_from_explicit_image_path(tmp_path, library):
    png = _cover(tmp_path / "elsewhere" / "封面.png", size=(500, 500))
    plan = _plan(source=f'"{png}"')
    variables, _, _ = bc.card_variables(plan, plan["rows"][0], library=library)
    assert variables["cover"].startswith("data:image/png;base64,")
    assert variables["cover_ratio"] == 1.0


@pytest.mark.parametrize(
    "source, message",
    [
        ("", "不知道是哪本書"),
        ("沒有這本書", "書庫沒有《沒有這本書》"),
        ("C:/nope/x.jpg", "找不到書封圖檔"),
    ],
)
def test_missing_cover_is_reported(library, source, message):
    plan = _plan(source=source)
    with pytest.raises(delivery.DeliveryError, match=message):
        bc.card_variables(plan, plan["rows"][0], library=library)


def test_book_title_falls_back_to_dp_instruction(library):
    plan = _plan(source="")
    plan["rows"][0]["dp_instruction"] = "講到《財富階梯》時放書封"
    variables, _, _ = bc.card_variables(plan, plan["rows"][0], library=library)
    assert variables["cover"].startswith("data:image/jpeg;base64,")
    assert bc.cover_source({"source": "《財富階梯》"}) == "《財富階梯》"
    assert bc.find_cover("《財富階梯》", library=library).name == "cover.jpg"


def test_duration_is_clamped():
    assert bc.show_seconds(0.6) == bc.MIN_SEC
    assert bc.show_seconds(12.0) == bc.MAX_SEC


def test_full_layout_is_not_designed_yet(library):
    plan = _plan()
    plan["rows"][0]["layout"] = "full"
    with pytest.raises(delivery.DeliveryError, match="滿版書封還沒設計"):
        bc.card_variables(plan, plan["rows"][0], library=library)


def test_deliver_writes_media_and_slim_receipt_then_reuses(tmp_path, library):
    plan = _plan()
    runner = FakeRunner()
    episode = tmp_path / "ep"
    receipt, rendered = bc.deliver_row(
        episode, plan, plan["rows"][0], runner=runner, library=library
    )
    assert rendered
    paths = delivery.asset_paths(episode, "row-a")
    saved = json.loads(paths.receipt.read_text(encoding="utf-8"))
    assert saved == receipt
    assert saved["component"] == "book_cover_frame"
    assert saved["start_sec"] == 6.0 and saved["duration_sec"] == 3.8
    assert "base64" not in saved["variables"]["cover"]  # 收據不存整張圖
    render, trim = runner.calls
    assert render[render.index("-c") + 1] == "compositions/book_cover_frame.html"
    assert "--fps" in render and render[render.index("--fps") + 1] == "30000/1001"
    assert trim[trim.index("-t") + 1] == "3.800"
    assert sorted(p.name for p in paths.media.parent.iterdir()) == ["row-a.json", "row-a.mov"]

    _, again = bc.deliver_row(episode, plan, plan["rows"][0], runner=runner, library=library)
    assert not again and len(runner.calls) == 2


def test_changed_cover_image_triggers_rerender(tmp_path, library):
    plan = _plan()
    runner = FakeRunner()
    episode = tmp_path / "ep"
    bc.deliver_row(episode, plan, plan["rows"][0], runner=runner, library=library)
    _cover(library / "財富階梯" / "cover.jpg", color=(10, 10, 10))  # 換了一張書封
    _, rendered = bc.deliver_row(episode, plan, plan["rows"][0], runner=runner, library=library)
    assert rendered


def test_only_approved_book_covers_are_built():
    plan = _plan()
    other = dict(plan["rows"][0], id="row-b", status=None)
    title = dict(plan["rows"][0], id="row-c", category="concept_title")
    plan["rows"] += [other, title]
    assert [row["id"] for row in bc.approved_rows(plan)] == ["row-a"]
