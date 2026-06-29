"""WP6 data-layer tests (pure)."""

from agents.zoro.coach.progress import (
    is_pr,
    missing_weight_sets,
    progress_summary,
    volume_trend,
)


def mkset(key, reps, kg, day, *, category, set_type="active", warmup=False):
    return {
        "activity_id": f"act-{day}",
        "set_index": 0,
        "set_type": set_type,
        "is_warmup": warmup,
        "exercise_key": key,
        "category": category,
        "exercise_name": key,
        "reps": reps,
        "weight_kg": kg,
        "performed_at": f"{day}T13:00:00+08:00",
    }


def test_is_pr_detects_all_time_max():
    assert is_pr([("d1", 100.0), ("d2", 105.0)]) is True
    assert is_pr([("d1", 105.0), ("d2", 100.0)]) is False
    assert is_pr([("d1", 100.0)]) is False  # need a prior to beat


def test_volume_trend_per_session():
    sets = [
        mkset("BENCH", 10, 20.0, "2026-06-15", category="BENCH_PRESS"),
        mkset("BENCH", 10, 22.0, "2026-06-22", category="BENCH_PRESS"),
    ]
    tr = volume_trend(sets, "BENCH")
    assert tr == [("2026-06-15", 200.0), ("2026-06-22", 220.0)]


def test_missing_weight_flags_null_not_zero():
    sets = [
        mkset("BENCH", 8, None, "2026-06-22", category="BENCH_PRESS"),     # forgot -> flag
        mkset("PULLUP", 8, 0.0, "2026-06-22", category="PULL_UP"),         # bodyweight -> no flag
        mkset("STRETCH", None, 0.0, "2026-06-22", category="WARM_UP", warmup=True),  # warmup -> no
        mkset(None, None, None, "2026-06-22", category=None, set_type="rest"),       # rest -> no
    ]
    missing = missing_weight_sets(sets)
    assert len(missing) == 1
    assert missing[0]["exercise_key"] == "BENCH"


def test_progress_summary_shape():
    sets = [
        mkset("BENCH", 5, 60.0, "2026-06-08", category="BENCH_PRESS"),
        mkset("BENCH", 5, 65.0, "2026-06-15", category="BENCH_PRESS"),  # E1RM up -> PR
    ]
    summ = progress_summary(sets)
    assert "BENCH" in summ["exercises"]
    ex = summ["exercises"]["BENCH"]
    assert ex["muscle"] == "chest"
    assert ex["is_pr"] is True
    assert "recommendation" in ex and "action" in ex["recommendation"]
    assert "chest" in summ["muscles"]
    assert summ["muscles"]["chest"]["status"] in {"below_mev", "in_range", "over_mrv"}
    assert summ["missing_weight"] == []
