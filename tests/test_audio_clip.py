"""shared.audio_clip 測試。

Fixture：用 ffmpeg 動態產生 10 秒 sine wave，避免依賴大型測試音檔。
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from shared.audio_clip import extract_clip, get_audio_duration


@pytest.fixture
def sine_wav(tmp_path: Path) -> Path:
    """產生 10 秒、44.1kHz、stereo sine wave 作為測試音檔。"""
    path = tmp_path / "sine.wav"
    subprocess.run(
        [
            "ffmpeg",
            "-y",
            "-loglevel",
            "error",
            "-f",
            "lavfi",
            "-i",
            "sine=frequency=440:duration=10:sample_rate=44100",
            "-ac",
            "2",
            str(path),
        ],
        check=True,
    )
    return path


def _wav_info(path: Path) -> dict:
    """用 ffprobe 讀 WAV 的 sample_rate / channels / duration。"""
    result = subprocess.run(
        [
            "ffprobe",
            "-v",
            "error",
            "-show_entries",
            "stream=sample_rate,channels:format=duration",
            "-of",
            "default=noprint_wrappers=1",
            str(path),
        ],
        capture_output=True,
        text=True,
        check=True,
    )
    info = {}
    for line in result.stdout.strip().splitlines():
        key, _, value = line.partition("=")
        info[key] = value
    return info


def test_extract_basic(sine_wav: Path, tmp_path: Path):
    """切 2-5 秒片段，驗證輸出檔為 mono + 16kHz + duration 正確（含 padding）。"""
    output = tmp_path / "clip.wav"
    result = extract_clip(sine_wav, 2.0, 5.0, output_path=output)

    assert result == output
    assert output.exists()

    info = _wav_info(output)
    assert int(info["sample_rate"]) == 16000
    assert int(info["channels"]) == 1
    # padding=1.0 預設 → duration 約 (5-2) + 2*1 = 5 秒
    assert abs(float(info["duration"]) - 5.0) < 0.1


def test_padding_zero(sine_wav: Path, tmp_path: Path):
    """padding=0 時 duration = end - start。"""
    output = tmp_path / "clip.wav"
    extract_clip(sine_wav, 2.0, 5.0, padding=0.0, output_path=output)

    info = _wav_info(output)
    assert abs(float(info["duration"]) - 3.0) < 0.1


def test_start_clamped_to_zero(sine_wav: Path, tmp_path: Path):
    """start - padding < 0 時 clamp 到 0，不會爆。"""
    output = tmp_path / "clip.wav"
    extract_clip(sine_wav, 0.5, 2.0, padding=1.0, output_path=output)

    info = _wav_info(output)
    # clip_start = max(0, 0.5-1) = 0, clip_end = 2+1 = 3 → duration ≈ 3
    assert abs(float(info["duration"]) - 3.0) < 0.1


def test_end_clamped_to_duration(sine_wav: Path, tmp_path: Path):
    """end + padding > audio duration 時 clamp 到音檔長度。"""
    output = tmp_path / "clip.wav"
    # 音檔 10 秒，end=9.5, padding=1 → clip_end clamp 到 10
    extract_clip(sine_wav, 8.0, 9.5, padding=1.0, output_path=output)

    info = _wav_info(output)
    # clip_start = 8-1 = 7, clip_end = min(10, 10.5) = 10 → duration ≈ 3
    assert abs(float(info["duration"]) - 3.0) < 0.1


def test_tempfile_when_no_output(sine_wav: Path):
    """不指定 output_path 時寫到 tempfile，回傳的路徑要存在。"""
    result = extract_clip(sine_wav, 2.0, 4.0)
    try:
        assert result.exists()
        assert result.suffix == ".wav"
        info = _wav_info(result)
        assert int(info["sample_rate"]) == 16000
        assert int(info["channels"]) == 1
    finally:
        result.unlink(missing_ok=True)


def test_missing_audio_raises(tmp_path: Path):
    """來源音檔不存在時 raise FileNotFoundError。"""
    with pytest.raises(FileNotFoundError):
        extract_clip(tmp_path / "does-not-exist.wav", 0.0, 1.0)


def test_invalid_range_raises(sine_wav: Path):
    """end_seconds <= start_seconds 時 raise ValueError。"""
    with pytest.raises(ValueError):
        extract_clip(sine_wav, 5.0, 5.0)
    with pytest.raises(ValueError):
        extract_clip(sine_wav, 5.0, 3.0)


def test_get_audio_duration(sine_wav: Path):
    """get_audio_duration 回傳正確的音檔長度。"""
    duration = get_audio_duration(sine_wav)
    assert abs(duration - 10.0) < 0.1


def test_custom_sample_rate(sine_wav: Path, tmp_path: Path):
    """可以指定非預設的 sample_rate。"""
    output = tmp_path / "clip.wav"
    extract_clip(sine_wav, 2.0, 4.0, output_path=output, sample_rate=22050)

    info = _wav_info(output)
    assert int(info["sample_rate"]) == 22050


def test_tempfile_cleaned_on_ffmpeg_failure(tmp_path: Path, monkeypatch):
    """ffmpeg 失敗時，內部建立的 tempfile 要被清掉（不洩漏）。"""
    # 先做一個有效的 sine wav，讓 get_audio_duration 先過
    wav = tmp_path / "good.wav"
    subprocess.run(
        [
            "ffmpeg",
            "-y",
            "-loglevel",
            "error",
            "-f",
            "lavfi",
            "-i",
            "sine=frequency=440:duration=5:sample_rate=44100",
            str(wav),
        ],
        check=True,
    )

    # 記錄 monkeypatch 前的 subprocess.run
    real_run = subprocess.run
    captured_tempfile: list[Path] = []

    def fake_run(cmd, *args, **kwargs):
        # 只攔截 ffmpeg 主切片呼叫（ffprobe 放行）
        if cmd and cmd[0] == "ffmpeg" and "-to" in cmd:
            # 從 cmd 尾端抓出 output 路徑
            captured_tempfile.append(Path(cmd[-1]))
            raise subprocess.CalledProcessError(1, cmd)
        return real_run(cmd, *args, **kwargs)

    monkeypatch.setattr(subprocess, "run", fake_run)

    with pytest.raises(subprocess.CalledProcessError):
        extract_clip(wav, 1.0, 2.0)  # output_path=None → 走 tempfile 路徑

    # tempfile 應該被清掉
    assert len(captured_tempfile) == 1
    assert not captured_tempfile[0].exists(), "ffmpeg 失敗後 tempfile 未被清除"
