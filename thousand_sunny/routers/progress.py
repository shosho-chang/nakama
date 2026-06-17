"""Internal partner-readiness progress page (`/progress`).

Summarises Nakama's Lines × Stages, Agents × Stages, and Channels × Stages
readiness. Readiness is hand-curated in ``templates/bridge/progress.html`` and
committed to git as the single source of truth (no DB).

Formerly a public static page; now cookie-authed and rendered through the
shared chassis nav so it lives inside the Bridge instead of carrying its own
docnav. Render pattern (auth + Jinja + asset-versioned CSS) mirrors
``franky.py`` / ``inventory.py``.
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
        "progress/progress.css",
    ):
        path = _STATIC_DIR / rel
        if path.exists():
            h.update(path.read_bytes())
    return h.hexdigest()[:8]


_ASSET_VERSION = _asset_version()


@router.api_route(
    "/progress", methods=["GET", "HEAD"], response_class=HTMLResponse, include_in_schema=False
)
def progress_page(request: Request, nakama_auth: str | None = Cookie(None)):
    if not check_auth(nakama_auth):
        return RedirectResponse("/login?next=/progress", status_code=302)
    return _templates.TemplateResponse(
        request, "progress.html", {"asset_version": _ASSET_VERSION}
    )
