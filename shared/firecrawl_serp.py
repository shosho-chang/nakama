"""Firecrawl top-N SERP fetcher：給 keyword，回傳 top-N 結果含 markdown 內容。

ADR-009 Phase 1.5 Slice F — competitor SERP enrichment 來源。

與 `shared.firecrawl_search` 的職責切分：
- `firecrawl_search`：給 query 拿 N 個 URL + title + description（候選清單，給 Robin /
  Zoro 等 agent 做 source discovery）；不 scrape 內文
- `firecrawl_serp`：給 keyword 拿 top-N URL + 內文 markdown（pre-LLM 摘要的原料）；
  scrape 每筆，內含截斷邏輯避免 token 爆炸

兩階段：
1. `firecrawl_search` 拿 top-N URL（reuse 既有 search wrapper）
2. 每 URL 個別 `app.scrape(formats=["markdown"], only_main_content=True)` 抓正文
3. 每篇 markdown 截到 _MAX_PAGE_CHARS 以下（pre-LLM token budget control）

失敗容忍（partial-list semantics）：
- search 失敗 → 整體 raise FirecrawlSerpError（caller `_enrich_with_serp` 抓住 fallback None）
- 個別 scrape 失敗（429 / timeout / 解析錯）→ 跳過該筆繼續，return list 可能 < N
"""

from __future__ import annotations

import os
from typing import Any

from shared.firecrawl_search import FirecrawlSearchError, firecrawl_search
from shared.log import get_logger

logger = get_logger("nakama.shared.firecrawl_serp")

# 每篇 markdown 截斷上限（pre-LLM）。Haiku 4.5 input pricing 偏便宜但仍要控；
# 3 篇 × 3000 chars ≈ 9000 chars ≈ ~3000 tokens，配 prompt 不到 4k input tokens。
_MAX_PAGE_CHARS = 3000

# 單篇 scrape timeout（秒）；firecrawl SDK 預設較長，這裡縮短避免單一卡頓拖累整批。
_SCRAPE_TIMEOUT_MS = 20000


class FirecrawlSerpError(RuntimeError):
    """Firecrawl SERP fetch failed at search stage (caller should fallback)."""


def fetch_top_n_serp(
    keyword: str,
    *,
    n: int = 3,
    country: str = "tw",
    lang: str = "zh-tw",
) -> list[dict[str, Any]]:
    """對 keyword 拉 top-N SERP 並 scrape 每筆 markdown 正文。

    Args:
        keyword: 搜尋字串
        n: 要拿幾筆（嚴格上限；scrape 失敗的不補）
        country: 國別 code（v2 SDK 透過 search location 傳遞 — 目前僅 log，
            firecrawl-py 4.22 search() signature 不顯式接 country；保留參數
            供未來 SDK 升級或自訂 location 字串使用）
        lang: 搜尋語言代碼（同上，保留欄位）

    Returns:
        list of dict，每筆含 keys：`url`, `title`, `description`, `content_markdown`
        （markdown 已截斷到 ≤ _MAX_PAGE_CHARS）。
        len(returns) ≤ n；search OK 但所有 scrape 全失敗時 return []。

    Raises:
        FirecrawlSerpError: search 階段失敗（quota 用完、auth 錯、network 中斷等）。
            caller 應 catch 並 fallback `competitor_serp_summary=None`。
    """
    logger.info(
        "firecrawl_serp_start keyword=%r n=%d country=%s lang=%s",
        keyword,
        n,
        country,
        lang,
    )

    # Stage 1: search for top-N URLs (reuse existing wrapper)
    try:
        candidates = firecrawl_search(keyword, num_results=n, lang=lang)
    except FirecrawlSearchError as e:
        raise FirecrawlSerpError(f"firecrawl search stage failed: {e}") from e

    if not candidates:
        logger.warning("firecrawl_serp_empty_search keyword=%r", keyword)
        return []

    # Stage 2: scrape each candidate URL for markdown body
    api_key = os.environ.get("FIRECRAWL_API_KEY")
    if not api_key:
        raise FirecrawlSerpError("FIRECRAWL_API_KEY 未設定，無法 scrape SERP 內文")

    try:
        from firecrawl import FirecrawlApp
    except ImportError as e:
        raise FirecrawlSerpError("firecrawl-py 未安裝") from e

    app = FirecrawlApp(api_key=api_key)
    results: list[dict[str, Any]] = []
    for cand in candidates[:n]:
        url = cand.get("url", "")
        if not url:
            continue
        markdown = _scrape_markdown(app, url)
        if markdown is None:
            # 個別失敗跳過，下一篇繼續
            continue
        results.append(
            {
                "url": url,
                "title": cand.get("title", ""),
                "description": cand.get("description", ""),
                "content_markdown": markdown,
            }
        )

    logger.info(
        "firecrawl_serp_done keyword=%r returned=%d/%d",
        keyword,
        len(results),
        len(candidates),
    )
    return results


def _scrape_markdown(app: Any, url: str) -> str | None:
    """Scrape one URL，回 truncated markdown；失敗 None（caller 跳過）。"""
    try:
        doc = app.scrape(
            url,
            formats=["markdown"],
            only_main_content=True,
            timeout=_SCRAPE_TIMEOUT_MS,
        )
    except Exception as e:
        logger.warning("firecrawl_scrape_failed url=%s err=%s", url, e)
        return None

    md = getattr(doc, "markdown", None) or ""
    if not md:
        # Document 物件可能 fallback 到 dict（v2 SDK 有時候 dump 成 dict）
        if isinstance(doc, dict):
            md = doc.get("markdown", "") or ""
    if not md:
        logger.debug("firecrawl_scrape_empty_markdown url=%s", url)
        return None

    if len(md) > _MAX_PAGE_CHARS:
        md = md[:_MAX_PAGE_CHARS] + "\n…（已截斷）"
    return md
