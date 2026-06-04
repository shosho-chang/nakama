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

import asyncio
import hashlib
import json
import os
import re
from datetime import datetime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Cookie, Form, Header, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates

from agents.robin.kb_search import search_kb
from agents.zoro.keyword_research import research_keywords
from shared.config import get_vault_path
from shared.lifeos_writer import (
    CONTENT_TYPES,
    ProjectExistsError,
    create_project_with_tasks,
    default_task_names,
)
from shared.log import get_logger
from shared.markdown import render_markdown
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
    update_marked_section,
    update_research_block,
    update_task_status,
)
from thousand_sunny.auth import check_auth
from thousand_sunny.routers.bridge_project_thumbnails import (
    prepare_existing_ideas_for_template,
)

logger = get_logger("nakama.web.bridge_projects")

page_router = APIRouter(prefix="/bridge", tags=["bridge-projects"])

_TEMPLATE_DIR = Path(__file__).resolve().parent.parent / "templates" / "bridge"
_templates = Jinja2Templates(directory=str(_TEMPLATE_DIR))

# Tab definitions: (slug, label_en, label_zh) — ADR-031 D4
# Order: Brief → Title&Thumb (Zoro keywords + titles) → Research (Robin KB
# synthesis + DR) → Hook → Script → Review → Publish. Zoro's output feeds
# Robin's KB query and DR prompt, so Zoro precedes Research.
TABS: tuple[tuple[str, str, str], ...] = (
    ("brief", "Brief", "簡介"),
    ("title-thumbnail", "Title & Thumbnail", "標題與縮圖"),
    ("research", "Research", "研究"),
    ("hook", "Hook", "鉤子"),
    ("script", "Script", "腳本"),
    ("review", "Review", "審查"),
    ("publish", "Publish", "發布"),
)
TAB_SLUGS = tuple(t[0] for t in TABS)

# Pomodoro duration in minutes — fixed in PR1 v1 (configurable later)
POMODORO_MINUTES = 25
DEEP_POMODORO_MINUTES = 75  # 🤩 super-focus block (unified dock 25/75 toggle)


# ADR-031 PR3: research result cache lives in repo data dir (gitignored). Sidecar
# JSON per project keeps the rich Zoro/Robin payload so a page refresh can
# re-render the tab without re-running the agent. Vault stays human-friendly
# (frontmatter + markdown tables only); the structured copy is local-only.
# NAKAMA_RESEARCH_CACHE_DIR env override lets tests redirect to tmp_path so
# unit-test runs don't pollute the real ``data/research_cache/`` directory.
def _resolve_cache_root() -> Path:
    override = os.environ.get("NAKAMA_RESEARCH_CACHE_DIR")
    if override:
        return Path(override)
    return Path(__file__).resolve().parent.parent.parent / "data" / "research_cache"


def _shosho_asset_version() -> str:
    """8-char hash of design-system CSS this page links — cache-bust friendly."""
    static_dir = Path(__file__).resolve().parent.parent / "static" / "shosho"
    h = hashlib.sha1()
    for css in (
        "tokens.css",
        "bridge.css",
        "bridge-pages.css",
        "pomodoro-dock.css",
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

    # T&T = title + thumbnail filled, plus Zoro keyword research run.
    has_titles = bool(entry.title_candidates)
    has_thumb = bool(entry.thumbnail_concept.strip())
    raw_fm = entry.raw_frontmatter if isinstance(entry.raw_frontmatter, dict) else {}
    has_zoro = bool(raw_fm.get("keyword_research_at"))
    filled = sum([has_titles, has_thumb, has_zoro])
    if filled == 3:
        out["title-thumbnail"] = "✓"
    elif filled >= 1:
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

    # ADR-031 PR3: hydrate Research tab from sidecar cache if present, so a page
    # refresh re-renders prior Zoro / Robin results without re-running the agent.
    cached_zoro = _load_cache(entry.slug, "zoro")
    cached_kb = _load_cache(entry.slug, "kb")
    cached_synth = _load_cache(entry.slug, "synthesis")
    cached_dr = _load_cache(entry.slug, "dr")
    if cached_zoro:
        zoro_result = cached_zoro.get("result") or {}
        videos = zoro_result.get("trending_videos") or []
        _enrich_video_embeds(videos)
        long_v, short_v = _split_long_short_videos(videos)
        zoro_result["long_videos"] = long_v
        zoro_result["short_videos"] = short_v

    # Pre-render markdown → HTML for synthesis & DR cache so the template
    # doesn't need to call render_markdown (Jinja doesn't have it; doing it
    # here keeps the partial dumb).
    if cached_synth and cached_synth.get("synthesis_md"):
        cached_synth["synthesis_html"] = render_markdown(
            cached_synth["synthesis_md"], wikilink_resolver=None
        )
    if cached_dr and cached_dr.get("report_md"):
        cached_dr["report_html"] = render_markdown(cached_dr["report_md"], wikilink_resolver=None)

    # Pre-parse existing thumbnail ideas so the tab template can use the same
    # editable-card partial that HTMX swaps target. Each entry has shape
    # {index, raw, parsed: dict|None, parse_error: str|None}.
    existing_thumb_ideas = prepare_existing_ideas_for_template(
        entry.raw_frontmatter if isinstance(entry.raw_frontmatter, dict) else {}
    )

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
            "cached_zoro": cached_zoro,
            "cached_kb": cached_kb,
            "cached_synth": cached_synth,
            "cached_dr": cached_dr,
            "existing_thumb_ideas": existing_thumb_ideas,
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

    # Panel #10 (ADR-031 v2): if the owner just flipped status→published while
    # other tabs are still incomplete, write an audit entry so the soft gate
    # has a paper trail (not just a dismissible toast). Bridge ops surface
    # can summarize weekly publish-with-incomplete counts off this.
    if field == "status" and value == "published":
        _audit_publish_decision(slug)

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
    mode: str = Form("pomodoro"),  # 'pomodoro' (25min 🍅) | 'deep' (75min 🤩) — unified dock
):
    """Pomodoro timer state transitions.

    ``complete`` writes a timeEntry to the active task and recomputes the project
    pomodoro rollup; the block length follows ``mode`` (25-min 🍅 pomodoro or
    75-min 🤩 super-focus — the unified dock's 25/75 toggle). ``start`` and
    ``cancel`` are no-ops (timer state lives in the browser).
    """
    if not check_auth(nakama_auth):
        return RedirectResponse(f"/login?next=/bridge/projects/{slug}", status_code=302)

    if action not in ("start", "complete", "cancel"):
        raise HTTPException(status_code=400, detail=f"unknown action: {action!r}")

    slug = normalize_slug(slug)

    if action == "complete":
        if not task_name.strip():
            raise HTTPException(status_code=400, detail="task_name required for complete")
        minutes = DEEP_POMODORO_MINUTES if mode == "deep" else POMODORO_MINUTES
        _write_pomodoro_entry(
            slug=slug,
            task_name=task_name.strip(),
            now_minus_minutes=minutes,
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


def _audit_publish_decision(slug: str) -> None:
    """Log a publish-decision entry to ``state.db api_calls.scope_json``.

    ADR-031 v2 panel #10 adopt-with-modification: turn the soft gate from
    a dismissible toast into a persisted decision. Schema:

        {"decision": "published_with_incomplete" | "published",
         "project": slug,
         "incomplete_tabs": [<tab_slug>, ...]}

    Indexer failures and DB failures both swallow + log — the publish itself
    has already succeeded and must not be rolled back by an audit-log hiccup.
    """
    try:
        idx = _indexer()
        entry = idx.get(slug)
        body_md = idx.load_body(slug)
        tab_status_map = _tab_status(entry, body_md)
    except Exception:  # noqa: BLE001
        logger.exception("audit_publish_decision: indexer failure slug=%s", slug)
        tab_status_map = {}

    incomplete = sorted(s for s, st in tab_status_map.items() if st != "✓" and s != "publish")

    scope = {
        "decision": "published_with_incomplete" if incomplete else "published",
        "project": slug,
        "incomplete_tabs": incomplete,
    }

    try:
        from shared.state import record_api_call

        record_api_call(
            agent="bridge",
            model="(audit-publish-decision)",
            input_tokens=0,
            output_tokens=0,
            scope_json=json.dumps(scope, ensure_ascii=False),
        )
    except Exception:  # noqa: BLE001
        logger.exception("audit_publish_decision: record_api_call failed slug=%s", slug)


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
                "slug": name[: -len(".md")],  # full file stem → weekly task-page link
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


# ── Research endpoints (ADR-031 PR3) ──────────────────────────────────────


@page_router.post("/projects/{slug}/research/keyword")
async def projects_research_keyword(
    request: Request,
    slug: str,
    nakama_auth: str | None = Cookie(None),
):
    """Run Zoro keyword research for this project, in-place.

    Reads ``entry.search_topic`` + ``content_type``, runs the 10-source
    bilingual collector + Claude synthesis, then persists:

    - Frontmatter: ``keywords: [...]`` (core keywords) + ``keyword_research_at: ISO ts``
    - Body marker section ``nakama:research:zoro``: rich rendering
      (title suggestions, trending videos, social posts, analysis summary)

    Returns an HTML fragment for inline injection into the Research tab.
    """
    if not check_auth(nakama_auth):
        return RedirectResponse(f"/login?next=/bridge/projects/{slug}", status_code=302)

    slug = normalize_slug(slug)
    try:
        entry = _indexer().get(slug)
    except ProjectNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc

    # raw_frontmatter.get vs entry.search_topic — the indexer falls back to
    # slug when the field is missing, but for the "did the user fill this?"
    # gate we need the literal value, not the display fallback.
    raw_topic = ""
    if isinstance(entry.raw_frontmatter, dict):
        raw_topic = str(entry.raw_frontmatter.get("search_topic") or "").strip()
    topic = raw_topic
    if not topic:
        raise HTTPException(
            status_code=400,
            detail="search_topic 是空的；先到 Brief 填一行短主題再試。",
        )

    content_type = entry.content_type if entry.content_type in ("youtube", "blog") else "youtube"

    try:
        result = await asyncio.to_thread(research_keywords, topic, content_type)
    except Exception as exc:  # noqa: BLE001 — surface failures cleanly
        logger.exception("zoro keyword research failed: slug=%s topic=%s", slug, topic)
        raise HTTPException(status_code=500, detail=f"Zoro 失敗：{exc}") from exc

    timestamp = datetime.now(ZoneInfo("Asia/Taipei")).isoformat(timespec="seconds")
    # Zoro returns keywords as a list of dicts (keyword/keyword_en/search_volume/
    # competition/opportunity/reason/source). Frontmatter persistence: keep the
    # full structured dicts so downstream callers (Robin KB, scripts) can read
    # them. The flat string list (`keywords_simple`) is just for the
    # search_topic-based Robin fallback's query construction.
    keywords_struct = list(result.get("keywords", []))
    videos = result.get("trending_videos") or []
    _enrich_video_embeds(videos)
    long_videos, short_videos = _split_long_short_videos(videos)
    result["long_videos"] = long_videos
    result["short_videos"] = short_videos
    md_block = _render_zoro_markdown(result, timestamp)

    try:
        update_research_block(
            vault_root=get_vault_path(),
            slug=slug,
            frontmatter_patch={
                "keywords": keywords_struct,
                "keyword_research_at": timestamp,
            },
            marker="nakama:research:zoro",
            content=md_block,
        )
    except ProjectWriteError as exc:
        logger.exception("zoro write-back failed: slug=%s", slug)
        raise HTTPException(status_code=500, detail=str(exc)) from exc

    # Sidecar cache for page-refresh persistence (best-effort; vault md is canonical).
    _save_cache(
        slug,
        "zoro",
        {
            "result": result,
            "timestamp": timestamp,
            "topic": topic,
            "content_type": content_type,
        },
    )

    return _templates.TemplateResponse(
        request,
        "projects/_research_zoro_result.html",
        {
            "result": result,
            "timestamp": timestamp,
            "topic": topic,
            "content_type": content_type,
        },
    )


@page_router.post("/projects/{slug}/research/kb")
async def projects_research_kb(
    request: Request,
    slug: str,
    nakama_auth: str | None = Cookie(None),
):
    """Run Robin KB search for this project, in-place.

    Query selection priority:
      1. ``keywords[]`` frontmatter (set by Zoro) — joined with spaces
      2. ``search_topic`` fallback

    Uses ``purpose="youtube"`` lens (Tier C is content creation context).
    Persists results to body marker section ``nakama:research:kb``.
    """
    if not check_auth(nakama_auth):
        return RedirectResponse(f"/login?next=/bridge/projects/{slug}", status_code=302)

    slug = normalize_slug(slug)
    try:
        entry = _indexer().get(slug)
    except ProjectNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc

    raw_fm = entry.raw_frontmatter if isinstance(entry.raw_frontmatter, dict) else {}
    raw_keywords = raw_fm.get("keywords")
    keywords_list: list[str] = []
    if isinstance(raw_keywords, list):
        # Zoro now writes keywords as list[dict]; older payloads may be list[str].
        # Extract the zh/en label whichever shape we get.
        for k in raw_keywords:
            if isinstance(k, dict):
                label = str(k.get("keyword") or k.get("keyword_en") or "").strip()
            else:
                label = str(k).strip()
            if label:
                keywords_list.append(label)

    if keywords_list:
        query = " ".join(keywords_list)
        query_label = "、".join(keywords_list)
    else:
        # Mirror the keyword endpoint: use raw_frontmatter, not the indexer's
        # slug-fallback search_topic — otherwise an unfilled Brief still
        # passes through with the slug as bogus query.
        raw_topic = str(raw_fm.get("search_topic") or "").strip()
        query = raw_topic
        query_label = raw_topic

    if not query:
        raise HTTPException(
            status_code=400,
            detail="沒有可搜尋的關鍵字；先跑 Zoro 或在 Brief 填 search_topic。",
        )

    try:
        # Over-fetch (top_k=20) so the post-filter still has enough hits to
        # show after dropping nav/dup/wikilink-only chunks.
        raw_hits = await asyncio.to_thread(
            search_kb,
            query,
            get_vault_path(),
            top_k=20,
            purpose="youtube",
            engine="hybrid",
        )
    except Exception as exc:  # noqa: BLE001 — surface failures cleanly
        logger.exception("robin kb search failed: slug=%s query=%s", slug, query)
        raise HTTPException(status_code=500, detail=f"Robin 失敗：{exc}") from exc

    # Drop "Related Concepts" / wikilink-only chunks + dedupe by page.
    hits = _filter_low_value_hits(raw_hits)[:12]

    # Hybrid hits don't include LLM-generated relevance_reason. For Tier C
    # research the user wants angle suggestions, so we batch-call Haiku once
    # with all hits and merge the responses back into each hit dict. Haiku
    # also tags each hit with strong / weak / off-topic so we can drop the
    # off-topic ones before rendering.
    try:
        await asyncio.to_thread(_generate_kb_angles, hits, entry.one_sentence or query)
    except Exception:  # noqa: BLE001 — angle is enrichment; never block the result
        logger.exception("kb angle generation failed (non-fatal): slug=%s", slug)

    # Drop LLM-tagged off-topic hits. weak ones survive but get a UI marker.
    hits = [h for h in hits if h.get("relevance_tier") != "off-topic"]

    timestamp = datetime.now(ZoneInfo("Asia/Taipei")).isoformat(timespec="seconds")
    md_block = _render_kb_markdown(hits, timestamp, query_label)

    try:
        update_marked_section(
            vault_root=get_vault_path(),
            slug=slug,
            marker="nakama:research:kb",
            content=md_block,
        )
    except ProjectWriteError as exc:
        logger.exception("kb write-back failed: slug=%s", slug)
        raise HTTPException(status_code=500, detail=str(exc)) from exc

    _save_cache(
        slug,
        "kb",
        {
            "hits": hits,
            "timestamp": timestamp,
            "query_label": query_label,
            "used_keywords": bool(keywords_list),
        },
    )

    return _templates.TemplateResponse(
        request,
        "projects/_research_kb_result.html",
        {
            "hits": hits,
            "timestamp": timestamp,
            "query_label": query_label,
            "used_keywords": bool(keywords_list),
        },
    )


@page_router.post("/projects/{slug}/research/synthesis")
async def projects_research_synthesis(
    request: Request,
    slug: str,
    nakama_auth: str | None = Cookie(None),
):
    """Robin KB synthesis — compile filtered KB hits into a coherent summary.

    Reads the sidecar KB cache (must run Robin first), the project brief,
    and the Zoro keyword cache if present. Calls Claude (heavier model than
    the Haiku angle pass) to produce a 600-800 字 markdown summary with
    sections: 機制 / 證據 / 爭議 / 角度, inline ``[[wikilink]]`` citations
    pointing back to the source page.

    Persisted to body marker ``nakama:research:kb-synthesis`` + sidecar
    cache for refresh-persistence.
    """
    if not check_auth(nakama_auth):
        return RedirectResponse(f"/login?next=/bridge/projects/{slug}", status_code=302)

    slug = normalize_slug(slug)
    try:
        entry = _indexer().get(slug)
    except ProjectNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc

    kb_cache = _load_cache(slug, "kb")
    hits = (kb_cache or {}).get("hits") or []
    if not hits:
        raise HTTPException(
            status_code=400,
            detail="沒有 KB 命中可摘要；先按「📚 Robin 從 KB 抓素材」再來。",
        )

    zoro_cache = _load_cache(slug, "zoro")
    zoro_keywords: list[Any] = []
    if zoro_cache and isinstance(zoro_cache.get("result"), dict):
        zoro_keywords = list(zoro_cache["result"].get("keywords") or [])

    brief = {
        "one_sentence": entry.one_sentence or "",
        "search_topic": entry.search_topic or slug,
        "content_type": entry.content_type or "youtube",
    }

    try:
        synthesis_md = await asyncio.to_thread(_synthesize_kb_hits, hits, brief, zoro_keywords)
    except Exception as exc:  # noqa: BLE001 — surface failures cleanly
        logger.exception("synthesis failed: slug=%s", slug)
        raise HTTPException(status_code=500, detail=f"Robin 摘要失敗：{exc}") from exc

    if not synthesis_md.strip():
        raise HTTPException(status_code=500, detail="Robin 回了空字串；請重試。")

    timestamp = datetime.now(ZoneInfo("Asia/Taipei")).isoformat(timespec="seconds")
    block_body = f"_Robin 摘要 · {timestamp} · 用了 {len(hits)} 個 KB 片段_\n\n{synthesis_md}"
    try:
        update_marked_section(
            vault_root=get_vault_path(),
            slug=slug,
            marker="nakama:research:kb-synthesis",
            content=block_body,
        )
    except ProjectWriteError as exc:
        logger.exception("synthesis write-back failed: slug=%s", slug)
        raise HTTPException(status_code=500, detail=str(exc)) from exc

    _save_cache(
        slug,
        "synthesis",
        {
            "synthesis_md": synthesis_md,
            "timestamp": timestamp,
            "hits_used": len(hits),
        },
    )

    synthesis_html = render_markdown(synthesis_md, wikilink_resolver=None)

    return _templates.TemplateResponse(
        request,
        "projects/_research_synthesis_result.html",
        {
            "synthesis_html": synthesis_html,
            "synthesis_md": synthesis_md,
            "timestamp": timestamp,
            "hits_used": len(hits),
        },
    )


@page_router.post("/projects/{slug}/research/dr-prompt")
async def projects_research_dr_prompt(
    request: Request,
    slug: str,
    nakama_auth: str | None = Cookie(None),
):
    """Build optimized Deep Research prompt from cached research context.

    Returns JSON ``{"prompt": "..."}`` so the JS can copy to clipboard +
    open ChatGPT/Claude.ai in a new tab. Does NOT call any LLM — pure
    template assembly from brief + Zoro cache + synthesis cache.
    """
    if not check_auth(nakama_auth):
        from fastapi.responses import JSONResponse

        return JSONResponse({"detail": "auth required"}, status_code=401)

    slug = normalize_slug(slug)
    try:
        entry = _indexer().get(slug)
    except ProjectNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc

    zoro_cache = _load_cache(slug, "zoro") or {}
    synth_cache = _load_cache(slug, "synthesis") or {}

    prompt = _build_dr_prompt(entry, zoro_cache, synth_cache)

    from fastapi.responses import JSONResponse

    return JSONResponse({"prompt": prompt})


@page_router.post("/projects/{slug}/research/dr-report")
async def projects_research_dr_report(
    request: Request,
    slug: str,
    report: str = Form(""),
    source: str = Form("manual"),
    nakama_auth: str | None = Cookie(None),
):
    """Save pasted-back Deep Research report to vault marker section.

    ``source`` is a free-text label (e.g. "chatgpt-deepresearch" /
    "claude-extended"); recorded in the block header so the user can tell
    which tool produced it. No LLM call — just markdown write-back.
    """
    if not check_auth(nakama_auth):
        return RedirectResponse(f"/login?next=/bridge/projects/{slug}", status_code=302)

    slug = normalize_slug(slug)
    try:
        _indexer().get(slug)
    except ProjectNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc

    body = (report or "").strip()
    if not body:
        raise HTTPException(status_code=400, detail="貼上的 DR 報告是空的。")

    timestamp = datetime.now(ZoneInfo("Asia/Taipei")).isoformat(timespec="seconds")
    safe_source = re.sub(r"[^a-zA-Z0-9._-]+", "-", source.strip() or "manual")[:40]
    block_body = f"_Deep Research · {timestamp} · 來源 {safe_source}_\n\n{body}"

    try:
        update_marked_section(
            vault_root=get_vault_path(),
            slug=slug,
            marker="nakama:research:dr",
            content=block_body,
        )
    except ProjectWriteError as exc:
        logger.exception("dr-report write-back failed: slug=%s", slug)
        raise HTTPException(status_code=500, detail=str(exc)) from exc

    _save_cache(
        slug,
        "dr",
        {
            "report_md": body,
            "timestamp": timestamp,
            "source": safe_source,
        },
    )

    report_html = render_markdown(body, wikilink_resolver=None)
    return _templates.TemplateResponse(
        request,
        "projects/_research_dr_result.html",
        {
            "report_html": report_html,
            "report_md": body,
            "timestamp": timestamp,
            "source": safe_source,
        },
    )


def _cache_path(slug: str, kind: str) -> Path:
    """Cache file path: ``data/research_cache/{slug}.{kind}.json``.

    ``kind`` ∈ {"zoro", "kb"}. Caller decides whether to read or write.
    Re-resolves the cache root each call so tests can patch the env var.
    """
    return _resolve_cache_root() / f"{slug}.{kind}.json"


def _save_cache(slug: str, kind: str, payload: dict[str, Any]) -> None:
    """Best-effort write — failures don't break the user-facing render.

    Sidecar JSON is purely a UX nicety (page-refresh persistence); the
    canonical record is in vault md (frontmatter + marker section).
    """
    path = _cache_path(slug, kind)
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
    except OSError:
        logger.warning("research cache write failed: %s", path)


def _load_cache(slug: str, kind: str) -> dict[str, Any] | None:
    """Return cached payload dict or ``None`` if absent/corrupt.

    Corrupt-cache silently returns None so a manual edit / partial write
    doesn't break the page render.
    """
    path = _cache_path(slug, kind)
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        logger.warning("research cache read failed: %s", path)
        return None


_YT_ID_RE = re.compile(r"(?:watch\?v=|shorts/|youtu\.be/|/embed/)([A-Za-z0-9_-]{11})")
_SHORTS_HINT_RE = re.compile(r"/shorts/|#shorts", re.IGNORECASE)


def _enrich_video_embeds(videos: list[dict]) -> None:
    """In-place: add ``embed_id`` to each video dict by parsing the YouTube URL.

    Supports ``watch?v=XXX``, ``shorts/XXX``, ``youtu.be/XXX`` and ``/embed/XXX``.
    Non-YouTube urls get ``embed_id = ""`` so the template falls back to a link.
    """
    for v in videos:
        url = str(v.get("url") or "")
        m = _YT_ID_RE.search(url)
        v["embed_id"] = m.group(1) if m else ""


_LOW_VALUE_HEADINGS = frozenset(
    {
        "Related Concepts",
        "Related",
        "See Also",
        "References",
        "Sources",
        "引用來源",
        "參考來源",
        "相關概念",
        "相關",
        "延伸閱讀",
    }
)


def _filter_low_value_hits(hits: list[dict]) -> list[dict]:
    """Drop pure-navigation chunks + dedupe by source page.

    Three filters:
      1. Heading-based: known nav buckets ("Related Concepts" / "Sources" /
         "References" / "引用來源" etc.) — these are wikilink lists by design
      2. Density-based: chunks with ≥3 wikilinks AND <60 chars of non-link
         prose — covers the same pattern under unexpected heading names
      3. Page-dedup: keep highest-ranked chunk per ``path``. Hybrid returns
         sorted by rrf_score so first encounter wins. Trade-off: lose
         per-section granularity, but better than showing the same source
         3 times with marginal chunks each.
    """
    out: list[dict] = []
    seen_pages: set[str] = set()
    for h in hits:
        heading = str(h.get("heading") or "").strip()
        if heading in _LOW_VALUE_HEADINGS:
            continue
        chunk = str(h.get("chunk_text") or "")
        link_count = chunk.count("[[")
        prose_only = re.sub(r"\[\[[^\]]+\]\]", "", chunk).strip()
        if link_count >= 3 and len(prose_only) < 60:
            continue
        path = str(h.get("path") or "")
        if path and path in seen_pages:
            continue
        seen_pages.add(path)
        out.append(h)
    return out


def _generate_kb_angles(hits: list[dict], topic: str) -> None:
    """In-place: enrich each KB hit with an LLM-generated ``angle``.

    Hybrid search returns chunk_text + heading + title but no semantic
    "how does this help my project" reason. We batch all hits in a single
    Haiku call so the user gets concrete angle suggestions per hit instead
    of just the raw chunk text. Non-fatal — caller catches exceptions so
    a generation failure still returns the hits with empty angle strings.
    """
    if not hits or not topic.strip():
        return

    from shared.llm import ask
    from shared.llm_context import set_current_agent

    excerpts: list[str] = []
    for i, h in enumerate(hits, start=1):
        title = str(h.get("title") or "?")
        heading = str(h.get("heading") or "")
        chunk = str(h.get("chunk_text") or h.get("preview") or "")[:500]
        head_suffix = f" / {heading}" if heading else ""
        excerpts.append(f"[{i}] {title}{head_suffix}\n{chunk}")

    prompt = (
        "你正在協助一位 health/longevity 內容創作者，幫他評估 KB 中已有的素材如何"
        f"切入他正在做的專案主題。\n\n專案主題：{topic}\n\n"
        "以下是 KB 中相關的片段：\n\n"
        + "\n\n".join(excerpts)
        + "\n\n請為每個片段判斷 relevance，分三級：\n\n"
        "- **off-topic**：片段的主體在講"
        "**別的補充劑 / 別的疾病 / 別的訓練法 / 別的營養素**，"
        "雖然可能被向量搜尋抓到，但本質與專案主題無直接證據鏈。"
        "**範例**：專案主題是「肌酸」，片段在講薑黃素的抗發炎機制 → off-topic"
        "（即使你能想到「兩者都跟肌肉恢復有關」也算 off-topic，"
        "因為片段本身沒提到肌酸）；專案主題是「肌酸」，"
        "片段在講骨肌減少症診斷標準 → off-topic（片段沒提到肌酸的角色）。\n"
        "- **weak**：片段**有明確提到主題本身**"
        "（例如真的講到 creatine / creatine kinase / PCr），"
        "但只是順帶一句、列在表格中、或沒有具體機轉/數字/論點。\n"
        "- **strong**：片段直接論述主題的機轉、效益、劑量、爭議，"
        "含具體數字/實驗/論點。\n\n"
        "**關鍵原則**：判斷 off-topic 時，"
        "看「片段文字本身有沒有提到主題的核心詞」，"
        "不要用「我可以幫他想到一個切入角度」來救援；"
        "如果你自己都要「補充說明」「對比參考」「框架延伸」才能連到主題，"
        "就是 off-topic。\n\n"
        "為 relevance != off-topic 的片段，寫一句話「這段如何切入專案主題」— "
        "給**具體**可操作的切入點，引用片段中的關鍵數字或論點。"
        "不要空泛詞（「相關」、「有幫助」、「值得參考」、「可作為對比」、"
        "「補充說明」、「框架可用於」一律不用）。"
        "每句限 60 字內。off-topic 的 angle 留空字串。\n\n"
        "用繁體中文回答。JSON 格式回覆（純 JSON，不加 markdown 圍欄）：\n"
        '[{"idx": 1, "relevance": "strong|weak|off-topic", "angle": "..."}, ...]'
    )

    set_current_agent("robin")
    raw = ask(
        prompt,
        model="claude-haiku-4-5-20251001",
        max_tokens=2048,
        temperature=0.3,
    )
    cleaned = re.sub(r"^```(?:json)?\s*\n?", "", raw.strip())
    cleaned = re.sub(r"\n?```\s*$", "", cleaned)
    m = re.search(r"\[[\s\S]*\]", cleaned)
    if not m:
        return
    try:
        items = json.loads(m.group())
    except json.JSONDecodeError:
        return
    if not isinstance(items, list):
        return
    for item in items:
        if not isinstance(item, dict):
            continue
        idx = item.get("idx")
        if not isinstance(idx, int) or not (1 <= idx <= len(hits)):
            continue
        angle = str(item.get("angle") or "").strip()
        relevance = str(item.get("relevance") or "").strip().lower()
        if angle:
            hits[idx - 1]["relevance_reason"] = angle
        if relevance in {"strong", "weak", "off-topic"}:
            hits[idx - 1]["relevance_tier"] = relevance


def _wikilink_target_from_path(path: str, fallback: str = "") -> str:
    """Convert a KB path like ``KB/Wiki/Concepts/creatine supplementation``
    into the wikilink target ``creatine supplementation``. Falls back to
    ``fallback`` (usually the hit title) when path is empty.
    """
    if not path:
        return fallback
    last = path.rsplit("/", 1)[-1]
    if last.endswith(".md"):
        last = last[:-3]
    return last or fallback


def _synthesize_kb_hits(
    hits: list[dict],
    brief: dict[str, str],
    zoro_keywords: list[Any],
) -> str:
    """Call Claude to compile filtered KB hits into a coherent summary.

    Output is markdown with H2 sections (機制/證據/爭議/角度) and inline
    ``[[wikilink]]`` citations to source pages. Heavier model than the
    Haiku angle pass — synthesis quality is the user-facing value here.
    """
    from shared.llm import ask
    from shared.llm_context import set_current_agent

    excerpts: list[str] = []
    for i, h in enumerate(hits, start=1):
        title = str(h.get("title") or "?")
        heading = str(h.get("heading") or "")
        chunk = str(h.get("chunk_text") or h.get("preview") or "")[:600]
        path = str(h.get("path") or "")
        tier = str(h.get("relevance_tier") or "")
        wl = _wikilink_target_from_path(path, fallback=title)
        head_suffix = f" / {heading}" if heading else ""
        tier_tag = f" [{tier}]" if tier else ""
        excerpts.append(f"[{i}] [[{wl}]] · {title}{head_suffix}{tier_tag}\n{chunk}")

    kw_summary = ""
    if zoro_keywords:
        kw_list: list[str] = []
        for k in zoro_keywords[:8]:
            if isinstance(k, dict):
                label = str(k.get("keyword") or k.get("keyword_en") or "").strip()
            else:
                label = str(k).strip()
            if label:
                kw_list.append(label)
        if kw_list:
            kw_summary = "Zoro 抓的核心關鍵字：" + "、".join(kw_list) + "\n"

    one_sentence = brief.get("one_sentence", "")
    search_topic = brief.get("search_topic", "")
    content_type = brief.get("content_type", "youtube")

    prompt = (
        "你是 Robin，協助一位 health/longevity 內容創作者把 KB 中的素材 "
        "compile 成一篇可以直接拿來規劃腳本/文章的摘要。\n\n"
        f"專案主題：{search_topic}\n"
        f"專案一句話：{one_sentence}\n"
        f"產出形式：{content_type}\n"
        f"{kw_summary}\n"
        "以下是 KB 中經過 relevance 過濾的片段。"
        "每段標頭：`[編號] [[wikilink]] · 標題 / 子標 [tier]`。"
        "tier 是這段相對主題的關聯強度"
        "（strong = 直接論述、weak = 提到但不深入）。\n\n" + "\n\n".join(excerpts) + "\n\n"
        "請整合這些片段，產出一篇 **600-800 字** 的繁體中文摘要。\n\n"
        "**格式要求**：\n"
        "1. 必須有四個 H2 段落，順序固定：\n"
        "   ## 機制 — 主題在生理/生化/運作層面怎麼起作用。\n"
        "   ## 證據 — 引用具體實驗、劑量、人群、效果量。\n"
        "   ## 爭議 — 不同來源的矛盾、不確定性、邊界條件。\n"
        "   ## 角度 — 3-5 個可拍/可寫的具體切入點（bullet list），"
        "每點要說明切入點本身"
        "（不只是「劑量」、「副作用」這種題目，"
        "而是「以 20g/5天負荷期 vs 3g/30天慢速法的取捨」這種帶觀點的）。\n"
        "2. 每個論點 inline 用 `[[wikilink]]` 引用片段來源 — "
        "用片段標頭給你的 wikilink 名稱（雙方括號裡的字串）。\n"
        "3. 引用具體數字 / 實驗條件 / 人群 — 不空話。"
        "「有助於」、「值得參考」、「相當重要」一律不寫。\n"
        "4. 不要 paraphrase 每個片段；"
        "要在片段間建立連結、找矛盾、找互補、找空白。\n"
        "5. 若某片段 tier=weak 且實際上偏題，"
        "可以選擇不引用或只在「角度」段一筆提到當延伸觀察。\n"
        "6. 「機制」/「證據」/「爭議」段若 KB 中真的沒有資料支撐，"
        "就明寫「KB 中目前缺 X 角度的資料」— 不要編造。\n\n"
        "直接輸出 markdown 主體，**不要**加 ```` ``` ```` 圍欄，"
        "**不要**先講「以下是摘要」/「我為你整理了…」之類的話，"
        "第一行直接是 `## 機制`。"
    )

    set_current_agent("robin")
    # Opus 4.7 deprecated `temperature` — pass only what it still accepts.
    return ask(
        prompt,
        model="claude-opus-4-7",
        max_tokens=4096,
    )


def _build_dr_prompt(entry: Any, zoro_cache: dict, synth_cache: dict) -> str:
    """Compose a paste-ready Deep Research prompt from cached research.

    Pure template — no LLM. Used by the JS to populate clipboard + open
    ChatGPT/Claude.ai in a new tab so user can toggle DR + submit.
    """
    one_sentence = (getattr(entry, "one_sentence", "") or "").strip()
    search_topic = (getattr(entry, "search_topic", "") or "").strip()
    content_type = (getattr(entry, "content_type", "") or "youtube").strip()

    zoro_result = zoro_cache.get("result") if isinstance(zoro_cache, dict) else None
    keywords_zh: list[str] = []
    keywords_en: list[str] = []
    if isinstance(zoro_result, dict):
        for k in (zoro_result.get("keywords") or [])[:10]:
            if isinstance(k, dict):
                zh = str(k.get("keyword") or "").strip()
                en = str(k.get("keyword_en") or "").strip()
                if zh:
                    keywords_zh.append(zh)
                if en:
                    keywords_en.append(en)
            else:
                keywords_zh.append(str(k))

    trend_gaps_lines: list[str] = []
    if isinstance(zoro_result, dict):
        for gap in (zoro_result.get("trend_gaps") or [])[:5]:
            if isinstance(gap, dict):
                topic = str(gap.get("topic") or "").strip()
                en_signal = str(gap.get("en_signal") or "").strip()
                zh_status = str(gap.get("zh_status") or "").strip()
                if topic:
                    trend_gaps_lines.append(
                        f"- {topic}：EN 訊號「{en_signal}」、ZH 現況「{zh_status}」"
                    )

    synthesis_md = ""
    if isinstance(synth_cache, dict):
        synthesis_md = synth_cache.get("synthesis_md") or ""

    parts: list[str] = []
    parts.append(f"請幫我做一份深度研究報告，主題：「{search_topic}」。")
    if one_sentence:
        parts.append(f"\n我想表達的核心觀點：{one_sentence}")
    parts.append(
        f"\n預計產出形式：{content_type}。讀者是 30-50 歲關注健康與長壽的一般大眾，不是專業研究者。"
    )

    if keywords_zh or keywords_en:
        parts.append("\n## 我已知的關鍵字（請在報告中覆蓋這些角度）")
        if keywords_zh:
            parts.append("中文關鍵字：" + "、".join(keywords_zh))
        if keywords_en:
            parts.append("English keywords: " + ", ".join(keywords_en))

    if trend_gaps_lines:
        parts.append("\n## 趨勢落差（這些是 EN 圈在談但中文圈尚未鋪開的角度，請優先補）")
        parts.extend(trend_gaps_lines)

    if synthesis_md:
        parts.append("\n## 我自己 KB 已經整理出的摘要（請以此為基礎再延伸，不要重複）")
        parts.append(synthesis_md.strip())

    parts.append(
        "\n## 報告需求\n"
        "1. **覆蓋面**：機制 / 證據 / 爭議 / 實際應用，四個面向都要。\n"
        "2. **證據等級**：優先引用 systematic review、meta-analysis、RCT；"
        "觀察性研究與動物實驗請明確標註。\n"
        "3. **數字優先**：每個論點附具體數字"
        "（劑量、效果量、樣本數、p 值或 95% CI），"
        "不要寫「顯著」、「有效」這種模糊詞。\n"
        "4. **爭議**：找出近 3 年內仍有分歧的議題"
        "（不同人群、不同劑量、不同 endpoint 的結論差異）。\n"
        "5. **實用切入**：給 5-8 個我可以直接拿來做內容的角度，"
        "每個附 1-2 段背景＋為什麼一般人會關心。\n"
        "6. **可信來源**：所有引用標明出處"
        "（期刊名 + 年份 + DOI / PMID 或可驗證連結）。"
        "盡量避免引用一般媒體與健康部落格。\n"
        "7. **語言**：用繁體中文寫，專有名詞保留原文（首次出現後加中文翻譯）。"
    )

    return "\n".join(parts).strip()


def _split_long_short_videos(videos: list[dict]) -> tuple[list[dict], list[dict]]:
    """Split trending videos into (long-form, shorts) buckets.

    Detection: URL has ``/shorts/`` OR title contains ``#shorts``. YouTube
    Shorts URLs sometimes use ``/watch?v=`` (when shared from the Shorts UI),
    so title hashtag is a necessary backup signal.
    """
    longs: list[dict] = []
    shorts: list[dict] = []
    for v in videos:
        url = str(v.get("url") or "")
        title = str(v.get("title") or "")
        if _SHORTS_HINT_RE.search(url) or _SHORTS_HINT_RE.search(title):
            shorts.append(v)
        else:
            longs.append(v)
    return longs, shorts


def _md_escape_pipe(s: str) -> str:
    """Escape ``|`` chars in markdown table cells; collapse newlines."""
    return str(s).replace("|", r"\|").replace("\n", " ").strip()


def _render_zoro_markdown(result: dict, timestamp: str) -> str:
    """Render Zoro result as a markdown block for vault body.

    Uses Github-flavored markdown tables for keywords + trend gaps (Obsidian
    renders these natively in preview). Plain bullet lists for titles. Plain
    markdown only — no dataviewjs / code fences. Truncates list-heavy fields
    to keep project.md scannable; full data lives in
    ``shared/keyword_research_history_store``.
    """
    lines: list[str] = [f"## 🗝 Zoro 關鍵字研究（{timestamp}）", ""]

    keywords = result.get("keywords") or []
    if keywords:
        lines.append(f"### Core keywords ({len(keywords)})")
        lines.append("")
        lines.append("| 中文關鍵字 | EN | 搜尋量 | 競爭 | 機會 | 來源 | 理由 |")
        lines.append("|---|---|---|---|---|---|---|")
        for kw in keywords:
            if isinstance(kw, dict):
                lines.append(
                    "| {zh} | {en} | {sv} | {cp} | {op} | {src} | {rs} |".format(
                        zh=_md_escape_pipe(kw.get("keyword") or "—"),
                        en=_md_escape_pipe(kw.get("keyword_en") or "—"),
                        sv=_md_escape_pipe(kw.get("search_volume") or "—"),
                        cp=_md_escape_pipe(kw.get("competition") or "—"),
                        op=_md_escape_pipe(kw.get("opportunity") or "—"),
                        src=_md_escape_pipe(kw.get("source") or "—"),
                        rs=_md_escape_pipe(kw.get("reason") or ""),
                    )
                )
            else:
                lines.append(f"| {_md_escape_pipe(kw)} |  |  |  |  |  |  |")
        lines.append("")

    trend_gaps = result.get("trend_gaps") or []
    if trend_gaps:
        lines.append(f"### Trend gaps ({len(trend_gaps)})")
        lines.append("")
        lines.append("| 主題 | EN signal | ZH 現況 | 機會 |")
        lines.append("|---|---|---|---|")
        for gap in trend_gaps:
            if isinstance(gap, dict):
                lines.append(
                    "| {t} | {en} | {zh} | {op} |".format(
                        t=_md_escape_pipe(gap.get("topic") or "—"),
                        en=_md_escape_pipe(gap.get("en_signal") or ""),
                        zh=_md_escape_pipe(gap.get("zh_status") or ""),
                        op=_md_escape_pipe(gap.get("opportunity") or ""),
                    )
                )
            else:
                lines.append(f"| {_md_escape_pipe(gap)} |  |  |  |")
        lines.append("")

    yt_titles = [str(t) for t in (result.get("youtube_titles") or []) if t]
    if yt_titles:
        lines.append(f"### YouTube 標題建議 ({len(yt_titles)})")
        lines.append("")
        lines.append("| # | 標題 | 字數 |")
        lines.append("|---|---|---|")
        for i, t in enumerate(yt_titles, 1):
            lines.append(f"| {i} | {_md_escape_pipe(t)} | {len(t)} |")
        lines.append("")

    blog_titles = [str(t) for t in (result.get("blog_titles") or []) if t]
    if blog_titles:
        lines.append(f"### Blog 標題建議 ({len(blog_titles)})")
        lines.append("")
        lines.append("| # | 標題 | 字數 |")
        lines.append("|---|---|---|")
        for i, t in enumerate(blog_titles, 1):
            lines.append(f"| {i} | {_md_escape_pipe(t)} | {len(t)} |")
        lines.append("")

    summary = str(result.get("analysis_summary") or "").strip()
    if summary:
        lines.append("### Analysis")
        lines.append(summary)
        lines.append("")

    long_videos = result.get("long_videos") or []
    short_videos = result.get("short_videos") or []
    if long_videos:
        lines.append(f"### 📺 Long-form videos ({len(long_videos)})")
        for v in long_videos[:12]:
            views = int(v.get("views", 0) or 0)
            title = str(v.get("title") or "?")
            channel = str(v.get("channel") or "")
            url = str(v.get("url") or "")
            if url:
                lines.append(f"- [{title}]({url}) — {channel} · {views:,} views")
            else:
                lines.append(f"- {title} — {channel} · {views:,} views")
        lines.append("")
    if short_videos:
        lines.append(f"### 📱 Shorts ({len(short_videos)})")
        for v in short_videos[:12]:
            views = int(v.get("views", 0) or 0)
            title = str(v.get("title") or "?")
            channel = str(v.get("channel") or "")
            url = str(v.get("url") or "")
            if url:
                lines.append(f"- [{title}]({url}) — {channel} · {views:,} views")
            else:
                lines.append(f"- {title} — {channel} · {views:,} views")
        lines.append("")

    social = result.get("social_posts") or []
    if social:
        lines.append(f"### Social posts (top {min(len(social), 12)})")
        for p in social[:12]:
            platform = str(p.get("platform") or "?")
            handle = str(p.get("username") or p.get("subreddit") or "")
            text = str(p.get("text") or p.get("title") or "?")
            url = str(p.get("url") or "")
            text_short = text[:120] + ("…" if len(text) > 120 else "")
            label = f"{platform}/{handle}" if handle else platform
            if url:
                lines.append(f"- [{label}]({url}) — {text_short}")
            else:
                lines.append(f"- {label} — {text_short}")

    return "\n".join(lines).rstrip()


def _render_kb_markdown(hits: list[dict], timestamp: str, query_label: str) -> str:
    """Render Robin KB hits as a markdown block for vault body.

    Per hit emits: wikilink heading, LLM-generated angle line, and a
    collapsible ``<details>`` excerpt of the chunk_text so Obsidian preview
    keeps the section scannable but each chunk is readable on demand.
    """
    lines: list[str] = [
        f"## 📚 Robin KB 命中（{timestamp}）",
        "",
        f"_Query: {query_label} · {len(hits)} hits_",
        "",
    ]

    if not hits:
        lines.append("（沒有命中。試著補 Brief 的 search_topic 或先跑 Zoro 拿 keywords 再來。）")
        return "\n".join(lines).rstrip()

    by_type: dict[str, list[dict]] = {}
    for hit in hits:
        t = str(hit.get("type") or "unknown")
        by_type.setdefault(t, []).append(hit)

    type_label_zh = {
        "source": "Source · 來源文獻",
        "concept": "Concept · 概念",
        "entity": "Entity · 實體",
        "annotation": "Annotation · 劃線",
        "unknown": "Other",
    }
    for t in ("source", "concept", "entity", "annotation", "unknown"):
        if t not in by_type:
            continue
        lines.append(f"### {type_label_zh[t]}（{len(by_type[t])}）")
        lines.append("")
        for hit in by_type[t]:
            title = str(hit.get("title") or "?")
            path = str(hit.get("path") or "")
            heading = str(hit.get("heading") or "").strip()
            angle = str(hit.get("relevance_reason") or "").strip()
            chunk = str(hit.get("chunk_text") or hit.get("preview") or "").strip()

            link = f"[[{path}|{title}]]" if path else title
            head_suffix = f" — §{heading}" if heading else ""
            lines.append(f"**{link}{head_suffix}**")
            if angle:
                lines.append(f"  💡 {angle}")
            if chunk:
                # Obsidian doesn't render <details> blocks in Live Preview as
                # natively as HTML; use a blockquote so the excerpt stays
                # visually grouped and scannable in preview mode.
                excerpt = chunk[:400] + ("…" if len(chunk) > 400 else "")
                quoted = "\n".join(f"  > {ln}" for ln in excerpt.splitlines())
                lines.append(quoted)
            lines.append("")

    return "\n".join(lines).rstrip()


__all__ = ["page_router"]
