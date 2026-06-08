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

    Only enforced when WEB_SECRET is set (production mode). Dev machines that
    have not configured auth at all are left alone — use NAKAMA_DEV_AUTH_BYPASS=1
    to silence the check explicitly.

    Also emits warnings for operational env vars that fall back to config.yaml
    defaults but should be explicitly pinned in production deployments.
    """
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

    for var in _OPERATIONAL_VARS:
        if not os.environ.get(var, "").strip():
            logger.warning("env var %s not set — falling back to config.yaml default", var)
