"""Contract tests for the native Text + Audio Full Audit aggregate."""

from __future__ import annotations

import json
import wave
from io import BytesIO
from pathlib import Path

import pytest

from agents.brook.podcast_subtitles.audio_audit_execution import (
    AudioFullAuditExecutor,
    build_audio_audit_adapter_identity,
)
from agents.brook.podcast_subtitles.audit_plan import (
    build_audit_plan,
    default_correction_audit_policy,
)
from agents.brook.podcast_subtitles.candidate_generation import (
    derive_candidate_group_set,
    derive_candidate_signal_set,
)
from agents.brook.podcast_subtitles.correction_execution import (
    build_correction_audit_execution_plan,
    default_correction_audit_execution_policy,
    materialize_audio_correction_packet_sources,
    materialize_text_correction_packet_sources,
)
from agents.brook.podcast_subtitles.full_audit_attestation import (
    FullAuditAggregateAttestationV2,
    FullAuditAttestationError,
    build_full_audit_aggregate,
    full_audit_aggregate_bytes,
    full_audit_aggregate_digest,
    verify_full_audit_aggregate,
)
from agents.brook.podcast_subtitles.hashing import canonical_json_bytes, hash_object, sha256_bytes
from agents.brook.podcast_subtitles.text_audit_execution import (
    TextFullAuditExecutor,
    build_text_audit_adapter_identity,
)
from shared.schemas.podcast_subtitles_v2 import (
    CanonicalTranscript,
    RecognitionEvidence,
    recognition_evidence_set_hash,
)
from shared.schemas.podcast_subtitles_v2_audio_audit import (
    AudioAuditExecutionRecordV2,
    AudioAuditProviderRequestV2,
    AudioCandidateDiscoverySetV2,
    AudioDispositionSetV2,
)
from shared.schemas.podcast_subtitles_v2_text_audit import (
    TextAuditCellAssessmentV2,
    TextAuditExecutionRecordV2,
    TextAuditProviderRequestV2,
    TextAuditProviderResponseReceiptV2,
    TextCandidateDiscoverySetV2,
    TextCellDispositionV2,
    TextDispositionSetV2,
)
from tests.agents.brook.podcast_subtitles.test_audio_audit_execution import (
    _response_bytes as audio_response_bytes,
)
from tests.agents.brook.podcast_subtitles.test_audio_audit_execution import (
    _response_payload as audio_response_payload,
)
from tests.agents.brook.podcast_subtitles.test_audio_audit_execution import (
    _typed_source as audio_typed_source,
)
from tests.agents.brook.podcast_subtitles.test_audit_plan import (
    make_case,
    make_coverage,
    make_seam,
)
from tests.agents.brook.podcast_subtitles.test_correction_execution import build_fixture
from tests.agents.brook.podcast_subtitles.test_text_audit_execution import (
    _response_bytes as text_response_bytes,
)

_AUDIO_ONLY = {"speaker_identity", "chunk_seam", "speech_coverage", "speaker_transition"}


def _complete_full_audit(
    tmp_path: Path,
    *,
    findings: bool = False,
    fixture: tuple[object, ...] | None = None,
    include_bytes: bool = False,
):
    fixture = fixture or build_fixture(tmp_path)
    audit_plan, signal_set, group_set, execution = fixture[:4]
    audio_path, transcript, seam, recognitions = fixture[4:8]

    text_sources: dict[str, bytes] = {}
    for packet in (item for item in execution.packets if item.modality == "text"):
        text_sources.update(
            materialize_text_correction_packet_sources(
                execution,
                packet.id,
                audit_plan,
                signal_set,
                group_set,
                transcript,
                recognitions,
                seam_evidence=seam,
            )
        )
    text_finding_cell = next(
        (
            item
            for item in audit_plan.cells
            if item.applicability == "required" and item.category not in _AUDIO_ONLY
        ),
        None,
    )

    def text_runner(request_bytes: bytes) -> bytes:
        request = TextAuditProviderRequestV2.model_validate_json(request_bytes)
        finding_cell_id = (
            text_finding_cell.id
            if findings
            and text_finding_cell is not None
            and text_finding_cell.id in request.expected_cell_ids
            else None
        )
        return text_response_bytes(request_bytes, finding_cell_id=finding_cell_id)

    text_result = TextFullAuditExecutor(
        identity=build_text_audit_adapter_identity(
            adapter="fixture-text-auditor",
            adapter_version="1",
            model="fixture-text-model",
            model_version="1",
            execution_mode="fixture",
        ),
        runner=text_runner,
    ).execute(execution, audit_plan, text_sources)

    audio_sources: dict[str, bytes] = {}
    for packet in (item for item in execution.packets if item.modality == "audio"):
        audio_sources.update(
            materialize_audio_correction_packet_sources(
                execution,
                packet.id,
                audit_plan,
                signal_set,
                group_set,
                transcript,
                recognitions,
                normalized_audio_path=audio_path,
                seam_evidence=seam,
            )
        )
    audio_finding_created = False

    def audio_runner(request_bytes: bytes, clip_bytes: bytes) -> bytes:
        nonlocal audio_finding_created
        if not findings or audio_finding_created:
            return audio_response_bytes(request_bytes, clip_bytes)
        payload = audio_response_payload(request_bytes)
        request = AudioAuditProviderRequestV2.model_validate_json(request_bytes)
        canonical = audio_typed_source(request, "canonical_transcript")
        assessment = payload["assessments"][0]
        assert isinstance(assessment, dict)
        token_id = assessment["cited_token_ids"][0]
        token = next(item for item in canonical.tokens if item.id == token_id)
        assessment.update(
            {
                "status": "finding",
                "rationale": "The exact bounded clip supports another lexical form.",
                "affected_token_ids": (token_id,),
                "observed_text": token.text,
                "candidate_text": f"{token.text}改",
                "evidence_basis": "exact_audio_clip",
            }
        )
        audio_finding_created = True
        return canonical_json_bytes(payload)

    audio_result = AudioFullAuditExecutor(
        identity=build_audio_audit_adapter_identity(
            adapter="fixture-audio-auditor",
            adapter_version="1",
            model="fixture-audio-model",
            model_version="1",
            execution_mode="fixture",
        ),
        runner=audio_runner,
        workspace_root=tmp_path / "audio-workspace",
    ).execute(execution, audit_plan, audio_sources)
    complete = (
        audit_plan,
        signal_set,
        group_set,
        execution,
        text_result.record,
        audio_result.record,
    )
    if not include_bytes:
        return complete
    return (
        *complete,
        text_result.request_bytes,
        text_result.response_bytes,
        audio_result.request_bytes,
        audio_result.response_bytes,
        audio_result.clip_bytes,
    )


def _clean_zero_signal_fixture(tmp_path: Path) -> tuple[object, ...]:
    wav = BytesIO()
    with wave.open(wav, "wb") as sink:
        sink.setnchannels(1)
        sink.setsampwidth(2)
        sink.setframerate(8_000)
        sink.writeframes(b"\x00\x00" * 16_000)
    audio_bytes = wav.getvalue()
    audio_hash = sha256_bytes(audio_bytes)
    audio_path = tmp_path / "clean-normalized.wav"
    audio_path.write_bytes(audio_bytes)

    transcript, recognitions = make_case()
    recognition = RecognitionEvidence.model_validate(
        {
            **recognitions[0].model_dump(mode="python"),
            "normalized_audio_hash": audio_hash,
        }
    )
    recognitions = (recognition,)
    transcript = CanonicalTranscript.model_validate(
        {
            **transcript.model_dump(mode="python"),
            "normalized_audio_hash": audio_hash,
            "evidence_hash": recognition_evidence_set_hash(recognitions),
        }
    )
    coverage = make_coverage(transcript, recognitions)
    seam = make_seam(transcript, recognitions, seam_ms=None)
    audit_plan = build_audit_plan(
        transcript,
        recognitions,
        default_correction_audit_policy(),
        references_enrolled=False,
        speech_coverage=coverage,
        seam_evidence=seam,
    )
    signal_set = derive_candidate_signal_set(
        audit_plan,
        transcript,
        recognitions,
        references_enrolled=False,
        speech_coverage=coverage,
        seam_evidence=seam,
    )
    group_set = derive_candidate_group_set(audit_plan, signal_set, transcript)
    execution = build_correction_audit_execution_plan(
        audit_plan,
        signal_set,
        group_set,
        transcript,
        recognitions,
        default_correction_audit_execution_policy(),
        seam_evidence=seam,
        normalized_audio_path=audio_path,
    )
    return (
        audit_plan,
        signal_set,
        group_set,
        execution,
        audio_path,
        transcript,
        seam,
        recognitions,
        default_correction_audit_execution_policy(),
    )


@pytest.fixture
def complete_full_audit(tmp_path: Path):
    return _complete_full_audit(tmp_path)


def _build(complete: tuple[object, ...]) -> FullAuditAggregateAttestationV2:
    audit_plan, signal_set, group_set, execution, text_record, audio_record = complete[:6]
    raw = complete[6:]
    return build_full_audit_aggregate(
        audit_plan=audit_plan,
        candidate_signal_set=signal_set,
        candidate_group_set=group_set,
        execution_plan=execution,
        text_record=text_record,
        audio_record=audio_record,
        **(
            {
                "text_request_bytes": raw[0],
                "text_response_bytes": raw[1],
                "audio_request_bytes": raw[2],
                "audio_response_bytes": raw[3],
                "audio_clip_bytes": raw[4],
            }
            if raw
            else {}
        ),
    )


def _verify(
    exact_bytes: bytes,
    complete: tuple[object, ...],
) -> FullAuditAggregateAttestationV2:
    audit_plan, signal_set, group_set, execution, text_record, audio_record = complete[:6]
    raw = complete[6:]
    return verify_full_audit_aggregate(
        exact_bytes,
        audit_plan=audit_plan,
        candidate_signal_set=signal_set,
        candidate_group_set=group_set,
        execution_plan=execution,
        text_record=text_record,
        audio_record=audio_record,
        **(
            {
                "text_request_bytes": raw[0],
                "text_response_bytes": raw[1],
                "audio_request_bytes": raw[2],
                "audio_response_bytes": raw[3],
                "audio_clip_bytes": raw[4],
            }
            if raw
            else {}
        ),
    )


def _addressed(model_type, payload: dict[str, object]):
    digest = hash_object(payload)
    return model_type(**payload, id=digest, content_hash=digest)


def _with_coverage_mutation(
    complete: tuple[object, ...],
    *,
    modality: str,
    mutation: str,
) -> tuple[object, ...]:
    audit_plan, signal_set, group_set, execution, text_record, audio_record = complete
    record = text_record if modality == "text" else audio_record
    disposition_set = (
        record.text_disposition_set if modality == "text" else record.audio_disposition_set
    )
    cells = list(disposition_set.all_cell_ids)
    dispositions = list(disposition_set.dispositions)
    if mutation == "missing":
        index = next(
            index for index, cell in enumerate(audit_plan.cells) if cell.applicability != "required"
        )
        cells.pop(index)
        dispositions.pop(index)
    elif mutation == "reordered":
        cells[0], cells[1] = cells[1], cells[0]
        dispositions[0], dispositions[1] = dispositions[1], dispositions[0]
    elif mutation == "duplicate":
        cells[1] = cells[0]
        dispositions[1] = dispositions[0]
        invalid_set = disposition_set.model_copy(
            update={"all_cell_ids": tuple(cells), "dispositions": tuple(dispositions)}
        )
        forged_record = record.model_copy(
            update={
                (
                    "text_disposition_set" if modality == "text" else "audio_disposition_set"
                ): invalid_set
            }
        )
        return (
            audit_plan,
            signal_set,
            group_set,
            execution,
            forged_record if modality == "text" else text_record,
            forged_record if modality == "audio" else audio_record,
        )
    else:  # pragma: no cover - helper guard
        raise AssertionError(mutation)

    set_payload = disposition_set.model_dump(mode="python", exclude={"id", "content_hash"})
    set_payload.update({"all_cell_ids": tuple(cells), "dispositions": tuple(dispositions)})
    set_type = TextDispositionSetV2 if modality == "text" else AudioDispositionSetV2
    revised_set = _addressed(set_type, set_payload)
    discovery = record.candidate_discovery_set
    discovery_payload = discovery.model_dump(mode="python", exclude={"id", "content_hash"})
    discovery_payload[
        "text_disposition_set_id" if modality == "text" else "audio_disposition_set_id"
    ] = revised_set.id
    discovery_type = (
        TextCandidateDiscoverySetV2 if modality == "text" else AudioCandidateDiscoverySetV2
    )
    revised_discovery = _addressed(discovery_type, discovery_payload)
    record_payload = record.model_dump(mode="python", exclude={"id", "content_hash"})
    record_payload["text_disposition_set" if modality == "text" else "audio_disposition_set"] = (
        revised_set
    )
    record_payload["candidate_discovery_set"] = revised_discovery
    record_type = TextAuditExecutionRecordV2 if modality == "text" else AudioAuditExecutionRecordV2
    revised_record = _addressed(record_type, record_payload)
    return (
        audit_plan,
        signal_set,
        group_set,
        execution,
        revised_record if modality == "text" else text_record,
        revised_record if modality == "audio" else audio_record,
    )


def _text_record_with_required_cell_falsely_inherited(
    complete: tuple[object, ...],
) -> tuple[object, ...]:
    audit_plan, signal_set, group_set, execution, text_record, audio_record = complete
    dispositions = list(text_record.text_disposition_set.dispositions)
    target_index = next(
        index for index, cell in enumerate(audit_plan.cells) if cell.applicability == "required"
    )
    original = dispositions[target_index]
    assert original.assessment_id is not None
    inherited_payload = original.model_dump(mode="python", exclude={"id", "content_hash"})
    inherited_payload.update(
        {
            "status": "inherited_unavailable",
            "source": "inherited_audit_plan_applicability",
            "applicability_reason": "upstream_missing",
            "assessment_id": None,
            "packet_id": None,
        }
    )
    dispositions[target_index] = _addressed(TextCellDispositionV2, inherited_payload)

    receipts: list[TextAuditProviderResponseReceiptV2] = []
    for receipt in text_record.response_receipts:
        receipt_payload = receipt.model_dump(mode="python", exclude={"id", "content_hash"})
        receipt_payload["assessment_ids"] = tuple(
            item for item in receipt.assessment_ids if item != original.assessment_id
        )
        receipts.append(_addressed(TextAuditProviderResponseReceiptV2, receipt_payload))
    assessments = tuple(
        item for item in text_record.assessments if item.id != original.assessment_id
    )
    set_payload = text_record.text_disposition_set.model_dump(
        mode="python", exclude={"id", "content_hash"}
    )
    set_payload.update(
        {
            "response_receipt_ids": tuple(item.id for item in receipts),
            "dispositions": tuple(dispositions),
        }
    )
    disposition_set = _addressed(TextDispositionSetV2, set_payload)
    discovery_payload = text_record.candidate_discovery_set.model_dump(
        mode="python", exclude={"id", "content_hash"}
    )
    discovery_payload["text_disposition_set_id"] = disposition_set.id
    discovery_set = _addressed(TextCandidateDiscoverySetV2, discovery_payload)
    record_payload = text_record.model_dump(mode="python", exclude={"id", "content_hash"})
    record_payload.update(
        {
            "response_receipts": tuple(receipts),
            "assessments": assessments,
            "text_disposition_set": disposition_set,
            "candidate_discovery_set": discovery_set,
        }
    )
    forged = _addressed(TextAuditExecutionRecordV2, record_payload)
    return audit_plan, signal_set, group_set, execution, forged, audio_record


def _text_record_with_nonrequired_provider_overreach(
    complete: tuple[object, ...],
) -> tuple[object, ...]:
    audit_plan, signal_set, group_set, execution, text_record, audio_record = complete
    target_index = next(
        index for index, cell in enumerate(audit_plan.cells) if cell.applicability != "required"
    )
    target_cell = audit_plan.cells[target_index]
    original_assessment = text_record.assessments[0]
    assessment_payload = original_assessment.model_dump(
        mode="python", exclude={"id", "content_hash"}
    )
    assessment_payload.update(
        {
            "cell_id": target_cell.id,
            "target_id": target_cell.target_id,
            "category": target_cell.category,
        }
    )
    extra_assessment = _addressed(TextAuditCellAssessmentV2, assessment_payload)

    receipts: list[TextAuditProviderResponseReceiptV2] = []
    for receipt in text_record.response_receipts:
        receipt_payload = receipt.model_dump(mode="python", exclude={"id", "content_hash"})
        if receipt.request_id == original_assessment.request_id:
            receipt_payload["assessment_ids"] = (*receipt.assessment_ids, extra_assessment.id)
        receipts.append(_addressed(TextAuditProviderResponseReceiptV2, receipt_payload))
    assessment_by_id = {item.id: item for item in (*text_record.assessments, extra_assessment)}
    assessments = tuple(
        assessment_by_id[item_id] for receipt in receipts for item_id in receipt.assessment_ids
    )

    dispositions = list(text_record.text_disposition_set.dispositions)
    disposition_payload = dispositions[target_index].model_dump(
        mode="python", exclude={"id", "content_hash"}
    )
    disposition_payload.update(
        {
            "status": extra_assessment.status,
            "source": "provider_text_assessment",
            "assessment_id": extra_assessment.id,
            "packet_id": extra_assessment.packet_id,
        }
    )
    dispositions[target_index] = _addressed(TextCellDispositionV2, disposition_payload)
    set_payload = text_record.text_disposition_set.model_dump(
        mode="python", exclude={"id", "content_hash"}
    )
    set_payload.update(
        {
            "response_receipt_ids": tuple(item.id for item in receipts),
            "dispositions": tuple(dispositions),
        }
    )
    disposition_set = _addressed(TextDispositionSetV2, set_payload)
    discovery_payload = text_record.candidate_discovery_set.model_dump(
        mode="python", exclude={"id", "content_hash"}
    )
    discovery_payload["text_disposition_set_id"] = disposition_set.id
    discovery_set = _addressed(TextCandidateDiscoverySetV2, discovery_payload)
    record_payload = text_record.model_dump(mode="python", exclude={"id", "content_hash"})
    record_payload.update(
        {
            "response_receipts": tuple(receipts),
            "assessments": assessments,
            "text_disposition_set": disposition_set,
            "candidate_discovery_set": discovery_set,
        }
    )
    forged = _addressed(TextAuditExecutionRecordV2, record_payload)
    return audit_plan, signal_set, group_set, execution, forged, audio_record


def test_full_audit_aggregate_is_content_addressed_and_fresh_process_verifiable(
    complete_full_audit: tuple[object, ...],
) -> None:
    audit_plan, signal_set, group_set, execution, text_record, audio_record = complete_full_audit

    aggregate = _build(complete_full_audit)
    exact_bytes = full_audit_aggregate_bytes(aggregate)

    assert aggregate.id == aggregate.content_hash
    assert full_audit_aggregate_digest(aggregate) == sha256_bytes(exact_bytes)
    assert aggregate.all_cell_ids == execution.all_cell_ids
    assert aggregate.authority == (
        "no_text_mutation_no_correction_decision_no_arbitration_no_release_approval"
    )
    assert aggregate.execution_scope == "all_plan_cells_both_modalities_dispositioned"
    sources = {item.kind: item for item in aggregate.source_artifacts}
    assert sources["text_audit_execution_record"].artifact_sha256 == sha256_bytes(
        canonical_json_bytes(text_record)
    )
    assert sources["text_audit_execution_record"].object_content_hash == text_record.content_hash
    assert sources["audio_audit_execution_record"].artifact_sha256 == sha256_bytes(
        canonical_json_bytes(audio_record)
    )
    assert sources["audio_audit_execution_record"].object_content_hash == audio_record.content_hash
    assert _verify(exact_bytes, complete_full_audit) == aggregate


def test_full_audit_findings_remain_discoveries_without_mutation_or_verdict_authority(
    tmp_path: Path,
) -> None:
    complete = _complete_full_audit(tmp_path, findings=True)
    aggregate = _build(complete)

    assert aggregate.text_discovery_ids
    assert aggregate.audio_discovery_ids
    assert aggregate.authority.endswith("no_release_approval")
    assert aggregate.execution_scope == "all_plan_cells_both_modalities_dispositioned"
    forbidden = {
        "canonical_transcript",
        "canonical_text",
        "correction_proposal",
        "correction_decision",
        "arbitration_verdict",
        "release_accepted",
        "release_approval",
        "mutation",
        "replacement_text",
    }
    assert forbidden.isdisjoint(FullAuditAggregateAttestationV2.model_fields)
    assert _verify(full_audit_aggregate_bytes(aggregate), complete) == aggregate


def test_zero_signals_groups_and_discoveries_still_require_both_modalities_for_every_cell(
    tmp_path: Path,
) -> None:
    complete = _complete_full_audit(
        tmp_path,
        fixture=_clean_zero_signal_fixture(tmp_path),
    )
    audit_plan, signal_set, group_set, execution, text_record, audio_record = complete

    assert signal_set.signals == ()
    assert group_set.groups == ()
    assert text_record.candidate_discovery_set.candidates == ()
    assert audio_record.candidate_discovery_set.candidates == ()
    aggregate = _build(complete)
    expected_cells = tuple(item.id for item in audit_plan.cells)
    assert aggregate.all_cell_ids == expected_cells == execution.all_cell_ids
    assert tuple(item.cell_id for item in text_record.text_disposition_set.dispositions) == (
        expected_cells
    )
    assert tuple(item.cell_id for item in audio_record.audio_disposition_set.dispositions) == (
        expected_cells
    )


@pytest.mark.parametrize("modality", ("text", "audio"))
@pytest.mark.parametrize("mutation", ("missing", "duplicate", "reordered"))
def test_aggregate_rejects_any_nonexact_cell_coverage_even_when_record_is_rehashed(
    complete_full_audit: tuple[object, ...],
    modality: str,
    mutation: str,
) -> None:
    forged = _with_coverage_mutation(
        complete_full_audit,
        modality=modality,
        mutation=mutation,
    )

    with pytest.raises(FullAuditAttestationError):
        _build(forged)


def test_required_cell_cannot_be_reclassified_as_inherited_unavailable(
    complete_full_audit: tuple[object, ...],
) -> None:
    forged = _text_record_with_required_cell_falsely_inherited(complete_full_audit)
    forged_record = forged[4]
    assert (
        TextAuditExecutionRecordV2.model_validate_json(
            canonical_json_bytes(forged_record), strict=True
        )
        == forged_record
    )

    with pytest.raises(FullAuditAttestationError, match="required cell"):
        _build(forged)


def test_nonrequired_cell_cannot_gain_provider_assessment_authority(
    complete_full_audit: tuple[object, ...],
) -> None:
    forged = _text_record_with_nonrequired_provider_overreach(complete_full_audit)
    forged_record = forged[4]
    assert (
        TextAuditExecutionRecordV2.model_validate_json(
            canonical_json_bytes(forged_record), strict=True
        )
        == forged_record
    )

    with pytest.raises(FullAuditAttestationError, match="non-required cell"):
        _build(forged)


@pytest.mark.parametrize(
    ("target", "field"),
    (
        ("audit_plan", "episode_id"),
        ("execution_plan", "generation_id"),
        ("text_record", "execution_plan_id"),
        ("text_record", "policy_hash"),
        ("audio_record", "adapter_identity_hash"),
    ),
)
def test_aggregate_rejects_cross_episode_generation_plan_policy_or_adapter_drift(
    complete_full_audit: tuple[object, ...],
    target: str,
    field: str,
) -> None:
    values = list(complete_full_audit)
    index = {
        "audit_plan": 0,
        "execution_plan": 3,
        "text_record": 4,
        "audio_record": 5,
    }[target]
    replacement = "other-generation" if field in {"episode_id", "generation_id"} else "0" * 64
    values[index] = values[index].model_copy(update={field: replacement})

    with pytest.raises(FullAuditAttestationError):
        _build(tuple(values))


def test_coordinated_signal_group_and_plan_outer_hash_rewrite_is_not_trusted(
    complete_full_audit: tuple[object, ...],
) -> None:
    audit_plan, signal_set, group_set, execution, text_record, audio_record = complete_full_audit
    signal_payload = signal_set.model_dump(mode="python", exclude={"content_hash"})
    signal_payload["signals"] = ()
    signal_payload["content_hash"] = hash_object(signal_payload)
    forged_signals = type(signal_set)(**signal_payload)

    group_payload = group_set.model_dump(mode="python", exclude={"content_hash"})
    group_payload.update(
        {
            "signal_set_hash": sha256_bytes(canonical_json_bytes(forged_signals)),
            "signal_set_content_hash": forged_signals.content_hash,
            "signal_ids": (),
            "groups": (),
        }
    )
    group_payload["content_hash"] = hash_object(group_payload)
    forged_groups = type(group_set)(**group_payload)

    execution_payload = execution.model_dump(mode="python", exclude={"id", "content_hash"})
    execution_payload.update(
        {
            "candidate_signal_set_hash": sha256_bytes(canonical_json_bytes(forged_signals)),
            "candidate_signal_set_content_hash": forged_signals.content_hash,
            "candidate_group_set_hash": sha256_bytes(canonical_json_bytes(forged_groups)),
            "candidate_group_set_content_hash": forged_groups.content_hash,
        }
    )
    execution_digest = hash_object(execution_payload)
    forged_execution = execution.model_copy(
        update={
            "candidate_signal_set_hash": execution_payload["candidate_signal_set_hash"],
            "candidate_signal_set_content_hash": execution_payload[
                "candidate_signal_set_content_hash"
            ],
            "candidate_group_set_hash": execution_payload["candidate_group_set_hash"],
            "candidate_group_set_content_hash": execution_payload[
                "candidate_group_set_content_hash"
            ],
            "id": execution_digest,
            "content_hash": execution_digest,
        }
    )

    with pytest.raises(FullAuditAttestationError):
        _build(
            (
                audit_plan,
                forged_signals,
                forged_groups,
                forged_execution,
                text_record,
                audio_record,
            )
        )


def _self_rehashed_aggregate(
    aggregate: FullAuditAggregateAttestationV2,
    **updates: object,
) -> FullAuditAggregateAttestationV2:
    payload = aggregate.model_dump(mode="python", exclude={"id", "content_hash"})
    payload.update(updates)
    digest = hash_object(payload)
    return FullAuditAggregateAttestationV2(**payload, id=digest, content_hash=digest)


def test_record_hash_and_source_artifact_digest_tamper_fail_closed(
    complete_full_audit: tuple[object, ...],
) -> None:
    values = list(complete_full_audit)
    values[4] = values[4].model_copy(update={"content_hash": "0" * 64})
    with pytest.raises(FullAuditAttestationError):
        _build(tuple(values))

    aggregate = _build(complete_full_audit)
    sources = list(aggregate.source_artifacts)
    sources[-1] = sources[-1].model_copy(update={"artifact_sha256": "0" * 64})
    forged = _self_rehashed_aggregate(aggregate, source_artifacts=tuple(sources))
    with pytest.raises(FullAuditAttestationError, match="differs from frozen parents"):
        _verify(full_audit_aggregate_bytes(forged), complete_full_audit)


@pytest.mark.parametrize("raw_index", range(5))
def test_exact_request_response_or_clip_byte_tamper_fails_before_aggregation(
    tmp_path: Path,
    raw_index: int,
) -> None:
    complete = list(_complete_full_audit(tmp_path, include_bytes=True))
    raw_sets = list(complete[6:])
    selected = list(raw_sets[raw_index])
    selected[0] = selected[0] + b"\x20"
    raw_sets[raw_index] = tuple(selected)
    complete[6:] = raw_sets

    with pytest.raises(FullAuditAttestationError, match="raw artifact bytes differ"):
        _build(tuple(complete))


def test_text_and_audio_discovery_ids_cannot_impersonate_each_other(tmp_path: Path) -> None:
    complete = _complete_full_audit(tmp_path, findings=True)
    aggregate = _build(complete)
    forged = _self_rehashed_aggregate(
        aggregate,
        text_discovery_ids=aggregate.audio_discovery_ids,
        audio_discovery_ids=aggregate.text_discovery_ids,
    )

    with pytest.raises(FullAuditAttestationError, match="differs from frozen parents"):
        _verify(full_audit_aggregate_bytes(forged), complete)


@pytest.mark.parametrize("mutation", ("schema_v1", "unknown_field", "noncanonical_bytes"))
def test_fresh_verifier_rejects_schema_drift_unknown_fields_and_raw_byte_tamper(
    complete_full_audit: tuple[object, ...],
    mutation: str,
) -> None:
    aggregate = _build(complete_full_audit)
    payload = json.loads(full_audit_aggregate_bytes(aggregate))
    if mutation == "schema_v1":
        payload["schema_version"] = 1
        exact = canonical_json_bytes(payload)
    elif mutation == "unknown_field":
        payload["release_accepted"] = True
        exact = canonical_json_bytes(payload)
    else:
        exact = full_audit_aggregate_bytes(aggregate) + b"\n"

    with pytest.raises(FullAuditAttestationError):
        _verify(exact, complete_full_audit)


def test_canonical_bytes_roundtrip_is_stable_and_non_self_attested(
    complete_full_audit: tuple[object, ...],
) -> None:
    aggregate = _build(complete_full_audit)
    exact = full_audit_aggregate_bytes(aggregate)
    parsed = FullAuditAggregateAttestationV2.model_validate_json(exact, strict=True)

    assert parsed == aggregate
    assert canonical_json_bytes(parsed) == exact
    assert full_audit_aggregate_digest(parsed) == sha256_bytes(exact)
    assert _verify(exact, complete_full_audit) == aggregate
