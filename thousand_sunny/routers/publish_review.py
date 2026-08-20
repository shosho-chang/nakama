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
import os
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Sequence
from urllib.parse import quote, unquote, urlsplit

from fastapi import APIRouter, Cookie, Form, HTTPException, Response
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from starlette.requests import Request

from shared.config import get_vault_path
from shared.log import get_logger
from shared.publish_calendar import short_execution_readiness
from shared.release_store import get_release, list_releases, update_target
from shared.tight_srt import latest_tight_srt, srt_to_vtt
from thousand_sunny.auth import check_auth

logger = get_logger("nakama.web.publish_review")

page_router = APIRouter(prefix="/bridge/publish", tags=["bridge-publish"])
_templates = Jinja2Templates(
    directory=str(Path(__file__).resolve().parent.parent / "templates" / "bridge")
)

_TITLE_MAX = 100  # YT 標題硬上限
_DESC_MAX = 5000  # YT 描述硬上限
_SHORT_PLATFORMS = ("youtube", "instagram_reels", "facebook_reels")
_PLATFORM_LABELS = {
    "youtube": "YouTube Shorts",
    "instagram_reels": "Instagram Reels",
    "facebook_reels": "Facebook Page Reels",
}
_META_SETTINGS = (
    "META_GRAPH_API_VERSION",
    "META_PAGE_ID",
    "META_IG_USER_ID",
    "META_PAGE_ACCESS_TOKEN",
)
_META_STAGING_SETTINGS = (
    "META_MEDIA_R2_ACCOUNT_ID",
    "META_MEDIA_R2_ACCESS_KEY_ID",
    "META_MEDIA_R2_SECRET_ACCESS_KEY",
    "META_MEDIA_R2_BUCKET",
)


def _upload_progress_dir() -> Path:
    root = Path(__file__).resolve().parent.parent.parent
    data_dir = Path(os.environ.get("NAKAMA_DATA_DIR", "") or root / "data")
    return data_dir / "upload_progress"


def _upload_progress_file(episode: str, cut_id: str) -> Path:
    safe = f"{episode}_{cut_id}".replace("/", "_").replace("\\", "_")
    return _upload_progress_dir() / f"{safe}.json"


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


def _mutation_return_url(return_to: str, *, episode: str, cut_id: str) -> str:
    """Allow only an exact relative Calendar path; otherwise return to review."""

    fallback = f"/bridge/publish/{quote(episode, safe='')}/{quote(cut_id, safe='')}"
    decoded = unquote(return_to)
    if (
        not return_to
        or "\\" in decoded
        or any(ord(character) < 32 or ord(character) == 127 for character in decoded)
    ):
        return fallback
    parsed = urlsplit(decoded)
    if (
        parsed.scheme
        or parsed.netloc
        or parsed.fragment
        or parsed.path != "/bridge/publish/calendar"
    ):
        return fallback
    return return_to


def _yt_target(episode: str, cut_id: str) -> tuple[dict, dict]:
    rel = get_release(episode, cut_id)
    if rel is None:
        raise HTTPException(status_code=404, detail=f"{cut_id} 未登錄——先跑 publish_prep")
    t = next((x for x in rel["targets"] if x["platform"] == "youtube"), None)
    if t is None:
        raise HTTPException(status_code=404, detail="youtube target 不存在")
    return rel, t


def _episode_dir(rel: dict) -> Path:
    """成品在 `<episode>/highlights/exports/<cut>.mp4`——往上三層＝episode 目錄
    （與 publish_upload 推導 CC 來源的方式相同，不硬編磁碟位置）。"""
    return Path(rel["file_path"]).parents[2]


def _platform_targets(rel: dict) -> list[dict]:
    """Stable Bridge projection; missing Short targets remain virtual until approval."""
    by_platform = {target["platform"]: target for target in rel["targets"]}
    platforms = _SHORT_PLATFORMS if rel["format"] == "short" else ("youtube",)
    rows: list[dict] = []
    for platform in platforms:
        target = dict(by_platform.get(platform) or {})
        target.setdefault("platform", platform)
        target.setdefault("status", "draft")
        target["label"] = _PLATFORM_LABELS.get(platform, platform)
        target["retryable"] = target["status"] == "failed"
        required = ()
        if platform == "instagram_reels":
            required = (*_META_SETTINGS, *_META_STAGING_SETTINGS)
        elif platform == "facebook_reels":
            required = _META_SETTINGS
        missing = [name for name in required if not os.environ.get(name, "").strip()]
        target["connection_state"] = "not_executable" if missing else "ready"
        target["connection_note"] = (
            "NOT EXECUTABLE · missing " + ", ".join(missing)
            if missing
            else (
                "Desktop OAuth worker validates the YouTube connection at execution time."
                if platform == "youtube"
                else "Connection settings present; supervised live probe is still required."
            )
        )
        if platform == "facebook_reels" and rel["duration_sec"] > 60:
            target["status"] = "ineligible"
            target["error"] = "Facebook Page Reels 此流程限制最長 60 秒；不自動裁切或重壓。"
            target["retryable"] = False
        rows.append(target)
    return rows


def _spawn_publish_worker(
    episode: str,
    cut_id: str,
    *,
    platform: str | None = None,
    platforms: Sequence[str] | None = None,
) -> None:
    """Start the desktop dispatcher and keep its output in the existing progress log."""
    if platform and platforms:
        raise ValueError("choose platform or platforms, not both")
    selected = tuple(platforms or ((platform,) if platform else ()))
    root = Path(__file__).resolve().parent.parent.parent
    script = root / "scripts" / "publish_dispatch.py"
    log_dir = _upload_progress_dir()
    log_dir.mkdir(parents=True, exist_ok=True)
    safe = f"{episode}_{cut_id}".replace("/", "_").replace("\\", "_")
    suffix = "_" + "_".join(selected) if selected else ""
    log_f = open(  # noqa: SIM115 — child process owns this handle
        log_dir / f"{safe}{suffix}.log", "a", encoding="utf-8"
    )
    command = [
        sys.executable,
        str(script),
        "--release",
        "--episode",
        episode,
        "--cut",
        cut_id,
        "--execute",
    ]
    for selected_platform in selected:
        command.extend(("--platform", selected_platform))
    subprocess.Popen(
        command,
        cwd=str(root),
        stdout=log_f,
        stderr=subprocess.STDOUT,
    )


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _has_future_campaign_anchor(value: object, *, now: datetime | None = None) -> bool:
    if value in {None, ""}:
        return False
    try:
        anchor = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError("Campaign Anchor 必須是有效且含時區的時間") from exc
    if anchor.tzinfo is None or anchor.utcoffset() is None:
        raise ValueError("Campaign Anchor 必須包含時區")
    current = now or _utc_now()
    if current.tzinfo is None or current.utcoffset() is None:
        raise ValueError("approval clock 必須包含時區")
    return anchor.astimezone(timezone.utc) > current.astimezone(timezone.utc)


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
    cc_policy = "sidecar_required" if rel["format"] == "long" else "burned_only"
    # Short 成品的字幕已燒入畫面；不要掃 tight SRT，否則 UI 會誤導為另上 CC。
    subs = latest_tight_srt(_episode_dir(rel), cut_id) if cc_policy == "sidecar_required" else None
    return _templates.TemplateResponse(
        request,
        "publish_cut.html",
        {
            "rel": rel,
            "t": t,
            "subs_name": subs.name if subs else None,
            "cc_policy": cc_policy,
            "platform_targets": _platform_targets(rel),
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


@page_router.get("/subs/{episode}/{cut_id}")
def publish_subs(episode: str, cut_id: str, nakama_auth: str | None = Cookie(None)) -> Response:
    """審核頁影片的字幕軌（`<track>`）——**就是實際會上架的那份 CC**。

    長片成品不燒字幕（ADR-055 Q4b），所以審核時看不到字幕；修修 2026-08-12：
    「可以預設顯示由 SRT 抓來的字幕檔嗎？不需要直接燒到影片裡面去。」
    轉成 WebVTT 是因為瀏覽器的 `<track>` 不吃 SRT。
    """
    _require_auth(nakama_auth)
    rel, _ = _yt_target(episode, cut_id)
    if rel["format"] != "long":
        raise HTTPException(status_code=404, detail="Short 字幕已燒入畫面，不提供 sidecar CC")
    srt = latest_tight_srt(_episode_dir(rel), cut_id)
    if srt is None:
        raise HTTPException(status_code=404, detail=f"{cut_id} 沒有 tight SRT")
    return Response(
        srt_to_vtt(srt.read_text(encoding="utf-8")),
        media_type="text/vtt; charset=utf-8",
    )


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
    episode: str,
    cut_id: str,
    return_to: str = Form(""),
    nakama_auth: str | None = Cookie(None),
) -> RedirectResponse:
    _require_auth(nakama_auth)
    return_url = _mutation_return_url(return_to, episode=episode, cut_id=cut_id)
    rel, t = _yt_target(episode, cut_id)
    if t["status"] in ("uploading", "uploaded", "published"):
        raise HTTPException(status_code=409, detail="已上傳/上傳中")
    if not t.get("title") or not t.get("description"):
        raise HTTPException(status_code=422, detail="先儲存 Title/Description 才能上傳")
    if not Path(rel["file_path"]).exists():
        raise HTTPException(status_code=422, detail="成品檔不存在——重跑 publish_prep")
    if rel["format"] == "short":
        readiness = short_execution_readiness(rel)
        if not readiness.ready:
            raise HTTPException(status_code=422, detail=readiness.reason)
        from agents.usopp.social_publish import approve_short_targets

        try:
            future_anchor = _has_future_campaign_anchor(t.get("publish_at"))
        except ValueError as error:
            raise HTTPException(status_code=422, detail=str(error)) from error
        targets = approve_short_targets(rel, t)
        if future_anchor:
            native_platforms = tuple(
                target["platform"]
                for target in targets
                if target["platform"] in {"youtube", "facebook_reels"}
                and target["status"] == "approved"
            )
            _spawn_publish_worker(episode, cut_id, platforms=native_platforms)
        else:
            _spawn_publish_worker(episode, cut_id)
        logger.info("publish approve+dispatch: %s/%s（Short multi-target）", episode, cut_id)
        return RedirectResponse(return_url, status_code=303)

    update_target(t["id"], status="approved")
    # 與 CLI 同一條路：subprocess 跑 uploader（狀態轉移由它寫 DB，頁面 poll）。
    # stdout/stderr 落 log 檔——先前 DEVNULL 把 token 缺失的死訊吞掉，修修按了
    # 上傳「後台沒反應」（2026-08-04）
    root = Path(__file__).resolve().parent.parent.parent
    script = root / "scripts" / "publish_upload.py"
    log_dir = _upload_progress_dir()
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
    return RedirectResponse(return_url, status_code=303)


@page_router.post("/{episode}/{cut_id}/retry/{platform}")
def publish_retry_target(
    episode: str,
    cut_id: str,
    platform: str,
    return_to: str = Form(""),
    nakama_auth: str | None = Cookie(None),
) -> RedirectResponse:
    """Retry exactly one failed target; successful siblings are never reopened."""
    _require_auth(nakama_auth)
    rel, _ = _yt_target(episode, cut_id)
    if rel["format"] != "short" or platform not in _SHORT_PLATFORMS:
        raise HTTPException(status_code=404, detail="publish target 不存在")
    target = next((item for item in rel["targets"] if item["platform"] == platform), None)
    if target is None:
        raise HTTPException(status_code=404, detail="publish target 尚未建立")
    if target["status"] != "failed":
        raise HTTPException(status_code=409, detail="只有 failed target 可以單獨重試")
    update_target(target["id"], status="approved", error=None)
    _spawn_publish_worker(episode, cut_id, platform=platform)
    logger.info("publish target retry: %s/%s/%s", episode, cut_id, platform)
    return RedirectResponse(
        _mutation_return_url(return_to, episode=episode, cut_id=cut_id), status_code=303
    )


@page_router.get("/{episode}/{cut_id}/status")
def publish_status(
    episode: str, cut_id: str, nakama_auth: str | None = Cookie(None)
) -> JSONResponse:
    _require_auth(nakama_auth)
    rel, t = _yt_target(episode, cut_id)
    progress = None
    if t["status"] in ("uploading", "uploaded", "published", "failed"):
        pf = _upload_progress_file(episode, cut_id)
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
            "targets": _platform_targets(rel),
            "checked_at": datetime.now(timezone.utc).isoformat(),
        }
    )
