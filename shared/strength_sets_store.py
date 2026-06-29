"""Persistence + row model for Zoro coach strength sets (WP1).

Owns the ``strength_sets`` table (canonical DDL: migrations/018_strength_sets.sql,
mirrored in shared/state.py:_init_tables). Sets are read back from Garmin by
agents/zoro/coach/garmin_read.py; analysis (volume-load / E1RM / progression) is
WP2's job and lives elsewhere — this module only stores and reads rows.

Natural key (activity_id, set_index) -> re-sync is an idempotent INSERT OR IGNORE.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Optional

from shared.state import _get_conn


@dataclass(frozen=True)
class StrengthSet:
    """One recorded set (ACTIVE or REST) from a Garmin strength activity."""

    activity_id: str
    set_index: int                  # raw messageIndex, or array position when absent
    set_type: str                   # 'active' | 'rest'
    exercise_key: Optional[str]     # exercise_name or category (canonical id); None on REST
    category: Optional[str]         # raw exercises[0].category (FIT enum)
    exercise_name: Optional[str]    # raw exercises[0].name (FIT enum, often None)
    reps: Optional[int]             # None on REST / 0 on aborted set
    weight_kg: Optional[float]      # grams/1000; None or 0.0 == no load
    duration_sec: Optional[float]
    is_warmup: bool                 # category == 'WARM_UP'
    performed_at: str               # ISO +08:00 aware
    wkt_step_index: Optional[int]
    source: str = "garmin"          # 'garmin' | 'manual'


def upsert_sets(sets: list[StrengthSet], *, operation_id: str) -> int:
    """Idempotently insert sets; return the number of NEW rows written.

    Re-syncing the same activity is a no-op (UNIQUE(activity_id, set_index) +
    INSERT OR IGNORE), so this is safe to run repeatedly.
    """
    if not sets:
        return 0
    conn = _get_conn()
    now = datetime.now(timezone.utc).isoformat()
    before = conn.total_changes
    for s in sets:
        conn.execute(
            """INSERT OR IGNORE INTO strength_sets
                   (activity_id, set_index, set_type, exercise_key, category,
                    exercise_name, reps, weight_kg, duration_sec, is_warmup,
                    performed_at, wkt_step_index, source, operation_id, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                s.activity_id, s.set_index, s.set_type, s.exercise_key, s.category,
                s.exercise_name, s.reps, s.weight_kg, s.duration_sec, int(s.is_warmup),
                s.performed_at, s.wkt_step_index, s.source, operation_id, now,
            ),
        )
    conn.commit()
    return conn.total_changes - before


def query_sets(
    *,
    activity_id: Optional[str] = None,
    exercise_key: Optional[str] = None,
    since: Optional[str] = None,
    include_rest: bool = True,
) -> list[dict]:
    """Read sets newest-first. ``since`` is an ISO date/datetime string compared
    against ``performed_at``. Filters are ANDed; all optional."""
    conn = _get_conn()
    sql = ["SELECT * FROM strength_sets WHERE 1=1"]
    params: list = []
    if activity_id is not None:
        sql.append("AND activity_id = ?")
        params.append(activity_id)
    if exercise_key is not None:
        sql.append("AND exercise_key = ?")
        params.append(exercise_key)
    if since is not None:
        sql.append("AND performed_at >= ?")
        params.append(since)
    if not include_rest:
        sql.append("AND set_type = 'active'")
    sql.append("ORDER BY performed_at DESC, activity_id, set_index")
    rows = conn.execute(" ".join(sql), params).fetchall()
    return [dict(r) for r in rows]
