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
import json
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import quote

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
    """子資源（media / thumb / status / POST）用：未登入回 401 JSON。

    ⚠️ 參數名必須叫 `nakama_auth` — FastAPI 的 `Cookie()` 以**參數名**當 cookie 名，
    而 `/login` 發的是 `nakama_auth`（thousand_sunny/auth.py::set_auth_cookie）。
    本 router 原本宣告成 `auth`，等於讀一個沒人會設的 cookie → 登入後照樣 401，
    審核頁在瀏覽器裡從來打不開（2026-08-11 修修實際撞到）。
    """
    if not check_auth(auth_cookie):
        raise HTTPException(status_code=401, detail="unauthorized")


def _login_redirect(request: Request) -> RedirectResponse:
    """**頁面**請求未登入時導去 /login，不要回 401 JSON——瀏覽器看到的是死路。

    與 bridge 其餘頁面（bridge_index 等）同一個慣例。"""
    return RedirectResponse(f"/login?next={quote(request.url.path, safe='/')}", status_code=302)


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


@page_router.get("", response_class=HTMLResponse, response_model=None)
def publish_list(
    request: Request, nakama_auth: str | None = Cookie(None)
) -> HTMLResponse | RedirectResponse:
    if not check_auth(nakama_auth):
        return _login_redirect(request)
    releases = list_releases()
    # 列表主顯示 = 發布標題（修修：cut 編號沒有意義）；未回填時退回工作代號
    for r in releases:
        full = get_release(r["episode"], r["cut_id"])
        yt = next((x for x in full["targets"] if x["platform"] == "youtube"), None)
        r["display_title"] = (yt or {}).get("title") or r["work_title"] or r["cut_id"]
    return _templates.TemplateResponse(
        request,
        "publish_list.html",
        {"releases": releases, "asset_version": _asset_version()},
    )


def _ab_alternates(episode: str, cut_id: str, current_title: str | None) -> list[dict]:
    """packaging 的其餘 package 組（title+縮圖）——Test & Compare 無 API
    （2026-08-04 官方 revision history 重查證，同 ADR-054 §7），A/B 測試是
    修修上傳後在 Studio 手動建。這裡把備用組端出來讓他一鍵複製。"""
    try:
        from agents.usopp.video_description import find_packaging_dir

        pdir = find_packaging_dir(get_vault_path(), episode)
        packages = json.loads((pdir / "packages.json").read_text(encoding="utf-8"))
        cut = next((c for c in packages["cuts"] if c["cut_id"] == cut_id), None)
        if cut is None:
            return []
        titles = {x["rank"]: x["text"] for x in cut.get("titles", [])}
        out = []
        for p in cut.get("packages", []):
            title = titles.get(p.get("title_rank"))
            if not title or title == current_title:
                continue
            out.append({"title": title, "thumbnail": p.get("thumbnail_png")})
        return out
    except (ValueError, OSError, json.JSONDecodeError, KeyError):
        return []  # packaging 交接檔缺漏不擋審核頁——A/B 是加值不是必需


@page_router.get("/{episode}/{cut_id}", response_class=HTMLResponse, response_model=None)
def publish_cut(
    episode: str, cut_id: str, request: Request, nakama_auth: str | None = Cookie(None)
) -> HTMLResponse | RedirectResponse:
    if not check_auth(nakama_auth):
        return _login_redirect(request)
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
            "ab_alternates": _ab_alternates(episode, cut_id, t.get("title"))
            if rel["format"] == "long"
            else [],
            "locked": t["status"] in ("uploading", "uploaded", "published"),
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
    episode: str, cut_id: str, nakama_auth: str | None = Cookie(None)
) -> FileResponse:
    _require_auth(nakama_auth)
    rel, _ = _yt_target(episode, cut_id)
    p = Path(rel["file_path"])
    if not p.exists():
        raise HTTPException(status_code=404, detail="成品檔不存在——重跑 publish_prep")
    return FileResponse(p, media_type="video/mp4")


@page_router.get("/thumb/{episode}/{cut_id}")
def publish_thumb(
    episode: str, cut_id: str, nakama_auth: str | None = Cookie(None)
) -> FileResponse:
    _require_auth(nakama_auth)
    _, t = _yt_target(episode, cut_id)
    if not t.get("thumbnail_path"):
        raise HTTPException(status_code=404, detail="無縮圖")
    p = get_vault_path() / t["thumbnail_path"]
    if not p.exists():
        raise HTTPException(status_code=404, detail=f"縮圖不存在: {p}")
    return FileResponse(p, media_type="image/png")


@page_router.get("/vault-thumb")
def publish_vault_thumb(path: str, nakama_auth: str | None = Cookie(None)) -> FileResponse:
    """A/B 備用縮圖預覽。只放行 vault Attachments/packaging/ 下的 png——
    resolve 後驗前綴，擋 path traversal。"""
    _require_auth(nakama_auth)
    vault = get_vault_path().resolve()
    allowed = (vault / "Attachments" / "packaging").resolve()
    p = (vault / path).resolve()
    if not str(p).startswith(str(allowed)) or p.suffix.lower() != ".png":
        raise HTTPException(status_code=403, detail="僅限 packaging 縮圖")
    if not p.exists():
        raise HTTPException(status_code=404, detail="縮圖不存在")
    return FileResponse(p, media_type="image/png")


@page_router.post("/{episode}/{cut_id}/save")
def publish_save(
    episode: str,
    cut_id: str,
    title: str = Form(...),
    description: str = Form(...),
    publish_at_local: str = Form(""),
    nakama_auth: str | None = Cookie(None),
) -> RedirectResponse:
    _require_auth(nakama_auth)
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
    episode: str, cut_id: str, nakama_auth: str | None = Cookie(None)
) -> RedirectResponse:
    _require_auth(nakama_auth)
    rel, t = _yt_target(episode, cut_id)
    if t["status"] in ("uploading", "uploaded", "published"):
        raise HTTPException(status_code=409, detail="已上傳/上傳中")
    if not t.get("title") or not t.get("description"):
        raise HTTPException(status_code=422, detail="先儲存 Title/Description 才能上傳")
    if not Path(rel["file_path"]).exists():
        raise HTTPException(status_code=422, detail="成品檔不存在——重跑 publish_prep")
    update_target(t["id"], status="approved")
    # 與 CLI 同一條路：subprocess 跑 uploader（狀態轉移由它寫 DB，頁面 poll）。
    # stdout/stderr 落 log 檔——先前 DEVNULL 把 token 缺失的死訊吞掉，修修按了
    # 上傳「後台沒反應」（2026-08-04）
    root = Path(__file__).resolve().parent.parent.parent
    script = root / "scripts" / "publish_upload.py"
    log_dir = root / "data" / "upload_progress"
    log_dir.mkdir(parents=True, exist_ok=True)
    safe = f"{episode}_{cut_id}".replace("/", "_").replace("\\", "_")
    log_f = open(log_dir / f"{safe}.log", "a", encoding="utf-8")  # noqa: SIM115 — 交給子程序持有
    subprocess.Popen(
        [sys.executable, str(script), "--run", "--episode", episode, "--cut", cut_id],
        cwd=str(root),
        stdout=log_f,
        stderr=subprocess.STDOUT,
    )
    logger.info("publish approve+upload: %s/%s（log: %s.log）", episode, cut_id, safe)
    return RedirectResponse(f"/bridge/publish/{episode}/{cut_id}", status_code=303)


@page_router.get("/{episode}/{cut_id}/status")
def publish_status(
    episode: str, cut_id: str, nakama_auth: str | None = Cookie(None)
) -> JSONResponse:
    _require_auth(nakama_auth)
    _, t = _yt_target(episode, cut_id)
    progress = None
    if t["status"] == "uploading":
        root = Path(__file__).resolve().parent.parent.parent
        safe = f"{episode}_{cut_id}".replace("/", "_").replace("\\", "_")
        pf = root / "data" / "upload_progress" / f"{safe}.json"
        if pf.exists():
            try:
                progress = json.loads(pf.read_text(encoding="utf-8"))
            except (json.JSONDecodeError, OSError):
                progress = None
    return JSONResponse(
        {
            "status": t["status"],
            "progress": progress,
            "video_id": t.get("video_id"),
            "url": t.get("url"),
            "error": t.get("error"),
            "publish_at": t.get("publish_at"),
            "checked_at": datetime.now(timezone.utc).isoformat(),
        }
    )
