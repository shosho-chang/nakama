"""Replace structurally-invalid Definition sections with the canonical placeholder.

The 7-condition acceptance gate's C5 rule rejects concept pages whose Definition
section either is missing, is empty, or contains embedded markdown headings
(a regression-guard for the seed-body fallback accidentally capturing chapter
H1/H2 text — see run_s8_preflight._concept_page_quality_problem).

This script scans ``KB/Wiki.staging/Concepts/*.md`` and, for any page whose
Definition triggers ``definition contains embedded markdown headings``,
replaces the Definition body with the project-standard
``_(尚無內容)_`` placeholder, which is the L2 marker accepted by C5 as
intentional empty.

Side-effects: writes back the patched pages in place. Idempotent: re-running
after a clean run is a no-op. Empty-Definition cases and missing-Definition
cases are left alone — they indicate a genuinely broken file that needs
human review (or a re-dispatch), not a mechanical placeholder.

Usage:
    python -m scripts.cleanup_c5_stubs --dry-run
    python -m scripts.cleanup_c5_stubs
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

if sys.stdout.encoding and sys.stdout.encoding.lower() != "utf-8":
    sys.stdout.reconfigure(encoding="utf-8")

# Repo path bootstrap.
_REPO_ROOT = Path(__file__).resolve().parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from scripts.run_s8_preflight import _concept_page_quality_problem  # noqa: E402

DEFAULT_STAGING = Path("E:/Shosho LifeOS/KB/Wiki.staging/Concepts")
PLACEHOLDER = "_(尚無內容)_"
DEFINITION_PATTERN = re.compile(r"(## Definition\n\n)(.*?)(\n\n## )", re.DOTALL)


def fix_page(path: Path, *, dry_run: bool) -> str:
    """Return one of: 'fixed', 'unchanged', 'skip-other' for a single page."""
    text = path.read_text(encoding="utf-8", errors="replace")
    problem = _concept_page_quality_problem(text)
    if problem is None:
        return "unchanged"
    if problem != "definition contains embedded markdown headings":
        # Empty or missing Definition is a deeper issue — leave for human review.
        print(f"  [SKIP non-stub  ] {path.stem}: {problem}")
        return "skip-other"
    new = DEFINITION_PATTERN.sub(rf"\1{PLACEHOLDER}\3", text, count=1)
    if new == text:
        print(f"  [SKIP no-match  ] {path.stem}: regex did not match")
        return "skip-other"
    if not dry_run:
        path.write_text(new, encoding="utf-8")
    print(f"  [FIXED          ] {path.stem}")
    return "fixed"


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument(
        "--staging",
        type=Path,
        default=DEFAULT_STAGING,
        help=f"path to staging Concepts/ dir (default: {DEFAULT_STAGING})",
    )
    ap.add_argument("--dry-run", action="store_true", help="preview only, no writes")
    args = ap.parse_args()

    mode = "DRY-RUN" if args.dry_run else "APPLY"
    print(f"=== C5 stub cleanup ({mode}) — {args.staging} ===\n")
    counts = {"fixed": 0, "unchanged": 0, "skip-other": 0}
    for page in sorted(args.staging.glob("*.md")):
        counts[fix_page(page, dry_run=args.dry_run)] += 1

    print("\n=== Summary ===")
    print(f"  Fixed:        {counts['fixed']}")
    print(f"  Unchanged:    {counts['unchanged']}")
    print(f"  Skipped/oth.: {counts['skip-other']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
