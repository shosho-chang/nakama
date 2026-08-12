"""停頓圖：門檻自校準、時鐘映射、時鐘防呆。"""

from __future__ import annotations

import numpy as np
import pytest

from shared.pause_map import FRAME, PauseMap


def make_env(n_speech: int = 200, n_silence: int = 100) -> np.ndarray:
    """七成說話（0.1）三成靜音（0.0005）——底噪 P5 落在靜音上。"""
    return np.concatenate([np.full(n_speech, 0.1), np.full(n_silence, 0.0005)])


def test_thresholds_calibrate_from_noise_floor():
    pm = PauseMap(make_env())
    assert pm.noise_floor == pytest.approx(0.0005, rel=0.2)
    assert pm.quiet == pytest.approx(pm.noise_floor * 5)
    assert pm.noisy == pytest.approx(pm.noise_floor * 20)
    # 說話音量必須遠高於吵門檻，否則整份都會被判成可切
    assert pm.speech > pm.noisy * 5


def test_floor_takes_quietest_frame_in_window():
    env = np.full(200, 0.1)
    env[100] = 0.001  # 2.00s 處一格靜音
    pm = PauseMap(env)
    assert pm.floor(2.00) == pytest.approx(0.001)
    assert pm.floor(2.04, win=0.06) == pytest.approx(0.001)  # ±60ms 抓得到
    assert pm.floor(2.40, win=0.06) == pytest.approx(0.1)  # 太遠就抓不到


def test_is_quiet_and_is_noisy_are_complementary_ends():
    pm = PauseMap(make_env())
    env_len = len(pm)
    assert env_len == 300
    quiet_t = 205 * FRAME  # 落在靜音區
    speech_t = 50 * FRAME
    assert pm.is_quiet(quiet_t)
    assert not pm.is_noisy(quiet_t)
    assert pm.is_noisy(speech_t)
    assert not pm.is_quiet(speech_t)


def test_to_audio_maps_cue_clock_into_audio_clock():
    env = np.full(400, 0.1)
    env[300] = 0.0005  # 音檔 6.00s
    pm = PauseMap(env, to_audio=lambda t: t + 5.0)
    assert pm.floor(1.00) == pytest.approx(0.0005)  # cue 1.00s → 音檔 6.00s
    assert pm.floor(0.50) == pytest.approx(0.1)  # 沒套映射才會抓到靜音的位置


def test_sanity_check_passes_when_boundaries_sit_in_silence():
    env = np.full(1000, 0.1)
    bounds = [i * 0.5 for i in range(1, 40)]
    for t in bounds:
        env[int(t / FRAME)] = 0.0005
    pm = PauseMap(env)
    ratio = pm.sanity_check(bounds, [t + 0.25 for t in bounds])
    assert ratio < 0.1


def test_sanity_check_raises_when_clock_is_wrong():
    """對錯時鐘時切點與中點統計上無異——這是唯一能自動抓到 71 秒災難的訊號。"""
    env = np.full(1000, 0.1)
    bounds = [i * 0.5 for i in range(1, 40)]
    for t in bounds:
        env[int(t / FRAME)] = 0.0005
    pm = PauseMap(env, to_audio=lambda t: t + 3.7)  # 亂掉的映射
    with pytest.raises(ValueError, match="時鐘對不上"):
        pm.sanity_check(bounds, [t + 0.25 for t in bounds])


def test_sanity_check_skips_when_sample_too_small():
    pm = PauseMap(make_env())
    assert pm.sanity_check([1.0, 2.0], [1.5, 2.5]) == 0.0


def test_rejects_empty_envelope():
    with pytest.raises(ValueError):
        PauseMap(np.array([]))
