"""Template-aware component renderer for staged YouTube thumbnails.

The arrangement layer decides where each component goes. This module decides
how the component should look: paper cards, Jeff-style UI panels, logo tiles,
dark command panels, metric text, arrows, and compact icon marks.
"""

from __future__ import annotations

import math
import re
from pathlib import Path
from typing import Any, Mapping

_CANVAS = (1280, 720)


def draw_thumbnail_components(
    canvas: Any,
    component_plan: Mapping[str, Any] | None,
    *,
    parsed_idea: Any = None,
) -> None:
    """Draw styled components from the selected component plan onto ``canvas``."""

    if not isinstance(component_plan, Mapping):
        return
    selected = component_plan.get("selected_variant")
    if not isinstance(selected, Mapping):
        return
    components = selected.get("components")
    if not isinstance(components, list):
        return

    reference_template_id = str(selected.get("reference_template_id") or "")
    for component in components:
        if not isinstance(component, Mapping):
            continue
        component_type = str(component.get("component_type") or "")
        if component_type == "benefit_list_card":
            _draw_benefit_list_card(canvas, component, parsed_idea=parsed_idea)
        elif component_type == "metric_badge_pair":
            _draw_metric_text(canvas, component)
        elif component_type == "arrow":
            _draw_curved_arrow(canvas, component)
        elif component_type in {"quote_card", "social_post_card"}:
            _draw_quote_card(canvas, component)
        elif component_type == "tool_label":
            _draw_tool_label(canvas, component)
        elif component_type == "ui_panel":
            _draw_ui_panel(canvas, component)
        elif component_type in {"logo_tile", "tool_logo", "simple_icon", "object_cutout"}:
            _draw_icon_tile(canvas, component, variant=component_type)
        elif component_type in {"command_panel", "action_label"}:
            _draw_command_panel(canvas, component)
        elif component_type == "small_badge":
            _draw_small_badge(canvas, component)
        else:
            _draw_generic_payload(canvas, component, reference_template_id=reference_template_id)


def render_component_style_contact_sheet(
    *,
    out_png: Path,
    canvas_size: tuple[int, int] = _CANVAS,
) -> Path:
    """Render a lab sheet that exercises the core component styles."""

    from PIL import Image

    canvas = Image.new("RGBA", canvas_size, (31, 35, 43, 255))
    _draw_lab_background(canvas)
    draw_thumbnail_components(
        canvas,
        {
            "selected_variant": {
                "reference_template_id": "component_style_lab",
                "components": [
                    {
                        "component_type": "benefit_list_card",
                        "box": {"x": 52, "y": 58, "w": 420, "h": 286},
                        "content": ["Brain", "Aging", "Focus"],
                    },
                    {
                        "component_type": "tool_label",
                        "box": {"x": 536, "y": 50, "w": 520, "h": 78},
                        "content": ["NotebookLM"],
                    },
                    {
                        "component_type": "ui_panel",
                        "box": {"x": 536, "y": 154, "w": 520, "h": 230},
                        "content": ["Start Here", "14 sources"],
                    },
                    {
                        "component_type": "logo_tile",
                        "box": {"x": 1080, "y": 154, "w": 134, "h": 134},
                        "content": ["NotebookLM"],
                    },
                    {
                        "component_type": "metric_badge_pair",
                        "box": {"x": 64, "y": 426, "w": 250, "h": 128},
                        "content": ["$0"],
                    },
                    {
                        "component_type": "arrow",
                        "box": {"x": 328, "y": 390, "w": 360, "h": 138},
                        "content": [],
                    },
                    {
                        "component_type": "metric_badge_pair",
                        "box": {"x": 700, "y": 426, "w": 292, "h": 128},
                        "content": ["$1M"],
                    },
                    {
                        "component_type": "command_panel",
                        "box": {"x": 830, "y": 426, "w": 386, "h": 198},
                        "content": ["<trigger>", "DO THIS INSTEAD"],
                    },
                    {
                        "component_type": "quote_card",
                        "box": {"x": 52, "y": 560, "w": 516, "h": 118},
                        "content": ["Stop wasting time."],
                    },
                ],
            }
        },
    )
    out_png.parent.mkdir(parents=True, exist_ok=True)
    canvas.convert("RGB").save(out_png)
    return out_png


def _draw_benefit_list_card(
    canvas: Any,
    component: Mapping[str, Any],
    *,
    parsed_idea: Any,
) -> None:
    from PIL import ImageDraw

    box = _component_box(component, canvas.size)
    if not box:
        return
    content = _benefit_content(component, parsed_idea)[:3]
    x0, y0, x1, y1 = box
    draw = ImageDraw.Draw(canvas, "RGBA")

    _draw_soft_shadow(canvas, box, offset=(18, 22), blur=18, alpha=118)
    draw.rounded_rectangle((x0, y0, x1, y1), radius=22, fill=(255, 248, 218, 250))
    draw.rounded_rectangle((x0, y0, x1, y1), radius=22, outline=(117, 82, 43, 190), width=3)
    _draw_paper_texture(draw, box)

    tape_w = min(192, max(108, (x1 - x0) // 3))
    draw.rounded_rectangle(
        (x0 + 24, y0 - 13, x0 + 24 + tape_w, y0 + 27),
        radius=8,
        fill=(255, 229, 130, 182),
    )
    draw.rounded_rectangle(
        (x0 + 28, y0 + 34, x0 + 45, y1 - 34),
        radius=9,
        fill=(255, 117, 74, 238),
    )

    font = _font(_fit_common_text_size(content, max_width=(x1 - x0) - 170, start=58), bold=True)
    line_gap = max(58, min(78, (y1 - y0 - 108) // max(1, len(content) - 1)))
    line_y = y0 + 66
    for idx, label in enumerate(content):
        baseline = line_y + idx * line_gap
        draw.rounded_rectangle(
            (x0 + 68, baseline + 7, x0 + 103, baseline + 42),
            radius=10,
            fill=(255, 111, 82, 255),
        )
        _draw_check(draw, (x0 + 76, baseline + 14, x0 + 95, baseline + 34), (255, 255, 255, 255))
        draw.text(
            (x0 + 128, baseline - 7),
            label[:8],
            fill=(35, 28, 22, 255),
            font=font,
            stroke_width=1,
            stroke_fill=(255, 253, 238, 210),
        )
        if idx < len(content) - 1:
            draw.line(
                (x0 + 122, baseline + 58, x1 - 56, baseline + 58),
                fill=(151, 112, 70, 48),
                width=2,
            )


def _draw_metric_text(canvas: Any, component: Mapping[str, Any]) -> None:
    from PIL import ImageDraw

    box = _component_box(component, canvas.size)
    if not box:
        return
    text = (_content(component) or ["0"])[0][:8]
    x0, y0, x1, y1 = box
    draw = ImageDraw.Draw(canvas, "RGBA")
    font = _font(_fit_text_size(text, x1 - x0, y1 - y0, start=118, min_size=54), bold=True)
    bbox = draw.textbbox((0, 0), text, font=font, stroke_width=5)
    text_w = bbox[2] - bbox[0]
    text_h = bbox[3] - bbox[1]
    tx = x0 + (x1 - x0 - text_w) // 2
    ty = y0 + (y1 - y0 - text_h) // 2 - 6
    draw.text(
        (tx + 7, ty + 8),
        text,
        font=font,
        fill=(0, 0, 0, 108),
        stroke_width=6,
        stroke_fill=(0, 0, 0, 108),
    )
    draw.text(
        (tx, ty),
        text,
        font=font,
        fill=(255, 255, 255, 255),
        stroke_width=6,
        stroke_fill=(30, 30, 34, 255),
    )


def _draw_curved_arrow(canvas: Any, component: Mapping[str, Any]) -> None:
    from PIL import ImageDraw

    box = _component_box(component, canvas.size)
    if not box:
        return
    x0, y0, x1, y1 = box
    draw = ImageDraw.Draw(canvas, "RGBA")
    points: list[tuple[int, int]] = []
    for i in range(32):
        t = i / 31
        x = round(
            (1 - t) * (1 - t) * (x0 + 16) + 2 * (1 - t) * t * ((x0 + x1) / 2) + t * t * (x1 - 48)
        )
        y = round((1 - t) * (1 - t) * (y1 - 24) + 2 * (1 - t) * t * (y0 + 4) + t * t * (y1 - 36))
        points.append((x, y))
    draw.line([(x + 4, y + 7) for x, y in points], fill=(0, 0, 0, 96), width=19, joint="curve")
    draw.line(points, fill=(49, 223, 77, 255), width=17, joint="curve")
    head = points[-1]
    prev = points[-4]
    angle = math.atan2(head[1] - prev[1], head[0] - prev[0])
    _draw_arrow_head(draw, head, angle, size=42, fill=(49, 223, 77, 255), shadow=True)


def _draw_quote_card(canvas: Any, component: Mapping[str, Any]) -> None:
    from PIL import ImageDraw

    box = _component_box(component, canvas.size)
    if not box:
        return
    x0, y0, x1, y1 = box
    content = _content(component)
    phrase = (content[0] if content else "Start Here")[:34]
    draw = ImageDraw.Draw(canvas, "RGBA")

    _draw_soft_shadow(canvas, box, offset=(12, 16), blur=16, alpha=116)
    draw.rounded_rectangle((x0, y0, x1, y1), radius=24, fill=(255, 255, 255, 250))
    draw.rounded_rectangle((x0, y0, x1, y1), radius=24, outline=(232, 236, 242, 255), width=2)
    avatar_r = min(31, max(20, (y1 - y0) // 8))
    ax = x0 + 34
    ay = y0 + 32
    draw.ellipse((ax, ay, ax + avatar_r * 2, ay + avatar_r * 2), fill=(225, 237, 255, 255))
    draw.ellipse(
        (ax + 8, ay + 8, ax + avatar_r * 2 - 8, ay + avatar_r * 2 - 8), fill=(43, 103, 246, 255)
    )
    draw.text(
        (ax + avatar_r * 2 + 16, ay - 3),
        "Ali Abdaal",
        fill=(17, 24, 39, 255),
        font=_font(26, bold=True),
    )
    draw.rounded_rectangle(
        (ax + avatar_r * 2 + 150, ay + 2, ax + avatar_r * 2 + 172, ay + 24),
        radius=11,
        fill=(37, 99, 235, 255),
    )
    draw.text(
        (x0 + 36, y0 + 86),
        phrase,
        fill=(9, 9, 11, 255),
        font=_font(_fit_text_size(phrase, x1 - x0 - 72, 76, start=48, min_size=28), bold=True),
    )
    draw.line((x0 + 44, y1 - 34, x0 + 74, y1 - 34), fill=(148, 163, 184, 255), width=2)
    draw.line((x0 + 122, y1 - 34, x0 + 156, y1 - 34), fill=(148, 163, 184, 255), width=2)
    draw.line((x0 + 204, y1 - 34, x0 + 246, y1 - 34), fill=(148, 163, 184, 255), width=2)


def _draw_tool_label(canvas: Any, component: Mapping[str, Any]) -> None:
    from PIL import ImageDraw

    box = _component_box(component, canvas.size)
    if not box:
        return
    label = (_content(component) or ["Tool"])[0][:22]
    x0, y0, x1, y1 = box
    draw = ImageDraw.Draw(canvas, "RGBA")
    _draw_soft_shadow(canvas, box, offset=(8, 11), blur=12, alpha=120)
    draw.rounded_rectangle((x0, y0, x1, y1), radius=18, fill=(18, 20, 24, 245))
    draw.rounded_rectangle((x0, y0, x1, y1), radius=18, outline=(255, 255, 255, 218), width=3)
    draw.rounded_rectangle(
        (x0 + 16, y0 + 14, x0 + 52, y1 - 14), radius=12, fill=_accent_for_label(label)
    )
    font = _font(
        _fit_text_size(label, x1 - x0 - 94, y1 - y0 - 16, start=42, min_size=24), bold=True
    )
    draw.text(
        (x0 + 70, y0 + (y1 - y0 - _text_height(label, font)) // 2 - 1),
        label,
        fill=(255, 255, 255, 255),
        font=font,
    )


def _draw_ui_panel(canvas: Any, component: Mapping[str, Any]) -> None:
    from PIL import ImageDraw

    box = _component_box(component, canvas.size)
    if not box:
        return
    x0, y0, x1, y1 = box
    content = _content(component)
    draw = ImageDraw.Draw(canvas, "RGBA")
    _draw_soft_shadow(canvas, box, offset=(16, 19), blur=18, alpha=132)
    draw.rounded_rectangle((x0, y0, x1, y1), radius=22, fill=(247, 249, 252, 252))
    draw.rounded_rectangle((x0, y0, x1, y1), radius=22, outline=(30, 41, 59, 220), width=3)
    draw.rounded_rectangle(
        (x0 + 26, y0 + 26, x1 - 26, y0 + 82),
        radius=16,
        fill=(255, 255, 255, 255),
        outline=(203, 213, 225, 255),
        width=2,
    )
    _draw_gradient_bar(draw, (x0 + 42, y0 + 40, x1 - 42, y0 + 68))
    left = x0 + 34
    top = y0 + 112
    for idx, line in enumerate(content[:3] or ["Start Here"]):
        fill = (255, 134, 36, 255) if idx == 0 else (226, 232, 240, 255)
        text_fill = (24, 25, 28, 255) if idx == 0 else (71, 85, 105, 255)
        card = (left, top + idx * 56, x1 - 34, top + idx * 56 + 42)
        draw.rounded_rectangle(card, radius=12, fill=fill)
        draw.text(
            (card[0] + 22, card[1] + 7), line[:20], fill=text_fill, font=_font(28, bold=idx == 0)
        )
        if idx == 0:
            draw.ellipse(
                (card[2] - 48, card[1] + 12, card[2] - 26, card[1] + 34), fill=(24, 24, 27, 255)
            )
    for idx in range(4):
        y = y1 - 44 + idx * 5
        draw.line((x0 + 42, y, x1 - 42, y), fill=(203, 213, 225, 95), width=2)


def _draw_icon_tile(canvas: Any, component: Mapping[str, Any], *, variant: str) -> None:
    from PIL import ImageDraw

    box = _component_box(component, canvas.size)
    if not box:
        return
    label = (_content(component) or [variant])[0]
    x0, y0, x1, y1 = box
    draw = ImageDraw.Draw(canvas, "RGBA")
    _draw_soft_shadow(canvas, box, offset=(10, 13), blur=14, alpha=112)
    radius = 22 if variant in {"logo_tile", "tool_logo"} else 999
    draw.rounded_rectangle((x0, y0, x1, y1), radius=radius, fill=(255, 255, 255, 248))
    draw.rounded_rectangle((x0, y0, x1, y1), radius=radius, outline=(226, 232, 240, 255), width=3)
    inner = _inset_tuple(box, 0.16, 0.16)
    _draw_icon_mark(draw, inner, label)


def _draw_command_panel(canvas: Any, component: Mapping[str, Any]) -> None:
    from PIL import ImageDraw

    box = _component_box(component, canvas.size)
    if not box:
        return
    content = _content(component)
    x0, y0, x1, y1 = box
    draw = ImageDraw.Draw(canvas, "RGBA")
    _draw_soft_shadow(canvas, box, offset=(16, 20), blur=18, alpha=148)
    draw.rounded_rectangle((x0, y0, x1, y1), radius=10, fill=(23, 27, 34, 252))
    draw.rectangle((x0, y0, x1, y0 + 12), fill=(249, 115, 22, 255))
    draw.rounded_rectangle(
        (x0 + 28, y0 + 32, x1 - 28, y0 + 92),
        radius=6,
        fill=(35, 41, 51, 255),
        outline=(55, 65, 81, 255),
        width=2,
    )
    command = (content[0] if content else "<trigger>")[:20]
    draw.text((x0 + 48, y0 + 46), command, fill=(174, 213, 255, 255), font=_font(34, bold=True))
    label = (content[1] if len(content) > 1 else "DO THIS INSTEAD")[:20]
    label_box = (x0 + 28, y1 - 82, min(x1 - 28, x0 + 440), y1 - 24)
    draw.rectangle(label_box, fill=(37, 99, 235, 255))
    draw.text(
        (label_box[0] + 16, label_box[1] + 9),
        label,
        fill=(4, 13, 31, 255),
        font=_font(32, bold=True),
    )
    for idx, icon_y in enumerate((y0 + 126, y0 + 156, y0 + 186)):
        if icon_y > y1 - 96:
            break
        draw.rounded_rectangle(
            (x0 + 32, icon_y, x0 + 50, icon_y + 18), radius=4, fill=(75, 85, 99, 255)
        )
        draw.line(
            (x0 + 68, icon_y + 9, x0 + 190 + idx * 26, icon_y + 9), fill=(75, 85, 99, 255), width=4
        )


def _draw_small_badge(canvas: Any, component: Mapping[str, Any]) -> None:
    from PIL import ImageDraw

    box = _component_box(component, canvas.size)
    if not box:
        return
    text = (_content(component) or ["6"])[0][:8]
    x0, y0, x1, y1 = box
    draw = ImageDraw.Draw(canvas, "RGBA")
    _draw_soft_shadow(canvas, box, offset=(8, 9), blur=10, alpha=90)
    draw.ellipse((x0, y0, x1, y1), fill=(255, 206, 80, 255), outline=(31, 41, 55, 255), width=5)
    font = _font(_fit_text_size(text, x1 - x0 - 24, y1 - y0 - 20, start=72, min_size=22), bold=True)
    _draw_centered_text(draw, text, box, font, fill=(17, 24, 39, 255), stroke=1)


def _draw_generic_payload(
    canvas: Any,
    component: Mapping[str, Any],
    *,
    reference_template_id: str,
) -> None:
    from PIL import ImageDraw

    box = _component_box(component, canvas.size)
    if not box:
        return
    x0, y0, x1, y1 = box
    content = _content(component)
    dark = reference_template_id.startswith("jeff_")
    fill = (24, 28, 36, 246) if dark else (255, 255, 255, 244)
    text_fill = (255, 255, 255, 255) if dark else (31, 41, 55, 255)
    outline = (255, 255, 255, 230) if dark else (37, 99, 235, 255)
    draw = ImageDraw.Draw(canvas, "RGBA")
    _draw_soft_shadow(canvas, box, offset=(12, 15), blur=14, alpha=105)
    draw.rounded_rectangle((x0, y0, x1, y1), radius=18, fill=fill)
    draw.rounded_rectangle((x0, y0, x1, y1), radius=18, outline=outline, width=4)
    y = y0 + 34
    for line in content[:3] or [str(component.get("component_type") or "payload")]:
        draw.text((x0 + 30, y), line[:18], fill=text_fill, font=_font(40, bold=True))
        y += 58


def _benefit_content(component: Mapping[str, Any], parsed_idea: Any) -> list[str]:
    values: list[str] = []
    values.extend(_content(component))
    values.extend(_iter_text_items(_get(parsed_idea, "component_text", ())))

    visual = str(_get(parsed_idea, "visual", "") or "")
    values.extend(_visual_text_items(visual))

    if len(_dedupe(values)) < 3:
        values.extend(["Cognition", "Strength", "Safety"])
    return _dedupe(values)[:3]


def _draw_lab_background(canvas: Any) -> None:
    from PIL import ImageDraw

    draw = ImageDraw.Draw(canvas, "RGBA")
    width, height = canvas.size
    for y in range(height):
        ratio = y / max(1, height - 1)
        shade = int(32 + ratio * 16)
        draw.line((0, y, width, y), fill=(shade, shade + 4, shade + 9, 255), width=1)
    for x in range(0, width, 80):
        draw.line((x, 0, x, height), fill=(255, 255, 255, 12), width=1)
    for y in range(0, height, 80):
        draw.line((0, y, width, y), fill=(255, 255, 255, 12), width=1)


def _draw_soft_shadow(
    canvas: Any,
    box: tuple[int, int, int, int],
    *,
    offset: tuple[int, int],
    blur: int,
    alpha: int,
) -> None:
    from PIL import Image, ImageDraw, ImageFilter

    x0, y0, x1, y1 = box
    overlay = Image.new("RGBA", canvas.size, (0, 0, 0, 0))
    draw = ImageDraw.Draw(overlay, "RGBA")
    ox, oy = offset
    draw.rounded_rectangle((x0 + ox, y0 + oy, x1 + ox, y1 + oy), radius=24, fill=(0, 0, 0, alpha))
    canvas.alpha_composite(overlay.filter(ImageFilter.GaussianBlur(blur)))


def _draw_paper_texture(draw: Any, box: tuple[int, int, int, int]) -> None:
    x0, y0, x1, y1 = box
    for idx, y in enumerate(range(y0 + 44, y1 - 24, 28)):
        alpha = 20 if idx % 2 == 0 else 12
        draw.line((x0 + 28, y, x1 - 28, y), fill=(133, 104, 66, alpha), width=1)


def _draw_gradient_bar(draw: Any, box: tuple[int, int, int, int]) -> None:
    x0, y0, x1, y1 = box
    colors = ((20, 221, 155), (79, 70, 229), (244, 63, 94))
    width = max(1, x1 - x0)
    for idx in range(width):
        t = idx / max(1, width - 1)
        if t < 0.5:
            local = t / 0.5
            c0, c1 = colors[0], colors[1]
        else:
            local = (t - 0.5) / 0.5
            c0, c1 = colors[1], colors[2]
        color = tuple(
            round(c0[channel] * (1 - local) + c1[channel] * local) for channel in range(3)
        )
        draw.line((x0 + idx, y0, x0 + idx, y1), fill=(*color, 255), width=1)


def _draw_icon_mark(draw: Any, box: tuple[int, int, int, int], label: str) -> None:
    lowered = label.lower()
    if any(token in lowered for token in ("chatgpt", "openai", "gpt")):
        _draw_openai_like_mark(draw, box)
    elif any(token in lowered for token in ("google", "gemini", "flow")):
        _draw_google_like_mark(draw, box)
    elif any(token in lowered for token in ("notebook", "perplexity", "claude")):
        _draw_geometric_tool_mark(draw, box, _accent_for_label(label))
    elif any(token in lowered for token in ("creatine", "supplement", "health", "brain")):
        _draw_health_mark(draw, box)
    else:
        _draw_monogram_mark(draw, box, label)


def _draw_openai_like_mark(draw: Any, box: tuple[int, int, int, int]) -> None:
    x0, y0, x1, y1 = box
    cx = (x0 + x1) / 2
    cy = (y0 + y1) / 2
    radius = min(x1 - x0, y1 - y0) * 0.30
    for idx in range(6):
        angle = idx * math.pi / 3
        px = cx + math.cos(angle) * radius * 0.72
        py = cy + math.sin(angle) * radius * 0.72
        draw.ellipse(
            (px - radius * 0.42, py - radius * 0.42, px + radius * 0.42, py + radius * 0.42),
            outline=(17, 24, 39, 255),
            width=max(3, int(radius * 0.10)),
        )
    draw.ellipse(
        (cx - radius * 0.22, cy - radius * 0.22, cx + radius * 0.22, cy + radius * 0.22),
        fill=(17, 24, 39, 255),
    )


def _draw_google_like_mark(draw: Any, box: tuple[int, int, int, int]) -> None:
    x0, y0, x1, y1 = box
    size = min(x1 - x0, y1 - y0)
    font = _font(max(34, int(size * 0.78)), bold=True)
    tx = x0 + (x1 - x0) * 0.19
    ty = y0 + (y1 - y0) * 0.01
    for offset, color in (
        ((0, 0), (66, 133, 244, 255)),
        ((5, 0), (234, 67, 53, 255)),
        ((0, 5), (251, 188, 5, 255)),
        ((5, 5), (52, 168, 83, 255)),
    ):
        draw.text((tx + offset[0], ty + offset[1]), "G", font=font, fill=color)


def _draw_geometric_tool_mark(
    draw: Any, box: tuple[int, int, int, int], color: tuple[int, int, int, int]
) -> None:
    x0, y0, x1, y1 = box
    cx = (x0 + x1) // 2
    cy = (y0 + y1) // 2
    r = min(x1 - x0, y1 - y0) // 3
    draw.rounded_rectangle((cx - r, cy - r, cx + r, cy + r), radius=10, outline=color, width=8)
    draw.line((cx - r, cy, cx + r, cy), fill=color, width=8)
    draw.line((cx, cy - r, cx, cy + r), fill=color, width=8)
    draw.polygon(
        [(cx, cy - r - 18), (cx + 16, cy), (cx, cy + r + 18), (cx - 16, cy)], fill=(*color[:3], 68)
    )


def _draw_health_mark(draw: Any, box: tuple[int, int, int, int]) -> None:
    x0, y0, x1, y1 = box
    cx = (x0 + x1) // 2
    cy = (y0 + y1) // 2
    r = min(x1 - x0, y1 - y0) // 4
    draw.rounded_rectangle(
        (cx - r, cy - r * 2, cx + r, cy + r * 2),
        radius=r // 2,
        fill=(255, 255, 255, 255),
        outline=(15, 118, 110, 255),
        width=5,
    )
    draw.rectangle((cx - r + 8, cy - 4, cx + r - 8, cy + 4), fill=(15, 118, 110, 255))
    draw.rectangle((cx - 4, cy - r + 8, cx + 4, cy + r - 8), fill=(15, 118, 110, 255))
    draw.ellipse(
        (cx - r - 22, cy - r - 18, cx - 4, cy + r + 18), outline=(20, 184, 166, 255), width=5
    )
    draw.ellipse(
        (cx + 4, cy - r - 18, cx + r + 22, cy + r + 18), outline=(20, 184, 166, 255), width=5
    )


def _draw_monogram_mark(draw: Any, box: tuple[int, int, int, int], label: str) -> None:
    x0, y0, x1, y1 = box
    letter = _monogram(label)
    accent = _accent_for_label(label)
    draw.rounded_rectangle(box, radius=18, fill=accent)
    font = _font(
        _fit_text_size(letter, x1 - x0 - 14, y1 - y0 - 14, start=70, min_size=24), bold=True
    )
    _draw_centered_text(draw, letter, box, font, fill=(255, 255, 255, 255), stroke=0)


def _draw_check(draw: Any, box: tuple[int, int, int, int], fill: tuple[int, int, int, int]) -> None:
    x0, y0, x1, y1 = box
    draw.line((x0, y0 + (y1 - y0) * 0.55, x0 + (x1 - x0) * 0.38, y1), fill=fill, width=4)
    draw.line((x0 + (x1 - x0) * 0.36, y1, x1, y0), fill=fill, width=4)


def _draw_arrow_head(
    draw: Any,
    point: tuple[int, int],
    angle: float,
    *,
    size: int,
    fill: tuple[int, int, int, int],
    shadow: bool = False,
) -> None:
    px, py = point
    left = angle + math.pi * 0.78
    right = angle - math.pi * 0.78
    poly = [
        (px, py),
        (round(px + math.cos(left) * size), round(py + math.sin(left) * size)),
        (
            round(px + math.cos(angle + math.pi) * size * 0.46),
            round(py + math.sin(angle + math.pi) * size * 0.46),
        ),
        (round(px + math.cos(right) * size), round(py + math.sin(right) * size)),
    ]
    if shadow:
        draw.polygon([(x + 5, y + 7) for x, y in poly], fill=(0, 0, 0, 96))
    draw.polygon(poly, fill=fill)


def _draw_centered_text(
    draw: Any,
    text: str,
    box: tuple[int, int, int, int],
    font: Any,
    *,
    fill: tuple[int, int, int, int],
    stroke: int,
) -> None:
    x0, y0, x1, y1 = box
    bbox = draw.textbbox((0, 0), text, font=font, stroke_width=stroke)
    tw = bbox[2] - bbox[0]
    th = bbox[3] - bbox[1]
    draw.text(
        (x0 + (x1 - x0 - tw) // 2, y0 + (y1 - y0 - th) // 2 - 2),
        text,
        font=font,
        fill=fill,
        stroke_width=stroke,
        stroke_fill=(17, 24, 39, 210),
    )


def _component_box(
    component: Mapping[str, Any],
    canvas_size: tuple[int, int],
) -> tuple[int, int, int, int] | None:
    raw = component.get("box")
    if not isinstance(raw, Mapping):
        return None
    width, height = canvas_size
    if {"x0", "y0", "x1", "y1"} <= set(raw):
        x0, y0, x1, y1 = (int(raw["x0"]), int(raw["y0"]), int(raw["x1"]), int(raw["y1"]))
    else:
        x = int(raw.get("x") or 0)
        y = int(raw.get("y") or 0)
        w = int(raw.get("w") or 0)
        h = int(raw.get("h") or 0)
        x0, y0, x1, y1 = x, y, x + w, y + h
    if x1 <= x0 or y1 <= y0:
        return None
    return (
        max(0, min(width - 1, x0)),
        max(0, min(height - 1, y0)),
        max(1, min(width, x1)),
        max(1, min(height, y1)),
    )


def _content(component: Mapping[str, Any]) -> list[str]:
    raw = component.get("content")
    if not isinstance(raw, list):
        return []
    return [str(item).strip() for item in raw if str(item).strip()]


def _visual_text_items(visual: str) -> list[str]:
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


def _fit_common_text_size(
    lines: list[str],
    *,
    max_width: int,
    start: int,
    min_size: int = 28,
) -> int:
    return min(
        _fit_text_size(line, max_width, 80, start=start, min_size=min_size)
        for line in lines or [""]
    )


def _fit_text_size(
    text: str,
    max_width: int,
    max_height: int,
    *,
    start: int,
    min_size: int,
) -> int:
    from PIL import Image, ImageDraw

    probe = Image.new("RGBA", (4, 4), (0, 0, 0, 0))
    draw = ImageDraw.Draw(probe)
    for size in range(start, min_size - 1, -2):
        font = _font(size, bold=True)
        bbox = draw.textbbox((0, 0), text, font=font, stroke_width=4)
        if bbox[2] - bbox[0] <= max_width and bbox[3] - bbox[1] <= max_height:
            return size
    return min_size


def _text_height(text: str, font: Any) -> int:
    from PIL import Image, ImageDraw

    probe = Image.new("RGBA", (4, 4), (0, 0, 0, 0))
    bbox = ImageDraw.Draw(probe).textbbox((0, 0), text, font=font)
    return bbox[3] - bbox[1]


def _inset_tuple(
    box: tuple[int, int, int, int],
    x_ratio: float,
    y_ratio: float,
) -> tuple[int, int, int, int]:
    x0, y0, x1, y1 = box
    dx = round((x1 - x0) * x_ratio)
    dy = round((y1 - y0) * y_ratio)
    return (x0 + dx, y0 + dy, x1 - dx, y1 - dy)


def _accent_for_label(label: str) -> tuple[int, int, int, int]:
    palettes = (
        (37, 99, 235, 255),
        (236, 72, 153, 255),
        (20, 184, 166, 255),
        (249, 115, 22, 255),
        (124, 58, 237, 255),
        (34, 197, 94, 255),
    )
    return palettes[sum(ord(char) for char in label) % len(palettes)]


def _monogram(label: str) -> str:
    for token in re.findall(r"[A-Za-z0-9]+", label):
        return token[:2].upper()
    stripped = label.strip()
    return stripped[:1] or "?"


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
