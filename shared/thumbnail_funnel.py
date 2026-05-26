"""Podcast thumbnail funnel (ADR-033 D8) — extract frame candidates from raw video.

Stages 1+2 of the funnel (the LLM-free part):

- :func:`stratified_sample` — ffmpeg-driven frame extraction at periodic
  intervals + audio-energy peaks (silencedetect inversion).
- :func:`rank_by_sharpness` — Laplacian-variance ranking, keep top N%.

Stage 3 (Sonnet 4.6 vision LLM evaluator) ships in a follow-up PR — see ADR-033
D8 §OQ3. PR4-B exposes top-N sharp candidates directly to 修修 via the Bridge
UI; 修修 manual-picks 2-3 host + 2-3 guest, then u2net runs only on the
confirmed picks (D8 Stage 5).

Why scipy.signal instead of OpenCV: scipy is already a project dep (clap-marker
detection — ADR-015). OpenCV would add ~50MB to the deployment for one convolve.

Why ``-ss`` BEFORE ``-i`` in :func:`_ffmpeg_extract`: fast-seek mode is much
quicker than frame-accurate seek and the funnel is tolerant of ±33ms drift
(both modes land within the same speech utterance).
"""

from __future__ import annotations

import asyncio
import logging
import math
import random
import re
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Literal

import numpy as np
from PIL import Image
from scipy.signal import convolve2d

logger = logging.getLogger(__name__)

# 4-connected discrete Laplacian kernel — same as cv2.Laplacian(ksize=1).
_LAPLACIAN_KERNEL = np.array(
    [[0.0, 1.0, 0.0], [1.0, -4.0, 1.0], [0.0, 1.0, 0.0]],
    dtype=np.float32,
)

# Where ffmpeg silencedetect prints to (stderr). Regex anchored to ffmpeg's
# canonical line format; tolerant of float precision differences.
_SILENCE_START_RE = re.compile(r"silence_start:\s*(-?[\d.]+)")
_SILENCE_END_RE = re.compile(r"silence_end:\s*(-?[\d.]+)")


class FunnelError(RuntimeError):
    """Raised when ffmpeg/ffprobe invocation or frame analysis fails."""


@dataclass(frozen=True)
class FrameCandidate:
    """One sampled frame with computed metrics.

    Attributes:
        path: absolute path to extracted PNG.
        timestamp_sec: when in the source video this frame was captured.
        sample_kind: ``"periodic"`` or ``"audio_peak"`` — informs 修修 in the UI
            which frames came from which sampling strategy (audio peaks tend to
            capture more emotive moments).
        sharpness: Laplacian variance. ``None`` until :func:`rank_by_sharpness`
            populates it.
    """

    path: Path
    timestamp_sec: float
    sample_kind: Literal["periodic", "audio_peak"]
    sharpness: float | None = None


async def _probe_duration(video_path: Path) -> float:
    """Return source video duration in seconds (via ``ffprobe``).

    Raises:
        FunnelError: ffprobe not on PATH, file unreadable, or duration not
            parseable.
    """
    argv = [
        "ffprobe",
        "-v",
        "error",
        "-show_entries",
        "format=duration",
        "-of",
        "default=noprint_wrappers=1:nokey=1",
        str(video_path),
    ]
    proc = await asyncio.create_subprocess_exec(
        *argv,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    stdout_bytes, stderr_bytes = await proc.communicate()
    if proc.returncode != 0:
        tail = stderr_bytes.decode(errors="replace")[-300:]
        raise FunnelError(f"ffprobe failed for {video_path.name}: {tail!r}")
    try:
        return float(stdout_bytes.decode().strip())
    except ValueError as exc:
        raise FunnelError(
            f"ffprobe returned non-float duration for {video_path.name}: {stdout_bytes!r}"
        ) from exc


async def _detect_audio_peaks(
    video_path: Path,
    *,
    threshold_db: float = -30.0,
    min_silence_sec: float = 0.5,
    min_speech_sec: float = 1.5,
) -> list[float]:
    """Find speech-segment midpoints via ``ffmpeg silencedetect``.

    Inverts ``silencedetect`` output (start/end pairs of silences) into a list
    of speech segments, returns the midpoint of each speech segment longer than
    ``min_speech_sec``.

    For a typical podcast recording with dense speech this yields ~5-20 peak
    timestamps — sparse enough to keep the periodic+peak combined sample
    list under the ``max_frames`` cap.

    Args:
        video_path: source video.
        threshold_db: audio level below which is considered silence. Default
            -30dB matches ffmpeg's documentation example.
        min_silence_sec: minimum silence duration to register a gap.
        min_speech_sec: minimum speech segment length to count as a "peak"
            (very short utterances are likely fillers, not emotive moments).

    Returns:
        Sorted list of timestamps (sec). Empty list if no peaks found.

    Raises:
        FunnelError: ffmpeg failure.
    """
    argv = [
        "ffmpeg",
        "-i",
        str(video_path),
        "-af",
        f"silencedetect=noise={threshold_db}dB:d={min_silence_sec}",
        "-f",
        "null",
        "-",
    ]
    proc = await asyncio.create_subprocess_exec(
        *argv,
        stdout=asyncio.subprocess.DEVNULL,
        stderr=asyncio.subprocess.PIPE,
    )
    _, stderr_bytes = await proc.communicate()
    if proc.returncode != 0:
        tail = stderr_bytes.decode(errors="replace")[-300:]
        raise FunnelError(f"silencedetect failed for {video_path.name}: {tail!r}")

    text = stderr_bytes.decode(errors="replace")
    starts = [float(m.group(1)) for m in _SILENCE_START_RE.finditer(text)]
    ends = [float(m.group(1)) for m in _SILENCE_END_RE.finditer(text)]

    # Speech segments live between silence_end and the next silence_start.
    # If the file starts with speech (no leading silence), prepend 0.0;
    # if it ends with speech, the final silence_end has no matching start
    # — handled by truncating to min(len) and letting the caller's max_frames
    # cap absorb anything missed.
    speech_starts = [0.0] + ends
    speech_ends = starts
    peaks: list[float] = []
    for s, e in zip(speech_starts, speech_ends, strict=False):
        if e - s >= min_speech_sec:
            peaks.append((s + e) / 2.0)
    return sorted(peaks)


async def _ffmpeg_extract(
    video_path: Path,
    timestamp_sec: float,
    out_path: Path,
) -> None:
    """Extract a single PNG frame at ``timestamp_sec``.

    Uses fast-seek (``-ss`` before ``-i``) — ~10× faster than frame-accurate
    seek and acceptable for thumbnail sampling.
    """
    argv = [
        "ffmpeg",
        "-y",  # overwrite existing
        "-ss",
        f"{timestamp_sec:.3f}",
        "-i",
        str(video_path),
        "-frames:v",
        "1",
        "-q:v",
        "2",
        str(out_path),
    ]
    proc = await asyncio.create_subprocess_exec(
        *argv,
        stdout=asyncio.subprocess.DEVNULL,
        stderr=asyncio.subprocess.PIPE,
    )
    _, stderr_bytes = await proc.communicate()
    if proc.returncode != 0:
        tail = stderr_bytes.decode(errors="replace")[-300:]
        raise FunnelError(
            f"ffmpeg extract failed at t={timestamp_sec:.3f}s ({video_path.name}): {tail!r}"
        )


def _laplacian_variance(image_path: Path) -> float:
    """Variance of Laplacian — high = sharp edges = sharp image.

    Standard blur-detection metric (Pech-Pacheco 2000). Operates on greyscale
    (PIL ``.convert('L')``) so colour saturation doesn't bias the score.
    """
    img = Image.open(image_path).convert("L")
    arr = np.asarray(img, dtype=np.float32)
    lap = convolve2d(arr, _LAPLACIAN_KERNEL, mode="valid")
    return float(lap.var())


async def stratified_sample(
    video_path: Path,
    out_dir: Path,
    *,
    periodic_interval: float = 10.0,
    audio_burst: bool = True,
    max_frames: int = 80,
    seed: int = 42,
    duration_sec: float | None = None,
) -> list[FrameCandidate]:
    """Stage 1 — extract candidate frames from a raw video.

    Baseline: 1 frame per ``periodic_interval`` seconds.
    Audio-energy hybrid (``audio_burst=True``): also sample 3 frames per audio
    peak (-0.3s, peak, +0.3s) to catch the lead-up + apex + recovery of each
    emotive moment.

    Deterministic given the same ``seed`` + same input video. If the union of
    periodic + peak samples exceeds ``max_frames``, downsample by seeded shuffle
    + truncate (preserves both sampling kinds proportionally on average).

    Args:
        video_path: source video.
        out_dir: directory to write extracted PNGs (created if missing).
        periodic_interval: seconds between baseline samples. Default 10s for
            conversation mode.
        audio_burst: enable audio-peak hybrid sampling.
        max_frames: hard cap on total extracted frames.
        seed: deterministic shuffling when capping.
        duration_sec: optional pre-computed duration; if None, ffprobe runs.

    Returns:
        List of :class:`FrameCandidate` ordered by ``timestamp_sec``.

    Raises:
        FunnelError: ffprobe / ffmpeg failure or no frames extractable.
    """
    if duration_sec is None:
        duration_sec = await _probe_duration(video_path)
    if duration_sec <= periodic_interval:
        # Video too short for stratified sampling; treat as one-shot.
        timestamps: list[tuple[float, str]] = [(duration_sec / 2.0, "periodic")]
    else:
        n_periodic = math.floor(duration_sec / periodic_interval)
        periodic_ts = [(i + 1) * periodic_interval for i in range(n_periodic)]
        timestamps = [(t, "periodic") for t in periodic_ts]

        if audio_burst:
            peaks = await _detect_audio_peaks(video_path)
            for p in peaks:
                for delta in (-0.3, 0.0, 0.3):
                    t = p + delta
                    if 0.0 <= t <= duration_sec:
                        timestamps.append((t, "audio_peak"))

    # Dedupe by (rounded ts, kind) so two peaks within 1 frame don't double-extract.
    seen: dict[tuple[int, str], tuple[float, str]] = {}
    for t, kind in timestamps:
        key = (round(t * 10), kind)  # 0.1s bucket
        if key not in seen:
            seen[key] = (t, kind)
    deduped = sorted(seen.values(), key=lambda x: x[0])

    if len(deduped) > max_frames:
        rng = random.Random(seed)
        idx = list(range(len(deduped)))
        rng.shuffle(idx)
        kept_idx = sorted(idx[:max_frames])
        deduped = [deduped[i] for i in kept_idx]

    out_dir.mkdir(parents=True, exist_ok=True)
    candidates: list[FrameCandidate] = []
    for i, (ts, kind) in enumerate(deduped):
        png_path = out_dir / f"frame_{i:03d}.png"
        await _ffmpeg_extract(video_path, ts, png_path)
        candidates.append(FrameCandidate(path=png_path, timestamp_sec=ts, sample_kind=kind))

    if not candidates:
        raise FunnelError(
            f"no frames extracted from {video_path.name} "
            f"(duration={duration_sec:.1f}s, interval={periodic_interval}s)"
        )
    return candidates


def rank_by_sharpness(
    candidates: list[FrameCandidate],
    *,
    top_pct: float = 0.25,
    min_count: int = 5,
) -> list[FrameCandidate]:
    """Stage 2 — compute Laplacian variance per frame, keep the sharpest.

    Pure function: reads input PNGs, returns a new list (does not mutate input
    or move files on disk). Sharpness is populated on each returned candidate.

    Args:
        candidates: from :func:`stratified_sample`.
        top_pct: keep top fraction. ``0.25`` → ~25% sharpest.
        min_count: floor — keep at least this many even if top_pct yields fewer
            (small videos can produce <20 candidates).

    Returns:
        New list, sorted by sharpness desc, length
        ``max(min_count, ceil(len * top_pct))`` capped to ``len(candidates)``.
    """
    if not candidates:
        return []

    scored: list[FrameCandidate] = []
    for c in candidates:
        var = _laplacian_variance(c.path)
        scored.append(replace(c, sharpness=var))
    scored.sort(key=lambda c: c.sharpness or 0.0, reverse=True)

    keep = min(len(scored), max(min_count, math.ceil(len(scored) * top_pct)))
    return scored[:keep]


async def run(
    video_path: Path,
    out_dir: Path,
    *,
    mode: Literal["conversation", "expression_sample"] = "conversation",
    top_pct: float = 0.25,
    seed: int = 42,
) -> list[FrameCandidate]:
    """End-to-end Stages 1+2.

    ``mode="conversation"`` → periodic 10s + audio-energy hybrid, max 80 frames.
    ``mode="expression_sample"`` → 30-sec deliberate-takes video (ADR-033 D8a),
    dense 1s periodic sampling, audio peaks off (silence between expressions
    would generate noisy peaks), max 40 frames.
    """
    if mode == "conversation":
        candidates = await stratified_sample(
            video_path,
            out_dir,
            periodic_interval=10.0,
            audio_burst=True,
            max_frames=80,
            seed=seed,
        )
    else:
        candidates = await stratified_sample(
            video_path,
            out_dir,
            periodic_interval=1.0,
            audio_burst=False,
            max_frames=40,
            seed=seed,
        )
    return rank_by_sharpness(candidates, top_pct=top_pct)
