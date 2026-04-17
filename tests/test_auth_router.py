"""Tests for shared auth router (login / logout) and VPS mode fallback."""

import importlib

import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def client_vps(monkeypatch):
    """VPS 模式：DISABLE_ROBIN=1，Robin router 不掛載。"""
    monkeypatch.setenv("DISABLE_ROBIN", "1")
    monkeypatch.setenv("WEB_PASSWORD", "testpass")
    monkeypatch.setenv("WEB_SECRET", "testsecret")
    import thousand_sunny.app as app_module
    import thousand_sunny.auth as auth_module
    import thousand_sunny.routers.auth as auth_router_module

    importlib.reload(auth_module)
    importlib.reload(auth_router_module)
    importlib.reload(app_module)
    return TestClient(app_module.app, follow_redirects=False)


@pytest.fixture
def client_local(monkeypatch):
    """本機模式：Robin router 掛載。"""
    monkeypatch.delenv("DISABLE_ROBIN", raising=False)
    monkeypatch.setenv("WEB_PASSWORD", "testpass")
    monkeypatch.setenv("WEB_SECRET", "testsecret")
    import thousand_sunny.app as app_module
    import thousand_sunny.auth as auth_module
    import thousand_sunny.routers.auth as auth_router_module

    importlib.reload(auth_module)
    importlib.reload(auth_router_module)
    importlib.reload(app_module)
    return TestClient(app_module.app, follow_redirects=False)


def test_login_page_available_in_vps_mode(client_vps):
    """VPS 模式下 /login 必須能訪問（原本 PR #13 的 bug）。"""
    r = client_vps.get("/login")
    assert r.status_code == 200
    assert "password" in r.text.lower()


def test_login_page_shows_next_param(client_vps):
    r = client_vps.get("/login?next=/brook/chat")
    assert r.status_code == 200
    assert "/brook/chat" in r.text


def test_login_success_redirects_to_next(client_vps):
    r = client_vps.post(
        "/login",
        data={"password": "testpass", "next": "/brook/chat"},
    )
    assert r.status_code == 302
    assert r.headers["location"] == "/brook/chat"
    assert "nakama_auth" in r.cookies


def test_login_success_default_redirect(client_vps):
    r = client_vps.post("/login", data={"password": "testpass"})
    assert r.status_code == 302
    assert r.headers["location"] == "/"


def test_login_wrong_password(client_vps):
    r = client_vps.post("/login", data={"password": "wrong"})
    assert r.status_code == 401
    assert "密碼錯誤" in r.text


def test_next_param_rejects_absolute_url(client_vps):
    """防 open redirect：外部 URL 應被拒絕，fallback 到 /。"""
    r = client_vps.post(
        "/login",
        data={"password": "testpass", "next": "https://evil.com/phish"},
    )
    assert r.status_code == 302
    assert r.headers["location"] == "/"


def test_next_param_rejects_protocol_relative(client_vps):
    r = client_vps.post(
        "/login",
        data={"password": "testpass", "next": "//evil.com/phish"},
    )
    assert r.status_code == 302
    assert r.headers["location"] == "/"


def test_next_param_rejects_non_slash_prefix(client_vps):
    r = client_vps.post(
        "/login",
        data={"password": "testpass", "next": "javascript:alert(1)"},
    )
    assert r.status_code == 302
    assert r.headers["location"] == "/"


def test_logout_clears_cookie(client_vps):
    r = client_vps.post("/logout")
    assert r.status_code == 302
    assert r.headers["location"] == "/login"
    # Set-Cookie 應該清除 nakama_auth
    set_cookie = r.headers.get("set-cookie", "")
    assert "nakama_auth" in set_cookie


def test_vps_root_redirects_to_brook(client_vps):
    """VPS 模式下 / 應重導到 /brook/chat，而非 404。"""
    r = client_vps.get("/")
    assert r.status_code == 302
    assert r.headers["location"] == "/brook/chat"


def test_vps_brook_unauth_redirects_with_next(client_vps):
    """VPS 上訪問 Brook 未登入應帶 ?next=/brook/chat redirect 到 /login。"""
    r = client_vps.get("/brook/chat")
    assert r.status_code == 302
    assert r.headers["location"] == "/login?next=/brook/chat"


def test_vps_brook_with_auth(client_vps):
    """登入後應能訪問 Brook chat 頁。"""
    login = client_vps.post("/login", data={"password": "testpass"})
    cookie = login.cookies.get("nakama_auth")
    assert cookie

    r = client_vps.get("/brook/chat", cookies={"nakama_auth": cookie})
    assert r.status_code == 200


def test_local_robin_root_available(client_local):
    """本機模式下 Robin / 仍可訪問（未登入會 redirect 到 /login?next=/）。"""
    r = client_local.get("/")
    assert r.status_code == 302
    assert "/login" in r.headers["location"]
