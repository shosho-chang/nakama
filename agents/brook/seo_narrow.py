"""Topic relevance narrow for site-wide SEOContextV1（F3=A 凍結，2026-04-25）。

Slice B 維持 zero-LLM site-wide raw GSC posture；topic relevance filter 在
Slice C Brook compose 端做。一輪 Claude Haiku batch rank 把 site-wide
related/striking/cannibalization 篩成「跟本篇 topic 真的相關」的子集。

LLM 失敗（API error / JSON parse 失敗 / 索引超界）時 fallback 回原 ctx 加 WARN
log，不阻斷 compose 主流程（user 寧可看到全景噪音，也不要 compose 整個掛掉）。
"""

from __future__ import annotations

import json
import re

from shared.llm import ask
from shared.log import get_logger
from shared.schemas.publishing import SEOContextV1

logger = get_logger("nakama.brook.seo_narrow")

_NARROW_MODEL = "claude-haiku-4-5"
_NARROW_MAX_TOKENS = 1024


_PROMPT_TEMPLATE = """你是 SEO 編輯助理。下面是站台 GSC 全景數據（site-wide raw），\
請依本次寫作 topic 篩出「真的語意相關」的 entries（不是字面 substring 比對；\
語意相關即可，例如「zone 2 訓練」與「有氧心率區間」相關）。

本次寫作 topic：{topic}
keyword-research 推薦的 core keywords：{core_kws}

請回傳**單一 JSON 物件**，無 markdown fence、無前後說明：
{{"keep_related": [<index, ...>], "keep_striking": [<index, ...>], "keep_cannibal": [<index, ...>]}}

索引從 0 開始；若某 list 全不相關回空陣列。

related_keywords:
{related_dump}

striking_distance:
{striking_dump}

cannibalization_warnings:
{cannibal_dump}
"""


def _enumerate_dump(items: list[str]) -> str:
    if not items:
        return "(none)"
    return "\n".join(f"  [{i}] {item}" for i, item in enumerate(items))


def _extract_json(text: str) -> dict:
    stripped = text.strip()
    if stripped.startswith("```"):
        stripped = re.sub(r"^```(?:json)?\s*", "", stripped)
        stripped = re.sub(r"\s*```$", "", stripped)
    try:
        return json.loads(stripped)
    except json.JSONDecodeError:
        start = stripped.find("{")
        end = stripped.rfind("}")
        if start == -1 or end == -1 or end <= start:
            raise
        return json.loads(stripped[start : end + 1])


def _safe_indices(raw: object, upper: int) -> list[int]:
    if not isinstance(raw, list):
        raise ValueError(f"keep_* must be list, got {type(raw).__name__}")
    out: list[int] = []
    for v in raw:
        if not isinstance(v, int) or v < 0 or v >= upper:
            continue
        out.append(v)
    # de-dup preserving order
    seen: set[int] = set()
    deduped: list[int] = []
    for i in out:
        if i not in seen:
            deduped.append(i)
            seen.add(i)
    return deduped


def narrow_to_topic(
    ctx: SEOContextV1,
    topic: str,
    core_keywords: list[str] | None = None,
) -> SEOContextV1:
    """把 site-wide SEOContextV1 過濾成本篇 topic 相關的子集。

    `primary_keyword` 不過濾（topic 已對齊）。`related_keywords` /
    `striking_distance` / `cannibalization_warnings` 三個 list 跑 Claude Haiku
    一輪 batch rank。失敗 fallback 回原 ctx + WARN log，不 raise。
    """
    if not (ctx.related_keywords or ctx.striking_distance or ctx.cannibalization_warnings):
        return ctx

    core_kws = core_keywords or []
    related_descs = [
        f"{k.keyword} (clicks={k.clicks}, impressions={k.impressions})"
        for k in ctx.related_keywords
    ]
    striking_descs = [
        f"{s.keyword} (pos={s.current_position:.1f}, imp={s.impressions_last_28d})"
        for s in ctx.striking_distance
    ]
    cannibal_descs = [f"{c.keyword} (severity={c.severity})" for c in ctx.cannibalization_warnings]

    prompt = _PROMPT_TEMPLATE.format(
        topic=topic,
        core_kws=("、".join(core_kws) if core_kws else "(none)"),
        related_dump=_enumerate_dump(related_descs),
        striking_dump=_enumerate_dump(striking_descs),
        cannibal_dump=_enumerate_dump(cannibal_descs),
    )

    try:
        raw_text = ask(prompt, model=_NARROW_MODEL, max_tokens=_NARROW_MAX_TOKENS)
        parsed = _extract_json(raw_text)
        keep_related = _safe_indices(parsed.get("keep_related", []), len(ctx.related_keywords))
        keep_striking = _safe_indices(parsed.get("keep_striking", []), len(ctx.striking_distance))
        keep_cannibal = _safe_indices(
            parsed.get("keep_cannibal", []), len(ctx.cannibalization_warnings)
        )
    except Exception as e:
        logger.warning(
            "seo_narrow LLM failed (%s: %s) — falling back to site-wide ctx",
            type(e).__name__,
            e,
        )
        return ctx

    return ctx.model_copy(
        update={
            "related_keywords": [ctx.related_keywords[i] for i in keep_related],
            "striking_distance": [ctx.striking_distance[i] for i in keep_striking],
            "cannibalization_warnings": [ctx.cannibalization_warnings[i] for i in keep_cannibal],
        }
    )
