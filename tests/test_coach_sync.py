"""WP1 coach-sync orchestration tests — Garmin IO mocked, DB isolated."""

import json
import sqlite3
from pathlib import Path

import pytest

import shared.state as state
from agents.zoro.coach import sync as coach_sync
from shared.strength_sets_store import query_sets

FENIX8 = json.loads(
    (Path(__file__).parent / "fixtures" / "strength_sets_fenix8.json").read_text(encoding="utf-8")
)


@pytest.fixture
def mem_db(monkeypatch):
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    state._init_tables(conn)
    monkeypatch.setattr(state, "_conn", conn)
    yield conn


def _patch_garmin(monkeypatch, raw, *, activities=None):
    from agents.zoro.coach import garmin_read

    monkeypatch.setattr(garmin_read, "login", lambda: object())
    monkeypatch.setattr(
        garmin_read,
        "fetch_strength_activities",
        lambda client, since, until=None: activities or [{"activityId": raw["activityId"]}],
    )
    monkeypatch.setattr(garmin_read, "fetch_exercise_sets", lambda client, aid: raw)
    monkeypatch.setattr(garmin_read, "fetch_cardio_activities", lambda client, since, until=None: [])


def test_sync_lands_sets(mem_db, monkeypatch):
    _patch_garmin(monkeypatch, FENIX8)
    result = coach_sync.run(since="8w")
    assert result["activities"] == 1
    assert result["sets_new"] == len(FENIX8["exerciseSets"])
    assert result["schema_errors"] == 0
    rows = query_sets(activity_id=str(FENIX8["activityId"]))
    assert len(rows) == len(FENIX8["exerciseSets"])


def test_sync_rerun_is_idempotent(mem_db, monkeypatch):
    _patch_garmin(monkeypatch, FENIX8)
    coach_sync.run(since="8w")
    assert coach_sync.run(since="8w")["sets_new"] == 0


def test_sync_rejects_bad_schema_without_crashing(mem_db, monkeypatch):
    bad = {"activityId": 5, "exerciseSets": [{"setType": "SPRINT"}]}
    _patch_garmin(monkeypatch, bad, activities=[{"activityId": 5}])
    result = coach_sync.run(since="8w")
    assert result["schema_errors"] == 1
    assert result["sets_new"] == 0
