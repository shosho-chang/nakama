"""靜音優先斷句——2026-08-12「冒牌｜者」出貨事故的回歸測試。

事故：詞典沒有「冒牌者」（jieba FREQ=None）→ 切成「冒牌｜者」→ 四條詞典規則
一致判定乾淨 → 「…完全沒有冒牌」｜「者的問題…」出貨。修修 review 45 秒就抓到。
根因是判準建立在詞典覆蓋率上，而詞典永遠不完整。
"""

from __future__ import annotations

import numpy as np
import pytest

from shared.pause_map import FRAME, PauseMap
from shared.subtitle_finalize import boundary_reason, find_bad_boundaries
from shared.subtitle_reboundary import _pick, _worth_moving, bare, repair_cues

CHAR = 0.2  # 每字 0.2s，第 i 個字佔 [0.2i, 0.2(i+1))


def chars_to_words(text: str) -> list[dict]:
    return [{"word": ch, "start": i * CHAR, "end": (i + 1) * CHAR} for i, ch in enumerate(text)]


def env_with_silence_at(times: list[float], total: float = 12.0) -> np.ndarray:
    """說話 0.1 為底，指定時間點挖靜音；尾端補足靜音讓底噪 P5 落在靜音上。"""
    n = int(total / FRAME)
    env = np.full(n, 0.1)
    for t in times:
        k = int(t / FRAME)
        env[k - 1 : k + 2] = 0.0005
    env[int(n * 0.7) :] = 0.0005
    return env


# ── 詞典層：封閉類詞素（者/們）不可當 cue 開頭 ──────────────────────────


def test_bound_morpheme_head_is_flagged_without_any_dictionary_entry():
    """「冒牌者」不在 jieba 詞庫也不需要在——「者」不能起句是語言事實。"""
    tail, head = "我覺得在女中之前我完全沒有冒牌", "者的問題 我就是一個很爽的"
    assert boundary_reason(tail, head) is not None


def test_men_suffix_head_is_flagged():
    assert boundary_reason("這件事情要問我同學", "們的看法才知道") is not None


@pytest.mark.parametrize("head", ["性別平等很重要", "化學課本很厚", "得到答案以後", "地方創生"])
def test_ambiguous_suffixes_are_not_flagged(head):
    """性/化/得/地 都能起詞，收進封閉類會誤旗標並驅動破壞性修復。"""
    assert boundary_reason("我們今天要討論的是", head) is None


def test_find_bad_boundaries_catches_the_shipped_cue_pair():
    cues = [
        (41.2, 43.0, "我覺得在女中之前我完全沒有冒牌"),
        (43.0, 46.1, "者的問題 我就是一個很爽的鬼混的學生"),
    ]
    flags = find_bad_boundaries(cues)
    assert [f["cue"] for f in flags] == [1]
    assert "者" in flags[0]["reason"]


# ── 音檔層：靜音是主判準 ────────────────────────────────────────────────


def test_noisy_boundary_moves_to_the_silent_candidate():
    """切點落在連續發聲中、附近有真停頓 → 搬過去。詞典完全沒參與這個判斷。"""
    a, b = "我覺得那個東西", "很好吃因為它是新鮮的"
    words = chars_to_words(a + b)
    # 原切點在 7 字處（t=1.4）；真停頓在 10 字處（t=2.0）
    pause = PauseMap(env_with_silence_at([2.0]))
    cues = [(0.0, 1.4, a), (1.4, 3.4, b)]
    assert not boundary_reason(a, b)  # 詞典認為這一刀沒問題
    assert pause.is_noisy(1.4)  # 音檔認為切在發聲中

    fixed, stats = repair_cues(cues, words=words, pause=pause)
    assert stats["moved"] == 1
    assert stats["pause_used"] is True
    assert fixed[0][2] + fixed[1][2] == a + b  # 一個字都沒動
    assert pause.is_quiet(fixed[1][0])


def test_clean_and_quiet_boundary_is_left_alone():
    a, b = "我覺得那個東西", "很好吃因為它是新鮮的"
    words = chars_to_words(a + b)
    pause = PauseMap(env_with_silence_at([1.4]))  # 原切點就在靜音上
    fixed, stats = repair_cues([(0.0, 1.4, a), (1.4, 3.4, b)], words=words, pause=pause)
    assert stats["moved"] == 0
    assert fixed[0][2] == a and fixed[1][2] == b


def test_noisy_boundary_without_better_option_is_not_churned():
    """全片沒有停頓可落時不要為了千分之一的差異亂搬（多輪漂移防呆）。"""
    a, b = "我覺得那個東西", "很好吃因為它是新鮮的"
    words = chars_to_words(a + b)
    pause = PauseMap(env_with_silence_at([9.5]))  # 靜音遠在 cue 範圍外
    fixed, stats = repair_cues([(0.0, 1.4, a), (1.4, 3.4, b)], words=words, pause=pause)
    assert stats["moved"] == 0
    assert fixed[0][2] == a


def test_bound_morpheme_is_fixed_even_when_no_silence_exists():
    """安吉那一刀的實況：前後十個字都在連續發聲，靜音救不了，靠封閉類規則。"""
    a, b = "我覺得在女中之前我完全沒有冒牌", "者的問題 我就是一個很爽的鬼混的學生"
    words = chars_to_words((a + b).replace(" ", ""))
    pause = PauseMap(env_with_silence_at([]))  # 完全沒有停頓
    fixed, stats = repair_cues([(0.0, 3.0, a), (3.0, 6.4, b)], words=words, pause=pause)
    assert stats["moved"] == 1
    assert "冒牌者" in fixed[0][2]  # 詞不再被攔腰切開
    assert not fixed[1][2].startswith("者")


def test_existing_space_wins_when_audio_has_no_opinion():
    """全都吵時比 RMS 是自欺——改用原文既有空格（上游標好的分句點）。"""
    cands = [
        {"p": 13, "rms": 0.080, "gap": 0.0, "space": False, "dist": 2, "imbalance": 6},
        {"p": 19, "rms": 0.052, "gap": 0.0, "space": True, "dist": 4, "imbalance": 6},
        {"p": 22, "rms": 0.032, "gap": 0.0, "space": False, "dist": 7, "imbalance": 12},
    ]
    assert _pick(cands, quiet=0.004, noisy=0.015)["p"] == 19


def test_real_silence_beats_the_space_hint():
    cands = [
        {"p": 13, "rms": 0.0008, "gap": 0.0, "space": False, "dist": 2, "imbalance": 6},
        {"p": 19, "rms": 0.052, "gap": 0.0, "space": True, "dist": 4, "imbalance": 6},
    ]
    assert _pick(cands, quiet=0.004, noisy=0.015)["p"] == 13


def test_pick_falls_back_to_est_gap_without_a_pause_map():
    cands = [
        {"p": 10, "rms": None, "gap": 0.05, "space": False, "dist": 2, "imbalance": 4},
        {"p": 16, "rms": None, "gap": 0.40, "space": False, "dist": 5, "imbalance": 2},
    ]
    assert _pick(cands, quiet=None, noisy=None)["p"] == 16


def test_repair_reports_when_running_blind():
    a, b = "我覺得那個東西", "很好吃因為它是新鮮的"
    _, stats = repair_cues([(0.0, 1.4, a), (1.4, 3.4, b)], words=chars_to_words(a + b))
    assert stats["pause_used"] is False


# ── 不變量 ──────────────────────────────────────────────────────────────


def test_ascii_seam_space_is_allowed_but_cjk_space_is_not():
    """`_seam_join` 規定 ASCII 相接補一格；不變量②要放行它，CJK 仍禁止。"""
    a, b = "他說 What do", "you mean 我就愣住了"
    words = chars_to_words((a + b).replace(" ", ""))
    pause = PauseMap(env_with_silence_at([2.4]))
    fixed, _ = repair_cues([(0.0, 2.0, a), (2.0, 4.4, b)], words=words, pause=pause)
    joined = fixed[0][2] + fixed[1][2]
    assert "Whatdo" not in joined and "youmean" not in joined


# ── 2026-08-12 第三輪回饋：音檔沒意見時的斷詞 ────────────────────────────


def test_english_word_is_never_split_even_at_a_quiet_spot():
    """`words.json` 把英文切成單一字母（本集去重只有 23 個 token＝字母表），
    字元時間又是均分的——`m` 常落進詞尾拖尾的靜音，看起來「很安靜」。少了
    空格規則，重切會把 team 切成 tea｜m（2026-08-12 SL7 實測出貨）。"""
    a, b = "他說 We are a team 然後", "到時候我就懂了這件事"
    words = chars_to_words((a + b).replace(" ", ""))
    ba = bare(a)
    # 把 team 內部（tea｜m）做成全片最安靜的地方，誘導演算法切在那裡
    pause = PauseMap(env_with_silence_at([(ba.index("team") + 3) * CHAR]))
    fixed, _ = repair_cues([(0.0, 3.4, a), (3.4, 6.4, b)], words=words, pause=pause)
    joined = [c[2] for c in fixed]
    assert not any(t.rstrip().endswith("tea") for t in joined)
    assert not any(t.lstrip().startswith("m ") for t in joined)


def test_cohesion_triggers_a_move_when_audio_has_no_opinion():
    """「健身｜教練」實測靜音持續 0ms——音檔給不出答案，靠本集自己的統計。"""
    from shared.word_cohesion import Cohesion

    co = Cohesion([(0.0, 1.0, "我要成為健身教練這件事")] * 5)
    assert co.reason("你好勇敢成為健身", "教練然後告訴大家") is not None
    assert co.reason("我昨天去了那間餐廳", "他們的牛肉麵很好吃") is None


class _Bar:
    quiet, noisy = 0.004, 0.015


@pytest.mark.parametrize(
    "cur,best,expect,why",
    [
        (0.01673, 0.00649, True, "黑馬｜班：吵→不吵是類別改善，只有 2.58 倍也要搬"),
        (0.05000, 0.03000, False, "兩邊都吵、只差 1.7 倍 → 不動（擋 churn）"),
        (0.05000, 0.01000, True, "兩邊都吵但差 5 倍 → 值得搬"),
        (0.00800, 0.00300, False, "本來就不吵 → 不必為了更安靜而搬"),
    ],
)
def test_worth_moving_gate(cur, best, expect, why):
    assert _worth_moving(cur, best, _Bar()) is expect, why


def test_worth_moving_without_audio_never_blocks():
    assert _worth_moving(None, None, None) is True
