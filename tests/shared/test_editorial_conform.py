"""conform map：成片修剪投影到原始素材的純函數測試（不需要 Resolve 或 G:）。"""

import json

import pytest

from shared.editorial_conform import (
    CONTRACT,
    ConformMapError,
    build_conform_map,
    load_conform_map,
    master_to_source_sec,
    project_master_range,
    removed_spans,
)

FPS = 30.0

# 仿 20260805 的形狀：片頭 C5487 → 主體 program feed 兩段（中間一刀）→ 片尾 C5487。
ITEMS = [
    {"tl_start": 0, "tl_end": 30, "src_left_offset": None, "source_path": None},  # 轉場
    {"tl_start": 0, "tl_end": 300, "src_left_offset": 60, "source_path": "C5487.MP4"},
    {"tl_start": 300, "tl_end": 900, "src_left_offset": 210, "source_path": "Default_1.mp4"},
    {"tl_start": 900, "tl_end": 1500, "src_left_offset": 1110, "source_path": "Default_1.mp4"},
    {"tl_start": 1500, "tl_end": 1800, "src_left_offset": 480, "source_path": "C5487.MP4"},
]

SOURCES = {
    "program": {"path": "Default_1.mp4", "offset_sec": 0.0},
    "cam1": {"path": "Video/1_CAMERA 1.mp4", "offset_sec": 0.0},
    "audio": {"path": "normalized.wav", "offset_sec": -0.05},
}


@pytest.fixture
def cmap():
    return build_conform_map(
        episode_id="20260805 林之晨",
        fps=FPS,
        lineage={"master_srt_sha256": "deadbeef"},
        timeline_items=ITEMS,
        sources=SOURCES,
        body_source_path="Default_1.mp4",
    )


def test_build_splits_body_from_intro_outro(cmap):
    assert cmap["contract"] == CONTRACT
    assert [s["master_start_sec"] for s in cmap["segments"]] == [10.0, 30.0]
    assert [s["source_start_sec"] for s in cmap["segments"]] == [7.0, 37.0]
    # 片頭片尾走 unconformable——三機沒有那段畫面
    assert [h["master_start_sec"] for h in cmap["unconformable"]] == [0.0, 50.0]


def test_single_point_projection_uses_the_owning_segment(cmap):
    # 主體第一段：成片 10s = 來源 7s
    assert master_to_source_sec(cmap, 10.0, source_key="cam1") == 7.0
    assert master_to_source_sec(cmap, 20.0, source_key="cam1") == 17.0
    # 過了那一刀之後換算基準改變（第二段從來源 37s 起）
    assert master_to_source_sec(cmap, 30.0, source_key="cam1") == 37.0
    assert master_to_source_sec(cmap, 40.0, source_key="cam1") == 47.0


def test_audio_offset_is_applied(cmap):
    assert master_to_source_sec(cmap, 10.0, source_key="audio") == 7.05


def test_range_spanning_a_cut_returns_two_pieces(cmap):
    pieces = project_master_range(cmap, 25.0, 35.0, source_key="cam1")
    assert len(pieces) == 2
    assert pieces[0] == {
        "master_start_sec": 25.0,
        "master_end_sec": 30.0,
        "source_path": "Video/1_CAMERA 1.mp4",
        "source_start_sec": 22.0,
        "source_end_sec": 27.0,
    }
    assert pieces[1]["source_start_sec"] == 37.0
    assert pieces[1]["source_end_sec"] == 42.0
    # 接起來的總長等於成片上要的長度
    total = sum(p["source_end_sec"] - p["source_start_sec"] for p in pieces)
    assert total == pytest.approx(10.0)


def test_intro_outro_fails_closed(cmap):
    """落在片頭就要明確拒絕——靜默給錯畫面比報錯糟得多。"""
    with pytest.raises(ConformMapError, match="片頭片尾"):
        project_master_range(cmap, 5.0, 12.0, source_key="cam1")


def test_unknown_source_fails_closed(cmap):
    with pytest.raises(ConformMapError, match="cam9"):
        project_master_range(cmap, 12.0, 15.0, source_key="cam9")


def test_removed_spans_are_the_safety_evidence(cmap):
    """被剪掉的段落 = 兩段之間的來源空隙；conform 之後任何素材都拿不到它。"""
    assert removed_spans(cmap) == [
        {"source_start_sec": 27.0, "source_end_sec": 37.0, "duration_sec": 10.0}
    ]


def test_build_rejects_overlapping_body_segments():
    bad = [
        {"tl_start": 0, "tl_end": 600, "src_left_offset": 0, "source_path": "Default_1.mp4"},
        {"tl_start": 300, "tl_end": 900, "src_left_offset": 900, "source_path": "Default_1.mp4"},
    ]
    with pytest.raises(ConformMapError, match="重疊"):
        build_conform_map(
            episode_id="x",
            fps=FPS,
            lineage={},
            timeline_items=bad,
            sources=SOURCES,
            body_source_path="Default_1.mp4",
        )


def test_build_rejects_body_item_without_source_offset():
    bad = [{"tl_start": 0, "tl_end": 600, "src_left_offset": None, "source_path": "Default_1.mp4"}]
    with pytest.raises(ConformMapError, match="src_left_offset"):
        build_conform_map(
            episode_id="x",
            fps=FPS,
            lineage={},
            timeline_items=bad,
            sources=SOURCES,
            body_source_path="Default_1.mp4",
        )


def test_load_rejects_wrong_contract(tmp_path):
    path = tmp_path / "conform-map.v1.json"
    path.write_text(json.dumps({"contract": "something-else"}), encoding="utf-8")
    with pytest.raises(ConformMapError, match="contract"):
        load_conform_map(path)


def test_load_roundtrip(tmp_path, cmap):
    path = tmp_path / "conform-map.v1.json"
    path.write_text(json.dumps(cmap, ensure_ascii=False), encoding="utf-8")
    assert load_conform_map(path)["segments"] == cmap["segments"]
