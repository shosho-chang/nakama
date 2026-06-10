"""Person placement preview artifact tests."""

from __future__ import annotations

from pathlib import Path

from PIL import Image

from shared.thumbnail_person_preview import (
    placement_spec_for_archetype,
    placement_spec_for_pose,
    render_person_placement_preview,
)


def _cutout(path: Path, size: tuple[int, int] = (240, 420)) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    img = Image.new("RGBA", size, (0, 0, 0, 0))
    for y in range(size[1]):
        for x in range(size[0]):
            if 30 < x < size[0] - 30 and 20 < y < size[1] - 10:
                img.putpixel((x, y), (60, 130, 210, 255))
    img.save(path)


def _face_cutout(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    img = Image.new("RGBA", (420, 900), (0, 0, 0, 0))
    for y in range(130, 900):
        for x in range(80, 360):
            img.putpixel((x, y), (34, 34, 34, 255))
    for y in range(80, 330):
        for x in range(125, 295):
            img.putpixel((x, y), (190, 132, 96, 255))
    img.save(path)


def test_placement_spec_matches_locked_template():
    spec = placement_spec_for_archetype("tv1-face-right")

    assert spec.height_ratio == 1.18
    assert spec.right == 24
    assert spec.max_width == 900
    assert spec.face_height_ratio == 0.55
    assert spec.safe_zone is None


def test_placement_flips_left_when_gaze_points_screen_right():
    spec = placement_spec_for_pose("tv3-split", {"gaze": "up_right"})

    assert spec.anchor == "left"
    assert spec.left == 24
    assert "gaze -> screen right" in spec.note


def test_render_person_placement_preview_writes_1280x720(tmp_path: Path):
    src = tmp_path / "cutout.png"
    _cutout(src)

    preview = render_person_placement_preview(
        cutout_path=src,
        out_png=tmp_path / "preview.png",
        archetype="tv10-number-hero",
        cutout_id="C16",
        pose_tags={"gaze": "up_right"},
    )

    assert preview.out_png.is_file()
    assert preview.spec.archetype == "tv10-number-hero"
    assert preview.spec.anchor == "left"
    assert preview.cutout_box[0] <= 100
    assert preview.cutout_box[2] <= 1280
    with Image.open(preview.out_png) as img:
        assert img.size == (1280, 720)


def test_face_aware_preview_places_face_near_left_third(tmp_path: Path):
    src = tmp_path / "face-cutout.png"
    _face_cutout(src)

    preview = render_person_placement_preview(
        cutout_path=src,
        out_png=tmp_path / "preview.png",
        archetype="tv3-split",
        cutout_id="C_FACE",
        pose_tags={"gaze": "up_right"},
    )

    assert preview.focal_point is not None
    assert abs(preview.focal_point[0] - int(1280 * 0.30)) <= 2
    assert abs(preview.focal_point[1] - int(720 * 0.34)) <= 2
    assert preview.face_box is not None
    face_center_x = (preview.face_box[0] + preview.face_box[2]) / 2
    assert abs(face_center_x - preview.focal_point[0]) <= 70
