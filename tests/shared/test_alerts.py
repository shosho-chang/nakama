"""Tests for shared/alerts.py."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from shared import alerts


@pytest.fixture
def fake_slack():
    """Patch the lazy-imported FrankySlackBot so no real DM is attempted."""
    bot = MagicMock()
    with patch("agents.franky.slack_bot.FrankySlackBot.from_env", return_value=bot):
        yield bot


def test_alert_info_does_not_call_slack(fake_slack, caplog):
    caplog.set_level("INFO", logger="nakama.alerts")

    alerts.alert("info", "deploy", "thousand-sunny restarted")

    fake_slack.post_plain.assert_not_called()
    assert any("thousand-sunny restarted" in r.message for r in caplog.records)


def test_alert_warn_does_not_call_slack(fake_slack, caplog):
    caplog.set_level("WARNING", logger="nakama.alerts")

    alerts.alert("warn", "publish", "WP request slow")

    fake_slack.post_plain.assert_not_called()


def test_alert_error_calls_slack(fake_slack):
    alerts.alert("error", "backup", "R2 upload failed")

    fake_slack.post_plain.assert_called_once()
    args, kwargs = fake_slack.post_plain.call_args
    posted_text = args[0]
    assert "[backup]" in posted_text
    assert "R2 upload failed" in posted_text
    assert kwargs["context"] == "alert/backup"


def test_alert_error_with_dedupe_key_records_state(fake_slack):
    from shared.state import _get_conn

    alerts.alert(
        "error",
        "backup",
        "R2 upload failed",
        dedupe_key="backup-r2-fail",
    )

    row = (
        _get_conn()
        .execute(
            "SELECT dedup_key, fire_count, state FROM alert_state WHERE dedup_key = ?",
            ("backup-r2-fail",),
        )
        .fetchone()
    )
    assert row is not None
    assert row["fire_count"] == 1
    assert row["state"] == "firing"


def test_alert_error_dedupes_within_window(fake_slack):
    alerts.alert("error", "backup", "fail 1", dedupe_key="dedupe-test")
    alerts.alert("error", "backup", "fail 2", dedupe_key="dedupe-test")
    alerts.alert("error", "backup", "fail 3", dedupe_key="dedupe-test")

    # Only the first call hit Slack; subsequent suppressed
    assert fake_slack.post_plain.call_count == 1


def test_alert_error_no_dedupe_key_always_calls_slack(fake_slack):
    alerts.alert("error", "backup", "fail 1")
    alerts.alert("error", "backup", "fail 2")

    assert fake_slack.post_plain.call_count == 2


def test_alert_error_re_fires_after_dedupe_window(fake_slack):
    """A 0-minute dedupe window means every call fires."""
    alerts.alert("error", "backup", "fail 1", dedupe_key="zero", dedupe_minutes=0)
    alerts.alert("error", "backup", "fail 2", dedupe_key="zero", dedupe_minutes=0)

    assert fake_slack.post_plain.call_count == 2


def test_alert_unknown_severity_raises():
    with pytest.raises(ValueError, match="unknown severity"):
        alerts.alert("critical", "x", "y")  # 'critical' not in Literal['error','warn','info']
