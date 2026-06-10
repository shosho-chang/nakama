"""Typography preview artifact tests."""

from __future__ import annotations

from pathlib import Path

from PIL import Image, ImageChops, ImageDraw, ImageStat

from shared.thumbnail_typography_preview import render_typography_preview


def test_typography_preview_renders_large_bottom_headline(tmp_path: Path):
    cutout = tmp_path / "host.png"
    _write_cutout(cutout)

    preview = render_typography_preview(
        out_png=tmp_path / "type.png",
        person_placement={
            "cutout_path": str(cutout),
            "cutout_box": [0, -80, 760, 760],
            "face_box": [205, 60, 540, 450],
            "focal_point": [360, 250],
        },
        layout_blocking={
            "layout_spec": {
                "headline_region": {"x": 650, "y": 74, "w": 586, "h": 202},
                "asset_region": {"x": 680, "y": 340, "w": 520, "h": 260},
            }
        },
        parsed_idea={
            "hook": "不只是增肌",
            "visual": "evidence card",
            "decoration": "",
        },
        director_notes="use the old warm glowing bottom text style",
    )

    assert preview.out_png.is_file()
    spec = preview.typography_spec
    assert spec["mode"] == "bottom_headline"
    assert spec["style"] == "shosho_warm_handwritten_glow"
    assert spec["primary_text"] == "不只是增肌"
    assert spec["allow_person_overlap"] is True
    assert spec["lines"][0]["font_size"] >= 100
    assert spec["lines"][0]["y"] >= 470
    assert spec["lines"][0]["fill"].startswith("#fff")

    with Image.open(preview.out_png) as img:
        assert img.size == (1280, 720)
        bottom = img.crop((0, 500, 1280, 720)).convert("L")
        assert ImageStat.Stat(bottom).stddev[0] > 18


def test_typography_preview_can_skip_headline_from_notes(tmp_path: Path):
    preview = render_typography_preview(
        out_png=tmp_path / "type.png",
        person_placement={"cutout_box": [620, -40, 1280, 760]},
        layout_blocking=None,
        parsed_idea={"hook": "不用大字"},
        director_notes="no text",
    )

    assert preview.typography_spec["mode"] == "no_headline"
    assert preview.typography_spec["lines"] == []


def test_typography_preview_preserves_oversized_negative_person_box(tmp_path: Path):
    cutout = tmp_path / "gradient-host.png"
    _write_vertical_gradient_cutout(cutout)

    preview = render_typography_preview(
        out_png=tmp_path / "type.png",
        person_placement={
            "cutout_path": str(cutout),
            "cutout_box": [-100, -100, 900, 1340],
        },
        layout_blocking=None,
        parsed_idea={"hook": "不要字"},
        director_notes="no text",
    )

    with Image.open(preview.out_png) as img:
        # If the negative/oversized box is incorrectly clamped before resizing,
        # this pixel comes from the bottom of the source cutout and is much
        # brighter. Correct behavior preserves the original 1000x1440 placement
        # then crops only the visible canvas area.
        assert img.convert("L").getpixel((200, 700)) < 130


def _write_cutout(path: Path) -> None:
    img = Image.new("RGBA", (500, 720), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)
    draw.ellipse((155, 30, 345, 235), fill=(229, 174, 132, 255))
    draw.rounded_rectangle((90, 220, 410, 710), radius=80, fill=(35, 42, 52, 255))
    alpha = img.getchannel("A")
    img.putalpha(ImageChops.multiply(alpha, alpha.point(lambda px: 255 if px else 0)))
    img.save(path)


def _write_vertical_gradient_cutout(path: Path) -> None:
    img = Image.new("RGBA", (500, 720), (0, 0, 0, 255))
    draw = ImageDraw.Draw(img)
    for y in range(720):
        shade = int(y / 719 * 255)
        draw.line((0, y, 500, y), fill=(shade, shade, shade, 255))
    img.save(path)
