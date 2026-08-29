"""Validate renderer evidence and build long-highlight composition receipts."""

from __future__ import annotations

import base64
import hashlib
import json
from dataclasses import dataclass
from typing import Literal
from pathlib import Path

from PIL import Image

MEASUREMENT_SCHEMA = "nakama.thumbnail_composition_measurement.v1"
RECEIPT_SCHEMA = "nakama.long_thumbnail_composition.v3"
LEGACY_RECEIPT_SCHEMA = "nakama.long_thumbnail_composition.v2"

# 中央卡的素材供給順序（SKILL.md 紅線 5）。redrawn = 自己重繪的圖表。
CENTER_SUPPLY = ("envato", "public_domain", "redrawn")
# object-fit: cover —— 原圖與卡片長寬比不合時，短邊會被裁掉。留給裁切的上限。
MIN_CENTER_RETENTION = 0.5


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


def _provenance_from_candidate(spec: dict) -> dict | None:
    """把 gate 挑中的候選轉成 provenance；`why` 仍然要人寫。"""
    candidate = spec.get("center_candidate")
    if not isinstance(candidate, dict):
        return None
    fields = {name: candidate.get(name) for name in ("supply", "source", "query")}
    if any(not value for value in fields.values()):
        return None
    why = str(spec.get("center_why") or "").strip()
    if not why:
        return None
    return {**fields, "why": why}


def _inherited_provenance(
    *, vault_root: Path, episode_slug: str, cut_id: str, rank: int, center: Path
) -> dict | None | Literal[False]:
    """上一份收據對**同一張**中央圖記了什麼。

    回 dict = 沿用它記的來歷；回 None = 上一份是 v2（那個年代沒有這個欄位），
    照舊發 v2；回 False = 沒有上一份，或圖換過了 → 由呼叫端要求交代。

    「同一張」用 SHA-256 判定，不比檔名——檔名可以一樣而內容換過。
    """
    path = (
        Path(vault_root)
        / "Attachments"
        / "packaging"
        / episode_slug
        / "composition_receipts"
        / f"{cut_id}-r{rank}.json"
    )
    if not path.is_file():
        return False
    try:
        previous = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        return False
    if previous.get("center_visual_sha256") != _sha(center):
        return False
    return previous.get("center_provenance")


def _center_provenance(spec: dict, *, inherited: object = False) -> dict | None:
    """中央卡的來歷——沒有它，成品就沒有人說得出「為什麼是這張圖」。

    2026-08-29 修修看到 punch-L04 rank 1 的中央卡是一隻鸚鵡，問為什麼。整條線
    翻完：receipt 只記幾何與 SHA-256，render spec 只記檔案路徑，run log 沒寫。
    那張圖想講的是 03:29 的「把你顧得好好的，其實是把你圈養起來」——推得出來，
    但不是紀錄。推論不能當交代，所以這裡把它變成必填欄位。
    """
    raw = spec.get("center_provenance")
    if raw is None and inherited is not False:
        # 中央圖跟上一份收據是同一張——調的是幾何或人臉，不是換圖。這種重出不該
        # 被擋下來（2026-08-29 修修調 value-L02 的 cutout 大小時整條產線卡死）。
        # inherited 是 None 代表上一份是 v2 的年代，照舊發 v2；不無中生有。
        return inherited  # type: ignore[return-value]
    if raw is None:
        # 修修在 gate 上挑的那張，來歷已經跟著候選池一起進來了（supply/source/
        # query 三項都有），只差「為什麼配這條標題」。要他挑完圖再把出處重打一次
        # 是白費工，所以這裡優先讀 candidate。
        raw = _provenance_from_candidate(spec)
    if not isinstance(raw, dict):
        raise ValueError(
            "spec 缺 center_provenance——中央卡必須交代來歷"
            f"（supply/source/query/why；supply ∈ {list(CENTER_SUPPLY)}）"
        )
    supply = str(raw.get("supply") or "")
    if supply not in CENTER_SUPPLY:
        raise ValueError(f"center_provenance.supply 必須是 {list(CENTER_SUPPLY)} 之一，收到 {supply!r}")
    fields = {"supply": supply}
    for name in ("source", "query", "why"):
        value = str(raw.get(name) or "").strip()
        if not value:
            raise ValueError(f"center_provenance.{name} 不可為空")
        fields[name] = value
    # 「配合主題」這種長度的字等於沒寫；配對理由要能指回某個 beat 或 quote。
    if len(fields["why"]) < 12:
        raise ValueError("center_provenance.why 太短——要寫出這張圖扣回哪一個 beat／quote")
    unknown = set(raw) - {"supply", "source", "query", "why"}
    if unknown:
        raise ValueError(f"center_provenance 有不認識的欄位：{sorted(unknown)}")
    return fields


def _assert_center_fits_card(center: Path, protected: dict) -> None:
    """原圖必須撐得起那張橫卡——直式硬塞等於交出一張中段裁切。

    卡片是 `object-fit: cover`：原圖長寬比與卡片不合時，短邊直接被裁掉。
    punch-L04 rank 1 的素材是 1080×1920 直式，卡片 678×455（1.49:1），
    等於只有 38% 的原圖進得了畫面——棲架、飼料碗、任何「被圈養」的線索全被
    切在框外，讀者只看到一隻可愛的鸚鵡。SKILL.md 早就寫「卡片必須是橫向長方形」，
    但先前只驗了卡片的 bbox，沒有人驗餵進去的素材。
    """
    with Image.open(center) as image:
        source_width, source_height = image.size
    if source_width <= source_height:
        raise ValueError(
            f"中央卡素材必須是橫式：{center.name} 是 {source_width}×{source_height}。"
            "直式塞進橫卡只會得到中段裁切，換一張橫式素材，不要靠裁切硬過。"
        )
    source_ratio = source_width / source_height
    card_ratio = protected["width"] / protected["height"]
    retention = min(source_ratio, card_ratio) / max(source_ratio, card_ratio)
    if retention < MIN_CENTER_RETENTION:
        raise ValueError(
            f"中央卡素材 {center.name}（{source_width}×{source_height}，"
            f"{source_ratio:.2f}:1）進 {card_ratio:.2f}:1 的卡片只留得下 "
            f"{retention:.0%}——低於 {MIN_CENTER_RETENTION:.0%}，換一張比例接近的素材。"
        )


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
    provenance = _center_provenance(
        spec,
        inherited=_inherited_provenance(
            vault_root=vault_root,
            episode_slug=episode_slug,
            cut_id=cut_id,
            rank=rank,
            center=center,
        ),
    )
    _assert_center_fits_card(center, protected)
    center_name = f"center-{cut_id}-r{rank}{center.suffix.lower()}"
    sidecar_name = f"{thumbnail.name}.composition.json"
    prefix = f"Attachments/packaging/{episode_slug}"
    renderer_identity = f"{renderer['name']}@{renderer['version']}"
    return ReceiptPlan(
        payload={
            # 有來歷才是 v3。舊中央圖沿用 v2，不為了版號好看而編造欄位。
            "schema": RECEIPT_SCHEMA if provenance is not None else LEGACY_RECEIPT_SCHEMA,
            "episode": episode,
            "cut_id": cut_id,
            "package_rank": rank,
            "thumbnail_png": f"{prefix}/{thumbnail.name}",
            "canvas_width": int(width),
            "canvas_height": int(height),
            "center_visual_asset": f"{prefix}/{center_name}",
            **({"center_provenance": provenance} if provenance is not None else {}),
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
