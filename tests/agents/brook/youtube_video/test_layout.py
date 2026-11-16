"""鋪時間軸：插入把後面往後推、V1 在插入點切開、疊加素材分軌、字幕跟著推、畫面搬移時間。

不開 Resolve。
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from agents.brook.youtube_video import layout as ly
from agents.brook.youtube_video import timeline as tl

FPS = 30000 / 1001


def _cut(tmp_path: Path, segments) -> tl.RoughCut:
    data = {
        "schema": tl.ROUGH_CUT_SCHEMA,
        "fps": "30000/1001",
        "media": "x.mp4",
        "segments": segments,
    }
    (tmp_path / tl.ROUGH_CUT_FILE).write_text(json.dumps(data), encoding="utf-8")
    return tl.load_rough_cut(tmp_path)


def _insert(at, push=1.7, length=2.0):
    return ly.Insert("ch", at, push, length, Path("ch.mov"))


def test_shift_pushes_everything_from_the_insert_point():
    ins = [_insert(10.0), _insert(20.0)]
    assert ly.shift(5.0, ins) == 5.0
    assert ly.shift(10.0, ins) == pytest.approx(11.7)  # 插入點那一句被推
    assert ly.shift(10.0, ins, include_at=False) == 10.0  # 章節卡本身放在推之前
    assert ly.shift(25.0, ins) == pytest.approx(28.4)


def test_v1_is_split_at_the_insert_point(tmp_path):
    cut = _cut(tmp_path, [{"src_in": 100, "src_out": 400}, {"src_in": 1000, "src_out": 1300}])
    at = 200 / FPS  # v1 第 200 格（第一段中間）
    pieces = ly.v1_pieces(cut, [_insert(at)])
    push = ly.frames(1.7)
    assert pieces == [(100, 300, 0), (300, 400, 200 + push), (1000, 1300, 300 + push)]


def test_insert_exactly_at_a_cut_does_not_make_an_empty_piece(tmp_path):
    cut = _cut(tmp_path, [{"src_in": 0, "src_out": 300}, {"src_in": 500, "src_out": 800}])
    pieces = ly.v1_pieces(cut, [_insert(300 / FPS)])
    assert pieces == [(0, 300, 0), (500, 800, 300 + ly.frames(1.7))]


def test_placements_stack_overlaps_onto_higher_tracks():
    layout = ly.Layout(
        inserts=[_insert(10.0)],
        overlays=[
            ly.Overlay("a", "concept_title", 2.0, 3.0, Path("a.mov")),
            ly.Overlay("b", "book_cover", 3.0, 3.0, Path("b.mov")),  # 跟 a 重疊 → V3
            ly.Overlay("c", "quote_card", 12.0, 5.0, Path("c.mov")),  # 插入之後 → 推 1.7 秒
        ],
    )
    placed = {p.row_id: p for p in ly.placements(layout)}
    assert placed["a"].track == 2 and placed["b"].track == 3
    assert placed["ch"].track == 2 and placed["ch"].record == ly.frames(10.0)
    assert placed["c"].record == ly.frames(13.7) and placed["c"].track == 2


def test_subtitles_follow_the_inserts():
    srt = (
        "1\n00:00:09,000 --> 00:00:10,000\n前一句\n\n2\n00:00:10,000 --> 00:00:12,000\n插入點這句\n"
    )
    out = ly.shifted_srt(srt, [_insert(10.0)])
    assert "00:00:09,000 --> 00:00:10,000" in out
    assert "00:00:11,700 --> 00:00:13,700" in out


def test_moves_for_side_overlays_are_shifted():
    layout = ly.Layout(
        inserts=[_insert(5.0)],
        overlays=[
            ly.Overlay("bk", "book_cover", 8.0, 3.0, Path("bk.mov")),
            ly.Overlay("ct", "concept_title", 20.0, 2.0, Path("ct.mov")),
        ],
    )
    (move,) = ly.moves(layout)
    assert (
        move.kind == "left" and move.start == pytest.approx(9.7) and move.end == pytest.approx(12.7)
    )


def test_load_layout_skips_rows_no_longer_in_the_plan(tmp_path):
    visuals = tmp_path / "visuals"
    visuals.mkdir()
    for row_id, kind in (("keep", "overlay"), ("gone", "overlay"), ("ch", "insert")):
        (visuals / f"{row_id}.mov").write_bytes(b"x")
        receipt = {
            "schema": "nakama.youtube_visual_asset.v1", "row_id": row_id,
            "category": "concept_title",
            "kind": kind, "media": f"{row_id}.mov", "start_sec": 1.0, "duration_sec": 2.0,
            "insert_sec": 1.7,
        }  # fmt: skip
        (visuals / f"{row_id}.json").write_text(json.dumps(receipt), encoding="utf-8")
    (visuals / "dp_report.json").write_text("{}", encoding="utf-8")
    plan = {"rows": [{"id": "keep"}, {"id": "ch"}]}
    layout = ly.load_layout(tmp_path, plan)
    assert [o.row_id for o in layout.overlays] == ["keep"]
    assert [i.row_id for i in layout.inserts] == ["ch"] and layout.inserts[0].push == 1.7
    assert any("gone" in s for s in layout.skipped)
