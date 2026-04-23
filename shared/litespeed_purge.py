"""LiteSpeed Cache purge helper (ADR-005b §5).

Purge strategy is selected via env var `LITESPEED_PURGE_METHOD` — the Day 1
research outcome (docs/runbooks/litespeed-purge.md) writes the chosen method
into the VPS .env, and this module honours that choice at runtime. Purge is
always non-blocking: any failure returns False and the caller logs WARNING
without aborting publish (page content will catch up at LiteSpeed's TTL).

Method choices:
    "rest"       — POST /wp-json/litespeed/v1/purge {url}
                   Uses WordPressClient's Basic auth + rate limit. Requires
                   the publisher WP role to carry litespeed_manage_options
                   capability (see docs/runbooks/wp-nakama-publisher-role.md).

    "admin_ajax" — POST /wp-admin/admin-ajax.php?action=litespeed_purge
                   Requires a one-time admin nonce fetched via /wp-admin page
                   load; complex from a headless Python process. Documented
                   in the runbook but not implemented in Phase 1 without
                   Day 1 validation.

    "noop"       — Do nothing. Page picks up new content at LiteSpeed TTL
                   (default 600 s). Use this if all purge methods fail Day 1
                   validation and operator accepts the delay.

Default: "rest".

Environment variables read:
    LITESPEED_PURGE_METHOD   one of "rest" / "admin_ajax" / "noop" (default "rest")
"""

from __future__ import annotations

import os
from typing import Literal

from shared.log import get_logger
from shared.wordpress_client import (
    WordPressClient,
    WPAuthError,
    WPClientError,
    WPServerError,
)

logger = get_logger("nakama.litespeed_purge")

LiteSpeedMethod = Literal["rest", "admin_ajax", "noop"]


def _get_method() -> LiteSpeedMethod:
    value = os.environ.get("LITESPEED_PURGE_METHOD", "rest").strip().lower()
    if value not in {"rest", "admin_ajax", "noop"}:
        logger.warning("Unknown LITESPEED_PURGE_METHOD=%r; falling back to 'noop'", value)
        return "noop"
    return value  # type: ignore[return-value]


def purge_url(
    url: str,
    *,
    wp_client: WordPressClient | None = None,
    method: LiteSpeedMethod | None = None,
    operation_id: str = "",
) -> bool:
    """Purge LiteSpeed cache for a single URL.

    Args:
        url:          Permalink to purge (the WP post URL).
        wp_client:    Needed for "rest" method (reuses Basic auth + rate limit).
        method:       Override env var for testing; defaults to env LITESPEED_PURGE_METHOD.
        operation_id: Log correlation.

    Returns:
        True if LiteSpeed acknowledged the purge, False otherwise (non-blocking).
    """
    chosen = method or _get_method()

    if chosen == "noop":
        logger.info(
            "litespeed purge skipped (method=noop) url=%s op=%s — TTL will expire content",
            url,
            operation_id,
        )
        return False

    if chosen == "admin_ajax":
        logger.warning(
            "litespeed purge method=admin_ajax not implemented in Phase 1; op=%s url=%s",
            operation_id,
            url,
        )
        return False

    # chosen == "rest"
    if wp_client is None:
        logger.warning(
            "litespeed purge method=rest requires wp_client; op=%s url=%s",
            operation_id,
            url,
        )
        return False
    return _purge_via_rest(url, wp_client=wp_client, operation_id=operation_id)


def _purge_via_rest(
    url: str,
    *,
    wp_client: WordPressClient,
    operation_id: str,
) -> bool:
    """POST /wp-json/litespeed/v1/purge {url}. All errors swallowed → False."""
    try:
        wp_client._request(
            "POST",
            "litespeed/v1/purge",
            json={"url": url},
            operation_id=operation_id,
        )
    except WPAuthError as exc:
        logger.warning(
            "litespeed purge auth failure url=%s op=%s err=%s — "
            "check nakama_publisher capabilities",
            url,
            operation_id,
            exc,
        )
        return False
    except WPClientError as exc:
        logger.warning(
            "litespeed purge client error url=%s op=%s err=%s — "
            "endpoint may be disabled or plugin not configured",
            url,
            operation_id,
            exc,
        )
        return False
    except WPServerError as exc:
        logger.warning(
            "litespeed purge server error url=%s op=%s err=%s",
            url,
            operation_id,
            exc,
        )
        return False

    logger.info("litespeed purge ok url=%s op=%s", url, operation_id)
    return True
