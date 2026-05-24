"""Tier C LLM persona reviews — advisory feedback for hooks + scripts.

ADR-031 §D8. Two personas, expert-advisor model (NOT style-mimic — owner
explicitly rejected mimicking past articles; see
``memory/claude/feedback_expert_persona_over_style_mimic.md``):

- ``storyteller`` (Master Storyteller) — hook + narrative arc + cognitive hook
- ``coach`` (Writing Coach) — sentence variety, jargon, rhythm

Each persona is a separate LLM call so user can re-run one without re-paying
for the other (and so prompts stay focused).

Output shape matches ``docs/schemas/project-frontmatter-nested.md`` §reviews:

    {run_at, prompt_version, score (1-5), summary (str), suggestions (list[str])}

The v2 schema is **list-of-versioned-objects** per persona (panel-Gemini
push, ADR-031 v2). The caller (router) is responsible for appending the
returned dict to ``reviews.{persona}`` list. See
:func:`shared.project_writer.append_review`.

**Traditional Chinese leakage guard** (panel-Gemini push): system prompt
hard-pins output to 繁體中文（台灣）. Tests assert the guard is in the
prompt; runtime validation rejects responses with 簡體 markers.

Few-shot example is **generic content** (not user's past articles) per
the expert-persona-over-style-mimic feedback. Owner can swap to a
gold-standard exemplar by editing the constants at the bottom of this
file without changing the dispatch logic.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from datetime import datetime
from typing import Any
from zoneinfo import ZoneInfo

from shared.anthropic_client import ask_claude

# Prompt version — bump whenever the rubric / few-shot / guard wording changes.
# Stored in each review's ``prompt_version`` so historical reviews stay
# interpretable when the prompt evolves.
PROMPT_VERSION = "v1.0-2026-05-24"

PERSONA_STORYTELLER = "storyteller"
PERSONA_COACH = "coach"
PERSONAS = (PERSONA_STORYTELLER, PERSONA_COACH)


class ProjectReviewError(RuntimeError):
    """Raised when the LLM response can't be parsed into a valid review."""


@dataclass(frozen=True)
class ReviewResult:
    """One persona's verdict — serializes to schema §reviews.{persona} entry."""

    run_at: str
    prompt_version: str
    score: int
    summary: str
    suggestions: list[str]

    def as_dict(self) -> dict[str, Any]:
        return {
            "run_at": self.run_at,
            "prompt_version": self.prompt_version,
            "score": self.score,
            "summary": self.summary,
            "suggestions": list(self.suggestions),
        }


# ── Public API ─────────────────────────────────────────────────────────────


def review_hook(hook_text: str) -> ReviewResult:
    """Master Storyteller verdict on a hook (30–60 秒 opener)."""
    if not hook_text.strip():
        raise ProjectReviewError("hook_text is empty — nothing to review")
    return _dispatch(PERSONA_STORYTELLER, _PROMPT_STORYTELLER, hook_text)


def review_script(script_text: str) -> ReviewResult:
    """Writing Coach verdict on a script body (full or H2 section)."""
    if not script_text.strip():
        raise ProjectReviewError("script_text is empty — nothing to review")
    return _dispatch(PERSONA_COACH, _PROMPT_COACH, script_text)


def get_prompt_for_persona(persona: str) -> str:
    """Return the raw prompt template (with ``{content}`` placeholder).

    Exposed for tests + ad-hoc inspection. Production code should call
    :func:`review_hook` / :func:`review_script`.
    """
    if persona == PERSONA_STORYTELLER:
        return _PROMPT_STORYTELLER
    if persona == PERSONA_COACH:
        return _PROMPT_COACH
    raise ValueError(f"unknown persona: {persona!r}")


# ── Internals ──────────────────────────────────────────────────────────────


def _dispatch(persona: str, prompt_template: str, content: str) -> ReviewResult:
    prompt = prompt_template.replace("{content}", content.strip())
    raw = ask_claude(prompt, max_tokens=1024)
    data = _parse_response(raw, persona=persona)
    _validate_no_simplified_leakage(data, persona=persona)

    now = datetime.now(ZoneInfo("Asia/Taipei")).isoformat(timespec="seconds")
    return ReviewResult(
        run_at=now,
        prompt_version=PROMPT_VERSION,
        score=int(data["score"]),
        summary=str(data["summary"]).strip(),
        suggestions=[str(s).strip() for s in data.get("suggestions", []) if str(s).strip()][:10],
    )


def _parse_response(raw: str, *, persona: str) -> dict[str, Any]:
    """Extract the JSON object from the LLM response, tolerating code-fence wrap."""
    text = raw.strip()
    # Strip ```json ... ``` or ``` ... ``` wrapping
    fence_match = re.search(r"```(?:json)?\s*(.+?)\s*```", text, re.DOTALL)
    if fence_match:
        text = fence_match.group(1).strip()

    try:
        data = json.loads(text)
    except json.JSONDecodeError as exc:
        raise ProjectReviewError(
            f"persona={persona} returned non-JSON: {raw[:300]!r}"
        ) from exc

    if not isinstance(data, dict):
        raise ProjectReviewError(f"persona={persona} returned non-object: {type(data).__name__}")

    try:
        score = int(data["score"])
    except (KeyError, TypeError, ValueError) as exc:
        raise ProjectReviewError(f"persona={persona} missing/invalid score") from exc
    if not (1 <= score <= 5):
        raise ProjectReviewError(f"persona={persona} score out of range [1,5]: {score}")

    if not str(data.get("summary", "")).strip():
        raise ProjectReviewError(f"persona={persona} missing summary")

    return data


# Common simplified-Chinese sentinels. Curated for false-positive control —
# only chars that are exclusively-simplified, not 通用 chars.
# (e.g. 学 is also a valid 異體字 in some 繁中 contexts; not on this list.)
_SIMPLIFIED_SENTINELS = ("简", "体", "汉", "实", "现", "经", "应", "时", "样", "复")


def _validate_no_simplified_leakage(data: dict[str, Any], *, persona: str) -> None:
    """Reject responses containing simplified-Chinese sentinel chars.

    Conservative — we only flag if 3+ distinct sentinels appear, to avoid
    false positives on legitimate uses (e.g. quoting a 簡體 source).
    """
    blob = str(data.get("summary", "")) + " ".join(str(s) for s in data.get("suggestions", []))
    hit = {c for c in _SIMPLIFIED_SENTINELS if c in blob}
    if len(hit) >= 3:
        raise ProjectReviewError(
            f"persona={persona} response contains simplified-Chinese chars {sorted(hit)!r}; "
            "expected 繁體中文 only (zh-Hant leakage guard, ADR-031 v2 panel push)"
        )


# ── Prompt templates ───────────────────────────────────────────────────────
#
# Two personas. Both end with the same ``_LANG_GUARD`` suffix that pins
# output format + language. The few-shot examples are **generic** content
# (not from owner's past articles, per feedback_expert_persona_over_style_mimic);
# owner can swap them by editing the in-line examples without touching
# dispatch logic.
#
# When bumping prompt content, also bump ``PROMPT_VERSION`` at the top.


_LANG_GUARD = """\

嚴格輸出規範：
1. 一律以繁體中文（台灣慣用語）回應。禁止任何簡體字、日文段落（人名 / 術語可保留原文，
   但解釋必須繁中）。
2. 輸出為單一 JSON 物件，欄位：
   - score: 整數 1–5
   - summary: 2–3 句中文摘要，總長 ≤120 字
   - suggestions: list of 1–10 個 actionable 建議句，每句 ≤80 字
3. 直接輸出 JSON，不要前言、不要結語、不要任何 markdown code fence。
"""


_PROMPT_STORYTELLER = """\
你是 Master Storyteller persona — 針對短影音 hook、podcast intro、文章開場進行
narrative arc 與認知鉤子的判讀。你的任務是用普遍適用的故事工藝原則評分，
**不要模仿作者過往風格**（作者明確希望從專家視角獲得回饋，而非延續既有平均）。

評分 rubric (1-5):
- 1 = 無 hook 結構。讀者第一眼就走。沒有認知落差、沒有具體數字、沒有承諾。
- 2 = 有試圖 hook 但太寬。例：「今天我們來談肌酸」「最新研究顯示」這種模板。
- 3 = 結構在但平淡。有「你以為 X」但 Y 不夠 surprising，或具體數字但落差弱。
- 4 = 結構漂亮 + 數字具體 + 鉤子明顯。讀者有理由停留 30 秒以上。
- 5 = 結構漂亮 + 認知落差強烈 + 鉤子埋得深。讀者必須繼續看才能解開。

評判維度：
- 第一句的 hook 強度（認知落差大小）
- 是否埋有「不講 X」式的下一段鉤子
- 具體數字 / 結論明確度
- 30–60 秒口語長度（台灣口語 200–300字/分鐘，soft cap ≤300 字）

few-shot 對齊範例（generic — 與作者過往文章無關）：

範例 INPUT:
你以為咖啡只是醒腦工具？2024 年一份對 18,000 人的世代研究顯示，每天 3 杯黑咖啡的人，
5 年內阿茲海默症的相對風險，比不喝者低 31%——而且咖啡因不是主要原因。
今天我們不講提神……

範例 OUTPUT:
{
  "score": 5,
  "summary": "認知落差強（醒腦→失智預防），數字具體，結尾埋鉤子，結構完整。",
  "suggestions": [
    "第一句可再精簡至 25 字內讓鉤子更快出現",
    "「世代研究」可白話化為「長期追蹤研究」",
    "「相對風險」對一般觀眾可改成「機率」"
  ]
}

待評輸入：

{content}
""" + _LANG_GUARD


_PROMPT_COACH = """\
你是 Writing Coach persona — 針對 script / outline / 撰文進行可讀性、句長變化、
專有名詞白話度、呼吸節奏的判讀。同樣 **不要模仿作者過往風格**，從普遍寫作工藝
原則出發。

評分 rubric (1-5):
- 1 = 全句長相同，jargon 重，讀者必須回頭重讀才能懂。
- 2 = 部分句長變化，jargon 偶有白話化但不一致。
- 3 = 句長變化明顯，jargon 有解釋但密度過高。
- 4 = 句長韻律好，jargon 解釋自然，呼吸節奏出來了。
- 5 = 句長像水波、jargon 已經白話化、呼吸節奏完美、讀者口語可朗讀。

評判維度：
- 句長變化（避免全句 25 字以上）
- 專有名詞白話化（如「統計顯著」→「不是運氣造成」）
- 段落呼吸（每 2-3 段加一句短句做對比）
- 整體口語朗讀流暢度

few-shot 對齊範例（generic）：

範例 INPUT:
肌酸是一種廣泛存在於人體骨骼肌中的含氮有機酸。它能透過磷酸肌酸系統參與細胞能量代謝。
多項隨機對照試驗證實，攝取外源性肌酸可顯著提升肌肉力量與運動表現。

範例 OUTPUT:
{
  "score": 2,
  "summary": "句長全部 25 字以上，jargon 密度高（含氮有機酸、磷酸肌酸），無呼吸節奏。",
  "suggestions": [
    "把「含氮有機酸」改成「身體會自製的能量物質」",
    "中間插一句 10 字以內的短句做呼吸對比",
    "「隨機對照試驗證實」改「實驗證明」即可",
    "「外源性肌酸」可改成「從補品攝取的肌酸」"
  ]
}

待評輸入：

{content}
""" + _LANG_GUARD
