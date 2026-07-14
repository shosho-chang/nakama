"""WP3 strength planner — LLM (ACSM 2026/NSCA) -> structured plan -> guardrail.

generate_plan() asks the LLM (routed by the current agent, cost attributed to
zoro) for a JSON workout, parses it, and runs the pure guardrail. It returns the
plan AND the guardrail report — it never auto-executes (HITL + WP4 own that).
The LLM call is injectable (``ask_fn``) so the parse + guardrail pipeline is
unit-testable without a live model.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Callable, Optional

from shared.llm import ask
from shared.llm_json import extract_json_object

from agents.zoro.coach.guardrail import (
    GuardrailContext,
    GuardrailReport,
    PlannedExercise,
    WorkoutPlan,
    validate_plan,
)
from agents.zoro.coach.profile import CoachProfile

_PROMPT_PATH = Path(__file__).resolve().parents[3] / "prompts" / "zoro" / "coach_strength.md"


def system_prompt() -> str:
    return _PROMPT_PATH.read_text(encoding="utf-8")


def build_user_prompt(
    profile: CoachProfile,
    advice: list[dict],
    *,
    recent_hard_sets_by_muscle: Optional[dict[str, int]] = None,
    e1rm_by_exercise: Optional[dict[str, float]] = None,
    deload_due: bool = False,
) -> str:
    """Serialize the deterministic context the LLM must plan against."""
    payload = {
        "profile": {
            "goal": profile.goal,
            "training_status": profile.training_status,
            "rep_range": list(profile.rep_range),
        },
        "deload_due": deload_due,
        "recent_hard_sets_by_muscle": recent_hard_sets_by_muscle or {},
        "working_e1rm_by_exercise_kg": e1rm_by_exercise or {},
        "wp2_advice": advice,
    }
    return (
        "依下列脈絡生成一次重訓 session 的結構化課表，只回 JSON：\n\n"
        + json.dumps(payload, ensure_ascii=False, indent=2)
    )


def parse_plan(d: dict) -> WorkoutPlan:
    """Parse the LLM's JSON object into a WorkoutPlan (defensive on missing keys)."""
    exercises = []
    for i, e in enumerate(d.get("exercises", []) or []):
        exercises.append(
            PlannedExercise(
                exercise_key=str(e.get("exercise_key", "")),
                category=str(e.get("category", "")),
                sets=int(e.get("sets", 0) or 0),
                reps_low=int(e.get("reps_low", 0) or 0),
                reps_high=int(e.get("reps_high", e.get("reps_low", 0)) or 0),
                target_weight_kg=(
                    float(e["target_weight_kg"])
                    if e.get("target_weight_kg") is not None
                    else None
                ),
                rest_sec=int(e.get("rest_sec", 0) or 0),
                order=int(e.get("order", i + 1) or (i + 1)),
                is_warmup=bool(e.get("is_warmup", False)),
            )
        )
    return WorkoutPlan(
        title=str(d.get("title", "")),
        exercises=exercises,
        notes=str(d.get("notes", "")),
        is_deload=bool(d.get("is_deload", False)),
    )


def generate_plan(
    *,
    profile: CoachProfile,
    advice: list[dict],
    ctx: GuardrailContext,
    deload_due: bool = False,
    ask_fn: Callable[..., str] = ask,
) -> tuple[WorkoutPlan, GuardrailReport]:
    """Generate a guarded strength plan. Returns (plan, guardrail report)."""
    raw = ask_fn(
        build_user_prompt(
            profile,
            advice,
            recent_hard_sets_by_muscle=ctx.recent_hard_sets_by_muscle,
            e1rm_by_exercise=ctx.e1rm_by_exercise,
            deload_due=deload_due,
        ),
        system=system_prompt(),
        task="coach_plan",
        temperature=0.3,
    )
    plan = parse_plan(extract_json_object(raw))
    report = validate_plan(plan, ctx)
    return plan, report
