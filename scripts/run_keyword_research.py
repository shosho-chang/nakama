"""Keyword Research CLI — skill-friendly wrapper around agents.zoro.keyword_research.

Invoked by the `keyword-research` Claude Code skill. Also usable standalone for
scheduled runs or ad-hoc CLI use.

Writes a markdown file with a structured frontmatter block that downstream
skills (Brook compose, future SEO audit) can parse without re-running the
pipeline.

Usage:
    python scripts/run_keyword_research.py "間歇性斷食" --content-type blog
    python scripts/run_keyword_research.py "磁振造影" --en-topic "MRI" --out out.md
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8")
sys.stderr.reconfigure(encoding="utf-8")

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import yaml  # noqa: E402

from agents.zoro.keyword_research import research_keywords  # noqa: E402


def _build_frontmatter(
    topic: str,
    en_topic: str,
    content_type: str,
    result: dict,
) -> dict:
    """Build structured frontmatter consumable by downstream skills.

    Downstream consumers (Brook compose, SEO audit) should be able to parse this
    block to reuse the research output without re-invoking the pipeline.
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


def _render_markdown(frontmatter: dict, result: dict) -> str:
    """Render human-readable markdown body. Frontmatter carries structured data."""
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


def _default_output_path(topic: str) -> Path:
    safe = "".join(c if c.isalnum() or c in "-_" else "_" for c in topic)[:50]
    stamp = datetime.now().strftime("%Y%m%d-%H%M")
    return Path.cwd() / f"keyword-research-{safe}-{stamp}.md"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Bilingual keyword research + markdown report generator."
    )
    parser.add_argument("topic", help="Chinese topic (e.g. 間歇性斷食)")
    parser.add_argument(
        "--en-topic",
        default=None,
        help="English equivalent (auto-translated if omitted)",
    )
    parser.add_argument(
        "--content-type",
        choices=["youtube", "blog"],
        default="youtube",
        help="Optimization target for title suggestions (default: youtube)",
    )
    parser.add_argument(
        "--out",
        type=Path,
        default=None,
        help="Output markdown path (default: ./keyword-research-<topic>-<timestamp>.md)",
    )
    parser.add_argument(
        "--json-out",
        type=Path,
        default=None,
        help="Also dump the raw result dict as JSON to this path (optional)",
    )

    args = parser.parse_args(argv)

    topic = args.topic.strip()
    if not topic:
        parser.error("topic cannot be empty")

    print(f"[keyword-research] 主題：{topic}", file=sys.stderr)
    if args.en_topic:
        print(f"[keyword-research] 英文：{args.en_topic}", file=sys.stderr)
    print(f"[keyword-research] 模式：{args.content_type}", file=sys.stderr)
    print("[keyword-research] 執行中…（預計 30-60s，取決於資料來源回應）", file=sys.stderr)

    try:
        result = research_keywords(
            topic,
            content_type=args.content_type,
            en_topic=args.en_topic,
        )
    except RuntimeError as e:
        print(f"[keyword-research] ERROR: {e}", file=sys.stderr)
        return 2

    frontmatter = _build_frontmatter(topic, args.en_topic or "", args.content_type, result)
    md = _render_markdown(frontmatter, result)

    out_path = args.out or _default_output_path(topic)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(md, encoding="utf-8")
    print(f"[keyword-research] 寫入：{out_path}", file=sys.stderr)

    if args.json_out:
        args.json_out.parent.mkdir(parents=True, exist_ok=True)
        args.json_out.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"[keyword-research] JSON dump：{args.json_out}", file=sys.stderr)

    sources_used = result.get("sources_used", [])
    sources_failed = result.get("sources_failed", [])
    print(
        f"[keyword-research] sources_used={len(sources_used)} sources_failed={len(sources_failed)}",
        file=sys.stderr,
    )

    return 0


if __name__ == "__main__":
    sys.exit(main())
