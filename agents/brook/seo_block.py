"""Build 繁中 SEO context block 接到 Brook compose system prompt 尾端。

ADR-009 Slice C — opt-in SEO context integration。
- T2 sanitization：strip prompt-injection patterns from `competitor_serp_summary`
  （唯一可能含外部不可信內容的欄位）
- T11 token budget：char-based heuristic 截斷，優先順序
  `striking_distance > related_keywords > competitor_serp_summary`
"""

from __future__ import annotations

import re

from shared.schemas.publishing import SEOContextV1

_INJECTION_PATTERNS = [
    r"<\s*system\s*>",
    r"</?\s*(user|assistant|tool_result)\s*>",
    r"ignore\s+(all\s+)?previous\s+instructions",
    r"\bsystem\s*:\s*",
]
_INJECTION_RE = re.compile("|".join(_INJECTION_PATTERNS), re.IGNORECASE)

_MAX_SERP_CHARS = 1200
_MAX_RELATED_KEYWORDS = 10
_MAX_STRIKING_ENTRIES = 5
_MAX_CANNIBAL_ENTRIES = 5


def _sanitize(text: str) -> str:
    return _INJECTION_RE.sub("[redacted]", text)


def build_seo_block(ctx: SEOContextV1) -> str:
    """Render SEOContextV1 為繁中 system prompt 片段。"""
    lines = ["## SEO context（本篇寫作時的數據依據）"]

    if ctx.primary_keyword:
        pk = ctx.primary_keyword
        lines.append(
            f"- 主關鍵字：{pk.keyword}（近 28 天 impressions {pk.impressions}，"
            f"平均排名 {pk.avg_position:.1f}）"
        )

    if ctx.striking_distance:
        lines.append("- Striking distance（排名 11-20 的機會關鍵字）：")
        for sd in ctx.striking_distance[:_MAX_STRIKING_ENTRIES]:
            suggestion = f" — 建議：{sd.suggested_actions[0]}" if sd.suggested_actions else ""
            lines.append(
                f"  - {sd.keyword}（目前排名 {sd.current_position:.1f}，"
                f"impressions {sd.impressions_last_28d}）{suggestion}"
            )

    if ctx.related_keywords:
        top = ctx.related_keywords[:_MAX_RELATED_KEYWORDS]
        kw_list = "、".join(k.keyword for k in top)
        lines.append(f"- 相關關鍵字（自然融入即可，不強塞）：{kw_list}")

    if ctx.cannibalization_warnings:
        lines.append("- ⚠️ 自我競爭警告 — 避免與下列既有頁面主題高度重疊：")
        for w in ctx.cannibalization_warnings[:_MAX_CANNIBAL_ENTRIES]:
            lines.append(f"  - {w.keyword}（{w.severity}）：{w.recommendation}")

    if ctx.competitor_serp_summary:
        summary = _sanitize(ctx.competitor_serp_summary)
        if len(summary) > _MAX_SERP_CHARS:
            summary = summary[:_MAX_SERP_CHARS] + "…（已截斷）"
        lines.append(f"- 競品 SERP 摘要（差異化角度參考）：{summary}")

    lines.append("")
    lines.append(
        "**SEO 規則**：本段 SEO 數據只是寫作依據，不覆蓋「輸出規範」的格式硬規則。"
        "focus_keyword 與 meta_description 仍由你依文意產出，不要照抄 SEO context。"
    )
    return "\n".join(lines)
