"""Preview rendering for arrangement candidate contact sheets."""

from __future__ import annotations

from pathlib import Path
from typing import Iterable

from shared.thumbnail_arrangement import ArrangementCandidate, LayerSpec, PixelBox

_CANVAS = (1280, 720)


def render_arrangement_contact_sheet(
    *,
    out_png: Path,
    candidates: Iterable[ArrangementCandidate],
    selected_candidate_id: str = "",
    canvas_size: tuple[int, int] = _CANVAS,
) -> Path:
    """Render a compact contact sheet for ranked arrangement candidates."""

    from PIL import Image, ImageDraw

    items = tuple(candidates)
    img = Image.new("RGB", canvas_size, (242, 244, 247))
    draw = ImageDraw.Draw(img)
    title_font = _font(34, bold=True)
    body_font = _font(22)
    small_font = _font(16)

    draw.text((42, 30), "Step 5 - arrangement candidates", fill=(17, 24, 39), font=title_font)
    draw.text(
        (42, 72),
        "Selected layout is converted into the renderable component plan.",
        fill=(75, 85, 99),
        font=body_font,
    )

    slot_w = 374
    slot_h = 210
    gap = 38
    start_x = 42
    y = 132
    for index, candidate in enumerate(items[:3]):
        x = start_x + index * (slot_w + gap)
        _draw_candidate_card(
            draw=draw,
            candidate=candidate,
            origin=(x, y),
            size=(slot_w, slot_h),
            selected=(
                candidate.candidate_id == selected_candidate_id
                or (not selected_candidate_id and index == 0)
            ),
            title_font=body_font,
            small_font=small_font,
        )

    _draw_legend(draw, (42, 600), body_font, small_font)
    out_png.parent.mkdir(parents=True, exist_ok=True)
    img.save(out_png)
    return out_png


def _draw_candidate_card(
    *,
    draw: object,
    candidate: ArrangementCandidate,
    origin: tuple[int, int],
    size: tuple[int, int],
    selected: bool,
    title_font: object,
    small_font: object,
) -> None:
    x, y = origin
    width, height = size
    frame = (x, y, x + width, y + height)
    outline = (37, 99, 235) if selected else (203, 213, 225)
    draw.rounded_rectangle(
        (x - 8, y - 44, x + width + 8, y + height + 120), radius=10, fill=(255, 255, 255)
    )
    draw.rounded_rectangle(
        (x - 8, y - 44, x + width + 8, y + height + 120), radius=10, outline=outline, width=3
    )

    status = candidate.cheap_eval.status
    score = candidate.cheap_eval.score
    badge_fill = (34, 197, 94) if status == "pass" else (239, 68, 68)
    draw.rounded_rectangle((x, y - 34, x + 128, y - 6), radius=7, fill=badge_fill)
    draw.text((x + 12, y - 31), f"{status} {score:.0f}", fill=(255, 255, 255), font=small_font)
    draw.text((x + 142, y - 31), candidate.candidate_id, fill=(31, 41, 55), font=small_font)

    draw.rectangle(frame, fill=(226, 232, 240), outline=(148, 163, 184), width=2)
    _draw_thirds(draw, frame)

    for layer in candidate.layers:
        if layer.role == "background":
            continue
        _draw_layer(draw, layer, frame, candidate.canvas_size)

    draw.text((x, y + height + 14), candidate.template_id[:34], fill=(17, 24, 39), font=title_font)
    summary = candidate.cheap_eval.reasons[0] if candidate.cheap_eval.reasons else "unscored"
    draw.text((x, y + height + 52), summary[:52], fill=(75, 85, 99), font=small_font)
    if candidate.cheap_eval.repairs:
        draw.text(
            (x, y + height + 78),
            f"repair: {candidate.cheap_eval.repairs[0]}"[:52],
            fill=(185, 28, 28),
            font=small_font,
        )


def _draw_layer(
    draw: object,
    layer: LayerSpec,
    frame: tuple[int, int, int, int],
    source_canvas: tuple[int, int],
) -> None:
    scaled = _scale_box(layer.box, frame, source_canvas)
    if layer.role == "host":
        fill = (31, 41, 55)
        outline = (17, 24, 39)
    elif layer.role == "headline_text":
        fill = (254, 249, 195)
        outline = (202, 138, 4)
    elif layer.role == "supporting_asset":
        fill = (219, 234, 254)
        outline = (37, 99, 235)
    else:
        fill = (255, 255, 255)
        outline = (37, 99, 235)
    draw.rounded_rectangle(scaled, radius=6, fill=fill, outline=outline, width=2)
    label = layer.kind.replace("_", " ")[:22]
    text_fill = (255, 255, 255) if layer.role == "host" else outline
    draw.text((scaled[0] + 7, scaled[1] + 7), label, fill=text_fill, font=_font(12))

    if layer.face_box:
        draw.rounded_rectangle(
            _scale_box(layer.face_box, frame, source_canvas),
            radius=6,
            outline=(249, 115, 22),
            width=2,
        )
    for hand in layer.hand_boxes:
        draw.rounded_rectangle(
            _scale_box(hand, frame, source_canvas),
            radius=5,
            outline=(168, 85, 247),
            width=2,
        )
    for protected in layer.protected_text_boxes:
        draw.rounded_rectangle(
            _scale_box(protected, frame, source_canvas),
            radius=4,
            outline=(239, 68, 68),
            width=2,
        )


def _draw_thirds(draw: object, frame: tuple[int, int, int, int]) -> None:
    x0, y0, x1, y1 = frame
    width = x1 - x0
    for ratio in (1 / 3, 2 / 3):
        x = round(x0 + width * ratio)
        draw.line((x, y0, x, y1), fill=(203, 213, 225), width=1)


def _draw_legend(
    draw: object, origin: tuple[int, int], body_font: object, small_font: object
) -> None:
    x, y = origin
    draw.text((x, y), "Legend", fill=(17, 24, 39), font=body_font)
    items = (
        ((31, 41, 55), "host/cutout"),
        ((37, 99, 235), "payload/component"),
        ((249, 115, 22), "face"),
        ((168, 85, 247), "hand"),
        ((239, 68, 68), "protected text"),
    )
    cursor = x + 110
    for color, label in items:
        draw.rounded_rectangle(
            (cursor, y + 4, cursor + 22, y + 26), radius=4, outline=color, width=3
        )
        draw.text((cursor + 32, y + 2), label, fill=(75, 85, 99), font=small_font)
        cursor += 178


def _scale_box(
    box: PixelBox,
    frame: tuple[int, int, int, int],
    source_canvas: tuple[int, int],
) -> tuple[int, int, int, int]:
    fx0, fy0, fx1, fy1 = frame
    frame_w = fx1 - fx0
    frame_h = fy1 - fy0
    source_w, source_h = source_canvas
    x0 = fx0 + round((box.x0 / source_w) * frame_w)
    y0 = fy0 + round((box.y0 / source_h) * frame_h)
    x1 = fx0 + round((box.x1 / source_w) * frame_w)
    y1 = fy0 + round((box.y1 / source_h) * frame_h)
    x0 = max(fx0, min(fx1 - 1, x0))
    y0 = max(fy0, min(fy1 - 1, y0))
    x1 = max(fx0 + 1, min(fx1, x1))
    y1 = max(fy0 + 1, min(fy1, y1))
    return (x0, y0, x1, y1)


def _font(size: int, *, bold: bool = False) -> object:
    from PIL import ImageFont

    candidates = (
        (
            "C:/Windows/Fonts/msjhbd.ttc",
            "C:/Windows/Fonts/arialbd.ttf",
            "C:/Windows/Fonts/msjh.ttc",
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
