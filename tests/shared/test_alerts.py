"""Tests for shared/alerts.py."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from agents.franky.slack_bot import FrankySlackBot
from shared import alerts


@pytest.fixture
def fake_slack():
    """Patch the lazy-imported FrankySlackBot so no real DM is attempted.

    `spec=FrankySlackBot` (the concrete class — Protocol-typed `SlackPoster`
    doesn't expose methods to `spec=`) catches typo'd method names like
    `bot.post_plian` that would otherwise silently pass.
    """
    bot = MagicMock(spec=FrankySlackBot)
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


# ---- incident archive integration -------------------------------------------


def test_alert_error_archives_to_pending_dir(fake_slack, tmp_path, monkeypatch):
    """Each unsuppressed error fire writes a stub to NAKAMA_INCIDENTS_PENDING_DIR."""
    monkeypatch.setenv("NAKAMA_INCIDENTS_PENDING_DIR", str(tmp_path))
    alerts.alert("error", "backup", "R2 upload failed", dedupe_key="backup-fail")

    files = list(tmp_path.glob("*.md"))
    assert len(files) == 1
    body = files[0].read_text(encoding="utf-8")
    assert 'trigger: "backup-fail"' in body
    assert "severity: SEV-2" in body
    assert "R2 upload failed" in body


def test_alert_error_warn_info_archive_only_error(fake_slack, tmp_path, monkeypatch):
    monkeypatch.setenv("NAKAMA_INCIDENTS_PENDING_DIR", str(tmp_path))
    alerts.alert("info", "deploy", "restarted")
    alerts.alert("warn", "publish", "slow")
    alerts.alert("error", "backup", "fail", dedupe_key="archive-test")

    files = list(tmp_path.glob("*.md"))
    assert len(files) == 1
    assert "archive-test" in files[0].name


def test_alert_error_suppressed_does_not_archive_twice(fake_slack, tmp_path, monkeypatch):
    """Second fire within suppress window must not append to the stub
    (only unsuppressed fires reach archive_incident)."""
    monkeypatch.setenv("NAKAMA_INCIDENTS_PENDING_DIR", str(tmp_path))
    alerts.alert("error", "backup", "fire 1", dedupe_key="dedup-archive")
    alerts.alert("error", "backup", "fire 2", dedupe_key="dedup-archive")  # suppressed

    files = list(tmp_path.glob("*.md"))
    assert len(files) == 1
    body = files[0].read_text(encoding="utf-8")
    assert "fire 1" in body
    assert "fire 2" not in body  # suppressed alerts skip archive
    assert "## Repeat fires" not in body


def test_alert_archive_failure_does_not_crash_alert(fake_slack, tmp_path, monkeypatch, caplog):
    """If archive_incident raises, the alert call still succeeds — _archive
    wraps it in try/except so Slack delivery is never blocked by archive IO."""
    from shared import incident_archive as ia_mod

    def boom(**kwargs):
        raise RuntimeError("disk full")

    monkeypatch.setattr(ia_mod, "archive_incident", boom)
    caplog.set_level("ERROR")

    # Alert should still complete without raising
    alerts.alert("error", "backup", "fail", dedupe_key="failure-test")

    fake_slack.post_plain.assert_called_once()  # Slack DM still went out
    assert any("incident archive failed" in r.message for r in caplog.records)
