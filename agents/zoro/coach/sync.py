"""coach-sync orchestration (WP1): read back strength sets into ``strength_sets``.

Pure-function cron path (bypasses BaseAgent); the heartbeat is recorded by the
CLI wrapper in agents/zoro/__main__.py. A schema mismatch on one activity alerts
and is skipped (never written), so one bad payload doesn't fail the whole sync.
"""

from __future__ import annotations

import re
import uuid
from datetime import date, timedelta
from typing import Optional

from shared.alerts import alert
from shared.strength_sets_store import upsert_sets


def _parse_since(since: Optional[str]) -> date:
    """Accept '8w' / '30d' / ISO date (YYYY-MM-DD). Default 8 weeks."""
    if not since:
        return date.today() - timedelta(weeks=8)
    m = re.fullmatch(r"(\d+)([dw])", since.strip().lower())
    if m:
        n, unit = int(m.group(1)), m.group(2)
        return date.today() - timedelta(days=n * (7 if unit == "w" else 1))
    return date.fromisoformat(since)


def run(since: Optional[str] = None) -> dict:
    """Read back strength sets since ``since`` and upsert into ``strength_sets``."""
    from agents.zoro.coach import garmin_read  # lazy (garminconnect optional dep)

    since_date = _parse_since(since)
    operation_id = f"coach-sync:{uuid.uuid4().hex[:12]}"

    client = garmin_read.login()
    activities = garmin_read.fetch_strength_activities(client, since_date)

    sets_total = sets_new = schema_errors = 0
    for act in activities:
        activity_id = str(act.get("activityId"))
        raw = garmin_read.fetch_exercise_sets(client, activity_id)
        try:
            sets = garmin_read.parse_exercise_sets(raw, activity_id=activity_id)
        except garmin_read.SchemaValidationError as exc:
            schema_errors += 1
            alert(
                "error",
                "garmin-coach",
                f"exerciseSets schema mismatch for activity {activity_id}: {exc}",
                dedupe_key="garmin-exerciseSets-schema",
            )
            continue
        sets_total += len(sets)
        sets_new += upsert_sets(sets, operation_id=operation_id)

    return {
        "since": since_date.isoformat(),
        "activities": len(activities),
        "sets_total": sets_total,
        "sets_new": sets_new,
        "schema_errors": schema_errors,
        "operation_id": operation_id,
    }
