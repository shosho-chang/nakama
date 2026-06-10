"""Layout blocking preview artifact tests."""

from __future__ import annotations

from pathlib import Path

from PIL import Image

from shared.thumbnail_layout_blocking import render_layout_blocking_preview


def test_layout_blocking_places_content_opposite_left_person(tmp_path: Path):
    preview = render_layout_blocking_preview(
        out_png=tmp_path / "layout.png",
        person_placement={
            "cutout_box": [0, 0, 800, 720],
            "face_box": [220, 40, 552, 440],
            "focal_point": [384, 244],
        },
        parsed_idea={
            "hook": "not just muscle",
            "recipe_id": "ali_warm_evidence_list",
            "visual": "six item list card",
            "decoration": "6(list badge)",
            "asset_queries": ["checklist card"],
        },
        director_notes="card lower",
    )

    assert preview.out_png.is_file()
    assert preview.content_side == "right"
    spec = preview.layout_spec
    assert spec["headline_region"]["x"] >= 640
    assert spec["asset_label"] == "ASSET CARD"
    assert spec["director_notes"] == "card lower"
    with Image.open(preview.out_png) as img:
        assert img.size == (1280, 720)


def test_layout_blocking_mirrors_for_right_person(tmp_path: Path):
    preview = render_layout_blocking_preview(
        out_png=tmp_path / "layout.png",
        person_placement={"cutout_box": [620, 0, 1280, 720]},
        parsed_idea={"hook": "5g", "visual": "scoop and molecule"},
    )

    assert preview.content_side == "left"
    assert preview.layout_spec["headline_region"]["x"] < 100
