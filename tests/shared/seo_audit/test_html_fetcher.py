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


# ---------------------------------------------------------------------------
# fetch_html_via_firecrawl — F4/F5-C 2026-04-27
# ---------------------------------------------------------------------------


class _FakeFirecrawlDoc:
    """Mimic firecrawl SDK Document: rawHtml + metadata.statusCode."""

    def __init__(self, raw_html: str, status_code: int = 200) -> None:
        self.rawHtml = raw_html  # noqa: N815 (mirroring firecrawl SDK API name)
        self.metadata = {"statusCode": status_code}


class _FakeFirecrawlApp:
    def __init__(
        self,
        doc: _FakeFirecrawlDoc | None = None,
        raise_exc: Exception | None = None,
    ) -> None:
        self._doc = doc
        self._raise_exc = raise_exc
        self.scrape_calls: list[dict] = []

    def scrape(self, url: str, **kwargs):
        self.scrape_calls.append({"url": url, **kwargs})
        if self._raise_exc:
            raise self._raise_exc
        return self._doc


def test_fetch_via_firecrawl_returns_soup_with_head():
    from shared.seo_audit.html_fetcher import fetch_html_via_firecrawl

    html = (
        "<html><head><title>T</title>"
        '<meta name="description" content="d">'
        '<meta name="viewport" content="width=device-width">'
        "</head><body><h1>H1</h1></body></html>"
    )
    app = _FakeFirecrawlApp(_FakeFirecrawlDoc(html, status_code=200))
    result = fetch_html_via_firecrawl("https://shosho.tw/x", firecrawl_app=app)

    assert result.fetch_check.status == "pass"
    assert result.soup is not None
    # head 必須完整：title / meta description / viewport 都看得到
    assert result.soup.title.string == "T"
    assert result.soup.find("meta", attrs={"name": "description"})["content"] == "d"
    assert result.soup.find("meta", attrs={"name": "viewport"})["content"] == "width=device-width"
    # firecrawl 用 rawHtml 不是 html
    assert app.scrape_calls[0]["formats"] == ["rawHtml"]
    assert app.scrape_calls[0]["only_main_content"] is False
    assert result.fetch_check.details["fetcher"] == "firecrawl"


def test_fetch_via_firecrawl_4xx_returns_fail():
    from shared.seo_audit.html_fetcher import fetch_html_via_firecrawl

    app = _FakeFirecrawlApp(_FakeFirecrawlDoc("", status_code=404))
    result = fetch_html_via_firecrawl("https://shosho.tw/missing", firecrawl_app=app)
    assert result.fetch_check.status == "fail"
    assert result.soup is None
    assert "404" in result.fetch_check.actual


def test_fetch_via_firecrawl_exception_returns_fail():
    from shared.seo_audit.html_fetcher import fetch_html_via_firecrawl

    app = _FakeFirecrawlApp(raise_exc=RuntimeError("network down"))
    result = fetch_html_via_firecrawl("https://shosho.tw/x", firecrawl_app=app)
    assert result.fetch_check.status == "fail"
    assert result.soup is None
    assert "network down" in result.fetch_check.actual
