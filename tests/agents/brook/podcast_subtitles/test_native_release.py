"""Fail-closed Phase A release evaluation for native Subtitle V2 Full Audit."""

from __future__ import annotations

from dataclasses import dataclass, replace
from pathlib import Path

import pytest

from agents.brook.podcast_subtitles.audit_plan import (
    build_audit_plan,
    default_correction_audit_policy,
)
from agents.brook.podcast_subtitles.candidate_generation import (
    derive_candidate_group_set,
    derive_candidate_signal_set,
)
from agents.brook.podcast_subtitles.canonical import CanonicalBuildResult
from agents.brook.podcast_subtitles.correction_execution import (
    build_correction_audit_execution_plan,
    default_correction_audit_execution_policy,
)
from agents.brook.podcast_subtitles.full_audit_attestation import (
    FullAuditAggregateAttestationV2,
)
from agents.brook.podcast_subtitles.hashing import canonical_json_bytes, hash_object
from agents.brook.podcast_subtitles.native_release import (
    NativeReleaseAttestationV2,
    NativeReleaseError,
    NativeReleaseReviewRequiredV2,
    build_native_release_policy,
    evaluate_native_release,
    native_release_policy_bytes,
    native_release_result_bytes,
    verify_native_release_policy,
    verify_native_release_result,
)
from agents.brook.podcast_subtitles.risk import RiskRecord
from shared.schemas.podcast_subtitles_v2 import (
    AcceptancePolicySnapshot,
    CanonicalTranscript,
    GenerationWarning,
    ReviewIssue,
    SpeechCoverageReceipt,
    recognition_evidence_content_hash,
    speech_coverage_receipt_content_hash,
)
from shared.schemas.podcast_subtitles_v2_audio_audit import (
    AudioAuditCellAssessmentV2,
    AudioAuditExecutionRecordV2,
    AudioAuditProviderResponseReceiptV2,
    AudioCandidateDiscoverySetV2,
    AudioCellDispositionV2,
    AudioDispositionSetV2,
)
from shared.schemas.podcast_subtitles_v2_text_audit import (
    TextAuditCellAssessmentV2,
    TextAuditExecutionRecordV2,
    TextAuditProviderResponseReceiptV2,
    TextCandidateDiscoverySetV2,
    TextCellDispositionV2,
    TextDispositionSetV2,
)
from tests.agents.brook.podcast_subtitles.test_audit_plan import (
    make_coverage,
    make_seam,
)
from tests.agents.brook.podcast_subtitles.test_full_audit_attestation import (
    _addressed,
    _build,
    _clean_zero_signal_fixture,
    _complete_full_audit,
    _self_rehashed_aggregate,
)


@dataclass(frozen=True, slots=True)
class _ReleaseCase:
    result: CanonicalBuildResult
    audit_plan: object
    text_record: TextAuditExecutionRecordV2
    audio_record: AudioAuditExecutionRecordV2
    aggregate: FullAuditAggregateAttestationV2
    acceptance_policy: AcceptancePolicySnapshot
    speech_coverage: SpeechCoverageReceipt | None
    release_policy: object
    complete: tuple[object, ...]


def _release_case(
    tmp_path: Path,
    *,
    generation_id: str = "generation-release-a",
    episode_id: str = "episode-1",
    issue_specs: tuple[tuple[str, str, str, str | None], ...] = (),
    permit_low: bool = True,
    warning: bool = False,
    references_enrolled: bool = False,
    coverage_status: str = "completed",
    findings: bool = False,
) -> _ReleaseCase:
    tmp_path.mkdir(parents=True, exist_ok=True)
    fixture = _clean_zero_signal_fixture(tmp_path)
    audio_path, base_transcript, _, recognitions = fixture[4:8]
    acceptance_policy = AcceptancePolicySnapshot(permit_unresolved_low_risk=permit_low)
    risks: list[RiskRecord] = []
    for ordinal, (code, severity, status, resolution_event_id) in enumerate(issue_specs):
        issue = ReviewIssue(
            id=f"issue-release-{ordinal}-{code}",
            risk="text",
            severity=severity,
            code=code,
            span_ids=(base_transcript.spans[0].id,),
            status=status,
        )
        risks.append(
            RiskRecord(
                issue=issue,
                audio_span_ids=issue.span_ids,
                resolution_event_id=resolution_event_id,
            )
        )
    warnings = ()
    if warning:
        warnings = (
            GenerationWarning(
                id="warning-recognizer-confidence",
                code="recognizer_confidence_unavailable",
                evidence_hashes=tuple(
                    recognition_evidence_content_hash(item) for item in recognitions
                ),
                adapter_ids=("fixture-asr",),
                affected_token_count=len(base_transcript.tokens),
            ),
        )
    interim = CanonicalTranscript.model_validate(
        {
            **base_transcript.model_dump(mode="python"),
            "episode_id": episode_id,
            "generation_id": generation_id,
            "acceptance_policy": acceptance_policy,
            "review_issues": tuple(item.issue for item in risks),
            "generation_warnings": warnings,
            "full_audit_receipt_set_hash": None,
            "verified_speech_coverage_receipt_hash": None,
        }
    )
    coverage = (
        None
        if coverage_status == "absent"
        else make_coverage(interim, recognitions, status=coverage_status)
    )
    verified_coverage_hash = (
        speech_coverage_receipt_content_hash(coverage)
        if coverage is not None and coverage.status == "completed" and coverage.passed
        else None
    )
    transcript = CanonicalTranscript.model_validate(
        {
            **interim.model_dump(mode="python"),
            "verified_speech_coverage_receipt_hash": verified_coverage_hash,
        }
    )
    seam = make_seam(transcript, recognitions, seam_ms=None)
    audit_plan = build_audit_plan(
        transcript,
        recognitions,
        default_correction_audit_policy(),
        reference_retrievals=(),
        references_enrolled=references_enrolled,
        speech_coverage=coverage,
        seam_evidence=seam,
    )
    signals = derive_candidate_signal_set(
        audit_plan,
        transcript,
        recognitions,
        reference_retrievals=(),
        references_enrolled=references_enrolled,
        speech_coverage=coverage,
        seam_evidence=seam,
    )
    groups = derive_candidate_group_set(audit_plan, signals, transcript)
    execution = build_correction_audit_execution_plan(
        audit_plan,
        signals,
        groups,
        transcript,
        recognitions,
        default_correction_audit_execution_policy(),
        reference_retrievals=(),
        seam_evidence=seam,
        normalized_audio_path=audio_path,
    )
    complete = _complete_full_audit(
        tmp_path,
        findings=findings,
        fixture=(
            audit_plan,
            signals,
            groups,
            execution,
            audio_path,
            transcript,
            seam,
            recognitions,
            default_correction_audit_execution_policy(),
        ),
    )
    aggregate = _build(complete)
    text_record = complete[4]
    audio_record = complete[5]
    assert isinstance(text_record, TextAuditExecutionRecordV2)
    assert isinstance(audio_record, AudioAuditExecutionRecordV2)
    result = CanonicalBuildResult(
        outcome="needs_review",
        transcript=transcript,
        candidates=(),
        risks=tuple(risks),
        reference_evidence_hash=transcript.reference_evidence_hash,
        generation_warnings=warnings,
    )
    return _ReleaseCase(
        result=result,
        audit_plan=audit_plan,
        text_record=text_record,
        audio_record=audio_record,
        aggregate=aggregate,
        acceptance_policy=acceptance_policy,
        speech_coverage=coverage,
        release_policy=build_native_release_policy(acceptance_policy),
        complete=complete,
    )


def _evaluate(
    case: _ReleaseCase,
    *,
    current_ledger_hash: str | None = None,
):
    return evaluate_native_release(
        canonical_result=case.result,
        audit_plan=case.audit_plan,
        text_record=case.text_record,
        audio_record=case.audio_record,
        full_audit_aggregate=case.aggregate,
        acceptance_policy=case.acceptance_policy,
        speech_coverage_receipt=case.speech_coverage,
        current_ledger_hash=(current_ledger_hash or case.result.transcript.ledger_hash),
        policy=case.release_policy,
    )


def _replace_text_status(
    case: _ReleaseCase,
    status: str,
) -> _ReleaseCase:
    record = case.text_record
    required_cell = next(cell for cell in case.audit_plan.cells if cell.applicability == "required")
    old_assessment = next(item for item in record.assessments if item.cell_id == required_cell.id)
    assessment_payload = old_assessment.model_dump(mode="python", exclude={"id", "content_hash"})
    assessment_payload["status"] = status
    assessment_payload["rationale"] = f"Fixture {status}."
    assessment = _addressed(TextAuditCellAssessmentV2, assessment_payload)
    receipts = []
    for receipt in record.response_receipts:
        payload = receipt.model_dump(mode="python", exclude={"id", "content_hash"})
        payload["assessment_ids"] = tuple(
            assessment.id if item == old_assessment.id else item for item in receipt.assessment_ids
        )
        receipts.append(_addressed(TextAuditProviderResponseReceiptV2, payload))
    assessments = tuple(
        assessment if item.id == old_assessment.id else item for item in record.assessments
    )
    dispositions = list(record.text_disposition_set.dispositions)
    disposition_index = next(
        index for index, item in enumerate(dispositions) if item.cell_id == required_cell.id
    )
    disposition_payload = dispositions[disposition_index].model_dump(
        mode="python", exclude={"id", "content_hash"}
    )
    disposition_payload.update({"status": status, "assessment_id": assessment.id})
    dispositions[disposition_index] = _addressed(
        TextCellDispositionV2,
        disposition_payload,
    )
    disposition_set_payload = record.text_disposition_set.model_dump(
        mode="python", exclude={"id", "content_hash"}
    )
    disposition_set_payload.update(
        {
            "response_receipt_ids": tuple(item.id for item in receipts),
            "dispositions": tuple(dispositions),
        }
    )
    disposition_set = _addressed(TextDispositionSetV2, disposition_set_payload)
    discovery_payload = record.candidate_discovery_set.model_dump(
        mode="python", exclude={"id", "content_hash"}
    )
    discovery_payload["text_disposition_set_id"] = disposition_set.id
    discovery_set = _addressed(TextCandidateDiscoverySetV2, discovery_payload)
    record_payload = record.model_dump(mode="python", exclude={"id", "content_hash"})
    record_payload.update(
        {
            "response_receipts": tuple(receipts),
            "assessments": assessments,
            "text_disposition_set": disposition_set,
            "candidate_discovery_set": discovery_set,
        }
    )
    revised = _addressed(TextAuditExecutionRecordV2, record_payload)
    complete = (*case.complete[:4], revised, case.audio_record)
    return replace(
        case,
        text_record=revised,
        aggregate=_build(complete),
        complete=complete,
    )


def _replace_audio_status(
    case: _ReleaseCase,
    status: str,
) -> _ReleaseCase:
    record = case.audio_record
    required_cell = next(cell for cell in case.audit_plan.cells if cell.applicability == "required")
    old_assessment = next(item for item in record.assessments if item.cell_id == required_cell.id)
    assessment_payload = old_assessment.model_dump(mode="python", exclude={"id", "content_hash"})
    assessment_payload["status"] = status
    assessment_payload["rationale"] = f"Fixture {status}."
    assessment = _addressed(AudioAuditCellAssessmentV2, assessment_payload)
    receipts = []
    for receipt in record.response_receipts:
        payload = receipt.model_dump(mode="python", exclude={"id", "content_hash"})
        payload["assessment_ids"] = tuple(
            assessment.id if item == old_assessment.id else item for item in receipt.assessment_ids
        )
        receipts.append(_addressed(AudioAuditProviderResponseReceiptV2, payload))
    assessments = tuple(
        assessment if item.id == old_assessment.id else item for item in record.assessments
    )
    dispositions = list(record.audio_disposition_set.dispositions)
    disposition_index = next(
        index for index, item in enumerate(dispositions) if item.cell_id == required_cell.id
    )
    disposition_payload = dispositions[disposition_index].model_dump(
        mode="python", exclude={"id", "content_hash"}
    )
    disposition_payload.update({"status": status, "assessment_id": assessment.id})
    dispositions[disposition_index] = _addressed(
        AudioCellDispositionV2,
        disposition_payload,
    )
    disposition_set_payload = record.audio_disposition_set.model_dump(
        mode="python", exclude={"id", "content_hash"}
    )
    disposition_set_payload.update(
        {
            "response_receipt_ids": tuple(item.id for item in receipts),
            "dispositions": tuple(dispositions),
        }
    )
    disposition_set = _addressed(AudioDispositionSetV2, disposition_set_payload)
    discovery_payload = record.candidate_discovery_set.model_dump(
        mode="python", exclude={"id", "content_hash"}
    )
    discovery_payload["audio_disposition_set_id"] = disposition_set.id
    discovery_set = _addressed(AudioCandidateDiscoverySetV2, discovery_payload)
    record_payload = record.model_dump(mode="python", exclude={"id", "content_hash"})
    record_payload.update(
        {
            "response_receipts": tuple(receipts),
            "assessments": assessments,
            "audio_disposition_set": disposition_set,
            "candidate_discovery_set": discovery_set,
        }
    )
    revised = _addressed(AudioAuditExecutionRecordV2, record_payload)
    complete = (*case.complete[:5], revised)
    return replace(
        case,
        audio_record=revised,
        aggregate=_build(complete),
        complete=complete,
    )


def test_zero_material_risk_exact_clean_audit_produces_replayable_attestation(
    tmp_path: Path,
) -> None:
    case = _release_case(tmp_path)

    approved = _evaluate(case)

    assert isinstance(approved, NativeReleaseAttestationV2)
    assert approved.release_status == "approved"
    assert approved.blocking_disposition_ids == ()
    assert approved.full_audit_aggregate_id == case.aggregate.id
    assert approved.full_audit_aggregate_authority == (
        "no_text_mutation_no_correction_decision_no_arbitration_no_release_approval"
    )
    assert all(not item.blocks_release for item in approved.system_dispositions)
    assert all(not item.blocks_release for item in approved.cell_dispositions)
    assert (
        verify_native_release_result(
            native_release_result_bytes(approved),
            canonical_result=case.result,
            audit_plan=case.audit_plan,
            text_record=case.text_record,
            audio_record=case.audio_record,
            full_audit_aggregate=case.aggregate,
            acceptance_policy=case.acceptance_policy,
            speech_coverage_receipt=case.speech_coverage,
            current_ledger_hash=case.result.transcript.ledger_hash,
            policy=case.release_policy,
        )
        == approved
    )
    assert (
        verify_native_release_policy(native_release_policy_bytes(case.release_policy))
        == case.release_policy
    )


@pytest.mark.parametrize("severity", ("medium", "high", "blocking"))
def test_each_unresolved_material_issue_is_named_once_and_blocks(
    tmp_path: Path,
    severity: str,
) -> None:
    case = _release_case(
        tmp_path,
        issue_specs=(("fixture_material", severity, "unresolved", None),),
    )

    result = _evaluate(case)

    assert isinstance(result, NativeReleaseReviewRequiredV2)
    assert len(result.issue_dispositions) == 1
    disposition = result.issue_dispositions[0]
    assert disposition.issue_id == case.result.risks[0].issue.id
    assert disposition.decision == "blocked_unresolved_material"
    assert disposition.id in result.blocking_disposition_ids


def test_phase_a_resolved_issue_without_exact_issue_bound_decision_still_blocks(
    tmp_path: Path,
) -> None:
    case = _release_case(
        tmp_path,
        issue_specs=(("fixture_resolved", "low", "resolved", "event-looking-only"),),
    )

    result = _evaluate(case)

    assert isinstance(result, NativeReleaseReviewRequiredV2)
    assert result.issue_dispositions[0].resolution_event_id == "event-looking-only"
    assert result.issue_dispositions[0].decision == (
        "blocked_resolved_without_verified_typed_event"
    )


@pytest.mark.parametrize(
    ("permit_low", "expected_type", "expected_decision"),
    (
        (
            True,
            NativeReleaseAttestationV2,
            "visible_unresolved_low_permitted_by_acceptance_policy",
        ),
        (
            False,
            NativeReleaseReviewRequiredV2,
            "blocked_unresolved_low_by_acceptance_policy",
        ),
    ),
)
def test_low_issue_follows_frozen_acceptance_policy_but_is_always_listed(
    tmp_path: Path,
    permit_low: bool,
    expected_type: type,
    expected_decision: str,
) -> None:
    case = _release_case(
        tmp_path,
        issue_specs=(("fixture_low", "low", "unresolved", None),),
        permit_low=permit_low,
    )

    result = _evaluate(case)

    assert isinstance(result, expected_type)
    assert len(result.issue_dispositions) == 1
    assert result.issue_dispositions[0].decision == expected_decision


@pytest.mark.parametrize(
    "code",
    (
        "reference_retrieval_failed",
        "reference_retrieval_incomplete",
        "orthographic_multi_option_ambiguity",
        "recognition_disagreement",
        "native_full_audit_requires_resolution",
    ),
)
def test_named_integrity_or_ambiguity_issue_blocks_even_if_downgraded_to_low(
    tmp_path: Path,
    code: str,
) -> None:
    case = _release_case(
        tmp_path,
        issue_specs=((code, "low", "unresolved", None),),
        permit_low=True,
    )

    result = _evaluate(case)

    assert isinstance(result, NativeReleaseReviewRequiredV2)
    assert result.issue_dispositions[0].decision == "blocked_closed_policy_issue_code"


def test_text_requires_audio_passes_only_with_same_cell_exact_audio_clean(
    tmp_path: Path,
) -> None:
    case = _replace_text_status(
        _release_case(tmp_path),
        "unresolved_requires_audio",
    )

    approved = _evaluate(case)

    assert isinstance(approved, NativeReleaseAttestationV2)
    resolved_cell = next(
        item
        for item in approved.cell_dispositions
        if item.text_status == "unresolved_requires_audio"
    )
    assert resolved_cell.pass_basis == "text_requires_audio_exact_same_cell_audio_clean"

    unresolved_audio = _replace_audio_status(case, "unresolved_insufficient_evidence")
    blocked = _evaluate(unresolved_audio)
    assert isinstance(blocked, NativeReleaseReviewRequiredV2)
    same_cell = next(
        item for item in blocked.cell_dispositions if item.cell_id == resolved_cell.cell_id
    )
    assert "text_requires_audio_without_exact_audio_clean" in same_cell.blocking_reasons


def test_text_insufficient_audio_conflict_and_discoveries_each_block(
    tmp_path: Path,
) -> None:
    text_insufficient = _replace_text_status(
        _release_case(tmp_path / "text"),
        "unresolved_insufficient_evidence",
    )
    assert isinstance(_evaluate(text_insufficient), NativeReleaseReviewRequiredV2)

    audio_conflict = _replace_audio_status(
        _release_case(tmp_path / "audio"),
        "conflict",
    )
    assert isinstance(_evaluate(audio_conflict), NativeReleaseReviewRequiredV2)

    findings = _release_case(tmp_path / "findings", findings=True)
    result = _evaluate(findings)
    assert isinstance(result, NativeReleaseReviewRequiredV2)
    system_codes = {item.code for item in result.system_dispositions if item.blocks_release}
    assert {"text_discoveries_present", "audio_discoveries_present"} <= system_codes


def test_unavailable_cell_reference_incomplete_speech_failure_and_stale_ledger_block(
    tmp_path: Path,
) -> None:
    unavailable = _release_case(tmp_path / "coverage", coverage_status="absent")
    unavailable_result = _evaluate(unavailable)
    assert isinstance(unavailable_result, NativeReleaseReviewRequiredV2)
    assert any(
        "inherited_unavailable" in item.blocking_reasons
        for item in unavailable_result.cell_dispositions
    )

    failed_coverage = _release_case(tmp_path / "coverage-failed", coverage_status="failed")
    failed_coverage_result = _evaluate(failed_coverage)
    assert isinstance(failed_coverage_result, NativeReleaseReviewRequiredV2)
    assert any(
        item.code == "speech_coverage_failed" and item.blocks_release
        for item in failed_coverage_result.system_dispositions
    )

    reference_incomplete = _release_case(
        tmp_path / "reference",
        references_enrolled=True,
    )
    reference_result = _evaluate(reference_incomplete)
    assert isinstance(reference_result, NativeReleaseReviewRequiredV2)
    assert any(
        item.code == "reference_retrieval_incomplete" and item.blocks_release
        for item in reference_result.system_dispositions
    )

    stale = _release_case(tmp_path / "ledger")
    stale_result = _evaluate(stale, current_ledger_hash="f" * 64)
    assert isinstance(stale_result, NativeReleaseReviewRequiredV2)
    assert any(
        item.code == "ledger_stale" and item.blocks_release
        for item in stale_result.system_dispositions
    )


def test_confidence_warning_requires_complete_clean_exact_audio_audit(
    tmp_path: Path,
) -> None:
    clean = _release_case(tmp_path / "clean", warning=True)
    approved = _evaluate(clean)
    assert isinstance(approved, NativeReleaseAttestationV2)
    assert approved.warning_dispositions[0].source_status == "requires_full_audit"
    assert approved.warning_dispositions[0].decision == (
        "release_gate_satisfied_by_complete_clean_exact_audio_audit"
    )

    incomplete = _replace_audio_status(
        _release_case(tmp_path / "incomplete", warning=True),
        "unresolved_insufficient_evidence",
    )
    blocked = _evaluate(incomplete)
    assert isinstance(blocked, NativeReleaseReviewRequiredV2)
    assert blocked.warning_dispositions[0].decision == (
        "blocked_without_complete_clean_exact_audio_audit"
    )

    discoveries = _release_case(tmp_path / "discoveries", warning=True, findings=True)
    discovery_result = _evaluate(discoveries)
    assert isinstance(discovery_result, NativeReleaseReviewRequiredV2)
    assert discovery_result.warning_dispositions[0].decision == (
        "blocked_without_complete_clean_exact_audio_audit"
    )


def test_cross_generation_aggregate_tamper_and_noncanonical_bytes_fail_closed(
    tmp_path: Path,
) -> None:
    first = _release_case(tmp_path / "first", generation_id="generation-release-a")
    other = _release_case(tmp_path / "other", generation_id="generation-release-b")

    with pytest.raises(NativeReleaseError):
        evaluate_native_release(
            canonical_result=first.result,
            audit_plan=first.audit_plan,
            text_record=other.text_record,
            audio_record=first.audio_record,
            full_audit_aggregate=first.aggregate,
            acceptance_policy=first.acceptance_policy,
            speech_coverage_receipt=first.speech_coverage,
            current_ledger_hash=first.result.transcript.ledger_hash,
            policy=first.release_policy,
        )

    forged = _self_rehashed_aggregate(first.aggregate, text_record_id="0" * 64)
    with pytest.raises(NativeReleaseError):
        evaluate_native_release(
            canonical_result=first.result,
            audit_plan=first.audit_plan,
            text_record=first.text_record,
            audio_record=first.audio_record,
            full_audit_aggregate=forged,
            acceptance_policy=first.acceptance_policy,
            speech_coverage_receipt=first.speech_coverage,
            current_ledger_hash=first.result.transcript.ledger_hash,
            policy=first.release_policy,
        )

    for aggregate_update in (
        {"recognition_evidence_hashes": ("0" * 64,)},
        {"boundary_constraint_receipt_hash": "0" * 64},
        {"text_policy_hash": "0" * 64},
        {"audio_adapter_identity_hash": "0" * 64},
    ):
        forged_binding = _self_rehashed_aggregate(first.aggregate, **aggregate_update)
        with pytest.raises(NativeReleaseError):
            evaluate_native_release(
                canonical_result=first.result,
                audit_plan=first.audit_plan,
                text_record=first.text_record,
                audio_record=first.audio_record,
                full_audit_aggregate=forged_binding,
                acceptance_policy=first.acceptance_policy,
                speech_coverage_receipt=first.speech_coverage,
                current_ledger_hash=first.result.transcript.ledger_hash,
                policy=first.release_policy,
            )

    approved = _evaluate(first)
    exact = native_release_result_bytes(approved)
    with pytest.raises(NativeReleaseError):
        verify_native_release_result(
            exact + b" ",
            canonical_result=first.result,
            audit_plan=first.audit_plan,
            text_record=first.text_record,
            audio_record=first.audio_record,
            full_audit_aggregate=first.aggregate,
            acceptance_policy=first.acceptance_policy,
            speech_coverage_receipt=first.speech_coverage,
            current_ledger_hash=first.result.transcript.ledger_hash,
            policy=first.release_policy,
        )


@pytest.mark.parametrize("mutation", ("missing", "duplicate", "reordered"))
def test_cell_disposition_coverage_mutation_fails_closed(
    tmp_path: Path,
    mutation: str,
) -> None:
    case = _release_case(tmp_path)
    dispositions = list(case.text_record.text_disposition_set.dispositions)
    all_cell_ids = list(case.text_record.text_disposition_set.all_cell_ids)
    if mutation == "missing":
        dispositions.pop()
        all_cell_ids.pop()
    elif mutation == "duplicate":
        dispositions[1] = dispositions[0]
        all_cell_ids[1] = all_cell_ids[0]
    else:
        dispositions[0], dispositions[1] = dispositions[1], dispositions[0]
        all_cell_ids[0], all_cell_ids[1] = all_cell_ids[1], all_cell_ids[0]
    forged_set = case.text_record.text_disposition_set.model_copy(
        update={
            "all_cell_ids": tuple(all_cell_ids),
            "dispositions": tuple(dispositions),
        }
    )
    forged_record = case.text_record.model_copy(update={"text_disposition_set": forged_set})

    with pytest.raises(NativeReleaseError):
        evaluate_native_release(
            canonical_result=case.result,
            audit_plan=case.audit_plan,
            text_record=forged_record,
            audio_record=case.audio_record,
            full_audit_aggregate=case.aggregate,
            acceptance_policy=case.acceptance_policy,
            speech_coverage_receipt=case.speech_coverage,
            current_ledger_hash=case.result.transcript.ledger_hash,
            policy=case.release_policy,
        )


def test_review_result_replay_rejects_missing_or_reordered_issue_dispositions(
    tmp_path: Path,
) -> None:
    case = _release_case(
        tmp_path,
        issue_specs=(
            ("fixture-a", "medium", "unresolved", None),
            ("fixture-b", "high", "unresolved", None),
        ),
    )
    result = _evaluate(case)
    assert isinstance(result, NativeReleaseReviewRequiredV2)

    for dispositions in (
        result.issue_dispositions[:-1],
        tuple(reversed(result.issue_dispositions)),
        (result.issue_dispositions[0], result.issue_dispositions[0]),
    ):
        payload = result.model_dump(mode="python", exclude={"id", "content_hash"})
        payload["issue_dispositions"] = dispositions
        payload["blocking_disposition_ids"] = tuple(
            item.id
            for group in (
                result.system_dispositions,
                result.cell_dispositions,
                dispositions,
                result.warning_dispositions,
            )
            for item in group
            if item.blocks_release
        )
        digest = hash_object(payload)
        forged_bytes = canonical_json_bytes({**payload, "id": digest, "content_hash": digest})
        with pytest.raises(NativeReleaseError):
            verify_native_release_result(
                forged_bytes,
                canonical_result=case.result,
                audit_plan=case.audit_plan,
                text_record=case.text_record,
                audio_record=case.audio_record,
                full_audit_aggregate=case.aggregate,
                acceptance_policy=case.acceptance_policy,
                speech_coverage_receipt=case.speech_coverage,
                current_ledger_hash=case.result.transcript.ledger_hash,
                policy=case.release_policy,
            )
