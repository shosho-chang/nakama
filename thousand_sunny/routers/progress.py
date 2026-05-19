"""Public partner-facing progress page (unauthenticated).

Serves a single static HTML at ``/progress`` summarising Nakama's
Lines × Stages, Agents × Stages, and Channels × Stages readiness for
external partners. No auth, no DB — readiness is hand-curated in the
HTML and committed to git as the single source of truth.

This breaks the all-authenticated pattern intentionally; see the
``/healthz`` precedent (franky.router) for the prior public route.
"""

import hashlib
from functools import lru_cache
from pathlib import Path

from fastapi import APIRouter
from fastapi.responses import HTMLResponse

router = APIRouter()

_STATIC_DIR = Path(__file__).resolve().parent.parent / "static"
_PAGE_PATH = _STATIC_DIR / "progress" / "index.html"

# Assets versioned in the served HTML. Hashing the bytes means any edit to
# these files yields a fresh URL, so Cloudflare's 4h CDN cache invalidates
# on deploy without manual purge. (Cf cf-cache-status: HIT incident
# 2026-05-19: post-#606 CSS sat in CF cache for 40+ min because /static/*
# URLs were unversioned.)
_VERSIONED_ASSETS = (
    _STATIC_DIR / "progress" / "progress.css",
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


@router.api_route("/progress", methods=["GET", "HEAD"], include_in_schema=False)
async def progress_page() -> HTMLResponse:
    # HEAD requests still hit this handler — FastAPI/Starlette drop the body
    # automatically. Cloudflare uptime probes and partner monitoring tools
    # commonly use HEAD, so 405 from a GET-only route would generate noise.
    return HTMLResponse(_rendered_html(), media_type="text/html; charset=utf-8")
