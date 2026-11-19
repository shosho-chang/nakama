"""YouTube 影片封面與上架標題 — `/bridge/youtube-packaging/<episode_slug>`。

主頻道書籍介紹（youtube_book）／主題介紹（youtube_explanation）影片的組封面頁。
與 podcast 的 `/bridge/packaging/<slug>` 分開（版型、素材、標題流程都不同），但共用同一個
vault 目錄 `Attachments/packaging/<slug>/`（Syncthing 同步）：

- `packages.json`／`full/bigtext.json` — 標題與大字候選（book-thumbnail skill 手動發想寫入，唯讀）
- `youtube_cover.json` — 封面配方。本 router 存配方時更新 `requested_at`；出圖由桌機
  `scripts/render_watcher.py` → `scripts/render_youtube_cover.py` 回填（VPS 不出圖，ADR-054 D11）
- `title_tracking.json` — 上架標題紀錄（微調文字、已上頻道＝參加 A/B、影片網址）
- `assets/` — 本集背景（`bg-*`）、主圖（`feature.*`）、來賓去背（`guest-*.png`，選用）；
  修修的去背照來自 vault `Attachments/cutouts/shosho/`

目前沒有上游觸發（修修 2026-11-12：YouTube 製作 pipeline 之後再設計，封面標題先手動叫 agent 做）。

衝突紀律：目錄內有 `*.sync-conflict-*` 一律 409；標題紀錄寫入前比對 `updated_at`，
不一致回 409 讓頁面重新載入，不做 last-write-wins（原型期發生過兩個頁面互相覆蓋）。
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import tempfile
from datetime import datetime, timezone
from pathlib import Path

from fastapi import APIRouter, Cookie, HTTPException
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from pydantic import ValidationError
from starlette.requests import Request

from shared.config import get_vault_path
from shared.log import get_logger
from shared.schemas.youtube_packaging import (
    AB_TITLE_MAX,
    TitleTrackingFileV1,
    YouTubeCoverFileV1,
    YouTubeCoverRecipeV1,
)
from thousand_sunny.auth import check_auth

logger = get_logger("nakama.web.youtube_packaging")

page_router = APIRouter(prefix="/bridge/youtube-packaging", tags=["bridge-youtube-packaging"])
_templates = Jinja2Templates(
    directory=str(Path(__file__).resolve().parent.parent / "templates" / "bridge")
)

YOUTUBE_RECIPES = frozenset({"youtube_book", "youtube_explanation"})
_SLUG = re.compile(r"^[A-Za-z0-9._-]{1,80}$")
_CUTOUT_PREFIX = "Attachments/cutouts/shosho/"
_IMG_EXT = {".png", ".jpg", ".jpeg", ".webp"}
_THUMB_CACHE = Path(tempfile.gettempdir()) / "nakama-youtube-thumbs"


def _asset_version() -> str:
    static = Path(__file__).resolve().parent.parent / "static" / "shosho"
    h = hashlib.sha1()
    for name in (
        "youtube-cover.js",
        "youtube-cover.css",
        "bridge-youtube-packaging.js",
        "bridge-youtube-packaging.css",
    ):
        p = static / name
        if p.is_file():
            h.update(p.read_bytes())
    return h.hexdigest()[:10]


def _root() -> Path:
    return get_vault_path() / "Attachments" / "packaging"


def _ep_dir(slug: str) -> Path:
    if not _SLUG.match(slug):
        raise HTTPException(status_code=404, detail="bad episode slug")
    d = _root() / slug
    if not d.is_dir():
        raise HTTPException(status_code=404, detail=f"packaging episode not found: {slug}")
    conflicts = sorted(p.name for p in d.rglob("*.sync-conflict-*"))
    if conflicts:
        raise HTTPException(
            status_code=409,
            detail=f"Syncthing conflict in {slug}: {', '.join(conflicts)} — 先在 vault 解掉再回來",
        )
    return d


def is_youtube_episode(ep_dir: Path) -> bool:
    """有 youtube_cover.json，或 packages.json 裡有主頻道 YouTube 配方的 cut。"""
    if (ep_dir / "youtube_cover.json").is_file():
        return True
    try:
        pkg = json.loads((ep_dir / "packages.json").read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return False
    return any(c.get("visual_recipe") in YOUTUBE_RECIPES for c in pkg.get("cuts", []))


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _atomic_write(path: Path, data: dict) -> None:
    tmp = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    os.replace(tmp, path)


def _packaging(ep_dir: Path) -> tuple[str, list[dict], list[dict]]:
    """(episode 名稱, 標題, 大字候選)；檔案缺就回空，不擋組封面。"""
    episode, titles, options = ep_dir.name, [], []
    try:
        pkg = json.loads((ep_dir / "packages.json").read_text(encoding="utf-8"))
        episode = pkg.get("episode") or episode
        cut = next(
            (c for c in pkg.get("cuts", []) if c.get("visual_recipe") in YOUTUBE_RECIPES), None
        )
        if cut:
            titles = sorted(
                ({"rank": t["rank"], "text": t["text"]} for t in cut.get("titles", [])),
                key=lambda t: t["rank"],
            )
            bt = ep_dir / cut["cut_id"] / "bigtext.json"
            if bt.is_file():
                seen = set()
                for o in json.loads(bt.read_text(encoding="utf-8")).get("options", []):
                    key = (o.get("lead", ""), o.get("main", ""), o.get("hl", ""))
                    if key not in seen:
                        seen.add(key)
                        options.append({"lead": key[0], "main": key[1], "hl": key[2]})
    except (OSError, json.JSONDecodeError, KeyError):
        pass
    return episode, titles, options


def _load_tracking(ep_dir: Path, titles: list[dict]) -> dict:
    p = ep_dir / "title_tracking.json"
    data = (
        TitleTrackingFileV1.model_validate(json.loads(p.read_text(encoding="utf-8"))).model_dump(
            mode="json"
        )
        if p.is_file()
        else {"video_url": "", "titles": []}
    )
    have = {t.get("source") for t in data["titles"]}
    for t in titles:  # 新一輪發想的標題補進來，不改掉已存在的列
        src = f"packages#{t['rank']}:{t['text']}"
        if src not in have:
            data["titles"].append(
                {
                    "id": f"t{len(data['titles']) + 1}",
                    "text": t["text"],
                    "original_text": t["text"],
                    "source": src,
                    "published": False,
                    "published_at": None,
                    "edited_at": None,
                    "ab": False,
                }
            )
    return data


def _list_images(ep_dir: Path, vault: Path, prefix: str) -> list[str]:
    d = ep_dir / "assets"
    if not d.is_dir():
        return []
    return [
        p.relative_to(vault).as_posix()
        for p in sorted(d.iterdir())
        if p.suffix.lower() in _IMG_EXT and p.name.startswith(prefix)
    ]


def _cutouts(vault: Path) -> list[str]:
    d = vault / _CUTOUT_PREFIX
    return (
        [p.relative_to(vault).as_posix() for p in sorted(d.glob("*/*.png"))] if d.is_dir() else []
    )


def _resolve_asset(vault: Path, slug: str, rel: str) -> Path:
    rel = (rel or "").replace("\\", "/")
    if not (rel.startswith(_CUTOUT_PREFIX) or rel.startswith(f"Attachments/packaging/{slug}/")):
        raise HTTPException(status_code=404, detail="asset not allowed")
    p = (vault / rel).resolve()
    if vault.resolve() not in p.parents or not p.is_file() or p.suffix.lower() not in _IMG_EXT:
        raise HTTPException(status_code=404, detail="asset not found")
    return p


def _status(ep_dir: Path) -> dict:
    p = ep_dir / "youtube_cover.json"
    if not p.is_file():
        return {"has_recipe": False}
    doc = YouTubeCoverFileV1.model_validate(json.loads(p.read_text(encoding="utf-8")))
    png = ep_dir / "cover.png"
    return {
        "has_recipe": True,
        "requested_at": doc.requested_at.isoformat() if doc.requested_at else None,
        "rendered_at": doc.rendered_at.isoformat() if doc.rendered_at else None,
        "render_error": doc.render_error,
        "needs_render": doc.needs_render(),
        "png_version": int(png.stat().st_mtime) if png.is_file() else None,
    }


# ---------------------------------------------------------------------------
# 頁面
# ---------------------------------------------------------------------------


@page_router.get("/{episode_slug}", response_class=HTMLResponse)
def youtube_packaging_page(
    request: Request, episode_slug: str, nakama_auth: str | None = Cookie(None)
):
    if not check_auth(nakama_auth):
        return RedirectResponse(
            f"/login?next=/bridge/youtube-packaging/{episode_slug}", status_code=302
        )
    ep = _ep_dir(episode_slug)
    vault = get_vault_path()
    episode, titles, options = _packaging(ep)
    cover_path = ep / "youtube_cover.json"
    cover = json.loads(cover_path.read_text(encoding="utf-8")) if cover_path.is_file() else None
    boot = {
        "slug": episode_slug,
        "episode": episode,
        "cover": cover["recipe"] if cover else None,
        "status": _status(ep),
        "tracking": _load_tracking(ep, titles),
        "bigtexts": options,
        "lists": {
            "cutouts": _cutouts(vault),
            "backgrounds": _list_images(ep, vault, "bg"),
            "features": _list_images(ep, vault, "feature"),
            "guests": _list_images(ep, vault, "guest"),
        },
        "ab_max": AB_TITLE_MAX,
    }
    return _templates.TemplateResponse(
        request,
        "youtube_packaging.html",
        {
            "slug": episode_slug,
            "episode": episode,
            "boot_json": json.dumps(boot, ensure_ascii=False),
            "asset_version": _asset_version(),
        },
    )


@page_router.get("/{episode_slug}/asset")
def youtube_asset(episode_slug: str, p: str, nakama_auth: str | None = Cookie(None)):
    if not check_auth(nakama_auth):
        raise HTTPException(status_code=401, detail="unauthorized")
    _ep_dir(episode_slug)
    return FileResponse(_resolve_asset(get_vault_path(), episode_slug, p))


@page_router.get("/{episode_slug}/thumb")
def youtube_thumb(episode_slug: str, p: str, nakama_auth: str | None = Cookie(None)):
    """挑選格用小圖（去背原檔 2–3k px）。快取在暫存資料夾，key 含 mtime。"""
    if not check_auth(nakama_auth):
        raise HTTPException(status_code=401, detail="unauthorized")
    _ep_dir(episode_slug)
    src = _resolve_asset(get_vault_path(), episode_slug, p)
    _THUMB_CACHE.mkdir(parents=True, exist_ok=True)
    key = hashlib.sha1(f"{src}|{src.stat().st_mtime}".encode()).hexdigest() + ".png"
    out = _THUMB_CACHE / key
    if not out.is_file():
        from PIL import Image

        im = Image.open(src)
        im.thumbnail((320, 320))
        im.save(out)
    return FileResponse(out, media_type="image/png")


@page_router.get("/{episode_slug}/cover.png")
def youtube_cover_png(episode_slug: str, small: int = 0, nakama_auth: str | None = Cookie(None)):
    if not check_auth(nakama_auth):
        raise HTTPException(status_code=401, detail="unauthorized")
    p = _ep_dir(episode_slug) / ("cover-320.png" if small else "cover.png")
    if not p.is_file():
        raise HTTPException(status_code=404, detail="還沒出過圖")
    return FileResponse(p, media_type="image/png", headers={"Cache-Control": "no-store"})


@page_router.get("/{episode_slug}/status")
def youtube_cover_status(episode_slug: str, nakama_auth: str | None = Cookie(None)):
    if not check_auth(nakama_auth):
        raise HTTPException(status_code=401, detail="unauthorized")
    return JSONResponse(_status(_ep_dir(episode_slug)))


@page_router.post("/{episode_slug}/cover")
async def youtube_save_cover(
    request: Request, episode_slug: str, nakama_auth: str | None = Cookie(None)
):
    """存配方 → requested_at 更新 → 桌機 watcher 出圖。"""
    if not check_auth(nakama_auth):
        raise HTTPException(status_code=401, detail="unauthorized")
    ep = _ep_dir(episode_slug)
    body = await request.json()
    try:
        recipe = YouTubeCoverRecipeV1.model_validate(body.get("recipe") or {})
    except ValidationError as exc:
        return JSONResponse({"error": f"配方格式不對：{exc.errors()[0]['msg']}"}, status_code=422)
    vault = get_vault_path()
    guest_src = recipe.guest.src if recipe.guest else ""
    for rel in (recipe.bg.src, recipe.feature.src, recipe.person.src, guest_src):
        if rel:
            _resolve_asset(vault, episode_slug, rel)  # 素材必須存在且屬於本集或去背照庫
    path = ep / "youtube_cover.json"
    prev = json.loads(path.read_text(encoding="utf-8")) if path.is_file() else {}
    episode, _, _ = _packaging(ep)
    doc = YouTubeCoverFileV1.model_validate(
        {
            **{k: prev.get(k) for k in ("rendered_at", "rendered_png", "render_error")},
            "episode": prev.get("episode") or episode,
            "recipe": recipe.model_dump(mode="json"),
            "requested_at": _now().isoformat(),
        }
    )
    _atomic_write(path, doc.model_dump(mode="json", by_alias=True))
    logger.info("youtube cover saved: %s", episode_slug)
    return JSONResponse(_status(ep))


@page_router.post("/{episode_slug}/titles")
async def youtube_save_titles(
    request: Request, episode_slug: str, nakama_auth: str | None = Cookie(None)
):
    """上架標題紀錄。`base_updated_at` 與檔案不一致 → 409（別的頁面先存過）。"""
    if not check_auth(nakama_auth):
        raise HTTPException(status_code=401, detail="unauthorized")
    ep = _ep_dir(episode_slug)
    body = await request.json()
    path = ep / "title_tracking.json"
    _, titles, _ = _packaging(ep)
    prev = _load_tracking(ep, titles)
    if (prev.get("updated_at") or None) != (body.get("base_updated_at") or None):
        return JSONResponse(
            {"error": "這份標題紀錄剛被別的頁面更新過，重新載入後再改", "current": prev},
            status_code=409,
        )
    data = {k: v for k, v in body.items() if k != "base_updated_at"}
    now = _now().isoformat()
    if not (data.get("video_url") or "").strip() and prev.get("video_url"):
        data["video_url"] = prev["video_url"]  # 舊頁面送空網址不蓋掉已記錄的
    old = {t["id"]: t for t in prev.get("titles", [])}
    for t in data.get("titles", []):
        t["text"] = (t.get("text") or "").strip()
        o = old.get(t.get("id"), {})
        if t["text"] != o.get("text"):
            t["edited_at"] = now
        if t.get("published") and not t.get("published_at"):
            t["published_at"] = now
        if not t.get("published"):
            t["published_at"] = None
    data["updated_at"] = now
    try:
        doc = TitleTrackingFileV1.model_validate(data)
    except ValidationError as exc:
        return JSONResponse({"error": exc.errors()[0]["msg"]}, status_code=422)
    _atomic_write(path, doc.model_dump(mode="json"))
    return JSONResponse(doc.model_dump(mode="json"))
