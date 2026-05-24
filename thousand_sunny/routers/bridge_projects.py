"""Bridge project workspace — Tier C (ADR-031).

Surfaces:

- ``GET  /bridge/projects``              — index list (status / priority / 🍅 rollup)
- ``GET  /bridge/projects/new``          — create form (non-Slack fallback)
- ``POST /bridge/projects``              — create handler (calls existing bootstrap)
- ``GET  /bridge/projects/{slug}``       — detail with 7-tab stage gate
- ``POST /bridge/projects/{slug}/frontmatter`` — α + γ field updates (form post)
- ``POST /bridge/projects/{slug}/section/{section_id}`` — long-form body section
- ``POST /bridge/projects/{slug}/timer/{action}`` — Pomodoro timer state (action ∈
  start | complete | cancel)
- ``POST /bridge/projects/{slug}/tasks/{task_name}/manual-pomodoro`` — +1🍅 manual

Review endpoints (Storyteller / Coach) are stubs in PR1; real LLM dispatch lands in PR2.

Auth: HMAC cookie (mirrors ``bridge.py`` / ``bridge_digests.py``).

Pattern: ADR-030 D2 (FS-direct read via ``shared.project_indexer``) + atomic
write via ``shared.project_writer``. Vault is canonical SoT.
"""

from __future__ import annotations

import hashlib
from datetime import datetime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Cookie, Form, Header, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates

from shared.config import get_vault_path
from shared.lifeos_writer import (
    CONTENT_TYPES,
    ProjectExistsError,
    create_project_with_tasks,
    default_task_names,
)
from shared.log import get_logger
from shared.project_indexer import ProjectIndexer, ProjectNotFoundError, normalize_slug
from shared.project_reviews import (
    ProjectReviewError,
    review_hook,
    review_script,
)
from shared.project_writer import (
    VALID_TASK_STATUSES,
    ProjectWriteError,
    append_review,
    append_timeentry,
    create_task,
    delete_task,
    pop_last_timeentry,
    read_task_status,
    update_body_section,
    update_frontmatter,
    update_task_status,
)
from thousand_sunny.auth import check_auth

logger = get_logger("nakama.web.bridge_projects")

page_router = APIRouter(prefix="/bridge", tags=["bridge-projects"])

_TEMPLATE_DIR = Path(__file__).resolve().parent.parent / "templates" / "bridge"
_templates = Jinja2Templates(directory=str(_TEMPLATE_DIR))

# Tab definitions: (slug, label_en, label_zh) — ADR-031 D4
TABS: tuple[tuple[str, str, str], ...] = (
    ("brief", "Brief", "簡介"),
    ("research", "Research", "研究"),
    ("title-thumbnail", "Title & Thumbnail", "標題與縮圖"),
    ("hook", "Hook", "鉤子"),
    ("script", "Script", "腳本"),
    ("review", "Review", "審查"),
    ("publish", "Publish", "發布"),
)
TAB_SLUGS = tuple(t[0] for t in TABS)

# Pomodoro duration in minutes — fixed in PR1 v1 (configurable later)
POMODORO_MINUTES = 25


def _shosho_asset_version() -> str:
    """8-char hash of design-system CSS this page links — cache-bust friendly."""
    static_dir = Path(__file__).resolve().parent.parent / "static" / "shosho"
    h = hashlib.sha1()
    for css in (
        "tokens.css",
        "bridge.css",
        "bridge-pages.css",
        "bridge-projects.css",
        "theme.js",
        "bridge-projects.js",
    ):
        path = static_dir / css
        if path.exists():
            h.update(path.read_bytes())
    return h.hexdigest()[:8]


_SHOSHO_ASSET_VERSION = _shosho_asset_version()


def _indexer() -> ProjectIndexer:
    return ProjectIndexer(get_vault_path())


# ─────────────────────────────────────────────────────────────────────────
# Stage-gate completeness probe (used by tab icons + pre-publish banner).
# Per ADR-031 D4 this is **advisory**, not enforced.
# ─────────────────────────────────────────────────────────────────────────


def _tab_status(entry, body_md: str) -> dict[str, str]:
    """Return {tab_slug: '✓' | '◐' | '○'} for each tab.

    Cheap heuristics — over-tune later if owner reports false signals.
    """
    out: dict[str, str] = {}

    out["brief"] = "✓" if entry.one_sentence.strip() else "○"

    # Research = some keyword research or KB result evidence in body.
    # Cheap probe: count of body bytes excluding empty sections.
    body_len = len(body_md.strip())
    out["research"] = "✓" if body_len > 500 else ("◐" if body_len > 100 else "○")

    if entry.title_candidates and entry.thumbnail_concept.strip():
        out["title-thumbnail"] = "✓"
    elif entry.title_candidates or entry.thumbnail_concept.strip():
        out["title-thumbnail"] = "◐"
    else:
        out["title-thumbnail"] = "○"

    out["hook"] = "✓" if entry.hook_text.strip() else "○"

    # Script = `## Script / Outline` section has more than the heading
    if "## Script / Outline" in body_md:
        # Crude: characters after the heading
        idx = body_md.find("## Script / Outline")
        script_body = body_md[idx + len("## Script / Outline") :].split("\n##", 1)[0]
        n = len(script_body.strip())
        out["script"] = "✓" if n > 400 else ("◐" if n > 50 else "○")
    else:
        out["script"] = "○"

    n_reviews = sum(1 for r in entry.reviews if r.summary)
    if n_reviews >= 2:
        out["review"] = "✓"
    elif n_reviews == 1:
        out["review"] = "◐"
    else:
        out["review"] = "○"

    out["publish"] = "✓" if entry.status == "published" else "○"

    return out


# ─────────────────────────────────────────────────────────────────────────
# Routes
# ─────────────────────────────────────────────────────────────────────────


@page_router.get("/projects", response_class=HTMLResponse)
async def projects_index(
    request: Request,
    nakama_auth: str | None = Cookie(None),
    include_archived: int = 0,
):
    if not check_auth(nakama_auth):
        return RedirectResponse("/login?next=/bridge/projects", status_code=302)

    idx = _indexer()
    entries = idx.list_all(include_archived=bool(include_archived))

    return _templates.TemplateResponse(
        request,
        "projects/index.html",
        {
            "entries": entries,
            "include_archived": bool(include_archived),
            "asset_version": _SHOSHO_ASSET_VERSION,
        },
    )


@page_router.get("/projects/new", response_class=HTMLResponse)
async def projects_new_get(request: Request, nakama_auth: str | None = Cookie(None)):
    if not check_auth(nakama_auth):
        return RedirectResponse("/login?next=/bridge/projects/new", status_code=302)
    return _templates.TemplateResponse(
        request,
        "projects/new.html",
        {
            "content_types": list(CONTENT_TYPES),
            "asset_version": _SHOSHO_ASSET_VERSION,
        },
    )


@page_router.post("/projects")
async def projects_create(
    request: Request,
    nakama_auth: str | None = Cookie(None),
    title: str = Form(...),
    content_type: str = Form(...),
    search_topic: str = Form(""),
    priority: str = Form("medium"),
    area: str = Form("work"),
):
    if not check_auth(nakama_auth):
        return RedirectResponse("/login?next=/bridge/projects/new", status_code=302)

    title = title.strip()
    if not title:
        raise HTTPException(status_code=400, detail="title required")
    if content_type not in CONTENT_TYPES:
        raise HTTPException(status_code=400, detail=f"unknown content_type: {content_type!r}")

    task_names = default_task_names(content_type)
    try:
        create_project_with_tasks(
            title=normalize_slug(title),
            content_type=content_type,  # type: ignore[arg-type]
            task_names=task_names,
            priority=priority,
            area=area,
            search_topic=search_topic.strip() or None,
        )
    except ProjectExistsError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc

    return RedirectResponse(f"/bridge/projects/{normalize_slug(title)}", status_code=303)


@page_router.get("/projects/{slug}", response_class=HTMLResponse)
async def projects_detail(
    request: Request,
    slug: str,
    nakama_auth: str | None = Cookie(None),
    tab: str = "brief",
):
    if not check_auth(nakama_auth):
        return RedirectResponse(f"/login?next=/bridge/projects/{slug}", status_code=302)

    idx = _indexer()
    try:
        entry = idx.get(slug)
        body_md = idx.load_body(slug)
    except ProjectNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc

    if tab not in TAB_SLUGS:
        tab = "brief"

    tasks = _scan_tasks(entry.slug)
    tab_status = _tab_status(entry, body_md)

    return _templates.TemplateResponse(
        request,
        "projects/detail.html",
        {
            "entry": entry,
            "body_md": body_md,
            "tab": tab,
            "tabs": TABS,
            "tab_status": tab_status,
            "tasks": tasks,
            "pomodoro_minutes": POMODORO_MINUTES,
            "asset_version": _SHOSHO_ASSET_VERSION,
        },
    )


# ── Mutations ──────────────────────────────────────────────────────────────


def _redirect_back(slug: str, tab: str = "brief") -> RedirectResponse:
    return RedirectResponse(f"/bridge/projects/{slug}#{tab}", status_code=303)


@page_router.post("/projects/{slug}/frontmatter")
async def projects_update_frontmatter(
    request: Request,
    slug: str,
    nakama_auth: str | None = Cookie(None),
    field: str = Form(...),
    value: str = Form(""),
    tab: str = Form("brief"),
):
    """Generic single-field frontmatter update (form-encoded).

    Whitelist of allowed fields keeps the surface tight; arbitrary writes are
    rejected so the route can't be coerced into bypassing schema.
    """
    if not check_auth(nakama_auth):
        return RedirectResponse(f"/login?next=/bridge/projects/{slug}", status_code=302)

    slug = normalize_slug(slug)

    # Single-value fields
    text_fields = {
        "one_sentence",
        "hook_text",
        "thumbnail_concept",
        "search_topic",
    }
    enum_fields = {
        "status": {"active", "paused", "published", "archived"},
        "priority": {"first", "high", "medium", "low"},
        "area": {"work", "play", "love", "health"},
    }
    nullable_str_fields = {"quarter", "parent_kr", "publish_date"}

    patch: dict[str, Any] = {}
    if field in text_fields:
        patch[field] = value
    elif field in enum_fields:
        if value not in enum_fields[field]:
            raise HTTPException(status_code=400, detail=f"invalid {field}: {value!r}")
        patch[field] = value
        if field == "status" and value == "published":
            patch["publish_date"] = datetime.now(ZoneInfo("Asia/Taipei")).date().isoformat()
    elif field in nullable_str_fields:
        patch[field] = value if value.strip() else None
    elif field == "title_candidates":
        # Multiline textarea → list of stripped non-empty lines
        items = [line.strip() for line in value.splitlines() if line.strip()]
        patch["title_candidates"] = items
    else:
        raise HTTPException(status_code=400, detail=f"unknown field: {field!r}")

    try:
        update_frontmatter(vault_root=get_vault_path(), slug=slug, patch=patch)
    except Exception as exc:  # noqa: BLE001 — surface any IO failure
        logger.exception("frontmatter update failed: slug=%s field=%s", slug, field)
        raise HTTPException(status_code=500, detail=str(exc)) from exc

    return _redirect_back(slug, tab if tab in TAB_SLUGS else "brief")


# URL slug → (H2 heading text, parent tab) — keep URL ASCII-safe;
# slashes in heading text like "Script / Outline" can't ride in path params.
EDITABLE_SECTIONS: dict[str, tuple[str, str]] = {
    "script": ("Script / Outline", "script"),
    "description": ("專案描述", "brief"),
    "expected": ("預期成果", "brief"),
    "draft-outline": ("Draft Outline", "script"),
    "notes": ("專案筆記", "script"),
    "youtube-description": ("YouTube Description", "publish"),
    "show-notes": ("Show Notes", "publish"),
}


@page_router.post("/projects/{slug}/section/{section_slug}")
async def projects_update_section(
    request: Request,
    slug: str,
    section_slug: str,
    nakama_auth: str | None = Cookie(None),
    content: str = Form(""),
    tab: str = Form("script"),
):
    """Update an H2 body section.

    ``section_slug`` ∈ EDITABLE_SECTIONS; we look up the heading text + parent
    tab from there. This avoids URL-encoding slashes in heading text like
    "Script / Outline".
    """
    if not check_auth(nakama_auth):
        return RedirectResponse(f"/login?next=/bridge/projects/{slug}", status_code=302)

    slug = normalize_slug(slug)

    if section_slug not in EDITABLE_SECTIONS:
        raise HTTPException(status_code=400, detail=f"section not editable: {section_slug!r}")

    if not content.strip():
        raise HTTPException(
            status_code=400,
            detail="empty content; submit non-empty body or use Obsidian to delete the section",
        )

    heading_text, parent_tab = EDITABLE_SECTIONS[section_slug]

    try:
        update_body_section(
            vault_root=get_vault_path(),
            slug=slug,
            heading_text=heading_text,
            content=content,
        )
    except Exception as exc:  # noqa: BLE001
        logger.exception("section update failed: slug=%s section_slug=%s", slug, section_slug)
        raise HTTPException(status_code=500, detail=str(exc)) from exc

    return _redirect_back(slug, parent_tab)


@page_router.post("/projects/{slug}/timer/{action}")
async def projects_timer(
    request: Request,
    slug: str,
    action: str,
    nakama_auth: str | None = Cookie(None),
    task_name: str = Form(""),
):
    """Pomodoro timer state transitions.

    PR1 v1: ``complete`` writes a 25-min timeEntry to the active task and
    recomputes project pomodoro rollup. ``start`` and ``cancel`` are no-ops
    (timer state lives in browser sessionStorage). Endpoint signatures
    reserved for future server-side persistence.
    """
    if not check_auth(nakama_auth):
        return RedirectResponse(f"/login?next=/bridge/projects/{slug}", status_code=302)

    if action not in ("start", "complete", "cancel"):
        raise HTTPException(status_code=400, detail=f"unknown action: {action!r}")

    slug = normalize_slug(slug)

    if action == "complete":
        if not task_name.strip():
            raise HTTPException(status_code=400, detail="task_name required for complete")
        _write_pomodoro_entry(
            slug=slug,
            task_name=task_name.strip(),
            now_minus_minutes=POMODORO_MINUTES,
        )

    return _redirect_back(slug, "brief")


@page_router.post("/projects/{slug}/tasks/new")
async def projects_tasks_new(
    request: Request,
    slug: str,
    nakama_auth: str | None = Cookie(None),
    name: str = Form(""),
    estimated_pomodoros: int = Form(4),
    priority: str = Form("normal"),
    scheduled: str = Form(""),
):
    """Create a new TaskNotes-compatible task for this project.

    Frontmatter shape matches :func:`shared.lifeos_writer.render_task`
    so the TaskNotes plugin indexes the file the same way as
    bootstrap-time tasks.
    """
    if not check_auth(nakama_auth):
        return RedirectResponse(f"/login?next=/bridge/projects/{slug}", status_code=302)

    slug = normalize_slug(slug)
    name = name.strip()
    if not name:
        raise HTTPException(status_code=400, detail="task name is required")

    if priority not in ("low", "normal", "high"):
        raise HTTPException(status_code=400, detail=f"unknown priority: {priority!r}")

    if not (1 <= estimated_pomodoros <= 20):
        raise HTTPException(status_code=400, detail="estimated_pomodoros must be between 1 and 20")

    try:
        create_task(
            vault_root=get_vault_path(),
            project_slug=slug,
            task_name=name,
            estimated_pomodoros=estimated_pomodoros,
            priority=priority,
            scheduled=scheduled.strip() or None,
        )
    except ProjectWriteError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except Exception as exc:  # noqa: BLE001
        logger.exception("create_task failed: slug=%s name=%s", slug, name)
        raise HTTPException(status_code=500, detail=str(exc)) from exc

    return _redirect_back(slug, "brief")


@page_router.post("/projects/{slug}/tasks/{task_name}/status")
async def projects_task_status(
    request: Request,
    slug: str,
    task_name: str,
    nakama_auth: str | None = Cookie(None),
    value: str = Form(...),
    accept: str = Header(""),
):
    """Update a TaskNote's ``status`` field (ADR-031 §F1 4-state workflow).

    Content-negotiates JSON same as +1🍅 / undo.
    """
    if not check_auth(nakama_auth):
        return RedirectResponse(f"/login?next=/bridge/projects/{slug}", status_code=302)

    slug = normalize_slug(slug)
    if value not in VALID_TASK_STATUSES:
        raise HTTPException(
            status_code=400,
            detail=f"unknown task status {value!r}; valid: {VALID_TASK_STATUSES}",
        )

    try:
        update_task_status(
            vault_root=get_vault_path(),
            project_slug=slug,
            task_name=task_name,
            status=value,
        )
    except ProjectWriteError as exc:
        # File not found → 404
        raise HTTPException(status_code=404, detail=str(exc)) from exc

    if "application/json" in accept:
        return {"task_name": task_name, "task_status": value}
    return _redirect_back(slug, "brief")


@page_router.post("/projects/{slug}/tasks/{task_name}/delete")
async def projects_task_delete(
    request: Request,
    slug: str,
    task_name: str,
    nakama_auth: str | None = Cookie(None),
    accept: str = Header(""),
):
    """Send a TaskNote .md to recycle bin and recompute project rollup.

    Recycle-bin (not hard delete) so user can recover from misclicks via
    Windows / macOS trash. Project ``pomodoro.{est,actual}_total`` is
    recomputed from the surviving tasks.
    """
    if not check_auth(nakama_auth):
        return RedirectResponse(f"/login?next=/bridge/projects/{slug}", status_code=302)

    slug = normalize_slug(slug)
    deleted = delete_task(
        vault_root=get_vault_path(),
        project_slug=slug,
        task_name=task_name,
    )
    if not deleted:
        raise HTTPException(status_code=404, detail=f"Task not found: {task_name}")

    tasks = _scan_tasks(slug)
    est_total = sum(t["est_pomodoros"] for t in tasks)
    actual_total = sum(t["actual_pomodoros"] for t in tasks)
    update_frontmatter(
        vault_root=get_vault_path(),
        slug=slug,
        patch={"pomodoro": {"est_total": est_total, "actual_total": actual_total}},
    )

    if "application/json" in accept:
        return {
            "task_name": task_name,
            "deleted": True,
            "project_actual_total": actual_total,
            "project_est_total": est_total,
        }
    return _redirect_back(slug, "brief")


@page_router.post("/projects/{slug}/tasks/{task_name}/manual-pomodoro")
async def projects_manual_pomodoro(
    request: Request,
    slug: str,
    task_name: str,
    nakama_auth: str | None = Cookie(None),
    accept: str = Header(""),
):
    """Synthesize a single completed pomodoro for offline / physical-timer use.

    Content negotiation:
    - ``Accept: application/json`` → returns ``{task_actual, project_actual_total,
      project_est_total}`` for AJAX callers (dock +1🍅 button). No page reload,
      preserves current tab + dock state.
    - default → 303 redirect to brief tab (no-JS fallback / direct form-post).
    """
    if not check_auth(nakama_auth):
        return RedirectResponse(f"/login?next=/bridge/projects/{slug}", status_code=302)

    slug = normalize_slug(slug)
    new_status = _write_pomodoro_entry(
        slug=slug, task_name=task_name, now_minus_minutes=POMODORO_MINUTES
    )

    if "application/json" in accept:
        tasks = _scan_tasks(slug)
        task_actual = next((t["actual_pomodoros"] for t in tasks if t["name"] == task_name), 0)
        return {
            "task_name": task_name,
            "task_actual": task_actual,
            "task_status": new_status,
            "project_actual_total": sum(t["actual_pomodoros"] for t in tasks),
            "project_est_total": sum(t["est_pomodoros"] for t in tasks),
        }
    return _redirect_back(slug, "brief")


@page_router.post("/projects/{slug}/tasks/{task_name}/manual-pomodoro/undo")
async def projects_manual_pomodoro_undo(
    request: Request,
    slug: str,
    task_name: str,
    nakama_auth: str | None = Cookie(None),
    accept: str = Header(""),
):
    """Undo: pop the last ``timeEntries`` entry from this task.

    Use case: accidental click on +1🍅, or just rolling back a session
    that didn't actually happen. Same content-negotiation as the +1
    endpoint — Accept: application/json → JSON payload, otherwise 303.

    Returns 409 if the task has no entries to remove.
    """
    if not check_auth(nakama_auth):
        return RedirectResponse(f"/login?next=/bridge/projects/{slug}", status_code=302)

    slug = normalize_slug(slug)
    popped = pop_last_timeentry(
        vault_root=get_vault_path(),
        project_slug=slug,
        task_name=task_name,
    )
    if not popped:
        raise HTTPException(status_code=409, detail="No timeEntries to undo")

    # Recompute project rollup so the dock 🍅 stays in sync.
    tasks = _scan_tasks(slug)
    est_total = sum(t["est_pomodoros"] for t in tasks)
    actual_total = sum(t["actual_pomodoros"] for t in tasks)
    update_frontmatter(
        vault_root=get_vault_path(),
        slug=slug,
        patch={"pomodoro": {"est_total": est_total, "actual_total": actual_total}},
    )

    if "application/json" in accept:
        task_actual = next((t["actual_pomodoros"] for t in tasks if t["name"] == task_name), 0)
        return {
            "task_name": task_name,
            "task_actual": task_actual,
            "project_actual_total": actual_total,
            "project_est_total": est_total,
        }
    return _redirect_back(slug, "brief")


@page_router.post("/projects/{slug}/review/{persona}")
async def projects_review_run(
    request: Request,
    slug: str,
    persona: str,
    nakama_auth: str | None = Cookie(None),
):
    """Run a persona review on the project's current hook (storyteller) or
    script body (coach). Appends the result to ``reviews.{persona}`` list
    (v2 list-shape per ADR-031 v2 panel push).

    Reviews are **advisory, not a publishing gate** (ADR-031 D8).
    """
    if not check_auth(nakama_auth):
        return RedirectResponse(f"/login?next=/bridge/projects/{slug}", status_code=302)

    if persona not in ("storyteller", "coach"):
        raise HTTPException(status_code=400, detail=f"unknown persona: {persona!r}")

    slug = normalize_slug(slug)

    try:
        entry = _indexer().get(slug)
    except ProjectNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc

    try:
        if persona == "storyteller":
            result = review_hook(entry.hook_text)
        else:  # coach
            body_md = _indexer().load_body(slug)
            result = review_script(body_md)
    except ProjectReviewError as exc:
        logger.warning("review dispatch failed: slug=%s persona=%s err=%s", slug, persona, exc)
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except Exception as exc:  # noqa: BLE001 — surface unexpected LLM/network failures
        logger.exception("review dispatch crashed: slug=%s persona=%s", slug, persona)
        raise HTTPException(status_code=500, detail=str(exc)) from exc

    append_review(
        vault_root=get_vault_path(),
        slug=slug,
        persona=persona,
        review=result.as_dict(),
    )

    return _redirect_back(slug, "review")


# ── Helpers ────────────────────────────────────────────────────────────────


def _write_pomodoro_entry(*, slug: str, task_name: str, now_minus_minutes: int) -> str | None:
    """Append a synthetic timeEntry + recompute project pomodoro rollup.

    Returns the task's status after the call (may be auto-flipped from
    ``to-do`` to ``doing`` per ADR-031 §F1 plan B — first 🍅 triggers
    "work started" transition; never auto-flips to ``done``).
    """
    vault_root = get_vault_path()
    # Snapshot status BEFORE writing the entry — used by the auto-flip below.
    pre_status = read_task_status(vault_root=vault_root, project_slug=slug, task_name=task_name)

    now = datetime.now(ZoneInfo("Asia/Taipei"))
    start = now.timestamp() - now_minus_minutes * 60
    start_dt = datetime.fromtimestamp(start, tz=ZoneInfo("Asia/Taipei"))
    start_iso = start_dt.isoformat(timespec="seconds")
    end_iso = now.isoformat(timespec="seconds")

    append_timeentry(
        vault_root=vault_root,
        project_slug=slug,
        task_name=task_name,
        start_iso=start_iso,
        end_iso=end_iso,
    )

    # Auto-flip to-do → doing (ADR-031 §F1 plan B). Only flip on the
    # first +1🍅 (when prior status was exactly "to-do"); never overwrite
    # a manual `doing`/`done`/`paused`.
    new_status = pre_status
    if pre_status == "to-do":
        update_task_status(
            vault_root=vault_root,
            project_slug=slug,
            task_name=task_name,
            status="doing",
        )
        new_status = "doing"

    # Recompute pomodoro rollup from TaskNotes scan, write back to project frontmatter.
    tasks = _scan_tasks(slug)
    est_total = sum(t["est_pomodoros"] for t in tasks)
    actual_total = sum(t["actual_pomodoros"] for t in tasks)
    update_frontmatter(
        vault_root=vault_root,
        slug=slug,
        patch={"pomodoro": {"est_total": est_total, "actual_total": actual_total}},
    )
    return new_status


def _scan_tasks(slug: str) -> list[dict[str, Any]]:
    """Scan TaskNotes/Tasks/ for tasks belonging to this project.

    Returns: [{name, est_pomodoros, actual_pomodoros, status}, ...]
    """
    import yaml

    tasks_dir = get_vault_path() / "TaskNotes" / "Tasks"
    out: list[dict[str, Any]] = []
    if not tasks_dir.exists():
        return out

    prefix = f"{normalize_slug(slug)} - "
    for p in tasks_dir.iterdir():
        name = normalize_slug(p.name)
        if not (name.startswith(prefix) and name.endswith(".md")):
            continue
        try:
            raw = p.read_text(encoding="utf-8")
        except OSError:
            continue
        import re

        m = re.match(r"^---\n(.*?)\n---\n?(.*)$", raw, re.DOTALL)
        if not m:
            continue
        try:
            fm = yaml.safe_load(m.group(1)) or {}
        except yaml.YAMLError:
            continue
        if not isinstance(fm, dict):
            continue

        task_name = name[len(prefix) : -len(".md")]
        est = fm.get("預估🍅", 0)
        try:
            est_int = int(est) if est is not None else 0
        except (TypeError, ValueError):
            est_int = 0

        time_entries = fm.get("timeEntries") or []
        total_min = 0
        if isinstance(time_entries, list):
            for entry in time_entries:
                if not isinstance(entry, dict):
                    continue
                s = entry.get("startTime")
                e = entry.get("endTime")
                if not (s and e):
                    continue
                try:
                    sdt = datetime.fromisoformat(str(s).replace("Z", "+00:00"))
                    edt = datetime.fromisoformat(str(e).replace("Z", "+00:00"))
                    total_min += (edt - sdt).total_seconds() / 60
                except ValueError:
                    continue
        actual = int(total_min // POMODORO_MINUTES)

        out.append(
            {
                "name": task_name,
                "est_pomodoros": est_int,
                "actual_pomodoros": actual,
                "status": str(fm.get("status") or "to-do"),
            }
        )

    # Sort by canonical default-task order if present
    canonical_order = {
        "Pre-production": 0,
        "Filming": 1,
        "Post-production": 2,
        "Prep & Booking": 0,
        "Recording": 1,
        "Edit & Publish": 2,
    }
    out.sort(key=lambda t: (canonical_order.get(t["name"], 99), t["name"]))
    return out


__all__ = ["page_router"]
