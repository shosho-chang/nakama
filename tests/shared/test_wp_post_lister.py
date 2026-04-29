"""Tests for `shared/wp_post_lister.py`.

Covers issue #229 acceptance:
- Listing returns projected `WpPostSummaryV1` records.
- Cache hit: 2 calls in TTL window → only 1 HTTP request.
- Cache TTL expiry: 2 calls separated by past TTL → 2 HTTP requests.
- WP API error → empty list + log warning, no exception.
- Missing WP_*_BASE_URL env vars → empty list + warning.
- SEOPress focus keyword extracted from `meta` dict; missing key → "" (UI
  shows "—" via the template, separately tested).
- Sort: `last_modified` descending.
- Title cleaning: HTML entities + tags stripped.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock, patch

import pytest

# ---------------------------------------------------------------------------
# Test scaffolding
# ---------------------------------------------------------------------------

WP_BASE = "http://wp.test"
WP_USER = "nakama_publisher"
WP_PASS = "test app pass"


@pytest.fixture(autouse=True)
def _wp_env(monkeypatch):
    """Stable WP env so `WordPressClient.from_env(target_site)` works."""
    monkeypatch.setenv("WP_SHOSHO_BASE_URL", WP_BASE)
    monkeypatch.setenv("WP_SHOSHO_USERNAME", WP_USER)
    monkeypatch.setenv("WP_SHOSHO_APP_PASSWORD", WP_PASS)
    monkeypatch.setenv("WP_FLEET_BASE_URL", WP_BASE)
    monkeypatch.setenv("WP_FLEET_USERNAME", WP_USER)
    monkeypatch.setenv("WP_FLEET_APP_PASSWORD", WP_PASS)


@pytest.fixture(autouse=True)
def _clear_cache():
    """Each test starts with an empty cache so order does not matter."""
    from shared import wp_post_lister

    wp_post_lister.clear_cache()
    yield
    wp_post_lister.clear_cache()


def _wp_post(
    *,
    post_id: int,
    title: str,
    modified: str,
    focus_keyword: str | None = None,
    link: str | None = None,
) -> dict[str, Any]:
    """Build a `/wp/v2/posts` listing item with realistic shape (WP 6.9.4)."""
    meta: dict[str, Any] = {}
    if focus_keyword is not None:
        meta["_seopress_analysis_target_kw"] = focus_keyword
    return {
        "id": post_id,
        "date": modified,
        "date_gmt": modified,
        "guid": {"rendered": f"http://wp.test/?p={post_id}", "protected": False},
        "modified": modified,
        "modified_gmt": modified,
        "slug": f"post-{post_id}",
        "status": "publish",
        "type": "post",
        "link": link or f"http://wp.test/post-{post_id}/",
        "title": {"rendered": title, "protected": False},
        "content": {"rendered": "<p>body</p>", "protected": False},
        "excerpt": {"rendered": "<p>summary</p>", "protected": False},
        "author": 1,
        "featured_media": 0,
        "comment_status": "open",
        "ping_status": "open",
        "sticky": False,
        "template": "",
        "format": "standard",
        "meta": meta,
        "categories": [1],
        "tags": [],
    }


class _MockResponse:
    """Minimal httpx-style response."""

    def __init__(self, status_code: int, body: Any):
        self.status_code = status_code
        self._body = body

    def json(self) -> Any:
        return self._body

    @property
    def text(self) -> str:
        import json

        return json.dumps(self._body)


def _httpx_ctx(status_code: int, body: Any):
    """Build a context-manager-like mock for `httpx.Client(...)`.

    `wordpress_client._request` does ``with httpx.Client(...) as c:`` so the
    mock has to support `__enter__`/`__exit__` and expose a `.request()`
    method.  Same scaffolding as `tests/shared/test_wordpress_client.py`.
    """
    ctx = MagicMock()
    ctx.__enter__ = MagicMock(return_value=ctx)
    ctx.__exit__ = MagicMock(return_value=False)
    ctx.request = MagicMock(return_value=_MockResponse(status_code, body))
    return ctx


# ---------------------------------------------------------------------------
# list_posts — happy path projection
# ---------------------------------------------------------------------------


def test_list_posts_projects_minimal_fields():
    from shared.wp_post_lister import WpPostSummaryV1, list_posts

    body = [
        _wp_post(
            post_id=42,
            title="Hello World",
            modified="2026-04-25T10:30:00",
            focus_keyword="zone 2 訓練",
        ),
    ]
    with patch("httpx.Client") as mock_cls:
        mock_cls.return_value = _httpx_ctx(200, body)
        posts = list_posts("wp_shosho")

    assert len(posts) == 1
    assert isinstance(posts[0], WpPostSummaryV1)
    assert posts[0].wp_post_id == 42
    assert posts[0].title == "Hello World"
    assert posts[0].link == "http://wp.test/post-42/"
    assert posts[0].focus_keyword == "zone 2 訓練"
    assert posts[0].last_modified == "2026-04-25T10:30:00"


def test_list_posts_handles_missing_seopress_focus_keyword():
    """No SEOPress meta → focus_keyword == '' (UI shows '—' separately)."""
    from shared.wp_post_lister import list_posts

    body = [_wp_post(post_id=1, title="Untagged", modified="2026-04-25T10:00:00")]
    with patch("httpx.Client") as mock_cls:
        mock_cls.return_value = _httpx_ctx(200, body)
        posts = list_posts("wp_shosho")

    assert len(posts) == 1
    assert posts[0].focus_keyword == ""


def test_list_posts_strips_html_entities_and_tags_from_title():
    from shared.wp_post_lister import list_posts

    body = [
        _wp_post(
            post_id=7,
            title="Sleep&nbsp;&amp; Recovery <em>科學</em>",
            modified="2026-04-25T09:00:00",
            focus_keyword="sleep",
        ),
    ]
    with patch("httpx.Client") as mock_cls:
        mock_cls.return_value = _httpx_ctx(200, body)
        posts = list_posts("wp_shosho")

    # `&nbsp;` decodes to a non-breaking space ( ); we keep that as-is
    # (it is whitespace, just not ASCII).  The key assertion: no HTML left.
    assert "<em>" not in posts[0].title
    assert "&amp;" not in posts[0].title
    assert "Recovery" in posts[0].title
    assert "科學" in posts[0].title


def test_list_posts_sorts_by_last_modified_descending():
    from shared.wp_post_lister import list_posts

    body = [
        _wp_post(post_id=1, title="oldest", modified="2026-01-01T00:00:00"),
        _wp_post(post_id=2, title="newest", modified="2026-04-25T00:00:00"),
        _wp_post(post_id=3, title="mid", modified="2026-02-15T00:00:00"),
    ]
    with patch("httpx.Client") as mock_cls:
        mock_cls.return_value = _httpx_ctx(200, body)
        posts = list_posts("wp_shosho")

    assert [p.title for p in posts] == ["newest", "mid", "oldest"]


def test_list_posts_skips_malformed_rows_but_keeps_healthy_ones():
    """Single bad row must not crash the entire listing."""
    from shared.wp_post_lister import list_posts

    body = [
        _wp_post(post_id=1, title="ok", modified="2026-04-25T00:00:00"),
        # Missing required fields
        {"id": 2, "title": {"rendered": "broken"}},
        # Not a dict at all
        "not a post",
        _wp_post(post_id=3, title="also ok", modified="2026-04-25T01:00:00"),
    ]
    with patch("httpx.Client") as mock_cls:
        mock_cls.return_value = _httpx_ctx(200, body)
        posts = list_posts("wp_shosho")

    ids = {p.wp_post_id for p in posts}
    assert ids == {1, 3}


# ---------------------------------------------------------------------------
# Cache behavior — hit + TTL expiry
# ---------------------------------------------------------------------------


def test_cache_hit_avoids_second_http_call():
    from shared.wp_post_lister import list_posts

    body = [_wp_post(post_id=1, title="post", modified="2026-04-25T10:00:00")]
    with patch("httpx.Client") as mock_cls:
        ctx = _httpx_ctx(200, body)
        mock_cls.return_value = ctx

        first = list_posts("wp_shosho")
        second = list_posts("wp_shosho")

    # Same projected content
    assert len(first) == 1
    assert first == second
    # And only one HTTP call across the two list_posts() invocations.
    assert ctx.request.call_count == 1


def test_cache_miss_after_ttl_expiry_makes_second_http_call(monkeypatch):
    """Force `time.monotonic` to jump past the TTL → second call hits HTTP."""
    from shared import wp_post_lister

    body = [_wp_post(post_id=1, title="post", modified="2026-04-25T10:00:00")]

    # Faux clock — successive calls advance.
    fake_now = {"t": 1_000.0}

    def _now() -> float:
        return fake_now["t"]

    monkeypatch.setattr(wp_post_lister.time, "monotonic", _now)

    with patch("httpx.Client") as mock_cls:
        ctx = _httpx_ctx(200, body)
        mock_cls.return_value = ctx

        # First call: fills cache at t=1000, expires at t=1000+3600=4600.
        wp_post_lister.list_posts("wp_shosho")
        assert ctx.request.call_count == 1

        # Inside TTL — still a cache hit.
        fake_now["t"] = 1_500.0
        wp_post_lister.list_posts("wp_shosho")
        assert ctx.request.call_count == 1

        # Past TTL — must re-fetch.
        fake_now["t"] = 4_700.0
        wp_post_lister.list_posts("wp_shosho")
        assert ctx.request.call_count == 2


def test_cache_keyed_per_target_site():
    """wp_shosho and wp_fleet evict independently; one populated does not
    serve the other."""
    from shared.wp_post_lister import list_posts

    shosho_body = [_wp_post(post_id=1, title="shosho", modified="2026-04-25T10:00:00")]
    fleet_body = [_wp_post(post_id=2, title="fleet", modified="2026-04-25T11:00:00")]

    with patch("httpx.Client") as mock_cls:
        # `httpx.Client` is constructed per request inside _request(); we
        # cycle the mock's `__call__` to return shosho then fleet.
        ctx_shosho = _httpx_ctx(200, shosho_body)
        ctx_fleet = _httpx_ctx(200, fleet_body)
        mock_cls.side_effect = [ctx_shosho, ctx_fleet]

        shosho = list_posts("wp_shosho")
        fleet = list_posts("wp_fleet")

    assert [p.title for p in shosho] == ["shosho"]
    assert [p.title for p in fleet] == ["fleet"]


# ---------------------------------------------------------------------------
# Error fallback — page must still render
# ---------------------------------------------------------------------------


def test_5xx_returns_empty_list_no_exception(monkeypatch):
    """WP server error → empty list (warning logged), do NOT raise.

    Patch the client method directly so we do not pay for the retry budget
    (3 attempts × `time.sleep(2/4/8)`) — that path is covered exhaustively
    in `tests/shared/test_wordpress_client.py`. Here we only care that the
    lister translates the raised error into an empty list.
    """
    from shared.wordpress_client import WordPressClient, WPServerError
    from shared.wp_post_lister import list_posts

    def _raise(self, **_kwargs):
        raise WPServerError("boom")

    monkeypatch.setattr(WordPressClient, "list_posts", _raise)
    posts = list_posts("wp_shosho")
    assert posts == []


def test_401_returns_empty_list_no_exception(monkeypatch):
    """Auth failure → empty list, no propagating WPAuthError."""
    from shared.wordpress_client import WordPressClient, WPAuthError
    from shared.wp_post_lister import list_posts

    def _raise(self, **_kwargs):
        raise WPAuthError("no")

    monkeypatch.setattr(WordPressClient, "list_posts", _raise)
    posts = list_posts("wp_shosho")
    assert posts == []


def test_missing_env_returns_empty_list(monkeypatch):
    """No WP_SHOSHO_BASE_URL → empty list + warning, no KeyError."""
    from shared.wp_post_lister import list_posts

    monkeypatch.delenv("WP_SHOSHO_BASE_URL", raising=False)
    posts = list_posts("wp_shosho")
    assert posts == []


def test_error_path_does_not_populate_cache():
    """A failed fetch must not poison the cache with []."""
    from shared.wp_post_lister import list_posts

    success_body = [_wp_post(post_id=1, title="ok", modified="2026-04-25T10:00:00")]

    # First call: WordPressClient.list_posts() raises a server error → empty
    # list, cache untouched.
    # Second call: returns a real listing → populates cache.
    # Patch on the WordPressClient method so the retry layer is not in play
    # (we want a clean two-state test, not a retry-budget test).
    from shared.wordpress_client import WordPressClient, WPServerError

    call_outputs = [WPServerError("boom"), success_body]

    def _stub(self, **_kwargs):
        out = call_outputs.pop(0)
        if isinstance(out, Exception):
            raise out
        return out

    with patch.object(WordPressClient, "list_posts", _stub):
        first = list_posts("wp_shosho")
        second = list_posts("wp_shosho")

    assert first == []
    assert len(second) == 1


# ---------------------------------------------------------------------------
# Integration smoke — full request shape
# ---------------------------------------------------------------------------


def test_list_posts_passes_per_page_and_status_params():
    """Acceptance: `/wp/v2/posts?per_page=100&status=publish`."""
    from shared.wp_post_lister import list_posts

    with patch("httpx.Client") as mock_cls:
        ctx = _httpx_ctx(200, [])
        mock_cls.return_value = ctx
        list_posts("wp_shosho")

    call_kwargs = ctx.request.call_args.kwargs
    assert call_kwargs["params"] == {"per_page": 100, "status": "publish"}
    # And the URL is the listing endpoint, not a single-post path.
    args = ctx.request.call_args.args
    assert args[0] == "GET"
    assert args[1].endswith("/wp-json/wp/v2/posts")
