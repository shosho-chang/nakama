"""Thousand Sunny — Nakama web server entry point."""

import os

from fastapi import FastAPI
from fastapi.responses import RedirectResponse

# Windows uvicorn inherits cp1252 stdout/stderr → any 中文 log message would
# raise UnicodeEncodeError per-record (logging then floods stderr with stack
# traces and silently drops the message). Force UTF-8 BEFORE any router
# import — routers create module-level loggers via shared.log.get_logger,
# which captures sys.stdout at handler-attach time.
from shared.log import force_utf8_console

force_utf8_console()

from thousand_sunny.routers import (  # noqa: E402
    auth,
    bridge,
    bridge_zoro,
    brook,
    franky,
    repurpose,
    zoro,
)

app = FastAPI(docs_url=None, redoc_url=None)

app.include_router(auth.router)
app.include_router(bridge.router)
app.include_router(bridge.page_router)
app.include_router(bridge_zoro.page_router)
app.include_router(repurpose.page_router)
# Franky /healthz must be mounted unconditionally — UptimeRobot probes this regardless of
# DISABLE_ROBIN or any other feature flag (ADR-007 §2).
app.include_router(franky.router)
app.include_router(franky.page_router)

# Robin（KB ingest + reader）僅本機執行，VPS 設 DISABLE_ROBIN=1 跳過
if not os.getenv("DISABLE_ROBIN"):
    from thousand_sunny.routers import robin

    app.include_router(robin.router)
else:

    @app.get("/")
    async def root_redirect():
        return RedirectResponse("/brook/chat", status_code=302)


app.include_router(zoro.router)
app.include_router(brook.router)
