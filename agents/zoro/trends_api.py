"""Google Trends — 取得相關搜尋詞與趨勢方向 + scout discovery（trending_now）。"""

from __future__ import annotations

from shared.log import get_logger
from shared.retry import with_retry

logger = get_logger("nakama.zoro.trends")


# Health-related 種子詞（英文為主，對應 trending_now en-US）。
# 命中 keyword 或任一 trend_keyword → 視為 health 相關。
_HEALTH_SEEDS = [
    # Sleep
    "sleep",
    "insomnia",
    "melatonin",
    "circadian",
    "nap",
    "chronotype",
    # Diet / Nutrition
    "nutrition",
    "diet",
    "fasting",
    "glucose",
    "cgm",
    "keto",
    "protein",
    "carb",
    "fiber",
    "supplement",
    "vitamin",
    "metabolism",
    "obesity",
    "weight loss",
    "weight gain",
    "ozempic",
    "wegovy",
    "glp-1",
    # Exercise
    "exercise",
    "workout",
    "fitness",
    "cardio",
    "strength",
    "running",
    "hiit",
    "zone 2",
    "vo2",
    "muscle",
    "mobility",
    "recovery",
    # Emotion / Mental
    "stress",
    "anxiety",
    "depression",
    "meditation",
    "mindful",
    "mental health",
    "burnout",
    "therapy",
    # Longevity / Biohacking
    "longevity",
    "aging",
    "biohack",
    "hormesis",
    "autophagy",
    "nad",
    "peptide",
    "testosterone",
    "rapamycin",
    "metformin",
    "senolytic",
    # Clinical
    "cancer",
    "alzheimer",
    "diabetes",
    "cardiovascular",
    "heart disease",
]


def get_trends(topic: str) -> dict:
    """Fetch Google Trends data for a topic.

    Returns dict with keys: related_top, related_rising, trend_direction.
    Returns empty dict on failure.
    """
    try:
        from trendspy import Trends

        tr = Trends(language="zh-TW", tzs=480)

        def _fetch():
            related = tr.related_queries(topic, timeframe="today 3-m")
            top_queries = []
            rising_queries = []

            if related:
                top_df = related.get("top")
                if top_df is not None and not top_df.empty:
                    top_queries = top_df.head(15).to_dict("records")

                rising_df = related.get("rising")
                if rising_df is not None and not rising_df.empty:
                    rising_queries = rising_df.head(10).to_dict("records")

            trend_direction = "stable"
            try:
                iot = tr.interest_over_time([topic], timeframe="today 3-m")
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


def _is_health_related(keyword: str, trend_keywords: list[str]) -> bool:
    """keyword 或任一 trend_keyword 命中 _HEALTH_SEEDS → True。"""
    haystack = " ".join([keyword or ""] + list(trend_keywords or [])).lower()
    return any(seed in haystack for seed in _HEALTH_SEEDS)


def _velocity_from_trend(volume: int, growth_pct: int) -> float:
    """把 trendspy 的 volume + growth_pct 映射到 0–100 velocity。

    volume_growth_pct 觀察值：熱題 1000、冷題 50–100。
    volume 觀察值：大熱 2M+、中熱 100K、末段 100。

    策略：growth_pct / 10 當基底（1000%→100、500%→50），volume 大給 boost：
      - volume ≥ 1M：+30
      - volume ≥ 100K：+15
    clamp 到 [0, 100]。
    """
    base = (growth_pct or 0) / 10.0
    boost = 0.0
    if volume and volume >= 1_000_000:
        boost = 30.0
    elif volume and volume >= 100_000:
        boost = 15.0
    return max(0.0, min(100.0, base + boost))


def discover_trending_health(*, geo: str = "US") -> list[dict]:
    """從 Google Trends `trending_now` 拉 en-<geo> 趨勢，過濾健康相關，回 list[dict]。

    匿名訪問從 VPS datacenter IP 可跑（不像 Reddit JSON 會被封）。

    回傳 dict 欄位（跟 reddit_api.hot_in_health_subreddits 對齊，scout 好共用）：
      - title: keyword（主關鍵字）
      - velocity_score: 0-100（volume + growth_pct 混合）
      - subreddit: "trends" （用來標示 source 面板，非實際 subreddit）
      - score: volume_growth_pct 原值
      - num_comments: 0（trends 沒 comment 概念）
      - age_hours: 0（trending_now 本來就是當下熱門，不計齡）
      - url: 空（Trends 沒 permalink）
      - related: trend_keywords（前 10 個）

    失敗回 []。
    """
    try:
        from trendspy import Trends
    except ImportError:
        logger.error("trendspy not installed")
        return []

    try:
        tr = Trends(language=f"en-{geo}", tzs=-240)
        trends = tr.trending_now()
    except Exception as e:
        logger.error(f"Trends trending_now failed: {e}")
        return []

    results: list[dict] = []
    for t in trends or []:
        keyword = getattr(t, "keyword", None) or getattr(t, "normalized_keyword", None)
        trend_keywords = getattr(t, "trend_keywords", None) or []
        if not keyword:
            continue
        if not _is_health_related(keyword, trend_keywords):
            continue
        volume = getattr(t, "volume", 0) or 0
        growth_pct = getattr(t, "volume_growth_pct", 0) or 0
        velocity = _velocity_from_trend(volume, growth_pct)
        results.append(
            {
                "title": keyword,
                "velocity_score": round(velocity, 2),
                "subreddit": "trends",
                "score": int(growth_pct),
                "num_comments": 0,
                "age_hours": 0.0,
                "url": "",
                "volume": int(volume),
                "related": list(trend_keywords)[:10],
            }
        )

    results.sort(key=lambda r: r["velocity_score"], reverse=True)
    total = len(trends or [])
    logger.info(f"trends discovery: {len(results)} health-related from {total} total")
    return results
