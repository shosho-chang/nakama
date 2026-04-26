"""Tests for shared.state cost tracking: api_calls schema migration,
record_api_call with cache tokens, get_cost_summary, get_cost_timeseries.
"""

from __future__ import annotations

import pytest

from shared import state


def test_record_api_call_defaults_cache_tokens_to_zero():
    state.record_api_call(
        agent="nami",
        model="claude-sonnet-4-6",
        input_tokens=100,
        output_tokens=50,
    )
    rows = state.get_cost_summary(agent="nami", days=7)
    assert len(rows) == 1
    assert rows[0]["cache_read_tokens"] == 0
    assert rows[0]["cache_write_tokens"] == 0


def test_record_api_call_stores_cache_tokens():
    state.record_api_call(
        agent="nami",
        model="claude-sonnet-4-6",
        input_tokens=100,
        output_tokens=50,
        cache_read_tokens=200,
        cache_write_tokens=100,
    )
    rows = state.get_cost_summary(agent="nami", days=7)
    assert rows[0]["cache_read_tokens"] == 200
    assert rows[0]["cache_write_tokens"] == 100


def test_get_cost_summary_groups_by_agent_and_model():
    state.record_api_call(
        agent="nami", model="claude-sonnet-4-6", input_tokens=100, output_tokens=50
    )
    state.record_api_call(
        agent="nami", model="claude-sonnet-4-6", input_tokens=200, output_tokens=100
    )
    state.record_api_call(
        agent="nami", model="claude-haiku-4-5", input_tokens=1000, output_tokens=500
    )
    state.record_api_call(
        agent="zoro", model="claude-sonnet-4-6", input_tokens=50, output_tokens=25
    )

    rows = state.get_cost_summary(days=7)
    by_key = {(r["agent"], r["model"]): r for r in rows}
    assert by_key[("nami", "claude-sonnet-4-6")]["calls"] == 2
    assert by_key[("nami", "claude-sonnet-4-6")]["input_tokens"] == 300
    assert by_key[("nami", "claude-haiku-4-5")]["calls"] == 1
    assert by_key[("zoro", "claude-sonnet-4-6")]["calls"] == 1


def test_get_cost_summary_filters_by_agent():
    state.record_api_call(
        agent="nami", model="claude-sonnet-4-6", input_tokens=100, output_tokens=50
    )
    state.record_api_call(
        agent="zoro", model="claude-sonnet-4-6", input_tokens=50, output_tokens=25
    )

    rows = state.get_cost_summary(agent="nami", days=7)
    assert len(rows) == 1
    assert rows[0]["agent"] == "nami"


def test_get_cost_timeseries_day_bucket():
    state.record_api_call(
        agent="nami", model="claude-sonnet-4-6", input_tokens=100, output_tokens=50
    )
    state.record_api_call(
        agent="nami", model="claude-sonnet-4-6", input_tokens=200, output_tokens=100
    )

    rows = state.get_cost_timeseries(days=7, bucket="day")
    # All calls land in same day bucket
    assert len(rows) == 1
    assert len(rows[0]["bucket"]) == 10  # YYYY-MM-DD
    assert rows[0]["calls"] == 2
    assert rows[0]["input_tokens"] == 300


def test_get_cost_timeseries_hour_bucket_format():
    state.record_api_call(
        agent="nami", model="claude-sonnet-4-6", input_tokens=100, output_tokens=50
    )
    rows = state.get_cost_timeseries(days=1, bucket="hour")
    assert len(rows) == 1
    # 'YYYY-MM-DDTHH:00'
    assert len(rows[0]["bucket"]) == 16
    assert rows[0]["bucket"][13:] == ":00"


def test_get_cost_timeseries_rejects_bad_bucket():
    with pytest.raises(ValueError):
        state.get_cost_timeseries(bucket="week")


def test_get_cost_timeseries_filters_by_agent():
    state.record_api_call(
        agent="nami", model="claude-sonnet-4-6", input_tokens=100, output_tokens=50
    )
    state.record_api_call(
        agent="zoro", model="claude-sonnet-4-6", input_tokens=50, output_tokens=25
    )

    rows = state.get_cost_timeseries(agent="nami", days=7, bucket="day")
    assert len(rows) == 1
    assert rows[0]["agent"] == "nami"


# ---------------------------------------------------------------------------
# Phase 5A — latency_ms instrumentation
# ---------------------------------------------------------------------------


def test_record_api_call_defaults_latency_to_zero():
    """既有 callers（沒傳 latency_ms）仍應 work，欄位預設 0。"""
    state.record_api_call(
        agent="nami",
        model="claude-sonnet-4-6",
        input_tokens=100,
        output_tokens=50,
    )
    # raw row 確認
    conn = state._get_conn()
    row = conn.execute(
        "SELECT latency_ms FROM api_calls WHERE agent = 'nami' ORDER BY id DESC LIMIT 1"
    ).fetchone()
    assert row["latency_ms"] == 0


def test_record_api_call_stores_latency_ms():
    state.record_api_call(
        agent="nami",
        model="claude-sonnet-4-6",
        input_tokens=100,
        output_tokens=50,
        latency_ms=1234,
    )
    conn = state._get_conn()
    row = conn.execute(
        "SELECT latency_ms FROM api_calls WHERE agent = 'nami' ORDER BY id DESC LIMIT 1"
    ).fetchone()
    assert row["latency_ms"] == 1234


def test_get_latency_summary_excludes_zero_latency():
    """latency_ms=0 表示未測量（既有資料），不能拉低 percentile。"""
    state.record_api_call(
        agent="nami",
        model="claude-sonnet-4-6",
        input_tokens=10,
        output_tokens=10,
        latency_ms=0,
    )
    state.record_api_call(
        agent="nami",
        model="claude-sonnet-4-6",
        input_tokens=10,
        output_tokens=10,
        latency_ms=500,
    )
    rows = state.get_latency_summary(days=7)
    assert len(rows) == 1
    assert rows[0]["calls"] == 1
    assert rows[0]["latency_p50_ms"] == 500


def test_get_latency_summary_computes_percentiles():
    # 100 calls：1, 2, ..., 100 ms
    for ms in range(1, 101):
        state.record_api_call(
            agent="nami",
            model="claude-sonnet-4-6",
            input_tokens=1,
            output_tokens=1,
            latency_ms=ms,
        )
    rows = state.get_latency_summary(agent="nami", days=7)
    assert len(rows) == 1
    r = rows[0]
    assert r["calls"] == 100
    # nearest-rank: p50 = sorted[49] = 50, p95 = sorted[94] = 95, p99 = sorted[98] = 99
    assert r["latency_p50_ms"] == 50
    assert r["latency_p95_ms"] == 95
    assert r["latency_p99_ms"] == 99
    assert r["latency_max_ms"] == 100


def test_get_latency_summary_groups_by_agent_model():
    state.record_api_call(
        agent="nami",
        model="claude-sonnet-4-6",
        input_tokens=1,
        output_tokens=1,
        latency_ms=100,
    )
    state.record_api_call(
        agent="nami",
        model="claude-haiku-4-5",
        input_tokens=1,
        output_tokens=1,
        latency_ms=200,
    )
    state.record_api_call(
        agent="zoro",
        model="claude-sonnet-4-6",
        input_tokens=1,
        output_tokens=1,
        latency_ms=300,
    )
    rows = state.get_latency_summary(days=7)
    by_key = {(r["agent"], r["model"]): r for r in rows}
    assert len(by_key) == 3
    assert by_key[("nami", "claude-sonnet-4-6")]["latency_p50_ms"] == 100
    assert by_key[("nami", "claude-haiku-4-5")]["latency_p50_ms"] == 200
    assert by_key[("zoro", "claude-sonnet-4-6")]["latency_p50_ms"] == 300


def test_get_latency_summary_filters_by_agent():
    state.record_api_call(
        agent="nami",
        model="claude-sonnet-4-6",
        input_tokens=1,
        output_tokens=1,
        latency_ms=100,
    )
    state.record_api_call(
        agent="zoro",
        model="claude-sonnet-4-6",
        input_tokens=1,
        output_tokens=1,
        latency_ms=200,
    )
    rows = state.get_latency_summary(agent="nami", days=7)
    assert len(rows) == 1
    assert rows[0]["agent"] == "nami"


def test_get_latency_summary_empty_when_all_zero():
    """若所有 row latency_ms=0，return 空 list（不要回 dummy 0 percentile）。"""
    state.record_api_call(
        agent="nami",
        model="claude-sonnet-4-6",
        input_tokens=1,
        output_tokens=1,
        latency_ms=0,
    )
    rows = state.get_latency_summary(days=7)
    assert rows == []
