"""Brook routes — context handoff to Claude.ai (ADR-027 §Decision 8).

Per ADR-027, the original `/brook/chat` conversational composer was removed:
- No local LLM chat reply loop.
- No SQLite `brook_conversations` / `brook_messages` persistence.
- No `export_draft` endpoint.
- No sliding-window context management.

What remains is the **context packaging** logic — assembled into
``agents/brook/context_bridge.py`` and surfaced at ``GET /brook/handoff``.
The page renders a packaged prompt the owner copies into Claude.ai.

Legacy URLs ``/brook/chat`` and ``/brook/bridge`` 301 to ``/brook/handoff``
(link-rot mitigation; kept indefinitely for non-conflicting bookmarks per
Codex audit §4).
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


def _shosho_asset_version() -> str:
    """Return an 8-char hash of the Shosho design-system CSS files.

    Used to bust Cloudflare's /static/* edge cache when tokens.css or
    brook-handoff.css change. Matches the asset-versioning pattern in
    ``robin.py`` / ``progress.py`` / ``architecture.py``.
    """
    import hashlib

    static_dir = Path(__file__).resolve().parent.parent / "static" / "shosho"
    h = hashlib.sha1()
    for css in ("tokens.css", "brook-handoff.css", "theme.js"):
        path = static_dir / css
        if path.exists():
            h.update(path.read_bytes())
    return h.hexdigest()[:8]


_SHOSHO_ASSET_VERSION = _shosho_asset_version()


@router.get("/chat")
async def brook_chat_redirect():
    """Legacy URL — 301 to /brook/handoff (collapsed chain per Codex §1)."""
    return RedirectResponse("/brook/handoff", status_code=301)


@router.get("/bridge")
async def brook_bridge_redirect():
    """Legacy URL — 301 to /brook/handoff (URL renamed per /architecture v2)."""
    return RedirectResponse("/brook/handoff", status_code=301)


@router.get("/handoff", response_class=HTMLResponse)
async def brook_handoff_page(
    request: Request,
    topic: str | None = None,
    project_slug: str | None = None,
    source_slug: str | None = None,
    kb_query: str | None = None,
    category: str | None = None,
    nakama_auth: str | None = Cookie(None),
):
    """Render the context handoff page.

    Without query params: shows the input form only.
    With ``topic``: packages context + renders the prompt block for copying.
    """
    if not check_auth(nakama_auth):
        return RedirectResponse("/login?next=/brook/handoff", status_code=302)

    ctx: dict = {
        "topic": topic,
        "project_slug": project_slug,
        "source_slug": source_slug,
        "kb_query": kb_query,
        "category": category,
        "packaged": None,
        "asset_version": _SHOSHO_ASSET_VERSION,
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
            logger.error("brook handoff package error: %s", exc, exc_info=True)
            ctx["packaged"] = None

    return templates.TemplateResponse(request, "brook_handoff.html", ctx)
