"""Bridge projects — 長期戰線 read surface (ADR-068).

A Project is a grouping key for cross-week tasks (自由艦隊社群、電子報、課程…).
This router replaces the retired ADR-031 7-stage workspace with two thin pages:

- ``GET  /bridge/projects``                 — 戰線清單 + live rollups + 新戰線 form
- ``POST /bridge/projects/new``             — create minimal stub → redirect to detail
- ``GET  /bridge/projects/{name}``          — one thread: all member tasks across weeks
- ``POST /bridge/projects/{name}/status``   — archive / restore toggle
- ``POST /bridge/projects/{name}/attach``   — bulk-assign existing tasks into it

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
from shared.pomodoro_aggregator import POMODORO_MINUTES, weekly_actual
from shared.project_index import (
    ProjectEntry,
    ProjectError,
    find_project,
    list_projects,
    set_project_status,
    tasks_for,
)
from shared.project_templates import (
    TemplateError,
    create_project_with_template,
    kind_label,
    load_templates,
    stage_states,
    unstaged,
)
from shared.project_writer import ProjectWriteError, reassign_task_project
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
    "template": "找不到該專案類型，或樣板任務檔名已被佔用——請看 config/project-templates.yaml。",
    "attach_none": "沒有勾選任何任務。",
    "bracket": (
        "戰線名稱不可含 [ ] # ^ —— Obsidian 的 [[連結]] 沒有辦法跳脫它，"
        "任務歸屬與反向連結都會壞掉。改用全形版本即可，例如「【Pod】蘇予昕」。"
    ),
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


def _when_label(t: WeeklyTask) -> str:
    """The task's scheduled time as one short string: ``09-12 10:00–12:00`` for a
    single timed entry, ``09-18 · 09-19`` for two days, ``09-18 +3`` beyond that.
    Empty when the task has no ``plan[]`` at all — the caller renders that as the
    amber 未排時間, because an unscheduled task is what makes the ETA a lie."""
    if not t.plan:
        return ""
    entries = sorted(t.plan, key=lambda a: a.date)
    if len(entries) == 1:
        e = entries[0]
        stamp = e.date.strftime("%m-%d")
        detail = e.time_range or e.time_label
        return f"{stamp} {detail}".strip() if detail and detail != "整天" else stamp
    days = [e.date.strftime("%m-%d") for e in entries]
    return " · ".join(days[:2]) if len(days) == 2 else f"{days[0]} +{len(days) - 1}"


def _task_view(t: WeeklyTask, actual: dict[str, int], project: str) -> dict:
    last = _last_activity(t)
    # Legacy prefix-only members (no ``projects:`` fm) keep the full title in
    # t.name — strip the thread prefix here so rows read uniformly.
    name = t.name
    prefix = f"{project} - "
    if name.startswith(prefix):
        name = name[len(prefix) :] or name
    act = actual.get(t.slug, 0)
    return {
        "slug": t.slug,
        "name": name,
        "done": t.done,
        "status": t.status,
        "stage": t.stage,
        "est": t.est_pomodoros,
        "actual": act,
        "remaining": max(0, t.est_pomodoros - act) if not t.done else 0,
        "when": _when_label(t),
        "scheduled": bool(t.plan),
        "last": last.isoformat() if last else "",
    }


def _schedule_rows(
    members: list[WeeklyTask], views: dict[str, dict]
) -> tuple[list[dict], list[dict]]:
    """Day-keyed rows for the 時程 view: one row per (task, plan entry), grouped
    by date and ordered by time. Unscheduled open tasks come back as a final
    pseudo-day so they cannot quietly fall off the plan."""
    by_day: dict[date, list[dict]] = {}
    for t in members:
        for e in t.plan:
            by_day.setdefault(e.date, []).append(
                {
                    "slug": t.slug,
                    "name": views[t.slug]["name"],
                    "done": t.done,
                    "time": e.time_range or e.time_label or "整天",
                    "sort": e.start or "",
                    "pomodoros": e.pomodoros,
                }
            )
    rows = [
        {
            "date": d.isoformat(),
            "label": d.strftime("%m-%d"),
            "weekday": "週" + "一二三四五六日"[d.weekday()],
            # NOT "items": Jinja resolves ``d.items`` to the dict method, not the key.
            "slots": sorted(items, key=lambda i: (i["sort"] == "", i["sort"])),
        }
        for d, items in sorted(by_day.items())
    ]
    loose = [
        {"slug": t.slug, "name": views[t.slug]["name"], "pomodoros": views[t.slug]["est"]}
        for t in members
        if not t.plan and not t.done
    ]
    return rows, loose


def _project_view(p: ProjectEntry, members: list[WeeklyTask], actual: dict[str, int]) -> dict:
    views = [_task_view(t, actual, p.name) for t in members]
    by_slug = {v["slug"]: v for v in views}
    # 未完在前、已完成沉底；兩組內都以最近活動新→舊排。
    open_views = sorted([v for v in views if not v["done"]], key=lambda v: v["last"], reverse=True)
    done_views = sorted([v for v in views if v["done"]], key=lambda v: v["last"], reverse=True)
    ordered = open_views + done_views
    last = max((v["last"] for v in ordered if v["last"]), default="")

    # ── 「還差多久 / 還剩多少」(修修 2026-09-10) — every figure read-time ──
    remaining_pom = sum(v["remaining"] for v in open_views)
    unscheduled = [v for v in open_views if not v["scheduled"]]
    eta_dates = [a.date for t in members if not t.done for a in t.plan]
    eta = max(eta_dates) if eta_dates else None
    eta_days = (eta - today_taipei()).days if eta else None

    sched_rows, sched_loose = _schedule_rows(members, by_slug)
    stages = stage_states(p, members, actual)
    # Hoisted: inside the comprehension's `if` this re-ran (and re-read the
    # templates YAML) once per member task (review 2026-09-10).
    loose_slugs = {t.slug for t in unstaged(p, members)}

    return {
        "name": p.name,
        "status": p.status,
        "kind": p.kind,
        "kind_label": kind_label(p.kind) if p.kind else "",
        "created": p.created[:10],
        "tasks": ordered,
        "stages": [
            {
                "name": s.name,
                "order": s.order,
                "total": s.total,
                "done": s.done,
                "est": s.est,
                "actual": s.actual,
                "is_done": s.is_done,
                "is_now": s.is_now,
                "tasks": [v for v in ordered if v["stage"] == s.name],
            }
            for s in stages
        ],
        "loose_tasks": [v for v in ordered if v["slug"] in loose_slugs],
        "kanban": {
            "todo": [v for v in ordered if not v["done"] and v["status"] != "doing"],
            "doing": [v for v in ordered if not v["done"] and v["status"] == "doing"],
            "done": done_views,
        },
        "sched_rows": sched_rows,
        "sched_loose": sched_loose,
        "open_count": len(open_views),
        "done_count": len(done_views),
        "total_count": len(ordered),
        "est_total": sum(v["est"] for v in ordered),
        "actual_total": sum(v["actual"] for v in ordered),
        "remaining_pom": remaining_pom,
        "remaining_hours": round(remaining_pom * POMODORO_MINUTES / 60, 1),
        "eta": eta.strftime("%m-%d") if eta else "",
        "eta_days": eta_days,
        "unscheduled_count": len(unscheduled),
        "pct": round(len(done_views) * 100 / len(ordered)) if ordered else 0,
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
            "kinds": [(k, t.label) for k, t in sorted(load_templates().items())],
            "error_msg": _ERRORS.get(err) if err else None,
            "asset_version": _SHOSHO_ASSET_VERSION,
        },
    )


@page_router.post("/projects/new")
async def projects_create(
    name: str = Form(""),
    kind: str = Form(""),
    nakama_auth: str | None = Cookie(None),
):
    if not check_auth(nakama_auth):
        return RedirectResponse("/login?next=/bridge/projects", status_code=302)
    try:
        entry, tasks = create_project_with_template(get_vault_path(), name, kind)
    except ProjectError as e:
        return RedirectResponse(f"/bridge/projects?err={e.code}", status_code=303)
    except (TemplateError, ProjectWriteError):
        logger.exception("template create failed for %r (kind=%r)", name, kind)
        return RedirectResponse("/bridge/projects?err=template", status_code=303)
    logger.info("project created: %s (kind=%s, %d tasks)", entry.name, kind or "-", len(tasks))
    return RedirectResponse(f"/bridge/projects/{quote(entry.name)}", status_code=303)


@page_router.get("/projects/{name}", response_class=HTMLResponse)
async def project_detail(
    request: Request,
    name: str,
    err: str | None = None,
    saved: str | None = None,
    n: int = 0,
    failed: int = 0,
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

    # Attach picker: every OPEN task that isn't already a member. Tasks belonging
    # to another project stay selectable but carry their owner's name, so moving
    # one is a deliberate act rather than an accident (修修 2026-09-10).
    member_slugs = {t.slug for t in members}
    candidates = [
        {"slug": t.slug, "name": t.name or t.title, "owner": t.project}
        for t in all_tasks
        if not t.done and t.slug not in member_slugs
    ]
    candidates.sort(key=lambda c: (bool(c["owner"]), c["name"]))

    # `n` = succeeded, `failed` = did not. Reported separately so an all-failed
    # batch can never render as "部分成功" (review 2026-09-10).
    saved_msg = error_msg_extra = None
    if saved == "attached":
        if n and failed:
            saved_msg = (
                f"已加入 {n} 個任務；{failed} 個失敗（檔名衝突或已在 Obsidian 改名／移動）。"
            )
        elif n:
            saved_msg = f"已把 {n} 個任務加入這個專案。"
        elif failed:
            error_msg_extra = (
                f"{failed} 個任務都沒有加入成功"
                f"（檔名衝突或已在 Obsidian 改名／移動）——這個專案沒有變動。"
            )

    return _templates.TemplateResponse(
        request,
        "projects/detail.html",
        {
            "p": view,
            "candidates": candidates,
            "notes_html": render_markdown(entry.body) if entry.body else "",
            "error_msg": (_ERRORS.get(err) if err else None) or error_msg_extra,
            "saved_msg": saved_msg,
            "asset_version": _SHOSHO_ASSET_VERSION,
        },
    )


@page_router.post("/projects/{name}/attach")
async def project_attach_tasks(
    name: str,
    task: list[str] = Form(default=[]),
    nakama_auth: str | None = Cookie(None),
):
    """Bulk-assign existing tasks into this project (修修 2026-09-10).

    Each pick goes through the ordinary ``reassign_task_project`` path so the
    filename prefix, ``projects:`` frontmatter and any linked calendar event
    titles all stay in step — one rule, not a second bulk-only code path.
    Failures are counted and reported rather than aborting the whole batch.
    """
    if not check_auth(nakama_auth):
        return RedirectResponse("/login?next=/bridge/projects", status_code=302)
    name = unicodedata.normalize("NFC", name)
    back = f"/bridge/projects/{quote(name)}"

    # A missing project is the more fundamental failure — report it even when the
    # selection is also empty, so the redirect lands somewhere that exists.
    if find_project(get_vault_path(), name) is None:
        return RedirectResponse("/bridge/projects?err=missing", status_code=303)
    picks = [t.strip() for t in task if t and t.strip()]
    if not picks:
        return RedirectResponse(f"{back}?err=attach_none", status_code=303)

    ok = failed = 0
    for slug in picks:
        try:
            reassign_task_project(vault_root=get_vault_path(), task_slug=slug, project_slug=name)
            ok += 1
        except (ProjectWriteError, OSError):
            logger.exception("attach failed: %s → %s", slug, name)
            failed += 1
    return RedirectResponse(f"{back}?saved=attached&n={ok}&failed={failed}", status_code=303)


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
