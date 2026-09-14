"""Hero 大字卡的複述閘（`_hero_text`）。

每一筆斷言都是 20260901 蘇予昕的真實案例——四張修修判「砍」的、兩張判「留」的。
這個閘的價值完全取決於這六筆分不分得開，所以測試就用它們，不另外發明資料。
"""

from __future__ import annotations

import pytest

from agents.brook.script_video.finished_cut_production._hero_text import (
    is_verbatim_quote,
    normalize_for_verbatim,
)

# (名稱, 卡片, 該卡宣告的 master cue 逐字稿)
VERBATIM_CASES = [
    pytest.param(
        "我們DNA就不是這樣子設計的",
        "我們DNA就不是這樣子設計的啊",
        id="punch-L02-只差一個語助詞",
    ),
    pytest.param(
        "不會停止愛父母，只會停止愛自己",
        "他不會停止愛父母他只會停止愛他自己",
        id="punch-L02-兩句合併只去掉他",
    ),
    pytest.param(
        "其實就是重播",
        "跟你舊的劇情其實就是重播就是重演",
        id="punch-L03-整句逐字",
    ),
    pytest.param(
        "成為一個自在的我",
        "我現在就要成為一個自在的我",
        id="punch-L03-整句的後半",
    ),
]

EDITORIAL_CASES = [
    pytest.param(
        "一天六七千個念頭都在罵自己",
        "一天大概有六七千個念頭哈哈哈哈然後絕大部分都在批判自己",
        id="punch-L04-跨段濃縮加換詞",
    ),
    pytest.param(
        "被允許不做，才有力氣做",
        "所以當你被允許可以不用做的時候你反而有力氣去做了",
        id="punch-L04-重寫成悖論",
    ),
]


@pytest.mark.parametrize(("display", "cue_text"), VERBATIM_CASES)
def test_verbatim_heroes_are_rejected(display: str, cue_text: str):
    """修修 2026-09-14 在 review 裡指出的四張，全部要被擋下來。

    他的原話：「hero title 就是重複來賓和我講的話⋯⋯為什麼會有這種低級錯誤？」
    """
    assert is_verbatim_quote(display, cue_text) is True


@pytest.mark.parametrize(("display", "cue_text"), EDITORIAL_CASES)
def test_editorial_heroes_pass(display: str, cue_text: str):
    """修修 2026-09-09 判「留」的兩張，一張都不能誤傷。

    這兩筆是整個判準的存在理由：模糊相似度會擋掉「一天六七千個念頭都在罵自己」
    （它跟原文共用大量字元），子字串測試不會。誤擋好卡片比放過壞卡片更糟——
    前者會逼人繞過這道閘，後者至少還有人眼在 review。
    """
    assert is_verbatim_quote(display, cue_text) is False


def test_normalization_drops_only_non_editorial_differences():
    # 標點、空白、語助詞、第三人稱：這些的有無不構成改寫。
    assert normalize_for_verbatim("他不會停止愛父母，只會停止愛自己啊") == normalize_for_verbatim(
        "不會停止愛父母只會停止愛自己"
    )
    # 第一/第二人稱**保留**：手冊明訂卡片裡的「我」必須是講者本人，
    # 抹掉它等於放棄一個有意義的區別。
    assert normalize_for_verbatim("我很累") != normalize_for_verbatim("很累")
    assert normalize_for_verbatim("你很累") != normalize_for_verbatim("很累")
    # 全形轉半形，這樣「ＤＮＡ」與「DNA」不會被當成兩件事。
    assert normalize_for_verbatim("ＤＮＡ") == "DNA"


def test_換詞就不是複述():
    """換一個詞就足以脫離這道閘——它擋的是搬運，不是要求重寫得面目全非。"""
    cue = "然後絕大部分都在批判自己"
    assert is_verbatim_quote("絕大部分都在批判自己", cue) is True
    assert is_verbatim_quote("絕大部分都在罵自己", cue) is False


def test_empty_after_normalization_is_not_this_gates_problem():
    """只有標點或語助詞的卡片是另一種壞，交給既有的長度檢查擋。

    在這裡回 True 會讓錯誤訊息指向「複述」，那是說謊——它根本沒有引用任何東西。
    """
    assert is_verbatim_quote("？！", "他說他不知道") is False
    assert is_verbatim_quote("有內容", "") is False
