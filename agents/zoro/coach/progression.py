"""WP2 progression engine — pure functions over strength_sets rows.

Turns read-back sets (dicts as returned by shared.strength_sets_store.query_sets)
into: per-exercise volume-load, top-set E1RM (<=10 reps only), weekly hard sets
per muscle, double-progression + 2-for-2 weight recommendations, and a deload
signal. No I/O, no workout writing, no medical judgment (v2 §5 WP2 boundary).

E1RM is computed ONLY for low-rep top sets (<=10): the Epley/Brzycki family is
systematically unreliable in the high-rep ranges ACSM 2026 hypertrophy allows,
so high-rep progress is tracked by rep-at-load / volume-load instead.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from datetime import date
from typing import Iterable, Optional

from agents.zoro.coach.muscle_map import muscle_group
from agents.zoro.coach.profile import CoachProfile

E1RM_MAX_REPS = 10


def e1rm(weight_kg: Optional[float], reps: Optional[int]) -> Optional[float]:
    """Estimated 1RM (Epley/Brzycki mean), or None when not meaningful.

    Returns None for missing/zero data and for reps > 10 (high-rep E1RM is
    unreliable — caller should fall back to rep-at-load PR / volume-load).
    """
    if not weight_kg or not reps or reps <= 0 or weight_kg <= 0:
        return None
    if reps == 1:
        return round(float(weight_kg), 1)
    if reps > E1RM_MAX_REPS:
        return None
    epley = weight_kg * (1 + reps / 30)
    brzycki = weight_kg * 36 / (37 - reps)
    return round((epley + brzycki) / 2, 1)


def is_working_set(s: dict) -> bool:
    """A real working set: active, not warm-up, with reps recorded (>0)."""
    return (
        s.get("set_type") == "active"
        and not s.get("is_warmup")
        and (s.get("reps") or 0) > 0
    )


def _session_date(s: dict) -> str:
    return (s.get("performed_at") or "")[:10]


def _iso_week(day: str) -> Optional[str]:
    try:
        y, w, _ = date.fromisoformat(day).isocalendar()
        return f"{y}-W{w:02d}"
    except ValueError:
        return None


def volume_load(sets: Iterable[dict]) -> float:
    """Sum reps*weight over LOADED working sets (excludes warm-up/rest/bodyweight)."""
    return round(
        sum(
            s["reps"] * s["weight_kg"]
            for s in sets
            if is_working_set(s) and (s.get("weight_kg") or 0) > 0
        ),
        1,
    )


def working_sets_for(sets: Iterable[dict], exercise_key: str) -> list[dict]:
    return [s for s in sets if is_working_set(s) and s.get("exercise_key") == exercise_key]


def sessions_for(sets: Iterable[dict], exercise_key: str) -> list[tuple[str, list[dict]]]:
    """[(date, [working sets]), ...] ascending by date for one exercise."""
    by_day: dict[str, list[dict]] = defaultdict(list)
    for s in working_sets_for(sets, exercise_key):
        by_day[_session_date(s)].append(s)
    return sorted(by_day.items())


def top_set(session_sets: Iterable[dict]) -> Optional[dict]:
    """Heaviest loaded working set in a session (max weight, then max reps)."""
    loaded = [s for s in session_sets if (s.get("weight_kg") or 0) > 0]
    if not loaded:
        return None
    return max(loaded, key=lambda s: (s["weight_kg"], s["reps"]))


def e1rm_trend(sets: Iterable[dict], exercise_key: str) -> list[tuple[str, Optional[float]]]:
    """Per-session (date, top-set E1RM) for one exercise, ascending by date."""
    out = []
    for day, ss in sessions_for(sets, exercise_key):
        ts = top_set(ss)
        out.append((day, e1rm(ts["weight_kg"], ts["reps"]) if ts else None))
    return out


def weekly_hard_sets_by_muscle(sets: Iterable[dict]) -> dict[tuple[str, str], int]:
    """{(iso_week, muscle): hard-set count}. Excludes warm-up and unknown muscle."""
    counts: dict[tuple[str, str], int] = defaultdict(int)
    for s in sets:
        if not is_working_set(s):
            continue
        muscle = muscle_group(s.get("category"), s.get("exercise_name"))
        if muscle == "unknown":
            continue
        week = _iso_week(_session_date(s))
        if week is None:
            continue
        counts[(week, muscle)] += 1
    return dict(counts)


@dataclass(frozen=True)
class ProgressionAdvice:
    exercise_key: str
    action: str  # 'add_weight' | 'add_reps' | 'hold' | 'insufficient_data'
    current_top_weight_kg: Optional[float]
    suggested_weight_kg: Optional[float]
    increment_kg: Optional[float]
    rationale: str


def _muscle_for(sets: Iterable[dict], exercise_key: str) -> str:
    cat = next((s.get("category") for s in working_sets_for(sets, exercise_key)), None)
    return muscle_group(cat, exercise_key)


def recommend(
    sets: Iterable[dict],
    exercise_key: str,
    profile: Optional[CoachProfile] = None,
) -> ProgressionAdvice:
    """Double progression + NSCA 2-for-2: add load only after the top of the rep
    range is held for 2 consecutive sessions at the same weight; otherwise add
    reps toward the top; if hit once, hold one session to confirm."""
    profile = profile or CoachProfile()
    sets = list(sets)
    sessions = sessions_for(sets, exercise_key)
    if not sessions:
        return ProgressionAdvice(exercise_key, "insufficient_data", None, None, None,
                                 "no working sets recorded")
    rep_hi = profile.rep_range[1]
    inc = profile.increment_kg(_muscle_for(sets, exercise_key))
    last_top = top_set(sessions[-1][1])
    cur_w = last_top["weight_kg"] if last_top else None

    if len(sessions) >= 2:
        t1, t2 = top_set(sessions[-1][1]), top_set(sessions[-2][1])
        if (
            t1 and t2
            and t1["reps"] >= rep_hi and t2["reps"] >= rep_hi
            and abs((t1["weight_kg"] or 0) - (t2["weight_kg"] or 0)) < 1e-6
        ):
            new_w = round((cur_w or 0) + inc, 2)
            return ProgressionAdvice(
                exercise_key, "add_weight", cur_w, new_w, inc,
                f"top set {t1['reps']}/{t2['reps']} reps ≥ {rep_hi} for 2 sessions at "
                f"{cur_w}kg → +{inc}kg (double progression / 2-for-2)",
            )

    if last_top and last_top["reps"] < rep_hi:
        return ProgressionAdvice(
            exercise_key, "add_reps", cur_w, cur_w, None,
            f"last top set {last_top['reps']} reps < {rep_hi} → add reps before load",
        )

    return ProgressionAdvice(
        exercise_key, "hold", cur_w, cur_w, None,
        f"hit top of range ({rep_hi}) once; repeat once more to confirm before adding load",
    )


@dataclass(frozen=True)
class DeloadSignal:
    triggered: bool
    reasons: list[str]


def deload_signal(
    sets: Iterable[dict],
    exercise_key: str,
    profile: Optional[CoachProfile] = None,
) -> DeloadSignal:
    """Objective deload proxies (RIR is unavailable from Garmin): completed-rep
    decline, MRV proximity, and a time-based fallback over many training weeks."""
    profile = profile or CoachProfile()
    sets = list(sets)
    reasons: list[str] = []
    sessions = sessions_for(sets, exercise_key)

    if len(sessions) >= 3:
        tops = [top_set(ss) for _, ss in sessions[-3:]]
        reps = [t["reps"] for t in tops if t]
        if len(reps) == 3 and reps[0] > reps[1] > reps[2]:
            reasons.append(f"top-set reps declined 3 sessions ({reps[0]}→{reps[1]}→{reps[2]})")

    muscle = _muscle_for(sets, exercise_key)
    if muscle != "unknown":
        weeks = weekly_hard_sets_by_muscle(sets)
        muscle_weeks = {w: c for (w, m), c in weeks.items() if m == muscle}
        if muscle_weeks:
            latest = max(muscle_weeks)
            _, _, mrv = profile.landmarks(muscle)
            if muscle_weeks[latest] >= mrv:
                reasons.append(f"{muscle} weekly hard sets {muscle_weeks[latest]} ≥ MRV {mrv}")

    trained_weeks = {
        _iso_week(_session_date(s))
        for s in working_sets_for(sets, exercise_key)
    }
    trained_weeks.discard(None)
    if len(trained_weeks) >= 6:
        reasons.append(f"{len(trained_weeks)} training weeks without a deload (time fallback)")

    return DeloadSignal(bool(reasons), reasons)
