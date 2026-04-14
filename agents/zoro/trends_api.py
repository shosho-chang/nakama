"""Google Trends — 取得相關搜尋詞與趨勢方向。"""

from __future__ import annotations

from shared.log import get_logger
from shared.retry import with_retry

logger = get_logger("nakama.zoro.trends")


def get_trends(topic: str) -> dict:
    """Fetch Google Trends data for a topic.

    Returns dict with keys: related_top, related_rising, trend_direction.
    Returns empty dict on failure.
    """
    try:
        from pytrends.request import TrendReq

        pytrends = TrendReq(hl="zh-TW", tz=480)

        def _fetch():
            pytrends.build_payload([topic], timeframe="today 3-m")

            # Related queries
            related = pytrends.related_queries()
            top_queries = []
            rising_queries = []

            if topic in related:
                top_df = related[topic].get("top")
                if top_df is not None and not top_df.empty:
                    top_queries = top_df.head(15).to_dict("records")

                rising_df = related[topic].get("rising")
                if rising_df is not None and not rising_df.empty:
                    rising_queries = rising_df.head(10).to_dict("records")

            # Interest over time (for trend direction)
            trend_direction = "stable"
            try:
                iot = pytrends.interest_over_time()
                if not iot.empty and topic in iot.columns:
                    values = iot[topic].tolist()
                    if len(values) >= 4:
                        first_quarter = sum(values[: len(values) // 4]) / (len(values) // 4)
                        last_quarter = sum(values[-len(values) // 4 :]) / (len(values) // 4)
                        if last_quarter > first_quarter * 1.2:
                            trend_direction = "rising"
                        elif last_quarter < first_quarter * 0.8:
                            trend_direction = "declining"
            except Exception:
                pass  # trend_direction stays "stable"

            return {
                "related_top": [
                    {"query": r["query"], "value": int(r.get("value", 0))} for r in top_queries
                ],
                "related_rising": [
                    {"query": r["query"], "value": str(r.get("value", ""))} for r in rising_queries
                ],
                "trend_direction": trend_direction,
            }

        return with_retry(_fetch, max_attempts=2, backoff_base=3.0)

    except Exception as e:
        logger.error(f"Google Trends error: {e}")
        return {}
