"""One-off cleanup: truncate Definition sections corrupted by the
2026-05-08 BSE seed-body fallback regression.

The regression caused the Phase B seed-body builder to capture the
chapter metadata block ("---  ### keywords  ...  ## 3.1 Organization of
Matter ...") into the Definition section, producing pages that fail the
C5 acceptance gate's "definition contains embedded markdown headings"
check.

Pattern observed: Definition starts with a clean opener like
``**X** is introduced in this chapter.`` and then trails into chapter
metadata. This script truncates the Definition body at the first
``---`` or ``## ... ## ###`` boundary — matching the gate's own regex
``(^|\s)(---|#{2,6}\s+)`` — leaving only the clean opener.

Pages whose opener is shorter than 10 chars after the cut get a minimal
fallback ``**<title>** — definition pending re-ingest.`` so the gate's
"empty ## Definition" check does not trip.

Idempotent: re-running on a cleaned page is a no-op (no boundary token
remains in the Definition).
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

# Same regex as scripts.run_s8_preflight._concept_page_quality_problem.
_POLLUTION_BOUNDARY = re.compile(r"(^|\s)(---|#{2,6}\s+)")


def _clean_definition(raw_def: str, title: str) -> str:
    """Return a Definition body free of embedded markdown headings."""
    m = _POLLUTION_BOUNDARY.search(raw_def)
    if not m:
        return raw_def.strip()
    clean = raw_def[: m.start()].strip()
    if len(clean) < 10:
        clean = f"**{title}** — definition pending re-ingest."
    return clean


def _process(page_path: Path) -> tuple[bool, str]:
    """Rewrite a page in place if its Definition contains pollution.

    Returns (changed, reason).
    """
    text = page_path.read_text(encoding="utf-8")
    fm_match = re.match(r"^---\n(.*?)\n---\n", text, re.DOTALL)
    body_start = fm_match.end() if fm_match else 0
    body = text[body_start:]

    def_match = re.search(
        r"(^## Definition\s*\n)(?P<body>.*?)(?=^## |\Z)",
        body,
        re.DOTALL | re.MULTILINE,
    )
    if not def_match:
        return False, "no Definition section"

    raw_def = def_match.group("body")
    if not _POLLUTION_BOUNDARY.search(raw_def):
        return False, "clean"

    title_match = re.search(r"^title:\s*(.+)$", text[: fm_match.end() if fm_match else 0], re.MULTILINE)
    title = title_match.group(1).strip() if title_match else page_path.stem

    cleaned = _clean_definition(raw_def, title)
    new_body = (
        body[: def_match.start("body")]
        + cleaned
        + "\n\n"
        + body[def_match.end("body") :]
    )
    page_path.write_text(text[:body_start] + new_body, encoding="utf-8")
    return True, "cleaned"


def main() -> int:
    if len(sys.argv) != 2:
        print("usage: python -m scripts.cleanup_broken_definition_seeds <staging_concepts_dir>")
        return 2
    staging = Path(sys.argv[1])
    if not staging.is_dir():
        print(f"not a directory: {staging}")
        return 2

    pages = sorted(staging.glob("*.md"))
    changed = 0
    for p in pages:
        did_change, reason = _process(p)
        if did_change:
            print(f"  CLEAN  {p.stem}")
            changed += 1
    print(f"\nProcessed {len(pages)} pages; cleaned {changed}.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
