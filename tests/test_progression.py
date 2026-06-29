"""WP2 progression-engine unit tests (pure functions on synthetic set rows)."""

from agents.zoro.coach.muscle_map import muscle_group
from agents.zoro.coach.profile import CoachProfile
from agents.zoro.coach.progression import (
    deload_signal,
    e1rm,
    recommend,
    volume_load,
    weekly_hard_sets_by_muscle,
)


def mkset(key, reps, kg, day, *, category, set_type="active", warmup=False):
    return {
        "set_type": set_type,
        "is_warmup": warmup,
        "exercise_key": key,
        "category": category,
        "exercise_name": key,
        "reps": reps,
        "weight_kg": kg,
        "performed_at": f"{day}T13:00:00+08:00",
    }


# --- E1RM ---------------------------------------------------------------- #

def test_e1rm_low_rep():
    val = e1rm(100, 5)  # Epley 116.67 + Brzycki 112.5 -> mean ~114.6
    assert 114.0 <= val <= 115.0


def test_e1rm_single_rep_is_weight():
    assert e1rm(120, 1) == 120.0


def test_e1rm_high_rep_returns_none():
    assert e1rm(100, 12) is None       # >10 reps: unreliable
    assert e1rm(60, 20) is None


def test_e1rm_missing_data():
    assert e1rm(None, 5) is None
    assert e1rm(0, 5) is None
    assert e1rm(100, 0) is None


# --- volume-load --------------------------------------------------------- #

def test_volume_load_excludes_warmup_rest_bodyweight():
    sets = [
        mkset("BB_BENCH", 10, 20.0, "2026-06-22", category="BENCH_PRESS"),
        mkset("BB_BENCH", 8, 22.5, "2026-06-22", category="BENCH_PRESS"),
        mkset("STRETCH", None, 0.0, "2026-06-22", category="WARM_UP", warmup=True),
        mkset(None, None, None, "2026-06-22", category=None, set_type="rest"),
        mkset("PULLUP", 8, 0.0, "2026-06-22", category="PULL_UP"),  # bodyweight -> 0 load
    ]
    assert volume_load(sets) == 10 * 20.0 + 8 * 22.5  # 380.0


# --- weekly hard sets by muscle ----------------------------------------- #

def test_weekly_hard_sets_groups_and_excludes():
    sets = [
        mkset("BB_BENCH", 10, 20.0, "2026-06-22", category="BENCH_PRESS"),
        mkset("BB_BENCH", 10, 20.0, "2026-06-22", category="BENCH_PRESS"),
        mkset("STRETCH", None, 0.0, "2026-06-22", category="WARM_UP", warmup=True),
        mkset("MYSTERY", 10, 5.0, "2026-06-22", category="UNKNOWN"),  # excluded
    ]
    wk = weekly_hard_sets_by_muscle(sets)
    chest_weeks = {w: c for (w, m), c in wk.items() if m == "chest"}
    assert sum(chest_weeks.values()) == 2
    assert all(m != "unknown" for (_w, m) in wk)


# --- recommendation (double progression + 2-for-2) ---------------------- #

def test_recommend_add_weight_after_two_top_sessions():
    sets = [
        mkset("BB_BENCH", 12, 20.0, "2026-06-15", category="BENCH_PRESS"),
        mkset("BB_BENCH", 12, 20.0, "2026-06-22", category="BENCH_PRESS"),
    ]
    adv = recommend(sets, "BB_BENCH")
    assert adv.action == "add_weight"
    assert adv.suggested_weight_kg == 22.5   # upper-body +2.5
    assert adv.increment_kg == 2.5


def test_recommend_lower_body_increment():
    sets = [
        mkset("BB_SQUAT", 12, 100.0, "2026-06-15", category="SQUAT"),
        mkset("BB_SQUAT", 12, 100.0, "2026-06-22", category="SQUAT"),
    ]
    adv = recommend(sets, "BB_SQUAT")
    assert adv.action == "add_weight"
    assert adv.suggested_weight_kg == 105.0  # lower-body +5


def test_recommend_add_reps_when_below_top():
    sets = [mkset("BB_BENCH", 9, 20.0, "2026-06-22", category="BENCH_PRESS")]
    adv = recommend(sets, "BB_BENCH")
    assert adv.action == "add_reps"


def test_recommend_hold_after_single_top_session():
    sets = [mkset("BB_BENCH", 12, 20.0, "2026-06-22", category="BENCH_PRESS")]
    adv = recommend(sets, "BB_BENCH")
    assert adv.action == "hold"


def test_recommend_insufficient_data():
    adv = recommend([], "BB_BENCH")
    assert adv.action == "insufficient_data"


# --- deload signal ------------------------------------------------------- #

def test_deload_on_rep_decline():
    sets = [
        mkset("BB_BENCH", 12, 20.0, "2026-06-08", category="BENCH_PRESS"),
        mkset("BB_BENCH", 10, 20.0, "2026-06-15", category="BENCH_PRESS"),
        mkset("BB_BENCH", 8, 20.0, "2026-06-22", category="BENCH_PRESS"),
    ]
    sig = deload_signal(sets, "BB_BENCH")
    assert sig.triggered
    assert any("declined" in r for r in sig.reasons)


def test_deload_on_mrv_proximity():
    # 20 chest hard sets in one ISO week (>= MRV 20), across 2 days, stable reps.
    sets = []
    for i in range(10):
        sets.append(mkset("BB_BENCH", 10, 20.0, "2026-06-22", category="BENCH_PRESS"))
        sets.append(mkset("BB_BENCH", 10, 20.0, "2026-06-23", category="BENCH_PRESS"))
    sig = deload_signal(sets, "BB_BENCH")
    assert sig.triggered
    assert any("MRV" in r for r in sig.reasons)


def test_no_deload_when_stable():
    sets = [
        mkset("BB_BENCH", 10, 20.0, "2026-06-15", category="BENCH_PRESS"),
        mkset("BB_BENCH", 10, 20.0, "2026-06-22", category="BENCH_PRESS"),
    ]
    assert deload_signal(sets, "BB_BENCH").triggered is False


# --- muscle map ---------------------------------------------------------- #

def test_muscle_map():
    assert muscle_group("BENCH_PRESS") == "chest"
    assert muscle_group("SQUAT") == "quads"
    assert muscle_group("WARM_UP") == "unknown"
    assert muscle_group("UNKNOWN") == "unknown"
    assert muscle_group(None) == "unknown"


def test_profile_defaults():
    p = CoachProfile()
    assert p.goal == "balanced" and p.training_status == "intermediate"
    assert p.increment_kg("chest") == 2.5
    assert p.increment_kg("quads") == 5.0
