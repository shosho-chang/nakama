"""Stock footage（第 6 類）：規格檔檢查、收據、鋪時間軸時一鏡一鏡放原檔。

不開 Resolve、不跑 ffprobe。
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from agents.brook.youtube_video import dp
from agents.brook.youtube_video import layout as ly
from agents.brook.youtube_video.categories import load_categories
from agents.brook.youtube_video.visuals import stock as st

FPS = 30000 / 1001
PROBES = {
    "a.mov": st.Probe(1920, 1080, 25.0, 10.0),
    "b.mov": st.Probe(3840, 2160, FPS, 8.0),
    "c.mov": st.Probe(1920, 1080, FPS, 8.0),
    "dci.mov": st.Probe(4096, 2160, 25.0, 10.0),
    "small.mov": st.Probe(1280, 720, 25.0, 10.0),
}


def probe(path: Path) -> st.Probe:
    return PROBES[path.name]


def _row(row_id: str, start_cue: int, end_cue: int, layout: str = "full") -> dict:
    return {
        "id": row_id,
        "origin": "director",
        "kind": "overlay",
        "category": "stock",
        "start_cue": start_cue,
        "end_cue": end_cue,
        "before_cue": None,
        "insert_sec": None,
        "layout": layout,
        "description": "倒咖啡",
        "dp_instruction": "咖啡倒進杯子",
        "onscreen_text": "",
        "source": "",
        "status": "approved",
        "note": "",
        "program_state": None,
    }


@pytest.fixture
def ep(tmp_path: Path) -> Path:
    folder = tmp_path / "assets" / "stock"
    folder.mkdir(parents=True)
    for name in PROBES:
        (folder / name).write_bytes(b"x" * 10)
    return tmp_path


def _plan(*rows: dict) -> dict:
    cues = [
        {"id": i, "start": 100.0 + i * 2.0, "end": 102.0 + i * 2.0, "text": f"第 {i} 句"}
        for i in range(1, 11)
    ]
    return {"schema": "nakama.youtube_visual_plan.v1", "cues": cues, "rows": list(rows)}


def _spec(ep: Path, row_id: str, shots: list) -> None:
    folder = ep / st.SOURCES_DIR
    folder.mkdir(exist_ok=True)
    (folder / f"{row_id}.json").write_text(json.dumps({"shots": shots}), encoding="utf-8")


def test_stock_receipt_lists_raw_shots_and_layout_places_them(ep):
    row = _row("s1", 2, 3)  # 104–108 秒，4 秒
    plan = _plan(row)
    _spec(ep, "s1", [
        {"file": "assets/stock/a.mov", "in": 1.0, "dur": 2.0, "source": "u1", "note": "倒"},
        {"file": "assets\\stock\\b.mov", "in": 0.5},
    ])  # fmt: skip
    receipt, wrote = st.deliver_row(ep, plan, row, probe=probe)
    assert wrote
    assert receipt["media"] is None and receipt["audio"] is False
    assert receipt["start_sec"] == 104.0 and receipt["duration_sec"] == 4.0
    a, b = receipt["shots"]
    assert (a["file"], a["src_in"]) == ("assets/stock/a.mov", 1.0)
    assert (a["offset_frames"], a["frames"]) == (0, 60)
    assert (b["file"], b["offset_frames"], b["frames"]) == ("assets/stock/b.mov", 60, 60)
    assert a["source"] == "u1" and a["src_fps"] == 25.0

    # 輸入沒變 → 沿用；改了取用點 → 重寫
    assert st.deliver_row(ep, plan, row, probe=probe)[1] is False
    _spec(ep, "s1", [{"file": "assets/stock/a.mov", "in": 1.5, "dur": 2.0},
                     {"file": "assets/stock/b.mov", "in": 0.5}])  # fmt: skip
    assert st.deliver_row(ep, plan, row, probe=probe)[1] is True

    layout = ly.load_layout(ep, plan)
    assert len(layout.overlays) == 2 and not layout.skipped
    assert ly.moves(layout) == []  # 滿版：臉不搬
    first, second = ly.placements(layout)
    assert first.track == second.track == 2  # 一鏡接一鏡，同一軌
    assert first.record == ly.frames(104.0) and first.length == 60
    assert second.record == first.record + 60 and second.length == 60
    assert first.media == ep / "assets/stock/a.mov"
    assert (first.src_in, first.src_fps) == (round(1.5 * 25), 25.0)  # 原檔 25 fps 的格數
    assert (second.src_in, second.src_fps) == (round(0.5 * FPS), FPS)
    assert ly.audio_placements(layout) == []  # 只放畫面


def test_missing_raw_file_is_skipped_when_laying_out(ep):
    row = _row("s1", 2, 2)
    plan = _plan(row)
    _spec(ep, "s1", [{"file": "assets/stock/c.mov", "in": 0}])
    st.deliver_row(ep, plan, row, probe=probe)
    (ep / "assets/stock/c.mov").unlink()
    layout = ly.load_layout(ep, plan)
    assert layout.overlays == [] and "c.mov" in layout.skipped[0]


@pytest.mark.parametrize(
    "shots, message",
    [
        ([{"file": "a.mov", "in": 0}], "assets/stock/ 裡"),
        ([{"file": "E:/a.mov", "in": 0}], "assets/stock/ 裡"),
        ([{"file": "assets/stock/../a.mov", "in": 0}], "assets/stock/ 裡"),
        ([{"file": "assets/stock/none.mov", "in": 0}], "要先搬進這一集的資料夾"),
        ([{"file": "assets/stock/dci.mov", "in": 0}], "只收 16:9"),
        ([{"file": "assets/stock/small.mov", "in": 0}], "至少要 1080p"),
        (
            [{"file": "assets/stock/a.mov", "in": 0, "dur": 3.5}, {"file": "assets/stock/c.mov"}],
            "單鏡不超過 3 秒",
        ),
        ([{"file": "assets/stock/a.mov", "in": 9.0}], "超出 assets/stock/a.mov 的長度"),
        (
            [{"file": "assets/stock/a.mov", "in": 0}, {"file": "assets/stock/c.mov", "in": 0}],
            "只有最後一鏡可以不寫 dur",
        ),
        (
            [
                {"file": "assets/stock/a.mov", "in": 0, "dur": 1.0},
                {"file": "assets/stock/c.mov", "in": 0, "dur": 0.5},
            ],
            "鏡頭加起來",
        ),
        (
            [
                {"file": "assets/stock/a.mov", "in": 0, "dur": 1.0},
                {"file": "assets/stock/a.mov", "in": 3},
            ],
            "用了兩次",
        ),
        ([], "至少一鏡"),
    ],
)
def test_bad_specs_are_refused(ep, shots, message):
    row = _row("s1", 2, 2)  # 2 秒
    _spec(ep, "s1", shots)
    with pytest.raises(st.StockError, match=message.replace("(", r"\(")):
        st.check_row(ep, _plan(row), row, probe=probe)


def test_same_footage_cannot_be_used_in_two_rows(ep):
    first, second = _row("s1", 2, 2), _row("s2", 5, 5)
    _spec(ep, "s1", [{"file": "assets/stock/c.mov", "in": 0}])
    _spec(ep, "s2", [{"file": "assets/stock/c.mov", "in": 4}])
    with pytest.raises(st.StockError, match="已經用在 s1"):
        st.check_row(ep, _plan(first, second), second, probe=probe)


def test_stock_must_be_full_screen(ep):
    row = _row("s1", 2, 2, layout="overlay")
    _spec(ep, "s1", [{"file": "assets/stock/c.mov", "in": 0}])
    with pytest.raises(st.StockError, match="只做滿版"):
        st.check_row(ep, _plan(row), row, probe=probe)


def test_no_spec_says_what_to_write(ep):
    row = _row("s1", 2, 2)
    with pytest.raises(st.StockError, match="youtube-stock"):
        st.check_row(ep, _plan(row), row, probe=probe)


def test_stock_is_a_ready_full_screen_category_with_a_dp_component():
    category = load_categories()["stock"]
    assert category.layout == "full" and category.readiness == "ready"
    assert dp.RENDERERS["stock"].endswith("visuals.stock")


def test_a_three_second_shot_is_allowed(ep):
    row = _row("s1", 4, 5)  # 4 秒 → 3 秒＋1 秒
    _spec(ep, "s1", [{"file": "assets/stock/a.mov", "in": 0, "dur": 3.0},
                     {"file": "assets/stock/c.mov", "in": 0}])  # fmt: skip
    _, _, shots = st.check_row(ep, _plan(row), row, probe=probe)
    assert [s.frames for s in shots] == [90, 30]


def test_conformed_frames_match_resolve():
    # 2026-11-26 Resolve 實測（財富階梯 流程測試，Envato 原檔）
    assert ly.conformed_frames(50, 25.0) == 59
    assert ly.conformed_frames(43, 25.0) == 51
    assert ly.conformed_frames(28, 24.0) == 34


def test_source_frames_never_overshoot_and_miss_by_at_most_one():
    # 舊算法 24 fps 要蓋 36 格只取 28 格 → 34 格，早兩格；現在取 29 格剛好 36
    assert ly.source_frames(36, 24.0) == 29
    assert ly.source_frames(60, 25.0) == 50
    assert ly.source_frames(60, FPS) == 60  # 同格率剛好
    for n in range(1, 400):
        for fps in (23.976, 24.0, 25.0, FPS, 30.0, 50.0, 59.94, 60.0):
            covered = ly.conformed_frames(ly.source_frames(n, fps), fps)
            assert n - 1 <= covered <= n


def test_dp_reports_stock_as_delivered_not_rendered(ep):
    row = _row("s1", 2, 2)
    _spec(ep, "s1", [{"file": "assets/stock/c.mov", "in": 0}])

    def deliver_for(_category):
        return lambda *a, **k: st.deliver_row(*a[:3], probe=probe)

    plan = _plan(row)
    (first,) = dp.build(ep, plan, [row], deliver_for=deliver_for, log=lambda _: None)
    (again,) = dp.build(ep, plan, [row], deliver_for=deliver_for, log=lambda _: None)
    assert (first.result, again.result) == ("delivered", "reused")
