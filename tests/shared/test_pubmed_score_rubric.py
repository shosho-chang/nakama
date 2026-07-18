"""Tests for shared.pubmed_score_rubric — zip parsed scores with rubric text."""

from __future__ import annotations

from shared.digest_parser import parse_pubmed_digest
from shared.pubmed_score_rubric import PUBMED_DIMENSIONS, build_score_rows

_SAMPLE = """
### 1. A study

- **Journal**: Cell (Q1 · SJR 24.592)
- **Domain**: `longevity`
- **Score**: 3.6  (R4/I4/C2/A2/F5/N5)
- **Verdict**: v
- **Why**: w
- **→** [PubMed](https://pubmed.ncbi.nlm.nih.gov/42468523/)
"""


def _study():
    return parse_pubmed_digest(_SAMPLE)[0]


def test_rows_follow_canonical_order_with_scores():
    rows = build_score_rows(_study())
    assert [r.code for r in rows] == ["R", "I", "C", "A", "F", "N"]
    assert [r.score for r in rows] == [4, 4, 2, 2, 5, 5]


def test_level_def_matches_rubric_text():
    rows = {r.code: r for r in build_score_rows(_study())}
    # R4 rubric definition
    assert rows["R"].level_def == PUBMED_DIMENSIONS[0].levels[4]
    # C2 rubric definition (clinical relevance, animal mechanism)
    assert "動物研究" in rows["C"].level_def
    # F is reverse-scored; 5 = no red flags
    assert rows["F"].reverse is True
    assert "未揭露明顯弱點" in rows["F"].level_def


def test_labels_are_corrected():
    rows = {r.code: r for r in build_score_rows(_study())}
    assert "Clinical Relevance" in rows["C"].label
    assert "Actionability" in rows["A"].label
    assert "Red Flags" in rows["F"].label


def test_no_breakdown_returns_empty():
    class _Bare:
        score_dims = ()

    assert build_score_rows(_Bare()) == []
