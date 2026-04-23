"""Brook 端的 regex-based 合規檢查（Phase 1 seed）。

產出兩份互補的 schema：
- `DraftComplianceV1`：compose 期 snapshot（「有沒有避開療效、有沒有加免責」）
- `PublishComplianceGateV1`：publish gate scan（Brook enqueue + Usopp claim 各跑一次）

Phase 1 只提供最小 vocabulary。正規 `shared/compliance/medical_claim_vocab.py`
屬桌機 lane（Usopp Slice B），完成後 Brook 會切到 shared 版本並保留相同 API。

使用方式：
    text = draft_content_as_plaintext
    gate = scan_publish_gate(text)
    snapshot = scan_draft_compliance(text)
"""

from __future__ import annotations

import re

from shared.schemas.publishing import DraftComplianceV1, PublishComplianceGateV1

# 療效 / 診斷 / 藥物類比詞彙（Phase 1 seed，桌機 vocab 上線後 deprecate）
MEDICAL_CLAIM_PATTERNS: tuple[str, ...] = (
    r"治癒\s*(?:癌症|慢性病|糖尿病|高血壓|失智|憂鬱)",
    r"根治\s*(?:癌症|慢性病|糖尿病|高血壓|失智|憂鬱)",
    r"(?:保證|一定).{0,4}(?:治好|治療|痊癒)",
    r"替代\s*(?:藥物|處方|醫師|醫療)",
    r"停止\s*(?:服藥|用藥|療程)",
    r"(?:診斷|確診).{0,4}(?:方法|技巧)",
)

# 絕對斷言 / 誇大宣稱
ABSOLUTE_ASSERTION_PATTERNS: tuple[str, ...] = (
    r"100\s*%\s*(?:有效|成功|安全)",
    r"百分之百\s*(?:有效|成功|安全)",
    r"絕對\s*(?:有效|安全|不會|沒有)",
    r"永遠\s*(?:不會|不)\s*(?:生病|失敗|發胖)",
    r"完全\s*(?:沒有)?\s*副作用",
)

# 免責 / 非醫療建議聲明（compose 階段 snapshot 用）
DISCLAIMER_PATTERNS: tuple[str, ...] = (
    r"非\s*醫療\s*建議",
    r"僅供\s*參考",
    r"請\s*(?:諮詢|徵詢|洽詢)\s*(?:醫師|專業)",
    r"不\s*構成\s*(?:醫療|診斷)",
    r"This is not medical advice",
)


def _collect_matches(patterns: tuple[str, ...], text: str) -> list[str]:
    matches: list[str] = []
    for pattern in patterns:
        for m in re.finditer(pattern, text, re.IGNORECASE):
            matches.append(m.group(0))
    return matches


def scan_publish_gate(text: str) -> PublishComplianceGateV1:
    """Publish gate scan — Brook enqueue 前與 Usopp claim 後各執行一次，結果必須一致。"""
    medical_hits = _collect_matches(MEDICAL_CLAIM_PATTERNS, text)
    absolute_hits = _collect_matches(ABSOLUTE_ASSERTION_PATTERNS, text)
    # matched_terms 去重保序，利於 HITL UI 顯示
    seen: set[str] = set()
    ordered_matches: list[str] = []
    for term in medical_hits + absolute_hits:
        if term not in seen:
            seen.add(term)
            ordered_matches.append(term)

    return PublishComplianceGateV1(
        medical_claim=bool(medical_hits),
        absolute_assertion=bool(absolute_hits),
        matched_terms=ordered_matches,
    )


def scan_draft_compliance(text: str) -> DraftComplianceV1:
    """Compose 期 snapshot — 回報「有沒有避開療效、有沒有加免責」。"""
    medical_hits = _collect_matches(MEDICAL_CLAIM_PATTERNS, text)
    disclaimer_hits = _collect_matches(DISCLAIMER_PATTERNS, text)
    # blacklist_hits：medical claim + absolute assertion 都算
    blacklist_hits = medical_hits + _collect_matches(ABSOLUTE_ASSERTION_PATTERNS, text)
    seen: set[str] = set()
    unique_blacklist: list[str] = []
    for hit in blacklist_hits:
        if hit not in seen:
            seen.add(hit)
            unique_blacklist.append(hit)

    return DraftComplianceV1(
        claims_no_therapeutic_effect=not medical_hits,
        has_disclaimer=bool(disclaimer_hits),
        detected_blacklist_hits=unique_blacklist,
    )
