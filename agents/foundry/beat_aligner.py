"""Anchor → timing aligner (ADR-032 §6).

Primary path: deterministic substring search (str.find) on the normalized
flat text. On miss → raise AnchorNotFoundError (hard fail; LLM retry that
beat up to 3x; else escalate to human).

rapidfuzz is demoted to --diagnostic-fuzzy flag (lists candidates for
debug only, never modifies return value or rescues a failed match).

PR-2 implementation.
"""

from __future__ import annotations


class AnchorNotFoundError(Exception):
    """LLM anchor not found verbatim in normalized transcript.

    Raised by align_beat() when start_quote or end_quote cannot be located.
    Carries the offending beat and the flat_text for debugging.
    """

    def __init__(self, beat: dict, flat_text: str):
        self.beat = beat
        self.flat_text = flat_text
        super().__init__(
            f"anchor not found in flat_text: "
            f"start_quote={beat.get('start_quote')!r} "
            f"end_quote={beat.get('end_quote')!r}"
        )


def align_beat(beat, flat_text, char_to_time):  # pragma: no cover — PR-2
    raise NotImplementedError("PR-2 — see issue #713")
