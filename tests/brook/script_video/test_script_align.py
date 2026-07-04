"""script_align — script-anchored cut detection + corrected SRT（純合成，零 GPU）.

情境 fixture 模擬修修 2026-07-04 工作流：
  正確段 A（0.5–3.3s）→ 靜音 → 失敗 take B（5.0–7.4s）→ 👏(8.0s) →
  停頓 → retake B'（9.5–12.7s，開頭照稿）

驗收：
- cut 起點錨定在失敗 take B 的第一個字，正確段 A 不被誤刪
- 誤偵測的拍手（前方無重複文字）不產 cut
- corrected SRT：ASR 錯字換成稿上正確寫法、時間單調、cut 重映射後總長縮短
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from agents.brook.script_video.cleanup.cuts import CutPoint
from agents.brook.script_video.cleanup.mistake_removal import detect_single_claps
from agents.brook.script_video.cleanup.script_align import (
    Word,
    correct_srt,
    detect_script_anchored_cuts,
    load_words,
    normalize_text,
    remap_words_through_cuts,
)

_CLAP_FIXTURE = Path(__file__).parents[2] / "fixtures" / "script_video" / "clap_marker_audio.wav"

_SCRIPT = "今天想跟大家聊三個增肌的觀念。第一個觀念是蛋白質要吃夠，不是吃多。"


def _chars_as_words(text: str, start: float, end: float) -> list[Word]:
    """把一句話拆成逐字 Word（模擬 WhisperX zh align 的 per-char 輸出）."""
    span = end - start
    n = len(text)
    return [
        Word(text=ch, start=start + span * i / n, end=start + span * (i + 1) / n)
        for i, ch in enumerate(text)
    ]


def _scenario_words(broll_error: bool = False) -> list[Word]:
    """A 正確 → B 失敗 → clap@8.0 → B' retake。broll_error=True 時 ASR 出錯字."""
    retake_text = (
        "第一個觀念是蛋白值要吃夠不是吃多" if broll_error else "第一個觀念是蛋白質要吃夠不是吃多"
    )
    return (
        _chars_as_words("今天想跟大家聊三個增肌的觀念", 0.5, 3.3)
        + _chars_as_words("第一個觀念是蛋白質要吃", 5.0, 7.4)  # 失敗 take（沒講完）
        + _chars_as_words(retake_text, 9.5, 12.7)
    )


# ---------------------------------------------------------------------------
# detect_script_anchored_cuts
# ---------------------------------------------------------------------------


def test_cut_anchors_to_failed_take_not_preceding_correct_segment() -> None:
    cuts = detect_script_anchored_cuts(_scenario_words(), [8.0])
    assert len(cuts) == 1
    cut = cuts[0]
    # 失敗 take 從 5.0s 開始；正確段 A 結束在 3.3s — cut 不得吃進 A
    assert 4.9 <= cut.start_sec <= 5.2
    # cut_end = retake 開口 9.5 − lead-in（4/30 ≈ 0.133）
    assert cut.end_sec == pytest.approx(9.5 - 4 / 30, abs=0.05)
    assert cut.type == "ripple-delete"
    assert cut.reason == "alignment-detected"
    assert cut.confidence >= 0.7


def test_false_clap_without_repeated_text_produces_no_cut() -> None:
    # 4.2s 的「拍手」（關門聲）：其後第一段語音是失敗 take B，
    # 但其前（正確段 A）沒有重複文字 → 不產 cut
    words = _chars_as_words("今天想跟大家聊三個增肌的觀念", 0.5, 3.3) + _chars_as_words(
        "第一個觀念是蛋白質要吃夠不是吃多", 5.0, 8.4
    )
    assert detect_script_anchored_cuts(words, [4.2]) == []


def test_false_clap_then_real_clap_windows_are_independent() -> None:
    # 誤偵測@4.2 + 真拍手@8.0：真拍手的回溯視窗從 4.2 開始，仍抓到失敗 take
    cuts = detect_script_anchored_cuts(_scenario_words(), [4.2, 8.0])
    assert len(cuts) == 1
    assert 4.9 <= cuts[0].start_sec <= 5.2


def test_consecutive_failures_produce_overlapping_mergeable_cuts() -> None:
    # 失敗兩次：take1 fail → 👏@3.6 → take2 fail → 👏@7.0 → take3 好
    words = (
        _chars_as_words("第一個觀念是蛋白質要", 0.5, 3.0)
        + _chars_as_words("第一個觀念是蛋白質要吃", 4.2, 6.6)
        + _chars_as_words("第一個觀念是蛋白質要吃夠不是吃多", 8.0, 11.2)
    )
    cuts = detect_script_anchored_cuts(words, [3.6, 7.0])
    assert len(cuts) == 2
    assert cuts[0].start_sec == pytest.approx(0.5, abs=0.2)
    assert cuts[1].start_sec == pytest.approx(4.2, abs=0.2)
    # 兩段相接（cut1.end 到 take2 開口前、cut2 從 take2 開頭起）→ merge 後全刪
    assert cuts[0].end_sec >= cuts[1].start_sec - 0.5


def test_retake_with_minor_wording_drift_still_matches() -> None:
    # 失敗 take 措辭跟 retake 有小差（fuzzy ratio 要吃得下）
    words = (
        _chars_as_words("第一個觀念是蛋白質得吃夠", 1.0, 3.4)  # 「得」
        + _chars_as_words("第一個觀念是蛋白質要吃夠不是吃多", 5.0, 8.2)  # 「要」
    )
    cuts = detect_script_anchored_cuts(words, [4.0])
    assert len(cuts) == 1
    assert cuts[0].start_sec == pytest.approx(1.0, abs=0.2)


def test_clap_with_no_speech_after_produces_no_cut() -> None:
    words = _chars_as_words("今天想跟大家聊", 0.5, 2.0)
    assert detect_script_anchored_cuts(words, [3.0]) == []


# ---------------------------------------------------------------------------
# detect_single_claps（fixture：peaks 2.0/2.15 + 5.0/5.1 → merge 成 2 events）
# ---------------------------------------------------------------------------


def test_detect_single_claps_merges_adjacent_peaks() -> None:
    events = detect_single_claps(_CLAP_FIXTURE)
    assert len(events) == 2
    assert events[0] == pytest.approx(2.0, abs=0.1)
    assert events[1] == pytest.approx(5.0, abs=0.1)


# ---------------------------------------------------------------------------
# load_words
# ---------------------------------------------------------------------------


def test_load_words_accepts_all_three_shapes(tmp_path: Path) -> None:
    entries = [
        {"word": "今", "start": 0.5, "end": 0.6},
        {"word": "天", "start": 0.6, "end": 0.7},
        {"word": "壞", "start": None, "end": 0.9},  # align 失敗 → 略過
    ]
    shapes = {
        "wrapped.json": {"words": entries},
        "bare.json": entries,
        "whisperx.json": {"segments": [{"words": entries}]},
    }
    for name, payload in shapes.items():
        p = tmp_path / name
        p.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
        words = load_words(p)
        assert [w.text for w in words] == ["今", "天"], name
        assert words[0].start == 0.5


# ---------------------------------------------------------------------------
# normalize_text
# ---------------------------------------------------------------------------


def test_normalize_strips_punctuation_and_maps_indices() -> None:
    norm, index_map = normalize_text("你好，World！")
    assert norm == "你好world"
    assert index_map == [0, 1, 3, 4, 5, 6, 7]


# ---------------------------------------------------------------------------
# correct_srt
# ---------------------------------------------------------------------------


def test_correct_srt_replaces_asr_errors_with_script_text() -> None:
    # ASR 把「蛋白質」聽成「蛋白值」；正確段 A + retake（cut 後的乾淨流）
    words = _chars_as_words("今天想跟大家聊三個增肌的觀念", 0.5, 3.3) + _chars_as_words(
        "第一個觀念是蛋白值要吃夠不是吃多", 5.0, 8.2
    )
    srt = correct_srt(words, _SCRIPT)
    assert "蛋白質" in srt
    assert "蛋白值" not in srt
    assert "今天想跟大家聊三個增肌的觀念" in srt


def test_correct_srt_timestamps_are_monotonic() -> None:
    words = _chars_as_words("今天想跟大家聊三個增肌的觀念", 0.5, 3.3) + _chars_as_words(
        "第一個觀念是蛋白質要吃夠不是吃多", 5.0, 8.2
    )
    srt = correct_srt(words, _SCRIPT)
    starts = []
    for block in srt.strip().split("\n\n"):
        ts_line = block.splitlines()[1]
        h, m, rest = ts_line.split(" --> ")[0].split(":")
        s, ms = rest.split(",")
        starts.append(int(h) * 3600 + int(m) * 60 + int(s) + int(ms) / 1000)
    assert starts == sorted(starts)
    assert starts[0] == pytest.approx(0.5, abs=0.1)


def test_correct_srt_with_cuts_remaps_to_clean_timeline() -> None:
    words = _scenario_words(broll_error=True)
    cuts = detect_script_anchored_cuts(words, [8.0])
    srt = correct_srt(words, _SCRIPT, cuts=cuts, total_duration_sec=13.0)
    # 失敗 take 的字被丟棄，不會出現重複的「第一個觀念」cue
    assert srt.count("第一個觀念") == 1
    assert "蛋白質" in srt
    # 乾淨 timeline：retake 原本 9.5s 開口，cut [~5.0, ~9.37] 移除後前移到 ~5s 附近
    last_block = srt.strip().split("\n\n")[-1]
    end_ts = last_block.splitlines()[1].split(" --> ")[1]
    h, m, rest = end_ts.split(":")
    s = int(rest.split(",")[0])
    total = int(h) * 3600 + int(m) * 60 + s
    assert total < 9  # 未重映射的話 retake 結束在 12.7s


def test_correct_srt_raises_on_empty_inputs() -> None:
    with pytest.raises(ValueError, match="無法對齊"):
        correct_srt([], _SCRIPT)
    with pytest.raises(ValueError, match="無法對齊"):
        correct_srt(_chars_as_words("你好", 0.0, 1.0), "。。。")


# ---------------------------------------------------------------------------
# remap_words_through_cuts
# ---------------------------------------------------------------------------


def test_remap_drops_cut_words_and_shifts_later_words() -> None:
    words = _chars_as_words("前段", 0.0, 1.0) + _chars_as_words("後段", 5.0, 6.0)
    cuts = [
        CutPoint(
            type="ripple-delete", start_sec=1.5, end_sec=4.5, reason="marker", confidence=1.0
        )
    ]
    out = remap_words_through_cuts(words, cuts, total_duration_sec=6.0)
    assert [w.text for w in out] == ["前", "段", "後", "段"]
    # 後段原 5.0 起，往前移 3.0s
    assert out[2].start == pytest.approx(2.0, abs=0.01)
