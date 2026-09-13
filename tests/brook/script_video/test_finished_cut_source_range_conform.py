"""來源端的格數要跟著實際記錄長度換算，而且 GetSourceEndFrame 是索引不是格數。

20260721 value-L02 被擋在：
    actual=(1127,1367,0,479,True); expected=(1127,1368±1,0,480|481,True)

記錄端 1367 在 1368±1 裡面，過了；卡住的是來源端。落點 8.02s、來源 59.94fps、
時間軸 30fps：`round(8.02*59.94)=481`，但 Resolve 先把記錄端取整成 240 格，
再 conform 回來源＝480 格，末格索引 479。舊檢查同時接受 480 與 481——那個
「索引或格數都算過」的集合等於沒有真正選邊，而且兩個值都是從浮點秒數來的，
跟 Resolve 實際做的兩層取整無關。
"""

from __future__ import annotations

import pytest

from agents.brook.script_video.finished_cut_production._resolve_fusion import (
    ResolveTransactionError,
    _conformed_source_frames,
)


def _old_rule(actual_source_end: int, source_duration_frames: int) -> bool:
    """舊判準：末格索引去比「用浮點秒數算出來的格數」，接受 n-1 或 n。"""
    return actual_source_end not in {source_duration_frames - 1, source_duration_frames}


def _new_rule(
    actual_source_start: int,
    actual_source_end: int,
    timeline_frames: int,
    source_fps: float,
    timeline_fps: float,
) -> bool:
    expected = _conformed_source_frames(timeline_frames, source_fps, timeline_fps)
    return abs((actual_source_end - actual_source_start + 1) - expected) > 1


def test_same_frame_rate_needs_no_conform():
    assert _conformed_source_frames(240, 30.0, 30.0) == 240


def test_a_faster_source_is_conformed_up():
    assert _conformed_source_frames(240, 59.94, 30.0) == 480


def test_a_slower_source_is_conformed_down():
    """punch-L03 那類 25fps 素材進 30fps 時間軸。"""
    assert _conformed_source_frames(240, 25.0, 30.0) == 200


@pytest.mark.parametrize("bad_fps", [0.0, -30.0])
def test_a_non_positive_frame_rate_is_refused(bad_fps):
    with pytest.raises(ResolveTransactionError, match="positive"):
        _conformed_source_frames(240, bad_fps, 30.0)
    with pytest.raises(ResolveTransactionError, match="positive"):
        _conformed_source_frames(240, 59.94, bad_fps)


def test_the_real_value_l02_case_passes_only_under_the_new_rule():
    assert _old_rule(479, 481)
    assert not _new_rule(0, 479, 240, 59.94, 30.0)


@pytest.mark.parametrize("drift", [0, 1, -1])
def test_one_frame_of_conform_slack_is_accepted(drift):
    assert not _new_rule(0, 479 + drift, 240, 59.94, 30.0)


@pytest.mark.parametrize("drift", [2, -2, 40])
def test_more_than_one_frame_is_still_rejected(drift):
    """規則沒有被放寬——真的抓錯一段來源照擋。"""
    assert _new_rule(0, 479 + drift, 240, 59.94, 30.0)


def test_a_source_span_from_the_wrong_place_is_rejected():
    """整段抓到別的地方：來源端長度對不上實際記錄長度。"""
    assert _new_rule(0, 239, 240, 59.94, 30.0)
