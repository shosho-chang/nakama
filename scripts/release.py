"""Compute next semver bump + changelog entry from conventional commits.

Reads `git log <last-tag>..HEAD` titles, parses each as `<type>(<scope>): <desc>`,
groups by commit-type → changelog section, computes next semver:

- BREAKING CHANGE in commit body → major bump
- any `feat:` → minor bump
- any `fix:` → patch bump (default)
- only `docs:` / `chore:` / `test:` etc. → patch bump

Dry-run by default. `--apply` rewrites the `[Unreleased]` block in CHANGELOG.md
to `[X.Y.Z] - YYYY-MM-DD` and prepends a fresh empty `[Unreleased]`. Git tag
step is **intentional manual** — operator reviews diff before tagging:

    git tag -a vX.Y.Z -m "Release notes"
    git push --tags

Usage:
    python scripts/release.py            # preview
    python scripts/release.py --apply    # write CHANGELOG.md
    python scripts/release.py --bump minor  # force bump level, override auto-detect

Exit codes:
    0 — preview / apply succeeded
    1 — no commits since last tag, or git not available
"""

from __future__ import annotations

import argparse
import re
import subprocess
import sys
from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Literal

_REPO_ROOT = Path(__file__).resolve().parent.parent
_CHANGELOG = _REPO_ROOT / "CHANGELOG.md"

# Section ordering matches Keep-a-Changelog conventions
_SECTION_ORDER = ("Added", "Changed", "Fixed", "Deprecated", "Removed", "Security", "Other")

# Conventional-commit-type → CHANGELOG section
_TYPE_TO_SECTION = {
    "feat": "Added",
    "fix": "Fixed",
    "docs": "Changed",
    "refactor": "Changed",
    "perf": "Changed",
    "revert": "Removed",
    "style": "Other",
    "test": "Other",
    "ci": "Other",
    "chore": "Other",
    "build": "Other",
    "memory": "Other",
    "cleanup": "Changed",
}

_TITLE_PATTERN = re.compile(
    r"^(?P<type>[a-z]+)(?:\((?P<scope>[a-z0-9][a-z0-9_/-]*)\))?: (?P<desc>\S.*)$"
)


@dataclass(frozen=True)
class Commit:
    sha: str
    type: str
    scope: str | None
    desc: str
    breaking: bool


@dataclass
class ReleasePlan:
    last_tag: str | None
    next_version: str
    bump_level: Literal["major", "minor", "patch"]
    commits: list[Commit]
    sections: dict[str, list[str]]


# ---- git ---------------------------------------------------------------------


def _git(*args: str) -> str:
    """Run `git ...` in repo root; return stdout. Raise SystemExit on failure."""
    try:
        out = subprocess.run(
            ["git", *args],
            cwd=_REPO_ROOT,
            check=True,
            capture_output=True,
            text=True,
        )
    except FileNotFoundError as exc:
        raise SystemExit(f"git not on PATH: {exc}") from exc
    except subprocess.CalledProcessError as exc:
        raise SystemExit(f"git {' '.join(args)} failed: {exc.stderr.strip()}") from exc
    return out.stdout


def _last_tag() -> str | None:
    """Return latest `vX.Y.Z` tag, or None if no semver tags exist."""
    raw = _git("tag", "--list", "v*", "--sort=-v:refname")
    for line in raw.splitlines():
        line = line.strip()
        if re.fullmatch(r"v\d+\.\d+\.\d+", line):
            return line
    return None


def _commits_since(ref: str | None) -> list[Commit]:
    """Return parsed commits since `ref` (or all commits if ref is None).

    Uses `git log --format=<sha>%x00<title>%x00<body>%x00` with NUL field +
    `%x1e` (ASCII record separator) record terminators so commit bodies with
    newlines don't break parsing.
    """
    rev = f"{ref}..HEAD" if ref else "HEAD"
    raw = _git("log", rev, "--format=%H%x00%s%x00%b%x1e")
    commits: list[Commit] = []
    for record in raw.split("\x1e"):
        record = record.strip("\n")
        if not record:
            continue
        sha, title, body = (record.split("\x00") + ["", ""])[:3]
        title = title.strip()
        match = _TITLE_PATTERN.match(title)
        if not match:
            # Not a conventional commit — surface in "Other" so it doesn't go silent.
            commits.append(
                Commit(sha=sha[:7], type="other", scope=None, desc=title, breaking=False)
            )
            continue
        commits.append(
            Commit(
                sha=sha[:7],
                type=match.group("type"),
                scope=match.group("scope"),
                desc=match.group("desc"),
                breaking="BREAKING CHANGE" in body,
            )
        )
    return commits


# ---- semver ------------------------------------------------------------------


def _compute_bump(
    commits: list[Commit], force: Literal["auto", "major", "minor", "patch"] = "auto"
) -> Literal["major", "minor", "patch"]:
    """Pick semver bump level from commit set or operator override."""
    if force != "auto":
        return force
    if any(c.breaking for c in commits):
        return "major"
    if any(c.type == "feat" for c in commits):
        return "minor"
    return "patch"


def _bump_version(last_tag: str | None, level: Literal["major", "minor", "patch"]) -> str:
    """Bump `vX.Y.Z` (or default `v0.0.0`) by `level`, return next `X.Y.Z` (no v)."""
    base = last_tag or "v0.0.0"
    m = re.fullmatch(r"v(\d+)\.(\d+)\.(\d+)", base)
    if not m:
        raise SystemExit(f"can't parse version from tag {base!r}")
    major, minor, patch = (int(x) for x in m.groups())
    if level == "major":
        major, minor, patch = major + 1, 0, 0
    elif level == "minor":
        minor, patch = minor + 1, 0
    else:
        patch += 1
    return f"{major}.{minor}.{patch}"


# ---- changelog rendering -----------------------------------------------------


def _section_for(commit: Commit) -> str:
    return _TYPE_TO_SECTION.get(commit.type, "Other")


def _format_entry(commit: Commit) -> str:
    """Render one commit as a markdown bullet."""
    scope = f"({commit.scope})" if commit.scope else ""
    breaking = " ⚠️ BREAKING" if commit.breaking else ""
    return f"- {commit.type}{scope}: {commit.desc}{breaking} ({commit.sha})"


def build_release_plan(
    force_bump: Literal["auto", "major", "minor", "patch"] = "auto",
) -> ReleasePlan:
    last = _last_tag()
    commits = _commits_since(last)
    if not commits:
        raise SystemExit(f"no commits since {last or 'repo init'} — nothing to release")

    bump = _compute_bump(commits, force_bump)
    next_v = _bump_version(last, bump)

    sections: dict[str, list[str]] = defaultdict(list)
    for c in commits:
        sections[_section_for(c)].append(_format_entry(c))

    return ReleasePlan(
        last_tag=last,
        next_version=next_v,
        bump_level=bump,
        commits=commits,
        sections=dict(sections),
    )


def render_release_block(plan: ReleasePlan, today_iso: str) -> str:
    """Render `## [X.Y.Z] - YYYY-MM-DD` block from plan.sections."""
    lines = [f"## [{plan.next_version}] - {today_iso}", ""]
    for section in _SECTION_ORDER:
        entries = plan.sections.get(section)
        if not entries:
            continue
        lines.append(f"### {section}")
        lines.extend(entries)
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def apply_to_changelog(plan: ReleasePlan, today_iso: str, path: Path = _CHANGELOG) -> None:
    """Replace `[Unreleased]` block: insert dated release block + fresh `[Unreleased]`."""
    text = path.read_text(encoding="utf-8")
    if "## [Unreleased]" not in text:
        raise SystemExit(f"no [Unreleased] section in {path}")

    new_block = render_release_block(plan, today_iso)
    fresh_unreleased = (
        "## [Unreleased]\n\n"
        "_(no entries yet — added by next `python scripts/release.py`)_\n\n"
        "---\n\n"
    )

    # Replace from "## [Unreleased]" (inclusive) up to next "## [" or end-of-file
    pattern = re.compile(
        r"## \[Unreleased\].*?(?=^## \[|\Z)",
        flags=re.DOTALL | re.MULTILINE,
    )
    new_text = pattern.sub(fresh_unreleased + new_block + "\n---\n\n", text, count=1)

    path.write_text(new_text, encoding="utf-8")


# ---- CLI ---------------------------------------------------------------------


def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="release",
        description="Cut next semver release from conventional commits.",
    )
    p.add_argument(
        "--bump",
        choices=("auto", "major", "minor", "patch"),
        default="auto",
        help="force a particular bump level (default: auto-detect from commits)",
    )
    p.add_argument(
        "--apply",
        action="store_true",
        help="write CHANGELOG.md (default is preview to stdout)",
    )
    p.add_argument(
        "--date",
        default=datetime.now(timezone.utc).strftime("%Y-%m-%d"),
        help="release date (YYYY-MM-DD); default = today UTC",
    )
    return p


def main(argv: list[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    plan = build_release_plan(force_bump=args.bump)

    print(f"Last tag:    {plan.last_tag or '(none)'}")
    print(f"Bump level:  {plan.bump_level}")
    print(f"Next version: v{plan.next_version}")
    print(f"Commits:     {len(plan.commits)}")
    print()
    print("---- Changelog block ----")
    print(render_release_block(plan, args.date))
    print("-------------------------")

    if args.apply:
        apply_to_changelog(plan, args.date)
        print("\n✅ CHANGELOG.md updated.")
        print("\nNext step (manual — review diff first):")
        print(f"  git add CHANGELOG.md && git commit -m 'chore(release): v{plan.next_version}'")
        print(f"  git tag -a v{plan.next_version} -m 'Release v{plan.next_version}'")
        print("  git push origin main && git push --tags")
    else:
        print("\n(dry-run — re-run with --apply to write CHANGELOG.md)")

    return 0


if __name__ == "__main__":
    sys.exit(main())
