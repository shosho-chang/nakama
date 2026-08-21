"""Split text/audio execution freezes only selected audio deltas."""

from __future__ import annotations

import pytest

import agents.brook.podcast_subtitles.correction_execution as correction_execution_module
from agents.brook.podcast_subtitles.audio_audit_selection import (
    build_audio_audit_selection_plan,
    build_audio_audit_selection_receipt,
    build_selective_audio_round_receipt_v3,
    default_audio_audit_selection_policy,
)
from agents.brook.podcast_subtitles.correction_execution import (
    build_modality_audit_execution_plan_v3,
    materialize_audio_correction_packet_sources,
    materialize_text_correction_packet_sources,
    materialize_text_correction_packet_sources_batch,
)
from agents.brook.podcast_subtitles.full_audit_attestation import (
    FullAuditAttestationError,
    build_selective_audit_aggregate_v3,
    selective_audit_aggregate_bytes_v3,
    verify_selective_audit_aggregate_v3,
)
from agents.brook.podcast_subtitles.hashing import canonical_json_bytes, hash_object
from agents.brook.podcast_subtitles.selective_audio_orchestration import (
    SelectiveAudioAuditCompletedV3,
    SelectiveAudioOrchestrationError,
    execute_selective_audio_audit_v3,
)
from tests.agents.brook.podcast_subtitles.test_audio_audit_execution import (
    _executor as audio_executor,
)
from tests.agents.brook.podcast_subtitles.test_audio_audit_execution import (
    _response_bytes as audio_response_bytes,
)
from tests.agents.brook.podcast_subtitles.test_correction_execution import (
    build_fixture,
    replace_execution_limits,
)
from tests.agents.brook.podcast_subtitles.test_text_audit_execution import (
    _executor as text_executor,
)
from tests.agents.brook.podcast_subtitles.test_text_audit_execution import (
    _response_bytes as text_response_bytes,
)


def _counted_tuple(values):  # type: ignore[no-untyped-def]
    class CountedTuple(tuple):
        iterations = 0

        def __iter__(self):  # type: ignore[no-untyped-def]
            type(self).iterations += 1
            return super().__iter__()

    return CountedTuple(values), CountedTuple


def _completed_selective_audit(tmp_path):
    (
        audit,
        signals,
        groups,
        _,
        audio_path,
        transcript,
        seam,
        recognitions,
        execution_policy,
    ) = build_fixture(tmp_path)
    text_plan = build_modality_audit_execution_plan_v3(
        audit,
        signals,
        groups,
        transcript,
        recognitions,
        execution_policy,
        modality="text",
        seam_evidence=seam,
    )
    text_sources: dict[str, bytes] = {}
    for packet in text_plan.packets:
        text_sources.update(
            materialize_text_correction_packet_sources(
                text_plan,
                packet.id,
                audit,
                signals,
                groups,
                transcript,
                recognitions,
                seam_evidence=seam,
            )
        )
    text_record = text_executor(runner=text_response_bytes).execute(
        text_plan, audit, text_sources
    ).record
    selection = build_audio_audit_selection_plan(
        audit,
        transcript,
        default_audio_audit_selection_policy(),
        tier="sample_10",
        text_record=text_record,
    )
    audio_plan = build_modality_audit_execution_plan_v3(
        audit,
        signals,
        groups,
        transcript,
        recognitions,
        execution_policy,
        modality="audio",
        selection_plan=selection,
        seam_evidence=seam,
        normalized_audio_path=audio_path,
    )
    audio_sources: dict[str, bytes] = {}
    for packet in audio_plan.packets:
        audio_sources.update(
            materialize_audio_correction_packet_sources(
                audio_plan,
                packet.id,
                audit,
                signals,
                groups,
                transcript,
                recognitions,
                normalized_audio_path=audio_path,
                seam_evidence=seam,
            )
        )
    audio_record = audio_executor(tmp_path, runner=audio_response_bytes).execute(
        audio_plan, audit, audio_sources
    ).record
    round_receipt = build_selective_audio_round_receipt_v3(
        selection, (audio_record,)
    )
    return (
        audit,
        signals,
        groups,
        text_plan,
        text_record,
        (selection,),
        (audio_plan,),
        (audio_record,),
        (round_receipt,),
    )


def test_text_plan_token_scans_do_not_scale_with_boundary_count(tmp_path) -> None:
    (
        audit,
        signals,
        groups,
        _,
        _,
        transcript,
        seam,
        recognitions,
        execution_policy,
    ) = build_fixture(tmp_path, mandatory_boundaries=False)
    counted_tokens, counter = _counted_tuple(transcript.tokens)
    counted_transcript = transcript.model_copy(update={"tokens": counted_tokens})

    build_modality_audit_execution_plan_v3(
        audit,
        signals,
        groups,
        counted_transcript,
        recognitions,
        execution_policy,
        modality="text",
        seam_evidence=seam,
    )

    # Fixed setup/final binding scans are permitted; the three internal
    # boundaries must not each rebuild the complete token lookup twice.
    assert counter.iterations <= 5


def _build_text_plan_with_scan_counts(tmp_path, *, max_spans_per_packet: int | None):
    (
        audit,
        signals,
        groups,
        _,
        _,
        transcript,
        seam,
        recognitions,
        execution_policy,
    ) = build_fixture(tmp_path, mandatory_boundaries=False)
    if max_spans_per_packet is not None:
        execution_policy = replace_execution_limits(
            execution_policy,
            text_updates={"max_spans_per_packet": max_spans_per_packet},
        )

    counters = {}
    audit_updates = {}
    for field_name in ("cells", "span_targets", "boundary_targets"):
        counted, counter = _counted_tuple(getattr(audit, field_name))
        audit_updates[field_name] = counted
        counters[field_name] = counter
    counted_audit = audit.model_copy(update=audit_updates)

    counted, counter = _counted_tuple(signals.signals)
    counted_signals = signals.model_copy(update={"signals": counted})
    counters["signals"] = counter
    counted, counter = _counted_tuple(groups.groups)
    counted_groups = groups.model_copy(update={"groups": counted})
    counters["groups"] = counter

    transcript_updates = {}
    for field_name in ("tokens", "spans"):
        counted, counter = _counted_tuple(getattr(transcript, field_name))
        transcript_updates[field_name] = counted
        counters[f"transcript_{field_name}"] = counter
    counted_transcript = transcript.model_copy(update=transcript_updates)

    counted, counter = _counted_tuple(recognitions[0].tokens)
    counted_recognitions = (
        recognitions[0].model_copy(update={"tokens": counted}),
    )
    counters["recognition_tokens"] = counter

    result = build_modality_audit_execution_plan_v3(
        counted_audit,
        counted_signals,
        counted_groups,
        counted_transcript,
        counted_recognitions,
        execution_policy,
        modality="text",
        seam_evidence=seam,
    )
    return result, {name: counter.iterations for name, counter in counters.items()}


def test_text_plan_global_input_scans_do_not_scale_with_packet_count(tmp_path) -> None:
    single, single_scans = _build_text_plan_with_scan_counts(
        tmp_path,
        max_spans_per_packet=None,
    )
    split, split_scans = _build_text_plan_with_scan_counts(
        tmp_path,
        max_spans_per_packet=1,
    )

    assert len(single.packets) == 1
    assert len(split.packets) == 4
    assert split_scans == single_scans
    replayed, replayed_scans = _build_text_plan_with_scan_counts(
        tmp_path,
        max_spans_per_packet=1,
    )
    assert replayed_scans == split_scans
    assert replayed.id == split.id
    assert canonical_json_bytes(replayed) == canonical_json_bytes(split)


def test_text_packet_batch_prepares_episode_scale_parents_once_and_is_byte_exact(
    tmp_path,
    monkeypatch,
) -> None:
    (
        audit,
        signals,
        groups,
        _,
        _,
        transcript,
        seam,
        recognitions,
        execution_policy,
    ) = build_fixture(tmp_path, mandatory_boundaries=False)
    execution_policy = replace_execution_limits(
        execution_policy,
        text_updates={"max_spans_per_packet": 1},
    )
    plan = build_modality_audit_execution_plan_v3(
        audit,
        signals,
        groups,
        transcript,
        recognitions,
        execution_policy,
        modality="text",
        seam_evidence=seam,
    )
    assert len(plan.packets) > 1

    expected: dict[str, bytes] = {}
    for packet in plan.packets:
        expected.update(
            materialize_text_correction_packet_sources(
                plan,
                packet.id,
                audit,
                signals,
                groups,
                transcript,
                recognitions,
                seam_evidence=seam,
            )
        )

    counts: dict[str, int] = {}
    for name in (
        "_validate_execution_plan_artifact",
        "_exact_inputs",
        "_artifact_identities",
        "_binding_indices",
    ):
        original = getattr(correction_execution_module, name)

        def counted(*args, _name=name, _original=original, **kwargs):
            counts[_name] = counts.get(_name, 0) + 1
            return _original(*args, **kwargs)

        monkeypatch.setattr(correction_execution_module, name, counted)

    actual = materialize_text_correction_packet_sources_batch(
        plan,
        audit,
        signals,
        groups,
        transcript,
        recognitions,
        seam_evidence=seam,
    )

    assert counts == {
        "_validate_execution_plan_artifact": 1,
        "_exact_inputs": 1,
        "_artifact_identities": 1,
        "_binding_indices": 1,
    }
    assert tuple(actual) == tuple(expected)
    assert actual == expected


def test_terminal_selective_aggregate_preserves_universal_text_and_scoped_audio(
    tmp_path,
) -> None:
    parents = _completed_selective_audit(tmp_path)
    aggregate = build_selective_audit_aggregate_v3(
        audit_plan=parents[0],
        candidate_signal_set=parents[1],
        candidate_group_set=parents[2],
        text_execution_plan=parents[3],
        text_record=parents[4],
        selection_plans=parents[5],
        audio_execution_plans=parents[6],
        audio_records=parents[7],
        round_receipts=parents[8],
    )

    assert aggregate.terminal_decision == "complete"
    assert set(aggregate.text_disposition_cell_ids) == set(aggregate.all_cell_ids)
    assert set(aggregate.audio_assessed_cell_ids) == set(
        aggregate.final_selected_required_cell_ids
    )
    assert set(aggregate.audio_assessed_cell_ids).isdisjoint(
        aggregate.unselected_cell_ids
    )
    assert verify_selective_audit_aggregate_v3(
        selective_audit_aggregate_bytes_v3(aggregate),
        audit_plan=parents[0],
        candidate_signal_set=parents[1],
        candidate_group_set=parents[2],
        text_execution_plan=parents[3],
        text_record=parents[4],
        selection_plans=parents[5],
        audio_execution_plans=parents[6],
        audio_records=parents[7],
        round_receipts=parents[8],
    ) == aggregate


def test_selective_aggregate_rejects_missing_selected_record(tmp_path) -> None:
    parents = _completed_selective_audit(tmp_path)
    with pytest.raises(FullAuditAttestationError, match="one audio record"):
        build_selective_audit_aggregate_v3(
            audit_plan=parents[0],
            candidate_signal_set=parents[1],
            candidate_group_set=parents[2],
            text_execution_plan=parents[3],
            text_record=parents[4],
            selection_plans=parents[5],
            audio_execution_plans=parents[6],
            audio_records=(),
            round_receipts=parents[8],
        )


def test_selective_aggregate_rejects_overlapping_audio_delta(tmp_path) -> None:
    parents = _completed_selective_audit(tmp_path)
    with pytest.raises(FullAuditAttestationError, match="round counts|tier sequence"):
        build_selective_audit_aggregate_v3(
            audit_plan=parents[0],
            candidate_signal_set=parents[1],
            candidate_group_set=parents[2],
            text_execution_plan=parents[3],
            text_record=parents[4],
            selection_plans=parents[5] * 2,
            audio_execution_plans=parents[6] * 2,
            audio_records=parents[7] * 2,
            round_receipts=parents[8] * 2,
        )


def test_selective_aggregate_rejects_nonterminal_escalation_decision(tmp_path) -> None:
    parents = _completed_selective_audit(tmp_path)
    round_receipt = parents[8][0]
    selection = parents[5][0]
    escalation = build_audio_audit_selection_receipt(
        selection,
        material_error_span_ids=selection.sample_span_ids,
    )
    payload = round_receipt.model_dump(mode="python", exclude={"id", "content_hash"})
    payload["decision_receipt"] = escalation
    payload["decision_receipt_artifact_hash"] = hash_object(escalation)
    digest = hash_object(payload)
    forged = type(round_receipt)(**payload, id=digest, content_hash=digest)

    with pytest.raises(FullAuditAttestationError, match="terminal decision"):
        build_selective_audit_aggregate_v3(
            audit_plan=parents[0],
            candidate_signal_set=parents[1],
            candidate_group_set=parents[2],
            text_execution_plan=parents[3],
            text_record=parents[4],
            selection_plans=parents[5],
            audio_execution_plans=parents[6],
            audio_records=parents[7],
            round_receipts=(forged,),
        )


def test_isolated_selective_orchestrator_runs_text_ready_to_terminal_aggregate(
    tmp_path,
) -> None:
    (
        audit,
        signals,
        groups,
        _,
        audio_path,
        transcript,
        seam,
        recognitions,
        execution_policy,
    ) = build_fixture(tmp_path)
    text_plan = build_modality_audit_execution_plan_v3(
        audit,
        signals,
        groups,
        transcript,
        recognitions,
        execution_policy,
        modality="text",
        seam_evidence=seam,
    )
    text_sources: dict[str, bytes] = {}
    for packet in text_plan.packets:
        text_sources.update(
            materialize_text_correction_packet_sources(
                text_plan,
                packet.id,
                audit,
                signals,
                groups,
                transcript,
                recognitions,
                seam_evidence=seam,
            )
        )
    text_run = text_executor(runner=text_response_bytes).execute(
        text_plan, audit, text_sources
    )

    outcome = execute_selective_audio_audit_v3(
        audit_plan=audit,
        candidate_signal_set=signals,
        candidate_group_set=groups,
        transcript=transcript,
        recognition_evidence=recognitions,
        execution_policy=execution_policy,
        text_execution_plan=text_plan,
        text_run=text_run,
        audio_executor=audio_executor(tmp_path, runner=audio_response_bytes),
        normalized_audio_path=audio_path,
        seam_evidence=seam,
    )

    assert isinstance(outcome, SelectiveAudioAuditCompletedV3)
    assert tuple(item.tier for item in outcome.selection_plans) == ("sample_10",)
    assert outcome.aggregate.terminal_decision == "complete"
    assert outcome.aggregate.audio_record_ids == tuple(
        item.record.id for item in outcome.runs
    )

    different_text_plan = text_plan.model_copy(update={"id": "0" * 64})
    with pytest.raises(SelectiveAudioOrchestrationError, match="text run differs"):
        execute_selective_audio_audit_v3(
            audit_plan=audit,
            candidate_signal_set=signals,
            candidate_group_set=groups,
            transcript=transcript,
            recognition_evidence=recognitions,
            execution_policy=execution_policy,
            text_execution_plan=different_text_plan,
            text_run=text_run,
            audio_executor=audio_executor(tmp_path, runner=audio_response_bytes),
            normalized_audio_path=audio_path,
            seam_evidence=seam,
        )


def test_text_plan_is_universal_without_opening_normalized_audio(tmp_path) -> None:
    (
        audit,
        signals,
        groups,
        _,
        _,
        transcript,
        seam,
        recognitions,
        policy,
    ) = build_fixture(tmp_path)

    text = build_modality_audit_execution_plan_v3(
        audit,
        signals,
        groups,
        transcript,
        recognitions,
        policy,
        modality="text",
        seam_evidence=seam,
    )

    assert text.modality == "text"
    assert set(text.owned_cell_ids) == set(text.all_cell_ids)
    assert set(text.owned_span_ids) == {item.span_id for item in audit.span_targets}
    assert all(packet.modality == "text" for packet in text.packets)
    sources: dict[str, bytes] = {}
    for packet in text.packets:
        sources.update(
            materialize_text_correction_packet_sources(
                text,
                packet.id,
                audit,
                signals,
                groups,
                transcript,
                recognitions,
                seam_evidence=seam,
            )
        )
    run = text_executor(runner=text_response_bytes).execute(text, audit, sources)
    assert run.record.text_disposition_set.all_cell_ids == tuple(
        item.id for item in audit.cells
    )


def test_audio_plan_owns_only_exact_selected_spans_and_context_is_not_owned(tmp_path) -> None:
    (
        audit,
        signals,
        groups,
        _,
        audio_path,
        transcript,
        seam,
        recognitions,
        policy,
    ) = build_fixture(tmp_path)
    selection = build_audio_audit_selection_plan(
        audit,
        transcript,
        default_audio_audit_selection_policy(),
        tier="sample_10",
    )

    audio = build_modality_audit_execution_plan_v3(
        audit,
        signals,
        groups,
        transcript,
        recognitions,
        policy,
        modality="audio",
        selection_plan=selection,
        seam_evidence=seam,
        normalized_audio_path=audio_path,
    )

    assert audio.owned_span_ids == selection.selected_span_ids
    assert set(audio.owned_cell_ids) == set(selection.selected_owned_cell_ids)
    assert set(audio.required_cell_ids) == set(selection.selected_required_cell_ids)
    assert {
        span_id for packet in audio.packets for span_id in packet.context_span_ids
    }.isdisjoint(audio.owned_span_ids)
    sources: dict[str, bytes] = {}
    for packet in audio.packets:
        sources.update(
            materialize_audio_correction_packet_sources(
                audio,
                packet.id,
                audit,
                signals,
                groups,
                transcript,
                recognitions,
                normalized_audio_path=audio_path,
                seam_evidence=seam,
            )
        )
    run = audio_executor(tmp_path, runner=audio_response_bytes).execute(
        audio,
        audit,
        sources,
    )
    assert set(run.record.assessed_cell_ids) == set(audio.required_cell_ids)
    assert set(run.record.assessed_cell_ids).isdisjoint(
        set(audio.all_cell_ids) - set(audio.owned_cell_ids)
    )
    round_receipt = build_selective_audio_round_receipt_v3(
        selection,
        (run.record,),
    )
    assert round_receipt.decision_receipt.decision == "complete"
    assert round_receipt.decision_receipt.audited_span_ids == selection.selected_span_ids


def test_escalated_audio_plans_emit_only_30_and_full_deltas(tmp_path) -> None:
    (
        audit,
        signals,
        groups,
        _,
        audio_path,
        transcript,
        seam,
        recognitions,
        execution_policy,
    ) = build_fixture(tmp_path)
    selection_policy = default_audio_audit_selection_policy(clock_strata=1)
    ten = build_audio_audit_selection_plan(
        audit, transcript, selection_policy, tier="sample_10"
    )
    ten_execution = build_modality_audit_execution_plan_v3(
        audit,
        signals,
        groups,
        transcript,
        recognitions,
        execution_policy,
        modality="audio",
        selection_plan=ten,
        seam_evidence=seam,
        normalized_audio_path=audio_path,
    )
    ten_receipt = build_audio_audit_selection_receipt(
        ten,
        material_error_span_ids=ten.sample_span_ids,
    )
    thirty = build_audio_audit_selection_plan(
        audit,
        transcript,
        selection_policy,
        tier="sample_30",
        prior_plan=ten,
        prior_receipt=ten_receipt,
    )
    thirty_execution = build_modality_audit_execution_plan_v3(
        audit,
        signals,
        groups,
        transcript,
        recognitions,
        execution_policy,
        modality="audio",
        selection_plan=thirty,
        prior_audio_plans=(ten_execution,),
        seam_evidence=seam,
        normalized_audio_path=audio_path,
    )

    assert set(thirty_execution.owned_span_ids) == set(thirty.selected_span_ids) - set(
        ten.selected_span_ids
    )
    thirty_receipt = build_audio_audit_selection_receipt(
        thirty,
        material_error_span_ids=thirty.sample_span_ids,
    )
    full = build_audio_audit_selection_plan(
        audit,
        transcript,
        selection_policy,
        tier="full",
        prior_plan=thirty,
        prior_receipt=thirty_receipt,
    )
    full_execution = build_modality_audit_execution_plan_v3(
        audit,
        signals,
        groups,
        transcript,
        recognitions,
        execution_policy,
        modality="audio",
        selection_plan=full,
        prior_audio_plans=(ten_execution, thirty_execution),
        seam_evidence=seam,
        normalized_audio_path=audio_path,
    )

    assert set(full_execution.owned_span_ids) == set(full.selected_span_ids) - set(
        thirty.selected_span_ids
    )
    assert set(ten_execution.owned_span_ids).isdisjoint(thirty_execution.owned_span_ids)
    assert set(ten_execution.owned_span_ids).isdisjoint(full_execution.owned_span_ids)
    assert set(thirty_execution.owned_span_ids).isdisjoint(full_execution.owned_span_ids)
