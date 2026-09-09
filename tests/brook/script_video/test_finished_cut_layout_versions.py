"""版位版本只能有一個真相來源。

2026-09-09 的 regression：版本號同時寫在 `_engine._LAYOUT_VERSIONS` 與
`_long_visual_renderer._RECIPES`，9-08 把 hero bump 到 v2 時兩邊沒同步，渲染器對
不上就丟 `long visual geometry does not match its canonical layout`——27 個測試一起
紅。版本現在住在 `_projection.LAYOUT_VERSIONS`，這幾條鎖住「只有那一份」。
"""

from __future__ import annotations

import pytest

from agents.brook.script_video.finished_cut_production._long_visual_renderer import (
    _RECIPES,
)
from agents.brook.script_video.finished_cut_production._projection import (
    _ACTIVE_PROJECTION_COMBINATIONS,
    LAYOUT_VERSIONS,
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
