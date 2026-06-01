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
from datetime import date
from pathlib import Path

from fastapi import APIRouter, Cookie, Form, Query, Request
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
from shared.weekly_writer import (
    WeekendReasonRequired,
    WeeklyWriteError,
    add_plan_entry,
    remove_plan_entry,
    sync_scheduled_to_next_plan,
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


_PLAN_ERRORS = {
    "weekend": "週末排程需填寫原因（D9）——已取消這次排入。",
    "pomodoros": "番茄數需介於 1–20。",
    "date": "排程日期格式無效。",
    "task": "找不到該任務檔，可能已在 Obsidian 改名或移除。",
}


@page_router.get("/weekly", response_class=HTMLResponse)
async def weekly_landing(
    request: Request,
    week: str | None = Query(None),
    err: str | None = Query(None),
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
        {
            "view": view,
            "asset_version": _SHOSHO_ASSET_VERSION,
            "error_msg": _PLAN_ERRORS.get(err) if err else None,
        },
    )


# ── Plan mutations (Slice 1a — ADR-039 D4/D9) ────────────────────────────────
# page_router (no blanket auth dep) → manual check_auth per endpoint; form POST →
# 303; failures that are 修修-recoverable redirect back with ?err=… (banner),
# genuine not-found is a 404. Mirrors bridge_projects mutation idiom.


def _safe_week_key(week: str | None) -> str:
    wk = week_from_key(week) if week else None
    if wk is None:
        wk = week_for_date(today_taipei())
    return wk.file_key


def _back(week_key: str, err: str | None = None) -> RedirectResponse:
    url = f"/bridge/weekly?week={week_key}"
    if err:
        url += f"&err={err}"
    return RedirectResponse(url, status_code=303)


def _parse_entry_date(entry_date: str) -> date | None:
    try:
        return date.fromisoformat(entry_date.strip())
    except ValueError:
        return None


@page_router.post("/weekly/plan")
async def weekly_plan_add(
    task_slug: str = Form(..., min_length=1),
    entry_date: str = Form(..., min_length=1),
    pomodoros: int = Form(...),
    reason: str = Form(""),
    week: str = Form(""),
    nakama_auth: str | None = Cookie(None),
):
    if not check_auth(nakama_auth):
        return RedirectResponse("/login?next=/bridge/weekly", status_code=302)
    wk_key = _safe_week_key(week)

    ed = _parse_entry_date(entry_date)
    if ed is None:
        return _back(wk_key, "date")
    if not 1 <= pomodoros <= 20:
        return _back(wk_key, "pomodoros")

    try:
        add_plan_entry(get_vault_path(), task_slug, ed, pomodoros, reason.strip() or None)
    except WeekendReasonRequired:
        return _back(wk_key, "weekend")
    except WeeklyWriteError as exc:
        logger.warning("weekly_plan_add: %s", exc)
        return _back(wk_key, "task")
    return _back(wk_key)


@page_router.post("/weekly/plan/remove")
async def weekly_plan_remove(
    task_slug: str = Form(..., min_length=1),
    entry_date: str = Form(..., min_length=1),
    week: str = Form(""),
    nakama_auth: str | None = Cookie(None),
):
    if not check_auth(nakama_auth):
        return RedirectResponse("/login?next=/bridge/weekly", status_code=302)
    wk_key = _safe_week_key(week)

    ed = _parse_entry_date(entry_date)
    if ed is None:
        return _back(wk_key, "date")

    try:
        remove_plan_entry(get_vault_path(), task_slug, ed)
    except WeeklyWriteError as exc:
        logger.warning("weekly_plan_remove: %s", exc)
        return _back(wk_key, "task")
    return _back(wk_key)


@page_router.post("/weekly/sync-scheduled")
async def weekly_sync_scheduled(
    task_slug: str = Form(..., min_length=1),
    week: str = Form(""),
    nakama_auth: str | None = Cookie(None),
):
    if not check_auth(nakama_auth):
        return RedirectResponse("/login?next=/bridge/weekly", status_code=302)
    wk_key = _safe_week_key(week)

    try:
        sync_scheduled_to_next_plan(get_vault_path(), task_slug)
    except WeeklyWriteError as exc:
        logger.warning("weekly_sync_scheduled: %s", exc)
        return _back(wk_key, "task")
    return _back(wk_key)
