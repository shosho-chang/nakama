"""V3 split-modality execution contracts for selective audio audit."""

from __future__ import annotations

import hashlib
import json
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, StringConstraints, model_validator

from shared.schemas.podcast_subtitles_v2_audio_audit import (
    AudioAuditCellAssessmentV2,
    AudioAuditInvocationJournalV2,
    AudioAuditProviderResponseReceiptV2,
    AudioDiscoveredCandidateV2,
)
from shared.schemas.podcast_subtitles_v2_audio_selection import (
    AudioAuditSelectionReceiptV1,
    AudioAuditSelectionTierV1,
)
from shared.schemas.podcast_subtitles_v2_correction import (
    CorrectionAuditExecutionPolicyV2,
    CorrectionAuditPacketV2,
    CorrectionSourceBindingV2,
)

Sha256 = Annotated[str, StringConstraints(pattern=r"^[0-9a-f]{64}$")]


def _hash_json(value: object) -> str:
    if isinstance(value, BaseModel):
        value = value.model_dump(mode="json", exclude_none=False)
    return hashlib.sha256(
        json.dumps(
            value,
            ensure_ascii=False,
            allow_nan=False,
            separators=(",", ":"),
            sort_keys=True,
        ).encode("utf-8")
    ).hexdigest()


class _Contract(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class ModalityAuditExecutionPlanV3(_Contract):
    """One exact modality topology; text and audio no longer freeze together."""

    schema_version: Literal[3] = 3
    id: Sha256
    episode_id: str
    generation_id: str
    modality: Literal["text", "audio"]
    policy: CorrectionAuditExecutionPolicyV2
    policy_hash: Sha256
    audit_plan_hash: Sha256
    audit_plan_content_hash: Sha256
    audit_input_hash: Sha256
    candidate_signal_set_hash: Sha256
    candidate_signal_set_content_hash: Sha256
    candidate_group_set_hash: Sha256
    candidate_group_set_content_hash: Sha256
    canonical_transcript_hash: Sha256
    canonical_content_hash: Sha256
    recognition_evidence_set_hash: Sha256
    reference_retrieval_receipt_set_hash: Sha256
    normalized_audio_hash: Sha256
    all_cell_ids: tuple[Sha256, ...]
    owned_cell_ids: tuple[Sha256, ...]
    required_cell_ids: tuple[Sha256, ...]
    owned_span_ids: tuple[str, ...]
    selection_plan_id: Sha256 | None = None
    selection_plan_content_hash: Sha256 | None = None
    selection_plan_artifact_hash: Sha256 | None = None
    tier: AudioAuditSelectionTierV1 | None = None
    prior_execution_plan_id: Sha256 | None = None
    source_bindings: tuple[CorrectionSourceBindingV2, ...]
    packets: tuple[CorrectionAuditPacketV2, ...]
    planner_id: Literal["nakama-correction-audit-execution-planner"]
    planner_version: Literal[2]
    planner_code_hash: Sha256
    execution_status: Literal["planned_not_executed"] = "planned_not_executed"
    completeness_statement: Literal[
        "text_universal_or_audio_exact_selected_delta_not_semantic_recall_proof"
    ] = "text_universal_or_audio_exact_selected_delta_not_semantic_recall_proof"
    content_hash: Sha256

    @model_validator(mode="after")
    def _is_exact_partition(self) -> ModalityAuditExecutionPlanV3:
        if self.policy_hash != self.policy.content_hash:
            raise ValueError("modality execution policy hash mismatch")
        if not self.all_cell_ids or len(set(self.all_cell_ids)) != len(self.all_cell_ids):
            raise ValueError("modality execution requires unique AuditPlan cells")
        if not self.owned_cell_ids or len(set(self.owned_cell_ids)) != len(self.owned_cell_ids):
            raise ValueError("modality execution requires unique owned cells")
        if not set(self.owned_cell_ids) <= set(self.all_cell_ids):
            raise ValueError("modality execution owns an unknown AuditPlan cell")
        if len(set(self.required_cell_ids)) != len(self.required_cell_ids) or not set(
            self.required_cell_ids
        ) <= set(self.owned_cell_ids):
            raise ValueError("modality required cells differ from owned cells")
        if not self.owned_span_ids or len(set(self.owned_span_ids)) != len(self.owned_span_ids):
            raise ValueError("modality execution requires unique owned spans")
        selection_values = (
            self.selection_plan_id,
            self.selection_plan_content_hash,
            self.selection_plan_artifact_hash,
            self.tier,
        )
        if self.modality == "text":
            if any(value is not None for value in selection_values):
                raise ValueError("text execution cannot claim audio selection")
            if set(self.owned_cell_ids) != set(self.all_cell_ids):
                raise ValueError("text execution must retain universal cell ownership")
        elif any(value is None for value in selection_values):
            raise ValueError("audio execution requires exact selection lineage")
        if not self.packets or any(item.modality != self.modality for item in self.packets):
            raise ValueError("modality execution packets have the wrong modality")
        if tuple(item.packet_index for item in self.packets) != tuple(range(len(self.packets))):
            raise ValueError("modality execution packet indices are incomplete")
        owned = tuple(cell for packet in self.packets for cell in packet.owned_cell_ids)
        requested = tuple(cell for packet in self.packets for cell in packet.requested_cell_ids)
        if len(set(owned)) != len(owned) or set(owned) != set(self.owned_cell_ids):
            raise ValueError("modality packets do not partition exact owned cells")
        if len(set(requested)) != len(requested) or set(requested) != set(self.required_cell_ids):
            raise ValueError("modality packets do not partition exact required cells")
        for packet in self.packets:
            if (
                packet.episode_id != self.episode_id
                or packet.generation_id != self.generation_id
                or packet.audit_plan_hash != self.audit_plan_hash
                or packet.audit_plan_content_hash != self.audit_plan_content_hash
                or packet.canonical_transcript_hash != self.canonical_transcript_hash
                or packet.normalized_audio_hash != self.normalized_audio_hash
                or packet.policy_hash != self.policy_hash
            ):
                raise ValueError("modality packet lineage differs from its plan")
        payload = self.model_dump(mode="json", exclude={"id", "content_hash"})
        expected = _hash_json(payload)
        if self.id != expected or self.content_hash != expected:
            raise ValueError("modality execution identity/content_hash mismatch")
        return self


class SelectiveAudioAuditExecutionRecordV3(_Contract):
    """Exact selected-cell execution; unselected cells receive no disposition."""

    schema_version: Literal[3] = 3
    id: Sha256
    execution_plan_id: Sha256
    execution_plan_content_hash: Sha256
    selection_plan_id: Sha256
    selection_plan_content_hash: Sha256
    tier: AudioAuditSelectionTierV1
    audit_plan_hash: Sha256
    audit_plan_content_hash: Sha256
    policy_hash: Sha256
    adapter_identity_hash: Sha256
    request_ids: tuple[Sha256, ...]
    invocation_journals: tuple[AudioAuditInvocationJournalV2, ...]
    response_receipts: tuple[AudioAuditProviderResponseReceiptV2, ...]
    assessments: tuple[AudioAuditCellAssessmentV2, ...]
    selected_owned_span_ids: tuple[str, ...]
    selected_owned_cell_ids: tuple[Sha256, ...]
    selected_required_cell_ids: tuple[Sha256, ...]
    assessed_cell_ids: tuple[Sha256, ...]
    discovered_candidates: tuple[AudioDiscoveredCandidateV2, ...]
    material_span_ids: tuple[str, ...]
    major_span_ids: tuple[str, ...]
    unresolved_span_ids: tuple[str, ...]
    catastrophic_span_ids: tuple[str, ...]
    execution_status: Literal["complete_selective_audio_delta"] = "complete_selective_audio_delta"
    coverage_statement: Literal[
        "exact_selected_delta_required_cells_assessed_unselected_cells_have_no_disposition"
    ] = "exact_selected_delta_required_cells_assessed_unselected_cells_have_no_disposition"
    authority: Literal["not_correction_decisions_or_release_approval"] = (
        "not_correction_decisions_or_release_approval"
    )
    content_hash: Sha256

    @model_validator(mode="after")
    def _is_exact_selected_record(self) -> SelectiveAudioAuditExecutionRecordV3:
        for label, values in (
            ("request_ids", self.request_ids),
            ("selected_owned_span_ids", self.selected_owned_span_ids),
            ("selected_owned_cell_ids", self.selected_owned_cell_ids),
            ("selected_required_cell_ids", self.selected_required_cell_ids),
            ("assessed_cell_ids", self.assessed_cell_ids),
            ("material_span_ids", self.material_span_ids),
            ("major_span_ids", self.major_span_ids),
            ("unresolved_span_ids", self.unresolved_span_ids),
            ("catastrophic_span_ids", self.catastrophic_span_ids),
        ):
            if len(set(values)) != len(values):
                raise ValueError(f"selective audio record {label} must be unique")
        if (
            not self.request_ids
            or tuple(item.request_id for item in self.response_receipts) != self.request_ids
        ):
            raise ValueError("selective audio responses differ from requests")
        if len(self.invocation_journals) != len(self.request_ids):
            raise ValueError("selective audio journals lack request coverage")
        assessment_ids = tuple(
            assessment_id
            for receipt in self.response_receipts
            for assessment_id in receipt.assessment_ids
        )
        if assessment_ids != tuple(item.id for item in self.assessments):
            raise ValueError("selective audio assessments differ from receipts")
        if self.assessed_cell_ids != tuple(item.cell_id for item in self.assessments):
            raise ValueError("selective audio assessed-cell order drift")
        if set(self.assessed_cell_ids) != set(self.selected_required_cell_ids):
            raise ValueError("selective audio lacks exact selected required-cell coverage")
        if tuple(sorted(self.discovered_candidates, key=lambda item: item.id)) != (
            self.discovered_candidates
        ) or len({item.id for item in self.discovered_candidates}) != len(
            self.discovered_candidates
        ):
            raise ValueError("selective audio discoveries require canonical unique order")
        assessment_ids = {item.id for item in self.assessments}
        if any(item.assessment_id not in assessment_ids for item in self.discovered_candidates):
            raise ValueError("selective audio discovery lacks an exact selected assessment")
        spans = set(self.selected_owned_span_ids)
        if (
            not (
                set(self.material_span_ids)
                | set(self.major_span_ids)
                | set(self.unresolved_span_ids)
                | set(self.catastrophic_span_ids)
            )
            <= spans
        ):
            raise ValueError("selective audio findings target an unselected owned span")
        if not set(self.major_span_ids) <= set(self.material_span_ids):
            raise ValueError("selective audio major findings must be material")
        payload = self.model_dump(mode="json", exclude={"id", "content_hash"})
        expected = _hash_json(payload)
        if self.id != expected or self.content_hash != expected:
            raise ValueError("selective audio record identity/content_hash mismatch")
        return self


class SelectiveAudioAuditRoundReceiptV3(_Contract):
    """Cumulative round ledger entry over exact incremental execution records."""

    schema_version: Literal[3] = 3
    id: Sha256
    episode_id: str
    generation_id: str
    normalized_audio_hash: Sha256
    selection_plan_id: Sha256
    selection_plan_content_hash: Sha256
    selection_plan_artifact_hash: Sha256
    tier: AudioAuditSelectionTierV1
    previous_round_receipt_id: Sha256 | None = None
    execution_record_ids: tuple[Sha256, ...]
    execution_record_content_hashes: tuple[Sha256, ...]
    execution_record_artifact_hashes: tuple[Sha256, ...]
    decision_receipt: AudioAuditSelectionReceiptV1
    decision_receipt_artifact_hash: Sha256
    execution_scope: Literal[
        "cumulative_exact_selected_spans_from_nonoverlapping_delta_records"
    ] = "cumulative_exact_selected_spans_from_nonoverlapping_delta_records"
    authority: Literal["escalation_only_not_correction_or_release"] = (
        "escalation_only_not_correction_or_release"
    )
    content_hash: Sha256

    @model_validator(mode="after")
    def _is_content_addressed(self) -> SelectiveAudioAuditRoundReceiptV3:
        count = len(self.execution_record_ids)
        if count == 0 or any(
            len(values) != count
            for values in (
                self.execution_record_content_hashes,
                self.execution_record_artifact_hashes,
            )
        ):
            raise ValueError("selective audio round lacks execution record coverage")
        if len(set(self.execution_record_ids)) != count:
            raise ValueError("selective audio round repeats an execution record")
        if (
            self.decision_receipt.plan_id != self.selection_plan_id
            or self.decision_receipt.plan_content_hash != self.selection_plan_content_hash
            or self.decision_receipt.tier != self.tier
            or self.decision_receipt_artifact_hash != _hash_json(self.decision_receipt)
        ):
            raise ValueError("selective audio decision differs from round selection")
        payload = self.model_dump(mode="json", exclude={"id", "content_hash"})
        expected = _hash_json(payload)
        if self.id != expected or self.content_hash != expected:
            raise ValueError("selective audio round identity/content_hash mismatch")
        return self


__all__ = [
    "ModalityAuditExecutionPlanV3",
    "SelectiveAudioAuditExecutionRecordV3",
    "SelectiveAudioAuditRoundReceiptV3",
    "Sha256",
]
