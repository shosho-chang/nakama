"""Unit tests for Stage 1c deterministic acceptance gate.

Covers: normalize_for_verbatim_compare, verbatim_match_pct,
section_anchors_match, and compute_acceptance.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from scripts.run_s8_preflight import (
    compute_acceptance,
    normalize_for_verbatim_compare,
    section_anchors_match,
    verbatim_match_pct,
)

# ---------------------------------------------------------------------------
# normalize_for_verbatim_compare
# ---------------------------------------------------------------------------


def test_normalize_strips_chapter_appendix():
    """Patch 3 (2026-05-08): metadata moved from per-section interleave to chapter-end appendix."""
    body = (
        "## Section One\n\nParagraph A.\n\n"
        "## Section Two\n\nParagraph B.\n\n"
        "---\n\n"
        "## Section Concept Maps\n\n"
        "### Section One\n\n"
        "```mermaid\nflowchart LR\n  A --> B\n```\n\n"
        "### Section Two\n\n"
        "```mermaid\nflowchart LR\n  C --> D\n```\n\n"
        "## Wikilinks Introduced\n\n"
        "- [[TermA]]\n"
        "- [[TermB]]\n"
        "- [[TermC]]\n"
    )
    result = normalize_for_verbatim_compare(body)

    assert "## Section Concept Maps" not in result
    assert "## Wikilinks Introduced" not in result
    assert "TermA" not in result
    assert "TermC" not in result
    # Original section content preserved
    assert "Paragraph A." in result
    assert "Paragraph B." in result
    assert "## Section One" in result
    assert "## Section Two" in result


def test_normalize_reverses_v2_figure_transform():
    body = (
        "Some text.\n\n"
        "![[Attachments/Books/bse/fig1-1.png]]\n"
        "*Figure 1.1 The energy continuum*\n\n"
        "More text."
    )
    result = normalize_for_verbatim_compare(body)

    assert "![Figure 1.1 The energy continuum](Attachments/Books/bse/fig1-1.png)" in result
    assert "![[Attachments/Books/bse/fig1-1.png]]" not in result


# ---------------------------------------------------------------------------
# verbatim_match_pct
# ---------------------------------------------------------------------------


def test_verbatim_match_99_pct_pass():
    # 99 paragraphs present + 1 missing → 0.99
    paragraphs = [f"Paragraph {i}." for i in range(100)]
    walker_verbatim = "\n\n".join(paragraphs)
    # page_body has 99 of them (drop the last)
    page_body = "\n\n".join(paragraphs[:99])

    pct = verbatim_match_pct(page_body, walker_verbatim)

    assert pct == pytest.approx(0.99)
    assert pct >= 0.99


def test_verbatim_match_empty_walker_returns_1():
    assert verbatim_match_pct("anything", "") == pytest.approx(1.0)


# ---------------------------------------------------------------------------
# section_anchors_match
# ---------------------------------------------------------------------------


def test_section_anchors_match_exact_order():
    body = "## Energy Sources\n\ntext\n\n## ATP Resynthesis\n\nmore text"
    assert section_anchors_match(body, ["Energy Sources", "ATP Resynthesis"]) is True


def test_section_anchors_match_strict_order():
    body = "## ATP Resynthesis\n\ntext\n\n## Energy Sources\n\nmore text"
    # Permuted order → False
    assert section_anchors_match(body, ["Energy Sources", "ATP Resynthesis"]) is False


def test_section_anchors_match_empty_both():
    assert section_anchors_match("No headings here.", []) is True


def test_section_anchors_match_missing_anchor():
    body = "## Energy Sources\n\ntext"
    assert section_anchors_match(body, ["Energy Sources", "ATP Resynthesis"]) is False


# ---------------------------------------------------------------------------
# compute_acceptance — figures embedded count (rule 3)
# ---------------------------------------------------------------------------


def test_figures_embedded_count_pass():
    # 3 walker figures + 3 [[Attachments/Books/...]] embeds → ok
    page_body = (
        "## Section\n\nText with "
        "[[Attachments/Books/bse/fig1.png]] "
        "[[Attachments/Books/bse/fig2.png]] "
        "[[Attachments/Books/bse/fig3.png]]"
    )
    acc = compute_acceptance(
        page_body=page_body,
        walker_verbatim="## Section\n\nText",
        walker_section_anchors=["Section"],
        walker_figures_count=3,
        wikilinks_introduced=["[[A]]", "[[B]]"],
    )
    assert acc.figures_ok is True
    assert acc.figures_embedded == 3


def test_figures_embedded_count_fail():
    # 3 walker figures but only 2 embeds → fail
    page_body = (
        "## Section\n\nText [[Attachments/Books/bse/fig1.png]] [[Attachments/Books/bse/fig2.png]]"
    )
    acc = compute_acceptance(
        page_body=page_body,
        walker_verbatim="## Section\n\nText",
        walker_section_anchors=["Section"],
        walker_figures_count=3,
        wikilinks_introduced=["[[A]]", "[[B]]"],
    )
    assert acc.figures_ok is False
    assert acc.figures_embedded == 2
    assert acc.figures_expected == 3


# ---------------------------------------------------------------------------
# compute_acceptance — wikilinks dynamic threshold (rule 4)
# ---------------------------------------------------------------------------


def test_wikilinks_dynamic_threshold_pass():
    # 4000-char body → threshold = 4000 // 2000 = 2; 2 wikilinks passes
    page_body = "A" * 4000
    acc = compute_acceptance(
        page_body=page_body,
        walker_verbatim="A",
        walker_section_anchors=[],
        walker_figures_count=0,
        wikilinks_introduced=["[[TermA]]", "[[TermB]]"],
    )
    assert acc.wikilinks_threshold == 2
    assert acc.wikilinks_ok is True


def test_wikilinks_dynamic_threshold_fail():
    # 4000-char body needs ≥ 2 wikilinks; only 1 → fail
    page_body = "A" * 4000
    acc = compute_acceptance(
        page_body=page_body,
        walker_verbatim="A",
        walker_section_anchors=[],
        walker_figures_count=0,
        wikilinks_introduced=["[[TermA]]"],
    )
    assert acc.wikilinks_ok is False
    assert acc.wikilinks_count == 1
    assert acc.wikilinks_threshold == 2


# ---------------------------------------------------------------------------
# compute_acceptance — all-4 pass / fail
# ---------------------------------------------------------------------------


def test_acceptance_pass_all_4():
    walker_verbatim = "## Methods\n\nGlucose is the primary fuel."
    page_body = (
        "## Methods\n\n"
        "Glucose is the primary fuel.\n\n"
        "---\n\n"
        "## Section Concept Maps\n\n"
        "### Methods\n\n"
        "```mermaid\nflowchart LR\n  Glucose --> ATP\n```\n\n"
        "## Wikilinks Introduced\n\n"
        "- [[Glucose]]\n"
    )
    acc = compute_acceptance(
        page_body=page_body,
        walker_verbatim=walker_verbatim,
        walker_section_anchors=["Methods"],
        walker_figures_count=0,
        wikilinks_introduced=["[[Glucose]]"],
    )
    assert acc.verbatim_ok is True
    assert acc.anchors_match is True
    assert acc.figures_ok is True
    assert acc.wikilinks_ok is True
    assert acc.acceptance_pass is True


def test_acceptance_fail_logs_measurements():
    # verbatim fails (totally different text) → acceptance_pass=False, measurements exposed
    walker_verbatim = "## Section\n\nThis specific text must appear."
    page_body = "## Section\n\nCompletely different content here."

    acc = compute_acceptance(
        page_body=page_body,
        walker_verbatim=walker_verbatim,
        walker_section_anchors=["Section"],
        walker_figures_count=0,
        wikilinks_introduced=[],
    )

    assert acc.acceptance_pass is False
    assert acc.verbatim_ok is False
    assert acc.verbatim_match < 0.99
    # The AcceptanceResult dataclass exposes all measurements
    assert hasattr(acc, "verbatim_match")
    assert hasattr(acc, "anchors_match")
    assert hasattr(acc, "figures_embedded")
    assert hasattr(acc, "wikilinks_count")
    assert hasattr(acc, "wikilinks_threshold")
