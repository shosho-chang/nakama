"""Franky alert router — dedup + Slack DM dispatch (ADR-007 §4).

Responsibilities:
1. Receive AlertV1 events (from health_check, r2_backup_verify, etc.)
2. Dedup against `alert_state` table — suppress repeats within dedup_window_seconds
3. Dispatch unsuppressed alerts to Slack DM via SlackPoster
4. On resolved alerts (severity=info, rule ends with _recovered/_resolved), clear firing state

Design:
- Stateless router — all state lives in alert_state table (ADR-007 §4)
- `dispatch(alert)` is idempotent: replaying the same (dedup_key, fired_at) in the same
  dedup window suppresses, which is the desired behavior
- Slack bot is injected; no env lookups inside the router itself
"""

from __future__ import annotations

import sqlite3
from datetime import datetime, timedelta, timezone
from typing import Any

from agents.franky.slack_bot import FrankySlackBot, SlackPoster
from shared.log import get_logger
from shared.schemas.franky import AlertV1
from shared.state import _get_conn

logger = get_logger("nakama.franky.alert_router")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _iso(dt: datetime) -> str:
    return dt.isoformat()


def _is_resolved_alert(alert: AlertV1) -> bool:
    """Heuristic: info-severity alerts whose rule_id ends with _recovered/_resolved
    are treated as resolution events (no dedup, clears firing state)."""
    if alert.severity != "info":
        return False
    return alert.rule_id.endswith("_recovered") or alert.rule_id.endswith("_resolved")


def _read_state(conn: sqlite3.Connection, dedup_key: str) -> dict[str, Any] | None:
    row = conn.execute(
        """SELECT rule_id, last_fired_at, suppress_until, state, last_message, fire_count
           FROM alert_state WHERE dedup_key = ?""",
        (dedup_key,),
    ).fetchone()
    return dict(row) if row else None


def _upsert_state(
    conn: sqlite3.Connection,
    *,
    dedup_key: str,
    rule_id: str,
    last_fired_at: str,
    suppress_until: str,
    state: str,
    last_message: str,
    fire_count: int,
) -> None:
    conn.execute(
        """INSERT INTO alert_state
              (dedup_key, rule_id, last_fired_at, suppress_until, state, last_message, fire_count)
           VALUES (?, ?, ?, ?, ?, ?, ?)
           ON CONFLICT(dedup_key) DO UPDATE SET
              rule_id        = excluded.rule_id,
              last_fired_at  = excluded.last_fired_at,
              suppress_until = excluded.suppress_until,
              state          = excluded.state,
              last_message   = excluded.last_message,
              fire_count     = excluded.fire_count""",
        (dedup_key, rule_id, last_fired_at, suppress_until, state, last_message, fire_count),
    )
    conn.commit()


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def dispatch(alert: AlertV1, *, slack_bot: SlackPoster | None = None) -> dict[str, Any]:
    """Process one alert; dedup + (maybe) DM.

    Returns dict:
        action:     'sent' | 'suppressed' | 'resolved'
        slack_ts:   str | None   (Slack message ts when sent, None otherwise)
        fire_count: int          (running total for this dedup_key, updated after this call)
    """
    conn = _get_conn()
    now = _now()
    now_iso = _iso(now)
    prev = _read_state(conn, alert.dedup_key)

    # --- Resolved path: info + _recovered/_resolved suffix, no dedup, clears firing state ---
    if _is_resolved_alert(alert):
        slack_ts = slack_bot.post_alert(alert) if slack_bot is not None else None
        new_fire_count = (prev or {}).get("fire_count", 0)
        # Mark state resolved if we had a firing row; keep last_fired_at as the firing timestamp
        # (don't overwrite with resolution time — that would lose signal on "how long was it down").
        _upsert_state(
            conn,
            dedup_key=alert.dedup_key,
            rule_id=alert.rule_id,
            last_fired_at=(prev or {}).get("last_fired_at", now_iso),
            suppress_until=now_iso,
            state="resolved",
            last_message=alert.message,
            fire_count=new_fire_count,
        )
        logger.info(
            "alert resolved rule=%s dedup=%s prior_fires=%s",
            alert.rule_id,
            alert.dedup_key,
            new_fire_count,
        )
        return {"action": "resolved", "slack_ts": slack_ts, "fire_count": new_fire_count}

    # --- Firing path ---
    if prev is not None and prev["state"] == "firing":
        try:
            suppress_until = datetime.fromisoformat(prev["suppress_until"])
        except ValueError:
            # Corrupt state row — treat as expired, fall through to send
            suppress_until = now - timedelta(seconds=1)

        if suppress_until > now:
            new_fire_count = int(prev["fire_count"]) + 1
            _upsert_state(
                conn,
                dedup_key=alert.dedup_key,
                rule_id=alert.rule_id,
                last_fired_at=now_iso,
                suppress_until=prev["suppress_until"],
                state="firing",
                last_message=alert.message,
                fire_count=new_fire_count,
            )
            logger.info(
                "alert suppressed rule=%s dedup=%s fire_count=%s suppress_until=%s",
                alert.rule_id,
                alert.dedup_key,
                new_fire_count,
                prev["suppress_until"],
            )
            return {
                "action": "suppressed",
                "slack_ts": None,
                "fire_count": new_fire_count,
            }

    # --- Send: new dedup_key OR prior window expired OR prior state=resolved ---
    slack_ts = slack_bot.post_alert(alert) if slack_bot is not None else None
    new_suppress_until = _iso(now + timedelta(seconds=alert.dedup_window_seconds))
    new_fire_count = (int(prev["fire_count"]) + 1) if prev else 1
    _upsert_state(
        conn,
        dedup_key=alert.dedup_key,
        rule_id=alert.rule_id,
        last_fired_at=now_iso,
        suppress_until=new_suppress_until,
        state="firing",
        last_message=alert.message,
        fire_count=new_fire_count,
    )
    logger.info(
        "alert sent rule=%s dedup=%s severity=%s fire_count=%s slack_ts=%s",
        alert.rule_id,
        alert.dedup_key,
        alert.severity,
        new_fire_count,
        slack_ts,
    )
    return {"action": "sent", "slack_ts": slack_ts, "fire_count": new_fire_count}


def dispatch_all(
    alerts: list[AlertV1],
    *,
    slack_bot: SlackPoster | None = None,
) -> list[dict[str, Any]]:
    """Convenience wrapper — dispatch a batch of alerts in order, collect results."""
    return [dispatch(a, slack_bot=slack_bot) for a in alerts]


def make_default_sink(*, slack_bot: SlackPoster | None = None):
    """Return an `AlertSink` (Callable[[AlertV1], None]) backed by dispatch.

    If `slack_bot` is None, constructs one via `FrankySlackBot.from_env()` (which
    itself degrades to a log-only stub when Slack env is missing).
    """
    bot = slack_bot if slack_bot is not None else FrankySlackBot.from_env()

    def _sink(alert: AlertV1) -> None:
        dispatch(alert, slack_bot=bot)

    return _sink
