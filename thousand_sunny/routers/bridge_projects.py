"""Bridge projects — 長期戰線 read surface (ADR-068).

A Project is a grouping key for cross-week tasks (自由艦隊社群、電子報、課程…).
This router replaces the retired ADR-031 7-stage workspace with two thin pages:

- ``GET  /bridge/projects``                 — 戰線清單 + live rollups + 新戰線 form
- ``POST /bridge/projects/new``             — create minimal stub → redirect to detail
- ``GET  /bridge/projects/{name}``          — one thread: all member tasks across weeks
- ``POST /bridge/projects/{name}/status``   — archive / restore toggle

Every number is computed on read (ADR-068: no stats snapshots). Task membership
and 🍅 semantics are shared with the Weekly dashboard: tasks come from
``WeeklyIndexer.read_tasks()``; actual 🍅 is the ADR-039 D3 union of daily-note
``pomodoros[]`` and task ``timeEntries[]`` via ``pomodoro_aggregator``.

Auth: HMAC cookie (mirrors ``bridge_weekly.py``).
"""

from __future__ import annotations

import hashlib
import re
import unicodedata
from datetime import date
from pathlib import Path
from typing import Optional
from urllib.parse import quote

from fastapi import APIRouter, Cookie, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates

from shared.config import get_vault_path
from shared.log import get_logger
from shared.markdown import render_markdown
from shared.pomodoro_aggregator import weekly_actual
from shared.project_index import (
    ProjectEntry,
    ProjectError,
    create_project,
    find_project,
    list_projects,
    set_project_status,
    tasks_for,
)
from shared.weekly_indexer import WeeklyIndexer, WeeklyTask, today_taipei
from thousand_sunny.auth import check_auth

logger = get_logger("nakama.web.bridge_projects")

page_router = APIRouter(prefix="/bridge", tags=["bridge-projects"])

_TEMPLATE_DIR = Path(__file__).resolve().parent.parent / "templates" / "bridge"
_templates = Jinja2Templates(directory=str(_TEMPLATE_DIR))

_DAILY_NOTE_RE = re.compile(r"^(\d{4}-\d{2}-\d{2})\.md$")


def _shosho_asset_version() -> str:
    static_dir = Path(__file__).resolve().parent.parent / "static" / "shosho"
    h = hashlib.sha1()
    for css in (
        "tokens.css",
        "bridge.css",
        "bridge-pages.css",
        "bridge-projects.css",
        "theme.js",
    ):
        path = static_dir / css
        if path.exists():
            h.update(path.read_bytes())
    return h.hexdigest()[:8]


_SHOSHO_ASSET_VERSION = _shosho_asset_version()

_ERRORS = {
    "invalid": '戰線名稱不可為空，且不可含 \\ / : * ? " < > | 等字元（80 字內）。',
    "exists": "同名戰線已存在。",
    "missing": "找不到該戰線檔，可能已在 Obsidian 改名或移除。",
}


# ── rollup helpers (all computed on read — ADR-068) ──────────────────────────


def _history_floor(vault: Path) -> date:
    """Earliest daily-note date — the all-time window's start for the 🍅 union."""
    daily = vault / "Journals" / "Daily"
    floor: Optional[date] = None
    if daily.is_dir():
        for p in daily.iterdir():
            m = _DAILY_NOTE_RE.match(p.name)
            if not m:
                continue
            try:
                d = date.fromisoformat(m.group(1))
            except ValueError:
                continue
            if floor is None or d < floor:
                floor = d
    return floor or today_taipei()


def _actual_by_slug(vault: Path, tasks: list[WeeklyTask]) -> dict[str, int]:
    """All-time actual 🍅 per task slug — ONE daily scan for the whole set."""
    if not tasks:
        return {}
    slugs = {t.slug for t in tasks}
    # Window floor: earliest daily note AND earliest task trace — a timeEntry can
    # predate the daily-notes history (or the Journals/Daily dir may be absent).
    floor = _history_floor(vault)
    for t in tasks:
        for e in t.time_entries:
            if isinstance(e, dict):
                raw = str(e.get("startTime") or e.get("endTime") or "")[:10]
                try:
                    floor = min(floor, date.fromisoformat(raw))
                except ValueError:
                    continue
        if t.plan:
            floor = min(floor, min(a.date for a in t.plan))
    rollup = weekly_actual(
        vault,
        floor,
        today_taipei(),
        task_time_entries=[(t.slug, t.time_entries) for t in tasks],
        work_task_keys=slugs,
    )
    return rollup.by_task


def _last_activity(t: WeeklyTask) -> Optional[date]:
    """Most recent trace of the task: latest plan date or timeEntry end date."""
    dates: list[date] = [a.date for a in t.plan]
    for e in t.time_entries:
        if isinstance(e, dict):
            raw = str(e.get("endTime") or e.get("startTime") or "")[:10]
            try:
                dates.append(date.fromisoformat(raw))
            except ValueError:
                continue
    return max(dates) if dates else None


def _task_view(t: WeeklyTask, actual: dict[str, int], project: str) -> dict:
    last = _last_activity(t)
    # Legacy prefix-only members (no ``projects:`` fm) keep the full title in
    # t.name — strip the thread prefix here so rows read uniformly.
    name = t.name
    prefix = f"{project} - "
    if name.startswith(prefix):
        name = name[len(prefix) :] or name
    return {
        "slug": t.slug,
        "name": name,
        "done": t.done,
        "status": t.status,
        "est": t.est_pomodoros,
        "actual": actual.get(t.slug, 0),
        "last": last.isoformat() if last else "",
    }


def _project_view(p: ProjectEntry, members: list[WeeklyTask], actual: dict[str, int]) -> dict:
    views = [_task_view(t, actual, p.name) for t in members]
    # 未完在前、已完成沉底；兩組內都以最近活動新→舊排。
    open_views = sorted([v for v in views if not v["done"]], key=lambda v: v["last"], reverse=True)
    done_views = sorted([v for v in views if v["done"]], key=lambda v: v["last"], reverse=True)
    views = open_views + done_views
    last = max((v["last"] for v in views if v["last"]), default="")
    return {
        "name": p.name,
        "status": p.status,
        "created": p.created[:10],
        "tasks": views,
        "open_count": len(open_views),
        "done_count": len(done_views),
        "est_total": sum(v["est"] for v in views),
        "actual_total": sum(v["actual"] for v in views),
        "last": last,
    }


# ── routes ───────────────────────────────────────────────────────────────────


@page_router.get("/projects", response_class=HTMLResponse)
async def projects_index(
    request: Request,
    err: str | None = None,
    nakama_auth: str | None = Cookie(None),
):
    if not check_auth(nakama_auth):
        return RedirectResponse("/login?next=/bridge/projects", status_code=302)

    vault = get_vault_path()
    projects = list_projects(vault)
    all_tasks = WeeklyIndexer(vault).read_tasks()
    members = {p.name: tasks_for(p.name, all_tasks) for p in projects}
    involved = [t for ts in members.values() for t in ts]
    actual = _actual_by_slug(vault, involved)

    views = [_project_view(p, members[p.name], actual) for p in projects]
    active = sorted(
        [v for v in views if v["status"] == "active"], key=lambda v: v["last"], reverse=True
    )
    archived = sorted(
        [v for v in views if v["status"] == "archived"], key=lambda v: v["last"], reverse=True
    )

    return _templates.TemplateResponse(
        request,
        "projects/index.html",
        {
            "active": active,
            "archived": archived,
            "error_msg": _ERRORS.get(err) if err else None,
            "asset_version": _SHOSHO_ASSET_VERSION,
        },
    )


@page_router.post("/projects/new")
async def projects_create(
    name: str = Form(""),
    nakama_auth: str | None = Cookie(None),
):
    if not check_auth(nakama_auth):
        return RedirectResponse("/login?next=/bridge/projects", status_code=302)
    try:
        entry = create_project(get_vault_path(), name)
    except ProjectError as e:
        code = "exists" if "已存在" in str(e) else "invalid"
        return RedirectResponse(f"/bridge/projects?err={code}", status_code=303)
    logger.info("project created: %s", entry.name)
    return RedirectResponse(f"/bridge/projects/{quote(entry.name)}", status_code=303)


@page_router.get("/projects/{name}", response_class=HTMLResponse)
async def project_detail(
    request: Request,
    name: str,
    nakama_auth: str | None = Cookie(None),
):
    if not check_auth(nakama_auth):
        return RedirectResponse("/login?next=/bridge/projects", status_code=302)

    name = unicodedata.normalize("NFC", name)
    vault = get_vault_path()
    entry = find_project(vault, name)
    if entry is None:
        return RedirectResponse("/bridge/projects?err=missing", status_code=303)

    all_tasks = WeeklyIndexer(vault).read_tasks()
    members = tasks_for(entry.name, all_tasks)
    actual = _actual_by_slug(vault, members)
    view = _project_view(entry, members, actual)

    return _templates.TemplateResponse(
        request,
        "projects/detail.html",
        {
            "p": view,
            "notes_html": render_markdown(entry.body) if entry.body else "",
            "asset_version": _SHOSHO_ASSET_VERSION,
        },
    )


@page_router.post("/projects/{name}/status")
async def project_status(
    name: str,
    status: str = Form(...),
    nakama_auth: str | None = Cookie(None),
):
    if not check_auth(nakama_auth):
        return RedirectResponse("/login?next=/bridge/projects", status_code=302)
    name = unicodedata.normalize("NFC", name)
    try:
        set_project_status(get_vault_path(), name, status)
    except ProjectError:
        return RedirectResponse("/bridge/projects?err=missing", status_code=303)
    return RedirectResponse(f"/bridge/projects/{quote(name)}", status_code=303)
