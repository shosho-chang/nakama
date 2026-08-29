"""Focus-music picker for the pomodoro dock (修修 2026-08-25).

按下 ▶ 開始計時 → 前端打 ``/bridge/focus-music/random`` 抽一首、邊倒數邊播，
曲名顯示在計時 bar；一首播完前端再抽下一首（25 分鐘 > 單曲長度）。

來源資料夾由 ``FOCUS_MUSIC_DIR`` env 指定（桌機 ``E:\\data\\focus music``；VPS
放 ``/home/nakama/data/focus_music``）。env 未設 / 資料夾不存在 / 沒有音檔 →
``{"available": false}``，計時器照常運作、只是無配樂 — 音樂永遠是 best-effort。

檔案路由只供應 random 抽得到的同一批檔案（resolve + parent check 擋 traversal），
與其他 bridge 頁面同一套 cookie auth。
"""

from __future__ import annotations

import os
import re
import secrets
from pathlib import Path
from urllib.parse import quote

from fastapi import APIRouter, Cookie, HTTPException
from fastapi.responses import FileResponse

from shared.log import get_logger
from thousand_sunny.auth import check_auth

logger = get_logger("nakama.web.focus_music")

page_router = APIRouter(prefix="/bridge/focus-music", tags=["bridge-focus-music"])

_AUDIO_EXTS = {".mp3", ".m4a", ".aac", ".ogg", ".opus", ".flac", ".wav"}
_MEDIA_TYPES = {
    ".mp3": "audio/mpeg",
    ".m4a": "audio/mp4",
    ".aac": "audio/aac",
    ".ogg": "audio/ogg",
    ".opus": "audio/ogg",
    ".flac": "audio/flac",
    ".wav": "audio/wav",
}

# Envato-style download names carry a long provenance tail, e.g.
# ``a-blanket-of-stars-all-ambient-main-version-44352-05-27.mp3`` — cut at the
# marketing suffix and de-kebab so the bar shows「a blanket of stars all ambient」.
_TITLE_NOISE = re.compile(r"-(main|full)-version.*$", re.IGNORECASE)
_TRAIL_IDS = re.compile(r"[-\s]*\(?\d[\d()-]*\)?\s*$")


def _music_dir() -> Path | None:
    raw = os.environ.get("FOCUS_MUSIC_DIR", "").strip()
    if not raw:
        return None
    p = Path(raw)
    return p if p.is_dir() else None


def _kind_dir(root: Path, kind: str) -> Path | None:
    """Resolve the category pool (修修 2026-08-25: focus / uplifting 子資料夾).

    A subdirectory whose name starts with ``kind``（如 ``focus music`` /
    ``uplifting music``）wins; a flat library (no such subdir) keeps serving the
    root for ``focus``, and has no ``uplifting`` pool (the JS falls back to focus).
    """
    if kind not in {"focus", "uplifting"}:
        return None
    match = sorted(d for d in root.iterdir() if d.is_dir() and d.name.lower().startswith(kind))
    if match:
        return match[0]
    return root if kind == "focus" else None


def _tracks(root: Path) -> list[Path]:
    return sorted(f for f in root.iterdir() if f.is_file() and f.suffix.lower() in _AUDIO_EXTS)


def _display_title(stem: str) -> str:
    t = _TITLE_NOISE.sub("", stem)
    t = _TRAIL_IDS.sub("", t)
    t = t.replace("-", " ").replace("_", " ").strip()
    return t or stem


@page_router.get("/random")
async def focus_music_random(kind: str = "focus", nakama_auth: str | None = Cookie(None)):
    """Pick one random track from the ``kind`` pool (focus / uplifting). Never
    errors on a missing/empty library — the timer must start regardless, so
    absence degrades to ``available: false``."""
    if not check_auth(nakama_auth):
        raise HTTPException(status_code=403, detail="Unauthorized")
    root = _music_dir()
    pool = _kind_dir(root, kind) if root is not None else None
    tracks = _tracks(pool) if pool is not None else []
    if not tracks:
        return {"available": False}
    pick = secrets.choice(tracks)
    return {
        "available": True,
        "title": _display_title(pick.stem),
        "url": f"/bridge/focus-music/file/{quote(pick.name)}?kind={quote(kind)}",
    }


@page_router.get("/file/{name}")
async def focus_music_file(name: str, kind: str = "focus", nakama_auth: str | None = Cookie(None)):
    if not check_auth(nakama_auth):
        raise HTTPException(status_code=403, detail="Unauthorized")
    root = _music_dir()
    pool = _kind_dir(root, kind) if root is not None else None
    if pool is None:
        raise HTTPException(status_code=404, detail="music library not configured")
    target = (pool / name).resolve()
    # traversal guard: the resolved file must sit DIRECTLY inside the pool dir
    if target.parent != pool.resolve() or not target.is_file():
        raise HTTPException(status_code=404, detail="unknown track")
    if target.suffix.lower() not in _AUDIO_EXTS:
        raise HTTPException(status_code=404, detail="unknown track")
    media = _MEDIA_TYPES.get(target.suffix.lower(), "application/octet-stream")
    return FileResponse(target, media_type=media)
