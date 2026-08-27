"""Packaging gate — `/bridge/packaging` (ADR-054 D10/D11, issue #1071).

穩態唯一人工介入點：修修看每支長片的 3 個 package（標題×封面綁定）後 approve。
資料面 SoT = vault `Attachments/packaging/<episode_slug>/`（Syncthing 同步）：

- packages.json  — packaging skill 寫（titles + packages），本 router 只讀
- approval.json  — 本 router 唯一寫入者（ApprovalFileV1；與 packages.json 分檔
  縮小 Syncthing conflict 破壞面 — ADR-054 A10③）

**UI 零 LLM**（D11）：無 brainstorm / reroll 按鈕；重抽路徑 = 回 Cowork 跑
thumbnail-brainstorm。短片標題可改字（LLM 直出僅是初稿 — D4），寫回 packages.json
是本 router 對該檔唯一的寫入例外，且整檔過 PackagesFileV1 驗證後才落盤。

Syncthing conflict policy（docs/VAULT-LAYOUT.md）：目錄內出現 `*.sync-conflict-*`
即 fail loud — 列表頁該集標錯、board 拒開，不靜默讀任一版本。
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Literal
from urllib.parse import quote

from fastapi import APIRouter, Cookie, Form, HTTPException
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator
from starlette.requests import Request

from scripts.packaging_manifest import load_manifest
from shared.background_job import atomic_job_write, job_expired, load_job, new_job
from shared.config import get_db_path, get_vault_path
from shared.log import get_logger
from shared.release_store import ensure_target, get_release, register_release, update_target
from shared.schemas.packaging import (
    ApprovalFileV1,
    ApprovalV1,
    CenterGeometryV1,
    GeometryV1,
    PackagesFileV1,
    PackagingRevisionJobV1,
    RenderRequestV1,
    parse_approval_file,
    parse_packages,
)
from thousand_sunny.auth import check_auth

logger = get_logger("nakama.web.packaging")

page_router = APIRouter(prefix="/bridge/packaging", tags=["bridge-packaging"])
_templates = Jinja2Templates(
    directory=str(Path(__file__).resolve().parent.parent / "templates" / "bridge")
)

_EP_SLUG_MAX = 80
_TITLE_MAX = 200
_NOTE_MAX = 2000
_SAFE_RECEIPT_ID = re.compile(r"^[A-Za-z0-9._-]+$")
_DESCRIPTION_GENERATING = "DESCRIPTION_DRAFT_GENERATING"
_DESCRIPTION_INTERRUPTED = "DESCRIPTION_DRAFT_INTERRUPTED:"
_DESCRIPTION_TIMEOUT_SECONDS = 900
_DESCRIPTION_PROCESSES: dict[tuple[str, str], subprocess.Popen] = {}
_PUBLISH_PREP_TIMEOUT_SECONDS = 7200
_PUBLISH_PREP_PROCESSES: dict[tuple[str, str], subprocess.Popen] = {}


def _render_watcher_state_path() -> Path:
    """Desktop watcher state shared with the authenticated Bridge status surface."""
    configured = os.environ.get("NAKAMA_RENDER_WATCHER_STATE")
    if configured:
        return Path(configured)
    return Path(__file__).resolve().parents[2] / "logs" / "render-watcher-state.json"


def _same_requested_at(left: str, right: str) -> bool:
    """Compare ISO timestamps while accepting the equivalent Z/+00:00 spelling."""
    try:
        a = datetime.fromisoformat(left.replace("Z", "+00:00"))
        b = datetime.fromisoformat(right.replace("Z", "+00:00"))
    except ValueError:
        return False
    return a == b


class CompositionBBoxV1(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    x: float
    y: float
    width: float = Field(gt=0)
    height: float = Field(gt=0)


class LongThumbnailCompositionReceiptV2(BaseModel):
    """Measured long-highlight composition accepted by the packaging gate."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_: Literal["nakama.long_thumbnail_composition.v2"] = Field(alias="schema")
    episode: str
    cut_id: str
    package_rank: int = Field(ge=1, le=3)
    thumbnail_png: str
    canvas_width: int = Field(gt=0)
    canvas_height: int = Field(gt=0)
    center_visual_asset: str = Field(min_length=1)
    thumbnail_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    center_visual_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    measurement_sidecar: str = Field(min_length=1)
    measurement_sidecar_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    renderer_identity: str = Field(min_length=1)
    protected_center_bbox: CompositionBBoxV1
    host_bbox: CompositionBBoxV1
    guest_bbox: CompositionBBoxV1
    title_bbox: CompositionBBoxV1 | None = None
    max_protected_overlap_ratio: float = Field(default=1.0, ge=0, le=1.0)

    @model_validator(mode="after")
    def _bounds_fit_canvas(self) -> "LongThumbnailCompositionReceiptV2":
        for name in ("protected_center_bbox", "host_bbox", "guest_bbox", "title_bbox"):
            box = getattr(self, name)
            if box is None:
                continue
            if name in {"host_bbox", "guest_bbox"}:
                if (
                    box.x >= self.canvas_width
                    or box.y >= self.canvas_height
                    or box.x + box.width <= 0
                    or box.y + box.height <= 0
                ):
                    raise ValueError(f"{name} does not intersect canvas")
            elif (
                box.x < 0
                or box.y < 0
                or box.x + box.width > self.canvas_width
                or box.y + box.height > self.canvas_height
            ):
                raise ValueError(f"{name} exceeds canvas bounds")
        protected = self.protected_center_bbox
        if not (protected.x <= self.canvas_width / 2 <= protected.x + protected.width):
            raise ValueError("protected_center_bbox 必須覆蓋畫布中央")
        if protected.width <= protected.height or protected.width < self.canvas_width * 0.5:
            raise ValueError("protected_center_bbox 必須是至少占畫布 50% 寬的橫向卡片")
        return self


def _shosho_asset_version() -> str:
    static_dir = Path(__file__).resolve().parent.parent / "static" / "shosho"
    h = hashlib.sha1()
    for name in ("tokens.css", "bridge.css", "bridge-pages.css"):
        p = static_dir / name
        if p.is_file():
            h.update(p.read_bytes())
    return h.hexdigest()[:8]


_SHOSHO_ASSET_VERSION = _shosho_asset_version()


def _packaging_root() -> Path:
    return get_vault_path() / "Attachments" / "packaging"


def _sync_conflicts(ep_dir: Path) -> list[str]:
    return sorted(p.name for p in ep_dir.glob("*sync-conflict*"))


def _load_brief(ep_dir: Path, cut_id: str) -> dict | None:
    """讀該支的內容速覽（`briefs/<cut_id>.json`），沒有就回 None。

    修修 2026-07-30：「審 util-L4 的時候我不太清楚這支影片在講什麼，所以我也
    沒辦法判斷。」board 原本只給封面＋標題文字，零內容脈絡 → 判斷不了是必然的。

    **UI 零 LLM 不變**（ADR-054 D11）：速覽由 Cowork 端 `packaging_brief.py`
    從該支 SRT 生成後落檔，本 router 只讀。壞檔不擋 board——速覽是輔助資訊，
    不該讓它的問題阻擋裁決；改為在該區塊顯示錯誤。
    """
    path = ep_dir / "briefs" / f"{cut_id}.json"
    if not path.is_file():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        return {"error": f"brief 壞檔：{exc}"}
    if not isinstance(data, dict):
        return {"error": "brief 格式錯誤：頂層必須是物件"}
    return data


def _load_cutout_choices(episode_slug: str) -> dict[str, list[dict]]:
    """列出這集可選的 cutout（vault `Attachments/cutouts/podcast/<slug>/`）。

    `records` 是本集所有已產出的正式 cutout；`validated` 只是量測狀態，不能拿來
    當 picker 過濾器。只收 records 中且頂層 PNG 實際存在的項目，避免把迭代路徑
    或 stale manifest row 呈現成可選素材。舊 manifest 沒 records 才退回 validated，
    再舊則列頂層 PNG，三條路徑都 fail-visible 標示 validated 狀態。
    """
    d = get_vault_path() / "Attachments" / "cutouts" / "podcast" / episode_slug
    out: dict[str, list[dict]] = {"host": [], "guest": []}
    if not d.is_dir():
        return out
    manifest = d / "cutouts_manifest.json"
    rows: list[dict] = []
    validated_names: set[str] = set()
    if manifest.is_file():
        try:
            payload = json.loads(manifest.read_text(encoding="utf-8"))
            validated_names = set((payload.get("validated") or {}).keys())
            for record in payload.get("records") or []:
                if not isinstance(record, dict) or not isinstance(record.get("file"), str):
                    continue
                rows.append(record)
            if not rows:
                rows = [{"file": name} for name in sorted(validated_names)]
        except json.JSONDecodeError:
            rows = []
    if not rows:
        rows = [{"file": p.name} for p in sorted(d.glob("*.png"))]
    seen: set[str] = set()
    for record in rows:
        name = record["file"]
        if name in seen:
            continue
        seen.add(name)
        asset = d / name
        if not asset.is_file():
            continue
        role = record.get("role")
        if role not in {"host", "guest"}:
            role = (
                "host" if name.startswith("host") else "guest" if name.startswith("guest") else None
            )
        if role is None:
            continue
        parts = name.removesuffix(".png").split("_")
        out[role].append(
            {
                "file": name,
                "vault_path": f"Attachments/cutouts/podcast/{episode_slug}/{name}",
                "emotion": record.get("emotion") or (parts[-1] if len(parts) >= 3 else ""),
                "validated": bool(record.get("validated") or name in validated_names),
                # The selector intentionally uses stable filenames so saved recipes do not
                # drift.  The preview URL therefore needs a content version; otherwise a
                # replaced PNG remains visually stale in the browser cache.
                "version": hashlib.sha256(asset.read_bytes()).hexdigest(),
            }
        )
    return out


def _load_approvals(ep_dir: Path, episode: str) -> ApprovalFileV1:
    path = ep_dir / "approval.json"
    if not path.is_file():
        return ApprovalFileV1(episode=episode, approvals=[])
    return parse_approval_file(path)


def _load_cut_tabs(ep_dir: Path, pkg: PackagesFileV1) -> list[dict]:
    """Build Full/Long tabs from the resume ledger without inventing ready assets."""
    manifest_path = ep_dir / "manifest.json"
    if not manifest_path.is_file():
        return []
    try:
        manifest = load_manifest(ep_dir)
    except SystemExit as exc:
        raise HTTPException(status_code=422, detail=f"manifest.json 驗證失敗：{exc}") from exc

    package_by_id = {cut.cut_id: cut for cut in pkg.cuts}
    ready_by_id = {cut_id: cut for cut_id, cut in package_by_id.items() if cut.format == "long"}
    rows: list[dict] = []
    used_ranks: set[int] = set()
    manifest_cuts = manifest["cuts"]
    for order, (cut_id, raw) in enumerate(manifest_cuts.items()):
        if not cut_id.strip():
            raise HTTPException(status_code=422, detail="manifest.json cut_id 不可為空")
        packaged_cut = package_by_id.get(cut_id)
        if packaged_cut is not None and packaged_cut.format == "short":
            continue
        is_full = cut_id.casefold() == "full"
        rank = raw.get("rank")
        if not is_full and rank is not None:
            if not isinstance(rank, int) or not 1 <= rank <= 3 or rank in used_ranks:
                raise HTTPException(
                    status_code=422,
                    detail=f"manifest.json Long rank 重複或超出 1..3：{rank!r}",
                )
            used_ranks.add(rank)
        packaging_work = raw.get("packaging")
        if packaging_work is not None and not isinstance(packaging_work, dict):
            raise HTTPException(
                status_code=422, detail=f"manifest.json {cut_id}.packaging 必須是物件"
            )
        status = (packaging_work or {}).get("status")
        if cut_id in ready_by_id:
            status = "ready"
        elif status == "ready" or "emitted" in raw:
            raise HTTPException(
                status_code=422,
                detail=f"manifest.json {cut_id} 已標完成，但 packages.json 沒有該 cut",
            )
        elif status is None:
            status = (
                "running" if any(stage in raw for stage in ("titles", "thumbnails")) else "queued"
            )
        if status not in {"queued", "running", "ready", "failed"}:
            raise HTTPException(
                status_code=422,
                detail=f"manifest.json {cut_id} Packaging 狀態不合法：{status!r}",
            )
        video_work = raw.get("video") if isinstance(raw.get("video"), dict) else {}
        video_status = video_work.get("status")
        if video_status is not None and video_status not in {
            "queued",
            "running",
            "ready",
            "failed",
        }:
            raise HTTPException(
                status_code=422,
                detail=f"manifest.json {cut_id} video 狀態不合法：{video_status!r}",
            )
        rows.append(
            {
                "cut_id": cut_id,
                "rank": rank,
                "label": "Full" if is_full else None,
                "title": raw.get("title") or cut_id,
                "status": status,
                "video_status": video_status,
                "is_full": is_full,
                "order": order,
            }
        )

    known_ids = {row["cut_id"] for row in rows}
    for cut in ready_by_id.values():
        if cut.cut_id in known_ids:
            continue
        rows.append(
            {
                "cut_id": cut.cut_id,
                "rank": None,
                "label": "Full" if cut.cut_id.casefold() == "full" else None,
                "title": cut.cut_id,
                "status": "ready",
                "video_status": None,
                "is_full": cut.cut_id.casefold() == "full",
                "order": len(rows),
            }
        )

    next_rank = 1
    for row in sorted(rows, key=lambda item: item["order"]):
        if row["is_full"] or row["rank"] is not None:
            continue
        while next_rank in used_ranks:
            next_rank += 1
        if next_rank > 3:
            raise HTTPException(status_code=422, detail="manifest.json 超過三支 Long Highlight")
        row["rank"] = next_rank
        used_ranks.add(next_rank)
    for row in rows:
        if not row["is_full"]:
            row["label"] = f"Long {row['rank']}"
    return sorted(
        rows,
        key=lambda item: (0, 0) if item["is_full"] else (1, item["rank"]),
    )


def _scan_episodes() -> list[dict]:
    """列表頁資料：每個 episode 目錄一列；壞檔/conflict 標 error，不吞。"""
    root = _packaging_root()
    rows: list[dict] = []
    if not root.is_dir():
        return rows
    for ep_dir in sorted((p for p in root.iterdir() if p.is_dir()), reverse=True):
        row: dict = {
            "slug": ep_dir.name,
            "episode": ep_dir.name,
            "error": None,
            "cuts_total": 0,
            "long_total": 0,
            "approved": 0,
        }
        conflicts = _sync_conflicts(ep_dir)
        if conflicts:
            row["error"] = f"Syncthing conflict：{', '.join(conflicts)} — 先解 conflict 再進 gate"
            rows.append(row)
            continue
        pkg_path = ep_dir / "packages.json"
        if not pkg_path.is_file():
            row["error"] = "缺 packages.json"
            rows.append(row)
            continue
        try:
            pkg = parse_packages(pkg_path)
            approvals = _load_approvals(ep_dir, pkg.episode)
        except (ValidationError, json.JSONDecodeError) as exc:
            row["error"] = f"檔案驗證失敗：{type(exc).__name__} — {str(exc)[:200]}"
            rows.append(row)
            continue
        row["episode"] = pkg.episode
        row["cuts_total"] = len(pkg.cuts)
        row["long_total"] = sum(1 for c in pkg.cuts if c.format == "long")
        row["approved"] = sum(1 for a in approvals.approvals if a.approved)
        rows.append(row)
    return rows


def _board_context(episode_slug: str) -> dict:
    """Board 頁資料。壞檔 → HTTPException（fail loud，不渲染半套 board）。"""
    ep_dir = _packaging_root() / episode_slug
    if not ep_dir.is_dir():
        raise HTTPException(status_code=404, detail=f"packaging episode not found: {episode_slug}")
    conflicts = _sync_conflicts(ep_dir)
    if conflicts:
        raise HTTPException(
            status_code=409,
            detail=f"Syncthing conflict in {episode_slug}: {', '.join(conflicts)} — "
            "先在 vault 解掉 *.sync-conflict-* 檔再進 gate（VAULT-LAYOUT conflict policy）",
        )
    try:
        pkg = parse_packages(ep_dir / "packages.json")
        approvals = _load_approvals(ep_dir, pkg.episode)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=f"packages.json missing: {exc}") from exc
    except (ValidationError, json.JSONDecodeError) as exc:
        raise HTTPException(
            status_code=422, detail=f"packages/approval 驗證失敗：{str(exc)[:500]}"
        ) from exc

    approval_by_cut = {a.cut_id: a for a in approvals.approvals}
    cuts = []
    for cut in pkg.cuts:
        titles_by_rank = {t.rank: t for t in cut.titles}
        package_views = []
        for package in cut.packages:
            editor_recipe = package.render_recipe or _legacy_reaction_recipe(
                ep_dir,
                episode=pkg.episode,
                cut_id=cut.cut_id,
                package=package,
            )
            package_views.append(
                {
                    "pkg": package,
                    "title": titles_by_rank.get(package.title_rank),
                    "editor_recipe": editor_recipe,
                    "composition": _composition_status(
                        ep_dir,
                        episode=pkg.episode,
                        cut_id=cut.cut_id,
                        package_rank=package.title_rank,
                        thumbnail_png=package.thumbnail_png,
                    ),
                }
            )
        recipe_payload = [
            {
                "package_rank": item["pkg"].title_rank,
                "title_rank": (
                    item["editor_recipe"].title_rank
                    if item["editor_recipe"]
                    else item["pkg"].title_rank
                ),
                "host_cutout": item["pkg"].host_cutout,
                "guest_cutout": item["pkg"].guest_cutout,
                "recipe": (
                    item["editor_recipe"].model_dump(mode="json")
                    if item["editor_recipe"]
                    else None
                ),
                "center_visual_url": (
                    f"/bridge/packaging/{episode_slug}/center-visual/"
                    f"{cut.cut_id}/{item['pkg'].title_rank}"
                    if item["editor_recipe"]
                    and item["editor_recipe"].composition == "thumbnail_reaction"
                    else None
                ),
            }
            for item in package_views
        ]
        view = {
            "cut": cut,
            "approval": approval_by_cut.get(cut.cut_id),
            "packages": package_views,
            "runners_up": [t for t in cut.titles if t.rank >= 4],
            "brief": _load_brief(ep_dir, cut.cut_id),
            "recipe_payload": recipe_payload,
        }
        cuts.append(view)
    return {
        "episode_slug": episode_slug,
        "pkg": pkg,
        "cuts": cuts,
        "cut_tabs": _load_cut_tabs(ep_dir, pkg),
        "cutouts": _load_cutout_choices(episode_slug),
    }


def _composition_receipt_path(ep_dir: Path, cut_id: str, package_rank: int) -> Path:
    if not _SAFE_RECEIPT_ID.fullmatch(cut_id):
        raise HTTPException(status_code=409, detail="composition receipt cut_id 不安全")
    return ep_dir / "composition_receipts" / f"{cut_id}-r{package_rank}.json"


def _load_composition_receipt(
    ep_dir: Path,
    *,
    episode: str,
    cut_id: str,
    package_rank: int,
    thumbnail_png: str,
    vault_root: Path | None = None,
) -> LongThumbnailCompositionReceiptV2:
    path = _composition_receipt_path(ep_dir, cut_id, package_rank)
    if not path.is_file():
        raise HTTPException(
            status_code=409,
            detail=f"composition receipt 缺失: {path.name}；長 highlight 必須保留中央主圖",
        )
    try:
        receipt = LongThumbnailCompositionReceiptV2.model_validate_json(
            path.read_text(encoding="utf-8")
        )
    except (OSError, ValidationError) as exc:
        raise HTTPException(
            status_code=422, detail=f"composition receipt 無效: {str(exc)[:500]}"
        ) from exc
    expected = (episode, cut_id, package_rank, thumbnail_png)
    actual = (
        receipt.episode,
        receipt.cut_id,
        receipt.package_rank,
        receipt.thumbnail_png,
    )
    if actual != expected:
        raise HTTPException(
            status_code=409,
            detail="composition receipt 與 selected package 不一致",
        )
    asset_ref = receipt.center_visual_asset.replace("\\", "/")
    if (
        asset_ref.startswith("/")
        or re.match(r"^[A-Za-z]:/", asset_ref)
        or ".." in Path(asset_ref).parts
        or Path(asset_ref).suffix.lower() not in {".png", ".jpg", ".jpeg", ".webp"}
    ):
        raise HTTPException(status_code=422, detail="center visual asset 路徑無效")
    vault_root = (vault_root or get_vault_path()).resolve()
    asset_path = (vault_root / asset_ref).resolve()
    try:
        asset_path.relative_to(ep_dir.resolve())
    except ValueError as exc:
        raise HTTPException(
            status_code=422,
            detail="center visual asset 必須位於該集 packaging 目錄",
        ) from exc
    if not asset_path.is_file():
        raise HTTPException(status_code=409, detail="center visual asset 檔案不存在")
    thumbnail_ref = receipt.thumbnail_png.replace("\\", "/")
    sidecar_ref = receipt.measurement_sidecar.replace("\\", "/")
    for label, ref in (("thumbnail", thumbnail_ref), ("measurement sidecar", sidecar_ref)):
        if ref.startswith("/") or re.match(r"^[A-Za-z]:/", ref) or ".." in Path(ref).parts:
            raise HTTPException(status_code=422, detail=f"{label} 路徑無效")
    thumbnail_path = (vault_root / thumbnail_ref).resolve()
    sidecar_path = (vault_root / sidecar_ref).resolve()
    try:
        thumbnail_path.relative_to(ep_dir.resolve())
        sidecar_path.relative_to(ep_dir.resolve())
    except ValueError as exc:
        raise HTTPException(
            status_code=422, detail="composition evidence 必須位於該集 packaging 目錄"
        ) from exc
    for label, evidence_path, expected_hash in (
        ("thumbnail", thumbnail_path, receipt.thumbnail_sha256),
        ("center visual", asset_path, receipt.center_visual_sha256),
        ("measurement sidecar", sidecar_path, receipt.measurement_sidecar_sha256),
    ):
        if not evidence_path.is_file():
            raise HTTPException(status_code=409, detail=f"{label} 檔案不存在")
        if hashlib.sha256(evidence_path.read_bytes()).hexdigest() != expected_hash:
            raise HTTPException(status_code=409, detail=f"{label} hash 不一致")
    try:
        measurement = json.loads(sidecar_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise HTTPException(status_code=422, detail="measurement sidecar 無效") from exc
    renderer = measurement.get("renderer") or {}
    measured_identity = f"{renderer.get('name')}@{renderer.get('version')}"
    center_hash = (measurement.get("assets") or {}).get("prop_image_data_url", {}).get("sha256")
    if (
        measurement.get("schema") != "nakama.thumbnail_composition_measurement.v1"
        or measurement.get("composition") != "thumbnail_reaction"
        or measured_identity != receipt.renderer_identity
        or measurement.get("png_sha256") != receipt.thumbnail_sha256
        or center_hash != receipt.center_visual_sha256
    ):
        raise HTTPException(status_code=409, detail="measurement sidecar identity/hash 不一致")
    measured_canvas = measurement.get("canvas") or {}
    measured_boxes = measurement.get("bboxes") or {}
    expected_boxes = {
        "protected_center_bbox": receipt.protected_center_bbox.model_dump(),
        "host_bbox": receipt.host_bbox.model_dump(),
        "guest_bbox": receipt.guest_bbox.model_dump(),
        "title_bbox": receipt.title_bbox.model_dump() if receipt.title_bbox else None,
    }
    if (
        measured_canvas != {"width": receipt.canvas_width, "height": receipt.canvas_height}
        or measured_boxes != expected_boxes
    ):
        raise HTTPException(status_code=409, detail="measurement sidecar bbox 與 receipt 不一致")
    return receipt


def _legacy_reaction_recipe(
    ep_dir: Path,
    *,
    episode: str,
    cut_id: str,
    package,
) -> RenderRequestV1 | None:
    """Hydrate one legacy N2 package only from its own measured receipt."""
    try:
        receipt = _load_composition_receipt(
            ep_dir,
            episode=episode,
            cut_id=cut_id,
            package_rank=package.title_rank,
            thumbnail_png=package.thumbnail_png,
        )
    except HTTPException:
        return None
    canvas_w = float(receipt.canvas_width)
    canvas_h = float(receipt.canvas_height)
    center = receipt.protected_center_bbox
    host = receipt.host_bbox
    guest = receipt.guest_bbox
    return RenderRequestV1(
        composition="thumbnail_reaction",
        title_rank=package.title_rank,
        host_cutout=package.host_cutout,
        guest_cutout=package.guest_cutout,
        big_text=[],
        requested_at=datetime.fromtimestamp(
            _composition_receipt_path(ep_dir, cut_id, package.title_rank).stat().st_mtime,
            tz=timezone.utc,
        ),
        geometry=GeometryV1(
            host_height_pct=host.height / canvas_h * 100,
            host_x_pct=host.x / canvas_w * 100,
            host_y_pct=(canvas_h - host.y - host.height) / canvas_h * 100,
            guest_height_pct=guest.height / canvas_h * 100,
            guest_x_pct=(canvas_w - guest.x - guest.width) / canvas_w * 100,
            guest_y_pct=(canvas_h - guest.y - guest.height) / canvas_h * 100,
        ),
        geometry_manual=True,
        center_visual_asset=receipt.center_visual_asset,
        center_geometry=CenterGeometryV1(
            width_pct=center.width / canvas_w * 100,
            height_px=center.height,
            x_pct=(center.x + center.width / 2) / canvas_w * 100,
            y_pct=(center.y + center.height / 2) / canvas_h * 100,
        ),
    )


def _composition_status(
    ep_dir: Path,
    *,
    episode: str,
    cut_id: str,
    package_rank: int,
    thumbnail_png: str,
) -> dict:
    try:
        receipt = _load_composition_receipt(
            ep_dir,
            episode=episode,
            cut_id=cut_id,
            package_rank=package_rank,
            thumbnail_png=thumbnail_png,
        )
    except HTTPException as exc:
        return {"verified": False, "reason": str(exc.detail)}
    return {
        "verified": True,
        "reason": "中央橫向主圖與 render 成品已驗證",
        "center_visual_asset": receipt.center_visual_asset,
    }


def _publish_url(episode: str, cut_id: str) -> str:
    return f"/bridge/publish/{quote(episode, safe='')}/{quote(cut_id, safe='')}"


def _youtube_target(release: dict) -> dict:
    target = next((row for row in release["targets"] if row["platform"] == "youtube"), None)
    if target is None:
        raise HTTPException(status_code=409, detail="youtube release target 不存在")
    return target


def _description_job_path(episode: str, cut_id: str) -> Path:
    data_dir = Path(os.environ.get("NAKAMA_DATA_DIR") or get_db_path().parent)
    safe = f"{episode}_{cut_id}".replace("/", "_").replace("\\", "_")
    return data_dir / "description_progress" / f"{safe}.job.json"


def _description_state(
    release: dict, episode: str | None = None, cut_id: str | None = None
) -> tuple[str, str | None]:
    target = _youtube_target(release)
    # Compatibility for old receipts/tests that predate the description column in the view.
    if "description" not in target:
        return "ready", None
    if str(target.get("description") or "").strip():
        return "ready", None
    error = str(target.get("error") or "")
    if error.startswith(_DESCRIPTION_INTERRUPTED):
        return "interrupted", error
    if error == _DESCRIPTION_GENERATING:
        if episode and cut_id:
            receipt_path = _description_job_path(episode, cut_id)
            job = load_job(receipt_path)
            process = _DESCRIPTION_PROCESSES.get((episode, cut_id))
            exit_code = process.poll() if process is not None else None
            stale = (
                job is None or job_expired(job) or (process is not None and exit_code is not None)
            )
            if stale:
                reason = (
                    f"background child exited ({exit_code})"
                    if process is not None and exit_code is not None
                    else "background attempt exceeded its deadline or receipt is missing"
                )
                interrupted = f"{_DESCRIPTION_INTERRUPTED} {reason}"
                update_target(int(target["id"]), error=interrupted)
                if job is not None:
                    atomic_job_write(
                        receipt_path,
                        {**job, "status": "failed", "exit_code": exit_code, "error": reason},
                    )
                return "interrupted", interrupted
        return "generating", None
    return "missing", None


def _episode_dir(episode: str) -> Path:
    configured = os.environ.get("PODCAST_EPISODES_ROOT", "").strip()
    if not configured:
        raise HTTPException(status_code=503, detail="PODCAST_EPISODES_ROOT 未設定")
    root = Path(configured).resolve()
    episode_dir = (root / episode).resolve()
    try:
        episode_dir.relative_to(root)
    except ValueError as exc:
        raise HTTPException(
            status_code=403,
            detail="episode 路徑超出 PODCAST_EPISODES_ROOT",
        ) from exc
    if not episode_dir.is_dir():
        raise HTTPException(status_code=404, detail=f"episode 不存在: {episode}")
    return episode_dir


def _start_description_draft(episode: str, cut_id: str, target_id: int) -> None:
    """Start subscription-backed description generation without blocking the review UI."""
    episode_dir = _episode_dir(episode)
    receipt = _description_job_path(episode, cut_id)
    current = load_job(receipt)
    running = _DESCRIPTION_PROCESSES.get((episode, cut_id))
    if running is not None and running.poll() is None:
        return
    if current and current.get("status") == "generating" and not job_expired(current):
        return
    job = new_job(
        status="generating",
        timeout_seconds=_DESCRIPTION_TIMEOUT_SECONDS,
        episode=episode,
        cut_id=cut_id,
        target_id=target_id,
    )
    atomic_job_write(receipt, job)
    update_target(target_id, error=_DESCRIPTION_GENERATING)
    root = Path(__file__).resolve().parent.parent.parent
    script = root / "scripts" / "publish_description.py"
    data_dir = Path(os.environ.get("NAKAMA_DATA_DIR") or get_db_path().parent)
    log_dir = data_dir / "description_progress"
    log_dir.mkdir(parents=True, exist_ok=True)
    safe = f"{episode}_{cut_id}".replace("/", "_").replace("\\", "_")
    log_file = open(log_dir / f"{safe}.log", "a", encoding="utf-8")  # noqa: SIM115
    try:
        process = subprocess.Popen(
            [
                sys.executable,
                str(script),
                str(episode_dir),
                "--cut",
                cut_id,
                "--auto",
                "--job-receipt",
                str(receipt),
                "--attempt-id",
                str(job["attempt_id"]),
            ],
            cwd=str(root),
            env=os.environ.copy(),
            stdout=log_file,
            stderr=subprocess.STDOUT,
        )
        _DESCRIPTION_PROCESSES[(episode, cut_id)] = process
        atomic_job_write(receipt, {**job, "pid": process.pid})
    except OSError as exc:
        atomic_job_write(
            receipt,
            {**job, "status": "failed", "exit_code": None, "error": f"OSError: {exc}"},
        )
        update_target(target_id, error=f"{_DESCRIPTION_INTERRUPTED} OSError: {exc}")
        raise HTTPException(status_code=503, detail=f"無法啟動 description draft：{exc}") from exc
    finally:
        # Popen duplicates/inherits the handle it needs; the web worker must not
        # retain one descriptor per description attempt for its entire lifetime.
        log_file.close()


def _ensure_description_handoff(episode: str, cut_id: str, release: dict) -> str:
    state, _ = _description_state(release, episode, cut_id)
    if state == "ready" or state == "generating":
        return state
    target = _youtube_target(release)
    _start_description_draft(episode, cut_id, int(target["id"]))
    return "generating"


def _publish_prep_state(episode_dir: Path, cut_id: str) -> dict | None:
    receipt = episode_dir / "highlights" / "exports" / f".publish_prep_{cut_id}.json"
    payload = load_job(receipt)
    if not payload or payload.get("status") != "rendering":
        return payload
    key = (str(episode_dir.resolve()), cut_id)
    process = _PUBLISH_PREP_PROCESSES.get(key)
    exit_code = process.poll() if process is not None else None
    if (process is not None and exit_code is not None) or job_expired(payload):
        reason = (
            f"background child exited ({exit_code})"
            if process is not None and exit_code is not None
            else "background attempt exceeded its deadline"
        )
        payload = {**payload, "status": "failed", "exit_code": exit_code, "error": reason}
        atomic_job_write(receipt, payload)
    return payload


def _ensure_publish_prep(episode: str, cut_id: str) -> None:
    """Start or resume the full-resolution export for an approved package."""
    root_value = os.environ.get("PODCAST_EPISODES_ROOT", "").strip()
    if not root_value:
        raise HTTPException(status_code=503, detail="PODCAST_EPISODES_ROOT 未設定")
    root = Path(root_value).resolve()
    episode_dir = (root / episode).resolve()
    try:
        episode_dir.relative_to(root)
    except ValueError as exc:
        raise HTTPException(
            status_code=403, detail="episode 路徑超出 PODCAST_EPISODES_ROOT"
        ) from exc
    if not episode_dir.is_dir():
        raise HTTPException(status_code=404, detail=f"episode 不存在: {episode}")
    key = (str(episode_dir.resolve()), cut_id)
    running = _PUBLISH_PREP_PROCESSES.get(key)
    root_path = Path(__file__).resolve().parent.parent.parent
    script = root_path / "scripts" / "publish_prep.py"
    data_dir = Path(os.environ.get("NAKAMA_DATA_DIR") or get_db_path().parent)
    log_dir = data_dir / "publish_prep"
    log_dir.mkdir(parents=True, exist_ok=True)
    receipt = episode_dir / "highlights" / "exports" / f".publish_prep_{cut_id}.json"
    current = _publish_prep_state(episode_dir, cut_id)
    if current and current.get("status") == "rendered":
        return
    if running is not None and running.poll() is None:
        return
    if current and current.get("status") == "rendering" and not job_expired(current):
        return
    job = new_job(
        status="rendering",
        timeout_seconds=_PUBLISH_PREP_TIMEOUT_SECONDS,
        episode=episode_dir.name,
        cut_id=cut_id,
    )
    atomic_job_write(receipt, job)
    safe_episode = episode_dir.name.replace("/", "_").replace("\\", "_")
    log_file = open(  # noqa: SIM115 -- Popen duplicates the descriptor
        log_dir / f"{safe_episode}_{cut_id}.log", "a", encoding="utf-8"
    )
    configured = os.environ.get("NAKAMA_RESOLVE_PYTHON", "").strip()
    command = [configured, str(script)] if configured else [sys.executable, str(script)]
    try:
        process = subprocess.Popen(
            [
                *command,
                str(episode_dir),
                "--cut",
                cut_id,
                "--render-only",
                "--receipt",
                str(receipt),
                "--attempt-id",
                str(job["attempt_id"]),
            ],
            cwd=str(root_path),
            stdout=log_file,
            stderr=subprocess.STDOUT,
        )
    except OSError as exc:
        log_file.close()
        atomic_job_write(
            receipt,
            {**job, "status": "failed", "exit_code": None, "error": f"OSError: {exc}"},
        )
        raise HTTPException(status_code=503, detail=f"無法啟動最終成品匯出：{exc}") from exc
    _PUBLISH_PREP_PROCESSES[key] = process
    atomic_job_write(receipt, {**job, "pid": process.pid})
    log_file.close()


def _release_from_receipt(episode: str, cut_id: str) -> dict | None:
    """Validate a Resolve receipt and register it with the Web runtime."""
    release = get_release(episode, cut_id)
    if release is not None:
        return release
    root_value = os.environ.get("PODCAST_EPISODES_ROOT", "").strip()
    if not root_value:
        return None
    root = Path(root_value).resolve()
    episode_dir = (root / episode).resolve()
    try:
        episode_dir.relative_to(root)
    except ValueError:
        return None
    receipt = episode_dir / "highlights" / "exports" / f".publish_prep_{cut_id}.json"
    if not receipt.is_file():
        return None
    _publish_prep_state(episode_dir, cut_id)
    try:
        payload = json.loads(receipt.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    if payload.get("status") != "rendered" or payload.get("episode") != episode:
        return None
    rows = [row for row in payload.get("cuts", []) if row.get("cut_id") == cut_id]
    if len(rows) != 1:
        raise HTTPException(status_code=409, detail="publish_prep receipt 缺少唯一 cut")
    row = rows[0]
    file_path = Path(str(row.get("file", ""))).resolve()
    exports_dir = (episode_dir / "highlights" / "exports").resolve()
    try:
        file_path.relative_to(exports_dir)
    except ValueError as exc:
        raise HTTPException(status_code=409, detail="publish_prep receipt 成品路徑越界") from exc
    if not file_path.is_file() or file_path.stat().st_size != int(row.get("file_bytes", -1)):
        raise HTTPException(status_code=409, detail="publish_prep receipt 與成品檔不一致")
    release_id = register_release(
        episode,
        cut_id,
        str(row.get("format", "")),
        str(file_path),
        work_title=str(row.get("work_title", "")),
        file_bytes=file_path.stat().st_size,
        duration_sec=float(row.get("duration_sec", 0)),
    )
    ensure_target(release_id, "youtube")
    logger.info("publish_prep receipt registered: %s/%s", episode, cut_id)
    return get_release(episode, cut_id)


def _apply_packaging_to_release(
    pkg: PackagesFileV1, cut_id: str, primary_package: int, release: dict
) -> None:
    cut = next((row for row in pkg.cuts if row.cut_id == cut_id), None)
    if cut is None:
        raise HTTPException(status_code=404, detail=f"cut not found: {cut_id}")
    target = next((row for row in release["targets"] if row["platform"] == "youtube"), None)
    if target is None:
        raise HTTPException(status_code=409, detail="youtube release target 不存在")
    if target["status"] in {"uploading", "uploaded", "published"}:
        raise HTTPException(status_code=409, detail="影片已進入上傳流程，不能更換 Packaging")
    title_by_rank = {row.rank: row.text for row in cut.titles}
    if primary_package not in title_by_rank:
        raise HTTPException(status_code=409, detail="primary package 的標題不存在")
    fields = {"title": title_by_rank[primary_package]}
    if cut.format == "long":
        selected_package = next(
            (row for row in cut.packages if row.title_rank == primary_package), None
        )
        if selected_package is None:
            raise HTTPException(status_code=409, detail="primary package 不存在")
        fields["thumbnail_path"] = selected_package.thumbnail_png
    update_target(target["id"], **fields)


@page_router.get("", response_class=HTMLResponse)
async def packaging_list(request: Request, nakama_auth: str | None = Cookie(None)):
    if not check_auth(nakama_auth):
        return RedirectResponse("/login?next=/bridge/packaging", status_code=302)
    return _templates.TemplateResponse(
        request,
        "packaging_list.html",
        {"rows": _scan_episodes(), "asset_version": _SHOSHO_ASSET_VERSION},
    )


@page_router.get("/{episode_slug}/thumbnail/{filename}")
async def packaging_thumbnail(
    episode_slug: str,
    filename: str,
    nakama_auth: str | None = Cookie(None),
) -> FileResponse:
    if not check_auth(nakama_auth):
        raise HTTPException(status_code=401, detail="unauthorized")
    if filename != Path(filename).name or not filename.lower().endswith(".png"):
        raise HTTPException(status_code=403, detail="僅限 Packaging PNG")
    ctx = _board_context(episode_slug)
    refs = {
        package.thumbnail_png
        for cut in ctx["pkg"].cuts
        for package in cut.packages
        if Path(package.thumbnail_png).name == filename
    }
    if len(refs) != 1:
        raise HTTPException(status_code=404, detail="Packaging 縮圖不存在或不唯一")
    path = get_vault_path() / refs.pop()
    try:
        path.resolve().relative_to((_packaging_root() / episode_slug).resolve())
    except ValueError as exc:
        raise HTTPException(status_code=403, detail="Packaging 縮圖超出 episode 目錄") from exc
    if not path.is_file():
        raise HTTPException(status_code=404, detail="Packaging 縮圖檔不存在")
    return FileResponse(path, media_type="image/png")


@page_router.get("/{episode_slug}/cutout/{filename}")
async def packaging_cutout(
    episode_slug: str,
    filename: str,
    nakama_auth: str | None = Cookie(None),
) -> FileResponse:
    """Serve a registered episode cutout through the mounted Packaging router."""
    if not check_auth(nakama_auth):
        raise HTTPException(status_code=401, detail="unauthorized")
    if filename != Path(filename).name or not filename.lower().endswith(".png"):
        raise HTTPException(status_code=403, detail="僅限 Packaging cutout PNG")
    choices = _load_cutout_choices(episode_slug)
    allowed = {row["file"] for rows in choices.values() for row in rows}
    if filename not in allowed:
        raise HTTPException(status_code=404, detail="Packaging cutout 不存在或未登記")
    path = (
        get_vault_path()
        / "Attachments"
        / "cutouts"
        / "podcast"
        / episode_slug
        / filename
    )
    if not path.is_file():
        raise HTTPException(status_code=404, detail="Packaging cutout 檔不存在")
    return FileResponse(path, media_type="image/png")


@page_router.get("/{episode_slug}/recipe-asset/{filename}")
async def packaging_recipe_asset(
    episode_slug: str,
    filename: str,
    nakama_auth: str | None = Cookie(None),
) -> FileResponse:
    """Serve only an episode-local PNG explicitly referenced by a package recipe."""
    if not check_auth(nakama_auth):
        raise HTTPException(status_code=401, detail="unauthorized")
    if filename != Path(filename).name or not filename.lower().endswith(".png"):
        raise HTTPException(status_code=403, detail="僅限 Packaging recipe PNG")
    ctx = _board_context(episode_slug)
    refs = {
        package.render_recipe.book_cover
        for cut in ctx["pkg"].cuts
        for package in cut.packages
        if package.render_recipe
        and package.render_recipe.book_cover
        and Path(package.render_recipe.book_cover).name == filename
    }
    if len(refs) != 1:
        raise HTTPException(status_code=404, detail="Recipe asset 不存在或不唯一")
    path = get_vault_path() / refs.pop()
    try:
        path.resolve().relative_to((_packaging_root() / episode_slug).resolve())
    except ValueError as exc:
        raise HTTPException(status_code=403, detail="Recipe asset 超出 episode 目錄") from exc
    if not path.is_file():
        raise HTTPException(status_code=404, detail="Recipe asset 檔不存在")
    return FileResponse(path, media_type="image/png")


@page_router.get("/{episode_slug}/center-visual/{cut_id}/{package_rank}")
async def packaging_center_visual(
    episode_slug: str,
    cut_id: str,
    package_rank: int,
    nakama_auth: str | None = Cookie(None),
) -> FileResponse:
    """Serve the center visual bound to exactly one package rank."""
    if not check_auth(nakama_auth):
        raise HTTPException(status_code=401, detail="unauthorized")
    ep_dir = _packaging_root() / episode_slug
    pkg = parse_packages(ep_dir / "packages.json")
    cut = next((row for row in pkg.cuts if row.cut_id == cut_id), None)
    package = next(
        (row for row in (cut.packages if cut else []) if row.title_rank == package_rank),
        None,
    )
    if package is None:
        raise HTTPException(status_code=404, detail="package 不存在")
    recipe = package.render_recipe or _legacy_reaction_recipe(
        ep_dir,
        episode=pkg.episode,
        cut_id=cut_id,
        package=package,
    )
    if (
        recipe is None
        or recipe.composition != "thumbnail_reaction"
        or not recipe.center_visual_asset
    ):
        raise HTTPException(status_code=404, detail="package 沒有中央主圖")
    path = (get_vault_path() / recipe.center_visual_asset).resolve()
    try:
        path.relative_to(ep_dir.resolve())
    except ValueError as exc:
        raise HTTPException(status_code=403, detail="中央主圖超出 episode 目錄") from exc
    if path.suffix.lower() not in {".png", ".jpg", ".jpeg", ".webp"} or not path.is_file():
        raise HTTPException(status_code=404, detail="中央主圖不存在")
    return FileResponse(path)


@page_router.get("/{episode_slug}/render-status/{cut_id}/{package_rank}")
async def packaging_render_status(
    episode_slug: str,
    cut_id: str,
    package_rank: int,
    requested_at: str,
    nakama_auth: str | None = Cookie(None),
) -> JSONResponse:
    """Return the exact desktop-render state for one saved package recipe.

    The client must present the recipe timestamp it just saved.  A stale tab therefore
    cannot accidentally display another package or a newer revision as its own result.
    """
    if not check_auth(nakama_auth):
        raise HTTPException(status_code=401, detail="unauthorized")
    if package_rank < 1 or package_rank > 3:
        raise HTTPException(status_code=404, detail="package 不存在")

    ep_dir = _packaging_root() / episode_slug
    try:
        packages = parse_packages(ep_dir / "packages.json")
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail="packaging episode 不存在") from exc
    cut = next((row for row in packages.cuts if row.cut_id == cut_id), None)
    if cut is None:
        raise HTTPException(status_code=404, detail="cut 不存在")
    package = next((row for row in cut.packages if row.title_rank == package_rank), None)
    if package is None:
        raise HTTPException(status_code=404, detail="package 不存在")
    recipe = package.render_recipe
    if recipe is None:
        raise HTTPException(status_code=409, detail="這個 package 尚未儲存 render 配方")
    expected = recipe.requested_at.isoformat()
    if not _same_requested_at(requested_at, expected):
        raise HTTPException(status_code=409, detail="這份 render 狀態不屬於目前配方")

    key = f"{episode_slug}/{cut_id}/r{package_rank}"
    state: dict = {}
    state_path = _render_watcher_state_path()
    if state_path.is_file():
        try:
            loaded = json.loads(state_path.read_text(encoding="utf-8"))
            if isinstance(loaded, dict):
                state = loaded
        except (json.JSONDecodeError, OSError):
            # A partial/corrupt state file cannot be trusted as success or failure.
            state = {}
    row = state.get(key) if isinstance(state.get(key), dict) else None
    if row is None or not _same_requested_at(str(row.get("requested_at", "")), expected):
        status = "queued"
        message = "配方已儲存，等待桌面 render"
        error = None
    else:
        raw_status = row.get("status")
        if raw_status not in {"running", "done", "failed"}:
            # Backward-compatible read of watcher state written before explicit statuses.
            raw_status = "done" if row.get("ok") is True else "failed"
        status = raw_status
        message = {
            "running": "正在 render 新封面",
            "done": "新封面已完成",
            "failed": "封面 render 失敗",
        }[status]
        error = str(row.get("last_error") or "")[:500] or None

    thumbnail_url = None
    if status == "done":
        version_seed = str(row.get("rendered_at") or expected) if row else expected
        version = hashlib.sha1(version_seed.encode("utf-8")).hexdigest()[:12]
        filename = quote(Path(package.thumbnail_png).name, safe="")
        thumbnail_url = (
            f"/bridge/packaging/{quote(episode_slug, safe='')}/thumbnail/{filename}?v={version}"
        )
    return JSONResponse(
        {
            "status": status,
            "message": message,
            "error": error,
            "episode_slug": episode_slug,
            "cut_id": cut_id,
            "package_rank": package_rank,
            "requested_at": requested_at,
            "thumbnail_url": thumbnail_url,
        },
        headers={"Cache-Control": "no-store"},
    )


@page_router.get("/{episode_slug}", response_class=HTMLResponse)
async def packaging_board(
    request: Request,
    episode_slug: str,
    edited: str | None = None,
    composed: str | None = None,
    package_rank: int | None = None,
    cut: str | None = None,
    release_pending: str | None = None,
    description_pending: str | None = None,
    nakama_auth: str | None = Cookie(None),
):
    if not check_auth(nakama_auth):
        return RedirectResponse(f"/login?next=/bridge/packaging/{episode_slug}", status_code=302)
    ctx = _board_context(episode_slug)
    focused_cut = cut
    pending_cut = None
    if ctx["cut_tabs"]:
        focused_cut = focused_cut or ctx["cut_tabs"][0]["cut_id"]
        selected_tab = next((tab for tab in ctx["cut_tabs"] if tab["cut_id"] == focused_cut), None)
        if selected_tab is None:
            raise HTTPException(status_code=404, detail=f"cut not found: {focused_cut}")
        focused = [view for view in ctx["cuts"] if view["cut"].cut_id == focused_cut]
        ctx["cuts"] = focused
        if not focused:
            pending_cut = selected_tab
    elif cut:
        focused = [view for view in ctx["cuts"] if view["cut"].cut_id == cut]
        if not focused:
            raise HTTPException(status_code=404, detail=f"cut not found: {cut}")
        ctx["cuts"] = focused
    if release_pending and cut:
        if pending_cut is not None:
            raise HTTPException(status_code=409, detail="Packaging 尚未完成")
        release = _release_from_receipt(ctx["pkg"].episode, cut)
        if release is not None:
            approval = ctx["cuts"][0]["approval"]
            if approval is None or not approval.approved:
                raise HTTPException(status_code=409, detail="Packaging 尚未核准")
            _apply_packaging_to_release(ctx["pkg"], cut, approval.primary_package, release)
            description_state = _ensure_description_handoff(ctx["pkg"].episode, cut, release)
            if description_state == "ready":
                return RedirectResponse(_publish_url(ctx["pkg"].episode, cut), status_code=303)
            focused_board = (
                f"/bridge/packaging/{quote(episode_slug, safe='')}?cut={quote(cut, safe='')}"
            )
            return RedirectResponse(f"{focused_board}&description_pending=1", status_code=303)
    description_state = None
    description_error = None
    if description_pending and cut:
        release = get_release(ctx["pkg"].episode, cut)
        if release is not None:
            description_state, description_error = _description_state(
                release, ctx["pkg"].episode, cut
            )
            if description_state == "ready":
                return RedirectResponse(_publish_url(ctx["pkg"].episode, cut), status_code=303)
    ctx["asset_version"] = _SHOSHO_ASSET_VERSION
    # 剛改完字的那支：改字區保持展開（見 packaging_edit_title 的 redirect 註解）
    ctx["edited_cut"] = edited
    # 剛存完配方那支：組封面區保持展開（同 edited 的理由 — <details> 會因重載收起）
    ctx["composed_cut"] = composed
    ctx["editor_package_rank"] = package_rank
    ctx["focused_cut"] = focused_cut
    ctx["pending_cut"] = pending_cut
    ctx["release_pending"] = bool(release_pending)
    ctx["description_pending"] = bool(description_pending)
    ctx["description_state"] = description_state
    ctx["description_error"] = description_error
    ctx["revision_polling"] = any(
        view["approval"]
        and view["approval"].revision_job
        and view["approval"].revision_job.status in {"queued", "running"}
        for view in ctx["cuts"]
    )
    return _templates.TemplateResponse(request, "packaging_board.html", ctx)


@page_router.post("/{episode_slug}/approve")
async def packaging_approve(
    episode_slug: str,
    cut_id: str = Form(..., max_length=_EP_SLUG_MAX),
    decision: str = Form(...),
    primary_package: int | None = Form(None),
    reject_note: str = Form("", max_length=_NOTE_MAX),
    nakama_auth: str | None = Cookie(None),
):
    """寫 approval.json（upsert 該 cut 的裁決）— 本 router 唯一寫這個檔。"""
    if not check_auth(nakama_auth):
        return RedirectResponse("/login?next=/bridge/packaging", status_code=302)
    if decision not in ("approve", "reject"):
        raise HTTPException(status_code=400, detail=f"unknown decision: {decision!r}")
    approved = decision == "approve"
    feedback = reject_note.strip()
    if approved and primary_package is None:
        raise HTTPException(status_code=400, detail="approve 需要指定 primary_package (1–3)")
    if not approved and not feedback:
        raise HTTPException(status_code=400, detail="Reject 需要填寫 feedback，Agent 才能重做")

    ctx = _board_context(episode_slug)  # 同時擋 conflict / 壞檔
    pkg: PackagesFileV1 = ctx["pkg"]
    cut = next((c for c in pkg.cuts if c.cut_id == cut_id), None)
    if cut is None:
        raise HTTPException(status_code=404, detail=f"cut not found: {cut_id}")
    if approved and cut.format == "long" and not any(p.title_rank for p in cut.packages):
        raise HTTPException(status_code=409, detail="長片尚無 package，先跑 thumbnail-brainstorm")

    ep_dir = _packaging_root() / episode_slug
    if (
        approved
        and cut.format == "long"
        and not any(row.title_rank == primary_package for row in cut.packages)
    ):
        raise HTTPException(status_code=409, detail="primary package 不存在")
    # Human approval is the final taste decision. Composition receipts and
    # protected-center checks remain visible diagnostics on the board, but they
    # must never veto an explicit Approve. Structural failures above still stop
    # the request because there would be no concrete package to publish.
    existing = _load_approvals(ep_dir, pkg.episode)
    prev = next((a for a in existing.approvals if a.cut_id == cut_id), None)
    decided_at = datetime.now(timezone.utc)
    revision_job = None
    if not approved:
        source_assets: dict[str, str] = {}
        for package in cut.packages:
            asset = get_vault_path() / package.thumbnail_png
            if asset.is_file():
                source_assets[package.thumbnail_png] = hashlib.sha256(
                    asset.read_bytes()
                ).hexdigest()
        request_seed = "\0".join((pkg.episode, cut_id, decided_at.isoformat(), feedback)).encode(
            "utf-8"
        )
        revision_job = PackagingRevisionJobV1(
            request_id=f"revision-{hashlib.sha256(request_seed).hexdigest()[:16]}",
            feedback=feedback,
            requested_at=decided_at,
            source_packages_sha256=hashlib.sha256(
                (ep_dir / "packages.json").read_bytes()
            ).hexdigest(),
            source_assets=source_assets,
        )
    entry = ApprovalV1(
        cut_id=cut_id,
        approved=approved,
        primary_package=primary_package if approved else 1,
        reject_note=feedback or None,
        decided_at=decided_at,
        decision=decision,
        # 挑臉／打大字是另一支 form 寫的，approve 不可以把它們洗掉
        # （2026-08-14 browser UAT 抓到：勾完變體再 approve，選擇整個不見）。
        selected_variant=prev.selected_variant if prev else None,
        bigtext_request=prev.bigtext_request if prev else None,
        render_request=prev.render_request if prev else None,
        revision_job=revision_job,
    )
    others = [a for a in existing.approvals if a.cut_id != cut_id]
    updated = ApprovalFileV1(episode=pkg.episode, approvals=[*others, entry])
    (ep_dir / "approval.json").write_text(
        updated.model_dump_json(indent=2) + "\n", encoding="utf-8"
    )
    logger.info("packaging approve: %s/%s approved=%s", episode_slug, cut_id, approved)
    focused_board = f"/bridge/packaging/{quote(episode_slug, safe='')}?cut={quote(cut_id, safe='')}"
    if not approved:
        return RedirectResponse(focused_board, status_code=303)
    release = _release_from_receipt(pkg.episode, cut_id)
    if release is None:
        _ensure_publish_prep(pkg.episode, cut_id)
        return RedirectResponse(f"{focused_board}&release_pending=1", status_code=303)
    _apply_packaging_to_release(pkg, cut_id, primary_package or 1, release)
    description_state = _ensure_description_handoff(pkg.episode, cut_id, release)
    logger.info("packaging -> publish handoff: %s/%s", pkg.episode, cut_id)
    if description_state != "ready":
        return RedirectResponse(f"{focused_board}&description_pending=1", status_code=303)
    return RedirectResponse(_publish_url(pkg.episode, cut_id), status_code=303)


@page_router.post("/{episode_slug}/description/retry")
async def packaging_retry_description(
    episode_slug: str,
    cut_id: str = Form(..., max_length=_EP_SLUG_MAX),
    nakama_auth: str | None = Cookie(None),
):
    if not check_auth(nakama_auth):
        return RedirectResponse("/login?next=/bridge/packaging", status_code=302)
    ctx = _board_context(episode_slug)
    approval = next((row["approval"] for row in ctx["cuts"] if row["cut"].cut_id == cut_id), None)
    if approval is None or not approval.approved:
        raise HTTPException(status_code=409, detail="Packaging 尚未核准")
    release = get_release(ctx["pkg"].episode, cut_id)
    if release is None:
        raise HTTPException(status_code=409, detail="publish_prep 尚未完成")
    state, _ = _description_state(release, ctx["pkg"].episode, cut_id)
    if state not in {"ready", "generating"}:
        target = _youtube_target(release)
        _start_description_draft(ctx["pkg"].episode, cut_id, int(target["id"]))
    focused_board = f"/bridge/packaging/{quote(episode_slug, safe='')}?cut={quote(cut_id, safe='')}"
    return RedirectResponse(f"{focused_board}&description_pending=1", status_code=303)


@page_router.post("/{episode_slug}/revision/retry")
async def packaging_retry_revision(
    episode_slug: str,
    cut_id: str = Form(..., max_length=_EP_SLUG_MAX),
    nakama_auth: str | None = Cookie(None),
):
    """Requeue one failed revision; the worker still cannot approve the result."""
    if not check_auth(nakama_auth):
        return RedirectResponse("/login?next=/bridge/packaging", status_code=302)
    ctx = _board_context(episode_slug)
    ep_dir = _packaging_root() / episode_slug
    approvals = _load_approvals(ep_dir, ctx["pkg"].episode)
    previous = next((row for row in approvals.approvals if row.cut_id == cut_id), None)
    if previous is None or previous.revision_job is None:
        raise HTTPException(status_code=404, detail="revision job not found")
    if previous.revision_job.status != "failed":
        raise HTTPException(status_code=409, detail="只有 failed revision 可以重試")
    revision = previous.revision_job.model_copy(
        update={
            "status": "queued",
            "started_at": None,
            "finished_at": None,
            "result_receipt": None,
            "error": None,
        }
    )
    retried = previous.model_copy(
        update={"approved": False, "decision": "reject", "revision_job": revision}
    )
    updated = ApprovalFileV1(
        episode=approvals.episode,
        approvals=[retried if row.cut_id == cut_id else row for row in approvals.approvals],
    )
    pending = ep_dir / "approval.json.tmp"
    pending.write_text(updated.model_dump_json(indent=2) + "\n", encoding="utf-8")
    pending.replace(ep_dir / "approval.json")
    logger.info("packaging revision retry: %s/%s", episode_slug, cut_id)
    return RedirectResponse(
        f"/bridge/packaging/{quote(episode_slug, safe='')}?cut={quote(cut_id, safe='')}",
        status_code=303,
    )


@page_router.post("/{episode_slug}/variant")
async def packaging_select_variant(
    episode_slug: str,
    cut_id: str = Form(..., max_length=_EP_SLUG_MAX),
    selected_variant: str = Form("", max_length=_EP_SLUG_MAX),
    bigtext_request: str = Form("", max_length=_NOTE_MAX),
    nakama_auth: str | None = Cookie(None),
):
    """勾封面變體／打封面大字（修修 2026-08-14）— 只寫 approval.json，不動 packages.json。

    勾選是即時生效的（變體 PNG 桌機端已經 render 完，gate 純挑）；打字則是**請求**
    ——VPS 叫不到桌機的 render（ADR-054 D11），桌機端 thumbnail-brainstorm 讀到
    `bigtext_request` 後才重出一張新變體。UI 上要講清楚這個時間差，不要讓修修
    以為打完字圖就變了。

    不改 approved／primary_package／reject_note：挑臉跟拍板是兩件事，挑到一半
    不該被當成已核准。
    """
    if not check_auth(nakama_auth):
        return RedirectResponse("/login?next=/bridge/packaging", status_code=302)

    ctx = _board_context(episode_slug)
    pkg: PackagesFileV1 = ctx["pkg"]
    cut = next((c for c in pkg.cuts if c.cut_id == cut_id), None)
    if cut is None:
        raise HTTPException(status_code=404, detail=f"cut not found: {cut_id}")

    variant = selected_variant.strip() or None
    if variant is not None:
        known = {v.variant_id for p in cut.packages for v in p.variants}
        if variant not in known:
            raise HTTPException(
                status_code=404,
                detail=f"{cut_id} 沒有 variant {variant!r}（已知：{sorted(known)}）",
            )

    ep_dir = _packaging_root() / episode_slug
    existing = _load_approvals(ep_dir, pkg.episode)
    prev = next((a for a in existing.approvals if a.cut_id == cut_id), None)
    if prev and prev.revision_job and prev.revision_job.status in {"queued", "running"}:
        raise HTTPException(status_code=409, detail="Packaging revision 正在處理，完成後再改變體")
    entry = ApprovalV1(
        cut_id=cut_id,
        approved=prev.approved if prev else False,
        primary_package=prev.primary_package if prev else 1,
        reject_note=prev.reject_note if prev else None,
        decided_at=datetime.now(timezone.utc),
        # 挑變體不是裁決：沒按過 Approve/Reject 就維持 None，board 才不會把
        # 「挑到一半」顯示成 REJECTED（2026-08-14 browser UAT 抓到）。
        decision=prev.decision if prev else None,
        selected_variant=(
            variant if variant is not None else (prev.selected_variant if prev else None)
        ),
        bigtext_request=bigtext_request.strip() or None,
        render_request=prev.render_request if prev else None,
        revision_job=prev.revision_job if prev else None,
    )
    others = [a for a in existing.approvals if a.cut_id != cut_id]
    updated = ApprovalFileV1(episode=pkg.episode, approvals=[*others, entry])
    (ep_dir / "approval.json").write_text(
        updated.model_dump_json(indent=2) + "\n", encoding="utf-8"
    )
    logger.info(
        "packaging variant: %s/%s variant=%s bigtext=%s",
        episode_slug,
        cut_id,
        entry.selected_variant,
        bool(entry.bigtext_request),
    )
    return RedirectResponse(f"/bridge/packaging/{episode_slug}#cut-{cut_id}", status_code=303)


@page_router.post("/{episode_slug}/compose")
async def packaging_compose(
    episode_slug: str,
    cut_id: str = Form(..., max_length=_EP_SLUG_MAX),
    package_rank: int | None = Form(None, ge=1, le=3),
    title_rank: int = Form(..., ge=1, le=5),
    host_cutout: str = Form(..., max_length=_EP_SLUG_MAX * 4),
    guest_cutout: str = Form(..., max_length=_EP_SLUG_MAX * 4),
    composition: Literal["thumbnail_full", "thumbnail_reaction"] = Form(
        "thumbnail_full"
    ),
    big_text_1: str = Form("", max_length=40),
    big_text_2: str = Form("", max_length=40),
    big_text_3: str = Form("", max_length=40),
    highlight_text: str = Form("", max_length=40),
    title_max_width: int = Form(580, ge=300, le=1000),
    guest_credit: str = Form("", max_length=40),
    book_cover: str = Form("", max_length=_EP_SLUG_MAX * 4),
    book_cover_opacity: float = Form(0.42, ge=0, le=1),
    book_cover_brightness: float = Form(0.38, ge=0, le=1),
    book_cover_height_pct: float = Form(100, ge=20, le=150),
    center_visual_asset: str = Form("", max_length=_EP_SLUG_MAX * 4),
    center_width_pct: float | None = Form(None),
    center_height_px: float | None = Form(None),
    center_x_pct: float | None = Form(None),
    center_y_pct: float | None = Form(None),
    geometry_mode: str = Form("auto", max_length=8),
    host_height_pct: float = Form(0.0),
    host_x_pct: float = Form(0.0),
    host_y_pct: float = Form(0.0),
    guest_height_pct: float = Form(0.0),
    guest_x_pct: float = Form(0.0),
    guest_y_pct: float = Form(0.0),
    nakama_auth: str | None = Cookie(None),
):
    """存「封面配方」：哪張臉、什麼大字、哪個詞加橘框（修修 2026-08-14 裁決）。

    **gate 不 render**（D11 不變）——這裡只寫配方，桌機端 thumbnail-brainstorm
    讀 `render_request` 後**出圖一次**。這比預先窮舉變體省：cutout × 大字的組合
    數是乘法，而他真正想要的那一組往往不在預先 render 的清單裡。
    """
    if not check_auth(nakama_auth):
        return RedirectResponse("/login?next=/bridge/packaging", status_code=302)

    ctx = _board_context(episode_slug)
    pkg: PackagesFileV1 = ctx["pkg"]
    cut = next((c for c in pkg.cuts if c.cut_id == cut_id), None)
    if cut is None:
        raise HTTPException(status_code=404, detail=f"cut not found: {cut_id}")
    target_rank = package_rank or title_rank  # legacy forms used title_rank as the package id
    target_package = next((p for p in cut.packages if p.title_rank == target_rank), None)
    if target_package is None:
        raise HTTPException(status_code=404, detail=f"package not found: rank {target_rank}")

    choices = ctx["cutouts"]
    known = {c["vault_path"] for c in choices["host"]} | {c["vault_path"] for c in choices["guest"]}
    for label, val in (("host_cutout", host_cutout), ("guest_cutout", guest_cutout)):
        if val not in known:
            raise HTTPException(status_code=404, detail=f"{label} 不在本集 cutout 清單：{val}")

    ep_dir = _packaging_root() / episode_slug
    if book_cover:
        book_path = get_vault_path() / book_cover
        try:
            book_path.resolve().relative_to(ep_dir.resolve())
        except ValueError as exc:
            raise HTTPException(
                status_code=403, detail="book_cover 必須位於本集 episode 目錄"
            ) from exc
        if book_path.suffix.lower() != ".png" or not book_path.is_file():
            raise HTTPException(status_code=404, detail="本集 book_cover PNG 不存在")

    lines = [ln.strip() for ln in (big_text_1, big_text_2, big_text_3) if ln.strip()]
    if composition == "thumbnail_full" and not lines:
        raise HTTPException(status_code=400, detail="封面大字不可為空")
    if composition == "thumbnail_reaction" and lines:
        raise HTTPException(status_code=400, detail="N2 中央圖卡不使用封面大字")
    hl = highlight_text.strip()
    if hl and hl not in "".join(lines):
        raise HTTPException(
            status_code=400, detail=f"橘框詞「{hl}」不在大字裡，render 出來不會有框"
        )

    # geometry_mode=manual → 用他在預覽上拖出來的位置；auto → 交還給 solver
    # （auto 時保留上一份 geometry 當拖曳介面的起點，但不鎖住 render）
    geometry = None
    manual = geometry_mode == "manual"
    if manual:
        try:
            geometry = GeometryV1(
                host_height_pct=host_height_pct,
                host_x_pct=host_x_pct,
                host_y_pct=host_y_pct,
                guest_height_pct=guest_height_pct,
                guest_x_pct=guest_x_pct,
                guest_y_pct=guest_y_pct,
            )
        except ValidationError as exc:
            raise HTTPException(
                status_code=400, detail=f"位置/大小超出範圍：{str(exc)[:300]}"
            ) from exc

    existing = _load_approvals(ep_dir, pkg.episode)
    prev = next((a for a in existing.approvals if a.cut_id == cut_id), None)
    source_recipe = target_package.render_recipe or _legacy_reaction_recipe(
        ep_dir,
        episode=pkg.episode,
        cut_id=cut_id,
        package=target_package,
    )
    center_geometry = None
    if composition == "thumbnail_reaction":
        expected_center = (
            source_recipe.center_visual_asset
            if source_recipe and source_recipe.composition == "thumbnail_reaction"
            else None
        )
        if not expected_center or center_visual_asset.strip() != expected_center:
            raise HTTPException(
                status_code=409,
                detail="center_visual_asset 必須來自這個 package 自己的 composition receipt",
            )
        values = (center_width_pct, center_height_px, center_x_pct, center_y_pct)
        if any(value is None for value in values):
            center_geometry = source_recipe.center_geometry if source_recipe else None
        else:
            try:
                center_geometry = CenterGeometryV1(
                    width_pct=center_width_pct,
                    height_px=center_height_px,
                    x_pct=center_x_pct,
                    y_pct=center_y_pct,
                )
            except ValidationError as exc:
                raise HTTPException(
                    status_code=400, detail=f"中央圖卡位置/大小超出範圍：{str(exc)[:300]}"
                ) from exc
        if center_geometry is None:
            raise HTTPException(status_code=400, detail="中央圖卡缺少可還原的幾何資料")
    if geometry is None and source_recipe:
        geometry = source_recipe.geometry
    elif geometry is None and prev and prev.render_request:
        # Backward compatibility for pre-recipe approval files.  It is only an
        # initial seed; the board never presents it as another package's state.
        geometry = prev.render_request.geometry

    try:
        req = RenderRequestV1(
            composition=composition,
            title_rank=title_rank,
            host_cutout=host_cutout,
            guest_cutout=guest_cutout,
            big_text=lines,
            highlight_text=hl,
            title_max_width=title_max_width,
            guest_credit=guest_credit.strip(),
            book_cover=book_cover.strip() or None,
            book_cover_opacity=book_cover_opacity,
            book_cover_brightness=book_cover_brightness,
            book_cover_height_pct=book_cover_height_pct,
            center_visual_asset=center_visual_asset.strip() or None,
            center_geometry=center_geometry,
            requested_at=datetime.now(timezone.utc),
            geometry=geometry,
            geometry_manual=manual,
        )
    except ValidationError as exc:
        raise HTTPException(status_code=400, detail=f"配方驗證失敗：{str(exc)[:300]}") from exc

    if prev and prev.revision_job and prev.revision_job.status in {"queued", "running"}:
        raise HTTPException(status_code=409, detail="Packaging revision 正在處理，完成後再存配方")
    entry = ApprovalV1(
        cut_id=cut_id,
        approved=prev.approved if prev else False,
        primary_package=prev.primary_package if prev else 1,
        reject_note=prev.reject_note if prev else None,
        decided_at=datetime.now(timezone.utc),
        decision=prev.decision if prev else None,
        selected_variant=prev.selected_variant if prev else None,
        bigtext_request=prev.bigtext_request if prev else None,
        render_request=req,
        revision_job=prev.revision_job if prev else None,
    )
    others = [a for a in existing.approvals if a.cut_id != cut_id]
    updated = ApprovalFileV1(episode=pkg.episode, approvals=[*others, entry])
    (ep_dir / "approval.json").write_text(
        updated.model_dump_json(indent=2) + "\n", encoding="utf-8"
    )
    # Persist the recipe on the package it actually renders.  approval.render_request
    # remains a temporary dispatch envelope for old desktop watchers, not UI state.
    packages_path = ep_dir / "packages.json"
    packages_data = json.loads(packages_path.read_text(encoding="utf-8"))
    for raw_cut in packages_data["cuts"]:
        if raw_cut["cut_id"] != cut_id:
            continue
        for raw_package in raw_cut["packages"]:
            if raw_package["title_rank"] == target_rank:
                raw_package["render_recipe"] = req.model_dump(mode="json")
                break
        break
    PackagesFileV1.model_validate(packages_data)
    packages_path.write_text(
        json.dumps(packages_data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    logger.info(
        "packaging compose: %s/%s rank=%s host=%s guest=%s text=%s",
        episode_slug,
        cut_id,
        target_rank,
        Path(host_cutout).name,
        Path(guest_cutout).name,
        lines,
    )
    return RedirectResponse(
        f"/bridge/packaging/{episode_slug}?composed={cut_id}&package_rank={target_rank}"
        f"#compose-{cut_id}",
        status_code=303,
    )


@page_router.post("/{episode_slug}/title")
async def packaging_edit_title(
    episode_slug: str,
    cut_id: str = Form(..., max_length=_EP_SLUG_MAX),
    title_text: str = Form(..., max_length=_TITLE_MAX),
    rank: int = Form(1, ge=1, le=5),
    nakama_auth: str | None = Cookie(None),
):
    """標題手改字 — **長短片皆可**（修修 2026-07-30 裁決）。整檔重驗後才落盤。

    ADR-054 D11 的「UI 零 LLM」硬約束原文是「VPS FastAPI 呼叫不到桌機 Cowork」，
    禁的是 LLM **生成**（所以沒有重抽按鈕），不禁人工編輯。舊版把長片擋掉是實作
    時自己加的限制，ADR 無此決定；修修是品味的最終裁決者
    （feedback_hitl_gate_serves_subjective_taste），為一個用字繞回 Cowork 是純
    摩擦（feedback_minimize_manual_friction）。

    首次改字把原文存進 `original_text` 並蓋 `edited_at`——archetype_id /
    angle_combo / cite / payoff 描述的是**原句**，沒有這欄推導鏈就會謊稱手改後
    的文字是 panel 跑出來的。重複改字保留最初的 original_text。
    """
    if not check_auth(nakama_auth):
        return RedirectResponse("/login?next=/bridge/packaging", status_code=302)
    text = title_text.strip()
    if not text:
        raise HTTPException(status_code=400, detail="標題不可為空")

    ctx = _board_context(episode_slug)
    pkg: PackagesFileV1 = ctx["pkg"]
    cut = next((c for c in pkg.cuts if c.cut_id == cut_id), None)
    if cut is None:
        raise HTTPException(status_code=404, detail=f"cut not found: {cut_id}")
    if cut.format == "short":
        rank = 1  # 短片只有一條標題，忽略傳入的 rank

    ep_dir = _packaging_root() / episode_slug
    path = ep_dir / "packages.json"
    data = json.loads(path.read_text(encoding="utf-8"))
    edited = False
    for raw_cut in data["cuts"]:
        if raw_cut["cut_id"] != cut_id:
            continue
        target = next((t for t in raw_cut["titles"] if t.get("rank") == rank), None)
        if target is None:
            raise HTTPException(status_code=404, detail=f"{cut_id} 沒有 rank {rank} 的標題")
        if target["text"] != text:
            # setdefault 不夠：TitleV1 序列化後 original_text 這個 key 是存在的
            # （值 null），setdefault 看到 key 就不寫 → 推導鏈斷掉（2026-08-14
            # browser UAT 抓到：改完字 original_text 還是 null）。
            if not target.get("original_text"):
                target["original_text"] = target["text"]
            target["edited_at"] = datetime.now(timezone.utc).isoformat()
            target["text"] = text
            edited = True
        break

    if edited:
        PackagesFileV1.model_validate(data)  # 驗證失敗即不落盤
        path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        logger.info("packaging title edit: %s/%s rank=%s", episode_slug, cut_id, rank)
    # 帶回 `?edited=<cut_id>`：改完一條後 <details> 會因為重載而收起，要再點一次
    # 才能改下一條（2026-07-30 browser UAT 抓到）。template 靠這個把該支的改字區
    # 保持展開並 scroll 回來。
    return RedirectResponse(
        f"/bridge/packaging/{episode_slug}?edited={cut_id}#title-edit-{cut_id}",
        status_code=303,
    )
