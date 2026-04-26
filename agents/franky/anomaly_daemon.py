"""Phase 5B-3 anomaly daemon — 4 statistical checks over agent metrics.

Cron entrypoint: ``python -m agents.franky anomaly`` every 15 min.

Compares the past 60 minutes against a trailing 7-day hourly baseline using a
one-sided 3σ rule (see ``shared/anomaly.py`` for the math + edge cases). When
something spikes, dispatch a Slack DM via ``shared.alerts.alert`` with a
60-minute dedup window so a sustained anomaly pages once per hour, not 4×.

Four checks (registered in ``run_once``):

1. ``check_cost_spike`` — per-agent USD cost (``shared.pricing.calc_cost`` over
   ``api_calls`` token sums), 60min vs 7d hourly baseline, gate ≥24 baseline
   hours per agent.
2. ``check_latency_p95_spike`` — per-agent p95 latency_ms, computed via
   nearest-rank from ``api_calls.latency_ms`` (>0 only — caller didn't measure
   means we can't trust it). Two gates: ≥50 raw baseline calls AND ≥24
   hourly p95 baseline samples.
3. ``check_error_rate_spike`` — global ERROR/CRITICAL log count via
   ``LogIndex.count_by_hour``. ``target='_global'``; gate ≥24 active baseline
   hours (silent hours don't count as samples — fresh-deploy safety).
4. ``check_cron_failure_cluster`` — heartbeat snapshot, fires when ≥2 cron
   jobs simultaneously have ``consecutive_failures >= 3``. State-based, no
   baseline; alone it reads as a single-cron blip (the freshness probe handles
   that), but ≥2 simultaneous = systemic (VPS reboot, network down, env drift).

Each ``check_*`` returns ``list[AnomalyV1]``. ``run_once`` aggregates,
dispatches alerts (with per-check exception isolation so one sql error
doesn't black-hole the others), then writes a heartbeat success row keyed
``nakama-anomaly-daemon``.
"""

from __future__ import annotations

import math
import sqlite3
import time
import uuid
from collections.abc import Callable
from datetime import datetime, timedelta, timezone

from shared import heartbeat
from shared.alerts import alert
from shared.anomaly import is_3sigma_anomaly, rolling_baseline
from shared.heartbeat import record_success
from shared.log import get_logger
from shared.log_index import LogIndex
from shared.pricing import calc_cost
from shared.schemas.franky import AnomalyV1
from shared.state import _get_conn

logger = get_logger("nakama.franky.anomaly_daemon")

# Heartbeat job name; CRON_SCHEDULES in health_check.py registers the same key.
JOB_NAME = "nakama-anomaly-daemon"

# Window sizes — frozen by Q1 / Q2 in the task prompt (defaults accepted).
CURRENT_WINDOW_HOURS = 1
BASELINE_WINDOW_HOURS = 7 * 24

# Sample-size gates per metric (task prompt §4.1).
MIN_BASELINE_BUCKETS = 24
MIN_BASELINE_LATENCY_CALLS = 50
MIN_CRON_CLUSTER_SIZE = 2

# Slack DM dedup window for repeating anomalies (Q4 = 60 min).
DEDUP_MINUTES = 60


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _p95(values: list[int] | list[float]) -> float:
    """Nearest-rank p95. ``values`` need not be sorted."""
    if not values:
        return 0.0
    sorted_v = sorted(values)
    rank = max(1, math.ceil(0.95 * len(sorted_v)))
    return float(sorted_v[rank - 1])


def _windows(now: datetime) -> tuple[datetime, datetime, datetime]:
    """Return ``(current_start, baseline_start, baseline_end)``.

    Baseline ends where current starts (no overlap), so a real spike in the
    current window can't pollute its own baseline.
    """
    current_start = now - timedelta(hours=CURRENT_WINDOW_HOURS)
    baseline_end = current_start
    baseline_start = baseline_end - timedelta(hours=BASELINE_WINDOW_HOURS)
    return current_start, baseline_start, baseline_end


def _format_alert(a: AnomalyV1) -> str:
    """Slack DM body. Plain text — Slack mrkdwn ``*bold*`` leaks on CJK
    so we keep formatting minimal (see feedback_slack_cjk_mrkdwn)."""
    if a.metric == "cron_failure_cluster":
        jobs = a.detail.get("failing_jobs", "")
        return (
            f"anomaly cron_failure_cluster: {int(a.current)} cron jobs "
            f"with ≥3 consecutive failures ({jobs})"
        )
    if a.baseline_stddev > 0:
        z = (a.current - a.baseline_mean) / a.baseline_stddev
        z_str = f"z={z:.1f}"
    else:
        z_str = "flat-baseline"
    return (
        f"anomaly {a.metric} for {a.target}: "
        f"current={a.current:.3f}, baseline mean={a.baseline_mean:.3f} "
        f"± {a.baseline_stddev:.3f} (n={a.sample_size}, {z_str})"
    )


# ---------------------------------------------------------------------------
# Check #1 — cost spike (per agent, USD)
# ---------------------------------------------------------------------------


def check_cost_spike(*, now: datetime | None = None) -> list[AnomalyV1]:
    """Per-agent USD cost in past hour vs 7d hourly baseline.

    Token sums grouped by (hour, agent, model) — pricing is per-model, so
    we can't aggregate across models before applying ``calc_cost``. Then
    Python sums per-(hour, agent) for the baseline samples and per-agent
    for the current window.
    """
    now = now or _now()
    current_start, baseline_start, baseline_end = _windows(now)

    conn = _get_conn()

    baseline_rows = conn.execute(
        """SELECT
               strftime('%Y-%m-%dT%H', called_at) AS hour_bucket,
               agent,
               model,
               SUM(input_tokens)        AS in_tok,
               SUM(output_tokens)       AS out_tok,
               SUM(cache_read_tokens)   AS cr_tok,
               SUM(cache_write_tokens)  AS cw_tok
           FROM api_calls
           WHERE called_at >= ? AND called_at < ?
           GROUP BY hour_bucket, agent, model""",
        (baseline_start.isoformat(), baseline_end.isoformat()),
    ).fetchall()

    # agent → {hour_bucket: cumulative cost across models that hour}
    baseline_by_agent: dict[str, dict[str, float]] = {}
    for row in baseline_rows:
        usd = calc_cost(
            row["model"],
            input_tokens=row["in_tok"] or 0,
            output_tokens=row["out_tok"] or 0,
            cache_read_tokens=row["cr_tok"] or 0,
            cache_write_tokens=row["cw_tok"] or 0,
        )
        bucket = baseline_by_agent.setdefault(row["agent"], {})
        bucket[row["hour_bucket"]] = bucket.get(row["hour_bucket"], 0.0) + usd

    current_rows = conn.execute(
        """SELECT agent, model,
                  SUM(input_tokens)        AS in_tok,
                  SUM(output_tokens)       AS out_tok,
                  SUM(cache_read_tokens)   AS cr_tok,
                  SUM(cache_write_tokens)  AS cw_tok
           FROM api_calls
           WHERE called_at >= ?
           GROUP BY agent, model""",
        (current_start.isoformat(),),
    ).fetchall()

    current_by_agent: dict[str, float] = {}
    for row in current_rows:
        usd = calc_cost(
            row["model"],
            input_tokens=row["in_tok"] or 0,
            output_tokens=row["out_tok"] or 0,
            cache_read_tokens=row["cr_tok"] or 0,
            cache_write_tokens=row["cw_tok"] or 0,
        )
        current_by_agent[row["agent"]] = current_by_agent.get(row["agent"], 0.0) + usd

    anomalies: list[AnomalyV1] = []
    for agent, current_cost in current_by_agent.items():
        samples = list(baseline_by_agent.get(agent, {}).values())
        baseline = rolling_baseline(samples)
        if not is_3sigma_anomaly(current_cost, baseline, min_n=MIN_BASELINE_BUCKETS):
            continue
        anomalies.append(
            AnomalyV1(
                metric="cost_spike",
                target=agent,
                current=round(current_cost, 6),
                baseline_mean=round(baseline.mean, 6),
                baseline_stddev=round(baseline.stddev, 6),
                sample_size=baseline.n,
                detail={
                    "window_hours": CURRENT_WINDOW_HOURS,
                    "baseline_hours": BASELINE_WINDOW_HOURS,
                    "current_usd": round(current_cost, 6),
                    "baseline_mean_usd": round(baseline.mean, 6),
                },
                detected_at=now,
            )
        )
    return anomalies


# ---------------------------------------------------------------------------
# Check #2 — latency p95 spike (per agent, ms)
# ---------------------------------------------------------------------------


def check_latency_p95_spike(*, now: datetime | None = None) -> list[AnomalyV1]:
    """Per-agent p95 latency_ms in past hour vs 7d hourly p95 baseline.

    Two-gate: ≥``MIN_BASELINE_LATENCY_CALLS`` raw calls in the baseline
    window AND ≥``MIN_BASELINE_BUCKETS`` non-empty hourly p95 samples.
    Both gates protect against agents that run rarely (a once-a-day cron
    spiking on its first call doesn't get to alert).

    ``latency_ms = 0`` rows are excluded throughout; ``record_api_call``
    docstring documents 0 as "caller didn't measure", so they'd skew p95
    downward.
    """
    now = now or _now()
    current_start, baseline_start, baseline_end = _windows(now)

    conn = _get_conn()

    baseline_rows = conn.execute(
        """SELECT
               strftime('%Y-%m-%dT%H', called_at) AS hour_bucket,
               agent,
               latency_ms
           FROM api_calls
           WHERE called_at >= ? AND called_at < ? AND latency_ms > 0""",
        (baseline_start.isoformat(), baseline_end.isoformat()),
    ).fetchall()

    by_agent_hour: dict[str, dict[str, list[int]]] = {}
    by_agent_total: dict[str, int] = {}
    for row in baseline_rows:
        agent = row["agent"]
        by_agent_total[agent] = by_agent_total.get(agent, 0) + 1
        by_agent_hour.setdefault(agent, {}).setdefault(row["hour_bucket"], []).append(
            row["latency_ms"]
        )

    current_rows = conn.execute(
        """SELECT agent, latency_ms
           FROM api_calls
           WHERE called_at >= ? AND latency_ms > 0""",
        (current_start.isoformat(),),
    ).fetchall()
    current_by_agent: dict[str, list[int]] = {}
    for row in current_rows:
        current_by_agent.setdefault(row["agent"], []).append(row["latency_ms"])

    anomalies: list[AnomalyV1] = []
    for agent, latencies in current_by_agent.items():
        if by_agent_total.get(agent, 0) < MIN_BASELINE_LATENCY_CALLS:
            continue
        current_p95 = _p95(latencies)
        # Hourly p95 baseline samples — skip empty hours so cold-start
        # weeks don't dilute stddev with a sea of zeros.
        hourly_p95s = [
            _p95(buckets) for buckets in by_agent_hour.get(agent, {}).values() if buckets
        ]
        baseline = rolling_baseline(hourly_p95s)
        if not is_3sigma_anomaly(current_p95, baseline, min_n=MIN_BASELINE_BUCKETS):
            continue
        anomalies.append(
            AnomalyV1(
                metric="latency_p95_spike",
                target=agent,
                current=round(current_p95, 1),
                baseline_mean=round(baseline.mean, 1),
                baseline_stddev=round(baseline.stddev, 1),
                sample_size=baseline.n,
                detail={
                    "window_hours": CURRENT_WINDOW_HOURS,
                    "current_p95_ms": round(current_p95, 1),
                    "baseline_mean_p95_ms": round(baseline.mean, 1),
                    "current_call_count": len(latencies),
                    "baseline_total_calls": by_agent_total[agent],
                },
                detected_at=now,
            )
        )
    return anomalies


# ---------------------------------------------------------------------------
# Check #3 — error rate spike (global, log count)
# ---------------------------------------------------------------------------


def check_error_rate_spike(*, now: datetime | None = None) -> list[AnomalyV1]:
    """ERROR/CRITICAL log count past hour vs 7d hourly baseline (global).

    Per task prompt §4.1 — first-iteration is global, not per-logger; the
    Slack DM is enough to send the operator into ``/bridge/logs`` to
    drill down. Per-agent split is a follow-up if false positives accumulate.

    The "active baseline hours" trick: only count hours that produced *any*
    log row as baseline samples. Otherwise a freshly-deployed daemon shows
    160 zero-error baseline buckets + 8 hours of real activity, stddev is
    near zero, and the first real ERROR fires false-positive.
    """
    now = now or _now()
    current_start, baseline_start, baseline_end = _windows(now)

    try:
        log_index = LogIndex.from_default_path()
    except RuntimeError:
        # FTS5 not built into this sqlite — anomaly daemon shouldn't
        # crash the cron; just skip this metric.
        logger.warning("logs.db unavailable, skipping error_rate_spike")
        return []

    try:
        active = log_index.count_by_hour(since=baseline_start, until=baseline_end)
        baseline_errors = log_index.count_by_hour(
            since=baseline_start,
            until=baseline_end,
            levels=("ERROR", "CRITICAL"),
        )
        current_errors = log_index.count_by_hour(
            since=current_start,
            until=now,
            levels=("ERROR", "CRITICAL"),
        )
    except sqlite3.OperationalError:
        logger.warning("logs.db query failed, skipping error_rate_spike", exc_info=True)
        return []

    # One sample per active hour: error count for that hour (default 0
    # for hours with activity but no errors).
    samples = [float(baseline_errors.get(h, 0)) for h in active]
    baseline = rolling_baseline(samples)
    current = float(sum(current_errors.values()))

    if not is_3sigma_anomaly(current, baseline, min_n=MIN_BASELINE_BUCKETS):
        return []

    return [
        AnomalyV1(
            metric="error_rate_spike",
            target="_global",
            current=current,
            baseline_mean=round(baseline.mean, 3),
            baseline_stddev=round(baseline.stddev, 3),
            sample_size=baseline.n,
            detail={
                "window_hours": CURRENT_WINDOW_HOURS,
                "current_error_count": int(current),
                "baseline_mean": round(baseline.mean, 3),
                "active_baseline_hours": baseline.n,
            },
            detected_at=now,
        )
    ]


# ---------------------------------------------------------------------------
# Check #4 — cron failure cluster (state snapshot)
# ---------------------------------------------------------------------------


def check_cron_failure_cluster(*, now: datetime | None = None) -> list[AnomalyV1]:
    """Snapshot-based: ≥``MIN_CRON_CLUSTER_SIZE`` cron jobs simultaneously
    with ``consecutive_failures >= 3``.

    Single-job streak is already covered by ``probe_cron_freshness``; we're
    catching the *correlated* mode (VPS reboot, env drift, network outage)
    that hits multiple crons at once.

    State-based, so no baseline statistics; ``baseline_mean`` /
    ``baseline_stddev`` / ``sample_size`` are all zero. The Slack dedup
    (60-min window) absorbs repeated firings until the cluster clears.
    """
    now = now or _now()
    failing = [hb for hb in heartbeat.list_all() if hb.consecutive_failures >= 3]
    if len(failing) < MIN_CRON_CLUSTER_SIZE:
        return []
    failing_jobs = sorted(hb.job_name for hb in failing)
    return [
        AnomalyV1(
            metric="cron_failure_cluster",
            target="_global",
            current=float(len(failing)),
            baseline_mean=0.0,
            baseline_stddev=0.0,
            sample_size=0,
            detail={
                "failing_count": len(failing),
                "failing_jobs": ",".join(failing_jobs),
                "min_consecutive_failures": min(hb.consecutive_failures for hb in failing),
                "max_consecutive_failures": max(hb.consecutive_failures for hb in failing),
            },
            detected_at=now,
        )
    ]


# ---------------------------------------------------------------------------
# Orchestrator
# ---------------------------------------------------------------------------


CheckFn = Callable[..., "list[AnomalyV1]"]

_CHECKS: tuple[tuple[str, CheckFn], ...] = (
    ("cost_spike", check_cost_spike),
    ("latency_p95_spike", check_latency_p95_spike),
    ("error_rate_spike", check_error_rate_spike),
    ("cron_failure_cluster", check_cron_failure_cluster),
)


def run_once(*, now: datetime | None = None) -> list[AnomalyV1]:
    """Run all four checks once (15-min cron tick).

    Per-check exception isolation: one SQL error or schema drift in
    ``check_error_rate_spike`` shouldn't black-hole the cost / latency /
    cron checks. Same for per-alert dispatch — a Slack outage shouldn't
    swallow the in-memory anomaly list (still returned for dashboard).

    Always records ``record_success(JOB_NAME)`` at the end so
    ``probe_cron_freshness`` sees the daemon as live; ``__main__`` wraps
    this whole thing in its own ``record_failure`` for the catastrophic
    crash path.
    """
    now = now or _now()
    started = time.monotonic()
    operation_id = f"op_{uuid.uuid4().hex[:8]}"

    anomalies: list[AnomalyV1] = []
    for name, fn in _CHECKS:
        try:
            anomalies.extend(fn(now=now))
        except Exception:
            logger.exception("anomaly check raised name=%s op=%s", name, operation_id)

    for a in anomalies:
        try:
            alert(
                "error",
                "anomaly",
                _format_alert(a),
                dedupe_key=f"anomaly:{a.metric}:{a.target}",
                dedupe_minutes=DEDUP_MINUTES,
            )
        except Exception:
            logger.exception(
                "alert dispatch raised metric=%s target=%s op=%s",
                a.metric,
                a.target,
                operation_id,
            )

    duration_ms = int((time.monotonic() - started) * 1000)
    logger.info(
        "anomaly_daemon tick op=%s duration_ms=%s anomalies=%s",
        operation_id,
        duration_ms,
        len(anomalies),
    )
    record_success(JOB_NAME)
    return anomalies
