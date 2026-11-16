"""初剪時間軸：剪點檔檢查、AppendToTimeline 參數、放上去之後逐段讀回。不開 Resolve。"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from agents.brook.youtube_video import timeline as tl


def _write(tmp_path: Path, segments, **extra) -> Path:
    data = {
        "schema": tl.ROUGH_CUT_SCHEMA,
        "fps": "30000/1001",
        "media": str(tmp_path / "raw.mp4"),
        "segments": segments,
        **extra,
    }
    (tmp_path / tl.ROUGH_CUT_FILE).write_text(json.dumps(data), encoding="utf-8")
    return tmp_path


def test_load_and_length(tmp_path):
    cut = tl.load_rough_cut(
        _write(tmp_path, [{"src_in": 0, "src_out": 30}, {"src_in": 90, "src_out": 120}])
    )
    assert cut.segments == ((0, 30), (90, 120))
    assert cut.frames == 60 and cut.seconds == pytest.approx(60 * 1001 / 30000)


@pytest.mark.parametrize(
    "segments, extra, message",
    [
        ([], {}, "沒有任何一段"),
        ([{"src_in": 10, "src_out": 10}], {}, "要大於"),
        ([{"src_in": 0, "src_out": 50}, {"src_in": 40, "src_out": 60}], {}, "重疊"),
        ([{"src_in": 0, "src_out": 5}], {"fps": "25"}, "只支援"),
        ([{"src_in": 0, "src_out": 5}], {"schema": "x"}, "schema"),
    ],
)
def test_bad_cut_files(tmp_path, segments, extra, message):
    with pytest.raises(tl.TimelineError, match=message):
        tl.load_rough_cut(_write(tmp_path, segments, **extra))


def test_missing_file(tmp_path):
    with pytest.raises(tl.TimelineError, match="找不到"):
        tl.load_rough_cut(tmp_path)


def test_clip_infos_end_frame_is_exclusive(tmp_path):
    cut = tl.load_rough_cut(_write(tmp_path, [{"src_in": 5, "src_out": 35}]))
    assert tl.clip_infos("clip", cut) == [
        {"mediaPoolItem": "clip", "startFrame": 5, "endFrame": 35}
    ]


class Item:
    def __init__(self, src_frame, duration):
        self.src_frame, self.duration = src_frame, duration

    def GetSourceStartTime(self):  # noqa: N802 — Resolve API；實際秒數（從某個時間碼起算）
        return 32656.624 + self.src_frame * 1001 / 30000

    def GetDuration(self):  # noqa: N802
        return self.duration


class Timeline:
    def __init__(self, items):
        self.items = items

    def GetItemListInTrack(self, kind, index):  # noqa: N802
        return self.items


def test_check_placed(tmp_path):
    cut = tl.load_rough_cut(
        _write(tmp_path, [{"src_in": 0, "src_out": 30}, {"src_in": 9849, "src_out": 9879}])
    )
    tl.check_placed(Timeline([Item(0, 30), Item(9849, 30)]), cut)
    with pytest.raises(tl.TimelineError, match="放上 1 段"):
        tl.check_placed(Timeline([Item(0, 30)]), cut)
    with pytest.raises(tl.TimelineError, match="長度差 -1"):
        tl.check_placed(Timeline([Item(0, 29), Item(9849, 30)]), cut)
    with pytest.raises(tl.TimelineError, match="開始"):
        tl.check_placed(Timeline([Item(0, 30), Item(9858, 30)]), cut)


def test_extra_frames_are_trimmed_from_end_frame(tmp_path):
    cut = tl.load_rough_cut(
        _write(tmp_path, [{"src_in": 0, "src_out": 30}, {"src_in": 100, "src_out": 1934}])
    )
    errors = tl.duration_errors([Item(0, 30), Item(100, 1835)], cut)
    assert errors == [0, 1]
    ends = [b - e for (_, b), e in zip(cut.segments, errors)]
    assert tl.clip_infos("c", cut, ends)[1] == {
        "mediaPoolItem": "c",
        "startFrame": 100,
        "endFrame": 1933,
    }
