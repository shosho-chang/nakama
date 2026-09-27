"""Strict execution contracts for Podcast Subtitle V2 correction quality.

This schema family is deliberately isolated from the frozen Slice 1 audit
contracts.  Version 2 artifacts describe bounded execution topology only;
they are neither provider responses nor proof of semantic recall.
"""

from __future__ import annotations

import hashlib
import json
import re
from typing import Annotated, Literal

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    StringConstraints,
    field_validator,
    model_validator,
)

from shared.schemas.podcast_subtitles_v2 import (
    AuditCell,
    BoundaryAuditTarget,
    CandidateGroup,
    CandidateSignal,
    CanonicalSpan,
    CanonicalToken,
    EvidenceToken,
    RecognitionSeamObservation,
    ReferenceKind,
    ReferenceLocatorKind,
    ReferenceOffsetUnit,
    ReferenceSourceFormat,
    ReferenceTrustTier,
    SpanAuditTarget,
)

Sha256 = Annotated[str, StringConstraints(pattern=r"^[0-9a-f]{64}$")]
CorrectionAuditModalityV2 = Literal["text", "audio"]
CorrectionSourceKindV2 = Literal[
    "audit_plan",
    "candidate_signal_set",
    "candidate_group_set",
    "canonical_transcript",
    "recognition_evidence_set",
    "recognition_seam_evidence",
    "reference_retrieval_receipt_set",
    "reference_excerpt",
    "normalized_audio",
    "audio_clip",
    "execution_plan",
    "work_packet",
    "provider_request",
    "provider_response",
    "provider_failure",
    "text_disposition_set",
    "audio_disposition_set",
    "candidate_discovery_set",
    "final_candidate_group_set",
    "group_arbitration_set",
    "correction_quality_attestation",
]
CorrectionArtifactMediaTypeV2 = Literal["application/json", "audio/wav"]
CorrectionSplitReasonV2 = Literal[
    "episode_start",
    "episode_end",
    "known_speaker_transition",
    "recognition_seam",
    "selection_gap",
    "max_cells",
    "max_spans",
    "max_tokens",
    "max_duration",
]

_SPLIT_REASON_ORDER: tuple[CorrectionSplitReasonV2, ...] = (
    "episode_start",
    "episode_end",
    "known_speaker_transition",
    "recognition_seam",
    "selection_gap",
    "max_cells",
    "max_spans",
    "max_tokens",
    "max_duration",
)
_SOURCE_KIND_ORDER: tuple[CorrectionSourceKindV2, ...] = (
    "audit_plan",
    "candidate_signal_set",
    "candidate_group_set",
    "canonical_transcript",
    "recognition_evidence_set",
    "recognition_seam_evidence",
    "reference_retrieval_receipt_set",
    "reference_excerpt",
    "normalized_audio",
    "audio_clip",
    "execution_plan",
    "work_packet",
    "provider_request",
    "provider_response",
    "provider_failure",
    "text_disposition_set",
    "audio_disposition_set",
    "candidate_discovery_set",
    "final_candidate_group_set",
    "group_arbitration_set",
    "correction_quality_attestation",
)


def _hash_json(value: object) -> str:
    if isinstance(value, BaseModel):
        value = value.model_dump(mode="json", exclude_none=False)
    payload = json.dumps(
        value,
        ensure_ascii=False,
        allow_nan=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


class _CorrectionV2Contract(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)


class CorrectionSourceBindingV2(_CorrectionV2Contract):
    """Pair a logical source identity with the digest of its exact bytes.

    ``internal_object_hash`` is explicitly separate from ``content_hash``.
    The former may identify a domain payload; only ``content_hash`` identifies
    the exact serialized JSON or media artifact bytes.  Plan bindings are
    lineage; packet bindings are the bounded artifacts permitted to a worker.
    """

    schema_version: Literal[2] = 2
    kind: CorrectionSourceKindV2
    id: str
    content_hash: Sha256
    record_ids: tuple[str, ...]
    artifact_uri: str
    size_bytes: int = Field(ge=0)
    media_type: CorrectionArtifactMediaTypeV2
    internal_object_hash: Sha256 | None = None
    binding_hash: Sha256

    @field_validator("id", "artifact_uri")
    @classmethod
    def _source_metadata_is_bounded(cls, value: str, info: object) -> str:
        if not value or value != value.strip():
            raise ValueError(f"{getattr(info, 'field_name', 'value')} must be non-blank")
        if len(value) > 1_024:
            raise ValueError(f"{getattr(info, 'field_name', 'value')} is too long")
        return value

    @model_validator(mode="after")
    def _binding_is_typed_and_content_addressed(self) -> CorrectionSourceBindingV2:
        if not self.artifact_uri.startswith("execution-artifact://"):
            raise ValueError("correction source binding requires a portable artifact URI")
        if (
            not self.record_ids
            or tuple(sorted(self.record_ids)) != self.record_ids
            or len(set(self.record_ids)) != len(self.record_ids)
        ):
            raise ValueError(
                "correction source binding record IDs must be non-empty, sorted, and unique"
            )
        audio_kind = self.kind in {"normalized_audio", "audio_clip"}
        if audio_kind != (self.media_type == "audio/wav"):
            raise ValueError("correction source binding media type is incompatible with kind")
        if audio_kind and self.internal_object_hash is not None:
            raise ValueError("audio byte digest cannot masquerade as an internal content hash")
        expected = _hash_json(self.model_dump(mode="json", exclude={"binding_hash"}))
        if self.binding_hash != expected:
            raise ValueError("correction source binding binding_hash mismatch")
        return self


class _CorrectionPacketSourceSliceV2(_CorrectionV2Contract):
    """Common immutable lineage for exact, bounded packet input bytes."""

    schema_version: Literal[2] = 2
    parent_source_id: str
    parent_artifact_hash: Sha256
    parent_internal_object_hash: Sha256
    episode_id: str
    generation_id: str
    record_ids: tuple[str, ...]
    content_hash: Sha256

    @field_validator("parent_source_id", "episode_id", "generation_id")
    @classmethod
    def _slice_identity_is_bounded(cls, value: str, info: object) -> str:
        if not value or value != value.strip() or len(value) > 1_024:
            raise ValueError(f"{getattr(info, 'field_name', 'value')} must be bounded")
        return value

    @model_validator(mode="after")
    def _slice_is_content_addressed(self) -> _CorrectionPacketSourceSliceV2:
        if (
            not self.record_ids
            or tuple(sorted(self.record_ids)) != self.record_ids
            or len(set(self.record_ids)) != len(self.record_ids)
        ):
            raise ValueError("packet source slice record IDs must be non-empty, sorted, unique")
        expected = _hash_json(self.model_dump(mode="json", exclude={"content_hash"}))
        if self.content_hash != expected:
            raise ValueError("packet source slice content_hash mismatch")
        return self


class CorrectionAuditPlanSliceV2(_CorrectionPacketSourceSliceV2):
    kind: Literal["audit_plan"] = "audit_plan"
    cells: tuple[AuditCell, ...]
    span_targets: tuple[SpanAuditTarget, ...]
    boundary_targets: tuple[BoundaryAuditTarget, ...]

    @model_validator(mode="after")
    def _audit_slice_is_exact(self) -> CorrectionAuditPlanSliceV2:
        if not self.cells or not self.span_targets:
            raise ValueError("audit packet slice requires cells and span targets")
        if self.record_ids != tuple(sorted(item.id for item in self.cells)):
            raise ValueError("audit packet slice record IDs differ from cells")
        target_ids = {item.id for item in (*self.span_targets, *self.boundary_targets)}
        if {item.target_id for item in self.cells} != target_ids:
            raise ValueError("audit packet slice targets differ from its cells")
        return self


class CorrectionCandidateSignalSliceV2(_CorrectionPacketSourceSliceV2):
    kind: Literal["candidate_signal_set"] = "candidate_signal_set"
    signals: tuple[CandidateSignal, ...]

    @model_validator(mode="after")
    def _signal_slice_is_exact(self) -> CorrectionCandidateSignalSliceV2:
        expected = tuple(sorted(item.id for item in self.signals)) or (
            self.parent_internal_object_hash,
        )
        if self.record_ids != expected:
            raise ValueError("candidate signal slice record IDs differ from signals")
        return self


class CorrectionCandidateGroupSliceV2(_CorrectionPacketSourceSliceV2):
    kind: Literal["candidate_group_set"] = "candidate_group_set"
    groups: tuple[CandidateGroup, ...]

    @model_validator(mode="after")
    def _group_slice_is_exact(self) -> CorrectionCandidateGroupSliceV2:
        expected = tuple(sorted(item.id for item in self.groups)) or (
            self.parent_internal_object_hash,
        )
        if self.record_ids != expected:
            raise ValueError("candidate group slice record IDs differ from groups")
        return self


class CorrectionCanonicalTranscriptSliceV2(_CorrectionPacketSourceSliceV2):
    kind: Literal["canonical_transcript"] = "canonical_transcript"
    tokens: tuple[CanonicalToken, ...]
    spans: tuple[CanonicalSpan, ...]

    @model_validator(mode="after")
    def _canonical_slice_is_exact(self) -> CorrectionCanonicalTranscriptSliceV2:
        if not self.tokens or not self.spans:
            raise ValueError("canonical packet slice requires visible tokens and spans")
        token_ids = {item.id for item in self.tokens}
        if any(not set(item.token_ids) <= token_ids for item in self.spans):
            raise ValueError("canonical packet slice span refers outside visible tokens")
        expected = tuple(sorted((*token_ids, *(item.id for item in self.spans))))
        if self.record_ids != expected:
            raise ValueError("canonical packet slice record IDs differ from tokens/spans")
        return self


class CorrectionRecognitionEvidenceItemSliceV2(_CorrectionV2Contract):
    schema_version: Literal[2] = 2
    source_content_hash: Sha256
    episode_id: str
    invocation_id: str
    adapter: str
    model: str
    language: str
    config_hash: Sha256
    raw_output_hash: Sha256
    raw_output_size_bytes: int = Field(ge=0)
    normalized_audio_hash: Sha256
    tokens: tuple[EvidenceToken, ...]

    @model_validator(mode="after")
    def _recognition_item_is_bounded(self) -> CorrectionRecognitionEvidenceItemSliceV2:
        if not self.tokens or len({item.id for item in self.tokens}) != len(self.tokens):
            raise ValueError("recognition packet source requires unique visible Evidence tokens")
        return self


class CorrectionRecognitionEvidenceSliceV2(_CorrectionPacketSourceSliceV2):
    kind: Literal["recognition_evidence_set"] = "recognition_evidence_set"
    sources: tuple[CorrectionRecognitionEvidenceItemSliceV2, ...]

    @model_validator(mode="after")
    def _recognition_slice_is_exact(self) -> CorrectionRecognitionEvidenceSliceV2:
        if not self.sources:
            raise ValueError("recognition packet slice requires visible source Evidence")
        if len({item.source_content_hash for item in self.sources}) != len(self.sources):
            raise ValueError("recognition packet slice repeats a source")
        expected = tuple(
            sorted(
                f"{item.source_content_hash}:{token.id}"
                for item in self.sources
                for token in item.tokens
            )
        )
        if self.record_ids != expected:
            raise ValueError("recognition packet slice record IDs differ from Evidence tokens")
        return self


class CorrectionRecognitionSeamSliceV2(_CorrectionPacketSourceSliceV2):
    kind: Literal["recognition_seam_evidence"] = "recognition_seam_evidence"
    observations: tuple[RecognitionSeamObservation, ...]

    @model_validator(mode="after")
    def _seam_slice_is_exact(self) -> CorrectionRecognitionSeamSliceV2:
        if not self.observations:
            raise ValueError("seam packet slice requires visible observations")
        if self.record_ids != tuple(sorted(item.id for item in self.observations)):
            raise ValueError("seam packet slice record IDs differ from observations")
        return self


class CorrectionPacketReferenceLocatorPartV2(_CorrectionV2Contract):
    schema_version: Literal[2] = 2
    kind: ReferenceLocatorKind
    value: str

    @field_validator("value")
    @classmethod
    def _locator_value_is_safe(cls, value: str) -> str:
        if not value or value != value.strip() or len(value) > 1_024:
            raise ValueError("packet reference locator must be bounded")
        if any(ord(character) < 32 or ord(character) == 127 for character in value):
            raise ValueError("packet reference locator cannot contain control characters")
        normalized = value.replace("\\", "/")
        if (
            re.search(r"(?i)(?:file|https?|ftp)://", normalized)
            or re.match(r"(?i)^[a-z]:/", normalized)
            or normalized.startswith("//")
            or normalized.startswith("/")
        ):
            raise ValueError("packet reference locator cannot contain a URI or absolute path")
        return value


class CorrectionPacketReferenceLocatorV2(_CorrectionV2Contract):
    schema_version: Literal[2] = 2
    parts: tuple[CorrectionPacketReferenceLocatorPartV2, ...]

    @model_validator(mode="after")
    def _locator_is_nonempty_and_unique(self) -> CorrectionPacketReferenceLocatorV2:
        if not self.parts:
            raise ValueError("packet reference locator requires structured parts")
        keys = tuple((item.kind, item.value) for item in self.parts)
        if len(set(keys)) != len(keys):
            raise ValueError("packet reference locator parts must be unique")
        return self


class CorrectionReferenceExcerptSliceV2(_CorrectionPacketSourceSliceV2):
    """URI-free least-context Reference Evidence presented to one packet."""

    kind: Literal["reference_excerpt"] = "reference_excerpt"
    evidence_id: str
    retrieved_for_audio_span_ids: tuple[str, ...]
    source_id: str
    reference_kind: ReferenceKind
    source_format: ReferenceSourceFormat
    source_sha256: Sha256
    source_size_bytes: int = Field(ge=0)
    extracted_text_sha256: Sha256
    extracted_text_size_bytes: int = Field(ge=0)
    extractor_name: str
    extractor_version: str
    extractor_config_hash: Sha256
    extractor_code_hash: Sha256
    extractor_runtime_hash: Sha256
    offset_unit: ReferenceOffsetUnit
    extraction_block_count: int = Field(gt=0)
    title: str
    author: str | None = None
    publisher: str | None = None
    source_version: str
    document_date: str
    enrollment_manifest_sha256: Sha256 | None = None
    trust_tier: ReferenceTrustTier
    locator: CorrectionPacketReferenceLocatorV2
    extraction_block_index: int = Field(ge=0)
    extraction_block_hash: Sha256
    excerpt_start: int = Field(ge=0)
    excerpt_end: int = Field(gt=0)
    excerpt: str
    excerpt_hash: Sha256

    @model_validator(mode="after")
    def _reference_slice_is_exact(self) -> CorrectionReferenceExcerptSliceV2:
        if self.record_ids != (self.evidence_id,):
            raise ValueError("reference excerpt slice record ID differs from Evidence ID")
        if not self.retrieved_for_audio_span_ids or len(
            set(self.retrieved_for_audio_span_ids)
        ) != len(self.retrieved_for_audio_span_ids):
            raise ValueError(
                "reference excerpt slice requires unique retrieval target span membership"
            )
        if any(
            not item or item != item.strip() or len(item) > 256
            for item in self.retrieved_for_audio_span_ids
        ):
            raise ValueError("reference excerpt retrieval target span ID is invalid")
        if self.excerpt_end - self.excerpt_start != len(self.excerpt):
            raise ValueError("reference excerpt slice range differs from excerpt")
        expected_excerpt_hash = hashlib.sha256(self.excerpt.encode("utf-8")).hexdigest()
        if self.excerpt_hash != expected_excerpt_hash:
            raise ValueError("reference excerpt slice excerpt_hash mismatch")
        if self.extraction_block_index >= self.extraction_block_count:
            raise ValueError("reference excerpt slice block index is outside the source")
        return self


class CorrectionAuditExecutionLimitsV2(_CorrectionV2Contract):
    """All simultaneous packet limits for one correction modality."""

    schema_version: Literal[2] = 2
    modality: CorrectionAuditModalityV2
    max_cells_per_packet: int = Field(ge=1)
    max_spans_per_packet: int = Field(ge=1)
    max_tokens_per_packet: int = Field(ge=1)
    max_window_duration_ms: int = Field(ge=1)
    context_halo_spans_per_side: int = Field(ge=0)
    clip_padding_ms: int = Field(ge=0)
    max_clip_duration_ms: int | None = Field(default=None, ge=1)

    @model_validator(mode="after")
    def _modality_shape(self) -> CorrectionAuditExecutionLimitsV2:
        if self.modality == "text":
            if self.clip_padding_ms != 0 or self.max_clip_duration_ms is not None:
                raise ValueError("text execution limits cannot claim audio clip limits")
        elif self.max_clip_duration_ms is None:
            raise ValueError("audio execution limits require max_clip_duration_ms")
        return self


class CorrectionAuditExecutionPolicyV2(_CorrectionV2Contract):
    """Immutable policy that makes packet derivation replayable after restart."""

    schema_version: Literal[2] = 2
    policy_id: Literal["nakama-correction-audit-execution"] = "nakama-correction-audit-execution"
    policy_version: Literal[2] = 2
    modalities: tuple[CorrectionAuditModalityV2, ...] = ("text", "audio")
    text: CorrectionAuditExecutionLimitsV2
    audio: CorrectionAuditExecutionLimitsV2
    boundary_ownership: Literal["right_span_then_final_left_v1"] = "right_span_then_final_left_v1"
    split_on_known_speaker_transition: Literal[True] = True
    split_on_recognition_seam: Literal[True] = True
    context_selection_algorithm: Literal["bounded-nearest-bilateral-v1"] = (
        "bounded-nearest-bilateral-v1"
    )
    audio_clip_derivation: Literal["pcm-wav-frame-exact-v1"] = "pcm-wav-frame-exact-v1"
    unsplittable_target_mode: Literal["fail_closed"] = "fail_closed"
    planner_id: Literal["nakama-correction-audit-execution-planner"] = (
        "nakama-correction-audit-execution-planner"
    )
    planner_version: Literal[2] = 2
    planner_code_hash: Sha256
    content_hash: Sha256

    @field_validator(
        "split_on_known_speaker_transition",
        "split_on_recognition_seam",
        mode="before",
    )
    @classmethod
    def _literal_flags_are_exact_bools(cls, value: object) -> object:
        if type(value) is not bool:
            raise ValueError("execution policy flags require exact bool values")
        return value

    @model_validator(mode="after")
    def _policy_is_closed_and_content_addressed(
        self,
    ) -> CorrectionAuditExecutionPolicyV2:
        if self.modalities != ("text", "audio"):
            raise ValueError("correction execution modalities are a closed ordered set")
        if self.text.modality != "text" or self.audio.modality != "audio":
            raise ValueError("execution limit modalities are swapped")
        expected = _hash_json(self.model_dump(mode="json", exclude={"content_hash"}))
        if self.content_hash != expected:
            raise ValueError("correction execution policy content_hash mismatch")
        return self


class CorrectionAuditPacketV2(_CorrectionV2Contract):
    """One bounded, modality-specific packet over exact AuditPlan cells."""

    schema_version: Literal[2] = 2
    id: Sha256
    packet_index: int = Field(ge=0)
    episode_id: str
    generation_id: str
    modality: CorrectionAuditModalityV2
    audit_plan_hash: Sha256
    audit_plan_content_hash: Sha256
    audit_input_hash: Sha256
    candidate_signal_set_hash: Sha256
    candidate_signal_set_content_hash: Sha256
    candidate_group_set_hash: Sha256
    candidate_group_set_content_hash: Sha256
    canonical_transcript_hash: Sha256
    canonical_content_hash: Sha256
    normalized_audio_hash: Sha256
    policy_hash: Sha256
    owned_cell_ids: tuple[Sha256, ...]
    requested_cell_ids: tuple[Sha256, ...]
    context_cell_ids: tuple[Sha256, ...]
    owned_target_ids: tuple[Sha256, ...]
    context_target_ids: tuple[Sha256, ...]
    owned_span_ids: tuple[str, ...]
    context_span_ids: tuple[str, ...]
    owned_token_ids: tuple[str, ...]
    context_token_ids: tuple[str, ...]
    window_start_ms: int = Field(ge=0)
    window_end_ms: int = Field(gt=0)
    clip_start_ms: int | None = Field(default=None, ge=0)
    clip_end_ms: int | None = Field(default=None, gt=0)
    clip_start_frame: int | None = Field(default=None, ge=0)
    clip_end_frame: int | None = Field(default=None, gt=0)
    speaker_labels: tuple[str, ...]
    recognition_seam_ids: tuple[str, ...]
    reference_evidence_ids: tuple[str, ...]
    split_before_reasons: tuple[CorrectionSplitReasonV2, ...]
    split_after_reasons: tuple[CorrectionSplitReasonV2, ...]
    source_bindings: tuple[CorrectionSourceBindingV2, ...]
    planner_id: Literal["nakama-correction-audit-execution-planner"]
    planner_version: Literal[2]
    planner_code_hash: Sha256
    execution_status: Literal["planned_not_executed"] = "planned_not_executed"
    content_hash: Sha256

    @field_validator("episode_id", "generation_id")
    @classmethod
    def _packet_identity_is_bounded(cls, value: str, info: object) -> str:
        if not value or value != value.strip() or len(value) > 256:
            raise ValueError(f"{getattr(info, 'field_name', 'value')} must be bounded")
        return value

    @model_validator(mode="after")
    def _packet_is_bounded_and_content_addressed(self) -> CorrectionAuditPacketV2:
        for label, values, allow_empty in (
            ("owned_cell_ids", self.owned_cell_ids, False),
            ("requested_cell_ids", self.requested_cell_ids, True),
            ("context_cell_ids", self.context_cell_ids, True),
            ("owned_target_ids", self.owned_target_ids, False),
            ("context_target_ids", self.context_target_ids, True),
            ("owned_span_ids", self.owned_span_ids, False),
            ("context_span_ids", self.context_span_ids, True),
            ("owned_token_ids", self.owned_token_ids, False),
            ("context_token_ids", self.context_token_ids, True),
            ("speaker_labels", self.speaker_labels, True),
            ("recognition_seam_ids", self.recognition_seam_ids, True),
            ("reference_evidence_ids", self.reference_evidence_ids, True),
        ):
            if not allow_empty and not values:
                raise ValueError(f"correction packet requires non-empty {label}")
            if len(set(values)) != len(values):
                raise ValueError(f"correction packet {label} must be unique")
        if not set(self.requested_cell_ids) <= set(self.owned_cell_ids):
            raise ValueError("requested cells must be owned by the same packet")
        for owned, context, label in (
            (self.owned_cell_ids, self.context_cell_ids, "cells"),
            (self.owned_target_ids, self.context_target_ids, "targets"),
            (self.owned_span_ids, self.context_span_ids, "spans"),
            (self.owned_token_ids, self.context_token_ids, "tokens"),
        ):
            if set(owned) & set(context):
                raise ValueError(f"packet cannot mark the same {label} owned and context")
        if self.window_end_ms <= self.window_start_ms:
            raise ValueError("correction packet window must have positive duration")
        clip_values = (
            self.clip_start_ms,
            self.clip_end_ms,
            self.clip_start_frame,
            self.clip_end_frame,
        )
        if self.modality == "text":
            if any(value is not None for value in clip_values):
                raise ValueError("text correction packet cannot claim an audio clip")
            if any(item.kind == "audio_clip" for item in self.source_bindings):
                raise ValueError("text correction packet cannot bind an audio clip")
        else:
            if any(value is None for value in clip_values):
                raise ValueError("audio correction packet requires exact clip bounds")
            assert self.clip_start_ms is not None
            assert self.clip_end_ms is not None
            assert self.clip_start_frame is not None
            assert self.clip_end_frame is not None
            if self.clip_end_ms <= self.clip_start_ms:
                raise ValueError("audio correction clip must have positive duration")
            if self.clip_end_frame <= self.clip_start_frame:
                raise ValueError("audio correction clip must have positive frame coverage")
            if self.clip_start_ms > self.window_start_ms or self.clip_end_ms < self.window_end_ms:
                raise ValueError("audio clip must contain the complete audit window")
            if sum(item.kind == "audio_clip" for item in self.source_bindings) != 1:
                raise ValueError("audio correction packet requires exactly one clip binding")
        for label, values in (
            ("speaker_labels", self.speaker_labels),
            ("recognition_seam_ids", self.recognition_seam_ids),
            ("reference_evidence_ids", self.reference_evidence_ids),
        ):
            if tuple(sorted(values)) != values:
                raise ValueError(f"correction packet {label} must be sorted")
        reason_order = {item: index for index, item in enumerate(_SPLIT_REASON_ORDER)}
        for label, values in (
            ("split_before_reasons", self.split_before_reasons),
            ("split_after_reasons", self.split_after_reasons),
        ):
            if not values or len(set(values)) != len(values):
                raise ValueError(f"correction packet {label} must be non-empty and unique")
            if tuple(sorted(values, key=reason_order.__getitem__)) != values:
                raise ValueError(f"correction packet {label} must use canonical order")
        source_order = {item: index for index, item in enumerate(_SOURCE_KIND_ORDER)}
        binding_keys = tuple((item.kind, item.id) for item in self.source_bindings)
        if not binding_keys or len(set(binding_keys)) != len(binding_keys):
            raise ValueError("correction packet source bindings must be non-empty and unique")
        if (
            tuple(sorted(binding_keys, key=lambda item: (source_order[item[0]], item[1])))
            != binding_keys
        ):
            raise ValueError("correction packet source bindings must use canonical order")
        required_kinds = {
            "audit_plan",
            "candidate_signal_set",
            "candidate_group_set",
            "canonical_transcript",
            "recognition_evidence_set",
        }
        if not required_kinds <= {item.kind for item in self.source_bindings}:
            raise ValueError("correction packet lacks a required source artifact binding")
        forbidden_full_artifacts = {
            "reference_retrieval_receipt_set",
            "normalized_audio",
        }
        if any(item.kind in forbidden_full_artifacts for item in self.source_bindings):
            raise ValueError(
                "correction packet cannot expose full retrieval or normalized-audio artifacts"
            )
        packet_source_kinds = {
            "audit_plan",
            "candidate_signal_set",
            "candidate_group_set",
            "canonical_transcript",
            "recognition_evidence_set",
            "recognition_seam_evidence",
            "reference_excerpt",
            "audio_clip",
        }
        if any(item.kind not in packet_source_kinds for item in self.source_bindings):
            raise ValueError("planned correction packet contains an execution-result artifact")
        if any(
            item.kind != "audio_clip"
            and not item.artifact_uri.startswith("execution-artifact://packet-inputs/")
            for item in self.source_bindings
        ):
            raise ValueError("correction packet JSON inputs must bind bounded packet slices")
        excerpt_ids = tuple(
            item.id for item in self.source_bindings if item.kind == "reference_excerpt"
        )
        if excerpt_ids != self.reference_evidence_ids:
            raise ValueError(
                "correction packet must bind exactly its visible-span reference excerpts"
            )
        payload = self.model_dump(mode="json", exclude={"id", "content_hash"})
        expected = _hash_json(payload)
        if self.id != expected or self.content_hash != expected:
            raise ValueError("correction packet identity/content_hash mismatch")
        return self


class CorrectionAuditExecutionPlanV2(_CorrectionV2Contract):
    """Exact replayable text/audio packet topology over one frozen AuditPlan."""

    schema_version: Literal[2] = 2
    id: Sha256
    episode_id: str
    generation_id: str
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
    required_cell_ids: tuple[Sha256, ...]
    source_bindings: tuple[CorrectionSourceBindingV2, ...]
    packets: tuple[CorrectionAuditPacketV2, ...]
    planner_id: Literal["nakama-correction-audit-execution-planner"]
    planner_version: Literal[2]
    planner_code_hash: Sha256
    execution_status: Literal["planned_not_executed"] = "planned_not_executed"
    completeness_statement: Literal["cell_execution_topology_only_not_semantic_recall_proof"] = (
        "cell_execution_topology_only_not_semantic_recall_proof"
    )
    content_hash: Sha256

    @model_validator(mode="after")
    def _plan_is_two_exact_partitions(self) -> CorrectionAuditExecutionPlanV2:
        if self.policy_hash != self.policy.content_hash:
            raise ValueError("execution plan policy hash mismatch")
        if (
            self.planner_id != self.policy.planner_id
            or self.planner_version != self.policy.planner_version
            or self.planner_code_hash != self.policy.planner_code_hash
        ):
            raise ValueError("execution plan planner identity differs from policy")
        if not self.all_cell_ids or len(set(self.all_cell_ids)) != len(self.all_cell_ids):
            raise ValueError("execution plan requires unique AuditPlan cell IDs")
        if len(set(self.required_cell_ids)) != len(self.required_cell_ids) or not set(
            self.required_cell_ids
        ) <= set(self.all_cell_ids):
            raise ValueError("execution plan required cells must be a unique cell subset")
        modalities = tuple(item.modality for item in self.packets)
        if not self.packets or modalities != tuple(sorted(modalities, key=("text", "audio").index)):
            raise ValueError("execution packets must be ordered text then audio")
        if len({item.id for item in self.packets}) != len(self.packets):
            raise ValueError("execution packet IDs must be unique")
        cell_order = {cell_id: index for index, cell_id in enumerate(self.all_cell_ids)}
        required_order = {cell_id: index for index, cell_id in enumerate(self.required_cell_ids)}
        for modality in ("text", "audio"):
            packets = tuple(item for item in self.packets if item.modality == modality)
            if not packets or tuple(item.packet_index for item in packets) != tuple(
                range(len(packets))
            ):
                raise ValueError(f"{modality} packets require complete ordered indices")
            owned = tuple(cell_id for packet in packets for cell_id in packet.owned_cell_ids)
            requested = tuple(
                cell_id for packet in packets for cell_id in packet.requested_cell_ids
            )
            if len(set(owned)) != len(owned) or set(owned) != set(self.all_cell_ids):
                raise ValueError(f"{modality} packets must partition every AuditPlan cell")
            if tuple(sorted(owned, key=cell_order.__getitem__)) != self.all_cell_ids:
                raise ValueError(f"{modality} packet cell ownership order drift")
            if len(set(requested)) != len(requested) or set(requested) != set(
                self.required_cell_ids
            ):
                raise ValueError(f"{modality} requested cells must partition required cells")
            if tuple(sorted(requested, key=required_order.__getitem__)) != (self.required_cell_ids):
                raise ValueError(f"{modality} requested cell order drift")
            if packets[0].split_before_reasons != ("episode_start",):
                raise ValueError(f"{modality} packet sequence lacks episode_start")
            if packets[-1].split_after_reasons != ("episode_end",):
                raise ValueError(f"{modality} packet sequence lacks episode_end")
            for left, right in zip(packets[:-1], packets[1:], strict=True):
                if left.split_after_reasons != right.split_before_reasons:
                    raise ValueError(f"{modality} packet split reasons do not meet exactly")
            limits = self.policy.text if modality == "text" else self.policy.audio
            for packet in packets:
                visible_cells = set(packet.owned_cell_ids) | set(packet.context_cell_ids)
                visible_spans = set(packet.owned_span_ids) | set(packet.context_span_ids)
                visible_tokens = set(packet.owned_token_ids) | set(packet.context_token_ids)
                if len(visible_cells) > limits.max_cells_per_packet:
                    raise ValueError(f"{modality} packet exceeds cell cap")
                if len(visible_spans) > limits.max_spans_per_packet:
                    raise ValueError(f"{modality} packet exceeds span cap")
                if len(visible_tokens) > limits.max_tokens_per_packet:
                    raise ValueError(f"{modality} packet exceeds token cap")
                if packet.window_end_ms - packet.window_start_ms > (limits.max_window_duration_ms):
                    raise ValueError(f"{modality} packet exceeds duration cap")
                if modality == "audio":
                    assert packet.clip_start_ms is not None
                    assert packet.clip_end_ms is not None
                    assert limits.max_clip_duration_ms is not None
                    if packet.clip_end_ms - packet.clip_start_ms > (limits.max_clip_duration_ms):
                        raise ValueError("audio packet exceeds clip-duration cap")
        for packet in self.packets:
            if (
                packet.episode_id != self.episode_id
                or packet.generation_id != self.generation_id
                or packet.audit_plan_hash != self.audit_plan_hash
                or packet.audit_plan_content_hash != self.audit_plan_content_hash
                or packet.audit_input_hash != self.audit_input_hash
                or packet.candidate_signal_set_hash != self.candidate_signal_set_hash
                or packet.candidate_signal_set_content_hash
                != self.candidate_signal_set_content_hash
                or packet.candidate_group_set_hash != self.candidate_group_set_hash
                or packet.candidate_group_set_content_hash != self.candidate_group_set_content_hash
                or packet.canonical_transcript_hash != self.canonical_transcript_hash
                or packet.canonical_content_hash != self.canonical_content_hash
                or packet.normalized_audio_hash != self.normalized_audio_hash
                or packet.policy_hash != self.policy_hash
                or packet.planner_code_hash != self.planner_code_hash
            ):
                raise ValueError("execution packet lineage differs from its plan")
            for label, values in (
                ("owned_cell_ids", packet.owned_cell_ids),
                ("context_cell_ids", packet.context_cell_ids),
            ):
                if any(value not in cell_order for value in values):
                    raise ValueError(f"packet {label} contains an unknown AuditPlan cell")
                if tuple(sorted(values, key=cell_order.__getitem__)) != values:
                    raise ValueError(f"packet {label} is reordered from AuditPlan")
        source_order = {item: index for index, item in enumerate(_SOURCE_KIND_ORDER)}
        binding_keys = tuple((item.kind, item.id) for item in self.source_bindings)
        if not binding_keys or len(set(binding_keys)) != len(binding_keys):
            raise ValueError("execution plan source bindings must be non-empty and unique")
        if (
            tuple(sorted(binding_keys, key=lambda item: (source_order[item[0]], item[1])))
            != binding_keys
        ):
            raise ValueError("execution plan source bindings must use canonical order")
        required_source_kinds = {
            "audit_plan",
            "candidate_signal_set",
            "candidate_group_set",
            "canonical_transcript",
            "recognition_evidence_set",
            "reference_retrieval_receipt_set",
            "normalized_audio",
        }
        if not required_source_kinds <= {item.kind for item in self.source_bindings}:
            raise ValueError("execution plan lacks a required source artifact binding")
        payload = self.model_dump(mode="json", exclude={"id", "content_hash"})
        expected = _hash_json(payload)
        if self.id != expected or self.content_hash != expected:
            raise ValueError("execution plan identity/content_hash mismatch")
        return self


__all__ = [
    "CorrectionArtifactMediaTypeV2",
    "CorrectionAuditPlanSliceV2",
    "CorrectionAuditExecutionLimitsV2",
    "CorrectionAuditExecutionPlanV2",
    "CorrectionAuditExecutionPolicyV2",
    "CorrectionAuditModalityV2",
    "CorrectionAuditPacketV2",
    "CorrectionCandidateGroupSliceV2",
    "CorrectionCandidateSignalSliceV2",
    "CorrectionCanonicalTranscriptSliceV2",
    "CorrectionRecognitionEvidenceItemSliceV2",
    "CorrectionRecognitionEvidenceSliceV2",
    "CorrectionRecognitionSeamSliceV2",
    "CorrectionPacketReferenceLocatorPartV2",
    "CorrectionPacketReferenceLocatorV2",
    "CorrectionReferenceExcerptSliceV2",
    "CorrectionSourceBindingV2",
    "CorrectionSourceKindV2",
    "CorrectionSplitReasonV2",
]
