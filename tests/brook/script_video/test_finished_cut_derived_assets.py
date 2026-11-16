from __future__ import annotations

from agents.brook.script_video.finished_cut_production._context import (
    CueAnchor,
    CutSourceRange,
    EditorialCutContext,
)
from agents.brook.script_video.finished_cut_production._derived_assets import (
    DerivedAssetGeometry,
    DerivedAssetInstruction,
)
from agents.brook.script_video.finished_cut_production._projection import (
    layout_identity,
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
    # hero_title 的落點自 2026-09-09 起必須逐字回應語意證據，所以「證據長、落點短」
    # 這個情境改用 identity_card 驗——要測的是 show_sec 取自 placement 而不是語意跨度。
    semantic = context.derive_anchor(("cue-1", "cue-2"))
    placement = context.derive_visual_placement(
        semantic_cue_ids=semantic.master_cue_ids,
        placement_cue_ids=("cue-1",),
        semantic_kind="identity_card",
    )
    instruction = DerivedAssetInstruction(
        component_id="component:hero",
        event_id="event-hero",
        semantic_kind="identity_card",
        implementation_kind="identity_card",
        lane="identity_card",
        display="完整命題",
        t0=placement.t0,
        t1=placement.t1,
        source_asset_ref=None,
        geometry=DerivedAssetGeometry(1920, 1080, layout_identity("identity_card")),
        recipe_identity="recipe-sha256:" + "b" * 64,
    )

    assert semantic.t1 - semantic.t0 == 60.0
    assert instruction.show_sec == 4.0
