"""Person placement preview for staged thumbnail production.

This is an inspection artifact, not the final thumbnail. It renders the chosen
transparent cutout on a 1280x720 guide canvas using the same rough placement
contract as the Hyperframes CSS archetypes. The goal is to validate the first
production step independently: expression choice, face size, and position.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from pathlib import Path
from typing import Literal, Mapping

CanvasAnchor = Literal["left", "right", "center"]


@dataclass(frozen=True)
class PersonPlacementSpec:
    archetype: str
    anchor: CanvasAnchor
    height_ratio: float
    max_width: int
    face_height_ratio: float = 0.55
    face_center_y: float = 0.34
    left_face_x: float = 0.30
    right_face_x: float = 0.70
    left: int = 0
    right: int = 0
    bottom: int = 0
    safe_zone: tuple[int, int, int, int] | None = None
    note: str = ""

    def to_dict(self) -> dict:
        return {
            "archetype": self.archetype,
            "anchor": self.anchor,
            "height_ratio": self.height_ratio,
            "max_width": self.max_width,
            "face_height_ratio": self.face_height_ratio,
            "face_center_y": self.face_center_y,
            "left_face_x": self.left_face_x,
            "right_face_x": self.right_face_x,
            "left": self.left,
            "right": self.right,
            "bottom": self.bottom,
            "safe_zone": list(self.safe_zone) if self.safe_zone else None,
            "note": self.note,
        }


@dataclass(frozen=True)
class PersonPlacementPreview:
    out_png: Path
    cutout_path: Path
    spec: PersonPlacementSpec
    canvas_size: tuple[int, int]
    cutout_box: tuple[int, int, int, int]
    face_box: tuple[int, int, int, int] | None = None
    focal_point: tuple[int, int] | None = None

    def to_dict(self) -> dict:
        return {
            "filename": self.out_png.name,
            "cutout_path": self.cutout_path.as_posix(),
            "spec": self.spec.to_dict(),
            "canvas_size": list(self.canvas_size),
            "cutout_box": list(self.cutout_box),
            "face_box": list(self.face_box) if self.face_box else None,
            "focal_point": list(self.focal_point) if self.focal_point else None,
        }


_CANVAS = (1280, 720)

_SPECS: dict[str, PersonPlacementSpec] = {
    "tv1-face-right": PersonPlacementSpec(
        archetype="tv1-face-right",
        anchor="right",
        height_ratio=1.18,
        max_width=900,
        left=24,
        right=24,
        bottom=-80,
        note="close portrait; gaze controls side",
    ),
    "tv2-face-center": PersonPlacementSpec(
        archetype="tv2-face-center",
        anchor="center",
        height_ratio=1.18,
        max_width=960,
        bottom=-80,
        note="close center portrait",
    ),
    "tv3-split": PersonPlacementSpec(
        archetype="tv3-split",
        anchor="right",
        height_ratio=1.18,
        max_width=900,
        left=24,
        right=24,
        bottom=-80,
        note="close portrait; gaze controls side",
    ),
    "tv8-color-pop": PersonPlacementSpec(
        archetype="tv8-color-pop",
        anchor="right",
        height_ratio=1.18,
        max_width=900,
        left=24,
        right=24,
        bottom=-80,
        note="close portrait; gaze controls side",
    ),
    "tv10-number-hero": PersonPlacementSpec(
        archetype="tv10-number-hero",
        anchor="right",
        height_ratio=1.12,
        max_width=860,
        face_height_ratio=0.48,
        left=24,
        right=24,
        bottom=-72,
        note="close portrait; gaze controls side",
    ),
}

_FALLBACK_SPEC = PersonPlacementSpec(
    archetype="default",
    anchor="right",
    height_ratio=1.12,
    max_width=860,
    face_height_ratio=0.48,
    left=24,
    right=24,
    bottom=-72,
    note="fallback close portrait",
)


def placement_spec_for_archetype(archetype: str) -> PersonPlacementSpec:
    return _SPECS.get(archetype, _FALLBACK_SPEC)


def placement_spec_for_pose(
    archetype: str,
    pose_tags: Mapping[str, str] | None = None,
) -> PersonPlacementSpec:
    """Return a placement spec after applying gaze/gesture direction.

    Person placement is person-first: if the face or gesture points toward
    screen right, the person belongs on the left so their attention leads into
    the canvas. Camera-facing poses keep the template's default side.
    """

    spec = placement_spec_for_archetype(archetype)
    if not pose_tags:
        return spec

    direction = _attention_direction(pose_tags)
    if direction == "right":
        return replace(spec, anchor="left", note=f"{spec.note}; gaze -> screen right")
    if direction == "left":
        return replace(spec, anchor="right", note=f"{spec.note}; gaze -> screen left")
    return spec


def render_person_placement_preview(
    *,
    cutout_path: Path,
    out_png: Path,
    archetype: str,
    cutout_id: str | None = None,
    pose_tags: Mapping[str, str] | None = None,
    canvas_size: tuple[int, int] = _CANVAS,
) -> PersonPlacementPreview:
    """Render a person-only placement preview PNG."""

    from PIL import Image, ImageDraw, ImageFont

    if not cutout_path.is_file():
        raise FileNotFoundError(f"cutout missing: {cutout_path}")

    spec = placement_spec_for_pose(archetype, pose_tags)
    canvas = Image.new("RGB", canvas_size, (238, 242, 246))
    draw = ImageDraw.Draw(canvas)
    font = ImageFont.load_default()

    _draw_guides(draw, canvas_size, spec)

    with Image.open(cutout_path) as raw:
        cutout = raw.convert("RGBA")
        face_box_original = _estimate_face_box(cutout)
        if face_box_original:
            target_face_h = int(canvas_size[1] * spec.face_height_ratio)
            face_h = max(1, face_box_original[3] - face_box_original[1])
            scale = target_face_h / face_h
        else:
            target_h = int(canvas_size[1] * spec.height_ratio)
            scale = target_h / cutout.height
        target_w = int(cutout.width * scale)
        target_h = int(cutout.height * scale)
        if target_w > spec.max_width:
            scale = spec.max_width / cutout.width
            target_w = spec.max_width
            target_h = int(cutout.height * scale)
        cutout = cutout.resize((target_w, target_h), Image.Resampling.LANCZOS)

    face_box_scaled = _scale_box(face_box_original, scale) if face_box_original else None
    if face_box_scaled:
        x, y, focal_point = _place_by_face(
            spec=spec,
            canvas_size=canvas_size,
            cutout_size=(target_w, target_h),
            face_box=face_box_scaled,
        )
    else:
        if spec.anchor == "center":
            x = (canvas_size[0] - target_w) // 2
        elif spec.anchor == "left":
            x = spec.left
        else:
            x = canvas_size[0] - spec.right - target_w
        y = canvas_size[1] - spec.bottom - target_h
        focal_point = None

    shadow = Image.new("RGBA", cutout.size, (0, 0, 0, 0))
    shadow_alpha = cutout.getchannel("A").point(lambda px: int(px * 0.28))
    shadow.putalpha(shadow_alpha)
    shadow_x = x + 16 if spec.anchor == "left" else x - 16
    canvas.paste(shadow, (shadow_x, y + 18), shadow)
    canvas.paste(cutout, (x, y), cutout)

    box = (x, y, x + target_w, y + target_h)
    draw.rectangle(box, outline=(14, 116, 144), width=4)
    placed_face_box = _offset_box(face_box_scaled, x, y) if face_box_scaled else None
    if placed_face_box:
        draw.rectangle(placed_face_box, outline=(255, 122, 89), width=3)
    if focal_point:
        fx, fy = focal_point
        draw.ellipse((fx - 7, fy - 7, fx + 7, fy + 7), fill=(255, 122, 89))
    label = cutout_id or cutout_path.stem
    label_x = 18 if spec.anchor != "left" else canvas_size[0] - 408
    draw.rectangle(
        (label_x, 18, label_x + 390, 72),
        fill=(255, 255, 255),
        outline=(180, 190, 200),
    )
    draw.text((label_x + 14, 30), f"{label} / {spec.archetype}", fill=(17, 24, 39), font=font)
    draw.text((label_x + 14, 48), spec.note, fill=(75, 85, 99), font=font)

    out_png.parent.mkdir(parents=True, exist_ok=True)
    canvas.save(out_png)
    return PersonPlacementPreview(
        out_png=out_png,
        cutout_path=cutout_path,
        spec=spec,
        canvas_size=canvas_size,
        cutout_box=box,
        face_box=placed_face_box,
        focal_point=focal_point,
    )


def _draw_guides(
    draw: object,
    canvas_size: tuple[int, int],
    spec: PersonPlacementSpec,
) -> None:
    width, height = canvas_size
    for x in range(0, width, 80):
        draw.line((x, 0, x, height), fill=(224, 229, 235), width=1)
    for y in range(0, height, 80):
        draw.line((0, y, width, y), fill=(224, 229, 235), width=1)
    draw.rectangle((0, 0, width - 1, height - 1), outline=(156, 163, 175), width=2)
    draw.line((width // 3, 0, width // 3, height), fill=(148, 163, 184), width=2)
    draw.line((2 * width // 3, 0, 2 * width // 3, height), fill=(148, 163, 184), width=2)
    draw.line((width // 2, 0, width // 2, height), fill=(202, 209, 216), width=1)

    if spec.safe_zone:
        x0, y0, x1, y1 = spec.safe_zone
        draw.rectangle((x0, y0, x1, y1), outline=(202, 138, 4), width=2)
        draw.text((x0 + 16, y0 + 16), "GUIDE", fill=(120, 53, 15))


def _attention_direction(pose_tags: Mapping[str, str]) -> Literal["left", "right", "camera"]:
    gaze = str(pose_tags.get("gaze") or "").lower()
    hands = str(pose_tags.get("hands") or "").lower()

    if "right" in gaze:
        return "right"
    if "left" in gaze:
        return "left"
    if (
        "screen_right" in hands
        or "point_screen_right" in hands
        or "open_palm_screen_right" in hands
    ):
        return "right"
    if "screen_left" in hands or "point_screen_left" in hands or "open_palm_screen_left" in hands:
        return "left"
    return "camera"


def _estimate_face_box(cutout: object) -> tuple[int, int, int, int] | None:
    """Estimate the visible face/head area for placement previews.

    This is intentionally lightweight and deterministic: transparent cutouts get
    a skin-tone estimate in the upper body region. If that fails, the fallback
    uses the upper portion of the alpha silhouette so the preview still places
    the head near a thirds focal point.
    """

    import numpy as np

    arr = np.asarray(cutout)
    if arr.ndim != 3 or arr.shape[2] < 4:
        return None
    alpha = arr[:, :, 3] > 30
    alpha_ys, alpha_xs = np.where(alpha)
    if len(alpha_xs) == 0:
        return None

    ax0 = int(alpha_xs.min())
    ay0 = int(alpha_ys.min())
    ax1 = int(alpha_xs.max() + 1)
    ay1 = int(alpha_ys.max() + 1)
    alpha_h = max(1, ay1 - ay0)

    rgb = arr[:, :, :3].astype("int16")
    r = rgb[:, :, 0]
    g = rgb[:, :, 1]
    b = rgb[:, :, 2]
    yy = np.indices(alpha.shape)[0]
    skin = (
        alpha
        & (yy < ay0 + alpha_h * 0.46)
        & (r > 70)
        & (g > 35)
        & (b > 20)
        & (r > g * 1.04)
        & (r > b * 1.08)
        & ((np.maximum.reduce([r, g, b]) - np.minimum.reduce([r, g, b])) > 15)
    )
    sy, sx = np.where(skin)
    if len(sx) > 80:
        x0 = int(np.percentile(sx, 8))
        x1 = int(np.percentile(sx, 92))
        y0 = int(np.percentile(sy, 3))
        y1 = int(np.percentile(sy, 82))
        w = max(1, x1 - x0)
        h = max(1, y1 - y0)
        # Include hair/forehead and a little jawline around the skin estimate.
        pad_x = int(w * 0.18)
        pad_top = int(h * 0.22)
        pad_bottom = int(h * 0.08)
        return (
            max(ax0, x0 - pad_x),
            max(ay0, y0 - pad_top),
            min(ax1, x1 + pad_x),
            min(ay1, y1 + pad_bottom),
        )

    head_h = int(alpha_h * 0.36)
    head_w = int((ax1 - ax0) * 0.46)
    cx = (ax0 + ax1) // 2
    return (
        max(ax0, cx - head_w // 2),
        ay0,
        min(ax1, cx + head_w // 2),
        min(ay1, ay0 + head_h),
    )


def _scale_box(
    box: tuple[int, int, int, int] | None,
    scale: float,
) -> tuple[int, int, int, int] | None:
    if not box:
        return None
    x0, y0, x1, y1 = box
    return (
        int(round(x0 * scale)),
        int(round(y0 * scale)),
        int(round(x1 * scale)),
        int(round(y1 * scale)),
    )


def _offset_box(
    box: tuple[int, int, int, int] | None,
    dx: int,
    dy: int,
) -> tuple[int, int, int, int] | None:
    if not box:
        return None
    x0, y0, x1, y1 = box
    return (x0 + dx, y0 + dy, x1 + dx, y1 + dy)


def _place_by_face(
    *,
    spec: PersonPlacementSpec,
    canvas_size: tuple[int, int],
    cutout_size: tuple[int, int],
    face_box: tuple[int, int, int, int],
) -> tuple[int, int, tuple[int, int]]:
    canvas_w, canvas_h = canvas_size
    cutout_w, cutout_h = cutout_size
    face_cx = (face_box[0] + face_box[2]) / 2
    face_cy = (face_box[1] + face_box[3]) / 2

    if spec.anchor == "left":
        focal_x = int(canvas_w * spec.left_face_x)
    elif spec.anchor == "right":
        focal_x = int(canvas_w * spec.right_face_x)
    else:
        focal_x = int(canvas_w * 0.5)
    focal_y = int(canvas_h * spec.face_center_y)

    x = int(round(focal_x - face_cx))
    y = int(round(focal_y - face_cy))

    if spec.anchor == "left":
        x = min(x, spec.left + 64)
        x = max(x, -int(cutout_w * 0.35))
    elif spec.anchor == "right":
        x = max(x, canvas_w - cutout_w - spec.right - 64)
        x = min(x, canvas_w - int(cutout_w * 0.65))
    else:
        x = max(-int(cutout_w * 0.25), min(x, canvas_w - int(cutout_w * 0.75)))

    face_top = y + face_box[1]
    face_bottom = y + face_box[3]
    min_top = 8
    max_bottom = int(canvas_h * 0.86)
    if face_top < min_top:
        y += min_top - face_top
    elif face_bottom > max_bottom:
        y -= face_bottom - max_bottom

    return x, y, (focal_x, focal_y)
