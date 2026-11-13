"""Tests for shared/fca_events_client.py — no network (httpx.MockTransport)."""

from __future__ import annotations

import base64
import json
import re
from datetime import datetime, timedelta, timezone

import httpx
import pytest

from shared.fca_events_client import (
    CreatedEvent,
    EventDraft,
    FcaEventsClient,
    FcaEventsResponseError,
    UploadedImage,
    normalize_base_url,
)
from shared.wordpress_client import WPAuthError, WPClientError, WPServerError

BASE = "https://fleet.example.test"


class Recorder:
    """MockTransport handler that records requests and replies with a canned response."""

    def __init__(
        self, status: int = 200, body=None, *, text: str | None = None, exc: Exception | None = None
    ):
        self.status = status
        self.body = body
        self.text = text
        self.exc = exc
        self.requests: list[httpx.Request] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        request.read()
        self.requests.append(request)
        if self.exc is not None:
            raise self.exc
        if self.text is not None:
            return httpx.Response(self.status, text=self.text)
        return httpx.Response(self.status, json=self.body)


def _client(handler: Recorder, base: str = BASE) -> FcaEventsClient:
    return FcaEventsClient(
        base_url=base,
        username="sanji-bot",
        app_password="abcd efgh ijkl",
        transport=httpx.MockTransport(handler),
    )


def _draft(**overrides) -> EventDraft:
    # 2026-12-01 12:00 UTC == 2026-12-01 20:00 Asia/Taipei
    start = datetime(2026, 12, 1, 12, 0, tzinfo=timezone.utc)
    values = dict(
        title="  長壽讀書會  ",
        content_html="<p>hello</p>",
        start=start,
        end=start + timedelta(hours=1, minutes=30),
        event_type="online",
        event_url="https://us02web.zoom.us/j/123456789",
        meeting_provider="zoom",
    )
    values.update(overrides)
    return EventDraft(**values)


# ---------------------------------------------------------------------------
# Base URL / env
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "raw",
    [
        "https://fleet.shosho.tw",
        "https://fleet.shosho.tw/",
        "https://fleet.shosho.tw/wp-json",
        "https://fleet.shosho.tw/wp-json/",
        "  https://fleet.shosho.tw/wp-json/  ",
    ],
)
def test_normalize_base_url(raw):
    assert normalize_base_url(raw) == "https://fleet.shosho.tw"


def test_request_url_and_auth_with_wp_json_base():
    handler = Recorder(201, {"id": 5, "url": "u", "thumbnail": "t"})
    client = _client(handler, base="https://fleet.shosho.tw/wp-json")
    client.upload_image(b"\x89PNG", "cover.png")
    req = handler.requests[0]
    assert str(req.url) == "https://fleet.shosho.tw/wp-json/fca-events/v1/upload"
    expected = base64.b64encode(b"sanji-bot:abcdefghijkl").decode()
    assert req.headers["Authorization"] == f"Basic {expected}"
    assert req.headers["User-Agent"] == "nakama-fca-events-client/1.0"
    assert client.edit_url("abc") == "https://fleet.shosho.tw/deck/events/edit/abc"
    assert client.manage_url == "https://fleet.shosho.tw/deck/events/manage"


def test_from_env_prefers_username(monkeypatch):
    monkeypatch.setenv("WP_FLEET_BASE_URL", "https://fleet.shosho.tw/wp-json")
    monkeypatch.setenv("WP_FLEET_USERNAME", "primary")
    monkeypatch.setenv("WP_FLEET_USER", "fallback")
    monkeypatch.setenv("WP_FLEET_APP_PASSWORD", "aa bb")
    client = FcaEventsClient.from_env()
    assert client.base_url == "https://fleet.shosho.tw"
    assert client._b64 == base64.b64encode(b"primary:aabb").decode()


def test_from_env_falls_back_to_user(monkeypatch):
    monkeypatch.setenv("WP_FLEET_BASE_URL", "https://fleet.shosho.tw")
    monkeypatch.delenv("WP_FLEET_USERNAME", raising=False)
    monkeypatch.setenv("WP_FLEET_USER", "fallback")
    monkeypatch.setenv("WP_FLEET_APP_PASSWORD", "aa bb")
    client = FcaEventsClient.from_env()
    assert client._b64 == base64.b64encode(b"fallback:aabb").decode()


def test_from_env_missing(monkeypatch):
    monkeypatch.setenv("WP_FLEET_BASE_URL", "https://fleet.shosho.tw")
    monkeypatch.delenv("WP_FLEET_USERNAME", raising=False)
    monkeypatch.delenv("WP_FLEET_USER", raising=False)
    monkeypatch.delenv("WP_FLEET_APP_PASSWORD", raising=False)
    with pytest.raises(KeyError, match="WP_FLEET_APP_PASSWORD"):
        FcaEventsClient.from_env()


# ---------------------------------------------------------------------------
# Upload
# ---------------------------------------------------------------------------


def test_upload_multipart_field_file():
    handler = Recorder(
        201, {"id": 777, "url": "https://fleet/x.png", "thumbnail": "https://fleet/x-150.png"}
    )
    image = _client(handler).upload_image(b"\x89PNGdata", "cover.png")
    assert image == UploadedImage(id=777, url="https://fleet/x.png")
    req = handler.requests[0]
    assert req.method == "POST"
    assert req.headers["Content-Type"].startswith("multipart/form-data")
    body = req.content
    assert b'name="file"; filename="cover.png"' in body
    assert b"Content-Type: image/png" in body
    assert b"\x89PNGdata" in body


def test_upload_too_large_rejected_before_sending():
    handler = Recorder(201, {"id": 1, "url": "u"})
    with pytest.raises(ValueError, match="5 MB"):
        _client(handler).upload_image(b"x" * (5 * 1024 * 1024 + 1), "big.jpg")
    assert handler.requests == []


def test_upload_bad_extension_rejected():
    handler = Recorder(201, {"id": 1, "url": "u"})
    with pytest.raises(ValueError):
        _client(handler).upload_image(b"GIF89a", "anim.gif")
    assert handler.requests == []


# ---------------------------------------------------------------------------
# Create
# ---------------------------------------------------------------------------


def test_create_body_and_result():
    handler = Recorder(200, {"id": 321, "status": "draft", "slug": "server-slug"})
    created = _client(handler).create_draft_event(_draft(featured_image_id=777))

    req = handler.requests[0]
    assert str(req.url) == f"{BASE}/wp-json/fca-events/v1/events"
    payload = json.loads(req.content)
    assert payload["status"] == "draft"
    assert payload["hide_from_global"] == "no"
    assert payload["title"] == "長壽讀書會"
    assert payload["content"] == "<p>hello</p>"
    # UTC 12:00 → Asia/Taipei 20:00 wall time; end 13:30 UTC → 21:30
    assert payload["date"] == "2026-12-01"
    assert payload["time"] == "20:00"
    assert payload["end_date"] == "2026-12-01"
    assert payload["end_time"] == "21:30"
    assert payload["timezone"] == "Asia/Taipei"
    assert payload["event_type"] == "online"
    assert payload["event_url"] == "https://us02web.zoom.us/j/123456789"
    assert payload["meeting_provider"] == "zoom"
    assert payload["featured_image_id"] == 777
    assert "space_ids" not in payload
    assert "location" not in payload
    assert re.fullmatch(r"sanji-20261201-[a-z0-9]{6}", payload["slug"])

    assert created == CreatedEvent(
        id=321,
        slug="server-slug",
        status="draft",
        edit_url=f"{BASE}/deck/events/edit/server-slug",
    )


def test_create_slug_uses_local_date_across_midnight():
    # 2026-12-01 17:00 UTC == 2026-12-02 01:00 Asia/Taipei
    start = datetime(2026, 12, 1, 17, 0, tzinfo=timezone.utc)
    handler = Recorder(200, {"id": 1, "status": "draft"})
    created = _client(handler).create_draft_event(
        _draft(start=start, end=start + timedelta(hours=2))
    )
    payload = json.loads(handler.requests[0].content)
    assert payload["date"] == "2026-12-02"
    assert payload["time"] == "01:00"
    assert payload["slug"].startswith("sanji-20261202-")
    # Response lacks slug → falls back to the slug we sent.
    assert created.slug == payload["slug"]
    assert created.edit_url.endswith(f"/deck/events/edit/{payload['slug']}")


def test_create_in_person_sends_location():
    handler = Recorder(200, {"id": 2, "status": "draft", "slug": "s"})
    _client(handler).create_draft_event(
        _draft(event_type="in_person", event_url="", meeting_provider="", location="台北市信義區")
    )
    payload = json.loads(handler.requests[0].content)
    assert payload["event_type"] == "in_person"
    assert payload["location"] == "台北市信義區"
    assert "event_url" not in payload
    assert "meeting_provider" not in payload
    assert "featured_image_id" not in payload


def test_create_published_response_raises_with_id():
    handler = Recorder(200, {"id": 999, "status": "publish", "slug": "oops"})
    with pytest.raises(FcaEventsResponseError, match="999"):
        _client(handler).create_draft_event(_draft())


def test_create_missing_status_raises():
    handler = Recorder(200, {"id": 998, "slug": "oops"})
    with pytest.raises(FcaEventsResponseError, match="998"):
        _client(handler).create_draft_event(_draft())


@pytest.mark.parametrize("key", ["event", "data"])
def test_create_wrapped_response(key):
    handler = Recorder(
        201, {"message": "ok", key: {"id": 55, "status": "draft", "slug": "wrapped"}}
    )
    created = _client(handler).create_draft_event(_draft())
    assert created.id == 55
    assert created.slug == "wrapped"


def test_create_403_auth_error_mentions_event_manager():
    handler = Recorder(403, {"code": "rest_forbidden", "message": "Sorry"})
    with pytest.raises(WPAuthError) as excinfo:
        _client(handler).create_draft_event(_draft())
    msg = str(excinfo.value)
    assert "Event Manager" in msg
    assert "rest_forbidden" in msg
    assert "abcdefghijkl" not in msg


def test_create_400_client_error_truncates_body():
    handler = Recorder(400, text="x" * 1000)
    with pytest.raises(WPClientError) as excinfo:
        _client(handler).create_draft_event(_draft())
    assert "x" * 300 in str(excinfo.value)
    assert "x" * 301 not in str(excinfo.value)


def test_create_500_server_error_no_retry():
    handler = Recorder(502, text="bad gateway")
    with pytest.raises(WPServerError, match="502"):
        _client(handler).create_draft_event(_draft())
    assert len(handler.requests) == 1


def test_timeout_maps_to_server_error():
    handler = Recorder(exc=httpx.ReadTimeout("slow"))
    with pytest.raises(WPServerError, match="timeout"):
        _client(handler).create_draft_event(_draft())
    assert len(handler.requests) == 1


# ---------------------------------------------------------------------------
# Validation (nothing sent)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "overrides",
    [
        {"title": "   "},
        {"start": datetime(2026, 12, 1, 20, 0), "end": datetime(2026, 12, 1, 21, 0)},
        {"end": datetime(2026, 12, 1, 12, 0, tzinfo=timezone.utc)},  # end == start
        {"end": datetime(2026, 12, 1, 11, 0, tzinfo=timezone.utc)},  # end < start
        {"event_url": ""},  # online without link
    ],
)
def test_validation_errors(overrides):
    handler = Recorder(200, {"id": 1, "status": "draft"})
    with pytest.raises(ValueError):
        _client(handler).create_draft_event(_draft(**overrides))
    assert handler.requests == []
