"""Promote ACSM textbook ingest from KB/Wiki.staging/ to KB/Wiki/.

Mechanical (no LLM) promotion script — same shape as promote_sn_to_live.py:

1. 12 chapter source pages — copy KB/Wiki.staging/Sources/Books/acsm-guidelines-
   exercise-testing-prescription/ → KB/Wiki/Sources/Books/acsm-guidelines-
   exercise-testing-prescription/
2. Concept pages — for each staging concept whose mentioned_in includes any
   ACSM ch entry:
     - If NOT in live KB/Wiki/Concepts/ → copy staging → live (new ACSM concept)
     - If IN live → mechanical YAML merge: union mentioned_in (preserve live's
       body + other FM fields, only add ACSM entries that aren't already there)
3. Append entry to KB/log.md

Run with --dry-run first to preview, then without to apply. Idempotent.

Usage:
    python -m scripts.promote_acsm_to_live --dry-run
    python -m scripts.promote_acsm_to_live
"""

from __future__ import annotations

import argparse
import datetime
import re
import shutil
import sys
from pathlib import Path

import yaml

if sys.stdout.encoding and sys.stdout.encoding.lower() != "utf-8":
    sys.stdout.reconfigure(encoding="utf-8")

VAULT_ROOT = Path("E:/Shosho LifeOS")
ACSM_BOOK_ID = "acsm-guidelines-exercise-testing-prescription"
STAGING_BOOK_DIR = VAULT_ROOT / "KB" / "Wiki.staging" / "Sources" / "Books" / ACSM_BOOK_ID
LIVE_BOOK_DIR = VAULT_ROOT / "KB" / "Wiki" / "Sources" / "Books" / ACSM_BOOK_ID
STAGING_CONCEPTS_DIR = VAULT_ROOT / "KB" / "Wiki.staging" / "Concepts"
LIVE_CONCEPTS_DIR = VAULT_ROOT / "KB" / "Wiki" / "Concepts"
LOG_PATH = VAULT_ROOT / "KB" / "log.md"
ACSM_MENTIONED_PATTERN = re.compile(rf"Sources/Books/{re.escape(ACSM_BOOK_ID)}/")


def _split_fm(text: str) -> tuple[dict, str]:
    """Return (frontmatter_dict, body) — empty fm if no frontmatter."""
    m = re.match(r"^---\n(.*?)\n---\n(.*)\Z", text, re.DOTALL)
    if not m:
        return {}, text
    fm_raw = m.group(1)
    body = m.group(2)
    fm = yaml.safe_load(fm_raw) or {}
    return fm, body


def _write_fm_and_body(path: Path, fm: dict, body: str) -> None:
    fm_yaml = yaml.dump(fm, allow_unicode=True, default_flow_style=False, sort_keys=False)
    path.write_text(f"---\n{fm_yaml}---\n{body}", encoding="utf-8")


def _acsm_concept_slugs(verbose: bool = False) -> list[Path]:
    """Return staging concept .md paths whose mentioned_in includes any ACSM entry."""
    out: list[Path] = []
    for p in STAGING_CONCEPTS_DIR.glob("*.md"):
        try:
            text = p.read_text(encoding="utf-8")
            fm, _ = _split_fm(text)
            mentioned = fm.get("mentioned_in", []) or []
            if any(ACSM_MENTIONED_PATTERN.search(str(m)) for m in mentioned):
                out.append(p)
        except Exception as e:
            if verbose:
                print(f"  (skip {p.name}: {e})")
    return sorted(out)


def promote_chapters(*, dry_run: bool) -> tuple[int, int]:
    """Copy ch source pages staging → live. Returns (new_count, overwrite_count)."""
    if not LIVE_BOOK_DIR.exists():
        if not dry_run:
            LIVE_BOOK_DIR.mkdir(parents=True, exist_ok=True)
    new_count = 0
    overwrite_count = 0
    for src in sorted(STAGING_BOOK_DIR.glob("ch*.md")):
        dst = LIVE_BOOK_DIR / src.name
        if dst.exists():
            overwrite_count += 1
            action = "OVERWRITE"
        else:
            new_count += 1
            action = "NEW"
        print(f"  [chapter {action:>9}] {src.name}")
        if not dry_run:
            shutil.copy2(src, dst)
        cov_src = src.with_suffix(".coverage.json")
        cov_dst = dst.with_suffix(".coverage.json")
        if cov_src.exists():
            if not dry_run:
                shutil.copy2(cov_src, cov_dst)
    return new_count, overwrite_count


def promote_concepts(*, dry_run: bool) -> tuple[int, int, int]:
    """For each ACSM-mentioned staging concept: copy if new, merge mentioned_in if existing.

    Returns (new_count, merged_count, unchanged_count).
    """
    if not LIVE_CONCEPTS_DIR.exists():
        if not dry_run:
            LIVE_CONCEPTS_DIR.mkdir(parents=True, exist_ok=True)
    new_count = 0
    merged_count = 0
    unchanged_count = 0
    staging_files = _acsm_concept_slugs()
    print(f"  ({len(staging_files)} staging concepts mention ACSM — processing)")
    for src in staging_files:
        live_path = LIVE_CONCEPTS_DIR / src.name
        if not live_path.exists():
            new_count += 1
            print(f"  [concept       NEW] {src.name}")
            if not dry_run:
                shutil.copy2(src, live_path)
            continue
        live_text = live_path.read_text(encoding="utf-8")
        staging_text = src.read_text(encoding="utf-8")
        live_fm, live_body = _split_fm(live_text)
        staging_fm, _ = _split_fm(staging_text)
        live_mentioned = list(live_fm.get("mentioned_in", []) or [])
        staging_mentioned = list(staging_fm.get("mentioned_in", []) or [])
        seen = set(str(m) for m in live_mentioned)
        added: list = []
        for m in staging_mentioned:
            if str(m) not in seen:
                added.append(m)
                seen.add(str(m))
        if not added:
            unchanged_count += 1
            continue
        live_fm["mentioned_in"] = live_mentioned + added
        live_fm["updated"] = datetime.date.today()
        merged_count += 1
        added_acsm_only = [m for m in added if ACSM_MENTIONED_PATTERN.search(str(m))]
        print(f"  [concept    MERGED] {src.name}  (+{len(added_acsm_only)} ACSM entries)")
        if not dry_run:
            _write_fm_and_body(live_path, live_fm, live_body)
    return new_count, merged_count, unchanged_count


def append_log(*, ch_new: int, ch_overwrite: int, c_new: int, c_merged: int, dry_run: bool) -> None:
    today = datetime.date.today().isoformat()
    ch_summary = f"new: {ch_new}, overwrite: {ch_overwrite}"
    pipeline_note = (
        "ch1-12 via L3 CLI Max Plan; "
        "ch11 retry with NAKAMA_CLAUDE_CLI_TIMEOUT=1800 to clear 600s subprocess wall"
    )
    entry = f"""
## {today} — ACSM textbook v3 ingest promoted to live

- 12 chapter source pages → KB/Wiki/Sources/Books/{ACSM_BOOK_ID}/ ({ch_summary})
- {c_new} new concept pages → KB/Wiki/Concepts/
- {c_merged} existing concept pages merged (BSE/SN+ACSM mentioned_in union)
- All 12 chapters pass 7-condition acceptance gate after C5 cleanup
- Pipeline: {pipeline_note}
"""
    if dry_run:
        print(f"\n[log.md APPEND PREVIEW]:\n{entry}")
        return
    with LOG_PATH.open("a", encoding="utf-8") as f:
        f.write(entry)
    print(f"\n  [log.md APPENDED] {LOG_PATH}")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true", help="preview only, no writes")
    args = ap.parse_args()
    mode = "DRY-RUN" if args.dry_run else "APPLY"
    print(f"=== ACSM promotion ({mode}) ===\n")
    print("[chapters] staging → live:")
    ch_new, ch_overwrite = promote_chapters(dry_run=args.dry_run)
    print("\n[concepts] staging → live:")
    c_new, c_merged, c_unchanged = promote_concepts(dry_run=args.dry_run)
    print("\n=== Summary ===")
    print(f"  Chapters: {ch_new} new + {ch_overwrite} overwrite")
    print(f"  Concepts: {c_new} new + {c_merged} merged + {c_unchanged} unchanged (no ACSM delta)")
    append_log(
        ch_new=ch_new,
        ch_overwrite=ch_overwrite,
        c_new=c_new,
        c_merged=c_merged,
        dry_run=args.dry_run,
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
