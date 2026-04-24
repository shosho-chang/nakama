"""WordPress REST API client — agent-agnostic (ADR-005b §7 / §9).

Wraps WP REST v2 endpoints with:
- httpx async + sync clients
- 1 req/sec rate limit (ADR-005b §7, avoids LiteSpeed/WAF blocks)
- tenacity retry: 5xx/timeout → exponential backoff (2→4→8s, max 3 attempts)
  4xx auth/permission → no retry, raises immediately
- Application password auth via Authorization: Basic header
- Structured logging with operation_id (observability.md §2)
- Password/token never logged — only last-4 of base64 (observability.md §9)
- Anti-corruption: all responses parsed through WpPostV1 / WpMediaV1 / WpTermV1

Design:
    WordPressClient is instantiated per-site (wp_shosho / wp_fleet).
    Credentials are loaded from environment variables; never hardcoded.
    The client is agent-agnostic — both Usopp and Franky import it.

Environment variables:
    WP_SHOSHO_BASE_URL      e.g. https://shosho.tw
    WP_SHOSHO_USERNAME      WP username for nakama_publisher role
    WP_SHOSHO_APP_PASSWORD  WP application password (spaces OK, stripped)
    WP_FLEET_BASE_URL
    WP_FLEET_USERNAME
    WP_FLEET_APP_PASSWORD

Usage::

    from shared.wordpress_client import WordPressClient

    client = WordPressClient.from_env("wp_shosho")
    post = client.create_post(
        title="Hello",
        content="<p>World</p>",
        status="draft",
        operation_id="op_12345678",
    )
    print(post.id, post.link)
"""

from __future__ import annotations

import base64
import os
import time
from typing import Any

import httpx

from shared.log import get_logger
from shared.schemas.external.seopress import (
    SEOpressWritePayloadV1,
    parse_seopress_response,
)
from shared.schemas.external.wordpress import WpMediaV1, WpPostV1, WpTermV1

logger = get_logger("nakama.wordpress_client")

# ---------------------------------------------------------------------------
# Retry / rate-limit config (ADR-005b §7, reliability.md §5)
# ---------------------------------------------------------------------------

_RATE_LIMIT_INTERVAL_S = 1.0  # minimum seconds between WP requests
_RETRY_ATTEMPTS = 3  # max attempts (tenacity-style)
_RETRY_BACKOFF = [2.0, 4.0, 8.0]  # seconds after attempt 1, 2, 3
_WP_TIMEOUT_S = 3.0  # per-request connect+read timeout (reliability.md §7)


def _mask_password(b64_creds: str) -> str:
    """Return last 4 chars of base64 credential for log masking (observability.md §9)."""
    return f"...{b64_creds[-4:]}" if len(b64_creds) > 4 else "****"


class WPAuthError(PermissionError):
    """4xx auth/permission failure — not retried (ADR-005b §1 retry rules)."""


class WPClientError(ValueError):
    """4xx non-auth client error — not retried."""


class WPServerError(RuntimeError):
    """5xx / timeout — retried up to _RETRY_ATTEMPTS times."""


# ---------------------------------------------------------------------------
# WordPressClient
# ---------------------------------------------------------------------------


class WordPressClient:
    """Synchronous WP REST v2 client with rate-limit and retry.

    Synchronous design matches the existing Nakama codebase (approval_queue,
    state.py are all sync).  Async variant deferred to Phase 2.
    """

    def __init__(
        self,
        *,
        base_url: str,
        username: str,
        app_password: str,
        site_id: str = "wp_shosho",
    ) -> None:
        self._base_url = base_url.rstrip("/")
        self.site_id = site_id
        # Strip spaces from application password (WP copies them with spaces)
        cleaned = app_password.replace(" ", "")
        raw = f"{username}:{cleaned}"
        self._b64 = base64.b64encode(raw.encode()).decode()
        self._masked = _mask_password(self._b64)
        self._last_request_at: float = 0.0

        logger.info(
            "WordPressClient init site=%s base_url=%s credential=...%s",
            site_id,
            self._base_url,
            self._masked,
        )

    # ------------------------------------------------------------------
    # Factory
    # ------------------------------------------------------------------

    @classmethod
    def from_env(cls, site: str = "wp_shosho") -> "WordPressClient":
        """Construct from environment variables.

        site must be one of: "wp_shosho", "wp_fleet".
        Reads {PREFIX}_BASE_URL, {PREFIX}_USERNAME, {PREFIX}_APP_PASSWORD.
        """
        prefix = site.upper().replace("WP_", "WP_", 1)
        base_url = os.environ[f"{prefix}_BASE_URL"]
        username = os.environ[f"{prefix}_USERNAME"]
        app_password = os.environ[f"{prefix}_APP_PASSWORD"]
        return cls(
            base_url=base_url,
            username=username,
            app_password=app_password,
            site_id=site,
        )

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _headers(self) -> dict[str, str]:
        return {
            "Authorization": f"Basic {self._b64}",
            "Content-Type": "application/json",
            "Accept": "application/json",
        }

    def _rate_limit(self) -> None:
        """Block until 1 req/sec rate limit is satisfied."""
        elapsed = time.monotonic() - self._last_request_at
        if elapsed < _RATE_LIMIT_INTERVAL_S:
            time.sleep(_RATE_LIMIT_INTERVAL_S - elapsed)
        self._last_request_at = time.monotonic()

    def _request(
        self,
        method: str,
        path: str,
        *,
        operation_id: str = "",
        **kwargs: Any,
    ) -> dict[str, Any] | list[Any]:
        """Execute one HTTP request with rate-limiting and retry.

        Args:
            method:       HTTP verb (GET/POST/PUT/PATCH/DELETE)
            path:         Path relative to /wp-json/ (e.g. "wp/v2/posts")
            operation_id: For structured log correlation
            **kwargs:     Passed to httpx.request (json=, params=, content=, etc.)

        Returns:
            Parsed JSON body (dict or list).

        Raises:
            WPAuthError:   401/403 response
            WPClientError: other 4xx response
            WPServerError: 5xx / timeout (after exhausting retries)
        """
        url = f"{self._base_url}/wp-json/{path}"
        last_exc: Exception | None = None

        # Caller may pass extra headers (e.g. upload_media Content-Disposition);
        # merge with our auth headers instead of fighting over the `headers=` kwarg.
        extra_headers = kwargs.pop("headers", None) or {}
        merged_headers = {**self._headers(), **extra_headers}

        for attempt in range(1, _RETRY_ATTEMPTS + 1):
            self._rate_limit()
            try:
                with httpx.Client(timeout=_WP_TIMEOUT_S) as client:
                    resp = client.request(
                        method,
                        url,
                        headers=merged_headers,
                        **kwargs,
                    )
                logger.debug(
                    "wp_request method=%s path=%s status=%s op=%s attempt=%s",
                    method,
                    path,
                    resp.status_code,
                    operation_id,
                    attempt,
                )

                if resp.status_code in (401, 403):
                    raise WPAuthError(
                        f"WP auth error {resp.status_code} on {method} {path}: {resp.text[:200]}"
                    )
                if 400 <= resp.status_code < 500:
                    raise WPClientError(
                        f"WP client error {resp.status_code} on {method} {path}: {resp.text[:200]}"
                    )
                if resp.status_code >= 500:
                    raise WPServerError(
                        f"WP server error {resp.status_code} on {method} {path}: {resp.text[:200]}"
                    )

                return resp.json()

            except (WPAuthError, WPClientError):
                # 4xx — never retry
                raise

            except (WPServerError, httpx.TimeoutException, httpx.ConnectError) as exc:
                last_exc = exc
                if attempt < _RETRY_ATTEMPTS:
                    wait = _RETRY_BACKOFF[attempt - 1]
                    logger.warning(
                        "wp_request retrying attempt=%s/%s wait=%.1fs op=%s err=%s",
                        attempt,
                        _RETRY_ATTEMPTS,
                        wait,
                        operation_id,
                        exc,
                    )
                    time.sleep(wait)
                else:
                    logger.error(
                        "wp_request exhausted retries method=%s path=%s op=%s err=%s",
                        method,
                        path,
                        operation_id,
                        exc,
                    )

        raise WPServerError(
            f"WP request failed after {_RETRY_ATTEMPTS} attempts: {last_exc}"
        ) from last_exc

    # ------------------------------------------------------------------
    # Posts
    # ------------------------------------------------------------------

    def create_post(
        self,
        *,
        title: str,
        content: str,
        status: str = "draft",
        slug: str = "",
        excerpt: str = "",
        categories: list[int] | None = None,
        tags: list[int] | None = None,
        featured_media: int | None = None,
        meta: dict[str, Any] | None = None,
        operation_id: str = "",
    ) -> WpPostV1:
        """Create a new WP post (always draft first, per ADR-005b §4).

        Returns:
            WpPostV1 parsed response.
        """
        body: dict[str, Any] = {
            "title": title,
            "content": content,
            "status": status,
        }
        if slug:
            body["slug"] = slug
        if excerpt:
            body["excerpt"] = excerpt
        if categories is not None:
            body["categories"] = categories
        if tags is not None:
            body["tags"] = tags
        if featured_media is not None:
            body["featured_media"] = featured_media
        if meta is not None:
            body["meta"] = meta

        raw = self._request("POST", "wp/v2/posts", json=body, operation_id=operation_id)
        post = WpPostV1.model_validate(raw)
        logger.info(
            "wp create_post post_id=%s status=%s op=%s site=%s",
            post.id,
            post.status,
            operation_id,
            self.site_id,
        )
        return post

    def get_post(self, post_id: int, *, operation_id: str = "") -> WpPostV1:
        """Fetch a single post by ID."""
        raw = self._request("GET", f"wp/v2/posts/{post_id}", operation_id=operation_id)
        return WpPostV1.model_validate(raw)

    def update_post(
        self,
        post_id: int,
        *,
        operation_id: str = "",
        **fields: Any,
    ) -> WpPostV1:
        """Partially update a post (PATCH semantics via POST with only changed fields)."""
        raw = self._request(
            "POST",
            f"wp/v2/posts/{post_id}",
            json=fields,
            operation_id=operation_id,
        )
        post = WpPostV1.model_validate(raw)
        logger.info(
            "wp update_post post_id=%s op=%s fields=%s",
            post_id,
            operation_id,
            list(fields.keys()),
        )
        return post

    def find_by_meta(
        self,
        meta_key: str,
        meta_value: str,
        *,
        operation_id: str = "",
    ) -> WpPostV1 | None:
        """Find a post by custom meta field (requires register_post_meta show_in_rest=True).

        Returns first match or None.

        Note: WP REST meta search is only available when the meta key is
        registered with show_in_rest=True (ADR-005b §2 / §2.1). Without that,
        this will silently return None even if a post exists.
        """
        raw_list = self._request(
            "GET",
            "wp/v2/posts",
            params={
                "meta_key": meta_key,
                "meta_value": meta_value,
                "status": "any",
                "per_page": 1,
            },
            operation_id=operation_id,
        )
        if not isinstance(raw_list, list) or not raw_list:
            return None
        return WpPostV1.model_validate(raw_list[0])

    # ------------------------------------------------------------------
    # Media
    # ------------------------------------------------------------------

    def upload_media(
        self,
        *,
        filename: str,
        content: bytes,
        mime_type: str = "image/jpeg",
        alt_text: str = "",
        operation_id: str = "",
    ) -> WpMediaV1:
        """Upload a file to WP media library.

        Args:
            filename:   filename including extension (e.g. "hero.jpg")
            content:    raw file bytes
            mime_type:  MIME type (default image/jpeg)
            alt_text:   alt text set after upload
            operation_id: for log correlation
        """
        # First upload — extra headers merge with auth headers inside _request()
        raw = self._request(
            "POST",
            "wp/v2/media",
            content=content,
            headers={
                "Content-Disposition": f'attachment; filename="{filename}"',
                "Content-Type": mime_type,
            },
            operation_id=operation_id,
        )
        media = WpMediaV1.model_validate(raw)
        logger.info(
            "wp upload_media media_id=%s filename=%s op=%s",
            media.id,
            filename,
            operation_id,
        )

        # Optionally set alt_text via update
        if alt_text:
            self._request(
                "POST",
                f"wp/v2/media/{media.id}",
                json={"alt_text": alt_text},
                operation_id=operation_id,
            )

        return media

    # ------------------------------------------------------------------
    # Taxonomy
    # ------------------------------------------------------------------

    def list_categories(self, *, per_page: int = 100, operation_id: str = "") -> list[WpTermV1]:
        """Fetch all categories (up to per_page).  Builds slug→id cache."""
        raw_list = self._request(
            "GET",
            "wp/v2/categories",
            params={"per_page": per_page},
            operation_id=operation_id,
        )
        if not isinstance(raw_list, list):
            return []
        return [WpTermV1.model_validate(r) for r in raw_list]

    def list_tags(self, *, per_page: int = 100, operation_id: str = "") -> list[WpTermV1]:
        """Fetch tags (up to per_page)."""
        raw_list = self._request(
            "GET",
            "wp/v2/tags",
            params={"per_page": per_page},
            operation_id=operation_id,
        )
        if not isinstance(raw_list, list):
            return []
        return [WpTermV1.model_validate(r) for r in raw_list]

    # ------------------------------------------------------------------
    # SEOPress integration (ADR-005b §3)
    # ------------------------------------------------------------------

    def write_seopress_meta(
        self,
        post_id: int,
        payload: SEOpressWritePayloadV1,
        *,
        operation_id: str = "",
    ) -> tuple[bool, str]:
        """Write SEO metadata via SEOPress REST API (primary path).

        Returns:
            (success: bool, path_used: str)
            path_used is one of: "rest", "fallback_meta", "skipped"

        The caller (seopress_writer.py in Slice B) implements the full
        three-level fallback chain.  This method only attempts the REST path
        and raises SEOPressSchemaDriftError on schema mismatch.
        """
        body = payload.model_dump(mode="json")
        try:
            raw = self._request(
                "POST",
                f"seopress/v1/posts/{post_id}",
                json=body,
                operation_id=operation_id,
            )
            # Validate response shape (anti-corruption)
            parse_seopress_response(raw if isinstance(raw, dict) else {})
            logger.info(
                "wp seopress_write post_id=%s op=%s path=rest",
                post_id,
                operation_id,
            )
            return True, "rest"
        except (WPClientError, WPServerError):
            # 404/410 = SEOPress REST endpoint unavailable → let caller do Fallback A
            raise

    def write_seopress_fallback_meta(
        self,
        post_id: int,
        payload: SEOpressWritePayloadV1,
        *,
        operation_id: str = "",
    ) -> bool:
        """Fallback A: write SEOPress meta keys directly via post meta (ADR-005b §3).

        Uses SEOPRESS_META_KEYS_V941 constant.  If WP rejects the update,
        returns False (caller handles Fallback B).
        """
        from shared.schemas.external.seopress import SEOPRESS_META_KEYS_V941

        meta = {
            SEOPRESS_META_KEYS_V941["title"]: payload.title,
            SEOPRESS_META_KEYS_V941["description"]: payload.description,
            SEOPRESS_META_KEYS_V941["focus_keyword"]: payload.focus_keyword,
        }
        if payload.canonical:
            meta[SEOPRESS_META_KEYS_V941["canonical"]] = payload.canonical

        try:
            self._request(
                "POST",
                f"wp/v2/posts/{post_id}",
                json={"meta": meta},
                operation_id=operation_id,
            )
            logger.info(
                "wp seopress_fallback_meta post_id=%s op=%s path=fallback_meta",
                post_id,
                operation_id,
            )
            return True
        except (WPClientError, WPServerError) as exc:
            logger.warning(
                "wp seopress_fallback_meta failed post_id=%s op=%s err=%s",
                post_id,
                operation_id,
                exc,
            )
            return False

    # ------------------------------------------------------------------
    # Health check
    # ------------------------------------------------------------------

    def health_check(self, *, operation_id: str = "") -> bool:
        """Verify WP REST API is reachable and credentials are valid.

        Used by /healthz endpoint (ADR-005b Checklist) and Franky monitoring.
        Returns True if the site root endpoint responds 200.
        """
        try:
            self._request("GET", "wp/v2/users/me", operation_id=operation_id)
            return True
        except (WPAuthError, WPClientError, WPServerError, httpx.HTTPError) as exc:
            logger.warning("wp health_check failed site=%s err=%s", self.site_id, exc)
            return False
