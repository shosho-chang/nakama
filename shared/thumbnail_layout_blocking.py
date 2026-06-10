"""Layout blocking preview for staged thumbnail production.

This stage runs after person placement and before typography/background/assets.
It intentionally uses simple grayscale regions so composition can be judged
without AI texture, color, or decorative type hiding the hierarchy.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping

_CANVAS = (1280, 720)


@dataclass(frozen=True)
class LayoutBlockingPreview:
    out_png: Path
    canvas_size: tuple[int, int]
    content_side: str
    layout_spec: dict[str, Any]

    def to_dict(self) -> dict[str, Any]:
        return {
            "filename": self.out_png.name,
            "canvas_size": list(self.canvas_size),
            "content_side": self.content_side,
            "layout_spec": self.layout_spec,
        }


def render_layout_blocking_preview(
    *,
    out_png: Path,
    person_placement: Mapping[str, Any],
    parsed_idea: Any,
    director_notes: str = "",
    canvas_size: tuple[int, int] = _CANVAS,
) -> LayoutBlockingPreview:
    """Render a black/white layout blocking artifact."""

    from PIL import Image, ImageDraw, ImageFont

    width, height = canvas_size
    person_box = _box(person_placement.get("cutout_box"), canvas_size)
    face_box = _box(person_placement.get("face_box"), canvas_size)
    focal_point = _point(person_placement.get("focal_point"), canvas_size)
    person_center = (person_box[0] + person_box[2]) / 2
    content_side = "right" if person_center < width / 2 else "left"
    regions = _build_regions(content_side, person_box, parsed_idea, canvas_size)

    img = Image.new("RGB", canvas_size, (238, 241, 244))
    draw = ImageDraw.Draw(img)
    font = ImageFont.load_default()

    _draw_grid(draw, canvas_size)
    _draw_region(
        draw, regions["negative_space"], "NEGATIVE SPACE", (226, 231, 236), (150, 158, 166)
    )
    _draw_region(draw, person_box, "PERSON", (47, 52, 56), (20, 24, 28), text_fill=(255, 255, 255))
    if face_box:
        _draw_outline(draw, face_box, "FACE", (255, 122, 89), font)
    if focal_point:
        fx, fy = focal_point
        draw.ellipse((fx - 8, fy - 8, fx + 8, fy + 8), fill=(255, 122, 89))

    _draw_region(draw, regions["headline_region"], "HEADLINE", (255, 255, 255), (28, 32, 36))
    _draw_region(
        draw, regions["asset_region"], regions["asset_label"], (210, 216, 224), (28, 32, 36)
    )

    note = _summarize(parsed_idea, director_notes)
    draw.rectangle(
        (24, height - 74, width - 24, height - 24), fill=(255, 255, 255), outline=(184, 192, 200)
    )
    draw.text((40, height - 58), note[:155], fill=(31, 41, 55), font=font)

    out_png.parent.mkdir(parents=True, exist_ok=True)
    img.save(out_png)
    return LayoutBlockingPreview(
        out_png=out_png,
        canvas_size=canvas_size,
        content_side=content_side,
        layout_spec={
            "person_region": _dict_box(person_box),
            "face_region": _dict_box(face_box) if face_box else None,
            "focal_point": list(focal_point) if focal_point else None,
            "headline_region": _dict_box(regions["headline_region"]),
            "asset_region": _dict_box(regions["asset_region"]),
            "negative_space": _dict_box(regions["negative_space"]),
            "content_side": content_side,
            "overlap_allowed": True,
            "safe_margin": 24,
            "asset_label": regions["asset_label"],
            "director_notes": director_notes,
        },
    )


def _build_regions(
    content_side: str,
    person_box: tuple[int, int, int, int],
    parsed_idea: Any,
    canvas_size: tuple[int, int],
) -> dict[str, tuple[int, int, int, int] | str]:
    width, height = canvas_size
    if content_side == "right":
        content = (650, 52, width - 44, height - 96)
    else:
        content = (44, 52, 630, height - 96)

    x0, y0, x1, y1 = content
    headline = (x0, y0 + 22, x1, y0 + 224)
    asset = (x0 + 28, y0 + 286, x1 - 8, y1 - 22)
    if not _needs_asset_card(parsed_idea):
        asset = (x0 + 52, y0 + 318, x1 - 24, y1 - 34)

    # Let the person overlap the content column slightly; that is often the
    # Ali/Jeff look. Negative space is where later typography/assets can breathe.
    negative = (x0, y0, x1, y1)
    return {
        "headline_region": headline,
        "asset_region": asset,
        "negative_space": negative,
        "asset_label": "ASSET CARD" if _needs_asset_card(parsed_idea) else "SUPPORTING VISUAL",
        "person_region": person_box,
    }


def _needs_asset_card(parsed_idea: Any) -> bool:
    text = " ".join(
        [
            str(_get(parsed_idea, "visual", "")),
            str(_get(parsed_idea, "decoration", "")),
            " ".join(str(q) for q in (_get(parsed_idea, "asset_queries", ()) or ())),
        ]
    ).lower()
    return any(
        token in text
        for token in ("card", "list", "note", "sticky", "checklist", "清單", "卡", "便條")
    )


def _summarize(parsed_idea: Any, director_notes: str) -> str:
    hook = str(_get(parsed_idea, "hook", "") or "").strip()
    recipe = str(_get(parsed_idea, "recipe_id", "") or "").strip()
    note = director_notes.strip()
    parts = [
        part
        for part in (
            f"hook={hook}",
            f"recipe={recipe}" if recipe else "",
            f"feedback={note}" if note else "",
        )
        if part
    ]
    return " / ".join(parts)


def _draw_grid(draw: object, canvas_size: tuple[int, int]) -> None:
    width, height = canvas_size
    for x in range(0, width, 80):
        draw.line((x, 0, x, height), fill=(220, 226, 232), width=1)
    for y in range(0, height, 80):
        draw.line((0, y, width, y), fill=(220, 226, 232), width=1)
    draw.line((width // 3, 0, width // 3, height), fill=(121, 135, 150), width=2)
    draw.line((2 * width // 3, 0, 2 * width // 3, height), fill=(121, 135, 150), width=2)
    draw.rectangle((0, 0, width - 1, height - 1), outline=(148, 163, 184), width=2)


def _draw_region(
    draw: object,
    box: tuple[int, int, int, int],
    label: str,
    fill: tuple[int, int, int],
    outline: tuple[int, int, int],
    *,
    text_fill: tuple[int, int, int] = (31, 41, 55),
) -> None:
    x0, y0, x1, y1 = box
    draw.rounded_rectangle((x0, y0, x1, y1), radius=8, fill=fill, outline=outline, width=3)
    draw.text((x0 + 18, y0 + 16), label, fill=text_fill)


def _draw_outline(
    draw: object,
    box: tuple[int, int, int, int],
    label: str,
    color: tuple[int, int, int],
    font: object,
) -> None:
    x0, y0, x1, y1 = box
    draw.rectangle((x0, y0, x1, y1), outline=color, width=3)
    draw.text((x0 + 8, max(4, y0 - 16)), label, fill=color, font=font)


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


def _point(value: Any, canvas_size: tuple[int, int]) -> tuple[int, int] | None:
    width, height = canvas_size
    if not isinstance(value, (list, tuple)) or len(value) != 2:
        return None
    x, y = [int(v) for v in value]
    return (max(0, min(width - 1, x)), max(0, min(height - 1, y)))


def _dict_box(box: tuple[int, int, int, int]) -> dict[str, int]:
    x0, y0, x1, y1 = box
    return {"x": x0, "y": y0, "w": x1 - x0, "h": y1 - y0}


def _get(obj: Any, key: str, default: Any = None) -> Any:
    if isinstance(obj, Mapping):
        return obj.get(key, default)
    return getattr(obj, key, default)
