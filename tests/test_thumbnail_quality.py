from __future__ import annotations

from pathlib import Path

from PIL import Image, ImageDraw

from shared.thumbnail_quality import evaluate_thumbnail_render


def _write_high_contrast_thumbnail(path: Path, size: tuple[int, int] = (1280, 720)) -> None:
    img = Image.new("RGB", size, "white")
    draw = ImageDraw.Draw(img)
    draw.rectangle((0, 0, size[0] // 2, size[1]), fill="black")
    draw.rectangle((size[0] // 2, 0, size[0], size[1]), fill=(245, 245, 245))
    draw.rectangle((64, 80, 520, 220), fill="white")
    draw.rectangle((700, 360, 1180, 620), fill=(20, 120, 60))
    path.parent.mkdir(parents=True, exist_ok=True)
    img.save(path)


def test_evaluate_thumbnail_render_passes_valid_1280x720_png(tmp_path: Path):
    path = tmp_path / "thumb.png"
    _write_high_contrast_thumbnail(path)

    result = evaluate_thumbnail_render(path)

    assert result.status == "pass"
    assert result.width == 1280
    assert result.height == 720
    assert {check.check_id for check in result.checks} >= {
        "dimensions",
        "aspect_ratio",
        "luminance_variation",
        "contrast_proxy",
    }


def test_evaluate_thumbnail_render_fails_wrong_dimensions(tmp_path: Path):
    path = tmp_path / "small.png"
    _write_high_contrast_thumbnail(path, size=(640, 360))

    result = evaluate_thumbnail_render(path)

    assert result.status == "fail"
    dimensions = next(check for check in result.checks if check.check_id == "dimensions")
    assert dimensions.status == "fail"
    assert dimensions.value["actual"] == "640x360"


def test_evaluate_thumbnail_render_fails_blank_image(tmp_path: Path):
    path = tmp_path / "blank.png"
    Image.new("RGB", (1280, 720), (128, 128, 128)).save(path)

    result = evaluate_thumbnail_render(path)

    assert result.status == "fail"
    assert any(
        check.check_id == "luminance_variation" and check.status == "fail"
        for check in result.checks
    )


def test_evaluate_thumbnail_render_fails_unreadable_file(tmp_path: Path):
    path = tmp_path / "not-a-real.png"
    path.write_bytes(b"\x89PNG\r\n\x1a\nnot enough")

    result = evaluate_thumbnail_render(path)

    assert result.status == "fail"
    assert result.width is None
    assert any(check.check_id == "image_readable" for check in result.checks)
