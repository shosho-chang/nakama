"""shared.zh_linebreak — 字卡斷行／斷句的語法判準。

這些案例全部來自 2026-08-30 修修驗收 punch-S02 時抓到的實際字卡
（「這切的也很奇怪，為什麼會這樣切？」）。
"""

from __future__ import annotations

import pytest

from shared.zh_linebreak import clean_breaks, split_clause, wrap_lines


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        # 「這件事情」不可剖半——最均衡的 7/9 正是最爛的那個切點
        ("當我們把玩這件事情排除在學習之外", ["當我們把玩這件事情", "排除在學習之外"]),
        ("其實如果玩這件事情真的是玩物喪志", ["其實如果玩這件事情", "真的是玩物喪志"]),
        # 動詞＋了 收尾是合法的（扼殺了｜玩這個東西的價值）
        ("其實是完全扼殺了玩這個東西的價值", ["其實是完全扼殺了", "玩這個東西的價值"]),
        # 助動詞「會」不留在行尾——主要動詞在下一行
        ("那為什麼演化會送給物種玩這個東西", ["那為什麼演化", "會送給物種玩這個東西"]),
        ("那自然小孩子就會覺得他很喜歡學習", ["那自然小孩子", "就會覺得他很喜歡學習"]),
        # 單獨的主語代名詞不孤懸在行尾
        ("所以我覺得接下來的時代", ["所以我覺得", "接下來的時代"]),
        ("應該要把玩跟學習重新結合回去", ["應該要把玩跟學習", "重新結合回去"]),
    ],
)
def test_wrap_lines_picks_the_grammatical_seam(text, expected):
    assert wrap_lines(text, 10, 2) == expected


def test_short_text_stays_on_one_line():
    assert wrap_lines("狗也愛玩", 10, 2) == ["狗也愛玩"]


def test_wrap_lines_gives_up_instead_of_overflowing():
    """排不下就回 None——降級成別的樣式是把版面問題推給樣式（hybrid 三行卡）。"""
    assert wrap_lines("去使用你危機的時候會使用到的肌肉或者是大腦", 10, 2) is None


def test_split_clause_cuts_the_over_long_cue_before_the_predicate():
    """21 字：切在「時候｜會」，不是切在「使用｜到」把動補結構砍斷。"""
    assert split_clause("去使用你危機的時候會使用到的肌肉或者是大腦", 10, 2) == [
        "去使用你危機的時候",
        "會使用到的肌肉或者是大腦",
    ]


def test_split_clause_pieces_all_fit():
    for piece in split_clause("去使用你危機的時候會使用到的肌肉或者是大腦", 10, 2):
        assert wrap_lines(piece, 10, 2) is not None


def test_split_clause_is_verbatim():
    text = "去使用你危機的時候會使用到的肌肉或者是大腦"
    assert "".join(split_clause(text, 10, 2)) == text


def test_split_clause_leaves_fitting_text_alone():
    assert split_clause("其實玩是一個鼓勵你去在平常沒有危機的時候", 10, 2) == [
        "其實玩是一個鼓勵你去在平常沒有危機的時候"
    ]


def test_card_mode_is_stricter_than_line_mode():
    """斷句（先後出現）比斷行（同框）嚴：賓語子句不可丟到下一張卡。"""
    text = "所以我覺得接下來的時代"
    assert 5 in clean_breaks(text, "line")  # 所以我覺得｜接下來的時代 → 同框可以
    assert 5 not in clean_breaks(text, "card")  # 分成兩張卡就是話沒講完


def test_never_orphans_a_two_char_line():
    """孤字行：切在語法接縫上也沒用，畫面上就是一行長、一行兩個字。"""
    assert wrap_lines("這就好像是那個佛教講的", 10, 2) == ["這就好像", "是那個佛教講的"]


def test_relaxed_fallback_never_starts_a_line_with_a_sticky_char():
    """排不下時可以鬆行尾，不可以鬆行首——行首掛「的」讀起來是話斷掉。"""
    lines = wrap_lines("好好的定義人類的Agency是什麼", 10, 2)
    assert lines == ["好好的定義人類的", "Agency是什麼"]
    assert not lines[1].startswith("的")
