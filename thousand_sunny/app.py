"""Thousand Sunny — Nakama web server entry point."""

import os

from fastapi import FastAPI

from thousand_sunny.routers import brook, zoro

app = FastAPI(docs_url=None, redoc_url=None)

# Robin（KB ingest + reader）僅本機執行，VPS 設 DISABLE_ROBIN=1 跳過
if not os.getenv("DISABLE_ROBIN"):
    from thousand_sunny.routers import robin

    app.include_router(robin.router)

app.include_router(zoro.router)
app.include_router(brook.router)
