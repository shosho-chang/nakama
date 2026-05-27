"""Splice the LLM-generated body into the hand-curated playbook scaffold.

Reads:
- ``prompts/thumbnail/playbook_v1.md`` (scaffold with placeholder regions)
- ``prompts/thumbnail/playbook_v1_body_generated.md`` (compose_playbook_v1 output)

Writes ``prompts/thumbnail/playbook_v1.md`` (in place, splice applied).

Placeholder regions are identified by their leading `## N.` heading + a sentinel
italic line starting with `*To be populated`. Each region extends until the next
`##` heading (or `### 5.{n}` heading inside §5).

The script is **idempotent on already-spliced files**: if no `*To be populated`
sentinel is found in a section, it's left untouched.

Run after `compose_playbook_v1.py`. Future v2 (corpus growth) re-runs
extract → cluster → compose → splice without manual intervention.
"""

from __future__ import annotations

import argparse
import logging
import re
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parent.parent
_SCAFFOLD_PATH = _REPO_ROOT / "prompts" / "thumbnail" / "playbook_v1.md"
_BODY_PATH = _REPO_ROOT / "prompts" / "thumbnail" / "playbook_v1_body_generated.md"

logger = logging.getLogger(__name__)


def _split_body_sections(body: str) -> dict[str, str]:
    """Parse body into {section_key: markdown_block}.

    Sections are identified by their `## N.` heading. Returns dict mapping
    {"2": "## 2. Title Archetypes\n\n### T-A1...\n---\n", "3": ..., ...}.
    """
    sections: dict[str, str] = {}
    # Match `## 2. ...` `## 3. ...` `## 4. ...` `## 5.2 ...` `## 7. ...`
    # Lines starting with `## ` followed by digit
    pattern = re.compile(r"^## (\d+(?:\.\d+)?)\.?\s.*$", re.MULTILINE)
    matches = list(pattern.finditer(body))
    for i, m in enumerate(matches):
        key = m.group(1)
        start = m.start()
        end = matches[i + 1].start() if i + 1 < len(matches) else len(body)
        sections[key] = body[start:end].rstrip() + "\n"
    return sections


def _replace_section(scaffold: str, section_marker: str, new_content: str, next_marker: str) -> tuple[str, bool]:
    """Replace scaffold region from section_marker to next_marker with new_content.

    section_marker: e.g. "## 2. Title Archetypes"
    next_marker: e.g. "## 3. Thumbnail Archetypes" (where to stop)

    Returns (new_scaffold, was_modified).
    """
    start = scaffold.find(section_marker)
    if start < 0:
        logger.warning("section marker not found: %r — skipping", section_marker)
        return scaffold, False
    nxt = scaffold.find(next_marker, start + len(section_marker))
    if nxt < 0:
        logger.warning("next marker not found after %r — skipping", section_marker)
        return scaffold, False
    region = scaffold[start:nxt]
    if "*To be populated" not in region:
        logger.info("region %r appears already spliced (no placeholder sentinel) — skipping", section_marker)
        return scaffold, False
    # Preserve trailing newline patterns of original region
    return scaffold[:start] + new_content.rstrip() + "\n\n" + scaffold[nxt:], True


def _splice_section_5_2(scaffold: str, new_5_2: str) -> tuple[str, bool]:
    """§5.2 is a subsection inside §5 — bounded by `### 5.2` start and `### 5.3` end."""
    start_marker = "### 5.2 Archetype × archetype × brand-fit grade matrix"
    # Look for several possible scaffold section headers
    for candidate in (
        "### 5.2 Archetype × brand-fit grade matrix",
        "### 5.2 Archetype × archetype × brand-fit grade matrix",
        "## 5.2 Archetype × Brand-Fit Matrix",
    ):
        if candidate in scaffold:
            start_marker = candidate
            break
    start = scaffold.find(start_marker)
    if start < 0:
        logger.warning("§5.2 start marker not found — skipping")
        return scaffold, False
    end_marker = "### 5.3 Hormozi adaptation"
    nxt = scaffold.find(end_marker, start)
    if nxt < 0:
        logger.warning("§5.2 end marker (5.3 Hormozi) not found — skipping")
        return scaffold, False
    region = scaffold[start:nxt]
    if "*To be populated" not in region:
        logger.info("§5.2 already spliced — skipping")
        return scaffold, False
    # new_5_2 from body uses `## 5.2 ...` h2; convert to `### 5.2 ...` h3 to fit §5
    new_body = new_5_2.replace("## 5.2 Archetype × Brand-Fit Matrix", "### 5.2 Archetype × Brand-Fit Matrix", 1)
    return scaffold[:start] + new_body.rstrip() + "\n\n" + scaffold[nxt:], True


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--scaffold", type=Path, default=_SCAFFOLD_PATH)
    parser.add_argument("--body", type=Path, default=_BODY_PATH)
    parser.add_argument("--verbose", "-v", action="store_true")
    args = parser.parse_args(argv)

    logging.basicConfig(level=logging.DEBUG if args.verbose else logging.INFO, format="%(levelname)s %(message)s")

    scaffold = args.scaffold.read_text(encoding="utf-8")
    body = args.body.read_text(encoding="utf-8")

    sections = _split_body_sections(body)
    logger.info("body parsed into sections: %s", sorted(sections.keys()))

    changes = 0
    for sec, next_top in [
        ("2", "## 3. Thumbnail Archetypes"),
        ("3", "## 4. Joint Pairings"),
        ("4", "## 5. 修修 Brand Adaptation Layer"),
        ("7", "## 8. Versioning"),
    ]:
        if sec not in sections:
            logger.warning("body missing section %s — skipping", sec)
            continue
        scaffold_marker = {
            "2": "## 2. Title Archetypes",
            "3": "## 3. Thumbnail Archetypes",
            "4": "## 4. Joint Pairings (Title × Thumbnail recipes)",
            "7": "## 7. Methodology Caveats (self-critique — required honest section)",
        }[sec]
        scaffold, modified = _replace_section(scaffold, scaffold_marker, sections[sec], next_top)
        if modified:
            changes += 1

    if "5.2" in sections:
        scaffold, modified = _splice_section_5_2(scaffold, sections["5.2"])
        if modified:
            changes += 1

    if not changes:
        logger.warning("no changes applied — scaffold may already be fully spliced")
        return 1

    args.scaffold.write_text(scaffold, encoding="utf-8")
    logger.info("spliced %d sections into %s", changes, args.scaffold)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
