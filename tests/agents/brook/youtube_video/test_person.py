"""第 3 類人物彈入：交件規則（照片、去背、臉部定位、名字、左右、收據）。

不真的開 hyperframes／mediapipe。
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from PIL import Image

from agents.brook.youtube_video.visuals import delivery
from agents.brook.youtube_video.visuals import person as pp

FACE = pp.Face(eye_x=400.0, eye_y=170.0, eye_to_chin=205.0, width=780, height=1010)


def _photo(path: Path, *, cut: bool) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    img = Image.new(
        "RGBA" if cut else "RGB", (780, 1010), (200, 150, 120, 255) if cut else (200, 150, 120)
    )
    if cut:  # 左右兩條透明
        for x in list(range(0, 120)) + list(range(660, 780)):
            for y in range(0, 1010, 1):
                img.putpixel((x, y), (0, 0, 0, 0))
    img.save(path)
    return path


def _plan(
    tmp_path: Path, *, source: str, name: str = "Nick Maggiulli", instruction: str = ""
) -> dict:
    cues = [
        {"id": i, "start": i * 2.0, "end": i * 2.0 + 1.8, "text": f"第 {i} 句"}
        for i in range(1, 11)
    ]
    row = {
        "id": "row-a",
        "origin": "director",
        "kind": "overlay",
        "category": "person",
        "start_cue": 3,
        "end_cue": 4,
        "before_cue": None,
        "insert_sec": None,
        "layout": "overlay",
        "description": "",
        "dp_instruction": instruction,
        "onscreen_text": name,
        "source": source,
        "status": "approved",
        "note": "",
        "program_state": None,
    }
    return {"schema": "nakama.youtube_visual_plan.v1", "cues": cues, "rows": [row]}


class FakeRunner:
    """去背：寫一張帶透明的 PNG；ffmpeg：產出交件檔。"""

    def __init__(self):
        self.calls: list[list[str]] = []

    def __call__(self, argv: list[str], cwd: Path) -> None:
        self.calls.append(argv)
        if "remove-background" in argv:
            _photo(Path(argv[argv.index("-o") + 1]), cut=True)
        if argv[0] == "ffmpeg":
            Path(argv[-1]).write_bytes(b"mov")


def fake_measure(_path: Path) -> pp.Face:
    return FACE


def test_layout_matches_reference_face_size_and_eye_position():
    place = pp.layout(FACE, "left")
    scale = pp.EYE_TO_CHIN / FACE.eye_to_chin
    assert place["photo_w"] == pytest.approx(FACE.width * scale, abs=0.1)
    assert place["photo_x"] + FACE.eye_x * scale == pytest.approx(pp.EYE_X, abs=0.1)
    assert place["photo_y"] + FACE.eye_y * scale == pytest.approx(pp.EYE_Y, abs=0.1)
    right = pp.layout(FACE, "right")
    assert right["photo_x"] + FACE.eye_x * scale == pytest.approx(1920 - pp.EYE_X, abs=0.1)


def test_photo_too_short_is_refused():
    short = pp.Face(eye_x=400, eye_y=170, eye_to_chin=205, width=780, height=420)
    with pytest.raises(delivery.DeliveryError, match="照片太短"):
        pp.layout(short, "left")


def test_existing_cutout_is_used_as_is(tmp_path):
    photo = _photo(tmp_path / "nick.png", cut=True)
    plan = _plan(tmp_path, source=str(photo))
    runner = FakeRunner()
    variables, start, seconds = pp.card_variables(
        tmp_path / "ep", plan, plan["rows"][0], runner=runner, measure=fake_measure
    )
    assert runner.calls == []  # 不用再去背
    assert variables["photo"].startswith("data:image/png;base64,")
    assert variables["name"] == "Nick Maggiulli" and variables["side"] == "left"
    assert start == 6.0 and seconds == 3.8


def test_plain_photo_gets_background_removed(tmp_path):
    photo = _photo(tmp_path / "nick.jpg", cut=False)
    plan = _plan(tmp_path, source=str(photo))
    runner = FakeRunner()
    pp.card_variables(tmp_path / "ep", plan, plan["rows"][0], runner=runner, measure=fake_measure)
    (call,) = runner.calls
    assert "remove-background" in call
    assert Path(call[call.index("-o") + 1]) == pp.cutout_path(tmp_path / "ep", "row-a")


def test_right_side_from_dp_instruction(tmp_path):
    photo = _photo(tmp_path / "naval.png", cut=True)
    plan = _plan(tmp_path, source=str(photo), instruction="放右側")
    variables, _, _ = pp.card_variables(
        tmp_path / "ep", plan, plan["rows"][0], runner=FakeRunner(), measure=fake_measure
    )
    assert variables["side"] == "right"


@pytest.mark.parametrize(
    "source, name, message",
    [
        ("", "Nick", "等你提供素材"),
        ("C:/nope/x.png", "Nick", "找不到人物照片"),
        (None, "Nick\nMaggiulli", "只能一行"),
        (None, "這是一個非常非常非常長的人名名字", "太長"),
    ],
)
def test_row_problems_are_reported(tmp_path, source, name, message):
    photo = _photo(tmp_path / "nick.png", cut=True)
    plan = _plan(tmp_path, source=str(photo) if source is None else source, name=name)
    with pytest.raises(delivery.DeliveryError, match=message):
        pp.card_variables(
            tmp_path / "ep", plan, plan["rows"][0], runner=FakeRunner(), measure=fake_measure
        )


def test_deliver_writes_media_and_slim_receipt_then_reuses(tmp_path):
    photo = _photo(tmp_path / "nick.png", cut=True)
    plan = _plan(tmp_path, source=str(photo))
    runner = FakeRunner()
    episode = tmp_path / "ep"
    receipt, rendered = pp.deliver_row(
        episode, plan, plan["rows"][0], runner=runner, measure=fake_measure
    )
    assert rendered
    paths = delivery.asset_paths(episode, "row-a")
    saved = json.loads(paths.receipt.read_text(encoding="utf-8"))
    assert saved == receipt and saved["component"] == "person_pop"
    assert "base64" not in saved["variables"]["photo"]
    render, trim = runner.calls
    assert render[render.index("-c") + 1] == "compositions/person_pop.html"
    assert render[render.index("--fps") + 1] == "30000/1001"
    assert trim[trim.index("-t") + 1] == "3.800"
    assert sorted(p.name for p in paths.media.parent.iterdir()) == ["row-a.json", "row-a.mov"]
    _, again = pp.deliver_row(episode, plan, plan["rows"][0], runner=runner, measure=fake_measure)
    assert not again and len(runner.calls) == 2


def test_only_approved_people_are_built(tmp_path):
    plan = _plan(tmp_path, source="x.png")
    plan["rows"] += [
        dict(plan["rows"][0], id="row-b", status=None),
        dict(plan["rows"][0], id="row-c", category="book_cover"),
    ]
    assert [row["id"] for row in pp.approved_rows(plan)] == ["row-a"]
