"""這一輪 vs 上一輪（ADR-069 階段 6）。

2026-09-09 punch-L04：worker 回了一份把 34 個 event 整份平移同一個常數的複製品。
逐條看，每一條都「可能合理」——3 秒的位移在剪輯上完全正常。要看出它是機器產物，
必須把整份放在一起看：**34 個 event 位移完全相同**。

所以 diff 不只列出差異，還要把那個結論講出來（`uniform_shift_sec`）。一個 event
被搬 3 秒是判斷；34 個被搬同樣的 3 秒不是。
"""

from __future__ import annotations

from dataclasses import replace

import pytest

from agents.brook.script_video.finished_cut_production._correction import (
    EVENT_SHIFT_EPSILON_SEC,
    _event_diff,
    _latest_round,
    _uniform_shift,
)
from agents.brook.script_video.finished_cut_production._records import (
    EventRecord,
    _mint_accepted_stage,
)


def _event(
    index: int,
    t0: float,
    *,
    display: str = "轉折",
    implementation_kind: str = "fullscreen_transition",
) -> EventRecord:
    return EventRecord(
        event_id=f"event-{index}",
        master_cue_ids=(f"cue-{index}",),
        text_hash=f"{index:064d}",
        intent="章節卡",
        visual_status="approved",
        t0=t0,
        t1=t0 + 3.0,
        section_id=f"section-{index}",
        display=display,
        semantic_kind="chapter",
        implementation_kind=implementation_kind,
        lane="fullscreen_transition",
    )


def _stage(acceptance_id: str, stage: str, events: tuple[EventRecord, ...]):
    return _mint_accepted_stage(
        acceptance_id=acceptance_id,
        run_id="run-1",
        request_id="request-" + "1" * 32,
        stage=stage,  # type: ignore[arg-type]
        attempt=1,
        scope="full_stage",
        event_id=None,
        parent_acceptance_id=None,
        events=events,
    )


def test_the_first_round_marks_every_event_as_added() -> None:
    diff = _event_diff(tuple(_event(i, i * 10.0) for i in range(3)), ())

    assert [(row.event_id, row.changes) for row in diff] == [
        ("event-0", ("added",)),
        ("event-1", ("added",)),
        ("event-2", ("added",)),
    ]
    # 第一輪沒有「位移」可言，所以不該報告一個平移常數。
    assert _uniform_shift(diff) is None


def test_thirty_four_events_moved_by_the_same_constant_are_named_as_one_shift() -> None:
    """ADR-069 階段 6 指定的回歸：punch-L04 那份平移複製的回覆。"""

    previous = tuple(_event(i, i * 10.0) for i in range(34))
    current = tuple(_event(i, i * 10.0 + 4.25) for i in range(34))

    diff = _event_diff(current, previous)

    assert len(diff) == 34
    assert {row.changes for row in diff} == {("moved",)}
    assert {row.shift_sec for row in diff} == {4.25}
    assert _uniform_shift(diff) == 4.25


def test_one_event_moved_on_purpose_is_not_reported_as_a_uniform_shift() -> None:
    # 一個 event 被搬 3 秒是剪輯判斷。這一格有值才是異常訊號，所以它不能對
    # 單一改動也亮起來——亮了就沒有人會再相信它。
    diff = _event_diff(
        (_event(0, 0.0), _event(1, 13.0)),
        (_event(0, 0.0), _event(1, 10.0)),
    )

    assert [(row.event_id, row.changes, row.shift_sec) for row in diff] == [
        ("event-1", ("moved",), 3.0)
    ]
    assert _uniform_shift(diff) is None


def test_a_mixed_round_is_not_a_uniform_shift_even_if_the_moves_agree() -> None:
    # 兩個 event 位移相同，但還有一個是改寫——整份不是「平移複製」。
    diff = _event_diff(
        (_event(0, 4.0), _event(1, 14.0), _event(2, 20.0, display="新文")),
        (_event(0, 0.0), _event(1, 10.0), _event(2, 20.0, display="原文")),
    )

    assert _uniform_shift(diff) is None
    assert {row.event_id: row.changes for row in diff} == {
        "event-0": ("moved",),
        "event-1": ("moved",),
        "event-2": ("retitled",),
    }


def test_a_rewritten_card_carries_the_old_text_and_the_new_text() -> None:
    diff = _event_diff((_event(0, 0.0, display="新文"),), (_event(0, 0.0, display="原文"),))

    assert len(diff) == 1
    assert diff[0].changes == ("retitled",)
    assert (diff[0].previous_display, diff[0].display) == ("原文", "新文")


def test_a_card_that_changed_kind_is_recast() -> None:
    diff = _event_diff(
        (_event(0, 0.0, implementation_kind="hero_title"),),
        (_event(0, 0.0, implementation_kind="fullscreen_transition"),),
    )

    assert diff[0].changes == ("recast",)
    assert diff[0].implementation_kind == "hero_title"


def test_an_event_can_be_moved_and_rewritten_at_once() -> None:
    # 強迫二選一會讓其中一半的事實消失，所以 `changes` 是集合。
    diff = _event_diff((_event(0, 9.0, display="新文"),), (_event(0, 0.0, display="原文"),))

    assert diff[0].changes == ("moved", "retitled")


def test_a_removed_event_is_reported_where_it_last_was() -> None:
    diff = _event_diff((_event(0, 0.0),), (_event(0, 0.0), _event(1, 42.0)))

    assert [(row.event_id, row.changes, row.t0) for row in diff] == [
        ("event-1", ("removed",), 42.0)
    ]
    assert diff[0].previous_display == "轉折"
    assert diff[0].display is None


def test_an_unchanged_event_is_not_listed_at_all() -> None:
    # diff 是給人看「有什麼變了」。把 34 個沒動的 event 一起列出來，就等於沒有 diff。
    assert _event_diff((_event(0, 0.0),), (_event(0, 0.0),)) == ()


@pytest.mark.parametrize("drift", [0.0, EVENT_SHIFT_EPSILON_SEC])
def test_frame_level_quantisation_is_not_a_move(drift: float) -> None:
    assert _event_diff((_event(0, drift),), (_event(0, 0.0),)) == ()


def test_the_diff_is_ordered_by_where_things_land(tmp_path) -> None:
    diff = _event_diff(
        (_event(2, 5.0), _event(0, 1.0), _event(1, 3.0)),
        (),
    )

    assert [row.t0 for row in diff] == [1.0, 3.0, 5.0]


def test_the_round_is_the_furthest_stage_reached_and_its_latest_attempt() -> None:
    """「這一輪」是走得最遠那一關的最後一次驗收。

    同一關可以被 event retry 重試好幾次；`accepted_stage_history` 是 append 上去的，
    所以順序就是時間順序。
    """

    stages = (
        _stage("acceptance-director", "director", (_event(0, 0.0),)),
        _stage("acceptance-dp-1", "dp", (_event(0, 0.0),)),
        _stage("acceptance-dp-2", "dp", (_event(0, 7.0),)),
    )

    assert _latest_round(stages).acceptance_id == "acceptance-dp-2"
    assert _latest_round(()) is None


def test_a_card_that_only_got_longer_is_still_a_change() -> None:
    """同一個落點、多撐兩秒——只比 t0 的話這一列根本不會出現。

    片長護欄也看不出這種改動（總長可以不變），所以 diff 是唯一會講的人。
    """
    before = (_event(0, 10.0),)
    after = (replace(before[0], t1=before[0].t1 + 2.0),)

    diff = _event_diff(after, before)

    assert [(row.event_id, row.changes) for row in diff] == [("event-0", ("retimed",))]


def test_swapping_the_asset_behind_an_event_is_a_change() -> None:
    """同一句話、同一個落點，換了另一支 B-roll——畫面全變了，文字一個字沒動。"""
    before = (replace(_event(0, 10.0), asset_ref="active-sha256:" + "a" * 64),)
    after = (replace(before[0], asset_ref="active-sha256:" + "b" * 64),)

    diff = _event_diff(after, before)

    assert [(row.event_id, row.changes) for row in diff] == [("event-0", ("reshot",))]


def test_one_event_can_be_moved_and_retimed_and_reshot_at_once() -> None:
    """`changes` 是集合不是分類——強迫二選一會讓其中一半的事實消失。"""
    before = (replace(_event(0, 10.0), asset_ref="active-sha256:" + "a" * 64),)
    after = (
        replace(
            before[0],
            t0=20.0,
            t1=25.0,
            display="改寫過的標題",
            asset_ref="active-sha256:" + "b" * 64,
        ),
    )

    diff = _event_diff(after, before)

    assert diff[0].changes == ("moved", "retimed", "retitled", "reshot")


def test_frame_level_jitter_is_not_a_length_change() -> None:
    """1/30 秒的量化誤差不算改長度——算進去的話每一輪都會整份標成 retimed。"""
    before = (_event(0, 10.0),)
    after = (replace(before[0], t1=before[0].t1 + EVENT_SHIFT_EPSILON_SEC / 2),)

    assert _event_diff(after, before) == ()
