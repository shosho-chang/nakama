"""Tests for agents/franky/alert_router.py (ADR-007 §4 dedup).

Verification matrix:
- First fire sends + records firing state
- Same dedup_key within window → suppressed, fire_count increments, no Slack call
- After dedup window expires → sends again, new suppress_until
- Resolved alert (severity=info, rule ends _recovered) → sends once, state=resolved
- dispatch_all batches alerts in order
- make_default_sink wraps dispatch and returns a Callable (no side-effects beyond dispatch)
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock

import pytest

from agents.franky.alert_router import dispatch, dispatch_all, make_default_sink
from shared.schemas.franky import AlertV1


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _make_alert(
    *,
    rule_id: str = "wp_shosho_unhealthy",
    severity: str = "critical",
    dedup_window_seconds: int = 900,
    dedup_key: str | None = None,
) -> AlertV1:
    return AlertV1(
        rule_id=rule_id,
        severity=severity,  # type: ignore[arg-type]
        title=f"{rule_id} title",
        message=f"{rule_id} message",
        fired_at=_now(),
        dedup_key=dedup_key or rule_id,
        dedup_window_seconds=dedup_window_seconds,
        operation_id="op_12345678",
    )


# ---------------------------------------------------------------------------
# Dedup core
# ---------------------------------------------------------------------------


def test_first_fire_sends_and_records_firing():
    bot = MagicMock()
    bot.post_alert.return_value = "1234567890.123456"
    result = dispatch(_make_alert(), slack_bot=bot)
    assert result["action"] == "sent"
    assert result["slack_ts"] == "1234567890.123456"
    assert result["fire_count"] == 1
    bot.post_alert.assert_called_once()


def test_critical_alert_archives_to_pending_dir(tmp_path, monkeypatch):
    """Critical AlertV1 dispatch creates an incident stub."""
    monkeypatch.setenv("NAKAMA_INCIDENTS_PENDING_DIR", str(tmp_path))
    bot = MagicMock()
    dispatch(_make_alert(rule_id="wp_fleet_unhealthy", severity="critical"), slack_bot=bot)

    files = list(tmp_path.glob("*.md"))
    assert len(files) == 1
    body = files[0].read_text(encoding="utf-8")
    assert 'trigger: "wp_fleet_unhealthy"' in body
    assert "severity: SEV-1" in body  # critical → SEV-1


def test_warning_alert_does_not_archive(tmp_path, monkeypatch):
    monkeypatch.setenv("NAKAMA_INCIDENTS_PENDING_DIR", str(tmp_path))
    bot = MagicMock()
    dispatch(_make_alert(rule_id="wp_warning_slow", severity="warning"), slack_bot=bot)

    assert list(tmp_path.glob("*.md")) == []


def test_suppressed_critical_does_not_archive(tmp_path, monkeypatch):
    """Within dedup window, repeats are suppressed and never reach archive."""
    monkeypatch.setenv("NAKAMA_INCIDENTS_PENDING_DIR", str(tmp_path))
    bot = MagicMock()
    dispatch(_make_alert(rule_id="cron_stale", severity="critical"), slack_bot=bot)
    dispatch(_make_alert(rule_id="cron_stale", severity="critical"), slack_bot=bot)

    files = list(tmp_path.glob("*.md"))
    assert len(files) == 1
    body = files[0].read_text(encoding="utf-8")
    # Only the first fire was recorded; the second was suppressed before archive
    assert "## Repeat fires" not in body


def test_repeated_fire_within_window_suppresses():
    bot = MagicMock()
    bot.post_alert.return_value = "ts1"

    first = dispatch(_make_alert(), slack_bot=bot)
    second = dispatch(_make_alert(), slack_bot=bot)
    third = dispatch(_make_alert(), slack_bot=bot)

    assert first["action"] == "sent"
    assert second["action"] == "suppressed"
    assert third["action"] == "suppressed"
    assert second["fire_count"] == 2
    assert third["fire_count"] == 3
    assert bot.post_alert.call_count == 1  # only the first sent


def test_distinct_dedup_keys_both_send():
    bot = MagicMock()
    r1 = dispatch(_make_alert(dedup_key="wp_shosho_unhealthy"), slack_bot=bot)
    r2 = dispatch(_make_alert(dedup_key="wp_fleet_unhealthy"), slack_bot=bot)
    assert r1["action"] == "sent"
    assert r2["action"] == "sent"
    assert bot.post_alert.call_count == 2


def test_after_window_expires_sends_again(monkeypatch):
    """After dedup_window_seconds elapse, suppress_until should be in the past → send again."""
    bot = MagicMock()
    # First fire
    dispatch(_make_alert(dedup_window_seconds=60), slack_bot=bot)

    # Rewind suppress_until by 2 minutes to simulate the window having expired
    from shared.state import _get_conn

    conn = _get_conn()
    past = (_now() - timedelta(minutes=2)).isoformat()
    conn.execute(
        "UPDATE alert_state SET suppress_until = ? WHERE dedup_key = ?",
        (past, "wp_shosho_unhealthy"),
    )
    conn.commit()

    bot.reset_mock()
    result = dispatch(_make_alert(dedup_window_seconds=60), slack_bot=bot)
    assert result["action"] == "sent"
    assert result["fire_count"] == 2
    bot.post_alert.assert_called_once()


# ---------------------------------------------------------------------------
# Resolved path
# ---------------------------------------------------------------------------


def test_resolved_alert_sends_once_and_updates_state():
    bot = MagicMock()
    bot.post_alert.return_value = "ts_resolved"

    # Firing first
    dispatch(_make_alert(), slack_bot=bot)
    # Resolved
    resolved = _make_alert(
        rule_id="wp_shosho_recovered",
        severity="info",
        dedup_key="wp_shosho_recovered",
    )
    bot.reset_mock()
    result = dispatch(resolved, slack_bot=bot)
    assert result["action"] == "resolved"
    assert result["slack_ts"] == "ts_resolved"
    bot.post_alert.assert_called_once()

    # Subsequent resolved dispatches should still send (no dedup on resolved)
    bot.reset_mock()
    result2 = dispatch(resolved, slack_bot=bot)
    assert result2["action"] == "resolved"
    assert bot.post_alert.called


def test_resolved_without_slack_bot_still_runs():
    resolved = _make_alert(
        rule_id="wp_shosho_recovered", severity="info", dedup_key="wp_shosho_recovered"
    )
    result = dispatch(resolved, slack_bot=None)
    assert result["action"] == "resolved"
    assert result["slack_ts"] is None


# ---------------------------------------------------------------------------
# Dispatch-all + default sink
# ---------------------------------------------------------------------------


def test_dispatch_all_preserves_order():
    bot = MagicMock()
    bot.post_alert.return_value = "ts"
    alerts = [_make_alert(dedup_key=f"rule_{i}", rule_id=f"test_rule_{i}") for i in range(3)]
    results = dispatch_all(alerts, slack_bot=bot)
    assert len(results) == 3
    assert all(r["action"] == "sent" for r in results)


def test_make_default_sink_returns_callable_and_writes_state():
    bot = MagicMock()
    bot.post_alert.return_value = "ts"
    sink = make_default_sink(slack_bot=bot)

    alert = _make_alert()
    sink(alert)  # should not raise
    bot.post_alert.assert_called_once()

    # Second call in same window suppresses — sink still returns None
    bot.reset_mock()
    sink(alert)
    bot.post_alert.assert_not_called()


def test_dispatch_with_none_slack_bot_updates_state():
    """Running without Slack (CI, dev) still exercises dedup bookkeeping."""
    r1 = dispatch(_make_alert(), slack_bot=None)
    r2 = dispatch(_make_alert(), slack_bot=None)
    assert r1["action"] == "sent"
    assert r2["action"] == "suppressed"
    assert r1["slack_ts"] is None


# ---------------------------------------------------------------------------
# Edge cases
# ---------------------------------------------------------------------------


def test_corrupt_suppress_until_falls_through_to_send():
    """Bad ISO string in DB shouldn't crash — router should treat as expired."""
    from shared.state import _get_conn

    bot = MagicMock()
    dispatch(_make_alert(), slack_bot=bot)
    conn = _get_conn()
    conn.execute(
        "UPDATE alert_state SET suppress_until = 'not-a-date' WHERE dedup_key = ?",
        ("wp_shosho_unhealthy",),
    )
    conn.commit()

    bot.reset_mock()
    result = dispatch(_make_alert(), slack_bot=bot)
    assert result["action"] == "sent"
    bot.post_alert.assert_called_once()


@pytest.mark.parametrize("severity", ["critical", "warning"])
def test_non_info_alert_never_takes_resolved_path(severity):
    bot = MagicMock()
    alert = _make_alert(
        rule_id="wp_shosho_recovered",  # suffix implies resolved
        severity=severity,
        dedup_key="wp_shosho_recovered",
    )
    result = dispatch(alert, slack_bot=bot)
    assert result["action"] == "sent"  # NOT resolved, because severity != info
