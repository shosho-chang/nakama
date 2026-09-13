"""品牌 badge 落點推導。

手冊（.claude/skills/longform-cut/SKILL.md:197）：
「只出現開場（收在名牌進場前，如 7.4s）+ 每個轉場卡結束後 ~8s」，鋪 track 5。
"""

from __future__ import annotations

import pytest

from agents.brook.script_video.finished_cut_production._brand_badge import (
    BRAND_BADGE_TRACK_INDEX,
    BrandBadgeOverlay,
    derive_brand_badge_overlays,
)
from agents.brook.script_video.finished_cut_production._records import (
    _mint_projected_component,
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
