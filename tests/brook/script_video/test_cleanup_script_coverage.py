"""cleanup v2 tests — clap_impulse 物理偵測 + script_coverage 選 take。

每個測試鎖一個 2026-07-30「頻道復出」實測學到的 failure mode：
- 語音爆破音混充拍手（掰掰的ㄅ）→ 頻譜 + 靜音上下文分離
- 失敗 take 與成功 take 的 ASR 輸出不同 → 以稿為中介
- WhisperX 隨機輸出簡體 → t2s 歸一
- 講一半就拍手的前綴片段 → explain-fraction 的 partial 分支
- 懸空字與保留字零間隙相連 → 段界 clamp
- cut 縫隙孤兒微段 → 丟棄
"""

from __future__ import annotations

import math
import wave
from pathlib import Path

import numpy as np
import pytest

from agents.brook.script_video.cleanup.clap_impulse import detect_claps, merge_ng_markers
from agents.brook.script_video.cleanup.script_align import Word
from agents.brook.script_video.cleanup import script_coverage as sc

pytest.importorskip("rapidfuzz")
pytest.importorskip("opencc")


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------


def make_words(spans: list[tuple[str, float, float]]) -> list[Word]:
    """[(text, start, end)] → 每字均分時間的 Word 列表。"""
    words: list[Word] = []
    for text, t0, t1 in spans:
        n = len(text)
        for i, ch in enumerate(text):
            words.append(
                Word(text=ch, start=t0 + (t1 - t0) * i / n, end=t0 + (t1 - t0) * (i + 1) / n)
            )
    return words


def write_wav(path: Path, audio: np.ndarray, sr: int = 16000) -> Path:
    pcm = (np.clip(audio, -1, 1) * 32767).astype(np.int16)
    with wave.open(str(path), "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(sr)
        wf.writeframes(pcm.tobytes())
    return path


def synth(sr: int, duration: float) -> np.ndarray:
    return np.zeros(int(sr * duration), dtype=np.float32)


def add_burst(audio: np.ndarray, sr: int, t: float, *, amp: float = 0.9, ms: float = 5.0) -> None:
    """寬頻 impulse（白噪 burst）≈ 拍手。"""
    rng = np.random.default_rng(42)
    a = int(t * sr)
    n = int(ms / 1000 * sr)
    audio[a : a + n] += (rng.uniform(-1, 1, n) * amp).astype(np.float32)


def add_tone_burst(audio: np.ndarray, sr: int, t: float, *, hz: float, amp: float, dur: float) -> None:
    a = int(t * sr)
    n = int(dur * sr)
    ts = np.arange(n) / sr
    audio[a : a + n] += (amp * np.sin(2 * math.pi * hz * ts)).astype(np.float32)


# ---------------------------------------------------------------------------
# clap_impulse
# ---------------------------------------------------------------------------


def test_clap_detected_in_silence(tmp_path: Path) -> None:
    sr = 16000
    audio = synth(sr, 12.0)
    add_burst(audio, sr, 6.0)
    claps = detect_claps(write_wav(tmp_path / "a.wav", audio, sr))
    assert len(claps) == 1
    assert abs(claps[0].time_sec - 6.0) < 0.05


def test_loud_transient_inside_speech_rejected(tmp_path: Path) -> None:
    """語音環繞的高振幅瞬態（句中爆破音）不是拍手。"""
    sr = 16000
    audio = synth(sr, 12.0)
    add_tone_burst(audio, sr, 1.5, hz=300, amp=0.15, dur=1.0)  # 語音
    add_burst(audio, sr, 2.0)  # 語音中的瞬態
    claps = detect_claps(write_wav(tmp_path / "b.wav", audio, sr))
    assert claps == []


def test_low_freq_plosive_in_silence_rejected(tmp_path: Path) -> None:
    """被靜音包圍的孤立詞爆破音（實測「掰掰」）：振幅夠大但無高頻 → 不是拍手。"""
    sr = 16000
    audio = synth(sr, 12.0)
    add_tone_burst(audio, sr, 9.0, hz=400, amp=0.9, dur=0.02)
    claps = detect_claps(write_wav(tmp_path / "c.wav", audio, sr))
    assert claps == []


def test_ng_marker_merge_by_intervening_voice(tmp_path: Path) -> None:
    sr = 16000
    audio = synth(sr, 20.0)
    add_burst(audio, sr, 4.0)
    add_burst(audio, sr, 6.0)  # 4→6 之間靜音 → 同一 NG
    add_burst(audio, sr, 14.0)  # 6→14 之間有語音 → 新 NG
    add_tone_burst(audio, sr, 9.0, hz=300, amp=0.15, dur=2.0)
    wav = write_wav(tmp_path / "d.wav", audio, sr)
    claps = detect_claps(wav)
    assert [round(c.time_sec) for c in claps] == [4, 6, 14]
    markers = merge_ng_markers(wav, claps)
    assert [len(m.clap_times) for m in markers] == [2, 1]


# ---------------------------------------------------------------------------
# script_coverage
# ---------------------------------------------------------------------------

SCRIPT = "今天天氣真的很好，我們一起出去外面玩吧。"


def test_split_units_merges_short() -> None:
    units = sc.split_units("好。今天天氣真的很好，我們出去玩吧。")
    assert all(len(u.norm) >= 6 for u in units)


def test_retake_prefers_last_take() -> None:
    """同單元兩次完整 take → 選最後一次（重講到對為止）。"""
    words = make_words(
        [
            ("今天天氣真的很好", 0.0, 2.0),
            ("今天天氣真的很好", 5.0, 7.0),
            ("我們一起出去外面玩吧", 7.5, 10.0),
        ]
    )
    plan = sc.build_clean_plan(words, SCRIPT, total_duration_sec=12.0)
    unit0 = next(o for o in plan.selected if o.unit_idx == 0)
    assert unit0.t0 >= 4.9, "應選較晚的 take"
    report = sc.verify_plan(plan, words, [])
    assert report["duplicated_units"] == []


def test_aborted_prefix_classified_failed_take() -> None:
    """講一半就拍手的前綴片段：explain-fraction 的 partial 分支 → 剪。"""
    words = make_words(
        [
            ("今天天氣真的很", 0.0, 1.8),  # 前綴，未講完
            ("今天天氣真的很好", 5.0, 7.0),
            ("我們一起出去外面玩吧", 7.5, 10.0),
        ]
    )
    plan = sc.build_clean_plan(words, SCRIPT, total_duration_sec=12.0)
    blocks = [b for b in plan.blocks if b.classification != "noise"]
    assert len(blocks) == 1
    assert blocks[0].classification == "failed-take"
    kept_t0 = plan.kept_segments[0][0]
    assert kept_t0 > 4.0, "失敗前綴不得進保留段"


def test_simplified_asr_matches_traditional_script() -> None:
    """WhisperX 隨機輸出簡體（實測整句）— t2s 歸一後照樣對得上。"""
    words = make_words([("今天天气真的很好", 0.0, 2.0), ("我们一起出去外面玩吧", 2.5, 5.0)])
    plan = sc.build_clean_plan(words, SCRIPT, total_duration_sec=6.0)
    assert plan.unmatched_units == []


def test_zero_gap_dangler_clamped_out_of_segment() -> None:
    """懸空字與保留字零間隙相連（實測「…年代|我覺得」）→ 段界 clamp。"""
    words = make_words(
        [
            ("今天天氣真的很好", 0.0, 2.0),
            ("我們一起出去外面玩吧", 2.3, 4.5),
            ("我覺", 4.5, 4.9),  # 零間隙懸空字（稿外、noise 級）
        ]
    )
    plan = sc.build_clean_plan(words, SCRIPT, total_duration_sec=10.0)
    kept_texts = "".join(words[i].text for i in plan.kept_word_idx)
    assert "我覺" not in kept_texts
    last_seg_end = plan.kept_segments[-1][1]
    assert last_seg_end <= 4.5, "段尾 pad 不得吃進懸空字"


def test_orphan_micro_segment_dropped() -> None:
    """兩個 cut 縫隙間的孤兒微段 → 丟棄併入 cut。"""
    words = make_words(
        [
            ("今天天氣真的很好", 0.0, 2.0),
            ("嗯", 30.0, 30.3),  # 遠離其他內容的孤兒字（稿外）
            ("我們一起出去外面玩吧", 50.0, 52.5),
        ]
    )
    plan = sc.build_clean_plan(words, SCRIPT, total_duration_sec=60.0)
    kept_texts = "".join(words[i].text for i in plan.kept_word_idx)
    assert "嗯" not in kept_texts


def test_pause_collapsed_to_target_gap() -> None:
    """字間長停頓 → 段落切開，乾淨 timeline 上 gap ≤ 收斂目標。"""
    words = make_words(
        [
            ("今天天氣真的很好", 0.0, 2.0),
            ("我們一起出去外面玩吧", 8.0, 10.5),  # 6s 停頓
        ]
    )
    plan = sc.build_clean_plan(words, SCRIPT, total_duration_sec=12.0)
    assert len(plan.kept_segments) == 2
    report = sc.verify_plan(plan, words, [])
    assert report["max_clean_gap_sec"] <= 0.5


def test_tail_policy_drops_post_script_content() -> None:
    """稿尾之後的內容（誤錄）→ script-end 預設丟棄。"""
    words = make_words(
        [
            ("今天天氣真的很好", 0.0, 2.0),
            ("我們一起出去外面玩吧", 2.5, 5.0),
            ("這一段是收音收到的閒聊內容跟稿完全無關", 10.0, 15.0),
        ]
    )
    plan = sc.build_clean_plan(words, SCRIPT, total_duration_sec=20.0)
    kept_texts = "".join(words[i].text for i in plan.kept_word_idx)
    assert "閒聊" not in kept_texts
    keep_all = sc.build_clean_plan(
        words, SCRIPT, total_duration_sec=20.0, tail_policy="keep-all"
    )
    kept_all_texts = "".join(words[i].text for i in keep_all.kept_word_idx)
    assert "閒聊" in kept_all_texts


def test_build_srt_scripted_text_and_clean_timeline() -> None:
    """SRT 文字以稿為準、時間映射到乾淨 timeline。"""
    words = make_words(
        [
            ("今天天气真的很好", 0.0, 2.0),  # 簡體 ASR
            ("我們一起出去外面玩吧", 8.0, 10.5),
        ]
    )
    plan = sc.build_clean_plan(words, SCRIPT, total_duration_sec=12.0)
    srt = sc.build_srt(plan, words)
    assert "今天天氣真的很好" in srt  # 稿上繁體，非 ASR 簡體
    assert "00:00:0" in srt  # 乾淨 timeline 從 0 起算


# ---------------------------------------------------------------------------
# 2026-07-30 11s bug 修復（拍手否決硬約束 / 縫隙吸收 / 時間戳修復）
# ---------------------------------------------------------------------------

from agents.brook.script_video.cleanup.clap_impulse import NgMarker


def test_clap_veto_overrides_score_bucket() -> None:
    """拍手否決 > 分數桶：失敗 take 的 ASR 較乾淨也必須選拍手後的重錄。

    2026-07-30 11s bug 本尊：重錄 take 的 ASR 差一個桶，lateness 軟偏好
    被蓋掉，成品留了被拍手否決的 take。
    """
    words = make_words(
        [
            ("今天天氣真的很好", 0.0, 2.0),  # 被否決的 take（ASR 完美，100→桶12）
            ("今天天氣真得很好", 5.0, 7.0),  # 重錄（ASR 差一字，87.5→桶11）
            ("我們一起出去外面玩吧", 7.5, 10.0),
        ]
    )
    markers = [NgMarker(clap_times=(3.0,))]
    plan = sc.build_clean_plan(
        words, SCRIPT, total_duration_sec=12.0, ng_markers=markers
    )
    unit0 = next(o for o in plan.selected if o.unit_idx == 0)
    assert unit0.t0 >= 4.9, "拍手否決的 take 不得中選"
    # 反向對照：沒有拍手時，ASR 較乾淨的早 take 才會贏（證明本測試有牙齒）
    plan2 = sc.build_clean_plan(words, SCRIPT, total_duration_sec=12.0)
    unit0b = next(o for o in plan2.selected if o.unit_idx == 0)
    assert unit0b.t0 < 1.0


def test_gap_absorption_between_adjacent_takes() -> None:
    """相鄰選中單元間的短亂碼縫隙（同 take 連續語流）併入保留。"""
    words = make_words(
        [
            ("今天天氣真的很好", 0.0, 2.0),
            ("哦一行口", 2.05, 2.85),  # ASR 亂碼的稿文（實測「寫過一行code」）
            ("我們一起出去外面玩吧", 2.9, 5.4),
        ]
    )
    plan = sc.build_clean_plan(words, SCRIPT, total_duration_sec=7.0)
    kept_texts = "".join(plan.words[i].text for i in plan.kept_word_idx)
    assert "一行口" in kept_texts, "縫隙字必須併入保留（否則成品憑空少內容）"


def test_timestamp_repair_stretches_garbage_run(tmp_path: Path) -> None:
    """壓縮崩壞的時間戳攤回 VAD 實測語音範圍。"""
    sr = 16000
    audio = synth(sr, 12.0)
    add_tone_burst(audio, sr, 5.0, hz=300, amp=0.15, dur=3.0)  # 真實語音 5–8s
    wav = write_wav(tmp_path / "r.wav", audio, sr)
    # ASR 聲稱 20 個字擠在 5.0–5.5s（40 字/秒 — 崩壞）
    words = make_words([("今天天氣真的很好我們一起出去外面玩吧對啊", 5.0, 5.5)])
    repaired, notes = sc.repair_word_timestamps(words, wav)
    assert notes, "應偵測到崩壞 run"
    assert repaired[-1].end > 7.0, f"應攤回語音端點附近，got {repaired[-1].end}"
