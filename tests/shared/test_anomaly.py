"""Unit tests for shared/anomaly.py pure-math primitives."""

from __future__ import annotations

import pytest

from shared.anomaly import BaselineStats, is_3sigma_anomaly, rolling_baseline

# ---------------------------------------------------------------------------
# rolling_baseline
# ---------------------------------------------------------------------------


def test_rolling_baseline_empty_returns_zero_n():
    stats = rolling_baseline([])
    assert stats == BaselineStats(mean=0.0, stddev=0.0, n=0)


def test_rolling_baseline_single_value_zero_stddev():
    stats = rolling_baseline([5.0])
    assert stats.mean == 5.0
    assert stats.stddev == 0.0
    assert stats.n == 1


def test_rolling_baseline_uniform_values_zero_stddev():
    stats = rolling_baseline([7.0] * 30)
    assert stats.mean == 7.0
    assert stats.stddev == 0.0
    assert stats.n == 30


def test_rolling_baseline_known_distribution():
    # values 1..10 → mean 5.5, sample stddev ≈ 3.0277
    stats = rolling_baseline([float(i) for i in range(1, 11)])
    assert stats.mean == pytest.approx(5.5)
    assert stats.stddev == pytest.approx(3.0276503540, rel=1e-6)
    assert stats.n == 10


# ---------------------------------------------------------------------------
# is_3sigma_anomaly
# ---------------------------------------------------------------------------


def test_anomaly_blocked_when_sample_too_small():
    # 23 samples is below default min_n=24 → False even for an obvious outlier.
    baseline = rolling_baseline([1.0] * 23)
    assert is_3sigma_anomaly(1000.0, baseline) is False


def test_anomaly_allowed_at_exactly_min_n():
    # min_n=24 boundary inclusive. Flat baseline at 1.0 → 1.5*1=1.5 cutoff.
    baseline = rolling_baseline([1.0] * 24)
    assert is_3sigma_anomaly(2.0, baseline) is True
    assert is_3sigma_anomaly(1.4, baseline) is False


def test_anomaly_flat_baseline_uses_1_5x_rule():
    # stddev=0 path: only fires when current > mean * 1.5.
    baseline = rolling_baseline([10.0] * 30)
    assert is_3sigma_anomaly(14.0, baseline) is False  # 1.4x — safe
    assert is_3sigma_anomaly(15.0, baseline) is False  # exactly 1.5x — not strictly greater
    assert is_3sigma_anomaly(15.01, baseline) is True


def test_anomaly_negative_deviations_never_fire():
    # One-sided: drops below baseline don't alert (outage signal lives elsewhere).
    baseline = rolling_baseline([100.0 + i * 0.1 for i in range(40)])
    assert is_3sigma_anomaly(0.0, baseline) is False
    assert is_3sigma_anomaly(-50.0, baseline) is False


def test_anomaly_3sigma_threshold_strict():
    # mean=10, stddev=2 → 3σ cutoff = 16. Strictly greater than triggers.
    values = [8.0, 9.0, 10.0, 11.0, 12.0] * 8  # 40 samples, mean=10, stddev≈sqrt(2)
    baseline = rolling_baseline(values)
    z3_cutoff = baseline.mean + 3 * baseline.stddev
    assert is_3sigma_anomaly(z3_cutoff - 0.01, baseline) is False
    assert is_3sigma_anomaly(z3_cutoff + 0.01, baseline) is True


def test_anomaly_custom_min_n_override():
    # Caller can tighten / loosen the gate without touching baseline math.
    baseline = rolling_baseline([1.0] * 10)
    assert is_3sigma_anomaly(100.0, baseline, min_n=5) is True
    assert is_3sigma_anomaly(100.0, baseline, min_n=20) is False


def test_anomaly_min_n_zero_allows_anything():
    # Edge: min_n=0 with empty baseline is still safe (stddev=0, mean=0,
    # and 1.5x rule says current > 0 → True). Document the behavior.
    baseline = rolling_baseline([])
    assert is_3sigma_anomaly(1.0, baseline, min_n=0) is True
    assert is_3sigma_anomaly(0.0, baseline, min_n=0) is False
