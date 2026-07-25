"""shared/audio_trim.py + scripts/run_audio_prep.py 的單元測試（零 ffmpeg 依賴）。"""

from __future__ import annotations

from pathlib import Path

import pytest

from scripts.run_audio_prep import find_source_audio
from shared.audio_trim import decide_trim, parse_silencedetect

# ── parse_silencedetect ──


def test_parse_typical_stderr():
    stderr = (
        "[silencedetect @ 0x1] silence_start: 0\n"
        "[silencedetect @ 0x1] silence_end: 4.51 | silence_duration: 4.51\n"
        "frame= 1000\n"
        "[silencedetect @ 0x1] silence_start: 3810.2\n"
        "[silencedetect @ 0x1] silence_end: 3818.7 | silence_duration: 8.5\n"
    )
    assert parse_silencedetect(stderr) == [(0.0, 4.51), (3810.2, 3818.7)]


def test_parse_eof_silence_without_end():
    stderr = (
        "[silencedetect @ 0x1] silence_start: 100.5\n"
        "[silencedetect @ 0x1] silence_end: 103.0 | silence_duration: 2.5\n"
        "[silencedetect @ 0x1] silence_start: 3815.0\n"
    )
    assert parse_silencedetect(stderr) == [(100.5, 103.0), (3815.0, None)]


def test_parse_negative_start():
    # ffmpeg 對檔案開頭的靜音偶爾回報 silence_start: -0.00xxx
    stderr = (
        "[silencedetect @ 0x1] silence_start: -0.007\n"
        "[silencedetect @ 0x1] silence_end: 2.0 | silence_duration: 2.0\n"
    )
    assert parse_silencedetect(stderr) == [(-0.007, 2.0)]


def test_parse_empty():
    assert parse_silencedetect("") == []


# ── decide_trim ──


def test_decide_head_and_tail():
    spans = [(0.0, 5.0), (1000.0, 1002.0), (3810.0, 3818.0)]
    start, end = decide_trim(spans, 3818.5, padding=0.25)
    assert start == pytest.approx(4.75)
    assert end == pytest.approx(3810.25)


def test_decide_head_only():
    spans = [(0.0, 5.0), (1000.0, 1002.0)]
    start, end = decide_trim(spans, 3818.5, padding=0.25)
    assert start == pytest.approx(4.75)
    assert end == 3818.5


def test_decide_tail_eof_none():
    spans = [(3815.0, None)]
    start, end = decide_trim(spans, 3818.5, padding=0.25)
    assert start == 0.0
    assert end == pytest.approx(3815.25)


def test_decide_mid_silence_untouched():
    spans = [(100.0, 105.0), (2000.0, 2010.0)]
    assert decide_trim(spans, 3818.5) == (0.0, 3818.5)


def test_decide_no_spans():
    assert decide_trim([], 3818.5) == (0.0, 3818.5)


def test_decide_whole_file_silent_falls_back():
    # 整檔靜音：頭尾裁切點交叉 → 放棄裁切回傳原樣
    spans = [(0.0, None)]
    assert decide_trim(spans, 60.0) == (0.0, 60.0)


def test_decide_head_not_at_edge():
    # 第一段靜音離開頭太遠 → 不裁頭
    spans = [(10.0, 15.0)]
    start, end = decide_trim(spans, 3818.5)
    assert start == 0.0


# ── find_source_audio ──


def _make_episode(tmp_path: Path, audio_dir_name: str, files: list[str]) -> Path:
    ep = tmp_path / "20260723 測試"
    (ep / audio_dir_name).mkdir(parents=True)
    for name in files:
        (ep / audio_dir_name / name).write_bytes(b"RIFF")
    return ep


def test_find_source_case_insensitive_dir_and_file(tmp_path):
    ep = _make_episode(tmp_path, "Audio", ["1_COMBO-1.wav", "Live-Mix.wav"])
    assert find_source_audio(ep).name == "Live-Mix.wav"

    ep2 = _make_episode(tmp_path / "b", "audio", ["live-mix.wav"])
    assert find_source_audio(ep2).name == "live-mix.wav"


def test_find_source_missing_audio_dir(tmp_path):
    ep = tmp_path / "ep"
    ep.mkdir()
    with pytest.raises(FileNotFoundError, match="Audio"):
        find_source_audio(ep)


def test_find_source_missing_livemix_lists_wavs(tmp_path):
    ep = _make_episode(tmp_path, "Audio", ["1_COMBO-1.wav", "4_SOUNDS.wav"])
    with pytest.raises(FileNotFoundError, match="1_COMBO-1.wav"):
        find_source_audio(ep)
