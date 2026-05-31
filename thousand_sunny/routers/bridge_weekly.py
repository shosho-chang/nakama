"""Bridge Weekly Dashboard — Tier B (vault-as-substrate, read-only Slice 0).

Surface:
  - ``GET /bridge/weekly`` — the focused-week dashboard (ADR-039). Defaults to
    the current Sunday-start week; ``?week=YYYY-MM-DD`` (a start-Sunday key)
    navigates. Read-only: aggregates real vault tasks + pomodoro session logs;
    no writes (plan[]/review writes land in Slice 1/2).

Auth: HMAC cookie (mirrors bridge_digests / bridge_projects).
"""

from __future__ import annotations

import hashlib
from pathlib import Path

from fastapi import APIRouter, Cookie, Query, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates

from shared.config import get_vault_path
from shared.log import get_logger
from shared.weekly_indexer import (
    WeeklyIndexer,
    today_taipei,
    week_for_date,
    week_from_key,
)
from thousand_sunny.auth import check_auth

logger = get_logger("nakama.web.bridge_weekly")

page_router = APIRouter(prefix="/bridge", tags=["bridge-weekly"])

_TEMPLATE_DIR = Path(__file__).resolve().parent.parent / "templates" / "bridge"
_templates = Jinja2Templates(directory=str(_TEMPLATE_DIR))


def _shosho_asset_version() -> str:
    static_dir = Path(__file__).resolve().parent.parent / "static" / "shosho"
    h = hashlib.sha1()
    for css in ("tokens.css", "bridge.css", "bridge-pages.css", "bridge-weekly.css", "theme.js"):
        path = static_dir / css
        if path.exists():
            h.update(path.read_bytes())
    return h.hexdigest()[:8]


_SHOSHO_ASSET_VERSION = _shosho_asset_version()


@page_router.get("/weekly", response_class=HTMLResponse)
async def weekly_landing(
    request: Request,
    week: str | None = Query(None),
    nakama_auth: str | None = Cookie(None),
):
    if not check_auth(nakama_auth):
        return RedirectResponse("/login?next=/bridge/weekly", status_code=302)

    wk = week_from_key(week) if week else None
    if wk is None:
        wk = week_for_date(today_taipei())

    idx = WeeklyIndexer(get_vault_path())
    try:
        view = idx.view(wk)
    except Exception:  # noqa: BLE001 — surface an empty-but-rendered page, never 500 the dashboard
        logger.exception("weekly view assembly failed for week=%s", wk.file_key)
        raise

    return _templates.TemplateResponse(
        request,
        "weekly.html",
        {"view": view, "asset_version": _SHOSHO_ASSET_VERSION},
    )
