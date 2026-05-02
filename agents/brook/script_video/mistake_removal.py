"""Mistake removal — detect double-clap markers in A-roll audio.

Primary path (α): audio spike detection
  - High-pass filter at 3 kHz to isolate clap burst frequencies
  - Short-time energy peak detection
  - Group pairs of peaks < ``max_clap_gap_sec`` apart → double-clap marker
  - Each marker → CutPoint covering (segment_start → marker_time − 0.5 s)

Alignment fallback (β): Slice 2+ — stub logs a warning if whisperx_words are
provided (Phase 1 does not perform Needleman-Wunsch alignment).
"""

from __future__ import annotations

import logging
import wave
from pathlib import Path
from typing import Sequence

import numpy as np

from agents.brook.script_video.cuts import CutPoint

logger = logging.getLogger(__name__)

# Seconds of audio before the clap to keep (post-marker buffer).
_MARKER_POST_BUFFER_SEC = 0.5

# Default lookback when WhisperX words are unavailable.
_DEFAULT_LOOKBACK_SEC = 3.0

# Minimum distance between two detected energy peaks (avoids double-detection
# of the same impulse across adjacent frames).
_MIN_PEAK_DISTANCE_SEC = 0.050


def detect_clap_markers(
    audio_path: Path,
    *,
    hp_cutoff_hz: float = 3000.0,
    threshold_ratio: float = 0.3,
    max_clap_gap_sec: float = 0.30,
    whisperx_words: Sequence[dict] | None = None,
) -> list[CutPoint]:
    """Detect double-clap markers and return the regions to ripple-delete.

    Parameters
    ----------
    audio_path:
        Path to a WAV file (mono or stereo; other formats require librosa).
    hp_cutoff_hz:
        High-pass filter cutoff in Hz.  Clap bursts are primarily > 3 kHz.
    threshold_ratio:
        Energy peaks below ``threshold_ratio × max_energy`` are ignored.
    max_clap_gap_sec:
        Maximum gap between two consecutive peaks that forms a double-clap.
    whisperx_words:
        Optional word-level ASR results.  If provided, the alignment fallback
        (β) is attempted; in Phase 1 this only logs a warning.
    """
    audio, sr = _load_wav(audio_path)

    if whisperx_words:
        logger.warning(
            "WhisperX alignment fallback (β) not implemented in Slice 1 — "
            "marker-primary (α) path used; WhisperX words ignored."
        )

    filtered = _highpass_filter(audio, sr, hp_cutoff_hz)
    peak_times = _detect_energy_peaks(filtered, sr, threshold_ratio)
    marker_times = _group_double_claps(peak_times, max_clap_gap_sec)

    cuts: list[CutPoint] = []
    for marker_time in marker_times:
        segment_start = max(0.0, marker_time - _DEFAULT_LOOKBACK_SEC)
        cut_end = max(segment_start, marker_time - _MARKER_POST_BUFFER_SEC)
        if cut_end <= segment_start:
            continue
        cuts.append(
            CutPoint(
                type="ripple-delete",
                start_sec=segment_start,
                end_sec=cut_end,
                reason="marker",
                confidence=0.9,
            )
        )

    logger.info(
        "Detected %d double-clap marker(s) → %d cut point(s) in %s",
        len(marker_times),
        len(cuts),
        audio_path.name,
    )
    return cuts


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _load_wav(path: Path) -> tuple[np.ndarray, int]:
    """Load a WAV file and return (float32 mono array, sample_rate)."""
    with wave.open(str(path), "rb") as wf:
        sr = wf.getframerate()
        n_channels = wf.getnchannels()
        sampwidth = wf.getsampwidth()
        n_frames = wf.getnframes()
        raw = wf.readframes(n_frames)

    # Decode raw bytes to int samples
    if sampwidth == 1:
        dtype = np.uint8
    elif sampwidth == 2:
        dtype = np.int16
    elif sampwidth == 4:
        dtype = np.int32
    else:
        raise ValueError(f"Unsupported sample width: {sampwidth} bytes")

    samples = np.frombuffer(raw, dtype=dtype)

    if n_channels > 1:
        samples = samples.reshape(-1, n_channels).mean(axis=1)

    # Normalise to float32 [-1, 1]
    max_val = float(np.iinfo(dtype).max) if dtype != np.uint8 else 128.0
    audio = samples.astype(np.float32)
    if dtype == np.uint8:
        audio = (audio - 128.0) / 128.0
    else:
        audio /= max_val

    return audio, sr


def _highpass_filter(audio: np.ndarray, sr: int, cutoff_hz: float) -> np.ndarray:
    """Apply a 4th-order Butterworth high-pass filter."""
    from scipy.signal import butter, sosfilt

    nyq = sr / 2.0
    # Clamp cutoff to 90% of Nyquist to avoid instability
    normalised = min(cutoff_hz, nyq * 0.90) / nyq
    sos = butter(4, normalised, btype="high", output="sos")
    return sosfilt(sos, audio).astype(np.float32)


def _detect_energy_peaks(
    audio: np.ndarray,
    sr: int,
    threshold_ratio: float,
) -> np.ndarray:
    """Return an array of peak times (seconds) sorted ascending."""
    from scipy.signal import find_peaks

    hop = max(1, int(sr * 0.005))  # 5 ms hop
    frame = max(1, int(sr * 0.010))  # 10 ms frame

    n_hops = max(1, (len(audio) - frame) // hop + 1)
    energy = np.empty(n_hops, dtype=np.float32)
    for i in range(n_hops):
        segment = audio[i * hop : i * hop + frame]
        energy[i] = float(np.sqrt(np.mean(segment**2))) if len(segment) else 0.0

    max_e = float(np.max(energy))
    if max_e == 0.0:
        return np.array([], dtype=np.float64)

    min_distance_frames = max(1, int(_MIN_PEAK_DISTANCE_SEC * sr / hop))
    peak_idx, _ = find_peaks(
        energy,
        height=threshold_ratio * max_e,
        distance=min_distance_frames,
    )
    return (peak_idx * hop / sr).astype(np.float64)


def _group_double_claps(
    peak_times: np.ndarray,
    max_gap_sec: float,
) -> list[float]:
    """Pair consecutive peaks separated by ≤ max_gap_sec into double-claps.

    Returns the midpoint time of each double-clap.
    """
    markers: list[float] = []
    i = 0
    while i < len(peak_times) - 1:
        gap = float(peak_times[i + 1]) - float(peak_times[i])
        if gap <= max_gap_sec:
            markers.append((float(peak_times[i]) + float(peak_times[i + 1])) / 2.0)
            i += 2
        else:
            i += 1
    return markers
