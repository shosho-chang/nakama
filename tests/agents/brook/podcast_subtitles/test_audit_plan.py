"""Deterministic and hostile tests for the Correction AuditPlan builder."""

from __future__ import annotations

from collections.abc import Sequence

import pytest
from pydantic import ValidationError

from agents.brook.podcast_subtitles.audit_plan import (
    AuditPlanError,
    assert_audit_plan,
    build_audit_plan,
    default_correction_audit_policy,
)
from agents.brook.podcast_subtitles.hashing import hash_object
from shared.schemas.podcast_subtitles_v2 import (
    EMPTY_REFERENCE_EVIDENCE_HASH,
    ArtifactDigest,
    AuditCell,
    CanonicalSpan,
    CanonicalToken,
    CanonicalTranscript,
    EvidenceToken,
    RecognitionEvidence,
    RecognitionSeamEvidence,
    RecognitionSeamObservation,
    SpeechActivityInterval,
    SpeechCoverageReceipt,
    canonical_content_hash,
    recognition_evidence_content_hash,
    recognition_evidence_set_hash,
)

H1 = "1" * 64
H2 = "2" * 64
H3 = "3" * 64
H4 = "4" * 64
H5 = "5" * 64
H6 = "6" * 64
H7 = "7" * 64
H8 = "8" * 64
H9 = "9" * 64
HA = "a" * 64
HB = "b" * 64
HC = "c" * 64


@pytest.fixture(autouse=True)
def isolated_db() -> None:
    """This pure suite performs no application-state access."""


def make_case(
    texts: Sequence[str] = ("今天", "訪談"),
    *,
    confidences: Sequence[float | None] | None = None,
    speakers: Sequence[str | None] | None = None,
    recognition_speakers: Sequence[str | None] | None = None,
) -> tuple[CanonicalTranscript, tuple[RecognitionEvidence, ...]]:
    count = len(texts)
    confidences = confidences or tuple(0.95 for _ in range(count))
    speakers = speakers or tuple("guest" for _ in range(count))
    recognition_speakers = recognition_speakers or speakers
    timings = tuple((100 + index * 400, 300 + index * 400) for index in range(count))
    evidence_tokens = tuple(
        EvidenceToken(
            id=f"ev-{index}",
            text=text,
            start_ms=start,
            end_ms=end,
            confidence=confidences[index],
            speaker=recognition_speakers[index],
            evidence_refs=(f"segment-{index}",),
        )
        for index, (text, (start, end)) in enumerate(zip(texts, timings, strict=True))
    )
    recognition = RecognitionEvidence(
        episode_id="episode-1",
        invocation_id="recognition-1",
        adapter="fixture-asr",
        model="fixture-model",
        language="zh",
        config_hash=H4,
        raw_output=ArtifactDigest(uri="file:///raw.json", sha256=H5, size_bytes=100),
        raw_output_hash=H5,
        normalized_audio_hash=H1,
        tokens=evidence_tokens,
    )
    recognitions = (recognition,)
    canonical_tokens = tuple(
        CanonicalToken(
            id=f"token-{index}",
            text=text,
            start_ms=start,
            end_ms=end,
            evidence_ids=(f"ev-{index}",),
            confidence=confidences[index],
            speaker=speakers[index],
        )
        for index, (text, (start, end)) in enumerate(zip(texts, timings, strict=True))
    )
    transcript = CanonicalTranscript(
        episode_id="episode-1",
        generation_id="generation-1",
        revision=1,
        status="draft",
        source_audio_hash=H2,
        normalized_audio_hash=H1,
        normalization_receipt_hash=H3,
        evidence_hash=recognition_evidence_set_hash(recognitions),
        reference_evidence_hash=EMPTY_REFERENCE_EVIDENCE_HASH,
        ledger_hash=H6,
        policy_hash=H7,
        acceptance_policy={"permit_unresolved_low_risk": True},
        tokens=canonical_tokens,
        spans=tuple(
            CanonicalSpan(
                id=f"span-{index}",
                token_ids=(f"token-{index}",),
                start_ms=start,
                end_ms=end,
            )
            for index, (start, end) in enumerate(timings)
        ),
        content_hash=canonical_content_hash(canonical_tokens),
    )
    return transcript, recognitions


def make_coverage(
    transcript: CanonicalTranscript,
    recognitions: tuple[RecognitionEvidence, ...],
    *,
    uncovered: Sequence[tuple[int, int]] = (),
    status: str = "completed",
) -> SpeechCoverageReceipt:
    intervals = tuple(
        SpeechActivityInterval(id=f"uncovered-{index}", start_ms=start, end_ms=end)
        for index, (start, end) in enumerate(uncovered)
    )
    payload = {
        "status": status,
        "episode_id": transcript.episode_id,
        "invocation_id": "coverage-1",
        "analyzer": "fixture-vad",
        "analyzer_version": "1",
        "analyzer_config_hash": H8,
        "analyzer_code_hash": H9,
        "analyzer_runtime_hash": HA,
        "method": "fixture_intervals_v1",
        "normalized_audio_hash": transcript.normalized_audio_hash,
        "normalized_audio_size_bytes": 1000,
        "normalized_audio_duration_ms": max(
            transcript.spans[-1].end_ms + 100,
            max((end for _, end in uncovered), default=0),
        ),
        "recognition_evidence_hash": recognition_evidence_set_hash(recognitions),
        "recognition_interval_hash": HB,
        "coverage_tolerance_ms": 0,
        "minimum_uncovered_ms": 1,
        "raw_output": ArtifactDigest(uri="file:///coverage.json", sha256=HC, size_bytes=100),
        "raw_output_hash": HC,
        "activity_intervals": (
            SpeechActivityInterval(
                id="activity-all",
                start_ms=0,
                end_ms=max(
                    transcript.spans[-1].end_ms + 100,
                    max((end for _, end in uncovered), default=0),
                ),
            ),
        )
        if status == "completed"
        else (),
        "uncovered_intervals": intervals if status == "completed" else (),
        "passed": status == "completed" and not intervals,
        "failure_reason": None if status == "completed" else "fixture failure",
    }
    return SpeechCoverageReceipt(**payload)


def make_seam(
    transcript: CanonicalTranscript,
    recognitions: tuple[RecognitionEvidence, ...],
    *,
    seam_ms: int | None,
    observation_status: str = "matched",
    wrapper_status: str | None = None,
    raw_artifact_hash: str = H5,
) -> RecognitionSeamEvidence:
    recognition_hashes = tuple(
        sorted(recognition_evidence_content_hash(item) for item in recognitions)
    )
    observations: tuple[RecognitionSeamObservation, ...] = ()
    if seam_ms is not None:
        observation_payload = {
            "schema_version": 1,
            "id": "seam-observation-1",
            "seam_ms": seam_ms,
            "status": observation_status,
            "raw_artifact_hash": raw_artifact_hash,
            "left_chunk_id": "chunk-left",
            "right_chunk_id": "chunk-right",
            "left_chunk_receipt_hash": H8,
            "right_chunk_receipt_hash": H9,
        }
        observations = (
            RecognitionSeamObservation(
                **observation_payload,
                observation_hash=hash_object(observation_payload),
            ),
        )
    status = wrapper_status or (
        "unchunked"
        if seam_ms is None
        else ("complete" if observation_status == "matched" else "conflicted")
    )
    payload = {
        "schema_version": 1,
        "status": status,
        "normalized_audio_hash": transcript.normalized_audio_hash,
        "recognition_evidence_hashes": recognition_hashes,
        "raw_artifact_hash": raw_artifact_hash,
        "observations": observations,
        "failure_reason": "fixture conflict" if status == "conflicted" else None,
    }
    return RecognitionSeamEvidence(**payload, content_hash=hash_object(payload))


def cell(plan: object, target_kind: str, ordinal: int, category: str) -> AuditCell:
    targets = (
        plan.span_targets if target_kind == "span" else plan.boundary_targets  # type: ignore[attr-defined]
    )
    target_id = targets[ordinal].id
    return next(
        item
        for item in plan.cells  # type: ignore[attr-defined]
        if item.target_kind == target_kind
        and item.target_id == target_id
        and item.category == category
    )


def test_plan_has_exact_n_plus_one_boundaries_and_cartesian_cells() -> None:
    transcript, recognitions = make_case()
    policy = default_correction_audit_policy()

    plan = build_audit_plan(transcript, recognitions, policy, references_enrolled=False)

    assert len(plan.span_targets) == 2
    assert len(plan.boundary_targets) == 3
    assert len(plan.cells) == 2 * 9 + 3 * 5
    assert {item.basis_hash for item in plan.span_targets} == {plan.basis_hash}
    assert {item.basis_hash for item in plan.boundary_targets} == {plan.basis_hash}
    assert {item.basis_hash for item in plan.cells} == {plan.basis_hash}
    assert plan.execution_status == "dormant_not_executed"
    assert "not_semantic_recall_proof" in plan.completeness_statement


def test_plan_applicability_is_explicit_per_target_not_global() -> None:
    transcript, recognitions = make_case()
    plan = build_audit_plan(
        transcript,
        recognitions,
        default_correction_audit_policy(),
        references_enrolled=False,
    )

    assert cell(plan, "span", 0, "lexical_fidelity").applicability == "required"
    assert cell(plan, "span", 0, "reference_name_term").applicability == "not_applicable"
    assert cell(plan, "span", 0, "confidence_integrity").applicability == "not_applicable"
    assert cell(plan, "boundary", 0, "cross_span_fidelity").applicability == "not_applicable"
    assert cell(plan, "boundary", 1, "cross_span_fidelity").applicability == "required"
    assert cell(plan, "boundary", 1, "chunk_seam").applicability == "unavailable"
    assert cell(plan, "boundary", 1, "speech_coverage").applicability == "unavailable"


@pytest.mark.parametrize(
    ("confidences", "speakers", "category", "expected"),
    [
        ((None, 0.95), ("guest", "guest"), "confidence_integrity", "required"),
        ((0.20, 0.95), ("guest", "guest"), "confidence_integrity", "required"),
        ((0.95, 0.95), (None, "guest"), "speaker_identity", "unavailable"),
    ],
)
def test_metadata_gaps_fail_closed_per_span(
    confidences: Sequence[float | None],
    speakers: Sequence[str | None],
    category: str,
    expected: str,
) -> None:
    transcript, recognitions = make_case(
        confidences=confidences,
        speakers=speakers,
        recognition_speakers=speakers,
    )
    plan = build_audit_plan(
        transcript,
        recognitions,
        default_correction_audit_policy(),
        references_enrolled=False,
    )

    assert cell(plan, "span", 0, category).applicability == expected


def test_coverage_assesses_leading_internal_and_trailing_boundaries() -> None:
    transcript, recognitions = make_case()
    coverage = make_coverage(
        transcript,
        recognitions,
        uncovered=((20, 80), (450, 550), (720, 760)),
    )
    plan = build_audit_plan(
        transcript,
        recognitions,
        default_correction_audit_policy(),
        references_enrolled=False,
        speech_coverage=coverage,
    )

    assert [
        cell(plan, "boundary", ordinal, "speech_coverage").applicability for ordinal in range(3)
    ] == ["required", "required", "required"]
    assert cell(plan, "span", 1, "lexical_fidelity").applicability == "required"


@pytest.mark.parametrize(
    ("observation_status", "expected"),
    [("matched", "not_applicable"), ("duplicate", "required"), ("conflict", "required")],
)
def test_seam_observation_has_one_internal_boundary_owner(
    observation_status: str, expected: str
) -> None:
    transcript, recognitions = make_case()
    seam = make_seam(
        transcript,
        recognitions,
        seam_ms=500,
        observation_status=observation_status,
    )
    plan = build_audit_plan(
        transcript,
        recognitions,
        default_correction_audit_policy(),
        references_enrolled=False,
        seam_evidence=seam,
    )

    assert cell(plan, "boundary", 1, "chunk_seam").applicability == expected
    assert cell(plan, "boundary", 0, "chunk_seam").applicability == "not_applicable"
    assert cell(plan, "boundary", 2, "chunk_seam").applicability == "not_applicable"


def test_long_gap_seam_inside_boundary_window_is_not_rejected_by_midpoint_distance() -> None:
    transcript, recognitions = make_case()
    seam = make_seam(transcript, recognitions, seam_ms=301)

    plan = build_audit_plan(
        transcript,
        recognitions,
        default_correction_audit_policy(seam_match_tolerance_ms=0),
        references_enrolled=False,
        seam_evidence=seam,
    )

    assert cell(plan, "boundary", 1, "chunk_seam").applicability == "not_applicable"


def test_equidistant_seam_ownership_fails_closed() -> None:
    transcript, recognitions = make_case(("甲", "乙", "丙"))
    seam = make_seam(transcript, recognitions, seam_ms=600)

    with pytest.raises(AuditPlanError, match="equidistant"):
        build_audit_plan(
            transcript,
            recognitions,
            default_correction_audit_policy(seam_match_tolerance_ms=200),
            references_enrolled=False,
            seam_evidence=seam,
        )


def test_seam_wrapper_cannot_name_an_arbitrary_raw_artifact() -> None:
    transcript, recognitions = make_case()
    seam = make_seam(
        transcript,
        recognitions,
        seam_ms=500,
        raw_artifact_hash=HA,
    )

    with pytest.raises(AuditPlanError, match="not owned"):
        build_audit_plan(
            transcript,
            recognitions,
            default_correction_audit_policy(),
            references_enrolled=False,
            seam_evidence=seam,
        )


def test_transcript_and_coverage_recognition_lineage_must_match_exactly() -> None:
    transcript, recognitions = make_case()
    forged = CanonicalTranscript.model_validate({**transcript.model_dump(), "evidence_hash": HA})
    with pytest.raises(AuditPlanError, match="Canonical Recognition Evidence lineage"):
        build_audit_plan(
            forged,
            recognitions,
            default_correction_audit_policy(),
            references_enrolled=False,
        )

    coverage = make_coverage(transcript, recognitions)
    forged_coverage = SpeechCoverageReceipt.model_validate(
        {**coverage.model_dump(), "recognition_evidence_hash": HA}
    )
    with pytest.raises(AuditPlanError, match="Speech Coverage"):
        build_audit_plan(
            transcript,
            recognitions,
            default_correction_audit_policy(),
            references_enrolled=False,
            speech_coverage=forged_coverage,
        )


def test_policy_and_generation_are_part_of_target_basis_and_ids() -> None:
    transcript, recognitions = make_case()
    base = build_audit_plan(
        transcript,
        recognitions,
        default_correction_audit_policy(),
        references_enrolled=False,
    )
    changed_policy = build_audit_plan(
        transcript,
        recognitions,
        default_correction_audit_policy(low_confidence_threshold=0.8),
        references_enrolled=False,
    )
    changed_generation = CanonicalTranscript.model_validate(
        {**transcript.model_dump(), "generation_id": "generation-2"}
    )
    next_generation = build_audit_plan(
        changed_generation,
        recognitions,
        default_correction_audit_policy(),
        references_enrolled=False,
    )

    assert base.basis_hash != changed_policy.basis_hash
    assert base.span_targets[0].id != changed_policy.span_targets[0].id
    assert base.basis_hash != next_generation.basis_hash
    assert base.span_targets[0].id != next_generation.span_targets[0].id


def test_swapped_left_and_right_speaker_labels_are_not_hidden_by_set_union() -> None:
    transcript, recognitions = make_case(
        speakers=("speaker-a", "speaker-b"),
        recognition_speakers=("speaker-b", "speaker-a"),
    )
    plan = build_audit_plan(
        transcript,
        recognitions,
        default_correction_audit_policy(),
        references_enrolled=False,
    )

    assert cell(plan, "boundary", 1, "speaker_transition").applicability == "required"


def test_reference_union_is_exact_even_when_enrollment_is_incomplete() -> None:
    transcript, recognitions = make_case()
    forged = CanonicalTranscript.model_validate(
        {**transcript.model_dump(), "reference_evidence_hash": HA}
    )

    with pytest.raises(AuditPlanError, match="retrieval union"):
        build_audit_plan(
            forged,
            recognitions,
            default_correction_audit_policy(),
            references_enrolled=True,
            reference_retrievals=(),
        )


def test_plan_rejects_reordered_or_missing_cartesian_cells_after_rehash() -> None:
    transcript, recognitions = make_case()
    plan = build_audit_plan(
        transcript,
        recognitions,
        default_correction_audit_policy(),
        references_enrolled=False,
    )
    payload = plan.model_dump()
    payload["cells"] = tuple(reversed(payload["cells"][:-1]))
    payload_without_hash = {key: value for key, value in payload.items() if key != "content_hash"}
    payload["content_hash"] = hash_object(payload_without_hash)

    with pytest.raises(ValidationError, match="Cartesian|ordered"):
        type(plan).model_validate(payload)


def test_assert_plan_is_an_exact_rebuild_not_a_hash_only_check() -> None:
    transcript, recognitions = make_case()
    plan = build_audit_plan(
        transcript,
        recognitions,
        default_correction_audit_policy(),
        references_enrolled=False,
    )
    assert (
        assert_audit_plan(
            plan,
            transcript,
            recognitions,
            default_correction_audit_policy(),
            references_enrolled=False,
        )
        == plan
    )

    with pytest.raises(AuditPlanError, match="not reproducible"):
        assert_audit_plan(
            plan,
            transcript,
            recognitions,
            default_correction_audit_policy(low_confidence_threshold=0.8),
            references_enrolled=False,
        )
