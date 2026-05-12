"""Thousand Sunny — Nakama web server entry point."""

import os
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import RedirectResponse
from fastapi.staticfiles import StaticFiles

# Windows uvicorn inherits cp1252 stdout/stderr → any 中文 log message would
# raise UnicodeEncodeError per-record (logging then floods stderr with stack
# traces and silently drops the message). Force UTF-8 BEFORE any router
# import — routers create module-level loggers via shared.log.get_logger,
# which captures sys.stdout at handler-attach time.
from shared.log import force_utf8_console, get_logger

force_utf8_console()

from thousand_sunny.middleware.csp import add_csp_middleware  # noqa: E402
from thousand_sunny.promotion_wiring import (  # noqa: E402
    load_promotion_wiring_config,
    wire_promotion_surfaces,
)
from thousand_sunny.routers import (  # noqa: E402
    auth,
    bridge,
    bridge_zoro,
    brook,
    franky,
    projects,
    promotion_review,
    repurpose,
    writing_assist,
    zoro,
)

_logger = get_logger("nakama.web.app")


# ── ADR-024 Promotion wiring (N518a-b / issue #540) ─────────────────────────
#
# The env → adapter → service injection plumbing lives in
# ``thousand_sunny.promotion_wiring`` (extracted from this file in N518b
# C2 carry-over). ``app.py`` only owns route inclusion, middleware setup,
# and the FastAPI lifespan that triggers the wiring.


@asynccontextmanager
async def _lifespan(app_: FastAPI):
    """FastAPI lifespan that wires ADR-024 promotion surfaces at startup.

    Honours ``DISABLE_ROBIN=1`` (mirrors the existing routes-level guard
    below) — when set, the promotion services are NOT constructed and the
    routers fall through to their 503 default. The ``/`` redirect path
    elsewhere still works.

    Startup failures (missing ``VAULT_PATH``, unknown promotion
    mode) propagate as ``RuntimeError`` so uvicorn / systemd surface the
    crash to the operator (W4) — silent fallback would mask the misconfig.
    """
    if not os.getenv("DISABLE_ROBIN"):
        config = load_promotion_wiring_config()
        wire_promotion_surfaces(config)
    yield
    # No teardown wired in N518 — services hold no per-request state.


app = FastAPI(docs_url=None, redoc_url=None, lifespan=_lifespan)

# Reader CSP must be installed BEFORE routes so middleware wraps everything.
add_csp_middleware(app)

app.include_router(auth.router)
app.include_router(bridge.router)
app.include_router(bridge.page_router)
app.include_router(bridge_zoro.page_router)
app.include_router(repurpose.page_router)
# Franky /healthz must be mounted unconditionally — UptimeRobot probes this regardless of
# DISABLE_ROBIN or any other feature flag (ADR-007 §2).
app.include_router(franky.router)
app.include_router(franky.page_router)

# /static must mount unconditionally — /projects/{slug} (issue #458) ships with
# Robin disabled (VPS) too, and pulls /static/projects/{tokens,review}.css/js.
_static_dir = Path(__file__).resolve().parent / "static"
if _static_dir.is_dir():
    app.mount(
        "/static",
        StaticFiles(directory=str(_static_dir)),
        name="static",
    )

# Robin（KB ingest + reader）僅本機執行，VPS 設 DISABLE_ROBIN=1 跳過
if not os.getenv("DISABLE_ROBIN"):
    from thousand_sunny.routers import books, robin

    app.include_router(robin.router)
    app.include_router(books.router)

    # foliate-js must be served from the same origin as /books/* so CSP
    # ``script-src 'self'`` allows it. Mount the vendored submodule under
    # /vendor/foliate-js/ as static files. Missing dir (fresh checkout
    # forgot ``git submodule update --init``) → skip the mount and log;
    # the reader page will fail to load JS but /books library still works.
    _foliate_dir = Path(__file__).resolve().parent.parent / "vendor" / "foliate-js"
    if _foliate_dir.is_dir():
        app.mount(
            "/vendor/foliate-js",
            StaticFiles(directory=str(_foliate_dir)),
            name="foliate-js",
        )
else:

    @app.get("/")
    async def root_redirect():
        return RedirectResponse("/brook/chat", status_code=302)


app.include_router(zoro.router)
app.include_router(brook.router)
app.include_router(projects.router)
app.include_router(projects.page_router)

# Promotion Review UI (ADR-024 Slice 8 / issue #516). Production service
# wiring lives in the ``_lifespan`` context manager above (N518a / #540).
# Without DISABLE_ROBIN the lifespan calls ``set_service`` so requests are
# served. With DISABLE_ROBIN=1 the lifespan skips wiring and the routes
# fall through to their 503 default (which the VPS deployment expects).
# Tests reload the module to inject a fake service.
app.include_router(promotion_review.router)

# Writing Assist scaffold (ADR-024 Slice 9 / issue #517). Same dependency-
# injection pattern as #516 — service wired by ``_lifespan`` above. The
# route NEVER composes prose — only renders scaffold structure; the surface
# enforces W1-W7 no-ghostwriting invariants.
app.include_router(writing_assist.router)
