"""品牌 badge 落點推導。

手冊（.claude/skills/longform-cut/SKILL.md:197）：
「只出現開場（收在名牌進場前，如 7.4s）+ 每個轉場卡結束後 ~8s」，鋪 track 5。
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from agents.brook.script_video.finished_cut_production._brand_badge import (
    BRAND_BADGE_SLUG_SECONDS,
    BRAND_BADGE_TRACK_INDEX,
    BrandBadgeOverlay,
    derive_brand_badge_overlays,
)
from agents.brook.script_video.finished_cut_production._codex_semantic import (
    _response_schema,
)
from agents.brook.script_video.finished_cut_production._materialization import (
    MaterializationError,
    _validate_brand_badge_assets,
    _validate_final_assets,
)
from agents.brook.script_video.finished_cut_production._projection import (
    _ACTIVE_COMPONENT_LANES,
    _ACTIVE_PROJECTION_COMBINATIONS,
    _ACTIVE_SEMANTIC_KINDS,
    _WORKER_PROJECTION_COMBINATIONS,
    LANE_TRACKS,
    VOCABULARY,
)
from agents.brook.script_video.finished_cut_production._records import (
    EventRecord,
    MaterializationPlan,
    _mint_materialization_plan,
    _mint_projected_component,
)
from agents.brook.script_video.finished_cut_production._timeline_apply import (
    BRAND_BADGE_MEDIA_SUFFIX,
    BrandBadgeAssetCatalog,
    PreRenderedAsset,
    PreRenderedAssetCatalog,
    TimelineApplyError,
    project_timeline_application,
)


def _component(component_id: str, lane: str, t0: float, t1: float):
    semantic = {
        "fullscreen_transition": "chapter",
        "identity_card": "identity_card",
        "hero_title": "hero_title",
    }[lane]
    return _mint_projected_component(
        component_id=component_id,
        event_id=f"event-{component_id}",
        semantic_kind=semantic,
        implementation_kind=lane,
        lane=lane,
        display="測試",
        t0=t0,
        t1=t1,
        asset_ref="asset-sha256:" + "a" * 64,
    )


def test_opening_badge_and_one_per_transition() -> None:
    components = (
        _component("nc", "identity_card", 55.8, 61.8),
        _component("tr-1", "fullscreen_transition", 52.6, 55.6),
        _component("tr-2", "fullscreen_transition", 107.9, 110.9),
    )

    overlays = derive_brand_badge_overlays(components=components, duration_sec=693.1)

    # tr-1 收在 55.6s，名牌 55.8s 進場——badge 只剩 0.2 秒，塞不下任何一支，
    # 而且左下角會跟名牌撞在一起，所以那一段沒有 badge。
    assert [overlay.origin for overlay in overlays] == [
        "opening",
        "after_transition:tr-2",
    ]
    assert all(overlay.track_index == BRAND_BADGE_TRACK_INDEX for overlay in overlays)
    assert overlays[0].t0 == 0.0
    assert overlays[0].slug == "brand-badge-7s"
    assert overlays[1].t0 == pytest.approx(110.9)
    assert overlays[1].t1 == pytest.approx(118.9)
    assert overlays[1].slug == "brand-badge-8s"


def test_opening_badge_is_dropped_when_the_namecard_enters_too_early() -> None:
    """定長素材不能裁——窗口塞不下最短那支就不放，不是硬剪掉淡出。"""
    components = (
        _component("nc", "identity_card", 5.0, 11.0),
        _component("tr-1", "fullscreen_transition", 52.6, 55.6),
    )

    overlays = derive_brand_badge_overlays(components=components, duration_sec=693.1)

    assert [overlay.origin for overlay in overlays] == ["after_transition:tr-1"]


def test_badge_never_runs_into_the_next_transition_card() -> None:
    components = (
        _component("tr-1", "fullscreen_transition", 10.0, 13.0),
        _component("tr-2", "fullscreen_transition", 18.0, 21.0),
    )

    overlays = derive_brand_badge_overlays(components=components, duration_sec=693.1)

    # tr-1 之後只剩 5 秒（13.0 → 18.0），最短的 7.4s 也塞不下，所以那一段不放。
    # 開場 badge 不受影響——這一支沒有名牌，窗口就是完整的 7.4 秒。
    assert [overlay.origin for overlay in overlays] == ["opening", "after_transition:tr-2"]


def test_badge_is_clamped_by_the_end_of_the_cut() -> None:
    components = (_component("tr-1", "fullscreen_transition", 100.0, 103.0),)

    overlays = derive_brand_badge_overlays(components=components, duration_sec=108.0)

    # 片長 108 秒，轉場卡 103 秒收掉，只剩 5 秒——放不下，那一段沒有 badge。
    assert [overlay.origin for overlay in overlays] == ["opening"]


def test_overlay_rejects_invalid_timing() -> None:
    with pytest.raises(ValueError):
        BrandBadgeOverlay(
            overlay_id="badge:opening",
            slug="brand-badge-7s",
            track_index=BRAND_BADGE_TRACK_INDEX,
            t0=5.0,
            t1=5.0,
            origin="opening",
        )


def test_badge_never_shares_the_lower_left_with_the_namecard() -> None:
    """名牌與 badge 都在左下角，同框就是擠（手冊第 197 行）。

    2026-09-09 第一版只擋了開場那一段，結果 0:52 轉場卡之後的 badge 正好壓在
    0:55 進場的名牌上——修修 review 的 timeline 上就看得到。
    """
    components = (
        _component("tr-1", "fullscreen_transition", 52.6, 55.6),
        _component("nc", "identity_card", 55.8, 61.8),
    )

    overlays = derive_brand_badge_overlays(components=components, duration_sec=693.1)

    assert [overlay.origin for overlay in overlays] == ["opening"]
    assert overlays[0].t1 <= 55.8


# --- 接線：落點推導 → V5 軌 -------------------------------------------------
#
# 2026-09-09 的 regression 有兩層。落點推導是第一層（上面那幾條）；這一段鎖住第二
# 層：算出來的 overlay 真的被投影成 placement、真的落在 track 5、素材真的解析得到。
# 「算得出來但沒人用」在畫面上跟「算不出來」是同一件事。

_BADGE_SECONDS = dict(BRAND_BADGE_SLUG_SECONDS)
_DEFAULT_BADGE_SLUGS = ("brand-badge-7s", "brand-badge-8s")


def _badge_asset_root(tmp_path: Path, slugs: tuple[str, ...] = _DEFAULT_BADGE_SLUGS) -> Path:
    root = tmp_path / "assets" / "broll"
    root.mkdir(parents=True)
    for slug in slugs:
        (root / f"{slug}{BRAND_BADGE_MEDIA_SUFFIX}").write_bytes(slug.encode())
    return root


def _declared_duration_probe(path: Path) -> float:
    return _BADGE_SECONDS[path.stem]


def _badge_catalog(root: Path, *, duration_probe=None) -> BrandBadgeAssetCatalog:
    return BrandBadgeAssetCatalog(root, duration_probe=duration_probe or _declared_duration_probe)


def _transition_plan(transition_count: int, *, duration_sec: float = 600.0) -> MaterializationPlan:
    """一支有 N 張滿版轉場卡的長片。轉場卡之間留得下一整段 badge。"""
    components = tuple(
        _component(f"tr-{index}", "fullscreen_transition", 100.0 * index, 100.0 * index + 3.0)
        for index in range(1, transition_count + 1)
    )
    events = tuple(
        EventRecord(
            event_id=component.event_id,
            master_cue_ids=(f"cue-{component.component_id}",),
            text_hash="c" * 64,
            intent="transition",
            asset_ref=component.asset_ref,
            visual_status="approved",
        )
        for component in components
    )
    return _mint_materialization_plan(
        plan_id="plan-badge",
        run_id="run-badge",
        command_id="command-badge",
        episode_id="episode-001",
        cut_id="punch-L03",
        format="long",
        director_acceptance_id="director-001",
        dp_acceptance_id="dp-001",
        visual_acceptance_id="visual-001",
        events=events,
        components=components,
        duration_sec=duration_sec,
    )


def _component_catalog(tmp_path: Path, plan: MaterializationPlan) -> PreRenderedAssetCatalog:
    path = tmp_path / "component.mov"
    path.write_bytes(b"component")
    return PreRenderedAssetCatalog(
        tuple(
            PreRenderedAsset(reference=reference, path=path)
            for reference in {component.asset_ref for component in plan.components}
        )
    )


@pytest.mark.parametrize("transition_count", [1, 3, 5])
def test_every_overlay_becomes_one_track_five_placement(
    tmp_path: Path, transition_count: int
) -> None:
    """開場一段 ＋ 每張轉場卡之後一段，全部鋪在 badge 的 lane（track 5）。"""
    plan = _transition_plan(transition_count)
    assert len(plan.brand_badge_overlays) == transition_count + 1

    application = project_timeline_application(
        plan,
        _component_catalog(tmp_path, plan),
        brand_badge_assets=_badge_catalog(_badge_asset_root(tmp_path)),
    )

    badges = [placement for placement in application.placements if placement.is_brand_badge]
    assert len(badges) == transition_count + 1
    assert {LANE_TRACKS[placement.lane] for placement in badges} == {BRAND_BADGE_TRACK_INDEX}
    assert {LANE_TRACKS[placement.lane] for placement in badges} == {5}
    # component 那一邊原封不動——badge 不是多出來的一張卡，是另一條軌。
    assert len(application.placements) - len(badges) == len(plan.components)
    assert [placement.component_id for placement in badges] == [
        overlay.overlay_id for overlay in plan.brand_badge_overlays
    ]
    assert [placement.t0 for placement in badges] == [
        overlay.t0 for overlay in plan.brand_badge_overlays
    ]
    assert {placement.source_path.name for placement in badges} <= {
        f"brand-badge-7s{BRAND_BADGE_MEDIA_SUFFIX}",
        f"brand-badge-8s{BRAND_BADGE_MEDIA_SUFFIX}",
    }


def test_badge_placements_carry_no_component_or_event_identity(tmp_path: Path) -> None:
    """badge 沒有 event，也不是任何 component 的實現——下游不該把它當成一張卡。"""
    plan = _transition_plan(1)

    application = project_timeline_application(
        plan,
        _component_catalog(tmp_path, plan),
        brand_badge_assets=_badge_catalog(_badge_asset_root(tmp_path)),
    )

    badges = [placement for placement in application.placements if placement.is_brand_badge]
    assert badges
    for placement in badges:
        assert placement.event_id == ""
        assert placement.component_id.startswith("badge:")
        assert placement.component_id not in {
            component.component_id for component in plan.components
        }
        assert placement.semantic_kind == "brand_badge"


def test_missing_badge_media_fails_loud_and_names_the_badge(tmp_path: Path) -> None:
    """定長素材缺檔不是「這一段沒有 badge」，是接線／資料夾錯了。"""
    plan = _transition_plan(2)
    root = _badge_asset_root(tmp_path, slugs=("brand-badge-7s",))

    with pytest.raises(TimelineApplyError, match="brand badge asset is missing") as raised:
        project_timeline_application(
            plan,
            _component_catalog(tmp_path, plan),
            brand_badge_assets=_badge_catalog(root),
        )

    assert "brand-badge-8s" in str(raised.value)


def test_badge_media_of_the_wrong_length_fails_loud(tmp_path: Path) -> None:
    """定長預合成的淡出烘在檔案裡；長度對不上就代表那支不是它宣稱的那一支。"""
    plan = _transition_plan(1)

    with pytest.raises(TimelineApplyError, match="not its declared length") as raised:
        project_timeline_application(
            plan,
            _component_catalog(tmp_path, plan),
            brand_badge_assets=_badge_catalog(
                _badge_asset_root(tmp_path),
                duration_probe=lambda path: 5.0,
            ),
        )

    assert "brand-badge" in str(raised.value)


def test_a_plan_with_overlays_refuses_to_apply_without_a_badge_catalog(tmp_path: Path) -> None:
    """沒給素材目錄要當場炸，不能靜默少鋪一軌——那正是 9-09 那次的樣子。"""
    plan = _transition_plan(1)

    with pytest.raises(TimelineApplyError, match="no brand badge asset catalog"):
        project_timeline_application(plan, _component_catalog(tmp_path, plan))


def test_a_plan_without_overlays_needs_no_badge_catalog(tmp_path: Path) -> None:
    """沒有片長就算不出 badge，不該因此要求素材目錄。"""
    plan = _transition_plan(1, duration_sec=0.0)
    assert plan.brand_badge_overlays == ()

    application = project_timeline_application(plan, _component_catalog(tmp_path, plan))

    assert all(not placement.is_brand_badge for placement in application.placements)


def test_badge_is_never_worker_selectable() -> None:
    """落點由規則算得出來，就不該讓 worker 有機會提案錯（`_brand_badge` docstring）。"""
    worker_kinds = {kind for _semantic, kind, _lane in _WORKER_PROJECTION_COMBINATIONS}
    worker_lanes = {lane for _semantic, _kind, lane in _WORKER_PROJECTION_COMBINATIONS}

    assert "brand_badge" not in worker_kinds
    assert "brand_badge" not in worker_lanes
    assert "brand_badge" not in _ACTIVE_SEMANTIC_KINDS
    assert "brand_badge" not in _ACTIVE_COMPONENT_LANES
    assert VOCABULARY["brand_badge"].worker_selectable is False
    # core 端要鑄得出來——投影那一關讀的是現役集合，不是 worker 可提案集合。
    assert ("brand_badge", "brand_badge", "brand_badge") in _ACTIVE_PROJECTION_COMBINATIONS


@pytest.mark.parametrize("stage", ["director", "dp"])
def test_badge_is_not_in_the_worker_response_schema(stage: str) -> None:
    """worker 看得到的 enum 裡有 badge，它就會被提案——那是多一個做錯的地方。"""
    assert "brand_badge" not in json.dumps(_response_schema(stage))


def test_the_vocabulary_and_the_derivation_agree_on_track_five() -> None:
    """兩邊各自宣告 badge 在第幾軌，就會有一天只改到一邊。"""
    assert LANE_TRACKS["brand_badge"] == BRAND_BADGE_TRACK_INDEX
    assert VOCABULARY["brand_badge"].track_index == BRAND_BADGE_TRACK_INDEX


def test_materialization_blocks_a_plan_whose_badge_media_is_absent(tmp_path: Path) -> None:
    """缺 badge 素材有自己的 reason_code，不跟 component 的素材問題混在一起。"""
    plan = _transition_plan(1)
    (tmp_path / "assets" / "broll").mkdir(parents=True)

    class _NeverResolves:
        def resolve_active_asset(self, reference: str) -> object:
            raise AssertionError("badge 驗檔要在 component 素材之前擋下來")

    with pytest.raises(MaterializationError) as raised:
        _validate_final_assets(plan, _NeverResolves(), episode_root=tmp_path)

    assert raised.value.reason_code == "brand_badge_asset_unavailable"
    assert "brand-badge" in str(raised.value)


def test_materialization_accepts_a_plan_whose_badge_media_is_present(tmp_path: Path) -> None:
    plan = _transition_plan(1)
    _badge_asset_root(tmp_path)

    _validate_brand_badge_assets(plan, episode_root=tmp_path)
