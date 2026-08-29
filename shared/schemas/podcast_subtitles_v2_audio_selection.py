"""Versioned contracts for selective Podcast Subtitle V2 audio audit.

These artifacts authorize only an exact audio-audit scope.  They do not
constitute an AudioAudit receipt, a Correction Decision, or release approval.
"""

from __future__ import annotations

import hashlib
import json
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, model_validator

Sha256 = Annotated[str, StringConstraints(pattern=r"^[0-9a-f]{64}$")]
AudioAuditSelectionTierV1 = Literal["sample_10", "sample_30", "full"]
AudioAuditSelectionDecisionV1 = Literal["complete", "escalate_30", "escalate_full"]
AudioAuditRiskReasonV1 = Literal[
    "explicit_low_confidence",
    "speaker_conflict",
    "boundary_or_seam_anomaly",
    "speech_coverage_anomaly",
    "text_finding",
    "text_disagreement",
    "canonical_review_issue",
]


def _hash_json(value: object) -> str:
    if isinstance(value, BaseModel):
        value = value.model_dump(mode="json", exclude_none=False)
    payload = json.dumps(
        value,
        ensure_ascii=False,
        allow_nan=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


class _SelectionContract(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class AudioAuditSelectionPolicyV1(_SelectionContract):
    schema_version: Literal[1] = 1
    policy_id: Literal["nakama-selective-audio-audit"] = "nakama-selective-audio-audit"
    policy_version: Literal[1] = 1
    initial_sample_basis_points: Literal[1000] = 1000
    expanded_sample_basis_points: Literal[3000] = 3000
    material_error_threshold_basis_points: Literal[200] = 200
    clock_strata: int = Field(default=10, ge=1, le=100)
    speaker_stratification: Literal[True] = True
    sample_algorithm: Literal["sha256-ranked-clock-speaker-strata-v1"] = (
        "sha256-ranked-clock-speaker-strata-v1"
    )
    risk_policy: Literal["closed-risk-codes-plus-text-findings-v1"] = (
        "closed-risk-codes-plus-text-findings-v1"
    )
    catastrophic_escalation: Literal["full_immediately"] = "full_immediately"
    threshold_comparison: Literal["strictly_greater_than"] = "strictly_greater_than"
    content_hash: Sha256

    @model_validator(mode="after")
    def _is_content_addressed(self) -> AudioAuditSelectionPolicyV1:
        expected = _hash_json(self.model_dump(mode="json", exclude={"content_hash"}))
        if self.content_hash != expected:
            raise ValueError("audio selection policy content_hash mismatch")
        return self


class AudioAuditRiskTargetV1(_SelectionContract):
    schema_version: Literal[1] = 1
    span_id: str
    span_target_id: Sha256
    reasons: tuple[AudioAuditRiskReasonV1, ...]
    source_finding_ids: tuple[str, ...]

    @model_validator(mode="after")
    def _has_canonical_reasons(self) -> AudioAuditRiskTargetV1:
        if not self.span_id or self.span_id != self.span_id.strip():
            raise ValueError("audio risk target requires a bounded span_id")
        if not self.reasons or len(set(self.reasons)) != len(self.reasons):
            raise ValueError("audio risk target requires unique reasons")
        if tuple(sorted(self.reasons)) != self.reasons:
            raise ValueError("audio risk reasons must use canonical order")
        if not self.source_finding_ids or len(set(self.source_finding_ids)) != len(
            self.source_finding_ids
        ):
            raise ValueError("audio risk target requires unique source finding IDs")
        if tuple(sorted(self.source_finding_ids)) != self.source_finding_ids:
            raise ValueError("audio risk source finding IDs must use canonical order")
        return self


class AudioAuditStratumAllocationV1(_SelectionContract):
    schema_version: Literal[1] = 1
    stratum_id: Sha256
    clock_bucket: int = Field(ge=0)
    speaker_label: str
    population_span_ids: tuple[str, ...]
    quota: int = Field(ge=0)
    selected_span_ids: tuple[str, ...]

    @model_validator(mode="after")
    def _is_exact_allocation(self) -> AudioAuditStratumAllocationV1:
        if not self.speaker_label or self.speaker_label != self.speaker_label.strip():
            raise ValueError("audio stratum requires a bounded speaker label")
        if not self.population_span_ids or len(set(self.population_span_ids)) != len(
            self.population_span_ids
        ):
            raise ValueError("audio stratum requires a unique population")
        if self.quota != len(self.selected_span_ids):
            raise ValueError("audio stratum quota differs from selected spans")
        if not set(self.selected_span_ids) <= set(self.population_span_ids):
            raise ValueError("audio stratum selected an out-of-population span")
        expected = _hash_json(
            {
                "clock_bucket": self.clock_bucket,
                "speaker_label": self.speaker_label,
                "population_span_ids": self.population_span_ids,
            }
        )
        if self.stratum_id != expected:
            raise ValueError("audio stratum identity mismatch")
        return self


class AudioAuditSelectionPlanV1(_SelectionContract):
    schema_version: Literal[1] = 1
    id: Sha256
    episode_id: str
    generation_id: str
    normalized_audio_hash: Sha256
    audit_plan_hash: Sha256
    audit_plan_content_hash: Sha256
    canonical_transcript_hash: Sha256
    canonical_content_hash: Sha256
    text_audit_record_hash: Sha256 | None = None
    text_audit_record_content_hash: Sha256 | None = None
    policy: AudioAuditSelectionPolicyV1
    policy_hash: Sha256
    tier: AudioAuditSelectionTierV1
    prior_plan_id: Sha256 | None = None
    prior_receipt_id: Sha256 | None = None
    sample_seed: Sha256
    all_span_ids: tuple[str, ...]
    risk_targets: tuple[AudioAuditRiskTargetV1, ...]
    risk_span_ids: tuple[str, ...]
    strata: tuple[AudioAuditStratumAllocationV1, ...]
    sample_span_ids: tuple[str, ...]
    selected_span_ids: tuple[str, ...]
    selected_owned_cell_ids: tuple[Sha256, ...]
    selected_required_cell_ids: tuple[Sha256, ...]
    selection_scope: Literal["exact_selected_audio_targets_only"] = (
        "exact_selected_audio_targets_only"
    )
    authority: Literal["scope_only_not_audio_evidence_or_release_approval"] = (
        "scope_only_not_audio_evidence_or_release_approval"
    )
    content_hash: Sha256

    @model_validator(mode="after")
    def _is_closed_and_content_addressed(self) -> AudioAuditSelectionPlanV1:
        if not self.all_span_ids or len(set(self.all_span_ids)) != len(self.all_span_ids):
            raise ValueError("audio selection plan requires unique ordered spans")
        all_spans = set(self.all_span_ids)
        for label, values in (
            ("risk_span_ids", self.risk_span_ids),
            ("sample_span_ids", self.sample_span_ids),
            ("selected_span_ids", self.selected_span_ids),
            ("selected_owned_cell_ids", self.selected_owned_cell_ids),
            ("selected_required_cell_ids", self.selected_required_cell_ids),
        ):
            if len(set(values)) != len(values):
                raise ValueError(f"audio selection {label} must be unique")
        if not set(self.risk_span_ids) <= all_spans or not set(self.sample_span_ids) <= all_spans:
            raise ValueError("audio selection names an unknown span")
        if set(self.risk_span_ids) & set(self.sample_span_ids):
            raise ValueError("risk targets cannot also count as deterministic sample")
        if set(self.selected_span_ids) != set(self.risk_span_ids) | set(self.sample_span_ids):
            raise ValueError("selected audio targets differ from risk plus sample union")
        if tuple(item.span_id for item in self.risk_targets) != self.risk_span_ids:
            raise ValueError("audio risk target details differ from risk span IDs")
        ordinary = all_spans - set(self.risk_span_ids)
        stratum_population = tuple(
            span_id for item in self.strata for span_id in item.population_span_ids
        )
        if (
            len(set(stratum_population)) != len(stratum_population)
            or set(stratum_population) != ordinary
        ):
            raise ValueError("audio strata do not exactly partition ordinary spans")
        if set(span_id for item in self.strata for span_id in item.selected_span_ids) != set(
            self.sample_span_ids
        ):
            raise ValueError("audio strata selections differ from sample spans")
        if self.policy_hash != _hash_json(self.policy):
            raise ValueError("audio selection plan policy hash mismatch")
        if (self.text_audit_record_hash is None) != (self.text_audit_record_content_hash is None):
            raise ValueError("text audit bindings are all-or-none")
        if self.tier == "sample_10" and (
            self.prior_plan_id is not None or self.prior_receipt_id is not None
        ):
            raise ValueError("initial audio selection cannot claim a prior round")
        if self.tier != "sample_10" and (
            self.prior_plan_id is None or self.prior_receipt_id is None
        ):
            raise ValueError("escalated audio selection requires an exact prior round")
        payload = self.model_dump(mode="json", exclude={"id", "content_hash"})
        expected = _hash_json(payload)
        if self.id != expected or self.content_hash != expected:
            raise ValueError("audio selection plan identity/content_hash mismatch")
        return self


class AudioAuditSelectionReceiptV1(_SelectionContract):
    schema_version: Literal[1] = 1
    id: Sha256
    plan_id: Sha256
    plan_content_hash: Sha256
    plan_artifact_hash: Sha256
    episode_id: str
    generation_id: str
    normalized_audio_hash: Sha256
    policy_hash: Sha256
    tier: AudioAuditSelectionTierV1
    audited_span_ids: tuple[str, ...]
    sampled_audited_span_ids: tuple[str, ...]
    risk_audited_span_ids: tuple[str, ...]
    material_error_span_ids: tuple[str, ...]
    major_error_span_ids: tuple[str, ...]
    unresolved_span_ids: tuple[str, ...]
    risk_finding_span_ids: tuple[str, ...]
    catastrophic_span_ids: tuple[str, ...]
    material_error_numerator: int = Field(ge=0)
    material_error_denominator: int = Field(ge=0)
    decision: AudioAuditSelectionDecisionV1
    coverage_statement: Literal["exact_selected_targets_have_audio_receipts"] = (
        "exact_selected_targets_have_audio_receipts"
    )
    authority: Literal["selection_escalation_only_not_correction_or_release"] = (
        "selection_escalation_only_not_correction_or_release"
    )
    content_hash: Sha256

    @model_validator(mode="after")
    def _is_closed_and_content_addressed(self) -> AudioAuditSelectionReceiptV1:
        audited = set(self.audited_span_ids)
        if not self.audited_span_ids or len(audited) != len(self.audited_span_ids):
            raise ValueError("audio selection receipt requires unique audited spans")
        if not set(self.material_error_span_ids) <= audited:
            raise ValueError("material errors must have selected audio coverage")
        if set(self.sampled_audited_span_ids) != set(self.audited_span_ids) - set(
            self.risk_audited_span_ids
        ):
            raise ValueError("sample/risk receipt partitions differ from audited coverage")
        if not set(self.material_error_span_ids) <= set(self.sampled_audited_span_ids):
            raise ValueError("sample material errors must belong to sampled ordinary spans")
        if not set(self.major_error_span_ids) <= set(self.material_error_span_ids):
            raise ValueError("major errors must be material sampled errors")
        if not set(self.unresolved_span_ids) <= audited:
            raise ValueError("unresolved audio findings require selected coverage")
        if not set(self.risk_finding_span_ids) <= set(self.risk_audited_span_ids):
            raise ValueError("risk findings must belong to risk-targeted spans")
        if not set(self.catastrophic_span_ids) <= set(self.material_error_span_ids):
            if not set(self.catastrophic_span_ids) <= audited:
                raise ValueError("catastrophic findings require selected audio coverage")
        if self.material_error_numerator != len(self.material_error_span_ids):
            raise ValueError("audio material-error numerator mismatch")
        if self.material_error_denominator != len(self.sampled_audited_span_ids):
            raise ValueError("audio material-error denominator mismatch")
        payload = self.model_dump(mode="json", exclude={"id", "content_hash"})
        expected = _hash_json(payload)
        if self.id != expected or self.content_hash != expected:
            raise ValueError("audio selection receipt identity/content_hash mismatch")
        return self


__all__ = [
    "AudioAuditRiskReasonV1",
    "AudioAuditRiskTargetV1",
    "AudioAuditStratumAllocationV1",
    "AudioAuditSelectionDecisionV1",
    "AudioAuditSelectionPlanV1",
    "AudioAuditSelectionPolicyV1",
    "AudioAuditSelectionReceiptV1",
    "AudioAuditSelectionTierV1",
]
