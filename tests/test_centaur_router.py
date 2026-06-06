"""Smoke tests for the public /centaur methodology page.

Mirrors the /crew public-surface precedent: no auth, served even in VPS mode
(DISABLE_ROBIN=1), and indexable (no robots noindex meta). Unlike /crew there
is no __ASSET_VERSION__ substitution — the doc is fully self-contained.
"""

import importlib

import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def client_vps(monkeypatch):
    """VPS 模式：DISABLE_ROBIN=1。/centaur 是公開頁，必須仍可訪問。"""
    monkeypatch.setenv("DISABLE_ROBIN", "1")
    monkeypatch.setenv("WEB_PASSWORD", "testpass")
    monkeypatch.setenv("WEB_SECRET", "testsecret")
    import thousand_sunny.app as app_module

    importlib.reload(app_module)
    return TestClient(app_module.app, follow_redirects=False)


def test_centaur_page_available_without_auth(client_vps):
    r = client_vps.get("/centaur")
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("text/html")


def test_centaur_page_has_core_sections(client_vps):
    body = client_vps.get("/centaur").text
    markers = (
        "The Compounding Vault",
        "核心原則：摩擦篩選",
        "系統架構",
        "人機分工界線",
        "完整工作流",
        "給 Claude Code 的實作規格",
    )
    for marker in markers:
        assert marker in body, f"missing section marker: {marker}"


def test_centaur_page_is_indexable(client_vps):
    """對比 /architecture 的 noindex：/centaur 是 shareable showcase，不可有 noindex。"""
    body = client_vps.get("/centaur").text
    assert "noindex" not in body


def test_centaur_page_is_self_contained(client_vps):
    """自包含：無 __ASSET_VERSION__ placeholder，也不連任何第一方 /static 資產。"""
    body = client_vps.get("/centaur").text
    assert "__ASSET_VERSION__" not in body
    assert "/static/" not in body
