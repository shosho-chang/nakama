"""Final deterministic renderer for the staged YouTube thumbnail workflow.

The old Hyperframes path renders from a broad T-V archetype and does not know
about the approved person/layout/component stages. This module renders from the
same stage artifacts the user just reviewed, so Step 6 cannot drift away from
Step 1-5.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Mapping

from shared.thumbnail_background_plate import _draw_scene_plate as _draw_background_scene
from shared.thumbnail_background_plate import build_background_plate_spec
from shared.thumbnail_component_renderer_v2 import draw_thumbnail_components
from shared.thumbnail_typography_preview import draw_person_and_typography

_CANVAS = (1280, 720)


def render_staged_youtube_thumbnail(
    *,
    out_png: Path,
    parsed_idea: Any,
    person_placement: Mapping[str, Any],
    layout_blocking: Mapping[str, Any] | None,
    component_plan: Mapping[str, Any] | None,
    director_notes: str = "",
    canvas_size: tuple[int, int] = _CANVAS,
) -> Path:
    """Render a final candidate from the approved staged artifacts."""

    from PIL import Image

    canvas = Image.new("RGBA", canvas_size, (0, 0, 0, 255))
    background_spec = build_background_plate_spec(
        person_placement=person_placement,
        layout_blocking=layout_blocking,
        parsed_idea=parsed_idea,
        director_notes=director_notes,
        canvas_size=canvas_size,
    )
    _draw_background_scene(canvas, background_spec)
    draw_thumbnail_components(canvas, component_plan, parsed_idea=parsed_idea)
    typography_notes = director_notes
    if not _should_draw_global_typography(component_plan, parsed_idea):
        typography_notes = f"{director_notes} no headline".strip()
    draw_person_and_typography(
        canvas=canvas,
        person_placement=person_placement,
        layout_blocking=layout_blocking,
        parsed_idea=parsed_idea,
        director_notes=typography_notes,
    )

    out_png.parent.mkdir(parents=True, exist_ok=True)
    canvas.convert("RGB").save(out_png)
    return out_png


def _draw_components(
    canvas: Any,
    component_plan: Mapping[str, Any] | None,
    *,
    parsed_idea: Any,
) -> None:
    if not isinstance(component_plan, Mapping):
        return
    selected = component_plan.get("selected_variant")
    if not isinstance(selected, Mapping):
        return
    components = selected.get("components")
    if not isinstance(components, list):
        return
    for component in components:
        if not isinstance(component, Mapping):
            continue
        component_type = str(component.get("component_type") or "")
        if component_type == "benefit_list_card":
            _draw_benefit_list_card(canvas, component, parsed_idea=parsed_idea)
        elif component_type == "metric_badge_pair":
            _draw_metric_badge(canvas, component)
        elif component_type == "arrow":
            _draw_arrow(canvas, component)
        elif component_type in {"tool_label", "ui_panel", "command_panel", "quote_card"}:
            _draw_generic_card(canvas, component, dark=component_type == "command_panel")
        else:
            _draw_generic_card(canvas, component)


def _draw_benefit_list_card(
    canvas: Any,
    component: Mapping[str, Any],
    *,
    parsed_idea: Any,
) -> None:
    from PIL import Image, ImageDraw, ImageFilter

    box = _component_box(component, canvas.size)
    if not box:
        return
    content = _benefit_content(component, parsed_idea)[:3]
    x0, y0, x1, y1 = box
    overlay = Image.new("RGBA", canvas.size, (0, 0, 0, 0))
    draw = ImageDraw.Draw(overlay, "RGBA")
    shadow = (x0 + 20, y0 + 24, x1 + 20, y1 + 24)
    draw.rounded_rectangle(shadow, radius=26, fill=(19, 13, 9, 122))
    overlay = overlay.filter(ImageFilter.GaussianBlur(14))
    canvas.alpha_composite(overlay)

    draw = ImageDraw.Draw(canvas, "RGBA")
    draw.rounded_rectangle((x0, y0, x1, y1), radius=24, fill=(255, 246, 204, 250))
    draw.rounded_rectangle((x0, y0, x1, y1), radius=24, outline=(105, 64, 28, 210), width=3)
    draw.rounded_rectangle(
        (x0 + 22, y0 - 12, x0 + 192, y0 + 28), radius=9, fill=(255, 229, 128, 168)
    )
    draw.rounded_rectangle((x0 + 24, y0 + 26, x0 + 38, y1 - 28), radius=7, fill=(255, 125, 82, 230))

    font = _font(52, bold=True)
    line_gap = 62 if len(content) >= 3 else 78
    line_y = y0 + 56
    for idx, label in enumerate(content):
        baseline = line_y + idx * line_gap
        draw.line(
            (x0 + 100, baseline + 56, x1 - 62, baseline + 56),
            fill=(154, 115, 68, 48),
            width=2,
        )
        draw.ellipse(
            (x0 + 70, baseline + 18, x0 + 96, baseline + 44),
            fill=(255, 111, 82, 255),
        )
        draw.text(
            (x0 + 122, baseline - 4),
            label,
            fill=(38, 31, 24, 255),
            font=font,
            stroke_width=1,
            stroke_fill=(255, 252, 234, 190),
        )


def _draw_metric_badge(canvas: Any, component: Mapping[str, Any]) -> None:
    from PIL import ImageDraw

    box = _component_box(component, canvas.size)
    if not box:
        return
    text = (_content(component) or ["0"])[0]
    x0, y0, x1, y1 = box
    draw = ImageDraw.Draw(canvas, "RGBA")
    draw.rounded_rectangle((x0, y0, x1, y1), radius=18, fill=(255, 255, 255, 244))
    draw.rounded_rectangle((x0, y0, x1, y1), radius=18, outline=(18, 184, 88, 255), width=5)
    draw.text((x0 + 32, y0 + 30), text[:8], fill=(20, 83, 45, 255), font=_font(86, bold=True))


def _draw_arrow(canvas: Any, component: Mapping[str, Any]) -> None:
    from PIL import ImageDraw

    box = _component_box(component, canvas.size)
    if not box:
        return
    x0, y0, x1, y1 = box
    mid_y = (y0 + y1) // 2
    draw = ImageDraw.Draw(canvas, "RGBA")
    draw.line((x0 + 28, mid_y, x1 - 48, mid_y), fill=(21, 190, 91, 255), width=15)
    draw.polygon(
        [(x1 - 52, mid_y - 32), (x1 - 52, mid_y + 32), (x1 - 10, mid_y)],
        fill=(21, 190, 91, 255),
    )


def _draw_generic_card(canvas: Any, component: Mapping[str, Any], *, dark: bool = False) -> None:
    from PIL import ImageDraw

    box = _component_box(component, canvas.size)
    if not box:
        return
    x0, y0, x1, y1 = box
    content = _content(component)
    fill = (24, 28, 36, 246) if dark else (255, 255, 255, 244)
    text_fill = (255, 255, 255, 255) if dark else (31, 41, 55, 255)
    outline = (255, 255, 255, 255) if dark else (37, 99, 235, 255)
    draw = ImageDraw.Draw(canvas, "RGBA")
    draw.rounded_rectangle((x0, y0, x1, y1), radius=18, fill=fill)
    draw.rounded_rectangle((x0, y0, x1, y1), radius=18, outline=outline, width=4)
    y = y0 + 42
    for line in content[:3] or [str(component.get("component_type") or "payload")]:
        draw.text((x0 + 34, y), line[:18], fill=text_fill, font=_font(42, bold=True))
        y += 62


def _component_box(
    component: Mapping[str, Any],
    canvas_size: tuple[int, int],
) -> tuple[int, int, int, int] | None:
    raw = component.get("box")
    if not isinstance(raw, Mapping):
        return None
    width, height = canvas_size
    x = int(raw.get("x") or 0)
    y = int(raw.get("y") or 0)
    w = int(raw.get("w") or 0)
    h = int(raw.get("h") or 0)
    if w <= 0 or h <= 0:
        return None
    return (
        max(0, min(width - 1, x)),
        max(0, min(height - 1, y)),
        max(1, min(width, x + w)),
        max(1, min(height, y + h)),
    )


def _content(component: Mapping[str, Any]) -> list[str]:
    raw = component.get("content")
    if not isinstance(raw, list):
        return []
    return [str(item).strip() for item in raw if str(item).strip()]


def _benefit_content(component: Mapping[str, Any], parsed_idea: Any) -> list[str]:
    values: list[str] = []
    values.extend(_content(component))
    values.extend(_iter_text_items(_get(parsed_idea, "component_text", ())))

    visual = str(_get(parsed_idea, "visual", "") or "")
    values.extend(_visual_text_items(visual))

    if len(_dedupe(values)) < 3:
        values.extend(["認知", "增力", "安全"])
    return _dedupe(values)[:3]


def _visual_text_items(visual: str) -> list[str]:
    import re

    items: list[str] = []
    match = re.search(r"(?:^|[;\s])text\s*=\s*([^;]+)", visual, flags=re.IGNORECASE)
    if match:
        items.extend(_split_label_text(match.group(1)))
    return items


def _iter_text_items(value: Any) -> list[str]:
    if isinstance(value, str):
        return _split_label_text(value)
    if isinstance(value, (list, tuple)):
        items: list[str] = []
        for item in value:
            items.extend(_iter_text_items(item))
        return items
    return []


def _split_label_text(value: str) -> list[str]:
    import re

    return [
        token.strip()
        for token in re.split(r"\s*(?:/|、|,|，|\||・|;|；)\s*", value)
        if token.strip()
    ]


def _dedupe(values: list[str]) -> list[str]:
    out: list[str] = []
    seen: set[str] = set()
    for value in values:
        normalized = " ".join(str(value).strip().split())
        if not normalized:
            continue
        key = normalized.casefold()
        if key in seen:
            continue
        seen.add(key)
        out.append(normalized[:8])
    return out


def _font(size: int, *, bold: bool = False) -> Any:
    from PIL import ImageFont

    candidates = (
        (
            "C:/Windows/Fonts/msjhbd.ttc",
            "C:/Windows/Fonts/msjh.ttc",
            "C:/Windows/Fonts/arialbd.ttf",
        )
        if bold
        else (
            "C:/Windows/Fonts/msjh.ttc",
            "C:/Windows/Fonts/arial.ttf",
        )
    )
    for candidate in candidates:
        try:
            return ImageFont.truetype(candidate, size)
        except OSError:
            continue
    return ImageFont.load_default()


def _get(obj: Any, key: str, default: Any = None) -> Any:
    if isinstance(obj, Mapping):
        return obj.get(key, default)
    return getattr(obj, key, default)


def _should_draw_global_typography(
    component_plan: Mapping[str, Any] | None,
    parsed_idea: Any,
) -> bool:
    """Only Shosho/Ali benefit-list layouts use the handwritten bottom headline.

    Jeff-style templates and Ali metric/quote templates carry their text inside
    the component itself. Drawing the global Shosho headline over them creates
    the giant unrelated bottom text seen in failed renders.
    """

    template_id = ""
    if isinstance(component_plan, Mapping):
        selected = component_plan.get("selected_variant")
        if isinstance(selected, Mapping):
            template_id = str(selected.get("reference_template_id") or "")
    if not template_id:
        template_id = str(_get(parsed_idea, "reference_template_id", "") or "")
    return template_id in {"", "shosho_benefit_list_card"}
