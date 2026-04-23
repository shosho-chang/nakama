"""SEOPress three-tier fallback orchestrator (ADR-005b §3).

Wraps the two WP client methods (REST + fallback meta) with drift detection
and a defined cascade:

    正常：POST /wp-json/seopress/v1/posts/{id}
      ↓ SEOPressSchemaDriftError or 404/410
    Fallback A：直接寫 post meta keys via WP REST
      ↓ 仍失敗
    Fallback B：publish 照發，SEO meta 留空 → caller should log WARNING + fire Critical alert

This module is deliberately pure (no logging side effects, no alert dispatch).
The publisher decides how to react to the returned status.

Usage::

    from shared.seopress_writer import write_seopress, SEOPressStatus

    status = write_seopress(
        wp_client=wp,
        post_id=42,
        payload=SEOpressWritePayloadV1(title=..., description=..., focus_keyword=...),
        operation_id="op_12345678",
    )
    # status in {"written", "fallback_meta", "skipped"}
"""

from __future__ import annotations

from typing import Literal

from shared.log import get_logger
from shared.schemas.external.seopress import (
    SEOPressSchemaDriftError,
    SEOpressWritePayloadV1,
)
from shared.wordpress_client import WordPressClient, WPClientError, WPServerError

logger = get_logger("nakama.seopress_writer")

SEOPressStatus = Literal["written", "fallback_meta", "skipped"]


def write_seopress(
    *,
    wp_client: WordPressClient,
    post_id: int,
    payload: SEOpressWritePayloadV1,
    operation_id: str = "",
) -> SEOPressStatus:
    """Write SEO metadata with three-tier fallback.

    Returns:
        "written"        — REST path succeeded.
        "fallback_meta"  — REST failed / drifted, post meta fallback succeeded.
        "skipped"        — Both paths failed; caller should fire Critical alert
                           and continue publish with empty SEO meta.
    """
    # Tier 1: SEOPress REST API (primary)
    try:
        ok, _ = wp_client.write_seopress_meta(
            post_id=post_id,
            payload=payload,
            operation_id=operation_id,
        )
        if ok:
            return "written"
    except SEOPressSchemaDriftError as exc:
        logger.warning(
            "seopress REST drift detected post_id=%s op=%s err=%s — falling back to meta keys",
            post_id,
            operation_id,
            exc,
        )
    except (WPClientError, WPServerError) as exc:
        # 4xx/5xx on REST path → try fallback A. Auth errors (WPAuthError) are
        # already a subclass of PermissionError and propagate naturally.
        logger.warning(
            "seopress REST unavailable post_id=%s op=%s err=%s — falling back to meta keys",
            post_id,
            operation_id,
            exc,
        )

    # Tier 2: Fallback A — direct post meta write (SEOPRESS_META_KEYS_V941)
    try:
        if wp_client.write_seopress_fallback_meta(
            post_id=post_id,
            payload=payload,
            operation_id=operation_id,
        ):
            logger.info(
                "seopress fallback_meta succeeded post_id=%s op=%s",
                post_id,
                operation_id,
            )
            return "fallback_meta"
    except (WPClientError, WPServerError) as exc:
        logger.error(
            "seopress fallback_meta raised post_id=%s op=%s err=%s",
            post_id,
            operation_id,
            exc,
        )

    # Tier 3: Fallback B — skip. Publish continues without SEO; caller alerts.
    logger.error(
        "seopress skipped post_id=%s op=%s — both REST and fallback_meta failed",
        post_id,
        operation_id,
    )
    return "skipped"
