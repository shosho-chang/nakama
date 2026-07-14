"""有氧一覽 tests — parse + store + overview (pure / isolated DB)."""

import sqlite3

import pytest

import shared.state as state
from agents.zoro.coach.cardio import cardio_overview, icon, label
from agents.zoro.coach.garmin_read import SchemaValidationError, parse_cardio_activity
from shared.cardio_sessions_store import CardioSession, query_sessions, upsert_sessions


@pytest.fixture
def mem_db(monkeypatch):
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    state._init_tables(conn)
    monkeypatch.setattr(state, "_conn", conn)
    yield conn


# --- parse --------------------------------------------------------------- #

def test_parse_cardio_activity_real_shape():
    raw = {
        "activityId": 23358550829,
        "activityType": {"typeKey": "lap_swimming"},
        "startTimeLocal": "2026-06-24 09:04:53",   # naive local, space-separated
        "duration": 2768.7,
        "distance": 1500.0,
        "averageSpeed": 0.711,
        "averageHR": 130.0,
        "maxHR": 148.0,
        "calories": 484.0,
    }
    s = parse_cardio_activity(raw)
    assert s.activity_id == "23358550829"
    assert s.activity_type == "lap_swimming"
    assert s.performed_at.endswith("+08:00")
    assert s.distance_m == 1500.0
    assert s.avg_hr == 130 and isinstance(s.avg_hr, int)
    assert s.calories == 484


def test_parse_cardio_missing_fields_rejected():
    with pytest.raises(SchemaValidationError):
        parse_cardio_activity({"activityType": {"typeKey": "running"}})   # no activityId
    with pytest.raises(SchemaValidationError):
        parse_cardio_activity({"activityId": 1})                          # no typeKey


# --- store --------------------------------------------------------------- #

def _sess(aid, t="running", dist=5000.0):
    return CardioSession(
        activity_id=str(aid), activity_type=t, performed_at="2026-06-24T09:00:00+08:00",
        duration_sec=1800.0, distance_m=dist, avg_speed_mps=2.7, avg_hr=140, max_hr=160,
        calories=400, source="garmin",
    )


def test_cardio_upsert_idempotent(mem_db):
    assert upsert_sessions([_sess(1), _sess(2, "cycling")], operation_id="op1") == 2
    assert upsert_sessions([_sess(1), _sess(2, "cycling")], operation_id="op2") == 0
    assert len(query_sessions()) == 2
    assert len(query_sessions(activity_type="cycling")) == 1


# --- overview ------------------------------------------------------------ #

def test_cardio_overview_totals_and_recent():
    sessions = [
        {"activity_type": "running", "performed_at": "2026-06-20T09:00:00+08:00",
         "distance_m": 6000.0, "duration_sec": 1800.0, "avg_hr": 140},
        {"activity_type": "running", "performed_at": "2026-06-22T09:00:00+08:00",
         "distance_m": 4000.0, "duration_sec": 1200.0, "avg_hr": 138},
        {"activity_type": "cycling", "performed_at": "2026-06-24T09:00:00+08:00",
         "distance_m": 20000.0, "duration_sec": 3600.0, "avg_hr": 120},
    ]
    o = cardio_overview(sessions)
    assert o["modalities"]["running"]["count"] == 2
    assert o["modalities"]["running"]["distance_km"] == 10.0
    assert o["modalities"]["cycling"]["distance_km"] == 20.0
    assert o["modalities"]["running"]["label"] == "跑步"
    # recent is newest-first
    assert o["recent"][0]["activity_type"] == "cycling"
    assert o["recent"][0]["duration_min"] == 60


def test_label_icon_fallback():
    assert label("running") == "跑步" and icon("running") == "🏃"
    assert label("paddling") == "paddling"   # unknown -> passthrough
    assert icon("paddling") == "•"
