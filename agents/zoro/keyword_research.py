"""Keyword Research orchestrator — 中英雙語平行蒐集數據，Claude 合成標題建議。"""

from __future__ import annotations

import json
import re
from concurrent.futures import ThreadPoolExecutor, as_completed

from agents.zoro.autocomplete import get_suggestions
from agents.zoro.reddit_api import search_reddit_posts
from agents.zoro.trends_api import get_trends
from agents.zoro.twitter_api import search_recent_tweets
from agents.zoro.youtube_api import search_top_videos
from shared.anthropic_client import ask_claude, set_current_agent
from shared.log import get_logger
from shared.prompt_loader import load_prompt

logger = get_logger("nakama.zoro.keyword_research")

_SOURCE_TIMEOUT = 15  # seconds per data source


def _auto_translate(topic: str) -> str:
    """Use Claude to translate a Chinese topic to its English equivalent."""
    raw = ask_claude(
        "Translate the following topic to English."
        " Reply with ONLY the English term, nothing else."
        f"\n\n{topic}",
        max_tokens=100,
        temperature=0.0,
    )
    return raw.strip().strip('"').strip("'")


def research_keywords(
    topic: str,
    content_type: str = "youtube",
    en_topic: str | None = None,
) -> dict:
    """Run bilingual keyword research for *topic* and generate title suggestions.

    Searches both Chinese and English sources in parallel to capture
    international trends that haven't reached the Chinese market yet.

    Args:
        topic: Chinese topic (e.g. "間歇性斷食")
        content_type: "youtube" or "blog"
        en_topic: English equivalent (e.g. "intermittent fasting").
                  Auto-translated from *topic* if not provided.

    Returns JSON-serializable dict with keys:
        keywords, youtube_titles, blog_titles, analysis_summary,
        sources_used, sources_failed
    """
    set_current_agent("zoro")

    # ── Auto-translate if needed ──────────────────────────────────────────
    if not en_topic:
        logger.info(f"Auto-translating topic: {topic}")
        en_topic = _auto_translate(topic)
        logger.info(f"English topic: {en_topic}")

    # ── Parallel data collection (10 tasks: 3x2 lang + 2x2 social) ─────
    collectors = {
        "youtube_zh": lambda: search_top_videos(topic),
        "youtube_en": lambda: search_top_videos(en_topic),
        "trends_zh": lambda: get_trends(topic),
        "trends_en": lambda: get_trends(en_topic),
        "autocomplete_zh": lambda: get_suggestions(topic),
        "autocomplete_en": lambda: get_suggestions(en_topic),
        "twitter_zh": lambda: search_recent_tweets(topic),
        "twitter_en": lambda: search_recent_tweets(en_topic),
        "reddit_zh": lambda: search_reddit_posts(topic),
        "reddit_en": lambda: search_reddit_posts(en_topic),
    }

    data: dict[str, dict] = {}
    sources_used: list[str] = []
    sources_failed: list[str] = []

    with ThreadPoolExecutor(max_workers=10) as pool:
        futures = {pool.submit(fn): name for name, fn in collectors.items()}
        for future in as_completed(futures, timeout=_SOURCE_TIMEOUT + 5):
            name = futures[future]
            try:
                result = future.result(timeout=_SOURCE_TIMEOUT)
                if result:
                    data[name] = result
                    sources_used.append(name)
                else:
                    sources_failed.append(name)
            except Exception as e:
                logger.warning(f"Data source {name} failed: {e}")
                sources_failed.append(name)

    if not sources_used:
        raise RuntimeError("所有資料來源都失敗，無法進行關鍵字研究")

    # ── Format data for Claude (bilingual) ────────────────────────────────
    youtube_data_zh = _format_youtube(data.get("youtube_zh", {}))
    youtube_data_en = _format_youtube(data.get("youtube_en", {}))
    trends_data_zh = _format_trends(data.get("trends_zh", {}))
    trends_data_en = _format_trends(data.get("trends_en", {}))
    autocomplete_data_zh = _format_autocomplete(data.get("autocomplete_zh", {}))
    autocomplete_data_en = _format_autocomplete(data.get("autocomplete_en", {}))
    twitter_data_zh = _format_twitter(data.get("twitter_zh", {}))
    twitter_data_en = _format_twitter(data.get("twitter_en", {}))
    reddit_data_zh = _format_reddit(data.get("reddit_zh", {}))
    reddit_data_en = _format_reddit(data.get("reddit_en", {}))

    # ── Claude synthesis ──────────────────────────────────────────────────
    prompt = load_prompt(
        "zoro",
        "keyword_research",
        topic=topic,
        en_topic=en_topic,
        content_type=content_type,
        youtube_data_zh=youtube_data_zh,
        youtube_data_en=youtube_data_en,
        trends_data_zh=trends_data_zh,
        trends_data_en=trends_data_en,
        autocomplete_data_zh=autocomplete_data_zh,
        autocomplete_data_en=autocomplete_data_en,
        twitter_data_zh=twitter_data_zh,
        twitter_data_en=twitter_data_en,
        reddit_data_zh=reddit_data_zh,
        reddit_data_en=reddit_data_en,
    )

    raw = ask_claude(prompt, max_tokens=4096, temperature=0.5)
    result = _parse_claude_response(raw)

    # ── Attach raw data for Obsidian rendering ─────────────────────────
    trending_videos = []
    for lang, key in [("zh", "youtube_zh"), ("en", "youtube_en")]:
        vdata = data.get(key, {})
        for v in vdata.get("top_videos", []):
            trending_videos.append({**v, "lang": lang})
    trending_videos.sort(key=lambda v: v["views"], reverse=True)

    social_posts = []
    for lang, key in [("zh", "twitter_zh"), ("en", "twitter_en")]:
        for t in data.get(key, {}).get("tweets", []):
            social_posts.append({**t, "lang": lang, "platform": "twitter"})
    for lang, key in [("zh", "reddit_zh"), ("en", "reddit_en")]:
        for p in data.get(key, {}).get("posts", []):
            social_posts.append(
                {
                    "title": p["title"],
                    "score": p["score"],
                    "num_comments": p["num_comments"],
                    "subreddit": p["subreddit"],
                    "url": p["url"],
                    "lang": lang,
                    "platform": "reddit",
                }
            )

    result["en_topic"] = en_topic
    result["trending_videos"] = trending_videos[:20]
    result["social_posts"] = social_posts[:20]
    result["sources_used"] = sources_used
    result["sources_failed"] = sources_failed

    return result


def _format_youtube(data: dict) -> str:
    if not data:
        return "（YouTube 數據不可用）"
    lines = [f"平均觀看次數：{data.get('avg_views', 0):,}"]
    for v in data.get("top_videos", []):
        lines.append(f"- [{v['views']:,} views] {v['title']} ({v['channel']}, {v['published']})")
    if data.get("common_words"):
        words = ", ".join(f"{w['word']}({w['count']})" for w in data["common_words"][:15])
        lines.append(f"\n高頻詞彙：{words}")
    return "\n".join(lines)


def _format_trends(data: dict) -> str:
    if not data:
        return "（Google Trends 數據不可用）"
    lines = [f"趨勢方向：{data.get('trend_direction', 'unknown')}"]
    top = data.get("related_top", [])
    if top:
        lines.append("相關熱門查詢：")
        for q in top[:10]:
            lines.append(f"- {q['query']}（相關度 {q['value']}）")
    rising = data.get("related_rising", [])
    if rising:
        lines.append("快速上升查詢：")
        for q in rising[:5]:
            lines.append(f"- {q['query']}（成長 {q['value']}）")
    return "\n".join(lines)


def _format_autocomplete(data: dict) -> str:
    if not data:
        return "（Autocomplete 數據不可用）"
    suggestions = data.get("suggestions", [])
    return "YouTube 搜尋建議：\n" + "\n".join(f"- {s}" for s in suggestions)


def _format_twitter(data: dict) -> str:
    if not data:
        return "（Twitter 數據不可用）"
    tweets = data.get("tweets", [])
    if not tweets:
        return "（無相關推文）"
    lines = []
    for t in tweets[:10]:
        engagement = f"❤️{t['likes']} 🔄{t['retweets']}"
        lines.append(f"- [{engagement}] @{t['username']}: {t['text'][:120]}")
    return "熱門推文：\n" + "\n".join(lines)


def _format_reddit(data: dict) -> str:
    if not data:
        return "（Reddit 數據不可用）"
    posts = data.get("posts", [])
    if not posts:
        return "（無相關討論）"
    lines = []
    for p in posts[:10]:
        lines.append(f"- [⬆{p['score']} 💬{p['num_comments']}] r/{p['subreddit']}: {p['title']}")
    return "熱門討論：\n" + "\n".join(lines)


def _parse_claude_response(raw: str) -> dict:
    """Extract JSON from Claude's response, handling markdown fences."""
    # Strip markdown code fences if present
    cleaned = re.sub(r"^```(?:json)?\s*\n?", "", raw.strip())
    cleaned = re.sub(r"\n?```\s*$", "", cleaned)

    try:
        parsed = json.loads(cleaned)
    except json.JSONDecodeError:
        # Fallback: try to find JSON object in response
        match = re.search(r"\{[\s\S]*\}", raw)
        if match:
            try:
                parsed = json.loads(match.group())
            except json.JSONDecodeError:
                parsed = {}

    if not parsed:
        logger.warning("Failed to parse Claude JSON, returning raw text as summary")
        return {
            "keywords": [],
            "youtube_titles": [],
            "blog_titles": [],
            "analysis_summary": raw[:500],
        }

    return {
        "keywords": parsed.get("core_keywords", []),
        "trend_gaps": parsed.get("trend_gaps", []),
        "youtube_titles": parsed.get("youtube_titles", []),
        "blog_titles": parsed.get("blog_titles", []),
        "analysis_summary": parsed.get("analysis_summary", ""),
    }
