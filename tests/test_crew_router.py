"""Smoke tests for the public /crew system-overview page.

Mirrors the public-surface precedent (/healthz, /progress, /architecture):
no auth, served even in VPS mode (DISABLE_ROBIN=1), and — unlike the
internal /architecture doc — indexable (no robots noindex meta).
"""

import importlib

import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def client_vps(monkeypatch):
    """VPS 模式：DISABLE_ROBIN=1。/crew 是公開頁，必須仍可訪問。"""
    monkeypatch.setenv("DISABLE_ROBIN", "1")
    monkeypatch.setenv("WEB_PASSWORD", "testpass")
    monkeypatch.setenv("WEB_SECRET", "testsecret")
    import thousand_sunny.app as app_module

    importlib.reload(app_module)
    return TestClient(app_module.app, follow_redirects=False)


def test_crew_page_available_without_auth(client_vps):
    r = client_vps.get("/crew")
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("text/html")


def test_crew_page_has_core_sections(client_vps):
    body = client_vps.get("/crew").text
    markers = (
        "八個 AI 船員",
        "系統架構",
        "內容流程七層",
        "一日工作節奏",
        "操作原則",
        "Runtime Policy",
    )
    for marker in markers:
        assert marker in body, f"missing section marker: {marker}"


def test_crew_page_is_indexable(client_vps):
    """對比 /architecture 的 noindex：/crew 是 showcase，不可有 noindex。"""
    body = client_vps.get("/crew").text
    assert "noindex" not in body
    assert 'property="og:title"' in body  # OG tags for social unfurl


def test_crew_asset_version_substituted(client_vps):
    """boot 時 __ASSET_VERSION__ 必須被換成 sha1 slug（CF cache-bust）。"""
    body = client_vps.get("/crew").text
    assert "__ASSET_VERSION__" not in body
    assert "/static/crew/crew.css?v=" in body
