from __future__ import annotations

from shared.thumbnail_arrangement_eval import score_arrangement_candidate
from shared.thumbnail_arrangement_generator import generate_arrangement_candidates
from shared.thumbnail_component_compatibility import score_arrangement_against_reference
from shared.thumbnail_idea import ParsedIdea
from shared.thumbnail_reference_corpus import best_deconstruction_examples


def _idea(
    reference_template_id: str,
    *,
    component_type: str,
    component_text: tuple[str, ...],
) -> ParsedIdea:
    return ParsedIdea(
        hook="Do this instead",
        emotion_key="explaining",
        emotion_input="explaining",
        visual=f"template={reference_template_id}; component={component_type}; host=right",
        decoration="",
        bg="blurred studio",
        reference_template_id=reference_template_id,
        title_pairing="95% of People STILL Prompt ChatGPT-5 Wrong",
        component_type=component_type,
        component_text=component_text,
    )


def test_component_compatibility_accepts_jeff_command_reference_layout():
    reference = best_deconstruction_examples("jeff_command_panel", limit=1)[0]
    candidate = generate_arrangement_candidates(
        parsed_idea=_idea(
            "jeff_command_panel",
            component_type="command_panel",
            component_text=("Wrong", "Do this instead"),
        ),
        person_placement={"cutout_box": [700, -20, 1320, 800], "face_box": [860, 44, 1130, 420]},
        layout_blocking={"content_side": "left"},
        limit=1,
    )[0]

    result = score_arrangement_against_reference(candidate=candidate, reference_record=reference)

    assert result.ok is True
    assert result.reference_id == "jeff_su_003"
    assert "command_panel" in result.observed_component_types


def test_component_compatibility_rejects_wrong_side_component_zone():
    reference = best_deconstruction_examples("jeff_command_panel", limit=1)[0]
    candidate = generate_arrangement_candidates(
        parsed_idea=_idea(
            "jeff_command_panel",
            component_type="command_panel",
            component_text=("Wrong", "Do this instead"),
        ),
        person_placement={"cutout_box": [700, -20, 1320, 800], "face_box": [860, 44, 1130, 420]},
        layout_blocking={"content_side": "right"},
        limit=1,
    )[0]

    result = score_arrangement_against_reference(candidate=candidate, reference_record=reference)

    assert result.ok is False
    assert any("wrong zone" in reason for reason in result.reasons)
    assert any(hint.startswith("move_component_to_reference_zone") for hint in result.repair_hints)


def test_component_compatibility_accepts_jeff_tool_panel_component_set():
    reference = best_deconstruction_examples("jeff_tool_header_panel", limit=1)[0]
    candidate = generate_arrangement_candidates(
        parsed_idea=_idea(
            "jeff_tool_header_panel",
            component_type="ui_panel",
            component_text=("NotebookLM", "Start Here"),
        ),
        person_placement={"cutout_box": [-40, -20, 620, 800], "face_box": [130, 44, 390, 420]},
        layout_blocking={"content_side": "right"},
        limit=2,
    )[1]

    result = score_arrangement_against_reference(candidate=candidate, reference_record=reference)

    assert result.ok is True
    assert set(result.observed_component_types) >= {"tool_label", "ui_panel", "logo_tile"}


def test_component_compatibility_accepts_ali_metric_arrow():
    reference = best_deconstruction_examples("ali_metric_arrow", limit=1)[0]
    candidate = generate_arrangement_candidates(
        parsed_idea=_idea(
            "ali_metric_arrow",
            component_type="metric_badge_pair",
            component_text=("0", "30"),
        ),
        person_placement={"cutout_box": [300, -30, 980, 800], "face_box": [460, 54, 780, 430]},
        layout_blocking={"content_side": "right"},
        limit=1,
    )[0]

    result = score_arrangement_against_reference(candidate=candidate, reference_record=reference)

    assert result.ok is True
    assert result.observed_component_types.count("metric_badge_pair") == 2
    assert "arrow" in result.observed_component_types


def test_component_compatibility_rejects_non_renderable_reference():
    reference = dict(best_deconstruction_examples("ali_metric_arrow", limit=1)[0])
    reference["renderable_v1"] = False
    candidate = generate_arrangement_candidates(
        parsed_idea=_idea(
            "ali_metric_arrow",
            component_type="metric_badge_pair",
            component_text=("0", "30"),
        ),
        person_placement={"cutout_box": [300, -30, 980, 800], "face_box": [460, 54, 780, 430]},
        layout_blocking={"content_side": "right"},
        limit=1,
    )[0]

    result = score_arrangement_against_reference(candidate=candidate, reference_record=reference)

    assert result.ok is False
    assert "choose_renderable_reference" in result.repair_hints


def test_arrangement_eval_uses_concrete_reference_gate():
    reference = best_deconstruction_examples("jeff_command_panel", limit=1)[0]
    candidate = generate_arrangement_candidates(
        parsed_idea=_idea(
            "jeff_command_panel",
            component_type="command_panel",
            component_text=("Wrong", "Do this instead"),
        ),
        person_placement={"cutout_box": [700, -20, 1320, 800], "face_box": [860, 44, 1130, 420]},
        layout_blocking={"content_side": "right"},
        limit=1,
    )[0]

    result = score_arrangement_candidate(candidate, reference_record=reference)

    assert result.status == "fail"
    assert any("reference mismatch" in reason for reason in result.reasons)
