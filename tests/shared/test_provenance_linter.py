"""Centaur — provenance linter (red lines 2 & 5).

N520 froze the interface + the atomic ``is_terminal_evidence`` rule with a
``deferred`` stub. N524 upgrades ``lint_page`` to real enforcement: red line 5
(Concept/Output terminal evidence must cite Sources/Raw/Annotations, never
another Concept/Output). These tests lock both the atomic rule and the
adversarial enforcement so a future task can't silently regress it.
"""

from __future__ import annotations

import pytest

from shared.provenance_linter import (
    ENFORCED_RED_LINES,
    ProvenanceLinter,
    ProvenanceViolation,
    extract_evidence_citations,
    is_terminal_evidence,
)


@pytest.mark.parametrize(
    "path,expected",
    [
        ("KB/Raw/Articles/foo.md", True),
        ("KB/Annotations/bar.md", True),
        ("KB/Wiki/Sources/baz.md", True),
        ("KB/Wiki/Concepts/overtraining.md", False),  # red line 5 — derived layer
        ("KB/Wiki/Outputs/some-query.md", False),
        (r"KB\Raw\Articles\win.md", True),  # backslash-normalized
    ],
)
def test_is_terminal_evidence(path, expected):
    assert is_terminal_evidence(path) is expected


def test_enforced_red_lines_are_2_and_5():
    assert ENFORCED_RED_LINES == (2, 5)


# ---------------------------------------------------------------------------
# lint_page — scope: only Concept / Output pages are in-scope (red line 5)
# ---------------------------------------------------------------------------


def test_lint_skips_non_concept_pages():
    """Source / Literature / Raw pages are not bound by red line 5 → skipped."""
    report = ProvenanceLinter().lint_page(
        "KB/Wiki/Sources/foo.md",
        "## Sources\n\n- [[Concepts/bar]]\n",
    )
    assert report.status == "skipped"
    assert report.ok is True


def test_lint_is_no_longer_deferred():
    """N520 deferred stub is gone — concept pages get real checks."""
    report = ProvenanceLinter().lint_page("KB/Wiki/Concepts/x.md", "## Definition\n\nbody\n")
    assert report.status == "clean"  # checked, no violation — NOT "deferred"


# ---------------------------------------------------------------------------
# RED LINE 5 — adversarial enforcement (the most important correctness point)
# ---------------------------------------------------------------------------


def test_concept_citing_another_concept_in_sources_rejected():
    """A Concept whose ## Sources cites another Concept → violation (wiki self-feed)."""
    body = "## Definition\n\n睡眠壓力是腺苷累積。^p-3\n\n## Sources\n\n- [[Concepts/adenosine]]\n"
    report = ProvenanceLinter().lint_page("KB/Wiki/Concepts/sleep-pressure.md", body)
    assert report.status == "violations"
    assert report.ok is False
    assert len(report.findings) == 1
    assert report.findings[0].red_line == 5
    assert "Concepts/adenosine" in report.findings[0].citation


def test_concept_citing_an_output_in_sources_rejected():
    body = "## Definition\n\nx\n\n## Sources\n\n- [[Outputs/2026-01-01-query]]\n"
    report = ProvenanceLinter().lint_page("KB/Wiki/Concepts/foo.md", body)
    assert report.status == "violations"
    assert report.findings[0].red_line == 5


def test_concept_citing_sources_raw_annotations_passes():
    """Terminal evidence pointing to Sources / Raw / Annotations → clean."""
    body = (
        "## Definition\n\nx\n\n"
        "## Sources\n\n"
        "- [[Sources/colleen-carney-cbti]]\n"
        "- [[Raw/Articles/sleep-2024]]\n"
        "- [[Annotations/sleep-2024]]\n"
    )
    report = ProvenanceLinter().lint_page("KB/Wiki/Concepts/foo.md", body)
    assert report.status == "clean"
    assert report.findings == []


def test_related_concepts_section_is_not_evidence():
    """Concept→Concept links in ## Related Concepts are legit relations, NOT evidence."""
    body = (
        "## Definition\n\nx\n\n"
        "## Related Concepts\n\n- [[Concepts/adenosine]]\n- [[Concepts/circadian-rhythm]]\n\n"
        "## Sources\n\n- [[Sources/foo]]\n"
    )
    report = ProvenanceLinter().lint_page("KB/Wiki/Concepts/foo.md", body)
    assert report.status == "clean", "related-concept links must not trip red line 5"


def test_bare_stem_wikilink_is_unknown_not_violation():
    """`[[foo]]` (no sub-dir prefix) can't be classified → not a violation (no false positive)."""
    body = "## Sources\n\n- [[colleen-carney-cbti]]\n"
    report = ProvenanceLinter().lint_page("KB/Wiki/Concepts/foo.md", body)
    assert report.status == "clean"


def test_mentioned_in_frontmatter_citing_concept_rejected():
    """frontmatter mentioned_in pointing to a Concept → violation."""
    report = ProvenanceLinter().lint_page(
        "KB/Wiki/Concepts/foo.md",
        "## Definition\n\nx\n",
        mentioned_in=["[[Sources/legit]]", "[[Concepts/laundered]]"],
    )
    assert report.status == "violations"
    assert any("Concepts/laundered" in f.citation for f in report.findings)


def test_source_line_citing_concept_rejected():
    """promotion_renderer-style `source: ` line pointing to a Concept → violation."""
    body = (
        "## Evidence\n\n"
        "- **excerpt** `^p-1` (confidence=0.9)\n"
        "  > 引文\n"
        "  source: `KB/Wiki/Concepts/derived`\n"
    )
    report = ProvenanceLinter().lint_page("KB/Wiki/Outputs/q.md", body)
    assert report.status == "violations"
    assert report.findings[0].red_line == 5


def test_output_page_is_in_scope():
    """Outputs/ pages are bound by red line 5 too (not just Concepts/)."""
    body = "## Sources\n\n- [[Concepts/foo]]\n"
    report = ProvenanceLinter().lint_page("KB/Wiki/Outputs/2026-q.md", body)
    assert report.status == "violations"


# ---------------------------------------------------------------------------
# extract_evidence_citations — only evidence positions, not relation links
# ---------------------------------------------------------------------------


def test_extract_only_evidence_positions():
    body = (
        "## Definition\n\nclaim with [[Concepts/inline]] link\n\n"
        "## Related Concepts\n\n- [[Concepts/related]]\n\n"
        "## Sources\n\n- [[Sources/real]]\n- [[Concepts/bad]]\n"
    )
    cites = extract_evidence_citations(body)
    # Only the two links inside ## Sources; Definition + Related Concepts excluded.
    assert cites == ["Sources/real", "Concepts/bad"]


def test_provenance_violation_carries_report():
    body = "## Sources\n\n- [[Concepts/bad]]\n"
    report = ProvenanceLinter().lint_page("KB/Wiki/Concepts/x.md", body)
    exc = ProvenanceViolation(report)
    assert exc.report is report
    assert "Concepts/bad" in str(exc)
