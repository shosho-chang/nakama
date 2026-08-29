from __future__ import annotations

from agents.brook.script_video.finished_cut_production._assets import (
    AssetKind,
    WorkerCatalogItem,
)
from agents.brook.script_video.finished_cut_production._context import (
    CueAnchor,
    CutSourceRange,
    EditorialCutContext,
)
from agents.brook.script_video.finished_cut_production._derived_assets import (
    DerivedAssetBuildRequest,
    DerivedAssetGeometry,
    DerivedAssetInstruction,
)


def test_instruction_show_sec_uses_core_visual_placement_not_semantic_span() -> None:
    context = EditorialCutContext(
        episode_id="episode-1",
        cut_id="long-1",
        format="long",
        editorial_master_id="a" * 64,
        tight_cut_id="tight-1",
        duration_sec=60.0,
        source_ranges=(CutSourceRange(100.0, 160.0),),
        cues=(
            CueAnchor("cue-1", "visual moment", 0.0, 4.0, "section-1"),
            CueAnchor("cue-2", "semantic continuation", 4.0, 60.0, "section-1"),
        ),
    )
    semantic = context.derive_anchor(("cue-1", "cue-2"))
    placement = context.derive_visual_placement(
        semantic_cue_ids=semantic.master_cue_ids,
        placement_cue_ids=("cue-1",),
        semantic_kind="hero_title",
    )
    instruction = DerivedAssetInstruction(
        component_id="component:hero",
        event_id="event-hero",
        semantic_kind="hero_title",
        implementation_kind="hero_title",
        lane="hero_title",
        display="完整命題",
        t0=placement.t0,
        t1=placement.t1,
        source_asset_ref=None,
        geometry=DerivedAssetGeometry(1920, 1080, "hero_title:v1"),
        recipe_identity="recipe-sha256:" + "b" * 64,
    )

    assert semantic.t1 - semantic.t0 == 60.0
    assert instruction.show_sec == 4.0


def test_build_request_carries_core_recipe_and_neutral_catalog_context() -> None:
    catalog_item = WorkerCatalogItem(
        reference="asset-sha256:" + "a" * 64,
        kind=AssetKind.PHOTO,
        visual_summary="Headshot of the named guest on a neutral background",
        width=1600,
        height=1200,
        duration_sec=None,
    )
    geometry = DerivedAssetGeometry(
        target_width=1920,
        target_height=1080,
        layout_identity="person-inset-v1",
    )
    instruction = DerivedAssetInstruction(
        component_id="component:event-1",
        event_id="event-1",
        semantic_kind="b_roll",
        implementation_kind="person_inset",
        lane="b_roll",
        display="簡立峰博士",
        t0=12.0,
        t1=18.0,
        source_asset_ref=catalog_item.reference,
        geometry=geometry,
        recipe_identity="recipe-sha256:" + "b" * 64,
    )

    request = DerivedAssetBuildRequest(
        build_request_id="build-request-1",
        run_id="run-1",
        command_id="approved-cut:1",
        episode_id="episode-1",
        cut_id="punch-L04",
        format="long",
        dp_acceptance_id="acceptance-dp-1",
        scope="full_stage",
        event_id=None,
        instructions=(instruction,),
        worker_catalog_items=(catalog_item,),
    )

    assert request.instructions == (instruction,)
    assert request.worker_catalog_items == (catalog_item,)
    assert request.episode_id == "episode-1"
    assert request.format == "long"
