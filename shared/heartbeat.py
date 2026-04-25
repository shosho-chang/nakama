"""Per-job heartbeat: every cron / daemon writes a `success` or `fail` row
on each tick. `/bridge/health` (Phase 3 UI) reads this table to surface "is
job X actually running?" — silent failures (cron quietly stopped, env drift,
permission churn) are otherwise invisible.

Schema lives in `shared/state.py` `_init_tables`. Contract:

- `record_success(job)` — overwrites prior row, resets `consecutive_failures` to 0,
  bumps `last_success_at` and `last_run_at` to now.
- `record_failure(job, error)` — increments `consecutive_failures` (so `/bridge/health`
  can surface streaks), keeps `last_success_at` from the most-recent success row
  (operators care most about "how long since this last worked").
- `list_all()` — every job (sorted alphabetically). Used by `/bridge/health`.
- `list_stale(threshold_minutes)` — jobs whose `last_run_at` is older than the
  threshold. Used by Phase 4 incident-postmortem alert and Phase 5 anomaly daemon.

Conventional `job_name` values (set as constants where consumed):

- `nakama-backup`           — daily 04:00 R2 snapshot
- `franky-health-probe`     — */5 min nakama_gateway probe
- `franky-r2-backup-verify` — every 5 min, drift-checks xCloud + nakama-backup
- `franky-weekly-report`    — Mon 01:00 weekly engineering digest
- `robin-pubmed-digest`     — daily 05:30 PubMed RSS digest
- `zoro-brainstorm-scout`   — daily 05:00 brainstorm topic prompt
- `external-uptime-probe`   — GH Actions every 5 min
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Optional

from shared.log import get_logger
from shared.state import _get_conn

logger = get_logger("nakama.heartbeat")


@dataclass(frozen=True)
class Heartbeat:
    """A row from the `heartbeats` table — one per job_name."""

    job_name: str
    last_success_at: Optional[datetime]
    last_run_at: datetime
    last_status: str  # "success" | "fail"
    last_error: Optional[str]
    consecutive_failures: int
    updated_at: datetime

    @property
    def stale_minutes(self) -> Optional[int]:
        """How long ago this job last RAN (regardless of pass/fail)."""
        delta = datetime.now(timezone.utc) - self.last_run_at
        return int(delta.total_seconds() // 60)

    @property
    def success_age_minutes(self) -> Optional[int]:
        """How long ago this job last *succeeded*. None if never succeeded."""
        if self.last_success_at is None:
            return None
        delta = datetime.now(timezone.utc) - self.last_success_at
        return int(delta.total_seconds() // 60)


def _row_to_heartbeat(row) -> Heartbeat:
    """Convert sqlite3.Row → Heartbeat. Times parsed back to tz-aware UTC."""
    return Heartbeat(
        job_name=row["job_name"],
        last_success_at=_parse_iso(row["last_success_at"]),
        last_run_at=_parse_iso(row["last_run_at"]),
        last_status=row["last_status"],
        last_error=row["last_error"],
        consecutive_failures=row["consecutive_failures"],
        updated_at=_parse_iso(row["updated_at"]),
    )


def _parse_iso(value: str | None) -> datetime | None:
    """Parse ISO 8601 (with or without `Z` suffix) → tz-aware UTC datetime."""
    if value is None:
        return None
    # `datetime.fromisoformat` accepts `+00:00` but not `Z` — normalize first.
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


# ---- write API --------------------------------------------------------------


def record_success(job_name: str) -> None:
    """Mark `job_name` as succeeded right now. Resets consecutive_failures to 0.

    Idempotent: calling twice in the same tick just bumps `last_run_at` /
    `last_success_at` to the second timestamp (no error, no row duplication —
    PRIMARY KEY guarantees a single row per job).
    """
    now = _now_iso()
    conn = _get_conn()
    conn.execute(
        """
        INSERT INTO heartbeats (
            job_name, last_success_at, last_run_at, last_status,
            last_error, consecutive_failures, updated_at
        ) VALUES (?, ?, ?, 'success', NULL, 0, ?)
        ON CONFLICT(job_name) DO UPDATE SET
            last_success_at = excluded.last_success_at,
            last_run_at = excluded.last_run_at,
            last_status = 'success',
            last_error = NULL,
            consecutive_failures = 0,
            updated_at = excluded.updated_at
        """,
        (job_name, now, now, now),
    )
    conn.commit()
    logger.info("heartbeat success", extra={"job": job_name})


def record_failure(job_name: str, error: str) -> None:
    """Mark `job_name` as failed. Preserves `last_success_at` from prior row
    (so operators can see "last worked 3 days ago"). Increments
    `consecutive_failures` — a job failing 5 ticks in a row shows that here,
    not just on the latest row.
    """
    now = _now_iso()
    conn = _get_conn()
    # Get the prior `last_success_at` and `consecutive_failures` (if any).
    prior = conn.execute(
        "SELECT last_success_at, consecutive_failures FROM heartbeats WHERE job_name = ?",
        (job_name,),
    ).fetchone()
    prior_success = prior["last_success_at"] if prior else None
    prior_failures = prior["consecutive_failures"] if prior else 0

    conn.execute(
        """
        INSERT INTO heartbeats (
            job_name, last_success_at, last_run_at, last_status,
            last_error, consecutive_failures, updated_at
        ) VALUES (?, ?, ?, 'fail', ?, ?, ?)
        ON CONFLICT(job_name) DO UPDATE SET
            last_run_at = excluded.last_run_at,
            last_status = 'fail',
            last_error = excluded.last_error,
            consecutive_failures = excluded.consecutive_failures,
            updated_at = excluded.updated_at
        """,
        (job_name, prior_success, now, error[:2000], prior_failures + 1, now),
    )
    conn.commit()
    logger.warning(
        "heartbeat fail",
        extra={"job": job_name, "consecutive_failures": prior_failures + 1, "err": error[:200]},
    )


# ---- read API ---------------------------------------------------------------


def get_heartbeat(job_name: str) -> Heartbeat | None:
    """Return the heartbeat row for `job_name`, or None if never recorded."""
    row = _get_conn().execute("SELECT * FROM heartbeats WHERE job_name = ?", (job_name,)).fetchone()
    return _row_to_heartbeat(row) if row else None


def list_all() -> list[Heartbeat]:
    """All heartbeats, alphabetical by job_name."""
    rows = _get_conn().execute("SELECT * FROM heartbeats ORDER BY job_name").fetchall()
    return [_row_to_heartbeat(row) for row in rows]


def list_stale(threshold_minutes: int) -> list[Heartbeat]:
    """Jobs whose last RUN (regardless of status) is older than threshold."""
    cutoff = datetime.now(timezone.utc) - timedelta(minutes=threshold_minutes)
    rows = (
        _get_conn()
        .execute(
            "SELECT * FROM heartbeats WHERE last_run_at < ? ORDER BY last_run_at ASC",
            (cutoff.isoformat(),),
        )
        .fetchall()
    )
    return [_row_to_heartbeat(row) for row in rows]
