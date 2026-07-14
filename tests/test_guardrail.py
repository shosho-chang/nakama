"""WP3 guardrail unit tests (pure, deterministic)."""

from agents.zoro.coach.guardrail import (
    GuardrailContext,
    PlannedExercise,
    ScheduledSession,
    WorkoutPlan,
    check_1rm_safety,
    check_deload,
    check_load_rep,
    check_volume,
    concurrent_guardrail,
    validate_plan,
)
from agents.zoro.coach.profile import CoachProfile


def ex(key, cat, sets=3, lo=8, hi=12, kg=20.0, *, warmup=False, order=1):
    return PlannedExercise(key, cat, sets, lo, hi, kg, 120, order, warmup)


# --- load/rep ------------------------------------------------------------ #

def test_load_rep_blocks_heavy_high_rep():
    v = check_load_rep(ex("BENCH", "BENCH_PRESS", lo=12, kg=85.0), e1rm_kg=100.0)  # 85% @ 12
    assert v is not None and v.severity == "block"


def test_load_rep_allows_light_high_rep():
    # 50% 1RM @ 20 reps is fine (hypertrophy) -> no violation.
    assert check_load_rep(ex("BENCH", "BENCH_PRESS", lo=20, kg=50.0), e1rm_kg=100.0) is None


def test_load_rep_blocks_target_above_1rm():
    v = check_load_rep(ex("BENCH", "BENCH_PRESS", lo=5, kg=110.0), e1rm_kg=100.0)
    assert v is not None and v.severity == "block"


def test_load_rep_skips_warmup_and_missing_e1rm():
    assert check_load_rep(ex("BENCH", "BENCH_PRESS", warmup=True), e1rm_kg=100.0) is None
    assert check_load_rep(ex("BENCH", "BENCH_PRESS"), e1rm_kg=None) is None


# --- volume cap ---------------------------------------------------------- #

def test_volume_blocks_over_mrv():
    plan = WorkoutPlan("x", [ex("BENCH", "BENCH_PRESS", sets=12)])
    ctx = GuardrailContext(CoachProfile(), recent_hard_sets_by_muscle={"chest": 12})  # 12+12 > MRV 20
    viols = check_volume(plan, ctx)
    assert any(v.rule == "volume" and v.severity == "block" for v in viols)


def test_volume_ok_within_mrv():
    plan = WorkoutPlan("x", [ex("BENCH", "BENCH_PRESS", sets=6)])
    assert check_volume(plan, GuardrailContext(CoachProfile())) == []


# --- 1RM safety ---------------------------------------------------------- #

def test_1rm_test_blocked_for_novice():
    plan = WorkoutPlan("x", [ex("SQUAT", "SQUAT", lo=1, hi=1)])
    viols = check_1rm_safety(plan, GuardrailContext(CoachProfile(training_status="novice")))
    assert any(v.severity == "block" for v in viols)


def test_1rm_test_allowed_for_intermediate():
    plan = WorkoutPlan("x", [ex("SQUAT", "SQUAT", lo=1, hi=1)])
    assert check_1rm_safety(plan, GuardrailContext(CoachProfile())) == []


# --- deload -------------------------------------------------------------- #

def test_deload_warns_when_due_and_not_marked():
    plan = WorkoutPlan("x", [ex("BENCH", "BENCH_PRESS")], is_deload=False)
    viols = check_deload(plan, GuardrailContext(CoachProfile(), deload_due=True))
    assert any(v.rule == "deload" and v.severity == "warn" for v in viols)


def test_deload_silent_when_plan_is_deload():
    plan = WorkoutPlan("x", [ex("BENCH", "BENCH_PRESS")], is_deload=True)
    assert check_deload(plan, GuardrailContext(CoachProfile(), deload_due=True)) == []


# --- aggregate ----------------------------------------------------------- #

def test_validate_plan_ok_and_blocked():
    ctx = GuardrailContext(CoachProfile(), e1rm_by_exercise={"BENCH": 100.0})
    good = WorkoutPlan("ok", [ex("BENCH", "BENCH_PRESS", lo=6, hi=8, kg=70.0)])
    assert validate_plan(good, ctx).ok is True

    bad = WorkoutPlan("bad", [ex("BENCH", "BENCH_PRESS", lo=12, hi=15, kg=90.0)])  # 90% @ 12
    rep = validate_plan(bad, ctx)
    assert rep.ok is False and any(v.severity == "block" for v in rep.violations)


# --- concurrent guardrail ------------------------------------------------ #

def test_concurrent_order_warn():
    sessions = [
        ScheduledSession(0, "strength", frozenset({"chest"}), start_hour=18),
        ScheduledSession(0, "cycling", frozenset({"quads"}), start_hour=8),
    ]
    viols = concurrent_guardrail(sessions, CoachProfile())
    assert any(v.rule == "concurrent_order" for v in viols)


def test_concurrent_leg_cycling_interference_block():
    sessions = [
        ScheduledSession(1, "strength", frozenset({"quads"}), start_hour=8),
        ScheduledSession(1, "cycling", frozenset({"quads"}), start_hour=10),  # <6h
    ]
    viols = concurrent_guardrail(sessions, CoachProfile())
    assert any(v.rule == "concurrent_interference" and v.severity == "block" for v in viols)


def test_concurrent_endurance_dose_cap():
    sessions = [ScheduledSession(d, "cycling", start_hour=7) for d in range(5)]  # 5 > cap 4
    viols = concurrent_guardrail(sessions, CoachProfile())
    assert any(v.rule == "endurance_dose" for v in viols)


def test_concurrent_clean_schedule_ok():
    sessions = [
        ScheduledSession(0, "strength", frozenset({"chest"}), start_hour=8),
        ScheduledSession(2, "cycling", frozenset({"quads"}), start_hour=8),
        ScheduledSession(4, "swim", frozenset(), start_hour=8),
    ]
    assert concurrent_guardrail(sessions, CoachProfile()) == []
