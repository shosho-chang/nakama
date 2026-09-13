"""Hero 大字卡的落點必須逐字回應它的語意證據。

Hero 的「說什麼」由 Director 決定、「什麼時候說」原本由 DP 決定，而 DP 只被要求落在
Director 證據的**子集**內。Director 的證據可以橫跨一分多鐘，DP 挑最前面那幾句就合法
——卡片因此可以在講者說出那個主張之前就先講完。

2026-09-09 蘇予昕 punch-L04：Hero「原來這一切的源頭是我爸」落在 3:50.29，講者說出
「因此他看到原來源頭」是 5:13.96，早了 84 秒。修修 review 時直接刪掉。
"""

from __future__ import annotations

import pytest

from agents.brook.script_video.finished_cut_production._context import (
    CueAnchor,
    CutSourceRange,
    EditorialCutContext,
)


def _context() -> EditorialCutContext:
    return EditorialCutContext(
        episode_id="20260901 蘇予昕",
        cut_id="punch-L04",
        format="long",
        editorial_master_id="a" * 64,
        tight_cut_id="tight-1",
        duration_sec=693.0,
        source_ranges=(CutSourceRange(0.0, 693.0),),
        cues=(
            CueAnchor("cue-connect", "他就會突然幫我連結到", 230.0, 233.0, "section-03"),
            CueAnchor("cue-dad", "喔我爸就是這樣", 233.0, 236.0, "section-03"),
            CueAnchor("cue-source", "因此他看到原來源頭", 313.9, 317.0, "section-03"),
        ),
    )


def test_hero_placement_must_echo_its_semantic_proof() -> None:
    context = _context()

    with pytest.raises(ValueError, match="hero placement cue IDs must echo its semantic proof"):
        context.derive_visual_placement(
            semantic_cue_ids=("cue-connect", "cue-dad", "cue-source"),
            placement_cue_ids=("cue-connect",),
            semantic_kind="hero_title",
        )


def test_hero_placement_is_accepted_when_it_is_exactly_the_proof() -> None:
    context = _context()

    placement = context.derive_visual_placement(
        semantic_cue_ids=("cue-source",),
        placement_cue_ids=("cue-source",),
        semantic_kind="hero_title",
    )

    assert placement.t0 == pytest.approx(313.9)
    assert placement.t1 == pytest.approx(317.0)


def test_other_kinds_may_still_place_inside_a_wider_proof() -> None:
    """只有 Hero 與章節卡被鎖死；B-roll 仍可在證據內挑一個較短的落點。"""
    context = _context()

    placement = context.derive_visual_placement(
        semantic_cue_ids=("cue-connect", "cue-dad"),
        placement_cue_ids=("cue-dad",),
        semantic_kind="b_roll",
    )

    assert placement.t0 == pytest.approx(233.0)
    assert placement.t1 == pytest.approx(236.0)
