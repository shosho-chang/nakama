"""視覺詞彙只能有一個真相來源（ADR-069 階段 1）。

2026-09-09 的 regression：版本號同時寫在 `_engine._LAYOUT_VERSIONS` 與
`_long_visual_renderer._RECIPES`，9-08 把 hero bump 到 v2 時兩邊沒同步，渲染器對
不上就丟 `long visual geometry does not match its canonical layout`——27 個測試一起
紅。版本現在住在 `_projection.VOCABULARY`，這幾條鎖住「只有那一份」。

ADR-069 階段 1 把同一套詞彙的 9 份副本全部收斂到 `VOCABULARY`：lane track、
generated／passthrough、資產類別、檔案後綴、Release reader 白名單、持久化 lane
白名單。下面的 identity 測試（`is`）鎖住下游**不是抄一份而是指向同一個物件**——
值相等的測試擋不住有人把副本改成「剛好一樣」。
"""

from __future__ import annotations

from typing import get_args

import pytest

from agents.brook.script_video.finished_cut_production import (
    _derived_assets,
    _engine,
    _policy,
    _resolve_fusion,
    _visual_assets,
)
from agents.brook.script_video.finished_cut_production._long_visual_renderer import (
    _RECIPES,
)
from agents.brook.script_video.finished_cut_production._projection import (
    _ACTIVE_COMPONENT_LANES,
    _ACTIVE_PROJECTION_COMBINATIONS,
    _WORKER_PROJECTION_COMBINATIONS,
    ASSET_BACKED_IMPLEMENTATIONS,
    ASSET_KIND_BY_IMPLEMENTATION,
    GENERATED_IMPLEMENTATIONS,
    LANE_TRACKS,
    LAYOUT_VERSIONS,
    MEDIA_SUFFIX_BY_IMPLEMENTATION,
    NEUTRAL_PASSTHROUGH_IMPLEMENTATIONS,
    PERSISTED_COMPONENT_LANES,
    RELEASE_PROJECTIONS,
    SOURCE_ASSET_KIND_BY_IMPLEMENTATION,
    VOCABULARY,
    ComponentLane,
    layout_identity,
)

#: 渲染器的 role 名 → 契約層的 implementation_kind。
_ROLE_TO_IMPLEMENTATION = {
    "chapter": "fullscreen_transition",
    "hero_title": "hero_title",
    "identity_card": "identity_card",
    "visual_effect": "visual_effect",
}


@pytest.mark.parametrize(("role", "implementation_kind"), sorted(_ROLE_TO_IMPLEMENTATION.items()))
def test_renderer_recipe_reads_the_contract_layout_version(
    role: str, implementation_kind: str
) -> None:
    assert _RECIPES[role]["layout_identity"] == layout_identity(implementation_kind)


def test_every_renderer_role_has_a_declared_layout_version() -> None:
    assert set(_ROLE_TO_IMPLEMENTATION.values()) <= set(LAYOUT_VERSIONS)


def test_every_generated_active_implementation_has_a_layout_version() -> None:
    """現役的生成字卡都要有版位版本，否則會靜默退回 v1、渲染器直接拒收。"""
    generated = {
        implementation_kind
        for _semantic, implementation_kind, _lane in _ACTIVE_PROJECTION_COMBINATIONS
        if implementation_kind in _ROLE_TO_IMPLEMENTATION.values()
    }
    missing = sorted(kind for kind in generated if kind not in LAYOUT_VERSIONS)
    assert missing == []


# --- 詞彙只有一份（ADR-069 階段 1）-------------------------------------------


def test_component_lane_literal_matches_the_vocabulary() -> None:
    """型別層的 lane 白名單無法從資料推導，所以在這裡鎖住兩邊一致。"""
    declared = set(get_args(ComponentLane))
    from_vocabulary = {spec.lane for spec in VOCABULARY.values()}

    assert declared == from_vocabulary


def test_lane_track_map_is_the_vocabulary_not_a_copy() -> None:
    """`_resolve_fusion` 決定 B-roll 鋪在哪一軌；它以前自己抄了一份。"""
    assert _resolve_fusion._LANE_TRACKS is LANE_TRACKS


def test_generated_and_passthrough_sets_are_the_vocabulary_not_copies() -> None:
    assert _derived_assets._GENERATED_IMPLEMENTATIONS is GENERATED_IMPLEMENTATIONS
    assert (
        _derived_assets._NEUTRAL_PASSTHROUGH_IMPLEMENTATIONS is NEUTRAL_PASSTHROUGH_IMPLEMENTATIONS
    )


def test_generated_and_passthrough_do_not_overlap_and_cover_every_asset_kind() -> None:
    assert not (GENERATED_IMPLEMENTATIONS & NEUTRAL_PASSTHROUGH_IMPLEMENTATIONS)
    assert GENERATED_IMPLEMENTATIONS | NEUTRAL_PASSTHROUGH_IMPLEMENTATIONS == set(
        ASSET_KIND_BY_IMPLEMENTATION
    )


def test_release_reader_accepts_every_active_projection_plus_retired() -> None:
    """reader 比 writer 寬鬆是刻意的；但不可以比 writer 窄。"""
    assert _ACTIVE_PROJECTION_COMBINATIONS <= RELEASE_PROJECTIONS
    assert ("supporting_title", "supporting_title", "supporting_title") in RELEASE_PROJECTIONS


def test_persisted_lanes_cover_active_lanes_plus_retired() -> None:
    """既有 run JSON 帶著退役 lane，reader 不能因此整份炸掉。"""
    assert set(_ACTIVE_COMPONENT_LANES) <= PERSISTED_COMPONENT_LANES
    assert "visual_effect" in PERSISTED_COMPONENT_LANES


def test_retired_implementations_are_never_worker_selectable() -> None:
    """退役詞彙只為讀得回既有 receipt；worker 提案不出來。"""
    worker_kinds = {kind for _s, kind, _l in _WORKER_PROJECTION_COMBINATIONS}
    retired = {kind for kind, spec in VOCABULARY.items() if spec.retired}

    assert not (worker_kinds & retired)


def test_core_only_implementations_are_active_but_not_worker_selectable() -> None:
    """`camera_correction` 由 core 鑄，worker 看不到——這是既有的不對稱，別弄丟。"""
    worker_kinds = {kind for _s, kind, _l in _WORKER_PROJECTION_COMBINATIONS}
    active_kinds = {kind for _s, kind, _l in _ACTIVE_PROJECTION_COMBINATIONS}

    assert "camera_correction" in active_kinds
    assert "camera_correction" not in worker_kinds


@pytest.mark.parametrize(
    "implementation_kind",
    sorted(kind for kind, spec in VOCABULARY.items() if not spec.retired),
)
def test_no_active_implementation_is_half_declared(implementation_kind: str) -> None:
    """新增一種視覺元素只改 `VOCABULARY` 一個檔——但不能只填一半。

    這條是 brand badge 那次的解法：四道門讀同一張表之後，漏填任何一個欄位都在這裡
    當場擋下來，不會變成「渲染器對不上版位」那種離根因很遠的錯。
    """
    spec = VOCABULARY[implementation_kind]

    assert (spec.semantic_kind, implementation_kind, spec.lane) in _ACTIVE_PROJECTION_COMBINATIONS
    assert LANE_TRACKS[spec.lane] == spec.track_index
    assert spec.lane in PERSISTED_COMPONENT_LANES
    if spec.generated:
        assert spec.layout_version is not None, "生成的字卡沒有版位版本會靜默退回 v1"
        assert spec.media_suffix is not None, "生成的字卡沒有後綴，渲染器驗不了產物"
        assert LAYOUT_VERSIONS[implementation_kind] == spec.layout_version
        assert MEDIA_SUFFIX_BY_IMPLEMENTATION[implementation_kind] == spec.media_suffix
    if spec.asset_kind is not None:
        assert ASSET_KIND_BY_IMPLEMENTATION[implementation_kind] is spec.asset_kind


def test_engine_asset_kind_maps_are_the_vocabulary_not_copies() -> None:
    """階段 1 漏掉的兩份就在 `_engine`。

    `source` 是 worker 挑進來的素材類別，`asset_kind` 是成品的類別——person_inset
    挑 PHOTO、產 COMPOSITE，所以是兩張表，不是同一張抄兩次。
    """
    assert _engine._ASSET_KIND_BY_IMPLEMENTATION is SOURCE_ASSET_KIND_BY_IMPLEMENTATION
    assert _engine._FINAL_ASSET_KIND_BY_IMPLEMENTATION is ASSET_KIND_BY_IMPLEMENTATION
    assert _engine._NEUTRAL_PASSTHROUGH_IMPLEMENTATIONS is NEUTRAL_PASSTHROUGH_IMPLEMENTATIONS


def test_passthrough_set_is_the_vocabulary_in_both_builders() -> None:
    assert _visual_assets._NEUTRAL_PASSTHROUGH is NEUTRAL_PASSTHROUGH_IMPLEMENTATIONS


def test_policy_visual_coverage_set_is_the_vocabulary() -> None:
    assert _policy.VISUAL_COVERAGE_BROLL_IMPLEMENTATIONS is ASSET_BACKED_IMPLEMENTATIONS


def test_asset_backed_implementations_are_exactly_those_with_a_source_kind() -> None:
    """`person_inset` 吃素材但不是 passthrough——這條分界別再弄丟。"""
    assert ASSET_BACKED_IMPLEMENTATIONS == set(SOURCE_ASSET_KIND_BY_IMPLEMENTATION)
    assert NEUTRAL_PASSTHROUGH_IMPLEMENTATIONS < ASSET_BACKED_IMPLEMENTATIONS
