"""Thousand Sunny — Nakama web server entry point."""

import os

from fastapi import FastAPI
from fastapi.responses import RedirectResponse

from thousand_sunny.routers import auth, brook, zoro

app = FastAPI(docs_url=None, redoc_url=None)

app.include_router(auth.router)

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
