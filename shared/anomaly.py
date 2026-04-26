"""Pure-math primitives for the Phase 5B-3 anomaly daemon.

Split from ``agents/franky/anomaly_daemon.py`` so the baseline / 3σ math
can be unit-tested with plain ``list[float]`` inputs — no DB, no alert
plumbing, no cron context. The daemon's per-metric ``check_*()`` paths
do the SQL aggregation, then call ``rolling_baseline`` + ``is_3sigma_anomaly``
to make the statistical decision.

The 3σ rule has three edge cases worth pinning explicitly:

1. ``baseline.n < min_n`` (default 24) → False. A cold-start daemon has
   no history; alerting on tiny samples produces noise on day 1 and 2
   when only a handful of hourly buckets exist.
2. ``baseline.stddev == 0`` (perfectly flat baseline) → True iff
   ``current > mean * 1.5``. Division-by-zero guard, plus a
   conservative "anything bigger than +50% of a flat line is suspicious"
   rule. 1.5x is intentionally generous; tighten if false positives
   accumulate.
3. Otherwise → True iff ``(current - mean) / stddev > 3.0``. Standard
   one-sided z-score. We only flag *positive* deviations because the
   metrics we cover (cost, latency, error rate) are bad-direction
   spikes — sudden drops are typically outages, which the heartbeat /
   health probes catch separately (no double-paging).
"""

from __future__ import annotations

import statistics
from dataclasses import dataclass


@dataclass(frozen=True)
class BaselineStats:
    """Sample mean / sample stddev / count over a baseline window."""

    mean: float
    stddev: float
    n: int


def rolling_baseline(values: list[float]) -> BaselineStats:
    """Compute mean / sample-stddev / count over a list of measurements.

    Uses sample stddev (Bessel's correction via ``statistics.stdev``)
    because the baseline is treated as a sample of the underlying
    distribution, not the full population. ``n=1`` returns stddev=0
    (a single point has no spread). Empty input returns an all-zero
    BaselineStats — caller's ``min_n`` gate rejects it before any
    decision.
    """
    if not values:
        return BaselineStats(mean=0.0, stddev=0.0, n=0)
    n = len(values)
    mean = statistics.fmean(values)
    stddev = statistics.stdev(values) if n > 1 else 0.0
    return BaselineStats(mean=mean, stddev=stddev, n=n)


def is_3sigma_anomaly(
    current: float,
    baseline: BaselineStats,
    *,
    min_n: int = 24,
) -> bool:
    """Return True when ``current`` is a positive 3σ outlier vs ``baseline``.

    See module docstring for the cold-start, flat-baseline, and
    one-sided-only design choices.
    """
    if baseline.n < min_n:
        return False
    if baseline.stddev == 0:
        return current > baseline.mean * 1.5
    z = (current - baseline.mean) / baseline.stddev
    return z > 3.0
