"""發布審核頁的登入驗證（ADR-055 slice 4）。

回歸背景：本 router 原本把 cookie 參數宣告成 `auth`，但 FastAPI 的 `Cookie()`
以**參數名**當 cookie 名，而 `/login` 發的 cookie 叫 `nakama_auth`
（`thousand_sunny/auth.py::set_auth_cookie`）——等於整頁在讀一個沒人會設的
cookie，登入後照樣 401，審核頁在瀏覽器裡從來打不開（2026-08-11 修修實際撞到）。
Slice 4 沒有任何 route 測試，才會漏到上線後才被人工發現。

另外：頁面（非子資源）未登入要導 /login，不是丟 401 JSON——瀏覽器看到 JSON 是死路。
"""

from __future__ import annotations

import importlib

import pytest
from fastapi.testclient import TestClient

PASSWORD = "test-web-password"
SECRET = "test-web-secret"


@pytest.fixture
def client(monkeypatch, tmp_path):
    monkeypatch.setenv("WEB_PASSWORD", PASSWORD)
    monkeypatch.setenv("WEB_SECRET", SECRET)
    monkeypatch.delenv("NAKAMA_DEV_AUTH_BYPASS", raising=False)
    monkeypatch.setenv("DISABLE_ROBIN", "1")
    monkeypatch.setenv("VAULT_PATH", str(tmp_path))
    monkeypatch.setenv("DB_PATH", str(tmp_path / "state.db"))

    import thousand_sunny.app as app_module
    import thousand_sunny.auth as auth_module
    import thousand_sunny.routers.publish_review as pub_module

    importlib.reload(auth_module)
    importlib.reload(pub_module)
    importlib.reload(app_module)
    return TestClient(app_module.app, follow_redirects=False)


@pytest.fixture
def token(client):
    import thousand_sunny.auth as auth_module

    return auth_module.make_token(PASSWORD)


def test_page_without_cookie_redirects_to_login(client):
    r = client.get("/bridge/publish")
    assert r.status_code == 302
    assert r.headers["location"].startswith("/login")


def test_login_cookie_opens_page(client, token):
    """`/login` 發的 cookie 名（nakama_auth）必須就是本 router 認的那個。"""
    r = client.get("/bridge/publish", cookies={"nakama_auth": token})
    assert r.status_code == 200


def test_legacy_auth_cookie_name_is_not_accepted(client, token):
    """回歸鎖：叫 `auth` 的 cookie 沒有任何地方會發，不可以當成已登入。"""
    r = client.get("/bridge/publish", cookies={"auth": token})
    assert r.status_code == 302


def test_subresource_without_cookie_returns_401(client):
    """子資源（縮圖/影片/status/POST）維持 401 JSON——那些是 fetch 端點不是頁面。"""
    r = client.get("/bridge/publish/thumb/20260415%20ep/SL4")
    assert r.status_code == 401
