"""Taiwan health content regulatory vocabulary + scanner (ADR-005b §10).

Scans DraftV1 title + content for terms that trigger Taiwan 《藥事法》/
《醫療法》/《食品安全衛生管理法》risk categories. Returns a
PublishComplianceGateV1 reporting which flags (if any) are set.

Vocab sources:
    - 藥事法 §69 §70（非藥品不得宣稱療效）
    - 衛福部健康食品管理法第十四條（不得虛偽或涉及醫療效能）
    - 食品標示宣傳廣告管理指引（不得有療效、預防疾病、誇張、易生誤解之宣稱）
    - 衛福部 2020 食品標示宣傳或廣告詞句涉及誇張易生誤解或醫療效能認定基準

Design:
    - Simple substring matching (case-insensitive for latin chars).
    - CJK terms are matched literally; no word boundary since Chinese has no
      whitespace. A substring hit is sufficient evidence to warrant HITL review.
    - Scan the AST text content (not raw_html) to avoid matching HTML markup
      like `class="treatment"`.
    - Every 正體中文 entry is mirrored to 簡體中文 (defense in depth:
      Brook outputs 正體 but LLM drift could introduce 簡體; zh-CN search terms
      should never bypass the guardrail).

Phase 2 candidates (not in this module):
    - LLM-based paraphrase detection ("幫助入睡" close to "治療失眠")
    - Context-aware scoring (negation / disclaimer proximity)
"""

from __future__ import annotations

from shared.schemas.publishing import BlockNodeV1, DraftV1, PublishComplianceGateV1

# ---------------------------------------------------------------------------
# 正↔簡 character mirror (limited to CJK characters present in this vocab)
# ---------------------------------------------------------------------------

# The specific non-identity character substitutions that appear in this vocab.
# A full TC↔SC transform would pull in OpenCC; since the vocab is finite we
# mirror explicitly. Identity characters (無需轉換) are NOT listed — the
# fallback in `_to_simplified` returns them unchanged.
_TC_TO_SC: dict[str, str] = {
    "癒": "愈",
    "療": "疗",
    "診": "诊",
    "斷": "断",
    "藥": "药",
    "預": "预",
    "臟": "脏",
    "絕": "绝",
    "對": "对",
    "證": "证",
    "緩": "缓",
    "體": "体",
    "減": "减",
    "壓": "压",
    "膽": "胆",
    "淨": "净",
    "強": "强",
    "復": "复",
    "腦": "脑",
    "點": "点",
    "變": "变",
    "驗": "验",
    "檢": "检",
    "確": "确",
    "徹": "彻",
    "齡": "龄",
    "殺": "杀",
    "頸": "颈",
    "韌": "韧",
    "無": "无",
    "見": "见",
    "會": "会",
    "遠": "远",
    "沒": "没",
    "應": "应",
    "專": "专",
    "當": "当",
    "這": "这",
    "為": "为",
    "發": "发",
    "處": "处",
    "極": "极",
    "覺": "觉",
    "實": "实",
    "樣": "样",
}


def _to_simplified(term: str) -> str:
    """Map a 正體中文 term to its 簡體中文 equivalent using the subset mirror."""
    return "".join(_TC_TO_SC.get(ch, ch) for ch in term)


def _mirror(terms: list[str]) -> list[str]:
    """Return the input list plus the 簡體 mirror of every entry, de-duplicated."""
    out: list[str] = []
    seen: set[str] = set()
    for t in terms:
        for variant in (t, _to_simplified(t)):
            if variant not in seen:
                seen.add(variant)
                out.append(variant)
    return out


# ---------------------------------------------------------------------------
# Vocab (categorized) — 正體 sources; 簡體 variants expanded at module load
# ---------------------------------------------------------------------------

# 療效 / 診斷 / 藥物類比詞彙 — 命中即 medical_claim=True
_MEDICAL_CLAIM_TC: dict[str, list[str]] = {
    "therapeutic": [
        "治療",
        "治癒",
        "療效",
        "療程",
        "療法",
        "療癒",
        "根治",
        "徹底治好",
        "痊癒",
        "完全康復",
        "修復",
        "緩解",
        "舒緩",
        "消除",
        "去除",
        "止痛",
        "消炎",
        "殺菌",
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
        "立即見效",
        "強效",
    ],
    "disease_prevention": [
        "預防癌症",
        "預防心臟病",
        "預防糖尿病",
        "抗癌",
        "防癌",
        "強化免疫",
        "增強免疫力",
        "提升免疫力",
    ],
    "physiological_claim": [
        # 衛福部食品標示指引：對生理機能的療效宣稱
        "降血壓",
        "降血糖",
        "降膽固醇",
        "排毒",
        "淨化",
        "保健",
    ],
}

MEDICAL_CLAIM_TERMS: dict[str, list[str]] = {
    category: _mirror(terms) for category, terms in _MEDICAL_CLAIM_TC.items()
}

# 絕對斷言 — 命中即 absolute_assertion=True
_ABSOLUTE_ASSERTION_TC: list[str] = [
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

ABSOLUTE_ASSERTION_TERMS: list[str] = _mirror(_ABSOLUTE_ASSERTION_TC)


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
    """Recursively collect .content + text-bearing attrs from Gutenberg AST nodes.

    attrs str values are included so scanners catch compliance-risky text
    smuggled into e.g. image block `alt` fields (ADR-005b §10 defense in depth).
    """
    parts: list[str] = []

    def walk(ns: list[BlockNodeV1]) -> None:
        for n in ns:
            if n.content:
                parts.append(n.content)
            for v in n.attrs.values():
                if isinstance(v, str) and v:
                    parts.append(v)
            if n.children:
                walk(n.children)

    walk(nodes)
    return "\n".join(parts)
