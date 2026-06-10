"""Component layout planning and deterministic critique for thumbnails.

This is the first non-vision critic loop. It does not claim taste judgment; it
prevents common mechanical failures before a visual candidate reaches the user:
wrong component for the chosen reference template, face overlap, edge crowding,
and too many competing payloads.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping

from shared.thumbnail_reference_templates import get_reference_template

_CANVAS = (1280, 720)


@dataclass(frozen=True)
class ComponentBox:
    component_type: str
    role: str
    box: tuple[int, int, int, int]
    content: tuple[str, ...]
    style: str

    def to_dict(self) -> dict[str, Any]:
        x0, y0, x1, y1 = self.box
        return {
            "component_type": self.component_type,
            "role": self.role,
            "box": {"x": x0, "y": y0, "w": x1 - x0, "h": y1 - y0},
            "content": list(self.content),
            "style": self.style,
        }


@dataclass(frozen=True)
class ComponentVariant:
    variant_id: str
    reference_template_id: str
    components: tuple[ComponentBox, ...]
    score: float
    critique: tuple[str, ...]
    repair_patches: tuple[str, ...]

    def to_dict(self) -> dict[str, Any]:
        return {
            "variant_id": self.variant_id,
            "reference_template_id": self.reference_template_id,
            "score": round(self.score, 2),
            "critique": list(self.critique),
            "repair_patches": list(self.repair_patches),
            "components": [component.to_dict() for component in self.components],
        }


@dataclass(frozen=True)
class ComponentPlanPreview:
    out_png: Path
    canvas_size: tuple[int, int]
    selected_variant: ComponentVariant
    variants: tuple[ComponentVariant, ...]

    def to_dict(self) -> dict[str, Any]:
        return {
            "filename": self.out_png.name,
            "canvas_size": list(self.canvas_size),
            "selected_variant": self.selected_variant.to_dict(),
            "variants": [variant.to_dict() for variant in self.variants],
        }


def render_component_plan_preview(
    *,
    out_png: Path,
    parsed_idea: Any,
    person_placement: Mapping[str, Any],
    layout_blocking: Mapping[str, Any] | None,
    director_notes: str = "",
    canvas_size: tuple[int, int] = _CANVAS,
) -> ComponentPlanPreview:
    """Render a template-specific component blocking preview.

    This is deliberately low-fidelity: it lets the user and later model critic
    inspect whether the selected reference template maps to sane component
    count, size, and face-safe placement before any real Envato/stock asset is
    brought into the composition.
    """

    from PIL import Image, ImageDraw

    variants = tuple(
        plan_component_variants(
            parsed_idea=parsed_idea,
            person_placement=person_placement,
            layout_blocking=layout_blocking,
            canvas_size=canvas_size,
        )
    )
    selected = variants[0]

    img = Image.new("RGB", canvas_size, (226, 220, 208))
    draw = ImageDraw.Draw(img)
    small = _load_font(22)
    body = _load_font(34)
    title_font = _load_font(28, bold=True)

    _draw_warm_blocking_background(draw, canvas_size)
    _draw_thirds(draw, canvas_size)
    person_box = _box(person_placement.get("cutout_box"), canvas_size)
    face_box = _box(person_placement.get("face_box"), canvas_size)
    if person_box:
        _draw_host_blocking(draw, person_box, face_box, title_font, small)
    if face_box:
        draw.rounded_rectangle(face_box, radius=18, outline=(255, 122, 89), width=4)

    for idx, component in enumerate(selected.components):
        _draw_component_sketch(draw, component, idx, title_font, body, small)

    status = "OK" if selected.score >= 80 else "needs repair"
    draw.rounded_rectangle(
        (38, 36, 500, 88), radius=12, fill=(255, 255, 255), outline=(226, 232, 240)
    )
    draw.text((58, 48), f"Step 5 component map - {status}", fill=(31, 41, 55), font=small)
    out_png.parent.mkdir(parents=True, exist_ok=True)
    img.save(out_png)
    return ComponentPlanPreview(
        out_png=out_png,
        canvas_size=canvas_size,
        selected_variant=selected,
        variants=variants,
    )


def plan_component_variants(
    *,
    parsed_idea: Any,
    person_placement: Mapping[str, Any],
    layout_blocking: Mapping[str, Any] | None,
    canvas_size: tuple[int, int] = _CANVAS,
    limit: int = 3,
) -> list[ComponentVariant]:
    """Build and score component variants for the parsed thumbnail idea."""

    template_id = str(_get(parsed_idea, "reference_template_id", "") or "").strip()
    if not template_id:
        template_id = _fallback_template_id(parsed_idea)
    template = get_reference_template(template_id)
    content_side = _content_side(person_placement, layout_blocking, canvas_size)
    regions = _regions(content_side, canvas_size)

    raw_variants = _candidate_variants(
        template_id=template.template_id,
        component_types=template.component_types,
        regions=regions,
        parsed_idea=parsed_idea,
    )
    scored = [
        _score_variant(
            variant_id=f"v{i + 1}",
            reference_template_id=template.template_id,
            components=tuple(components),
            person_placement=person_placement,
            canvas_size=canvas_size,
            allowed_types=template.component_types,
        )
        for i, components in enumerate(raw_variants)
    ]
    scored.sort(key=lambda variant: (-variant.score, variant.variant_id))
    return scored[: max(1, limit)]


def _candidate_variants(
    *,
    template_id: str,
    component_types: tuple[str, ...],
    regions: Mapping[str, tuple[int, int, int, int]],
    parsed_idea: Any,
) -> list[list[ComponentBox]]:
    content = _component_content(parsed_idea)
    main = regions["main"]
    compact = regions["compact"]
    top = regions["top"]

    if "benefit_list_card" in component_types:
        return [
            [
                ComponentBox(
                    "benefit_list_card",
                    "primary_payload",
                    main,
                    tuple(content[:3]),
                    "warm_paper_card",
                )
            ],
            [
                ComponentBox(
                    "benefit_list_card",
                    "primary_payload",
                    compact,
                    tuple(content[:3]),
                    "compact_warm_card",
                ),
                ComponentBox(
                    "small_badge",
                    "evidence_badge",
                    top,
                    (_decoration(parsed_idea) or "研究",),
                    "soft_badge",
                ),
            ],
            [
                ComponentBox(
                    "benefit_list_card",
                    "primary_payload",
                    _nudge(main, dx=-34, dy=18),
                    tuple(content[:3]),
                    "large_low_card",
                )
            ],
        ]

    if "metric_badge_pair" in component_types:
        left, right = regions["left_badge"], regions["right_badge"]
        labels = _metric_pair(parsed_idea)
        return [
            [
                ComponentBox(
                    "metric_badge_pair", "contrast_left", left, (labels[0],), "big_metric"
                ),
                ComponentBox(
                    "metric_badge_pair", "contrast_right", right, (labels[1],), "big_metric"
                ),
                ComponentBox("arrow", "connector", regions["arrow"], ("",), "curved_green_arrow"),
            ]
        ]

    if "tool_label" in component_types or "ui_panel" in component_types:
        label = _tool_label(parsed_idea)
        return [
            [
                ComponentBox("tool_label", "product_name", top, (label,), "black_label"),
                ComponentBox(
                    "ui_panel",
                    "primary_payload",
                    main,
                    _tool_panel_lines(parsed_idea),
                    "clean_panel",
                ),
                ComponentBox(
                    "logo_tile", "supporting_logo", compact, (label[:2].upper(),), "logo_tile"
                ),
            ],
            [
                ComponentBox(
                    "ui_panel",
                    "primary_payload",
                    main,
                    _tool_panel_lines(parsed_idea),
                    "wide_panel",
                ),
                ComponentBox("tool_label", "product_name", top, (label,), "black_label"),
            ],
        ]

    if "command_panel" in component_types:
        labels = _tool_panel_lines(parsed_idea)
        panel_content = labels
        if len(panel_content) == 1:
            panel_content = (panel_content[0], "DO THIS INSTEAD")
        return [
            [
                ComponentBox(
                    "command_panel",
                    "primary_payload",
                    main,
                    panel_content[:2],
                    "dark_command_panel",
                ),
                ComponentBox(
                    "tool_logo",
                    "supporting_logo",
                    top,
                    (_tool_label(parsed_idea)[:8],),
                    "tool_logo",
                ),
            ],
            [
                ComponentBox(
                    "command_panel",
                    "primary_payload",
                    _nudge(main, dx=-18 if main[0] > 600 else 18, dy=-10),
                    panel_content[:2],
                    "dark_command_panel_large",
                )
            ],
        ]

    return [
        [
            ComponentBox(
                "quote_card",
                "primary_payload",
                main,
                (_short_phrase(parsed_idea),),
                "white_quote_card",
            )
        ]
    ]


def _score_variant(
    *,
    variant_id: str,
    reference_template_id: str,
    components: tuple[ComponentBox, ...],
    person_placement: Mapping[str, Any],
    canvas_size: tuple[int, int],
    allowed_types: tuple[str, ...],
) -> ComponentVariant:
    score = 100.0
    critique: list[str] = []
    repairs: list[str] = []
    face_box = _box(person_placement.get("face_box"), canvas_size)

    if len(components) > 3:
        score -= 18
        critique.append("too many components for Ali/Jeff thumbnail grammar")
        repairs.append("reduce_component_count")

    for component in components:
        if component.component_type not in allowed_types and component.component_type not in {
            "small_badge",
            "arrow",
            "logo_tile",
        }:
            score -= 20
            critique.append(f"component type not allowed by template: {component.component_type}")
            repairs.append(f"replace_component:{component.component_type}")

        if _edge_margin(component.box, canvas_size) < 24:
            score -= 8
            critique.append(f"{component.role} is too close to canvas edge")
            repairs.append(f"move_inward:{component.role}")

        if face_box and _iou(component.box, face_box) > 0.03:
            score -= 28
            critique.append(f"{component.role} overlaps the face")
            repairs.append(f"move_away_from_face:{component.role}")

        if not component.content or any(len(str(item).strip()) > 12 for item in component.content):
            score -= 10
            critique.append(f"{component.role} has text that may be too long")
            repairs.append(f"shorten_text:{component.role}")

    if not critique:
        critique.append("passes deterministic component layout gates")

    return ComponentVariant(
        variant_id=variant_id,
        reference_template_id=reference_template_id,
        components=components,
        score=max(0.0, score),
        critique=tuple(critique),
        repair_patches=tuple(dict.fromkeys(repairs)),
    )


def _component_content(parsed_idea: Any) -> list[str]:
    component_text = [
        str(item).strip()
        for item in (_get(parsed_idea, "component_text", ()) or ())
        if str(item).strip()
    ]
    if component_text:
        return component_text[:4]

    visual = str(_get(parsed_idea, "visual", "") or "")
    candidates = re.findall(r"[\u4e00-\u9fffA-Za-z0-9%]+", visual)
    priority = [
        token
        for token in candidates
        if 2 <= len(token) <= 8
        and token.lower()
        not in {
            "warm",
            "desk",
            "card",
            "host",
            "simple",
            "clean",
            "visual",
            "background",
        }
    ]
    for query in _get(parsed_idea, "asset_queries", ()) or ():
        priority.extend(
            token
            for token in re.findall(r"[A-Za-z0-9\u4e00-\u9fff%]+", str(query))
            if len(token) <= 8
        )
    deduped = list(dict.fromkeys(priority))
    if len(deduped) >= 3:
        return deduped[:3]
    return [*deduped, "重點一", "重點二", "重點三"][:3]


def _regions(
    content_side: str, canvas_size: tuple[int, int]
) -> dict[str, tuple[int, int, int, int]]:
    width, _height = canvas_size
    if content_side == "right":
        main = (738, 166, width - 62, 486)
        compact = (890, 486, width - 74, 634)
        top = (780, 56, width - 110, 132)
    else:
        main = (62, 166, 542, 486)
        compact = (74, 486, 350, 634)
        top = (110, 56, 500, 132)
    return {
        "main": main,
        "compact": compact,
        "top": top,
        "left_badge": (80, 206, 334, 356),
        "right_badge": (946, 206, 1200, 356),
        "arrow": (398, 92, 884, 260),
    }


def _content_side(
    person_placement: Mapping[str, Any],
    layout_blocking: Mapping[str, Any] | None,
    canvas_size: tuple[int, int],
) -> str:
    if isinstance(layout_blocking, Mapping):
        side = str(layout_blocking.get("content_side") or "")
        if side in {"left", "right"}:
            return side
    person = _box(person_placement.get("cutout_box"), canvas_size)
    if not person:
        return "right"
    center = (person[0] + person[2]) / 2
    return "right" if center < canvas_size[0] / 2 else "left"


def _fallback_template_id(parsed_idea: Any) -> str:
    text = " ".join(
        [
            str(_get(parsed_idea, "recipe_id", "")),
            str(_get(parsed_idea, "lane", "")),
            str(_get(parsed_idea, "title_pairing", "")),
            str(_get(parsed_idea, "visual", "")),
        ]
    ).lower()
    latin_tokens = set(re.findall(r"[a-z][a-z0-9-]*", text))
    if any(
        token in text
        for token in (
            "ali_warm",
            "ali warm",
            "health",
            "creatine",
            "longevity",
            "brain",
            "aging",
            "肌酸",
            "健康",
            "大腦",
            "認知",
            "抗老",
            "長壽",
            "研究",
            "清單",
            "效益",
        )
    ):
        return "shosho_benefit_list_card"
    if any(token in latin_tokens for token in ("wrong", "instead", "mistake", "prompt")) or any(
        token in text for token in ("錯", "不要", "改成", "取代", "提示詞")
    ):
        return "jeff_command_panel"
    if any(
        token in latin_tokens
        for token in ("tool", "app", "ai", "chatgpt", "claude", "tutorial", "notebooklm")
    ) or any(token in text for token in ("工具", "教學", "應用程式", "分鐘學會")):
        return "jeff_tool_header_panel"
    if any(token in latin_tokens for token in ("million", "before", "after")) or any(
        token in text for token in ("$", "之前", "之後", "財富")
    ):
        return "ali_metric_arrow"
    return "shosho_benefit_list_card"


def _metric_pair(parsed_idea: Any) -> tuple[str, str]:
    text = " ".join(
        [str(_get(parsed_idea, "title_pairing", "")), str(_get(parsed_idea, "hook", ""))]
    )
    found = re.findall(r"\$?\d+[A-Za-z%]*", text)
    if len(found) >= 2:
        return found[0], found[1]
    if found:
        return "0", found[0]
    return "Before", "After"


def _tool_label(parsed_idea: Any) -> str:
    text = " ".join(
        [
            str(_get(parsed_idea, "title_pairing", "")),
            str(_get(parsed_idea, "visual", "")),
            " ".join(str(q) for q in (_get(parsed_idea, "asset_queries", ()) or ())),
        ]
    )
    for token in re.findall(r"[A-Za-z][A-Za-z0-9-]{2,20}", text):
        if token.lower() not in {"learn", "master", "guide", "tool", "clean", "panel"}:
            return token
    return "Tool"


def _tool_panel_lines(parsed_idea: Any) -> tuple[str, ...]:
    hook = str(_get(parsed_idea, "hook", "") or "").strip()
    return (hook or "Start Here",)


def _decoration(parsed_idea: Any) -> str:
    return str(_get(parsed_idea, "decoration", "") or "").strip()


def _short_phrase(parsed_idea: Any) -> str:
    return str(
        _get(parsed_idea, "hook", "") or _get(parsed_idea, "title_pairing", "") or "Start Here"
    )[:16]


def _nudge(
    box: tuple[int, int, int, int], *, dx: int = 0, dy: int = 0
) -> tuple[int, int, int, int]:
    return (box[0] + dx, box[1] + dy, box[2] + dx, box[3] + dy)


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


def _edge_margin(box: tuple[int, int, int, int], canvas_size: tuple[int, int]) -> int:
    width, height = canvas_size
    x0, y0, x1, y1 = box
    return min(x0, y0, width - x1, height - y1)


def _iou(a: tuple[int, int, int, int], b: tuple[int, int, int, int]) -> float:
    ax0, ay0, ax1, ay1 = a
    bx0, by0, bx1, by1 = b
    ix0, iy0 = max(ax0, bx0), max(ay0, by0)
    ix1, iy1 = min(ax1, bx1), min(ay1, by1)
    if ix1 <= ix0 or iy1 <= iy0:
        return 0.0
    intersection = (ix1 - ix0) * (iy1 - iy0)
    area_a = max(1, (ax1 - ax0) * (ay1 - ay0))
    area_b = max(1, (bx1 - bx0) * (by1 - by0))
    return intersection / (area_a + area_b - intersection)


def _load_font(size: int, *, bold: bool = False) -> object:
    from PIL import ImageFont

    candidates = (
        (
            "C:/Windows/Fonts/msjhbd.ttc",
            "C:/Windows/Fonts/msjh.ttc",
            "C:/Windows/Fonts/arialbd.ttf",
            "C:/Windows/Fonts/arial.ttf",
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


def _draw_warm_blocking_background(draw: object, canvas_size: tuple[int, int]) -> None:
    width, height = canvas_size
    for y in range(height):
        ratio = y / max(1, height)
        r = int(230 - ratio * 24)
        g = int(224 - ratio * 30)
        b = int(210 - ratio * 36)
        draw.line((0, y, width, y), fill=(r, g, b), width=1)
    draw.rectangle((0, int(height * 0.68), width, height), fill=(68, 62, 58))
    draw.rectangle((0, 0, width, height), outline=(196, 181, 165), width=2)


def _draw_thirds(draw: object, canvas_size: tuple[int, int]) -> None:
    width, height = canvas_size
    for x in (width // 3, (2 * width) // 3):
        draw.line((x, 0, x, height), fill=(255, 255, 255), width=2)
        draw.line((x + 2, 0, x + 2, height), fill=(148, 130, 112), width=1)


def _draw_host_blocking(
    draw: object,
    person_box: tuple[int, int, int, int],
    face_box: tuple[int, int, int, int] | None,
    title_font: object,
    small_font: object,
) -> None:
    x0, y0, x1, y1 = person_box
    draw.rounded_rectangle((x0, y0, x1, y1), radius=18, fill=(38, 44, 52), outline=(17, 24, 39))
    if face_box:
        fx0, fy0, fx1, fy1 = face_box
        cx = (fx0 + fx1) // 2
        cy = (fy0 + fy1) // 2
        rx = max(70, (fx1 - fx0) // 2)
        ry = max(90, (fy1 - fy0) // 2)
        draw.ellipse(
            (cx - rx, cy - ry, cx + rx, cy + ry),
            fill=(232, 178, 145),
            outline=(255, 122, 89),
            width=4,
        )
    label_y = max(y0 + 24, y1 - 112)
    draw.text((x0 + 32, label_y), "HOST", fill=(248, 250, 252), font=title_font)
    draw.text(
        (x0 + 32, label_y + 42), "locked person placement", fill=(203, 213, 225), font=small_font
    )


def _draw_component_sketch(
    draw: object,
    component: ComponentBox,
    index: int,
    title_font: object,
    body_font: object,
    small_font: object,
) -> None:
    x0, y0, x1, y1 = component.box
    shadow = (x0 + 10, y0 + 12, x1 + 10, y1 + 12)
    draw.rounded_rectangle(shadow, radius=18, fill=(54, 47, 40))
    if component.component_type == "metric_badge_pair":
        fill = (255, 255, 255)
        outline = (20, 184, 88)
    elif component.component_type in {"tool_label", "command_panel"}:
        fill = (25, 29, 36)
        outline = (255, 255, 255)
    else:
        fill = (255, 255, 255)
        outline = (42, 101, 218)
    draw.rounded_rectangle((x0, y0, x1, y1), radius=18, fill=fill, outline=outline, width=4)

    dark = component.component_type in {"tool_label", "command_panel"}
    text_color = (255, 255, 255) if dark else (31, 41, 55)
    accent = (96, 165, 250) if dark else outline
    draw.text((x0 + 24, y0 + 20), component.role.replace("_", " "), fill=accent, font=small_font)

    content = [str(item).strip() for item in component.content if str(item).strip()]
    if component.component_type == "arrow":
        mid_y = (y0 + y1) // 2
        draw.line((x0 + 30, mid_y, x1 - 48, mid_y), fill=(20, 184, 88), width=14)
        draw.polygon(
            [(x1 - 48, mid_y - 30), (x1 - 48, mid_y + 30), (x1 - 10, mid_y)],
            fill=(20, 184, 88),
        )
        return

    if component.component_type == "metric_badge_pair":
        text = content[0] if content else "0"
        draw.text((x0 + 34, y0 + 58), text[:8], fill=text_color, font=_load_font(76, bold=True))
        return

    line_y = y0 + 72
    if not content:
        content = [component.component_type]
    for line in content[:4]:
        if component.component_type == "benefit_list_card":
            draw.rounded_rectangle(
                (x0 + 28, line_y + 4, x0 + 58, line_y + 34),
                radius=8,
                fill=(255, 122, 89),
            )
            text_x = x0 + 76
        else:
            text_x = x0 + 28
        draw.text((text_x, line_y), line[:14], fill=text_color, font=body_font)
        line_y += 68 if component.component_type == "benefit_list_card" else 54

    if index == 0 and component.style:
        draw.text((x0 + 24, y1 - 38), component.style, fill=(107, 114, 128), font=small_font)


def _draw_grid(draw: object, canvas_size: tuple[int, int]) -> None:
    width, height = canvas_size
    for x in range(0, width, 80):
        draw.line((x, 0, x, height), fill=(218, 224, 232), width=1)
    for y in range(0, height, 80):
        draw.line((0, y, width, y), fill=(218, 224, 232), width=1)
    draw.line((width // 3, 0, width // 3, height), fill=(100, 116, 139), width=2)
    draw.line((2 * width // 3, 0, 2 * width // 3, height), fill=(100, 116, 139), width=2)
    draw.rectangle((0, 0, width - 1, height - 1), outline=(148, 163, 184), width=2)


def _draw_box(
    draw: object,
    box: tuple[int, int, int, int],
    label: str,
    fill: tuple[int, int, int] | None,
    outline: tuple[int, int, int],
    font: object,
    *,
    light_text: bool = False,
) -> None:
    x0, y0, x1, y1 = box
    draw.rounded_rectangle(
        (x0, y0, x1, y1),
        radius=8,
        fill=fill,
        outline=outline,
        width=3,
    )
    draw.text(
        (x0 + 16, y0 + 16),
        label[:42],
        fill=(255, 255, 255) if light_text else outline,
        font=font,
    )


def _get(obj: Any, key: str, default: Any = None) -> Any:
    if isinstance(obj, Mapping):
        return obj.get(key, default)
    return getattr(obj, key, default)
