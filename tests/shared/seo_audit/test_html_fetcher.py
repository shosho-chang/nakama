"""HTML fetcher tests — mock httpx 邊界。

覆蓋：
- 200 happy path：soup 解析 OK + fetch_check.status="pass"
- 5xx retry：第 1 次 503，第 2 次 200
- 5xx giveup：3 次 502 → fetch_check.status="fail"
- 4xx 不 retry：404 → 立刻回 fail
- 連線錯誤 retry / giveup
- response time 計算到 fetch_check.details
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import httpx
import pytest

from shared.seo_audit.html_fetcher import fetch_html


@pytest.fixture(autouse=True)
def _no_sleep(monkeypatch):
    monkeypatch.setattr("shared.seo_audit.html_fetcher.time.sleep", lambda _s: None)


def _resp(
    status_code: int,
    body: str = "",
    content_type: str = "text/html; charset=utf-8",
    final_url: str = "",
) -> MagicMock:
    r = MagicMock(spec=httpx.Response)
    r.status_code = status_code
    r.text = body
    r.headers = {"content-type": content_type}
    r.url = final_url or "https://example.com/post"
    return r


def test_fetch_200_returns_soup():
    html = "<html><head><title>Hi</title></head><body><h1>Hello</h1></body></html>"
    with patch("shared.seo_audit.html_fetcher.httpx.get") as m_get:
        m_get.return_value = _resp(200, html)
        result = fetch_html("https://example.com/post")

    assert result.soup is not None
    assert result.soup.title.string == "Hi"
    assert result.fetch_check.status == "pass"
    assert result.status_code == 200
    assert "html.parser" not in str(result.soup)  # smoke
    # response time 寫進 details
    assert "response_time_ms" in result.fetch_check.details


def test_fetch_5xx_retries_then_success():
    with patch("shared.seo_audit.html_fetcher.httpx.get") as m_get:
        m_get.side_effect = [_resp(503), _resp(200, "<html><body>ok</body></html>")]
        result = fetch_html("https://example.com")
    assert result.fetch_check.status == "pass"
    assert m_get.call_count == 2


def test_fetch_5xx_giveup():
    with patch("shared.seo_audit.html_fetcher.httpx.get") as m_get:
        m_get.return_value = _resp(502)
        result = fetch_html("https://example.com")
    assert result.fetch_check.status == "fail"
    assert "502" in result.fetch_check.actual
    assert m_get.call_count == 3
    assert result.soup is None


def test_fetch_4xx_no_retry():
    with patch("shared.seo_audit.html_fetcher.httpx.get") as m_get:
        m_get.return_value = _resp(404)
        result = fetch_html("https://example.com/missing")
    assert result.fetch_check.status == "fail"
    assert "404" in result.fetch_check.actual
    assert m_get.call_count == 1
    assert result.soup is None


def test_fetch_request_error_retries():
    with patch("shared.seo_audit.html_fetcher.httpx.get") as m_get:
        m_get.side_effect = [
            httpx.ConnectTimeout("timeout"),
            _resp(200, "<html></html>"),
        ]
        result = fetch_html("https://example.com")
    assert result.fetch_check.status == "pass"
    assert m_get.call_count == 2


def test_fetch_request_error_giveup():
    with patch("shared.seo_audit.html_fetcher.httpx.get") as m_get:
        m_get.side_effect = httpx.ConnectError("refused")
        result = fetch_html("https://example.com")
    assert result.fetch_check.status == "fail"
    assert "ConnectError" in result.fetch_check.actual
    assert result.status_code == 0
    assert m_get.call_count == 3


def test_fetch_records_content_type_and_final_url():
    with patch("shared.seo_audit.html_fetcher.httpx.get") as m_get:
        m_get.return_value = _resp(
            200,
            "<html></html>",
            content_type="text/html; charset=utf-8",
            final_url="https://example.com/post-redirected",
        )
        result = fetch_html("https://example.com/post")
    assert result.content_type.startswith("text/html")
    assert result.final_url == "https://example.com/post-redirected"
    assert result.fetch_check.details["content_type"] == "text/html; charset=utf-8"


def test_user_agent_sent():
    with patch("shared.seo_audit.html_fetcher.httpx.get") as m_get:
        m_get.return_value = _resp(200, "<html></html>")
        fetch_html("https://example.com")
    headers = m_get.call_args[1]["headers"]
    assert "User-Agent" in headers
    assert "NakamaBot" in headers["User-Agent"]
