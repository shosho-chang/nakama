"""Tests for shared/wordpress_client.py.

Uses `responses` library to intercept httpx calls (cassette-style mocking).
No real WP server needed.

Test coverage:
- create_post / get_post / update_post / find_by_meta happy paths
- list_categories / list_tags
- health_check success + failure
- 401 → WPAuthError (no retry)
- 5xx → WPServerError (retried up to 3 times)
- Timeout → WPServerError (retried)
- Anti-corruption: unexpected field in WP response raises ValidationError
- Rate limit: two rapid requests are separated by ≥ _RATE_LIMIT_INTERVAL_S
- SEOPress write_seopress_meta REST path + schema drift detection
- SEOPress fallback_meta path
"""

from __future__ import annotations

import time
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------

WP_BASE = "http://wp.test"
WP_USER = "nakama_publisher"
WP_PASS = "test app pass"


def _make_client():
    """Return a WordPressClient pointed at fake base URL."""
    from shared.wordpress_client import WordPressClient

    return WordPressClient(
        base_url=WP_BASE,
        username=WP_USER,
        app_password=WP_PASS,
        site_id="wp_test",
    )


def _post_body(**overrides) -> dict[str, Any]:
    """Minimal WP post response body."""
    base: dict[str, Any] = {
        "id": 42,
        "date": "2026-04-23T00:00:00",
        "date_gmt": "2026-04-23T00:00:00",
        "guid": {"rendered": "http://wp.test/?p=42", "protected": False},
        "modified": "2026-04-23T00:00:00",
        "modified_gmt": "2026-04-23T00:00:00",
        "slug": "test-post",
        "status": "draft",
        "type": "post",
        "link": "http://wp.test/test-post/",
        "title": {"rendered": "Test Post", "protected": False},
        "content": {"rendered": "<p>Hello</p>", "protected": False},
        "excerpt": {"rendered": "<p>Excerpt</p>", "protected": False},
        "author": 1,
        "featured_media": 0,
        "comment_status": "open",
        "ping_status": "open",
        "sticky": False,
        "template": "",
        "format": "standard",
        "meta": {},
        "categories": [],
        "tags": [],
    }
    base.update(overrides)
    return base


def _category_body(**overrides) -> dict[str, Any]:
    base: dict[str, Any] = {
        "id": 5,
        "count": 3,
        "description": "",
        "link": "http://wp.test/category/sleep/",
        "name": "Sleep Science",
        "slug": "sleep-science",
        "taxonomy": "category",
        "parent": 0,
        "meta": {},
    }
    base.update(overrides)
    return base


# ---------------------------------------------------------------------------
# Helper: mock httpx.Client.request
# ---------------------------------------------------------------------------


class MockResponse:
    """Minimal httpx response mock."""

    def __init__(self, status_code: int, body: Any):
        self.status_code = status_code
        self._body = body

    def json(self) -> Any:
        return self._body

    @property
    def text(self) -> str:
        import json

        return json.dumps(self._body)


def _mock_request(status_code: int, body: Any):
    """Return a context manager that mocks httpx.Client.request."""
    mock = MagicMock()
    mock.__enter__ = MagicMock(return_value=mock)
    mock.__exit__ = MagicMock(return_value=False)
    mock.request = MagicMock(return_value=MockResponse(status_code, body))
    return mock


# ---------------------------------------------------------------------------
# Tests: create_post
# ---------------------------------------------------------------------------


def test_create_post_happy_path():
    client = _make_client()
    body = _post_body(status="draft")

    with patch("httpx.Client") as mock_cls:
        mock_cls.return_value = _mock_request(201, body)
        post = client.create_post(
            title="Test Post",
            content="<p>Hello</p>",
            status="draft",
            operation_id="op_12345678",
        )

    assert post.id == 42
    assert post.status == "draft"
    assert post.title.rendered == "Test Post"


def test_create_post_with_meta():
    """meta dict is passed through and reflected in response."""
    client = _make_client()
    body = _post_body(meta={"nakama_draft_id": "draft_20260423T000000_abc123"})

    with patch("httpx.Client") as mock_cls:
        mock_cls.return_value = _mock_request(201, body)
        post = client.create_post(
            title="Test",
            content="<p>X</p>",
            meta={"nakama_draft_id": "draft_20260423T000000_abc123"},
            operation_id="op_12345678",
        )

    assert post.meta.get("nakama_draft_id") == "draft_20260423T000000_abc123"


# ---------------------------------------------------------------------------
# Tests: get_post
# ---------------------------------------------------------------------------


def test_get_post():
    client = _make_client()

    with patch("httpx.Client") as mock_cls:
        mock_cls.return_value = _mock_request(200, _post_body(id=99, status="publish"))
        post = client.get_post(99)

    assert post.id == 99
    assert post.status == "publish"


# ---------------------------------------------------------------------------
# Tests: update_post
# ---------------------------------------------------------------------------


def test_update_post_status():
    client = _make_client()

    with patch("httpx.Client") as mock_cls:
        mock_cls.return_value = _mock_request(200, _post_body(id=42, status="publish"))
        post = client.update_post(42, status="publish", operation_id="op_12345678")

    assert post.status == "publish"


# ---------------------------------------------------------------------------
# Tests: find_by_meta
# ---------------------------------------------------------------------------


def test_find_by_meta_found():
    client = _make_client()
    body = [_post_body(id=77, meta={"nakama_draft_id": "draft_xyz"})]

    with patch("httpx.Client") as mock_cls:
        mock_cls.return_value = _mock_request(200, body)
        post = client.find_by_meta("nakama_draft_id", "draft_xyz")

    assert post is not None
    assert post.id == 77


def test_find_by_meta_not_found():
    client = _make_client()

    with patch("httpx.Client") as mock_cls:
        mock_cls.return_value = _mock_request(200, [])
        post = client.find_by_meta("nakama_draft_id", "draft_notexist")

    assert post is None


# ---------------------------------------------------------------------------
# Tests: list_categories / list_tags
# ---------------------------------------------------------------------------


def test_list_categories():
    client = _make_client()
    body = [_category_body(id=5, slug="sleep-science", taxonomy="category")]

    with patch("httpx.Client") as mock_cls:
        mock_cls.return_value = _mock_request(200, body)
        cats = client.list_categories()

    assert len(cats) == 1
    assert cats[0].slug == "sleep-science"
    assert cats[0].id == 5


def test_list_tags():
    client = _make_client()
    body = [
        {
            "id": 10,
            "count": 5,
            "description": "",
            "link": "http://wp.test/tag/sleep/",
            "name": "Sleep",
            "slug": "sleep",
            "taxonomy": "post_tag",
            "parent": 0,
            "meta": {},
        }
    ]

    with patch("httpx.Client") as mock_cls:
        mock_cls.return_value = _mock_request(200, body)
        tags = client.list_tags()

    assert tags[0].slug == "sleep"


# ---------------------------------------------------------------------------
# Tests: health_check
# ---------------------------------------------------------------------------


def test_health_check_ok():
    client = _make_client()
    me_body = {"id": 2, "name": "nakama_publisher"}  # minimal /users/me

    with patch("httpx.Client") as mock_cls:
        # health_check calls GET wp/v2/users/me → 200
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = me_body
        mock_resp.text = "{}"
        ctx = MagicMock()
        ctx.__enter__ = MagicMock(return_value=ctx)
        ctx.__exit__ = MagicMock(return_value=False)
        ctx.request = MagicMock(return_value=mock_resp)
        mock_cls.return_value = ctx

        result = client.health_check()

    assert result is True


def test_health_check_auth_failure():
    client = _make_client()

    with patch("httpx.Client") as mock_cls:
        mock_resp = MagicMock()
        mock_resp.status_code = 401
        mock_resp.text = "Unauthorized"
        ctx = MagicMock()
        ctx.__enter__ = MagicMock(return_value=ctx)
        ctx.__exit__ = MagicMock(return_value=False)
        ctx.request = MagicMock(return_value=mock_resp)
        mock_cls.return_value = ctx

        result = client.health_check()

    # health_check catches WPAuthError and returns False
    assert result is False


# ---------------------------------------------------------------------------
# Tests: Error handling — 401 no retry
# ---------------------------------------------------------------------------


def test_401_raises_auth_error_no_retry():
    """401 response must raise WPAuthError without retrying."""
    from shared.wordpress_client import WPAuthError

    client = _make_client()
    call_count = 0

    def fake_request(method, url, **kwargs):
        nonlocal call_count
        call_count += 1
        return MockResponse(401, {"code": "invalid_username", "message": "Wrong creds", "data": {}})

    with patch("httpx.Client") as mock_cls:
        ctx = MagicMock()
        ctx.__enter__ = MagicMock(return_value=ctx)
        ctx.__exit__ = MagicMock(return_value=False)
        ctx.request = fake_request
        mock_cls.return_value = ctx

        with pytest.raises(WPAuthError):
            client.get_post(1)

    assert call_count == 1  # no retry on 401


# ---------------------------------------------------------------------------
# Tests: 5xx retry behaviour
# ---------------------------------------------------------------------------


def test_5xx_retries_then_raises():
    """5xx responses trigger retry up to _RETRY_ATTEMPTS, then raise WPServerError."""
    from shared.wordpress_client import _RETRY_ATTEMPTS, WPServerError

    client = _make_client()
    # Disable sleep to keep test fast
    client._last_request_at = 0.0

    call_count = 0

    def fake_request(method, url, **kwargs):
        nonlocal call_count
        call_count += 1
        return MockResponse(503, {"code": "service_unavailable", "message": "Down", "data": {}})

    with patch("httpx.Client") as mock_cls:
        ctx = MagicMock()
        ctx.__enter__ = MagicMock(return_value=ctx)
        ctx.__exit__ = MagicMock(return_value=False)
        ctx.request = fake_request
        mock_cls.return_value = ctx

        with patch("time.sleep"):  # skip actual waits
            with pytest.raises(WPServerError):
                client.get_post(1)

    assert call_count == _RETRY_ATTEMPTS


def test_5xx_succeeds_on_second_attempt():
    """If 2nd attempt succeeds, no exception raised."""
    client = _make_client()
    call_count = 0

    def fake_request(method, url, **kwargs):
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            return MockResponse(502, {"code": "bad_gateway", "message": "GW err", "data": {}})
        return MockResponse(200, _post_body(id=5))

    with patch("httpx.Client") as mock_cls:
        ctx = MagicMock()
        ctx.__enter__ = MagicMock(return_value=ctx)
        ctx.__exit__ = MagicMock(return_value=False)
        ctx.request = fake_request
        mock_cls.return_value = ctx

        with patch("time.sleep"):
            post = client.get_post(5)

    assert post.id == 5
    assert call_count == 2


# ---------------------------------------------------------------------------
# Tests: Timeout retry
# ---------------------------------------------------------------------------


def test_timeout_retries():
    """httpx.TimeoutException triggers retry."""
    import httpx

    from shared.wordpress_client import _RETRY_ATTEMPTS, WPServerError

    client = _make_client()
    call_count = 0

    def fake_request(method, url, **kwargs):
        nonlocal call_count
        call_count += 1
        raise httpx.TimeoutException("timed out")

    with patch("httpx.Client") as mock_cls:
        ctx = MagicMock()
        ctx.__enter__ = MagicMock(return_value=ctx)
        ctx.__exit__ = MagicMock(return_value=False)
        ctx.request = fake_request
        mock_cls.return_value = ctx

        with patch("time.sleep"):
            with pytest.raises(WPServerError):
                client.get_post(1)

    assert call_count == _RETRY_ATTEMPTS


# ---------------------------------------------------------------------------
# Tests: Anti-corruption — unexpected field raises ValidationError
# ---------------------------------------------------------------------------


def test_unexpected_field_raises_validation_error():
    """WP response with extra unexpected field must raise pydantic.ValidationError."""
    from pydantic import ValidationError

    client = _make_client()
    bad_body = _post_body()
    bad_body["surprise_new_field"] = "this_was_not_there_in_6_9_4"

    with patch("httpx.Client") as mock_cls:
        mock_cls.return_value = _mock_request(200, bad_body)
        with pytest.raises(ValidationError):
            client.get_post(42)


# ---------------------------------------------------------------------------
# Tests: Rate limit
# ---------------------------------------------------------------------------


def test_rate_limit_enforced(monkeypatch):
    """Two consecutive requests must be separated by ≥ _RATE_LIMIT_INTERVAL_S."""
    from shared.wordpress_client import _RATE_LIMIT_INTERVAL_S

    client = _make_client()
    timestamps: list[float] = []

    def fake_request(method, url, **kwargs):
        timestamps.append(time.monotonic())
        return MockResponse(200, _post_body())

    with patch("httpx.Client") as mock_cls:
        ctx = MagicMock()
        ctx.__enter__ = MagicMock(return_value=ctx)
        ctx.__exit__ = MagicMock(return_value=False)
        ctx.request = fake_request
        mock_cls.return_value = ctx

        client.get_post(1)
        client.get_post(2)

    assert len(timestamps) == 2
    gap = timestamps[1] - timestamps[0]
    assert gap >= _RATE_LIMIT_INTERVAL_S * 0.9, f"Rate limit not enforced: gap={gap:.3f}s"


# ---------------------------------------------------------------------------
# Tests: SEOPress — REST write happy path
# ---------------------------------------------------------------------------


def test_write_seopress_meta_rest_happy():
    """write_seopress_meta returns ('rest') on 200 response."""
    from shared.schemas.external.seopress import SEOpressWritePayloadV1

    client = _make_client()
    payload = SEOpressWritePayloadV1(
        title="Test SEO Title",
        description="A meta description for testing, at least 50 characters long.",
        focus_keyword="sleep science",
    )
    seopress_resp = {
        "title": "Test SEO Title",
        "description": "A meta description for testing, at least 50 characters long.",
        "focus_keyword": "sleep science",
        "canonical": "",
        "noindex": False,
        "nofollow": False,
    }

    with patch("httpx.Client") as mock_cls:
        mock_cls.return_value = _mock_request(200, seopress_resp)
        success, path = client.write_seopress_meta(42, payload, operation_id="op_12345678")

    assert success is True
    assert path == "rest"


# ---------------------------------------------------------------------------
# Tests: SEOPress — schema drift detection
# ---------------------------------------------------------------------------


def test_seopress_schema_drift_raises():
    """SEOPress response with unexpected field triggers SEOPressSchemaDriftError."""
    from shared.schemas.external.seopress import SEOPressSchemaDriftError

    # parse_seopress_response is called inside write_seopress_meta
    drifted_resp = {
        "title": "T",
        "description": "D" * 50,
        "focus_keyword": "kw",
        "canonical": "",
        "noindex": False,
        "nofollow": False,
        "brand_new_field_in_v10": "boom",  # ← drift
    }

    from shared.schemas.external.seopress import parse_seopress_response

    with pytest.raises(SEOPressSchemaDriftError):
        parse_seopress_response(drifted_resp)


# ---------------------------------------------------------------------------
# Tests: SEOPress — Fallback A (write_seopress_fallback_meta)
# ---------------------------------------------------------------------------


def test_write_seopress_fallback_meta():
    """Fallback A writes raw meta keys and returns True."""
    from shared.schemas.external.seopress import SEOpressWritePayloadV1

    client = _make_client()
    payload = SEOpressWritePayloadV1(
        title="Fallback Title",
        description="Fallback description that is long enough to pass validation here.",
        focus_keyword="fallback keyword",
    )

    with patch("httpx.Client") as mock_cls:
        mock_cls.return_value = _mock_request(200, _post_body())
        result = client.write_seopress_fallback_meta(42, payload, operation_id="op_12345678")

    assert result is True
