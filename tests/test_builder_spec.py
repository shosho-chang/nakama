"""WP4 builder-spec generator tests (pure)."""

from agents.zoro.coach.builder_spec import build_garmin_spec, render_markdown
from agents.zoro.coach.guardrail import PlannedExercise, WorkoutPlan


def _plan():
    return WorkoutPlan(
        title="Push A",
        notes="within MRV",
        exercises=[
            PlannedExercise("WARMUP", "WARM_UP", 1, 10, 10, 0.0, 30, 1, is_warmup=True),
            PlannedExercise("BARBELL_BENCH_PRESS", "BENCH_PRESS", 3, 6, 8, 70.0, 180, 2),
            PlannedExercise("CABLE_FLYE", "FLYE", 3, 12, 12, 15.0, 90, 3),
        ],
    )


def test_spec_carries_target_weight_on_fenix8():
    spec = build_garmin_spec(_plan(), supports_target_weight=True)
    assert spec.supports_target_weight is True
    bench = [s for s in spec.steps if s.exercise_key == "BARBELL_BENCH_PRESS"][0]
    assert bench.target_weight_kg == 70.0
    assert bench.reps == "6-8"
    flye = [s for s in spec.steps if s.exercise_key == "CABLE_FLYE"][0]
    assert flye.reps == "12"          # low==high collapses
    assert spec.steps[0].is_warmup is True


def test_spec_degrades_without_target_weight_support():
    spec = build_garmin_spec(_plan(), supports_target_weight=False)
    assert spec.supports_target_weight is False
    assert all(s.target_weight_kg is None for s in spec.steps)
    assert "純記錄" in spec.notes


def test_steps_sorted_by_order():
    spec = build_garmin_spec(_plan())
    assert [s.order for s in spec.steps] == [1, 2, 3]


def test_render_markdown_contains_exercise_and_weight():
    md = render_markdown(build_garmin_spec(_plan()))
    assert "BARBELL_BENCH_PRESS" in md
    assert "70.0kg" in md
    assert "Push A" in md
