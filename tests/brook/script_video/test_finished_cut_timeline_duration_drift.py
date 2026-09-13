"""canonical Timeline 的「差超過一格」要在格上比，不能混用單位。

ApprovedCut 的 source_ranges 是秒，剪輯卻活在格線上：每個邊界最多帶半格的表示
誤差，段數一多就累積。20260721 punch-L03 有 59 段，timeline 16347 格、量化後
16346 格——內容只差一格，可是浮點和多帶了 0.0017 秒，舊式子算成 1.05 格而擋下。
"""

from __future__ import annotations

import pytest

from agents.brook.script_video.finished_cut_production._materialization import (
    _within_one_frame,
)

FPS = 30.0


def _old_rule(timeline_frames: int, duration_sec: float) -> bool:
    """舊判準：frame-exact 的長度去比浮點秒數，容忍度一格。"""
    return abs(timeline_frames / FPS - duration_sec) > 1.0 / FPS


def _new_rule(timeline_frames: int, duration_sec: float) -> bool:
    return not _within_one_frame(timeline_frames / FPS, duration_sec, FPS)


def test_a_non_finite_measurement_is_never_within_one_frame():
    assert not _within_one_frame(float("nan"), 10.0, FPS)
    assert not _within_one_frame(10.0, 10.0, 0.0)


@pytest.mark.parametrize("drift_frames", [0, 1, -1])
def test_within_one_frame_is_accepted(drift_frames):
    expected = 16346
    assert not _new_rule(expected + drift_frames, expected / FPS)


@pytest.mark.parametrize("drift_frames", [2, -2, 5])
def test_more_than_one_frame_is_still_rejected(drift_frames):
    """規則沒有被放寬——兩格照擋。"""
    expected = 16346
    assert _new_rule(expected + drift_frames, expected / FPS)


def test_the_real_punch_l03_case_passes_only_under_the_frame_rule():
    """59 段累積出的 0.0017 秒，不該把一格的差算成 1.05 格。"""
    timeline_frames = 16347
    duration_sec = 544.865  # source_ranges 的浮點和
    assert _old_rule(timeline_frames, duration_sec)
    assert not _new_rule(timeline_frames, duration_sec)


def test_a_genuinely_drifted_timeline_is_rejected_by_both():
    assert _old_rule(16400, 544.865)
    assert _new_rule(16400, 544.865)
