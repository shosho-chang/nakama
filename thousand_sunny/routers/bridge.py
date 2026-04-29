"""Bridge routes — memory management + cost dashboard (Phase 4).

V1 scope（見 ``docs/prds/phase-4-bridge-ui.md``）：

- Memory：列出 / 編輯 / 刪除 ``user_memories``（Phase 1-3 Nami 對修修的記憶）。
  Tier 3 ``memories`` 表（agent run 日記）**不在本頁範疇**。
- Cost：近 N 天 ``api_calls`` 的 agent × model 統計 + 時間序列 + USD 估算。
"""

from __future__ import annotations

import json
import os
import re
import threading
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Optional
from zoneinfo import ZoneInfo

from fastapi import (
    APIRouter,
    BackgroundTasks,
    Cookie,
    Depends,
    Form,
    HTTPException,
    Query,
    Request,
)
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel, Field, ValidationError

from shared import (
    agent_memory,
    approval_queue,
    audit_results_store,
    gsc_rows_store,
    heartbeat,
    state,
    target_keywords,
    wp_post_lister,
    wp_post_raw_fetcher,
)
from shared.doc_index import DocIndex
from shared.log import get_logger
from shared.log_index import LogIndex
from shared.pricing import calc_cost, get_pricing
from shared.schemas.approval import ApprovalPayloadV1Adapter, PublishWpPostV1, UpdateWpPostV1
from shared.schemas.publishing import PublishComplianceGateV1
from thousand_sunny.auth import check_auth, require_auth_or_key

_logger = get_logger("nakama.web.bridge")

# ── Agent roster ─────────────────────────────────────────────────────────────
# Static config for all 9 agents. "default_state" is the fallback when there
# are no api_calls today; agents with "offline" are never promoted.
AGENT_ROSTER = [
    {
        "code": "R-01",
        "key": "robin",
        "role": "知識管理",
        "en": "Knowledge",
        "model": "claude-sonnet-4-6",
        "default_state": "online",
    },  # noqa: E501
    {
        "code": "N-02",
        "key": "nami",
        "role": "日常秘書",
        "en": "Secretary",
        "model": "claude-haiku-4-5",
        "default_state": "online",
    },  # noqa: E501
    {
        "code": "Z-03",
        "key": "zoro",
        "role": "情報蒐集",
        "en": "Scout",
        "model": "claude-sonnet-4-6",
        "default_state": "online",
    },  # noqa: E501
    {
        "code": "B-04",
        "key": "brook",
        "role": "內容撰寫",
        "en": "Composer",
        "model": "claude-opus-4-7",
        "default_state": "online",
    },  # noqa: E501
    {
        "code": "S-05",
        "key": "sanji",
        "role": "社群營運",
        "en": "Community",
        "model": "claude-haiku-4-5",
        "default_state": "online",
    },  # noqa: E501
    {
        "code": "F-06",
        "key": "franky",
        "role": "系統監測",
        "en": "Systems",
        "model": "claude-haiku-4-5",
        "default_state": "online",
    },  # noqa: E501
    {
        "code": "U-07",
        "key": "usopp",
        "role": "發布管線",
        "en": "Publisher",
        "model": "claude-sonnet-4-6",
        "default_state": "hold",
    },  # noqa: E501
    {
        "code": "C-08",
        "key": "chopper",
        "role": "健康顧問",
        "en": "Counsel",
        "model": "—",
        "default_state": "offline",
    },  # noqa: E501
    {
        "code": "D-09",
        "key": "sunny",
        "role": "整合甲板",
        "en": "Deck",
        "model": "—",
        "default_state": "offline",
    },  # noqa: E501
]

router = APIRouter(prefix="/bridge", dependencies=[Depends(require_auth_or_key)])

# HTML 頁面走 cookie → /login redirect，不跟 API 共用 403 行為
page_router = APIRouter(prefix="/bridge")
_templates = Jinja2Templates(
    directory=str(Path(__file__).resolve().parent.parent / "templates" / "bridge")
)


@page_router.get("", response_class=HTMLResponse)
@page_router.get("/", response_class=HTMLResponse)
async def bridge_index(request: Request, nakama_auth: str | None = Cookie(None)):
    """Hub 首頁：列出可跳轉的 Bridge 工具 + 其他 Agent UI。"""
    if not check_auth(nakama_auth):
        return RedirectResponse("/login?next=/bridge", status_code=302)
    return _templates.TemplateResponse(
        request,
        "index.html",
        {
            "robin_enabled": not os.getenv("DISABLE_ROBIN"),
            "drafts_pending_count": approval_queue.count_by_status("pending"),
        },
    )


@page_router.get("/memory", response_class=HTMLResponse)
async def memory_page(request: Request, nakama_auth: str | None = Cookie(None)):
    if not check_auth(nakama_auth):
        return RedirectResponse("/login?next=/bridge/memory", status_code=302)
    return _templates.TemplateResponse(request, "memory.html", {})


@page_router.get("/cost", response_class=HTMLResponse)
async def cost_page(request: Request, nakama_auth: str | None = Cookie(None)):
    if not check_auth(nakama_auth):
        return RedirectResponse("/login?next=/bridge/cost", status_code=302)
    return _templates.TemplateResponse(request, "cost.html", {})


# SEO control center — list both target sites combined.  Order kept stable so
# the section ALWAYS shows wp_shosho first when present (modeled after the
# `target-keywords.yaml` reading convention).
_SEO_TARGET_SITES: tuple[str, ...] = ("wp_shosho", "wp_fleet")

# Anchor for rolling rank windows. We use Taipei calendar so the 28-day window
# matches the user's local "today" (cron + VPS are also Asia/Taipei per
# `reference_vps_timezone.md`).
_SEO_TZ = ZoneInfo("Asia/Taipei")

# Below this absolute delta (positions) we treat the keyword as "flat" rather
# than improving / declining. GSC position is a noisy float, so 0.0 strict
# equality is too brittle — half a slot of movement is not a real trend.
_RANK_FLAT_THRESHOLD = 0.5


def _summarize_seo_post(post: wp_post_lister.WpPostSummaryV1, target_site: str) -> dict[str, Any]:
    """Decorate a `WpPostSummaryV1` with grade/audit fields from `audit_results`.

    Slice #232 wires `latest_for_post` so previously-audited posts show their
    most recent grade + `audited_at`.  Posts that have never been audited
    keep `grade=None` / `last_audited_at=None`; the template renders "—".
    """
    latest = audit_results_store.latest_for_post(target_site, post.wp_post_id)
    grade = latest["overall_grade"] if latest else None
    last_audited_at = latest["audited_at"] if latest else None
    latest_audit_id = latest["id"] if latest else None
    return {
        "wp_post_id": post.wp_post_id,
        "title": post.title,
        "link": post.link,
        "focus_keyword": post.focus_keyword,
        "last_modified": post.last_modified,
        "target_site": target_site,
        "grade": grade,
        "last_audited_at": last_audited_at,
        # Surfaced so the template can link `[查 audit]` to the latest result
        # page once any post has at least one audit row.
        "latest_audit_id": latest_audit_id,
    }


def _summarize_target_keyword(kw: Any) -> dict[str, Any]:
    """Decorate a ``TargetKeywordV1`` with the static fields needed by the
    template (slice #230 shape).  Rank columns are attached separately by
    ``_attach_rank_change`` so the read against ``gsc_rows`` happens exactly
    once per row and the path is unit-testable.

    Attack URL: ``site`` is the canonical short host (``shosho.tw`` /
    ``fleet.shosho.tw``) per ADR-008 §6; we synthesize ``https://<site>``
    because there is no per-keyword landing page in v1 (Usopp will populate
    ``source_post_id`` later, but goal_rank is set against the site root
    for now).
    """
    return {
        "keyword": kw.keyword,
        "keyword_en": kw.keyword_en,
        "site": kw.site,
        "attack_url": f"https://{kw.site}",
        "goal_rank": kw.goal_rank,
        "added_by": kw.added_by,
    }


def _delta_direction(delta: Optional[float]) -> Optional[str]:
    """Map a position delta to a render direction.

    GSC ``position`` is "lower is better": current=5 vs prev=8 → delta=-3
    (rank improved). The template paints negatives as a *green up* arrow
    and positives as a *red down* arrow, hence this remap rather than
    using sign of delta directly.

    Returns ``None`` when delta is ``None`` (window has no rows on either
    side).  Returns ``"flat"`` when ``abs(delta) < _RANK_FLAT_THRESHOLD``
    so half-a-slot noise doesn't masquerade as movement.
    """
    if delta is None:
        return None
    if abs(delta) < _RANK_FLAT_THRESHOLD:
        return "flat"
    return "improved" if delta < 0 else "declined"


def _attach_rank_change(
    kw_row: dict[str, Any],
    today: Optional[datetime] = None,
) -> dict[str, Any]:
    """Enrich a target-keyword dict with current/prev rank, delta, impressions.

    Reads ``gsc_rows`` via ``shared.gsc_rows_store.rank_change_28d``; that
    helper already returns ``None`` for absent windows, which we surface
    as the dash placeholder downstream.

    Section 2 consumes ``current_rank`` / ``current_impressions``; section 3
    additionally consumes ``prev_rank`` / ``delta`` / ``delta_direction``.
    Both sections share this same row dict — single source of truth.
    """
    today_date = (today or datetime.now(_SEO_TZ)).astimezone(_SEO_TZ).date()
    rc = gsc_rows_store.rank_change_28d(
        keyword=kw_row["keyword"],
        url=kw_row["attack_url"],
        today=today_date,
    )
    return {
        **kw_row,
        "current_rank": rc.current_avg_pos,
        "prev_rank": rc.prev_avg_pos,
        "delta": rc.delta,
        "delta_direction": _delta_direction(rc.delta),
        "current_impressions": rc.current_impressions,
    }


def _load_target_keyword_rows(
    today: Optional[datetime] = None,
) -> list[dict[str, Any]]:
    """Load ``config/target-keywords.yaml`` for the SEO 中控台 §2 list and
    attach rolling 28d rank-change data from ``gsc_rows`` (slice #233).

    Returns an empty list when the file is missing OR the YAML has zero
    keywords (the seed file ships with ``keywords: []`` until Zoro Phase 1.5
    pushes the first attack keyword in).  Both shapes hand the template the
    same empty-state branch — see ``test_seo_page_target_keywords_empty_state``.

    When ``gsc_rows`` is empty (cron has not yet run / no data for any
    keyword) every row's rank columns gracefully become ``None`` so the
    template renders dashes — see ``test_seo_page_section3_smoke_no_gsc_rows``.
    """
    doc = target_keywords.load_target_keywords()
    if doc is None or not doc.keywords:
        return []
    rows = [_summarize_target_keyword(kw) for kw in doc.keywords]
    return [_attach_rank_change(row, today=today) for row in rows]


@page_router.get("/seo", response_class=HTMLResponse)
async def seo_page(request: Request, nakama_auth: str | None = Cookie(None)):
    """SEO 中控台 v1 — sections 1 + 2 + 3 live (closes the v1 vision).

    Section 1 (#229 + #232): WP REST live pull (wp_shosho + wp_fleet,
    1h cache, WP errors → empty-state), joined with
    ``audit_results.latest_for_post`` for GRADE / LAST AUDITED columns.
    Section 2 (#230 + #233): ``config/target-keywords.yaml`` via
    ``shared.target_keywords.load_target_keywords`` →
    ``TargetKeywordListV1``; current_rank + impressions columns now read
    from ``gsc_rows`` (slice #233).  Missing / empty file → empty-state.
    Section 3 (#233): rank change panel — same target keyword rows + Δ
    vs prev 28d.  When ``gsc_rows`` is entirely empty (cron not yet run)
    every row gracefully renders "—".
    """
    if not check_auth(nakama_auth):
        return RedirectResponse("/login?next=/bridge/seo", status_code=302)

    rows: list[dict[str, Any]] = []
    for target_site in _SEO_TARGET_SITES:
        for post in wp_post_lister.list_posts(target_site):  # type: ignore[arg-type]
            rows.append(_summarize_seo_post(post, target_site))
    # Combined sort: WP returns per-site sorted; combining requires re-sort.
    rows.sort(key=lambda r: r["last_modified"], reverse=True)

    keyword_rows = _load_target_keyword_rows()

    return _templates.TemplateResponse(
        request,
        "seo.html",
        {
            "articles": rows,
            "target_sites": list(_SEO_TARGET_SITES),
            "target_keywords": keyword_rows,
        },
    )


# ---------------------------------------------------------------------------
# /bridge/seo/audits — kick-off + progress + result (slice 4 / issue #232)
# ---------------------------------------------------------------------------
#
# Lifecycle:
#   1. POST `/bridge/seo/audits` form (url, target_site, wp_post_id?, focus_keyword?)
#      → generates job_id (UUID) + records "running" in `_audit_jobs`
#      → BackgroundTask kicks `_run_audit_job(job_id, ...)` and returns 303 to
#        `/bridge/seo/audits/{job_id}`
#   2. GET `/bridge/seo/audits/{job_id}` renders progress page; JS polls every
#      2s on `/bridge/seo/audits/{job_id}/status`
#   3. GET `/bridge/seo/audits/{job_id}/status` → JSON
#      `{status: 'running' | 'done' | 'error', audit_id, error_stage, ...}`
#   4. On `done`, page meta-refresh redirects to
#      `/bridge/seo/audits/{job_id}/result` which reads `audit_results.id` from
#      the job record + DB and renders the result.
#
# `_audit_jobs` is an in-process dict; on uvicorn restart, in-flight jobs are
# lost but persisted audits survive (DB row already written before the worker
# crash). This is acceptable for v1 (single-host BackgroundTasks; no HA).
# Acquired via `_audit_jobs_lock` for thread-safe writes from the worker.

_AUDIT_FORM_URL_MAX = 2000
_AUDIT_FORM_KEYWORD_MAX = 100

_audit_jobs: dict[str, dict[str, Any]] = {}
_audit_jobs_lock = threading.Lock()


def _now_utc_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _set_audit_job(job_id: str, **fields: Any) -> None:
    with _audit_jobs_lock:
        existing = _audit_jobs.get(job_id, {})
        existing.update(fields)
        _audit_jobs[job_id] = existing


def _get_audit_job(job_id: str) -> Optional[dict[str, Any]]:
    with _audit_jobs_lock:
        entry = _audit_jobs.get(job_id)
        return dict(entry) if entry is not None else None


def _run_audit_job(
    *,
    job_id: str,
    url: str,
    target_site: Optional[str],
    wp_post_id: Optional[int],
    focus_keyword: str,
) -> None:
    """BackgroundTask body — invoke audit_runner and stash result into `_audit_jobs`.

    Imported lazily so test harnesses that monkeypatch `audit_runner.run`
    target the right binding.
    """
    try:
        from agents.brook import audit_runner  # local import per docstring rationale

        # cast for runner: target_site may be str or None; runner accepts Literal.
        result = audit_runner.run(
            url,
            target_site=target_site,  # type: ignore[arg-type]
            wp_post_id=wp_post_id,
            focus_keyword=focus_keyword,
        )
    except Exception as exc:  # noqa: BLE001 — surface any uncaught failure
        _logger.exception("bridge audit BackgroundTask crashed job_id=%s", job_id)
        _set_audit_job(
            job_id,
            status="error",
            error_stage="bridge",
            error_message=f"{type(exc).__name__}: {exc}",
            finished_at=_now_utc_iso(),
        )
        return

    if result.status == "ok":
        _set_audit_job(
            job_id,
            status="done",
            audit_id=result.audit_id,
            finished_at=_now_utc_iso(),
        )
    else:
        _set_audit_job(
            job_id,
            status="error",
            error_stage=result.error_stage,
            error_message=result.error_message,
            finished_at=_now_utc_iso(),
        )


def _validate_target_site(raw: str) -> Optional[str]:
    """Normalize the form-posted target_site value.

    Empty string → None (external / non-WP audit). Any other value must be in
    the supported `_SEO_TARGET_SITES` list, else 422.
    """
    if not raw:
        return None
    if raw not in _SEO_TARGET_SITES:
        raise HTTPException(
            status_code=422,
            detail=f"target_site must be empty or one of {_SEO_TARGET_SITES}",
        )
    return raw


@page_router.post("/seo/audits")
async def seo_audit_kick_off(
    background_tasks: BackgroundTasks,
    url: str = Form(..., min_length=1, max_length=_AUDIT_FORM_URL_MAX),
    target_site: str = Form(""),
    wp_post_id: Optional[int] = Form(None),
    focus_keyword: str = Form("", max_length=_AUDIT_FORM_KEYWORD_MAX),
    nakama_auth: str | None = Cookie(None),
):
    """Kick off a new audit run via FastAPI BackgroundTasks.

    Returns 303 → `/bridge/seo/audits/{job_id}` so the user lands on the
    progress page; the audit subprocess runs in the background and updates
    `_audit_jobs[job_id]` once finished.
    """
    if not check_auth(nakama_auth):
        return RedirectResponse("/login?next=/bridge/seo", status_code=302)

    url_clean = url.strip()
    if not url_clean.startswith(("http://", "https://")):
        raise HTTPException(status_code=422, detail="url must start with http:// or https://")

    resolved_site = _validate_target_site(target_site.strip())

    job_id = uuid.uuid4().hex
    _set_audit_job(
        job_id,
        status="running",
        url=url_clean,
        target_site=resolved_site,
        wp_post_id=wp_post_id,
        focus_keyword=focus_keyword.strip(),
        started_at=_now_utc_iso(),
    )

    background_tasks.add_task(
        _run_audit_job,
        job_id=job_id,
        url=url_clean,
        target_site=resolved_site,
        wp_post_id=wp_post_id,
        focus_keyword=focus_keyword.strip(),
    )

    return RedirectResponse(f"/bridge/seo/audits/{job_id}", status_code=303)


@page_router.get("/seo/audits/{job_id}", response_class=HTMLResponse)
async def seo_audit_progress(
    job_id: str,
    request: Request,
    nakama_auth: str | None = Cookie(None),
):
    """Render the progress page. JS polls `/status` every 2s + auto-redirects."""
    if not check_auth(nakama_auth):
        return RedirectResponse(f"/login?next=/bridge/seo/audits/{job_id}", status_code=302)
    job = _get_audit_job(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail=f"audit job {job_id} not found")
    return _templates.TemplateResponse(
        request,
        "seo_audit_progress.html",
        {
            "job_id": job_id,
            "job": job,
        },
    )


@page_router.get("/seo/audits/{job_id}/status")
async def seo_audit_status(
    job_id: str,
    nakama_auth: str | None = Cookie(None),
):
    """Polling endpoint for the progress page. JSON only."""
    if not check_auth(nakama_auth):
        return JSONResponse({"status": "unauthorized"}, status_code=401)
    job = _get_audit_job(job_id)
    if job is None:
        return JSONResponse({"status": "not_found"}, status_code=404)
    return JSONResponse(
        {
            "status": job["status"],
            "audit_id": job.get("audit_id"),
            "error_stage": job.get("error_stage"),
            "error_message": job.get("error_message"),
            "started_at": job.get("started_at"),
            "finished_at": job.get("finished_at"),
            "redirect_to": (
                f"/bridge/seo/audits/{job_id}/result" if job["status"] == "done" else None
            ),
        }
    )


@page_router.get("/seo/audits/{job_id}/result", response_class=HTMLResponse)
async def seo_audit_result(
    job_id: str,
    request: Request,
    nakama_auth: str | None = Cookie(None),
):
    """Render the audit result page (overall_grade + counts + raw markdown)."""
    if not check_auth(nakama_auth):
        return RedirectResponse(f"/login?next=/bridge/seo/audits/{job_id}/result", status_code=302)
    job = _get_audit_job(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail=f"audit job {job_id} not found")
    if job["status"] != "done":
        # Still running / errored — bounce back to progress page rather than
        # showing a half-rendered result.
        return RedirectResponse(f"/bridge/seo/audits/{job_id}", status_code=303)
    audit_id = job.get("audit_id")
    if audit_id is None:
        raise HTTPException(status_code=500, detail=f"audit job {job_id} done but audit_id missing")
    audit = audit_results_store.get_by_id(audit_id)
    if audit is None:
        raise HTTPException(
            status_code=404, detail=f"audit_results.id={audit_id} not found (DB drift?)"
        )
    return _templates.TemplateResponse(
        request,
        "seo_audit_result.html",
        {
            "job_id": job_id,
            "audit": audit,
            "review_url": f"/bridge/seo/audits/{audit_id}/review",
        },
    )


@page_router.get("/seo/audits/by-id/{audit_id:int}", response_class=HTMLResponse)
async def seo_audit_view_by_id(
    audit_id: int,
    request: Request,
    nakama_auth: str | None = Cookie(None),
):
    """Direct DB-id-based view of a past audit result (no job context).

    Section 1 of `/bridge/seo` links here when a post already has a latest
    audit row, so the user can re-open the result without firing a new run.
    """
    if not check_auth(nakama_auth):
        return RedirectResponse(f"/login?next=/bridge/seo/audits/by-id/{audit_id}", status_code=302)
    audit = audit_results_store.get_by_id(audit_id)
    if audit is None:
        raise HTTPException(status_code=404, detail=f"audit_results.id={audit_id} not found")
    return _templates.TemplateResponse(
        request,
        "seo_audit_result.html",
        {
            "job_id": None,
            "audit": audit,
            "review_url": f"/bridge/seo/audits/{audit_id}/review",
        },
    )


# ---------------------------------------------------------------------------
# /bridge/seo/posts/{wp_post_id}/audits — per-post audit history (Slice 3 / #259)
# ---------------------------------------------------------------------------


@page_router.get("/seo/posts/{wp_post_id:int}/audits", response_class=HTMLResponse)
async def seo_post_audits_history(
    wp_post_id: int,
    request: Request,
    target_site: str | None = None,
    nakama_auth: str | None = Cookie(None),
):
    """Read-only timeline of all audits for a single WP post.

    Slice 3 of PRD #255 (#259 / E). Reads from existing `audit_results`
    table (no schema migration needed — `migrations/006_audit_results.sql:34`
    already supports multi-row per post). Linked from each row in
    `/bridge/seo` article list.

    `target_site` is an optional query param (`?target_site=wp_shosho`)
    that scopes the history when the same `wp_post_id` could exist on both
    `wp_shosho` and `wp_fleet`. Without it, audits across both sites are
    merged in the timeline.
    """
    auth_next = f"/login?next=/bridge/seo/posts/{wp_post_id}/audits"
    if target_site:
        auth_next += f"?target_site={target_site}"
    if not check_auth(nakama_auth):
        return RedirectResponse(auth_next, status_code=302)

    audits = audit_results_store.list_audits_by_post(wp_post_id, target_site=target_site)

    # Build display-friendly rows. The template expects severity counts
    # already broken out so it can render the badges without re-walking the
    # suggestions list per row.
    display_rows: list[dict[str, Any]] = []
    for a in audits:
        suggestions = a.get("suggestions") or []
        # Status badge label per PRD #255 user story #14:
        #   pending  — review_status='fresh' (no review touched)
        #   partial  — review_status='in_review' (some suggestions reviewed)
        #   exported — review_status='exported' (approval_queue row created)
        review_status = a.get("review_status") or "fresh"
        if review_status == "exported":
            badge_label = (
                f"✅ exported #{a.get('approval_queue_id')}"
                if a.get("approval_queue_id") is not None
                else "✅ exported"
            )
        elif review_status == "in_review":
            badge_label = "📝 partial"
        elif review_status == "archived":
            badge_label = "📦 archived"
        else:
            badge_label = "⏸ pending"

        display_rows.append(
            {
                "id": a.get("id"),
                "audited_at": a.get("audited_at"),
                "audited_at_display": (a.get("audited_at") or "")[:16].replace("T", " "),
                "overall_grade": a.get("overall_grade"),
                "fail_count": a.get("fail_count", 0),
                "warn_count": a.get("warn_count", 0),
                "review_status": review_status,
                "review_status_label": badge_label,
                "suggestions_total": len(suggestions),
                "url": a.get("url"),
                "focus_keyword": a.get("focus_keyword") or "",
                "target_site": a.get("target_site"),
            }
        )

    # First row's url / focus_keyword power the "+ 重 audit" prefilled form
    # at the top of the page. If there are no audits we render the empty
    # state and skip that affordance.
    first_url = display_rows[0]["url"] if display_rows else None
    first_focus_keyword = display_rows[0]["focus_keyword"] if display_rows else ""
    first_target_site = display_rows[0]["target_site"] if display_rows else target_site

    return _templates.TemplateResponse(
        request,
        "seo_audit_history.html",
        {
            "wp_post_id": wp_post_id,
            "target_site": target_site,
            "rows": display_rows,
            "first_url": first_url,
            "first_focus_keyword": first_focus_keyword,
            "first_target_site": first_target_site,
        },
    )


# ---------------------------------------------------------------------------
# /bridge/seo/audits/{id}/review — Y+ tier review UX (slice #234, issue #234)
# ---------------------------------------------------------------------------
#
# Layout:
#   left  — textarea pre-filled with the WP post's content.raw (Gutenberg
#           HTML). Read-only in this slice; slice #235 will wire the edited
#           body back to WP REST + into approval_queue.
#   right — list of suggestion cards (one per fail/warn entry). Each card
#           supports approve / edit / reject + a `[在左側顯示]` button that
#           scrolls the textarea to the matched current_value and briefly
#           highlights it in yellow.
#
# State persistence (PRD §"Review semantics" Q9): per-suggestion status,
# edited_value, reviewed_at all live in `audit_results.suggestions_json`.
# Mutation endpoints rewrite that blob via `audit_results_store.update_suggestion`.
# Re-visiting the page reads the same blob; statuses survive uvicorn restart.

# Per-suggestion edit text cap (PRD #226 §"Implementation Decisions" form
# pattern). 8000 chars accommodates long meta-description / image-alt edits
# without blowing up the form post; suggestions exceeding this should fall
# back to manual WP editing for v1.
_REVIEW_EDIT_MAX = 8000


def _serialize_suggestion_for_template(suggestion: Any) -> dict[str, Any]:
    """Translate an `AuditSuggestionV1` into a template-friendly dict.

    Keeps Jinja sandboxed from pydantic objects (which don't expose a
    ``.get`` interface and force every template attr lookup through method
    descriptors). Also pre-computes the ``severity_label`` zh string so the
    template doesn't have to map.
    """
    return {
        "rule_id": suggestion.rule_id,
        "severity": suggestion.severity,
        "title": suggestion.title,
        "current_value": suggestion.current_value,
        "suggested_value": suggestion.suggested_value,
        "rationale": suggestion.rationale,
        "status": suggestion.status,
        "edited_value": suggestion.edited_value,
        "reviewed_at": (
            suggestion.reviewed_at.isoformat() if suggestion.reviewed_at is not None else None
        ),
    }


@page_router.get("/seo/audits/{audit_id:int}/review", response_class=HTMLResponse)
async def seo_audit_review_page(
    audit_id: int,
    request: Request,
    nakama_auth: str | None = Cookie(None),
):
    """Render the Y+ tier review UI: left textarea + right suggestion cards.

    Resumable: each card reflects the current persisted ``status`` /
    ``edited_value`` straight from the DB row. No client-side state.
    """
    if not check_auth(nakama_auth):
        return RedirectResponse(
            f"/login?next=/bridge/seo/audits/{audit_id}/review",
            status_code=302,
        )
    audit = audit_results_store.get_by_id(audit_id)
    if audit is None:
        raise HTTPException(status_code=404, detail=f"audit_results.id={audit_id} not found")

    # Fetch raw HTML body. For non-WP audits (no target_site / wp_post_id)
    # we skip the fetch and surface a friendly notice in the textarea panel.
    raw_html = ""
    fetch_error: Optional[str] = None
    if audit.get("target_site") and audit.get("wp_post_id"):
        result = wp_post_raw_fetcher.fetch_raw_html(
            target_site=audit["target_site"],  # type: ignore[arg-type]
            wp_post_id=int(audit["wp_post_id"]),
            operation_id=f"review_audit_{audit_id}",
        )
        if result.ok:
            raw_html = result.raw_html
        else:
            fetch_error = result.error_message
    else:
        fetch_error = "外站 / 非 WP audit 無 wp_post_id — 文章主體不可從 WP REST 抓取"

    suggestions = [_serialize_suggestion_for_template(s) for s in audit["suggestions"]]
    has_actionable = any(s["status"] in ("approved", "edited") for s in suggestions)
    actionable_count = sum(1 for s in suggestions if s["status"] in ("approved", "edited"))
    can_export = (
        has_actionable
        and audit.get("review_status") != "exported"
        and bool(audit.get("target_site"))
        and audit.get("wp_post_id") is not None
    )

    return _templates.TemplateResponse(
        request,
        "seo_audit_review.html",
        {
            "audit": audit,
            "raw_html": raw_html,
            "fetch_error": fetch_error,
            "suggestions": suggestions,
            "has_actionable": has_actionable,
            "actionable_count": actionable_count,
            "can_export": can_export,
            "review_status": audit.get("review_status"),
            "approval_queue_id": audit.get("approval_queue_id"),
            "edit_max_length": _REVIEW_EDIT_MAX,
        },
    )


def _require_audit_row(audit_id: int) -> dict[str, Any]:
    audit = audit_results_store.get_by_id(audit_id)
    if audit is None:
        raise HTTPException(status_code=404, detail=f"audit_results.id={audit_id} not found")
    return audit


def _redirect_to_review(audit_id: int) -> RedirectResponse:
    """303 back to the review page so the form-post resolves to a GET (PRG)."""
    return RedirectResponse(f"/bridge/seo/audits/{audit_id}/review", status_code=303)


@page_router.post("/seo/audits/{audit_id:int}/suggestions/{rule_id}/approve")
async def seo_audit_review_approve(
    audit_id: int,
    rule_id: str,
    nakama_auth: str | None = Cookie(None),
):
    """Mark a single suggestion as approved."""
    if not check_auth(nakama_auth):
        return RedirectResponse(
            f"/login?next=/bridge/seo/audits/{audit_id}/review",
            status_code=302,
        )
    _require_audit_row(audit_id)
    try:
        audit_results_store.update_suggestion(
            audit_id=audit_id,
            rule_id=rule_id,
            status="approved",
        )
    except audit_results_store.SuggestionNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    return _redirect_to_review(audit_id)


@page_router.post("/seo/audits/{audit_id:int}/suggestions/{rule_id}/reject")
async def seo_audit_review_reject(
    audit_id: int,
    rule_id: str,
    nakama_auth: str | None = Cookie(None),
):
    """Mark a single suggestion as rejected (excluded from export)."""
    if not check_auth(nakama_auth):
        return RedirectResponse(
            f"/login?next=/bridge/seo/audits/{audit_id}/review",
            status_code=302,
        )
    _require_audit_row(audit_id)
    try:
        audit_results_store.update_suggestion(
            audit_id=audit_id,
            rule_id=rule_id,
            status="rejected",
        )
    except audit_results_store.SuggestionNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    return _redirect_to_review(audit_id)


@page_router.post("/seo/audits/{audit_id:int}/suggestions/{rule_id}/edit")
async def seo_audit_review_edit(
    audit_id: int,
    rule_id: str,
    edited_value: str = Form(..., min_length=1, max_length=_REVIEW_EDIT_MAX),
    nakama_auth: str | None = Cookie(None),
):
    """Mark a suggestion as edited and persist the user's replacement text."""
    if not check_auth(nakama_auth):
        return RedirectResponse(
            f"/login?next=/bridge/seo/audits/{audit_id}/review",
            status_code=302,
        )
    _require_audit_row(audit_id)
    try:
        audit_results_store.update_suggestion(
            audit_id=audit_id,
            rule_id=rule_id,
            status="edited",
            edited_value=edited_value,
        )
    except audit_results_store.SuggestionNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    return _redirect_to_review(audit_id)


# ---------------------------------------------------------------------------
# /bridge/seo/audits/{id}/export — slice #235 / issue #235
# ---------------------------------------------------------------------------
#
# Hand the reviewed audit off to the existing ADR-006 approval_queue:
#   1. Collect approved + edited suggestions (rejected + pending excluded).
#   2. Build an `UpdateWpPostV1` payload — `change_summary` is auto-generated;
#      `proposed_changes` (in `patch`) is a markdown block formatted for
#      reviewer reading on `/bridge/drafts/{id}`.
#   3. `enqueue` the payload (status=pending, source_agent=brook).
#   4. `mark_exported` writes the queue id back into `audit_results`.
#   5. 303 redirect to `/bridge/drafts` so reviewer sees the new pending row.
#
# Re-export guard: if the audit row already shows `review_status='exported'`
# the endpoint short-circuits with 409, surfacing the prior queue id so the
# user knows where the existing entry lives.
#
# Atomicity: option (b) per issue #235 acceptance — `enqueue` and
# `mark_exported` each commit their own rows because they share the singleton
# sqlite connection from `shared.state._get_conn()` and both call
# `conn.commit()` internally; wrapping in a single BEGIN IMMEDIATE would
# require leaking commit-control into both deep modules, which is exactly
# what their short interfaces try to hide. The practical risk is microsecond:
# if the Python process crashes between step 3 and step 4, the queue ends up
# with an orphan pending row whose `operation_id == seo_audit_export_<id>`
# while the audit row still reads `review_status != 'exported'`. Recovery
# path (manual): `SELECT id FROM approval_queue WHERE operation_id LIKE
# 'seo_audit_export_%' AND id NOT IN (SELECT approval_queue_id FROM
# audit_results WHERE approval_queue_id IS NOT NULL)` → for each, either run
# `audit_results_store.mark_exported(audit_id, queue_id)` or transition the
# orphan queue row to `archived` via `approval_queue.transition`.

# Per-suggestion text rendered into `proposed_changes` is bounded by
# `_REVIEW_EDIT_MAX` per field on input; the assembled markdown body itself
# is left unbounded because reviewer-side `/bridge/drafts/{id}` already pages
# the payload preview behind a `<pre>`.


def _collect_export_suggestions(suggestions: list[Any]) -> list[dict[str, Any]]:
    """Filter `audit_results.suggestions` down to the rows that should ship.

    Only `approved` and `edited` are exported. For an `edited` row we swap
    in `edited_value` — `suggested_value` is the LLM/deterministic seed and
    must NOT survive into the exported payload when the reviewer overrode
    it (per issue #235 acceptance).
    """
    out: list[dict[str, Any]] = []
    for s in suggestions:
        if s.status == "approved":
            value = s.suggested_value
        elif s.status == "edited":
            # `update_suggestion(status='edited', edited_value=...)` is the
            # only ingress path; the ValueError there guards `edited_value`
            # being None, but we keep a defensive fallback so a corrupted
            # blob doesn't silently ship empty text.
            value = s.edited_value if s.edited_value else s.suggested_value
        else:
            continue
        out.append(
            {
                "rule_id": s.rule_id,
                "severity": s.severity,
                "title": s.title,
                "current_value": s.current_value or "",
                "value": value or "",
                "rationale": s.rationale or "",
                "status": s.status,
            }
        )
    return out


def _format_proposed_changes_md(audit: dict[str, Any], picks: list[dict[str, Any]]) -> str:
    """Render exported suggestions as a markdown block for HITL review.

    The reviewer reads this on `/bridge/drafts/{id}` payload preview and
    decides whether to `approve` (Usopp picks up + writes WP) or `reject`.
    Format choice: human-friendly headers + bullet trio per suggestion (
    現況 / 建議 / 為什麼) — easier to scan than raw JSON, easier to compare
    side-by-side with the WP post.
    """
    lines: list[str] = []
    lines.append(f"## SEO audit 改動建議（{len(picks)} 條）")
    lines.append("")
    audit_url = audit.get("url") or ""
    if audit_url:
        lines.append(f"- 來源 audit：`{audit_url}`")
    target_site = audit.get("target_site") or ""
    wp_post_id = audit.get("wp_post_id")
    if target_site and wp_post_id is not None:
        lines.append(f"- 目標：`{target_site}` · post #{wp_post_id}")
    overall = audit.get("overall_grade") or ""
    if overall:
        lines.append(f"- 整體分數（audit 當下）：**{overall}**")
    lines.append("")
    for pick in picks:
        title = pick["title"] or pick["rule_id"]
        lines.append(f"### [{pick['rule_id']}] {title}")
        # `severity` + `status` together help the reviewer triage; e.g. an
        # `edited` warn vs a raw approved fail change urgency.
        lines.append(f"- severity：`{pick['severity']}` · 來自：`{pick['status']}`")
        lines.append(f"- 現況：{pick['current_value'] or '（無）'}")
        lines.append(f"- 建議：{pick['value'] or '（無）'}")
        if pick["rationale"]:
            lines.append(f"- 為什麼：{pick['rationale']}")
        lines.append("")
    # Strip trailing blank line for a clean tail.
    while lines and lines[-1] == "":
        lines.pop()
    return "\n".join(lines)


def _build_export_change_summary(picks: list[dict[str, Any]]) -> str:
    """One-liner reviewer sees on `/bridge/drafts` list view.

    Examples:
        "SEO audit: 接受 3 條建議（M2 + L9 + H4）"
        "SEO audit: 接受 1 條建議（M1）"

    Caps at the first 6 rule ids; beyond that we add `… +N` so the
    title_snippet (capped at 80 chars by `approval_queue._snippet`) stays
    informative without truncating mid-pill.
    """
    rule_ids = [p["rule_id"] for p in picks]
    head = " + ".join(rule_ids[:6])
    if len(rule_ids) > 6:
        head = f"{head} … +{len(rule_ids) - 6}"
    return f"SEO audit: 接受 {len(picks)} 條建議（{head}）"


def _build_export_payload(
    audit: dict[str, Any],
    picks: list[dict[str, Any]],
) -> UpdateWpPostV1:
    """Construct the `UpdateWpPostV1` payload for `approval_queue.enqueue`.

    The `patch` field is a free-form dict per ADR-006 §2; we put the
    markdown block under a `proposed_changes` key so the reviewer-side UI
    has a stable address to render. Usopp doesn't read `proposed_changes`
    directly — that's the reviewer's discretionary edit step. Once approved,
    Usopp's `update_post` action will need to translate the reviewer's
    confirmed text into actual WP REST patches; that translation lives
    outside this slice.

    `compliance_flags`: this is an SEO metadata change, not new editorial
    copy, so we ship the gate with both flags False. If a reviewer's edited
    value contains medical-claim vocab, they will be the one to flip the
    `reviewer_compliance_ack` toggle on `/bridge/drafts/{id}` before
    approving — same defense-in-depth as Brook's compose path.
    """
    proposed_changes_md = _format_proposed_changes_md(audit, picks)
    change_summary = _build_export_change_summary(picks)
    return UpdateWpPostV1(
        schema_version=1,
        action_type="update_post",
        target_site=audit["target_site"],  # type: ignore[arg-type]
        wp_post_id=int(audit["wp_post_id"]),
        patch={"proposed_changes": proposed_changes_md},
        change_summary=change_summary,
        draft_id=f"audit_{audit['id']}",
        compliance_flags=PublishComplianceGateV1(
            schema_version=1,
            medical_claim=False,
            absolute_assertion=False,
            matched_terms=[],
        ),
        reviewer_compliance_ack=False,
    )


@page_router.post("/seo/audits/{audit_id:int}/export")
async def seo_audit_export(audit_id: int, nakama_auth: str | None = Cookie(None)):
    """Export reviewed audit → approval_queue + redirect to `/bridge/drafts`.

    Behavior matrix (issue #235 acceptance):

    - audit not found                              → 404
    - audit is non-WP (no target_site / wp_post_id) → 422
    - already exported                              → 409 with prior queue id
    - zero approved+edited suggestions              → 409 (mirrors button gate)
    - happy path                                    → enqueue + mark_exported
                                                       → 303 → `/bridge/drafts`
    """
    if not check_auth(nakama_auth):
        return RedirectResponse(
            f"/login?next=/bridge/seo/audits/{audit_id}/review",
            status_code=302,
        )
    audit = audit_results_store.get_by_id(audit_id)
    if audit is None:
        raise HTTPException(status_code=404, detail=f"audit_results.id={audit_id} not found")

    if audit.get("review_status") == "exported":
        existing = audit.get("approval_queue_id")
        raise HTTPException(
            status_code=409,
            detail=f"already exported as queue #{existing}",
        )

    if not (audit.get("target_site") and audit.get("wp_post_id")):
        # Non-WP audits have nothing to patch on WP REST → can't be exported.
        raise HTTPException(
            status_code=422,
            detail="cannot export audit without target_site + wp_post_id (non-WP audit)",
        )

    picks = _collect_export_suggestions(audit["suggestions"])
    if not picks:
        # The button is disabled in the template when this is true; we still
        # 409 server-side so a stale form-submit can't sneak past the gate.
        raise HTTPException(
            status_code=409,
            detail="no approved or edited suggestions to export",
        )

    payload = _build_export_payload(audit, picks)
    operation_id = f"seo_audit_export_{audit_id}"
    queue_id = approval_queue.enqueue(
        source_agent="brook",
        payload_model=payload,
        operation_id=operation_id,
        priority=50,
        initial_status="pending",
    )
    # Two-step write: see module-level note at the top of this section for
    # the recovery rationale (option b — orphan queue row is the documented
    # failure mode if Python crashes between these two lines).
    audit_results_store.mark_exported(audit_id, queue_id)
    _logger.info(
        "seo_audit export audit_id=%d queue_id=%d picks=%d",
        audit_id,
        queue_id,
        len(picks),
    )
    return RedirectResponse("/bridge/drafts", status_code=303)


# Stale thresholds (minutes) used to colour-code rows on /bridge/health.
# `green` ≤ HEALTH_GREEN_MIN; HEALTH_GREEN_MIN < `yellow` ≤ HEALTH_YELLOW_MIN;
# HEALTH_YELLOW_MIN < `orange` ≤ HEALTH_ORANGE_MIN; > HEALTH_ORANGE_MIN → `red`.
# Tuned for 5-min cron tick + 15-min GH Actions delay budget.
HEALTH_GREEN_MIN = 60
HEALTH_YELLOW_MIN = 6 * 60
HEALTH_ORANGE_MIN = 24 * 60


def _health_chip(stale_minutes: int | None) -> str:
    if stale_minutes is None:
        return "never"
    if stale_minutes <= HEALTH_GREEN_MIN:
        return "green"
    if stale_minutes <= HEALTH_YELLOW_MIN:
        return "yellow"
    if stale_minutes <= HEALTH_ORANGE_MIN:
        return "orange"
    return "red"


@page_router.get("/health", response_class=HTMLResponse)
async def health_page(request: Request, nakama_auth: str | None = Cookie(None)):
    """Phase 3 observability: per-job heartbeat surface."""
    if not check_auth(nakama_auth):
        return RedirectResponse("/login?next=/bridge/health", status_code=302)

    rows = []
    for hb in heartbeat.list_all():
        rows.append(
            {
                "job_name": hb.job_name,
                "last_status": hb.last_status,
                "stale_minutes": hb.stale_minutes,
                "success_age_minutes": hb.success_age_minutes,
                "consecutive_failures": hb.consecutive_failures,
                "last_error": hb.last_error,
                "last_run_at": hb.last_run_at.isoformat() if hb.last_run_at else None,
                "chip": _health_chip(hb.stale_minutes),
            }
        )

    return _templates.TemplateResponse(
        request,
        "health.html",
        {
            "rows": rows,
            "thresholds": {
                "green_min": HEALTH_GREEN_MIN,
                "yellow_min": HEALTH_YELLOW_MIN,
                "orange_min": HEALTH_ORANGE_MIN,
            },
        },
    )


# ---------------------------------------------------------------------------
# /bridge/docs — FTS5 search across docs/ + memory/ markdown (Phase 9)
# ---------------------------------------------------------------------------

# Module-level singleton: index is rebuilt on first request and on demand.
# Cheap rebuild (<1s for ~700 files) means we don't need persistent staleness
# tracking — the page query string can include &reindex=1 to force rebuild.
_doc_index: DocIndex | None = None


def _get_doc_index() -> DocIndex:
    global _doc_index
    if _doc_index is None:
        _doc_index = DocIndex.from_repo_root()
        _doc_index.rebuild()
    return _doc_index


@page_router.get("/docs", response_class=HTMLResponse)
async def docs_page(
    request: Request,
    q: str = Query("", max_length=200),
    category: str = Query("", max_length=64),
    reindex: bool = Query(False),
    nakama_auth: str | None = Cookie(None),
):
    """Full-text search across docs/ + memory/ markdown."""
    if not check_auth(nakama_auth):
        return RedirectResponse("/login?next=/bridge/docs", status_code=302)

    if reindex:
        global _doc_index
        _doc_index = None  # force rebuild on next access
    idx = _get_doc_index()

    hits = idx.search(q, limit=30, category=category or None) if q.strip() else []
    stats = idx.stats()

    return _templates.TemplateResponse(
        request,
        "docs.html",
        {
            "q": q,
            "category": category,
            "hits": hits,
            "stats": stats,
            "categories": sorted(stats.keys()),
        },
    )


# ---------------------------------------------------------------------------
# /bridge/logs — FTS5 search across structured nakama logs (Phase 5C)
# ---------------------------------------------------------------------------

_log_index: LogIndex | None = None


def _get_log_index() -> LogIndex:
    global _log_index
    if _log_index is None:
        _log_index = LogIndex.from_default_path()
    return _log_index


_RELATIVE_TIME_RE = re.compile(r"^\s*(\d+)\s*([smhd])\s*(?:ago)?\s*$", flags=re.IGNORECASE)


def _parse_time_filter(raw: str) -> datetime | None:
    """Parse user-supplied time filter into a tz-aware UTC datetime.

    Accepted forms (most lenient last):
      - ISO8601:    `2026-04-26T07:00:00Z` / `2026-04-26T07:00:00+00:00`
      - Date-only:  `2026-04-26` (interpreted as 00:00 UTC)
      - Relative:   `30m ago` / `1h` / `24h ago` / `7d` — uses `s/m/h/d` units

    Returns None on empty / unparseable input (caller treats as "no filter").
    """
    if not raw or not raw.strip():
        return None
    s = raw.strip()
    rel = _RELATIVE_TIME_RE.match(s)
    if rel:
        amount = int(rel.group(1))
        unit = rel.group(2).lower()
        delta = {
            "s": timedelta(seconds=amount),
            "m": timedelta(minutes=amount),
            "h": timedelta(hours=amount),
            "d": timedelta(days=amount),
        }[unit]
        return datetime.now(timezone.utc) - delta
    # ISO8601 / date-only
    try:
        # `Z` suffix isn't recognized by fromisoformat() pre-3.11 in some builds
        normalized = s.replace("Z", "+00:00") if s.endswith("Z") else s
        if "T" not in normalized and len(normalized) == 10:
            normalized += "T00:00:00+00:00"
        dt = datetime.fromisoformat(normalized)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc)
    except ValueError:
        return None


@page_router.get("/logs", response_class=HTMLResponse)
async def logs_page(
    request: Request,
    q: str = Query("", max_length=200),
    level: str = Query("", max_length=16),
    logger: str = Query("", max_length=128),
    since: str = Query("", max_length=64),
    until: str = Query("", max_length=64),
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    nakama_auth: str | None = Cookie(None),
):
    """Full-text + filter search over structured nakama log records.

    See task prompt `2026-04-26-phase-5c-log-search-fts5.md` for behavior spec.
    """
    if not check_auth(nakama_auth):
        return RedirectResponse("/login?next=/bridge/logs", status_code=302)

    idx = _get_log_index()
    since_dt = _parse_time_filter(since)
    until_dt = _parse_time_filter(until)

    hits = idx.search(
        q,
        level=level or None,
        logger_prefix=logger or None,
        since=since_dt,
        until=until_dt,
        limit=limit,
        offset=offset,
    )
    stats = idx.stats()

    # Build prev/next URLs preserving filters; offset is the only thing we change.
    base_qs: list[str] = []
    for k, v in (
        ("q", q),
        ("level", level),
        ("logger", logger),
        ("since", since),
        ("until", until),
    ):
        if v:
            base_qs.append(f"{k}={v}")
    base_qs.append(f"limit={limit}")
    base = "&".join(base_qs)
    prev_offset = max(offset - limit, 0)
    next_offset = offset + limit
    prev_url = f"/bridge/logs?{base}&offset={prev_offset}" if offset > 0 else None
    next_url = f"/bridge/logs?{base}&offset={next_offset}" if len(hits) == limit else None

    return _templates.TemplateResponse(
        request,
        "logs.html",
        {
            "q": q,
            "level": level,
            "logger": logger,
            "since": since,
            "until": until,
            "limit": limit,
            "offset": offset,
            "hits": hits,
            "stats": stats,
            "prev_url": prev_url,
            "next_url": next_url,
        },
    )


# ---------------------------------------------------------------------------
# Drafts — HITL approval queue UI（read-only scaffolding, ADR-006）
# ---------------------------------------------------------------------------


def _summarize_draft_row(row: dict[str, Any]) -> dict[str, Any]:
    """Decorate a raw approval_queue row with a parsed-payload summary for the UI.

    Bad payloads (schema drift / corrupted JSON) get `parse_error` set instead
    of crashing the whole list — same soft-fail philosophy as
    `claim_approved_drafts()` (ADR-006 borderline #2.5).
    """
    summary: dict[str, Any] = {
        "id": row["id"],
        "status": row["status"],
        "source_agent": row["source_agent"],
        "target_platform": row.get("target_platform"),
        "target_site": row.get("target_site"),
        "action_type": row.get("action_type"),
        "title_snippet": row.get("title_snippet") or "",
        "operation_id": row.get("operation_id"),
        "created_at": row.get("created_at"),
        "updated_at": row.get("updated_at"),
        "priority": row.get("priority", 50),
        "compliance_ack": bool(row.get("reviewer_compliance_ack")),
        "parse_error": None,
        "compliance_flagged": False,
    }
    raw = row.get("payload")
    if not raw:
        return summary
    try:
        payload = ApprovalPayloadV1Adapter.validate_python(json.loads(raw))
    except (json.JSONDecodeError, ValidationError, TypeError) as e:
        summary["parse_error"] = f"{type(e).__name__}: {e}"
        return summary
    flags = payload.compliance_flags
    summary["compliance_flagged"] = bool(flags.medical_claim or flags.absolute_assertion)
    return summary


@page_router.get("/drafts", response_class=HTMLResponse)
async def drafts_page(request: Request, nakama_auth: str | None = Cookie(None)):
    """List drafts in the HITL approval queue. Read-only in this scaffolding."""
    if not check_auth(nakama_auth):
        return RedirectResponse("/login?next=/bridge/drafts", status_code=302)

    list_limit = 50  # mirrors approval_queue.list_by_status default
    pending_rows = approval_queue.list_by_status("pending", limit=list_limit)
    in_review_rows = approval_queue.list_by_status("in_review", limit=list_limit)
    drafts = [_summarize_draft_row(r) for r in (pending_rows + in_review_rows)]

    pending_total = approval_queue.count_by_status("pending")
    in_review_total = approval_queue.count_by_status("in_review")
    truncated = pending_total > len(pending_rows) or in_review_total > len(in_review_rows)

    return _templates.TemplateResponse(
        request,
        "drafts.html",
        {
            "drafts": drafts,
            "pending_count": pending_total,
            "in_review_count": in_review_total,
            "list_limit": list_limit,
            "truncated": truncated,
            "shown_count": len(drafts),
        },
    )


@page_router.get("/drafts/{draft_id:int}", response_class=HTMLResponse)
async def draft_detail_page(
    draft_id: int, request: Request, nakama_auth: str | None = Cookie(None)
):
    """Single draft detail — payload preview + stub action buttons (Phase 2)."""
    if not check_auth(nakama_auth):
        return RedirectResponse(f"/login?next=/bridge/drafts/{draft_id}", status_code=302)

    row = approval_queue.get_by_id(draft_id)
    if row is None:
        raise HTTPException(status_code=404, detail=f"draft {draft_id} not found")

    summary = _summarize_draft_row(row)
    parsed_payload: ApprovalPayloadV1Adapter | None = None
    payload_pretty: str | None = None
    headline_fields: list[tuple[str, str]] = []

    raw = row.get("payload")
    if raw:
        try:
            payload = ApprovalPayloadV1Adapter.validate_python(json.loads(raw))
            parsed_payload = payload
            payload_pretty = payload.model_dump_json(indent=2)
            if isinstance(payload, PublishWpPostV1):
                headline_fields = [
                    ("title", payload.draft.title),
                    ("target_site", payload.target_site),
                    ("scheduled_at", str(payload.scheduled_at) if payload.scheduled_at else "—"),
                ]
            elif isinstance(payload, UpdateWpPostV1):
                headline_fields = [
                    ("change_summary", payload.change_summary),
                    ("target_site", payload.target_site),
                    ("wp_post_id", str(payload.wp_post_id)),
                ]
        except (json.JSONDecodeError, ValidationError, TypeError) as e:
            summary["parse_error"] = f"{type(e).__name__}: {e}"
            payload_pretty = raw  # show raw text so reviewer can triage manually

    return _templates.TemplateResponse(
        request,
        "draft_detail.html",
        {
            "draft": summary,
            "raw_row": row,
            "headline_fields": headline_fields,
            "payload_pretty": payload_pretty,
            "has_parsed_payload": parsed_payload is not None,
            "raw_payload_text": raw,
            "error_log": row.get("error_log"),
            "retry_count": row.get("retry_count") or 0,
        },
    )


# ---------------------------------------------------------------------------
# Drafts — mutation endpoints (Phase 2, ADR-006 §7)
# ---------------------------------------------------------------------------
#
# All four endpoints are POST + 303 redirect (form-post pattern, no JS required).
# Auth = same cookie path as the page routes; the dev fallback when WEB_PASSWORD
# is unset returns True so unit tests don't need to forge cookies.
# Reviewer is hard-coded to "shosho" — single-reviewer assumption (task邊界:
# permission/role check pushed to Phase 4 if multi-reviewer ever lands).

_REVIEWER = "shosho"


def _require_draft(draft_id: int) -> dict[str, Any]:
    row = approval_queue.get_by_id(draft_id)
    if row is None:
        raise HTTPException(status_code=404, detail=f"draft {draft_id} not found")
    return row


@page_router.post("/drafts/{draft_id:int}/approve")
async def draft_approve(draft_id: int, nakama_auth: str | None = Cookie(None)):
    """POST → set status=approved (pending or in_review), redirect to list."""
    if not check_auth(nakama_auth):
        return RedirectResponse(f"/login?next=/bridge/drafts/{draft_id}", status_code=302)
    row = _require_draft(draft_id)
    if row["status"] not in ("pending", "in_review"):
        raise HTTPException(
            status_code=409,
            detail=f"cannot approve from status={row['status']!r}",
        )
    try:
        approval_queue.approve(draft_id, reviewer=_REVIEWER, from_status=row["status"])
    except approval_queue.ConcurrentTransitionError as e:
        raise HTTPException(status_code=409, detail=str(e))
    return RedirectResponse("/bridge/drafts", status_code=303)


@page_router.post("/drafts/{draft_id:int}/reject")
async def draft_reject(
    draft_id: int,
    reason: str = Form(..., min_length=1),
    nakama_auth: str | None = Cookie(None),
):
    """POST → set status=rejected (pending or in_review), record reason, redirect to list."""
    if not check_auth(nakama_auth):
        return RedirectResponse(f"/login?next=/bridge/drafts/{draft_id}", status_code=302)
    row = _require_draft(draft_id)
    if row["status"] not in ("pending", "in_review"):
        raise HTTPException(
            status_code=409,
            detail=f"cannot reject from status={row['status']!r}",
        )
    try:
        approval_queue.reject(draft_id, reviewer=_REVIEWER, note=reason, from_status=row["status"])
    except approval_queue.ConcurrentTransitionError as e:
        raise HTTPException(status_code=409, detail=str(e))
    return RedirectResponse("/bridge/drafts", status_code=303)


_EDIT_PAYLOAD_MAX_BYTES = 200_000  # 200 KB hard cap on payload edit (form post)


@page_router.post("/drafts/{draft_id:int}/edit")
async def draft_edit(
    draft_id: int,
    payload: str = Form(..., min_length=1, max_length=_EDIT_PAYLOAD_MAX_BYTES),
    nakama_auth: str | None = Cookie(None),
):
    """POST → overwrite payload JSON in place; status preserved; redirect to detail."""
    if not check_auth(nakama_auth):
        return RedirectResponse(f"/login?next=/bridge/drafts/{draft_id}", status_code=302)
    row = _require_draft(draft_id)
    if row["status"] not in ("pending", "in_review"):
        raise HTTPException(
            status_code=409,
            detail=f"cannot edit payload from status={row['status']!r}",
        )
    try:
        parsed = ApprovalPayloadV1Adapter.validate_python(json.loads(payload))
    except (json.JSONDecodeError, ValidationError, TypeError) as e:
        raise HTTPException(
            status_code=400,
            detail=f"payload invalid: {type(e).__name__}: {e}",
        )
    try:
        approval_queue.update_payload(
            draft_id,
            payload_model=parsed,
            expected_status=row["status"],
        )
    except approval_queue.ConcurrentTransitionError as e:
        raise HTTPException(status_code=409, detail=str(e))
    return RedirectResponse(f"/bridge/drafts/{draft_id}", status_code=303)


@page_router.post("/drafts/{draft_id:int}/requeue")
async def draft_requeue(draft_id: int, nakama_auth: str | None = Cookie(None)):
    """POST → failed → pending, clear error_log + retry_count, redirect to list."""
    if not check_auth(nakama_auth):
        return RedirectResponse(f"/login?next=/bridge/drafts/{draft_id}", status_code=302)
    row = _require_draft(draft_id)
    if row["status"] != "failed":
        raise HTTPException(
            status_code=409,
            detail=f"cannot requeue from status={row['status']!r} (only 'failed')",
        )
    try:
        approval_queue.requeue(draft_id, actor=_REVIEWER)
    except approval_queue.ConcurrentTransitionError as e:
        raise HTTPException(status_code=409, detail=str(e))
    return RedirectResponse("/bridge/drafts", status_code=303)


# ---------------------------------------------------------------------------
# Agent roster API
# ---------------------------------------------------------------------------


@router.get("/api/agents")
def agents_list() -> dict:
    """回傳 9 個 agent 的定義 + 今日 token / run 統計。

    State 邏輯：
    - default_state == "offline" → 永遠 offline
    - default_state == "hold"    → 永遠 hold
    - 有今日 api_calls            → "online"
    - 否則                        → "idle"
    """
    today_rows = state.get_cost_summary(days=1)

    # Aggregate by agent key (sum across models)
    today_by_agent: dict[str, dict] = {}
    for row in today_rows:
        key = row["agent"]
        if key not in today_by_agent:
            today_by_agent[key] = {
                "calls": 0,
                "input_tokens": 0,
                "output_tokens": 0,
                "cache_read_tokens": 0,
                "cache_write_tokens": 0,
            }
        today_by_agent[key]["calls"] += row.get("calls") or 0
        today_by_agent[key]["input_tokens"] += row.get("input_tokens") or 0
        today_by_agent[key]["output_tokens"] += row.get("output_tokens") or 0
        today_by_agent[key]["cache_read_tokens"] += row.get("cache_read_tokens") or 0
        today_by_agent[key]["cache_write_tokens"] += row.get("cache_write_tokens") or 0

    result = []
    for a in AGENT_ROSTER:
        key = a["key"]
        usage = today_by_agent.get(key, {})
        tok_today = (usage.get("input_tokens") or 0) + (usage.get("output_tokens") or 0)
        runs_today = usage.get("calls") or 0

        # Derive state
        if a["default_state"] in ("offline", "hold"):
            agent_state = a["default_state"]
        elif runs_today > 0:
            agent_state = "online"
        else:
            agent_state = "idle"

        # Cost estimate for today
        cost_today = round(
            calc_cost(
                a["model"],
                input_tokens=usage.get("input_tokens") or 0,
                output_tokens=usage.get("output_tokens") or 0,
                cache_read_tokens=usage.get("cache_read_tokens") or 0,
                cache_write_tokens=usage.get("cache_write_tokens") or 0,
            ),
            4,
        )

        result.append(
            {
                "code": a["code"],
                "key": key,
                "role": a["role"],
                "en": a["en"],
                "model": a["model"],
                "state": agent_state,
                "tok_today": tok_today,
                "runs_today": runs_today,
                "cost_today": cost_today,
            }
        )

    return {"agents": result}


# ---------------------------------------------------------------------------
# Memory API
# ---------------------------------------------------------------------------


def _default_user_id() -> str:
    """單一使用者專案用 env 決定 user_id。"""
    return os.environ.get("SLACK_USER_ID_SHOSHO") or os.environ.get(
        "NAKAMA_DEFAULT_USER_ID", "shosho"
    )


class MemoryUpdate(BaseModel):
    """PATCH payload — 只傳有要改的欄位。"""

    type: Optional[str] = Field(None, description="preference / fact / decision / context")
    subject: Optional[str] = None
    content: Optional[str] = None
    confidence: Optional[float] = Field(None, ge=0.0, le=1.0)


@router.get("/api/memory/agents")
def memory_agents() -> dict:
    """列出目前有記憶資料的 agent（給前端 tab 用）。"""
    return {"agents": agent_memory.list_agents_with_memory()}


@router.get("/api/memory")
def memory_list(
    agent: str = Query(..., min_length=1),
    user_id: Optional[str] = Query(None),
    limit: int = Query(100, ge=1, le=500),
) -> dict:
    """列出該 agent 的所有 user_memories（不更新 last_accessed_at）。"""
    uid = user_id or _default_user_id()
    rows = agent_memory.list_all(agent=agent, user_id=uid, limit=limit)
    return {
        "agent": agent,
        "user_id": uid,
        "memories": [
            {
                "id": m.id,
                "agent": m.agent,
                "user_id": m.user_id,
                "type": m.type,
                "subject": m.subject,
                "content": m.content,
                "confidence": m.confidence,
                "source_thread": m.source_thread,
                "created_at": m.created_at,
                "last_accessed_at": m.last_accessed_at,
            }
            for m in rows
        ],
    }


@router.patch("/api/memory/{memory_id}")
def memory_update(memory_id: int, payload: MemoryUpdate) -> dict:
    """編輯一筆記憶。傳入的 None 欄位不動。"""
    try:
        updated = agent_memory.update(
            memory_id,
            type=payload.type,
            subject=payload.subject,
            content=payload.content,
            confidence=payload.confidence,
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    if updated is None:
        raise HTTPException(status_code=404, detail="memory not found")
    return {
        "id": updated.id,
        "agent": updated.agent,
        "user_id": updated.user_id,
        "type": updated.type,
        "subject": updated.subject,
        "content": updated.content,
        "confidence": updated.confidence,
        "source_thread": updated.source_thread,
        "created_at": updated.created_at,
        "last_accessed_at": updated.last_accessed_at,
    }


@router.delete("/api/memory/{memory_id}")
def memory_delete(memory_id: int) -> dict:
    """刪除一筆記憶。"""
    if not agent_memory.forget(memory_id):
        raise HTTPException(status_code=404, detail="memory not found")
    return {"ok": True, "id": memory_id}


# ---------------------------------------------------------------------------
# Cost API
# ---------------------------------------------------------------------------


_ALLOWED_RANGES: dict[str, tuple[int, str]] = {
    # key -> (days, bucket)
    "24h": (1, "hour"),
    "7d": (7, "day"),
    "30d": (30, "day"),
}


@router.get("/api/cost")
def cost_overview(
    range: str = Query("7d"),
    agent: Optional[str] = Query(None),
) -> dict:
    """回傳 summary + timeseries + pricing 給前端一次畫圖。"""
    if range not in _ALLOWED_RANGES:
        raise HTTPException(
            status_code=400,
            detail=f"range must be one of {list(_ALLOWED_RANGES)}",
        )
    days, bucket = _ALLOWED_RANGES[range]

    summary_rows = state.get_cost_summary(agent=agent, days=days)
    timeseries_rows = state.get_cost_timeseries(agent=agent, days=days, bucket=bucket)
    latency_rows = state.get_latency_summary(agent=agent, days=days)

    def _enrich(row: dict) -> dict:
        cost = calc_cost(
            row["model"],
            input_tokens=row.get("input_tokens") or 0,
            output_tokens=row.get("output_tokens") or 0,
            cache_read_tokens=row.get("cache_read_tokens") or 0,
            cache_write_tokens=row.get("cache_write_tokens") or 0,
        )
        return {**row, "cost_usd": round(cost, 6)}

    summary = [_enrich(r) for r in summary_rows]
    timeseries = [_enrich(r) for r in timeseries_rows]

    total_cost = round(sum(r["cost_usd"] for r in summary), 6)

    # 列出這批資料實際用到的 models 的 pricing（前端 tooltip 用）
    models_seen = {r["model"] for r in summary_rows}
    pricing_map = {m: get_pricing(m).to_dict() for m in sorted(models_seen)}

    return {
        "range": range,
        "days": days,
        "bucket": bucket,
        "agent_filter": agent,
        "total_cost_usd": total_cost,
        "summary": summary,
        "timeseries": timeseries,
        "latency": latency_rows,
        "pricing": pricing_map,
    }
