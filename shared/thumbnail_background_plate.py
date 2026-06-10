"""Background plate preview for staged thumbnail production.

This stage runs after typography and before visual assets/final render. It
creates a deterministic scene plate from the thumbnail brief, then overlays the
already-approved person placement and headline type so background readability
can be reviewed without introducing asset cards or a full AI render.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping

from shared.thumbnail_typography_preview import draw_person_and_typography

_CANVAS = (1280, 720)


@dataclass(frozen=True)
class BackgroundPlatePreview:
    out_png: Path
    canvas_size: tuple[int, int]
    background_spec: dict[str, Any]
    typography_spec: dict[str, Any]

    def to_dict(self) -> dict[str, Any]:
        return {
            "filename": self.out_png.name,
            "canvas_size": list(self.canvas_size),
            "background_spec": self.background_spec,
            "typography_spec": self.typography_spec,
        }


def render_background_plate_preview(
    *,
    out_png: Path,
    person_placement: Mapping[str, Any],
    layout_blocking: Mapping[str, Any] | None,
    parsed_idea: Any,
    director_notes: str = "",
    canvas_size: tuple[int, int] = _CANVAS,
) -> BackgroundPlatePreview:
    """Render a background plate with person + typography overlay."""

    from PIL import Image

    background_spec = build_background_plate_spec(
        person_placement=person_placement,
        layout_blocking=layout_blocking,
        parsed_idea=parsed_idea,
        director_notes=director_notes,
        canvas_size=canvas_size,
    )
    canvas = Image.new("RGBA", canvas_size, (0, 0, 0, 255))
    _draw_scene_plate(canvas, background_spec)

    typography_spec = draw_person_and_typography(
        canvas=canvas,
        person_placement=person_placement,
        layout_blocking=layout_blocking,
        parsed_idea=parsed_idea,
        director_notes="",
    )

    out_png.parent.mkdir(parents=True, exist_ok=True)
    canvas.convert("RGB").save(out_png)
    return BackgroundPlatePreview(
        out_png=out_png,
        canvas_size=canvas_size,
        background_spec=background_spec,
        typography_spec=typography_spec,
    )


def build_background_plate_spec(
    *,
    person_placement: Mapping[str, Any],
    layout_blocking: Mapping[str, Any] | None,
    parsed_idea: Any,
    director_notes: str = "",
    canvas_size: tuple[int, int] = _CANVAS,
) -> dict[str, Any]:
    """Build a deterministic background brief/spec from the parsed idea."""

    brief = _brief_text(parsed_idea, director_notes)
    style = _choose_style(brief)
    layout_spec = {}
    if isinstance(layout_blocking, Mapping):
        layout_spec = layout_blocking.get("layout_spec") or {}

    asset_region = _box_from_dict(layout_spec.get("asset_region"), canvas_size)
    headline_region = _box_from_dict(layout_spec.get("headline_region"), canvas_size)
    face_region = _box(person_placement.get("face_box"), canvas_size)
    person_region = _visible_box(
        _raw_box(person_placement.get("cutout_box"), canvas_size), canvas_size
    )
    content_side = str(
        layout_spec.get("content_side") or layout_blocking.get("content_side")
        if isinstance(layout_blocking, Mapping)
        else ""
    )
    if content_side not in {"left", "right"}:
        content_side = "right" if person_region[2] < canvas_size[0] * 0.68 else "left"

    source_background_path = _usable_source_background_path(person_placement)

    return {
        "style": style,
        "palette": _palette_for_style(style),
        "source_background_path": source_background_path,
        "background_source": "aroll_cutout_rgb" if source_background_path else "generated_plate",
        "content_side": content_side,
        "asset_region": _dict_box(asset_region) if asset_region else None,
        "headline_region": _dict_box(headline_region) if headline_region else None,
        "face_region": _dict_box(face_region) if face_region else None,
        "person_region": _dict_box(person_region),
        "detail_density": "low_behind_face_and_bottom_type",
        "readability_strategy": [
            "soft depth-of-field scene texture",
            "quiet content-side plate for later assets",
            "bottom luminance kept dark enough for warm headline type",
        ],
        "director_notes": director_notes,
        "source_brief": {
            "bg": str(_get(parsed_idea, "bg", "") or ""),
            "visual": str(_get(parsed_idea, "visual", "") or ""),
            "lane": str(_get(parsed_idea, "lane", "") or ""),
            "recipe_id": str(_get(parsed_idea, "recipe_id", "") or ""),
        },
    }


def _draw_scene_plate(canvas: Any, spec: Mapping[str, Any]) -> None:
    if _draw_aroll_background(canvas, spec):
        _draw_quiet_asset_zone(canvas, spec)
        _draw_vignette(canvas)
        return

    style = str(spec.get("style") or "")
    if style == "warm_study_plate":
        _draw_warm_study(canvas, spec)
    elif style == "clean_lab_plate":
        _draw_clean_lab(canvas, spec)
    elif style == "medical_hallway_plate":
        _draw_medical_hallway(canvas, spec)
    else:
        _draw_neutral_depth(canvas, spec)
    _draw_quiet_asset_zone(canvas, spec)
    _draw_vignette(canvas)


def _draw_aroll_background(canvas: Any, spec: Mapping[str, Any]) -> bool:
    from PIL import Image, ImageEnhance, ImageFilter

    path = Path(str(spec.get("source_background_path") or ""))
    if not path.is_file():
        return False
    try:
        with Image.open(path) as raw:
            source = raw.convert("RGBA")
            if not _has_usable_source_rgb(source):
                return False
            rgb = source.convert("RGB")
    except OSError:
        return False

    fitted = _cover_resize(rgb, canvas.size)
    fitted = fitted.filter(ImageFilter.GaussianBlur(18))
    fitted = ImageEnhance.Color(fitted).enhance(0.82)
    fitted = ImageEnhance.Contrast(fitted).enhance(0.92)
    fitted = ImageEnhance.Brightness(fitted).enhance(0.68)
    canvas.alpha_composite(fitted.convert("RGBA"))
    return True


def _draw_warm_study(canvas: Any, spec: Mapping[str, Any]) -> None:
    from PIL import Image, ImageDraw, ImageFilter

    width, height = canvas.size
    _vertical_gradient(canvas, (58, 42, 31), (130, 82, 45))

    detail = Image.new("RGBA", canvas.size, (0, 0, 0, 0))
    draw = ImageDraw.Draw(detail, "RGBA")
    for shelf_y in (72, 166, 260, 354):
        draw.rounded_rectangle(
            (0, shelf_y, width, shelf_y + 18),
            radius=4,
            fill=(60, 38, 23, 120),
        )
        x = 24 + (shelf_y % 37)
        while x < width:
            book_w = 14 + ((x + shelf_y) % 34)
            book_h = 42 + ((x * 3 + shelf_y) % 62)
            color = _warm_book_color(x + shelf_y)
            draw.rounded_rectangle(
                (x, shelf_y - book_h + 8, x + book_w, shelf_y + 6),
                radius=3,
                fill=color,
            )
            x += book_w + 8 + ((x + shelf_y) % 13)

    draw.rectangle((0, 500, width, height), fill=(111, 67, 34, 210))
    for y in range(520, height, 28):
        draw.line((0, y, width, y + 16), fill=(158, 98, 50, 42), width=5)
    draw.rounded_rectangle((838, 86, 1030, 222), radius=24, fill=(245, 150, 58, 32))
    draw.rounded_rectangle((930, 112, 1195, 286), radius=28, fill=(255, 198, 101, 18))

    canvas.alpha_composite(detail.filter(ImageFilter.GaussianBlur(6)))
    _draw_depth_scrim(canvas, top_alpha=18, bottom_alpha=78, color=(25, 18, 13))


def _draw_clean_lab(canvas: Any, spec: Mapping[str, Any]) -> None:
    from PIL import Image, ImageDraw, ImageFilter

    width, height = canvas.size
    _vertical_gradient(canvas, (207, 220, 226), (118, 144, 155))
    detail = Image.new("RGBA", canvas.size, (0, 0, 0, 0))
    draw = ImageDraw.Draw(detail, "RGBA")
    for x in range(-80, width, 180):
        draw.rounded_rectangle((x, 72, x + 126, 420), radius=14, fill=(255, 255, 255, 58))
    draw.rectangle((0, 500, width, height), fill=(84, 104, 112, 170))
    draw.line((0, 500, width, 500), fill=(255, 255, 255, 80), width=5)
    canvas.alpha_composite(detail.filter(ImageFilter.GaussianBlur(5)))
    _draw_depth_scrim(canvas, top_alpha=8, bottom_alpha=70, color=(29, 43, 49))


def _draw_medical_hallway(canvas: Any, spec: Mapping[str, Any]) -> None:
    from PIL import Image, ImageDraw, ImageFilter

    width, height = canvas.size
    _vertical_gradient(canvas, (157, 184, 176), (70, 91, 88))
    detail = Image.new("RGBA", canvas.size, (0, 0, 0, 0))
    draw = ImageDraw.Draw(detail, "RGBA")
    center_x = width // 2
    for offset in range(0, 520, 80):
        draw.line(
            (center_x - offset, 80 + offset // 3, 0, height), fill=(230, 244, 236, 24), width=3
        )
        draw.line(
            (center_x + offset, 80 + offset // 3, width, height),
            fill=(230, 244, 236, 24),
            width=3,
        )
    draw.rectangle((0, 508, width, height), fill=(55, 70, 68, 170))
    canvas.alpha_composite(detail.filter(ImageFilter.GaussianBlur(5)))
    _draw_depth_scrim(canvas, top_alpha=10, bottom_alpha=82, color=(18, 29, 28))


def _draw_neutral_depth(canvas: Any, spec: Mapping[str, Any]) -> None:
    from PIL import Image, ImageDraw, ImageFilter

    width, height = canvas.size
    _vertical_gradient(canvas, (47, 55, 58), (92, 74, 58))
    detail = Image.new("RGBA", canvas.size, (0, 0, 0, 0))
    draw = ImageDraw.Draw(detail, "RGBA")
    for x in range(0, width, 92):
        alpha = 20 + (x % 4) * 8
        draw.rectangle((x, 0, x + 38, height), fill=(255, 255, 255, alpha))
    draw.rectangle((0, 500, width, height), fill=(47, 38, 31, 160))
    canvas.alpha_composite(detail.filter(ImageFilter.GaussianBlur(12)))
    _draw_depth_scrim(canvas, top_alpha=10, bottom_alpha=82, color=(14, 17, 18))


def _draw_quiet_asset_zone(canvas: Any, spec: Mapping[str, Any]) -> None:
    from PIL import Image, ImageDraw, ImageFilter

    asset = _box_from_dict(spec.get("asset_region"), canvas.size)
    if not asset:
        return
    x0, y0, x1, y1 = asset
    pad = 86
    zone = (
        max(0, x0 - pad),
        max(0, y0 - pad * 2),
        min(canvas.width, x1 + pad),
        min(canvas.height, y1 + pad * 2),
    )
    overlay = Image.new("RGBA", canvas.size, (0, 0, 0, 0))
    draw = ImageDraw.Draw(overlay, "RGBA")
    draw.rectangle(zone, fill=(20, 20, 18, 8))
    canvas.alpha_composite(overlay.filter(ImageFilter.GaussianBlur(90)))


def _draw_vignette(canvas: Any) -> None:
    from PIL import Image, ImageDraw, ImageFilter

    width, height = canvas.size
    overlay = Image.new("RGBA", canvas.size, (0, 0, 0, 0))
    draw = ImageDraw.Draw(overlay, "RGBA")
    draw.rectangle((0, 0, width, 40), fill=(0, 0, 0, 34))
    draw.rectangle((0, height - 120, width, height), fill=(0, 0, 0, 54))
    draw.rectangle((0, 0, 80, height), fill=(0, 0, 0, 30))
    draw.rectangle((width - 90, 0, width, height), fill=(0, 0, 0, 34))
    canvas.alpha_composite(overlay.filter(ImageFilter.GaussianBlur(26)))


def _vertical_gradient(
    canvas: Any, top: tuple[int, int, int], bottom: tuple[int, int, int]
) -> None:
    from PIL import ImageDraw

    width, height = canvas.size
    draw = ImageDraw.Draw(canvas, "RGBA")
    for y in range(height):
        t = y / max(1, height - 1)
        color = tuple(int(top[i] * (1 - t) + bottom[i] * t) for i in range(3))
        draw.line((0, y, width, y), fill=(*color, 255))


def _draw_depth_scrim(
    canvas: Any,
    *,
    top_alpha: int,
    bottom_alpha: int,
    color: tuple[int, int, int],
) -> None:
    from PIL import Image, ImageDraw

    overlay = Image.new("RGBA", canvas.size, (0, 0, 0, 0))
    draw = ImageDraw.Draw(overlay, "RGBA")
    width, height = canvas.size
    for y in range(height):
        t = y / max(1, height - 1)
        alpha = int(top_alpha * (1 - t) + bottom_alpha * t)
        draw.line((0, y, width, y), fill=(*color, alpha))
    canvas.alpha_composite(overlay)


def _usable_source_background_path(person_placement: Mapping[str, Any]) -> str:
    path = Path(str(person_placement.get("cutout_path") or ""))
    if not path.is_file():
        return ""
    try:
        from PIL import Image

        with Image.open(path) as raw:
            source = raw.convert("RGBA")
            if _has_usable_source_rgb(source):
                return path.as_posix()
    except OSError:
        return ""
    return ""


def _has_usable_transparent_rgb(source: Any) -> bool:
    small = source.resize((96, 96))
    raw = small.tobytes()
    pixels = [
        (raw[idx], raw[idx + 1], raw[idx + 2], raw[idx + 3])
        for idx in range(0, len(raw), 4)
        if raw[idx + 3] < 30
    ]
    if len(pixels) < 900:
        return False
    sample = pixels[:: max(1, len(pixels) // 900)]
    channels = list(zip(*[(r, g, b) for r, g, b, _a in sample], strict=False))
    means = [sum(channel) / len(channel) for channel in channels]
    variances = [
        sum((value - mean) ** 2 for value in channel) / len(channel)
        for channel, mean in zip(channels, means, strict=False)
    ]
    avg_mean = sum(means) / 3
    avg_std = (sum(variances) / 3) ** 0.5
    return avg_mean > 18 and avg_std > 8


def _has_usable_source_rgb(source: Any) -> bool:
    if _has_usable_transparent_rgb(source):
        return True
    if source.width < 1000 or source.height < 900:
        return False
    small = source.convert("RGB").resize((96, 96))
    raw = small.tobytes()
    channels = [raw[offset::3] for offset in range(3)]
    means = [sum(channel) / len(channel) for channel in channels]
    variances = [
        sum((value - mean) ** 2 for value in channel) / len(channel)
        for channel, mean in zip(channels, means, strict=False)
    ]
    avg_mean = sum(means) / 3
    avg_std = (sum(variances) / 3) ** 0.5
    return avg_mean > 28 and avg_std > 18


def _cover_resize(image: Any, canvas_size: tuple[int, int]) -> Any:
    from PIL import Image

    width, height = canvas_size
    scale = max(width / image.width, height / image.height)
    resized = image.resize(
        (max(width, int(image.width * scale)), max(height, int(image.height * scale))),
        Image.Resampling.LANCZOS,
    )
    x0 = (resized.width - width) // 2
    y0 = (resized.height - height) // 2
    return resized.crop((x0, y0, x0 + width, y0 + height))


def _warm_book_color(seed: int) -> tuple[int, int, int, int]:
    colors = [
        (164, 91, 45, 90),
        (204, 132, 58, 92),
        (110, 67, 48, 92),
        (185, 156, 104, 84),
        (82, 96, 86, 78),
    ]
    return colors[seed % len(colors)]


def _choose_style(brief: str) -> str:
    lower = brief.lower()
    if any(token in lower for token in ("lab", "laboratory", "實驗", "白底")):
        return "clean_lab_plate"
    if any(token in lower for token in ("hospital", "medical", "clinic", "醫院", "診所")):
        return "medical_hallway_plate"
    if any(
        token in lower
        for token in (
            "warm",
            "desk",
            "wood",
            "bookshelf",
            "study",
            "溫暖",
            "木質",
            "書桌",
            "書架",
            "書房",
        )
    ):
        return "warm_study_plate"
    return "neutral_depth_plate"


def _palette_for_style(style: str) -> dict[str, str]:
    if style == "warm_study_plate":
        return {"top": "#3a2a1f", "bottom": "#82522d", "desk": "#6f4322"}
    if style == "clean_lab_plate":
        return {"top": "#cfdce2", "bottom": "#76909b", "table": "#546870"}
    if style == "medical_hallway_plate":
        return {"top": "#9db8b0", "bottom": "#465b58", "floor": "#374644"}
    return {"top": "#2f373a", "bottom": "#5c4a3a", "floor": "#2f261f"}


def _brief_text(parsed_idea: Any, director_notes: str) -> str:
    return " ".join(
        str(part)
        for part in (
            _get(parsed_idea, "bg", ""),
            _get(parsed_idea, "visual", ""),
            _get(parsed_idea, "lane", ""),
            _get(parsed_idea, "recipe_id", ""),
            director_notes,
        )
        if part
    )


def _raw_box(value: Any, canvas_size: tuple[int, int]) -> tuple[int, int, int, int]:
    width, height = canvas_size
    if not isinstance(value, (list, tuple)) or len(value) != 4:
        return (0, 0, width // 2, height)
    x0, y0, x1, y1 = [int(v) for v in value]
    if x1 <= x0:
        x1 = x0 + 1
    if y1 <= y0:
        y1 = y0 + 1
    return (x0, y0, x1, y1)


def _visible_box(
    box: tuple[int, int, int, int],
    canvas_size: tuple[int, int],
) -> tuple[int, int, int, int]:
    width, height = canvas_size
    x0, y0, x1, y1 = box
    return (
        max(0, min(width - 1, x0)),
        max(0, min(height - 1, y0)),
        max(1, min(width, x1)),
        max(1, min(height, y1)),
    )


def _box(value: Any, canvas_size: tuple[int, int]) -> tuple[int, int, int, int] | None:
    width, height = canvas_size
    if not isinstance(value, (list, tuple)) or len(value) != 4:
        return None
    x0, y0, x1, y1 = [int(v) for v in value]
    return (
        max(0, min(width - 1, x0)),
        max(0, min(height - 1, y0)),
        max(1, min(width, x1)),
        max(1, min(height, y1)),
    )


def _box_from_dict(value: Any, canvas_size: tuple[int, int]) -> tuple[int, int, int, int] | None:
    width, height = canvas_size
    if not isinstance(value, Mapping):
        return None
    try:
        x = int(value["x"])
        y = int(value["y"])
        w = int(value["w"])
        h = int(value["h"])
    except (KeyError, TypeError, ValueError):
        return None
    return (
        max(0, min(width - 1, x)),
        max(0, min(height - 1, y)),
        max(1, min(width, x + w)),
        max(1, min(height, y + h)),
    )


def _dict_box(box: tuple[int, int, int, int]) -> dict[str, int]:
    x0, y0, x1, y1 = box
    return {"x": x0, "y": y0, "w": x1 - x0, "h": y1 - y0}


def _get(obj: Any, key: str, default: Any = None) -> Any:
    if isinstance(obj, Mapping):
        return obj.get(key, default)
    return getattr(obj, key, default)
