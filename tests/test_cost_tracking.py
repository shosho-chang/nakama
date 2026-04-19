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
