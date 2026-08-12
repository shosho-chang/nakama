"""黏著度——從一集自己的逐字稿長出來的詞表，取代永遠補不完的詞庫。

2026-08-12 修修連續三輪 review 抓到的斷詞，全部在這裡鎖住：
冒牌者 → 黑馬班 → 健身教練 / team / attachment。
"""

from __future__ import annotations

import pytest

from shared.word_cohesion import Cohesion


def corpus(*lines: str) -> Cohesion:
    return Cohesion([(0.0, 1.0, t) for t in lines])


@pytest.fixture
def zh() -> Cohesion:
    """「冒牌」幾乎總是接「者」；「可是／然後」後面接什麼都有。"""
    return corpus(
        *["我有「冒牌者」的感覺" for _ in range(5)],
        "可是我覺得那樣不行",
        "可是他們都說很好",
        "可是那時候太小了",
        "然後我就走了",
        "然後他說沒關係",
        "然後大家都笑了",
    )


def test_flags_the_episode_specific_term(zh):
    """jieba 沒有「冒牌者」也不需要有——本集統計自己會說話。"""
    assert zh.splits_cjk("牌", "者") is not None
    assert "同一個詞" in zh.reason("我完全沒有冒牌", "者的問題 我就是")


def test_does_not_flag_frequent_function_word_collocations(zh):
    """「可是｜我」「然後｜我」同現次數很高，但條件機率低——那是搭配不是詞。"""
    assert zh.splits_cjk("是", "我") is None
    assert zh.splits_cjk("後", "我") is None
    assert zh.reason("然後上臺大可是", "我覺得那一整年") is None


def test_needs_enough_evidence():
    """只出現一兩次的搭配不足以判定，寧可放行也不要誤旗標。"""
    c = corpus("這是一個罕見詞組")
    assert c.splits_cjk("罕", "見") is None


def test_threshold_is_configurable():
    c = corpus(*["專有名詞" for _ in range(5)])
    assert c.splits_cjk("有", "名") is not None
    assert Cohesion([(0.0, 1.0, "專有名詞")] * 5, min_p=0.99).splits_cjk("有", "名") is not None
    assert Cohesion([(0.0, 1.0, "專有名詞")] * 5, min_n=99).splits_cjk("有", "名") is None


# ── 英文：靠 token 集合，不靠機率 ────────────────────────────────────────


@pytest.fixture
def en() -> Cohesion:
    return corpus("他說 We are a team 然後", "那個 attachment 就是不管", "What do you think")


def test_flags_split_english_word(en):
    assert en.splits_ascii_word("We are a tea", "m 然後到時候") == "team"
    assert "team" in en.reason("We are a tea", "m 然後到時候")
    assert "attachment" in en.reason("也是一個attachmen", "t就是不管")


def test_does_not_flag_a_real_english_word_boundary(en):
    """「do｜you」兩邊都是英數字，但 doyou 不是本集出現過的詞。"""
    assert en.splits_ascii_word("他說 What do", "you think") is None
    assert en.reason("他說 What do", "you think") is None


def test_cjk_side_disables_the_ascii_rule(en):
    assert en.splits_ascii_word("We are a team", "然後到時候") is None


def test_space_chunks_do_not_leak_across(en):
    """空格是 house style 的停頓標記——跨過去的相鄰不代表黏著。"""
    c = corpus(*["結束 開始" for _ in range(9)])
    assert c.splits_cjk("束", "開") is None


def test_empty_inputs_are_safe(zh):
    assert zh.reason("", "者的問題") is None
    assert zh.reason("我完全沒有冒牌", "") is None
    assert zh.splits_cjk("", "") is None
