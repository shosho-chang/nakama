from __future__ import annotations

from shared.thumbnail_reference_templates import (
    build_title_template_match_plan,
    format_reference_template_pack_for_prompt,
    format_title_template_match_plan_for_prompt,
    select_reference_templates,
)


def test_select_reference_templates_prefers_shosho_health_list_for_creatine():
    matches = select_reference_templates(
        title_candidates=["肌酸不只練肌肉：3 個你沒聽過的妙用"],
        project_brief="探討肌酸、大腦、抗老、認知與研究證據",
    )

    assert matches[0].template.template_id == "shosho_benefit_list_card"
    assert matches[0].score > matches[-1].score


def test_select_reference_templates_prefers_jeff_tool_panel_for_ai_tutorial():
    matches = select_reference_templates(
        title_candidates=["Learn 80% of NotebookLM in Under 13 Minutes"],
        project_brief="AI tool tutorial with clean UI panel",
    )

    assert matches[0].template.template_id == "jeff_tool_header_panel"


def test_format_reference_template_pack_names_the_required_metadata_line():
    text = format_reference_template_pack_for_prompt(
        title_candidates=["肌酸不只練肌肉：3 個你沒聽過的妙用"],
        project_brief="健康與大腦研究",
    )

    assert "Reference templates selected before thumbnail brief" in text
    assert "shosho_benefit_list_card" in text
    assert "reference_template: <one selected reference_template ID>" in text


def test_build_title_template_match_plan_adds_slot_brief_for_each_title():
    plan = build_title_template_match_plan(
        title_candidates=[
            "肌酸不只練肌肉：3 個你沒聽過的妙用",
            "Learn 80% of NotebookLM in Under 13 Minutes",
        ],
        project_brief="肌酸、大腦、抗老、認知與研究證據",
    )

    assert len(plan) == 2
    assert plan[0].title_id == "T01"
    assert plan[0].best_template.template_id == "shosho_benefit_list_card"
    assert plan[0].component_type == "benefit_list_card"
    assert len(plan[0].component_text) == 3
    assert plan[0].host_directive
    options = {option.template.template_id: option for option in plan[0].template_options}
    assert options["shosho_benefit_list_card"].component_type == "benefit_list_card"
    assert "ali_metric_arrow" not in options
    assert "ali_social_quote_card" not in options
    assert plan[1].best_template.template_id == "jeff_tool_header_panel"


def test_format_title_template_match_plan_is_the_brainstorm_input_contract():
    text = format_title_template_match_plan_for_prompt(
        title_candidates=["肌酸不只練肌肉：3 個你沒聽過的妙用"],
        project_brief="肌酸、大腦、抗老、認知與研究證據",
    )

    assert "Title-template match plan" in text
    assert "### T01" in text
    assert "best_template: shosho_benefit_list_card" in text
    assert "template_options" in text
    assert "shosho_benefit_list_card:" in text
    assert "component=benefit_list_card" in text
    assert "ali_metric_arrow:" not in text
    assert "component=metric_badge_pair" not in text
    assert "brief_contract" in text
    assert "component: <component from the same template_option>" in text
