from __future__ import annotations

from shared.thumbnail_template_selector import (
    build_title_reference_family_match_plan,
    format_selected_reference_context,
    format_title_reference_family_match_plan_for_prompt,
    select_reference_family_matches,
)


def test_selector_matches_tool_tutorial_to_concrete_jeff_tool_reference():
    matches = select_reference_family_matches(
        title="Learn 80% of NotebookLM in Under 13 Minutes",
        project_brief="AI tool tutorial with clean UI panel",
    )

    assert matches[0].family_id == "jeff_tool_header_panel"
    assert matches[0].reference_id == "jeff_su_014"
    assert matches[0].record["image_path"].endswith("NotebookLM in Under 13 Minutes!.jpg")


def test_selector_matches_prompt_mistake_to_jeff_command_panel():
    matches = select_reference_family_matches(
        title="95% of People STILL Prompt ChatGPT-5 Wrong",
        project_brief="prompting mistakes and do this instead correction",
    )

    assert matches[0].family_id == "jeff_command_panel"
    assert matches[0].reference_id == "jeff_su_003"
    assert matches[0].score > matches[1].score


def test_selector_matches_money_transformation_to_ali_metric_arrow():
    matches = select_reference_family_matches(
        title="If I Wanted to Be a Millionaire Before 30, I'd Do This",
        project_brief="money transformation with before and after contrast",
    )

    assert matches[0].family_id == "ali_metric_arrow"
    assert matches[0].reference_id == "ali_abdaal_016"


def test_selector_matches_honest_advice_to_social_quote_card():
    matches = select_reference_family_matches(
        title="My honest advice to someone who feels behind in life",
        project_brief="soft personal reassurance",
    )

    assert matches[0].family_id == "ali_social_quote_card"
    assert matches[0].reference_id == "ali_abdaal_018"


def test_selector_matches_ai_agent_learning_path_to_roadmap_board():
    matches = select_reference_family_matches(
        title="The AI Agent Tutorial That Should've Been Your First",
        project_brief="no-code learning path and concept roadmap",
    )

    assert matches[0].family_id == "jeff_roadmap_board"
    assert matches[0].reference_id == "jeff_su_022"


def test_selector_excludes_archive_and_reference_only_by_default():
    matches = select_reference_family_matches(
        title="Top 6 AI Trends That Will Define 2026",
        project_brief="trend report backed by data",
        limit=20,
    )

    family_ids = {match.family_id for match in matches}
    assert "jeff_trend_collage" not in family_ids
    assert "ali_whiteboard_diagram" not in family_ids


def test_selector_can_include_non_renderable_when_explicitly_requested():
    matches = select_reference_family_matches(
        title="Top 6 AI Trends That Will Define 2026",
        project_brief="trend report backed by data",
        limit=20,
        include_non_renderable=True,
    )

    assert "jeff_trend_collage" in {match.family_id for match in matches}


def test_title_reference_family_match_plan_returns_concrete_records_per_title():
    plan = build_title_reference_family_match_plan(
        title_candidates=[
            "Learn 80% of NotebookLM in Under 13 Minutes",
            "My honest advice to someone who wants financial freedom",
        ],
        project_brief="AI tools and personal advice",
        limit_per_title=2,
    )

    assert plan[0]["title_id"] == "T01"
    assert plan[0]["matches"][0]["family_id"] == "jeff_tool_header_panel"
    assert plan[0]["matches"][0]["reference_id"] == "jeff_su_014"
    assert plan[1]["matches"][0]["family_id"] == "ali_social_quote_card"
    assert plan[1]["matches"][0]["reference_id"] == "ali_abdaal_019"


def test_prompt_formatter_is_concrete_but_compact():
    text = format_title_reference_family_match_plan_for_prompt(
        title_candidates=["95% of People STILL Prompt ChatGPT-5 Wrong"],
        project_brief="prompt correction tutorial",
        limit_per_title=2,
    )

    assert "Concrete Ali/Jeff reference match plan" in text
    assert "jeff_command_panel / jeff_su_003" in text
    assert "image_path:" in text
    assert len(text) < 2200


def test_prompt_formatter_omits_weak_non_executable_health_matches():
    text = format_title_reference_family_match_plan_for_prompt(
        title_candidates=["肌酸的 6 個健康效益：不只是增肌，更是護腦與抗老"],
        project_brief="肌酸 補充品 健康 長壽 腦部 抗老",
        limit_per_title=3,
    )

    assert "no strong executable Ali/Jeff reference match" in text
    assert "ali_metric_arrow /" not in text


def test_selected_reference_context_includes_family_and_record_contracts():
    match = select_reference_family_matches(
        title="If I Wanted to Be a Millionaire Before 30, I'd Do This",
        project_brief="before after money transformation",
        limit=1,
    )[0]
    text = format_selected_reference_context(match)

    assert "## Template family" in text
    assert "family_id: ali_metric_arrow" in text
    assert "## Concrete reference" in text
    assert "reference_id: ali_abdaal_016" in text
    assert "metric_left" in text
