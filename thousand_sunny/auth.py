"""Shared authentication — cookie + API key auth for all agents."""

import hashlib
import hmac
import os

from fastapi import Cookie, Header, HTTPException

from shared.log import get_logger

logger = get_logger("nakama.web.auth")

WEB_PASSWORD = os.environ.get("WEB_PASSWORD", "")
WEB_SECRET = os.environ.get("WEB_SECRET", "")
_DEV_AUTH_BYPASS = os.environ.get("NAKAMA_DEV_AUTH_BYPASS", "").strip().lower() in (
    "1",
    "true",
    "yes",
)

if _DEV_AUTH_BYPASS:
    logger.warning(
        "NAKAMA_DEV_AUTH_BYPASS=1 — all web auth checks short-circuited. "
        "This MUST NOT be set on VPS / production."
    )


def make_token(password: str) -> str:
    return hmac.new(WEB_SECRET.encode(), password.encode(), hashlib.sha256).hexdigest()


# Persistent auth cookie: 90 days. The token is a static HMAC(WEB_SECRET,
# WEB_PASSWORD) (no per-session state), so persisting it just spares users —
# especially mobile/iPad behind Cloudflare Access — from re-entering
# WEB_PASSWORD every time the browser drops the session cookie. WEB_PASSWORD
# stays the second defense-in-depth layer behind Access per ADR-044.
AUTH_COOKIE_MAX_AGE = 90 * 24 * 60 * 60  # seconds


def set_auth_cookie(response, token: str) -> None:
    """Set the nakama_auth cookie with consistent persistent flags.

    Single source of truth for the cookie attributes so every place that
    issues it (login route, upload redirect) stays in lockstep.
    """
    response.set_cookie(
        "nakama_auth",
        token,
        max_age=AUTH_COOKIE_MAX_AGE,
        httponly=True,
        secure=True,
        samesite="lax",
    )


def check_auth(auth_cookie: str | None) -> bool:
    if _DEV_AUTH_BYPASS:
        return True
    if not WEB_PASSWORD:
        return True
    if not auth_cookie:
        return False
    return hmac.compare_digest(auth_cookie, make_token(WEB_PASSWORD))


def check_key(key: str | None) -> bool:
    """Accept X-Robin-Key header as alternative to cookie auth."""
    if _DEV_AUTH_BYPASS:
        return True
    if not WEB_SECRET:
        return False
    return bool(key and hmac.compare_digest(key, WEB_SECRET))


def require_auth_or_key(
    nakama_auth: str | None = Cookie(None),
    x_robin_key: str | None = Header(None),
) -> None:
    """FastAPI dependency: require either cookie or API key auth."""
    if not (check_auth(nakama_auth) or check_key(x_robin_key)):
        raise HTTPException(status_code=403, detail="Unauthorized")
