"""Taiwan health content regulatory vocabulary + scanner (ADR-005b §10).

Scans DraftV1 title + content for terms that trigger Taiwan 《藥事法》/
《醫療法》/《食品安全衛生管理法》risk categories. Returns a
PublishComplianceGateV1 reporting which flags (if any) are set.

Vocab sources:
    - 藥事法 §69 §70（非藥品不得宣稱療效）
    - 衛福部健康食品管理法第十四條（不得虛偽或涉及醫療效能）
    - 食品標示宣傳廣告管理指引（不得有療效、預防疾病、誇張、易生誤解之宣稱）

Design:
    - Simple substring matching (case-insensitive for latin chars).
    - CJK terms are matched literally; no word boundary since Chinese has no
      whitespace. A substring hit is sufficient evidence to warrant HITL review.
    - Scan the AST text content (not raw_html) to avoid matching HTML markup
      like `class="treatment"`.

Phase 2 candidates (not in this module):
    - LLM-based paraphrase detection ("幫助入睡" close to "治療失眠")
    - Context-aware scoring (negation / disclaimer proximity)
"""

from __future__ import annotations

from shared.schemas.publishing import BlockNodeV1, DraftV1, PublishComplianceGateV1

# ---------------------------------------------------------------------------
# Vocab (categorized)
# ---------------------------------------------------------------------------

# 療效 / 診斷 / 藥物類比詞彙 — 命中即 medical_claim=True
MEDICAL_CLAIM_TERMS: dict[str, list[str]] = {
    "therapeutic": [
        "治療",
        "治癒",
        "療效",
        "療程",
        "根治",
        "徹底治好",
        "痊癒",
        "療法",
    ],
    "diagnostic": [
        "診斷",
        "確診",
        "檢驗出",
    ],
    "drug_analog": [
        "特效",
        "神奇",
        "專治",
        "替代藥物",
        "藥到病除",
        "速效",
    ],
    "disease_prevention": [
        "預防癌症",
        "預防心臟病",
        "預防糖尿病",
        "抗癌",
        "防癌",
    ],
}

# 絕對斷言 — 命中即 absolute_assertion=True
ABSOLUTE_ASSERTION_TERMS: list[str] = [
    "百分之百",
    "100%",
    "百分百",
    "保證",
    "無副作用",
    "絕對有效",
    "絕對安全",
    "完全沒有副作用",
    "一定能",
    "一定會",
    "永遠不會",
]


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def scan(draft: DraftV1) -> PublishComplianceGateV1:
    """Scan draft title + AST content for compliance-risky vocab.

    Args:
        draft: DraftV1 from approval queue (Brook-produced).

    Returns:
        PublishComplianceGateV1 with matched_terms sorted and deduplicated.
    """
    haystack = f"{draft.title}\n{_ast_text(draft.content.ast)}"
    return scan_text(haystack)


def scan_text(text: str) -> PublishComplianceGateV1:
    """Scan arbitrary text (exposed for Brook compose-time use)."""
    lowered = text.lower()

    medical_hits: list[str] = []
    for terms in MEDICAL_CLAIM_TERMS.values():
        for t in terms:
            if _matches(t, text, lowered):
                medical_hits.append(t)

    absolute_hits: list[str] = []
    for t in ABSOLUTE_ASSERTION_TERMS:
        if _matches(t, text, lowered):
            absolute_hits.append(t)

    return PublishComplianceGateV1(
        medical_claim=bool(medical_hits),
        absolute_assertion=bool(absolute_hits),
        matched_terms=sorted(set(medical_hits + absolute_hits)),
    )


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _matches(term: str, original: str, lowered: str) -> bool:
    """Substring match, case-insensitive for latin chars, literal for CJK."""
    if term.isascii():
        return term.lower() in lowered
    return term in original


def _ast_text(nodes: list[BlockNodeV1]) -> str:
    """Recursively collect .content from Gutenberg AST nodes."""
    parts: list[str] = []

    def walk(ns: list[BlockNodeV1]) -> None:
        for n in ns:
            if n.content:
                parts.append(n.content)
            if n.children:
                walk(n.children)

    walk(nodes)
    return "\n".join(parts)
