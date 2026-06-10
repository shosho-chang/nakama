from __future__ import annotations

from shared.thumbnail_arrangement_generator import (
    candidate_to_component_plan,
    generate_arrangement_candidates,
)
from shared.thumbnail_idea import ParsedIdea


def _idea(reference_template_id: str = "shosho_benefit_list_card") -> ParsedIdea:
    return ParsedIdea(
        hook="肌酸不只增肌",
        emotion_key="explaining",
        emotion_input="解釋",
        visual="便條紙上有護腦、抗老、認知三行",
        decoration="6",
        bg="模糊 A-roll 工作室背景",
        lane="Ali Warm Explainer",
        recipe_id="ali_warm_evidence_list",
        reference_template_id=reference_template_id,
        title_pairing="肌酸的 6 個健康效益",
        asset_queries=("creatine jar cutout", "sticky note benefit list"),
        component_type="benefit_list_card",
        component_text=("護腦", "抗老", "認知"),
    )


def test_generate_benefit_card_arrangement_uses_contract_slots():
    candidates = generate_arrangement_candidates(
        parsed_idea=_idea(),
        person_placement={
            "cutout_box": [-90, -40, 650, 820],
            "face_box": [210, 52, 520, 430],
            "right_hand_box": [525, 320, 710, 560],
        },
        layout_blocking={"content_side": "right"},
    )

    first = candidates[0]
    payload = first.layer_by_id("card_right_third")

    assert first.template_id == "shosho_benefit_list_card"
    assert [layer.layer_id for layer in first.layers][:2] == ["background", "card_right_third"]
    assert payload.kind == "benefit_list_card"
    assert payload.content == ("護腦", "抗老", "認知")
    assert payload.box.x0 > 700
    assert first.layer_by_id("host").hand_boxes
    assert first.protected_text_boxes()


def test_generator_adds_optional_asset_as_second_variant():
    candidates = generate_arrangement_candidates(
        parsed_idea=_idea(),
        person_placement={"cutout_box": [-90, -40, 650, 820], "face_box": [210, 52, 520, 430]},
        layout_blocking={"content_side": "right"},
    )

    assert len(candidates) == 3
    assert not candidates[0].layers_by_role("supporting_asset")
    assert candidates[1].layers_by_role("supporting_asset")
    assert "with_optional_asset" in candidates[1].notes[0]


def test_generator_uses_left_payload_when_layout_requires_it():
    candidates = generate_arrangement_candidates(
        parsed_idea=_idea("ali_social_quote_card"),
        person_placement={"cutout_box": [620, -30, 1360, 820], "face_box": [810, 60, 1120, 430]},
        layout_blocking={"content_side": "left"},
        limit=1,
    )

    assert candidates[0].layer_by_id("quote_card_left").box.x1 < 700


def test_metric_template_keeps_both_metrics_and_arrow():
    idea = _idea("ali_metric_arrow")
    candidates = generate_arrangement_candidates(
        parsed_idea=idea,
        person_placement={"cutout_box": [300, -30, 980, 800], "face_box": [460, 54, 780, 430]},
        layout_blocking={"content_side": "right"},
        limit=1,
    )

    roles = [layer.role for layer in candidates[0].layers]
    assert roles.count("payload_metric") == 2
    assert "payload_arrow" in roles


def test_candidate_to_component_plan_matches_legacy_renderer_shape():
    candidate = generate_arrangement_candidates(
        parsed_idea=_idea(),
        person_placement={"cutout_box": [-90, -40, 650, 820], "face_box": [210, 52, 520, 430]},
        layout_blocking={"content_side": "right"},
        limit=1,
    )[0]

    plan = candidate_to_component_plan(candidate)

    selected = plan["selected_variant"]
    assert selected["reference_template_id"] == "shosho_benefit_list_card"
    assert selected["components"][0]["component_type"] == "benefit_list_card"
    assert selected["components"][0]["box"] == {"x": 730, "y": 115, "w": 499, "h": 375}
    assert plan["arrangement_candidate"]["candidate_id"] == "idea01-hyp01-cand01"
