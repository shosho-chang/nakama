"""speaker_assign（Viterbi 說話者判定）與 run_speaker_split 切分邏輯測試。"""

from __future__ import annotations

import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

from shared.speaker_assign import (
    FRAME_SEC,
    MAX_WORD_SEC,
    SEGMENT_MAX_SEC,
    _evidence_span,
    assign_word_speakers,
)


def _env_from_segments(segments: list[tuple[float, float, int]], total_sec: float) -> np.ndarray:
    """(start, end, spk) 段落 → 兩軌合成 RMS 包絡（說話 0.1、靜音 1e-6）。"""
    n = int(total_sec / FRAME_SEC)
    env = np.full((2, n), 1e-6, dtype=np.float32)
    for start, end, spk in segments:
        f0, f1 = int(start / FRAME_SEC), int(end / FRAME_SEC)
        env[spk, f0:f1] = 0.1
    return env


def _words(specs: list[tuple[float, float, str]]) -> list[dict]:
    return [{"word": w, "start": s, "end": e} for s, e, w in specs]


def test_clean_turn_assignment():
    env = _env_from_segments([(0, 2, 0), (2.4, 4, 1)], 5)
    words = _words([(0.2, 0.5, "你"), (0.6, 1.0, "好"), (2.5, 2.9, "嗨"), (3.0, 3.4, "囉")])
    assert assign_word_speakers(words, env) == [0, 0, 1, 1]


def test_single_word_flip_suppressed():
    # 連續說話中單一詞在對方軌較大聲（crosstalk）→ 轉換成本壓掉假輪替
    env = _env_from_segments([(0, 3, 0)], 4)
    env[1, int(1.0 / FRAME_SEC) : int(1.2 / FRAME_SEC)] = 0.15  # 短暫串音
    words = _words([(0.1, 0.5, "一"), (0.6, 0.9, "二"), (1.0, 1.2, "三"), (1.3, 1.8, "四")])
    assert assign_word_speakers(words, env) == [0, 0, 0, 0]


def test_tail_silence_word_stays_with_previous_speaker():
    # 句尾輕聲字（兩軌皆無聲）→ 歸把句子講完的前一位，不歸下一位
    env = _env_from_segments([(0, 1.0, 0), (1.6, 3, 1)], 4)
    words = _words([(0.1, 0.5, "講"), (0.6, 0.9, "什"), (1.05, 1.3, "書"), (1.7, 2.2, "今")])
    assert assign_word_speakers(words, env) == [0, 0, 0, 1]


def test_stretched_word_end_does_not_leak_next_speaker():
    # WhisperX 常把詞 end 拉到下一詞 start：能量窗截斷後不受下一位污染
    env = _env_from_segments([(0, 1.0, 0), (2.0, 3.5, 1)], 4)
    words = _words([(0.1, 0.6, "尾"), (0.7, 2.0, "字"), (2.0, 2.5, "換"), (2.6, 3.0, "人")])
    assert assign_word_speakers(words, env) == [0, 0, 1, 1]


def test_missing_timestamps_inherit():
    env = _env_from_segments([(0, 2, 0)], 3)
    words = [
        {"word": "有", "start": 0.2, "end": 0.6},
        {"word": "缺", "start": None, "end": None},
        {"word": "尾", "start": 1.0, "end": 1.4},
    ]
    assert assign_word_speakers(words, env) == [0, 0, 0]


def test_split_helpers():
    from run_speaker_split import _map_position, _runs, _snap_outside_brackets, _snap_word_boundary

    # difflib 位置映射：校正插入字後位置跟著平移
    assert _map_position("今天是講什麼書", "今天是講什麼書", 4) == 4
    assert _map_position("上鳳鑫傑的節目", "上鳳馨姐的節目", 4) == 4
    # 括號內部切點移到括號邊界
    assert _snap_outside_brackets("讀《大腦簡史》很好", 4) in (1, 7)
    # jieba 詞邊界吸附
    bounds = {0, 2, 4, 7}
    assert _snap_word_boundary(3, bounds) == 2
    assert _snap_word_boundary(4, bounds) == 4
    # runs：None 併入前段、變更點正確
    assert _runs([None, 0, 0, 1, None, 1, 0]) == [(0, 3), (3, 6), (6, 7)]


def test_split_runs_single_speaker_untouched():
    from run_speaker_split import _runs

    assert _runs([0, 0, None, 0]) == [(0, 4)]


# --- token 粒度：詞級 vs 句級（修修 2026-08-30） -------------------------------


def _envelope(pattern: list[tuple[float, float, int]], total_sec: float) -> np.ndarray:
    """(start, end, loud_channel) → 兩軌 50ms RMS 包絡；響 0.1、靜 0.0005。"""
    n = int(total_sec / FRAME_SEC)
    env = np.full((2, n), 0.0005, dtype=np.float32)
    for start, end, ch in pattern:
        f0 = int(start / FRAME_SEC)
        f1 = int(end / FRAME_SEC)
        env[ch, f0:f1] = 0.1
    return env


def test_evidence_span_follows_token_granularity():
    """粒度是量出來的：WhisperX 詞約 0.3s、memo 句級 segment 約 1.9s。"""
    assert _evidence_span([0.25, 0.3, 0.35]) == MAX_WORD_SEC
    assert _evidence_span([1.2, 1.9, 2.4]) == SEGMENT_MAX_SEC
    assert _evidence_span([]) == MAX_WORD_SEC


def test_segment_token_is_judged_on_the_whole_sentence():
    """句級 token 的判定不可被句首那 0.4 秒綁架。

    實案：來賓講「所以玩其實就是一種學習」，句首壓著主持人的尾音。用 0.4s 窗
    整句被判給主持人，畫面因此切走 1.68 秒；用完整句窗才判對。
    """
    words = [
        {"word": "前句", "start": 0.0, "end": 2.0},
        {"word": "爭議句", "start": 2.0, "end": 4.0},
        {"word": "後句", "start": 4.0, "end": 6.0},
    ]
    envelopes = _envelope(
        [
            (0.0, 2.0, 1),  # 來賓
            (2.0, 2.5, 0),  # 句首壓著主持人的尾音
            (2.5, 4.0, 1),  # 這一句其實是來賓講的
            (4.0, 6.0, 1),  # 來賓
        ],
        12.0,  # 後半留白：兩軌都要有足夠靜音，噪音底才量得準
    )
    assert assign_word_speakers(words, envelopes) == [1, 1, 1]
