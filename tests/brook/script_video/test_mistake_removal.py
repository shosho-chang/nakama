"""Tests for agents.brook.script_video.mistake_removal.

Uses a synthetic WAV fixture containing impulse bursts at known positions:
  - Double-clap 1: 2.000 s + 2.150 s  (gap = 150 ms)
  - Double-clap 2: 5.000 s + 5.100 s  (gap = 100 ms)

Both gaps are < 300 ms → two markers expected.
"""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest

from agents.brook.script_video.mistake_removal import (
    _group_double_claps,
    _highpass_filter,
    detect_clap_markers,
)

_FIXTURE = Path(__file__).parents[2] / "fixtures" / "script_video" / "clap_marker_audio.wav"

# Expected marker mid-points and derived end_sec
_MARKER_1_SEC = (2.000 + 2.150) / 2  # 2.075
_MARKER_2_SEC = (5.000 + 5.100) / 2  # 5.050
_TOLERANCE_SEC = 0.050  # ±50 ms


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def fixture_audio() -> Path:
    assert _FIXTURE.exists(), (
        f"Fixture not found: {_FIXTURE}\n"
        'Run: python -c "import tests.fixtures.script_video as m" to regenerate.'
    )
    return _FIXTURE


# ---------------------------------------------------------------------------
# detect_clap_markers — integration
# ---------------------------------------------------------------------------


def test_detects_two_double_claps(fixture_audio: Path) -> None:
    cuts = detect_clap_markers(fixture_audio)
    assert len(cuts) == 2, f"Expected 2 cuts, got {len(cuts)}: {cuts}"


def test_cut1_end_within_tolerance(fixture_audio: Path) -> None:
    cuts = detect_clap_markers(fixture_audio)
    expected_end = _MARKER_1_SEC - 0.5  # ~1.575 s
    assert abs(cuts[0].end_sec - expected_end) <= _TOLERANCE_SEC, (
        f"Cut 1 end_sec={cuts[0].end_sec:.4f}s, expected {expected_end:.4f}s ±{_TOLERANCE_SEC}s"
    )


def test_cut2_end_within_tolerance(fixture_audio: Path) -> None:
    cuts = detect_clap_markers(fixture_audio)
    expected_end = _MARKER_2_SEC - 0.5  # ~4.550 s
    assert abs(cuts[1].end_sec - expected_end) <= _TOLERANCE_SEC, (
        f"Cut 2 end_sec={cuts[1].end_sec:.4f}s, expected {expected_end:.4f}s ±{_TOLERANCE_SEC}s"
    )


def test_cut_fields(fixture_audio: Path) -> None:
    cuts = detect_clap_markers(fixture_audio)
    for cut in cuts:
        assert cut.type == "ripple-delete"
        assert cut.reason == "marker"
        assert 0.0 <= cut.confidence <= 1.0
        assert cut.start_sec <= cut.end_sec


def test_no_claps_in_silence(tmp_path: Path) -> None:
    """Pure-silence audio → no cuts detected."""
    import wave

    silent_wav = tmp_path / "silence.wav"
    n_samples = 16000 * 3  # 3 seconds
    with wave.open(str(silent_wav), "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(16000)
        wf.writeframes(b"\x00" * n_samples * 2)

    cuts = detect_clap_markers(silent_wav)
    assert cuts == [], f"Expected no cuts in silence, got {cuts}"


def test_single_clap_not_counted(tmp_path: Path) -> None:
    """A lone impulse (not a double-clap) produces no cut."""
    import wave

    wav = tmp_path / "single.wav"
    sr = 16000
    n = sr * 4  # 4 seconds
    samples = np.zeros(n, dtype=np.int16)
    samples[int(2.0 * sr)] = 32767  # single impulse, no pair

    with wave.open(str(wav), "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(sr)
        wf.writeframes(samples.tobytes())

    cuts = detect_clap_markers(wav)
    assert cuts == [], f"Single impulse should not produce cuts, got {cuts}"


# ---------------------------------------------------------------------------
# Unit tests for internal helpers
# ---------------------------------------------------------------------------


def test_group_double_claps_pairs_close_peaks() -> None:
    peak_times = np.array([1.0, 1.1, 3.0, 3.2])
    markers = _group_double_claps(peak_times, max_gap_sec=0.3)
    assert len(markers) == 2
    assert abs(markers[0] - 1.05) < 0.001
    assert abs(markers[1] - 3.10) < 0.001


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
    assert rms_out < rms_in * 0.01, (
        f"HPF should suppress 100 Hz by >40 dB; rms_in={rms_in:.4f} rms_out={rms_out:.6f}"
    )


# ---------------------------------------------------------------------------
# Alignment fallback (β) — Slice 1 must raise so callers cannot silent no-op
# ---------------------------------------------------------------------------


def test_alignment_cuts_raises_not_implemented() -> None:
    from agents.brook.script_video.mistake_removal import detect_alignment_cuts

    with pytest.raises(NotImplementedError, match="Slice 2"):
        detect_alignment_cuts([{"word": "x", "start": 0.0, "end": 0.1}], ["x"])
