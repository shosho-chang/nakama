"""Tests for agents/franky/weekly_digest.py.

Coverage:
- render_slack_text produces a digest with all 5 sections
- summarise_cron happy path + empty DB
- summarise_alerts counts Critical/Warning/Info + firing_now
- summarise_backup counts distinct days + latest
- summarise_cost with zero last-week → delta_pct=0, no div-by-zero
- summarise_cost with positive both → delta_pct computed
- send_digest calls slack_bot.post_plain
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock, patch

from agents.franky import weekly_digest
from agents.franky.weekly_digest import (
    AlertSummary,
    BackupSummary,
    CostSummary,
    CronSummary,
    DigestBundle,
    VPSSnapshot,
    build_digest_text,
    render_slack_text,
    send_digest,
    summarise_alerts,
    summarise_backup,
    summarise_cost,
    summarise_cron,
)


def _now() -> datetime:
    return datetime.now(timezone.utc)


# ---------------------------------------------------------------------------
# Rendering
# ---------------------------------------------------------------------------


def _fake_bundle() -> DigestBundle:
    now = _now()
    return DigestBundle(
        period_start=now - timedelta(days=7),
        period_end=now,
        vps=VPSSnapshot(
            cpu_pct=14.0,
            ram_pct=62.0,
            ram_used_mb=2420,
            ram_total_mb=3910,
            swap_pct=4.0,
            disk_pct=26.0,
        ),
        cron=CronSummary(total=2016, done=2012, failed=4, running=0, success_pct=99.8),
        alerts=AlertSummary(critical_count=0, warning_count=2, info_count=1, firing_now=0),
        backup=BackupSummary(
            days_checked=7,
            days_ok=7,
            latest_status="ok",
            latest_size_mb=142,
            latest_checked_at=now.isoformat(),
        ),
        cost=CostSummary(this_week_usd=3.21, last_week_usd=3.06, delta_pct=4.9),
        operation_id="op_deadbeef",
    )


def test_render_contains_all_5_sections():
    text = render_slack_text(_fake_bundle())
    for marker in ("VPS 快照", "Cron 成功率", "Alert 統計", "R2 備份", "LLM 花費"):
        assert marker in text
    assert "op_deadbeef" in text


def test_render_formats_delta_plus_percent():
    text = render_slack_text(_fake_bundle())
    assert "+4.9%" in text


def test_render_handles_zero_last_week_cost():
    bundle = _fake_bundle()
    bundle_no_last = DigestBundle(
        **{
            **bundle.__dict__,
            "cost": CostSummary(this_week_usd=2.10, last_week_usd=0.0, delta_pct=0.0),
        }
    )
    text = render_slack_text(bundle_no_last)
    # Zero-delta helper text present
    assert "與上週持平" in text
    assert "$2.10" in text


# ---------------------------------------------------------------------------
# summarise_cron
# ---------------------------------------------------------------------------


def test_summarise_cron_empty_db_returns_zeros():
    result = summarise_cron(since=_now() - timedelta(days=7))
    assert result.total == 0
    assert result.done == 0
    assert result.failed == 0
    assert result.success_pct == 100.0  # vacuous truth


def test_summarise_cron_mixed_statuses():
    from shared.state import _get_conn

    conn = _get_conn()
    now_iso = _now().isoformat()
    for status, count in (("done", 10), ("failed", 2), ("running", 1)):
        for _ in range(count):
            conn.execute(
                "INSERT INTO agent_runs (agent, started_at, status) VALUES (?, ?, ?)",
                ("franky", now_iso, status),
            )
    conn.commit()

    result = summarise_cron(since=_now() - timedelta(days=1))
    assert result.total == 13
    assert result.done == 10
    assert result.failed == 2
    assert result.running == 1
    assert abs(result.success_pct - 83.3) < 0.1  # 10 / 12 = 83.3%


def test_summarise_cron_ignores_other_agents():
    from shared.state import _get_conn

    conn = _get_conn()
    now_iso = _now().isoformat()
    conn.execute(
        "INSERT INTO agent_runs (agent, started_at, status) VALUES (?, ?, ?)",
        ("robin", now_iso, "done"),
    )
    conn.commit()

    result = summarise_cron(since=_now() - timedelta(days=1))
    assert result.total == 0  # robin row excluded


# ---------------------------------------------------------------------------
# summarise_alerts
# ---------------------------------------------------------------------------


def _insert_alert_state(*, dedup_key, rule_id, state, fired_at, suppress_until, fire_count=1):
    from shared.state import _get_conn

    conn = _get_conn()
    conn.execute(
        """INSERT INTO alert_state
              (dedup_key, rule_id, last_fired_at, suppress_until, state, last_message, fire_count)
           VALUES (?, ?, ?, ?, ?, 'm', ?)""",
        (dedup_key, rule_id, fired_at, suppress_until, state, fire_count),
    )
    conn.commit()


def test_summarise_alerts_empty():
    result = summarise_alerts(since=_now() - timedelta(days=7))
    assert result.critical_count == 0
    assert result.warning_count == 0
    assert result.info_count == 0
    assert result.firing_now == 0


def test_summarise_alerts_counts_by_suffix_and_state():
    now = _now()
    future = (now + timedelta(minutes=10)).isoformat()
    past = (now - timedelta(hours=1)).isoformat()

    _insert_alert_state(
        dedup_key="a1",
        rule_id="wp_shosho_unhealthy",
        state="firing",
        fired_at=now.isoformat(),
        suppress_until=future,
    )
    _insert_alert_state(
        dedup_key="a2",
        rule_id="disk_warning",
        state="firing",
        fired_at=now.isoformat(),
        suppress_until=past,  # suppress window expired — not firing_now
    )
    _insert_alert_state(
        dedup_key="a3",
        rule_id="wp_shosho_recovered",
        state="resolved",
        fired_at=now.isoformat(),
        suppress_until=past,
    )

    result = summarise_alerts(since=now - timedelta(days=1))
    assert result.critical_count == 1
    assert result.warning_count == 1
    assert result.info_count == 1
    assert result.firing_now == 1  # only a1


# ---------------------------------------------------------------------------
# summarise_backup
# ---------------------------------------------------------------------------


def _insert_backup(*, when: datetime, status: str, size: int | None = 10_000_000):
    from shared.state import _get_conn

    conn = _get_conn()
    conn.execute(
        """INSERT INTO r2_backup_checks
              (checked_at, latest_object_key, latest_object_size,
               latest_object_mtime, status, detail)
           VALUES (?, 'k', ?, ?, ?, '')""",
        (when.isoformat(), size, when.isoformat(), status),
    )
    conn.commit()


def test_summarise_backup_empty():
    result = summarise_backup(since=_now() - timedelta(days=7))
    assert result.days_checked == 0
    assert result.days_ok == 0
    assert result.latest_status is None


def test_summarise_backup_mixed_days():
    now = _now()
    _insert_backup(when=now - timedelta(days=5), status="ok")
    _insert_backup(when=now - timedelta(days=4), status="ok")
    _insert_backup(when=now - timedelta(days=3), status="missing", size=None)
    _insert_backup(when=now - timedelta(days=2), status="ok")
    _insert_backup(when=now - timedelta(days=1), status="ok", size=150 * 1024 * 1024)
    _insert_backup(when=now, status="ok", size=152 * 1024 * 1024)  # latest

    result = summarise_backup(since=now - timedelta(days=7))
    assert result.days_checked == 6
    assert result.days_ok == 5
    assert result.latest_status == "ok"
    assert result.latest_size_mb == 152


# ---------------------------------------------------------------------------
# summarise_cost
# ---------------------------------------------------------------------------


def _insert_api_call(*, model: str, when: datetime, in_tok=1000, out_tok=1000):
    from shared.state import _get_conn

    conn = _get_conn()
    conn.execute(
        """INSERT INTO api_calls
              (agent, model, input_tokens, output_tokens, called_at,
               cache_read_tokens, cache_write_tokens)
           VALUES ('franky', ?, ?, ?, ?, 0, 0)""",
        (model, in_tok, out_tok, when.isoformat()),
    )
    conn.commit()


def test_summarise_cost_no_data_returns_zeros():
    result = summarise_cost(period_end=_now())
    assert result.this_week_usd == 0.0
    assert result.last_week_usd == 0.0
    assert result.delta_pct == 0.0  # div-by-zero guarded


def test_summarise_cost_with_delta():
    now = _now()
    # last week: 2000 out tokens on Sonnet ($15/1M out) = $0.03
    _insert_api_call(model="claude-sonnet-4-6", when=now - timedelta(days=10), out_tok=2000)
    # this week: 4000 out tokens = $0.06 — double
    _insert_api_call(model="claude-sonnet-4-6", when=now - timedelta(days=3), out_tok=4000)

    result = summarise_cost(period_end=now)
    assert result.this_week_usd > result.last_week_usd
    # Expect ~+100% delta
    assert 80 < result.delta_pct < 120


# ---------------------------------------------------------------------------
# Build + send
# ---------------------------------------------------------------------------


def test_build_digest_text_returns_bundle_and_string():
    with patch.object(weekly_digest, "sample_vps") as mock_vps:
        mock_vps.return_value = VPSSnapshot(
            cpu_pct=10,
            ram_pct=50,
            ram_used_mb=1000,
            ram_total_mb=4000,
            swap_pct=0,
            disk_pct=20,
        )
        bundle, text = build_digest_text()
    assert isinstance(bundle, DigestBundle)
    assert "Franky Weekly Digest" in text


def test_send_digest_calls_post_plain():
    bot = MagicMock()
    bot.post_plain.return_value = "1700000000.111"
    with patch.object(weekly_digest, "sample_vps") as mock_vps:
        mock_vps.return_value = VPSSnapshot(
            cpu_pct=10,
            ram_pct=50,
            ram_used_mb=1000,
            ram_total_mb=4000,
            swap_pct=0,
            disk_pct=20,
        )
        result = send_digest(slack_bot=bot)
    bot.post_plain.assert_called_once()
    call = bot.post_plain.call_args
    assert "Franky Weekly Digest" in call.args[0]
    assert call.kwargs.get("context") == "weekly_digest"
    assert result["slack_ts"] == "1700000000.111"
    assert result["operation_id"].startswith("op_")


def test_send_digest_without_slack_bot_uses_from_env(monkeypatch):
    monkeypatch.delenv("SLACK_FRANKY_BOT_TOKEN", raising=False)
    monkeypatch.delenv("SLACK_SHOSHO_USER_ID", raising=False)
    with patch.object(weekly_digest, "sample_vps") as mock_vps:
        mock_vps.return_value = VPSSnapshot(
            cpu_pct=10,
            ram_pct=50,
            ram_used_mb=1000,
            ram_total_mb=4000,
            swap_pct=0,
            disk_pct=20,
        )
        result = send_digest()  # env missing → _NoopSlackStub used
    assert result["slack_ts"] is None  # stub always returns None
