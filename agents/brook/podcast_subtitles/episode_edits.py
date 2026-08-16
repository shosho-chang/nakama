"""Typed, audio-backed episode edits applied before semantic segmentation.

These edits are deliberately separate from generic ASR/reference rules.  Each
edit binds an exact text span, a normalized-audio interval, and explicit
evidence notes, so a stale base transcript fails instead of being silently
rewritten.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from typing import Sequence

from .accurate_correction import AccurateCorrectionResult, CorrectedTimedToken


@dataclass(frozen=True, slots=True)
class EpisodeTranscriptEdit:
    id: str
    start_ms: int
    end_ms: int
    current: str
    replacement: str
    evidence: str
    confidence: str = "confirmed"

    def __post_init__(self) -> None:
        if not self.id.strip() or not self.current.strip() or not self.replacement.strip():
            raise ValueError("episode edit requires id and non-empty exact text")
        if self.end_ms <= self.start_ms:
            raise ValueError("episode edit interval must be positive")
        if self.confidence not in {"confirmed", "high"}:
            raise ValueError("episode edit confidence must be confirmed or high")


def _replace_token_span(
    tokens: list[CorrectedTimedToken],
    *,
    first: int,
    last: int,
    replacement: str,
) -> None:
    source = tokens[first : last + 1]
    start_ms = source[0].start_ms
    end_ms = source[-1].end_ms
    duration = max(1, end_ms - start_ms)
    source_ids = tuple(item for token in source for item in token.source_primary_token_ids)
    recognition_refs = tuple(
        dict.fromkeys(item for token in source for item in token.recognition_refs)
    )
    confidence_values = [token.confidence for token in source if token.confidence is not None]
    confidence = min(confidence_values) if confidence_values else None
    speaker_values = {token.speaker for token in source}
    speaker = next(iter(speaker_values)) if len(speaker_values) == 1 else None
    produced: list[CorrectedTimedToken] = []
    pending_leading_whitespace = ""
    for index, character in enumerate(replacement):
        char_start = start_ms + round(duration * index / len(replacement))
        char_end = start_ms + round(duration * (index + 1) / len(replacement))
        if char_end <= char_start:
            char_end = char_start + 1
        if character.isspace():
            if produced:
                produced[-1] = replace(
                    produced[-1],
                    text=produced[-1].text + character,
                    end_ms=char_end,
                )
            else:
                pending_leading_whitespace += character
            continue
        produced.append(
            CorrectedTimedToken(
                id="episode-edit-pending",
                text=pending_leading_whitespace + character,
                start_ms=start_ms if pending_leading_whitespace else char_start,
                end_ms=char_end,
                confidence=confidence,
                speaker=speaker,
                source_primary_token_ids=source_ids,
                recognition_refs=recognition_refs,
            )
        )
        pending_leading_whitespace = ""
    tokens[first : last + 1] = produced


def apply_episode_transcript_edits(
    result: AccurateCorrectionResult,
    edits: Sequence[EpisodeTranscriptEdit],
) -> AccurateCorrectionResult:
    """Apply non-overlapping, exact, time-bounded edits to corrected tokens."""

    tokens = list(result.tokens)
    unresolved = list(result.unresolved)
    ordered = sorted(edits, key=lambda item: (item.start_ms, item.end_ms, item.id))
    for edit in ordered:
        candidates: list[tuple[int, int]] = []
        for first, token in enumerate(tokens):
            if token.end_ms < edit.start_ms or token.start_ms > edit.end_ms:
                continue
            text = ""
            for last in range(first, len(tokens)):
                candidate = tokens[last]
                if candidate.start_ms > edit.end_ms:
                    break
                text += candidate.text
                if text == edit.current:
                    candidates.append((first, last))
                    break
                if not edit.current.startswith(text):
                    break
        bounded = [
            (first, last)
            for first, last in candidates
            if tokens[first].end_ms >= edit.start_ms and tokens[last].start_ms <= edit.end_ms
        ]
        if len(bounded) != 1:
            raise ValueError(
                f"episode edit {edit.id} exact target count is {len(bounded)}, expected 1"
            )
        first, last = bounded[0]
        target_start = sum(len(token.text) for token in tokens[:first])
        target_end = target_start + sum(len(token.text) for token in tokens[first : last + 1])
        delta = len(edit.replacement) - (target_end - target_start)
        shifted_unresolved = []
        for decision in unresolved:
            if decision.target_start_char is None or decision.target_end_char is None:
                shifted_unresolved.append(decision)
                continue
            if decision.target_end_char <= target_start:
                shifted_unresolved.append(decision)
                continue
            if decision.target_start_char >= target_end:
                shifted_unresolved.append(
                    replace(
                        decision,
                        target_start_char=decision.target_start_char + delta,
                        target_end_char=decision.target_end_char + delta,
                    )
                )
                continue
            # The bounded audio audit supersedes any machine disagreement that
            # touches the exact edited span.
        unresolved = shifted_unresolved
        _replace_token_span(tokens, first=first, last=last, replacement=edit.replacement)

    tokens = [replace(token, id=f"corrected-{index:08d}") for index, token in enumerate(tokens)]
    updated_text = "".join(token.text for token in tokens)
    for decision in unresolved:
        if decision.target_start_char is None or decision.target_end_char is None:
            continue
        if updated_text[decision.target_start_char : decision.target_end_char] != decision.current:
            raise ValueError("episode edit left a stale unresolved correction target")
    return replace(
        result,
        status="completed_with_review" if unresolved else "completed",
        tokens=tuple(tokens),
        unresolved=tuple(unresolved),
    )


__all__ = ["EpisodeTranscriptEdit", "apply_episode_transcript_edits"]
