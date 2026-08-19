
"""Lightweight local entry point for the Stage 5 finished-cut review gate.

The full Thousand Sunny composition root intentionally imports every agent
surface.  Editors reviewing local video artifacts should not need Robin,
WordPress, Slack, or scraping dependencies just to open this fail-closed gate.
"""

from __future__ import annotations

from pathlib import Path

from dotenv import load_dotenv
from fastapi import FastAPI
from fastapi.responses import RedirectResponse
from fastapi.staticfiles import StaticFiles

_REPO_ROOT = Path(__file__).resolve().parent.parent
load_dotenv(_REPO_ROOT / ".env")

# Auth reads its configuration at import time, so dotenv loading must happen
# before importing either router.
from thousand_sunny.routers import auth as auth_routes  # noqa: E402
from thousand_sunny.routers import highlight_review, packaging, publish_review  # noqa: E402

app = FastAPI(title="Nakama Finished Review", docs_url=None, redoc_url=None)
app.include_router(auth_routes.router)
app.include_router(highlight_review.page_router)
app.include_router(packaging.page_router)
app.include_router(publish_review.page_router)

_static_dir = Path(__file__).resolve().parent / "static"
app.mount("/static", StaticFiles(directory=str(_static_dir)), name="static")


@app.get("/", include_in_schema=False)
async def root() -> RedirectResponse:
    return RedirectResponse("/login", status_code=302)


@app.get("/healthz", include_in_schema=False)
async def healthz() -> dict[str, str]:
    return {"status": "ok", "surface": "finished-review"}
