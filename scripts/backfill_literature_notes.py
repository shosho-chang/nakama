"""Backfill — render Literature Notes for annotation sets that never had one.

ADR-046 Slice 0B: the reader now auto-renders a Literature Note on every
annotation save/delete (article + video), matching books. Annotation sets that
were created BEFORE the trigger landed (e.g. existing videos) have no
``KB/Literature/{slug}.md`` yet. This walks ``KB/Annotations/`` and renders the
missing ones. ``source_kind`` is auto-inferred (``youtube_`` → video, book set
→ book, else article).

Idempotent: ``write_literature_note`` preserves the ledger/frontmatter on
re-render. Default only touches slugs WITHOUT a Literature Note; ``--all``
re-renders every set.

    python -m scripts.backfill_literature_notes --dry-run   # preview
    python -m scripts.backfill_literature_notes             # render missing
    python -m scripts.backfill_literature_notes --all       # re-render all
"""

from __future__ import annotations

import argparse
from pathlib import Path

from shared.config import get_vault_path
from shared.literature_writer import write_literature_note


def backfill(vault: Path, *, dry_run: bool, render_all: bool) -> int:
    ann_dir = vault / "KB" / "Annotations"
    lit_dir = vault / "KB" / "Literature"
    if not ann_dir.is_dir():
        print(f"(no {ann_dir} — nothing to backfill)")
        return 0

    rendered = skipped = failed = 0
    for ann_path in sorted(ann_dir.glob("*.md")):
        slug = ann_path.stem
        if not render_all and (lit_dir / f"{slug}.md").is_file():
            skipped += 1
            continue
        print(f"{'PLAN ' if dry_run else 'render'} {slug}")
        if dry_run:
            rendered += 1
            continue
        try:
            write_literature_note(slug)  # source_kind auto-inferred
            rendered += 1
        except Exception as exc:  # noqa: BLE001 — keep going, report at end
            print(f"  FAILED {slug}: {type(exc).__name__}: {exc}")
            failed += 1

    verb = "would render" if dry_run else "rendered"
    print(f"\n{verb} {rendered}, skipped (already had note) {skipped}, failed {failed}")
    return rendered


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dry-run", action="store_true", help="preview without writing")
    parser.add_argument("--all", action="store_true", help="re-render every set, not just missing")
    parser.add_argument("--vault", type=Path, default=None, help="vault root (default: config)")
    args = parser.parse_args()
    vault = args.vault or get_vault_path()
    print(f"vault: {vault}")
    backfill(vault, dry_run=args.dry_run, render_all=args.all)
    if args.dry_run:
        print("\n(dry-run — re-run without --dry-run to apply)")


if __name__ == "__main__":
    main()
