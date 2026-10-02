"""resolve_subtitle_rebuild 的純邏輯——不連 DaVinci Resolve。

幀對應的期望值取自 2026-10-02 20260722 李海碩 實際上軌的 69 句 intro／outro
（`intro-outro.cues.json`），那次重建在 Resolve 上逐句驗證 4225/4225 全符。
"""

from __future__ import annotations

import json
from fractions import Fraction
from pathlib import Path

import pytest

from scripts import resolve_subtitle_rebuild as rsr
from scripts.resolve_subtitle_rebuild import Cue, Fix, Piece, RebuildError

NTSC_30 = Fraction(30000, 1001)

# C5497.MP4（29.97）在 30fps timeline 上的四段：(tl_start, tl_end, GetSourceStartFrame())
PIECES = {
    "P1": Piece(3, 705, 292, NTSC_30),
    "P2": Piece(705, 2690, 1149, NTSC_30),
    "P3": Piece(2690, 4248, 3373, NTSC_30),
    "OUT": Piece(199726, 200575, 4999, NTSC_30),
}

# (piece, src_start_sec, src_end_sec, tl_start_frame, tl_end_frame) — 2026-10-02 實際上軌值
LI_HAI_SHUO_2026_10_02 = [
    ("P1", 11.44, 14.96, 54, 159),
    ("P1", 14.96, 15.92, 159, 188),
    ("P1", 15.92, 18.94, 188, 279),
    ("P1", 18.94, 20.74, 279, 333),
    ("P1", 20.74, 22.84, 333, 396),
    ("P1", 22.84, 26.04, 396, 491),
    ("P1", 26.04, 28.42, 491, 563),
    ("P1", 28.42, 30.88, 563, 636),
    ("P1", 30.88, 32.68, 636, 690),
    ("P2", 37.8, 40.3, 705, 764),
    ("P2", 40.3, 43.84, 764, 870),
    ("P2", 43.84, 46.44, 870, 948),
    ("P2", 46.44, 50.02, 948, 1055),
    ("P2", 50.02, 53.52, 1055, 1160),
    ("P2", 53.52, 57.32, 1160, 1274),
    ("P2", 57.32, 60.1, 1274, 1357),
    ("P2", 60.1, 61.96, 1357, 1413),
    ("P2", 61.96, 64.24, 1413, 1481),
    ("P2", 64.24, 65.6, 1481, 1522),
    ("P2", 65.6, 68.08, 1522, 1596),
    ("P2", 68.08, 70.3, 1596, 1663),
    ("P2", 70.3, 72.44, 1663, 1727),
    ("P2", 72.44, 76.84, 1727, 1859),
    ("P2", 76.84, 79.52, 1859, 1939),
    ("P2", 79.52, 80.92, 1939, 1981),
    ("P2", 80.92, 83.04, 1981, 2045),
    ("P2", 83.04, 85.32, 2045, 2113),
    ("P2", 85.32, 87.12, 2113, 2167),
    ("P2", 87.12, 89.56, 2167, 2240),
    ("P2", 89.56, 90.66, 2240, 2273),
    ("P2", 90.66, 93.82, 2273, 2368),
    ("P2", 93.82, 95.64, 2368, 2422),
    ("P2", 95.64, 97.78, 2422, 2486),
    ("P2", 97.78, 99.74, 2486, 2545),
    ("P2", 99.74, 101.84, 2545, 2608),
    ("P2", 101.84, 104.24, 2608, 2680),
    ("P3", 112.2, 115.08, 2690, 2766),
    ("P3", 115.08, 117.94, 2766, 2852),
    ("P3", 117.94, 121.1, 2852, 2946),
    ("P3", 121.1, 122.68, 2946, 2994),
    ("P3", 122.68, 125.66, 2994, 3083),
    ("P3", 125.66, 128.64, 3083, 3172),
    ("P3", 128.64, 131.42, 3172, 3256),
    ("P3", 131.42, 134.08, 3256, 3335),
    ("P3", 134.08, 136.82, 3335, 3417),
    ("P3", 136.82, 140.38, 3417, 3524),
    ("P3", 140.38, 143.54, 3524, 3619),
    ("P3", 143.54, 145.4, 3619, 3675),
    ("P3", 145.4, 147.82, 3675, 3747),
    ("P3", 148.16, 150.74, 3757, 3835),
    ("P3", 150.74, 153.14, 3835, 3907),
    ("P3", 153.14, 155.32, 3907, 3972),
    ("P3", 155.32, 157.32, 3972, 4032),
    ("P3", 157.32, 158.94, 4032, 4080),
    ("P3", 158.94, 160.54, 4080, 4128),
    ("P3", 160.54, 162.2, 4128, 4178),
    ("P3", 162.2, 163.34, 4178, 4212),
    ("OUT", 166.68, 168.18, 199726, 199767),
    ("OUT", 168.18, 170.38, 199767, 199833),
    ("OUT", 170.38, 171.94, 199833, 199880),
    ("OUT", 171.94, 173.7, 199880, 199933),
    ("OUT", 173.7, 175.7, 199933, 199993),
    ("OUT", 175.7, 178.0, 199993, 200062),
    ("OUT", 178.0, 180.98, 200062, 200151),
    ("OUT", 180.98, 184.98, 200151, 200271),
    ("OUT", 185.36, 187.0, 200282, 200331),
    ("OUT", 187.0, 189.28, 200331, 200400),
    ("OUT", 189.28, 191.28, 200400, 200460),
    ("OUT", 191.78, 193.28, 200475, 200520),
]


# ------------------------------------------------------------------ frame rates


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("29.97", NTSC_30),
        ("30000/1001", NTSC_30),
        ("23.976", Fraction(24000, 1001)),
        ("59.94", Fraction(60000, 1001)),
        ("30", Fraction(30)),
        ("30.0", Fraction(30)),
        (30, Fraction(30)),
    ],
)
def test_parse_fps_maps_resolve_decimal_rates_to_exact_ntsc(raw, expected) -> None:
    assert rsr.parse_fps(raw) == expected


@pytest.mark.parametrize("raw", ["", "abc", "0", "-30", "30/0"])
def test_parse_fps_rejects_garbage(raw) -> None:
    with pytest.raises(RebuildError):
        rsr.parse_fps(raw)


def test_piece_json_round_trip_keeps_exact_source_rate() -> None:
    raw = PIECES["P1"].to_json()
    assert raw == {"tl_start": 3, "tl_end": 705, "src_start_frame": 292, "src_fps": "30000/1001"}
    assert Piece.from_json(raw, label="P1") == PIECES["P1"]


# --------------------------------------------------------------------- mapping


@pytest.mark.parametrize(("piece", "s0", "s1", "f0", "f1"), LI_HAI_SHUO_2026_10_02)
def test_mapping_reproduces_li_hai_shuo_frames_29_97_source_on_30_timeline(
    piece, s0, s1, f0, f1
) -> None:
    assert rsr.src_sec_to_tl_frame(s0, PIECES[piece]) == f0
    assert rsr.src_sec_to_tl_frame(s1, PIECES[piece]) == f1


def test_mapping_must_use_source_rate_not_timeline_rate() -> None:
    """191.78s 用 29.97 → 200475（實際上軌值）；誤用 timeline 的 30 會晚 5 幀。"""
    wrong = Piece(199726, 200575, 4999, Fraction(30))
    assert rsr.src_sec_to_tl_frame(191.78, PIECES["OUT"]) == 200475
    assert rsr.src_sec_to_tl_frame(191.78, wrong) == 200480


def test_mapping_clamps_to_the_edited_piece() -> None:
    # 37.80s 在 P2 的剪輯入點之前（第一個 take 被剪掉）→ 夾到 tl_start
    assert rsr.src_sec_to_tl_frame(37.8, PIECES["P2"]) == 705
    # 遠超過段尾 → 夾到 tl_end
    assert rsr.src_sec_to_tl_frame(500, PIECES["P2"]) == 2690


def test_mapping_handles_whole_and_fraction_seconds() -> None:
    piece = Piece(0, 10_000, 0, NTSC_30)
    assert rsr.src_sec_to_tl_frame(0, piece) == 0
    assert rsr.src_sec_to_tl_frame(Fraction(1001, 1000), piece) == 30  # 1.001s == 30 NTSC frames


def _adjudicated(rows):
    return [
        {"piece": p, "src_start_sec": s0, "src_end_sec": s1, "text": f"cue {i}", "note": ""}
        for i, (p, s0, s1, *_rest) in enumerate(rows, 1)
    ]


def test_map_cues_adds_timeline_frames_and_keeps_notes() -> None:
    rows = _adjudicated(LI_HAI_SHUO_2026_10_02)
    rows[2]["note"] = "Memo 漏字；講稿 一位"
    mapped = rsr.map_cues(PIECES, rows)
    assert [(m["tl_start_frame"], m["tl_end_frame"]) for m in mapped] == [
        (f0, f1) for *_rest, f0, f1 in LI_HAI_SHUO_2026_10_02
    ]
    assert mapped[2]["note"] == "Memo 漏字；講稿 一位"


def test_map_cues_rejects_cue_entirely_outside_its_piece() -> None:
    rows = _adjudicated([("P2", 30.0, 35.0)])  # 整句落在被剪掉的 take
    with pytest.raises(RebuildError, match="outside the edited piece"):
        rsr.map_cues(PIECES, rows)


def test_map_cues_rejects_unknown_piece_and_overlap() -> None:
    with pytest.raises(RebuildError, match="unknown piece"):
        rsr.map_cues(PIECES, _adjudicated([("P9", 1.0, 2.0)]))
    with pytest.raises(RebuildError, match="overlap"):
        rsr.map_cues(PIECES, _adjudicated([("P1", 11.44, 15.0), ("P1", 14.96, 15.92)]))


def test_load_pieces_reads_named_pieces() -> None:
    data = {"pieces": {name: piece.to_json() for name, piece in PIECES.items()}}
    assert rsr.load_pieces(data) == PIECES
    with pytest.raises(RebuildError):
        rsr.load_pieces({"pieces": {}})


# ------------------------------------------------------------------ timestamps


def test_timestamp_round_trip_every_frame_0_to_300_at_30fps() -> None:
    for frame in range(0, 301):
        assert rsr.parse_srt_timestamp(rsr.format_srt_timestamp(frame)) == frame


def test_timestamp_round_trip_ntsc_and_long_timelines() -> None:
    for frame in [*range(0, 301), 107_892, 199_726, 200_575]:
        assert rsr.parse_srt_timestamp(rsr.format_srt_timestamp(frame, NTSC_30), NTSC_30) == frame
        assert rsr.parse_srt_timestamp(rsr.format_srt_timestamp(frame)) == frame


@pytest.mark.parametrize(
    ("frame", "text"),
    [
        (0, "00:00:00,000"),
        (54, "00:00:01,800"),  # 2026-10-02 st1-rebuild r001 第 1 句
        (159, "00:00:05,300"),
        (188, "00:00:06,267"),  # 6266.67ms 四捨五入
        (200_575, "01:51:25,833"),
    ],
)
def test_timestamp_format_matches_the_2026_10_02_srt(frame, text) -> None:
    assert rsr.format_srt_timestamp(frame) == text


def test_parse_timestamp_rejects_garbage() -> None:
    with pytest.raises(ValueError):
        rsr.parse_srt_timestamp("1:2:3")


def test_render_and_parse_srt_round_trip() -> None:
    cues = [Cue(54, 159, "大家好歡迎收聽今天的不正常人類研究所"), Cue(4251, 4288, "海碩哥你還記得")]
    text = rsr.render_srt(cues)
    assert text.startswith("1\n00:00:01,800 --> 00:00:05,300\n大家好")
    assert text.endswith("海碩哥你還記得\n")
    assert rsr.parse_srt(text) == cues
    assert rsr.parse_srt("﻿" + text.replace("\n", "\r\n")) == cues


# ---------------------------------------------------------------------- merging

BODY = [Cue(4251, 4288, "海碩哥你還記得"), Cue(4288, 4334, "我們第一次見面的時候嗎")]
INTRO = [Cue(54, 159, "大家好歡迎收聽今天的不正常人類研究所"), Cue(4178, 4212, "希望對你有幫助")]


def test_merge_sorts_intro_before_body_and_allows_touching_cues() -> None:
    merged = rsr.merge_cues(BODY, INTRO + [Cue(4212, 4251, "接縫")])
    assert merged == sorted(BODY + INTRO + [Cue(4212, 4251, "接縫")])
    assert merged[0].text.startswith("大家好")


def test_merge_rejects_intro_overlapping_body() -> None:
    with pytest.raises(RebuildError, match="overlap"):
        rsr.merge_cues(BODY, [Cue(4200, 4260, "拖進正片第一句")])


def test_merge_rejects_re_adding_cues_already_on_st1() -> None:
    """第一次重建後 live ST1 已含 intro；再 --add-cues 同一份必須被擋，不能疊兩層。"""
    live_after_first_rebuild = sorted(BODY + INTRO)
    with pytest.raises(RebuildError, match="overlap"):
        rsr.merge_cues(live_after_first_rebuild, INTRO)


@pytest.mark.parametrize(
    "bad",
    [Cue(10, 10, "零長度"), Cue(20, 10, "倒退"), Cue(10, 20, ""), Cue(10, 20, "a\r\nb")],
)
def test_merge_rejects_invalid_cues(bad) -> None:
    with pytest.raises(RebuildError):
        rsr.merge_cues([], [bad])


def test_merge_rejects_cue_past_timeline_end() -> None:
    with pytest.raises(RebuildError, match="timeline end"):
        rsr.merge_cues(BODY, [Cue(200_600, 200_700, "超出")], limit=200_654)


# ------------------------------------------------------------------------ fixes


def test_parse_fix_splits_frame_old_new() -> None:
    fix = rsr.parse_fix("185351=叫 Ethos, Pathos, Logos=>叫 Ethos Pathos Logos")
    assert fix == Fix(185351, "叫 Ethos, Pathos, Logos", "叫 Ethos Pathos Logos")


@pytest.mark.parametrize("spec", ["185351", "abc=old=>new", "185351=old->new", "185351=old=>  "])
def test_parse_fix_rejects_malformed(spec) -> None:
    with pytest.raises(RebuildError):
        rsr.parse_fix(spec)


def test_apply_fix_replaces_only_the_matching_cue() -> None:
    live = [Cue(185300, 185351, "前一句"), Cue(185351, 185400, "叫 Ethos, Pathos, Logos")]
    fixed = rsr.apply_fixes(live, [Fix(185351, "叫 Ethos, Pathos, Logos", "叫 Ethos Pathos Logos")])
    assert fixed == [live[0], Cue(185351, 185400, "叫 Ethos Pathos Logos")]


def test_apply_fix_refuses_when_live_text_differs_from_old() -> None:
    """修修已在 Resolve 上改過那句 → live 才是權威，修正單不可蓋過去。"""
    live = [Cue(185351, 185400, "叫 Ethos Pathos Logos")]
    with pytest.raises(RebuildError, match="live text is '叫 Ethos Pathos Logos'"):
        rsr.apply_fixes(live, [Fix(185351, "叫 Ethos, Pathos, Logos", "叫 ethos")])


def test_apply_fix_refuses_missing_frame_and_duplicate_fix() -> None:
    live = [Cue(100, 200, "a")]
    with pytest.raises(RebuildError, match="0 cues start there"):
        rsr.apply_fixes(live, [Fix(101, "a", "b")])
    with pytest.raises(RebuildError, match="more than one"):
        rsr.apply_fixes(live, [Fix(100, "a", "b"), Fix(100, "a", "c")])


def test_build_plan_requires_a_real_change() -> None:
    with pytest.raises(RebuildError, match="nothing to do"):
        rsr.build_plan(BODY, [], [])
    with pytest.raises(RebuildError, match="identical"):
        rsr.build_plan(BODY, [], [Fix(4251, "海碩哥你還記得", "海碩哥你還記得")])
    assert rsr.build_plan(BODY, INTRO, [], limit=200_654) == sorted(BODY + INTRO)


# ------------------------------------------------------------------------- diff


def test_diff_identical_is_ok() -> None:
    diff = rsr.diff_cues(BODY, list(BODY))
    assert diff.ok
    assert diff.describe() == "identical: 2 cues"


def test_diff_reports_text_change_as_missing_and_extra() -> None:
    actual = [BODY[0], BODY[1]._replace(text="我們第一次見面的時候")]
    diff = rsr.diff_cues(BODY, actual)
    assert not diff.ok
    assert diff.first_mismatch == 1
    assert diff.missing == (BODY[1],)
    assert diff.extra == (actual[1],)
    assert "first difference at position 1" in diff.describe()


def test_diff_reports_nothing_placed() -> None:
    """ST1 鎖住時 append 回 True 卻什麼都沒放——必須被逐句驗證抓到。"""
    diff = rsr.diff_cues(BODY, [])
    assert not diff.ok
    assert (diff.expected_count, diff.actual_count, diff.first_mismatch) == (2, 0, 0)
    assert diff.missing == tuple(BODY)


def test_diff_reports_frame_shift() -> None:
    shifted = [cue._replace(start=cue.start + 1, end=cue.end + 1) for cue in BODY]
    diff = rsr.diff_cues(BODY, shifted)
    assert not diff.ok and diff.first_mismatch == 0
    assert len(diff.missing) == len(diff.extra) == 2


# --------------------------------------------------------------------- file I/O


def test_load_add_cues_reads_the_2026_10_02_cues_json_shape(tmp_path: Path) -> None:
    path = tmp_path / "intro-outro.cues.json"
    path.write_text(
        json.dumps(
            {
                "episode_id": "20260722 李海碩",
                "pieces": {"P1": [3, 705, 292]},  # 舊格式的 pieces 不影響讀 cues
                "cues": [
                    {
                        "piece": "P1",
                        "src_start_sec": 11.44,
                        "src_end_sec": 14.96,
                        "tl_start_frame": 54,
                        "tl_end_frame": 159,
                        "text": " 大家好歡迎收聽今天的不正常人類研究所 ",
                        "note": "",
                    }
                ],
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    assert rsr.load_add_cues(path) == [Cue(54, 159, "大家好歡迎收聽今天的不正常人類研究所")]


def test_load_add_cues_rejects_non_integer_frames(tmp_path: Path) -> None:
    path = tmp_path / "cues.json"
    path.write_text(
        json.dumps({"cues": [{"tl_start_frame": 54.5, "tl_end_frame": 60, "text": "x"}]})
    )
    with pytest.raises(RebuildError):
        rsr.load_add_cues(path)


def test_artifact_paths_take_the_next_unused_revision(tmp_path: Path) -> None:
    srt, snapshot = rsr.artifact_paths(tmp_path, "20260722 李海碩")
    assert srt.name == "st1-rebuild.20260722_李海碩.r001.srt"
    assert snapshot.name == "st1-rebuild.20260722_李海碩.r001.pre-snapshot.json"
    snapshot.write_text("{}", encoding="utf-8")
    assert rsr.artifact_paths(tmp_path, "20260722 李海碩")[0].name.endswith(".r002.srt")
    assert rsr.artifact_paths(tmp_path, 'a:b/c "d"')[0].name == "st1-rebuild.a_b_c_d_.r001.srt"


def test_map_cues_cli_writes_rebuild_input(tmp_path: Path) -> None:
    source = tmp_path / "adjudicated.json"
    source.write_text(
        json.dumps(
            {
                "episode_id": "20260722 李海碩",
                "pieces": {"P1": PIECES["P1"].to_json()},
                "cues": _adjudicated(LI_HAI_SHUO_2026_10_02[:2]),
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    out = tmp_path / "cues.json"
    assert rsr.main(["map-cues", "--input", str(source), "--out", str(out)]) == rsr.EXIT_OK
    assert rsr.load_add_cues(out) == [Cue(54, 159, "cue 1"), Cue(159, 188, "cue 2")]
    assert json.loads(out.read_text(encoding="utf-8"))["episode_id"] == "20260722 李海碩"


def test_map_cues_cli_exits_2_on_bad_input(tmp_path: Path) -> None:
    source = tmp_path / "adjudicated.json"
    source.write_text(json.dumps({"pieces": {}, "cues": []}), encoding="utf-8")
    out = tmp_path / "cues.json"
    assert rsr.main(["map-cues", "--input", str(source), "--out", str(out)]) == 2
    assert not out.exists()
