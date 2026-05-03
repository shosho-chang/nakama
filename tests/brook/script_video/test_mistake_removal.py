"""Tests for agents.brook.script_video.mistake_removal.

Cut semantics (Slice 1, 修修 workflow 2026-05-02):
  cut.start = voice onset BEFORE marker (start of failed take's speech)
  cut.end   = voice onset AFTER  marker − 4 frames (lead-in buffer)

The static fixture (clap_marker_audio.wav) is silence + claps only, no voice.
For voice-aware assertions, tests synthesize their own audio in-memory.
"""

from __future__ import annotations

import wave
from pathlib import Path

import numpy as np
import pytest

from agents.brook.script_video.mistake_removal import (
    _find_voice_onset,
    _group_double_claps,
    _highpass_filter,
    _MarkerBounds,
    detect_clap_markers,
)

_FIXTURE = Path(__file__).parents[2] / "fixtures" / "script_video" / "clap_marker_audio.wav"

# Fixture marker positions (peak times, gap < 300 ms → grouped)
_M1_PEAK1, _M1_PEAK2 = 2.000, 2.150
_M2_PEAK1, _M2_PEAK2 = 5.000, 5.100
_FIXTURE_DURATION_SEC = 8.0  # WAV is exactly 8s

# 4 frames @ 30 fps = 0.13333... s
_LEAD_IN_SEC_30FPS = 4 / 30
_TOLERANCE_SEC = 0.050  # ±50 ms for peak detection drift


# ---------------------------------------------------------------------------
# Audio synthesis helpers (used by voice-aware tests)
# ---------------------------------------------------------------------------


def _write_wav(path: Path, samples: np.ndarray, sr: int = 16000) -> None:
    """Write a float32 [-1, 1] mono numpy array to a 16-bit PCM WAV file."""
    int16 = np.clip(samples * 32767, -32768, 32767).astype(np.int16)
    with wave.open(str(path), "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(sr)
        wf.writeframes(int16.tobytes())


def _impulse(sr: int, duration_sec: float, time_sec: float) -> np.ndarray:
    """Return a buffer of length ``duration_sec`` with a single impulse at ``time_sec``."""
    n = int(duration_sec * sr)
    out = np.zeros(n, dtype=np.float32)
    idx = int(time_sec * sr)
    if 0 <= idx < n:
        out[idx] = 1.0
    return out


def _voice_burst(sr: int, duration_sec: float, start_sec: float, length_sec: float) -> np.ndarray:
    """Synthesize voice as a 200 Hz sine over [start_sec, start_sec+length_sec]."""
    n = int(duration_sec * sr)
    out = np.zeros(n, dtype=np.float32)
    s = int(start_sec * sr)
    e = min(n, int((start_sec + length_sec) * sr))
    if s < e:
        t = np.arange(e - s, dtype=np.float32) / sr
        out[s:e] = 0.3 * np.sin(2 * np.pi * 200 * t)
    return out


# ---------------------------------------------------------------------------
# detect_clap_markers — fixture-based
# ---------------------------------------------------------------------------


@pytest.fixture
def fixture_audio() -> Path:
    assert _FIXTURE.exists(), f"Fixture not found: {_FIXTURE}"
    return _FIXTURE


def test_detects_two_double_claps(fixture_audio: Path) -> None:
    cuts = detect_clap_markers(fixture_audio)
    assert len(cuts) == 2, f"Expected 2 cuts, got {len(cuts)}: {cuts}"


def test_cut_fields(fixture_audio: Path) -> None:
    cuts = detect_clap_markers(fixture_audio)
    for cut in cuts:
        assert cut.type == "ripple-delete"
        assert cut.reason == "marker"
        assert 0.0 <= cut.confidence <= 1.0
        assert cut.start_sec < cut.end_sec


def test_cut1_end_uses_next_marker_clap_start_minus_lead_in(fixture_audio: Path) -> None:
    """Silence-only fixture: cut₁ end ≈ M₂.clap_start − 4/30 s (no voice found)."""
    cuts = detect_clap_markers(fixture_audio)
    expected_end = _M2_PEAK1 - _LEAD_IN_SEC_30FPS
    assert abs(cuts[0].end_sec - expected_end) <= _TOLERANCE_SEC, (
        f"Cut₁ end_sec={cuts[0].end_sec:.4f}s, expected {expected_end:.4f}s"
    )


def test_cut2_end_uses_audio_end_minus_lead_in(fixture_audio: Path) -> None:
    """Silence-only fixture: last cut ends at audio_duration − 4/30 s."""
    cuts = detect_clap_markers(fixture_audio)
    expected_end = _FIXTURE_DURATION_SEC - _LEAD_IN_SEC_30FPS
    assert abs(cuts[1].end_sec - expected_end) <= _TOLERANCE_SEC, (
        f"Cut₂ end_sec={cuts[1].end_sec:.4f}s, expected {expected_end:.4f}s"
    )


def test_cut1_starts_at_zero_when_no_voice_before(fixture_audio: Path) -> None:
    """First marker, silence-only audio → cut₁ starts at 0 (audio start)."""
    cuts = detect_clap_markers(fixture_audio)
    assert abs(cuts[0].start_sec - 0.0) <= _TOLERANCE_SEC


def test_cut2_starts_at_prev_marker_end_when_no_voice_between(fixture_audio: Path) -> None:
    """Second marker, no voice between markers → cut₂ starts at M₁.clap_end."""
    cuts = detect_clap_markers(fixture_audio)
    # M₁ peaks at 2.000 + 2.150 → clap_end ≈ 2.150
    assert abs(cuts[1].start_sec - _M1_PEAK2) <= _TOLERANCE_SEC, (
        f"Cut₂ start_sec={cuts[1].start_sec:.4f}s, expected ~{_M1_PEAK2:.4f}s"
    )


# ---------------------------------------------------------------------------
# detect_clap_markers — voice-aware (synthesized audio)
# ---------------------------------------------------------------------------


def test_cut_starts_at_voice_onset_before_marker(tmp_path: Path) -> None:
    """Cut.start should equal the failed take's voice onset, not 0."""
    sr = 16000
    duration = 10.0  # seconds
    # Layout: silence 0-1s, voice (failed) 1-3s, claps at 4.0+4.1, silence 4.1-6, voice 6-9
    audio = _voice_burst(sr, duration, 1.0, 2.0) + _voice_burst(sr, duration, 6.0, 3.0)
    audio += _impulse(sr, duration, 4.0) + _impulse(sr, duration, 4.1)
    wav_path = tmp_path / "voice_clap.wav"
    _write_wav(wav_path, audio, sr=sr)

    cuts = detect_clap_markers(wav_path)
    assert len(cuts) == 1
    # Voice onset before marker should be detected near 1.0s
    assert abs(cuts[0].start_sec - 1.0) <= 0.10, (
        f"Cut start_sec={cuts[0].start_sec:.4f}s, expected ~1.0s (failed-take voice onset)"
    )


def test_cut_end_uses_voice_onset_after_marker_minus_lead_in(tmp_path: Path) -> None:
    """Cut.end should equal retake voice onset − 4 frames @ 30 fps."""
    sr = 16000
    duration = 10.0
    # Layout: voice (failed) 1-3s, claps at 4.0+4.1, silence 4.1-6, retake voice 6-9
    audio = _voice_burst(sr, duration, 1.0, 2.0) + _voice_burst(sr, duration, 6.0, 3.0)
    audio += _impulse(sr, duration, 4.0) + _impulse(sr, duration, 4.1)
    wav_path = tmp_path / "voice_clap.wav"
    _write_wav(wav_path, audio, sr=sr)

    cuts = detect_clap_markers(wav_path, fps=30, lead_in_frames=4)
    assert len(cuts) == 1
    expected_end = 6.0 - _LEAD_IN_SEC_30FPS
    assert abs(cuts[0].end_sec - expected_end) <= 0.10, (
        f"Cut end_sec={cuts[0].end_sec:.4f}s, expected ~{expected_end:.4f}s"
    )


def test_lead_in_frames_param_is_respected(tmp_path: Path) -> None:
    """Different lead_in_frames values change cut end accordingly."""
    sr = 16000
    duration = 10.0
    audio = _voice_burst(sr, duration, 6.0, 3.0)  # only retake voice
    audio += _impulse(sr, duration, 4.0) + _impulse(sr, duration, 4.1)
    wav_path = tmp_path / "lead_in.wav"
    _write_wav(wav_path, audio, sr=sr)

    cuts_4f = detect_clap_markers(wav_path, fps=30, lead_in_frames=4)
    cuts_8f = detect_clap_markers(wav_path, fps=30, lead_in_frames=8)
    assert len(cuts_4f) == 1 and len(cuts_8f) == 1
    # 8 frames @ 30 fps = 0.267 s; cut ends ~0.133 s earlier than the 4-frame variant
    assert cuts_8f[0].end_sec < cuts_4f[0].end_sec - 0.10


# ---------------------------------------------------------------------------
# Edge cases
# ---------------------------------------------------------------------------


def test_no_claps_in_silence(tmp_path: Path) -> None:
    """Pure-silence audio → no markers → no cuts."""
    silent_wav = tmp_path / "silence.wav"
    n_samples = 16000 * 3
    with wave.open(str(silent_wav), "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(16000)
        wf.writeframes(b"\x00" * n_samples * 2)

    cuts = detect_clap_markers(silent_wav)
    assert cuts == []


def test_single_clap_not_counted(tmp_path: Path) -> None:
    """A lone impulse (not a double-clap pair) → no marker → no cut."""
    wav = tmp_path / "single.wav"
    sr = 16000
    n = sr * 4
    samples = np.zeros(n, dtype=np.int16)
    samples[int(2.0 * sr)] = 32767  # single impulse, no pair

    with wave.open(str(wav), "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(sr)
        wf.writeframes(samples.tobytes())

    cuts = detect_clap_markers(wav)
    assert cuts == []


# ---------------------------------------------------------------------------
# _group_double_claps — returns _MarkerBounds (midpoint + clap_start + clap_end)
# ---------------------------------------------------------------------------


def test_group_double_claps_pairs_close_peaks() -> None:
    peak_times = np.array([1.0, 1.1, 3.0, 3.2])
    markers = _group_double_claps(peak_times, max_gap_sec=0.3)
    assert len(markers) == 2
    assert isinstance(markers[0], _MarkerBounds)
    assert markers[0].clap_start_sec == pytest.approx(1.0)
    assert markers[0].clap_end_sec == pytest.approx(1.1)
    assert markers[0].midpoint_sec == pytest.approx(1.05)
    assert markers[1].clap_start_sec == pytest.approx(3.0)
    assert markers[1].clap_end_sec == pytest.approx(3.2)


def test_group_double_claps_ignores_wide_gap() -> None:
    peak_times = np.array([1.0, 1.5])  # gap = 500 ms > 300 ms
    markers = _group_double_claps(peak_times, max_gap_sec=0.3)
    assert markers == []


def test_highpass_filter_attenuates_low_freq() -> None:
    """Low-frequency sine (100 Hz) should be attenuated by >3 kHz HPF."""
    sr = 16000
    t = np.linspace(0, 1.0, sr, endpoint=False, dtype=np.float32)
    sine_100hz = np.sin(2 * np.pi * 100 * t)
    filtered = _highpass_filter(sine_100hz, sr, 3000.0)
    rms_in = float(np.sqrt(np.mean(sine_100hz**2)))
    rms_out = float(np.sqrt(np.mean(filtered**2)))
    assert rms_out < rms_in * 0.01


# ---------------------------------------------------------------------------
# _find_voice_onset — direct unit tests
# ---------------------------------------------------------------------------


def test_find_voice_onset_returns_none_when_silent() -> None:
    sr = 16000
    audio = np.zeros(sr * 3, dtype=np.float32)
    assert _find_voice_onset(audio, sr, 0.0, 3.0, rms_threshold=0.005) is None


def test_find_voice_onset_finds_first_voiced_window() -> None:
    sr = 16000
    duration = 5.0
    audio = _voice_burst(sr, duration, 2.0, 1.5)  # voice 2.0–3.5s
    onset = _find_voice_onset(audio, sr, 0.0, 5.0, rms_threshold=0.005)
    assert onset is not None
    assert abs(onset - 2.0) <= 0.05


def test_find_voice_onset_respects_search_window() -> None:
    sr = 16000
    duration = 5.0
    audio = _voice_burst(sr, duration, 1.0, 0.5)  # voice 1.0–1.5s only
    # Searching after 2.0s should miss the burst → None
    assert _find_voice_onset(audio, sr, 2.0, 5.0, rms_threshold=0.005) is None


# ---------------------------------------------------------------------------
# Alignment fallback (β) — must raise so callers cannot silent no-op
# ---------------------------------------------------------------------------


def test_alignment_cuts_raises_not_implemented() -> None:
    from agents.brook.script_video.mistake_removal import detect_alignment_cuts

    with pytest.raises(NotImplementedError, match="Slice 2"):
        detect_alignment_cuts([{"word": "x", "start": 0.0, "end": 0.1}], ["x"])
