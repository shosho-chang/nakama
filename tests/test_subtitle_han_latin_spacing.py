"""半形與漢字之間插空白——只插在該插的地方。

修修 2026-09-11：「所有半形的文字，像是英文字以及數字，都要跟全形的中文字有一個
空白。如果沒有的話，像 AI5分鐘 全部連在一起，就會讓觀眾看不懂。」

規則的邊界比聽起來窄，而且窄得有道理：**半形之間不插**。20260721 呂冠緯 的字幕裡
英數相鄰的只有「3C」與「Switch2」，而「3C」拆開就毀了。把規則寫成「英數之間也插」
會讓它自作聰明地改壞專有名詞。
"""

from __future__ import annotations

from pathlib import Path

import pytest

from shared.subtitle_finalize import space_han_latin, space_han_latin_srt_file


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        # 漢字 → 半形，兩個方向都要插
        ("用AI到極致", "用 AI 到極致"),
        ("AI很好", "AI 很好"),
        ("因為他自己是40歲的時候", "因為他自己是 40 歲的時候"),
        ("然後B就是Bad News", "然後 B 就是 Bad News"),
        ("as a friend認真關心這樣子", "as a friend 認真關心這樣子"),
        # 已經有空白：不會變成兩個
        ("用 AI 很好", "用 AI 很好"),
        # 純半形之間不動——3C 拆開就不是 3C 了
        ("用這些3C", "用這些 3C"),
        ("3C是相當的", "3C 是相當的"),
        ("就是我跟她一起玩Switch2這樣", "就是我跟她一起玩 Switch2 這樣"),
        # 半形標點不是觸發條件：引號緊貼字母是對的
        ('叫做"你拿幸運做什麼"', '叫做"你拿幸運做什麼"'),
        ("Let's see", "Let's see"),
        ("5.6 Soul", "5.6 Soul"),
        # 全形標點也不觸發——「AI」不該變成「 AI 」
        ("他說「AI」很好", "他說「AI」很好"),
        # 沒有漢字就沒事做
        ("OK", "OK"),
        ("", ""),
    ],
)
def test_spacing(text: str, expected: str) -> None:
    assert space_han_latin(text) == expected


def test_is_idempotent() -> None:
    """跑兩次跟跑一次一樣——顯示副本每次重建都會再跑一遍。"""
    once = space_han_latin("用AI到極致而且是40歲")
    assert space_han_latin(once) == once


def test_srt_file_reports_how_many_it_touched(tmp_path: Path) -> None:
    src = tmp_path / "in.srt"
    src.write_text(
        "1\n00:00:01,000 --> 00:00:02,000\n用AI到極致\n\n"
        "2\n00:00:02,000 --> 00:00:03,000\n沒有半形\n",
        encoding="utf-8",
    )
    dst = tmp_path / "out.srt"
    stats = space_han_latin_srt_file(src, dst)

    assert stats == {"cues": 2, "spaced": 1}
    body = dst.read_text(encoding="utf-8")
    assert "用 AI 到極致" in body
    assert "沒有半形" in body
    # 時間軸一個字元都不能動。
    assert "00:00:01,000 --> 00:00:02,000" in body
