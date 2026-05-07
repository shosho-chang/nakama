"""Thousand Sunny — Nakama web server entry point."""

import os
from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import RedirectResponse
from fastapi.staticfiles import StaticFiles

# Windows uvicorn inherits cp1252 stdout/stderr → any 中文 log message would
# raise UnicodeEncodeError per-record (logging then floods stderr with stack
# traces and silently drops the message). Force UTF-8 BEFORE any router
# import — routers create module-level loggers via shared.log.get_logger,
# which captures sys.stdout at handler-attach time.
from shared.log import force_utf8_console

force_utf8_console()

from thousand_sunny.middleware.csp import add_csp_middleware  # noqa: E402
from thousand_sunny.routers import (  # noqa: E402
    auth,
    bridge,
    bridge_zoro,
    brook,
    franky,
    projects,
    repurpose,
    zoro,
)

app = FastAPI(docs_url=None, redoc_url=None)

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
