"""千陽 /scrape-translate 路由測試。"""

import importlib
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def client(monkeypatch, tmp_path):
    monkeypatch.delenv("DISABLE_ROBIN", raising=False)
    monkeypatch.setenv("WEB_PASSWORD", "testpass")
    monkeypatch.setenv("WEB_SECRET", "testsecret")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")

    # 把 vault inbox 指向暫存目錄
    inbox = tmp_path / "Inbox" / "kb"
    inbox.mkdir(parents=True)
    monkeypatch.setenv("VAULT_PATH", str(tmp_path))

    import thousand_sunny.app as app_module
    import thousand_sunny.auth as auth_module
    import thousand_sunny.routers.auth as auth_router_module
    import thousand_sunny.routers.robin as robin_module

    importlib.reload(auth_module)
    importlib.reload(auth_router_module)
    importlib.reload(robin_module)
    importlib.reload(app_module)
    return TestClient(app_module.app, follow_redirects=False)


def _auth_cookie(client):
    """取得有效的 auth cookie。"""
    resp = client.post("/login", data={"password": "testpass"}, follow_redirects=False)
    return resp.cookies.get("nakama_auth", "")


# ── /scrape-translate ──


def test_scrape_translate_requires_auth(client):
    resp = client.post("/scrape-translate", data={"url": "https://example.com"})
    assert resp.status_code == 302
    assert "/login" in resp.headers["location"]


def test_scrape_translate_success(client, tmp_path):
    auth = _auth_cookie(client)
    bilingual_content = "# Title\n\nOriginal paragraph.\n\n> 原始段落。"

    with (
        patch("shared.web_scraper.scrape_url", return_value="# Title\n\nOriginal paragraph."),
        patch("shared.translator.translate_document", return_value=bilingual_content),
    ):
        resp = client.post(
            "/scrape-translate",
            data={
                "url": "https://example.com/article",
                "source_type": "article",
                "content_nature": "popular_science",
            },
            cookies={"nakama_auth": auth},
            follow_redirects=False,
        )

    assert resp.status_code == 303
    assert "/read?file=" in resp.headers["location"]


def test_scrape_translate_creates_md_file(client, tmp_path):
    auth = _auth_cookie(client)
    bilingual = "# Test\n\nBody text.\n\n> 本文。"
    inbox = tmp_path / "Inbox" / "kb"
    inbox.mkdir(parents=True, exist_ok=True)

    with (
        patch("shared.web_scraper.scrape_url", return_value="# Test\n\nBody text."),
        patch("shared.translator.translate_document", return_value=bilingual),
        patch("thousand_sunny.routers.robin._get_inbox", return_value=inbox),
    ):
        client.post(
            "/scrape-translate",
            data={"url": "https://nature.com/articles/s123", "source_type": "paper"},
            cookies={"nakama_auth": auth},
            follow_redirects=False,
        )

    md_files = list(inbox.glob("*.md"))
    assert len(md_files) == 1
    content = md_files[0].read_text(encoding="utf-8")
    assert "bilingual: true" in content
    assert "# Test" in content


def test_scrape_translate_fallback_on_translate_error(client, tmp_path):
    """翻譯失敗時應保留原文並成功儲存。"""
    auth = _auth_cookie(client)
    raw = "# Fallback\n\nOriginal only."
    inbox = tmp_path / "Inbox" / "kb"
    inbox.mkdir(parents=True, exist_ok=True)

    with (
        patch("shared.web_scraper.scrape_url", return_value=raw),
        patch("shared.translator.translate_document", side_effect=Exception("API error")),
        patch("thousand_sunny.routers.robin._get_inbox", return_value=inbox),
    ):
        resp = client.post(
            "/scrape-translate",
            data={"url": "https://example.com/fallback"},
            cookies={"nakama_auth": auth},
            follow_redirects=False,
        )

    assert resp.status_code == 303
    md_files = list(inbox.glob("*.md"))
    assert len(md_files) == 1
    assert "Fallback" in md_files[0].read_text(encoding="utf-8")


def test_scrape_translate_scrape_error_returns_422(client):
    auth = _auth_cookie(client)

    with patch("shared.web_scraper.scrape_url", side_effect=RuntimeError("connection refused")):
        resp = client.post(
            "/scrape-translate",
            data={"url": "https://unreachable.example.com"},
            cookies={"nakama_auth": auth},
            follow_redirects=False,
        )

    assert resp.status_code == 422
