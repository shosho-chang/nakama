"""shared/web_scraper.py 單元測試。

測試不需要網路或 API key 的純邏輯與 mock 路徑。
實際網頁擷取標記為 slow（需要網路）。
"""

from unittest.mock import MagicMock, patch

import pytest

from shared.web_scraper import (
    ScraperMode,
    _fetch_html,
    _html_to_text,
    _scrape_auto,
    _scrape_firecrawl,
    _scrape_readability,
    _scrape_trafilatura,
    scrape_url,
)

_SAMPLE_HTML = """
<html><body>
<h1>Sleep and Mitochondria</h1>
<p>Chronic sleep deprivation impairs mitochondrial function in neurons.</p>
<p>Studies show that delta wave sleep is essential for cellular repair processes.</p>
<p>This paragraph provides additional context about the research methodology used.</p>
</body></html>
"""

_SHORT_HTML = "<html><body><p>Hi</p></body></html>"


# ── _html_to_text ──


def test_html_to_text_basic():
    result = _html_to_text("<p>Hello world.</p><p>Second paragraph.</p>")
    assert "Hello world." in result
    assert "Second paragraph." in result


def test_html_to_text_headings():
    result = _html_to_text("<h1>Title</h1><p>Body text.</p>")
    assert "# Title" in result
    assert "Body text." in result


def test_html_to_text_list():
    result = _html_to_text("<ul><li>Item one</li><li>Item two</li></ul>")
    assert "- Item one" in result
    assert "- Item two" in result


def test_html_to_text_empty():
    result = _html_to_text("")
    assert result == "" or result is not None


# ── _fetch_html ──


def test_fetch_html_success():
    mock_response = MagicMock()
    mock_response.text = "<html><body>content</body></html>"
    mock_response.raise_for_status = MagicMock()
    with patch("shared.web_scraper.httpx.get", return_value=mock_response):
        result = _fetch_html("https://example.com")
    assert result == "<html><body>content</body></html>"


def test_fetch_html_failure():
    with patch("shared.web_scraper.httpx.get", side_effect=Exception("connection error")):
        result = _fetch_html("https://example.com")
    assert result is None


# ── _scrape_trafilatura ──


def test_scrape_trafilatura_success():
    long_text = "A" * 300
    with patch("trafilatura.extract", return_value=long_text):
        result = _scrape_trafilatura("https://example.com", html=_SAMPLE_HTML)
    assert result == long_text


def test_scrape_trafilatura_short_result():
    with patch("trafilatura.extract", return_value="Too short"):
        result = _scrape_trafilatura("https://example.com", html=_SAMPLE_HTML)
    assert result is None


def test_scrape_trafilatura_none_result():
    with patch("trafilatura.extract", return_value=None):
        result = _scrape_trafilatura("https://example.com", html=_SAMPLE_HTML)
    assert result is None


def test_scrape_trafilatura_exception():
    with patch("trafilatura.extract", side_effect=Exception("parse error")):
        result = _scrape_trafilatura("https://example.com", html=_SAMPLE_HTML)
    assert result is None


# ── _scrape_readability ──


def test_scrape_readability_success():
    result = _scrape_readability("https://example.com", html=_SAMPLE_HTML)
    assert result is not None
    assert len(result) >= 200
    assert "Sleep" in result or "sleep" in result or "mitochondria" in result.lower()


def test_scrape_readability_short_html():
    result = _scrape_readability("https://example.com", html=_SHORT_HTML)
    assert result is None


def test_scrape_readability_no_html_fetches():
    mock_response = MagicMock()
    mock_response.text = _SAMPLE_HTML
    mock_response.raise_for_status = MagicMock()
    with patch("shared.web_scraper.httpx.get", return_value=mock_response):
        result = _scrape_readability("https://example.com", html=None)
    assert result is not None


# ── _scrape_firecrawl ──


def test_scrape_firecrawl_no_api_key():
    with patch.dict("os.environ", {}, clear=True):
        with pytest.raises(RuntimeError, match="FIRECRAWL_API_KEY"):
            _scrape_firecrawl("https://example.com")


def test_scrape_firecrawl_success():
    mock_response = MagicMock()
    mock_response.markdown = "# Article\n\nContent here " * 10
    from firecrawl import Firecrawl

    # spec=instance（不是 class）— Firecrawl 用 delegation，方法在 __init__ 才綁
    mock_app = MagicMock(spec=Firecrawl(api_key="dummy-spec"))
    mock_app.scrape.return_value = mock_response
    with (
        patch.dict("os.environ", {"FIRECRAWL_API_KEY": "test-key"}),
        patch("firecrawl.Firecrawl", return_value=mock_app),
    ):
        result = _scrape_firecrawl("https://example.com")
    assert "Article" in result


def test_scrape_firecrawl_empty_response():
    mock_response = MagicMock()
    mock_response.markdown = None
    from firecrawl import Firecrawl

    # spec=instance（不是 class）— Firecrawl 用 delegation，方法在 __init__ 才綁
    mock_app = MagicMock(spec=Firecrawl(api_key="dummy-spec"))
    mock_app.scrape.return_value = mock_response
    with (
        patch.dict("os.environ", {"FIRECRAWL_API_KEY": "test-key"}),
        patch("firecrawl.Firecrawl", return_value=mock_app),
    ):
        with pytest.raises(RuntimeError, match="空內容"):
            _scrape_firecrawl("https://example.com")


# ── _scrape_auto ──


def test_scrape_auto_trafilatura_wins():
    long_text = "B" * 300
    with (
        patch("shared.web_scraper._fetch_html", return_value=_SAMPLE_HTML),
        patch("trafilatura.extract", return_value=long_text),
    ):
        result = _scrape_auto("https://example.com")
    assert result == long_text


def test_scrape_auto_falls_back_to_readability():
    with (
        patch("shared.web_scraper._fetch_html", return_value=_SAMPLE_HTML),
        patch("trafilatura.extract", return_value=None),
    ):
        result = _scrape_auto("https://example.com")
    assert result is not None
    assert len(result) >= 200


def test_scrape_auto_falls_back_to_firecrawl():
    mock_response = MagicMock()
    mock_response.markdown = "# JS Page\n\n" + "content " * 50
    from firecrawl import Firecrawl

    # spec=instance（不是 class）— Firecrawl 用 delegation，方法在 __init__ 才綁
    mock_app = MagicMock(spec=Firecrawl(api_key="dummy-spec"))
    mock_app.scrape.return_value = mock_response
    with (
        patch("shared.web_scraper._fetch_html", return_value=_SHORT_HTML),
        patch("trafilatura.extract", return_value=None),
        patch.dict("os.environ", {"FIRECRAWL_API_KEY": "test-key"}),
        patch("firecrawl.Firecrawl", return_value=mock_app),
    ):
        result = _scrape_auto("https://example.com")
    assert "JS Page" in result


def test_scrape_auto_no_firecrawl_key_raises():
    with (
        patch("shared.web_scraper._fetch_html", return_value=_SHORT_HTML),
        patch("trafilatura.extract", return_value=None),
        patch.dict("os.environ", {}, clear=True),
    ):
        with pytest.raises(RuntimeError, match="FIRECRAWL_API_KEY"):
            _scrape_auto("https://example.com")


# ── scrape_url (public API) ──


def test_scrape_url_mode_auto():
    long_text = "C" * 300
    with (
        patch("shared.web_scraper._fetch_html", return_value=_SAMPLE_HTML),
        patch("trafilatura.extract", return_value=long_text),
    ):
        result = scrape_url("https://example.com")
    assert result == long_text


def test_scrape_url_mode_string():
    long_text = "D" * 300
    with (
        patch("shared.web_scraper._fetch_html", return_value=_SAMPLE_HTML),
        patch("trafilatura.extract", return_value=long_text),
    ):
        result = scrape_url("https://example.com", mode="auto")
    assert result == long_text


def test_scrape_url_mode_firecrawl():
    mock_response = MagicMock()
    mock_response.markdown = "# Test\n\n" + "word " * 50
    from firecrawl import Firecrawl

    # spec=instance（不是 class）— Firecrawl 用 delegation，方法在 __init__ 才綁
    mock_app = MagicMock(spec=Firecrawl(api_key="dummy-spec"))
    mock_app.scrape.return_value = mock_response
    with (
        patch.dict("os.environ", {"FIRECRAWL_API_KEY": "key"}),
        patch("firecrawl.Firecrawl", return_value=mock_app),
    ):
        result = scrape_url("https://example.com", mode=ScraperMode.FIRECRAWL)
    assert "Test" in result


def test_scrape_url_trafilatura_no_result_raises():
    with (
        patch("shared.web_scraper._fetch_html", return_value=_SHORT_HTML),
        patch("trafilatura.extract", return_value=None),
    ):
        with pytest.raises(RuntimeError, match="Trafilatura"):
            scrape_url("https://example.com", mode=ScraperMode.TRAFILATURA)
