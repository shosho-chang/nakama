"""Anchor → timing aligner (ADR-032 §6).

Primary path: deterministic substring search (str.find) on the verified
flat text. On miss → raise AnchorNotFoundError (hard fail; LLM retry that
beat up to 3x; else escalate to human).

rapidfuzz is demoted to --diagnostic-fuzzy flag (lists candidates for
debug only, never modifies return value or rescues a failed match).
"""

from __future__ import annotations

import logging

logger = logging.getLogger(__name__)


class AnchorNotFoundError(Exception):
    """LLM anchor not found verbatim in Verified Projection text.

    Raised by align_beat() when start_quote or end_quote cannot be located.
    Carries the offending beat and the flat_text for debugging.
    """

    def __init__(self, beat: dict, flat_text: str) -> None:
        self.beat = beat
        self.flat_text = flat_text
        super().__init__(
            f"anchor not found in flat_text: "
            f"start_quote={beat.get('start_quote')!r} "
            f"end_quote={beat.get('end_quote')!r}"
        )


def _fuzzy_candidates(query: str, text: str, top_n: int = 5) -> list[tuple[str, float]]:
    """Return top-N fuzzy matches for query in text (diagnostic only)."""
    from rapidfuzz import fuzz  # noqa: PLC0415 — diagnostic import only

    window = len(query)
    if window == 0 or window > len(text):
        return []
    scored: list[tuple[str, float]] = []
    for i in range(len(text) - window + 1):
        candidate = text[i : i + window]
        score = fuzz.ratio(query, candidate)
        scored.append((candidate, score))
    scored.sort(key=lambda x: x[1], reverse=True)
    return scored[:top_n]


def _find_srt_line_ids(
    beat_start: int,
    beat_end: int,
    srt_line_id_to_char_range: dict[int, tuple[int, int]],
) -> list[int]:
    """Return sorted list of cue IDs whose char range overlaps [beat_start, beat_end)."""
    return sorted(
        cue_id
        for cue_id, (cue_start, cue_end) in srt_line_id_to_char_range.items()
        if cue_start < beat_end and cue_end > beat_start
    )


def align_beat(
    beat: dict,
    flat_text: str,
    char_to_time: dict[int, float],
    srt_line_id_to_char_range: dict[int, tuple[int, int]] | None = None,
    *,
    diagnostic_fuzzy: bool = False,
) -> dict:
    """Locate beat anchors in flat_text; return timing and SRT line coverage.

    Args:
        beat: beat dict with 'start_quote' and 'end_quote' keys.
        flat_text: flattened Verified Projection text (same bytes-derived text
            the planner saw).
        char_to_time: maps char index → cue start time (seconds).
        srt_line_id_to_char_range: maps cue.index → (start, end) in flat_text.
            If None, srt_line_ids in result will be [].
        diagnostic_fuzzy: if True, log fuzzy candidates on miss before raising.
            Never rescues the error — always raises AnchorNotFoundError on miss.

    Returns:
        {"timing": {"start": float, "duration": float}, "srt_line_ids": list[int]}

    Raises:
        AnchorNotFoundError: if start_quote or end_quote not found verbatim.
    """
    start_quote: str = beat.get("start_quote", "")
    end_quote: str = beat.get("end_quote", "")

    start_pos = flat_text.find(start_quote)
    if start_pos == -1:
        if diagnostic_fuzzy:
            candidates = _fuzzy_candidates(start_quote, flat_text)
            logger.debug(
                "diagnostic-fuzzy candidates for start_quote %r: %s",
                start_quote,
                candidates,
            )
        raise AnchorNotFoundError(beat, flat_text)

    end_pos = flat_text.find(end_quote)
    if end_pos == -1:
        if diagnostic_fuzzy:
            candidates = _fuzzy_candidates(end_quote, flat_text)
            logger.debug(
                "diagnostic-fuzzy candidates for end_quote %r: %s",
                end_quote,
                candidates,
            )
        raise AnchorNotFoundError(beat, flat_text)

    beat_char_start = start_pos
    beat_char_end = end_pos + len(end_quote)

    start_time = char_to_time.get(beat_char_start, 0.0)
    # End time from last char of end_quote
    end_time = char_to_time.get(beat_char_end - 1, start_time)

    srt_line_ids: list[int] = []
    if srt_line_id_to_char_range is not None:
        srt_line_ids = _find_srt_line_ids(beat_char_start, beat_char_end, srt_line_id_to_char_range)

    return {
        "timing": {
            "start": start_time,
            "duration": max(0.0, end_time - start_time),
        },
        "srt_line_ids": srt_line_ids,
    }
