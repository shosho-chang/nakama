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


# ---- Phase 6 Slice 2: deterministic dedupe edge cases -----------------------


def test_alert_dedupe_multi_key_independent(fake_slack):
    """Two dedupe keys fire independently — A's window doesn't suppress B."""
    alerts.alert("error", "backup", "A1", dedupe_key="key-A")
    alerts.alert("error", "backup", "B1", dedupe_key="key-B")
    alerts.alert("error", "backup", "A2", dedupe_key="key-A")  # suppressed by A1
    alerts.alert("error", "backup", "B2", dedupe_key="key-B")  # suppressed by B1

    # A1 + B1 = 2 unsuppressed; A2 / B2 dedupe'd
    assert fake_slack.post_plain.call_count == 2


def test_alert_dedupe_fire_count_increments_per_unsuppressed(fake_slack):
    """fire_count counts unsuppressed fires — with dedupe_minutes=0 every call fires."""
    from shared.state import _get_conn

    alerts.alert("error", "backup", "fire 1", dedupe_key="fc", dedupe_minutes=0)
    alerts.alert("error", "backup", "fire 2", dedupe_key="fc", dedupe_minutes=0)
    alerts.alert("error", "backup", "fire 3", dedupe_key="fc", dedupe_minutes=0)

    row = (
        _get_conn()
        .execute("SELECT fire_count FROM alert_state WHERE dedup_key = ?", ("fc",))
        .fetchone()
    )
    assert row["fire_count"] == 3


def test_alert_dedupe_last_message_updates_on_unsuppressed_fire(fake_slack):
    """ON CONFLICT UPDATE rewrites last_message — most recent unsuppressed wins."""
    from shared.state import _get_conn

    alerts.alert("error", "backup", "first one", dedupe_key="msg", dedupe_minutes=0)
    alerts.alert("error", "backup", "middle", dedupe_key="msg", dedupe_minutes=0)
    alerts.alert("error", "backup", "latest", dedupe_key="msg", dedupe_minutes=0)

    row = (
        _get_conn()
        .execute(
            "SELECT last_message, fire_count FROM alert_state WHERE dedup_key = ?",
            ("msg",),
        )
        .fetchone()
    )
    assert row["last_message"] == "latest"
    assert row["fire_count"] == 3


def test_alert_dedupe_expired_window_refires(fake_slack):
    """Once suppress_until is in the past, the same dedupe_key refires + bumps fire_count."""
    from datetime import datetime, timezone

    from shared.state import _get_conn

    # Initial fire registers the row at default 30-min suppression
    alerts.alert("error", "backup", "fire 1", dedupe_key="expired")
    assert fake_slack.post_plain.call_count == 1

    # Force-expire by rewriting suppress_until to a past timestamp
    past = datetime(2020, 1, 1, tzinfo=timezone.utc).isoformat()
    conn = _get_conn()
    conn.execute(
        "UPDATE alert_state SET suppress_until = ? WHERE dedup_key = ?",
        (past, "expired"),
    )
    conn.commit()

    alerts.alert("error", "backup", "fire 2", dedupe_key="expired")
    assert fake_slack.post_plain.call_count == 2  # window stale → fired again

    row = conn.execute(
        "SELECT fire_count, last_message, state FROM alert_state WHERE dedup_key = ?",
        ("expired",),
    ).fetchone()
    assert row["fire_count"] == 2  # ON CONFLICT incremented
    assert row["last_message"] == "fire 2"
    assert row["state"] == "firing"


def test_alert_dedupe_state_always_firing(fake_slack):
    """shared.alerts has no resolve path — state stays 'firing' regardless of fire count.

    The 'resolved' transition is owned by agents/franky/alert_router (ADR-007 §4),
    not this module. Tests that touch alert_state via shared.alerts must not
    expect state to ever flip — the schema's CHECK constraint allows 'resolved'
    so a future Franky integration can use it, but shared.alerts itself only
    writes 'firing'.
    """
    from shared.state import _get_conn

    alerts.alert("error", "backup", "f1", dedupe_key="state-stays")
    alerts.alert("error", "backup", "f2", dedupe_key="state-stays", dedupe_minutes=0)
    alerts.alert("error", "backup", "f3", dedupe_key="state-stays", dedupe_minutes=0)

    row = (
        _get_conn()
        .execute("SELECT state FROM alert_state WHERE dedup_key = ?", ("state-stays",))
        .fetchone()
    )
    assert row["state"] == "firing"


def test_alert_dedupe_long_message_truncated_to_2000(fake_slack):
    """alerts.py slices message[:2000] before persisting to last_message."""
    from shared.state import _get_conn

    long_msg = "x" * 3000
    alerts.alert("error", "backup", long_msg, dedupe_key="trunc")

    row = (
        _get_conn()
        .execute("SELECT last_message FROM alert_state WHERE dedup_key = ?", ("trunc",))
        .fetchone()
    )
    assert len(row["last_message"]) == 2000
    assert row["last_message"] == "x" * 2000


def test_alert_no_dedup_key_does_not_write_alert_state(fake_slack):
    """dedup_key=None bypasses _record_fired — alert_state stays empty."""
    from shared.state import _get_conn

    alerts.alert("error", "backup", "no key", dedupe_key=None)

    fake_slack.post_plain.assert_called_once()
    n = _get_conn().execute("SELECT COUNT(*) AS n FROM alert_state").fetchone()["n"]
    assert n == 0
