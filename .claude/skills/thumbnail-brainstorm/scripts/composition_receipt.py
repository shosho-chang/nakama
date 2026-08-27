"""Validate renderer evidence and build long-highlight composition receipts."""

from __future__ import annotations

import base64
import hashlib
import json
from dataclasses import dataclass
from pathlib import Path

MEASUREMENT_SCHEMA = "nakama.thumbnail_composition_measurement.v1"
RECEIPT_SCHEMA = "nakama.long_thumbnail_composition.v2"


@dataclass(frozen=True)
class ReceiptPlan:
    payload: dict
    center_source: Path
    center_name: str
    receipt_name: str
    sidecar_source: Path
    sidecar_name: str


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _data_url(path: Path) -> str:
    mime = {".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg"}.get(
        path.suffix.lower(), "application/octet-stream"
    )
    return f"data:{mime};base64,{base64.b64encode(path.read_bytes()).decode('ascii')}"


def _canonical_variables(render_spec: dict) -> dict:
    merged = dict(render_spec.get("variables") or {})
    for name, raw_path in (render_spec.get("images") or {}).items():
        path = Path(raw_path)
        if not path.is_file():
            raise FileNotFoundError(f"render asset not found: {path}")
        merged[name] = _data_url(path)
    return merged


def _assert_box(
    box: object, name: str, width: float, height: float, *, allow_bleed: bool = False
) -> dict:
    if not isinstance(box, dict):
        raise ValueError(f"measurement selector missing: {name}")
    if any(not isinstance(box.get(k), (int, float)) for k in ("x", "y", "width", "height")):
        raise ValueError(f"invalid measured bbox: {name}")
    if box["width"] <= 0 or box["height"] <= 0:
        raise ValueError(f"empty measured bbox: {name}")
    if allow_bleed:
        if (
            box["x"] >= width
            or box["y"] >= height
            or box["x"] + box["width"] <= 0
            or box["y"] + box["height"] <= 0
        ):
            raise ValueError(f"measured bbox does not intersect canvas: {name}")
    elif (
        box["x"] < 0
        or box["y"] < 0
        or box["x"] + box["width"] > width
        or box["y"] + box["height"] > height
    ):
        raise ValueError(f"measured bbox exceeds canvas: {name}")
    return {k: float(box[k]) for k in ("x", "y", "width", "height")}


def build_receipt_plan(
    *, spec: dict, episode: str, cut_id: str, episode_slug: str, vault_root: Path
) -> ReceiptPlan:
    thumbnail = Path(spec["thumbnail"])
    sidecar = thumbnail.with_suffix(thumbnail.suffix + ".composition.json")
    render_spec_path = Path(spec.get("render_spec", ""))
    if not sidecar.is_file():
        raise FileNotFoundError(f"composition sidecar not found: {sidecar}")
    if not render_spec_path.is_file():
        raise FileNotFoundError(f"render spec not found: {render_spec_path}")
    evidence = json.loads(sidecar.read_text(encoding="utf-8"))
    render_spec = json.loads(render_spec_path.read_text(encoding="utf-8"))
    if (
        evidence.get("schema") != MEASUREMENT_SCHEMA
        or evidence.get("composition") != "thumbnail_reaction"
    ):
        raise ValueError("long highlight requires thumbnail_reaction measurement sidecar")
    renderer = evidence.get("renderer") or {}
    if renderer.get("name") != "hyperframes" or not renderer.get("version"):
        raise ValueError("composition renderer identity missing")
    composition_source = (
        Path(__file__).resolve().parents[4]
        / "video"
        / "compositions"
        / "thumbnail_reaction"
        / "index.html"
    )
    if evidence.get("composition_sha256") != _sha(composition_source):
        raise ValueError("composition source hash drift")
    if render_spec.get("composition") != "thumbnail_reaction":
        raise ValueError("render spec composition drift")
    if (render_spec.get("variables") or {}).get("caption"):
        raise ValueError("long highlight center visual cannot be replaced by text")
    images = render_spec.get("images") or {}
    required = {"prop_image_data_url", "host_cutout_data_url", "guest_cutout_data_url"}
    if required.difference(images):
        raise ValueError(
            f"render spec missing central/person assets: {sorted(required.difference(images))}"
        )
    resolved = {name: Path(raw).resolve() for name, raw in images.items()}
    if (
        resolved["host_cutout_data_url"] != (vault_root / spec["host_cutout"]).resolve()
        and resolved["host_cutout_data_url"] != Path(spec["host_cutout"]).resolve()
    ):
        raise ValueError("host cutout path drift")
    if (
        resolved["guest_cutout_data_url"] != (vault_root / spec["guest_cutout"]).resolve()
        and resolved["guest_cutout_data_url"] != Path(spec["guest_cutout"]).resolve()
    ):
        raise ValueError("guest cutout path drift")
    assets = evidence.get("assets") or {}
    for name in required:
        asset = assets.get(name) or {}
        if Path(asset.get("path", "")).resolve() != resolved[name] or asset.get("sha256") != _sha(
            resolved[name]
        ):
            raise ValueError(f"composition asset hash/path drift: {name}")
    variables_hash = hashlib.sha256(
        json.dumps(
            _canonical_variables(render_spec),
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode()
    ).hexdigest()
    if evidence.get("variables_sha256") != variables_hash:
        raise ValueError("composition render variables hash drift")
    if evidence.get("png_sha256") != _sha(thumbnail):
        raise ValueError("composition PNG hash drift")

    canvas = evidence.get("canvas") or {}
    width, height = canvas.get("width"), canvas.get("height")
    if not isinstance(width, (int, float)) or not isinstance(height, (int, float)):
        raise ValueError("composition canvas missing")
    boxes = evidence.get("bboxes") or {}
    protected = _assert_box(
        boxes.get("protected_center_bbox"), "protected_center_bbox", width, height
    )
    host = _assert_box(boxes.get("host_bbox"), "host_bbox", width, height, allow_bleed=True)
    guest = _assert_box(boxes.get("guest_bbox"), "guest_bbox", width, height, allow_bleed=True)
    if not (protected["x"] <= width / 2 <= protected["x"] + protected["width"]):
        raise ValueError("protected center does not cover canvas center")
    if protected["width"] <= protected["height"] or protected["width"] < width * 0.5:
        raise ValueError("long highlight center must be a horizontal card at least 50% wide")

    rank = int(spec["title_rank"])
    center = resolved["prop_image_data_url"]
    if center.suffix.lower() not in {".png", ".jpg", ".jpeg", ".webp"}:
        raise ValueError("center visual asset must be an image")
    center_name = f"center-{cut_id}-r{rank}{center.suffix.lower()}"
    sidecar_name = f"{thumbnail.name}.composition.json"
    prefix = f"Attachments/packaging/{episode_slug}"
    renderer_identity = f"{renderer['name']}@{renderer['version']}"
    return ReceiptPlan(
        payload={
            "schema": RECEIPT_SCHEMA,
            "episode": episode,
            "cut_id": cut_id,
            "package_rank": rank,
            "thumbnail_png": f"{prefix}/{thumbnail.name}",
            "canvas_width": int(width),
            "canvas_height": int(height),
            "center_visual_asset": f"{prefix}/{center_name}",
            "thumbnail_sha256": _sha(thumbnail),
            "center_visual_sha256": _sha(center),
            "measurement_sidecar": f"{prefix}/{sidecar_name}",
            "measurement_sidecar_sha256": _sha(sidecar),
            "renderer_identity": renderer_identity,
            "protected_center_bbox": protected,
            "host_bbox": host,
            "guest_bbox": guest,
            "title_bbox": None,
            "max_protected_overlap_ratio": 1.0,
        },
        center_source=center,
        center_name=center_name,
        receipt_name=f"{cut_id}-r{rank}.json",
        sidecar_source=sidecar,
        sidecar_name=sidecar_name,
    )
