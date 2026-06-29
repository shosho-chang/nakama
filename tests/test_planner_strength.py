"""WP3 planner tests — parse + generate pipeline with the LLM mocked."""

import json

from agents.zoro.coach.guardrail import GuardrailContext
from agents.zoro.coach.planner_strength import generate_plan, parse_plan, system_prompt
from agents.zoro.coach.profile import CoachProfile

_GOOD = {
    "title": "Push A",
    "is_deload": False,
    "notes": "within MRV",
    "exercises": [
        {"exercise_key": "BARBELL_BENCH_PRESS", "category": "BENCH_PRESS", "sets": 3,
         "reps_low": 6, "reps_high": 8, "target_weight_kg": 70.0, "rest_sec": 180,
         "order": 1, "is_warmup": False},
    ],
}

_BAD = {
    "title": "Impossible",
    "exercises": [
        {"exercise_key": "BARBELL_BENCH_PRESS", "category": "BENCH_PRESS", "sets": 3,
         "reps_low": 12, "reps_high": 15, "target_weight_kg": 95.0, "rest_sec": 120,
         "order": 1},  # 95% 1RM @ 12 reps -> guardrail must block
    ],
}


def test_system_prompt_loads():
    sp = system_prompt()
    assert "ACSM" in sp and "JSON" in sp


def test_parse_plan_fields():
    plan = parse_plan(_GOOD)
    assert plan.title == "Push A"
    e = plan.exercises[0]
    assert e.exercise_key == "BARBELL_BENCH_PRESS"
    assert e.category == "BENCH_PRESS"
    assert e.sets == 3 and e.reps_low == 6 and e.reps_high == 8
    assert e.target_weight_kg == 70.0
    assert e.is_warmup is False


def test_generate_plan_happy_path():
    ctx = GuardrailContext(CoachProfile(), e1rm_by_exercise={"BARBELL_BENCH_PRESS": 100.0})
    # LLM returns fenced JSON (extractor must strip the fence).
    fake = "```json\n" + json.dumps(_GOOD) + "\n```"
    plan, report = generate_plan(
        profile=CoachProfile(), advice=[], ctx=ctx, ask_fn=lambda *a, **k: fake
    )
    assert plan.title == "Push A"
    assert report.ok is True


def test_generate_plan_guardrail_catches_bad_llm_output():
    ctx = GuardrailContext(CoachProfile(), e1rm_by_exercise={"BARBELL_BENCH_PRESS": 100.0})
    plan, report = generate_plan(
        profile=CoachProfile(), advice=[], ctx=ctx, ask_fn=lambda *a, **k: json.dumps(_BAD)
    )
    assert report.ok is False
    assert any(v.rule == "load_rep" and v.severity == "block" for v in report.violations)
