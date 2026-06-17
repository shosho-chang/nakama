"""Agent capability inventory page (`/bridge/inventory`).

Internal (cookie-authed) ops doc: per-agent functions, the `scripts/` each
agent runs, and which capabilities are skill-ified. Content is hand-curated in
`templates/bridge/inventory.html` and committed to git as the source of truth
(same spirit as `/progress`), but served behind auth via the chassis nav —
internal module/script names are not exposed publicly.

Render pattern (auth + Jinja + asset-versioned CSS) mirrors `franky.py`.
"""

from __future__ import annotations

import hashlib
from pathlib import Path

from fastapi import APIRouter, Cookie, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates

from thousand_sunny.auth import check_auth

page_router = APIRouter(prefix="/bridge", tags=["inventory"])

_TEMPLATE_DIR = Path(__file__).resolve().parent.parent / "templates" / "bridge"
_STATIC_DIR = Path(__file__).resolve().parent.parent / "static"
_templates = Jinja2Templates(directory=str(_TEMPLATE_DIR))


def _asset_version() -> str:
    """8-char hash over the CSS/JS this page links, to bust Cloudflare's
    /static/* edge cache on any edit (matches franky.py / progress.py)."""
    h = hashlib.sha1()
    for rel in (
        "shosho/tokens.css",
        "shosho/bridge.css",
        "shosho/theme.js",
        "progress/progress.css",
        "inventory/inventory.css",
    ):
        path = _STATIC_DIR / rel
        if path.exists():
            h.update(path.read_bytes())
    return h.hexdigest()[:8]


_ASSET_VERSION = _asset_version()


@page_router.get("/inventory", response_class=HTMLResponse)
def bridge_inventory_page(request: Request, nakama_auth: str | None = Cookie(None)):
    if not check_auth(nakama_auth):
        return RedirectResponse("/login?next=/bridge/inventory", status_code=302)
    return _templates.TemplateResponse(
        request, "inventory.html", {"asset_version": _ASSET_VERSION}
    )
