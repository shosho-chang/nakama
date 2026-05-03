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


def _resolve_prefixes() -> list[str]:
    """Resolve which R2 prefixes to verify in one cron pass.

    Resolution order:
        1. `FRANKY_R2_PREFIXES` (CSV, plural) — verify each entry independently
           so per-prefix consecutive-fail counting is isolated. Empty entries
           after split+strip are dropped.
        2. `FRANKY_R2_PREFIX` (singular, legacy) — wrap as single-element list
           for backward compatibility with pre-008 deployments.
        3. Both unset → [""] (one verify over the whole bucket).

    Always returns at least one element. The empty string semantically means
    "no prefix filter — see the entire bucket" and is recorded as `prefix=''`
    in `r2_backup_checks` for backward compatibility with rows written before
    migration 008.
    """
    csv = os.getenv("FRANKY_R2_PREFIXES", "").strip()
    if csv:
        prefixes = [p.strip() for p in csv.split(",") if p.strip()]
        if prefixes:
            return prefixes
    # Singular legacy env or unset → single bucket-wide pass
    return [os.getenv("FRANKY_R2_PREFIX", "")]


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
    prefix: str,
) -> None:
    conn = _get_conn()
    conn.execute(
        """INSERT INTO r2_backup_checks
              (checked_at, latest_object_key, latest_object_size,
               latest_object_mtime, status, detail, prefix)
           VALUES (?, ?, ?, ?, ?, ?, ?)""",
        (
            _iso(now),
            latest_object_key,
            latest_object_size,
            _iso(latest_object_mtime) if latest_object_mtime else None,
            status,
            detail,
            prefix,
        ),
    )
    conn.commit()


def _consecutive_fail_days(prefix: str) -> int:
    """Count consecutive non-ok days for the given prefix.

    Filters by `prefix` so per-prefix verify runs do not cross-pollute each
    other's escalation counts. A daily-fresh `shosho/` row never breaks the
    fail streak counted under `fleet/` and vice versa.
    """
    conn = _get_conn()
    rows = conn.execute(
        """SELECT status, checked_at FROM r2_backup_checks
           WHERE prefix = ?
           ORDER BY checked_at DESC LIMIT 60""",
        (prefix,),
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
    prefix: str,
) -> AlertV1 | None:
    if status == "ok":
        return None
    # Check DB for consecutive fail days for this prefix (includes the row we just wrote)
    fails = _consecutive_fail_days(prefix)
    if fails < CONSECUTIVE_FAIL_FOR_CRITICAL:
        logger.info(
            "r2_backup fail=%s prefix=%r consecutive=%s below critical threshold (%s)",
            status,
            prefix,
            fails,
            CONSECUTIVE_FAIL_FOR_CRITICAL,
        )
        return None
    # dedup_key includes prefix so fleet/ and shosho/ alerts do not collapse
    # against each other in alert_router's 15-min dedup window.
    dedup_suffix = prefix or "default"
    title_suffix = f" ({prefix})" if prefix else ""
    return AlertV1(
        rule_id="r2_backup_missing",
        severity="critical",
        title=f"R2 backup missing / stale{title_suffix}",
        message=(
            f"R2 backup prefix={prefix!r} status={status} for {fails} consecutive days. {detail}"
        ),
        fired_at=now,
        dedup_key=f"r2_backup_missing:{dedup_suffix}",
        operation_id=operation_id,
        context={"status": status, "consecutive_fail_days": fails, "prefix": prefix},
    )


# ---------------------------------------------------------------------------
# Public entry
# ---------------------------------------------------------------------------


def verify_once(*, prefix: str | None = None, operation_id: str | None = None) -> dict[str, Any]:
    """Run one verification pass against a single R2 prefix.

    Args:
        prefix:       override default R2 prefix filter. ``None`` falls back to
                      ``FRANKY_R2_PREFIX`` env (singular, legacy). For multi-prefix
                      cron pass use :func:`verify_all_prefixes` instead.
        operation_id: supply a shared op id for log correlation; auto-generated if None.

    Returns dict:
        status:        'ok' | 'stale' | 'too_small' | 'missing'
        detail:        human-readable summary
        alert:         AlertV1 | None (populated only after CONSECUTIVE_FAIL_FOR_CRITICAL
                       consecutive non-ok days *for this prefix*)
        operation_id:  the op id used
        prefix:        the effective prefix verified (echoed for caller convenience)
    """
    now = _now()
    op_id = operation_id or _new_op_id()
    effective_prefix = DEFAULT_PREFIX if prefix is None else prefix

    def _result(*, status: str, detail: str) -> dict[str, Any]:
        return {
            "status": status,
            "detail": detail,
            "alert": _maybe_alert(
                status=status,
                detail=detail,
                now=now,
                operation_id=op_id,
                prefix=effective_prefix,
            ),
            "operation_id": op_id,
            "prefix": effective_prefix,
        }

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
            prefix=effective_prefix,
        )
        logger.warning(
            "r2_backup env missing op=%s prefix=%r detail=%s", op_id, effective_prefix, detail
        )
        return _result(status="missing", detail=detail)

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
            prefix=effective_prefix,
        )
        logger.warning(
            "r2_backup list failed op=%s prefix=%r detail=%s", op_id, effective_prefix, detail
        )
        return _result(status="missing", detail=detail)

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
            prefix=effective_prefix,
        )
        logger.warning("r2_backup empty op=%s prefix=%r %s", op_id, effective_prefix, detail)
        return _result(status="missing", detail=detail)

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
        prefix=effective_prefix,
    )
    logger.info(
        "r2_backup verify op=%s prefix=%r status=%s %s", op_id, effective_prefix, status, detail
    )
    return _result(status=status, detail=detail)


def verify_all_prefixes(*, operation_id: str | None = None) -> list[dict[str, Any]]:
    """Run :func:`verify_once` for every prefix configured via env.

    Reads :func:`_resolve_prefixes` to decide the prefix list, then runs each
    verify pass independently — separate DB rows, separate consecutive-fail
    counts, separate AlertV1 dedup keys. Cron callers should use this entry
    point so adding a new prefix does not require a code change.

    Returns one result dict per prefix (matches :func:`verify_once` shape).
    All results share the same ``operation_id`` for log correlation across
    a single cron pass.
    """
    op_id = operation_id or _new_op_id()
    return [verify_once(prefix=p, operation_id=op_id) for p in _resolve_prefixes()]
