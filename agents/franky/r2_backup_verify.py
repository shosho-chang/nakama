"""R2 daily backup verification (ADR-007 §4 / §5).

Runs daily (03:30 台北時間 — after xCloud backup window). Verifies:
1. At least one object exists in R2 under the configured prefix
2. Latest snapshot is not older than `STALE_THRESHOLD_HOURS`
3. Latest snapshot is at least `MIN_BACKUP_SIZE_BYTES` (defends against empty/truncated uploads)

Status ladder (matches `r2_backup_checks.status` CHECK constraint):
    - 'ok'         — latest snapshot present, size OK, age OK
    - 'stale'      — latest snapshot > STALE_THRESHOLD_HOURS old
    - 'too_small'  — latest snapshot under MIN_BACKUP_SIZE_BYTES
    - 'missing'    — bucket empty / no objects under prefix / R2 unavailable

Alert emission (ADR-007 §1 table row 4): 連 2 日失敗 → Critical。
We walk the `r2_backup_checks` table backwards by distinct day and count consecutive
non-ok days including today's row; if >= CONSECUTIVE_FAIL_FOR_CRITICAL, emit one
AlertV1 with `rule_id='r2_backup_missing'`. alert_router's dedup (15-min default)
prevents spam even if verify_once runs multiple times per day.
"""

from __future__ import annotations

import os
import uuid
from datetime import datetime, timezone
from typing import Any

from shared.log import get_logger
from shared.r2_client import R2Client, R2Unavailable
from shared.schemas.franky import AlertV1
from shared.state import _get_conn

logger = get_logger("nakama.franky.r2_backup_verify")


# Thresholds — env-tunable for tests / staging variants.
# `... or "<default>"` handles both unset (None) and explicitly-empty key=
# in .env — the latter would crash the int/float cast (defensive pattern lifted
# from scripts/backup_nakama_state.py after the 04-26 morning incident).
MIN_BACKUP_SIZE_BYTES: int = int(os.getenv("FRANKY_R2_MIN_SIZE_BYTES") or str(1 * 1024 * 1024))
STALE_THRESHOLD_HOURS: float = float(os.getenv("FRANKY_R2_STALE_HOURS") or "25")
DEFAULT_PREFIX: str = os.getenv("FRANKY_R2_PREFIX", "")

# 連 N 日失敗升 Critical（ADR-007 §1 table row 4）
CONSECUTIVE_FAIL_FOR_CRITICAL: int = 2


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _iso(dt: datetime) -> str:
    return dt.isoformat()


def _new_op_id() -> str:
    return f"op_{uuid.uuid4().hex[:8]}"


def _record_check(
    *,
    now: datetime,
    latest_object_key: str | None,
    latest_object_size: int | None,
    latest_object_mtime: datetime | None,
    status: str,
    detail: str,
) -> None:
    conn = _get_conn()
    conn.execute(
        """INSERT INTO r2_backup_checks
              (checked_at, latest_object_key, latest_object_size,
               latest_object_mtime, status, detail)
           VALUES (?, ?, ?, ?, ?, ?)""",
        (
            _iso(now),
            latest_object_key,
            latest_object_size,
            _iso(latest_object_mtime) if latest_object_mtime else None,
            status,
            detail,
        ),
    )
    conn.commit()


def _consecutive_fail_days() -> int:
    """Walk r2_backup_checks backwards; count consecutive non-ok days (distinct date)."""
    conn = _get_conn()
    rows = conn.execute(
        "SELECT status, checked_at FROM r2_backup_checks ORDER BY checked_at DESC LIMIT 60"
    ).fetchall()

    count = 0
    seen_days: set[str] = set()
    for row in rows:
        day = row["checked_at"][:10]  # YYYY-MM-DD — first ISO component is date
        if day in seen_days:
            continue
        seen_days.add(day)
        if row["status"] == "ok":
            break
        count += 1
    return count


def _evaluate(
    *,
    latest_key: str,
    latest_size: int,
    latest_mtime: datetime,
    now: datetime,
) -> tuple[str, str]:
    """Pure function — decide status from gathered facts."""
    age_h = (now - latest_mtime).total_seconds() / 3600
    if age_h > STALE_THRESHOLD_HOURS:
        return (
            "stale",
            (
                f"latest snapshot {age_h:.1f}h old "
                f"(> {STALE_THRESHOLD_HOURS}h threshold); key={latest_key}"
            ),
        )
    if latest_size < MIN_BACKUP_SIZE_BYTES:
        return (
            "too_small",
            f"latest snapshot {latest_size} bytes < min {MIN_BACKUP_SIZE_BYTES}; key={latest_key}",
        )
    return (
        "ok",
        f"latest={latest_key} size={latest_size} age={age_h:.1f}h",
    )


def _maybe_alert(
    *,
    status: str,
    detail: str,
    now: datetime,
    operation_id: str,
) -> AlertV1 | None:
    if status == "ok":
        return None
    # Check DB for consecutive fail days (includes the row we just wrote)
    fails = _consecutive_fail_days()
    if fails < CONSECUTIVE_FAIL_FOR_CRITICAL:
        logger.info(
            "r2_backup fail=%s consecutive=%s below critical threshold (%s)",
            status,
            fails,
            CONSECUTIVE_FAIL_FOR_CRITICAL,
        )
        return None
    return AlertV1(
        rule_id="r2_backup_missing",
        severity="critical",
        title="R2 backup missing / stale",
        message=(f"R2 backup status={status} for {fails} consecutive days. {detail}"),
        fired_at=now,
        dedup_key="r2_backup_missing",
        operation_id=operation_id,
        context={"status": status, "consecutive_fail_days": fails},
    )


# ---------------------------------------------------------------------------
# Public entry
# ---------------------------------------------------------------------------


def verify_once(*, prefix: str | None = None, operation_id: str | None = None) -> dict[str, Any]:
    """Run one verification pass.

    Args:
        prefix:       override default R2 prefix filter (env FRANKY_R2_PREFIX).
        operation_id: supply a shared op id for log correlation; auto-generated if None.

    Returns dict:
        status:        'ok' | 'stale' | 'too_small' | 'missing'
        detail:        human-readable summary
        alert:         AlertV1 | None (populated only after CONSECUTIVE_FAIL_FOR_CRITICAL)
        operation_id:  the op id used
    """
    now = _now()
    op_id = operation_id or _new_op_id()
    effective_prefix = DEFAULT_PREFIX if prefix is None else prefix

    try:
        client = R2Client.from_env()
    except R2Unavailable as exc:
        detail = f"R2 client unavailable: {exc}"
        _record_check(
            now=now,
            latest_object_key=None,
            latest_object_size=None,
            latest_object_mtime=None,
            status="missing",
            detail=detail,
        )
        logger.warning("r2_backup env missing op=%s detail=%s", op_id, detail)
        return {
            "status": "missing",
            "detail": detail,
            "alert": _maybe_alert(status="missing", detail=detail, now=now, operation_id=op_id),
            "operation_id": op_id,
        }

    try:
        objects = client.list_objects(prefix=effective_prefix, max_keys=50)
    except R2Unavailable as exc:
        detail = f"R2 list_objects failed: {exc}"
        _record_check(
            now=now,
            latest_object_key=None,
            latest_object_size=None,
            latest_object_mtime=None,
            status="missing",
            detail=detail,
        )
        logger.warning("r2_backup list failed op=%s detail=%s", op_id, detail)
        return {
            "status": "missing",
            "detail": detail,
            "alert": _maybe_alert(status="missing", detail=detail, now=now, operation_id=op_id),
            "operation_id": op_id,
        }

    if not objects:
        detail = (
            f"R2 bucket={client.bucket} prefix={effective_prefix!r}: no objects (bucket empty?)"
        )
        _record_check(
            now=now,
            latest_object_key=None,
            latest_object_size=None,
            latest_object_mtime=None,
            status="missing",
            detail=detail,
        )
        logger.warning("r2_backup empty op=%s %s", op_id, detail)
        return {
            "status": "missing",
            "detail": detail,
            "alert": _maybe_alert(status="missing", detail=detail, now=now, operation_id=op_id),
            "operation_id": op_id,
        }

    latest = max(objects, key=lambda o: o.last_modified)
    status, detail = _evaluate(
        latest_key=latest.key,
        latest_size=latest.size,
        latest_mtime=latest.last_modified,
        now=now,
    )
    _record_check(
        now=now,
        latest_object_key=latest.key,
        latest_object_size=latest.size,
        latest_object_mtime=latest.last_modified,
        status=status,
        detail=detail,
    )
    logger.info("r2_backup verify op=%s status=%s %s", op_id, status, detail)
    return {
        "status": status,
        "detail": detail,
        "alert": _maybe_alert(status=status, detail=detail, now=now, operation_id=op_id),
        "operation_id": op_id,
    }
