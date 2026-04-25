"""Archive old project memories that are stale + unreferenced.

Memory `over-doc` risk grows linearly: project memories accumulate weekly,
many become stale (PR#X merged, ADR superseded, tech debt cleared) but stick
around in `memory/claude/` indefinitely. This script enforces a 90-day rolling
window for `project_*` memories, archiving (not deleting) ones that are both:

1. Older than `MIN_AGE_DAYS` (default 90, override via env)
2. Not referenced in `memory/claude/MEMORY.md` (the index)

Why archive, not delete:
- `memory/claude/_archive/{name}.md` keeps full content on git history
- If a future conversation references something, grep + restore works
- Less dramatic than `rm` — operator can mass-revert if cron drifts

Rules:
- `feedback_*.md` → never archive (long-term lessons)
- `reference_*.md` → never archive (external system pointers)
- `user_*.md` → never archive (operator profile)
- `project_*.md` → archive iff (1) and (2) above
- Other files → leave alone

Cron (suggested):
    0 3 1 * *  cd /home/nakama && /usr/bin/python3 scripts/prune_old_memories.py --apply \\
        >> /var/log/nakama/memory-prune.log 2>&1

Idempotent: re-run is safe — already-archived files don't get re-archived.

Usage:
    python scripts/prune_old_memories.py            # dry-run; print decisions
    python scripts/prune_old_memories.py --apply    # actually move files

Exit codes:
    0 — successful (regardless of how many files moved)
    1 — MEMORY.md missing or unparseable
"""

from __future__ import annotations

import argparse
import os
import re
import shutil
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parent.parent
_MEMORY_DIR = _REPO_ROOT / "memory" / "claude"
_INDEX = _MEMORY_DIR / "MEMORY.md"
_ARCHIVE_DIR = _MEMORY_DIR / "_archive"

_DEFAULT_MIN_AGE_DAYS = 90

# File-name prefixes that are NEVER archived (long-term value)
_PROTECTED_PREFIXES = ("feedback_", "reference_", "user_", "MEMORY")
# Files that ARE candidates for archive (only project-state memories)
_PRUNABLE_PREFIXES = ("project_",)


@dataclass(frozen=True)
class PruneDecision:
    """One memory file's verdict from the prune scan."""

    path: Path
    action: str  # "archive" / "skip-protected" / "skip-referenced" / "skip-fresh" / "skip-other"
    reason: str


# ---- index parsing ----------------------------------------------------------


def _parse_referenced_files(index_path: Path) -> set[str]:
    """Read `MEMORY.md` and extract referenced `*.md` filenames.

    Looks for `[name](file.md)` markdown links — that's how the index lists
    every active memory. Files not in the index are candidates for archive
    even if they exist on disk.
    """
    if not index_path.exists():
        raise SystemExit(f"index missing: {index_path}")

    text = index_path.read_text(encoding="utf-8")
    # Match markdown links to local .md files: [label](path.md)
    referenced = set()
    for match in re.finditer(r"\[[^\]]+\]\(([^)]+\.md)\)", text):
        link = match.group(1)
        # Index sometimes uses ../../ relative paths; normalize to bare filename
        # since archive decision is per-file-in-memory_dir.
        if "/" in link:
            link = link.rsplit("/", 1)[1]
        referenced.add(link)
    return referenced


# ---- frontmatter parsing ----------------------------------------------------


def _parse_created_date(text: str) -> datetime | None:
    """Pull `created: YYYY-MM-DD` from frontmatter. Returns None if not present."""
    match = re.search(
        r"^created:\s*(\d{4}-\d{2}-\d{2})",
        text[:1500],  # frontmatter only — don't scan bodies
        flags=re.MULTILINE,
    )
    if not match:
        return None
    return datetime.fromisoformat(match.group(1)).replace(tzinfo=timezone.utc)


# ---- decision logic ---------------------------------------------------------


def _file_age_days(path: Path, text: str, now: datetime) -> int:
    """Use frontmatter `created` if present, else file mtime, to age the file."""
    created = _parse_created_date(text)
    if created is None:
        mtime = datetime.fromtimestamp(path.stat().st_mtime, tz=timezone.utc)
        return (now - mtime).days
    return (now - created).days


def decide(
    path: Path,
    text: str,
    referenced: set[str],
    min_age_days: int,
    now: datetime,
) -> PruneDecision:
    """Decide what to do with one memory file."""
    name = path.name

    # 1. Protected prefixes — never archive
    if any(name.startswith(p) for p in _PROTECTED_PREFIXES):
        return PruneDecision(path, "skip-protected", f"prefix is protected ({name.split('_')[0]}_)")

    # 2. Only project_ files are candidates
    if not any(name.startswith(p) for p in _PRUNABLE_PREFIXES):
        return PruneDecision(path, "skip-other", "not a project_ memory")

    # 3. Still referenced in MEMORY.md
    if name in referenced:
        return PruneDecision(path, "skip-referenced", "still listed in MEMORY.md index")

    # 4. Fresh
    age = _file_age_days(path, text, now)
    if age < min_age_days:
        return PruneDecision(path, "skip-fresh", f"only {age} days old (threshold {min_age_days})")

    return PruneDecision(path, "archive", f"{age} days old, unreferenced")


# ---- run --------------------------------------------------------------------


def scan(min_age_days: int = _DEFAULT_MIN_AGE_DAYS) -> list[PruneDecision]:
    """Walk memory/claude/*.md and return prune decisions for each."""
    referenced = _parse_referenced_files(_INDEX)
    now = datetime.now(timezone.utc)
    decisions: list[PruneDecision] = []
    for path in sorted(_MEMORY_DIR.glob("*.md")):
        if path.name == "MEMORY.md":
            continue
        text = path.read_text(encoding="utf-8")
        decisions.append(decide(path, text, referenced, min_age_days, now))
    return decisions


def apply(decisions: list[PruneDecision]) -> int:
    """Move files marked `archive` to `_archive/`. Returns count moved."""
    _ARCHIVE_DIR.mkdir(parents=True, exist_ok=True)
    moved = 0
    for d in decisions:
        if d.action != "archive":
            continue
        target = _ARCHIVE_DIR / d.path.name
        if target.exists():
            # Race / re-run — leave both copies, log
            print(f"  ! skip (target exists): {d.path.name}", file=sys.stderr)
            continue
        shutil.move(str(d.path), str(target))
        moved += 1
        print(f"  → archived: {d.path.name}")
    return moved


def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="prune_old_memories",
        description="Archive stale + unreferenced project_ memories to memory/claude/_archive/.",
    )
    p.add_argument(
        "--apply",
        action="store_true",
        help="actually move files (default: dry-run only prints decisions)",
    )
    p.add_argument(
        "--min-age-days",
        type=int,
        default=int(os.environ.get("NAKAMA_MEMORY_PRUNE_MIN_AGE_DAYS") or _DEFAULT_MIN_AGE_DAYS),
        help=f"only archive files older than this (default: {_DEFAULT_MIN_AGE_DAYS})",
    )
    return p


def main(argv: list[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    decisions = scan(min_age_days=args.min_age_days)

    by_action: dict[str, int] = {}
    for d in decisions:
        by_action[d.action] = by_action.get(d.action, 0) + 1

    print(f"Scanned {len(decisions)} memory files; min_age_days={args.min_age_days}")
    for action in ("archive", "skip-fresh", "skip-referenced", "skip-protected", "skip-other"):
        n = by_action.get(action, 0)
        if n:
            print(f"  {action}: {n}")
    print()

    candidates = [d for d in decisions if d.action == "archive"]
    if not candidates:
        print("No files match archive criteria.")
        return 0

    print("Archive candidates:")
    for d in candidates:
        print(f"  - {d.path.name}: {d.reason}")
    print()

    if args.apply:
        moved = apply(decisions)
        try:
            display_path = str(_ARCHIVE_DIR.relative_to(_REPO_ROOT)) + "/"
        except ValueError:
            # Test fixtures point _ARCHIVE_DIR outside the real repo root —
            # show absolute path instead of crashing the success message.
            display_path = str(_ARCHIVE_DIR) + "/"
        print(f"\n✅ Moved {moved} file(s) to {display_path}")
    else:
        print("(dry-run — re-run with --apply to actually archive)")

    return 0


if __name__ == "__main__":
    sys.exit(main())
