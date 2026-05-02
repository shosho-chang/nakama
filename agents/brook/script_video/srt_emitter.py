"""SRT emitter — produce Chinese subtitle file from cleaned timeline.

Phase 1 stub: requires WhisperX word-level timestamps (Slice 2+).
In Slice 1 the pipeline calls this and an empty SRT is written if
whisperx_words are unavailable.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Sequence

from agents.brook.script_video.cuts import CutPoint

logger = logging.getLogger(__name__)


def emit(
    output_path: Path,
    whisperx_words: Sequence[dict] | None,
    cuts: Sequence[CutPoint],
) -> None:
    """Write SRT to *output_path*.

    In Slice 1 this is a stub that writes an empty SRT with a comment header.
    Full implementation (Slice 2) maps WhisperX word timestamps through the
    cut timeline to produce per-cue SRT entries.
    """
    output_path.parent.mkdir(parents=True, exist_ok=True)

    if not whisperx_words:
        logger.warning(
            "srt_emitter: no WhisperX words provided — writing empty SRT stub "
            "(full implementation in Slice 2)"
        )
        output_path.write_text(
            "1\n00:00:00,000 --> 00:00:00,001\n[字幕將在 Slice 2 產生]\n",
            encoding="utf-8",
        )
        return

    # Slice 2+: map words through cleaned timeline
    raise NotImplementedError("SRT generation from WhisperX words: Slice 2")
