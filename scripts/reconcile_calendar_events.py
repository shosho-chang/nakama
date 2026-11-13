"""One-off backfill — re-link existing Google Calendar events onto scheduled
tasks whose vault record lost the plan[] link (修修 回饋 item 4, option b).

Finds tasks with a ``scheduled`` date but no linked ``plan[]`` entry on that
date, looks up the GCal event by its ``lifeos_task = {slug}@{date}`` idempotency
key, and writes the event id back. **Link only — never creates an event.**
Idempotent (re-running re-links the same events). Default is dry-run; pass
``--apply`` to write.

    python -m scripts.reconcile_calendar_events            # dry-run (list only)
    python -m scripts.reconcile_calendar_events --apply    # write the links

Needs Google Calendar credentials (same as the weekly planner). A missing /
expired token surfaces per-task under "FAILED" rather than aborting the batch.
"""

from __future__ import annotations

import argparse
import sys

# CJK task slugs crash the Windows cp1252 console on print — force UTF-8 output.
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

from shared.calendar_reconcile import reconcile_scheduled_tasks
from shared.config import get_vault_path


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true", help="write the links (default: dry-run)")
    args = ap.parse_args()
    dry_run = not args.apply

    report = reconcile_scheduled_tasks(get_vault_path(), dry_run=dry_run)

    for slug, day, event_id in report.linked:
        print(f"{'WOULD link' if dry_run else 'linked'}: {slug} @ {day} → {event_id}")
    for slug, day in report.missing:
        print(f"no keyed event: {slug} @ {day} (skip — never projected / hand-made)")
    for slug, err in report.errors:
        print(f"FAILED  : {slug} — {err}")

    verb = "would link" if dry_run else "linked"
    print(
        f"\n{verb} {len(report.linked)}, no-event {len(report.missing)}, "
        f"errors {len(report.errors)}."
    )
    if dry_run and report.linked:
        print("re-run with --apply to write these links.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
