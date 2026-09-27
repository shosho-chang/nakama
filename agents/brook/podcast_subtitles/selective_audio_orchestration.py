"""Isolated selective-audio orchestration seam for Podcast Subtitle V2.

This module deliberately does not mutate Generation checkpoints.  It turns one
completed universal text audit into deterministic 10/30/full audio deltas and
returns either exact pending workspace paths or a terminal aggregate.  The
caller can invoke it again after responses appear; content-addressed requests
and the executor workspace make the replay deterministic.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from shared.schemas.podcast_subtitles_v2 import (
    AuditPlan,
    CandidateGroupSet,
    CandidateSignalSet,
    CanonicalTranscript,
    RecognitionEvidence,
    RecognitionSeamEvidence,
    ReferenceRetrievalReceipt,
)
from shared.schemas.podcast_subtitles_v2_audio_selection import (
    AudioAuditSelectionPlanV1,
    AudioAuditSelectionPolicyV1,
    AudioAuditSelectionTierV1,
)
from shared.schemas.podcast_subtitles_v2_correction import CorrectionAuditExecutionPolicyV2
from shared.schemas.podcast_subtitles_v2_selective_audio import (
    ModalityAuditExecutionPlanV3,
    SelectiveAudioAuditExecutionRecordV3,
    SelectiveAudioAuditRoundReceiptV3,
)

from .audio_audit_execution import (
    AudioAuditRunResult,
    AudioAuditWorkPending,
    AudioFullAuditExecutor,
)
from .audio_audit_selection import (
    build_audio_audit_selection_plan,
    build_selective_audio_round_receipt_v3,
    default_audio_audit_selection_policy,
)
from .correction_execution import (
    build_modality_audit_execution_plan_v3,
    materialize_audio_correction_packet_sources,
)
from .full_audit_attestation import (
    SelectiveAuditAggregateAttestationV3,
    build_selective_audit_aggregate_v3,
)
from .text_audit_execution import TextAuditRunResult


class SelectiveAudioOrchestrationError(ValueError):
    """Selective rounds cannot be derived from the supplied frozen parents."""


@dataclass(frozen=True, slots=True)
class SelectiveAudioAuditPendingV3:
    tier: AudioAuditSelectionTierV1
    selection_plan: AudioAuditSelectionPlanV1
    execution_plan: ModalityAuditExecutionPlanV3
    completed_selection_plans: tuple[AudioAuditSelectionPlanV1, ...]
    completed_execution_plans: tuple[ModalityAuditExecutionPlanV3, ...]
    completed_runs: tuple[AudioAuditRunResult, ...]
    completed_round_receipts: tuple[SelectiveAudioAuditRoundReceiptV3, ...]
    request_paths: tuple[Path, ...]
    clip_paths: tuple[Path, ...]
    response_paths: tuple[Path, ...]


@dataclass(frozen=True, slots=True)
class SelectiveAudioAuditCompletedV3:
    selection_plans: tuple[AudioAuditSelectionPlanV1, ...]
    execution_plans: tuple[ModalityAuditExecutionPlanV3, ...]
    runs: tuple[AudioAuditRunResult, ...]
    round_receipts: tuple[SelectiveAudioAuditRoundReceiptV3, ...]
    aggregate: SelectiveAuditAggregateAttestationV3

    def __post_init__(self) -> None:
        count = len(self.selection_plans)
        if count == 0 or any(
            len(values) != count
            for values in (self.execution_plans, self.runs, self.round_receipts)
        ):
            raise ValueError("completed selective audio audit round coverage differs")


SelectiveAudioAuditOutcomeV3 = (
    SelectiveAudioAuditPendingV3 | SelectiveAudioAuditCompletedV3
)


def execute_selective_audio_audit_v3(
    *,
    audit_plan: AuditPlan,
    candidate_signal_set: CandidateSignalSet,
    candidate_group_set: CandidateGroupSet,
    transcript: CanonicalTranscript,
    recognition_evidence: tuple[RecognitionEvidence, ...],
    execution_policy: CorrectionAuditExecutionPolicyV2,
    text_execution_plan: ModalityAuditExecutionPlanV3,
    text_run: TextAuditRunResult,
    audio_executor: AudioFullAuditExecutor,
    normalized_audio_path: Path,
    selection_policy: AudioAuditSelectionPolicyV1 | None = None,
    reference_retrievals: tuple[ReferenceRetrievalReceipt, ...] = (),
    seam_evidence: RecognitionSeamEvidence | None = None,
) -> SelectiveAudioAuditOutcomeV3:
    """Run deterministic selective rounds without checkpoint mutation or release authority."""

    if text_execution_plan.modality != "text":
        raise SelectiveAudioOrchestrationError("selective audio requires a text V3 plan")
    if (
        text_run.record.execution_plan_id != text_execution_plan.id
        or text_run.record.execution_plan_content_hash != text_execution_plan.content_hash
    ):
        raise SelectiveAudioOrchestrationError("text run differs from the frozen text plan")
    policy = selection_policy or default_audio_audit_selection_policy()
    selections: list[AudioAuditSelectionPlanV1] = []
    executions: list[ModalityAuditExecutionPlanV3] = []
    runs: list[AudioAuditRunResult] = []
    receipts: list[SelectiveAudioAuditRoundReceiptV3] = []
    tier: AudioAuditSelectionTierV1 = "sample_10"
    prior_selection = None
    prior_decision = None

    while True:
        selection = build_audio_audit_selection_plan(
            audit_plan,
            transcript,
            policy,
            tier=tier,
            text_record=text_run.record,
            prior_plan=prior_selection,
            prior_receipt=prior_decision,
        )
        execution = build_modality_audit_execution_plan_v3(
            audit_plan,
            candidate_signal_set,
            candidate_group_set,
            transcript,
            recognition_evidence,
            execution_policy,
            modality="audio",
            selection_plan=selection,
            prior_audio_plans=tuple(executions),
            reference_retrievals=reference_retrievals,
            seam_evidence=seam_evidence,
            normalized_audio_path=normalized_audio_path,
        )
        sources: dict[str, bytes] = {}
        for packet in execution.packets:
            materialized = materialize_audio_correction_packet_sources(
                execution,
                packet.id,
                audit_plan,
                candidate_signal_set,
                candidate_group_set,
                transcript,
                recognition_evidence,
                normalized_audio_path=normalized_audio_path,
                reference_retrievals=reference_retrievals,
                seam_evidence=seam_evidence,
            )
            for uri, payload in materialized.items():
                existing = sources.get(uri)
                if existing is not None and existing != payload:
                    raise SelectiveAudioOrchestrationError(
                        "audio source URI maps to conflicting exact bytes"
                    )
                sources[uri] = payload
        try:
            run = audio_executor.execute(execution, audit_plan, sources)
        except AudioAuditWorkPending as pending:
            return SelectiveAudioAuditPendingV3(
                tier=tier,
                selection_plan=selection,
                execution_plan=execution,
                completed_selection_plans=tuple(selections),
                completed_execution_plans=tuple(executions),
                completed_runs=tuple(runs),
                completed_round_receipts=tuple(receipts),
                request_paths=pending.request_paths,
                clip_paths=pending.clip_paths,
                response_paths=pending.response_paths,
            )
        if not isinstance(run.record, SelectiveAudioAuditExecutionRecordV3):
            raise SelectiveAudioOrchestrationError(
                "audio executor returned a legacy universal record for a selective plan"
            )
        selections.append(selection)
        executions.append(execution)
        runs.append(run)
        round_receipt = build_selective_audio_round_receipt_v3(
            selection,
            tuple(item.record for item in runs),
            previous_round=receipts[-1] if receipts else None,
        )
        receipts.append(round_receipt)
        decision = round_receipt.decision_receipt
        if decision.decision == "complete":
            aggregate = build_selective_audit_aggregate_v3(
                audit_plan=audit_plan,
                candidate_signal_set=candidate_signal_set,
                candidate_group_set=candidate_group_set,
                text_execution_plan=text_execution_plan,
                text_record=text_run.record,
                selection_plans=tuple(selections),
                audio_execution_plans=tuple(executions),
                audio_records=tuple(item.record for item in runs),
                round_receipts=tuple(receipts),
            )
            return SelectiveAudioAuditCompletedV3(
                selection_plans=tuple(selections),
                execution_plans=tuple(executions),
                runs=tuple(runs),
                round_receipts=tuple(receipts),
                aggregate=aggregate,
            )
        prior_selection = selection
        prior_decision = decision
        tier = "sample_30" if decision.decision == "escalate_30" else "full"


__all__ = [
    "SelectiveAudioAuditCompletedV3",
    "SelectiveAudioAuditOutcomeV3",
    "SelectiveAudioAuditPendingV3",
    "SelectiveAudioOrchestrationError",
    "execute_selective_audio_audit_v3",
]
