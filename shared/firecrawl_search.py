"""Firecrawl 搜尋模組：呼叫 /search endpoint 回傳候選清單。

與 web_scraper.py 平行，web_scraper 負責抓單頁內文，
本模組負責「給一個 query，回傳 N 個相關 URL + 摘要」。

使用方式：
    from shared.firecrawl_search import firecrawl_search

    results = firecrawl_search("褪黑激素 睡眠品質", num_results=10)
    for r in results:
        print(r["title"], r["url"])
"""

import os

from shared.log import get_logger

logger = get_logger("nakama.shared.firecrawl_search")


class FirecrawlSearchError(RuntimeError):
    """Firecrawl search 呼叫失敗。"""


def firecrawl_search(
    query: str,
    *,
    num_results: int = 10,
    lang: str = "zh-tw",
) -> list[dict]:
    """搜尋網頁，回傳候選清單。

    Args:
        query:       搜尋字串
        num_results: 最多回傳幾筆（上限 20，超過自動截斷）
        lang:        搜尋語言代碼（預設 zh-tw）

    Returns:
        List of dicts，每筆包含 title、url、description（均為 str，可能空字串）

    Raises:
        FirecrawlSearchError: API key 未設定或呼叫失敗
    """
    api_key = os.environ.get("FIRECRAWL_API_KEY")
    if not api_key:
        raise FirecrawlSearchError("FIRECRAWL_API_KEY 未設定，無法使用 Firecrawl search")

    try:
        from firecrawl import FirecrawlApp
    except ImportError as e:
        raise FirecrawlSearchError("firecrawl-py 未安裝") from e

    num_results = min(num_results, 20)

    logger.info(f"Firecrawl search: {query!r} (limit={num_results})")
    try:
        app = FirecrawlApp(api_key=api_key)
        response = app.search(query, limit=num_results, lang=lang)

        # v2 SDK: response 為 SearchData，web 為 List[SearchResultWeb | Document]
        raw_results = getattr(response, "web", None) or []

        results = []
        for item in raw_results:
            results.append(
                {
                    "title": str(getattr(item, "title", "") or ""),
                    "url": str(getattr(item, "url", "") or ""),
                    "description": str(getattr(item, "description", "") or ""),
                }
            )

        logger.info(f"Firecrawl search 回傳 {len(results)} 筆")
        return results

    except FirecrawlSearchError:
        raise
    except Exception as e:
        raise FirecrawlSearchError(f"Firecrawl search 失敗：{e}") from e
