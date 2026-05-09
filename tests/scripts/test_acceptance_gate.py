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
    compute_acceptance_7,
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


# ---------------------------------------------------------------------------
# 7-condition file-based acceptance gate (issue #502, P0.5)
# ---------------------------------------------------------------------------


def _make_source_page(
    tmp_path: Path, *, fm_wikilinks: list[str], body_wikilinks: list[str]
) -> Path:
    """Write a minimal source page with FM wikilinks_introduced and body appendix."""
    fm_list = "\n".join(f"- {w}" for w in fm_wikilinks)
    body_list = "\n".join(f"- [[{w}]]" for w in body_wikilinks)
    content = (
        f"---\ntitle: Test Chapter\nwikilinks_introduced:\n{fm_list}\n---\n"
        f"## Sec\n\nText.\n\n---\n\n## Section Concept Maps\n\n### Sec\n\nmap\n\n"
        f"## Wikilinks Introduced\n\n{body_list}\n"
    )
    p = tmp_path / "ch1.md"
    p.write_text(content, encoding="utf-8")
    return p


def _valid_concept_page(title: str = "ATP") -> str:
    return (
        f"# {title}\n\n"
        "## Definition\n\n"
        "ATP is a substantive concept page with a concrete definition paragraph.\n\n"
        "## Core Principles\n\n"
        "Additional body content keeps this page out of placeholder territory.\n"
    )


def test_c1_dispatch_log_clean_pass(tmp_path: Path) -> None:
    """C1 passes when dispatch_log has 0 errors."""
    staging = tmp_path / "staging"
    staging.mkdir()
    live = tmp_path / "live"
    live.mkdir()
    page = _make_source_page(tmp_path, fm_wikilinks=[], body_wikilinks=[])
    dispatch_log = [
        {"slug": "atp", "term": "ATP", "level": "L2", "action": "create"},
    ]
    acc = compute_acceptance_7(
        source_page_path=page,
        dispatch_log=dispatch_log,
        staging_concepts_dir=staging,
        live_concepts_dir=live,
    )
    assert acc.c1_dispatch_ok is True
    assert acc.c1_dispatch_errors == []


def test_c1_dispatch_log_error_fail(tmp_path: Path) -> None:
    """C1 fails when dispatch_log contains a dispatch-error entry."""
    staging = tmp_path / "staging"
    staging.mkdir()
    live = tmp_path / "live"
    live.mkdir()
    page = _make_source_page(tmp_path, fm_wikilinks=[], body_wikilinks=[])
    dispatch_log = [
        {"slug": "atp", "term": "ATP", "level": "L2", "action": "dispatch-error", "error": "boom"},
    ]
    acc = compute_acceptance_7(
        source_page_path=page,
        dispatch_log=dispatch_log,
        staging_concepts_dir=staging,
        live_concepts_dir=live,
    )
    assert acc.c1_dispatch_ok is False
    assert len(acc.c1_dispatch_errors) == 1


def test_c2_wikilinks_resolve_pass(tmp_path: Path) -> None:
    """C2 passes when all body [[slug]] concept pages exist in staging."""
    staging = tmp_path / "staging"
    staging.mkdir()
    (staging / "atp.md").write_text("# ATP", encoding="utf-8")
    live = tmp_path / "live"
    live.mkdir()
    page = _make_source_page(tmp_path, fm_wikilinks=["[[atp]]"], body_wikilinks=["atp"])
    acc = compute_acceptance_7(
        source_page_path=page,
        dispatch_log=[],
        staging_concepts_dir=staging,
        live_concepts_dir=live,
    )
    assert acc.c2_wikilinks_resolve_ok is True
    assert acc.c2_unresolved == []


def test_c2_wikilinks_resolve_fail(tmp_path: Path) -> None:
    """C2 fails when a body [[slug]] has no concept page in staging."""
    staging = tmp_path / "staging"
    staging.mkdir()
    # atp.md is NOT created — unresolved
    live = tmp_path / "live"
    live.mkdir()
    page = _make_source_page(tmp_path, fm_wikilinks=["[[atp]]"], body_wikilinks=["atp"])
    acc = compute_acceptance_7(
        source_page_path=page,
        dispatch_log=[],
        staging_concepts_dir=staging,
        live_concepts_dir=live,
    )
    assert acc.c2_wikilinks_resolve_ok is False
    assert "atp" in acc.c2_unresolved


def test_c3_fm_body_count_match_pass(tmp_path: Path) -> None:
    """C3 passes when FM wikilinks_introduced count == body appendix [[…]] count."""
    staging = tmp_path / "staging"
    staging.mkdir()
    live = tmp_path / "live"
    live.mkdir()
    page = _make_source_page(
        tmp_path, fm_wikilinks=["[[atp]]", "[[glucose]]"], body_wikilinks=["atp", "glucose"]
    )
    acc = compute_acceptance_7(
        source_page_path=page,
        dispatch_log=[],
        staging_concepts_dir=staging,
        live_concepts_dir=live,
    )
    assert acc.c3_fm_body_count_ok is True
    assert acc.c3_fm_count == 2
    assert acc.c3_body_count == 2


def test_c3_fm_body_count_mismatch_fail(tmp_path: Path) -> None:
    """C3 fails when FM count != body appendix count."""
    staging = tmp_path / "staging"
    staging.mkdir()
    live = tmp_path / "live"
    live.mkdir()
    # FM says 2, body only has 1
    page = _make_source_page(
        tmp_path, fm_wikilinks=["[[atp]]", "[[glucose]]"], body_wikilinks=["atp"]
    )
    acc = compute_acceptance_7(
        source_page_path=page,
        dispatch_log=[],
        staging_concepts_dir=staging,
        live_concepts_dir=live,
    )
    assert acc.c3_fm_body_count_ok is False
    assert acc.c3_fm_count == 2
    assert acc.c3_body_count == 1


def test_c4_no_live_writes_pass(tmp_path: Path) -> None:
    """C4 passes when dispatched slugs are absent from live KB/Wiki/Concepts/."""
    staging = tmp_path / "staging"
    staging.mkdir()
    live = tmp_path / "live"
    live.mkdir()
    page = _make_source_page(tmp_path, fm_wikilinks=[], body_wikilinks=[])
    dispatch_log = [{"slug": "atp", "term": "ATP", "level": "L2", "action": "create"}]
    # live does NOT have atp.md
    acc = compute_acceptance_7(
        source_page_path=page,
        dispatch_log=dispatch_log,
        staging_concepts_dir=staging,
        live_concepts_dir=live,
    )
    assert acc.c4_no_live_writes_ok is True
    assert acc.c4_live_slugs == []


def test_c4_no_live_writes_fail(tmp_path: Path) -> None:
    """C4 fails when a dispatched slug exists in live KB/Wiki/Concepts/."""
    staging = tmp_path / "staging"
    staging.mkdir()
    live = tmp_path / "live"
    live.mkdir()
    (live / "atp.md").write_text("# ATP", encoding="utf-8")  # leaked to live dir
    page = _make_source_page(tmp_path, fm_wikilinks=[], body_wikilinks=[])
    dispatch_log = [{"slug": "atp", "term": "ATP", "level": "L2", "action": "create"}]
    acc = compute_acceptance_7(
        source_page_path=page,
        dispatch_log=dispatch_log,
        staging_concepts_dir=staging,
        live_concepts_dir=live,
    )
    assert acc.c4_no_live_writes_ok is False
    assert "atp" in acc.c4_live_slugs


def test_c5_no_placeholders_pass(tmp_path: Path) -> None:
    """C5 passes when no concept pages contain placeholder stub text."""
    staging = tmp_path / "staging"
    staging.mkdir()
    (staging / "atp.md").write_text(_valid_concept_page(), encoding="utf-8")
    live = tmp_path / "live"
    live.mkdir()
    page = _make_source_page(tmp_path, fm_wikilinks=[], body_wikilinks=[])
    acc = compute_acceptance_7(
        source_page_path=page,
        dispatch_log=[],
        staging_concepts_dir=staging,
        live_concepts_dir=live,
    )
    assert acc.c5_no_placeholders_ok is True
    assert acc.c5_placeholder_hits == []


def test_c5_no_placeholders_fail(tmp_path: Path) -> None:
    """C5 fails when a concept page contains a placeholder stub pattern."""
    staging = tmp_path / "staging"
    staging.mkdir()
    (staging / "stub.md").write_text(
        "# Stub\n\nWill be enriched in phase-b-reconciliation.", encoding="utf-8"
    )
    live = tmp_path / "live"
    live.mkdir()
    page = _make_source_page(tmp_path, fm_wikilinks=[], body_wikilinks=[])
    acc = compute_acceptance_7(
        source_page_path=page,
        dispatch_log=[],
        staging_concepts_dir=staging,
        live_concepts_dir=live,
    )
    assert acc.c5_no_placeholders_ok is False
    assert any(slug == "stub" for slug, _ in acc.c5_placeholder_hits)


def test_c5_fails_missing_definition_section(tmp_path: Path) -> None:
    """C5 fails structurally unusable concept pages, not just literal placeholders."""
    staging = tmp_path / "staging"
    staging.mkdir()
    (staging / "stub.md").write_text("# Stub\n\nSome body without schema.", encoding="utf-8")
    live = tmp_path / "live"
    live.mkdir()
    page = _make_source_page(tmp_path, fm_wikilinks=[], body_wikilinks=[])
    acc = compute_acceptance_7(
        source_page_path=page,
        dispatch_log=[],
        staging_concepts_dir=staging,
        live_concepts_dir=live,
    )
    assert acc.c5_no_placeholders_ok is False
    assert ("stub", "missing ## Definition") in acc.c5_placeholder_hits


def test_c5_fails_definition_with_embedded_markdown_headings(tmp_path: Path) -> None:
    """Regression: seed-body fallback must not smuggle chapter metadata into Definition."""
    staging = tmp_path / "staging"
    staging.mkdir()
    (staging / "bad-definition.md").write_text(
        "# Bad\n\n## Definition\n\n"
        "The fallback captured metadata --- ### keywords ## 3.1 Intro instead of a definition.\n",
        encoding="utf-8",
    )
    live = tmp_path / "live"
    live.mkdir()
    page = _make_source_page(tmp_path, fm_wikilinks=[], body_wikilinks=[])
    acc = compute_acceptance_7(
        source_page_path=page,
        dispatch_log=[],
        staging_concepts_dir=staging,
        live_concepts_dir=live,
    )
    assert acc.c5_no_placeholders_ok is False
    assert ("bad-definition", "definition contains embedded markdown headings") in (
        acc.c5_placeholder_hits
    )


def test_c6_no_collisions_pass(tmp_path: Path) -> None:
    """C6 passes when dispatched terms have no canonical-slug collisions."""
    staging = tmp_path / "staging"
    staging.mkdir()
    live = tmp_path / "live"
    live.mkdir()
    page = _make_source_page(tmp_path, fm_wikilinks=[], body_wikilinks=[])
    dispatch_log = [
        {"slug": "atp", "term": "ATP", "level": "L2", "action": "create"},
        {"slug": "glucose", "term": "glucose", "level": "L2", "action": "create"},
    ]
    acc = compute_acceptance_7(
        source_page_path=page,
        dispatch_log=dispatch_log,
        staging_concepts_dir=staging,
        live_concepts_dir=live,
    )
    assert acc.c6_no_collisions_ok is True
    assert acc.c6_collision_pairs == []


def test_c6_no_collisions_fail(tmp_path: Path) -> None:
    """C6 fails when two dispatched terms canonicalize to the same slug."""
    staging = tmp_path / "staging"
    staging.mkdir()
    live = tmp_path / "live"
    live.mkdir()
    page = _make_source_page(tmp_path, fm_wikilinks=[], body_wikilinks=[])
    # "Glucose" and "glucose" both canonicalize to "glucose" → collision
    dispatch_log = [
        {"slug": "glucose", "term": "Glucose", "level": "L2", "action": "create"},
        {"slug": "glucose", "term": "glucose", "level": "L2", "action": "noop"},
    ]
    acc = compute_acceptance_7(
        source_page_path=page,
        dispatch_log=dispatch_log,
        staging_concepts_dir=staging,
        live_concepts_dir=live,
    )
    assert acc.c6_no_collisions_ok is False
    assert len(acc.c6_collision_pairs) >= 1


def test_c7_golden_skipped_always_pass(tmp_path: Path) -> None:
    """C7 is skipped (returns True) until golden fixture #501 lands."""
    staging = tmp_path / "staging"
    staging.mkdir()
    live = tmp_path / "live"
    live.mkdir()
    page = _make_source_page(tmp_path, fm_wikilinks=[], body_wikilinks=[])
    acc = compute_acceptance_7(
        source_page_path=page,
        dispatch_log=[],
        staging_concepts_dir=staging,
        live_concepts_dir=live,
    )
    assert acc.c7_golden_ok is True
    assert acc.c7_skipped is True
