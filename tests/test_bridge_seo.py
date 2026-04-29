"""Tests for /bridge/seo — SEO 中控台 v1 slice 1 foundation.

Covers acceptance criteria from issue #227:
- GET /bridge/seo with auth → 200 + three section headings + ADR-008 deferred note
- GET /bridge/seo without auth → 302 to /login?next=/bridge/seo
"""

from __future__ import annotations

import importlib

import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def authed_client(monkeypatch, tmp_path):
    """Bridge router in dev-mode (WEB_PASSWORD/SECRET unset → check_auth True)."""
    monkeypatch.delenv("WEB_PASSWORD", raising=False)
    monkeypatch.delenv("WEB_SECRET", raising=False)
    monkeypatch.setenv("DISABLE_ROBIN", "1")
    monkeypatch.setenv("NAKAMA_DEFAULT_USER_ID", "shosho")
    monkeypatch.setenv("NAKAMA_DOC_INDEX_DB_PATH", str(tmp_path / "doc_index.db"))

    import thousand_sunny.app as app_module
    import thousand_sunny.auth as auth_module
    import thousand_sunny.routers.bridge as bridge_module

    importlib.reload(auth_module)
    importlib.reload(bridge_module)
    importlib.reload(app_module)
    return TestClient(app_module.app)


@pytest.fixture
def unauthed_client(monkeypatch, tmp_path):
    """Bridge router with WEB_PASSWORD set → check_auth requires the cookie."""
    monkeypatch.setenv("WEB_PASSWORD", "test-password")
    monkeypatch.setenv("WEB_SECRET", "test-secret")
    monkeypatch.setenv("DISABLE_ROBIN", "1")
    monkeypatch.setenv("NAKAMA_DEFAULT_USER_ID", "shosho")
    monkeypatch.setenv("NAKAMA_DOC_INDEX_DB_PATH", str(tmp_path / "doc_index.db"))

    import thousand_sunny.app as app_module
    import thousand_sunny.auth as auth_module
    import thousand_sunny.routers.bridge as bridge_module

    importlib.reload(auth_module)
    importlib.reload(bridge_module)
    importlib.reload(app_module)
    return TestClient(app_module.app)


def test_seo_page_renders_with_auth(authed_client):
    r = authed_client.get("/bridge/seo")
    assert r.status_code == 200
    assert "text/html" in r.headers["content-type"]
    body = r.text
    # Page identity
    assert "Bridge · SEO" in body
    assert "SEO 中控台" in body
    # Active chassis-nav marker on this page
    assert 'class="active" aria-current="page">SEO' in body


def test_seo_page_has_three_section_headings(authed_client):
    r = authed_client.get("/bridge/seo")
    assert r.status_code == 200
    body = r.text
    # Section 1: 文章列表
    assert "文章列表" in body
    assert "§ 1 · ARTICLES" in body
    # Section 2: 攻擊關鍵字
    assert "攻擊關鍵字" in body
    assert "§ 2 · TARGET KEYWORDS" in body
    # Section 3: 排名變化
    assert "排名變化" in body
    assert "§ 3 · RANK CHANGE" in body


def test_seo_page_section_3_states_phase_2a_min_dependency(authed_client):
    r = authed_client.get("/bridge/seo")
    assert r.status_code == 200
    body = r.text
    # The ADR-008 phase 2a-min deferred note has to appear verbatim (Acceptance criterion)
    assert "ADR-008 Phase 2a-min 上線後啟用" in body


def test_seo_page_unauth_redirects_to_login(unauthed_client):
    r = unauthed_client.get("/bridge/seo", follow_redirects=False)
    assert r.status_code == 302
    assert r.headers["location"] == "/login?next=/bridge/seo"


def test_seo_page_chassis_nav_link_visible_on_other_bridge_pages(authed_client):
    """Smoke: every bridge page should now contain a SEO chassis-nav link."""
    for path in [
        "/bridge",
        "/bridge/drafts",
        "/bridge/memory",
        "/bridge/cost",
        "/bridge/franky",
        "/bridge/health",
        "/bridge/docs",
        "/bridge/logs",
    ]:
        r = authed_client.get(path)
        assert r.status_code == 200, f"{path} did not render"
        assert '<a href="/bridge/seo">SEO <span class="zh">優化</span></a>' in r.text, (
            f"{path} missing SEO chassis-nav link"
        )
