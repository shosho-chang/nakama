"""Background plate preview artifact tests."""

from __future__ import annotations

from pathlib import Path

from PIL import Image, ImageDraw, ImageStat

from shared.thumbnail_background_plate import render_background_plate_preview


def test_background_plate_renders_warm_study_with_person_and_type(tmp_path: Path):
    cutout = tmp_path / "host.png"
    _write_cutout(cutout)

    preview = render_background_plate_preview(
        out_png=tmp_path / "background.png",
        person_placement={
            "cutout_path": str(cutout),
            "cutout_box": [-64, -11, 801, 1090],
            "face_box": [218, 46, 550, 442],
            "focal_point": [384, 244],
        },
        layout_blocking={
            "content_side": "right",
            "layout_spec": {
                "content_side": "right",
                "headline_region": {"x": 650, "y": 74, "w": 586, "h": 202},
                "asset_region": {"x": 680, "y": 340, "w": 520, "h": 260},
            },
        },
        parsed_idea={
            "hook": "not just muscle",
            "visual": "warm desk lifestyle with research card",
            "bg": "warm wood desk, bookshelf blurred study room",
            "lane": "Ali Warm Explainer",
        },
        director_notes="less busy behind face",
    )

    assert preview.out_png.is_file()
    assert preview.background_spec["style"] == "warm_study_plate"
    assert preview.background_spec["content_side"] == "right"
    assert preview.background_spec["asset_region"]["x"] >= 650
    assert preview.typography_spec["mode"] == "bottom_headline"
    assert preview.typography_spec["primary_text"] == "not just muscle"

    with Image.open(preview.out_png) as img:
        assert img.size == (1280, 720)
        right_scene = img.crop((760, 80, 1220, 430)).convert("L")
        bottom_type = img.crop((0, 500, 1280, 720)).convert("L")
        assert ImageStat.Stat(right_scene).stddev[0] > 5
        assert ImageStat.Stat(bottom_type).stddev[0] > 18


def test_background_plate_can_choose_clean_lab_style(tmp_path: Path):
    preview = render_background_plate_preview(
        out_png=tmp_path / "background.png",
        person_placement={"cutout_box": [0, 0, 640, 720]},
        layout_blocking=None,
        parsed_idea={"hook": "daily 5g", "bg": "clean laboratory white table"},
    )

    assert preview.background_spec["style"] == "clean_lab_plate"


def test_background_plate_uses_cutout_rgb_when_background_is_preserved(tmp_path: Path):
    cutout = tmp_path / "host-aroll.png"
    _write_cutout_with_preserved_rgb_background(cutout)

    preview = render_background_plate_preview(
        out_png=tmp_path / "background.png",
        person_placement={
            "cutout_path": str(cutout),
            "cutout_box": [0, 0, 640, 720],
            "face_box": [120, 40, 320, 260],
        },
        layout_blocking=None,
        parsed_idea={"hook": "daily 5g", "bg": "warm A-roll studio"},
    )

    assert preview.background_spec["background_source"] == "aroll_cutout_rgb"
    assert preview.background_spec["source_background_path"] == cutout.as_posix()


def _write_cutout(path: Path) -> None:
    img = Image.new("RGBA", (500, 720), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)
    draw.ellipse((155, 30, 345, 235), fill=(229, 174, 132, 255))
    draw.rounded_rectangle((90, 220, 410, 710), radius=80, fill=(35, 42, 52, 255))
    img.save(path)


def _write_cutout_with_preserved_rgb_background(path: Path) -> None:
    img = Image.new("RGBA", (640, 720), (0, 0, 0, 0))
    pixels = img.load()
    for y in range(img.height):
        for x in range(img.width):
            pixels[x, y] = (80 + x % 90, 44 + y % 80, 32 + (x + y) % 70, 1)
    draw = ImageDraw.Draw(img)
    draw.ellipse((155, 30, 345, 235), fill=(229, 174, 132, 255))
    draw.rounded_rectangle((90, 220, 410, 710), radius=80, fill=(35, 42, 52, 255))
    img.save(path)
