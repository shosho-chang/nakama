"""mode B 字卡企劃工具——覆蓋率、斷行、預算三道自檢都要真的擋得住。"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

from author_shortform_titles import build  # noqa: E402

CUES = [
    "教育把玩排除在外",
    "可是其實人類很愛玩",
    "甚至連鳥都愛玩",
    "所以玩就是一種學習",
]


def _episode(tmp_path: Path, beats: list[dict], texts: list[str] = CUES) -> Path:
    srt = tmp_path / "highlights/srt"
    srt.mkdir(parents=True)
    blocks = []
    for i, text in enumerate(texts, 1):
        blocks.append(f"{i}\n00:00:{i - 1:02d},000 --> 00:00:{i:02d},000\n{text}\n")
    (srt / "punch-S99_tight_r001.srt").write_text("\n".join(blocks), encoding="utf-8")
    tighten = tmp_path / "highlights/tighten"
    tighten.mkdir(parents=True)
    (tighten / "punch-S99_titles.plan.json").write_text(
        json.dumps({"spine": "測試骨架", "beats": beats}, ensure_ascii=False),
        encoding="utf-8",
    )
    return tmp_path


def test_builds_one_state_per_cue(tmp_path):
    ep = _episode(
        tmp_path,
        [
            {"beat": "hook", "tier": 2, "transition": "enter", "cues": [1, 2]},
            {
                "beat": "closing",
                "tier": 1,
                "transition": "slam",
                "cues": [3, 4],
                "roles": {"3": "emphasis"},
            },
        ],
    )
    doc = build(ep, "punch-S99")
    states = [s for t in doc["titles"] for s in t["states"]]
    assert [s["trigger_cue"] for s in states] == [1, 2, 3, 4]
    assert doc["covers_full_transcript"] is True
    # beat 的第一張用宣告的進場，其餘 caption 用 cut
    assert [s["transition"] for s in states] == ["enter", "cut", "slam", "cut"]
    assert states[2]["role"] == "emphasis"
    assert doc["titles"][1]["pos_y"] == 0.62  # tier1 在上方


def test_rejects_incomplete_coverage(tmp_path):
    ep = _episode(
        tmp_path,
        [{"beat": "hook", "tier": 1, "transition": "enter", "cues": [1, 2, 3]}],
    )
    with pytest.raises(SystemExit, match="覆蓋不符"):
        build(ep, "punch-S99")


def test_rejects_cue_too_long_for_layout(tmp_path):
    """排不下是上游沒拆句，不是在這裡降級成三行（修修 2026-08-30 的三行卡）。"""
    ep = _episode(
        tmp_path,
        [{"beat": "hook", "tier": 1, "transition": "enter", "cues": [1, 2, 3, 4]}],
        texts=CUES[:3] + ["這一句故意寫得非常長長到兩行十個字絕對排不進去所以必須在導播那一步拆開"],
    )
    with pytest.raises(SystemExit, match="_split_long_cues"):
        build(ep, "punch-S99")


def test_rejects_emphasis_over_budget(tmp_path):
    ep = _episode(
        tmp_path,
        [
            {
                "beat": "hook",
                "tier": 1,
                "transition": "enter",
                "cues": [1, 2, 3, 4],
                "roles": {"1": "emphasis", "2": "emphasis"},
            }
        ],
    )
    with pytest.raises(SystemExit, match="強調預算超標"):
        build(ep, "punch-S99")


def test_requires_plan_spec(tmp_path):
    (tmp_path / "highlights/srt").mkdir(parents=True)
    (tmp_path / "highlights/srt/punch-S99_tight_r001.srt").write_text(
        "1\n00:00:00,000 --> 00:00:01,000\n測試\n", encoding="utf-8"
    )
    (tmp_path / "highlights/tighten").mkdir(parents=True)
    with pytest.raises(SystemExit, match="缺企劃規格"):
        build(tmp_path, "punch-S99")


def test_latin_word_gaps_survive_the_srt_line_join():
    """`Screen time` 不可以被黏成 `Screentime`。

    20260721 value-S03 的 cue 18 實際渲進字卡的就是 `Screentime`——舊的
    `.replace(" ", "")` 把所有空白一視同仁地砍掉。中文句子裡的空白確實是斷行
    殘留，但兩個拉丁字之間的空白是字的一部分。
    """
    from scripts.author_shortform_titles import join_cue_text

    assert join_cue_text(["Screen time"]) == "Screen time"
    assert join_cue_text(["Screen", "time"]) == "Screen time"
    # 「AI」與「5」兩邊都是拉丁／數字，那個空白要留——`AI5` 會讀成一個產品名。
    # 「5」與「分鐘」之間是拉丁對中文，照既有版面慣例拿掉。
    assert join_cue_text(["AI 5 分鐘", "就可以看完一本書"]) == "AI 5分鐘就可以看完一本書"
    assert join_cue_text(["我忘記 2025 還是 2026 的報告"]) == "我忘記2025還是2026的報告"
    assert join_cue_text(["就很清楚已經", "不去定義"]) == "就很清楚已經不去定義"
