"""Bridge UI surfaces under ``/bridge/zoro/`` — agent-rooted Zoro tools.

Per ADR-012, Zoro owns the "向外搜尋" (outbound scout) lane: keyword research,
trends, SERP. The agent-rooted ``/bridge/zoro/...`` namespace exists to host
these tools, distinct from the topic-rooted ``/bridge/seo`` SEO control center
(which mixes Brook audit + Zoro research data and stays topic-rooted).

Slice 7 of SEO 中控台 v1 lands ``/bridge/zoro/keyword-research`` — a thin web
wrapper around the existing ``agents.zoro.keyword_research.research_keywords``
pipeline. The form synchronously runs the ~30-60s research and renders the
markdown report inline; users get a ``下載 .md`` button to save the file
themselves. **Vault writes stay forbidden here** — the LifeOS Project
dataviewjs path is the only writer, by design (issue #231 acceptance).

Slice 2 of PRD #255 (#258 / A′) adds:
- POST side-effect: persist successful runs to ``keyword_research_runs``
  table with ``triggered_by='web'`` (best-effort; never breaks render)
- GET /history — paginated list of past runs
- GET /history/{id} — detail with re-rendered markdown + .md re-download
"""

from __future__ import annotations

import asyncio
import re
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Cookie, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, PlainTextResponse, RedirectResponse
from fastapi.templating import Jinja2Templates

from agents.zoro.report_renderer import build_frontmatter, render_markdown
from shared import keyword_research_history_store as kr_history
from shared.log import get_logger
from thousand_sunny.auth import check_auth

logger = get_logger("nakama.web.bridge_zoro")

# Form input ceilings — keep modest so a runaway paste doesn't end up in
# Zoro's ThreadPoolExecutor or in Claude's 4096-token prompt slot.
_TOPIC_MAX_CHARS = 200
_EN_TOPIC_MAX_CHARS = 200

page_router = APIRouter(prefix="/bridge/zoro")
_templates = Jinja2Templates(
    directory=str(Path(__file__).resolve().parent.parent / "templates" / "bridge")
)


def _slugify_topic(topic: str) -> str:
    """ASCII-safe slug for the download filename.

    Keeps Latin letters / digits / hyphens / underscores; everything else
    (including CJK) gets collapsed to ``_``. We don't translate — users get
    a recognisable stub of their input even if it's all CJK (becomes
    ``______``-ish, still better than crashing on browser download).
    """
    slug = re.sub(r"[^A-Za-z0-9_-]+", "_", topic.strip()).strip("_")
    return slug[:50] or "topic"


def _today_taipei_yyyymmdd() -> str:
    """Today's date in Asia/Taipei as YYYYMMDD — used in download filenames.

    See ``memory/claude/reference_lifeos_task_frontmatter.md`` and
    ``feedback_date_filename_review_checklist.md``: any daily-rotating path
    must use ``ZoneInfo("Asia/Taipei")`` so the VPS cron and local dev box
    both produce the same string.
    """
    return datetime.now(ZoneInfo("Asia/Taipei")).strftime("%Y%m%d")


def _download_filename(topic: str) -> str:
    """Filename for the ``下載 .md`` button: ``kw-research-<slug>-<YYYYMMDD>.md``."""
    return f"kw-research-{_slugify_topic(topic)}-{_today_taipei_yyyymmdd()}.md"


@page_router.get("/keyword-research", response_class=HTMLResponse)
async def keyword_research_page(
    request: Request,
    nakama_auth: str | None = Cookie(None),
):
    """Form page: topic + content_type + en_topic.

    Cookie auth gate. Empty form on first hit; the same template re-renders
    on POST with the markdown report inline + a ``下載 .md`` button.
    """
    if not check_auth(nakama_auth):
        return RedirectResponse("/login?next=/bridge/zoro/keyword-research", status_code=302)
    return _templates.TemplateResponse(
        request,
        "zoro_keyword_research.html",
        {
            "phase": "form",
            "topic": "",
            "content_type": "blog",
            "en_topic": "",
            "report_md": None,
            "download_filename": None,
            "error": None,
        },
    )


@page_router.post("/keyword-research", response_class=HTMLResponse)
async def keyword_research_run(
    request: Request,
    topic: str = Form(..., max_length=_TOPIC_MAX_CHARS),
    content_type: str = Form("blog"),
    en_topic: str = Form("", max_length=_EN_TOPIC_MAX_CHARS),
    nakama_auth: str | None = Cookie(None),
):
    """Synchronously run keyword research and render the report inline.

    Sync block (~30-60s, depends on data-source latency) by design — Y+ tier
    UX is a spinner over the form; we don't introduce a worker queue for a
    single-trigger user-driven action (PRD §"Audit kick-off" rationale, same
    pattern). Failures render an error page with a retry link instead of
    bubbling 500s.
    """
    if not check_auth(nakama_auth):
        return RedirectResponse("/login?next=/bridge/zoro/keyword-research", status_code=302)

    topic_clean = topic.strip()
    if not topic_clean:
        # 400 (not 422) per acceptance criterion — a valid form post with
        # whitespace-only topic is a UX-level validation failure, not a
        # framework type-coerce miss.
        raise HTTPException(status_code=400, detail="topic 不能空白")
    if content_type not in ("blog", "youtube"):
        # Defensive — frontend select offers only these two, but a hand-crafted
        # POST shouldn't sneak ``twitter`` past us.
        raise HTTPException(status_code=400, detail="content_type 必須是 blog 或 youtube")

    en_topic_clean = en_topic.strip() or None

    try:
        from agents.zoro.keyword_research import research_keywords

        result = await asyncio.to_thread(
            research_keywords,
            topic_clean,
            content_type,
            en_topic_clean,
        )
    except RuntimeError as e:
        # All upstream sources failed (research_keywords raises this when the
        # parallel collector pool returns no usable data).
        logger.warning(f"keyword research RuntimeError for topic={topic_clean!r}: {e}")
        return _templates.TemplateResponse(
            request,
            "zoro_keyword_research.html",
            {
                "phase": "error",
                "topic": topic_clean,
                "content_type": content_type,
                "en_topic": en_topic_clean or "",
                "report_md": None,
                "download_filename": None,
                "error": str(e),
            },
        )
    except Exception as e:
        # Unknown failure — log full trace, show user-friendly retry page.
        # Don't crash; a 500 from the research orchestrator is a UX bug for
        # this surface (synchronous workflow, no resume), not the user's
        # problem.
        logger.error(f"keyword research crashed for topic={topic_clean!r}: {e}", exc_info=True)
        return _templates.TemplateResponse(
            request,
            "zoro_keyword_research.html",
            {
                "phase": "error",
                "topic": topic_clean,
                "content_type": content_type,
                "en_topic": en_topic_clean or "",
                "report_md": None,
                "download_filename": None,
                "error": f"研究流程失敗：{type(e).__name__}: {e}",
            },
        )

    frontmatter = build_frontmatter(
        topic_clean,
        en_topic_clean or "",
        content_type,
        result,
    )
    report_md = render_markdown(frontmatter, result)
    filename = _download_filename(topic_clean)

    # Slice 2 (#258): persist the run so the user can browse history. This is
    # best-effort — a db failure must not break the rendered report (the user
    # already paid the 30-60s research wait).
    try:
        kr_history.insert_run(
            topic=topic_clean,
            en_topic=en_topic_clean,
            content_type=content_type,  # type: ignore[arg-type]
            report_md=report_md,
            triggered_by="web",
        )
    except Exception as e:  # noqa: BLE001 — defensive; never break the user view
        logger.warning(f"keyword_research_runs persist failed for topic={topic_clean!r}: {e}")

    return _templates.TemplateResponse(
        request,
        "zoro_keyword_research.html",
        {
            "phase": "result",
            "topic": topic_clean,
            "content_type": content_type,
            "en_topic": en_topic_clean or "",
            "report_md": report_md,
            "download_filename": filename,
            "error": None,
        },
    )


# ── Slice 2 (#258 / A′) — keyword-research history ──────────────────────


_HISTORY_PAGE_SIZE = 20


@page_router.get("/keyword-research/history", response_class=HTMLResponse)
async def keyword_research_history_list(
    request: Request,
    offset: int = 0,
    nakama_auth: str | None = Cookie(None),
):
    """Paginated list of past keyword-research runs.

    Pagination contract:
    - Page size fixed at ``_HISTORY_PAGE_SIZE`` (20).
    - ``?offset=N`` advances; client decides what N to send via the
      "Previous" / "Next" links the template renders.
    - Negative offsets coerced to 0; offsets past the tail are clamped so
      the user gets the last page rather than an empty page.
    """
    if not check_auth(nakama_auth):
        return RedirectResponse(
            "/login?next=/bridge/zoro/keyword-research/history", status_code=302
        )

    total = kr_history.count_runs()
    if total == 0:
        safe_offset = 0
    else:
        # Clamp to a valid page boundary: max start is the offset of the last page.
        last_page_offset = ((total - 1) // _HISTORY_PAGE_SIZE) * _HISTORY_PAGE_SIZE
        safe_offset = max(0, min(offset, last_page_offset))
    rows = kr_history.list_runs(limit=_HISTORY_PAGE_SIZE, offset=safe_offset)

    # Pre-format created_at for display (Asia/Taipei).
    display_rows = [
        {**r, "created_at_taipei": kr_history.to_taipei_display(r["created_at"])} for r in rows
    ]

    has_prev = safe_offset > 0
    has_next = safe_offset + _HISTORY_PAGE_SIZE < total
    prev_offset = max(0, safe_offset - _HISTORY_PAGE_SIZE)
    next_offset = safe_offset + _HISTORY_PAGE_SIZE

    return _templates.TemplateResponse(
        request,
        "zoro_keyword_research_history.html",
        {
            "rows": display_rows,
            "total": total,
            "page_size": _HISTORY_PAGE_SIZE,
            "offset": safe_offset,
            "has_prev": has_prev,
            "has_next": has_next,
            "prev_offset": prev_offset,
            "next_offset": next_offset,
        },
    )


@page_router.get("/keyword-research/history/{run_id}", response_class=HTMLResponse)
async def keyword_research_history_detail(
    request: Request,
    run_id: int,
    nakama_auth: str | None = Cookie(None),
):
    """Detail view of a single past run — full markdown + re-download.

    Re-download POSTs to the existing ``/keyword-research/download`` endpoint
    with the stored ``report_md`` as a hidden form field, so the file the user
    saves is byte-identical to the row in db. This avoids re-running research
    (which would consume LLM tokens and could yield different output).
    """
    if not check_auth(nakama_auth):
        return RedirectResponse(
            f"/login?next=/bridge/zoro/keyword-research/history/{run_id}",
            status_code=302,
        )

    row = kr_history.get_run(run_id)
    if row is None:
        raise HTTPException(status_code=404, detail="找不到這筆研究紀錄")

    # The download endpoint computes its own filename from topic + today's
    # date. For consistency with what the user sees, we compute it here for
    # display and let the download endpoint regenerate it on submit (which
    # uses *today's* date — by design, since the user is downloading today).
    display_filename = _download_filename(row["topic"])

    return _templates.TemplateResponse(
        request,
        "zoro_keyword_research_history_detail.html",
        {
            "row": row,
            "created_at_taipei": kr_history.to_taipei_display(row["created_at"]),
            "download_filename": display_filename,
        },
    )


@page_router.post("/keyword-research/download", response_class=PlainTextResponse)
async def keyword_research_download(
    topic: str = Form(..., max_length=_TOPIC_MAX_CHARS),
    content_type: str = Form("blog"),
    en_topic: str = Form("", max_length=_EN_TOPIC_MAX_CHARS),
    report_md: str = Form(...),
    nakama_auth: str | None = Cookie(None),
):
    """Server-side ``Content-Disposition: attachment`` for the markdown report.

    The result page also offers a client-side blob download via JS, but this
    endpoint exists as a no-JS fallback and as the primary path the form
    actually posts to. We don't re-run research here — the report body is
    round-tripped through a hidden form field, so the download is
    deterministic vs. whatever the user already sees on screen.
    """
    if not check_auth(nakama_auth):
        return RedirectResponse("/login?next=/bridge/zoro/keyword-research", status_code=302)

    topic_clean = topic.strip() or "topic"
    filename = _download_filename(topic_clean)
    return PlainTextResponse(
        content=report_md,
        headers={
            "Content-Disposition": f'attachment; filename="{filename}"',
            "Content-Type": "text/markdown; charset=utf-8",
        },
    )
