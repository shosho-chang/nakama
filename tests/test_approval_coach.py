"""WP5 schema tests — coach approval payloads validate via the union adapter."""

from datetime import datetime, timedelta, timezone

from agents.zoro.coach.builder_spec import build_garmin_spec
from agents.zoro.coach.guardrail import (
    GuardrailContext,
    PlannedExercise,
    Violation,
    WorkoutPlan,
    validate_plan,
)
from agents.zoro.coach.hitl import to_compliance, to_write_payload
from agents.zoro.coach.profile import CoachProfile
from shared.schemas.approval import (
    ApprovalPayloadV1Adapter,
    ScheduleTrainingBlockV1,
    WriteGarminWorkoutV1,
)
from shared.schemas.coach import CoachComplianceV1

TPE = timezone(timedelta(hours=8))


def _guarded_payload():
    plan = WorkoutPlan(
        title="Push A",
        notes="within MRV",
        exercises=[
            PlannedExercise("BARBELL_BENCH_PRESS", "BENCH_PRESS", 3, 6, 8, 70.0, 180, 1),
        ],
    )
    ctx = GuardrailContext(CoachProfile(), e1rm_by_exercise={"BARBELL_BENCH_PRESS": 100.0})
    report = validate_plan(plan, ctx)
    spec = build_garmin_spec(plan)
    return to_write_payload(spec, report), report


def test_write_payload_round_trips_through_union_adapter():
    payload, _ = _guarded_payload()
    # The discriminated union must dispatch on action_type back to WriteGarminWorkoutV1.
    parsed = ApprovalPayloadV1Adapter.validate_python(payload.model_dump())
    assert isinstance(parsed, WriteGarminWorkoutV1)
    assert parsed.target_platform == "garmin"
    assert parsed.title == "Push A"
    assert parsed.diff_target_id is None
    assert parsed.compliance_flags.guardrail_ok is True
    assert parsed.steps[0].exercise_key == "BARBELL_BENCH_PRESS"
    assert parsed.steps[0].target_weight_kg == 70.0


def test_to_compliance_splits_blocks_and_warns():
    from agents.zoro.coach.guardrail import GuardrailReport

    rep = GuardrailReport(
        ok=False,
        violations=[
            Violation("load_rep", "block", "impossible"),
            Violation("deload", "warn", "deload due"),
        ],
    )
    c = to_compliance(rep)
    assert c.guardrail_ok is False
    assert any("load_rep" in b for b in c.blocks)
    assert any("deload" in w for w in c.warns)
    assert c.blocks and not any("deload" in b for b in c.blocks)


def test_schedule_training_block_dispatches_and_exposes_props():
    block = ScheduleTrainingBlockV1(
        action_type="schedule_training_block",
        target_site="calendar",
        task_slug="cwi-recovery",
        block_title="冷水浴 10min",
        start=datetime(2026, 6, 30, 19, 0, tzinfo=TPE),
        duration_min=15,
        reason="耐力日後恢復（避開重訓後 6-8h）",
    )
    parsed = ApprovalPayloadV1Adapter.validate_python(block.model_dump())
    assert isinstance(parsed, ScheduleTrainingBlockV1)
    assert parsed.target_platform == "calendar"
    assert parsed.title == "冷水浴 10min"
    assert parsed.diff_target_id == "cwi-recovery"
    assert parsed.category == "health"


def test_compliance_model_defaults():
    c = CoachComplianceV1(guardrail_ok=True)
    assert c.blocks == [] and c.warns == []
