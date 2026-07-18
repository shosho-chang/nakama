"""The PubMed editor-scoring rubric, as reference data for the Bridge UI.

Robin scores each paper on six 1–5 dimensions but only persists the *numbers*
(plus one overall Verdict/Why) — never a per-dimension justification. To let a
reader understand *what a given score means*, we surface the rubric's own
definition of that score level next to the number. This is sourced, not
invented (``嚴禁幻想``): every string below is copied from the canonical prompt.

Source of truth: ``prompts/robin/pubmed_digest/score.md`` → "六維度評分" section.
Keep in sync if that rubric changes. The breakdown code ``R/I/C/A/F/N`` in a
digest's ``Score`` line maps to the six ``code`` values here, in this order.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional


@dataclass(frozen=True)
class ScoreDimension:
    code: str  # single-letter breakdown code (R/I/C/A/F/N)
    label: str  # "Rigor 嚴謹度"
    measures: str  # one-line "what this dimension evaluates"
    reverse: bool  # True → higher score = fewer problems (Red Flags)
    levels: dict[int, str]  # 1..5 → what that score level means


PUBMED_DIMENSIONS: tuple[ScoreDimension, ...] = (
    ScoreDimension(
        code="R",
        label="Rigor 嚴謹度",
        measures="研究設計與方法學",
        reverse=False,
        levels={
            5: "大型 pre-registered RCT 或高品質 systematic review／meta-analysis，多重敏感度分析",
            4: "中型 RCT、大型 prospective cohort、品質良好的 systematic review",
            3: "小型 RCT、cross-sectional with proper adjustment、有明確假設的 pilot study",
            2: "無對照組、小 N、retrospective 且缺乏穩健校正",
            1: "case series、軼事、重大方法學瑕疵",
        },
    ),
    ScoreDimension(
        code="I",
        label="Impact 影響力",
        measures="期刊 tier ＋ 研究問題重要性 ＋ 潛在引用力",
        reverse=False,
        levels={
            5: "Nature／Cell／Lancet／NEJM 等頂刊的突破性研究",
            4: "Q1 領域頂刊的重要發現",
            3: "Q1／Q2 標準發表",
            2: "中段期刊的漸進式研究",
            1: "低 tier 或牙縫研究",
        },
    ),
    ScoreDimension(
        code="C",
        label="Clinical Relevance 臨床關聯",
        measures="結論能否影響真實病患／讀者的決策",
        reverse=False,
        levels={
            5: "足以改變臨床指引的等級（large RCT with hard endpoints）",
            4: "明確的人體證據，effect size 落在臨床有意義範圍",
            3: "人體 observational 或 small RCT，需要更多驗證",
            2: "動物研究但機制清楚，有轉譯潛力",
            1: "體外研究、動物 pilot、純機制探索",
        },
    ),
    ScoreDimension(
        code="A",
        label="Actionability 實用性",
        measures="讀者能否把結論轉成生活方式調整",
        reverse=False,
        levels={
            5: "清楚告訴讀者「做什麼、做多少、多久見效」",
            4: "方向清楚但細節需要補充",
            3: "知道原則但具體操作不明",
            2: "只能當背景知識，無法具體行動",
            1: "純學術、無 actionable insight",
        },
    ),
    ScoreDimension(
        code="F",
        label="Red Flags 警訊",
        measures="反向分：5＝無警訊，1＝嚴重問題",
        reverse=True,
        levels={
            5: "未揭露明顯弱點",
            4: "有小幅限制（樣本屬性偏窄、single-center 等）",
            3: "有明顯限制但作者有承認",
            2: "方法學薄弱或利益衝突明顯",
            1: "工業界贊助未揭露、動物研究被過度外推、surrogate endpoint 當成 hard endpoint",
        },
    ),
    ScoreDimension(
        code="N",
        label="Novelty 新穎度",
        measures="相對既有文獻的原創程度",
        reverse=False,
        levels={
            5: "推翻既有共識、提出全新機制",
            4: "顯著推進機制理解或揭示新次族群效應",
            3: "在現有框架中補齊證據",
            2: "重複驗證已知結論",
            1: "完全是 me-too 研究",
        },
    ),
)


@dataclass(frozen=True)
class ScoreRow:
    code: str
    label: str
    measures: str
    score: Optional[int]  # None when the digest didn't record this dimension
    level_def: str  # rubric meaning of ``score`` (empty if score unknown)
    reverse: bool


def build_score_rows(study) -> list[ScoreRow]:
    """Zip a parsed study's per-dimension scores with the rubric definitions.

    Reads ``study.score_dims`` — a tuple of ``(code, value)`` parsed from the
    digest's ``Score`` breakdown. Returns one :class:`ScoreRow` per rubric
    dimension present in the study, in canonical R/I/C/A/F/N order. Returns
    ``[]`` when the study carries no dimension breakdown.
    """
    dims = getattr(study, "score_dims", ()) or ()
    scores = {code: value for code, value in dims}
    rows: list[ScoreRow] = []
    for dim in PUBMED_DIMENSIONS:
        if dim.code not in scores:
            continue
        value = scores[dim.code]
        rows.append(
            ScoreRow(
                code=dim.code,
                label=dim.label,
                measures=dim.measures,
                score=value,
                level_def=dim.levels.get(value, ""),
                reverse=dim.reverse,
            )
        )
    return rows
