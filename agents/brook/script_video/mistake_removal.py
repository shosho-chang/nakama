"""Mistake removal — detect double-clap markers in A-roll audio.

Workflow assumption (Slice 1 / 修修 2026-05-02):
  - Script is pre-segmented; each segment is recorded in one go.
  - On a failed take, 修修 immediately claps twice. The clap pair signals
    "discard the failed take that just happened, and the silence that
    follows while I gather thoughts before the retake".
  - Successful takes have NO cue — they simply continue into the next
    segment. The algorithm only marks failures.

Cut semantics for each double-clap marker M:
  cut.start = voice onset BEFORE M (start of the failed take's speech)
  cut.end   = voice onset AFTER M  − 4 frames (lead-in buffer for the retake)

Consecutive close markers (e.g. retake also failed → another clap pair)
produce overlapping cuts that ``fcpxml_emitter._build_segments`` merges
into one continuous ripple-delete spanning the whole failed sequence.

Primary path (α): audio spike detection
  - High-pass filter at 3 kHz to isolate clap burst frequencies
  - Short-time energy peak detection
  - Group pairs of peaks < ``max_clap_gap_sec`` apart → double-clap marker

Alignment fallback (β): Slice 2+ — `detect_alignment_cuts()` will run
Needleman-Wunsch over WhisperX words ↔ script words and standalone-emit
review markers (ADR-015 §Q3). Phase 1 only ships α; β attempt raises so
callers cannot silently lose the WhisperX input.
"""

from __future__ import annotations

import dataclasses
import logging
import wave
from pathlib import Path
from typing import Sequence

import numpy as np

from agents.brook.script_video.cuts import CutPoint

logger = logging.getLogger(__name__)

# Minimum distance between two detected energy peaks (avoids double-detection
# of the same impulse across adjacent frames).
_MIN_PEAK_DISTANCE_SEC = 0.050

# Voice activity detection (RMS energy threshold scan).
_VOICE_RMS_THRESHOLD = 0.005  # ~ −46 dBFS; tuned for typical clean recording
_VOICE_FRAME_SEC = 0.030  # 30 ms RMS window
_VOICE_HOP_SEC = 0.010  # 10 ms hop
# Voice must persist at least this many windows to count as onset (rejects
# clap impulse residue + filter ringing, which decay within 1–2 windows).
_VOICE_MIN_CONSECUTIVE_WINDOWS = 3
# Low-pass cutoff for VAD: voice fundamentals + first 2 formants live below
# 3 kHz; clap energy is mostly above. LPF strips clap content from the
# voice-detection signal even when search starts inside clap residue.
_VOICE_LPF_CUTOFF_HZ = 3000.0


@dataclasses.dataclass(frozen=True)
class _MarkerBounds:
    """Time boundaries of a single double-clap marker."""

    midpoint_sec: float
    clap_start_sec: float
    clap_end_sec: float


def detect_clap_markers(
    audio_path: Path,
    *,
    fps: int = 30,
    hp_cutoff_hz: float = 3000.0,
    threshold_ratio: float = 0.3,
    max_clap_gap_sec: float = 0.30,
    voice_rms_threshold: float = _VOICE_RMS_THRESHOLD,
    lead_in_frames: int = 4,
) -> list[CutPoint]:
    """Detect double-clap markers and return regions to ripple-delete.

    See module docstring for the workflow assumption and cut semantics.

    Parameters
    ----------
    audio_path:
        Path to a WAV file (mono or stereo).
    fps:
        Timeline frame rate; used to convert ``lead_in_frames`` to seconds.
    hp_cutoff_hz:
        High-pass filter cutoff in Hz. Clap bursts are primarily > 3 kHz.
    threshold_ratio:
        Energy peaks below ``threshold_ratio × max_energy`` are ignored.
    max_clap_gap_sec:
        Maximum gap between two consecutive peaks that forms a double-clap.
    voice_rms_threshold:
        RMS amplitude (linear, after high-pass filter removal) above which
        a window is considered voiced. Tune lower for quieter recordings.
    lead_in_frames:
        Frames of silence to keep before the retake's voice onset. Default
        4 frames @ 30 fps ≈ 133 ms.
    """
    audio, sr = _load_wav(audio_path)
    filtered_for_peaks = _highpass_filter(audio, sr, hp_cutoff_hz)
    peak_times = _detect_energy_peaks(filtered_for_peaks, sr, threshold_ratio)
    markers = _group_double_claps(peak_times, max_clap_gap_sec)

    audio_duration_sec = len(audio) / sr
    lead_in_sec = lead_in_frames / fps

    cuts: list[CutPoint] = []
    for i, M in enumerate(markers):
        # Search window for the failed take's voice onset:
        # from the previous marker's clap_end (or 0) up to this marker's clap_start.
        search_start = markers[i - 1].clap_end_sec if i > 0 else 0.0
        voice_before = _find_voice_onset(
            audio, sr, search_start, M.clap_start_sec, voice_rms_threshold
        )
        cut_start = voice_before if voice_before is not None else search_start

        # Search window for the retake's voice onset:
        # from this marker's clap_end up to the next marker's clap_start (or end).
        search_end = markers[i + 1].clap_start_sec if i + 1 < len(markers) else audio_duration_sec
        voice_after = _find_voice_onset(audio, sr, M.clap_end_sec, search_end, voice_rms_threshold)
        cut_end_voice = voice_after if voice_after is not None else search_end
        cut_end = max(M.clap_end_sec, cut_end_voice - lead_in_sec)

        if cut_start < cut_end:
            cuts.append(
                CutPoint(
                    type="ripple-delete",
                    start_sec=cut_start,
                    end_sec=cut_end,
                    reason="marker",
                    confidence=0.9,
                )
            )

    logger.info(
        "Detected %d double-clap marker(s) → %d cut point(s) in %s",
        len(markers),
        len(cuts),
        audio_path.name,
    )
    return cuts


def detect_alignment_cuts(
    whisperx_words: Sequence[dict],
    script_words: Sequence[str],
) -> list[CutPoint]:
    """Find unmarked retakes via WhisperX ↔ script alignment (β fallback).

    Slice 2+ — runs Needleman-Wunsch DP over the two word streams and emits
    review markers for repeated-then-corrected segments (ADR-015 §Q3 spec).
    """
    raise NotImplementedError("Alignment fallback (β): Slice 2 — see ADR-015 §Q3")


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
) -> list[_MarkerBounds]:
    """Pair consecutive peaks separated by ≤ max_gap_sec into double-claps.

    Returns a list of _MarkerBounds with midpoint, clap_start, clap_end.
    """
    markers: list[_MarkerBounds] = []
    i = 0
    while i < len(peak_times) - 1:
        t1 = float(peak_times[i])
        t2 = float(peak_times[i + 1])
        if t2 - t1 <= max_gap_sec:
            markers.append(
                _MarkerBounds(
                    midpoint_sec=(t1 + t2) / 2.0,
                    clap_start_sec=t1,
                    clap_end_sec=t2,
                )
            )
            i += 2
        else:
            i += 1
    return markers


def _lowpass_filter(audio: np.ndarray, sr: int, cutoff_hz: float) -> np.ndarray:
    """Apply a 4th-order Butterworth low-pass filter."""
    from scipy.signal import butter, sosfilt

    nyq = sr / 2.0
    normalised = min(cutoff_hz, nyq * 0.90) / nyq
    sos = butter(4, normalised, btype="low", output="sos")
    return sosfilt(sos, audio).astype(np.float32)


def _find_voice_onset(
    audio: np.ndarray,
    sr: int,
    start_sec: float,
    end_sec: float,
    rms_threshold: float,
) -> float | None:
    """Find first sustained voice activity in [start_sec, end_sec].

    Voice detection runs on a low-pass-filtered signal (LPF at
    ``_VOICE_LPF_CUTOFF_HZ``) so clap energy in the high-frequency band is
    suppressed. A window counts as voiced when its RMS ≥ ``rms_threshold``;
    an onset is declared only after ``_VOICE_MIN_CONSECUTIVE_WINDOWS`` such
    windows in a row, rejecting brief impulse residue and filter ringing.

    Returns the seconds offset of the first window in the qualifying run,
    or None if no sustained voice is found in the search range.
    """
    if end_sec <= start_sec:
        return None

    voice_signal = _lowpass_filter(audio, sr, _VOICE_LPF_CUTOFF_HZ)

    hop = max(1, int(sr * _VOICE_HOP_SEC))
    frame = max(1, int(sr * _VOICE_FRAME_SEC))
    start_idx = max(0, int(start_sec * sr))
    end_idx = min(len(voice_signal), int(end_sec * sr))

    consec = 0
    onset_idx: int | None = None
    pos = start_idx
    while pos + frame <= end_idx:
        segment = voice_signal[pos : pos + frame]
        rms = float(np.sqrt(np.mean(segment.astype(np.float32) ** 2)))
        if rms >= rms_threshold:
            if onset_idx is None:
                onset_idx = pos
            consec += 1
            if consec >= _VOICE_MIN_CONSECUTIVE_WINDOWS:
                assert onset_idx is not None
                return onset_idx / sr
        else:
            consec = 0
            onset_idx = None
        pos += hop
    return None
