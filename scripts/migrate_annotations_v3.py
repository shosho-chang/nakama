"""ADR-021 §1: one-shot migration — upgrade v1 / v2 annotation files in
``KB/Annotations/*.md`` to v3 (W3C Web Annotation shape). Idempotent: already-v3
files are skipped; failures are logged with file path and reason. Safe to re-run.

Usage::

    python -m scripts.migrate_annotations_v3            # run against $VAULT_PATH
    python -m scripts.migrate_annotations_v3 --dry-run  # log only, no writes
    python -m scripts.migrate_annotations_v3 --vault /custom/path

Logs to stdout in human-readable form. Exit code is 0 even when individual files
fail — the script's job is to surface what migrated, what was skipped, and what
errored, not to block on a single bad file. Run it again after fixing issues.
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

from shared.annotation_store import (
    AnnotationStore,
    _annotations_dir,
    _parse,
    upgrade_to_v3,
)
from shared.schemas.annotations import AnnotationSetV3


def _iter_annotation_files(directory: Path) -> list[Path]:
    if not directory.exists():
        return []
    return sorted(p for p in directory.glob("*.md") if p.is_file())


def migrate_file(path: Path, dry_run: bool, store: AnnotationStore) -> str:
    """Migrate a single annotation file. Returns one of: 'upgraded', 'already_v3',
    'failed:<reason>'."""
    text = path.read_text(encoding="utf-8")
    slug = path.stem
    try:
        ann_set = _parse(text, slug)
    except Exception as exc:  # noqa: BLE001
        return f"failed:parse:{exc}"

    if isinstance(ann_set, AnnotationSetV3):
        return "already_v3"

    try:
        upgraded = upgrade_to_v3(ann_set)
    except Exception as exc:  # noqa: BLE001
        return f"failed:upgrade:{exc}"

    if dry_run:
        return "upgraded(dry-run)"

    try:
        store.save(upgraded)
    except Exception as exc:  # noqa: BLE001
        return f"failed:save:{exc}"
    return "upgraded"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dry-run", action="store_true", help="Don't write, just log.")
    parser.add_argument(
        "--vault",
        default=None,
        help="Override VAULT_PATH for this run (else read from env / config).",
    )
    args = parser.parse_args(argv)

    if args.vault:
        os.environ["VAULT_PATH"] = str(Path(args.vault).resolve())

    # Re-resolve _annotations_dir() lazily so the env override takes effect.
    directory = _annotations_dir()
    files = _iter_annotation_files(directory)
    if not files:
        print(f"[migrate_v3] no annotation files found under {directory}")
        return 0

    store = AnnotationStore()
    counts: dict[str, int] = {"upgraded": 0, "already_v3": 0, "failed": 0}
    print(f"[migrate_v3] scanning {len(files)} file(s) under {directory} (dry_run={args.dry_run})")

    for path in files:
        result = migrate_file(path, args.dry_run, store)
        if result.startswith("failed"):
            counts["failed"] += 1
            print(f"  - {path.name}: {result}")
        elif result == "already_v3":
            counts["already_v3"] += 1
            print(f"  - {path.name}: already v3, skipped")
        else:
            counts["upgraded"] += 1
            print(f"  - {path.name}: {result}")

    print(
        f"[migrate_v3] done — upgraded={counts['upgraded']} "
        f"already_v3={counts['already_v3']} failed={counts['failed']}"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
