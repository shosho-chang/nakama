"""Public crew / system-architecture overview page (`/crew`).

A single-page tour of the whole Nakama + LifeOS setup for an outside reader:
the 8-crew roster (with honest shipped / in-dev / planned status), the
seven-layer content pipeline, the daily cron rhythm, the operating
principles, and the runtime permission policy. Modelled structurally on
cebillhsu.xyz/ai-employee-setup but rendered in the `--sho-*` design system.

Public route (no auth) AND indexable on purpose: it's a marketing-grade
showcase meant to be linked from shosho.tw and shared. Contrast with
`/architecture`, which is the internal noindex URL-planning doc. Same static
render + asset-versioning pattern as `architecture.py` / `progress.py` to
bypass Cloudflare's 4h CDN cache (any CSS edit → new hash → new URL).
"""

import hashlib
from functools import lru_cache
from pathlib import Path

from fastapi import APIRouter
from fastapi.responses import HTMLResponse

router = APIRouter()

_STATIC_DIR = Path(__file__).resolve().parent.parent / "static"
_PAGE_PATH = _STATIC_DIR / "crew" / "index.html"

_VERSIONED_ASSETS = (
    _STATIC_DIR / "crew" / "index.html",
    _STATIC_DIR / "crew" / "crew.css",
    _STATIC_DIR / "shosho" / "tokens.css",
    _STATIC_DIR / "shosho" / "brand" / "logo_1.png",
)


@lru_cache(maxsize=1)
def _rendered_html() -> str:
    h = hashlib.sha1()
    for f in _VERSIONED_ASSETS:
        h.update(f.read_bytes())
    version = h.hexdigest()[:8]
    return _PAGE_PATH.read_text(encoding="utf-8").replace("__ASSET_VERSION__", version)


@router.api_route("/crew", methods=["GET", "HEAD"], include_in_schema=False)
async def crew_page() -> HTMLResponse:
    return HTMLResponse(_rendered_html(), media_type="text/html; charset=utf-8")
