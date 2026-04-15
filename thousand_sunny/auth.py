"""Shared authentication — cookie + API key auth for all agents."""

import hashlib
import hmac
import os

from fastapi import Cookie, Header, HTTPException

from shared.log import get_logger

logger = get_logger("nakama.web.auth")

WEB_PASSWORD = os.environ.get("WEB_PASSWORD", "")
WEB_SECRET = os.environ.get("WEB_SECRET", "")
_DEV_MODE = not WEB_SECRET


def make_token(password: str) -> str:
    return hmac.new(WEB_SECRET.encode(), password.encode(), hashlib.sha256).hexdigest()


def check_auth(auth_cookie: str | None) -> bool:
    if not WEB_PASSWORD:
        return True
    if not auth_cookie:
        return False
    return hmac.compare_digest(auth_cookie, make_token(WEB_PASSWORD))


def check_key(key: str | None) -> bool:
    """Accept X-Robin-Key header as alternative to cookie auth."""
    if _DEV_MODE:
        logger.warning("WEB_SECRET not set — API key auth disabled (dev mode)")
        return True
    return bool(key and hmac.compare_digest(key, WEB_SECRET))


def require_auth_or_key(
    robin_auth: str | None = Cookie(None),
    x_robin_key: str | None = Header(None),
) -> None:
    """FastAPI dependency: require either cookie or API key auth."""
    if not (check_auth(robin_auth) or check_key(x_robin_key)):
        raise HTTPException(status_code=403, detail="Unauthorized")
