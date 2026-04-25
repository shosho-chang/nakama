#!/usr/bin/env python3
"""Validate a single commit / PR title against the project's conventional commit
shape: ``<type>(<scope>): <description>`` (scope optional).

Allowed types map roughly to feedback_dev_workflow.md categories:
  feat / fix / docs / chore / refactor / test / ci / perf / style / revert / memory

Rejects (exits 1):
  - "added new feature" (no type prefix)
  - "feat: " (empty description)
  - "FEAT: x" (uppercase type)
  - "feat (api): x" (space before scope paren)
  - "feat(api):x" (no space after colon)

Accepts:
  - "feat: add Brook compose endpoint"
  - "fix(usopp): handle WP rate-limit retries"
  - "docs(runbook): clarify R2 token rotation"
  - "memory: feedback_decision_questionnaire"

Usage:
    python3 .github/scripts/lint_commit_title.py "<title>"
"""

from __future__ import annotations

import re
import sys

ALLOWED_TYPES = {
    "feat",
    "fix",
    "docs",
    "chore",
    "refactor",
    "test",
    "ci",
    "perf",
    "style",
    "revert",
    "memory",  # Nakama-specific — memory file changes
    "build",
    "cleanup",  # Nakama-specific — drive-by cleanups
}

# <type>[(<scope>)]: <description>
# - type: lowercase letters
# - scope: optional, parens with non-empty content, no leading/trailing space
# - colon + single space + non-empty description
PATTERN = re.compile(r"^(?P<type>[a-z]+)(?:\((?P<scope>[a-z0-9][a-z0-9_/-]*)\))?: (?P<desc>\S.*)$")


def lint(title: str) -> tuple[bool, str]:
    """Return (ok, error_message). Empty error_message on success."""
    if not title.strip():
        return False, "title is empty"

    match = PATTERN.match(title)
    if not match:
        return False, (
            f"title does not match `<type>(<scope>): <desc>` pattern.\n"
            f"  got:      {title!r}\n"
            f"  examples: 'feat: add foo' / 'fix(usopp): WP retry' / 'docs(runbook): X'\n"
            f"  allowed types: {', '.join(sorted(ALLOWED_TYPES))}"
        )

    commit_type = match.group("type")
    if commit_type not in ALLOWED_TYPES:
        return False, (f"unknown type {commit_type!r}; allowed: {', '.join(sorted(ALLOWED_TYPES))}")

    desc = match.group("desc")
    if len(desc) < 3:
        return False, f"description too short: {desc!r}"

    return True, ""


def main(argv: list[str]) -> int:
    if len(argv) != 2:
        print(f"usage: {argv[0]} '<commit-title>'", file=sys.stderr)
        return 2
    title = argv[1]
    ok, err = lint(title)
    if ok:
        print(f"OK: {title}")
        return 0
    print(f"FAIL: {err}", file=sys.stderr)
    return 1


if __name__ == "__main__":
    sys.exit(main(sys.argv))
