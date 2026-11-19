"""shared/zoom.py — Server-to-Server OAuth + 建立 / 刪除會議（httpx MockTransport，不打真 API）。"""

from __future__ import annotations

import json

import httpx
import pytest

from shared import zoom


@pytest.fixture(autouse=True)
def _env_and_clean_token(monkeypatch):
    monkeypatch.setenv("ZOOM_ACCOUNT_ID", "acct-1")
    monkeypatch.setenv("ZOOM_CLIENT_ID", "cid")
    monkeypatch.setenv("ZOOM_CLIENT_SECRET", "secret")
    monkeypatch.delenv("ZOOM_USER_ID", raising=False)
    zoom._token_cache.update({"value": None, "expires_at": 0.0})
    yield
    zoom._token_cache.update({"value": None, "expires_at": 0.0})


def _install(monkeypatch, handler):
    """把 zoom._make_client 換成走 MockTransport 的 client；回傳收到的 request 清單。"""
    seen: list[httpx.Request] = []

    def _wrapped(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return handler(request)

    monkeypatch.setattr(
        zoom, "_make_client", lambda: httpx.Client(transport=httpx.MockTransport(_wrapped))
    )
    return seen


def _ok_handler(request: httpx.Request) -> httpx.Response:
    if request.url.host == "zoom.us":
        return httpx.Response(200, json={"access_token": "tok-1", "expires_in": 3600})
    if request.method == "POST":
        return httpx.Response(
            201,
            json={
                "id": 8812345678,
                "join_url": "https://zoom.us/j/8812345678?pwd=abc",
                "password": "pw123",
                "start_url": "https://zoom.us/s/8812345678?zak=SECRET",
            },
        )
    return httpx.Response(204)


def test_is_configured_requires_all_three(monkeypatch):
    assert zoom.is_configured() is True
    monkeypatch.setenv("ZOOM_CLIENT_SECRET", "")
    assert zoom.is_configured() is False


def test_create_meeting_happy_path_body_and_no_start_url(monkeypatch):
    seen = _install(monkeypatch, _ok_handler)

    m = zoom.create_meeting(
        topic="跟 Angie 開會",
        start="2026-04-25T15:00:00",
        end="2026-04-25T16:30:00",
        agenda="討論 Q2",
    )

    assert m.id == "8812345678"
    assert m.join_url == "https://zoom.us/j/8812345678?pwd=abc"
    assert m.passcode == "pw123"
    assert not hasattr(m, "start_url")  # 主持人連結帶權杖，不保存

    token_req, create_req = seen
    assert token_req.url.params["grant_type"] == "account_credentials"
    assert token_req.url.params["account_id"] == "acct-1"
    assert token_req.headers["authorization"].startswith("Basic ")
    assert create_req.url.path == "/v2/users/me/meetings"
    assert create_req.headers["authorization"] == "Bearer tok-1"
    body = json.loads(create_req.content)
    assert body == {
        "topic": "跟 Angie 開會",
        "type": 2,
        "start_time": "2026-04-25T15:00:00",
        "timezone": "Asia/Taipei",
        "duration": 90,
        "agenda": "討論 Q2",
    }


def test_create_meeting_converts_offset_to_taipei_and_uses_user_id(monkeypatch):
    monkeypatch.setenv("ZOOM_USER_ID", "host@example.com")
    seen = _install(monkeypatch, _ok_handler)

    zoom.create_meeting(
        topic="t", start="2026-04-25T07:00:00+00:00", end="2026-04-25T07:30:00+00:00"
    )

    create_req = seen[-1]
    assert create_req.url.path == "/v2/users/host@example.com/meetings"
    body = json.loads(create_req.content)
    assert body["start_time"] == "2026-04-25T15:00:00"  # UTC 07:00 = 台北 15:00
    assert "agenda" not in body


def test_token_is_cached_across_calls(monkeypatch):
    seen = _install(monkeypatch, _ok_handler)
    zoom.create_meeting(topic="a", start="2026-04-25T15:00:00", end="2026-04-25T16:00:00")
    zoom.create_meeting(topic="b", start="2026-04-26T15:00:00", end="2026-04-26T16:00:00")
    assert sum(1 for r in seen if r.url.host == "zoom.us") == 1


def test_401_refreshes_token_once_then_succeeds(monkeypatch):
    state = {"api_calls": 0, "tokens": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.host == "zoom.us":
            state["tokens"] += 1
            return httpx.Response(
                200, json={"access_token": f"tok-{state['tokens']}", "expires_in": 3600}
            )
        state["api_calls"] += 1
        if state["api_calls"] == 1:
            return httpx.Response(401, json={"code": 124, "message": "Invalid access token."})
        return httpx.Response(201, json={"id": 1, "join_url": "https://zoom.us/j/1"})

    _install(monkeypatch, handler)
    m = zoom.create_meeting(topic="t", start="2026-04-25T15:00:00", end="2026-04-25T16:00:00")
    assert m.join_url == "https://zoom.us/j/1"
    assert state["tokens"] == 2


def test_missing_credentials_raises_with_names(monkeypatch):
    monkeypatch.setenv("ZOOM_ACCOUNT_ID", "")
    monkeypatch.setenv("ZOOM_CLIENT_ID", "")
    with pytest.raises(zoom.ZoomError, match="ZOOM_ACCOUNT_ID.*ZOOM_CLIENT_ID"):
        zoom.create_meeting(topic="t", start="2026-04-25T15:00:00", end="2026-04-25T16:00:00")


def test_auth_failure_message_points_to_activate(monkeypatch):
    _install(
        monkeypatch,
        lambda r: httpx.Response(
            400, json={"reason": "Invalid client_id or client_secret", "error": "invalid_client"}
        ),
    )
    with pytest.raises(zoom.ZoomError, match="授權失敗.*Activate"):
        zoom.create_meeting(topic="t", start="2026-04-25T15:00:00", end="2026-04-25T16:00:00")


def test_api_error_surfaces_zoom_message(monkeypatch):
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.host == "zoom.us":
            return httpx.Response(200, json={"access_token": "t", "expires_in": 3600})
        return httpx.Response(
            400,
            json={
                "code": 4711,
                "message": "Invalid access token, does not contain scopes",
            },
        )

    _install(monkeypatch, handler)
    with pytest.raises(zoom.ZoomError, match="does not contain scopes.*4711"):
        zoom.create_meeting(topic="t", start="2026-04-25T15:00:00", end="2026-04-25T16:00:00")


def test_network_error_wrapped(monkeypatch):
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("boom")

    _install(monkeypatch, handler)
    with pytest.raises(zoom.ZoomError, match="連不上"):
        zoom.create_meeting(topic="t", start="2026-04-25T15:00:00", end="2026-04-25T16:00:00")


def test_end_not_after_start_rejected_before_any_request(monkeypatch):
    seen = _install(monkeypatch, _ok_handler)
    with pytest.raises(zoom.ZoomError, match="晚於"):
        zoom.create_meeting(topic="t", start="2026-04-25T16:00:00", end="2026-04-25T15:00:00")
    assert seen == []


def test_bad_time_format_rejected(monkeypatch):
    _install(monkeypatch, _ok_handler)
    with pytest.raises(zoom.ZoomError, match="格式無效"):
        zoom.create_meeting(topic="t", start="明天下午", end="2026-04-25T15:00:00")


def test_delete_meeting_idempotent_on_404(monkeypatch):
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.host == "zoom.us":
            return httpx.Response(200, json={"access_token": "t", "expires_in": 3600})
        return httpx.Response(404, json={"code": 3001, "message": "Meeting does not exist"})

    seen = _install(monkeypatch, handler)
    zoom.delete_meeting("123")  # 不 raise
    assert seen[-1].method == "DELETE"
    assert seen[-1].url.path == "/v2/meetings/123"


def test_delete_meeting_other_error_raises(monkeypatch):
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.host == "zoom.us":
            return httpx.Response(200, json={"access_token": "t", "expires_in": 3600})
        return httpx.Response(500, json={"message": "server on fire"})

    _install(monkeypatch, handler)
    with pytest.raises(zoom.ZoomError, match="server on fire"):
        zoom.delete_meeting("123")
