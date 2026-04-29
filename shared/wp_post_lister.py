"""WP REST post lister with 1h server-side TTL cache (SEO 中控台 v1 slice 2).

Wraps `WordPressClient` with a small purpose:

- `list_posts(target_site)` returns up to 100 published posts as
  `WpPostSummaryV1` records (`wp_post_id`, `title`, `link`, `focus_keyword`,
  `last_modified`), sorted by `last_modified` descending.
- Cache the response per `target_site` for 1 hour (cache hit avoids the
  `/wp/v2/posts` HTTP call entirely).
- WP API errors degrade to an empty list with a warning log — the SEO control
  center page must never crash because WP is briefly unreachable
  (PRD #226 acceptance criterion).

Why a separate module rather than a method on `WordPressClient`:

- `WordPressClient` is a thin I/O adapter that returns `WpPostV1` shapes; the
  list view needs a smaller, view-friendly projection (5 fields) plus
  SEOPress focus-keyword extraction plus caching policy. Keeping the cache
  + projection here matches the "deep module, narrow interface" lens
  (`memory/claude/feedback_deep_module_vs_leaky_abstraction.md`) — callers
  see one function, the module decides freshness + projection.
- Concept-aggregator level: callers in `thousand_sunny/routers/bridge.py`
  do not care about HTTP details, retries, SEOPress meta keys, or sort
  order. They get a list ready to render.

Hidden constraints worth recording (encountered while wiring this):

1. SEOPress 9.4.x **does** register their meta keys with `show_in_rest=true`
   so a vanilla `GET /wp/v2/posts?per_page=100&_embed=false` includes the
   `_seopress_analysis_target_kw` key inside the post's `meta` dict. If a
   site upgrades SEOPress and the registration drops, our `focus_keyword`
   value falls back to an empty string — the UI shows "—" but the page
   still renders (graceful degradation, no exception).
2. WP REST default `per_page` cap is 100 (`rest_per_page_in_filter`),
   matching PRD #226 §"WP article source"; we do not paginate in v1
   because the user's blog has < 100 posts.
3. The WP REST listing returns `modified` (local) and `modified_gmt` (UTC).
   We use `modified` for display since the bridge UI is operated from
   Asia/Taipei; sort key is the same string (ISO-comparable).

Cache behavior:

- TTL is `_CACHE_TTL_SECONDS` = 3600 from `time.monotonic()` (clock-jump
  safe; not affected by NTP corrections).
- Keyed by `target_site`. wp_shosho and wp_fleet evict independently.
- `clear_cache()` exposed for tests; also wipes between tests via
  autouse fixture in `tests/shared/test_wp_post_lister.py`.
- No persistent disk cache: 1h TTL + in-process dict is sufficient because
  the bridge process is long-lived (uvicorn) and the page load cost is
  the rare cache miss only.

Usage::

    from shared.wp_post_lister import list_posts

    posts = list_posts("wp_shosho")  # may be []
    for p in posts:
        print(p.wp_post_id, p.title, p.last_modified)

Tests live in `tests/shared/test_wp_post_lister.py`.
"""

from __future__ import annotations

import html
import re
import time
from typing import Any

import httpx
from pydantic import BaseModel, ConfigDict, Field

from shared.log import get_logger
from shared.schemas.publishing import TargetSite
from shared.wordpress_client import (
    WordPressClient,
    WPAuthError,
    WPClientError,
    WPServerError,
)

logger = get_logger("nakama.wp_post_lister")

# ---------------------------------------------------------------------------
# Public schema (view projection)
# ---------------------------------------------------------------------------

# SEOPress focus keyword postmeta key (kept in sync with
# `shared.schemas.external.seopress.SEOPRESS_META_KEYS_V941["focus_keyword"]`).
# Inlined here as a local constant so this module stays import-light and
# the projection is not silently broken by a typo at the read site.
_SEOPRESS_FOCUS_KEYWORD_META_KEY = "_seopress_analysis_target_kw"

# Hard-coded per PRD #226 §"WP article source": "WP REST `/wp/v2/posts?per_page=100`
# live pull". Up the cap here if the user's blog ever exceeds 100 posts.
_DEFAULT_PER_PAGE = 100

# 1-hour server-side TTL — Q4 decision in
# `memory/claude/project_seo_control_center_design_2026_04_29.md`.
_CACHE_TTL_SECONDS = 3600.0


class WpPostSummaryV1(BaseModel):
    """Minimal projection of a WP post for the SEO control center list view."""

    model_config = ConfigDict(extra="forbid", frozen=True)
    wp_post_id: int = Field(description="WP post ID")
    title: str = Field(description="Plain-text title (HTML entities decoded)")
    link: str = Field(description="Permalink to the post")
    focus_keyword: str = Field(
        default="",
        description="SEOPress focus keyword (empty string when unset / hidden)",
    )
    last_modified: str = Field(
        description=(
            "ISO8601 last-modified timestamp from WP `modified` field "
            "(local time per WP setting; sortable as string)."
        ),
    )


# ---------------------------------------------------------------------------
# In-process TTL cache
# ---------------------------------------------------------------------------

# `_cache[target_site] = (expires_at_monotonic, list[WpPostSummaryV1])`.
# Module-global on purpose — uvicorn workers each maintain their own copy,
# which is fine for read-only listing (no cross-worker invalidation needed).
_cache: dict[TargetSite, tuple[float, list[WpPostSummaryV1]]] = {}


def clear_cache() -> None:
    """Drop all cached entries. Tests + manual ops via `python -c`.

    Not exposed on the bridge HTTP surface for v1 — adding a `?bypass_cache=1`
    query string is left to a future slice if a "force refresh" button is
    needed in the UI.
    """
    _cache.clear()


def _cache_get(target_site: TargetSite) -> list[WpPostSummaryV1] | None:
    """Return cached list if fresh, else None (and evict the stale entry)."""
    entry = _cache.get(target_site)
    if entry is None:
        return None
    expires_at, posts = entry
    if time.monotonic() >= expires_at:
        # Evict expired entry so the next miss does not re-check.
        _cache.pop(target_site, None)
        return None
    return posts


def _cache_set(target_site: TargetSite, posts: list[WpPostSummaryV1]) -> None:
    _cache[target_site] = (time.monotonic() + _CACHE_TTL_SECONDS, posts)


# ---------------------------------------------------------------------------
# Title cleaning
# ---------------------------------------------------------------------------

# WP returns `title.rendered` as HTML (entities + occasional <em>/<strong>),
# we want plain text for table cells. Strip tags then unescape entities.
_HTML_TAG_RE = re.compile(r"<[^>]+>")


def _plain_title(rendered: str) -> str:
    """Decode HTML entities + strip tags from WP `title.rendered`."""
    return html.unescape(_HTML_TAG_RE.sub("", rendered)).strip()


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def list_posts(
    target_site: TargetSite,
    *,
    per_page: int = _DEFAULT_PER_PAGE,
    operation_id: str = "",
) -> list[WpPostSummaryV1]:
    """Return up to `per_page` published posts from `target_site`, sorted by
    `last_modified` descending.

    Behavior:
        - Cache hit (within 1h TTL): no HTTP call; cached list returned.
        - Cache miss / expired: GET /wp/v2/posts and cache the projection.
        - WP API error / network failure: log a warning and return [].
          The bridge `/bridge/seo` page must continue to render.
    """
    cached = _cache_get(target_site)
    if cached is not None:
        logger.debug(
            "wp_post_lister cache_hit target_site=%s count=%d op=%s",
            target_site,
            len(cached),
            operation_id,
        )
        return cached

    try:
        client = WordPressClient.from_env(target_site)
    except KeyError as exc:
        # Missing env vars — log and fall back to empty list. Same outcome as
        # any other transient failure: the page still renders.
        logger.warning(
            "wp_post_lister env_missing target_site=%s err=%s op=%s",
            target_site,
            exc,
            operation_id,
        )
        return []

    try:
        raw = client.list_posts(
            per_page=per_page,
            status="publish",
            operation_id=operation_id or "wp_post_lister",
        )
    except (WPAuthError, WPClientError, WPServerError, httpx.HTTPError) as exc:
        logger.warning(
            "wp_post_lister fetch_failed target_site=%s err=%s op=%s",
            target_site,
            exc,
            operation_id,
        )
        return []

    posts = _project(raw)
    posts.sort(key=lambda p: p.last_modified, reverse=True)
    _cache_set(target_site, posts)
    logger.info(
        "wp_post_lister cache_miss target_site=%s count=%d op=%s",
        target_site,
        len(posts),
        operation_id,
    )
    return posts


def _project(raw: list[Any]) -> list[WpPostSummaryV1]:
    """Project a raw WP REST listing into `list[WpPostSummaryV1]`.

    Tolerates malformed individual records: if a single post lacks a
    required field (`id`, `link`, `title.rendered`, or `modified`), it is
    skipped with a debug log rather than blowing up the whole list.
    The acceptance criterion "WP API error → graceful fallback" is mostly
    about the HTTP layer; this safety net handles the secondary case where
    a single bad row would otherwise mask 99 healthy rows.
    """
    out: list[WpPostSummaryV1] = []
    for entry in raw:
        if not isinstance(entry, dict):
            logger.debug("wp_post_lister skip_non_dict type=%s", type(entry).__name__)
            continue
        try:
            wp_post_id = int(entry["id"])
            link = str(entry["link"])
            title_rendered = entry["title"]["rendered"]
            last_modified = str(entry["modified"])
        except (KeyError, TypeError, ValueError) as exc:
            logger.debug(
                "wp_post_lister skip_malformed id=%s err=%s",
                entry.get("id") if isinstance(entry, dict) else None,
                exc,
            )
            continue

        meta = entry.get("meta") if isinstance(entry.get("meta"), dict) else {}
        focus_keyword_raw = meta.get(_SEOPRESS_FOCUS_KEYWORD_META_KEY, "")
        focus_keyword = str(focus_keyword_raw).strip() if focus_keyword_raw is not None else ""

        out.append(
            WpPostSummaryV1(
                wp_post_id=wp_post_id,
                title=_plain_title(str(title_rendered)),
                link=link,
                focus_keyword=focus_keyword,
                last_modified=last_modified,
            )
        )
    return out
