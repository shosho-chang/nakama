"""WP3 guardrail — PURE-program validation of a strength plan + concurrent
training interference. Never trusts the LLM to self-check (v2 §5 WP3).

A ``block`` violation means "do not execute"; a ``warn`` means "surface to the
human but allowed". ``validate_plan`` is deterministic — same input, same report
(that determinism IS the reproducibility guarantee for the validation layer).
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field
from typing import Optional

from agents.zoro.coach.muscle_map import muscle_group
from agents.zoro.coach.profile import CoachProfile

# Muscles that indoor cycling heavily recruits (quad-dominant interference).
_LEG_MUSCLES = {"quads", "hamstrings", "glutes", "calves", "posterior_chain"}
_ENDURANCE = {"cycling", "swim", "run"}
_NOVICE = {"novice", "beginner"}


# --------------------------------------------------------------------------- #
# Data model                                                                  #
# --------------------------------------------------------------------------- #

@dataclass(frozen=True)
class PlannedExercise:
    exercise_key: str
    category: str                      # FIT category, for muscle mapping
    sets: int
    reps_low: int
    reps_high: int
    target_weight_kg: Optional[float]
    rest_sec: int
    order: int
    is_warmup: bool = False


@dataclass(frozen=True)
class WorkoutPlan:
    title: str
    exercises: list[PlannedExercise]
    notes: str = ""
    is_deload: bool = False


@dataclass(frozen=True)
class GuardrailContext:
    profile: CoachProfile
    e1rm_by_exercise: dict[str, float] = field(default_factory=dict)        # working E1RM (kg)
    recent_hard_sets_by_muscle: dict[str, int] = field(default_factory=dict)  # already done this week
    injury_flags: frozenset[str] = frozenset()
    deload_due: bool = False


@dataclass(frozen=True)
class Violation:
    rule: str
    severity: str   # 'block' | 'warn'
    detail: str


@dataclass(frozen=True)
class GuardrailReport:
    ok: bool        # True when no 'block' violations
    violations: list[Violation]


@dataclass(frozen=True)
class ScheduledSession:
    weekday: int                       # 0=Mon .. 6=Sun
    modality: str                      # 'strength' | 'cycling' | 'swim' | 'run' | 'recovery'
    muscles: frozenset = frozenset()   # strength: target muscles; cycling: {'quads'} etc.
    start_hour: int = 12               # 0-23


# --------------------------------------------------------------------------- #
# Strength-plan checks                                                        #
# --------------------------------------------------------------------------- #

def _max_reps_at_pct(pct: float) -> float:
    """Approx max achievable reps at a given %1RM (inverse Epley)."""
    return 30.0 * (1.0 / pct - 1.0) if pct > 0 else 0.0


def check_load_rep(ex: PlannedExercise, e1rm_kg: Optional[float]) -> Optional[Violation]:
    """Bidirectional load/rep sanity. Blocks impossible high-load + high-rep
    pairings (and loads above estimated 1RM); low-load high-rep passes (ACSM 2026
    hypertrophy allows 30-100% 1RM near failure)."""
    if ex.is_warmup or not e1rm_kg or not ex.target_weight_kg or ex.target_weight_kg <= 0:
        return None
    pct = ex.target_weight_kg / e1rm_kg
    if pct > 1.05:
        return Violation("load_rep", "block",
                         f"{ex.exercise_key}: target {ex.target_weight_kg}kg > est 1RM {e1rm_kg}kg")
    max_reps = _max_reps_at_pct(pct)
    if ex.reps_low > max_reps * 1.3:   # 30% individual-variation margin
        return Violation("load_rep", "block",
                         f"{ex.exercise_key}: {ex.reps_low} reps @ ~{pct:.0%} 1RM is implausible "
                         f"(~{max_reps:.0f} reps max)")
    return None


def check_volume(plan: WorkoutPlan, ctx: GuardrailContext) -> list[Violation]:
    """Weekly hard-set cap: planned + already-done must not exceed MRV per muscle."""
    planned: dict[str, int] = defaultdict(int)
    for ex in plan.exercises:
        if ex.is_warmup:
            continue
        muscle = muscle_group(ex.category, ex.exercise_key)
        if muscle == "unknown":
            continue
        planned[muscle] += ex.sets
    out: list[Violation] = []
    for muscle, n in planned.items():
        _, _, mrv = ctx.profile.landmarks(muscle)
        total = n + ctx.recent_hard_sets_by_muscle.get(muscle, 0)
        if total > mrv:
            out.append(Violation("volume", "block",
                                 f"{muscle}: {total} weekly hard sets > MRV {mrv}"))
    return out


def check_1rm_safety(plan: WorkoutPlan, ctx: GuardrailContext) -> list[Violation]:
    """Novice or injured athletes must not be prescribed a 1RM max test."""
    if not (ctx.profile.training_status in _NOVICE or ctx.injury_flags):
        return []
    out: list[Violation] = []
    for ex in plan.exercises:
        if not ex.is_warmup and ex.reps_low <= 1 and ex.reps_high <= 1:
            out.append(Violation("1rm_safety", "block",
                                 f"{ex.exercise_key}: 1RM max test barred for novice/injured athlete"))
    return out


def check_deload(plan: WorkoutPlan, ctx: GuardrailContext) -> list[Violation]:
    if ctx.deload_due and not plan.is_deload:
        return [Violation("deload", "warn",
                          "deload signals present but plan is not marked a deload "
                          "(reduce volume ~40-50%, hold intensity ~70-80%)")]
    return []


def validate_plan(plan: WorkoutPlan, ctx: GuardrailContext) -> GuardrailReport:
    violations: list[Violation] = []
    for ex in plan.exercises:
        v = check_load_rep(ex, ctx.e1rm_by_exercise.get(ex.exercise_key))
        if v:
            violations.append(v)
    violations += check_volume(plan, ctx)
    violations += check_1rm_safety(plan, ctx)
    violations += check_deload(plan, ctx)
    ok = not any(v.severity == "block" for v in violations)
    return GuardrailReport(ok, violations)


# --------------------------------------------------------------------------- #
# Concurrent training interference                                            #
# --------------------------------------------------------------------------- #

def concurrent_guardrail(
    sessions: list[ScheduledSession],
    profile: CoachProfile,
    *,
    endurance_cap: Optional[int] = None,
) -> list[Violation]:
    """Concurrent-training interference rules (v2 §5 WP3): strength-before-endurance
    same day; leg strength vs indoor cycling >=6h apart; balanced endurance dose cap."""
    out: list[Violation] = []
    by_day: dict[int, list[ScheduledSession]] = defaultdict(list)
    for s in sessions:
        by_day[s.weekday].append(s)

    for day, day_sessions in by_day.items():
        strengths = [s for s in day_sessions if s.modality == "strength"]
        endurances = [s for s in day_sessions if s.modality in _ENDURANCE]
        for st in strengths:
            for en in endurances:
                if st.start_hour > en.start_hour:
                    out.append(Violation("concurrent_order", "warn",
                                         f"day {day}: schedule strength before {en.modality} "
                                         f"(strength {st.start_hour}h is after {en.modality} {en.start_hour}h)"))
                if (st.muscles & _LEG_MUSCLES) and en.modality == "cycling" \
                        and abs(st.start_hour - en.start_hour) < 6:
                    out.append(Violation("concurrent_interference", "block",
                                         f"day {day}: leg strength vs indoor cycling <6h apart "
                                         f"(quad interference) — separate by >=6h or onto different days"))

    cap = endurance_cap if endurance_cap is not None else (4 if profile.goal == "balanced" else 6)
    n_endurance = sum(1 for s in sessions if s.modality in _ENDURANCE)
    if n_endurance > cap:
        out.append(Violation("endurance_dose", "warn",
                             f"{n_endurance} endurance sessions/week > cap {cap} "
                             f"for season_priority={profile.goal}"))
    return out
