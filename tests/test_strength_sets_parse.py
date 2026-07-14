"""WP1 parser/schema-validation unit tests (pure, no garminconnect needed)."""

import json
from pathlib import Path

import pytest

from agents.zoro.coach.garmin_read import (
    SchemaValidationError,
    grams_to_kg,
    parse_exercise_sets,
    validate_exercise_sets_shape,
)

FENIX8 = json.loads(
    (Path(__file__).parent / "fixtures" / "strength_sets_fenix8.json").read_text(encoding="utf-8")
)


def test_grams_to_kg():
    assert grams_to_kg(39000.0) == 39.0
    assert grams_to_kg(0.0) == 0.0       # warm-up / bodyweight: explicit 0, not None
    assert grams_to_kg(None) is None     # not entered: stays None


def test_active_set_parsed():
    sets = parse_exercise_sets(FENIX8, activity_id="A1")
    first = sets[0]
    assert first.set_type == "active"
    assert first.reps == 10
    assert first.weight_kg == 20.0
    assert first.exercise_key == "DUMBBELL_BENCH_PRESS"  # name preferred over category
    assert first.set_index == 0
    assert first.performed_at.endswith("+08:00")
    assert first.is_warmup is False


def test_rest_set_nulls():
    rest = parse_exercise_sets(FENIX8, activity_id="A1")[1]
    assert rest.set_type == "rest"
    assert rest.reps is None and rest.weight_kg is None
    assert rest.exercise_key is None


def test_name_falls_back_to_category():
    assert parse_exercise_sets(FENIX8, activity_id="A1")[2].exercise_key == "BENCH_PRESS"


def test_warmup_flagged_zero_weight():
    warm = parse_exercise_sets(FENIX8, activity_id="A1")[3]
    assert warm.is_warmup is True
    assert warm.weight_kg == 0.0
    assert warm.reps is None


def test_aborted_zero_rep_set():
    aborted = parse_exercise_sets(FENIX8, activity_id="A1")[4]
    assert aborted.reps == 0
    assert aborted.category == "UNKNOWN"


def test_set_index_falls_back_to_position_without_message_index():
    raw = {
        "exerciseSets": [
            {
                "setType": "ACTIVE",
                "exercises": [{"category": "CURL", "name": None}],
                "repetitionCount": 12,
                "weight": 10000.0,
                "startTime": "2019-10-19T12:00:00.0",
            }
        ]
    }
    assert parse_exercise_sets(raw, activity_id="OLD")[0].set_index == 0


@pytest.mark.parametrize(
    "bad",
    [
        "not a dict",
        {"no_exercise_sets": []},
        {"exerciseSets": "not a list"},
        {"exerciseSets": [{"setType": "SPRINT"}]},                    # unknown enum
        {"exerciseSets": [{"setType": "ACTIVE", "exercises": "nope"}]},  # exercises not a list
    ],
)
def test_schema_mutation_rejected(bad):
    with pytest.raises(SchemaValidationError):
        validate_exercise_sets_shape(bad)


def test_extra_fields_tolerated():
    # A future/unknown key must not trip the guard (we only pin load-bearing fields).
    raw = {
        "exerciseSets": [
            {
                "setType": "ACTIVE",
                "exercises": [{"category": "SQUAT", "name": None}],
                "repetitionCount": 5,
                "weight": 60000.0,
                "startTime": "2026-06-24T13:00:00.0",
                "messageIndex": 0,
                "someBrandNewGarminField": 123,
            }
        ]
    }
    sets = parse_exercise_sets(raw, activity_id="A1")
    assert sets[0].weight_kg == 60.0
