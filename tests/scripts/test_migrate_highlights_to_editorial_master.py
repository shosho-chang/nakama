"""把 Editorial Master 之前開採的候選重新錨到 master 時鐘。

2026-09-03 抹布：release SRT 2,630 cue、master.srt 2,566 cue——兩個時鐘。
候選的 `t_start`／`t_end` 直接拿去對 master 切會切到錯的句子
（`run_short_tighten` 就是直接讀這兩個欄位）。
"""

from __future__ import annotations

from pathlib import Path

import pytest

from scripts.migrate_highlights_to_editorial_master import (
    MigrationError,
    _parse_srt,
    _resolve,
    build_index_map,
)


def _srt(path: Path, cues: list[tuple[int, float, float, str]]) -> Path:
    def stamp(value: float) -> str:
        ms = int(round(value * 1000))
        h, ms = divmod(ms, 3600_000)
        m, ms = divmod(ms, 60_000)
        s, ms = divmod(ms, 1000)
        return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"

    path.write_text(
        "\n\n".join(f"{i}\n{stamp(a)} --> {stamp(b)}\n{text}" for i, a, b, text in cues),
        encoding="utf-8",
    )
    return path


def test_alignment_survives_repeated_short_lines(tmp_path):
    """「對」「嗯」這種短句一集出現幾十次——逐條找相同文字必然對錯。

    所以用整份序列的全域對齊，不是逐條搜尋。
    """
    source = _srt(
        tmp_path / "src.srt",
        [
            (1, 0, 1, "對"),
            (2, 1, 2, "開場寒暄"),
            (3, 2, 3, "對"),
            (4, 3, 4, "正文開始"),
            (5, 4, 5, "對"),
            (6, 5, 6, "正文結束"),
        ],
    )
    # 成品把 cue 1–3 的寒暄剪掉了
    master = _srt(
        tmp_path / "master.srt",
        [
            (1, 0, 1, "正文開始"),
            (2, 1, 2, "對"),
            (3, 2, 3, "正文結束"),
        ],
    )
    mapping = build_index_map(_parse_srt(source), _parse_srt(master))
    assert mapping[4] == 1, "「正文開始」要對到成品的第 1 條"
    assert mapping[6] == 3
    assert 2 not in mapping, "被剪掉的寒暄不該出現在對照表裡"


def test_boundary_inside_a_removed_span_fails_loudly(tmp_path):
    """邊界那一句在成品裡被剪掉時**不猜**——指名是哪一段的哪一端。"""
    source = _srt(
        tmp_path / "src.srt",
        [
            (1, 0, 1, "被剪掉的開場"),
            (2, 1, 2, "留下來的話"),
        ],
    )
    master = _srt(tmp_path / "master.srt", [(1, 0, 1, "留下來的話")])
    src_cues, mst_cues = _parse_srt(source), _parse_srt(master)
    mapping = build_index_map(src_cues, mst_cues)
    by_index = {c["index"]: c for c in mst_cues}

    with pytest.raises(MigrationError, match="被剪掉"):
        _resolve({"id": "L01", "cue_start": 1, "cue_end": 2}, mapping, by_index)


def test_resolved_times_come_from_the_master_cues(tmp_path):
    """時間必須取自 master 的 cue，不是把舊時間平移——平移會累積誤差。"""
    source = _srt(
        tmp_path / "src.srt",
        [
            (1, 100.0, 101.0, "起點"),
            (2, 101.0, 102.0, "中間"),
            (3, 102.0, 103.5, "終點"),
        ],
    )
    master = _srt(
        tmp_path / "master.srt",
        [
            (1, 10.0, 11.0, "起點"),
            (2, 11.0, 12.0, "中間"),
            (3, 12.0, 13.5, "終點"),
        ],
    )
    src_cues, mst_cues = _parse_srt(source), _parse_srt(master)
    mapping = build_index_map(src_cues, mst_cues)
    by_index = {c["index"]: c for c in mst_cues}

    cue_start, cue_end, t0, t1 = _resolve(
        {"id": "L01", "cue_start": 1, "cue_end": 3}, mapping, by_index
    )
    assert (cue_start, cue_end) == (1, 3)
    assert (t0, t1) == (10.0, 13.5)
