"""AI 腔禁用清單：標題與描述共用（修修 2026-11-13：「那筆帳」「算一筆帳」嚴禁）。"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

from shared.ai_slop import slop_hits

_LINT = (
    Path(__file__).resolve().parents[2] / ".claude/skills/title-brainstorm/scripts/lint_titles.py"
)


@pytest.mark.parametrize(
    "text",
    [
        "不敢離職的人：她離職前算的那筆帳 ft. 李惠貞",  # 20261111 李惠貞 R3-09
        "離職前先算一筆帳",
        "矽谷公司正在算帳",  # 20261021 Kelly Tsai rank 1 的同一族
        "這筆帳怎麼算",
        "這集不是在講 AI，而是在講人",
        "帶你看懂凱道閱讀",
    ],
)
def test_slop_is_caught(text):
    assert slop_hits(text)


@pytest.mark.parametrize(
    "text",
    [
        "EP132｜1212 大家一起上凱道看書！（漫畫也可以喔）ft. 李惠貞",
        "老闆發現買 AI 比漲你薪水划算怎麼辦？白領中產的求生之道 ft. 程世嘉 Sega",
        "帳號被盜怎麼辦",  # 「帳」單獨出現不是那個句型
    ],
)
def test_clean_titles_pass(text):
    assert slop_hits(text) == []


def test_lint_titles_fails_on_slop():
    result = subprocess.run(
        [sys.executable, str(_LINT), "-"],
        input="她離職前算的那筆帳 ft. 李惠貞\n",
        capture_output=True,
        text=True,
        encoding="utf-8",
    )
    assert result.returncode == 1
    assert "AI 腔" in result.stdout
