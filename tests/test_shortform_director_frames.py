"""run_shortform_director 的機位片段取格：畫面長度要跟 Master 聲音逐段同長（純函數）。

20261007 李海碩 punch-S05：聲音逐保留段在 Master 時間軸取整，畫面卻在機位時間上
逐 shot 取整；兩者差一個小數格的同步位移，剪點陸續差一格，片尾畫面比聲音短一格，
最後一格黑底只剩字卡。
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

from run_shortform_director import _piece_source_frames, _placed_span  # noqa: E402

from shared.editorial_conform import project_master_range  # noqa: E402

FPS = 30.0


def _cmap(offset_sec: float) -> dict:
    return {
        "sources": {"cam2": {"path": "2_CAMERA 2.mp4", "offset_sec": offset_sec}},
        "segments": [{"master_start_sec": 0.0, "master_end_sec": 600.0, "source_start_sec": 0.0}],
    }


def test_shot_pieces_sum_to_the_master_audio_frames_of_the_keep_segment():
    # 半格的同步位移：機位時間上逐 shot 取整，最容易一進一出地差一格
    cmap = _cmap(offset_sec=-63.9833)
    seg_s, seg_e = 28.117, 101.683
    cuts = [seg_s, 30.25, 30.95, 31.567, 32.183, 38.6, 61.017, seg_e]
    total = 0
    for s, e in zip(cuts, cuts[1:], strict=False):
        for piece in project_master_range(cmap, s, e, source_key="cam2"):
            f0, f1 = _piece_source_frames(piece, FPS)
            total += f1 - f0
    assert total == int(round(seg_e * FPS)) - int(round(seg_s * FPS))


def test_in_point_still_follows_the_camera_timeline():
    piece = {
        "master_start_sec": 30.25,
        "master_end_sec": 30.95,
        "source_start_sec": 94.2333,
        "source_end_sec": 94.9333,
    }
    f0, f1 = _piece_source_frames(piece, FPS)
    assert f0 == int(round(94.2333 * FPS))
    assert f1 - f0 == int(round(30.95 * FPS)) - int(round(30.25 * FPS))


class _Item:
    def __init__(self, duration=None):
        if duration is not None:
            self.GetDuration = lambda: duration


def test_placed_span_trusts_get_duration_over_rounded_source_report():
    # 要 62064–62602：回報終點捨入成 62601，但實際放上去的是 538 格——不能補償
    assert _placed_span(_Item(duration=538), 62064, 62601) == 538


def test_placed_span_falls_back_to_reported_frames_without_get_duration():
    assert _placed_span(_Item(), 62064, 62601) == 537
