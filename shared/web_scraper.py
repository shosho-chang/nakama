"""網頁內容擷取模組：三層回退策略（Trafilatura → Readability → Firecrawl）。

使用方式：
    from shared.web_scraper import scrape_url

    markdown_text = scrape_url("https://example.com/article")

三層策略：
1. Trafilatura  — 本地、免費、CJK 友好，適合新聞/部落格/學術頁面
2. readability-lxml — 本地備選，去噪能力強，適合複雜版面
3. Firecrawl API — 需 FIRECRAWL_API_KEY，處理 JS 渲染、SPA、付費牆
"""

import os
from enum import Enum

import httpx

from shared.log import get_logger

logger = get_logger("nakama.shared.web_scraper")

_MIN_CONTENT_LENGTH = 200
_HTTP_TIMEOUT = 30


class ScraperMode(str, Enum):
    AUTO = "auto"
    TRAFILATURA = "trafilatura"
    READABILITY = "readability"
    FIRECRAWL = "firecrawl"


def scrape_url(url: str, *, mode: ScraperMode | str = ScraperMode.AUTO) -> str:
    """擷取網頁內容，回傳 Markdown 格式純文字。

    Args:
        url:  目標網頁 URL
        mode: 擷取模式（auto / trafilatura / readability / firecrawl）

    Returns:
        Markdown 格式的頁面主要內容

    Raises:
        RuntimeError: 所有方式均失敗時
    """
    mode = ScraperMode(mode)

    if mode == ScraperMode.TRAFILATURA:
        result = _scrape_trafilatura(url)
        if not result:
            raise RuntimeError(f"Trafilatura 無法擷取頁面：{url}")
        return result

    if mode == ScraperMode.READABILITY:
        result = _scrape_readability(url)
        if not result:
            raise RuntimeError(f"Readability 無法擷取頁面：{url}")
        return result

    if mode == ScraperMode.FIRECRAWL:
        return _scrape_firecrawl(url)

    # AUTO：三層回退
    return _scrape_auto(url)


def _fetch_html(url: str) -> str | None:
    """用 httpx 抓取 HTML，失敗回傳 None。"""
    try:
        headers = {
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/120.0.0.0 Safari/537.36"
            )
        }
        resp = httpx.get(url, headers=headers, timeout=_HTTP_TIMEOUT, follow_redirects=True)
        resp.raise_for_status()
        return resp.text
    except Exception as e:
        logger.debug(f"httpx 抓取失敗：{e}")
        return None


def _scrape_trafilatura(url: str, html: str | None = None) -> str | None:
    """用 Trafilatura 提取主要內容。

    Args:
        url:  頁面 URL（用於 metadata 提取）
        html: 預先抓取的 HTML（None 時由 trafilatura 自行抓取）

    Returns:
        Markdown 格式文字，或 None（提取失敗/內容過短）
    """
    try:
        import trafilatura
    except ImportError:
        logger.warning("trafilatura 未安裝")
        return None

    try:
        if html is None:
            downloaded = trafilatura.fetch_url(url)
        else:
            downloaded = html

        if not downloaded:
            return None

        result = trafilatura.extract(
            downloaded,
            output_format="markdown",
            include_comments=False,
            include_tables=True,
            favor_recall=True,
        )
        if result and len(result.strip()) >= _MIN_CONTENT_LENGTH:
            logger.debug(f"Trafilatura 成功：{len(result)} 字元")
            return result.strip()
        return None
    except Exception as e:
        logger.debug(f"Trafilatura 提取失敗：{e}")
        return None


def _scrape_readability(url: str, html: str | None = None) -> str | None:
    """用 readability-lxml 提取主要內容並轉為 Markdown。

    Args:
        url:  頁面 URL（用於相對連結解析）
        html: 預先抓取的 HTML（None 時重新抓取）

    Returns:
        Markdown 格式文字，或 None（提取失敗/內容過短）
    """
    try:
        from readability import Document
    except ImportError:
        logger.warning("readability-lxml 未安裝")
        return None

    try:
        if html is None:
            html = _fetch_html(url)
        if not html:
            return None

        doc = Document(html, url=url)
        content_html = doc.summary()

        # HTML → 純文字（保留基本段落結構）
        text = _html_to_text(content_html)
        if text and len(text.strip()) >= _MIN_CONTENT_LENGTH:
            title = doc.title()
            result = f"# {title}\n\n{text.strip()}" if title else text.strip()
            logger.debug(f"Readability 成功：{len(result)} 字元")
            return result
        return None
    except Exception as e:
        logger.debug(f"Readability 提取失敗：{e}")
        return None


def _html_to_text(html: str) -> str:
    """將 HTML 轉為保留段落結構的純文字。"""
    try:
        from lxml import etree

        parser = etree.HTMLParser()
        tree = etree.fromstring(html.encode(), parser)
        if tree is None:
            return html

        _BLOCK_TAGS = {"h1", "h2", "h3", "h4", "p", "li", "br"}
        lines = []
        for elem in tree.iter():
            tag = elem.tag if isinstance(elem.tag, str) else ""
            text = (elem.text or "").strip()
            tail = (elem.tail or "").strip()

            if tag in ("h1", "h2", "h3", "h4"):
                level = int(tag[1])
                if text:
                    lines.append(f"{'#' * level} {text}")
            elif tag == "p":
                if text:
                    lines.append(text)
            elif tag == "li":
                if text:
                    lines.append(f"- {text}")
            elif tag == "br":
                lines.append("")
            elif text and tag not in _BLOCK_TAGS:
                # inline 標籤（a, strong, em, span 等）的文字直接追加
                lines.append(text)

            if tail and tag not in _BLOCK_TAGS:
                lines.append(tail)

        return "\n\n".join(line for line in lines if line)
    except Exception:
        # 極端 fallback：直接去除所有 HTML 標籤
        import re

        return re.sub(r"<[^>]+>", " ", html).strip()


def _scrape_firecrawl(url: str) -> str:
    """用 Firecrawl API 擷取頁面（JS 渲染、廣告去噪）。

    需要 FIRECRAWL_API_KEY 環境變數。

    Raises:
        RuntimeError: API key 未設定或 API 呼叫失敗
    """
    api_key = os.environ.get("FIRECRAWL_API_KEY")
    if not api_key:
        raise RuntimeError("FIRECRAWL_API_KEY 未設定，無法使用 Firecrawl")

    try:
        from firecrawl import Firecrawl
    except ImportError as e:
        raise RuntimeError("firecrawl-py 未安裝") from e

    logger.info(f"Firecrawl 擷取：{url}")
    try:
        app = Firecrawl(api_key=api_key)
        # only_main_content=True 抽文章主體，不含 navigation / ad / share chrome
        # （Lancet 等 JS 渲染站不加這個會拉整頁 chrome 給 translator 翻譯廢內容）
        result = app.scrape(url, formats=["markdown"], only_main_content=True)
        md = result.markdown or ""
        if not md:
            raise RuntimeError("Firecrawl 回傳空內容")
        logger.debug(f"Firecrawl 成功：{len(md)} 字元")
        return md
    except RuntimeError:
        raise
    except Exception as e:
        raise RuntimeError(f"Firecrawl API 呼叫失敗：{e}") from e


def fetch_html_via_firecrawl(url: str) -> str:
    """用 Firecrawl 抓取頁面的原始 HTML（供 meta-tag / 結構化抽取使用）。

    與 ``_scrape_firecrawl`` 不同：
    - ``_scrape_firecrawl`` 回傳已經抽過主體的 markdown（適合給 translator）
    - ``fetch_html_via_firecrawl`` 回傳完整 raw HTML（meta tag 仍在 ``<head>``）

    用途：當 plain httpx 被 publisher（如 Lancet / NEJM）的 bot 偵測 / cloudflare
    擋下，無法讀到 ``<meta name="citation_doi">`` 時，改用 Firecrawl 的 anti-bot
    基礎建設取得相同頁面的 HTML，再跑相同的 regex 抽 DOI。

    需要 FIRECRAWL_API_KEY 環境變數。

    Raises:
        RuntimeError: API key 未設定、firecrawl-py 未安裝、或 API 呼叫失敗
    """
    api_key = os.environ.get("FIRECRAWL_API_KEY")
    if not api_key:
        raise RuntimeError("FIRECRAWL_API_KEY 未設定，無法使用 Firecrawl")

    try:
        from firecrawl import Firecrawl
    except ImportError as e:
        raise RuntimeError("firecrawl-py 未安裝") from e

    logger.info(f"Firecrawl 抓 raw HTML：{url}")
    try:
        app = Firecrawl(api_key=api_key)
        # rawHtml: 不抽主體、不去 chrome — 我們要 <head> 裡的 meta tag。
        # only_main_content 預設 False（Firecrawl 端行為），這裡明確不傳。
        result = app.scrape(url, formats=["rawHtml"])
        html = result.raw_html or result.html or ""
        if not html:
            raise RuntimeError("Firecrawl 回傳空 HTML")
        logger.debug(f"Firecrawl raw HTML 成功：{len(html)} 字元")
        return html
    except RuntimeError:
        raise
    except Exception as e:
        raise RuntimeError(f"Firecrawl API 呼叫失敗：{e}") from e


def _scrape_auto(url: str) -> str:
    """三層回退：Trafilatura → Readability → Firecrawl。"""
    # 先抓 HTML，兩個本地方式共用，避免重複請求
    html = _fetch_html(url)

    # 層 1：Trafilatura
    result = _scrape_trafilatura(url, html=html)
    if result:
        logger.info(f"Scrape [{url}] → Trafilatura ({len(result)} chars)")
        return result

    # 層 2：Readability
    logger.debug("Trafilatura 不足，嘗試 Readability")
    result = _scrape_readability(url, html=html)
    if result:
        logger.info(f"Scrape [{url}] → Readability ({len(result)} chars)")
        return result

    # 層 3：Firecrawl（需 API key）
    api_key = os.environ.get("FIRECRAWL_API_KEY")
    if not api_key:
        raise RuntimeError(
            f"無法擷取頁面：Trafilatura 和 Readability 均失敗，且未設定 FIRECRAWL_API_KEY（{url}）"
        )

    logger.info(f"本地方式均失敗，改用 Firecrawl：{url}")
    return _scrape_firecrawl(url)
