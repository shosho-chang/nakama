"""ffmpeg `silencedetect` parser → (speaking_start, speaking_end) spans.

Phase 2 PR-C of ADR-038 (§D5). Pure-Python, clean-room port — the borrowing
reference (`course-video-manager` `app/services/silence-detection.ts`) is
concept-only; this module does not lift code.

Two layers:

1. `parse_silencedetect_stderr(stderr, *, total_duration_s)` — pure function,
   regex over the captured stderr; returns a list of speaking spans (the
   complement of detected silences within ``[0, total_duration_s]``).
2. `find_speaking_spans(mp4, *, noise_db, min_silence_s)` — shell-out wrapper
   that runs `ffmpeg -af silencedetect=...` and pipes stderr through the
   parser. Requires ffmpeg + ffprobe on PATH.

The parser is the only thing tests need; the wrapper is integration-only.
"""

from __future__ import annotations

import json
import logging
import re
import subprocess
from pathlib import Path

logger = logging.getLogger(__name__)

# ffmpeg silencedetect emits two relevant log lines per silence interval:
#   [silencedetect @ 0x...] silence_start: 12.345
#   [silencedetect @ 0x...] silence_end: 17.890 | silence_duration: 5.545
# Match defensively — version variance in spacing/prefix is common.
_SILENCE_START_RE = re.compile(r"silence_start:\s*(-?\d+(?:\.\d+)?)")
_SILENCE_END_RE = re.compile(
    r"silence_end:\s*(-?\d+(?:\.\d+)?)\s*\|?\s*silence_duration:\s*(-?\d+(?:\.\d+)?)"
)


def _parse_silence_intervals(
    stderr: str,
) -> tuple[list[tuple[float, float]], list[float]]:
    """Pair silence_start / silence_end lines in stderr emission order.

    Returns ``(intervals, raw_starts)`` where ``intervals`` is the matched
    ``(silence_start, silence_end)`` pairs and ``raw_starts`` is every
    ``silence_start`` value (used by the caller to detect a trailing unmatched
    start, i.e. silence extending past the clip end).
    """
    starts: list[float] = [float(m.group(1)) for m in _SILENCE_START_RE.finditer(stderr)]
    ends: list[float] = [float(m.group(1)) for m in _SILENCE_END_RE.finditer(stderr)]

    intervals: list[tuple[float, float]] = []
    for s, e in zip(starts, ends):
        if e < s:
            logger.warning("silencedetect: end %.3f < start %.3f, skipping", e, s)
            continue
        intervals.append((s, e))
    return intervals, starts


def parse_silencedetect_stderr(
    stderr: str,
    *,
    total_duration_s: float,
) -> list[tuple[float, float]]:
    """Parse ffmpeg silencedetect stderr → list of (speaking_start, speaking_end).

    Args:
        stderr: raw stderr captured from `ffmpeg -af silencedetect=... -f null -`.
        total_duration_s: clip duration in seconds (typically from ffprobe).

    Returns:
        List of speaking spans (silence complement), clipped to
        ``[0, total_duration_s]``. Empty list if the whole clip is silent or
        if total_duration_s <= 0. Adjacent or zero-length spans are dropped.
    """
    if total_duration_s <= 0:
        return []

    intervals, raw_starts = _parse_silence_intervals(stderr)

    # Trailing silence: ffmpeg emits a final silence_start with no matching
    # silence_end when the clip ends mid-silence. Detect this *only* by
    # comparing raw start/end line counts (one extra start = EOF silence) — do
    # NOT use len(intervals), which would mishandle malformed mid-stream pairs.
    raw_ends_count = len(_SILENCE_END_RE.findall(stderr))
    if len(raw_starts) == raw_ends_count + 1:
        trailing_start = raw_starts[-1]
        if trailing_start < total_duration_s:
            intervals.append((trailing_start, total_duration_s))

    # Build complement.
    speaking: list[tuple[float, float]] = []
    cursor = 0.0
    for s, e in intervals:
        s_clamped = max(0.0, min(s, total_duration_s))
        e_clamped = max(0.0, min(e, total_duration_s))
        if s_clamped > cursor:
            speaking.append((cursor, s_clamped))
        cursor = max(cursor, e_clamped)
    if cursor < total_duration_s:
        speaking.append((cursor, total_duration_s))

    # Drop zero-length / inverted spans (defensive — clamping can collapse them).
    return [(a, b) for a, b in speaking if b > a]


def _probe_duration_s(mp4: Path) -> float:
    """Return mp4 duration via ffprobe. Raises RuntimeError on failure."""
    result = subprocess.run(
        [
            "ffprobe",
            "-v",
            "quiet",
            "-print_format",
            "json",
            "-show_format",
            str(mp4),
        ],
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )
    if result.returncode != 0:
        raise RuntimeError(f"ffprobe failed for {mp4}: {result.stderr.strip()}")
    data = json.loads(result.stdout or "{}")
    dur = data.get("format", {}).get("duration")
    if dur is None:
        raise RuntimeError(f"ffprobe: no duration for {mp4}")
    return float(dur)


def find_speaking_spans(
    mp4: Path,
    *,
    noise_db: float = -30.0,
    min_silence_s: float = 0.7,
) -> list[tuple[float, float]]:
    """Detect speaking spans in an mp4 by running ffmpeg silencedetect.

    Args:
        mp4: input video file.
        noise_db: silence threshold in dB (silencedetect ``noise=`` param).
        min_silence_s: minimum silence length to detect, seconds (``d=`` param).

    Returns:
        List of (speaking_start, speaking_end) spans in seconds.

    Raises:
        FileNotFoundError: if mp4 does not exist.
        RuntimeError: if ffmpeg or ffprobe fails.
    """
    if not mp4.exists():
        raise FileNotFoundError(mp4)

    total = _probe_duration_s(mp4)

    af = f"silencedetect=noise={noise_db}dB:d={min_silence_s}"
    result = subprocess.run(
        ["ffmpeg", "-hide_banner", "-nostats", "-i", str(mp4), "-af", af, "-f", "null", "-"],
        capture_output=True,
        text=True,
        timeout=600,
        check=False,
    )
    # silencedetect logs to stderr regardless of returncode; only fail hard if
    # ffmpeg exited non-zero AND stderr contains no silencedetect lines.
    if result.returncode != 0 and "silencedetect" not in result.stderr:
        raise RuntimeError(f"ffmpeg failed for {mp4}: {result.stderr.strip()[:500]}")

    return parse_silencedetect_stderr(result.stderr, total_duration_s=total)
