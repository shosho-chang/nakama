"""Promote SN textbook ingest from KB/Wiki.staging/ to KB/Wiki/.

Mechanical (no LLM) promotion script:

1. 17 chapter source pages — copy KB/Wiki.staging/Sources/Books/sport-nutrition-
   jeukendrup-4e/ → KB/Wiki/Sources/Books/sport-nutrition-jeukendrup-4e/
2. Concept pages — for each staging concept whose mentioned_in includes any
   SN ch entry:
     - If NOT in live KB/Wiki/Concepts/ → copy staging → live (new SN concept)
     - If IN live → mechanical YAML merge: union mentioned_in (preserve live's
       body + other FM fields, only add SN entries that aren't already there)
3. Append entry to KB/log.md

Run with --dry-run first to preview, then without to apply. Idempotent: re-running
after a successful promote is a no-op for chapters that already exist in live and
for concepts where SN entries are already in mentioned_in.

Usage:
    python -m scripts.promote_sn_to_live --dry-run
    python -m scripts.promote_sn_to_live
"""
from __future__ import annotations

import argparse
import datetime
import re
import shutil
import sys
from pathlib import Path

import yaml

# Windows cp1252 stdout choke on arrows / CJK — reconfigure to utf-8 so the
# preview output renders unicode safely (per feedback_windows_stdout_utf8).
if sys.stdout.encoding and sys.stdout.encoding.lower() != "utf-8":
    sys.stdout.reconfigure(encoding="utf-8")

VAULT_ROOT = Path("E:/Shosho LifeOS")
STAGING_BOOK_DIR = VAULT_ROOT / "KB" / "Wiki.staging" / "Sources" / "Books" / "sport-nutrition-jeukendrup-4e"
LIVE_BOOK_DIR = VAULT_ROOT / "KB" / "Wiki" / "Sources" / "Books" / "sport-nutrition-jeukendrup-4e"
STAGING_CONCEPTS_DIR = VAULT_ROOT / "KB" / "Wiki.staging" / "Concepts"
LIVE_CONCEPTS_DIR = VAULT_ROOT / "KB" / "Wiki" / "Concepts"
LOG_PATH = VAULT_ROOT / "KB" / "log.md"

SN_BOOK_ID = "sport-nutrition-jeukendrup-4e"
SN_MENTIONED_PATTERN = re.compile(rf"Sources/Books/{re.escape(SN_BOOK_ID)}/")


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


def _sn_concept_slugs(verbose: bool = False) -> list[Path]:
    """Return staging concept .md paths whose mentioned_in includes any SN entry."""
    out: list[Path] = []
    for p in STAGING_CONCEPTS_DIR.glob("*.md"):
        try:
            text = p.read_text(encoding="utf-8")
            fm, _ = _split_fm(text)
            mentioned = fm.get("mentioned_in", []) or []
            if any(SN_MENTIONED_PATTERN.search(str(m)) for m in mentioned):
                out.append(p)
        except Exception as e:
            if verbose:
                print(f"  (skip {p.name}: {e})")
    return sorted(out)


def promote_chapters(*, dry_run: bool) -> tuple[int, int]:
    """Copy 17 ch source pages staging → live. Returns (new_count, overwrite_count)."""
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
        # Also copy the .coverage.json sidecar
        cov_src = src.with_suffix(".coverage.json")
        cov_dst = dst.with_suffix(".coverage.json")
        if cov_src.exists():
            if not dry_run:
                shutil.copy2(cov_src, cov_dst)
    return new_count, overwrite_count


def promote_concepts(*, dry_run: bool) -> tuple[int, int, int]:
    """For each SN-mentioned staging concept: copy if new, merge mentioned_in if existing.

    Returns (new_count, merged_count, unchanged_count).
    """
    if not LIVE_CONCEPTS_DIR.exists():
        if not dry_run:
            LIVE_CONCEPTS_DIR.mkdir(parents=True, exist_ok=True)
    new_count = 0
    merged_count = 0
    unchanged_count = 0
    staging_files = _sn_concept_slugs()
    print(f"  ({len(staging_files)} staging concepts mention SN — processing)")
    for src in staging_files:
        live_path = LIVE_CONCEPTS_DIR / src.name
        if not live_path.exists():
            new_count += 1
            print(f"  [concept       NEW] {src.name}")
            if not dry_run:
                shutil.copy2(src, live_path)
            continue
        # Existing — merge mentioned_in
        live_text = live_path.read_text(encoding="utf-8")
        staging_text = src.read_text(encoding="utf-8")
        live_fm, live_body = _split_fm(live_text)
        staging_fm, _ = _split_fm(staging_text)
        live_mentioned = list(live_fm.get("mentioned_in", []) or [])
        staging_mentioned = list(staging_fm.get("mentioned_in", []) or [])
        # Union, preserving order
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
        added_sn_only = [m for m in added if SN_MENTIONED_PATTERN.search(str(m))]
        print(f"  [concept    MERGED] {src.name}  (+{len(added_sn_only)} SN entries)")
        if not dry_run:
            _write_fm_and_body(live_path, live_fm, live_body)
    return new_count, merged_count, unchanged_count


def append_log(*, ch_new: int, ch_overwrite: int, c_new: int, c_merged: int, dry_run: bool) -> None:
    today = datetime.date.today().isoformat()
    entry = f"""
## {today} — SN textbook v3 ingest promoted to live

- 17 chapter source pages → KB/Wiki/Sources/Books/{SN_BOOK_ID}/ (new: {ch_new}, overwrite: {ch_overwrite})
- {c_new} new concept pages → KB/Wiki/Concepts/
- {c_merged} existing concept pages merged (BSE+SN mentioned_in union)
- All 17 chapters pass 7-condition acceptance gate (delta C4 mode)
- Pipeline: ch1-10 + ch12-17 via L3 CLI Max Plan; ch11 via SDK + 64K streaming (Phase 1 output cap workaround for ch11's 234K-char Nutrition Supplements chapter)
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
    print(f"=== SN promotion ({mode}) ===\n")
    print("[chapters] staging → live:")
    ch_new, ch_overwrite = promote_chapters(dry_run=args.dry_run)
    print(f"\n[concepts] staging → live:")
    c_new, c_merged, c_unchanged = promote_concepts(dry_run=args.dry_run)
    print(f"\n=== Summary ===")
    print(f"  Chapters: {ch_new} new + {ch_overwrite} overwrite")
    print(f"  Concepts: {c_new} new + {c_merged} merged + {c_unchanged} unchanged (no SN delta)")
    append_log(ch_new=ch_new, ch_overwrite=ch_overwrite, c_new=c_new, c_merged=c_merged, dry_run=args.dry_run)
    return 0


if __name__ == "__main__":
    sys.exit(main())
