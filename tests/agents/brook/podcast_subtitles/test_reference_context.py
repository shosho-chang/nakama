from __future__ import annotations

import pytest

from agents.brook.podcast_subtitles.reference_context import (
    ReferenceContextError,
    build_reference_query_contexts,
    verify_reference_query_contexts,
)
from shared.schemas.podcast_subtitles_v2 import (
    EMPTY_REFERENCE_EVIDENCE_HASH,
    CanonicalSpan,
    CanonicalToken,
    CanonicalTranscript,
    ReferenceRetrievalPolicySnapshot,
    canonical_content_hash,
)

H0 = "0" * 64
H1 = "1" * 64
H2 = "2" * 64
H3 = "3" * 64


def _policy(**updates: object) -> ReferenceRetrievalPolicySnapshot:
    values: dict[str, object] = {
        "left_unicode_scalar_budget": 5,
        "right_unicode_scalar_budget": 5,
        "max_adjacent_spans_per_side": 5,
        "max_anchor_unicode_scalars": 256,
        "max_query_unicode_scalars": 266,
        "stop_at_known_speaker_change": True,
        "max_adjacent_gap_ms": 2_000,
        "max_candidate_terms": 16,
        "max_results": 6,
        "retrievable_codes": ("suspicious_token",),
        "vocabulary": ("數位遊牧",),
    }
    values.update(updates)
    return ReferenceRetrievalPolicySnapshot.model_validate(values)


def _transcript(
    texts: tuple[str, ...],
    *,
    speakers: tuple[str | None, ...] | None = None,
    gaps_ms: tuple[int, ...] | None = None,
) -> CanonicalTranscript:
    speakers = speakers or tuple("guest" for _ in texts)
    gaps_ms = gaps_ms or tuple(0 for _ in texts)
    tokens: list[CanonicalToken] = []
    spans: list[CanonicalSpan] = []
    cursor = 100
    for index, (text, speaker, gap) in enumerate(zip(texts, speakers, gaps_ms, strict=True)):
        cursor += gap
        token_id = f"token-{index}"
        span_id = f"span-{index}"
        end = cursor + 100
        tokens.append(
            CanonicalToken(
                id=token_id,
                text=text,
                start_ms=cursor,
                end_ms=end,
                speaker=speaker,
                evidence_ids=(f"evidence-{index}",),
            )
        )
        spans.append(
            CanonicalSpan(
                id=span_id,
                token_ids=(token_id,),
                start_ms=cursor,
                end_ms=end,
            )
        )
        cursor = end
    token_tuple = tuple(tokens)
    return CanonicalTranscript(
        episode_id="episode-1",
        generation_id="generation-1",
        revision=1,
        status="draft",
        source_audio_hash=H0,
        normalized_audio_hash=H0,
        normalization_receipt_hash=H0,
        evidence_hash=H1,
        reference_evidence_hash=EMPTY_REFERENCE_EVIDENCE_HASH,
        ledger_hash=H2,
        policy_hash=H3,
        acceptance_policy={"permit_unresolved_low_risk": True},
        tokens=token_tuple,
        spans=tuple(spans),
        content_hash=canonical_content_hash(token_tuple),
    )


def test_single_character_spans_rebuild_cross_span_term_for_every_anchor() -> None:
    transcript = _transcript(("數", "位", "遊", "牧"))

    contexts = build_reference_query_contexts(transcript, _policy())

    assert tuple(item.exact_query for item in contexts) == ("數位遊牧",) * 4
    assert tuple(
        item.exact_query[item.anchor_query_start : item.anchor_query_end] for item in contexts
    ) == ("數", "位", "遊", "牧")
    assert tuple(item.anchor_span_id for item in contexts) == tuple(
        span.id for span in transcript.spans
    )
    assert verify_reference_query_contexts(transcript, _policy(), contexts) == contexts


def test_context_stops_at_known_speaker_change_and_policy_gap() -> None:
    transcript = _transcript(
        ("甲", "乙", "丙", "丁"),
        speakers=("host", "host", "guest", "guest"),
        gaps_ms=(0, 0, 0, 2_001),
    )

    contexts = build_reference_query_contexts(transcript, _policy())

    assert tuple(item.exact_query for item in contexts) == ("甲乙", "甲乙", "丙", "丁")


def test_context_preserves_graphemes_and_rejects_oversized_anchor() -> None:
    transcript = _transcript(("e\u0301", "中", "🇹🇼"))
    policy = _policy(
        left_unicode_scalar_budget=1,
        right_unicode_scalar_budget=1,
        max_anchor_unicode_scalars=3,
        max_query_unicode_scalars=5,
    )

    contexts = build_reference_query_contexts(transcript, policy)

    assert contexts[1].exact_query == "中"
    assert contexts[0].exact_query == "e\u0301中"
    assert contexts[2].exact_query == "中🇹🇼"

    too_long = _transcript(("超過",))
    with pytest.raises(ReferenceContextError, match="exceeds"):
        build_reference_query_contexts(
            too_long,
            _policy(max_anchor_unicode_scalars=1, max_query_unicode_scalars=11),
        )
