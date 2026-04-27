"""LLM semantic check for `seo-audit-post`（ADR-009 Phase 1.5 Slice D.2，§附錄 C）。

12 條 semantic check 走 single-call batch（Sonnet 4.6，input ~3.5K + output ~2.5K
≈ $0.025–0.035 / audit）。每條回 `{status, actual, fix_suggestion}`；任何失敗
（API error / JSON parse 失敗 / LLM 回傳缺項）都降級成 `status="skip"`，**不**
讓 audit pipeline 因 LLM 層 fail 整個炸掉。

職責邊界
--------
- 負責 prompt 組裝 + LLM call + JSON 解析 + AuditCheck list 構造
- **不**做 deterministic check（那屬 metadata.py / headings.py / ...）
- **不**做 KB query（caller 負責先呼 `agents.robin.kb_search.search_kb` with
  `purpose="seo_audit"`，把結果以 `kb_context` 餵進來）
- **不**做 compliance regex scan（caller 負責先呼 `shared.compliance.scan_text`，
  把 `compliance_findings` 餵進來）

L9（藥事法）/ L10（schema vs internal link）兩條依賴 caller 注入的 context；缺
context 時降級 `status="skip"` 並在 actual 標明缺哪一塊。
"""

from __future__ import annotations

import json
import re
from typing import Any, Literal

from bs4 import BeautifulSoup

from shared.anthropic_client import ask_claude, set_current_agent
from shared.log import get_logger
from shared.seo_audit.types import AuditCheck

logger = get_logger("nakama.seo_audit.llm_review")

LLMLevel = Literal["sonnet", "haiku", "none"]

_SONNET_MODEL = "claude-sonnet-4-6"
_HAIKU_MODEL = "claude-haiku-4-5-20251001"
_MAX_OUTPUT_TOKENS = 4096
_HTML_EXCERPT_CHARS = 5000  # input cost cap

# Single source of truth for the 12 rule definitions — drives prompt 組裝、
# missing-rule fallback、order in returned `list[AuditCheck]`。
_RULES: tuple[dict[str, str], ...] = (
    {
        "id": "L1",
        "name": "H1 含 focus keyword 語義（字面或近似）",
        "category": "semantic",
        "severity": "warning",
        "expected": "H1 直接或語義上覆蓋 focus keyword",
        "intent": "字面 substring 不夠（中英混排 / 同義 / 詞序）；判斷語義覆蓋。",
    },
    {
        "id": "L2",
        "name": "第一段（前 200 字）含 focus keyword 語義",
        "category": "semantic",
        "severity": "warning",
        "expected": "首段或前 200 字內提及 focus keyword 主題",
        "intent": "Lead paragraph SEO 慣例；同 L1 的語義覆蓋判斷。",
    },
    {
        "id": "L3",
        "name": "focus keyword 密度合理（不過度堆砌）",
        "category": "semantic",
        "severity": "warning",
        "expected": "keyword 自然分布、不堆砌（≈1-3% 出現率為健康範圍）",
        "intent": "抓 keyword stuffing pattern（連續多段重複塞同一詞）。",
    },
    {
        "id": "L4",
        "name": "內容回答 user search intent",
        "category": "semantic",
        "severity": "critical",
        "expected": "內文涵蓋 focus keyword 對應的主要使用者疑問",
        "intent": "Search intent 與內文 fit；偏題或答非所問皆 fail。",
    },
    {
        "id": "L5",
        "name": "E-E-A-T Experience：第一人稱經驗 / 案例 / 照片證據",
        "category": "semantic",
        "severity": "warning",
        "expected": "明確第一人稱經驗、實作步驟或案例敘述",
        "intent": "Google QRG §3 Experience；判斷是否為紙上談兵。",
    },
    {
        "id": "L6",
        "name": "E-E-A-T Expertise：作者 bio / 引用 / credentials",
        "category": "semantic",
        "severity": "warning",
        "expected": "可見作者背景 + 至少 1 個權威引用 / 學歷 / 證照",
        "intent": "Expertise 信號；author bio + cited authority。",
    },
    {
        "id": "L7",
        "name": "E-E-A-T Authoritativeness：被引用 / 外部 mention 提示",
        "category": "semantic",
        "severity": "info",
        "expected": "內文或 author footer 提示該作者 / 站被外部引用",
        "intent": "從內文提示 author authority；外部 link 可參考。",
    },
    {
        "id": "L8",
        "name": "E-E-A-T Trustworthiness：HTTPS / 隱私頁 / 聯絡",
        "category": "semantic",
        "severity": "warning",
        "expected": "可見 HTTPS、隱私政策連結、聯絡方式或 footer 透明資訊",
        "intent": "結構性 + 內容 trust 信號混合。",
    },
    {
        "id": "L9",
        "name": "台灣藥事法 / 醫療法 compliance",
        "category": "semantic",
        "severity": "critical",
        "expected": "未提療效 / 治癒承諾 / 絕對保證；藥品提及不引導購買",
        "intent": (
            "中文法規語境；reuse `shared.compliance.scan_text` 的詞庫掃描結果"
            " + LLM 補抓詞庫漏網之魚（如語境改寫、正↔簡 drift）。"
        ),
    },
    {
        "id": "L10",
        "name": "Schema 與內容一致性 / Internal link 機會",
        "category": "semantic",
        "severity": "warning",
        "expected": "Article schema headline ≈ <h1>；KB 中相關頁面已被連結",
        "intent": (
            "Schema vs <h1> 一致；以 caller 注入的 KB context 提示 internal "
            "link 機會（例如 KB 有 [[能量系統]] 但內文未連）。"
        ),
    },
    {
        "id": "L11",
        "name": "Medical references / DOI / PubMed / 衛福部 / WHO 等權威源引用率",
        "category": "semantic",
        "severity": "warning",
        "expected": "至少 2-3 個外連到權威來源（PubMed / DOI / 衛福部 / WHO 等）",
        "intent": "YMYL Health niche；S3 字面 ≥1 link 過寬鬆，這條看權威性。",
    },
    {
        "id": "L12",
        "name": "Last reviewed date / 醫師審稿標記 / 內容更新頻率",
        "category": "semantic",
        "severity": "warning",
        "expected": "可見最後更新日期 / 醫師審稿 by / 內容版本標記",
        "intent": (
            "YMYL freshness；SC3 Author 不 cover 「reviewed by」層；"
            "可在內文「最後更新」標記或 article:modified_time meta 抓到。"
        ),
    },
)

_RULE_IDS: tuple[str, ...] = tuple(r["id"] for r in _RULES)


def _model_for_level(level: LLMLevel) -> str:
    if level == "sonnet":
        return _SONNET_MODEL
    if level == "haiku":
        return _HAIKU_MODEL
    raise ValueError(f"_model_for_level() not callable for level={level!r}")


def _extract_text_excerpt(soup: BeautifulSoup, html: str, cap: int) -> str:
    """從 soup 抽 visible text；fallback 到 raw html。"""
    if soup is not None:
        try:
            text = soup.get_text(separator="\n", strip=True)
        except Exception:
            text = html or ""
    else:
        text = html or ""
    return text[:cap]


def _format_kb_context(kb_context: list[dict] | None) -> str:
    if not kb_context:
        return "無 KB context（caller 未提供或 KB 查無結果）"
    lines: list[str] = []
    for item in kb_context[:8]:
        title = item.get("title", "?")
        path = item.get("path", "?")
        reason = item.get("relevance_reason", "")
        lines.append(f"- [[{path}]] ({title}) — {reason}")
    return "\n".join(lines)


def _format_compliance(compliance_findings: dict[str, Any] | None) -> str:
    if not compliance_findings:
        return "無 compliance pre-scan 資料（caller 未注入）"
    flags = []
    if compliance_findings.get("medical_claim"):
        flags.append("medical_claim=True")
    if compliance_findings.get("absolute_assertion"):
        flags.append("absolute_assertion=True")
    matched = compliance_findings.get("matched_terms") or []
    matched_str = "、".join(matched[:10]) if matched else "無命中"
    flag_str = " / ".join(flags) if flags else "全綠"
    return f"flags: {flag_str}；matched_terms: {matched_str}"


def _build_system_prompt() -> str:
    return (
        "You are an SEO semantic auditor for Health & Wellness blog content "
        "in Taiwan zh-TW context. You evaluate 12 rules in a single batch and "
        "output strict JSON only — no preamble, no markdown fence. Each rule "
        'returns {"status": "pass"|"warn"|"fail"|"skip", "actual": "...", '
        '"fix_suggestion": "..."}. Use "skip" when the data needed for the '
        "rule is missing. Keep `actual` and `fix_suggestion` concise, in "
        "繁體中文，each ≤ 60 字。"
        # L9 SEED caveat — see references/check-rule-catalog.md L9/L10 caveats.
        # SEED 詞庫只 6 條，LLM 補抓僅供參考，所以 fix_suggestion 要掛這個尾巴
        # 提醒讀者目前是 Phase 1 SEED 限制。
        "\nSpecial rule for L9 (台灣藥事法): always append '（SEED scan；醫療"
        "詞庫升級中）' to its fix_suggestion (whatever the status), so report "
        "readers know this rule still has SEED-only coverage."
    )


def _build_user_prompt(
    *,
    url: str,
    focus_keyword: str | None,
    text_excerpt: str,
    kb_context_str: str,
    compliance_str: str,
) -> str:
    rules_block = "\n".join(
        f"{r['id']}. {r['name']} — expected: {r['expected']}; intent: {r['intent']}" for r in _RULES
    )
    fk = focus_keyword or "(未指定 — focus keyword 相關規則回 status=skip)"
    return (
        f"URL: {url}\n"
        f"Focus keyword: {fk}\n"
        f"Article visible text excerpt (前 {len(text_excerpt)} 字):\n"
        f"{text_excerpt}\n\n"
        f"KB context (similar pages from Robin KB, purpose=seo_audit):\n"
        f"{kb_context_str}\n\n"
        f"Compliance pre-scan (from shared.compliance.scan_text):\n"
        f"{compliance_str}\n\n"
        f"Rules to evaluate:\n{rules_block}\n\n"
        'Return JSON of shape {"L1": {"status":"...","actual":"...",'
        '"fix_suggestion":"..."}, "L2": {...}, ...} with all 12 rule IDs '
        "present."
    )


def _skip_check(rule: dict[str, str], reason: str) -> AuditCheck:
    return AuditCheck(
        rule_id=rule["id"],
        name=rule["name"],
        category=rule["category"],  # type: ignore[arg-type]
        severity=rule["severity"],  # type: ignore[arg-type]
        status="skip",
        actual=reason,
        expected=rule["expected"],
        fix_suggestion="",
    )


def _all_skipped(reason: str) -> list[AuditCheck]:
    return [_skip_check(r, reason) for r in _RULES]


def _parse_response_json(text: str) -> dict[str, Any] | None:
    """LLM 回傳常被 markdown fence 或 prose 包住；regex 抓第一個 JSON object。"""
    fenced = re.search(r"```(?:json)?\s*(\{[\s\S]*\})\s*```", text)
    candidate = fenced.group(1) if fenced else None
    if candidate is None:
        bare = re.search(r"\{[\s\S]*\}", text)
        candidate = bare.group(0) if bare else None
    if candidate is None:
        return None
    try:
        parsed = json.loads(candidate)
    except json.JSONDecodeError:
        return None
    if not isinstance(parsed, dict):
        return None
    return parsed


_VALID_STATUSES: frozenset[str] = frozenset({"pass", "warn", "fail", "skip"})


def _coerce_status(raw: Any) -> str:
    if isinstance(raw, str) and raw in _VALID_STATUSES:
        return raw
    return "skip"


def _build_check(rule: dict[str, str], entry: dict[str, Any] | None) -> AuditCheck:
    if not isinstance(entry, dict):
        return _skip_check(rule, "LLM omitted")
    status = _coerce_status(entry.get("status"))
    actual_raw = entry.get("actual")
    actual = str(actual_raw).strip() if actual_raw is not None else ""
    fix_raw = entry.get("fix_suggestion") or entry.get("fix") or ""
    fix = str(fix_raw).strip() if fix_raw else ""
    if not actual:
        actual = "LLM omitted"
        # 若 LLM 給了 status pass/warn/fail 但沒 actual，視為輸出殘缺 → skip
        if status != "skip":
            status = "skip"
    return AuditCheck(
        rule_id=rule["id"],
        name=rule["name"],
        category=rule["category"],  # type: ignore[arg-type]
        severity=rule["severity"],  # type: ignore[arg-type]
        status=status,  # type: ignore[arg-type]
        actual=actual,
        expected=rule["expected"],
        fix_suggestion=fix,
    )


def review(
    soup: BeautifulSoup | None,
    html: str,
    focus_keyword: str | None,
    *,
    url: str = "",
    kb_context: list[dict] | None = None,
    compliance_findings: dict[str, Any] | None = None,
    model: LLMLevel = "sonnet",
    text_excerpt_chars: int = _HTML_EXCERPT_CHARS,
) -> list[AuditCheck]:
    """執行 12 條 LLM semantic check，回傳 AuditCheck list（順序 L1-L12）。

    Args:
        soup: BeautifulSoup 解析結果（可為 None — fetch 失敗時整批 skip）。
        html: 原始 HTML（soup 抽 text 失敗時 fallback 來源）。
        focus_keyword: 焦點關鍵字；None 時 L1/L2/L3/L4 仍跑但 LLM 會自行判斷
            為 skip（actual 標明）。
        url: 目標 URL（注入 prompt 上下文用）。
        kb_context: caller 從 `search_kb(..., purpose="seo_audit")` 拿到的
            list[dict]；None / 空 list 會讓 L10 internal link 部分降級提醒。
        compliance_findings: caller 從 `shared.compliance.scan_text(text)`
            拿到的 dict（`{medical_claim, absolute_assertion, matched_terms}`）；
            None 會讓 L9 LLM 看不到 scan 結果但仍會直接判斷。
        model: "sonnet" / "haiku" / "none"。"none" 直接回 12 條全 skip。
        text_excerpt_chars: HTML excerpt 上限字數（成本控制）。

    Returns:
        list[AuditCheck]，長度 == 12，順序與 `_RULES` 一致。
    """
    if model == "none":
        return _all_skipped("LLM semantic check disabled (--llm-level=none)")
    if soup is None and not html:
        return _all_skipped("頁面 fetch 失敗，無內容可 audit")

    text_excerpt = _extract_text_excerpt(soup, html, text_excerpt_chars)
    kb_str = _format_kb_context(kb_context)
    compliance_str = _format_compliance(compliance_findings)

    system = _build_system_prompt()
    user = _build_user_prompt(
        url=url,
        focus_keyword=focus_keyword,
        text_excerpt=text_excerpt,
        kb_context_str=kb_str,
        compliance_str=compliance_str,
    )

    set_current_agent("brook")  # SEO audit 暫掛 brook，cost tracking 一致
    # Use shared.anthropic_client.ask_claude wrapper so `record_api_call` fires
    # (the previous direct `client.messages.create` skipped cost tracking — see
    # follow-up A3 in project_seo_d2_f_merged_2026_04_26.md).
    try:
        text = ask_claude(
            user,
            system=system,
            model=_model_for_level(model),
            max_tokens=_MAX_OUTPUT_TOKENS,
        )
    except Exception as e:
        logger.warning("llm_review_call_failed err=%s", e)
        return _all_skipped(f"LLM API error: {type(e).__name__}")

    parsed = _parse_response_json(text)
    if parsed is None:
        logger.warning("llm_review_parse_failed text_head=%s", text[:200])
        return _all_skipped("LLM JSON parse 失敗")

    checks: list[AuditCheck] = []
    for rule in _RULES:
        entry = parsed.get(rule["id"])
        checks.append(_build_check(rule, entry))
    return checks


__all__ = ["LLMLevel", "review"]
