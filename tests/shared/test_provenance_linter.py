"""Centaur N520 — provenance linter scaffolding (red lines 2 & 5).

N520 freezes the interface + the atomic ``is_terminal_evidence`` rule; full
per-claim enforcement is deferred to N524. These tests lock the contract so a
future task can't silently regress it (panel Codex §2 / Gemini §3).
"""

from __future__ import annotations

import pytest

from shared.provenance_linter import (
    ENFORCED_RED_LINES,
    ProvenanceLinter,
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


def test_linter_is_deferred_at_n520():
    report = ProvenanceLinter().lint_page("KB/Wiki/Concepts/x.md", "body")
    assert report.status == "deferred"
    assert report.ok is True  # deferred ≠ violation
    assert report.findings  # carries an explanatory placeholder finding


def test_enforced_red_lines_are_2_and_5():
    assert ENFORCED_RED_LINES == (2, 5)
