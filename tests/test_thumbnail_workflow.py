from __future__ import annotations

from pathlib import Path

from agents.foundry.thumbnail_templates import TEMPLATES, list_templates
from shared.thumbnail_workflow import (
    DEFERRED_RECIPE_CARDS,
    DISALLOWED_VISUAL_ARCHETYPES,
    MODIFIER_ARCHETYPES,
    PRIMARY_TITLE_ARCHETYPES,
    RECIPE_CARDS,
    SUPPORTED_RENDERABLE_VISUAL_TAGS,
    format_thumbnail_workflow_pack_for_prompt,
)


def test_workflow_pack_is_ali_jeff_focused():
    text = format_thumbnail_workflow_pack_for_prompt()
    assert "Ali/Jeff thumbnail workflow pack" in text
    assert "Ali Warm Explainer" in text
    assert "Jeff Clean Tutorial" in text
    assert "Alex Hormozi-style dark aggression" in text


def test_modifiers_are_not_primary_title_archetypes():
    assert "T-A9" not in PRIMARY_TITLE_ARCHETYPES
    assert "T-A10" not in PRIMARY_TITLE_ARCHETYPES
    assert "T-A9" in MODIFIER_ARCHETYPES
    assert "T-A10" in MODIFIER_ARCHETYPES


def test_t_v6_is_disallowed_for_health_workflow():
    assert "T-V6" in DISALLOWED_VISUAL_ARCHETYPES
    assert "T-V6" in format_thumbnail_workflow_pack_for_prompt()
    assert "Do not use T-V6" in format_thumbnail_workflow_pack_for_prompt()


def test_recipe_cards_cover_expected_lanes_and_asset_needs():
    ids = {card.recipe_id for card in RECIPE_CARDS}
    assert "ali_warm_evidence_list" in ids
    assert "jeff_clean_tutorial_dual_zone" in ids
    assert "jeff_stack_or_tool_cluster" not in ids
    assert all(card.asset_needs for card in RECIPE_CARDS)


def test_active_recipe_cards_use_only_renderable_visual_tags():
    supported = set(SUPPORTED_RENDERABLE_VISUAL_TAGS)
    for card in RECIPE_CARDS:
        assert set(card.visual_tags).issubset(supported)


def test_deferred_tool_cluster_is_documented_not_active():
    deferred_ids = {card.recipe_id for card in DEFERRED_RECIPE_CARDS}
    assert "jeff_stack_or_tool_cluster" in deferred_ids
    text = format_thumbnail_workflow_pack_for_prompt()
    assert "Deferred recipes" in text
    assert "jeff_stack_or_tool_cluster" in text


def test_supported_renderable_tags_match_renderer_registry():
    """The LLM contract and renderer registry must stay in lockstep."""

    assert tuple(TEMPLATES) == SUPPORTED_RENDERABLE_VISUAL_TAGS
    assert (
        tuple(template.tv_id for template in list_templates())
        == SUPPORTED_RENDERABLE_VISUAL_TAGS
    )


def test_prompt_lists_only_renderer_supported_visual_tags():
    prompt = (
        Path(__file__).resolve().parents[1] / "prompts" / "thumbnail" / "brainstorm_youtube_v2.md"
    ).read_text(encoding="utf-8")

    rule_line = next(
        line for line in prompt.splitlines() if "Include exactly 1 renderable thumbnail" in line
    )
    for tag in SUPPORTED_RENDERABLE_VISUAL_TAGS:
        assert f"`{tag}`" in rule_line
    for unsupported in ("T-V4", "T-V5", "T-V7", "T-V9"):
        assert f"`{unsupported}`" not in rule_line


def test_composition_supports_every_renderer_template_overlay():
    composition = (
        Path(__file__).resolve().parents[1]
        / "video"
        / "compositions"
        / "thumbnail_youtube"
        / "index.html"
    ).read_text(encoding="utf-8")

    for template in list_templates():
        assert f'data-archetype="{template.overlay_archetype}"' in composition
