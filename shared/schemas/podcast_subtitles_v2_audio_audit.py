"""Strict evidence contracts for Podcast Subtitle V2 Audio Full Audit.

The models bind every assessment to one exact PCM clip and one frozen
correction packet.  A finding is audio-backed evidence, but never a
Correction Decision, arbitration verdict, or authorization to mutate truth.
"""

from __future__ import annotations

import hashlib
import json
import re
import unicodedata
from typing import Annotated, Literal

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    JsonValue,
    StringConstraints,
    field_validator,
    model_validator,
)

from shared.schemas.podcast_subtitles_v2 import (
    AuditApplicabilityReason,
    BoundaryAuditCategory,
    SpanAuditCategory,
)
from shared.schemas.podcast_subtitles_v2_correction import (
    CorrectionAuditPacketV2,
    CorrectionSourceBindingV2,
)

Sha256 = Annotated[str, StringConstraints(pattern=r"^[0-9a-f]{64}$")]
AudioAuditExecutionModeV2 = Literal["fixture", "local", "subscription", "paid_api", "other"]
AudioAssessmentStatusV2 = Literal[
    "verified_clean_from_exact_audio",
    "finding",
    "unresolved_insufficient_evidence",
    "conflict",
]
AudioDispositionStatusV2 = Literal[
    "verified_clean_from_exact_audio",
    "finding",
    "unresolved_insufficient_evidence",
    "conflict",
    "inherited_not_applicable",
    "inherited_unavailable",
]
AudioFindingEvidenceBasisV2 = Literal["exact_audio_clip", "exact_audio_clip_and_reference"]
AudioAuditFailureCodeV2 = Literal[
    "runner_unavailable",
    "runner_outcome_ambiguous",
    "response_type_invalid",
    "response_integrity_failed",
    "workspace_integrity_failed",
]
AudioAuditJournalEventKindV2 = Literal[
    "intent_persisted",
    "attempt_started_at_most_once",
    "ambiguous_manual_resolution_required",
    "response_committed",
]
AudioAuditJournalStateV2 = Literal[
    "intent_ready_not_sent",
    "attempt_ambiguous_manual_resolution_required",
    "response_committed",
]

_ARTIFACT_RE = re.compile(
    r"^execution-artifact://audio-audit/"
    r"(?P<kind>requests|responses|failures|clips|journals)/"
    r"(?P<digest>[0-9a-f]{64})\.(?P<extension>json|wav)$"
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


def _bounded_text(value: str, label: str, maximum: int) -> str:
    if not value or value != value.strip() or len(value) > maximum:
        raise ValueError(f"{label} must be non-blank and bounded")
    if any(
        ord(character) == 127 or unicodedata.category(character) in {"Cc", "Cf", "Cs"}
        for character in value
    ):
        raise ValueError(f"{label} cannot contain control characters")
    return value


def _bounded_ids(values: tuple[str, ...], label: str, *, allow_empty: bool = True) -> None:
    if not allow_empty and not values:
        raise ValueError(f"{label} must be non-empty")
    if len(set(values)) != len(values):
        raise ValueError(f"{label} must be unique")
    if any(
        not item
        or item != item.strip()
        or len(item) > 1_024
        or any(
            ord(character) == 127 or unicodedata.category(character) in {"Cc", "Cf", "Cs"}
            for character in item
        )
        for item in values
    ):
        raise ValueError(f"{label} contains an invalid identifier")


class _AudioAuditContract(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)


class AudioAuditAdapterIdentityV2(_AudioAuditContract):
    schema_version: Literal[2] = 2
    adapter: str
    adapter_version: str
    model: str
    model_version: str
    runtime_hash: Sha256
    adapter_code_hash: Sha256
    config_hash: Sha256
    execution_mode: AudioAuditExecutionModeV2
    content_hash: Sha256

    @field_validator("adapter", "adapter_version", "model", "model_version")
    @classmethod
    def _identity_is_safe(cls, value: str, info: object) -> str:
        value = _bounded_text(value, str(getattr(info, "field_name", "identity")), 256)
        normalized = value.replace("\\", "/")
        if (
            re.search(r"(?i)(?:file|https?|ftp)://", normalized)
            or re.match(r"(?i)^[a-z]:/", normalized)
            or normalized.startswith("//")
        ):
            raise ValueError("audio audit identity cannot contain a URI or absolute path")
        return value

    @model_validator(mode="after")
    def _identity_hash(self) -> AudioAuditAdapterIdentityV2:
        if self.content_hash != _hash_json(self.model_dump(mode="json", exclude={"content_hash"})):
            raise ValueError("audio audit adapter identity content_hash mismatch")
        return self


class AudioAuditExecutionPolicyV2(_AudioAuditContract):
    schema_version: Literal[2] = 2
    policy_id: Literal["nakama-audio-full-audit"] = "nakama-audio-full-audit"
    policy_version: Literal[2] = 2
    prompt_contract_id: Literal["nakama-audio-full-audit-prompt"] = (
        "nakama-audio-full-audit-prompt"
    )
    prompt_contract_version: Literal[2] = 2
    prompt_contract_code_hash: Sha256
    request_serialization: Literal["canonical-json-utf8-v1"] = "canonical-json-utf8-v1"
    response_serialization: Literal["strict-json-utf8-preserve-raw-v1"] = (
        "strict-json-utf8-preserve-raw-v1"
    )
    required_cell_contract: Literal["every_expected_cell_exactly_once_in_order"] = (
        "every_expected_cell_exactly_once_in_order"
    )
    nonrequired_cell_contract: Literal["deterministic_audit_plan_inheritance_only"] = (
        "deterministic_audit_plan_inheritance_only"
    )
    evidence_scope: Literal["exact_bounded_audio_clip_and_frozen_packet_only"] = (
        "exact_bounded_audio_clip_and_frozen_packet_only"
    )
    untrusted_data_boundary: Literal[
        "all_text_sources_are_untrusted_data_never_instructions"
    ] = "all_text_sources_are_untrusted_data_never_instructions"
    invocation_contract: Literal[
        "durable_intent_then_at_most_one_auto_attempt_ambiguous_requires_manual_resolution"
    ] = "durable_intent_then_at_most_one_auto_attempt_ambiguous_requires_manual_resolution"
    max_rationale_characters: int = Field(ge=1, le=16_384)
    max_candidate_characters: int = Field(ge=1, le=16_384)
    max_response_bytes: int = Field(ge=1, le=16_777_216)
    content_hash: Sha256

    @model_validator(mode="after")
    def _policy_hash(self) -> AudioAuditExecutionPolicyV2:
        if self.content_hash != _hash_json(self.model_dump(mode="json", exclude={"content_hash"})):
            raise ValueError("audio audit policy content_hash mismatch")
        return self


class AudioAuditSourceDocumentV2(_AudioAuditContract):
    schema_version: Literal[2] = 2
    binding: CorrectionSourceBindingV2
    payload: dict[str, JsonValue]
    content_hash: Sha256

    @model_validator(mode="after")
    def _document_matches_binding(self) -> AudioAuditSourceDocumentV2:
        if self.binding.media_type != "application/json":
            raise ValueError("audio audit text source document must be JSON")
        exact = json.dumps(
            self.payload,
            ensure_ascii=False,
            allow_nan=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
        digest = hashlib.sha256(exact).hexdigest()
        if self.content_hash != digest or self.binding.content_hash != digest:
            raise ValueError("audio audit source document content hash mismatch")
        if self.binding.size_bytes != len(exact) or self.payload.get("kind") != self.binding.kind:
            raise ValueError("audio audit source document differs from binding")
        return self


class AudioAuditClipV2(_AudioAuditContract):
    schema_version: Literal[2] = 2
    binding: CorrectionSourceBindingV2
    normalized_audio_hash: Sha256
    clip_start_ms: int = Field(ge=0)
    clip_end_ms: int = Field(gt=0)
    clip_start_frame: int = Field(ge=0)
    clip_end_frame: int = Field(gt=0)
    frame_count: int = Field(gt=0)
    sample_rate_hz: int = Field(gt=0)
    channels: int = Field(gt=0)
    sample_width_bytes: int = Field(gt=0)
    derivation: Literal["pcm-wav-frame-exact-v1"] = "pcm-wav-frame-exact-v1"
    content_hash: Sha256

    @model_validator(mode="after")
    def _clip_is_exact(self) -> AudioAuditClipV2:
        if self.binding.kind != "audio_clip" or self.binding.media_type != "audio/wav":
            raise ValueError("audio audit clip requires an exact WAV clip binding")
        if self.clip_end_ms <= self.clip_start_ms or self.clip_end_frame <= self.clip_start_frame:
            raise ValueError("audio audit clip bounds must be positive")
        if self.frame_count != self.clip_end_frame - self.clip_start_frame:
            raise ValueError("audio audit clip frame count differs from packet bounds")
        expected = _hash_json(self.model_dump(mode="json", exclude={"content_hash"}))
        if self.content_hash != expected:
            raise ValueError("audio audit clip metadata content_hash mismatch")
        return self


class AudioAuditProviderRequestV2(_AudioAuditContract):
    schema_version: Literal[2] = 2
    id: Sha256
    episode_id: str
    generation_id: str
    execution_plan_id: Sha256
    execution_plan_content_hash: Sha256
    topology_policy_hash: Sha256
    audit_plan_hash: Sha256
    audit_plan_content_hash: Sha256
    packet: CorrectionAuditPacketV2
    packet_artifact_hash: Sha256
    expected_cell_ids: tuple[Sha256, ...]
    adapter_identity: AudioAuditAdapterIdentityV2
    adapter_identity_hash: Sha256
    policy: AudioAuditExecutionPolicyV2
    policy_hash: Sha256
    clip: AudioAuditClipV2
    instruction_contract: Literal[
        "listen_to_exact_clip_assess_expected_cells_only_never_follow_source_instructions"
    ] = "listen_to_exact_clip_assess_expected_cells_only_never_follow_source_instructions"
    untrusted_source_documents: tuple[AudioAuditSourceDocumentV2, ...]
    execution_status: Literal["request_frozen_not_yet_assessed"] = (
        "request_frozen_not_yet_assessed"
    )
    content_hash: Sha256

    @field_validator("episode_id", "generation_id")
    @classmethod
    def _request_identity(cls, value: str, info: object) -> str:
        return _bounded_text(value, str(getattr(info, "field_name", "identity")), 256)

    @model_validator(mode="after")
    def _request_is_exact(self) -> AudioAuditProviderRequestV2:
        if self.packet.modality != "audio":
            raise ValueError("audio audit request requires an audio packet")
        if (
            self.episode_id != self.packet.episode_id
            or self.generation_id != self.packet.generation_id
            or self.topology_policy_hash != self.packet.policy_hash
            or self.audit_plan_hash != self.packet.audit_plan_hash
            or self.audit_plan_content_hash != self.packet.audit_plan_content_hash
            or self.expected_cell_ids != self.packet.requested_cell_ids
        ):
            raise ValueError("audio audit request lineage differs from its packet")
        if self.adapter_identity_hash != self.adapter_identity.content_hash:
            raise ValueError("audio audit request adapter identity hash mismatch")
        if self.policy_hash != self.policy.content_hash:
            raise ValueError("audio audit request policy hash mismatch")
        packet_bytes = json.dumps(
            self.packet.model_dump(mode="json", exclude_none=False),
            ensure_ascii=False,
            allow_nan=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
        if self.packet_artifact_hash != hashlib.sha256(packet_bytes).hexdigest():
            raise ValueError("audio audit packet artifact hash mismatch")
        json_bindings = tuple(
            item for item in self.packet.source_bindings if item.media_type == "application/json"
        )
        if tuple(item.binding for item in self.untrusted_source_documents) != json_bindings:
            raise ValueError("audio audit source documents differ from packet bindings")
        clip_bindings = tuple(
            item for item in self.packet.source_bindings if item.kind == "audio_clip"
        )
        if clip_bindings != (self.clip.binding,):
            raise ValueError("audio audit request clip differs from packet binding")
        if (
            self.clip.normalized_audio_hash != self.packet.normalized_audio_hash
            or self.clip.clip_start_ms != self.packet.clip_start_ms
            or self.clip.clip_end_ms != self.packet.clip_end_ms
            or self.clip.clip_start_frame != self.packet.clip_start_frame
            or self.clip.clip_end_frame != self.packet.clip_end_frame
        ):
            raise ValueError("audio audit clip lineage differs from packet")
        expected = _hash_json(self.model_dump(mode="json", exclude={"id", "content_hash"}))
        if self.id != expected or self.content_hash != expected:
            raise ValueError("audio audit provider request identity/content_hash mismatch")
        return self


class AudioAuditProviderAssessmentV2(_AudioAuditContract):
    schema_version: Literal[2] = 2
    cell_id: Sha256
    target_id: Sha256
    category: SpanAuditCategory | BoundaryAuditCategory
    status: AudioAssessmentStatusV2
    evidence_scope: Literal["exact_bounded_audio_clip_and_frozen_packet_only"] = (
        "exact_bounded_audio_clip_and_frozen_packet_only"
    )
    rationale: str
    audited_start_ms: int = Field(ge=0)
    audited_end_ms: int = Field(gt=0)
    cited_clip_hash: Sha256
    cited_span_ids: tuple[str, ...]
    cited_token_ids: tuple[str, ...]
    cited_reference_evidence_ids: tuple[str, ...]
    cited_recognition_evidence_ids: tuple[str, ...]
    trigger_signal_ids: tuple[Sha256, ...]
    trigger_group_ids: tuple[Sha256, ...]
    affected_token_ids: tuple[str, ...] = ()
    observed_text: str | None = None
    candidate_text: str | None = None
    evidence_basis: AudioFindingEvidenceBasisV2 | None = None

    @field_validator("rationale")
    @classmethod
    def _rationale(cls, value: str) -> str:
        return _bounded_text(value, "audio audit rationale", 16_384)

    @field_validator("observed_text", "candidate_text")
    @classmethod
    def _finding_text(cls, value: str | None, info: object) -> str | None:
        if value is None:
            return value
        return _bounded_text(value, str(getattr(info, "field_name", "finding text")), 16_384)

    @model_validator(mode="after")
    def _assessment_shape(self) -> AudioAuditProviderAssessmentV2:
        for label, values in (
            ("cited_span_ids", self.cited_span_ids),
            ("cited_token_ids", self.cited_token_ids),
            ("cited_reference_evidence_ids", self.cited_reference_evidence_ids),
            ("cited_recognition_evidence_ids", self.cited_recognition_evidence_ids),
            ("trigger_signal_ids", self.trigger_signal_ids),
            ("trigger_group_ids", self.trigger_group_ids),
            ("affected_token_ids", self.affected_token_ids),
        ):
            _bounded_ids(values, label)
        if self.audited_end_ms <= self.audited_start_ms:
            raise ValueError("audio assessment audit interval must be positive")
        if (
            not self.cited_span_ids
            or not self.cited_token_ids
            or not self.cited_recognition_evidence_ids
        ):
            raise ValueError("every audio assessment requires span/token/Recognition citations")
        finding_fields = (self.observed_text, self.candidate_text, self.evidence_basis)
        if self.status == "finding":
            if any(value is None for value in finding_fields) or not self.affected_token_ids:
                raise ValueError("audio finding requires affected text and evidence basis")
            if self.observed_text == self.candidate_text:
                raise ValueError("audio finding candidate cannot be a no-op")
            if self.evidence_basis == "exact_audio_clip_and_reference":
                if not self.cited_reference_evidence_ids:
                    raise ValueError("reference-backed audio finding requires Reference Evidence")
            elif self.cited_reference_evidence_ids:
                raise ValueError("audio-only finding cannot cite Reference Evidence")
        elif any(value is not None for value in finding_fields) or self.affected_token_ids:
            raise ValueError("non-finding audio assessment cannot carry replacement fields")
        return self


class AudioAuditProviderResponseV2(_AudioAuditContract):
    schema_version: Literal[2] = 2
    request_id: Sha256
    request_content_hash: Sha256
    packet_id: Sha256
    adapter_identity_hash: Sha256
    model: str
    model_version: str
    policy_hash: Sha256
    evidence_scope: Literal["exact_bounded_audio_clip_and_frozen_packet_only"] = (
        "exact_bounded_audio_clip_and_frozen_packet_only"
    )
    assessments: tuple[AudioAuditProviderAssessmentV2, ...]

    @field_validator("model", "model_version")
    @classmethod
    def _response_model(cls, value: str, info: object) -> str:
        return _bounded_text(value, str(getattr(info, "field_name", "model")), 256)


class AudioAuditCellAssessmentV2(AudioAuditProviderAssessmentV2):
    id: Sha256
    packet_id: Sha256
    request_id: Sha256
    response_artifact_hash: Sha256
    content_hash: Sha256

    @model_validator(mode="after")
    def _assessment_hash(self) -> AudioAuditCellAssessmentV2:
        expected = _hash_json(self.model_dump(mode="json", exclude={"id", "content_hash"}))
        if self.id != expected or self.content_hash != expected:
            raise ValueError("audio cell assessment identity/content_hash mismatch")
        return self


class AudioAuditArtifactDigestV2(_AudioAuditContract):
    schema_version: Literal[2] = 2
    uri: str
    sha256: Sha256
    size_bytes: int = Field(ge=1)
    media_type: Literal["application/json", "audio/wav"]

    @model_validator(mode="after")
    def _artifact_is_portable(self) -> AudioAuditArtifactDigestV2:
        match = _ARTIFACT_RE.fullmatch(self.uri)
        if not match or match.group("digest") != self.sha256:
            raise ValueError("audio audit artifact URI is not portable/content-addressed")
        extension = match.group("extension")
        if (extension == "wav") != (self.media_type == "audio/wav"):
            raise ValueError("audio audit artifact extension/media type mismatch")
        if match.group("kind") == "clips" and self.media_type != "audio/wav":
            raise ValueError("audio audit clip artifact must be WAV")
        if match.group("kind") != "clips" and self.media_type != "application/json":
            raise ValueError("audio audit non-clip artifact must be JSON")
        return self


class AudioAuditProviderResponseReceiptV2(_AudioAuditContract):
    schema_version: Literal[2] = 2
    id: Sha256
    request_id: Sha256
    request_content_hash: Sha256
    request_artifact: AudioAuditArtifactDigestV2
    packet_id: Sha256
    adapter_identity_hash: Sha256
    policy_hash: Sha256
    clip_artifact: AudioAuditArtifactDigestV2
    raw_response: AudioAuditArtifactDigestV2
    assessment_ids: tuple[Sha256, ...]
    execution_status: Literal["parsed_complete"] = "parsed_complete"
    content_hash: Sha256

    @model_validator(mode="after")
    def _receipt_hash(self) -> AudioAuditProviderResponseReceiptV2:
        if (
            "/requests/" not in self.request_artifact.uri
            or "/clips/" not in self.clip_artifact.uri
            or "/responses/" not in self.raw_response.uri
            or len(set(self.assessment_ids)) != len(self.assessment_ids)
        ):
            raise ValueError("audio audit response receipt artifact kind or assessment drift")
        expected = _hash_json(self.model_dump(mode="json", exclude={"id", "content_hash"}))
        if self.id != expected or self.content_hash != expected:
            raise ValueError("audio audit response receipt identity/content_hash mismatch")
        return self


class AudioAuditProviderFailureReceiptV2(_AudioAuditContract):
    schema_version: Literal[2] = 2
    id: Sha256
    request_id: Sha256
    request_content_hash: Sha256
    request_artifact: AudioAuditArtifactDigestV2
    packet_id: Sha256
    adapter_identity_hash: Sha256
    policy_hash: Sha256
    clip_artifact: AudioAuditArtifactDigestV2
    failure_code: AudioAuditFailureCodeV2
    retryable: Literal[False] = False
    redacted_failure_artifact: AudioAuditArtifactDigestV2
    execution_status: Literal["failed_no_disposition"] = "failed_no_disposition"
    content_hash: Sha256

    @model_validator(mode="after")
    def _failure_hash(self) -> AudioAuditProviderFailureReceiptV2:
        if (
            "/requests/" not in self.request_artifact.uri
            or "/clips/" not in self.clip_artifact.uri
            or "/failures/" not in self.redacted_failure_artifact.uri
        ):
            raise ValueError("audio audit failure artifact kind drift")
        redacted_bytes = json.dumps(
            {
                "schema_version": 2,
                "request_id": self.request_id,
                "packet_id": self.packet_id,
                "failure_code": self.failure_code,
                "redaction": "code_only_no_provider_body_exception_secret_or_path",
            },
            ensure_ascii=False,
            allow_nan=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
        if (
            self.redacted_failure_artifact.sha256
            != hashlib.sha256(redacted_bytes).hexdigest()
            or self.redacted_failure_artifact.size_bytes != len(redacted_bytes)
        ):
            raise ValueError("audio audit redacted failure artifact is not reproducible")
        expected = _hash_json(self.model_dump(mode="json", exclude={"id", "content_hash"}))
        if self.id != expected or self.content_hash != expected:
            raise ValueError("audio audit failure receipt identity/content_hash mismatch")
        return self


class AudioCellDispositionV2(_AudioAuditContract):
    schema_version: Literal[2] = 2
    id: Sha256
    cell_id: Sha256
    target_id: Sha256
    category: SpanAuditCategory | BoundaryAuditCategory
    status: AudioDispositionStatusV2
    source: Literal["provider_audio_assessment", "inherited_audit_plan_applicability"]
    applicability_reason: AuditApplicabilityReason
    assessment_id: Sha256 | None = None
    packet_id: Sha256 | None = None
    evidence_scope: Literal["exact_bounded_audio_clip_and_frozen_packet_only"] = (
        "exact_bounded_audio_clip_and_frozen_packet_only"
    )
    content_hash: Sha256

    @model_validator(mode="after")
    def _disposition_hash(self) -> AudioCellDispositionV2:
        inherited = self.status in {"inherited_not_applicable", "inherited_unavailable"}
        if inherited:
            if self.source != "inherited_audit_plan_applicability" or any(
                item is not None for item in (self.assessment_id, self.packet_id)
            ):
                raise ValueError("inherited audio disposition has provider authority")
        elif (
            self.source != "provider_audio_assessment"
            or self.assessment_id is None
            or self.packet_id is None
        ):
            raise ValueError("required audio disposition lacks provider assessment")
        expected = _hash_json(self.model_dump(mode="json", exclude={"id", "content_hash"}))
        if self.id != expected or self.content_hash != expected:
            raise ValueError("audio cell disposition identity/content_hash mismatch")
        return self


class AudioDispositionSetV2(_AudioAuditContract):
    schema_version: Literal[2] = 2
    id: Sha256
    execution_plan_id: Sha256
    audit_plan_hash: Sha256
    audit_plan_content_hash: Sha256
    policy_hash: Sha256
    adapter_identity_hash: Sha256
    response_receipt_ids: tuple[Sha256, ...]
    all_cell_ids: tuple[Sha256, ...]
    dispositions: tuple[AudioCellDispositionV2, ...]
    coverage_statement: Literal[
        "all_audit_plan_cells_audio_dispositioned_required_cells_exact_clip_assessed"
    ] = "all_audit_plan_cells_audio_dispositioned_required_cells_exact_clip_assessed"
    authority: Literal["not_correction_decisions_or_release_approval"] = (
        "not_correction_decisions_or_release_approval"
    )
    content_hash: Sha256

    @model_validator(mode="after")
    def _set_hash(self) -> AudioDispositionSetV2:
        if (
            not self.all_cell_ids
            or len(set(self.all_cell_ids)) != len(self.all_cell_ids)
            or tuple(item.cell_id for item in self.dispositions) != self.all_cell_ids
            or len(set(self.response_receipt_ids)) != len(self.response_receipt_ids)
        ):
            raise ValueError("audio dispositions do not exactly cover AuditPlan cell order")
        expected = _hash_json(self.model_dump(mode="json", exclude={"id", "content_hash"}))
        if self.id != expected or self.content_hash != expected:
            raise ValueError("audio disposition set identity/content_hash mismatch")
        return self


class AudioDiscoveredCandidateV2(_AudioAuditContract):
    schema_version: Literal[2] = 2
    id: Sha256
    assessment_id: Sha256
    cell_id: Sha256
    target_id: Sha256
    packet_id: Sha256
    clip_hash: Sha256
    affected_token_ids: tuple[str, ...]
    observed_text: str
    candidate_text: str
    cited_span_ids: tuple[str, ...]
    cited_recognition_evidence_ids: tuple[str, ...]
    cited_reference_evidence_ids: tuple[str, ...]
    trigger_signal_ids: tuple[Sha256, ...]
    trigger_group_ids: tuple[Sha256, ...]
    evidence_basis: AudioFindingEvidenceBasisV2
    authority: Literal[
        "audio_audit_discovery_not_correction_decision_or_arbitration"
    ] = "audio_audit_discovery_not_correction_decision_or_arbitration"
    requires_audio_arbitration: Literal[True] = True
    is_audio_evidence: Literal[True] = True
    content_hash: Sha256

    @model_validator(mode="after")
    def _candidate_hash(self) -> AudioDiscoveredCandidateV2:
        for label, values in (
            ("affected_token_ids", self.affected_token_ids),
            ("cited_span_ids", self.cited_span_ids),
            ("cited_recognition_evidence_ids", self.cited_recognition_evidence_ids),
            ("cited_reference_evidence_ids", self.cited_reference_evidence_ids),
            ("trigger_signal_ids", self.trigger_signal_ids),
            ("trigger_group_ids", self.trigger_group_ids),
        ):
            _bounded_ids(values, label)
        _bounded_text(self.observed_text, "observed_text", 16_384)
        _bounded_text(self.candidate_text, "candidate_text", 16_384)
        if not self.affected_token_ids or not self.cited_recognition_evidence_ids:
            raise ValueError("audio discovery requires affected tokens and Recognition Evidence")
        if self.observed_text == self.candidate_text:
            raise ValueError("audio discovery cannot be a no-op")
        if self.evidence_basis == "exact_audio_clip_and_reference":
            if not self.cited_reference_evidence_ids:
                raise ValueError("reference-backed audio discovery requires Reference Evidence")
        elif self.cited_reference_evidence_ids:
            raise ValueError("audio-only discovery cannot cite Reference Evidence")
        expected = _hash_json(self.model_dump(mode="json", exclude={"id", "content_hash"}))
        if self.id != expected or self.content_hash != expected:
            raise ValueError("audio discovered candidate identity/content_hash mismatch")
        return self


class AudioCandidateDiscoverySetV2(_AudioAuditContract):
    schema_version: Literal[2] = 2
    id: Sha256
    execution_plan_id: Sha256
    audio_disposition_set_id: Sha256
    adapter_identity_hash: Sha256
    policy_hash: Sha256
    candidates: tuple[AudioDiscoveredCandidateV2, ...]
    authority: Literal["not_correction_decisions_or_arbitration_verdicts"] = (
        "not_correction_decisions_or_arbitration_verdicts"
    )
    content_hash: Sha256

    @model_validator(mode="after")
    def _discovery_hash(self) -> AudioCandidateDiscoverySetV2:
        if tuple(sorted(self.candidates, key=lambda item: item.id)) != self.candidates or len(
            {item.id for item in self.candidates}
        ) != len(self.candidates):
            raise ValueError("audio discoveries must use unique canonical content-ID order")
        expected = _hash_json(self.model_dump(mode="json", exclude={"id", "content_hash"}))
        if self.id != expected or self.content_hash != expected:
            raise ValueError("audio discovery set identity/content_hash mismatch")
        return self


class AudioAuditInvocationJournalEventV2(_AudioAuditContract):
    schema_version: Literal[2] = 2
    id: Sha256
    event_index: int = Field(ge=0)
    request_id: Sha256
    packet_id: Sha256
    request_artifact: AudioAuditArtifactDigestV2
    clip_artifact: AudioAuditArtifactDigestV2
    event: AudioAuditJournalEventKindV2
    previous_event_hash: Sha256 | None = None
    response_artifact: AudioAuditArtifactDigestV2 | None = None
    redaction: Literal["no_provider_body_exception_secret_or_path"] = (
        "no_provider_body_exception_secret_or_path"
    )
    content_hash: Sha256

    @model_validator(mode="after")
    def _event_hash(self) -> AudioAuditInvocationJournalEventV2:
        if "/requests/" not in self.request_artifact.uri or "/clips/" not in self.clip_artifact.uri:
            raise ValueError("audio invocation journal source artifact kind drift")
        if self.event == "response_committed":
            if self.response_artifact is None or "/responses/" not in self.response_artifact.uri:
                raise ValueError("response-committed journal event lacks response artifact")
        elif self.response_artifact is not None:
            raise ValueError("non-commit journal event cannot claim response bytes")
        expected = _hash_json(self.model_dump(mode="json", exclude={"id", "content_hash"}))
        if self.id != expected or self.content_hash != expected:
            raise ValueError("audio invocation journal event identity/content_hash mismatch")
        return self


class AudioAuditInvocationJournalV2(_AudioAuditContract):
    schema_version: Literal[2] = 2
    id: Sha256
    request_id: Sha256
    packet_id: Sha256
    events: tuple[AudioAuditInvocationJournalEventV2, ...]
    state: AudioAuditJournalStateV2
    auto_attempt_limit: Literal[1] = 1
    content_hash: Sha256

    @model_validator(mode="after")
    def _journal_is_append_only_chain(self) -> AudioAuditInvocationJournalV2:
        if not self.events or self.events[0].event != "intent_persisted":
            raise ValueError("audio invocation journal must begin with durable intent")
        if tuple(item.event_index for item in self.events) != tuple(range(len(self.events))):
            raise ValueError("audio invocation journal event indices drift")
        if any(
            item.request_id != self.request_id or item.packet_id != self.packet_id
            for item in self.events
        ):
            raise ValueError("audio invocation journal event lineage drift")
        for index, item in enumerate(self.events):
            expected_previous = None if index == 0 else self.events[index - 1].id
            if item.previous_event_hash != expected_previous:
                raise ValueError("audio invocation journal hash chain drift")
        kinds = tuple(item.event for item in self.events)
        allowed = {
            ("intent_persisted",),
            ("intent_persisted", "attempt_started_at_most_once"),
            (
                "intent_persisted",
                "attempt_started_at_most_once",
                "ambiguous_manual_resolution_required",
            ),
            ("intent_persisted", "attempt_started_at_most_once", "response_committed"),
            ("intent_persisted", "response_committed"),
            (
                "intent_persisted",
                "attempt_started_at_most_once",
                "ambiguous_manual_resolution_required",
                "response_committed",
            ),
        }
        if kinds not in allowed:
            raise ValueError("audio invocation journal transition is invalid")
        expected_state: AudioAuditJournalStateV2
        if kinds[-1] == "response_committed":
            expected_state = "response_committed"
        elif "attempt_started_at_most_once" in kinds:
            expected_state = "attempt_ambiguous_manual_resolution_required"
        else:
            expected_state = "intent_ready_not_sent"
        if self.state != expected_state:
            raise ValueError("audio invocation journal state differs from events")
        expected = _hash_json(self.model_dump(mode="json", exclude={"id", "content_hash"}))
        if self.id != expected or self.content_hash != expected:
            raise ValueError("audio invocation journal identity/content_hash mismatch")
        return self


class AudioAuditExecutionRecordV2(_AudioAuditContract):
    schema_version: Literal[2] = 2
    id: Sha256
    execution_plan_id: Sha256
    execution_plan_content_hash: Sha256
    audit_plan_hash: Sha256
    audit_plan_content_hash: Sha256
    policy_hash: Sha256
    adapter_identity_hash: Sha256
    request_ids: tuple[Sha256, ...]
    invocation_journals: tuple[AudioAuditInvocationJournalV2, ...]
    response_receipts: tuple[AudioAuditProviderResponseReceiptV2, ...]
    assessments: tuple[AudioAuditCellAssessmentV2, ...]
    audio_disposition_set: AudioDispositionSetV2
    candidate_discovery_set: AudioCandidateDiscoverySetV2
    execution_status: Literal["complete_audio_full_audit"] = "complete_audio_full_audit"
    quality_statement: Literal[
        "exact_clip_cell_execution_complete_not_correction_decision_or_release_approval"
    ] = "exact_clip_cell_execution_complete_not_correction_decision_or_release_approval"
    content_hash: Sha256

    @model_validator(mode="after")
    def _record_is_exact(self) -> AudioAuditExecutionRecordV2:
        if (
            tuple(item.request_id for item in self.invocation_journals) != self.request_ids
            or tuple(item.request_id for item in self.response_receipts) != self.request_ids
            or any(item.state != "response_committed" for item in self.invocation_journals)
        ):
            raise ValueError("audio audit record request/journal/receipt coverage differs")
        assessment_ids = tuple(item.id for item in self.assessments)
        if assessment_ids != tuple(
            item_id for receipt in self.response_receipts for item_id in receipt.assessment_ids
        ):
            raise ValueError("audio audit record assessments differ from receipts")
        assessment_by_id = {item.id: item for item in self.assessments}
        if len(assessment_by_id) != len(self.assessments):
            raise ValueError("audio audit record repeats an assessment")
        journal_by_request = {item.request_id: item for item in self.invocation_journals}
        for receipt in self.response_receipts:
            if (
                receipt.policy_hash != self.policy_hash
                or receipt.adapter_identity_hash != self.adapter_identity_hash
            ):
                raise ValueError("audio audit response receipt policy/identity drift")
            journal = journal_by_request[receipt.request_id]
            terminal = journal.events[-1]
            if (
                journal.packet_id != receipt.packet_id
                or terminal.response_artifact != receipt.raw_response
                or terminal.request_artifact != receipt.request_artifact
                or terminal.clip_artifact != receipt.clip_artifact
            ):
                raise ValueError("audio audit journal differs from response receipt")
            for assessment_id in receipt.assessment_ids:
                assessment = assessment_by_id[assessment_id]
                if (
                    assessment.request_id != receipt.request_id
                    or assessment.packet_id != receipt.packet_id
                    or assessment.response_artifact_hash != receipt.raw_response.sha256
                    or assessment.cited_clip_hash != receipt.clip_artifact.sha256
                ):
                    raise ValueError("audio assessment differs from exact response/clip receipt")
        provider_dispositions = tuple(
            item
            for item in self.audio_disposition_set.dispositions
            if item.source == "provider_audio_assessment"
        )
        if {item.assessment_id for item in provider_dispositions} != set(assessment_by_id):
            raise ValueError("audio provider dispositions differ from exact assessments")
        for disposition in provider_dispositions:
            assert disposition.assessment_id is not None
            assessment = assessment_by_id[disposition.assessment_id]
            if (
                disposition.cell_id != assessment.cell_id
                or disposition.target_id != assessment.target_id
                or disposition.category != assessment.category
                or disposition.status != assessment.status
                or disposition.packet_id != assessment.packet_id
            ):
                raise ValueError("audio disposition differs from its assessment")
        if (
            self.audio_disposition_set.execution_plan_id != self.execution_plan_id
            or self.audio_disposition_set.audit_plan_hash != self.audit_plan_hash
            or self.audio_disposition_set.audit_plan_content_hash
            != self.audit_plan_content_hash
            or self.audio_disposition_set.policy_hash != self.policy_hash
            or self.audio_disposition_set.adapter_identity_hash != self.adapter_identity_hash
            or self.audio_disposition_set.response_receipt_ids
            != tuple(item.id for item in self.response_receipts)
        ):
            raise ValueError("audio disposition set lineage drift")
        if (
            self.candidate_discovery_set.execution_plan_id != self.execution_plan_id
            or self.candidate_discovery_set.audio_disposition_set_id
            != self.audio_disposition_set.id
            or self.candidate_discovery_set.policy_hash != self.policy_hash
            or self.candidate_discovery_set.adapter_identity_hash != self.adapter_identity_hash
        ):
            raise ValueError("audio discovery set lineage drift")
        findings = tuple(item for item in self.assessments if item.status == "finding")
        by_assessment = {
            item.assessment_id: item for item in self.candidate_discovery_set.candidates
        }
        if len(by_assessment) != len(self.candidate_discovery_set.candidates) or set(
            by_assessment
        ) != {item.id for item in findings}:
            raise ValueError("audio findings and discoveries are not one-to-one")
        for assessment in findings:
            candidate = by_assessment[assessment.id]
            if (
                candidate.cell_id != assessment.cell_id
                or candidate.target_id != assessment.target_id
                or candidate.packet_id != assessment.packet_id
                or candidate.clip_hash != assessment.cited_clip_hash
                or candidate.affected_token_ids != assessment.affected_token_ids
                or candidate.observed_text != assessment.observed_text
                or candidate.candidate_text != assessment.candidate_text
                or candidate.cited_span_ids != assessment.cited_span_ids
                or candidate.cited_recognition_evidence_ids
                != assessment.cited_recognition_evidence_ids
                or candidate.cited_reference_evidence_ids
                != assessment.cited_reference_evidence_ids
                or candidate.trigger_signal_ids != assessment.trigger_signal_ids
                or candidate.trigger_group_ids != assessment.trigger_group_ids
                or candidate.evidence_basis != assessment.evidence_basis
            ):
                raise ValueError("audio discovery differs from its finding")
        expected = _hash_json(self.model_dump(mode="json", exclude={"id", "content_hash"}))
        if self.id != expected or self.content_hash != expected:
            raise ValueError("audio audit execution record identity/content_hash mismatch")
        return self


__all__ = [name for name in globals() if name.startswith("Audio") or name == "Sha256"]
