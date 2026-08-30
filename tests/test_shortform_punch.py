"""run_shortform_director 的 punch 錨定與過長子句拆分（純函數，不碰 Resolve）。

2026-08-30 修修驗收 punch-S02 抓到兩個 zoom 病，根因是同一件事：punch 區間是
手寫的 timeline 秒數，沒有任何東西檢查它落在句子的哪裡。
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

from run_shortform_director import (  # noqa: E402
    CARD_LINE_CHARS,
    CARD_MAX_LINES,
    PUNCH_LEAD_SEC,
    _cue_index,
    _punch_curve,
    _resolve_punches,
    _slice_curve,
    _split_long_cues,
)

CFG = {"punch_ramp_sec": 0.25, "punch_scale": 1.25}

CUES = [
    {"n": 4, "t0": 8.130, "t1": 11.864, "text": "其實如果玩這件事情真的是玩物喪志"},
    {"n": 5, "t0": 11.864, "t1": 15.662, "text": "那在演化中喜歡玩的物種早就被淘汰了"},
    {"n": 6, "t0": 15.662, "t1": 17.425, "text": "可是其實人類很愛玩"},
]


def _ts(seconds: float) -> str:
    ms = int(round(seconds * 1000))
    h, ms = divmod(ms, 3_600_000)
    m, ms = divmod(ms, 60_000)
    s, ms = divmod(ms, 1000)
    return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"


def _write_srt(path: Path, rows: list[tuple[float, float, str]]) -> Path:
    path.write_text(
        "\n".join(f"{i}\n{_ts(a)} --> {_ts(b)}\n{t}\n" for i, (a, b, t) in enumerate(rows, 1)),
        encoding="utf-8",
    )
    return path


def test_card_budget_matches_the_titles_renderer():
    """版面預算只有一份真值；漂了就是字卡排不下卻沒人擋。"""
    from run_short_titles import FORMAT_TITLES

    assert CARD_LINE_CHARS == FORMAT_TITLES["short"]["max_line_caption"]
    assert CARD_MAX_LINES == 2


def test_ramp_starts_before_the_sentence():
    """修修：「要在他講那一句話前，可能 0.5 秒就要 zoom in。」"""
    (punch,) = _resolve_punches([{"cue": 5, "phrase": "那在演化中"}], CUES, CFG)
    assert punch["t0"] == pytest.approx(11.864 - PUNCH_LEAD_SEC)
    assert punch["t1"] == pytest.approx(15.662)  # 放掉的點＝該句句尾，不落在句中


def test_cut_lands_on_the_word_without_lead():
    """硬切是重音，要正好落在那個字上；提前 0.5s 就變成打錯地方。"""
    (punch,) = _resolve_punches([{"cue": 5, "phrase": "那在演化中", "style": "cut"}], CUES, CFG)
    assert punch["t0"] == pytest.approx(11.864)


def test_lead_is_overridable_per_punch():
    (punch,) = _resolve_punches([{"cue": 5, "phrase": "那在演化中", "lead_sec": 1.0}], CUES, CFG)
    assert punch["t0"] == pytest.approx(10.864)


def test_release_always_lands_on_a_cue_end():
    (punch,) = _resolve_punches([{"cue": 4, "phrase": "其實如果", "until_cue": 5}], CUES, CFG)
    assert punch["t1"] == pytest.approx(15.662)


def test_mid_sentence_anchor_is_refused():
    """字元比例內插實測會打錯句子——「早就被淘汰了」算出來落在「喜歡玩的物種」上。"""
    with pytest.raises(SystemExit, match="不是句首"):
        _resolve_punches([{"cue": 5, "phrase": "早就被淘汰了"}], CUES, CFG)


def test_phrase_must_be_verbatim():
    with pytest.raises(SystemExit, match="不在 cue"):
        _resolve_punches([{"cue": 5, "phrase": "早就被淘汏了"}], CUES, CFG)


def test_legacy_absolute_seconds_are_refused():
    with pytest.raises(SystemExit, match="舊的絕對秒數格式"):
        _resolve_punches([{"t0": 8.13, "t1": 12.53, "style": "ramp"}], CUES, CFG)


def test_release_and_reattack_inside_one_sentence_is_refused():
    """修修：「這一句為什麼要拉遠又拉近？很奇怪。」"""
    with pytest.raises(SystemExit, match="拉遠又拉近"):
        _resolve_punches(
            [
                {"cue": 4, "phrase": "其實如果", "until_cue": 5},
                {"cue": 5, "phrase": "早就被淘汰了"},
            ],
            CUES,
            CFG,
        )


def test_steps_escalate_without_releasing():
    (punch,) = _resolve_punches(
        [
            {
                "cue": 4,
                "phrase": "其實如果",
                "until_cue": 5,
                "style": "ramp",
                "steps": [{"cue": 5, "phrase": "那在演化中", "style": "cut", "scale": 1.45}],
            }
        ],
        CUES,
        CFG,
    )
    curve = _punch_curve(punch, CFG["punch_ramp_sec"], 30.0)
    sizes = [v for _t, v in curve]
    assert min(sizes) == pytest.approx(1.0)
    assert max(sizes) == pytest.approx(1.45)
    # 進場到放掉之間一路不回到 1.0——那個回落就是修修看到的「拉遠」
    inner = [v for t, v in curve if punch["t0"] + 0.8 < t < punch["t1"] - 0.3]
    assert inner and min(inner) >= 1.24


def test_steps_must_move_forward():
    with pytest.raises(SystemExit, match="沒有往前推"):
        _resolve_punches(
            [
                {
                    "cue": 4,
                    "phrase": "其實如果",
                    "until_cue": 5,
                    # 同一句、同樣提前 0.5s → 起跳點跟 punch 自己重疊
                    "steps": [{"cue": 4, "phrase": "其實如果", "style": "ramp", "scale": 1.45}],
                }
            ],
            CUES,
            CFG,
        )


def test_slice_curve_keeps_the_zoom_continuous_across_a_cut():
    curve = [(10.0, 1.0), (10.25, 1.25), (14.0, 1.25), (14.25, 1.0)]
    keys = _slice_curve(curve, 11.0, 13.0)  # 完全在 punch 內的 shot
    assert keys is not None
    assert keys[0] == (0.0, pytest.approx(1.25))  # 從前一刀延續進來就是 punch 級
    assert keys[-1][1] == pytest.approx(1.25)
    assert _slice_curve(curve, 20.0, 22.0) is None


def test_split_long_cues_rewrites_the_srt_verbatim(tmp_path):
    srt = _write_srt(
        tmp_path / "x_tight_r001.srt",
        [
            (29.971, 34.045, "去使用你危機的時候會使用到的肌肉或者是大腦"),
            (34.045, 34.378, "對不對"),
        ],
    )
    n_cues, notes = _split_long_cues(srt)
    rows = _cue_index(srt)
    assert n_cues == 3 and len(notes) == 1
    assert [r["text"] for r in rows] == [
        "去使用你危機的時候",
        "會使用到的肌肉或者是大腦",
        "對不對",
    ]
    assert rows[0]["t0"] == pytest.approx(29.971)
    assert rows[1]["t1"] == pytest.approx(34.045)
    assert rows[0]["t1"] == pytest.approx(rows[1]["t0"])


def test_split_long_cues_leaves_fitting_cues_alone(tmp_path):
    srt = _write_srt(
        tmp_path / "y_tight_r001.srt",
        [(0.0, 4.0, "其實玩是一個鼓勵你去在平常沒有危機的時候")],
    )
    n_cues, notes = _split_long_cues(srt)
    assert n_cues == 1 and notes == []
