"""Keyword Research orchestrator — 平行蒐集數據，Claude 合成標題建議。"""

from __future__ import annotations

import json
import re
from concurrent.futures import ThreadPoolExecutor, as_completed

from agents.zoro.autocomplete import get_suggestions
from agents.zoro.trends_api import get_trends
from agents.zoro.youtube_api import search_top_videos
from shared.anthropic_client import ask_claude, set_current_agent
from shared.log import get_logger
from shared.prompt_loader import load_prompt

logger = get_logger("nakama.zoro.keyword_research")

_SOURCE_TIMEOUT = 15  # seconds per data source


def research_keywords(topic: str, content_type: str = "youtube") -> dict:
    """Run keyword research for *topic* and generate title suggestions.

    Calls YouTube API, Google Trends, and YouTube Autocomplete in parallel,
    then asks Claude to synthesize the data into keyword analysis + titles.

    Returns JSON-serializable dict with keys:
        keywords, youtube_titles, blog_titles, analysis_summary,
        sources_used, sources_failed
    """
    set_current_agent("zoro")

    # ── Parallel data collection ──────────────────────────────────────────
    collectors = {
        "youtube_api": lambda: search_top_videos(topic),
        "google_trends": lambda: get_trends(topic),
        "autocomplete": lambda: get_suggestions(topic),
    }

    data: dict[str, dict] = {}
    sources_used: list[str] = []
    sources_failed: list[str] = []

    with ThreadPoolExecutor(max_workers=3) as pool:
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

    # ── Format data for Claude ────────────────────────────────────────────
    youtube_data = _format_youtube(data.get("youtube_api", {}))
    trends_data = _format_trends(data.get("google_trends", {}))
    autocomplete_data = _format_autocomplete(data.get("autocomplete", {}))

    # ── Claude synthesis ──────────────────────────────────────────────────
    prompt = load_prompt(
        "zoro",
        "keyword_research",
        topic=topic,
        content_type=content_type,
        youtube_data=youtube_data,
        trends_data=trends_data,
        autocomplete_data=autocomplete_data,
    )

    raw = ask_claude(prompt, max_tokens=4096, temperature=0.5)
    result = _parse_claude_response(raw)

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
        "youtube_titles": parsed.get("youtube_titles", []),
        "blog_titles": parsed.get("blog_titles", []),
        "analysis_summary": parsed.get("analysis_summary", ""),
    }
