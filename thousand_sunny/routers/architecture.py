"""Internal architecture planning page (`/architecture`).

Lists every Nakama web UI surface (existing + planned) with current vs
proposed paths so 修修 can make routing decisions in one view. Same
visual treatment as `/progress` — Shosho CI tokens, theme switch,
asset-versioned URLs to bypass Cloudflare's 4h CDN cache.

Public route (no auth) on purpose: it's a static doc, no sensitive data,
and keeping it open means the link survives session expiry. If this
sprouts into something with internal references that shouldn't leak,
gate it behind the HMAC cookie like the rest of /bridge.
"""

import hashlib
from functools import lru_cache
from pathlib import Path

from fastapi import APIRouter
from fastapi.responses import HTMLResponse

router = APIRouter()

_STATIC_DIR = Path(__file__).resolve().parent.parent / "static"
_PAGE_PATH = _STATIC_DIR / "architecture" / "index.html"

_VERSIONED_ASSETS = (
    _STATIC_DIR / "architecture" / "architecture.css",
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


@router.api_route("/architecture", methods=["GET", "HEAD"], include_in_schema=False)
async def architecture_page() -> HTMLResponse:
    return HTMLResponse(_rendered_html(), media_type="text/html; charset=utf-8")
