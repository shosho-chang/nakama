"""One-off backfill — migrate v2 task-level calendar projections to v3 per-entry.

ADR-041 v3 (V4): folds each task's legacy ``scheduled``/``scheduled_end``/
``calendar_event_id`` into the ``plan[]`` entry on ``scheduled``'s date, then drops
the task-level keys, so the indexer's transitional dual-read can be retired.
**Idempotent** (``migrate_legacy_projection`` no-ops on already-v3 / no-legacy
tasks) — safe to re-run.

    python -m scripts.backfill_adr041_v3            # apply
    python -m scripts.backfill_adr041_v3 --dry-run  # list candidates only
"""

from __future__ import annotations

import argparse
import sys

import yaml

# CJK task slugs crash the Windows cp1252 console on print — force UTF-8 output.
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

from shared.config import get_vault_path
from shared.weekly_writer import TASKS_DIR, migrate_legacy_projection

_FM = __import__("re").compile(r"^---\n(.*?)\n---", __import__("re").DOTALL)


def _has_legacy(text: str) -> bool:
    m = _FM.match(text)
    if not m:
        return False
    try:
        fm = yaml.safe_load(m.group(1)) or {}
    except yaml.YAMLError:
        return False
    if not isinstance(fm, dict):
        return False
    # legacy = a task-level event link, with no plan[] entry yet carrying `start`
    has_event = bool(str(fm.get("calendar_event_id") or "").strip())
    plan = fm.get("plan") if isinstance(fm.get("plan"), list) else []
    already_v3 = any(isinstance(e, dict) and e.get("start") for e in plan)
    return (has_event or bool(fm.get("scheduled"))) and not already_v3


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true", help="list candidates, write nothing")
    args = ap.parse_args()

    vault = get_vault_path()
    tasks_dir = vault / TASKS_DIR
    if not tasks_dir.exists():
        print(f"no tasks dir: {tasks_dir}")
        return 1

    migrated = skipped = 0
    for p in sorted(tasks_dir.glob("*.md")):
        try:
            text = p.read_text(encoding="utf-8")
        except OSError:
            continue
        if not _has_legacy(text):
            skipped += 1
            continue
        slug = p.stem
        if args.dry_run:
            print(f"WOULD migrate: {slug}")
            migrated += 1
            continue
        try:
            migrate_legacy_projection(vault, slug)
            print(f"migrated: {slug}")
            migrated += 1
        except Exception as exc:  # noqa: BLE001 — report + continue, never abort the batch
            print(f"FAILED  : {slug} — {exc}")
    verb = "would migrate" if args.dry_run else "migrated"
    print(f"\n{verb} {migrated}, skipped {skipped} (already v3 / no legacy).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
