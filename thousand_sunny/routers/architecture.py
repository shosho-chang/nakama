"""Internal architecture planning page (`/architecture`).

Lists every Nakama web UI surface (existing + planned) with current vs
proposed paths so 修修 can make routing decisions in one view.

Formerly a public (noindex) static page; now cookie-authed and rendered
through the shared chassis nav so it lives inside the Bridge alongside
/progress and /bridge/inventory. Render pattern (auth + Jinja +
asset-versioned CSS) mirrors ``franky.py`` / ``progress.py``.
"""

import hashlib
from pathlib import Path

from fastapi import APIRouter, Cookie, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates

from thousand_sunny.auth import check_auth

router = APIRouter()

_TEMPLATE_DIR = Path(__file__).resolve().parent.parent / "templates" / "bridge"
_STATIC_DIR = Path(__file__).resolve().parent.parent / "static"
_templates = Jinja2Templates(directory=str(_TEMPLATE_DIR))


def _asset_version() -> str:
    h = hashlib.sha1()
    for rel in (
        "shosho/tokens.css",
        "shosho/bridge.css",
        "shosho/theme.js",
        "architecture/architecture.css",
    ):
        path = _STATIC_DIR / rel
        if path.exists():
            h.update(path.read_bytes())
    return h.hexdigest()[:8]


_ASSET_VERSION = _asset_version()


@router.api_route(
    "/architecture", methods=["GET", "HEAD"], response_class=HTMLResponse, include_in_schema=False
)
def architecture_page(request: Request, nakama_auth: str | None = Cookie(None)):
    if not check_auth(nakama_auth):
        return RedirectResponse("/login?next=/architecture", status_code=302)
    return _templates.TemplateResponse(
        request, "architecture.html", {"asset_version": _ASSET_VERSION}
    )
