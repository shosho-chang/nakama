"""shared/zoom_client.py — 全部走 httpx.MockTransport，不打真 Zoom API。"""

from __future__ import annotations

import base64
import json
from datetime import datetime, timezone

import httpx
import pytest

from shared import zoom_client
from shared.zoom_client import ZoomError, ZoomMeeting, create_meeting, delete_meeting

MEETING_JSON = {
    "id": 81234567890,
    "join_url": "https://us06web.zoom.us/j/81234567890?pwd=abc",
    "start_url": "https://us06web.zoom.us/s/81234567890?zak=xyz",
    "password": "123456",
    "start_time": "2026-11-20T12:00:00Z",
    "timezone": "Asia/Taipei",
}


class Recorder:
    """MockTransport handler that records requests and dispatches by URL."""

    def __init__(
        self, *, create_status=201, create_json=None, delete_status=204, token_statuses=()
    ):
        self.requests: list[httpx.Request] = []
        self.create_status = create_status
        self.create_json = MEETING_JSON if create_json is None else create_json
        self.delete_status = delete_status
        self.token_statuses = list(token_statuses)

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        if request.url.host == "zoom.us" and request.url.path == "/oauth/token":
            status = self.token_statuses.pop(0) if self.token_statuses else 200
            if status != 200:
                return httpx.Response(status, text="token boom")
            return httpx.Response(200, json={"access_token": "tok-1", "expires_in": 3600})
        if request.method == "POST" and request.url.path == "/v2/users/me/meetings":
            if self.create_status >= 400:
                return httpx.Response(self.create_status, text="x" * 500)
            return httpx.Response(self.create_status, json=self.create_json)
        if request.method == "DELETE" and request.url.path.startswith("/v2/meetings/"):
            if self.delete_status >= 400:
                return httpx.Response(self.delete_status, json={"code": 3001, "message": "nope"})
            return httpx.Response(self.delete_status)
        return httpx.Response(500, text="unexpected")

    def token_requests(self):
        return [r for r in self.requests if r.url.path == "/oauth/token"]

    def api_requests(self):
        return [r for r in self.requests if r.url.host == "api.zoom.us"]


@pytest.fixture(autouse=True)
def _env(monkeypatch):
    monkeypatch.setenv("ZOOM_ACCOUNT_ID", "acct-123")
    monkeypatch.setenv("ZOOM_CLIENT_ID", "cid")
    monkeypatch.setenv("ZOOM_CLIENT_SECRET", "secret")
    monkeypatch.setattr(zoom_client, "_TOKEN_RETRY_DELAY_S", 0.0)
    zoom_client._token_cache.clear()
    yield
    zoom_client._token_cache.clear()


@pytest.fixture
def mock(monkeypatch):
    def install(**kwargs) -> Recorder:
        rec = Recorder(**kwargs)
        monkeypatch.setattr(zoom_client, "_transport", httpx.MockTransport(rec))
        return rec

    return install


START_UTC = datetime(2026, 11, 20, 12, 0, tzinfo=timezone.utc)


def test_token_request_shape(mock):
    rec = mock()
    create_meeting("讀書會", START_UTC, 60)
    (tok,) = rec.token_requests()
    assert tok.method == "POST"
    assert tok.url.params["grant_type"] == "account_credentials"
    assert tok.url.params["account_id"] == "acct-123"
    expected = "Basic " + base64.b64encode(b"cid:secret").decode()
    assert tok.headers["Authorization"] == expected


def test_token_cached_across_two_creates(mock):
    rec = mock()
    create_meeting("A", START_UTC, 30)
    create_meeting("B", START_UTC, 30)
    assert len(rec.token_requests()) == 1
    api = rec.api_requests()
    assert len(api) == 2
    assert all(r.headers["Authorization"] == "Bearer tok-1" for r in api)


def test_token_refetched_after_expiry(mock, monkeypatch):
    rec = mock()
    clock = [1000.0]
    monkeypatch.setattr(zoom_client.time, "monotonic", lambda: clock[0])
    create_meeting("A", START_UTC, 30)
    clock[0] += 3600 - 60 + 1  # past the 60s safety margin
    create_meeting("B", START_UTC, 30)
    assert len(rec.token_requests()) == 2


def test_create_body_converts_utc_to_taipei_wall_time(mock):
    rec = mock()
    create_meeting("  讀書會  ", START_UTC, 90)
    (req,) = rec.api_requests()
    assert req.method == "POST"
    assert str(req.url) == "https://api.zoom.us/v2/users/me/meetings"
    body = json.loads(req.content)
    assert body == {
        "topic": "讀書會",
        "type": 2,
        "start_time": "2026-11-20T20:00:00",  # UTC 12:00 → Taipei 20:00
        "timezone": "Asia/Taipei",
        "duration": 90,
    }


def test_topic_truncated_to_200(mock):
    rec = mock()
    create_meeting("字" * 250, START_UTC, 30)
    body = json.loads(rec.api_requests()[0].content)
    assert body["topic"] == "字" * 200


def test_create_returns_dataclass(mock):
    mock()
    meeting = create_meeting("讀書會", START_UTC, 60)
    assert meeting == ZoomMeeting(
        id=81234567890,
        join_url="https://us06web.zoom.us/j/81234567890?pwd=abc",
        start_url="https://us06web.zoom.us/s/81234567890?zak=xyz",
        password="123456",
    )


def test_create_4xx_raises_zoom_error_with_status_and_truncated_body(mock):
    rec = mock(create_status=400)
    with pytest.raises(ZoomError) as excinfo:
        create_meeting("讀書會", START_UTC, 60)
    msg = str(excinfo.value)
    assert "400" in msg
    assert "x" * 300 in msg and "x" * 301 not in msg
    assert len(rec.api_requests()) == 1  # no retry on create


def test_create_5xx_not_retried(mock):
    rec = mock(create_status=503)
    with pytest.raises(ZoomError, match="503"):
        create_meeting("讀書會", START_UTC, 60)
    assert len(rec.api_requests()) == 1


def test_create_401_invalidates_token_cache(mock):
    rec = mock(create_status=401)
    with pytest.raises(ZoomError, match="401"):
        create_meeting("讀書會", START_UTC, 60)
    assert zoom_client._token_cache == {}
    rec.create_status = 201
    create_meeting("讀書會", START_UTC, 60)
    assert len(rec.token_requests()) == 2


def test_create_timeout_raises_zoom_error(monkeypatch):
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/oauth/token":
            return httpx.Response(200, json={"access_token": "t", "expires_in": 3600})
        raise httpx.ReadTimeout("slow", request=request)

    monkeypatch.setattr(zoom_client, "_transport", httpx.MockTransport(handler))
    with pytest.raises(ZoomError, match="逾時"):
        create_meeting("讀書會", START_UTC, 60)


def test_token_5xx_retried_once_then_succeeds(mock):
    rec = mock(token_statuses=[502])
    create_meeting("讀書會", START_UTC, 60)
    assert len(rec.token_requests()) == 2


def test_token_5xx_twice_raises(mock):
    rec = mock(token_statuses=[502, 503])
    with pytest.raises(ZoomError, match="503"):
        create_meeting("讀書會", START_UTC, 60)
    assert len(rec.token_requests()) == 2
    assert rec.api_requests() == []


def test_token_4xx_not_retried(mock):
    rec = mock(token_statuses=[401])
    with pytest.raises(ZoomError, match="401"):
        create_meeting("讀書會", START_UTC, 60)
    assert len(rec.token_requests()) == 1


@pytest.mark.parametrize("missing", ["ZOOM_ACCOUNT_ID", "ZOOM_CLIENT_ID", "ZOOM_CLIENT_SECRET"])
def test_missing_env_raises_naming_variable(mock, monkeypatch, missing):
    rec = mock()
    monkeypatch.delenv(missing)
    with pytest.raises(ZoomError, match=missing):
        create_meeting("讀書會", START_UTC, 60)
    assert rec.requests == []


def test_naive_datetime_raises_value_error(mock):
    rec = mock()
    with pytest.raises(ValueError):
        create_meeting("讀書會", datetime(2026, 11, 20, 20, 0), 60)
    assert rec.requests == []


@pytest.mark.parametrize("duration", [0, 1441, -5])
def test_duration_out_of_range_raises(mock, duration):
    mock()
    with pytest.raises(ValueError):
        create_meeting("讀書會", START_UTC, duration)


def test_empty_topic_raises(mock):
    mock()
    with pytest.raises(ValueError):
        create_meeting("   ", START_UTC, 60)


def test_delete_success(mock):
    rec = mock()
    assert delete_meeting(81234567890) is None
    (req,) = rec.api_requests()
    assert req.method == "DELETE"
    assert str(req.url) == "https://api.zoom.us/v2/meetings/81234567890"
    assert req.headers["Authorization"] == "Bearer tok-1"


def test_delete_404_raises_zoom_error(mock):
    mock(delete_status=404)
    with pytest.raises(ZoomError, match="404"):
        delete_meeting(1)
