"""WP1 store tests — idempotent upsert + query, on an isolated in-memory DB."""

import sqlite3

import pytest

import shared.state as state
from shared.strength_sets_store import StrengthSet, query_sets, upsert_sets


@pytest.fixture
def mem_db(monkeypatch):
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    state._init_tables(conn)
    monkeypatch.setattr(state, "_conn", conn)
    yield conn


def _set(idx, key="BENCH_PRESS", reps=10, kg=20.0, stype="active"):
    return StrengthSet(
        activity_id="A1",
        set_index=idx,
        set_type=stype,
        exercise_key=key,
        category="BENCH_PRESS",
        exercise_name=key,
        reps=reps,
        weight_kg=kg,
        duration_sec=30.0,
        is_warmup=False,
        performed_at="2026-06-24T13:00:00+08:00",
        wkt_step_index=None,
        source="garmin",
    )


def test_upsert_and_query(mem_db):
    n = upsert_sets([_set(0), _set(1, stype="rest", reps=None, kg=None)], operation_id="op1")
    assert n == 2
    assert len(query_sets(activity_id="A1")) == 2
    active = query_sets(activity_id="A1", include_rest=False)
    assert len(active) == 1
    assert active[0]["weight_kg"] == 20.0


def test_upsert_is_idempotent(mem_db):
    sets = [_set(0), _set(1)]
    assert upsert_sets(sets, operation_id="op1") == 2
    assert upsert_sets(sets, operation_id="op2") == 0  # re-sync -> no new rows
    assert len(query_sets(activity_id="A1")) == 2


def test_query_by_exercise_key(mem_db):
    upsert_sets([_set(0, key="SQUAT"), _set(1, key="BENCH_PRESS")], operation_id="op1")
    assert len(query_sets(exercise_key="SQUAT")) == 1


def test_empty_upsert_is_noop(mem_db):
    assert upsert_sets([], operation_id="op1") == 0
