"""Deterministic least-context plans for per-span Reference retrieval.

This module is deliberately pure: it reads only the immutable Canonical
Transcript and a persisted policy snapshot.  It never calls a retriever and
never guesses context during replay.
"""

from __future__ import annotations

import unicodedata
from dataclasses import dataclass

from shared.schemas.podcast_subtitles_v2 import (
    CanonicalSpan,
    CanonicalToken,
    CanonicalTranscript,
    ReferenceQueryContext,
    ReferenceQueryContextSlice,
    ReferenceRetrievalPolicySnapshot,
    reference_retrieval_policy_hash,
)

from .hashing import sha256_bytes


class ReferenceContextError(ValueError):
    """The persisted policy cannot produce a safe, reproducible query plan."""


@dataclass(frozen=True, slots=True)
class _SpanView:
    span: CanonicalSpan
    text: str
    tokens: tuple[CanonicalToken, ...]
    token_ranges: tuple[tuple[int, int], ...]
    known_speaker: str | None


def _is_regional_indicator(character: str) -> bool:
    return "\U0001f1e6" <= character <= "\U0001f1ff"


def _is_grapheme_extend(character: str) -> bool:
    codepoint = ord(character)
    return (
        unicodedata.category(character).startswith("M")
        or 0xFE00 <= codepoint <= 0xFE0F
        or 0xE0100 <= codepoint <= 0xE01EF
        or 0x1F3FB <= codepoint <= 0x1F3FF
    )


def _grapheme_spans(value: str) -> tuple[tuple[int, int], ...]:
    """Conservative extended-grapheme edges for context truncation only."""

    result: list[tuple[int, int]] = []
    index = 0
    while index < len(value):
        start = index
        first = value[index]
        index += 1
        if (
            _is_regional_indicator(first)
            and index < len(value)
            and _is_regional_indicator(value[index])
        ):
            index += 1
        while index < len(value):
            if _is_grapheme_extend(value[index]):
                index += 1
                continue
            if value[index] == "\u200d" and index + 1 < len(value):
                index += 2
                continue
            break
        result.append((start, index))
    return tuple(result)


def _span_views(transcript: CanonicalTranscript) -> tuple[_SpanView, ...]:
    token_by_id = {token.id: token for token in transcript.tokens}
    views: list[_SpanView] = []
    for span in transcript.spans:
        tokens = tuple(token_by_id[token_id] for token_id in span.token_ids)
        cursor = 0
        ranges: list[tuple[int, int]] = []
        for token in tokens:
            end = cursor + len(token.text)
            ranges.append((cursor, end))
            cursor = end
        text = "".join(token.text for token in tokens)
        speakers = {token.speaker for token in tokens if token.speaker is not None}
        known_speaker = next(iter(speakers)) if len(speakers) == 1 else None
        views.append(
            _SpanView(
                span=span,
                text=text,
                tokens=tokens,
                token_ranges=tuple(ranges),
                known_speaker=known_speaker,
            )
        )
    return tuple(views)


def _slice_token_ids(view: _SpanView, start: int, end: int) -> tuple[str, ...]:
    return tuple(
        token.id
        for token, (token_start, token_end) in zip(view.tokens, view.token_ranges, strict=True)
        if token_start < end and token_end > start
    )


def _context_slice(
    view: _SpanView,
    *,
    start: int,
    end: int,
) -> ReferenceQueryContextSlice:
    if start < 0 or end > len(view.text) or end <= start:
        raise ReferenceContextError("Reference context slice is outside its Canonical span")
    token_ids = _slice_token_ids(view, start, end)
    if not token_ids:
        raise ReferenceContextError("Reference context slice has no owning Canonical token")
    return ReferenceQueryContextSlice(
        span_id=view.span.id,
        token_ids=token_ids,
        span_text_hash=sha256_bytes(view.text.encode("utf-8")),
        slice_start=start,
        slice_end=end,
    )


def _take_suffix(text: str, budget: int) -> tuple[int, int] | None:
    if budget <= 0 or not text:
        return None
    graphemes = _grapheme_spans(text)
    selected_start = len(text)
    used = 0
    for start, end in reversed(graphemes):
        width = end - start
        if used + width > budget:
            break
        selected_start = start
        used += width
    return (selected_start, len(text)) if used else None


def _take_prefix(text: str, budget: int) -> tuple[int, int] | None:
    if budget <= 0 or not text:
        return None
    graphemes = _grapheme_spans(text)
    selected_end = 0
    used = 0
    for start, end in graphemes:
        width = end - start
        if used + width > budget:
            break
        selected_end = end
        used += width
    return (0, selected_end) if used else None


def _crosses_policy_boundary(
    left: _SpanView,
    right: _SpanView,
    policy: ReferenceRetrievalPolicySnapshot,
) -> bool:
    gap = right.span.start_ms - left.span.end_ms
    if gap > policy.max_adjacent_gap_ms:
        return True
    return bool(
        policy.stop_at_known_speaker_change
        and left.known_speaker is not None
        and right.known_speaker is not None
        and left.known_speaker != right.known_speaker
    )


def _build_one(
    transcript: CanonicalTranscript,
    views: tuple[_SpanView, ...],
    anchor_index: int,
    policy: ReferenceRetrievalPolicySnapshot,
) -> ReferenceQueryContext:
    anchor = views[anchor_index]
    if len(anchor.text) > policy.max_anchor_unicode_scalars:
        raise ReferenceContextError(
            f"Canonical anchor {anchor.span.id!r} exceeds the persisted Reference query limit"
        )

    left_slices: list[ReferenceQueryContextSlice] = []
    remaining = policy.left_unicode_scalar_budget
    nearer_index = anchor_index
    for candidate_index in range(anchor_index - 1, -1, -1):
        if len(left_slices) >= policy.max_adjacent_spans_per_side or remaining <= 0:
            break
        candidate = views[candidate_index]
        nearer = views[nearer_index]
        if _crosses_policy_boundary(candidate, nearer, policy):
            break
        selected = _take_suffix(candidate.text, remaining)
        if selected is None:
            break
        item = _context_slice(candidate, start=selected[0], end=selected[1])
        left_slices.append(item)
        remaining -= item.slice_end - item.slice_start
        nearer_index = candidate_index
    left_slices.reverse()

    anchor_slice = _context_slice(anchor, start=0, end=len(anchor.text))

    right_slices: list[ReferenceQueryContextSlice] = []
    remaining = policy.right_unicode_scalar_budget
    nearer_index = anchor_index
    for candidate_index in range(anchor_index + 1, len(views)):
        if len(right_slices) >= policy.max_adjacent_spans_per_side or remaining <= 0:
            break
        candidate = views[candidate_index]
        nearer = views[nearer_index]
        if _crosses_policy_boundary(nearer, candidate, policy):
            break
        selected = _take_prefix(candidate.text, remaining)
        if selected is None:
            break
        item = _context_slice(candidate, start=selected[0], end=selected[1])
        right_slices.append(item)
        remaining -= item.slice_end - item.slice_start
        nearer_index = candidate_index

    slices = (*left_slices, anchor_slice, *right_slices)
    view_by_id = {view.span.id: view for view in views}
    query_parts = tuple(
        view_by_id[item.span_id].text[item.slice_start : item.slice_end] for item in slices
    )
    exact_query = "".join(query_parts)
    left_width = sum(len(part) for part in query_parts[: len(left_slices)])
    anchor_end = left_width + len(anchor.text)
    if len(exact_query) > policy.max_query_unicode_scalars:
        raise ReferenceContextError("Reference query exceeds its persisted Unicode-scalar limit")
    return ReferenceQueryContext(
        basis_content_hash=transcript.content_hash,
        anchor_span_id=anchor.span.id,
        anchor_query_start=left_width,
        anchor_query_end=anchor_end,
        slices=slices,
        exact_query=exact_query,
        algorithm="canonical_adjacent_context",
        algorithm_version="unicode-scalar-v1",
        policy_hash=reference_retrieval_policy_hash(policy),
    )


def build_reference_query_contexts(
    transcript: CanonicalTranscript,
    policy: ReferenceRetrievalPolicySnapshot,
) -> tuple[ReferenceQueryContext, ...]:
    """Build exactly one ordered least-context query for every Canonical span."""

    views = _span_views(transcript)
    return tuple(_build_one(transcript, views, index, policy) for index in range(len(views)))


def verify_reference_query_contexts(
    transcript: CanonicalTranscript,
    policy: ReferenceRetrievalPolicySnapshot,
    contexts: tuple[ReferenceQueryContext, ...],
) -> tuple[ReferenceQueryContext, ...]:
    """Fail closed unless stored contexts exactly equal deterministic rebuild."""

    expected = build_reference_query_contexts(transcript, policy)
    if contexts != expected:
        raise ReferenceContextError(
            "Stored Reference query contexts differ from the Canonical Transcript and policy"
        )
    return contexts


__all__ = [
    "ReferenceContextError",
    "build_reference_query_contexts",
    "verify_reference_query_contexts",
]
