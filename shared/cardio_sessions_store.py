"""Persistence + row model for Zoro coach cardio sessions (有氧一覽).

Summary-level cardio activities (running / cycling / swimming) read back from
Garmin's activity list — no per-set detail. Canonical DDL: migrations/019,
mirrored in shared/state.py:_init_tables. Natural key (activity_id) -> re-sync
is an idempotent INSERT OR IGNORE.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Optional

from shared.state import _get_conn


@dataclass(frozen=True)
class CardioSession:
    activity_id: str
    activity_type: str               # running / cycling / lap_swimming / ...
    performed_at: str                # ISO +08:00
    duration_sec: Optional[float]
    distance_m: Optional[float]
    avg_speed_mps: Optional[float]
    avg_hr: Optional[int]
    max_hr: Optional[int]
    calories: Optional[int]
    source: str = "garmin"


def upsert_sessions(sessions: list[CardioSession], *, operation_id: str) -> int:
    """Idempotently insert cardio sessions; return the number of NEW rows."""
    if not sessions:
        return 0
    conn = _get_conn()
    now = datetime.now(timezone.utc).isoformat()
    before = conn.total_changes
    for s in sessions:
        conn.execute(
            """INSERT OR IGNORE INTO cardio_sessions
                   (activity_id, activity_type, performed_at, duration_sec, distance_m,
                    avg_speed_mps, avg_hr, max_hr, calories, source, operation_id, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                s.activity_id, s.activity_type, s.performed_at, s.duration_sec, s.distance_m,
                s.avg_speed_mps, s.avg_hr, s.max_hr, s.calories, s.source, operation_id, now,
            ),
        )
    conn.commit()
    return conn.total_changes - before


def query_sessions(
    *,
    activity_type: Optional[str] = None,
    since: Optional[str] = None,
) -> list[dict]:
    """Read cardio sessions newest-first. ``since`` is an ISO date/datetime string."""
    conn = _get_conn()
    sql = ["SELECT * FROM cardio_sessions WHERE 1=1"]
    params: list = []
    if activity_type is not None:
        sql.append("AND activity_type = ?")
        params.append(activity_type)
    if since is not None:
        sql.append("AND performed_at >= ?")
        params.append(since)
    sql.append("ORDER BY performed_at DESC")
    return [dict(r) for r in conn.execute(" ".join(sql), params).fetchall()]
