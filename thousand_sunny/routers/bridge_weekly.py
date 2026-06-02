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
from datetime import date, datetime, timedelta
from pathlib import Path

from fastapi import APIRouter, Cookie, Form, Query, Request
from fastapi import Path as PathParam
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates

from shared.config import get_vault_path
from shared.log import get_logger
from shared.pomodoro_aggregator import TAIPEI
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
    log_time_entry,
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


# ── Task detail page + pomodoro timer (ADR-039 E) ────────────────────────────
# A per-task page; the bottom timer logs a real focus session into the task's
# timeEntries[] (TaskNotes write surface — NOT the Journals/Weekly red line).

# Nominal block lengths: 25-min pomodoro (+1🍅) / 75-min deep super-focus (UFO if
# run ≥70 min). The live timer reports its **actual elapsed** seconds (ADR-040 A2
# — fixes the v1 bug where 提早完成 logged a full block); the manual "+1" backup
# omits elapsed and logs a full nominal block flagged ``manual``.
_MODE_MINUTES = {"pomodoro": 25, "deep": 75}

_TASK_ERRORS = {
    "mode": "未知的計時模式。",
    "log": "記錄番茄鐘失敗，任務檔可能已在 Obsidian 改名或移除。",
}


def _now_taipei() -> datetime:
    return datetime.now(TAIPEI)


def _obsidian_uri(vault: Path, relative_path: str) -> str:
    """Best-effort ``obsidian://open`` deeplink. Vault name defaults to the
    vault folder's basename (Obsidian's default); ``file`` is the vault-relative
    path. Resolves on 修修's machine where the LifeOS vault is registered."""
    from urllib.parse import quote

    return f"obsidian://open?vault={quote(vault.name)}&file={quote(relative_path)}"


def _task_back(slug: str, week_key: str, *, err: str | None = None, logged: bool = False):
    from urllib.parse import quote

    url = f"/bridge/weekly/task/{quote(slug)}?week={week_key}"
    if err:
        url += f"&err={err}"
    elif logged:
        url += "&logged=1"
    return RedirectResponse(url, status_code=303)


@page_router.get("/weekly/task/{slug}", response_class=HTMLResponse)
async def weekly_task_detail(
    request: Request,
    slug: str = PathParam(..., min_length=1),
    week: str | None = Query(None),
    err: str | None = Query(None),
    logged: int | None = Query(None),
    nakama_auth: str | None = Cookie(None),
):
    if not check_auth(nakama_auth):
        return RedirectResponse("/login?next=/bridge/weekly", status_code=302)

    wk_key = _safe_week_key(week)
    vault = get_vault_path()
    idx = WeeklyIndexer(vault)
    task = idx.find_task(slug)
    if task is None:
        # renamed/removed in Obsidian — bounce back to the dashboard with a banner
        return _back(wk_key, "task")

    return _templates.TemplateResponse(
        request,
        "task.html",
        {
            "task": task,
            "week_key": wk_key,
            "obsidian_uri": _obsidian_uri(vault, task.relative_path),
            "asset_version": _SHOSHO_ASSET_VERSION,
            "error_msg": _TASK_ERRORS.get(err) if err else None,
            "logged": bool(logged),
        },
    )


@page_router.post("/weekly/task/{slug}/log")
async def weekly_task_log(
    slug: str = PathParam(..., min_length=1),
    mode: str = Form("pomodoro"),
    # actual elapsed from the live timer; 0/absent → manual full block
    elapsed_seconds: int = Form(0),
    manual: int = Form(0),
    week: str = Form(""),
    nakama_auth: str | None = Cookie(None),
):
    if not check_auth(nakama_auth):
        return RedirectResponse("/login?next=/bridge/weekly", status_code=302)
    wk_key = _safe_week_key(week)

    nominal = _MODE_MINUTES.get(mode)
    if nominal is None:
        return _task_back(slug, wk_key, err="mode")

    end = _now_taipei()
    # Live timer → actual elapsed (capped at the nominal block, floored at 1 min so
    # a stray 0 doesn't make a non-positive span); manual button → full nominal block.
    if elapsed_seconds and elapsed_seconds > 0:
        seconds = max(60, min(int(elapsed_seconds), nominal * 60))
        start = end - timedelta(seconds=seconds)
    else:
        start = end - timedelta(minutes=nominal)
    try:
        log_time_entry(
            get_vault_path(),
            slug,
            start,
            end,
            mode=mode,
            planned_minutes=nominal,
            completed=True,
            manual=bool(manual),
        )
    except WeeklyWriteError as exc:
        logger.warning("weekly_task_log: %s", exc)
        return _task_back(slug, wk_key, err="log")
    return _task_back(slug, wk_key, logged=True)
