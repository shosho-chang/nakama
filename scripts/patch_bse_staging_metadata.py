"""One-shot patch for BSE Wiki.staging — fix chapter_index frontmatter + delete dupes.

Diagnostic (2026-05-15):
- ch12.md byte-identical to ch10.md; ch13.md byte-identical to ch11.md → delete dupes + sidecars
- ch1.md, ch2.md, ch7.md, ch8.md, ch9.md, ch10.md, ch11.md frontmatter.chapter_index offset +2
  (walker had counted book-title + Preface H1 as ordinals 1-2; chapter_title prefix is canonical)
- ch5/ch6/ch7/ch8 phase1.json sidecars have internal title/index mismatch (older walker run) — realign

Body content, chapter_title, filenames are all correct. No LLM call.

Usage:
    python scripts/patch_bse_staging_metadata.py            # dry-run, prints diff
    python scripts/patch_bse_staging_metadata.py --apply    # writes changes
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

STAGING = Path(r"E:\Shosho LifeOS\KB\Wiki.staging\Sources\Books\biochemistry-for-sport-and-exercise-maclaren")

# Files whose frontmatter chapter_index needs setting to the canonical value
# (the integer prefix of chapter_title, which is correct in every file).
FRONTMATTER_FIX = {
    "ch1.md": 1,
    "ch2.md": 2,
    "ch7.md": 7,
    "ch8.md": 8,
    "ch9.md": 9,
    "ch10.md": 10,
    "ch11.md": 11,
}

# Dupes confirmed by md5: ch12.md == ch10.md, ch13.md == ch11.md
DELETE = ["ch12.md", "ch12.phase1.json", "ch13.md", "ch13.phase1.json"]

# Sidecars whose internal {chapter_index, chapter_title} disagree with the .md
# (older walker run wrote sidecar, newer walker run overwrote .md but not sidecar).
# Realign to .md's canonical values.
SIDECAR_FIX = {
    "ch5.phase1.json": {"chapter_index": 5, "chapter_title": "5 Carbohydrates"},
    "ch6.phase1.json": {"chapter_index": 6, "chapter_title": "6 Lipids"},
    "ch7.phase1.json": {"chapter_index": 7, "chapter_title": "7 Principles of Metabolic Regulation"},
    "ch8.phase1.json": {"chapter_index": 8, "chapter_title": "8 Techniques for Exercise Metabolism"},
    "ch9.phase1.json": {"chapter_index": 9, "chapter_title": "9 High-Intensity Exercise (HIE)"},
    "ch10.phase1.json": {"chapter_index": 10, "chapter_title": "10 Endurance Exercise"},
    "ch11.phase1.json": {"chapter_index": 11, "chapter_title": "11 High-intensity Intermittent Exercise"},
}


def _patch_md_chapter_index(path: Path, new_index: int, apply: bool) -> tuple[int, int] | None:
    """Returns (old, new) chapter_index if changed, None if already correct."""
    text = path.read_text(encoding="utf-8")
    m = re.search(r"^(chapter_index:\s*)(\d+)\s*$", text, flags=re.MULTILINE)
    if not m:
        print(f"  ! {path.name}: no chapter_index line found")
        return None
    old = int(m.group(2))
    if old == new_index:
        return None
    if apply:
        new_text = text[: m.start()] + f"{m.group(1)}{new_index}" + text[m.end() :]
        path.write_text(new_text, encoding="utf-8")
    return (old, new_index)


def _patch_sidecar(path: Path, fix: dict, apply: bool) -> dict | None:
    """Returns dict of changes if any, None if already correct."""
    data = json.loads(path.read_text(encoding="utf-8"))
    fm = data.get("frontmatter") or {}
    changes: dict = {}
    for k, v in fix.items():
        if fm.get(k) != v:
            changes[k] = {"old": fm.get(k), "new": v}
            fm[k] = v
    if not changes:
        return None
    data["frontmatter"] = fm
    if apply:
        path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    return changes


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true", help="Actually write changes (default: dry-run)")
    args = ap.parse_args()

    if not STAGING.is_dir():
        print(f"FATAL: staging dir not found: {STAGING}")
        return 2

    mode = "APPLY" if args.apply else "DRY-RUN"
    print(f"=== BSE staging metadata patch ({mode}) ===\n")

    print("[1/3] Delete dupes")
    for name in DELETE:
        p = STAGING / name
        if not p.exists():
            print(f"  - {name}: already absent")
            continue
        if args.apply:
            p.unlink()
            print(f"  - {name}: DELETED")
        else:
            print(f"  - {name}: would delete ({p.stat().st_size} bytes)")

    print("\n[2/3] Fix .md frontmatter chapter_index")
    for name, new_idx in FRONTMATTER_FIX.items():
        p = STAGING / name
        if not p.exists():
            print(f"  ! {name}: not found, skip")
            continue
        diff = _patch_md_chapter_index(p, new_idx, args.apply)
        if diff is None:
            print(f"  = {name}: already chapter_index={new_idx}")
        else:
            old, new = diff
            verb = "PATCHED" if args.apply else "would patch"
            print(f"  ~ {name}: chapter_index {old} -> {new} ({verb})")

    print("\n[3/3] Realign phase1.json sidecars")
    for name, fix in SIDECAR_FIX.items():
        p = STAGING / name
        if not p.exists():
            print(f"  ! {name}: not found, skip")
            continue
        changes = _patch_sidecar(p, fix, args.apply)
        if changes is None:
            print(f"  = {name}: already aligned")
        else:
            verb = "PATCHED" if args.apply else "would patch"
            for k, d in changes.items():
                print(f"  ~ {name}: {k} {d['old']!r} -> {d['new']!r} ({verb})")

    print(f"\n=== {mode} complete ===")
    if not args.apply:
        print("Re-run with --apply to write changes.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
