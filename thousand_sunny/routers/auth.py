"""Auth routes — shared login/logout for all agents."""

from pathlib import Path
from urllib.parse import urlparse

from fastapi import APIRouter, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates

from thousand_sunny.auth import WEB_PASSWORD, make_token

router = APIRouter()
templates = Jinja2Templates(
    directory=str(Path(__file__).resolve().parent.parent / "templates" / "auth")
)


def _safe_next(next_url: str | None, fallback: str = "/") -> str:
    """只允許相對路徑，避免 open redirect。"""
    if not next_url:
        return fallback
    parsed = urlparse(next_url)
    if parsed.scheme or parsed.netloc:
        return fallback
    if not next_url.startswith("/"):
        return fallback
    return next_url


@router.get("/login", response_class=HTMLResponse)
async def login_page(request: Request, next: str | None = None):
    return templates.TemplateResponse(
        request,
        "login.html",
        {"error": None, "next": _safe_next(next)},
    )


@router.post("/login")
async def login(
    request: Request,
    password: str = Form(...),
    next: str = Form("/"),
):
    if not WEB_PASSWORD or password == WEB_PASSWORD:
        response = RedirectResponse(_safe_next(next), status_code=302)
        response.set_cookie("robin_auth", make_token(password), httponly=True)
        return response
    return templates.TemplateResponse(
        request,
        "login.html",
        {"error": "密碼錯誤", "next": _safe_next(next)},
        status_code=401,
    )


@router.post("/logout")
async def logout():
    response = RedirectResponse("/login", status_code=302)
    response.delete_cookie("robin_auth")
    return response
