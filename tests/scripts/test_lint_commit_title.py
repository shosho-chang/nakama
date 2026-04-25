"""Tests for .github/scripts/lint_commit_title.py."""

from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

# Load the script as a module since it lives outside `scripts/` package.
_SCRIPT = (
    Path(__file__).resolve().parent.parent.parent / ".github" / "scripts" / "lint_commit_title.py"
)
_spec = importlib.util.spec_from_file_location("lint_commit_title", _SCRIPT)
lint_mod = importlib.util.module_from_spec(_spec)
assert _spec.loader is not None
_spec.loader.exec_module(lint_mod)


@pytest.mark.parametrize(
    "title",
    [
        "feat: add Brook compose endpoint",
        "fix(usopp): handle WP rate-limit retries",
        "docs(runbook): clarify R2 token rotation",
        "memory: feedback_decision_questionnaire",
        "refactor(shared): extract shared/sqlite_integrity",
        "ci: enforce conventional commits",
        "chore(deps): bump anthropic 0.51 -> 0.52",
        "perf(robin): cache Scimago lookup table",
        "fix(brook/compose): truncate over-long topic prefix",  # nested scope
    ],
)
def test_valid_titles_pass(title):
    ok, err = lint_mod.lint(title)
    assert ok is True, f"expected valid but got: {err}"


@pytest.mark.parametrize(
    "title,reason",
    [
        ("added new feature", "missing type prefix"),
        ("FEAT: x", "uppercase type"),
        ("feat:no space", "no space after colon"),
        ("feat (api): x", "space before scope paren"),
        ("feat: ", "empty description"),
        ("feat: ab", "description too short"),
        ("", "empty title"),
        ("nope: hi", "unknown type"),
        ("feat(): x", "empty scope"),
    ],
)
def test_invalid_titles_fail(title, reason):
    ok, err = lint_mod.lint(title)
    assert ok is False, f"expected fail ({reason}) but passed: {title!r}"
    assert err  # non-empty error message


def test_main_returns_0_on_valid_title(capsys):
    rc = lint_mod.main(["lint_commit_title.py", "feat: add x"])
    assert rc == 0
    out = capsys.readouterr().out
    assert "OK" in out


def test_main_returns_1_on_invalid_title(capsys):
    rc = lint_mod.main(["lint_commit_title.py", "added thing"])
    assert rc == 1
    err = capsys.readouterr().err
    assert "FAIL" in err


def test_main_returns_2_on_missing_arg(capsys):
    rc = lint_mod.main(["lint_commit_title.py"])  # no title arg
    assert rc == 2
