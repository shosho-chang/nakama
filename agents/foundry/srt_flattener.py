"""SRT → flat prose + char↔time index.

Parses an SRT file into SRTCue objects, then assembles a flat text string
using smart cue joining (see chinese_normalizer.smart_join_cues) while
building two mapping dicts:

- char_to_time: maps every character position in flat_text → cue start time (s)
- srt_line_id_to_char_range: maps cue.index → (start, end) slice in flat_text
"""

from __future__ import annotations

import re
from dataclasses import dataclass

_TS_RE = re.compile(r"(\d{2}):(\d{2}):(\d{2})[,.](\d{3})")

_SENTENCE_END = frozenset("。？！")
_SEP = "　"  # U+3000 IDEOGRAPHIC SPACE — prosody hint between non-sentence-final cues


@dataclass
class SRTCue:
    index: int
    start: float  # seconds
    end: float
    text: str


def _parse_ts(ts: str) -> float:
    m = _TS_RE.match(ts.strip())
    if not m:
        raise ValueError(f"invalid SRT timestamp: {ts!r}")
    h, mi, s, ms = int(m.group(1)), int(m.group(2)), int(m.group(3)), int(m.group(4))
    return h * 3600 + mi * 60 + s + ms / 1000


def parse_srt(srt_text: str) -> list[SRTCue]:
    """Parse raw SRT content into a list of SRTCue objects."""
    cues: list[SRTCue] = []
    for block in re.split(r"\n\n+", srt_text.strip()):
        lines = block.strip().splitlines()
        if len(lines) < 3:
            continue
        try:
            idx = int(lines[0].strip())
        except ValueError:
            continue
        if "-->" not in lines[1]:
            continue
        start_str, _, end_str = lines[1].partition("-->")
        cues.append(
            SRTCue(
                index=idx,
                start=_parse_ts(start_str),
                end=_parse_ts(end_str),
                text="\n".join(lines[2:]).strip(),
            )
        )
    return cues


def flatten_cues(
    cues: list[SRTCue],
) -> tuple[str, dict[int, float], dict[int, tuple[int, int]]]:
    """Build flat_text, char_to_time, and srt_line_id_to_char_range from cues.

    Returns:
        flat_text: cue texts joined by smart_join_cues logic
        char_to_time: char index in flat_text → cue.start (seconds)
        srt_line_id_to_char_range: cue.index → (start_char, end_char) in flat_text
    """
    char_to_time: dict[int, float] = {}
    srt_line_id_to_char_range: dict[int, tuple[int, int]] = {}

    flat_chars: list[str] = []
    pos = 0

    for i, cue in enumerate(cues):
        cue_text = cue.text.replace("\n", " ").strip()

        if i > 0 and flat_chars:
            last_non_sep = flat_chars[-1]
            if last_non_sep not in _SENTENCE_END:
                # Attribute separator to this cue's start time
                char_to_time[pos] = cue.start
                flat_chars.append(_SEP)
                pos += 1

        start_pos = pos
        for ch in cue_text:
            char_to_time[pos] = cue.start
            flat_chars.append(ch)
            pos += 1

        srt_line_id_to_char_range[cue.index] = (start_pos, pos)

    return "".join(flat_chars), char_to_time, srt_line_id_to_char_range


def flatten(
    srt_path: str,
) -> tuple[str, dict[int, float], dict[int, tuple[int, int]]]:
    """Parse SRT file and return (flat_text, char_to_time, srt_line_id_to_char_range)."""
    with open(srt_path, encoding="utf-8") as f:
        return flatten_cues(parse_srt(f.read()))
