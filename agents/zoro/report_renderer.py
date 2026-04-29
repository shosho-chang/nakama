"""Render keyword-research result dicts into markdown reports.

Originally lived inline in ``scripts/run_keyword_research.py``; extracted so the
bridge UI surface (``/bridge/zoro/keyword-research``) can reuse the exact same
frontmatter + body shape without re-implementing it.

Both consumers (the CLI script and the FastAPI router) feed the same
``research_keywords()`` result through here, so downstream skills (Brook
compose, SEO audit) see byte-identical frontmatter regardless of trigger
point — that's the deliberate sharing contract.
"""

from __future__ import annotations

from datetime import datetime, timezone

import yaml


def build_frontmatter(
    topic: str,
    en_topic: str,
    content_type: str,
    result: dict,
) -> dict:
    """Build structured frontmatter consumable by downstream skills.

    Downstream consumers (Brook compose, SEO audit) parse this block to reuse
    the research output without re-invoking the pipeline. Keep the key set in
    sync with the SKILL.md contract.
    """
    return {
        "type": "keyword-research",
        "topic": topic,
        "topic_en": en_topic or result.get("en_topic", ""),
        "content_type": content_type,
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "sources_used": result.get("sources_used", []),
        "sources_failed": result.get("sources_failed", []),
        "core_keywords": result.get("keywords", []),
        "trend_gaps": result.get("trend_gaps", []),
        "youtube_title_seeds": result.get("youtube_titles", []),
        "blog_title_seeds": result.get("blog_titles", []),
    }


def render_markdown(frontmatter: dict, result: dict) -> str:
    """Render a human-readable markdown body. Frontmatter carries structured data."""
    lines: list[str] = []
    lines.append("---")
    lines.append(yaml.safe_dump(frontmatter, allow_unicode=True, sort_keys=False).rstrip())
    lines.append("---")
    lines.append("")
    lines.append(f"# 關鍵字研究：{frontmatter['topic']}")
    lines.append("")

    summary = result.get("analysis_summary", "")
    if summary:
        lines.append("## 策略摘要")
        lines.append("")
        lines.append(summary)
        lines.append("")

    keywords = result.get("keywords", [])
    if keywords:
        lines.append("## 核心關鍵字")
        lines.append("")
        lines.append("| 關鍵字 | 英文 | 搜尋量 | 競爭度 | 機會 | 來源 | 說明 |")
        lines.append("|--------|------|--------|--------|------|------|------|")
        for kw in keywords:
            lines.append(
                "| {keyword} | {keyword_en} | {search_volume} | {competition} |"
                " {opportunity} | {source} | {reason} |".format(
                    keyword=kw.get("keyword", ""),
                    keyword_en=kw.get("keyword_en", ""),
                    search_volume=kw.get("search_volume", ""),
                    competition=kw.get("competition", ""),
                    opportunity=kw.get("opportunity", ""),
                    source=kw.get("source", ""),
                    reason=kw.get("reason", "").replace("|", "\\|"),
                )
            )
        lines.append("")

    gaps = result.get("trend_gaps", [])
    if gaps:
        lines.append("## 跨語言趨勢缺口")
        lines.append("")
        for g in gaps:
            lines.append(f"### {g.get('topic', '')}")
            lines.append(f"- **英文信號**：{g.get('en_signal', '')}")
            lines.append(f"- **中文現況**：{g.get('zh_status', '')}")
            lines.append(f"- **機會**：{g.get('opportunity', '')}")
            lines.append("")

    yt_titles = result.get("youtube_titles", [])
    if yt_titles:
        lines.append("## YouTube 標題建議")
        lines.append("")
        for t in yt_titles:
            lines.append(f"- {t}")
        lines.append("")

    blog_titles = result.get("blog_titles", [])
    if blog_titles:
        lines.append("## Blog 標題建議")
        lines.append("")
        for t in blog_titles:
            lines.append(f"- {t}")
        lines.append("")

    trending = result.get("trending_videos", [])
    if trending:
        lines.append("## 熱門影片參考")
        lines.append("")
        for v in trending[:10]:
            lang = v.get("lang", "")
            title = v.get("title", "")
            views = v.get("views", 0)
            channel = v.get("channel", "")
            url = v.get("url", "")
            lines.append(f"- [{lang}] [{title}]({url}) — {views:,} views ({channel})")
        lines.append("")

    social = result.get("social_posts", [])
    if social:
        lines.append("## 社群熱議")
        lines.append("")
        for p in social[:10]:
            platform = p.get("platform", "")
            lang = p.get("lang", "")
            title = p.get("title") or p.get("text", "")[:100]
            url = p.get("url", "")
            lines.append(f"- [{platform}/{lang}] [{title}]({url})")
        lines.append("")

    sources_used = result.get("sources_used", [])
    sources_failed = result.get("sources_failed", [])
    lines.append("## 資料來源狀態")
    lines.append("")
    lines.append(f"- 成功：{', '.join(sources_used) if sources_used else '無'}")
    if sources_failed:
        lines.append(f"- 失敗：{', '.join(sources_failed)}")
    lines.append("")

    return "\n".join(lines)
