"""Publish review — `/bridge/publish`（ADR-055 slice 4，發布審核頁）。

修修對單支影片做**最後確認**的地方：影片 preview、Title（顯示已決定 + 改寫）、
Thumbnail 預覽、Description 編輯、排程時間 → 「核准並上傳」。

資料面 SoT = state.db `releases` / `release_targets`（ADR-055 D3；本 router
經 shared.release_store 讀寫，不碰 SQL）。上傳執行 = subprocess 跑
`scripts/publish_upload.py --run`（與 CLI 同一條路，頁面只是駕駛艙）——
頁面 poll `/status` 看狀態轉移（uploading → uploaded / failed）。

**桌機 only**：影片檔與 uploader 都在桌機（Q2），本頁跑本機 dev server；
VPS 控制面版等 HTTP claim/report 契約（v2）。
"""

from __future__ import annotations

import hashlib
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

from fastapi import APIRouter, Cookie, Form, HTTPException
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from starlette.requests import Request

from shared.config import get_vault_path
from shared.log import get_logger
from shared.release_store import get_release, list_releases, update_target
from thousand_sunny.auth import check_auth

logger = get_logger("nakama.web.publish_review")

page_router = APIRouter(prefix="/bridge/publish", tags=["bridge-publish"])
_templates = Jinja2Templates(
    directory=str(Path(__file__).resolve().parent.parent / "templates" / "bridge")
)

_TITLE_MAX = 100  # YT 標題硬上限
_DESC_MAX = 5000  # YT 描述硬上限


def _asset_version() -> str:
    static_dir = Path(__file__).resolve().parent.parent / "static" / "shosho"
    h = hashlib.sha1()
    for name in ("tokens.css", "bridge.css", "bridge-pages.css"):
        p = static_dir / name
        if p.is_file():
            h.update(p.read_bytes())
    return h.hexdigest()[:10]


def _require_auth(auth_cookie: str | None) -> None:
    if not check_auth(auth_cookie):
        raise HTTPException(status_code=401, detail="unauthorized")


def _yt_target(episode: str, cut_id: str) -> tuple[dict, dict]:
    rel = get_release(episode, cut_id)
    if rel is None:
        raise HTTPException(status_code=404, detail=f"{cut_id} 未登錄——先跑 publish_prep")
    t = next((x for x in rel["targets"] if x["platform"] == "youtube"), None)
    if t is None:
        raise HTTPException(status_code=404, detail="youtube target 不存在")
    return rel, t


def taipei_to_iso(dt_local: str) -> str:
    """datetime-local 輸入（無時區）→ +08:00 ISO。頁面明示台北時間，這裡補上
    時區——不是猜，是 UI 契約。"""
    dt = datetime.fromisoformat(dt_local)
    if dt.tzinfo is not None:
        return dt.isoformat()
    return dt.isoformat() + "+08:00"


@page_router.get("", response_class=HTMLResponse)
def publish_list(request: Request, auth: str | None = Cookie(default=None)) -> HTMLResponse:
    _require_auth(auth)
    releases = list_releases()
    return _templates.TemplateResponse(
        request,
        "publish_list.html",
        {"releases": releases, "asset_version": _asset_version()},
    )


@page_router.get("/{episode}/{cut_id}", response_class=HTMLResponse)
def publish_cut(
    episode: str, cut_id: str, request: Request, auth: str | None = Cookie(default=None)
) -> HTMLResponse:
    _require_auth(auth)
    rel, t = _yt_target(episode, cut_id)
    thumb_exists = (
        bool(t.get("thumbnail_path")) and (get_vault_path() / t["thumbnail_path"]).exists()
    )
    # 排程欄 prefill：DB 的 +08:00 ISO → datetime-local 格式
    publish_local = ""
    if t.get("publish_at"):
        publish_local = t["publish_at"][:16]
    return _templates.TemplateResponse(
        request,
        "publish_cut.html",
        {
            "rel": rel,
            "t": t,
            "thumb_exists": thumb_exists,
            "publish_local": publish_local,
            "file_exists": Path(rel["file_path"]).exists(),
            "duration_min": round(rel["duration_sec"] / 60, 1),
            "size_mb": round(rel["file_bytes"] / 1e6),
            "asset_version": _asset_version(),
        },
    )


@page_router.get("/media/{episode}/{cut_id}")
def publish_media(
    episode: str, cut_id: str, auth: str | None = Cookie(default=None)
) -> FileResponse:
    _require_auth(auth)
    rel, _ = _yt_target(episode, cut_id)
    p = Path(rel["file_path"])
    if not p.exists():
        raise HTTPException(status_code=404, detail="成品檔不存在——重跑 publish_prep")
    return FileResponse(p, media_type="video/mp4")


@page_router.get("/thumb/{episode}/{cut_id}")
def publish_thumb(
    episode: str, cut_id: str, auth: str | None = Cookie(default=None)
) -> FileResponse:
    _require_auth(auth)
    _, t = _yt_target(episode, cut_id)
    if not t.get("thumbnail_path"):
        raise HTTPException(status_code=404, detail="無縮圖")
    p = get_vault_path() / t["thumbnail_path"]
    if not p.exists():
        raise HTTPException(status_code=404, detail=f"縮圖不存在: {p}")
    return FileResponse(p, media_type="image/png")


@page_router.post("/{episode}/{cut_id}/save")
def publish_save(
    episode: str,
    cut_id: str,
    title: str = Form(...),
    description: str = Form(...),
    publish_at_local: str = Form(""),
    auth: str | None = Cookie(default=None),
) -> RedirectResponse:
    _require_auth(auth)
    _, t = _yt_target(episode, cut_id)
    if t["status"] in ("uploading", "uploaded", "published"):
        raise HTTPException(status_code=409, detail="已上傳/上傳中——文案鎖定")
    title = title.strip()
    if not title or len(title) > _TITLE_MAX:
        raise HTTPException(status_code=422, detail=f"標題必填且 ≤{_TITLE_MAX} 字")
    if len(description) > _DESC_MAX:
        raise HTTPException(status_code=422, detail=f"描述 ≤{_DESC_MAX} 字")
    if "{{TODO_" in description:
        raise HTTPException(status_code=422, detail="描述還有 {{TODO_*}} 佔位符——先填掉")
    fields: dict = {"title": title, "description": description}
    if publish_at_local:
        fields["publish_at"] = taipei_to_iso(publish_at_local)
    else:
        fields["publish_at"] = None
    update_target(t["id"], **fields)
    logger.info("publish save: %s/%s", episode, cut_id)
    return RedirectResponse(f"/bridge/publish/{episode}/{cut_id}", status_code=303)


@page_router.post("/{episode}/{cut_id}/approve-upload")
def publish_approve_upload(
    episode: str, cut_id: str, auth: str | None = Cookie(default=None)
) -> RedirectResponse:
    _require_auth(auth)
    rel, t = _yt_target(episode, cut_id)
    if t["status"] in ("uploading", "uploaded", "published"):
        raise HTTPException(status_code=409, detail="已上傳/上傳中")
    if not t.get("title") or not t.get("description"):
        raise HTTPException(status_code=422, detail="先儲存 Title/Description 才能上傳")
    if not Path(rel["file_path"]).exists():
        raise HTTPException(status_code=422, detail="成品檔不存在——重跑 publish_prep")
    update_target(t["id"], status="approved")
    # 與 CLI 同一條路：subprocess 跑 uploader（狀態轉移由它寫 DB，頁面 poll）
    script = Path(__file__).resolve().parent.parent.parent / "scripts" / "publish_upload.py"
    subprocess.Popen(
        [sys.executable, str(script), "--run", "--episode", episode, "--cut", cut_id],
        cwd=str(script.parent.parent),
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    logger.info("publish approve+upload: %s/%s", episode, cut_id)
    return RedirectResponse(f"/bridge/publish/{episode}/{cut_id}", status_code=303)


@page_router.get("/{episode}/{cut_id}/status")
def publish_status(
    episode: str, cut_id: str, auth: str | None = Cookie(default=None)
) -> JSONResponse:
    _require_auth(auth)
    _, t = _yt_target(episode, cut_id)
    return JSONResponse(
        {
            "status": t["status"],
            "video_id": t.get("video_id"),
            "url": t.get("url"),
            "error": t.get("error"),
            "publish_at": t.get("publish_at"),
            "checked_at": datetime.now(timezone.utc).isoformat(),
        }
    )
