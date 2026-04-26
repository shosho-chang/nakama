"""Tests for `shared.firecrawl_serp.fetch_top_n_serp` (Slice F)."""

from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock, patch

import pytest

from shared.firecrawl_serp import (
    _MAX_PAGE_CHARS,
    FirecrawlSerpError,
    fetch_top_n_serp,
)


def _fake_search_result(n: int) -> list[dict[str, Any]]:
    return [
        {
            "url": f"https://example.com/p{i}",
            "title": f"第 {i} 名",
            "description": f"描述 {i}",
        }
        for i in range(n)
    ]


def _fake_doc(markdown: str) -> MagicMock:
    """Mimic firecrawl-py v2 Document — has `markdown` attribute."""
    doc = MagicMock()
    doc.markdown = markdown
    return doc


@pytest.fixture(autouse=True)
def _set_firecrawl_key(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("FIRECRAWL_API_KEY", "test-key")


def test_fetch_top_n_happy_path(monkeypatch: pytest.MonkeyPatch) -> None:
    """Search OK + every scrape OK → returns N dicts with all fields."""
    monkeypatch.setattr(
        "shared.firecrawl_serp.firecrawl_search",
        lambda *a, **kw: _fake_search_result(3),
    )

    fake_app = MagicMock()
    fake_app.scrape.side_effect = [
        _fake_doc(f"# 文章 {i}\n\n正文段落 ABC".encode().decode()) for i in range(3)
    ]

    with patch("firecrawl.FirecrawlApp", return_value=fake_app):
        results = fetch_top_n_serp("褪黑激素 睡眠", n=3)

    assert len(results) == 3
    for i, r in enumerate(results):
        assert r["url"] == f"https://example.com/p{i}"
        assert r["title"] == f"第 {i} 名"
        assert r["description"] == f"描述 {i}"
        assert "正文段落" in r["content_markdown"]


def test_fetch_top_n_country_lang_forwarded_to_search(monkeypatch: pytest.MonkeyPatch) -> None:
    """`lang` kw must reach `firecrawl_search`; `country` is logged but not yet
    a SDK arg (4.22 doesn't accept it). Regression guard for future SDK upgrades
    that wire country properly."""
    captured: dict[str, Any] = {}

    def _capture(query: str, *, num_results: int, lang: str) -> list[dict[str, Any]]:
        captured["query"] = query
        captured["num_results"] = num_results
        captured["lang"] = lang
        return _fake_search_result(1)

    monkeypatch.setattr("shared.firecrawl_serp.firecrawl_search", _capture)
    fake_app = MagicMock()
    fake_app.scrape.return_value = _fake_doc("body")
    with patch("firecrawl.FirecrawlApp", return_value=fake_app):
        fetch_top_n_serp("kw", n=2, country="us", lang="en")

    assert captured == {"query": "kw", "num_results": 2, "lang": "en"}


def test_fetch_top_n_search_failure_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    """search 階段失敗（quota / auth / network）→ FirecrawlSerpError。"""
    from shared.firecrawl_search import FirecrawlSearchError

    def _boom(*_a: Any, **_kw: Any) -> Any:
        raise FirecrawlSearchError("quota exceeded")

    monkeypatch.setattr("shared.firecrawl_serp.firecrawl_search", _boom)

    with pytest.raises(FirecrawlSerpError, match="search stage failed"):
        fetch_top_n_serp("kw")


def test_fetch_top_n_empty_search_returns_empty_list(monkeypatch: pytest.MonkeyPatch) -> None:
    """search 回 0 筆 → 不打 scrape，return []。"""
    monkeypatch.setattr("shared.firecrawl_serp.firecrawl_search", lambda *a, **kw: [])

    fake_app = MagicMock()
    with patch("firecrawl.FirecrawlApp", return_value=fake_app):
        results = fetch_top_n_serp("nothing-here")

    assert results == []
    fake_app.scrape.assert_not_called()


def test_fetch_top_n_partial_scrape_failure_continues(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """個別 scrape 失敗（429 / timeout / parse error）跳過該筆，回傳 < n 筆。"""
    monkeypatch.setattr(
        "shared.firecrawl_serp.firecrawl_search",
        lambda *a, **kw: _fake_search_result(3),
    )

    fake_app = MagicMock()
    # 第 1 筆 OK，第 2 筆 raise，第 3 筆 OK
    fake_app.scrape.side_effect = [
        _fake_doc("頁 1 內文"),
        Exception("429 too many requests"),
        _fake_doc("頁 3 內文"),
    ]

    with patch("firecrawl.FirecrawlApp", return_value=fake_app):
        results = fetch_top_n_serp("kw", n=3)

    assert len(results) == 2
    assert results[0]["url"] == "https://example.com/p0"
    assert results[1]["url"] == "https://example.com/p2"


def test_fetch_top_n_truncates_long_markdown(monkeypatch: pytest.MonkeyPatch) -> None:
    """每篇 markdown 超過 _MAX_PAGE_CHARS 就截斷 + 加 '已截斷' 標記。"""
    long_md = "甲" * (_MAX_PAGE_CHARS + 500)
    monkeypatch.setattr(
        "shared.firecrawl_serp.firecrawl_search",
        lambda *a, **kw: _fake_search_result(1),
    )

    fake_app = MagicMock()
    fake_app.scrape.return_value = _fake_doc(long_md)

    with patch("firecrawl.FirecrawlApp", return_value=fake_app):
        results = fetch_top_n_serp("kw", n=1)

    assert len(results) == 1
    assert "已截斷" in results[0]["content_markdown"]
    assert len(results[0]["content_markdown"]) <= _MAX_PAGE_CHARS + 20  # margin for marker


def test_fetch_top_n_no_api_key_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    """缺 FIRECRAWL_API_KEY → search stage raise FirecrawlSearchError →
    我們 wrap 成 FirecrawlSerpError。"""
    monkeypatch.delenv("FIRECRAWL_API_KEY", raising=False)
    with pytest.raises(FirecrawlSerpError):
        fetch_top_n_serp("kw")


def test_fetch_top_n_strict_n_limit(monkeypatch: pytest.MonkeyPatch) -> None:
    """`n=2` 即使 search 回 5 筆，scrape 只跑前 2 筆。"""
    monkeypatch.setattr(
        "shared.firecrawl_serp.firecrawl_search",
        lambda *a, **kw: _fake_search_result(5),
    )

    fake_app = MagicMock()
    fake_app.scrape.return_value = _fake_doc("body")
    with patch("firecrawl.FirecrawlApp", return_value=fake_app):
        results = fetch_top_n_serp("kw", n=2)

    assert len(results) == 2
    assert fake_app.scrape.call_count == 2


def test_fetch_top_n_skips_blank_url(monkeypatch: pytest.MonkeyPatch) -> None:
    """search 回有空 URL → 跳過該筆不 scrape。"""
    monkeypatch.setattr(
        "shared.firecrawl_serp.firecrawl_search",
        lambda *a, **kw: [
            {"url": "", "title": "weird", "description": ""},
            {"url": "https://example.com/p", "title": "ok", "description": ""},
        ],
    )

    fake_app = MagicMock()
    fake_app.scrape.return_value = _fake_doc("body")

    with patch("firecrawl.FirecrawlApp", return_value=fake_app):
        results = fetch_top_n_serp("kw", n=2)

    assert len(results) == 1
    assert results[0]["url"] == "https://example.com/p"


def test_fetch_top_n_empty_markdown_skipped(monkeypatch: pytest.MonkeyPatch) -> None:
    """scrape 成功但 markdown 是空字串 → 跳過該筆，不寫入結果。"""
    monkeypatch.setattr(
        "shared.firecrawl_serp.firecrawl_search",
        lambda *a, **kw: _fake_search_result(2),
    )

    fake_app = MagicMock()
    fake_app.scrape.side_effect = [_fake_doc(""), _fake_doc("real body")]

    with patch("firecrawl.FirecrawlApp", return_value=fake_app):
        results = fetch_top_n_serp("kw", n=2)

    assert len(results) == 1
    assert results[0]["url"] == "https://example.com/p1"
