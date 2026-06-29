"""WP5 (backend bridge): guarded builder spec -> HITL approval payload.

Pure mapping from coach domain objects (agents/zoro/coach) to the shared
ApprovalPayloadV1 union (shared/schemas) — agents -> shared, the right direction.
The Bridge `/bridge/drafts` rendering + Slack exception card + approval_queue
claim-side re-check are the UI half (deferred to the UI batch).
"""

from __future__ import annotations

from agents.zoro.coach.builder_spec import GarminBuilderSpec
from agents.zoro.coach.guardrail import GuardrailReport
from shared.schemas.approval import WriteGarminWorkoutV1
from shared.schemas.coach import CoachComplianceV1, StrengthBuilderStepV1


def to_compliance(report: GuardrailReport) -> CoachComplianceV1:
    """Flatten a GuardrailReport into the payload-carried compliance summary."""
    return CoachComplianceV1(
        guardrail_ok=report.ok,
        blocks=[f"{v.rule}: {v.detail}" for v in report.violations if v.severity == "block"],
        warns=[f"{v.rule}: {v.detail}" for v in report.violations if v.severity == "warn"],
    )


def to_write_payload(spec: GarminBuilderSpec, report: GuardrailReport) -> WriteGarminWorkoutV1:
    """Build the HITL payload for one-tap Garmin Strength Builder creation."""
    return WriteGarminWorkoutV1(
        action_type="write_garmin_workout",
        target_site="garmin",
        workout_title=spec.title,
        steps=[
            StrengthBuilderStepV1(
                order=s.order,
                exercise_key=s.exercise_key,
                category=s.category,
                sets=s.sets,
                reps=s.reps,
                target_weight_kg=s.target_weight_kg,
                rest_sec=s.rest_sec,
                is_warmup=s.is_warmup,
            )
            for s in spec.steps
        ],
        notes=spec.notes,
        supports_target_weight=spec.supports_target_weight,
        compliance_flags=to_compliance(report),
    )
