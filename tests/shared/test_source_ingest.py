"""Behavior tests for ``shared.source_ingest.walk_book_to_chapters`` (ADR-020 S1).

Tests use in-memory synthetic raw-markdown fixtures — no LLM calls, no vault access.
Each test probes a specific contract: chapter splitting, verbatim body losslessness,
section anchors, figure detection, table detection, and error paths.
"""

from __future__ import annotations

import textwrap
from pathlib import Path

import pytest

from shared.source_ingest import (
    ChapterPayload,
    FigureRef,
    InlineTable,
    RawMarkdownParseError,
    verbatim_paragraph_match_pct,
    walk_book_to_chapters,
)

# ---------------------------------------------------------------------------
# Fixtures — synthetic raw markdown content
# ---------------------------------------------------------------------------

_FRONTMATTER = """\
---
title: "Biochemistry of Sport and Exercise"
book_id: "bse-2024"
source_epub_path: "/path/to/bse.epub"
converted_date: 2026-05-06
converter_tool: "ebooklib+markdownify"
converter_version: "ebooklib/0.20+markdownify/1.2.2"
---
"""

_RAW_ONE_CHAPTER = (
    _FRONTMATTER
    + """
# Chapter 1 — Energy Systems

## 1.1 Introduction to Bioenergetics

The human body requires a continuous supply of energy.
ATP (adenosine triphosphate) is the primary energy currency of the cell.

The resting metabolic rate is approximately 80 W for a 70 kg adult.

## 1.2 Oxidative Phosphorylation

Oxidative phosphorylation produces approximately 30–32 ATP per glucose molecule.

The mitochondrial inner membrane contains ATP synthase complexes.
"""
)

_RAW_TWO_CHAPTERS = (
    _FRONTMATTER
    + """
# Chapter 1 — Energy Systems

## 1.1 Bioenergetics

The body converts chemical energy to mechanical work.

# Chapter 2 — Carbohydrate Metabolism

## 2.1 Glycolysis

Glycolysis occurs in the cytoplasm and yields 2 net ATP.
"""
)

_RAW_WITH_FIGURE = (
    _FRONTMATTER
    + """
# Chapter 1 — Energy Systems

## 1.1 ATP Structure

ATP consists of adenine, ribose, and three phosphate groups.

![Figure 1.1: ATP structure](Attachments/Books/bse-2024/fig1-1.png)

More text after the figure.
"""
)

_RAW_WITH_TABLE = (
    _FRONTMATTER
    + """
# Chapter 1 — Energy Systems

## 1.1 ATP Yield Comparison

**Table 1.1: ATP yields per substrate**

| Process | ATP Yield |
|---------|-----------|
| Glycolysis | 2 |
| Krebs Cycle | 2 |
| ETC | 28–30 |

Discussion follows.
"""
)

_RAW_THREE_CHAPTERS = (
    _FRONTMATTER
    + """
# Chapter 1 — Intro

First chapter body.

# Chapter 2 — Middle

Second chapter body.

# Chapter 3 — End

Third chapter body.
"""
)

_RAW_NO_CHAPTERS = (
    _FRONTMATTER
    + """
This content has no H1 headings.

Just paragraphs.
"""
)

_RAW_TWO_FIGS = (
    _FRONTMATTER
    + """
# Chapter 5 — Gut Physiology

## 5.1 Small Intestine

The surface area of the small intestine is approximately 250 m².

![Figure 5.1: Villi structure](Attachments/Books/bse-2024/fig5-1.png)

## 5.2 Absorption

Nutrients are absorbed via various transport mechanisms.

![Figure 5.2: Absorption pathways](Attachments/Books/bse-2024/fig5-2.png)
"""
)


def _write_raw(content: str, tmp_path: Path, name: str = "raw.md") -> Path:
    p = tmp_path / name
    p.write_text(content, encoding="utf-8")
    return p


# ---------------------------------------------------------------------------
# Basic split — one chapter
# ---------------------------------------------------------------------------


def test_single_chapter_returns_one_payload(tmp_path):
    p = _write_raw(_RAW_ONE_CHAPTER, tmp_path)
    payloads = walk_book_to_chapters(p)
    assert len(payloads) == 1


def test_single_chapter_index_is_one(tmp_path):
    p = _write_raw(_RAW_ONE_CHAPTER, tmp_path)
    payloads = walk_book_to_chapters(p)
    assert payloads[0].chapter_index == 1


def test_single_chapter_title_extracted(tmp_path):
    p = _write_raw(_RAW_ONE_CHAPTER, tmp_path)
    payloads = walk_book_to_chapters(p)
    assert payloads[0].chapter_title == "Chapter 1 — Energy Systems"


def test_single_chapter_book_id_from_frontmatter(tmp_path):
    p = _write_raw(_RAW_ONE_CHAPTER, tmp_path)
    payloads = walk_book_to_chapters(p)
    assert payloads[0].book_id == "bse-2024"


def test_single_chapter_raw_path_in_payload(tmp_path):
    p = _write_raw(_RAW_ONE_CHAPTER, tmp_path)
    payloads = walk_book_to_chapters(p)
    assert str(p) in payloads[0].raw_path


# ---------------------------------------------------------------------------
# Multi-chapter split
# ---------------------------------------------------------------------------


def test_two_chapters_split(tmp_path):
    p = _write_raw(_RAW_TWO_CHAPTERS, tmp_path)
    payloads = walk_book_to_chapters(p)
    assert len(payloads) == 2


def test_two_chapters_sequential_indices(tmp_path):
    p = _write_raw(_RAW_TWO_CHAPTERS, tmp_path)
    payloads = walk_book_to_chapters(p)
    assert payloads[0].chapter_index == 1
    assert payloads[1].chapter_index == 2


def test_two_chapters_titles_correct(tmp_path):
    p = _write_raw(_RAW_TWO_CHAPTERS, tmp_path)
    payloads = walk_book_to_chapters(p)
    assert payloads[0].chapter_title == "Chapter 1 — Energy Systems"
    assert payloads[1].chapter_title == "Chapter 2 — Carbohydrate Metabolism"


def test_chapter_two_body_does_not_contain_chapter_one_text(tmp_path):
    p = _write_raw(_RAW_TWO_CHAPTERS, tmp_path)
    payloads = walk_book_to_chapters(p)
    assert "Bioenergetics" not in payloads[1].verbatim_body


def test_chapter_one_body_does_not_contain_chapter_two_text(tmp_path):
    p = _write_raw(_RAW_TWO_CHAPTERS, tmp_path)
    payloads = walk_book_to_chapters(p)
    assert "Glycolysis" not in payloads[0].verbatim_body


def test_three_chapters_indices(tmp_path):
    p = _write_raw(_RAW_THREE_CHAPTERS, tmp_path)
    payloads = walk_book_to_chapters(p)
    assert [ch.chapter_index for ch in payloads] == [1, 2, 3]


# ---------------------------------------------------------------------------
# Section anchors
# ---------------------------------------------------------------------------


def test_section_anchors_extracted(tmp_path):
    p = _write_raw(_RAW_ONE_CHAPTER, tmp_path)
    payloads = walk_book_to_chapters(p)
    anchors = payloads[0].section_anchors
    assert "1.1 Introduction to Bioenergetics" in anchors
    assert "1.2 Oxidative Phosphorylation" in anchors


def test_section_anchors_order(tmp_path):
    p = _write_raw(_RAW_ONE_CHAPTER, tmp_path)
    payloads = walk_book_to_chapters(p)
    anchors = payloads[0].section_anchors
    assert anchors.index("1.1 Introduction to Bioenergetics") < anchors.index(
        "1.2 Oxidative Phosphorylation"
    )


def test_section_anchors_not_bleeding_across_chapters(tmp_path):
    p = _write_raw(_RAW_TWO_CHAPTERS, tmp_path)
    payloads = walk_book_to_chapters(p)
    # ch1 should only have its own section
    assert all("2." not in a for a in payloads[0].section_anchors)
    assert all("1." not in a for a in payloads[1].section_anchors)


# ---------------------------------------------------------------------------
# Verbatim body losslessness (BSE ch1 analogue)
# ---------------------------------------------------------------------------


def test_verbatim_body_contains_h1_heading(tmp_path):
    p = _write_raw(_RAW_ONE_CHAPTER, tmp_path)
    payloads = walk_book_to_chapters(p)
    assert "# Chapter 1 — Energy Systems" in payloads[0].verbatim_body


def test_verbatim_body_contains_h2_headings(tmp_path):
    p = _write_raw(_RAW_ONE_CHAPTER, tmp_path)
    payloads = walk_book_to_chapters(p)
    body = payloads[0].verbatim_body
    assert "## 1.1 Introduction to Bioenergetics" in body
    assert "## 1.2 Oxidative Phosphorylation" in body


def test_verbatim_body_contains_all_paragraphs(tmp_path):
    p = _write_raw(_RAW_ONE_CHAPTER, tmp_path)
    payloads = walk_book_to_chapters(p)
    body = payloads[0].verbatim_body
    assert "ATP (adenosine triphosphate) is the primary energy currency" in body
    assert "80 W for a 70 kg adult" in body
    assert "30–32 ATP per glucose molecule" in body
    assert "ATP synthase complexes" in body


def test_verbatim_paragraph_match_pct_gte_99(tmp_path):
    """BSE ch1 analogue: walker preserves ≥ 99% of source paragraphs verbatim."""
    p = _write_raw(_RAW_ONE_CHAPTER, tmp_path)
    payloads = walk_book_to_chapters(p)
    # Build the raw chapter slice (between first and next H1, which is EOF here)
    raw_chapter_content = payloads[0].verbatim_body
    pct = verbatim_paragraph_match_pct(raw_chapter_content, payloads[0].verbatim_body)
    assert pct >= 99.0


def test_verbatim_paragraph_match_pct_helper_detects_loss():
    """Helper correctly returns < 100 when a paragraph is missing."""
    source = "Paragraph one.\n\nParagraph two.\n\nParagraph three."
    extracted = "Paragraph one.\n\nParagraph three."
    pct = verbatim_paragraph_match_pct(source, extracted)
    assert pct < 100.0
    assert pct > 0.0


# ---------------------------------------------------------------------------
# Figure detection
# ---------------------------------------------------------------------------


def test_figure_detected_by_markdown_image_syntax(tmp_path):
    p = _write_raw(_RAW_WITH_FIGURE, tmp_path)
    payloads = walk_book_to_chapters(p)
    figs = payloads[0].figures
    assert len(figs) == 1


def test_figure_vault_path_extracted(tmp_path):
    p = _write_raw(_RAW_WITH_FIGURE, tmp_path)
    payloads = walk_book_to_chapters(p)
    fig = payloads[0].figures[0]
    assert fig.vault_path == "Attachments/Books/bse-2024/fig1-1.png"


def test_figure_alt_text_extracted(tmp_path):
    p = _write_raw(_RAW_WITH_FIGURE, tmp_path)
    payloads = walk_book_to_chapters(p)
    fig = payloads[0].figures[0]
    assert "ATP structure" in fig.alt_text


def test_two_figures_both_detected(tmp_path):
    p = _write_raw(_RAW_TWO_FIGS, tmp_path)
    payloads = walk_book_to_chapters(p)
    figs = payloads[0].figures
    assert len(figs) == 2
    paths = {f.vault_path for f in figs}
    assert "Attachments/Books/bse-2024/fig5-1.png" in paths
    assert "Attachments/Books/bse-2024/fig5-2.png" in paths


def test_no_figures_when_none_present(tmp_path):
    p = _write_raw(_RAW_ONE_CHAPTER, tmp_path)
    payloads = walk_book_to_chapters(p)
    assert payloads[0].figures == []


def test_duplicate_figure_refs_collapsed_by_vault_path(tmp_path):
    """EPUBs that reuse the same inline glyph 100+ times must dedupe — otherwise
    the LLM prompt enumerates all 428 refs and overruns the output budget.
    Regression: MEP ch10 was 428 refs / 34 unique paths.
    """
    raw = (
        _FRONTMATTER
        + """
# Chapter 1 — Equation Garden

![glyph](Attachments/Books/bk/si1.png) integrated over ![glyph](Attachments/Books/bk/si1.png)
equals ![glyph](Attachments/Books/bk/si2.png) times ![glyph](Attachments/Books/bk/si1.png).

![photo](Attachments/Books/bk/fig1.png)
"""
    )
    p = _write_raw(raw, tmp_path)
    figs = walk_book_to_chapters(p)[0].figures
    assert len(figs) == 3  # si1, si2, fig1 — not 5
    assert [f.vault_path for f in figs] == [
        "Attachments/Books/bk/si1.png",
        "Attachments/Books/bk/si2.png",
        "Attachments/Books/bk/fig1.png",
    ]


# ---------------------------------------------------------------------------
# Table detection
# ---------------------------------------------------------------------------


def test_table_detected(tmp_path):
    p = _write_raw(_RAW_WITH_TABLE, tmp_path)
    payloads = walk_book_to_chapters(p)
    tables = payloads[0].tables
    assert len(tables) == 1


def test_table_markdown_content_preserved(tmp_path):
    p = _write_raw(_RAW_WITH_TABLE, tmp_path)
    payloads = walk_book_to_chapters(p)
    tbl = payloads[0].tables[0]
    assert "| Process |" in tbl.markdown
    assert "| Glycolysis |" in tbl.markdown
    assert "| ETC |" in tbl.markdown


def test_table_caption_extracted(tmp_path):
    p = _write_raw(_RAW_WITH_TABLE, tmp_path)
    payloads = walk_book_to_chapters(p)
    tbl = payloads[0].tables[0]
    assert "ATP yields per substrate" in tbl.caption


def test_no_tables_when_none_present(tmp_path):
    p = _write_raw(_RAW_ONE_CHAPTER, tmp_path)
    payloads = walk_book_to_chapters(p)
    assert payloads[0].tables == []


# ---------------------------------------------------------------------------
# No-chapter edge case
# ---------------------------------------------------------------------------


def test_no_chapters_returns_empty_list(tmp_path):
    p = _write_raw(_RAW_NO_CHAPTERS, tmp_path)
    result = walk_book_to_chapters(p)
    assert result == []


# ---------------------------------------------------------------------------
# Detached "Chapter N" caption preceding bare H1 (Elsevier/Saunders style —
# Muscle and Exercise Physiology, Zoladz, 2018)
# ---------------------------------------------------------------------------


_RAW_DETACHED_LABEL = (
    _FRONTMATTER
    + """
# Preface

Some prose.

Chapter 1

# Human Body Composition and Muscle Mass

## 1.1 Introduction

Body composition matters for performance.

Chapter 2

# Functional Morphology of Striated Muscle

## 2.1 Overview

Myofibrils contain sarcomeres.
"""
)


def test_detached_chapter_label_synthesizes_numeric_prefix(tmp_path):
    """`Chapter N` line above a bare H1 should make prefix-mode catch the chapter."""
    p = _write_raw(_RAW_DETACHED_LABEL, tmp_path)
    payloads = walk_book_to_chapters(p)
    # Preface has no preceding `Chapter N`, so prefix-mode drops it.
    assert [pl.chapter_index for pl in payloads] == [1, 2]
    assert payloads[0].chapter_title == "1 Human Body Composition and Muscle Mass"
    assert payloads[1].chapter_title == "2 Functional Morphology of Striated Muscle"


def test_detached_label_does_not_alter_verbatim_body(tmp_path):
    """Synthesized title prefix must not leak into the verbatim_body slice."""
    p = _write_raw(_RAW_DETACHED_LABEL, tmp_path)
    payloads = walk_book_to_chapters(p)
    # The body still starts with the original bare H1 — no synthetic prefix.
    assert payloads[0].verbatim_body.startswith("# Human Body Composition and Muscle Mass")
    assert "# 1 Human Body Composition" not in payloads[0].verbatim_body


def test_existing_numeric_prefix_not_doubled(tmp_path):
    """If H1 already starts with `<digit> Title`, the label lookback should be a no-op."""
    raw = (
        _FRONTMATTER
        + """
Chapter 1

# 1 Energy Sources

Body text.
"""
    )
    p = _write_raw(raw, tmp_path)
    payloads = walk_book_to_chapters(p)
    assert len(payloads) == 1
    assert payloads[0].chapter_title == "1 Energy Sources"  # not "1 1 Energy Sources"


# ---------------------------------------------------------------------------
# Error paths
# ---------------------------------------------------------------------------


def test_file_not_found_raises(tmp_path):
    with pytest.raises(FileNotFoundError):
        walk_book_to_chapters(tmp_path / "nonexistent.md")


def test_missing_frontmatter_raises(tmp_path):
    p = _write_raw("# Chapter 1\n\nNo frontmatter here.\n", tmp_path)
    with pytest.raises(RawMarkdownParseError):
        walk_book_to_chapters(p)


def test_missing_book_id_raises(tmp_path):
    no_book_id = textwrap.dedent("""\
        ---
        title: "Some Book"
        source_epub_path: "/path"
        converted_date: 2026-05-06
        converter_tool: "ebooklib+markdownify"
        ---

        # Chapter 1 — Intro

        Body text.
    """)
    p = _write_raw(no_book_id, tmp_path)
    with pytest.raises(RawMarkdownParseError):
        walk_book_to_chapters(p)


# ---------------------------------------------------------------------------
# ChapterPayload type checks
# ---------------------------------------------------------------------------


def test_payloads_are_chapter_payload_instances(tmp_path):
    p = _write_raw(_RAW_ONE_CHAPTER, tmp_path)
    payloads = walk_book_to_chapters(p)
    assert all(isinstance(ch, ChapterPayload) for ch in payloads)


def test_figures_are_figure_ref_instances(tmp_path):
    p = _write_raw(_RAW_WITH_FIGURE, tmp_path)
    payloads = walk_book_to_chapters(p)
    assert all(isinstance(f, FigureRef) for f in payloads[0].figures)


def test_tables_are_inline_table_instances(tmp_path):
    p = _write_raw(_RAW_WITH_TABLE, tmp_path)
    payloads = walk_book_to_chapters(p)
    assert all(isinstance(t, InlineTable) for t in payloads[0].tables)


# ---------------------------------------------------------------------------
# Prefix mode — real-world textbook with front/back-matter H1s (BSE bug fix)
# ---------------------------------------------------------------------------

_RAW_WITH_FRONT_MATTER = (
    _FRONTMATTER
    + """
# Sport Nutrition

A book about sport nutrition.

# Preface

Acknowledgments and so on.

# 1 Nutrients

Chapter 1 body.

## 1.1 Macronutrients

Carbs, fats, proteins.

# 2 Healthy Eating

Chapter 2 body.

## 2.1 Principles

Eat well.

# 10 Vitamins and Minerals

Chapter 10 body (note: jumps from 2 to 10).

## 10.1 Vitamins

Vitamin A, B, C.

# Index

Index entries.
"""
)


def test_prefix_mode_drops_front_and_back_matter(tmp_path):
    p = _write_raw(_RAW_WITH_FRONT_MATTER, tmp_path)
    payloads = walk_book_to_chapters(p)
    assert len(payloads) == 3
    assert [ch.chapter_title for ch in payloads] == [
        "1 Nutrients",
        "2 Healthy Eating",
        "10 Vitamins and Minerals",
    ]


def test_prefix_mode_chapter_index_from_title_digit(tmp_path):
    p = _write_raw(_RAW_WITH_FRONT_MATTER, tmp_path)
    payloads = walk_book_to_chapters(p)
    assert [ch.chapter_index for ch in payloads] == [1, 2, 10]


def test_prefix_mode_preserves_body(tmp_path):
    """Front-matter content must not leak into the first chapter body."""
    p = _write_raw(_RAW_WITH_FRONT_MATTER, tmp_path)
    payloads = walk_book_to_chapters(p)
    assert "A book about sport nutrition" not in payloads[0].verbatim_body
    assert "Acknowledgments" not in payloads[0].verbatim_body
    assert "Index entries" not in payloads[-1].verbatim_body


def test_ordinal_fallback_when_no_numeric_prefix(tmp_path):
    """Books without numeric-prefix titles keep the legacy ordinal behavior."""
    p = _write_raw(_RAW_TWO_CHAPTERS, tmp_path)
    payloads = walk_book_to_chapters(p)
    assert [ch.chapter_index for ch in payloads] == [1, 2]
