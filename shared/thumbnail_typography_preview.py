"""Typography preview for staged thumbnail production.

This stage runs after layout blocking and before background/assets/final render.
It renders only the chosen person placement plus a deterministic headline style
so type size, position, stroke, glow, and color can be reviewed in isolation.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping

_CANVAS = (1280, 720)
_BOTTOM_TEXT_REGION = (34, 474, 1246, 694)


@dataclass(frozen=True)
class TypographyPreview:
    out_png: Path
    canvas_size: tuple[int, int]
    typography_spec: dict[str, Any]

    def to_dict(self) -> dict[str, Any]:
        return {
            "filename": self.out_png.name,
            "canvas_size": list(self.canvas_size),
            "typography_spec": self.typography_spec,
        }


def render_typography_preview(
    *,
    out_png: Path,
    person_placement: Mapping[str, Any],
    layout_blocking: Mapping[str, Any] | None,
    parsed_idea: Any,
    director_notes: str = "",
    canvas_size: tuple[int, int] = _CANVAS,
) -> TypographyPreview:
    """Render a person + headline type preview PNG."""

    from PIL import Image

    palette = _choose_palette(parsed_idea, director_notes)
    canvas = Image.new("RGBA", canvas_size, palette["background"])
    _draw_subtle_background(canvas, person_placement, layout_blocking)
    typography_spec = draw_person_and_typography(
        canvas=canvas,
        person_placement=person_placement,
        layout_blocking=layout_blocking,
        parsed_idea=parsed_idea,
        director_notes=director_notes,
    )

    out_png.parent.mkdir(parents=True, exist_ok=True)
    canvas.convert("RGB").save(out_png)

    return TypographyPreview(
        out_png=out_png,
        canvas_size=canvas_size,
        typography_spec=typography_spec,
    )


def draw_person_and_typography(
    *,
    canvas: Any,
    person_placement: Mapping[str, Any],
    layout_blocking: Mapping[str, Any] | None,
    parsed_idea: Any,
    director_notes: str = "",
) -> dict[str, Any]:
    """Draw the approved person placement plus headline type onto ``canvas``.

    Background-oriented stages reuse this function so person scaling/cropping is
    owned by one place. This avoids regressions where later stages clamp the
    oversized Step 1 box and distort the cutout.
    """

    headline = str(_get(parsed_idea, "hook", "") or "").strip()
    palette = _choose_palette(parsed_idea, director_notes)
    mode = "no_headline" if _notes_request_no_headline(director_notes) else "bottom_headline"
    text_region = _scaled_region(_BOTTOM_TEXT_REGION, canvas.size)

    _paste_person(canvas, person_placement)
    _draw_bottom_readability_gradient(canvas, text_region)

    lines: list[dict[str, Any]] = []
    if mode == "bottom_headline" and headline:
        layout = _fit_headline(headline, text_region, palette)
        _draw_headline(canvas, layout, palette)
        lines = [line.to_dict() for line in layout]

    return {
        "mode": mode,
        "style": "shosho_warm_handwritten_glow",
        "primary_text": headline,
        "placement": "bottom",
        "text_region": _dict_box(text_region),
        "allow_person_overlap": True,
        "lines": lines,
        "palette": {
            key: _rgba_to_hex(value)
            for key, value in palette.items()
            if key in {"primary_fill", "secondary_fill", "accent_fill", "stroke", "glow"}
        },
        "font_path": _font_path(),
        "director_notes": director_notes,
    }


@dataclass(frozen=True)
class _LineLayout:
    text: str
    box: tuple[int, int, int, int]
    font_size: int
    stroke_width: int
    fill: tuple[int, int, int, int]
    role: str
    font_path: str

    def to_dict(self) -> dict[str, Any]:
        x0, y0, x1, y1 = self.box
        return {
            "text": self.text,
            "x": x0,
            "y": y0,
            "w": x1 - x0,
            "h": y1 - y0,
            "font_size": self.font_size,
            "stroke_width": self.stroke_width,
            "fill": _rgba_to_hex(self.fill),
            "role": self.role,
        }


def _draw_subtle_background(
    canvas: Any,
    person_placement: Mapping[str, Any],
    layout_blocking: Mapping[str, Any] | None,
) -> None:
    from PIL import ImageDraw

    draw = ImageDraw.Draw(canvas, "RGBA")
    width, height = canvas.size
    for y in range(height):
        shade = int(42 + (y / max(1, height - 1)) * 22)
        draw.line((0, y, width, y), fill=(shade, shade + 7, shade + 10, 255))

    layout_spec = {}
    if isinstance(layout_blocking, Mapping):
        layout_spec = layout_blocking.get("layout_spec") or {}
    for key, color in (
        ("asset_region", (255, 255, 255, 18)),
        ("headline_region", (255, 255, 255, 12)),
    ):
        box = _box_from_dict(layout_spec.get(key), canvas.size)
        if box:
            draw.rounded_rectangle(box, radius=10, outline=color, width=2)

    person_box = _visible_box(
        _raw_box(person_placement.get("cutout_box"), canvas.size), canvas.size
    )
    draw.rectangle(person_box, fill=(0, 0, 0, 34))


def _paste_person(canvas: Any, person_placement: Mapping[str, Any]) -> None:
    from PIL import Image, ImageFilter

    box = _raw_box(person_placement.get("cutout_box"), canvas.size)
    cutout_path = Path(str(person_placement.get("cutout_path") or ""))
    if not cutout_path.is_file():
        return

    x0, y0, x1, y1 = box
    target_size = (max(1, x1 - x0), max(1, y1 - y0))
    with Image.open(cutout_path) as raw:
        cutout = raw.convert("RGBA").resize(target_size, Image.Resampling.LANCZOS)

    shadow = Image.new("RGBA", target_size, (0, 0, 0, 0))
    shadow.putalpha(cutout.getchannel("A").point(lambda px: int(px * 0.34)))
    shadow = shadow.filter(ImageFilter.GaussianBlur(18))
    _alpha_composite_clipped(canvas, shadow, (x0 + 18, y0 + 22))
    _alpha_composite_clipped(canvas, cutout, (x0, y0))


def _draw_bottom_readability_gradient(canvas: Any, region: tuple[int, int, int, int]) -> None:
    from PIL import Image, ImageDraw

    width, height = canvas.size
    overlay = Image.new("RGBA", canvas.size, (0, 0, 0, 0))
    draw = ImageDraw.Draw(overlay, "RGBA")
    start = max(0, region[1] - 70)
    for y in range(start, height):
        progress = (y - start) / max(1, height - start)
        alpha = int(12 + progress * 74)
        draw.line((0, y, width, y), fill=(0, 0, 0, alpha))
    canvas.alpha_composite(overlay)


def _fit_headline(
    headline: str,
    region: tuple[int, int, int, int],
    palette: Mapping[str, tuple[int, int, int, int]],
) -> list[_LineLayout]:
    from PIL import Image, ImageDraw

    font_path = _font_path()
    x0, y0, x1, y1 = region
    max_w = x1 - x0
    max_h = y1 - y0
    line_candidates = _line_candidates(headline)
    probe = Image.new("RGBA", (4, 4), (0, 0, 0, 0))
    draw = ImageDraw.Draw(probe)

    for candidate in line_candidates:
        for size in range(150, 55, -2):
            stroke = max(5, int(size * 0.075))
            font = _load_font(font_path, size)
            boxes = [
                draw.textbbox((0, 0), line, font=font, stroke_width=stroke) for line in candidate
            ]
            widths = [box[2] - box[0] for box in boxes]
            heights = [box[3] - box[1] for box in boxes]
            gap = -max(0, int(size * 0.02)) if len(candidate) > 1 else 0
            total_h = sum(heights) + gap * (len(candidate) - 1)
            if max(widths) <= max_w and total_h <= max_h:
                top = y1 - total_h - 10
                layouts: list[_LineLayout] = []
                cursor_y = max(y0, top)
                for idx, line in enumerate(candidate):
                    role, fill = _line_role_and_fill(line, idx, len(candidate), palette)
                    line_w = widths[idx]
                    line_h = heights[idx]
                    line_x = x0 + (max_w - line_w) // 2
                    layouts.append(
                        _LineLayout(
                            text=line,
                            box=(line_x, cursor_y, line_x + line_w, cursor_y + line_h),
                            font_size=size,
                            stroke_width=stroke,
                            fill=fill,
                            role=role,
                            font_path=font_path,
                        )
                    )
                    cursor_y += line_h + gap
                return layouts

    font = _load_font(font_path, 56)
    stroke = 5
    text = headline[:18].strip()
    box = draw.textbbox((0, 0), text, font=font, stroke_width=stroke)
    line_w = min(max_w, box[2] - box[0])
    line_h = box[3] - box[1]
    line_x = x0 + (max_w - line_w) // 2
    line_y = y1 - line_h - 10
    return [
        _LineLayout(
            text=text,
            box=(line_x, line_y, line_x + line_w, line_y + line_h),
            font_size=56,
            stroke_width=stroke,
            fill=palette["primary_fill"],
            role="primary",
            font_path=font_path,
        )
    ]


def _draw_headline(
    canvas: Any,
    layout: list[_LineLayout],
    palette: Mapping[str, tuple[int, int, int, int]],
) -> None:
    from PIL import Image, ImageDraw, ImageFilter

    glow = Image.new("RGBA", canvas.size, (0, 0, 0, 0))
    glow_draw = ImageDraw.Draw(glow)
    main_draw = ImageDraw.Draw(canvas)

    for line in layout:
        font = _load_font(line.font_path, line.font_size)
        x, y = line.box[0], line.box[1]
        glow_draw.text(
            (x, y),
            line.text,
            font=font,
            fill=palette["glow"],
            stroke_width=line.stroke_width + 8,
            stroke_fill=palette["glow"],
        )

    canvas.alpha_composite(glow.filter(ImageFilter.GaussianBlur(13)))

    for line in layout:
        font = _load_font(line.font_path, line.font_size)
        x, y = line.box[0], line.box[1]
        main_draw.text(
            (x, y),
            line.text,
            font=font,
            fill=line.fill,
            stroke_width=line.stroke_width,
            stroke_fill=palette["stroke"],
        )
        main_draw.text(
            (x + 2, y + 2),
            line.text,
            font=font,
            fill=(255, 255, 255, 54),
            stroke_width=0,
        )


def _choose_palette(
    parsed_idea: Any,
    director_notes: str,
) -> dict[str, tuple[int, int, int, int]]:
    text = " ".join(
        [
            str(_get(parsed_idea, "hook", "")),
            str(_get(parsed_idea, "visual", "")),
            str(_get(parsed_idea, "decoration", "")),
            director_notes,
        ]
    )
    secondary = (183, 253, 255, 255) if _looks_health_or_science(text) else (255, 248, 217, 255)
    accent = (255, 91, 91, 255) if _looks_warning_or_truth(text) else (255, 248, 217, 255)
    return {
        "background": (45, 54, 58, 255),
        "primary_fill": (255, 250, 220, 255),
        "secondary_fill": secondary,
        "accent_fill": accent,
        "stroke": (74, 54, 28, 255),
        "glow": (255, 230, 112, 184),
    }


def _line_role_and_fill(
    line: str,
    index: int,
    total: int,
    palette: Mapping[str, tuple[int, int, int, int]],
) -> tuple[str, tuple[int, int, int, int]]:
    if _looks_warning_or_truth(line):
        return "accent", palette["accent_fill"]
    if total > 1 and index == total - 1:
        return "secondary", palette["secondary_fill"]
    return "primary", palette["primary_fill"]


def _line_candidates(text: str) -> list[list[str]]:
    normalized = " ".join(text.replace("\n", " ").split()).strip()
    if not normalized:
        return [[""]]
    candidates = [[normalized]]
    if len(normalized) > 6:
        candidates.append(_balanced_split(normalized))
    if any(mark in normalized for mark in ("，", "、", "：", "?", "？", "/", "|")):
        split = _punctuation_split(normalized)
        if len(split) == 2:
            candidates.insert(0, split)
    return _dedupe_candidates(candidates)


def _balanced_split(text: str) -> list[str]:
    if " " in text:
        words = text.split()
        midpoint = max(1, len(words) // 2)
        return [" ".join(words[:midpoint]), " ".join(words[midpoint:])]

    target = len(text) // 2
    breakpoints = [idx for idx, char in enumerate(text) if char in "，、：:「」()（）"]
    if breakpoints:
        split_at = min(breakpoints, key=lambda idx: abs(idx - target))
        if text[split_at] in "「」()（）":
            split_at += 1
        else:
            split_at += 1
    else:
        split_at = target
    split_at = max(2, min(len(text) - 2, split_at))
    return [text[:split_at].strip(), text[split_at:].strip()]


def _punctuation_split(text: str) -> list[str]:
    for mark in ("？", "?", "：", "，", "、", "/", "|"):
        if mark in text:
            left, right = text.split(mark, 1)
            left = (left + mark).strip()
            right = right.strip()
            if left and right:
                return [left, right]
    return [text]


def _dedupe_candidates(candidates: list[list[str]]) -> list[list[str]]:
    seen: set[tuple[str, ...]] = set()
    out: list[list[str]] = []
    for candidate in candidates:
        clean = [line for line in candidate if line]
        key = tuple(clean)
        if key and key not in seen:
            seen.add(key)
            out.append(clean)
    return out


def _font_path() -> str:
    candidates = [
        Path("C:/Windows/Fonts/kaiu.ttf"),
        Path("C:/Windows/Fonts/STKAITI.TTF"),
        Path("C:/Windows/Fonts/msjhbd.ttc"),
        Path("C:/Windows/Fonts/msjh.ttc"),
        Path("C:/Windows/Fonts/arialbd.ttf"),
    ]
    for path in candidates:
        if path.is_file():
            return str(path)
    return ""


def _load_font(font_path: str, size: int) -> Any:
    from PIL import ImageFont

    if font_path:
        try:
            return ImageFont.truetype(font_path, size)
        except OSError:
            pass
    return ImageFont.load_default()


def _looks_health_or_science(text: str) -> bool:
    return any(
        token in text.lower()
        for token in (
            "health",
            "science",
            "evidence",
            "study",
            "醫",
            "健康",
            "長壽",
            "抗老",
            "認知",
            "腦",
            "研究",
            "發炎",
        )
    )


def _looks_warning_or_truth(text: str) -> bool:
    return any(
        token in text.lower()
        for token in (
            "truth",
            "wrong",
            "danger",
            "risk",
            "真相",
            "錯",
            "危險",
            "警告",
            "不要",
        )
    )


def _notes_request_no_headline(notes: str) -> bool:
    normalized = notes.strip().lower()
    if not normalized:
        return False
    return any(token in normalized for token in ("no text", "no headline", "無字", "不要字"))


def _scaled_region(
    region: tuple[int, int, int, int],
    canvas_size: tuple[int, int],
) -> tuple[int, int, int, int]:
    width, height = canvas_size
    if canvas_size == _CANVAS:
        return region
    x0, y0, x1, y1 = region
    return (
        int(x0 * width / _CANVAS[0]),
        int(y0 * height / _CANVAS[1]),
        int(x1 * width / _CANVAS[0]),
        int(y1 * height / _CANVAS[1]),
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


def _alpha_composite_clipped(canvas: Any, layer: Any, xy: tuple[int, int]) -> None:
    x, y = xy
    width, height = canvas.size
    dst_x0 = max(0, x)
    dst_y0 = max(0, y)
    dst_x1 = min(width, x + layer.width)
    dst_y1 = min(height, y + layer.height)
    if dst_x1 <= dst_x0 or dst_y1 <= dst_y0:
        return

    src_x0 = dst_x0 - x
    src_y0 = dst_y0 - y
    src_x1 = src_x0 + (dst_x1 - dst_x0)
    src_y1 = src_y0 + (dst_y1 - dst_y0)
    canvas.alpha_composite(layer.crop((src_x0, src_y0, src_x1, src_y1)), (dst_x0, dst_y0))


def _box(value: Any, canvas_size: tuple[int, int]) -> tuple[int, int, int, int]:
    width, height = canvas_size
    if not isinstance(value, (list, tuple)) or len(value) != 4:
        return (0, 0, width // 2, height)
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


def _rgba_to_hex(value: tuple[int, int, int, int]) -> str:
    r, g, b, a = value
    if a == 255:
        return f"#{r:02x}{g:02x}{b:02x}"
    return f"#{r:02x}{g:02x}{b:02x}{a:02x}"


def _get(obj: Any, key: str, default: Any = None) -> Any:
    if isinstance(obj, Mapping):
        return obj.get(key, default)
    return getattr(obj, key, default)
