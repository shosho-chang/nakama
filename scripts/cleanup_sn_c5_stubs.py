"""One-shot cleanup for SN ingest C5-flagged concept page Definitions.

8 concept pages produced by the SN ingest batch were flagged by the
``compute_acceptance_7`` C5 check ("definition contains embedded markdown
headings"). Cause: the Phase 2 seed-body fallback captured an H2/H3 heading
from the source page when building the concept's Definition snippet.

This script replaces the broken Definition body with the standard scaffold
placeholder ``_(尚無內容)_`` — same convention used by other newly-created L2
concepts in the SN batch (e.g. phytonutrient.md). The textbook prose that
leaked through alongside the heading was already structurally unsalvageable
(half-paragraph + heading bleed) and the L2 scaffold pattern is the
truthful state: "this concept exists as a wikilink target, definition TBD".

Usage:
    python -m scripts.cleanup_sn_c5_stubs
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

CONCEPTS_DIR = Path("E:/Shosho LifeOS/KB/Wiki.staging/Concepts")
STUB_SLUGS = [
    "bone mineral",
    "fat substitute",
    "four-component model",
    "labeled bicarbonate",
    "thermic effect of exercise",
    "three-component model",
    "total body water",
    "two-component model",
]

DEFINITION_PATTERN = re.compile(
    r"(## Definition\n\n)(.*?)(\n\n## )",
    re.DOTALL,
)
PLACEHOLDER = "_(尚無內容)_"


def fix_one(path: Path) -> str:
    text = path.read_text(encoding="utf-8")
    new_text, n = DEFINITION_PATTERN.subn(
        lambda m: f"{m.group(1)}{PLACEHOLDER}{m.group(3)}",
        text,
        count=1,
    )
    if n == 0:
        return "NO_MATCH"
    if new_text == text:
        return "ALREADY_CLEAN"
    path.write_text(new_text, encoding="utf-8")
    return "PATCHED"


def main() -> int:
    rc = 0
    print(f"Cleaning {len(STUB_SLUGS)} SN C5-flagged stub concept Definitions...")
    print(f"Concepts dir: {CONCEPTS_DIR}\n")
    for slug in STUB_SLUGS:
        path = CONCEPTS_DIR / f"{slug}.md"
        if not path.exists():
            print(f"  [MISSING] {slug}.md")
            rc = 1
            continue
        result = fix_one(path)
        print(f"  [{result:>14}] {slug}.md")
        if result == "NO_MATCH":
            rc = 1
    return rc


if __name__ == "__main__":
    sys.exit(main())
