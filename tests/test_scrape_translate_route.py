"""千陽 /scrape-translate 路由測試。

Slice 1 (issue #352) 後行為變更：
- redirect 由 ``/read?file=...`` 改為 ``/``（inbox view）
- 同步翻譯移除（翻譯按鈕在 Slice 4 reader header 才出現）
- scrape error 不再 raise 422；失敗檔以 ``fulltext_status: failed`` 寫入 inbox

更詳細的 BackgroundTask / placeholder / 短路 / 失敗 frontmatter 行為交由
``tests/integration/test_scrape_translate_endpoint.py`` 覆蓋（avoid duplication）。
本檔保留輕量 smoke：auth gate + 基本 redirect contract。
"""

import importlib
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

from agents.robin.url_dispatcher import URLDispatcher
from shared.schemas.ingest_result import IngestResult


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


def _ready(url: str = "https://example.com/article") -> IngestResult:
    return IngestResult(
        status="ready",
        fulltext_layer="readability",
        fulltext_source="Readability",
        markdown="# Title\n\n" + ("Body line.\n" * 80),
        title="Title",
        original_url=url,
    )


# ── /scrape-translate ──


def test_scrape_translate_requires_auth(client):
    resp = client.post("/scrape-translate", data={"url": "https://example.com"})
    assert resp.status_code == 302
    assert "/login" in resp.headers["location"]


def test_scrape_translate_success_redirects_to_inbox(client):
    """Slice 1: 成功 paste → 立刻 303 回 inbox view ``/``，不再等翻譯。"""
    auth = _auth_cookie(client)

    with patch("thousand_sunny.routers.robin.URLDispatcher", spec=URLDispatcher) as MockDispatcher:
        instance = MockDispatcher.return_value
        instance.dispatch.return_value = _ready("https://example.com/article")

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
    assert resp.headers["location"] == "/"


def test_scrape_translate_creates_md_file(client, tmp_path):
    """Slice 1: 後台跑完應寫入 ``Inbox/kb/{slug}.md`` 並含 fulltext_status。"""
    auth = _auth_cookie(client)
    inbox = tmp_path / "Inbox" / "kb"

    with patch("thousand_sunny.routers.robin.URLDispatcher", spec=URLDispatcher) as MockDispatcher:
        instance = MockDispatcher.return_value
        instance.dispatch.return_value = _ready("https://nature.com/articles/s123")

        client.post(
            "/scrape-translate",
            data={"url": "https://nature.com/articles/s123", "source_type": "paper"},
            cookies={"nakama_auth": auth},
            follow_redirects=False,
        )

    md_files = list(inbox.glob("*.md"))
    assert len(md_files) == 1
    content = md_files[0].read_text(encoding="utf-8")
    assert "fulltext_status: ready" in content
    assert "fulltext_layer: readability" in content
    assert "original_url:" in content
    assert "# Title" in content


def test_scrape_translate_dispatcher_error_writes_failed_file(client, tmp_path):
    """Slice 1: scraper 失敗不再 422；改寫入 status=failed inbox row。"""
    auth = _auth_cookie(client)
    inbox = tmp_path / "Inbox" / "kb"

    failed = IngestResult(
        status="failed",
        fulltext_layer="readability",
        fulltext_source="Readability",
        markdown="",
        title="unreachable.example.com",
        original_url="https://unreachable.example.com",
        error="RuntimeError: connection refused",
    )

    with patch("thousand_sunny.routers.robin.URLDispatcher", spec=URLDispatcher) as MockDispatcher:
        instance = MockDispatcher.return_value
        instance.dispatch.return_value = failed

        resp = client.post(
            "/scrape-translate",
            data={"url": "https://unreachable.example.com"},
            cookies={"nakama_auth": auth},
            follow_redirects=False,
        )

    assert resp.status_code == 303
    md_files = list(inbox.glob("*.md"))
    assert len(md_files) == 1
    content = md_files[0].read_text(encoding="utf-8")
    assert "fulltext_status: failed" in content
