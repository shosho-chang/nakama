"""WP6 data layer — progress summary + missing-weight inbox.

PURE aggregation over strength_sets rows, built on the WP2 engine. This is the
design-neutral half of WP6; it feeds the Bridge progress view + 補登 inbox, which
are built separately (Jinja template + CSS) with design + browser verification.
"""

from __future__ import annotations

from dataclasses import asdict
from typing import Iterable, Optional

from agents.zoro.coach import progression as P
from agents.zoro.coach.muscle_map import muscle_group
from agents.zoro.coach.profile import CoachProfile


def exercise_keys(sets: Iterable[dict]) -> list[str]:
    # Exclude the UNKNOWN auto-detect bucket — it's unclassified noise, not a
    # trackable exercise (still counted in raw sets, just not a headline card).
    keys = {
        s.get("exercise_key")
        for s in sets
        if P.is_working_set(s) and s.get("exercise_key") and s.get("exercise_key") != "UNKNOWN"
    }
    return sorted(keys)


def volume_trend(sets: Iterable[dict], exercise_key: str) -> list[tuple[str, float]]:
    return [(day, P.volume_load(ss)) for day, ss in P.sessions_for(sets, exercise_key)]


def is_pr(e1rm_trend: list[tuple[str, Optional[float]]]) -> bool:
    """Latest top-set E1RM is a strict all-time max => a PR worth a badge."""
    vals = [e for _, e in e1rm_trend if e is not None]
    return len(vals) >= 2 and vals[-1] > max(vals[:-1])


def missing_weight_sets(sets: Iterable[dict]) -> list[dict]:
    """Working sets with reps but weight==None (forgot to enter) — the 補登 inbox.

    weight==0.0 is bodyweight/warm-up and is NOT flagged (Phase 0: null vs 0.0 are
    distinct on Fenix 8)."""
    return [
        {
            "activity_id": s.get("activity_id"),
            "set_index": s.get("set_index"),
            "exercise_key": s.get("exercise_key"),
            "reps": s.get("reps"),
            "performed_at": s.get("performed_at"),
        }
        for s in sets
        if s.get("set_type") == "active"
        and not s.get("is_warmup")
        and (s.get("reps") or 0) > 0
        and s.get("weight_kg") is None
    ]


def progress_summary(sets: Iterable[dict], profile: Optional[CoachProfile] = None) -> dict:
    """Per-exercise trends + PR badge + next-step advice, and per-muscle weekly
    hard sets vs MEV/MAV/MRV landmarks. Shaped for direct template consumption."""
    profile = profile or CoachProfile()
    sets = list(sets)

    exercises = {}
    for key in exercise_keys(sets):
        e1rm_tr = P.e1rm_trend(sets, key)
        vol_tr = volume_trend(sets, key)
        cat = next((s.get("category") for s in P.working_sets_for(sets, key)), None)
        exercises[key] = {
            "muscle": muscle_group(cat, key),
            "e1rm_trend": e1rm_tr,
            "latest_e1rm": next((e for _, e in reversed(e1rm_tr) if e is not None), None),
            "volume_trend": vol_tr,
            "volume_values": [v for _, v in vol_tr],   # template-friendly sparkline data
            "volume_max": max([v for _, v in vol_tr], default=0.0),
            "is_pr": is_pr(e1rm_tr),
            "recommendation": asdict(P.recommend(sets, key, profile)),
        }

    weekly = P.weekly_hard_sets_by_muscle(sets)
    muscles: dict[str, dict] = {}
    for (week, muscle), count in weekly.items():
        latest = muscles.get(muscle)
        if latest is None or week > latest["week"]:
            mev, mav, mrv = profile.landmarks(muscle)
            muscles[muscle] = {
                "week": week,
                "hard_sets": count,
                "mev": mev,
                "mav": mav,
                "mrv": mrv,
                "status": (
                    "below_mev" if count < mev
                    else "over_mrv" if count > mrv
                    else "in_range"
                ),
            }

    return {
        "exercises": exercises,
        "muscles": muscles,
        "missing_weight": missing_weight_sets(sets),
    }
