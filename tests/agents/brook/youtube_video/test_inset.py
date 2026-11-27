"""第 10 類舊片導流、第 13 類梗圖：角落小卡的交件規則。不真的開 hyperframes。"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from PIL import Image

from agents.brook.youtube_video import dp
from agents.brook.youtube_video import layout as ly
from agents.brook.youtube_video.categories import load_categories
from agents.brook.youtube_video.visuals import delivery, inset
from agents.brook.youtube_video.visuals import reframe as rf


def _row(row_id: str, category: str, start_cue: int = 3, end_cue: int = 3, layout="overlay"):
    return {
        "id": row_id,
        "origin": "director",
        "kind": "overlay",
        "category": category,
        "start_cue": start_cue,
        "end_cue": end_cue,
        "before_cue": None,
        "insert_sec": None,
        "layout": layout,
        "description": "",
        "dp_instruction": "修修講 Naval 槓桿的舊片",
        "onscreen_text": "",
        "source": "",
        "status": "approved",
        "note": "",
        "program_state": None,
    }


def _plan(*rows) -> dict:
    cues = [
        {"id": i, "start": i * 2.0, "end": i * 2.0 + 1.8, "text": f"第 {i} 句"}
        for i in range(1, 11)
    ]
    return {"schema": "nakama.youtube_visual_plan.v1", "cues": cues, "rows": list(rows)}


@pytest.fixture
def ep(tmp_path: Path) -> Path:
    folder = tmp_path / "assets" / "inset"
    folder.mkdir(parents=True)
    Image.new("RGB", (1280, 720), (200, 80, 40)).save(folder / "naval.jpg")
    Image.new("RGB", (500, 500), (40, 80, 200)).save(folder / "meme.png")
    Image.new("RGB", (200, 150), (0, 0, 0)).save(folder / "tiny.jpg")
    return tmp_path


def _spec(ep: Path, row_id: str, spec: dict) -> None:
    folder = ep / inset.SOURCES_DIR
    folder.mkdir(exist_ok=True)
    (folder / f"{row_id}.json").write_text(json.dumps(spec), encoding="utf-8")


def _fake_runner(calls: list):
    def run(argv, cwd):
        calls.append(argv)
        if argv[0] == "ffmpeg":
            Path(argv[-1]).write_bytes(b"mov")

    return run


def test_old_video_gets_the_tag_and_meme_does_not(ep):
    old, meme = _row("o1", "old_video"), _row("m1", "meme", 5, 6)
    plan = _plan(old, meme)
    _spec(ep, "o1", {"image": "assets/inset/naval.jpg", "source": "https://youtu.be/x"})
    _spec(ep, "m1", {"image": "assets\\inset\\meme.png", "side": "left"})
    v_old, start, seconds, _ = inset.card_variables(ep, plan, old)
    v_meme, *_ = inset.card_variables(ep, plan, meme)
    assert (v_old["tag"], v_meme["tag"]) == ("延伸觀看", "")
    assert (v_old["side"], v_meme["side"]) == ("right", "left")
    assert v_old["ratio"] == round(1280 / 720, 4) and v_meme["ratio"] == 1.0
    assert v_old["image"].startswith("data:image/jpeg;base64,")
    assert (start, seconds) == (6.0, 3.0)  # 一句 1.8 秒 → 至少停 3 秒


def test_show_seconds_are_clamped():
    assert inset.show_seconds(1.0) == 3.0
    assert inset.show_seconds(3.3) == 3.3
    assert inset.show_seconds(9.0) == 4.0


def test_deliver_writes_receipt_and_reuses_it(ep):
    row = _row("o1", "old_video")
    plan = _plan(row)
    _spec(ep, "o1", {"image": "assets/inset/naval.jpg", "source": "https://youtu.be/x"})
    calls: list = []
    receipt, rendered = inset.deliver_row(ep, plan, row, runner=_fake_runner(calls))
    assert rendered and calls[0][2] == inset.HYPERFRAMES
    assert receipt["category"] == "old_video" and receipt["component"] == "inset_card"
    assert receipt["image_file"] == "assets/inset/naval.jpg"
    assert receipt["source"] == "https://youtu.be/x"
    assert receipt["side"] == "right"
    assert "chars data URI" in receipt["variables"]["image"]
    assert inset.deliver_row(ep, plan, row, runner=_fake_runner(calls))[1] is False
    # 換圖 → 重渲
    _spec(ep, "o1", {"image": "assets/inset/meme.png"})
    assert inset.deliver_row(ep, plan, row, runner=_fake_runner(calls))[1] is True


@pytest.mark.parametrize("side, kind", [("right", "nudge_left"), ("left", "nudge_right")])
def test_layout_nudges_the_face_away_from_the_card(ep, side, kind):
    row = _row("o1", "meme")
    plan = _plan(row)
    _spec(ep, "o1", {"image": "assets/inset/naval.jpg", "side": side})
    inset.deliver_row(ep, plan, row, runner=_fake_runner([]))
    layout = ly.load_layout(ep, plan)
    assert [o.row_id for o in layout.overlays] == ["o1"]
    assert ly.moves(layout) == [rf.Move(kind, 6.0, 9.0)]
    assert [m.kind for m in rf.moves_for_receipts(ep)] == [kind]


def test_nudge_is_milder_than_the_book_cover_move():
    face = rf.Anchor(0.5, 0.4)
    zoom, face_x = rf.KINDS["nudge_left"]
    assert (zoom, face_x) == (1.3, 0.4) and zoom < rf.KINDS["left"][0]
    # 臉在正中時移到 40%，不露黑邊（1.3 倍最多能平移 0.3）
    assert rf.final_shift("nudge_left", face) == pytest.approx(-0.25)
    assert face.x * zoom + rf.final_shift("nudge_left", face) == pytest.approx(0.4)
    assert face.x * zoom + rf.final_shift("nudge_right", face) == pytest.approx(0.6)


@pytest.mark.parametrize(
    "spec, message",
    [
        ({}, "要寫 image"),
        ({"image": "naval.jpg"}, "assets/inset/ 裡"),
        ({"image": "E:/naval.jpg"}, "assets/inset/ 裡"),
        ({"image": "assets/inset/../naval.jpg"}, "assets/inset/ 裡"),
        ({"image": "assets/inset/none.jpg"}, "要先搬進這一集的資料夾"),
        ({"image": "assets/inset/clip.mp4"}, "要是圖檔"),
        ({"image": "assets/inset/tiny.jpg"}, "圖太小"),
        ({"image": "assets/inset/naval.jpg", "side": "top"}, "right 或 left"),
    ],
)
def test_bad_specs_are_refused(ep, spec, message):
    row = _row("o1", "old_video")
    _spec(ep, "o1", spec)
    with pytest.raises(delivery.DeliveryError, match=message):
        inset.card_variables(ep, _plan(row), row)


def test_no_spec_points_to_the_skill(ep):
    row = _row("o1", "meme")
    with pytest.raises(delivery.DeliveryError, match="youtube-inset"):
        inset.card_variables(ep, _plan(row), row)


def test_full_screen_is_refused(ep):
    row = _row("o1", "meme", layout="full")
    _spec(ep, "o1", {"image": "assets/inset/meme.png"})
    with pytest.raises(delivery.DeliveryError, match="角落小卡"):
        inset.card_variables(ep, _plan(row), row)


def test_both_categories_are_ready_and_share_the_component():
    categories = load_categories()
    for cat in ("old_video", "meme"):
        assert categories[cat].readiness == "ready"
        assert dp.RENDERERS[cat].endswith("visuals.inset")
