"""Thousand Sunny — Nakama web server entry point."""

from fastapi import FastAPI

from thousand_sunny.routers import brook, robin, zoro

app = FastAPI(docs_url=None, redoc_url=None)

app.include_router(robin.router)
app.include_router(zoro.router)
app.include_router(brook.router)
