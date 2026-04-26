"""Phase 5B-3 anomaly_daemon integration tests.

Coverage (task prompt §5):
- 4 metric ``check_*()`` — positive (3σ trigger), negative (within baseline),
  small-sample gate (no false positive on cold start)
- ``shared.anomaly.is_3sigma_anomaly`` flat-baseline path validated end-to-end
- ``run_once`` empty inputs graceful (no raise, no anomalies, heartbeat success)
- 60-min dedup — second tick within window does NOT re-DM Slack
- Per-check exception isolation — one failing check doesn't break the others
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

import pytest

from agents.franky import anomaly_daemon
from agents.franky.anomaly_daemon import (
    JOB_NAME,
    check_cost_spike,
    check_cron_failure_cluster,
    check_error_rate_spike,
    check_latency_p95_spike,
    run_once,
)
from shared import heartbeat
from shared.log_index import LogIndex
from shared.state import _get_conn

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _insert_api_call(
    *,
    agent: str,
    model: str = "claude-haiku-4-5",
    input_tokens: int = 100,
    output_tokens: int = 50,
    cache_read_tokens: int = 0,
    cache_write_tokens: int = 0,
    latency_ms: int = 100,
    called_at: datetime,
) -> None:
    """Direct insert — backdating ``called_at`` requires bypassing the
    public ``record_api_call`` (which always stamps ``now``)."""
    _get_conn().execute(
        """INSERT INTO api_calls
              (agent, run_id, model, input_tokens, output_tokens,
               cache_read_tokens, cache_write_tokens, latency_ms, called_at)
           VALUES (?, NULL, ?, ?, ?, ?, ?, ?, ?)""",
        (
            agent,
            model,
            input_tokens,
            output_tokens,
            cache_read_tokens,
            cache_write_tokens,
            latency_ms,
            called_at.isoformat(),
        ),
    )
    _get_conn().commit()


def _seed_baseline_calls(
    agent: str,
    *,
    now: datetime,
    hours: int,
    calls_per_hour: int,
    output_tokens: int = 100,
    latency_ms: int = 100,
) -> None:
    """Spread ``calls_per_hour`` evenly across the trailing N hours that fall
    inside the baseline window (i.e. ``[now - 169h, now - 1h)``)."""
    for h in range(2, hours + 2):  # h=2..hours+1 → all in baseline window
        bucket_time = now - timedelta(hours=h)
        for i in range(calls_per_hour):
            _insert_api_call(
                agent=agent,
                output_tokens=output_tokens,
                latency_ms=latency_ms,
                called_at=bucket_time - timedelta(seconds=i * 30),
            )


def _seed_log_row(
    idx: LogIndex,
    *,
    ts: datetime,
    level: str = "INFO",
    logger: str = "nakama.test",
    msg: str = "row",
) -> None:
    idx.insert(ts=ts, level=level, logger=logger, msg=msg, extra={})


@pytest.fixture
def log_index(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> LogIndex:
    """Isolated logs.db routed via NAKAMA_LOG_DB_PATH so the daemon's
    ``LogIndex.from_default_path()`` lands here."""
    db = tmp_path / "test_logs.db"
    monkeypatch.setenv("NAKAMA_LOG_DB_PATH", str(db))
    return LogIndex.from_default_path()


# ---------------------------------------------------------------------------
# check_cost_spike
# ---------------------------------------------------------------------------


def test_cost_spike_empty_api_calls_returns_empty():
    assert check_cost_spike() == []


def test_cost_spike_below_min_n_does_not_alert():
    # 23 baseline samples — below MIN_BASELINE_BUCKETS=24, so even an obvious
    # spike doesn't fire on cold start.
    now = datetime(2026, 4, 26, 12, 0, tzinfo=timezone.utc)
    _seed_baseline_calls("robin", now=now, hours=23, calls_per_hour=1, output_tokens=100)
    # 100x spike in current window — should NOT alert because n<24.
    _insert_api_call(
        agent="robin",
        output_tokens=10_000,
        called_at=now - timedelta(minutes=10),
    )
    assert check_cost_spike(now=now) == []


def test_cost_spike_normal_traffic_no_alert():
    # 168 baseline samples at ~$X/h, current at ~same level → no anomaly.
    now = datetime(2026, 4, 26, 12, 0, tzinfo=timezone.utc)
    _seed_baseline_calls("robin", now=now, hours=168, calls_per_hour=2, output_tokens=100)
    # Current matches baseline tier — within 1.5x of mean, won't trip flat baseline.
    _insert_api_call(agent="robin", output_tokens=100, called_at=now - timedelta(minutes=10))
    _insert_api_call(agent="robin", output_tokens=100, called_at=now - timedelta(minutes=20))
    anomalies = check_cost_spike(now=now)
    assert anomalies == []


def test_cost_spike_flat_baseline_above_1_5x_alerts():
    """Flat baseline (stddev=0) → 1.5x rule. Verify the daemon path uses the
    same shared.anomaly logic end-to-end."""
    now = datetime(2026, 4, 26, 12, 0, tzinfo=timezone.utc)
    _seed_baseline_calls("robin", now=now, hours=24, calls_per_hour=1, output_tokens=100)
    # Current = 5x baseline call volume → far above 1.5x threshold.
    for i in range(5):
        _insert_api_call(
            agent="robin",
            output_tokens=100,
            called_at=now - timedelta(minutes=10 + i),
        )
    anomalies = check_cost_spike(now=now)
    assert len(anomalies) == 1
    assert anomalies[0].metric == "cost_spike"
    assert anomalies[0].target == "robin"
    assert anomalies[0].sample_size == 24


def test_cost_spike_per_agent_only_offending_alerts():
    """Two agents, only one spikes — only the spiker shows up."""
    now = datetime(2026, 4, 26, 12, 0, tzinfo=timezone.utc)
    _seed_baseline_calls("robin", now=now, hours=30, calls_per_hour=1, output_tokens=100)
    _seed_baseline_calls("brook", now=now, hours=30, calls_per_hour=1, output_tokens=100)
    # robin: 10x spike. brook: normal.
    for i in range(10):
        _insert_api_call(
            agent="robin",
            output_tokens=200,
            called_at=now - timedelta(minutes=5 + i),
        )
    _insert_api_call(agent="brook", output_tokens=100, called_at=now - timedelta(minutes=10))
    anomalies = check_cost_spike(now=now)
    assert len(anomalies) == 1
    assert anomalies[0].target == "robin"


# ---------------------------------------------------------------------------
# check_latency_p95_spike
# ---------------------------------------------------------------------------


def test_latency_p95_below_call_count_gate_no_alert():
    # 30 baseline calls — below MIN_BASELINE_LATENCY_CALLS=50, even an
    # obvious p95 spike shouldn't fire (rare-agent protection).
    now = datetime(2026, 4, 26, 12, 0, tzinfo=timezone.utc)
    _seed_baseline_calls("robin", now=now, hours=30, calls_per_hour=1, latency_ms=50)
    _insert_api_call(agent="robin", latency_ms=10_000, called_at=now - timedelta(minutes=5))
    assert check_latency_p95_spike(now=now) == []


def test_latency_p95_spike_alerts_on_jump():
    now = datetime(2026, 4, 26, 12, 0, tzinfo=timezone.utc)
    # Need ≥50 baseline raw calls AND ≥24 hourly p95 buckets.
    _seed_baseline_calls("robin", now=now, hours=30, calls_per_hour=3, latency_ms=100)
    # Current p95 = 5000ms — far above flat 100ms baseline.
    for i in range(20):
        _insert_api_call(agent="robin", latency_ms=5000, called_at=now - timedelta(minutes=5 + i))
    anomalies = check_latency_p95_spike(now=now)
    assert len(anomalies) == 1
    assert anomalies[0].metric == "latency_p95_spike"
    assert anomalies[0].target == "robin"
    assert anomalies[0].current >= 4000


def test_latency_p95_filters_zero_latency_rows():
    """latency_ms=0 means "caller didn't measure" — must not pollute baseline."""
    now = datetime(2026, 4, 26, 12, 0, tzinfo=timezone.utc)
    # Mix 3000 zeros (would dominate p95 baseline at 0) with 60 real calls.
    for i in range(60):
        _insert_api_call(
            agent="robin",
            latency_ms=200,
            called_at=now - timedelta(hours=2, minutes=i),
        )
    for i in range(3000):
        _insert_api_call(
            agent="robin",
            latency_ms=0,
            called_at=now - timedelta(hours=3, minutes=i % 60),
        )
    # If zero-filter is wrong, baseline mean ≈ 0 and current 200ms looks like a spike.
    # With proper filter, baseline ≈ 200ms and 200ms current is normal.
    _insert_api_call(agent="robin", latency_ms=200, called_at=now - timedelta(minutes=5))
    assert check_latency_p95_spike(now=now) == []


# ---------------------------------------------------------------------------
# check_error_rate_spike
# ---------------------------------------------------------------------------


def test_error_rate_spike_empty_logs_returns_empty(log_index):
    assert check_error_rate_spike() == []


def test_error_rate_spike_below_min_active_hours_no_alert(log_index):
    """Only 12 active hours → below MIN_BASELINE_BUCKETS=24, no alert."""
    now = datetime(2026, 4, 26, 12, 0, tzinfo=timezone.utc)
    for h in range(2, 14):  # 12 active baseline hours
        _seed_log_row(log_index, ts=now - timedelta(hours=h), level="INFO")
    # 100 errors in current window — would obviously be a spike if gate were absent.
    for i in range(100):
        _seed_log_row(
            log_index,
            ts=now - timedelta(minutes=5 + i // 10),
            level="ERROR",
        )
    assert check_error_rate_spike(now=now) == []


def test_error_rate_spike_alerts_on_burst(log_index):
    now = datetime(2026, 4, 26, 12, 0, tzinfo=timezone.utc)
    # 30 active baseline hours, all with INFO logs and zero errors.
    for h in range(2, 32):
        for i in range(5):
            _seed_log_row(log_index, ts=now - timedelta(hours=h, minutes=i), level="INFO")
    # 50 ERROR rows in past hour.
    for i in range(50):
        _seed_log_row(log_index, ts=now - timedelta(minutes=10 + i // 10), level="ERROR")
    anomalies = check_error_rate_spike(now=now)
    assert len(anomalies) == 1
    assert anomalies[0].metric == "error_rate_spike"
    assert anomalies[0].target == "_global"
    assert anomalies[0].current == 50.0


def test_error_rate_spike_critical_level_also_counts(log_index):
    """CRITICAL is in the level filter alongside ERROR (task prompt §3 contract)."""
    now = datetime(2026, 4, 26, 12, 0, tzinfo=timezone.utc)
    for h in range(2, 32):
        for i in range(5):
            _seed_log_row(log_index, ts=now - timedelta(hours=h, minutes=i), level="INFO")
    for i in range(40):
        _seed_log_row(log_index, ts=now - timedelta(minutes=10 + i // 10), level="CRITICAL")
    anomalies = check_error_rate_spike(now=now)
    assert len(anomalies) == 1
    assert anomalies[0].current == 40.0


# ---------------------------------------------------------------------------
# check_cron_failure_cluster
# ---------------------------------------------------------------------------


def test_cron_cluster_empty_heartbeat_no_alert():
    assert check_cron_failure_cluster() == []


def test_cron_cluster_one_failing_below_threshold():
    """Single cron streak → covered by probe_cron_freshness, not the daemon."""
    for _ in range(3):
        heartbeat.record_failure("robin-pubmed-digest", "boom")
    assert check_cron_failure_cluster() == []


def test_cron_cluster_two_or_more_simultaneous_alerts():
    for _ in range(3):
        heartbeat.record_failure("robin-pubmed-digest", "boom")
    for _ in range(4):
        heartbeat.record_failure("franky-news-digest", "boom")
    anomalies = check_cron_failure_cluster()
    assert len(anomalies) == 1
    a = anomalies[0]
    assert a.metric == "cron_failure_cluster"
    assert a.target == "_global"
    assert a.current == 2.0
    assert "franky-news-digest" in a.detail["failing_jobs"]
    assert "robin-pubmed-digest" in a.detail["failing_jobs"]
    assert a.detail["min_consecutive_failures"] == 3
    assert a.detail["max_consecutive_failures"] == 4


# ---------------------------------------------------------------------------
# run_once orchestration
# ---------------------------------------------------------------------------


def test_run_once_empty_state_records_heartbeat(log_index):
    """Empty everything: returns [], doesn't crash, writes daemon heartbeat."""
    anomalies = run_once()
    assert anomalies == []
    hb = heartbeat.get_heartbeat(JOB_NAME)
    assert hb is not None
    assert hb.last_status == "success"


def test_run_once_check_exception_isolated(log_index, monkeypatch):
    """One check raising shouldn't black-hole the others. Heartbeat still success."""

    def _boom(*, now=None):
        raise RuntimeError("simulated SQL drift")

    monkeypatch.setattr(anomaly_daemon, "check_cost_spike", _boom)
    # Replace the registry too — run_once iterates _CHECKS by reference.
    monkeypatch.setattr(
        anomaly_daemon,
        "_CHECKS",
        (
            ("cost_spike", _boom),
            ("latency_p95_spike", anomaly_daemon.check_latency_p95_spike),
            ("error_rate_spike", anomaly_daemon.check_error_rate_spike),
            ("cron_failure_cluster", anomaly_daemon.check_cron_failure_cluster),
        ),
    )
    anomalies = run_once()
    assert anomalies == []  # all other checks return empty for empty data
    hb = heartbeat.get_heartbeat(JOB_NAME)
    assert hb is not None and hb.last_status == "success"


def test_run_once_dedup_60min_window(log_index):
    """Same anomaly fired twice within 60 min → second call's alert is suppressed."""
    # Trigger a cron_failure_cluster anomaly twice.
    for _ in range(3):
        heartbeat.record_failure("robin-pubmed-digest", "boom")
    for _ in range(3):
        heartbeat.record_failure("franky-news-digest", "boom")

    with patch("agents.franky.anomaly_daemon.alert", wraps=anomaly_daemon.alert) as alert_mock:
        run_once()
        run_once()
    # First call: alert() invoked once for the cluster.
    # Second call: alert() invoked again, but the dedup guard inside
    # shared.alerts.alert short-circuits the Slack DM.
    assert alert_mock.call_count == 2
    # State sentinel proves dedup absorbed the second one.
    conn = _get_conn()
    row = conn.execute(
        "SELECT fire_count FROM alert_state WHERE dedup_key = ?",
        ("anomaly:cron_failure_cluster:_global",),
    ).fetchone()
    assert row is not None
    # fire_count = 1 because the second alert hit the dedup window and didn't insert.
    assert row["fire_count"] == 1
