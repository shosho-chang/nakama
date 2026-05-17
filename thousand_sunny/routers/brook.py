"""Brook routes — context bridge to Claude.ai (ADR-027 §Decision 8).

Per ADR-027, the original `/brook/chat` conversational composer was removed:
- No local LLM chat reply loop.
- No SQLite `brook_conversations` / `brook_messages` persistence.
- No `export_draft` endpoint.
- No sliding-window context management.

What remains is the **context packaging** logic — assembled into
``agents/brook/context_bridge.py`` and surfaced at ``GET /brook/bridge``
(with a 301 redirect from the old ``/brook/chat`` URL for one release
cycle). The page renders a packaged prompt the owner copies into Claude.ai.
"""

from __future__ import annotations

import asyncio
from pathlib import Path

from fastapi import APIRouter, Cookie, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates

from agents.brook.context_bridge import package_context
from shared.config import get_vault_path
from shared.log import get_logger
from thousand_sunny.auth import check_auth

logger = get_logger("nakama.web.brook")
router = APIRouter(prefix="/brook")
templates = Jinja2Templates(
    directory=str(Path(__file__).resolve().parent.parent / "templates" / "brook")
)


@router.get("/chat")
async def brook_chat_redirect():
    """Legacy URL — 301 redirect to /brook/bridge for one release cycle.

    Drop in the next major after ADR-027 lands. Keeping this avoids link rot
    in bookmarks / Obsidian buttons that were pointing at the old route.
    """
    return RedirectResponse("/brook/bridge", status_code=301)


@router.get("/bridge", response_class=HTMLResponse)
async def brook_bridge_page(
    request: Request,
    topic: str | None = None,
    project_slug: str | None = None,
    source_slug: str | None = None,
    kb_query: str | None = None,
    category: str | None = None,
    nakama_auth: str | None = Cookie(None),
):
    """Render the context bridge page.

    Without query params: shows the input form only.
    With ``topic``: packages context + renders the prompt block for copying.
    """
    if not check_auth(nakama_auth):
        return RedirectResponse("/login?next=/brook/bridge", status_code=302)

    ctx: dict = {
        "topic": topic,
        "project_slug": project_slug,
        "source_slug": source_slug,
        "kb_query": kb_query,
        "category": category,
        "packaged": None,
    }

    if topic and topic.strip():
        vault_path = get_vault_path()
        try:
            packaged = await asyncio.to_thread(
                package_context,
                topic=topic,
                vault_path=vault_path,
                project_slug=project_slug or None,
                source_slug=source_slug or None,
                kb_query=kb_query or None,
                category=category or None,
            )
            ctx["packaged"] = packaged
        except Exception as exc:
            logger.error("brook bridge package error: %s", exc, exc_info=True)
            # Render the form with an error hint rather than 500 — bridge
            # is the owner's hand-off path; a hard failure pushes them off
            # the rail. The summary section will just be missing.
            ctx["packaged"] = None

    return templates.TemplateResponse(request, "brook_bridge.html", ctx)
