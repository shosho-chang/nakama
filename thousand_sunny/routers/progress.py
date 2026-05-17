"""Public partner-facing progress page (unauthenticated).

Serves a single static HTML at ``/progress`` summarising Nakama's
Lines × Stages, Agents × Stages, and Channels × Stages readiness for
external partners. No auth, no DB — readiness is hand-curated in the
HTML and committed to git as the single source of truth.

This breaks the all-authenticated pattern intentionally; see the
``/healthz`` precedent (franky.router) for the prior public route.
"""

from pathlib import Path

from fastapi import APIRouter
from fastapi.responses import FileResponse

router = APIRouter()

_PAGE_PATH = (
    Path(__file__).resolve().parent.parent / "static" / "progress" / "index.html"
)


@router.get("/progress", include_in_schema=False)
async def progress_page() -> FileResponse:
    return FileResponse(str(_PAGE_PATH), media_type="text/html; charset=utf-8")
