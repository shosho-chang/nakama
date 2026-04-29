"""WP REST raw-HTML fetcher (PRD #226 slice 5 / issue #234).

Slice 5's review UI needs the WP article body as its raw HTML — Gutenberg
block markup, not the rendered post-process output — so the user can edit it
in place and (in slice #235) write back via `update_post`.

The deep-impl reason this is its own module:

- `WordPressClient.get_post()` validates against ``WpPostV1`` which has
  ``content: WpRenderedFieldV1 = {rendered: str}`` (``extra='forbid'``). When
  WP REST is asked with ``?context=edit`` it adds a ``content.raw`` key that
  would crash the strict validator. Rather than weaken the canonical schema
  (used by Usopp publish + create + update where strict validation is the
  whole point), we add a tiny untyped fetcher that reads the dict directly.
- The fetcher swallows transient failures and returns an
  :class:`RawPostFetchResult` with ``error_message`` set, mirroring the
  graceful-degrade shape that ``wp_post_lister`` already established for the
  /bridge/seo dashboard. The review page renders even when WP REST is briefly
  unreachable.

Hidden constraints worth recording:

1. ``?context=edit`` requires the user to have ``edit_posts`` capability for
   that post type. Our application password is created on the
   ``nakama_publisher`` role (per ADR-005b §5) which has that capability, so
   the call succeeds in production.
2. Some posts are pure rendered HTML (legacy migrations) without Gutenberg
   blocks. ``content.raw`` returns the same HTML in that case — there is no
   distinguishing "is this Gutenberg or not" flag we need to read; the
   textarea just shows whatever raw HTML/blocks WP holds.
3. The size limits are not validated server-side here — large posts can
   exceed 100 KB easily. The form-post endpoint uses
   ``max_length=8000`` for *individual suggestion edits*; the textarea
   itself is read-only on the review page (slice #235 will add the
   "publish edited body" path).

Tests live in ``tests/shared/test_wp_post_raw_fetcher.py``.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

import httpx

from shared.log import get_logger
from shared.schemas.publishing import TargetSite
from shared.wordpress_client import (
    WordPressClient,
    WPAuthError,
    WPClientError,
    WPServerError,
)

logger = get_logger("nakama.wp_post_raw_fetcher")


@dataclass(frozen=True)
class RawPostFetchResult:
    """Outcome of :func:`fetch_raw_html`.

    On success ``raw_html`` carries the body (may be an empty string if the
    post genuinely has no content). On failure ``error_message`` describes
    what went wrong; the review page renders an empty textarea with an
    inline notice.
    """

    raw_html: str = ""
    error_message: Optional[str] = None

    @property
    def ok(self) -> bool:
        return self.error_message is None


def fetch_raw_html(
    *,
    target_site: TargetSite,
    wp_post_id: int,
    operation_id: str = "",
) -> RawPostFetchResult:
    """Fetch ``content.raw`` for ``wp_post_id`` on ``target_site`` via WP REST.

    Returns a :class:`RawPostFetchResult`. Never raises — transient failures
    surface as ``error_message`` and the review page degrades gracefully.

    Hidden ``?context=edit`` is appended to the path: this is what makes WP
    return the ``raw`` field in addition to ``rendered``. Without it, WP only
    serves ``rendered`` (the post-shortcode-expansion HTML) which is not
    suitable for round-trip editing.
    """
    try:
        client = WordPressClient.from_env(target_site)
    except KeyError as exc:
        logger.warning(
            "wp_post_raw_fetcher env_missing target_site=%s err=%s op=%s",
            target_site,
            exc,
            operation_id,
        )
        return RawPostFetchResult(error_message=f"WP env missing for {target_site}: {exc}")

    try:
        raw = client._request(  # noqa: SLF001 — purposeful: see module docstring
            "GET",
            f"wp/v2/posts/{wp_post_id}",
            params={"context": "edit"},
            operation_id=operation_id or "wp_post_raw_fetcher",
        )
    except (WPAuthError, WPClientError, WPServerError, httpx.HTTPError) as exc:
        logger.warning(
            "wp_post_raw_fetcher fetch_failed target_site=%s wp_post_id=%s err=%s op=%s",
            target_site,
            wp_post_id,
            exc,
            operation_id,
        )
        return RawPostFetchResult(error_message=f"WP fetch failed: {type(exc).__name__}: {exc}")

    # ``raw`` is the parsed JSON body. We expect a dict with ``content``
    # which is itself a dict (because of ``?context=edit``); both ``raw`` and
    # ``rendered`` keys live there.
    if not isinstance(raw, dict):
        return RawPostFetchResult(
            error_message=f"WP REST returned non-dict for post {wp_post_id}",
        )
    content = raw.get("content")
    if not isinstance(content, dict):
        return RawPostFetchResult(
            error_message=(
                f"WP REST post {wp_post_id} has no content dict (got {type(content).__name__})"
            ),
        )
    raw_html = content.get("raw")
    if not isinstance(raw_html, str):
        # ``raw`` should always be a string when ``?context=edit`` is honoured.
        return RawPostFetchResult(
            error_message=(
                f"WP REST post {wp_post_id} content.raw missing — "
                "is the auth user lacking edit_posts capability?"
            ),
        )
    logger.debug(
        "wp_post_raw_fetcher ok target_site=%s wp_post_id=%s len=%d op=%s",
        target_site,
        wp_post_id,
        len(raw_html),
        operation_id,
    )
    return RawPostFetchResult(raw_html=raw_html)


__all__ = [
    "RawPostFetchResult",
    "fetch_raw_html",
]
