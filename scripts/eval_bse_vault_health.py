"""BSE vault health diagnostic — find concept-page fragmentation post-promote.

Doesn't query the agent. Just walks `KB/Wiki/Concepts/` and reports:

  1. Canonicalize collisions — distinct files whose `concept_canonicalize.canonicalize()`
     output is the same. These should have been merged at dispatch time but
     legacy pages from before the C6 fix landed remain on disk.
  2. Truncated `-sis` words — files ending in `genesi.md`, `lysi.md`,
     `biogenesi.md` etc. caused by the OLD plural-stripping rule that
     mis-handled Greek-derived `-sis` singulars.
  3. Case-only duplicates — `ATP.md` vs `atp.md`. Windows treats them as
     the same file (collision), but on case-sensitive filesystems Obsidian
     wikilinks would target distinct pages.
  4. Wikilink rot from concept pages → deleted source chapters
     (`mentioned_in:` references that no longer resolve, e.g. ch12/ch13
     dupes that got cleaned up).

No write side effects — read-only audit. Output prints to stdout +
JSON written to ``docs/runs/{date}-bse-vault-health.json``.

Usage:
    python -m scripts.eval_bse_vault_health
"""

from __future__ import annotations

import argparse
import json
import re
import sys

# Windows cp1252 stdout cannot print unicode arrows / checkmarks; force UTF-8.
if sys.stdout.encoding and sys.stdout.encoding.lower() != "utf-8":
    sys.stdout.reconfigure(encoding="utf-8")
from collections import defaultdict
from datetime import datetime
from pathlib import Path

import yaml

_REPO_ROOT = Path(__file__).resolve().parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

DEFAULT_OUTPUT_DIR = _REPO_ROOT / "docs" / "runs"

# Patterns indicating the OLD (pre-fix) over-aggressive `-sis` strip.
_TRUNCATED_SIS_RE = re.compile(r"(genesi|lysi|biogenesi|hydrolysi)$", re.IGNORECASE)


def find_canonicalize_collisions(concepts_dir: Path) -> dict[str, list[str]]:
    """Distinct stems whose canonical form collides."""
    from shared.concept_canonicalize import canonicalize  # noqa: PLC0415

    by_canonical: dict[str, list[str]] = defaultdict(list)
    for p in concepts_dir.glob("*.md"):
        stem = p.stem
        c = canonicalize(stem)
        by_canonical[c].append(stem)
    return {k: sorted(v) for k, v in by_canonical.items() if len(v) > 1}


def find_truncated_sis(concepts_dir: Path) -> list[tuple[str, str]]:
    """Pages with stems ending in `-genesi`, `-lysi`, `-biogenesi`, `-hydrolysi`.

    Returns (truncated_stem, suspected_correct_form) pairs.
    """
    out: list[tuple[str, str]] = []
    for p in concepts_dir.glob("*.md"):
        stem = p.stem
        m = _TRUNCATED_SIS_RE.search(stem)
        if m:
            corrected = stem[: m.start()] + m.group(1) + "s"
            out.append((stem, corrected))
    return out


def find_case_dupes(concepts_dir: Path) -> dict[str, list[str]]:
    """Case-only duplicates — distinct filenames sharing the same casefold."""
    by_casefold: dict[str, list[str]] = defaultdict(list)
    for p in concepts_dir.glob("*.md"):
        by_casefold[p.stem.casefold()].append(p.stem)
    return {k: sorted(v) for k, v in by_casefold.items() if len(v) > 1}


def find_wikilink_rot(
    concepts_dir: Path, sources_root: Path, book_id: str
) -> list[tuple[str, list[str]]]:
    """Concept pages whose `mentioned_in:` references chapters that no longer exist.

    Returns (concept_stem, dangling_chapter_refs) pairs. Only checks BSE chapter
    refs (Books/<book_id>/ch<n>) — not external Sources.
    """
    book_dir = sources_root / "Books" / book_id
    existing_chapters: set[str] = (
        {p.stem for p in book_dir.glob("ch*.md")} if book_dir.exists() else set()
    )
    rot_re = re.compile(rf"Sources/Books/{re.escape(book_id)}/(ch\d+)")

    dangling: list[tuple[str, list[str]]] = []
    for p in concepts_dir.glob("*.md"):
        text = p.read_text(encoding="utf-8")
        m = re.match(r"^---\n(.*?)\n---\n", text, re.DOTALL)
        if not m:
            continue
        try:
            fm = yaml.safe_load(m.group(1)) or {}
        except yaml.YAMLError:
            continue
        mentioned = fm.get("mentioned_in") or []
        chapters = []
        for entry in mentioned:
            mm = rot_re.search(str(entry))
            if mm:
                chapters.append(mm.group(1))
        bad = [c for c in chapters if c not in existing_chapters]
        if bad:
            dangling.append((p.stem, sorted(set(bad))))
    return dangling


def main() -> int:
    parser = argparse.ArgumentParser(description="BSE vault health diagnostic")
    parser.add_argument(
        "--book-id",
        default="biochemistry-for-sport-and-exercise-maclaren",
        help="Book id under KB/Wiki/Sources/Books/ (default: BSE)",
    )
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR)
    parser.add_argument("--no-write", action="store_true")
    args = parser.parse_args()

    from shared.config import get_vault_path  # noqa: PLC0415

    vault = get_vault_path()
    concepts_dir = vault / "KB" / "Wiki" / "Concepts"
    sources_root = vault / "KB" / "Wiki" / "Sources"

    print(f"Vault: {vault}")
    print(f"Concept pages: {len(list(concepts_dir.glob('*.md')))}")
    print()

    # ----- 1. Canonicalize collisions -----
    print("=" * 70)
    print("1. Canonicalize collisions (distinct files → same canonical slug)")
    print("=" * 70)
    coll = find_canonicalize_collisions(concepts_dir)
    if not coll:
        print("(none — all concept files have distinct canonical slugs ✓)")
    else:
        for canon, stems in sorted(coll.items()):
            print(f"  '{canon}' ← {stems}")
    print(f"\nTotal collisions: {len(coll)}")

    # ----- 2. Truncated -sis (legacy strip bug) -----
    print()
    print("=" * 70)
    print("2. Truncated -sis pages (legacy plural-strip regression)")
    print("=" * 70)
    truncated = find_truncated_sis(concepts_dir)
    if not truncated:
        print("(none — no -genesi/-lysi/-biogenesi/-hydrolysi pages ✓)")
    else:
        for stem, corrected in sorted(truncated):
            corrected_exists = (concepts_dir / f"{corrected}.md").exists()
            mark = "DUPE both exist" if corrected_exists else "ONLY truncated"
            print(f"  {stem!r} → suspect {corrected!r} [{mark}]")
    print(f"\nTotal truncated: {len(truncated)}")

    # ----- 3. Case-only duplicates -----
    print()
    print("=" * 70)
    print("3. Case-only duplicates (case-sensitive Obsidian linking impact)")
    print("=" * 70)
    dupes = find_case_dupes(concepts_dir)
    if not dupes:
        print("(none — all concept names case-unique ✓)")
    else:
        for cf, stems in sorted(dupes.items()):
            print(f"  '{cf}' has variants: {stems}")
    print(f"\nTotal case-dupes: {len(dupes)}")

    # ----- 4. Wikilink rot to deleted chapters -----
    print()
    print("=" * 70)
    print(f"4. Concept→source wikilink rot for book '{args.book_id}'")
    print("=" * 70)
    rot = find_wikilink_rot(concepts_dir, sources_root, args.book_id)
    if not rot:
        print("(none — all concept page mentioned_in refs resolve ✓)")
    else:
        for stem, bad in sorted(rot):
            print(f"  {stem}.md mentions deleted chapter(s): {bad}")
    print(f"\nTotal pages with rotted refs: {len(rot)}")

    # ----- Summary -----
    print()
    print("=" * 70)
    print("SUMMARY")
    print("=" * 70)
    total_issues = len(coll) + len(truncated) + len(dupes) + len(rot)
    print(f"Total issue groups: {total_issues}")
    print(f"  Canonicalize collisions  : {len(coll)}")
    print(f"  Truncated -sis pages     : {len(truncated)}")
    print(f"  Case-only duplicates     : {len(dupes)}")
    print(f"  Wikilink rot (concept→source) : {len(rot)}")

    if not args.no_write:
        args.output_dir.mkdir(parents=True, exist_ok=True)
        out_path = args.output_dir / f"{datetime.now().strftime('%Y-%m-%d')}-bse-vault-health.json"
        out_path.write_text(
            json.dumps(
                {
                    "generated": datetime.now().isoformat(timespec="seconds"),
                    "vault": str(vault),
                    "concept_pages_count": len(list(concepts_dir.glob("*.md"))),
                    "canonicalize_collisions": coll,
                    "truncated_sis": truncated,
                    "case_dupes": dupes,
                    "wikilink_rot": [{"concept": s, "deleted_chapters": b} for s, b in rot],
                    "summary": {
                        "canonicalize_collisions_count": len(coll),
                        "truncated_sis_count": len(truncated),
                        "case_dupes_count": len(dupes),
                        "wikilink_rot_count": len(rot),
                    },
                },
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )
        print(f"\nJSON summary: {out_path}")

    return 0 if total_issues == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
