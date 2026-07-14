"""WP4 — turn a guarded WorkoutPlan into a precise Garmin Connect Strength
Builder spec (the exact steps to one-tap-create the workout, incl. target weight).

Phase 0 confirmed the user's Fenix 8 supports predefined target weight + guided
strength, so ``supports_target_weight`` defaults True. When False (other model),
the spec degrades to a card + watch-record-only note (v2 §5 WP4 fallback). This
module is PURE (plan -> spec); the Bridge/Slack presentation lives in the UI WPs.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from agents.zoro.coach.guardrail import WorkoutPlan


@dataclass(frozen=True)
class BuilderStep:
    order: int
    exercise_key: str
    category: str
    sets: int
    reps: str                       # "6-8" or "8" when low==high
    target_weight_kg: Optional[float]
    rest_sec: int
    is_warmup: bool


@dataclass(frozen=True)
class GarminBuilderSpec:
    title: str
    steps: list[BuilderStep]
    supports_target_weight: bool
    notes: str


def _reps_label(lo: int, hi: int) -> str:
    return str(lo) if lo == hi else f"{lo}-{hi}"


def build_garmin_spec(plan: WorkoutPlan, *, supports_target_weight: bool = True) -> GarminBuilderSpec:
    steps = [
        BuilderStep(
            order=ex.order,
            exercise_key=ex.exercise_key,
            category=ex.category,
            sets=ex.sets,
            reps=_reps_label(ex.reps_low, ex.reps_high),
            # Drop preloaded weight entirely on watches that can't carry it.
            target_weight_kg=ex.target_weight_kg if supports_target_weight else None,
            rest_sec=ex.rest_sec,
            is_warmup=ex.is_warmup,
        )
        for ex in sorted(plan.exercises, key=lambda e: e.order)
    ]
    if supports_target_weight:
        notes = plan.notes
    else:
        notes = (
            "此錶款不支援預載目標重量 → 退化為「課表卡片 + 手錶純記錄」："
            "依下列 reps/休息執行，重量自行抓並於組後記錄。 " + plan.notes
        ).strip()
    return GarminBuilderSpec(
        title=plan.title,
        steps=steps,
        supports_target_weight=supports_target_weight,
        notes=notes,
    )


def render_markdown(spec: GarminBuilderSpec) -> str:
    """A human-followable checklist for building the workout in Garmin Connect."""
    lines = [f"### {spec.title}", ""]
    if spec.notes:
        lines += [f"> {spec.notes}", ""]
    for s in spec.steps:
        tag = "暖身 " if s.is_warmup else ""
        weight = f" @ {s.target_weight_kg}kg" if s.target_weight_kg else ""
        rest = f"，休 {s.rest_sec}s" if s.rest_sec else ""
        lines.append(f"{s.order}. {tag}{s.exercise_key} — {s.sets}×{s.reps}{weight}{rest}")
    if not spec.supports_target_weight:
        lines += ["", "_（目標重量未預載：手錶只記錄）_"]
    return "\n".join(lines)
