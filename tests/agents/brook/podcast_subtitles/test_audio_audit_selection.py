"""Selective audio-audit policy is deterministic, replayable, and fail closed."""

from __future__ import annotations

from collections.abc import Sequence

import pytest

from agents.brook.podcast_subtitles.audio_audit_selection import (
    AudioAuditSelectionError,
    build_audio_audit_selection_plan,
    build_audio_audit_selection_receipt,
    default_audio_audit_selection_policy,
    verify_audio_audit_selection_plan,
    verify_audio_audit_selection_receipt,
)
from agents.brook.podcast_subtitles.audit_plan import (
    build_audit_plan,
    default_correction_audit_policy,
)
from shared.schemas.podcast_subtitles_v2 import (
    EMPTY_REFERENCE_EVIDENCE_HASH,
    ArtifactDigest,
    CanonicalSpan,
    CanonicalToken,
    CanonicalTranscript,
    EvidenceToken,
    RecognitionEvidence,
    ReviewIssue,
    canonical_content_hash,
    recognition_evidence_set_hash,
)

H1 = "1" * 64
H2 = "2" * 64
H3 = "3" * 64
H4 = "4" * 64
H5 = "5" * 64
H6 = "6" * 64
H7 = "7" * 64


def _case(
    count: int = 100,
    *,
    episode_id: str = "episode-1",
    normalized_audio_hash: str = H1,
    risk_ordinals: Sequence[int] = (),
    confidence: float | None = 0.95,
) -> tuple[CanonicalTranscript, tuple[RecognitionEvidence, ...]]:
    texts = tuple(
        "Omega-3" if index in risk_ordinals else f"常態片段{chr(0x3400 + index)}"
        for index in range(count)
    )
    evidence_tokens = tuple(
        EvidenceToken(
            id=f"ev-{index}",
            text=text,
            start_ms=index * 1000,
            end_ms=index * 1000 + 700,
            confidence=0.2 if index in risk_ordinals else confidence,
            speaker="host" if index % 2 == 0 else "guest",
            evidence_refs=(f"segment-{index}",),
        )
        for index, text in enumerate(texts)
    )
    recognition = RecognitionEvidence(
        episode_id=episode_id,
        invocation_id="recognition-1",
        adapter="fixture-asr",
        model="fixture-model",
        language="zh",
        config_hash=H4,
        raw_output=ArtifactDigest(uri="file:///raw.json", sha256=H5, size_bytes=100),
        raw_output_hash=H5,
        normalized_audio_hash=normalized_audio_hash,
        tokens=evidence_tokens,
    )
    canonical_tokens = tuple(
        CanonicalToken(
            id=f"token-{index}",
            text=text,
            start_ms=index * 1000,
            end_ms=index * 1000 + 700,
            evidence_ids=(f"ev-{index}",),
            confidence=0.2 if index in risk_ordinals else confidence,
            speaker="host" if index % 2 == 0 else "guest",
        )
        for index, text in enumerate(texts)
    )
    transcript = CanonicalTranscript(
        episode_id=episode_id,
        generation_id="generation-1",
        revision=1,
        status="draft",
        source_audio_hash=H2,
        normalized_audio_hash=normalized_audio_hash,
        normalization_receipt_hash=H3,
        evidence_hash=recognition_evidence_set_hash((recognition,)),
        reference_evidence_hash=EMPTY_REFERENCE_EVIDENCE_HASH,
        ledger_hash=H6,
        policy_hash=H7,
        acceptance_policy={"permit_unresolved_low_risk": True},
        tokens=canonical_tokens,
        spans=tuple(
            CanonicalSpan(
                id=f"span-{index}",
                token_ids=(f"token-{index}",),
                start_ms=index * 1000,
                end_ms=index * 1000 + 700,
            )
            for index in range(count)
        ),
        content_hash=canonical_content_hash(canonical_tokens),
    )
    return transcript, (recognition,)


def _plan(
    count: int = 100,
    *,
    episode_id: str = "episode-1",
    normalized_audio_hash: str = H1,
    risk_ordinals: Sequence[int] = (),
    confidence: float | None = 0.95,
):
    transcript, recognitions = _case(
        count,
        episode_id=episode_id,
        normalized_audio_hash=normalized_audio_hash,
        risk_ordinals=risk_ordinals,
        confidence=confidence,
    )
    audit = build_audit_plan(
        transcript,
        recognitions,
        default_correction_audit_policy(),
        references_enrolled=False,
    )
    return transcript, audit


def test_default_selection_is_replayable_and_samples_at_least_ten_percent() -> None:
    transcript, audit = _plan()
    policy = default_audio_audit_selection_policy()

    first = build_audio_audit_selection_plan(audit, transcript, policy, tier="sample_10")
    second = build_audio_audit_selection_plan(audit, transcript, policy, tier="sample_10")

    assert first == second
    assert len(first.sample_span_ids) >= 10
    assert len(first.sample_span_ids) == len(set(first.sample_span_ids))
    assert set(first.selected_span_ids) == set(first.risk_span_ids) | set(first.sample_span_ids)
    assert verify_audio_audit_selection_plan(first, audit, transcript, policy) == first


def test_risk_targets_are_always_selected_and_do_not_count_as_sample() -> None:
    transcript, audit = _plan(risk_ordinals=(2, 51))
    selected = build_audio_audit_selection_plan(
        audit,
        transcript,
        default_audio_audit_selection_policy(),
        tier="sample_10",
    )

    assert {"span-2", "span-51"} <= set(selected.risk_span_ids)
    assert {"span-2", "span-51"} <= set(selected.selected_span_ids)
    assert not {"span-2", "span-51"} & set(selected.sample_span_ids)


def test_missing_confidence_capability_does_not_blanket_target_memo_spans() -> None:
    transcript, audit = _plan(confidence=None)
    selected = build_audio_audit_selection_plan(
        audit,
        transcript,
        default_audio_audit_selection_policy(),
        tier="sample_10",
    )

    assert selected.risk_span_ids == ()
    assert len(selected.sample_span_ids) >= 10


def test_generic_native_resolution_gate_is_not_an_audio_risk_signal() -> None:
    transcript, recognitions = _case(count=20)
    issue = ReviewIssue(
        id="issue-native-gate",
        risk="text",
        severity="high",
        code="native_full_audit_requires_resolution",
        span_ids=tuple(item.id for item in transcript.spans),
        candidates=(),
        status="unresolved",
    )
    transcript = transcript.model_copy(update={"review_issues": (issue,)})
    audit = build_audit_plan(
        transcript,
        recognitions,
        default_correction_audit_policy(),
        references_enrolled=False,
    )

    selected = build_audio_audit_selection_plan(
        audit,
        transcript,
        default_audio_audit_selection_policy(),
        tier="sample_10",
    )

    assert selected.risk_span_ids == ()
    assert len(selected.sample_span_ids) == 2


def test_explicit_low_confidence_is_always_targeted() -> None:
    transcript, audit = _plan(count=20, confidence=0.2)
    selected = build_audio_audit_selection_plan(
        audit,
        transcript,
        default_audio_audit_selection_policy(),
        tier="sample_10",
    )

    assert selected.risk_span_ids == selected.all_span_ids
    assert all("explicit_low_confidence" in item.reasons for item in selected.risk_targets)


def test_risk_targets_never_bias_sample_error_denominator() -> None:
    transcript, audit = _plan(count=100, risk_ordinals=(2, 51))
    plan = build_audio_audit_selection_plan(
        audit,
        transcript,
        default_audio_audit_selection_policy(clock_strata=1),
        tier="sample_10",
    )
    receipt = build_audio_audit_selection_receipt(
        plan,
        risk_finding_span_ids=("span-2",),
    )

    assert receipt.material_error_numerator == 0
    assert receipt.material_error_denominator == len(plan.sample_span_ids) == 10
    assert receipt.decision == "complete"


def test_major_error_escalates_even_at_exactly_two_percent() -> None:
    transcript, audit = _plan(count=500)
    policy = default_audio_audit_selection_policy(clock_strata=1)
    plan = build_audio_audit_selection_plan(audit, transcript, policy, tier="sample_10")
    assert len(plan.sample_span_ids) == 50
    receipt = build_audio_audit_selection_receipt(
        plan,
        material_error_span_ids=(plan.sample_span_ids[0],),
        major_error_span_ids=(plan.sample_span_ids[0],),
    )

    assert receipt.material_error_numerator * 10000 == (
        receipt.material_error_denominator * policy.material_error_threshold_basis_points
    )
    assert receipt.decision == "escalate_30"


def test_zero_ordinary_population_has_not_applicable_zero_denominator() -> None:
    transcript, audit = _plan(count=20, confidence=0.2)
    plan = build_audio_audit_selection_plan(
        audit,
        transcript,
        default_audio_audit_selection_policy(),
        tier="sample_10",
    )
    receipt = build_audio_audit_selection_receipt(plan)

    assert plan.sample_span_ids == ()
    assert receipt.material_error_denominator == 0
    assert receipt.decision == "complete"


def test_seed_changes_with_episode_or_audio_identity() -> None:
    policy = default_audio_audit_selection_policy()
    transcript_a, audit_a = _plan()
    transcript_b, audit_b = _plan(episode_id="episode-2")
    transcript_c, audit_c = _plan(normalized_audio_hash="a" * 64)

    plan_a = build_audio_audit_selection_plan(audit_a, transcript_a, policy, tier="sample_10")
    plan_b = build_audio_audit_selection_plan(audit_b, transcript_b, policy, tier="sample_10")
    plan_c = build_audio_audit_selection_plan(audit_c, transcript_c, policy, tier="sample_10")

    assert len({plan_a.sample_seed, plan_b.sample_seed, plan_c.sample_seed}) == 3
    assert plan_a.sample_span_ids != plan_b.sample_span_ids
    assert plan_a.sample_span_ids != plan_c.sample_span_ids


def test_threshold_and_escalation_are_evaluated_on_exact_selected_coverage() -> None:
    transcript, audit = _plan(count=500)
    policy = default_audio_audit_selection_policy(clock_strata=1)
    ten = build_audio_audit_selection_plan(audit, transcript, policy, tier="sample_10")
    assert len(ten.selected_span_ids) == 50

    exactly_two = build_audio_audit_selection_receipt(
        ten,
        material_error_span_ids=ten.selected_span_ids[:1],
    )
    assert exactly_two.decision == "complete"

    above_two_errors = 2
    above_two = build_audio_audit_selection_receipt(
        ten,
        material_error_span_ids=ten.selected_span_ids[:above_two_errors],
    )
    assert above_two.decision == "escalate_30"
    thirty = build_audio_audit_selection_plan(
        audit,
        transcript,
        policy,
        tier="sample_30",
        prior_plan=ten,
        prior_receipt=above_two,
    )
    assert set(ten.selected_span_ids) <= set(thirty.selected_span_ids)
    thirty_receipt = build_audio_audit_selection_receipt(
        thirty,
        material_error_span_ids=thirty.selected_span_ids[
            : max(1, len(thirty.selected_span_ids) // 20)
        ],
    )
    assert thirty_receipt.decision == "escalate_full"
    full = build_audio_audit_selection_plan(
        audit,
        transcript,
        policy,
        tier="full",
        prior_plan=thirty,
        prior_receipt=thirty_receipt,
    )
    assert full.selected_span_ids == tuple(item.span_id for item in audit.span_targets)


def test_catastrophic_finding_escalates_directly_to_full() -> None:
    transcript, audit = _plan()
    policy = default_audio_audit_selection_policy()
    plan = build_audio_audit_selection_plan(audit, transcript, policy, tier="sample_10")
    receipt = build_audio_audit_selection_receipt(
        plan,
        catastrophic_span_ids=(plan.selected_span_ids[0],),
    )
    assert receipt.decision == "escalate_full"


def test_receipt_rejects_missing_selected_coverage_and_tamper() -> None:
    transcript, audit = _plan()
    policy = default_audio_audit_selection_policy()
    plan = build_audio_audit_selection_plan(audit, transcript, policy, tier="sample_10")
    receipt = build_audio_audit_selection_receipt(plan)
    assert verify_audio_audit_selection_receipt(plan, receipt) == receipt

    with pytest.raises(AudioAuditSelectionError, match="exact selected"):
        build_audio_audit_selection_receipt(
            plan,
            audited_span_ids=plan.selected_span_ids[:-1],
        )
    with pytest.raises(AudioAuditSelectionError):
        verify_audio_audit_selection_plan(
            plan.model_copy(update={"normalized_audio_hash": "f" * 64}),
            audit,
            transcript,
            policy,
        )
