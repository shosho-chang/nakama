"""App startup preflight — refuse to start with a security misconfiguration."""

from __future__ import annotations

import os

from shared.log import get_logger

logger = get_logger("nakama.web.preflight")

_OPERATIONAL_VARS = (
    "VAULT_PATH",
    "DB_PATH",
    "NAKAMA_BOOKS_DIR",
    "NAKAMA_PROMOTION_MODE",
)


def run_preflight() -> None:
    """Raise RuntimeError if the app is started with a dangerous security gap.

    WEB_PASSWORD empty → refuse to start, unless the operator explicitly opted
    into an open (no-password) app with NAKAMA_ALLOW_OPEN_AUTH=1 — and even then
    not when WEB_SECRET is set (that combination means production with a dropped
    password). NAKAMA_DEV_AUTH_BYPASS=1 skips the check entirely.

    Also emits warnings for operational env vars that fall back to config.yaml
    defaults but should be explicitly pinned in production deployments.
    """
    from thousand_sunny.auth import OPEN_AUTH_ENV, open_auth_allowed

    dev_bypass = os.environ.get("NAKAMA_DEV_AUTH_BYPASS", "").strip().lower() in (
        "1",
        "true",
        "yes",
    )
    if dev_bypass:
        logger.debug("NAKAMA_DEV_AUTH_BYPASS — skipping production env preflight")
        return

    web_secret = os.environ.get("WEB_SECRET", "").strip()
    web_password = os.environ.get("WEB_PASSWORD", "").strip()

    if web_secret and not web_password:
        raise RuntimeError(
            "WEB_SECRET is set but WEB_PASSWORD is empty — refusing to start. "
            "Set WEB_PASSWORD or set NAKAMA_DEV_AUTH_BYPASS=1 for local dev."
        )
    if not web_password and not open_auth_allowed():
        raise RuntimeError(
            "WEB_PASSWORD is empty — refusing to start an app anyone could open. "
            f"Set WEB_PASSWORD, or set {OPEN_AUTH_ENV}=1 for a local no-password dev run."
        )

    for var in _OPERATIONAL_VARS:
        if not os.environ.get(var, "").strip():
            logger.warning("env var %s not set — falling back to config.yaml default", var)
