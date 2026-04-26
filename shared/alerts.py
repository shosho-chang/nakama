"""Severity-based alert router with dedupe.

Three severity levels with different routing:

- `error`: Slack DM (with dedupe so a flapping condition doesn't spam) + WARNING log.
- `warn`:  WARNING log only. Aggregated into Franky weekly report later (Phase 4).
- `info`:  INFO log only.

Reuses `agents/franky/slack_bot.FrankySlackBot.from_env()` — same SLACK_FRANKY_BOT_TOKEN
+ SLACK_USER_ID_SHOSHO env contract. If env missing, falls back to log-only stub
(`_NoopSlackStub`) so dev/CI never tries real Slack calls.

Dedupe lives in `alert_state` table (existing, ADR-007 §4 schema). Per-`dedupe_key`
suppression window prevents one error → 100 DMs in a flap. Default 30 min. The
Franky alert_router has its own logic for ADR-007 alert state machine (firing/
resolved transitions); this module is a lighter wrapper for ad-hoc operational
alerts (backup failed, cron stuck, secret missing) outside that pipeline.

Usage:

    from shared.alerts import alert

    # error → DM with dedupe
    alert("error", "backup", "R2 upload failed", dedupe_key="backup-r2-fail")

    # warn → log only (Franky weekly digest aggregates these)
    alert("warn", "publish", "WP request slow", dedupe_key="wp-slow")

    # info → log only
    alert("info", "deploy", "thousand-sunny restarted")
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Literal

from shared.log import get_logger
from shared.state import _get_conn

logger = get_logger("nakama.alerts")

Severity = Literal["error", "warn", "info"]
_DEFAULT_DEDUPE_MINUTES = 30


def alert(
    severity: Severity,
    category: str,
    message: str,
    *,
    dedupe_key: str | None = None,
    dedupe_minutes: int = _DEFAULT_DEDUPE_MINUTES,
) -> None:
    """Route an alert by severity.

    `category` is a free-form tag for log filtering (e.g. "backup", "publish").
    `dedupe_key` (error severity only) suppresses repeat DMs for the same
    underlying condition within `dedupe_minutes`. If omitted, every error DMs
    immediately — fine for one-shot conditions, dangerous for flapping ones.
    """
    log_extra = {"category": category, "severity": severity}

    if severity == "info":
        logger.info(message, extra=log_extra)
        return
    if severity == "warn":
        logger.warning(message, extra=log_extra)
        return
    if severity == "error":
        logger.error(message, extra={**log_extra, "dedupe_key": dedupe_key})
        if dedupe_key and _is_suppressed(dedupe_key):
            logger.info(
                "alert suppressed (within dedupe window)",
                extra={"dedupe_key": dedupe_key},
            )
            return
        _send_slack(category, message)
        if dedupe_key:
            _record_fired(dedupe_key, category, message, dedupe_minutes)
        _archive(
            rule_id=dedupe_key or category,
            severity=severity,
            category=category,
            message=message,
        )
        return
    raise ValueError(f"unknown severity: {severity!r}")


# ---- dedupe via alert_state table -------------------------------------------


def _is_suppressed(dedupe_key: str) -> bool:
    """Return True if this dedupe_key was fired within its suppression window."""
    now_iso = datetime.now(timezone.utc).isoformat()
    row = (
        _get_conn()
        .execute(
            "SELECT suppress_until FROM alert_state WHERE dedup_key = ? AND suppress_until > ?",
            (dedupe_key, now_iso),
        )
        .fetchone()
    )
    return row is not None


def _record_fired(dedupe_key: str, category: str, message: str, dedupe_minutes: int) -> None:
    """Record this firing in alert_state so subsequent calls within the window dedupe."""
    now = datetime.now(timezone.utc)
    suppress_until = (now + timedelta(minutes=dedupe_minutes)).isoformat()
    conn = _get_conn()
    conn.execute(
        """
        INSERT INTO alert_state (
            dedup_key, rule_id, last_fired_at, suppress_until, state, last_message, fire_count
        ) VALUES (?, ?, ?, ?, 'firing', ?, 1)
        ON CONFLICT(dedup_key) DO UPDATE SET
            last_fired_at = excluded.last_fired_at,
            suppress_until = excluded.suppress_until,
            state = 'firing',
            last_message = excluded.last_message,
            fire_count = alert_state.fire_count + 1
        """,
        (dedupe_key, f"shared.alerts/{category}", now.isoformat(), suppress_until, message[:2000]),
    )
    conn.commit()


# ---- Slack DM via reused Franky bot -----------------------------------------


def _send_slack(category: str, message: str) -> None:
    """Send the alert via the shared Franky slack_bot. Failure is logged, not raised."""
    # Lazy import — keeps `shared.alerts` consumable from cron contexts that
    # haven't loaded slack_sdk yet.
    from agents.franky.slack_bot import FrankySlackBot

    bot = FrankySlackBot.from_env()
    bot.post_plain(f":rotating_light: *[{category}]* {message}", context=f"alert/{category}")


# ---- vault archive (Phase 4 incident postmortem auto-archive) ---------------


def _archive(*, rule_id: str, severity: str, category: str, message: str) -> None:
    """Best-effort: write incident stub. Failure logged, never raised — alert
    delivery must not be blocked by archive IO."""
    try:
        from shared.incident_archive import archive_incident

        archive_incident(
            rule_id=rule_id,
            severity=severity,
            title=f"[{category}] {message[:80]}",
            message=message,
            fired_at=datetime.now(timezone.utc),
            context={"category": category},
        )
    except Exception as exc:
        logger.error("incident archive failed: %s", exc, exc_info=True)
