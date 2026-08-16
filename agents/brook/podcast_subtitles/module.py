"""Deep domain Module for evidence-backed Podcast Subtitle V2 generations.

Only :meth:`PodcastSubtitleV2.create`, :meth:`resolve`, and :meth:`project`
are mutation-capable domain operations.  Every artifact is reloaded through
the content-addressed store and every projection is independently gated.
"""

from __future__ import annotations

import inspect
import json
import os
import re
import shutil
import sys
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Literal, Mapping, Sequence
from urllib.parse import unquote, urlparse
from urllib.request import url2pathname

from shared.schemas.podcast_subtitles_v2 import (
    ArbitrationReceipt,
    ArtifactDigest,
    AudioAuditReceipt,
    AuditPlan,
    BoundaryConstraintReceipt,
    CandidateGroupSet,
    CandidateSignalSet,
    CanonicalTranscript,
    CorrectionAuditPolicySnapshot,
    CorrectionAuditReceipt,
    CorrectionDecision,
    GenerationManifest,
    NormalizationReceipt,
    ProjectionManifest,
    ProjectionProfile,
    ProtectedTokenRange,
    QualityReport,
    RecognitionEvidence,
    ReferenceArtifact,
    ReferenceEvidence,
    ReferenceExtractionSnapshot,
    ReferenceRetrievalPolicySnapshot,
    ReferenceRetrievalReceipt,
    ReviewIssue,
    SemanticUnit,
    SpeechCoverageReceipt,
    SubtitleProjection,
    audio_audit_receipt_set_hash,
    normalization_receipt_content_hash,
    recognition_evidence_content_hash,
    recognition_evidence_set_hash,
    reference_evidence_set_hash,
    speech_coverage_receipt_content_hash,
    token_sequence_hash,
)
from shared.schemas.podcast_subtitles_v2_audio_audit import (
    AudioAuditExecutionRecordV2,
    AudioDiscoveredCandidateV2,
)
from shared.schemas.podcast_subtitles_v2_correction import (
    CorrectionAuditExecutionPlanV2,
    CorrectionAuditExecutionPolicyV2,
)
from shared.schemas.podcast_subtitles_v2_text_audit import (
    TextAuditExecutionRecordV2,
    TextDiscoveredCandidateV2,
)

from .adapters.reference import (
    TrustedReferenceParserRegistry,
    verify_reference_evidence_membership,
    verify_reference_extraction_derivation,
)
from .audio_audit_execution import (
    AudioAuditInvocationAmbiguous,
    AudioAuditRunResult,
    AudioAuditWorkPending,
    AudioFullAuditExecutor,
)
from .audit_plan import build_audit_plan, default_correction_audit_policy
from .boundary_constraints import (
    BoundaryConstraintError,
    ProtectedTermMetadata,
    assert_boundary_constraint_receipt,
    build_boundary_constraint_receipt,
    classify_protected_kind,
    derive_protected_token_ranges,
    validate_semantic_non_degeneracy,
)
from .candidate_generation import derive_candidate_group_set, derive_candidate_signal_set
from .canonical import (
    CanonicalBuildResult,
    add_correction_proposals,
    add_review_risks,
    attest_full_audit,
    attest_speech_coverage,
    canonical_generation_id,
    derive_canonical_resolution,
    derive_native_canonical_resolution,
    prepare_canonical_resolution,
    reconcile_canonical,
    replay_correction_ledger,
    review_target_fingerprint,
    without_native_resolution_risk,
)
from .checkpoint import (
    CheckpointAdapterIdentity,
    CreateCheckpoint,
    build_create_checkpoint,
)
from .correction_acceptance import (
    CorrectionAcceptanceVerdictV2,
    verify_correction_acceptance_policy,
    verify_correction_acceptance_verdict,
    verify_human_audio_review_receipt,
    verify_human_reference_adjudication_receipt,
)
from .correction_execution import (
    build_correction_audit_execution_plan,
    default_correction_audit_execution_policy,
    materialize_audio_correction_packet_sources,
    materialize_text_correction_packet_sources,
)
from .editorial import editorial_detector_identity
from .errors import (
    ArtifactHashMismatchError,
    GenerationIsolationError,
    GenerationNotFoundError,
    NativeAcceptanceRequiredError,
    ResolutionTransactionError,
    SubtitleV2Error,
)
from .full_audit_attestation import (
    FullAuditAggregateAttestationV2,
    build_full_audit_aggregate,
    verify_full_audit_aggregate,
)
from .hashing import canonical_json_bytes, hash_file, hash_object, sha256_bytes
from .ledger import GENESIS_HASH, CorrectionLedger, LedgerEntry
from .ledger_decision_codec import (
    LedgerDecisionCodecError,
    decode_ledger_entry,
)
from .loaded_generation import (
    LoadedAdapterIdentity,
    LoadedCommonRuntimeState,
    LoadedGenerationState,
    LoadedLedgerEntrySnapshot,
    LoadedLegacyAuditState,
    LoadedLegacyCollectionArtifacts,
    LoadedLegacyRuntimeState,
    LoadedNativeAudioRunArtifacts,
    LoadedNativeAuditArtifacts,
    LoadedNativeAuditState,
    LoadedNativeResolutionArtifacts,
    LoadedNativeResolutionState,
    LoadedNativeRuntimeState,
    LoadedNativeSourceSet,
    LoadedNativeTextRunArtifacts,
    LoadedNormalizationState,
    LoadedRecognitionState,
    LoadedReferenceState,
    LoadedSpeechCoverageAdapterIdentity,
    LoadedSpeechCoverageState,
    LoadedStorageSnapshot,
    LoadedTargetedCorrectionAuditReceipt,
    VerifiedArtifactBytes,
    VerifiedArtifactSet,
    VerifiedNativeSource,
)
from .memo_boundary import MemoBoundaryAuthorityV1, MemoSrtBoundaryAuthorityV1
from .native_resolution import (
    NativeCorrectionDecisionV2,
    NativeResolveCheckpointV2,
    OriginalConfirmationAuthorizationV2,
    ResolveNativeRequest,
    build_native_correction_decision,
    build_native_resolve_checkpoint,
    native_correction_decision_bytes,
    verify_human_original_confirmation_receipt,
    verify_native_correction_decision,
    verify_native_reference_authority_proof,
    verify_original_confirmation_authorization,
    verify_original_confirmation_policy,
)
from .orthographic_projection import (
    OrthographicProjectionError,
    OrthographicProjectionEvidenceV1,
    measure_opencc_s2tw_identity,
    orthographic_projection_evidence_set_hash,
    orthographic_projection_raw_output_bytes,
    project_recognition_evidence_set,
    verify_orthographic_projection,
)
from .ports import (
    AdapterIntegrityError,
    AdapterWorkPending,
    Arbiter,
    ArbitrationRequest,
    ArbitrationVerdict,
    AudioAuditor,
    AudioAuditRequest,
    AudioModelIdentity,
    CorrectionExecutionReceipt,
    CorrectionModelIdentity,
    CorrectionProposal,
    CorrectionRequest,
    Corrector,
    Normalizer,
    NormalizeRequest,
    RecognitionModelIdentity,
    RecognitionRequest,
    Recognizer,
    ReferenceRetrievalRequest,
    ReferenceRetriever,
    SemanticAnalysisRequest,
    SemanticAnalyzer,
    SemanticExecutionReceipt,
    SemanticModelIdentity,
    SemanticRunResult,
    SpeakerAttributionProvenance,
    SpeakerAttributionRequest,
    SpeakerAttributor,
    SpeakerTrackBinding,
    SpeakerTrackInput,
    SpeechCoverageAnalyzer,
    SpeechCoverageRequest,
)
from .profiles import DEFAULT_SUBTITLE_POLICY, SubtitlePolicy
from .quality import assert_projection_quality
from .recognition_policy import (
    RecognitionIndependencePolicyV1,
    RecognitionIndependenceReceiptV1,
)
from .recognition_request import (
    RecognitionRequestArtifactV1,
    build_recognition_request_artifact,
    materialize_recognition_request,
)
from .reference_context import (
    ReferenceContextError,
    build_reference_query_contexts,
    verify_reference_query_contexts,
)
from .risk import RiskRecord
from .semantic_projection import (
    project_semantic_units,
    render_sidecar_json,
    render_srt,
)
from .speech_coverage import speech_coverage_risks, verified_speech_coverage_hash
from .store import (
    GenerationStore,
    StoredCreateCheckpoint,
    StoredGeneration,
    StoredNativeResolveCheckpoint,
)
from .text_audit_execution import (
    TextAuditRunResult,
    TextAuditWorkPending,
    TextFullAuditExecutor,
)


class ModuleInvariantError(SubtitleV2Error, ValueError):
    """A caller or Adapter attempted to cross a domain invariant."""


def _strict_json_object(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"duplicate JSON key is forbidden: {key!r}")
        result[key] = value
    return result


def _reject_json_constant(value: str) -> None:
    raise ValueError(f"non-finite JSON number is forbidden: {value}")


def _canonical_json_value(payload: bytes, *, label: str) -> object:
    try:
        value = json.loads(
            payload.decode("utf-8"),
            object_pairs_hook=_strict_json_object,
            parse_constant=_reject_json_constant,
        )
    except (UnicodeDecodeError, json.JSONDecodeError, ValueError) as exc:
        raise GenerationIsolationError(f"{label} is malformed JSON") from exc
    if canonical_json_bytes(value) != payload:
        raise GenerationIsolationError(f"{label} is not canonical JSON")
    return value


def _correction_packet_reference_binding(
    request_bytes: bytes,
) -> tuple[tuple[str, ...], str]:
    """Read the exact Reference Evidence claim from immutable Corrector bytes.

    This check is intentionally Module-owned and independent of Adapter replay:
    a buggy or hostile Adapter cannot make a receipt truthful merely by
    reproducing the same hidden reference filtering during replay.
    """

    if not isinstance(request_bytes, bytes):
        raise GenerationIsolationError("Corrector request artifact is not immutable bytes")
    try:
        payload = json.loads(
            request_bytes.decode("utf-8"),
            object_pairs_hook=_strict_json_object,
            parse_constant=_reject_json_constant,
        )
    except (UnicodeDecodeError, json.JSONDecodeError, ValueError) as exc:
        raise GenerationIsolationError("Corrector request artifact is malformed JSON") from exc
    if not isinstance(payload, dict) or canonical_json_bytes(payload) != request_bytes:
        raise GenerationIsolationError(
            "Corrector request artifact must be one canonical JSON object"
        )
    raw_ids = payload.get("presented_reference_evidence_ids")
    raw_hash = payload.get("presented_reference_evidence_hash")
    raw_references = payload.get("reference_evidence")
    if (
        not isinstance(raw_ids, list)
        or any(not isinstance(item, str) or not item.strip() for item in raw_ids)
        or len(set(raw_ids)) != len(raw_ids)
        or not isinstance(raw_hash, str)
        or not re.fullmatch(r"[0-9a-f]{64}", raw_hash)
        or not isinstance(raw_references, list)
    ):
        raise GenerationIsolationError(
            "Corrector request has an invalid presented Reference Evidence binding"
        )
    object_ids: list[str] = []
    for item in raw_references:
        if not isinstance(item, dict):
            raise GenerationIsolationError(
                "Corrector request Reference Evidence payload must contain objects"
            )
        reference_id = item.get("id")
        if not isinstance(reference_id, str) or not reference_id.strip():
            raise GenerationIsolationError(
                "Corrector request Reference Evidence object lacks an ID"
            )
        object_ids.append(reference_id)
    declared_ids = tuple(raw_ids)
    if tuple(object_ids) != declared_ids:
        raise GenerationIsolationError(
            "Corrector request Reference Evidence objects do not exactly match its "
            "declared presented IDs"
        )
    return declared_ids, raw_hash


@dataclass(frozen=True, slots=True)
class AdapterIdentity:
    """Explicit identity for an Adapter whose output lacks its own receipt."""

    name: str
    version: str
    config_hash: str
    execution_mode: Literal["fixture", "local", "paid_api", "subscription", "other"]

    def __post_init__(self) -> None:
        if not self.name.strip() or not self.version.strip():
            raise ValueError("Adapter identity requires non-blank name and version")
        if not re.fullmatch(r"[0-9a-f]{64}", self.config_hash):
            raise ValueError("Adapter identity config_hash must be lowercase SHA-256")
        if self.execution_mode not in {
            "fixture",
            "local",
            "paid_api",
            "subscription",
            "other",
        }:
            raise ValueError("Adapter identity execution_mode is unsupported")

    @property
    def content_hash(self) -> str:
        return hash_object(self)


@dataclass(frozen=True, slots=True)
class NativeFullAuditBundle:
    """Explicit composition for the V2 native full-audit evidence path.

    The bundle is intentionally mutually exclusive with the legacy Corrector /
    AudioAuditor pair.  Its executors can only disposition audit cells and
    discover candidates; they cannot mutate Canonical truth or approve release.
    """

    text_executor: TextFullAuditExecutor
    audio_executor: AudioFullAuditExecutor
    audit_policy: CorrectionAuditPolicySnapshot = field(
        default_factory=default_correction_audit_policy
    )
    execution_policy: CorrectionAuditExecutionPolicyV2 = field(
        default_factory=default_correction_audit_execution_policy
    )

    def __post_init__(self) -> None:
        if not isinstance(self.text_executor, TextFullAuditExecutor):
            raise TypeError("native full audit requires TextFullAuditExecutor")
        if not isinstance(self.audio_executor, AudioFullAuditExecutor):
            raise TypeError("native full audit requires AudioFullAuditExecutor")
        if not isinstance(self.audit_policy, CorrectionAuditPolicySnapshot):
            raise TypeError("native full audit requires a typed audit policy")
        if not isinstance(self.execution_policy, CorrectionAuditExecutionPolicyV2):
            raise TypeError("native full audit requires a typed execution policy")


@dataclass(frozen=True, slots=True)
class ReferenceEnrollment:
    """Caller-owned trust root for one immutable external source extraction."""

    artifact: ReferenceArtifact
    source_snapshot: bytes = field(
        repr=False,
        compare=False,
        hash=False,
        metadata={"canonical_json": False},
    )
    extraction_snapshot: bytes = field(
        repr=False,
        compare=False,
        hash=False,
        metadata={"canonical_json": False},
    )

    def __post_init__(self) -> None:
        if not isinstance(self.source_snapshot, bytes) or not isinstance(
            self.extraction_snapshot, bytes
        ):
            raise TypeError("Reference Enrollment snapshots must be immutable bytes")
        if (
            sha256_bytes(self.source_snapshot) != self.artifact.digest.sha256
            or len(self.source_snapshot) != self.artifact.digest.size_bytes
        ):
            raise ValueError("Reference Enrollment source snapshot digest mismatch")
        snapshot_bytes = self.extraction_snapshot
        if (
            sha256_bytes(snapshot_bytes) != self.artifact.extracted_text.sha256
            or len(snapshot_bytes) != self.artifact.extracted_text.size_bytes
        ):
            raise ValueError("Reference Enrollment snapshot digest mismatch")
        try:
            snapshot = ReferenceExtractionSnapshot.model_validate_json(snapshot_bytes)
        except ValueError as exc:
            raise ValueError("Reference Enrollment snapshot is invalid") from exc
        if canonical_json_bytes(snapshot) != snapshot_bytes:
            raise ValueError("Reference Enrollment snapshot must use canonical JSON bytes")
        if (
            snapshot.source_sha256 != self.artifact.digest.sha256
            or snapshot.source_size_bytes != self.artifact.digest.size_bytes
            or snapshot.source_format != self.artifact.source_format
            or snapshot.extractor_name != self.artifact.extractor_name
            or snapshot.extractor_version != self.artifact.extractor_version
            or snapshot.extractor_config_hash != self.artifact.extractor_config_hash
            or snapshot.extractor_code_hash != self.artifact.extractor_code_hash
            or snapshot.extractor_runtime_hash
            != self.artifact.extractor_runtime_hash
            or snapshot.offset_unit != self.artifact.offset_unit
            or len(snapshot.blocks) != self.artifact.extraction_block_count
        ):
            raise ValueError("Reference Enrollment snapshot crossed Artifact lineage")


@dataclass(frozen=True, slots=True)
class CreateRequest:
    episode_id: str
    source_audio: Path
    # Deprecated compatibility surface. Every Artifact must have a matching
    # ReferenceEnrollment; a bare Artifact is rejected fail-closed.
    reference_artifacts: tuple[ReferenceArtifact, ...] = ()
    reference_enrollments: tuple[ReferenceEnrollment, ...] = ()
    speaker_tracks: tuple[SpeakerTrackInput, ...] = ()
    vocabulary: tuple[str, ...] = ()
    language_hint: str | None = None
    policy: SubtitlePolicy = DEFAULT_SUBTITLE_POLICY

    def __post_init__(self) -> None:
        if not self.episode_id.strip():
            raise ValueError("create request requires a non-blank episode_id")
        object.__setattr__(self, "source_audio", Path(self.source_audio))
        artifacts = tuple(self.reference_artifacts)
        enrollments = tuple(self.reference_enrollments)
        if len({item.source_id for item in artifacts}) != len(artifacts):
            raise ValueError("reference artifact source IDs must be unique")
        if len({item.artifact.source_id for item in enrollments}) != len(enrollments):
            raise ValueError("reference enrollment source IDs must be unique")
        enrollment_manifest_hashes = tuple(
            item.artifact.enrollment_manifest_sha256 for item in enrollments
        )
        if any(value is not None for value in enrollment_manifest_hashes) and (
            any(value is None for value in enrollment_manifest_hashes)
            or len(set(enrollment_manifest_hashes)) != 1
        ):
            raise ValueError(
                "reference enrollments must belong to one exact operator manifest"
            )
        enrolled_by_source = {
            enrollment.artifact.source_id: enrollment for enrollment in enrollments
        }
        if artifacts:
            artifacts_by_source = {artifact.source_id: artifact for artifact in artifacts}
            if set(artifacts_by_source) != set(enrolled_by_source) or any(
                enrolled_by_source[source_id].artifact != artifact
                for source_id, artifact in artifacts_by_source.items()
            ):
                raise ValueError(
                    "every reference Artifact requires one exact Reference Enrollment"
                )
        else:
            artifacts = tuple(enrollment.artifact for enrollment in enrollments)
            object.__setattr__(self, "reference_artifacts", artifacts)
        if self.speaker_tracks and len(self.speaker_tracks) != 2:
            raise ValueError("speaker attribution requires exactly two microphone tracks")
        if len({item.speaker_label for item in self.speaker_tracks}) != len(
            self.speaker_tracks
        ):
            raise ValueError("speaker attribution labels must be unique")
        if len({str(item.path.resolve()) for item in self.speaker_tracks}) != len(
            self.speaker_tracks
        ):
            raise ValueError("speaker attribution track paths must be distinct")
        snapshots_by_digest: dict[str, bytes] = {}
        sources_by_digest: dict[str, bytes] = {}
        for enrollment in enrollments:
            source_digest = enrollment.artifact.digest.sha256
            existing_source = sources_by_digest.get(source_digest)
            if existing_source is not None and existing_source != enrollment.source_snapshot:
                raise ValueError(
                    "Reference Enrollments contain conflicting source bytes for one digest"
                )
            sources_by_digest[source_digest] = enrollment.source_snapshot
            digest = enrollment.artifact.extracted_text.sha256
            existing = snapshots_by_digest.get(digest)
            if existing is not None and existing != enrollment.extraction_snapshot:
                raise ValueError(
                    "Reference Enrollments contain conflicting bytes for one digest"
                )
            snapshots_by_digest[digest] = enrollment.extraction_snapshot


@dataclass(frozen=True, slots=True)
class ResolveRequest:
    generation_id: str
    decisions: tuple[CorrectionDecision, ...]

    def __post_init__(self) -> None:
        if not self.generation_id.strip() or not self.decisions:
            raise ValueError("resolve requires a generation ID and at least one decision")


@dataclass(frozen=True, slots=True)
class ProjectRequest:
    generation_id: str
    profile: ProjectionProfile

    def __post_init__(self) -> None:
        if not self.generation_id.strip():
            raise ValueError("project requires a generation ID")


@dataclass(frozen=True, slots=True)
class AcceptedGeneration:
    generation_id: str
    transcript: CanonicalTranscript
    manifest: GenerationManifest


@dataclass(frozen=True, slots=True)
class NeedsReview:
    generation_id: str
    transcript: CanonicalTranscript
    issues: tuple[ReviewIssue, ...]
    manifest: GenerationManifest


@dataclass(frozen=True, slots=True)
class VerifiedProjection:
    projection_id: str
    episode_id: str
    generation_id: str
    projection: SubtitleProjection
    manifest: ProjectionManifest
    quality_report: QualityReport
    projection_bytes: bytes
    manifest_bytes: bytes
    quality_report_bytes: bytes
    srt_bytes: bytes
    sidecar_bytes: bytes
    projection_sha256: str
    manifest_sha256: str
    quality_report_sha256: str
    srt_sha256: str
    sidecar_sha256: str


@dataclass(frozen=True, slots=True)
class Interrupted:
    operation: Literal["create", "resolve", "project"]
    reason: str
    generation_id: str | None = None
    audit_basis_generation_id: str | None = None
    packet_paths: tuple[Path, ...] = ()
    clip_paths: tuple[Path, ...] = ()
    response_paths: tuple[Path, ...] = ()


@dataclass(frozen=True, slots=True)
class _CreateEvidenceState:
    source_hash: str
    receipt: NormalizationReceipt
    receipt_hash: str
    invocation_id: str
    recognition_request: RecognitionRequestArtifactV1
    recognition_independence: RecognitionIndependenceReceiptV1 | None
    evidence: tuple[RecognitionEvidence, ...]
    orthographic_projections: tuple[OrthographicProjectionEvidenceV1, ...]
    orthographic_raw_outputs: dict[str, bytes]
    base_primary_evidence: RecognitionEvidence | None
    speaker_provenance: SpeakerAttributionProvenance | None
    speech_coverage_receipt: SpeechCoverageReceipt | None
    speech_coverage_raw_artifacts: dict[str, bytes]
    raw_outputs: dict[str, bytes]
    reference_retrieval_policy: ReferenceRetrievalPolicySnapshot
    checkpoint: CreateCheckpoint


@dataclass(frozen=True, slots=True)
class _CreateCorrectionState:
    evidence_state: _CreateEvidenceState
    result: CanonicalBuildResult
    references: tuple[ReferenceEvidence, ...]
    retrievals: tuple[ReferenceRetrievalReceipt, ...]
    proposals: tuple[CorrectionProposal, ...]
    correction_audits: tuple[CorrectionAuditReceipt, ...]
    targeted_correction_audits: tuple[_TargetedCorrectionAuditReceipt, ...]
    correction_execution_artifacts: dict[str, bytes]
    checkpoint: CreateCheckpoint


@dataclass(frozen=True, slots=True)
class _CreateAudioState:
    correction_state: _CreateCorrectionState
    result: CanonicalBuildResult
    proposals: tuple[CorrectionProposal, ...]
    audio_audits: tuple[AudioAuditReceipt, ...]
    audio_execution_artifacts: dict[str, bytes]
    checkpoint: CreateCheckpoint


@dataclass(frozen=True, slots=True)
class _NativeAuditBasisState:
    evidence_state: _CreateEvidenceState
    result: CanonicalBuildResult
    references: tuple[ReferenceEvidence, ...]
    retrievals: tuple[ReferenceRetrievalReceipt, ...]
    audit_plan: AuditPlan
    candidate_signal_set: CandidateSignalSet
    candidate_group_set: CandidateGroupSet
    execution_plan: CorrectionAuditExecutionPlanV2
    text_source_artifacts: dict[str, bytes]
    audio_source_artifacts: dict[str, bytes]
    checkpoint: CreateCheckpoint


@dataclass(frozen=True, slots=True)
class _NativeTextAuditState:
    basis: _NativeAuditBasisState
    run: TextAuditRunResult
    checkpoint: CreateCheckpoint


@dataclass(frozen=True, slots=True)
class _NativeAudioAuditState:
    text: _NativeTextAuditState
    run: AudioAuditRunResult
    aggregate: FullAuditAggregateAttestationV2
    checkpoint: CreateCheckpoint


NativeDiscoveredCandidateV2 = TextDiscoveredCandidateV2 | AudioDiscoveredCandidateV2


@dataclass(frozen=True, slots=True)
class _NativeResolveAuthorizationState:
    parent: LoadedGenerationState
    candidate: NativeDiscoveredCandidateV2
    authorization: CorrectionAcceptanceVerdictV2 | OriginalConfirmationAuthorizationV2
    decision: NativeCorrectionDecisionV2
    ledger_entry: LedgerEntry
    operation_key: str
    resolution_artifacts: dict[str, bytes]


@dataclass(frozen=True, slots=True)
class _NativeResolveAuditBasisState:
    authorization: _NativeResolveAuthorizationState
    result: CanonicalBuildResult
    references: tuple[ReferenceEvidence, ...]
    retrievals: tuple[ReferenceRetrievalReceipt, ...]
    audit_plan: AuditPlan
    candidate_signal_set: CandidateSignalSet
    candidate_group_set: CandidateGroupSet
    execution_plan: CorrectionAuditExecutionPlanV2
    text_source_artifacts: dict[str, bytes]
    audio_source_artifacts: dict[str, bytes]
    checkpoint: NativeResolveCheckpointV2


@dataclass(frozen=True, slots=True)
class _NativeResolveTextAuditState:
    basis: _NativeResolveAuditBasisState
    run: TextAuditRunResult
    checkpoint: NativeResolveCheckpointV2


@dataclass(frozen=True, slots=True)
class _NativeResolveAudioAuditState:
    text: _NativeResolveTextAuditState
    run: AudioAuditRunResult
    aggregate: FullAuditAggregateAttestationV2
    checkpoint: NativeResolveCheckpointV2


GenerationOutcome = AcceptedGeneration | NeedsReview | Interrupted
ProjectOutcome = VerifiedProjection | Interrupted


_RECEIPT = "normalization_receipt.json"
_AUDIO_ARTIFACTS = "audio_artifacts.json"
_EVIDENCE = "recognition_evidence.json"
_RECOGNITION_REQUEST = "recognition_request.json"
_RECOGNITION_INDEPENDENCE = "recognition_independence_receipt.json"
_ORTHOGRAPHIC_PROJECTIONS = "orthographic_projection_evidence.json"
_SPEECH_COVERAGE = "speech_coverage_receipt.json"
_BASE_PRIMARY_EVIDENCE = "base_primary_recognition_evidence.json"
_SPEAKER_PROVENANCE = "speaker_attribution_provenance.json"
_REFERENCE_ENROLLMENTS = "reference_enrollments.json"
_REFERENCES = "reference_evidence.json"
_RETRIEVALS = "reference_retrieval_receipts.json"
_REFERENCE_RETRIEVAL_POLICY = "reference_retrieval_policy.json"
_PROPOSALS = "correction_proposals.json"
_CORRECTION_AUDITS = "correction_audit_receipts.json"
_TARGETED_CORRECTION_AUDITS = "targeted_correction_audit_receipts.json"
_AUDIO_AUDITS = "audio_audit_receipts.json"
_ARBITRATIONS = "arbitration_receipts.json"
_ADAPTER_IDENTITIES = "adapter_identities.json"
_CANONICAL = "canonical_transcript.json"
_RISKS = "risks.json"
_MANIFEST = "manifest.json"
_SEMANTIC = "semantic_units.json"
_PROJECTION = "projection.json"
_PROJECTION_MANIFEST = "projection_manifest.json"
_QUALITY = "quality_report.json"
_PROFILE = "projection_profile.json"
_SEMANTIC_ADAPTER = "semantic_analyzer_identity.json"
_SEMANTIC_RECEIPTS = "semantic_execution_receipts.json"
_BOUNDARY_CONSTRAINTS = "boundary_constraints.json"
_SRT = "transcript.srt"
_SIDECAR = "transcript.tokens.json"
_CHECKPOINT_CANONICAL = "checkpoint/canonical_transcript.json"
_CHECKPOINT_RISKS = "checkpoint/risks.json"
_CHECKPOINT_AUDIO_CANONICAL = "checkpoint/audio_canonical_transcript.json"
_CHECKPOINT_AUDIO_RISKS = "checkpoint/audio_risks.json"
_CHECKPOINT_AUDIO_PROPOSALS = "checkpoint/audio_proposals.json"
_CHECKPOINT_CREATE_INTENT = "checkpoint/create_intent.json"
_CHECKPOINT_COMPLETE_BINDING = "checkpoint/complete_binding.json"
_NATIVE_AUDIT_PLAN = "native_full_audit/audit_plan.json"
_NATIVE_CANDIDATE_SIGNALS = "native_full_audit/candidate_signal_set.json"
_NATIVE_CANDIDATE_GROUPS = "native_full_audit/candidate_group_set.json"
_NATIVE_EXECUTION_PLAN = "native_full_audit/correction_audit_execution_plan.json"
_NATIVE_TEXT_RECORD = "native_full_audit/text_audit_execution_record.json"
_NATIVE_AUDIO_RECORD = "native_full_audit/audio_audit_execution_record.json"
_NATIVE_AGGREGATE = "native_full_audit/full_audit_aggregate.json"
_NATIVE_TEXT_SOURCE_INDEX = "native_full_audit/text_source_artifact_index.json"
_NATIVE_AUDIO_SOURCE_INDEX = "native_full_audit/audio_source_artifact_index.json"
_NATIVE_TEXT_RUN_INDEX = "native_full_audit/text_run_index.json"
_NATIVE_AUDIO_RUN_INDEX = "native_full_audit/audio_run_index.json"
_NATIVE_RESOLUTION_DECISION = "native_resolution/decision.json"
_NATIVE_RESOLUTION_CANDIDATE = "native_resolution/candidate_discovery.json"
_NATIVE_RESOLUTION_AUTHORIZATION = "native_resolution/authorization.json"
_NATIVE_RESOLUTION_ACCEPTANCE_POLICY = "native_resolution/acceptance_policy.json"
_NATIVE_RESOLUTION_ORIGINAL_POLICY = "native_resolution/original_confirmation_policy.json"
_NATIVE_RESOLUTION_HUMAN_AUDIO_INDEX = "native_resolution/human_audio_index.json"
_NATIVE_RESOLUTION_HUMAN_ORIGINAL_INDEX = (
    "native_resolution/human_original_confirmation_index.json"
)
_NATIVE_RESOLUTION_REFERENCE_PROOF = "native_resolution/reference_authority_proof.json"
_NATIVE_RESOLUTION_REFERENCE_ADJUDICATION = (
    "native_resolution/human_reference_adjudication.json"
)
_NATIVE_RESOLUTION_LEDGER_ENTRY = "native_resolution/prepared_ledger_entry.json"
_NATIVE_RESOLVE_PREFIX = "native_resolve/"

_RESOLUTION_TRANSACTION_VERSION = 1

_PROJECTION_ID_RE = re.compile(r"^projection-[0-9a-f]{64}$")
_MATERIAL_SEVERITIES = frozenset({"medium", "high", "blocking"})
_COVERAGE_GATE_CODES = frozenset(
    {
        "speech_coverage_receipt_missing",
        "speech_coverage_analyzer_failed",
        "uncovered_speech_activity",
    }
)


def _model_bytes(model: object) -> bytes:
    if hasattr(model, "model_dump_json"):
        return model.model_dump_json(exclude_none=False).encode("utf-8")
    raise TypeError(f"expected a Pydantic contract, got {type(model).__name__}")


def _ledger_entry_payload(entry: LedgerEntry) -> dict[str, object]:
    return {
        "sequence": entry.sequence,
        "previous_hash": entry.previous_hash,
        "decision": entry.decision,
        "decision_hash": entry.decision_hash,
        "entry_hash": entry.entry_hash,
    }


def _ledger_entry_from_payload(payload: object) -> LedgerEntry:
    required = {
        "sequence",
        "previous_hash",
        "decision",
        "decision_hash",
        "entry_hash",
    }
    if not isinstance(payload, dict) or set(payload) != required:
        raise ResolutionTransactionError(
            "resolution transaction carries an invalid Ledger entry"
        )
    if (
        not isinstance(payload["sequence"], int)
        or isinstance(payload["sequence"], bool)
        or payload["sequence"] < 1
        or not isinstance(payload["previous_hash"], str)
        or not isinstance(payload["decision"], dict)
        or not isinstance(payload["decision_hash"], str)
        or not isinstance(payload["entry_hash"], str)
    ):
        raise ResolutionTransactionError(
            "resolution transaction Ledger entry has invalid field types"
        )
    entry = LedgerEntry(
        sequence=payload["sequence"],
        previous_hash=payload["previous_hash"],
        decision=dict(payload["decision"]),
        decision_hash=payload["decision_hash"],
        entry_hash=payload["entry_hash"],
    )
    decision_hash = hash_object(entry.decision)
    identity = {
        "sequence": entry.sequence,
        "previous_hash": entry.previous_hash,
        "decision": entry.decision,
        "decision_hash": decision_hash,
    }
    if entry.decision_hash != decision_hash or entry.entry_hash != hash_object(identity):
        raise ResolutionTransactionError(
            "resolution transaction Ledger entry hash mismatch"
        )
    return entry


def _file_uri_path(uri: str) -> Path:
    parsed = urlparse(uri)
    if parsed.scheme != "file":
        raise ModuleInvariantError(
            "Recognition raw output must be a local immutable file artifact"
        )
    raw = url2pathname(unquote(parsed.path))
    if os.name == "nt" and len(raw) >= 3 and raw[0] in "/\\" and raw[2] == ":":
        raw = raw[1:]
    return Path(raw)


def _snapshot_raw_outputs(
    evidence: Sequence[RecognitionEvidence],
) -> tuple[dict[str, bytes], tuple[RecognitionEvidence, ...]]:
    snapshots: dict[str, bytes] = {}
    portable: list[RecognitionEvidence] = []
    for item in evidence:
        path = _file_uri_path(item.raw_output.uri)
        if not path.is_file():
            raise GenerationIsolationError(
                f"Recognition raw output does not exist: {item.raw_output.uri}"
            )
        payload = path.read_bytes()
        if (
            sha256_bytes(payload) != item.raw_output.sha256
            or len(payload) != item.raw_output.size_bytes
        ):
            raise GenerationIsolationError(
                f"Recognition raw output bytes do not match Evidence: {item.raw_output.uri}"
            )
        name = f"raw_evidence/{item.raw_output.sha256}.json"
        existing = snapshots.get(name)
        if existing is not None and existing != payload:
            raise GenerationIsolationError("raw Evidence digest collision")
        snapshots[name] = payload
        portable.append(
            item.model_copy(
                update={
                    "raw_output": item.raw_output.model_copy(
                        update={"uri": f"generation-artifact://{name}"}
                    )
                }
            )
        )
    return snapshots, tuple(portable)


def _speech_coverage_identity(
    analyzer: SpeechCoverageAnalyzer | None,
) -> dict[str, str] | None:
    """Freeze the analyzer's full replay identity without a second identity type."""

    if analyzer is None:
        return None
    identity = {
        "adapter_name": analyzer.adapter_name,
        "adapter_version": analyzer.adapter_version,
        "config_hash": analyzer.adapter_config_hash,
        "code_hash": analyzer.adapter_code_hash,
        "runtime_hash": analyzer.adapter_runtime_hash,
    }
    if not identity["adapter_name"].strip() or not identity["adapter_version"].strip():
        raise ValueError("Speech Coverage Analyzer identity must not be blank")
    for label in ("config_hash", "code_hash", "runtime_hash"):
        if not re.fullmatch(r"[0-9a-f]{64}", identity[label]):
            raise ValueError(f"Speech Coverage Analyzer {label} must be lowercase SHA-256")
    return identity


def _checkpoint_adapter_identity(
    adapter: object,
    *,
    role: str,
    ordinal: int = 0,
    declared: AdapterIdentity | None = None,
) -> CheckpointAdapterIdentity:
    """Measure enough executable identity to reject cross-process drift.

    Production Adapters expose their own measured config/code/runtime hashes.
    Small in-process fixture Adapters may omit them; their class source and
    process runtime are then the fail-closed executable boundary.  Mutable call
    counters and packet readiness are intentionally not configuration.
    """

    runtime_identity = getattr(adapter, "identity", None)
    adapter_name = (
        declared.name
        if declared is not None
        else getattr(runtime_identity, "adapter_name", None)
        or getattr(runtime_identity, "adapter", None)
        or getattr(adapter, "adapter_name", None)
        or f"{type(adapter).__module__}.{type(adapter).__qualname__}"
    )
    adapter_version = (
        declared.version
        if declared is not None
        else getattr(runtime_identity, "adapter_version", None)
        or getattr(adapter, "adapter_version", None)
        or "unversioned"
    )
    config_hash = (
        declared.config_hash
        if declared is not None
        else getattr(runtime_identity, "config_hash", None)
        or getattr(adapter, "adapter_config_hash", None)
    )
    try:
        source_path = inspect.getsourcefile(type(adapter))
    except (TypeError, OSError):
        source_path = None
    source_hash = (
        hash_file(source_path)
        if source_path is not None and Path(source_path).is_file()
        else hash_object(
            {
                "module": type(adapter).__module__,
                "qualname": type(adapter).__qualname__,
            }
        )
    )
    code_hash = (
        getattr(runtime_identity, "adapter_code_hash", None)
        or getattr(adapter, "adapter_code_hash", None)
        or source_hash
    )
    runtime_hash = (
        getattr(runtime_identity, "runtime_hash", None)
        or getattr(adapter, "adapter_runtime_hash", None)
        or hash_object(
            {
                "python_implementation": sys.implementation.name,
                "python_version": tuple(sys.version_info[:3]),
                "adapter_module": type(adapter).__module__,
            }
        )
    )
    if config_hash is None:
        stable_config = getattr(adapter, "_settings", None)
        config_hash = hash_object(
            {
                "adapter_class": (
                    f"{type(adapter).__module__}.{type(adapter).__qualname__}"
                ),
                "settings": stable_config if isinstance(stable_config, Mapping) else None,
            }
        )
    execution_mode = (
        declared.execution_mode
        if declared is not None
        else getattr(runtime_identity, "execution_mode", None)
        or "other"
    )
    # RecognitionModelIdentity records the more precise ``import`` mode for
    # immutable provider exports.  The older checkpoint envelope predates that
    # literal; retain the exact mode in Recognition Evidence while mapping only
    # this generic executable checkpoint category to its closed ``other`` value.
    if execution_mode == "import":
        execution_mode = "other"
    return CheckpointAdapterIdentity(
        role=role,
        ordinal=ordinal,
        adapter_name=str(adapter_name),
        adapter_version=str(adapter_version),
        config_hash=str(config_hash),
        code_hash=str(code_hash),
        runtime_hash=str(runtime_hash),
        execution_mode=execution_mode,
    )


def _checkpoint_artifact_bytes(value: object) -> bytes:
    return value if isinstance(value, bytes) else canonical_json_bytes(value)


def _merge_exact_artifacts(
    target: dict[str, bytes], incoming: Mapping[str, bytes], *, label: str
) -> None:
    for uri, payload in incoming.items():
        existing = target.get(uri)
        if existing is not None and existing != payload:
            raise GenerationIsolationError(f"{label} artifact URI has conflicting bytes")
        target[uri] = payload


def _address_native_sources(
    sources: Mapping[str, bytes], *, namespace: str
) -> tuple[bytes, dict[str, bytes]]:
    artifacts: dict[str, bytes] = {}
    entries: list[dict[str, object]] = []
    for ordinal, (uri, payload) in enumerate(sorted(sources.items())):
        if type(uri) is not str or not uri or type(payload) is not bytes:
            raise GenerationIsolationError("native source artifact mapping is invalid")
        digest = sha256_bytes(payload)
        name = f"native_full_audit/sources/{namespace}/{ordinal:04d}-{digest}.bin"
        artifacts[name] = payload
        entries.append(
            {
                "uri": uri,
                "artifact_name": name,
                "sha256": digest,
                "size_bytes": len(payload),
            }
        )
    index = canonical_json_bytes({"schema_version": 1, "entries": entries})
    return index, artifacts


def _address_native_run_bytes(
    *,
    prefix: str,
    categories: Mapping[str, Sequence[bytes]],
) -> tuple[bytes, dict[str, bytes]]:
    artifacts: dict[str, bytes] = {}
    index: dict[str, object] = {"schema_version": 1}
    for category, values in categories.items():
        names: list[str] = []
        for ordinal, payload in enumerate(values):
            if type(payload) is not bytes or not payload:
                raise GenerationIsolationError("native audit run requires exact non-empty bytes")
            digest = sha256_bytes(payload)
            name = f"native_full_audit/{prefix}/{category}/{ordinal:04d}-{digest}.bin"
            artifacts[name] = payload
            names.append(name)
        index[category] = names
    return canonical_json_bytes(index), artifacts


def _native_resolve_checkpoint_artifacts(
    artifacts: Mapping[str, bytes],
) -> dict[str, bytes]:
    """Namespace final Generation artifact names inside a resolve checkpoint."""

    return {
        f"{_NATIVE_RESOLVE_PREFIX}{name}": payload
        for name, payload in sorted(artifacts.items())
    }


def _address_native_resolution_receipts(
    receipts: Sequence[bytes],
    *,
    namespace: Literal["human_audio", "human_original_confirmation"] = "human_audio",
) -> tuple[bytes, dict[str, bytes]]:
    names: list[str] = []
    artifacts: dict[str, bytes] = {}
    for ordinal, payload in enumerate(receipts):
        digest = sha256_bytes(payload)
        name = f"native_resolution/{namespace}/{ordinal:04d}-{digest}.json"
        names.append(name)
        artifacts[name] = payload
    return canonical_json_bytes({"schema_version": 2, "receipts": names}), artifacts


def _snapshot_speech_coverage(
    receipt: SpeechCoverageReceipt,
) -> tuple[SpeechCoverageReceipt, dict[str, bytes]]:
    """Copy raw activity evidence into one portable content-addressed Generation path."""

    path = _file_uri_path(receipt.raw_output.uri)
    if not path.is_file():
        raise GenerationIsolationError("Speech Coverage raw output does not exist")
    payload = path.read_bytes()
    if (
        sha256_bytes(payload) != receipt.raw_output.sha256
        or len(payload) != receipt.raw_output.size_bytes
        or receipt.raw_output_hash != receipt.raw_output.sha256
    ):
        raise GenerationIsolationError("Speech Coverage raw output digest mismatch")
    name = f"speech_coverage/raw/{receipt.raw_output.sha256}.json"
    portable = receipt.model_copy(
        update={
            "raw_output": receipt.raw_output.model_copy(
                update={"uri": f"generation-artifact://{name}"}
            )
        }
    )
    return portable, {name: payload}


def _portable_receipt(receipt: NormalizationReceipt) -> NormalizationReceipt:
    """Remove machine-local transport paths without changing content lineage."""

    return receipt.model_copy(
        update={
            "source": receipt.source.model_copy(
                update={"uri": f"sha256://{receipt.source.sha256}"}
            ),
            "normalized": receipt.normalized.model_copy(
                update={"uri": f"sha256://{receipt.normalized.sha256}"}
            ),
        }
    )


def _audio_artifacts_payload(receipt: NormalizationReceipt) -> dict[str, dict[str, object]]:
    """Portable CAS identities for the two audio artifacts owned by a Generation."""

    return {
        "source": {
            "sha256": receipt.source.sha256,
            "size_bytes": receipt.source.size_bytes,
        },
        "normalized": {
            "sha256": receipt.normalized.sha256,
            "size_bytes": receipt.normalized.size_bytes,
        },
    }


def _portable_reference_artifact(artifact: ReferenceArtifact) -> ReferenceArtifact:
    source_name = f"reference_sources/{artifact.digest.sha256}.bin"
    digest = artifact.digest.model_copy(
        update={"uri": f"generation-artifact://{source_name}"}
    )
    extraction_name = (
        f"reference_extractions/{artifact.extracted_text.sha256}.json"
    )
    extracted_text = artifact.extracted_text.model_copy(
        update={"uri": f"generation-artifact://{extraction_name}"}
    )
    return artifact.model_copy(
        update={"digest": digest, "extracted_text": extracted_text}
    )


def _portable_reference(item: ReferenceEvidence) -> ReferenceEvidence:
    return item.model_copy(
        update={"artifact": _portable_reference_artifact(item.artifact)}
    )


def _reference_evidence_by_span(
    references: Sequence[ReferenceEvidence],
    retrievals: Sequence[ReferenceRetrievalReceipt],
) -> dict[str, tuple[ReferenceEvidence, ...]]:
    """Project stored retrieval membership into deterministic audit-window context."""

    reference_by_id = {item.id: item for item in references}
    projected: dict[str, tuple[ReferenceEvidence, ...]] = {}
    for retrieval in retrievals:
        if retrieval.audio_span_id in projected:
            raise GenerationIsolationError(
                "Reference Retrieval Receipts duplicate one Canonical anchor"
            )
        by_id: dict[str, ReferenceEvidence] = {}
        for item in retrieval.evidence:
            stored = reference_by_id.get(item.id)
            if stored is None or stored != item:
                raise GenerationIsolationError(
                    "Reference Retrieval Receipt names unavailable or conflicting evidence"
                )
            by_id[item.id] = stored
        projected[retrieval.audio_span_id] = tuple(
            sorted(by_id.values(), key=lambda evidence: evidence.id)
        )
    return projected


def _casefold_occurrences(value: str, needle: str) -> tuple[tuple[int, int], ...]:
    """Case-insensitive occurrences mapped back to exact Unicode-scalar ranges."""

    folded_parts: list[str] = []
    original_ranges: list[tuple[int, int]] = []
    for index, character in enumerate(value):
        folded = character.casefold()
        folded_parts.append(folded)
        original_ranges.extend((index, index + 1) for _ in folded)
    folded_value = "".join(folded_parts)
    folded_needle = needle.casefold()
    if not folded_needle:
        return ()
    matches: list[tuple[int, int]] = []
    search_start = 0
    while (position := folded_value.find(folded_needle, search_start)) >= 0:
        end = position + len(folded_needle)
        matches.append((original_ranges[position][0], original_ranges[end - 1][1]))
        search_start = position + 1
    return tuple(matches)


def _reference_retrieval_requests(
    *,
    transcript: CanonicalTranscript,
    policy: ReferenceRetrievalPolicySnapshot,
    invocation_id: str,
    allowed_source_ids: tuple[str, ...],
) -> tuple[ReferenceRetrievalRequest, ...]:
    """Build the one authoritative ordered request set used by run and replay."""

    contexts = build_reference_query_contexts(transcript, policy)
    issues_by_span: dict[str, list[ReviewIssue]] = {}
    retrievable_codes = set(policy.retrievable_codes)
    for issue in transcript.review_issues:
        if (
            not _is_material_issue(transcript, issue)
            or issue.code not in retrievable_codes
            or not issue.candidates
        ):
            continue
        for span_id in issue.span_ids:
            issues_by_span.setdefault(span_id, []).append(issue)

    requests: list[ReferenceRetrievalRequest] = []
    for context in contexts:
        candidate_terms: list[str] = []
        seen: set[str] = set()

        def add(term: str) -> None:
            value = term.strip()
            key = value.casefold()
            if not value or key in seen or len(candidate_terms) >= policy.max_candidate_terms:
                return
            seen.add(key)
            candidate_terms.append(value)

        for issue in issues_by_span.get(context.anchor_span_id, ()):
            for candidate in issue.candidates:
                add(candidate)
        for vocabulary_term in policy.vocabulary:
            if any(
                start < context.anchor_query_end and end > context.anchor_query_start
                for start, end in _casefold_occurrences(
                    context.exact_query, vocabulary_term
                )
            ):
                add(vocabulary_term)
        requests.append(
            ReferenceRetrievalRequest(
                episode_id=transcript.episode_id,
                invocation_id=invocation_id,
                context=context,
                policy=policy,
                candidate_terms=tuple(candidate_terms),
                allowed_artifact_ids=allowed_source_ids,
            )
        )
    return tuple(requests)


def _reference_retrieval_failure_risks(
    retrievals: Sequence[ReferenceRetrievalReceipt],
) -> tuple[RiskRecord, ...]:
    """Turn an operational no-answer into blocking, span-addressed provenance."""

    risks: list[RiskRecord] = []
    for receipt in retrievals:
        if receipt.status != "failed":
            continue
        issue = ReviewIssue(
            id="issue-" + hash_object(
                {
                    "code": "reference_retrieval_failed",
                    "span_ids": (receipt.audio_span_id,),
                    "query_id": receipt.query_id,
                    "failure_reason": receipt.failure_reason,
                }
            ),
            risk="provenance",
            severity="blocking",
            code="reference_retrieval_failed",
            span_ids=(receipt.audio_span_id,),
            status="unresolved",
        )
        risks.append(
            RiskRecord(
                issue=issue,
                audio_span_ids=(receipt.audio_span_id,),
            )
        )
    return tuple(risks)


def _native_full_audit_resolution_risk(
    transcript: CanonicalTranscript,
    discoveries: Sequence[NativeDiscoveredCandidateV2] = (),
) -> RiskRecord:
    """Keep native audit evidence non-authoritative until an explicit decision."""

    ordered = tuple(sorted(discoveries, key=lambda item: item.id))
    cited_span_ids = {
        span_id for candidate in ordered for span_id in candidate.cited_span_ids
    }
    span_ids = (
        tuple(span.id for span in transcript.spans if span.id in cited_span_ids)
        if ordered
        else tuple(item.id for item in transcript.spans)
    )
    candidates = tuple(item.id for item in ordered)
    if ordered and (
        not span_ids
        or cited_span_ids != set(span_ids)
        or any(
            not set(candidate.cited_span_ids).issubset(cited_span_ids)
            for candidate in ordered
        )
    ):
        raise GenerationIsolationError(
            "native discovery risk cites spans outside its audit-basis transcript"
        )
    issue_payload = {
        "code": "native_full_audit_requires_resolution",
        "span_ids": span_ids,
        "candidates": candidates,
    }
    issue = ReviewIssue(
        id=f"issue-{hash_object(issue_payload)}",
        risk="text",
        severity="high",
        code="native_full_audit_requires_resolution",
        span_ids=span_ids,
        candidates=candidates,
        status="unresolved",
    )
    return RiskRecord(issue=issue, audio_span_ids=span_ids)


def _native_resolved_audit_basis(
    result: CanonicalBuildResult,
) -> CanonicalBuildResult:
    """Return the exact child truth that provider audit artifacts are bound to."""

    return without_native_resolution_risk(result)


def _native_resolved_after_fresh_audit(
    result: CanonicalBuildResult,
    *,
    text_record: TextAuditExecutionRecordV2,
    audio_record: AudioAuditExecutionRecordV2,
) -> CanonicalBuildResult:
    """Attach a child-only gate exactly when the fresh audit found candidates."""

    basis = _native_resolved_audit_basis(result)
    discoveries: tuple[NativeDiscoveredCandidateV2, ...] = (
        *text_record.candidate_discovery_set.candidates,
        *audio_record.candidate_discovery_set.candidates,
    )
    if not discoveries:
        return basis
    return add_review_risks(
        basis,
        (_native_full_audit_resolution_risk(basis.transcript, discoveries),),
    )


def _reference_evidence_ids_for_target(
    transcript: CanonicalTranscript,
    references: Sequence[ReferenceEvidence],
    retrievals: Sequence[ReferenceRetrievalReceipt],
    target_span_ids: Sequence[str],
) -> tuple[str, ...]:
    """Return only Reference Evidence actually retrieved for the target lineage."""

    references_by_span = _reference_evidence_by_span(references, retrievals)
    span_by_id = {span.id: span for span in transcript.spans}
    allowed: set[str] = set()
    for span_id in target_span_ids:
        try:
            span = span_by_id[span_id]
        except KeyError as exc:
            raise GenerationIsolationError(
                f"Correction Decision targets unknown canonical identity {span_id!r}"
            ) from exc
        for lineage_span_id in (span.id, *span.lineage):
            allowed.update(
                item.id for item in references_by_span.get(lineage_span_id, ())
            )
    return tuple(sorted(allowed))


def _snapshot_reference_enrollments(
    enrollments: Sequence[ReferenceEnrollment],
    *,
    parser_registry: TrustedReferenceParserRegistry | None,
) -> tuple[tuple[ReferenceArtifact, ...], dict[str, bytes], dict[str, bytes]]:
    portable: list[ReferenceArtifact] = []
    source_snapshots: dict[str, bytes] = {}
    extraction_snapshots: dict[str, bytes] = {}
    for enrollment in enrollments:
        verify_reference_extraction_derivation(
            enrollment.source_snapshot,
            enrollment.extraction_snapshot,
            enrolled_artifact=enrollment.artifact,
            parser_registry=parser_registry,
        )
        source_name = f"reference_sources/{enrollment.artifact.digest.sha256}.bin"
        existing_source = source_snapshots.get(source_name)
        if existing_source is not None and existing_source != enrollment.source_snapshot:
            raise GenerationIsolationError(
                "Reference source digest collision has conflicting bytes"
            )
        source_snapshots[source_name] = enrollment.source_snapshot
        digest = enrollment.artifact.extracted_text.sha256
        name = f"reference_extractions/{digest}.json"
        existing = extraction_snapshots.get(name)
        if existing is not None and existing != enrollment.extraction_snapshot:
            raise GenerationIsolationError(
                "Reference extraction digest collision has conflicting bytes"
            )
        extraction_snapshots[name] = enrollment.extraction_snapshot
        portable.append(_portable_reference_artifact(enrollment.artifact))
    return tuple(portable), source_snapshots, extraction_snapshots


def _bind_speaker_tracks(
    tracks: Sequence[SpeakerTrackInput],
) -> tuple[SpeakerTrackBinding, ...]:
    bindings: list[SpeakerTrackBinding] = []
    for item in tracks:
        path = item.path.resolve()
        if not path.is_file():
            raise ModuleInvariantError(f"speaker microphone track is not a file: {path}")
        digest = hash_file(path)
        size_bytes = path.stat().st_size
        if hash_file(path) != digest or path.stat().st_size != size_bytes:
            raise GenerationIsolationError("speaker microphone track changed while binding")
        bindings.append(
            SpeakerTrackBinding(
                path=path,
                speaker_label=item.speaker_label,
                sha256=digest,
                size_bytes=size_bytes,
            )
        )
    if len({binding.sha256 for binding in bindings}) != len(bindings):
        raise ModuleInvariantError("speaker microphone tracks must contain distinct bytes")
    if any(
        hash_file(binding.path) != binding.sha256
        or binding.path.stat().st_size != binding.size_bytes
        for binding in bindings
    ):
        raise GenerationIsolationError("speaker microphone track changed while binding")
    return tuple(bindings)


def _assert_speaker_attribution_binding(
    *,
    attributed: RecognitionEvidence,
    base: RecognitionEvidence,
    provenance: SpeakerAttributionProvenance,
    identity: AdapterIdentity,
    expected_tracks: Sequence[SpeakerTrackBinding] = (),
) -> None:
    """Recheck speaker truth in the Module, independent of Adapter trust."""

    base_hash = recognition_evidence_content_hash(base)
    if (
        attributed.episode_id != base.episode_id
        or attributed.invocation_id != base.invocation_id
        or attributed.normalized_audio_hash != base.normalized_audio_hash
        or attributed.adapter != identity.name
        or provenance.adapter != identity.name
        or str(provenance.adapter_version) != identity.version
        or provenance.base_recognition_evidence_hash != base_hash
        or provenance.config_hash != attributed.config_hash
        or len(attributed.tokens) != len(base.tokens)
    ):
        raise GenerationIsolationError(
            "Speaker Attribution crossed base Evidence, Adapter, or invocation lineage"
        )
    allowed_speakers = {track.speaker_label for track in provenance.tracks}
    for base_token, attributed_token in zip(base.tokens, attributed.tokens):
        if (
            attributed_token.text != base_token.text
            or attributed_token.start_ms != base_token.start_ms
            or attributed_token.end_ms != base_token.end_ms
            or attributed_token.confidence != base_token.confidence
        ):
            raise GenerationIsolationError(
                "Speaker Attribution changed Recognition truth or timing"
            )
        if attributed_token.speaker not in allowed_speakers:
            raise GenerationIsolationError(
                "Speaker Attribution left an unknown or unenrolled speaker"
            )
    if expected_tracks:
        expected = tuple(
            {
                "slot": slot,
                **binding.portable_identity,
            }
            for slot, binding in enumerate(expected_tracks)
        )
        actual = tuple(
            {
                "slot": track.slot,
                "speaker_label": track.speaker_label,
                "sha256": track.sha256,
                "size_bytes": track.size_bytes,
            }
            for track in provenance.tracks
        )
        if actual != expected:
            raise GenerationIsolationError(
                "Speaker Attribution provenance differs from bound microphone tracks"
            )


def _adapter_identity_from_payload(payload: object) -> AdapterIdentity | None:
    if payload is None:
        return None
    if not isinstance(payload, dict):
        raise GenerationIsolationError("stored Adapter identity has invalid shape")
    try:
        return AdapterIdentity(**payload)
    except (TypeError, ValueError) as exc:
        raise GenerationIsolationError("stored Adapter identity is invalid") from exc


def _audio_identity_from_payload(payload: object) -> AudioModelIdentity | None:
    if payload is None:
        return None
    if not isinstance(payload, dict):
        raise GenerationIsolationError("stored Audio Adapter identity has invalid shape")
    try:
        return AudioModelIdentity(**payload)
    except (TypeError, ValueError) as exc:
        raise GenerationIsolationError("stored Audio Adapter identity is invalid") from exc


def _recognition_identity_from_payload(payload: object) -> RecognitionModelIdentity:
    if not isinstance(payload, dict):
        raise GenerationIsolationError(
            "stored Recognition Adapter identity has invalid shape"
        )
    typed = dict(payload)
    components = typed.get("runtime_components")
    if not isinstance(components, list):
        raise GenerationIsolationError(
            "stored Recognition runtime components have invalid shape"
        )
    typed["runtime_components"] = tuple(
        tuple(component) if isinstance(component, list) else component
        for component in components
    )
    try:
        return RecognitionModelIdentity(**typed)
    except (TypeError, ValueError) as exc:
        raise GenerationIsolationError(
            "stored Recognition Adapter identity is invalid"
        ) from exc


def _semantic_identity_from_payload(payload: object) -> SemanticModelIdentity:
    if not isinstance(payload, dict):
        raise GenerationIsolationError("stored Semantic Adapter identity has invalid shape")
    try:
        return SemanticModelIdentity(**payload)
    except (TypeError, ValueError) as exc:
        raise GenerationIsolationError("stored Semantic Adapter identity is invalid") from exc


def _semantic_execution_receipt_from_payload(payload: object) -> SemanticExecutionReceipt:
    if not isinstance(payload, dict):
        raise GenerationIsolationError("stored Semantic execution receipt has invalid shape")
    typed = dict(payload)
    try:
        typed["request"] = ArtifactDigest.model_validate(typed.get("request"))
        typed["response"] = ArtifactDigest.model_validate(typed.get("response"))
        typed["adapter_identity"] = _semantic_identity_from_payload(
            typed.get("adapter_identity")
        )
        return SemanticExecutionReceipt(**typed)
    except (TypeError, ValueError) as exc:
        raise GenerationIsolationError("stored Semantic execution receipt is invalid") from exc


def _correction_identity_from_payload(payload: object) -> CorrectionModelIdentity:
    if not isinstance(payload, dict):
        raise GenerationIsolationError("stored Corrector execution identity has invalid shape")
    try:
        return CorrectionModelIdentity(**payload)
    except (TypeError, ValueError) as exc:
        raise GenerationIsolationError("stored Corrector execution identity is invalid") from exc


def _speech_coverage_identity_from_payload(
    payload: object,
) -> dict[str, str] | None:
    if payload is None:
        return None
    expected = {
        "adapter_name",
        "adapter_version",
        "config_hash",
        "code_hash",
        "runtime_hash",
    }
    if not isinstance(payload, dict) or set(payload) != expected:
        raise GenerationIsolationError(
            "stored Speech Coverage Analyzer identity has invalid shape"
        )
    if not all(isinstance(payload[key], str) for key in expected):
        raise GenerationIsolationError(
            "stored Speech Coverage Analyzer identity has invalid field types"
        )
    identity = {key: payload[key] for key in expected}
    if not identity["adapter_name"].strip() or not identity["adapter_version"].strip():
        raise GenerationIsolationError(
            "stored Speech Coverage Analyzer identity is blank"
        )
    if any(
        not re.fullmatch(r"[0-9a-f]{64}", identity[label])
        for label in ("config_hash", "code_hash", "runtime_hash")
    ):
        raise GenerationIsolationError(
            "stored Speech Coverage Analyzer identity hashes are invalid"
        )
    return identity


def _risk_payload(risk: RiskRecord) -> dict[str, object]:
    return {
        "issue": risk.issue.model_dump(mode="json"),
        "audio_span_ids": list(risk.audio_span_ids),
        "evidence_ids": list(risk.evidence_ids),
        "supporting_reference_ids": list(risk.supporting_reference_ids),
        "conflicting_reference_ids": list(risk.conflicting_reference_ids),
        "resolution_event_id": risk.resolution_event_id,
    }


def _risk_from_payload(payload: dict[str, object]) -> RiskRecord:
    return RiskRecord(
        issue=ReviewIssue.model_validate(payload["issue"]),
        audio_span_ids=tuple(payload.get("audio_span_ids", ())),
        evidence_ids=tuple(payload.get("evidence_ids", ())),
        supporting_reference_ids=tuple(payload.get("supporting_reference_ids", ())),
        conflicting_reference_ids=tuple(payload.get("conflicting_reference_ids", ())),
        resolution_event_id=payload.get("resolution_event_id"),
    )


_SPEECH_COVERAGE_RISK_CODES = frozenset(
    {
        "speech_coverage_receipt_missing",
        "speech_coverage_analyzer_failed",
        "uncovered_speech_activity",
        "recognition_outside_audio_activity",
    }
)


def _validate_review_risk_evidence_membership(
    *,
    transcript: CanonicalTranscript,
    risks: Sequence[RiskRecord],
    evidence: Sequence[RecognitionEvidence],
    references: Sequence[ReferenceEvidence],
    speech_coverage_receipt: SpeechCoverageReceipt | None,
    context: str,
) -> None:
    """Reject Review/Risk provenance that is not owned by current Evidence."""

    risk_tuple = tuple(risks)
    if tuple(risk.issue for risk in risk_tuple) != transcript.review_issues:
        raise GenerationIsolationError(
            f"{context} Review Issues differ from persisted RiskRecord issues"
        )

    recognition_ids = {
        f"evidence:{recognition_evidence_content_hash(item)}:{token.id}"
        for item in evidence
        for token in item.tokens
    }
    reference_ids = {item.id for item in references}
    speech_coverage_ids: set[str] = set()
    if speech_coverage_receipt is not None:
        speech_coverage_ids.add(
            "speech-coverage-receipt:"
            f"{speech_coverage_receipt_content_hash(speech_coverage_receipt)}"
        )
        speech_coverage_ids.update(
            interval.id
            for interval in (
                *speech_coverage_receipt.activity_intervals,
                *speech_coverage_receipt.uncovered_intervals,
            )
        )

    def allowed_audio_ids(code: str) -> set[str]:
        if code in _SPEECH_COVERAGE_RISK_CODES:
            return speech_coverage_ids
        return recognition_ids

    for issue in transcript.review_issues:
        unknown_audio = set(issue.audio_evidence_ids) - allowed_audio_ids(issue.code)
        unknown_references = set(issue.reference_evidence_ids) - reference_ids
        if unknown_audio or unknown_references:
            raise GenerationIsolationError(
                f"{context} ReviewIssue carries orphan current-generation Evidence IDs: "
                f"audio={sorted(unknown_audio)!r}, references={sorted(unknown_references)!r}"
            )

    for risk in risk_tuple:
        unknown_evidence = set(risk.evidence_ids) - allowed_audio_ids(risk.issue.code)
        unknown_references = (
            set(risk.supporting_reference_ids)
            | set(risk.conflicting_reference_ids)
        ) - reference_ids
        if unknown_evidence or unknown_references:
            raise GenerationIsolationError(
                f"{context} RiskRecord carries orphan current-generation Evidence IDs: "
                f"audio={sorted(unknown_evidence)!r}, "
                f"references={sorted(unknown_references)!r}"
            )


@dataclass(frozen=True, slots=True)
class _TargetedCorrectionAuditReceipt:
    schema_version: int
    mode: Literal["targeted_review"]
    generation_id: str
    canonical_content_hash: str
    evidence_hash: str
    policy_hash: str
    reference_evidence_hash: str
    available_reference_evidence_ids: tuple[str, ...]
    available_reference_evidence_hash: str
    presented_reference_evidence_ids: tuple[str, ...]
    presented_reference_evidence_hash: str
    work_packet_id: str
    request: ArtifactDigest
    request_hash: str
    target_span_ids: tuple[str, ...]
    response: ArtifactDigest
    response_hash: str
    adapter_name: str
    adapter_version: str
    model: str
    model_version: str
    runtime_hash: str
    adapter_code_hash: str
    config_hash: str
    execution_mode: Literal["fixture", "local", "paid_api", "subscription", "other"]
    adapter_identity_hash: str
    proposal_ids: tuple[str, ...]
    proposal_set_hash: str

    def __post_init__(self) -> None:
        for field_name in (
            "presented_reference_evidence_ids",
            "available_reference_evidence_ids",
            "target_span_ids",
            "proposal_ids",
        ):
            object.__setattr__(self, field_name, tuple(getattr(self, field_name)))
        if isinstance(self.request, dict):
            object.__setattr__(self, "request", ArtifactDigest.model_validate(self.request))
        if isinstance(self.response, dict):
            object.__setattr__(self, "response", ArtifactDigest.model_validate(self.response))
        identity = CorrectionModelIdentity(
            adapter_name=self.adapter_name,
            adapter_version=self.adapter_version,
            model=self.model,
            model_version=self.model_version,
            runtime_hash=self.runtime_hash,
            adapter_code_hash=self.adapter_code_hash,
            config_hash=self.config_hash,
            execution_mode=self.execution_mode,
        )
        if self.adapter_identity_hash != identity.content_hash:
            raise ValueError("targeted Corrector Adapter identity hash mismatch")
        for label, artifact, digest in (
            ("request", self.request, self.request_hash),
            ("response", self.response, self.response_hash),
        ):
            if (
                not artifact.uri.startswith(f"generation-artifact://correction/{label}s/")
                or artifact.sha256 != digest
            ):
                raise ValueError(f"targeted Corrector {label} artifact mismatch")


def _is_material_issue(
    transcript: CanonicalTranscript,
    issue: ReviewIssue,
) -> bool:
    severities = set(_MATERIAL_SEVERITIES)
    if not transcript.acceptance_policy.permit_unresolved_low_risk:
        severities.add("low")
    return issue.status == "unresolved" and issue.severity in severities


def _material_issues(transcript: CanonicalTranscript) -> tuple[ReviewIssue, ...]:
    return tuple(
        issue
        for issue in transcript.review_issues
        if _is_material_issue(transcript, issue)
    )


def _proposal_review_issue_id(proposal: CorrectionProposal) -> str:
    return "issue-" + hash_object(
        {
            "code": "correction_proposal",
            "proposal_id": proposal.id,
            "span_ids": proposal.audio_span_ids,
            "candidate": proposal.candidate_text,
        }
    )


def _audio_audit_review_issue_id(receipt: AudioAuditReceipt) -> str:
    return "issue-" + hash_object(
        {
            "code": "audio_audit_unresolved",
            "receipt_id": receipt.id,
            "span_ids": receipt.target_span_ids,
        }
    )


def _derive_correction_preaudit_transcript(
    transcript: CanonicalTranscript,
    *,
    proposals: Sequence[CorrectionProposal],
    remove_proposal_ids: Sequence[str],
    audio_audits: Sequence[AudioAuditReceipt],
) -> CanonicalTranscript:
    """Reverse only this Generation's audit-derived state for typed replay.

    Text and audio adapters run before proposal risks and audio attestation are
    attached.  The immutable Generation stores the post-audit transcript, so a
    restart must mechanically derive that exact earlier request state rather
    than trusting fields parsed from the stored request bytes.
    """

    proposal_by_id = {proposal.id: proposal for proposal in proposals}
    if len(proposal_by_id) != len(tuple(proposals)):
        raise GenerationIsolationError(
            "Correction replay cannot derive a pre-audit transcript from duplicate Proposals"
        )
    try:
        removed_proposals = tuple(proposal_by_id[item] for item in remove_proposal_ids)
    except KeyError as exc:
        raise GenerationIsolationError(
            f"Correction replay receipt cites unknown Proposal {exc.args[0]!r}"
        ) from exc
    removed_issue_ids = {
        _proposal_review_issue_id(proposal) for proposal in removed_proposals
    }
    removed_issue_ids.update(
        _audio_audit_review_issue_id(receipt)
        for receipt in audio_audits
        if receipt.status == "unresolved"
    )
    review_issues = tuple(
        issue for issue in transcript.review_issues if issue.id not in removed_issue_ids
    )
    warnings = tuple(
        warning.model_copy(
            update={
                "status": "requires_full_audit",
                "mitigation_receipt_set_hash": None,
            }
        )
        if warning.status == "mitigated"
        else warning
        for warning in transcript.generation_warnings
    )
    preaudit = transcript.model_copy(
        update={
            "generation_id": "generation-pending-correction-replay",
            "status": "draft",
            "review_issues": review_issues,
            "generation_warnings": warnings,
            "full_audit_receipt_set_hash": None,
        }
    )
    preaudit = preaudit.model_copy(
        update={"generation_id": canonical_generation_id(preaudit)}
    )
    _validate_canonical_acceptance_identity(preaudit)
    return preaudit


def _derive_audio_preaudit_transcript(
    transcript: CanonicalTranscript,
    *,
    proposals: Sequence[CorrectionProposal],
    audio_audits: Sequence[AudioAuditReceipt],
) -> CanonicalTranscript:
    """Rebuild the exact post-text/pre-audio child truth for typed replay."""

    audio_proposal_ids = tuple(
        proposal_id for receipt in audio_audits for proposal_id in receipt.proposal_ids
    )
    proposal_by_id = {proposal.id: proposal for proposal in proposals}
    try:
        audio_proposals = tuple(proposal_by_id[item] for item in audio_proposal_ids)
    except KeyError as exc:
        raise GenerationIsolationError(
            f"Audio replay receipt cites unknown Proposal {exc.args[0]!r}"
        ) from exc
    removed_issue_ids = {
        _proposal_review_issue_id(proposal) for proposal in audio_proposals
    }
    removed_issue_ids.update(
        _audio_audit_review_issue_id(receipt)
        for receipt in audio_audits
        if receipt.status == "unresolved"
    )
    review_issues = tuple(
        issue for issue in transcript.review_issues if issue.id not in removed_issue_ids
    )
    warnings = tuple(
        warning.model_copy(
            update={
                "status": "requires_full_audit",
                "mitigation_receipt_set_hash": None,
            }
        )
        if warning.status == "mitigated"
        else warning
        for warning in transcript.generation_warnings
    )
    preaudit = transcript.model_copy(
        update={
            "generation_id": "generation-pending-audio-replay",
            "status": "draft",
            "review_issues": review_issues,
            "generation_warnings": warnings,
            "full_audit_receipt_set_hash": None,
        }
    )
    preaudit = preaudit.model_copy(
        update={"generation_id": canonical_generation_id(preaudit)}
    )
    _validate_canonical_acceptance_identity(preaudit)
    return preaudit


def _validate_canonical_acceptance_identity(transcript: CanonicalTranscript) -> None:
    pending_warning = any(
        warning.status == "requires_full_audit"
        for warning in transcript.generation_warnings
    )
    expected_status = (
        "draft"
        if _material_issues(transcript)
        or pending_warning
        or transcript.full_audit_receipt_set_hash is None
        or transcript.verified_speech_coverage_receipt_hash is None
        else "accepted"
    )
    if transcript.status != expected_status:
        raise GenerationIsolationError(
            "Canonical Transcript status differs from its persisted acceptance policy"
        )
    if transcript.generation_id != canonical_generation_id(transcript):
        raise GenerationIsolationError(
            "Canonical Transcript Generation identity does not match its sealed policy/content"
        )


def _validate_acceptance_policy_not_rewritten(
    transcript: CanonicalTranscript,
) -> None:
    """Recognize the one-field schema-v1 policy rewrite before other proof replay.

    Other coordinated transcript tampering is intentionally diagnosed by its
    narrower timing/Evidence/audit gate first.  The persisted acceptance rule
    has only one Boolean in schema v1, so toggling it and resealing status is a
    closed, deterministic test rather than a guess about an opaque policy hash.
    """

    if transcript.generation_id == canonical_generation_id(transcript):
        return
    alternate_policy = transcript.acceptance_policy.model_copy(
        update={
            "permit_unresolved_low_risk": not (
                transcript.acceptance_policy.permit_unresolved_low_risk
            )
        }
    )
    alternate = transcript.model_copy(update={"acceptance_policy": alternate_policy})
    pending_warning = any(
        warning.status == "requires_full_audit"
        for warning in alternate.generation_warnings
    )
    expected_status = (
        "draft"
        if _material_issues(alternate)
        or pending_warning
        or alternate.full_audit_receipt_set_hash is None
        or alternate.verified_speech_coverage_receipt_hash is None
        else "accepted"
    )
    alternate = alternate.model_copy(update={"status": expected_status})
    if canonical_generation_id(alternate) == transcript.generation_id:
        raise GenerationIsolationError(
            "Canonical Transcript Generation identity does not match its sealed policy/content"
        )


def _full_audit_windows(
    transcript: CanonicalTranscript,
    policy: SubtitlePolicy,
) -> tuple[tuple[str, ...], ...]:
    """Partition every span exactly once without cutting a material issue."""

    spans = transcript.spans
    positions = {span.id: index for index, span in enumerate(spans)}
    forbidden_cuts: set[int] = set()
    for issue in _material_issues(transcript):
        # Coverage issues are audio-presence gates, not one atomic text edit.
        # They may anchor the first/last transcript boundaries yet must not
        # force a whole episode into one Corrector/AudioAuditor request.
        if issue.code in _COVERAGE_GATE_CODES:
            continue
        issue_positions = sorted(positions[span_id] for span_id in issue.span_ids)
        forbidden_cuts.update(range(issue_positions[0] + 1, issue_positions[-1] + 1))

    windows: list[tuple[str, ...]] = []
    start = 0
    while start < len(spans):
        end = start
        token_count = 0
        while end < len(spans) and end - start < policy.full_audit_max_spans_per_request:
            next_count = len(spans[end].token_ids)
            if token_count + next_count > policy.full_audit_max_tokens_per_request:
                break
            token_count += next_count
            end += 1
        if end == start:
            raise ModuleInvariantError(
                f"Canonical span {spans[start].id!r} exceeds the full-audit token window"
            )
        safe_end = end
        while safe_end > start and safe_end in forbidden_cuts:
            safe_end -= 1
        if safe_end == start:
            raise ModuleInvariantError(
                "one material Review Issue exceeds the versioned full-audit window"
            )
        windows.append(tuple(span.id for span in spans[start:safe_end]))
        start = safe_end

    flattened = tuple(span_id for window in windows for span_id in window)
    expected = tuple(span.id for span in spans)
    if flattened != expected or len(set(flattened)) != len(flattened):
        raise ModuleInvariantError("full-audit windows must partition every Canonical span once")
    return tuple(windows)


def _single_span_audit_windows(
    transcript: CanonicalTranscript,
) -> tuple[tuple[str, ...], ...]:
    """Return a policy-independent, complete partition for child re-audit.

    Resolve does not receive the original ``SubtitlePolicy`` object, only its
    immutable hash.  One Canonical span per request is therefore the smallest
    deterministic window that cannot silently exceed a persisted span-count
    limit or leave a child span unaudited.
    """

    windows = tuple((span.id,) for span in transcript.spans)
    if not windows:
        raise ModuleInvariantError("child audio re-audit requires Canonical spans")
    return windows


def _validate_audio_full_audit_attestation(
    transcript: CanonicalTranscript,
    receipts: Sequence[AudioAuditReceipt],
    identity: AudioModelIdentity,
    *,
    preaudit_transcript: CanonicalTranscript | None = None,
) -> None:
    """Revalidate one complete current-content audio attestation.

    This is intentionally run both before commit and after a fresh load.  A
    hash-consistent receipt set is insufficient when the receipts cover a
    parent revision or omit/repeat child spans.
    """

    receipt_tuple = tuple(receipts)
    preaudit = preaudit_transcript or transcript
    audited_span_ids = tuple(
        span_id for receipt in receipt_tuple for span_id in receipt.target_span_ids
    )
    expected_span_ids = tuple(span.id for span in transcript.spans)
    if (
        not receipt_tuple
        or audited_span_ids != expected_span_ids
        or len(set(audited_span_ids)) != len(audited_span_ids)
    ):
        raise GenerationIsolationError(
            "audio full-audit receipts must exactly partition current Canonical spans"
        )
    if transcript.full_audit_receipt_set_hash != audio_audit_receipt_set_hash(
        list(receipt_tuple)
    ):
        raise GenerationIsolationError(
            "Canonical Transcript audio full-audit receipt set hash mismatch"
        )

    span_by_id = {span.id: span for span in transcript.spans}
    preaudit_generation_ids: set[str] = set()
    for receipt in receipt_tuple:
        selected = tuple(span_by_id[span_id] for span_id in receipt.target_span_ids)
        if (
            receipt.episode_id != preaudit.episode_id
            or receipt.preaudit_generation_id != preaudit.generation_id
            or receipt.preaudit_content_hash != preaudit.content_hash
            or receipt.evidence_hash != preaudit.evidence_hash
            or receipt.reference_evidence_hash != preaudit.reference_evidence_hash
            or receipt.policy_hash != preaudit.policy_hash
            or receipt.normalized_audio_hash != preaudit.normalized_audio_hash
            or receipt.adapter_identity_hash != identity.content_hash
            or receipt.adapter_name != identity.adapter_name
            or receipt.adapter_version != identity.adapter_version
            or receipt.model != identity.model
            or receipt.model_version != identity.model_version
            or receipt.runtime_hash != identity.runtime_hash
            or receipt.adapter_code_hash != identity.adapter_code_hash
            or receipt.config_hash != identity.config_hash
        ):
            raise GenerationIsolationError(
                "stored Audio Audit Receipt crossed current immutable truth or "
                "Adapter identity"
            )
        if (
            receipt.window_start_ms != selected[0].start_ms
            or receipt.window_end_ms != selected[-1].end_ms
        ):
            raise GenerationIsolationError(
                "stored Audio Audit Receipt window differs from current Canonical spans"
            )
        preaudit_generation_ids.add(receipt.preaudit_generation_id)
    if len(preaudit_generation_ids) != 1:
        raise GenerationIsolationError(
            "one audio full audit cannot mix pre-audit Generations"
        )


def _validate_text_full_audit_attestation(
    preaudit_transcript: CanonicalTranscript,
    receipts: Sequence[CorrectionAuditReceipt],
    identity: CorrectionModelIdentity,
    references: Sequence[ReferenceEvidence],
    retrievals: Sequence[ReferenceRetrievalReceipt],
) -> None:
    """Require one exact, ordered, current-child text audit partition.

    Proposal hashes alone cannot prove that every Canonical span was presented
    to the Corrector.  This verifier is deliberately independent from replay
    so commit and fresh load reject missing/reordered windows before trusting
    provider artifacts.
    """

    receipt_tuple = tuple(receipts)
    audited_span_ids = tuple(
        span_id for receipt in receipt_tuple for span_id in receipt.target_span_ids
    )
    expected_span_ids = tuple(span.id for span in preaudit_transcript.spans)
    if (
        not receipt_tuple
        or audited_span_ids != expected_span_ids
        or len(set(audited_span_ids)) != len(audited_span_ids)
    ):
        raise GenerationIsolationError(
            "text full-audit receipts must exactly partition current Canonical spans"
        )
    reference_ids = tuple(item.id for item in references)
    reference_hash = reference_evidence_set_hash(tuple(references))
    references_by_span = _reference_evidence_by_span(references, retrievals)
    span_by_id = {span.id: span for span in preaudit_transcript.spans}
    for receipt in receipt_tuple:
        presented: dict[str, ReferenceEvidence] = {}
        for span_id in receipt.target_span_ids:
            span = span_by_id[span_id]
            for lineage_span_id in (span.id, *span.lineage):
                for item in references_by_span.get(lineage_span_id, ()):
                    presented[item.id] = item
        expected_presented = tuple(sorted(presented.values(), key=lambda item: item.id))
        if (
            receipt.episode_id != preaudit_transcript.episode_id
            or receipt.preaudit_generation_id != preaudit_transcript.generation_id
            or receipt.preaudit_content_hash != preaudit_transcript.content_hash
            or receipt.evidence_hash != preaudit_transcript.evidence_hash
            or receipt.reference_evidence_hash
            != preaudit_transcript.reference_evidence_hash
            or receipt.available_reference_evidence_ids != reference_ids
            or receipt.available_reference_evidence_hash != reference_hash
            or receipt.presented_reference_evidence_ids
            != tuple(item.id for item in expected_presented)
            or receipt.presented_reference_evidence_hash
            != reference_evidence_set_hash(expected_presented)
            or receipt.policy_hash != preaudit_transcript.policy_hash
            or receipt.adapter_identity_hash != identity.content_hash
            or receipt.adapter_name != identity.adapter_name
            or receipt.adapter_version != identity.adapter_version
            or receipt.model != identity.model
            or receipt.model_version != identity.model_version
            or receipt.runtime_hash != identity.runtime_hash
            or receipt.adapter_code_hash != identity.adapter_code_hash
            or receipt.config_hash != identity.config_hash
            or receipt.execution_mode != identity.execution_mode
        ):
            raise GenerationIsolationError(
                "stored text full-audit receipt crossed its immutable pre-audit "
                "transcript, Reference Evidence, policy, or Adapter identity"
            )


def _validate_proposal_ownership(
    proposals: Sequence[CorrectionProposal],
    correction_audits: Sequence[CorrectionAuditReceipt],
    audio_audits: Sequence[AudioAuditReceipt],
    arbitration_receipts: Sequence[ArbitrationReceipt],
    *,
    require_arbitration: bool,
) -> None:
    """Bind stored proposals exactly to this Generation's producing receipts."""

    proposal_tuple = tuple(proposals)
    proposal_ids = tuple(proposal.id for proposal in proposal_tuple)
    if len(set(proposal_ids)) != len(proposal_ids):
        raise GenerationIsolationError("stored Correction Proposal IDs are not unique")
    if _merge_proposal_batches((proposal_tuple,)) != proposal_tuple:
        raise GenerationIsolationError(
            "stored Correction Proposals do not form one unambiguous ordered set"
        )
    text_ids = tuple(
        proposal_id for receipt in correction_audits for proposal_id in receipt.proposal_ids
    )
    audio_ids = tuple(
        proposal_id for receipt in audio_audits for proposal_id in receipt.proposal_ids
    )
    owned_ids = (*text_ids, *audio_ids)
    if len(set(owned_ids)) != len(owned_ids) or tuple(owned_ids) != proposal_ids:
        raise GenerationIsolationError(
            "stored Correction Proposals differ from the exact text/audio audit ownership union"
        )
    by_id = {proposal.id: proposal for proposal in proposal_tuple}
    for receipt in correction_audits:
        audited = tuple(by_id[item] for item in receipt.proposal_ids)
        if receipt.proposal_set_hash != hash_object(audited) or any(
            proposal.evidence_basis
            not in {"recognition", "recognition_and_reference", "reference_only"}
            for proposal in audited
        ):
            raise GenerationIsolationError(
                "text audit proposal ownership or evidence basis mismatch"
            )
    for receipt in audio_audits:
        audited = tuple(by_id[item] for item in receipt.proposal_ids)
        if receipt.proposal_set_hash != hash_object(audited) or any(
            proposal.evidence_basis not in {"audio", "audio_and_reference"}
            for proposal in audited
        ):
            raise GenerationIsolationError(
                "audio audit proposal ownership or evidence basis mismatch"
            )
    arbitration_ids = tuple(receipt.proposal_id for receipt in arbitration_receipts)
    if len(set(arbitration_ids)) != len(arbitration_ids):
        raise GenerationIsolationError("stored Arbitration Receipts duplicate a Proposal")
    expected_arbitration_ids = proposal_ids if require_arbitration else ()
    if arbitration_ids != expected_arbitration_ids:
        raise GenerationIsolationError(
            "stored Arbitration Receipts do not bind exactly one verdict per child Proposal"
        )


def _validate_canonical_timing_provenance(
    transcript: CanonicalTranscript,
    evidence: Sequence[RecognitionEvidence],
    orthographic_projections: Sequence[OrthographicProjectionEvidenceV1] = (),
) -> None:
    """Allow exact clocks only when they replay from root Recognition Evidence.

    Replacement exact timing is feature-gated until a typed, replayable
    ``ReplacementAlignmentReceipt`` exists.  Under schema_version=1, generic
    Recognition/Audio Evidence IDs cannot authorize a replacement clock.
    Running this check before commit and on every fresh load prevents callers
    from relabelling those IDs as forced or human alignment provenance.
    """

    evidence_tuple = tuple(evidence)
    projection_tuple = tuple(orthographic_projections)
    if transcript.orthographic_projection_evidence_hash is None:
        if projection_tuple:
            raise GenerationIsolationError(
                "Canonical Transcript omits supplied Orthographic Projection lineage"
            )
        projected_text_by_ref: dict[str, str] = {}
    else:
        try:
            projection_hash = orthographic_projection_evidence_set_hash(projection_tuple)
            for projection in projection_tuple:
                verify_orthographic_projection(projection, evidence_tuple)
        except (OrthographicProjectionError, ValueError) as exc:
            raise GenerationIsolationError(
                "Canonical Orthographic Projection Evidence is not reproducible"
            ) from exc
        if projection_hash != transcript.orthographic_projection_evidence_hash:
            raise GenerationIsolationError(
                "Canonical Orthographic Projection Evidence hash mismatch"
            )
        projected_text_by_ref = {
            (
                f"evidence:{projection.source_recognition_evidence_hash}:"
                f"{token.source_token_id}"
            ): token.projected_text
            for projection in projection_tuple
            for token in projection.tokens
        }

    evidence_tokens: dict[str, tuple[str, int, int, str | None]] = {}
    for item in evidence:
        evidence_hash = recognition_evidence_content_hash(item)
        for token in item.tokens:
            ref = f"evidence:{evidence_hash}:{token.id}"
            if ref in evidence_tokens:
                raise GenerationIsolationError(
                    "Recognition Evidence token reference is not globally unique"
                )
            evidence_tokens[ref] = (
                projected_text_by_ref.get(ref, token.text),
                token.start_ms,
                token.end_ms,
                token.speaker,
            )

    def root_span_id(start_ms: int, end_ms: int) -> str:
        identity = {
            "normalized_audio_hash": transcript.normalized_audio_hash,
            "start_ms": start_ms,
            "end_ms": end_ms,
        }
        return f"audio-span-{hash_object(identity)}"

    token_by_id = {token.id: token for token in transcript.tokens}
    for token in transcript.tokens:
        if token.timing_basis in {"forced_alignment", "human_alignment"}:
            raise GenerationIsolationError(
                "replacement exact timing is disabled until a typed "
                "ReplacementAlignmentReceipt is implemented"
            )
        if token.timing_basis == "coarse_span":
            continue
        if token.timing_basis != "recognition":  # pragma: no cover - closed schema
            raise GenerationIsolationError("Canonical token has unsupported timing provenance")
        if token.start_ms is None or token.end_ms is None:  # schema defense in depth
            raise GenerationIsolationError("Recognition-timed Canonical token lacks a clock")
        refs = set(token.alignment_evidence_ids)
        if (
            not refs
            or not refs.issubset(evidence_tokens)
            or not refs.issubset(token.evidence_ids)
        ):
            raise GenerationIsolationError(
                "Recognition-timed Canonical token lacks exact Recognition Evidence lineage"
            )
        canonical_observation = (
            token.text,
            token.start_ms,
            token.end_ms,
            token.speaker,
        )
        if not any(evidence_tokens[ref] == canonical_observation for ref in refs):
            raise GenerationIsolationError(
                "Recognition-timed Canonical token clock/text is not reproduced by Evidence"
            )
        expected_span_id = root_span_id(token.start_ms, token.end_ms)
        expected_token_id = f"canonical-{expected_span_id.removeprefix('audio-span-')}"
        if token.id != expected_token_id:
            raise GenerationIsolationError(
                "Recognition-timed Canonical token has a non-Recognition identity"
            )

    for span in transcript.spans:
        span_tokens = tuple(token_by_id[token_id] for token_id in span.token_ids)
        if span.alignment == "coarse":
            if span.alignment_evidence_ids:
                raise GenerationIsolationError(
                    "coarse Canonical span cannot claim exact alignment Evidence"
                )
            continue
        if len(span_tokens) != 1 or span_tokens[0].timing_basis != "recognition":
            raise GenerationIsolationError(
                "lexeme-exact Canonical span is not a root Recognition span"
            )
        token = span_tokens[0]
        assert token.start_ms is not None and token.end_ms is not None
        if (
            span.id != root_span_id(token.start_ms, token.end_ms)
            or span.alignment_evidence_ids != token.alignment_evidence_ids
        ):
            raise GenerationIsolationError(
                "lexeme-exact Canonical span does not replay Recognition provenance"
            )


def _merge_execution_artifacts(
    retained: Mapping[str, bytes],
    added: Mapping[str, bytes],
) -> dict[str, bytes]:
    merged = dict(retained)
    for name, payload in added.items():
        existing = merged.get(name)
        if existing is not None and existing != payload:
            raise GenerationIsolationError(
                "audio execution artifact digest has conflicting bytes"
            )
        merged[name] = payload
    return merged


def _derive_resolution_with_child_audit(
    parent: CanonicalBuildResult,
    decision: CorrectionDecision,
    *,
    ledger_hash: str,
    child_correction_audits: Sequence[CorrectionAuditReceipt],
    child_audio_audits: Sequence[AudioAuditReceipt],
    child_proposals: Sequence[CorrectionProposal],
    child_retrievals: Sequence[ReferenceRetrievalReceipt] = (),
) -> CanonicalBuildResult:
    """Replay a Decision and this child's complete staged audit state."""

    derived = derive_canonical_resolution(
        parent,
        decision,
        ledger_hash=ledger_hash,
    )
    retrieval_failure_risks = _reference_retrieval_failure_risks(child_retrievals)
    if retrieval_failure_risks:
        derived = add_review_risks(derived, retrieval_failure_risks)
    proposal_by_id = {proposal.id: proposal for proposal in child_proposals}
    child_text_proposal_ids = tuple(
        proposal_id
        for audit in child_correction_audits
        for proposal_id in audit.proposal_ids
    )
    child_audio_proposal_ids = tuple(
        proposal_id
        for audit in child_audio_audits
        for proposal_id in audit.proposal_ids
    )
    if (*child_text_proposal_ids, *child_audio_proposal_ids) != tuple(
        proposal_by_id
    ):
        raise GenerationIsolationError(
            "child proposal set differs from its text/audio audit ownership"
        )

    def attach(proposal_ids: tuple[str, ...]) -> None:
        nonlocal derived
        if not proposal_ids:
            return
        audited_proposals = tuple(proposal_by_id[item] for item in proposal_ids)
        derived = add_correction_proposals(
            derived,
            audited_proposals,
            allowed_reference_ids=tuple(
                sorted(
                    {
                        evidence_id
                        for proposal in audited_proposals
                        for evidence_id in proposal.reference_evidence_ids
                    }
                )
            ),
        )

    attach(child_text_proposal_ids)
    derived = attest_full_audit(derived, child_audio_audits)
    attach(child_audio_proposal_ids)
    return derived


def _merge_proposal_batches(
    batches: Sequence[Sequence[CorrectionProposal]],
) -> tuple[CorrectionProposal, ...]:
    """Deduplicate exact retries and reject ambiguous overlapping proposals."""

    by_id: dict[str, CorrectionProposal] = {}
    span_owner: dict[str, CorrectionProposal] = {}
    ordered: list[CorrectionProposal] = []
    for batch in batches:
        for proposal in batch:
            existing = by_id.get(proposal.id)
            if existing is not None:
                if existing != proposal:
                    raise GenerationIsolationError(
                        f"Correction Proposal ID {proposal.id!r} has conflicting content"
                    )
                continue
            conflicts = {
                span_owner[span_id].id
                for span_id in proposal.audio_span_ids
                if span_id in span_owner and span_owner[span_id] != proposal
            }
            if conflicts:
                raise ModuleInvariantError(
                    "overlapping Correction Proposals require explicit arbitration: "
                    f"{sorted(conflicts | {proposal.id})!r}"
                )
            by_id[proposal.id] = proposal
            ordered.append(proposal)
            for span_id in proposal.audio_span_ids:
                span_owner[span_id] = proposal
    return tuple(ordered)


def _validate_semantic_partition(
    transcript: CanonicalTranscript,
    units: Sequence[SemanticUnit],
) -> tuple[SemanticUnit, ...]:
    typed = tuple(units)
    positions = {token.id: index for index, token in enumerate(transcript.tokens)}
    unit_ids: set[str] = set()
    unit_ranges: set[tuple[str, ...]] = set()
    covered: set[str] = set()
    boundary_overlap = [0] * (len(transcript.tokens) + 1)
    for unit in typed:
        if unit.id in unit_ids:
            raise ModuleInvariantError(f"duplicate Semantic Unit ID {unit.id!r}")
        unit_ids.add(unit.id)
        try:
            indices = [positions[token_id] for token_id in unit.token_ids]
        except KeyError as exc:
            raise ModuleInvariantError(
                f"Semantic Unit references unknown canonical token {exc.args[0]!r}"
            ) from exc
        if indices != list(range(indices[0], indices[-1] + 1)):
            raise ModuleInvariantError(f"Semantic Unit {unit.id!r} is not contiguous")
        unit_range = tuple(unit.token_ids)
        if unit_range in unit_ranges:
            raise ModuleInvariantError(
                f"duplicate Semantic Unit token range {unit_range!r}"
            )
        unit_ranges.add(unit_range)
        if unit.kind in {"name", "term"} and not unit.forbid_cue_breaks:
            raise ModuleInvariantError(
                f"Semantic Unit {unit.id!r} must keep a {unit.kind} in one cue"
            )
        for cut in range(indices[0] + 1, indices[-1] + 1):
            boundary_overlap[cut] += 1
            if boundary_overlap[cut] > 8:
                raise ModuleInvariantError(
                    "Semantic Units exceed the bounded overlap budget at "
                    f"Canonical boundary {cut}"
                )
        covered.update(unit.token_ids)
    if not typed:
        raise ModuleInvariantError("Semantic Analyzer must return at least one Semantic Unit")
    missing = set(positions) - covered
    if missing:
        raise ModuleInvariantError(
            f"Semantic Units do not cover canonical tokens: {sorted(missing)!r}"
        )
    try:
        validate_semantic_non_degeneracy(transcript.tokens, typed)
    except BoundaryConstraintError as exc:
        raise ModuleInvariantError(str(exc)) from exc
    return typed


def _semantic_protected_ranges(
    transcript: CanonicalTranscript,
    retrieval_policy: ReferenceRetrievalPolicySnapshot,
    retrievals: Sequence[ReferenceRetrievalReceipt],
) -> tuple[ProtectedTokenRange, ...]:
    """Derive exact spoken ranges from policy and least-context retrieval metadata."""

    metadata: list[ProtectedTermMetadata] = [
        ProtectedTermMetadata(
            value=value,
            kind=classify_protected_kind(value),
            source="policy_vocabulary",
        )
        for value in retrieval_policy.vocabulary
    ]
    span_by_id = {span.id: span for span in transcript.spans}
    for receipt in retrievals:
        try:
            anchor = span_by_id[receipt.audio_span_id]
        except KeyError as exc:
            raise GenerationIsolationError(
                "Semantic reference metadata targets an unknown Canonical span"
            ) from exc
        hit_by_id = {hit.evidence_id: hit for hit in receipt.hits}
        for evidence in receipt.evidence:
            hit = hit_by_id.get(evidence.id)
            if (
                hit is None
                or hit.support_kind != "candidate_term_exact"
                or hit.candidate_term_index is None
            ):
                continue
            value = receipt.candidate_terms[hit.candidate_term_index]
            metadata.append(
                ProtectedTermMetadata(
                    value=value,
                    kind=classify_protected_kind(value),
                    source="retrieved_reference_metadata",
                    reference_evidence_ids=(evidence.id,),
                    scope_token_ids=anchor.token_ids,
                )
            )
    return derive_protected_token_ranges(transcript.tokens, tuple(metadata))


class PodcastSubtitleV2:
    """One high-leverage Interface over normalization, truth, and display."""

    def __init__(
        self,
        episode_root: str | Path,
        *,
        normalizer: Normalizer,
        recognizers: Sequence[Recognizer],
        semantic_analyzer: SemanticAnalyzer,
        semantic_analyzer_identity: AdapterIdentity,
        corrector: Corrector | None = None,
        audio_auditor: AudioAuditor | None = None,
        corrector_identity: AdapterIdentity | None = None,
        native_full_audit: NativeFullAuditBundle | None = None,
        speaker_attributor: SpeakerAttributor | None = None,
        speaker_attributor_identity: AdapterIdentity | None = None,
        reference_retriever: ReferenceRetriever | None = None,
        reference_retriever_identity: AdapterIdentity | None = None,
        reference_parser_registry: TrustedReferenceParserRegistry | None = None,
        arbiter: Arbiter | None = None,
        speech_coverage_analyzer: SpeechCoverageAnalyzer | None = None,
        recognition_independence_policy: RecognitionIndependencePolicyV1 | None = None,
        memo_boundary_authority_factory: (
            Callable[
                [RecognitionEvidence],
                MemoBoundaryAuthorityV1 | MemoSrtBoundaryAuthorityV1,
            ]
            | None
        ) = None,
        code_version: str = "podcast-subtitle-v2",
    ) -> None:
        if not recognizers:
            raise ValueError("PodcastSubtitleV2 requires at least one Recognizer Adapter")
        for recognizer in recognizers:
            instance_attributes = getattr(recognizer, "__dict__", {})
            if (
                not hasattr(type(recognizer), "identity")
                and "identity" not in instance_attributes
            ):
                raise ValueError(
                    "Recognizer must expose its measured executable "
                    "RecognitionModelIdentity"
                )
            if not callable(getattr(recognizer, "verify", None)):
                raise ValueError(
                    "Recognizer must expose a strict fresh-process Evidence verifier"
                )
        runtime_semantic_identity = getattr(semantic_analyzer, "identity", None)
        if not isinstance(runtime_semantic_identity, SemanticModelIdentity):
            raise ValueError(
                "Semantic Analyzer must expose its measured executable SemanticModelIdentity"
            )
        if (
            semantic_analyzer_identity.name != runtime_semantic_identity.adapter_name
            or semantic_analyzer_identity.version != runtime_semantic_identity.adapter_version
            or semantic_analyzer_identity.config_hash != runtime_semantic_identity.config_hash
            or semantic_analyzer_identity.execution_mode
            != runtime_semantic_identity.execution_mode
        ):
            raise ValueError(
                "configured Semantic Analyzer identity differs from the Adapter executable identity"
            )
        legacy_values = (corrector, audio_auditor, corrector_identity)
        if native_full_audit is not None:
            if any(value is not None for value in legacy_values) or arbiter is not None:
                raise ValueError(
                    "native Full Audit is mutually exclusive with legacy "
                    "Corrector, AudioAuditor, identity, and Arbiter"
                )
            runtime_corrector_identity = None
        else:
            if any(value is None for value in legacy_values):
                raise ValueError(
                    "legacy create requires Corrector, AudioAuditor, and Corrector identity"
                )
            assert corrector is not None
            assert corrector_identity is not None
            runtime_corrector_identity = getattr(corrector, "identity", None)
            if not isinstance(runtime_corrector_identity, CorrectionModelIdentity):
                raise ValueError(
                    "Corrector must expose its measured executable CorrectionModelIdentity"
                )
            if not callable(getattr(corrector, "replay", None)):
                raise ValueError(
                    "Corrector must expose a strict fresh-process execution replay verifier"
                )
            if (
                corrector_identity.name != runtime_corrector_identity.adapter_name
                or corrector_identity.version != runtime_corrector_identity.adapter_version
                or corrector_identity.config_hash != runtime_corrector_identity.config_hash
                or corrector_identity.execution_mode
                != runtime_corrector_identity.execution_mode
            ):
                raise ValueError(
                    "configured Corrector identity differs from the Adapter executable identity"
                )
        if (reference_retriever is None) != (reference_retriever_identity is None):
            raise ValueError(
                "Reference Retriever and its explicit Adapter identity must be configured together"
            )
        if (speaker_attributor is None) != (speaker_attributor_identity is None):
            raise ValueError(
                "Speaker Attributor and its explicit Adapter identity must be configured together"
            )
        if speaker_attributor is not None:
            assert speaker_attributor_identity is not None
            if (
                speaker_attributor_identity.name != speaker_attributor.adapter_name
                or speaker_attributor_identity.version
                != speaker_attributor.adapter_version
                or speaker_attributor_identity.config_hash
                != speaker_attributor.adapter_config_hash
            ):
                raise ValueError(
                    "configured Speaker Attributor identity differs from the Adapter runtime"
                )
        self.episode_root = Path(episode_root).resolve()
        self.store = GenerationStore(self.episode_root)
        self.ledger = CorrectionLedger(self.store.root / "ledger" / "events.ndjson")
        self._normalizer = normalizer
        self._recognizers = tuple(recognizers)
        self._recognition_independence_policy = recognition_independence_policy
        self._memo_boundary_authority_factory = memo_boundary_authority_factory
        self._semantic_analyzer = semantic_analyzer
        self._corrector = corrector
        self._audio_auditor = audio_auditor
        self._native_full_audit = native_full_audit
        self._arbiter = arbiter
        self._speech_coverage_analyzer = speech_coverage_analyzer
        self._speech_coverage_analyzer_identity = _speech_coverage_identity(
            speech_coverage_analyzer
        )
        self._corrector_identity = corrector_identity
        self._corrector_execution_identity = runtime_corrector_identity
        self._speaker_attributor = speaker_attributor
        self._speaker_attributor_identity = speaker_attributor_identity
        self._reference_retriever = reference_retriever
        self._reference_retriever_identity = reference_retriever_identity
        self._reference_parser_registry = reference_parser_registry
        self._semantic_analyzer_identity = semantic_analyzer_identity
        self._semantic_execution_identity = runtime_semantic_identity
        self._code_hash = hash_object({"implementation": code_version})
        # A process may have stopped after publishing a Ledger entry but before
        # moving the active pointer.  The authenticated journal contains enough
        # information to finish only that exact already-committed transition.
        if self.store.resolution_journal_exists():
            with self.store.episode_transaction():
                self._recover_resolution_transaction_locked()
        try:
            active_generation_id = self.store.active_generation_id()
        except GenerationNotFoundError:
            pass
        else:
            terminal = self.store.load_latest_create_checkpoint()
            if (
                terminal is not None
                and terminal.checkpoint.stage == "complete"
                and terminal.checkpoint.generation_id == active_generation_id
            ):
                self._load_terminal_create_checkpoint(
                    terminal,
                    require_active=True,
                )
            else:
                self._load_generation(active_generation_id, require_active=True)

    def assert_reference_composition(
        self,
        *,
        retriever: ReferenceRetriever,
        identity: AdapterIdentity,
        parser_registry: TrustedReferenceParserRegistry,
        index_hash: str,
    ) -> None:
        """Fail loudly unless a factory installed the CLI's exact frozen bundle."""

        runtime_index = getattr(retriever, "index", None)
        if (
            self._reference_retriever is not retriever
            or self._reference_retriever_identity != identity
            or self._reference_parser_registry is not parser_registry
            or runtime_index is None
            or getattr(runtime_index, "index_hash", None) != index_hash
        ):
            raise ModuleInvariantError(
                "composition factory did not install the exact enrolled Reference bundle"
            )

    def _create_checkpoint_adapter_identities(
        self,
        *,
        active_speaker_identity: AdapterIdentity | None,
    ) -> tuple[CheckpointAdapterIdentity, ...]:
        orthographic_identity = measure_opencc_s2tw_identity()
        identities = [
            _checkpoint_adapter_identity(self._normalizer, role="normalizer"),
            CheckpointAdapterIdentity(
                role="orthographic_projector",
                ordinal=0,
                adapter_name=orthographic_identity.implementation,
                adapter_version=orthographic_identity.implementation_version,
                config_hash=orthographic_identity.inventory_hash,
                code_hash=orthographic_identity.adapter_code_hash,
                runtime_hash=orthographic_identity.runtime_hash,
                execution_mode="local",
            ),
            *(
                _checkpoint_adapter_identity(
                    recognizer,
                    role="recognizer",
                    ordinal=index,
                )
                for index, recognizer in enumerate(self._recognizers)
            ),
            _checkpoint_adapter_identity(
                self._semantic_analyzer,
                role="semantic_analyzer",
                declared=self._semantic_analyzer_identity,
            ),
        ]
        if self._native_full_audit is None:
            assert self._corrector is not None
            assert self._audio_auditor is not None
            assert self._corrector_identity is not None
            identities.extend(
                (
                    _checkpoint_adapter_identity(
                        self._corrector,
                        role="corrector",
                        declared=self._corrector_identity,
                    ),
                    _checkpoint_adapter_identity(
                        self._audio_auditor,
                        role="audio_auditor",
                    ),
                )
            )
        else:
            identities.extend(
                (
                    _checkpoint_adapter_identity(
                        self._native_full_audit.text_executor,
                        role="native_text_full_audit",
                    ),
                    _checkpoint_adapter_identity(
                        self._native_full_audit.audio_executor,
                        role="native_audio_full_audit",
                    ),
                )
            )
        if self._speech_coverage_analyzer is not None:
            identities.append(
                _checkpoint_adapter_identity(
                    self._speech_coverage_analyzer,
                    role="speech_coverage_analyzer",
                )
            )
        if self._arbiter is not None:
            identities.append(_checkpoint_adapter_identity(self._arbiter, role="arbiter"))
        if self._reference_retriever is not None:
            identities.append(
                _checkpoint_adapter_identity(
                    self._reference_retriever,
                    role="reference_retriever",
                    declared=self._reference_retriever_identity,
                )
            )
        if self._speaker_attributor is not None and active_speaker_identity is not None:
            identities.append(
                _checkpoint_adapter_identity(
                    self._speaker_attributor,
                    role="speaker_attributor",
                    declared=active_speaker_identity,
                )
            )
        return tuple(sorted(identities, key=lambda item: (item.role, item.ordinal)))

    def _runtime_recognizer_identities(self) -> tuple[RecognitionModelIdentity, ...]:
        """Measure Recognizer runtimes only when create/load actually needs them.

        Keeping this out of construction lets a trusted composition root and
        ``--help`` remain import-safe: no torch/qwen import or GPU discovery is
        performed merely by assembling the Module.
        """

        identities: list[RecognitionModelIdentity] = []
        for recognizer in self._recognizers:
            identity = recognizer.identity
            if not isinstance(identity, RecognitionModelIdentity):
                raise ModuleInvariantError(
                    "Recognizer identity is not a RecognitionModelIdentity"
                )
            identities.append(identity)
        if len({identity.content_hash for identity in identities}) != len(identities):
            raise ModuleInvariantError("Recognizer executable identities must be unique")
        if self._recognition_independence_policy is not None:
            try:
                self._recognition_independence_policy.validate(tuple(identities))
            except ValueError as exc:
                raise ModuleInvariantError(
                    "Recognizer composition violates the production independence policy"
                ) from exc
        return tuple(identities)

    def _recognition_independence_receipt(
        self,
        identities: tuple[RecognitionModelIdentity, ...] | None = None,
    ) -> RecognitionIndependenceReceiptV1 | None:
        if self._recognition_independence_policy is None:
            return None
        runtime = identities or self._runtime_recognizer_identities()
        try:
            return self._recognition_independence_policy.validate(runtime)
        except ValueError as exc:
            raise ModuleInvariantError(
                "Recognizer composition violates the production independence policy"
            ) from exc

    def _commit_started_checkpoint(
        self,
        *,
        operation_key: str,
        input_binding_hash: str,
        request: CreateRequest,
        source: ArtifactDigest,
        effective_policy_hash: str,
        adapter_identities: tuple[CheckpointAdapterIdentity, ...],
        speaker_bindings: tuple[SpeakerTrackBinding, ...],
        enrolled_artifacts: tuple[ReferenceArtifact, ...],
    ) -> CreateCheckpoint:
        try:
            expected_active_generation_id = self.store.active_generation_id()
        except GenerationNotFoundError:
            expected_active_generation_id = None
        intent = {
            "schema_version": 1,
            "operation_key": operation_key,
            "episode_id": request.episode_id,
            "source": source.model_dump(mode="json"),
            "input_binding_hash": input_binding_hash,
            "policy_hash": effective_policy_hash,
            "code_hash": self._code_hash,
            "reference_enrollment_hash": hash_object(enrolled_artifacts),
            "speaker_track_binding_hash": hash_object(
                tuple(binding.portable_identity for binding in speaker_bindings)
            ),
            "adapter_identity_hash": hash_object(adapter_identities),
            "expected_active_generation_id": expected_active_generation_id,
        }
        payload = canonical_json_bytes(intent)
        checkpoint = build_create_checkpoint(
            operation_key=operation_key,
            episode_id=request.episode_id,
            stage="started",
            invocation_id=None,
            source=source,
            normalized=None,
            normalization_receipt_hash=None,
            input_binding_hash=input_binding_hash,
            policy_hash=effective_policy_hash,
            code_hash=self._code_hash,
            reference_enrollment_hash=hash_object(enrolled_artifacts),
            speaker_track_binding_hash=hash_object(
                tuple(binding.portable_identity for binding in speaker_bindings)
            ),
            adapter_identities=adapter_identities,
            artifact_hashes={_CHECKPOINT_CREATE_INTENT: sha256_bytes(payload)},
            expected_active_generation_id=expected_active_generation_id,
        )
        self.store.commit_create_checkpoint(
            checkpoint,
            artifacts={_CHECKPOINT_CREATE_INTENT: payload},
        )
        return checkpoint

    def _load_started_checkpoint(
        self,
        stored: StoredCreateCheckpoint,
        *,
        request: CreateRequest,
        source: ArtifactDigest,
        effective_policy_hash: str,
        input_binding_hash: str,
        adapter_identities: tuple[CheckpointAdapterIdentity, ...],
        speaker_bindings: tuple[SpeakerTrackBinding, ...],
        enrolled_artifacts: tuple[ReferenceArtifact, ...],
    ) -> CreateCheckpoint:
        checkpoint = stored.checkpoint
        if checkpoint.stage != "started":
            raise GenerationIsolationError("Create Checkpoint start lineage is invalid")
        expected = {
            "schema_version": 1,
            "operation_key": checkpoint.operation_key,
            "episode_id": request.episode_id,
            "source": source.model_dump(mode="json"),
            "input_binding_hash": input_binding_hash,
            "policy_hash": effective_policy_hash,
            "code_hash": self._code_hash,
            "reference_enrollment_hash": hash_object(enrolled_artifacts),
            "speaker_track_binding_hash": hash_object(
                tuple(binding.portable_identity for binding in speaker_bindings)
            ),
            "adapter_identity_hash": hash_object(adapter_identities),
            "expected_active_generation_id": checkpoint.expected_active_generation_id,
        }
        payload = self.store.read_create_checkpoint_artifact(
            stored, _CHECKPOINT_CREATE_INTENT
        )
        if (
            checkpoint.episode_id != request.episode_id
            or checkpoint.source != source
            or checkpoint.input_binding_hash != input_binding_hash
            or checkpoint.policy_hash != effective_policy_hash
            or checkpoint.code_hash != self._code_hash
            or checkpoint.reference_enrollment_hash != hash_object(enrolled_artifacts)
            or checkpoint.speaker_track_binding_hash
            != hash_object(tuple(item.portable_identity for item in speaker_bindings))
            or checkpoint.adapter_identities != adapter_identities
            or checkpoint.artifact_hashes
            != {_CHECKPOINT_CREATE_INTENT: sha256_bytes(payload)}
            or payload != canonical_json_bytes(expected)
        ):
            raise GenerationIsolationError(
                "Create Checkpoint binding differs from the current exact request"
            )
        return checkpoint

    @staticmethod
    def _assert_checkpoint_inherits(
        child: CreateCheckpoint,
        parent: CreateCheckpoint,
        *,
        expected_child_stage: str,
        expected_parent_stage: str,
    ) -> None:
        if child.stage != expected_child_stage or parent.stage != expected_parent_stage:
            raise GenerationIsolationError("Create Checkpoint stage lineage is invalid")
        if child.previous_checkpoint_id != parent.id:
            raise GenerationIsolationError("Create Checkpoint predecessor binding changed")
        inherited = (
            "operation_key",
            "episode_id",
            "source",
            "input_binding_hash",
            "policy_hash",
            "code_hash",
            "reference_enrollment_hash",
            "speaker_track_binding_hash",
            "adapter_identities",
            "expected_active_generation_id",
        )
        if any(getattr(child, name) != getattr(parent, name) for name in inherited):
            raise GenerationIsolationError("Create Checkpoint inherited identity changed")

    def _terminal_checkpoint_lineage(
        self, stored: StoredCreateCheckpoint
    ) -> tuple[StoredCreateCheckpoint, ...]:
        complete = stored.checkpoint
        if complete.stage != "complete" or complete.previous_checkpoint_id is None:
            raise GenerationIsolationError("terminal Create Checkpoint is not complete")
        audio = self.store.load_create_checkpoint_id(complete.previous_checkpoint_id)
        if audio.checkpoint.stage == "native_audio_audit_ready":
            if audio.checkpoint.previous_checkpoint_id is None:
                raise GenerationIsolationError("Native Audio Checkpoint lacks predecessor")
            text = self.store.load_create_checkpoint_id(
                audio.checkpoint.previous_checkpoint_id
            )
            if text.checkpoint.previous_checkpoint_id is None:
                raise GenerationIsolationError("Native Text Checkpoint lacks predecessor")
            basis = self.store.load_create_checkpoint_id(
                text.checkpoint.previous_checkpoint_id
            )
            if basis.checkpoint.previous_checkpoint_id is None:
                raise GenerationIsolationError("Native Audit Basis lacks predecessor")
            evidence = self.store.load_create_checkpoint_id(
                basis.checkpoint.previous_checkpoint_id
            )
            if evidence.checkpoint.previous_checkpoint_id is None:
                raise GenerationIsolationError("Evidence Create Checkpoint lacks predecessor")
            started = self.store.load_create_checkpoint_id(
                evidence.checkpoint.previous_checkpoint_id
            )
            for child, parent, child_stage, parent_stage in (
                (evidence, started, "evidence_ready", "started"),
                (basis, evidence, "native_audit_basis_ready", "evidence_ready"),
                (text, basis, "native_text_audit_ready", "native_audit_basis_ready"),
                (audio, text, "native_audio_audit_ready", "native_text_audit_ready"),
                (stored, audio, "complete", "native_audio_audit_ready"),
            ):
                self._assert_checkpoint_inherits(
                    child.checkpoint,
                    parent.checkpoint,
                    expected_child_stage=child_stage,
                    expected_parent_stage=parent_stage,
                )
            return audio, text, basis, evidence, started
        if audio.checkpoint.previous_checkpoint_id is None:
            raise GenerationIsolationError("Audio Create Checkpoint lacks predecessor")
        correction = self.store.load_create_checkpoint_id(
            audio.checkpoint.previous_checkpoint_id
        )
        if correction.checkpoint.previous_checkpoint_id is None:
            raise GenerationIsolationError("Correction Create Checkpoint lacks predecessor")
        evidence = self.store.load_create_checkpoint_id(
            correction.checkpoint.previous_checkpoint_id
        )
        if evidence.checkpoint.previous_checkpoint_id is None:
            raise GenerationIsolationError("Evidence Create Checkpoint lacks predecessor")
        started = self.store.load_create_checkpoint_id(
            evidence.checkpoint.previous_checkpoint_id
        )
        self._assert_checkpoint_inherits(
            evidence.checkpoint,
            started.checkpoint,
            expected_child_stage="evidence_ready",
            expected_parent_stage="started",
        )
        self._assert_checkpoint_inherits(
            correction.checkpoint,
            evidence.checkpoint,
            expected_child_stage="correction_ready",
            expected_parent_stage="evidence_ready",
        )
        self._assert_checkpoint_inherits(
            audio.checkpoint,
            correction.checkpoint,
            expected_child_stage="audio_audit_ready",
            expected_parent_stage="correction_ready",
        )
        self._assert_checkpoint_inherits(
            complete,
            audio.checkpoint,
            expected_child_stage="complete",
            expected_parent_stage="audio_audit_ready",
        )
        return audio, correction, evidence, started

    def _validate_terminal_runtime_identities(
        self, checkpoint: CreateCheckpoint
    ) -> None:
        terminal_has_speaker = any(
            identity.role == "speaker_attributor"
            for identity in checkpoint.adapter_identities
        )
        runtime_identities = self._create_checkpoint_adapter_identities(
            active_speaker_identity=(
                self._speaker_attributor_identity if terminal_has_speaker else None
            )
        )
        if runtime_identities != checkpoint.adapter_identities:
            raise GenerationIsolationError(
                "runtime Adapter identities differ from the terminal checkpoint"
            )

    def _validate_complete_checkpoint_binding(
        self, stored: StoredCreateCheckpoint
    ) -> None:
        checkpoint = stored.checkpoint
        if checkpoint.stage != "complete":
            raise GenerationIsolationError("terminal Create Checkpoint is not complete")
        binding_payload = self.store.read_create_checkpoint_artifact(
            stored, _CHECKPOINT_COMPLETE_BINDING
        )
        expected_binding = {
            "schema_version": 1,
            "generation_id": checkpoint.generation_id,
            "generation_artifact_set_hash": checkpoint.generation_artifact_set_hash,
            "generation_record_hash": checkpoint.generation_record_hash,
            "generation_manifest_hash": checkpoint.generation_manifest_hash,
            "generation_transcript_hash": checkpoint.generation_transcript_hash,
            "generation_outcome": checkpoint.generation_outcome,
            "previous_checkpoint_id": checkpoint.previous_checkpoint_id,
        }
        if (
            checkpoint.artifact_hashes
            != {_CHECKPOINT_COMPLETE_BINDING: sha256_bytes(binding_payload)}
            or binding_payload != canonical_json_bytes(expected_binding)
        ):
            raise GenerationIsolationError("complete checkpoint binding artifact changed")

    def _validate_native_terminal_checkpoint_artifacts(
        self,
        lineage: tuple[StoredCreateCheckpoint, ...],
        *,
        generation_id: str,
    ) -> None:
        """Bind every native predecessor artifact byte to the verified Generation.

        Structural stage continuity and content-addressed checkpoint IDs are not
        sufficient: an attacker could otherwise reseal a semantically empty chain
        around a valid Generation.  The stage contracts below mirror the native
        commit helpers and require exact same-name bytes in the immutable Generation.
        """

        if len(lineage) != 5 or lineage[0].checkpoint.stage != "native_audio_audit_ready":
            raise GenerationIsolationError("native terminal checkpoint lineage is invalid")
        audio, text, basis, evidence, started = lineage
        generation = self.store.load(generation_id)

        text_sources = self._load_native_generation_source_index_names(
            generation_id, _NATIVE_TEXT_SOURCE_INDEX
        )
        audio_sources = self._load_native_generation_source_index_names(
            generation_id, _NATIVE_AUDIO_SOURCE_INDEX
        )
        text_runs = self._load_native_generation_run_index_names(
            generation_id,
            index_name=_NATIVE_TEXT_RUN_INDEX,
            categories=("requests", "responses"),
        )
        audio_runs = self._load_native_generation_run_index_names(
            generation_id,
            index_name=_NATIVE_AUDIO_RUN_INDEX,
            categories=("requests", "responses", "clips", "journals"),
        )
        evidence_names = (
            {
                _RECEIPT,
                _AUDIO_ARTIFACTS,
                _EVIDENCE,
                _RECOGNITION_REQUEST,
                _RECOGNITION_INDEPENDENCE,
                _ORTHOGRAPHIC_PROJECTIONS,
                _BASE_PRIMARY_EVIDENCE,
                _SPEAKER_PROVENANCE,
                _REFERENCE_ENROLLMENTS,
                _REFERENCE_RETRIEVAL_POLICY,
                _SPEECH_COVERAGE,
            }
            | {
                name
                for name in generation.artifact_hashes
                if name.startswith(
                    (
                        "raw_evidence/",
                        "orthographic_projection/raw/",
                        "speech_coverage/raw/",
                        "reference_sources/",
                        "reference_extractions/",
                    )
                )
            }
        )
        expected_by_stage = {
            "started": {_CHECKPOINT_CREATE_INTENT},
            "evidence_ready": evidence_names,
            "native_audit_basis_ready": {
                _NATIVE_AUDIT_PLAN,
                _NATIVE_CANDIDATE_SIGNALS,
                _NATIVE_CANDIDATE_GROUPS,
                _NATIVE_EXECUTION_PLAN,
                _CHECKPOINT_CANONICAL,
                _CHECKPOINT_RISKS,
                _REFERENCES,
                _RETRIEVALS,
                *text_sources,
                *audio_sources,
            },
            "native_text_audit_ready": {_NATIVE_TEXT_RECORD, *text_runs},
            "native_audio_audit_ready": {
                _NATIVE_AUDIO_RECORD,
                _NATIVE_AGGREGATE,
                *audio_runs,
            },
        }
        for stored in (started, evidence, basis, text, audio):
            expected = expected_by_stage[stored.checkpoint.stage]
            if set(stored.checkpoint.artifact_hashes) != expected:
                raise GenerationIsolationError(
                    "native terminal checkpoint artifacts violate their stage profile"
                )
            for name in expected:
                checkpoint_bytes = self.store.read_create_checkpoint_artifact(stored, name)
                if name == _CHECKPOINT_CREATE_INTENT:
                    item = stored.checkpoint
                    expected_intent = {
                        "schema_version": 1,
                        "operation_key": item.operation_key,
                        "episode_id": item.episode_id,
                        "source": item.source.model_dump(mode="json"),
                        "input_binding_hash": item.input_binding_hash,
                        "policy_hash": item.policy_hash,
                        "code_hash": item.code_hash,
                        "reference_enrollment_hash": item.reference_enrollment_hash,
                        "speaker_track_binding_hash": item.speaker_track_binding_hash,
                        "adapter_identity_hash": hash_object(item.adapter_identities),
                        "expected_active_generation_id": item.expected_active_generation_id,
                    }
                    if checkpoint_bytes != canonical_json_bytes(expected_intent):
                        raise GenerationIsolationError(
                            "native terminal started intent is not reproducible"
                        )
                    continue
                if name not in generation.artifact_hashes or (
                    self.store.read_artifact(generation_id, name) != checkpoint_bytes
                ):
                    raise GenerationIsolationError(
                        "native terminal checkpoint artifacts differ from the Generation"
                    )

    def _load_native_generation_source_index_names(
        self, generation_id: str, index_name: str
    ) -> set[str]:
        value = _canonical_json_value(
            self.store.read_artifact(generation_id, index_name), label=index_name
        )
        if not isinstance(value, dict) or set(value) != {"schema_version", "entries"}:
            raise GenerationIsolationError("native Generation source index schema is invalid")
        entries = value["entries"]
        if value["schema_version"] != 1 or not isinstance(entries, list):
            raise GenerationIsolationError("native Generation source index version is invalid")
        names = {index_name}
        for entry in entries:
            if not isinstance(entry, dict) or not isinstance(entry.get("artifact_name"), str):
                raise GenerationIsolationError("native Generation source index entry is invalid")
            names.add(entry["artifact_name"])
        return names

    def _load_native_generation_run_index_names(
        self,
        generation_id: str,
        *,
        index_name: str,
        categories: tuple[str, ...],
    ) -> set[str]:
        value = _canonical_json_value(
            self.store.read_artifact(generation_id, index_name), label=index_name
        )
        if not isinstance(value, dict) or set(value) != {"schema_version", *categories}:
            raise GenerationIsolationError("native Generation run index schema is invalid")
        if value["schema_version"] != 1:
            raise GenerationIsolationError("native Generation run index version is invalid")
        names = {index_name}
        for category in categories:
            values = value[category]
            if not isinstance(values, list) or any(not isinstance(name, str) for name in values):
                raise GenerationIsolationError("native Generation run index paths are invalid")
            names.update(values)
        return names

    def _require_current_ledger_head(
        self, transcript: CanonicalTranscript
    ) -> tuple[LedgerEntry, ...]:
        """Return one verified Ledger snapshot iff ``transcript`` owns its head."""

        entries = self.ledger.entries()
        head_hash = entries[-1].entry_hash if entries else GENESIS_HASH
        if transcript.ledger_hash != head_hash:
            raise GenerationIsolationError(
                "active Canonical Transcript does not address the Correction Ledger head"
            )
        return entries

    def _load_terminal_create_checkpoint(
        self,
        stored: StoredCreateCheckpoint,
        *,
        require_active: bool,
    ) -> tuple[GenerationOutcome, tuple[ReferenceArtifact, ...]]:
        """Verify a sealed create locally, without replaying any Adapter.

        A terminal checkpoint is a storage proof, not merely
        ``manifest.status == complete``.  The complete seal, full predecessor
        chain, runtime identities, Generation bytes, Ledger head, and audio CAS
        must all agree before readers may report it as complete.
        """

        checkpoint = stored.checkpoint
        generation_id = checkpoint.generation_id
        if generation_id is None:
            raise GenerationIsolationError("complete checkpoint lacks Generation ID")
        if require_active and self.store.active_generation_id() != generation_id:
            raise GenerationIsolationError(
                f"generation {generation_id} is not the active generation"
            )
        self._validate_terminal_runtime_identities(checkpoint)
        self._terminal_checkpoint_lineage(stored)
        self._validate_complete_checkpoint_binding(stored)
        outcome = self._terminal_generation_outcome(checkpoint)
        if require_active:
            self._require_current_ledger_head(outcome.transcript)
        try:
            enrollment_payload = json.loads(
                self.store.read_artifact(generation_id, _REFERENCE_ENROLLMENTS)
            )
            enrolled_artifacts = tuple(
                ReferenceArtifact.model_validate(item) for item in enrollment_payload
            )
        except (TypeError, ValueError) as exc:
            raise GenerationIsolationError(
                "terminal Generation Reference Enrollments are invalid"
            ) from exc
        if (
            len({item.source_id for item in enrolled_artifacts})
            != len(enrolled_artifacts)
            or hash_object(enrolled_artifacts) != checkpoint.reference_enrollment_hash
        ):
            raise GenerationIsolationError(
                "terminal Generation Reference Enrollment binding changed"
            )
        return outcome, enrolled_artifacts

    def _terminal_generation_outcome(
        self, checkpoint: CreateCheckpoint
    ) -> GenerationOutcome:
        generation_id = checkpoint.generation_id
        if generation_id is None:
            raise GenerationIsolationError("complete checkpoint lacks Generation ID")
        record = self.store.load(generation_id)
        manifest = GenerationManifest.model_validate(record.manifest)
        canonical_bytes = self.store.read_artifact(generation_id, _CANONICAL)
        manifest_bytes = self.store.read_artifact(generation_id, _MANIFEST)
        adapter_identity_bytes = self.store.read_artifact(
            generation_id,
            _ADAPTER_IDENTITIES,
        )
        transcript = CanonicalTranscript.model_validate_json(canonical_bytes)
        artifact_manifest = GenerationManifest.model_validate_json(manifest_bytes)
        try:
            identity_discriminant = json.loads(adapter_identity_bytes)
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise GenerationIsolationError(
                "terminal Generation Adapter identity artifact is invalid JSON"
            ) from exc
        native_stage = "native_full_audit_aggregate" in manifest.stage_artifact_hashes
        native_artifact = _NATIVE_AGGREGATE in record.artifact_hashes
        native_identity = (
            isinstance(identity_discriminant, dict)
            and "native_text_full_audit" in identity_discriminant
        )
        if len({native_stage, native_artifact, native_identity}) != 1:
            raise GenerationIsolationError(
                "terminal Generation native Full Audit profile is incomplete or ambiguous"
            )
        record_hash = hash_file(record.directory / "generation.json")
        expected_outcome = (
            "accepted" if transcript.status == "accepted" else "needs_review"
        )
        ledger_entries = self.ledger.entries()
        verified_prefixes = {
            GENESIS_HASH,
            *(entry.entry_hash for entry in ledger_entries),
        }
        if transcript.ledger_hash not in verified_prefixes:
            raise GenerationIsolationError(
                "terminal Generation does not address a verified Correction Ledger prefix"
            )
        if (
            record.artifact_set_hash != checkpoint.generation_artifact_set_hash
            or record_hash != checkpoint.generation_record_hash
            or hash_object(manifest) != checkpoint.generation_manifest_hash
            or hash_object(transcript) != checkpoint.generation_transcript_hash
            or expected_outcome != checkpoint.generation_outcome
            or manifest != artifact_manifest
            or manifest.generation_id != generation_id
            or transcript.generation_id != generation_id
            or manifest.episode_id != checkpoint.episode_id
            or transcript.episode_id != checkpoint.episode_id
            or manifest.status != "complete"
            or manifest.code_hash != checkpoint.code_hash
            or manifest.policy_hash != checkpoint.policy_hash
            or transcript.policy_hash != checkpoint.policy_hash
            or transcript.source_audio_hash != checkpoint.source.sha256
            or checkpoint.normalized is None
            or transcript.normalized_audio_hash != checkpoint.normalized.sha256
            or transcript.normalization_receipt_hash
            != checkpoint.normalization_receipt_hash
            or manifest.stage_artifact_hashes.get("canonical_transcript")
            != hash_object(transcript)
        ):
            raise GenerationIsolationError(
                "complete checkpoint Generation binding is not reproducible"
            )
        _validate_acceptance_policy_not_rewritten(transcript)
        _validate_canonical_acceptance_identity(transcript)
        self.store.audio_path(
            checkpoint.source.sha256,
            size_bytes=checkpoint.source.size_bytes,
        )
        assert checkpoint.normalized is not None
        self.store.audio_path(
            checkpoint.normalized.sha256,
            size_bytes=checkpoint.normalized.size_bytes,
        )
        if expected_outcome == "accepted":
            outcome: GenerationOutcome = AcceptedGeneration(
                generation_id, transcript, manifest
            )
        else:
            issues = tuple(
                issue for issue in transcript.review_issues if issue.status == "unresolved"
            )
            outcome = NeedsReview(generation_id, transcript, issues, manifest)
        if native_stage:
            loaded = self._load_generation(
                generation_id,
                require_active=False,
                _artifact_cache={
                    _ADAPTER_IDENTITIES: adapter_identity_bytes,
                    _CANONICAL: canonical_bytes,
                    _MANIFEST: manifest_bytes,
                },
            )
            loaded_result = loaded.result
            if (
                loaded_result.transcript != outcome.transcript
                or loaded_result.outcome != expected_outcome
                or loaded.storage.generation_id != record.generation_id
                or loaded.storage.artifact_set_hash != record.artifact_set_hash
                or loaded.storage.manifest.materialize() != manifest
                or not isinstance(loaded.audit, LoadedNativeAuditState)
            ):
                raise GenerationIsolationError(
                    "native terminal replay differs from its sealed Generation outcome"
                )
            terminal = self.store.load_create_checkpoint_id(checkpoint.id)
            lineage = self._terminal_checkpoint_lineage(terminal)
            self._validate_native_terminal_checkpoint_artifacts(
                lineage,
                generation_id=generation_id,
            )
        return outcome

    def _replay_complete_create(
        self,
        stored: StoredCreateCheckpoint,
        *,
        request: CreateRequest,
        source: ArtifactDigest,
        effective_policy_hash: str,
        input_binding_hash: str,
        adapter_identities: tuple[CheckpointAdapterIdentity, ...],
        speaker_bindings: tuple[SpeakerTrackBinding, ...],
        enrolled_artifacts: tuple[ReferenceArtifact, ...],
    ) -> GenerationOutcome:
        checkpoint = stored.checkpoint
        started = self._terminal_checkpoint_lineage(stored)[-1]
        self._load_started_checkpoint(
            started,
            request=request,
            source=source,
            effective_policy_hash=effective_policy_hash,
            input_binding_hash=input_binding_hash,
            adapter_identities=adapter_identities,
            speaker_bindings=speaker_bindings,
            enrolled_artifacts=enrolled_artifacts,
        )
        self._validate_complete_checkpoint_binding(stored)
        outcome = self._terminal_generation_outcome(checkpoint)
        assert checkpoint.generation_id is not None
        with self.store.episode_transaction():
            try:
                active_generation_id = self.store.active_generation_id()
            except GenerationNotFoundError:
                active_generation_id = None
            if active_generation_id not in {
                None,
                checkpoint.generation_id,
                checkpoint.expected_active_generation_id,
            }:
                try:
                    active = self._load_generation(
                        active_generation_id,
                        require_active=True,
                    )
                except (SubtitleV2Error, TypeError, ValueError):
                    # Preserve the established CAS failure for an unrelated or
                    # unverifiable pointer.  The CAS below re-reads the pointer
                    # under this same episode lock and cannot mutate it.
                    active = None
                if active is not None and self._generation_descends_from(
                    active.result.transcript,
                    ancestor_generation_id=checkpoint.generation_id,
                ):
                    active_result = active.result
                    active_manifest = active.storage.manifest.materialize()
                    if active_result.outcome == "accepted":
                        return AcceptedGeneration(
                            active_generation_id,
                            active_result.transcript,
                            active_manifest,
                        )
                    active_issues = tuple(
                        issue
                        for issue in active_result.transcript.review_issues
                        if issue.status == "unresolved"
                    )
                    return NeedsReview(
                        active_generation_id,
                        active_result.transcript,
                        active_issues,
                        active_manifest,
                    )
            self.store.set_active_cas_locked(
                checkpoint.generation_id,
                expected_generation_id=checkpoint.expected_active_generation_id,
            )
        return outcome

    def _seal_and_activate_create(
        self,
        *,
        audio_checkpoint: CreateCheckpoint,
        outcome: GenerationOutcome,
    ) -> GenerationOutcome:
        if isinstance(outcome, Interrupted):
            raise GenerationIsolationError(
                "cannot seal an interrupted create as complete"
            )
        record = self.store.load(outcome.generation_id)
        manifest_hash = hash_object(outcome.manifest)
        transcript_hash = hash_object(outcome.transcript)
        generation_outcome = (
            "accepted" if isinstance(outcome, AcceptedGeneration) else "needs_review"
        )
        binding = {
            "schema_version": 1,
            "generation_id": outcome.generation_id,
            "generation_artifact_set_hash": record.artifact_set_hash,
            "generation_record_hash": hash_file(record.directory / "generation.json"),
            "generation_manifest_hash": manifest_hash,
            "generation_transcript_hash": transcript_hash,
            "generation_outcome": generation_outcome,
            "previous_checkpoint_id": audio_checkpoint.id,
        }
        payload = canonical_json_bytes(binding)
        if audio_checkpoint.normalized is None:
            raise GenerationIsolationError("Audio Create Checkpoint lacks normalized audio")
        checkpoint = build_create_checkpoint(
            operation_key=audio_checkpoint.operation_key,
            episode_id=audio_checkpoint.episode_id,
            stage="complete",
            invocation_id=audio_checkpoint.invocation_id,
            source=audio_checkpoint.source,
            normalized=audio_checkpoint.normalized,
            normalization_receipt_hash=(
                audio_checkpoint.normalization_receipt_hash
            ),
            input_binding_hash=audio_checkpoint.input_binding_hash,
            policy_hash=audio_checkpoint.policy_hash,
            code_hash=audio_checkpoint.code_hash,
            reference_enrollment_hash=audio_checkpoint.reference_enrollment_hash,
            speaker_track_binding_hash=audio_checkpoint.speaker_track_binding_hash,
            adapter_identities=audio_checkpoint.adapter_identities,
            artifact_hashes={_CHECKPOINT_COMPLETE_BINDING: sha256_bytes(payload)},
            expected_active_generation_id=(
                audio_checkpoint.expected_active_generation_id
            ),
            previous_checkpoint_id=audio_checkpoint.id,
            generation_id=outcome.generation_id,
            generation_artifact_set_hash=record.artifact_set_hash,
            generation_record_hash=binding["generation_record_hash"],
            generation_manifest_hash=manifest_hash,
            generation_transcript_hash=transcript_hash,
            generation_outcome=generation_outcome,
        )
        stored = self.store.commit_create_checkpoint(
            checkpoint,
            artifacts={_CHECKPOINT_COMPLETE_BINDING: payload},
        )
        # Reopen the seal before activation. Native Full Audit uses local exact-byte
        # replay here; it never invokes a provider or runner. Legacy generations retain
        # their storage-only terminal verification behavior.
        self._terminal_checkpoint_lineage(stored)
        self._validate_complete_checkpoint_binding(stored)
        verified = self._terminal_generation_outcome(stored.checkpoint)
        with self.store.episode_transaction():
            self._require_current_ledger_head(verified.transcript)
            self.store.set_active_cas_locked(
                outcome.generation_id,
                expected_generation_id=checkpoint.expected_active_generation_id,
            )
        return verified

    def _verify_recognition_derivations(
        self,
        evidence: Sequence[RecognitionEvidence],
        *,
        request: RecognitionRequest,
        raw_outputs: Mapping[str, bytes] | None = None,
        context: str,
    ) -> None:
        """Re-derive each Recognition Evidence stream from its exact raw bytes."""

        if len(evidence) != len(self._recognizers):
            raise GenerationIsolationError(
                f"{context} Recognition Evidence count differs from configured Recognizers"
            )
        identities = self._runtime_recognizer_identities()
        for ordinal, (recognizer, identity, item) in enumerate(
            zip(self._recognizers, identities, evidence)
        ):
            if (
                item.adapter != identity.adapter_name
                or item.config_hash != identity.config_hash
                or item.model
                not in {identity.model, f"{identity.model}@{identity.model_version}"}
            ):
                raise GenerationIsolationError(
                    f"{context} Recognition Evidence {ordinal} differs from its "
                    "executable identity"
                )
            if raw_outputs is None:
                raw_payload = _file_uri_path(item.raw_output.uri).read_bytes()
            else:
                name = f"raw_evidence/{item.raw_output.sha256}.json"
                try:
                    raw_payload = raw_outputs[name]
                except KeyError as exc:
                    raise GenerationIsolationError(
                        f"{context} lacks Recognition raw output {name!r}"
                    ) from exc
            if (
                sha256_bytes(raw_payload) != item.raw_output.sha256
                or len(raw_payload) != item.raw_output.size_bytes
            ):
                raise GenerationIsolationError(
                    f"{context} Recognition raw output {ordinal} digest mismatch"
                )
            try:
                verified = recognizer.verify(
                    item,
                    request=request,
                    raw_output=raw_payload,
                )
            except (AdapterIntegrityError, ValueError) as exc:
                raise GenerationIsolationError(
                    f"{context} Recognition Evidence {ordinal} is not reproducible"
                ) from exc
            if verified != item:
                raise GenerationIsolationError(
                    f"{context} Recognition verifier changed Evidence {ordinal}"
                )

    def _load_stored_recognition_request(
        self,
        generation_id: str,
        *,
        episode_id: str,
        normalized_audio: Path,
        normalized_sha256: str,
        normalized_size_bytes: int,
        read_artifact: Callable[[str], bytes] | None = None,
    ) -> tuple[RecognitionRequestArtifactV1, RecognitionRequest]:
        payload = (
            self.store.read_artifact(generation_id, _RECOGNITION_REQUEST)
            if read_artifact is None
            else read_artifact(_RECOGNITION_REQUEST)
        )
        try:
            artifact = RecognitionRequestArtifactV1.model_validate_json(
                payload,
                strict=True,
            )
            request = materialize_recognition_request(
                artifact,
                normalized_audio=normalized_audio,
                raw_output_dir=self.store.root / "replay-only-recognition",
            )
        except (TypeError, ValueError) as exc:
            raise GenerationIsolationError(
                "stored Recognition request artifact is invalid"
            ) from exc
        if canonical_json_bytes(artifact) != payload:
            raise GenerationIsolationError("stored Recognition request bytes are not canonical")
        if (
            artifact.episode_id != episode_id
            or artifact.normalized_audio_sha256 != normalized_sha256
            or artifact.normalized_audio_size_bytes != normalized_size_bytes
        ):
            raise GenerationIsolationError(
                "stored Recognition request crossed episode/audio lineage"
            )
        return artifact, request

    def _load_stored_recognition_independence(
        self,
        generation_id: str,
        *,
        read_artifact: Callable[[str], bytes] | None = None,
    ) -> RecognitionIndependenceReceiptV1 | None:
        payload = (
            self.store.read_artifact(generation_id, _RECOGNITION_INDEPENDENCE)
            if read_artifact is None
            else read_artifact(_RECOGNITION_INDEPENDENCE)
        )
        try:
            raw = json.loads(payload)
            receipt = (
                None
                if raw is None
                else RecognitionIndependenceReceiptV1.model_validate_json(
                    payload,
                    strict=True,
                )
            )
        except (TypeError, ValueError) as exc:
            raise GenerationIsolationError(
                "stored Recognition independence receipt is invalid"
            ) from exc
        if canonical_json_bytes(receipt) != payload:
            raise GenerationIsolationError(
                "stored Recognition independence receipt bytes are not canonical"
            )
        if receipt != self._recognition_independence_receipt():
            raise GenerationIsolationError(
                "stored Recognition independence receipt differs from runtime"
            )
        return receipt

    def _load_orthographic_projection_artifacts(
        self,
        generation_id: str,
        *,
        evidence: Sequence[RecognitionEvidence],
        expected_set_hash: str,
        read_artifact: Callable[[str], bytes] | None = None,
    ) -> tuple[tuple[OrthographicProjectionEvidenceV1, ...], dict[str, bytes]]:
        """Load exact projected Evidence/raw bytes and replay the installed trust root."""

        load = (
            (lambda name: self.store.read_artifact(generation_id, name))
            if read_artifact is None
            else read_artifact
        )
        payload = load(_ORTHOGRAPHIC_PROJECTIONS)
        try:
            values = json.loads(payload)
            if not isinstance(values, list):
                raise ValueError("projection Evidence is not a list")
            projections = tuple(
                OrthographicProjectionEvidenceV1.model_validate_json(
                    canonical_json_bytes(item)
                )
                for item in values
            )
            if orthographic_projection_evidence_set_hash(projections) != expected_set_hash:
                raise ValueError("projection Evidence set hash mismatch")
        except (OrthographicProjectionError, TypeError, ValueError) as exc:
            raise GenerationIsolationError(
                "stored Orthographic Projection Evidence is invalid"
            ) from exc

        raw_outputs: dict[str, bytes] = {}
        for projection in projections:
            name = (
                "orthographic_projection/raw/"
                f"{projection.raw_output.sha256}.txt"
            )
            if projection.raw_output.uri != f"generation-artifact://{name}":
                raise GenerationIsolationError(
                    "stored Orthographic Projection raw output URI is not canonical"
                )
            raw = load(name)
            try:
                verify_orthographic_projection(
                    projection,
                    evidence,
                    raw_output=raw,
                )
            except (OrthographicProjectionError, ValueError) as exc:
                raise GenerationIsolationError(
                    "stored Orthographic Projection is not byte-replayable"
                ) from exc
            raw_outputs[name] = raw
        return projections, raw_outputs

    def _commit_evidence_checkpoint(
        self,
        *,
        started_checkpoint: CreateCheckpoint,
        operation_key: str,
        input_binding_hash: str,
        request: CreateRequest,
        source_hash: str,
        receipt: NormalizationReceipt,
        receipt_hash: str,
        invocation_id: str,
        recognition_request: RecognitionRequestArtifactV1,
        recognition_independence: RecognitionIndependenceReceiptV1 | None,
        effective_policy_hash: str,
        adapter_identities: tuple[CheckpointAdapterIdentity, ...],
        speaker_bindings: tuple[SpeakerTrackBinding, ...],
        evidence: tuple[RecognitionEvidence, ...],
        base_primary_evidence: RecognitionEvidence | None,
        speaker_provenance: SpeakerAttributionProvenance | None,
        orthographic_projections: tuple[OrthographicProjectionEvidenceV1, ...],
        orthographic_raw_outputs: dict[str, bytes],
        enrolled_artifacts: tuple[ReferenceArtifact, ...],
        speech_coverage_receipt: SpeechCoverageReceipt | None,
        speech_coverage_raw_artifacts: dict[str, bytes],
        raw_outputs: dict[str, bytes],
        reference_retrieval_policy: ReferenceRetrievalPolicySnapshot,
        reference_source_snapshots: dict[str, bytes],
        reference_extraction_snapshots: dict[str, bytes],
    ) -> CreateCheckpoint:
        artifact_values: dict[str, object] = {
            _RECEIPT: receipt,
            _AUDIO_ARTIFACTS: _audio_artifacts_payload(receipt),
            _EVIDENCE: evidence,
            _RECOGNITION_REQUEST: recognition_request,
            _RECOGNITION_INDEPENDENCE: recognition_independence,
            _BASE_PRIMARY_EVIDENCE: (
                (base_primary_evidence,) if base_primary_evidence is not None else ()
            ),
            _SPEAKER_PROVENANCE: speaker_provenance,
            _ORTHOGRAPHIC_PROJECTIONS: orthographic_projections,
            _REFERENCE_ENROLLMENTS: enrolled_artifacts,
            _REFERENCE_RETRIEVAL_POLICY: reference_retrieval_policy,
            _SPEECH_COVERAGE: speech_coverage_receipt,
            **raw_outputs,
            **orthographic_raw_outputs,
            **speech_coverage_raw_artifacts,
            **reference_source_snapshots,
            **reference_extraction_snapshots,
        }
        artifacts = {
            name: _checkpoint_artifact_bytes(value)
            for name, value in artifact_values.items()
        }
        checkpoint = build_create_checkpoint(
            operation_key=operation_key,
            episode_id=request.episode_id,
            stage="evidence_ready",
            invocation_id=invocation_id,
            source=receipt.source,
            normalized=receipt.normalized,
            normalization_receipt_hash=receipt_hash,
            input_binding_hash=input_binding_hash,
            policy_hash=effective_policy_hash,
            code_hash=self._code_hash,
            reference_enrollment_hash=hash_object(enrolled_artifacts),
            speaker_track_binding_hash=hash_object(
                tuple(binding.portable_identity for binding in speaker_bindings)
            ),
            adapter_identities=adapter_identities,
            artifact_hashes={
                name: sha256_bytes(payload) for name, payload in sorted(artifacts.items())
            },
            expected_active_generation_id=(
                started_checkpoint.expected_active_generation_id
            ),
            previous_checkpoint_id=started_checkpoint.id,
        )
        self.store.commit_create_checkpoint(checkpoint, artifacts=artifacts)
        return checkpoint

    @staticmethod
    def _evidence_checkpoint_artifacts(
        state: _CreateEvidenceState,
        *,
        enrolled_artifacts: tuple[ReferenceArtifact, ...],
        reference_source_snapshots: dict[str, bytes],
        reference_extraction_snapshots: dict[str, bytes],
    ) -> dict[str, bytes]:
        values: dict[str, object] = {
            _RECEIPT: state.receipt,
            _AUDIO_ARTIFACTS: _audio_artifacts_payload(state.receipt),
            _EVIDENCE: state.evidence,
            _RECOGNITION_REQUEST: state.recognition_request,
            _RECOGNITION_INDEPENDENCE: state.recognition_independence,
            _BASE_PRIMARY_EVIDENCE: (
                (state.base_primary_evidence,)
                if state.base_primary_evidence is not None
                else ()
            ),
            _SPEAKER_PROVENANCE: state.speaker_provenance,
            _ORTHOGRAPHIC_PROJECTIONS: state.orthographic_projections,
            _REFERENCE_ENROLLMENTS: enrolled_artifacts,
            _REFERENCE_RETRIEVAL_POLICY: state.reference_retrieval_policy,
            _SPEECH_COVERAGE: state.speech_coverage_receipt,
            **state.raw_outputs,
            **state.orthographic_raw_outputs,
            **state.speech_coverage_raw_artifacts,
            **reference_source_snapshots,
            **reference_extraction_snapshots,
        }
        return {
            name: _checkpoint_artifact_bytes(value) for name, value in values.items()
        }

    def _load_evidence_checkpoint(
        self,
        stored: StoredCreateCheckpoint,
        *,
        request: CreateRequest,
        source_hash: str,
        source_size_bytes: int,
        effective_policy_hash: str,
        input_binding_hash: str,
        adapter_identities: tuple[CheckpointAdapterIdentity, ...],
        speaker_bindings: tuple[SpeakerTrackBinding, ...],
        enrolled_artifacts: tuple[ReferenceArtifact, ...],
        reference_source_snapshots: dict[str, bytes],
        reference_extraction_snapshots: dict[str, bytes],
    ) -> _CreateEvidenceState:
        checkpoint = stored.checkpoint
        if (
            checkpoint.episode_id != request.episode_id
            or checkpoint.source.sha256 != source_hash
            or checkpoint.source.size_bytes != source_size_bytes
            or checkpoint.input_binding_hash != input_binding_hash
            or checkpoint.policy_hash != effective_policy_hash
            or checkpoint.code_hash != self._code_hash
            or checkpoint.reference_enrollment_hash != hash_object(enrolled_artifacts)
            or checkpoint.speaker_track_binding_hash
            != hash_object(tuple(item.portable_identity for item in speaker_bindings))
            or checkpoint.adapter_identities != adapter_identities
        ):
            raise GenerationIsolationError(
                "Create Checkpoint binding differs from the current exact request"
            )

        def artifact(name: str) -> bytes:
            return self.store.read_create_checkpoint_artifact(stored, name)

        receipt = NormalizationReceipt.model_validate_json(artifact(_RECEIPT))
        if (
            receipt.status != "accepted"
            or normalization_receipt_content_hash(receipt)
            != checkpoint.normalization_receipt_hash
            or receipt.source != checkpoint.source
            or receipt.normalized != checkpoint.normalized
        ):
            raise GenerationIsolationError(
                "Create Checkpoint Normalization Receipt crossed its exact lineage"
            )
        audio_artifacts = json.loads(artifact(_AUDIO_ARTIFACTS))
        if audio_artifacts != _audio_artifacts_payload(receipt):
            raise GenerationIsolationError(
                "Create Checkpoint audio identities differ from its receipt"
            )
        self.store.audio_path(receipt.source.sha256, size_bytes=receipt.source.size_bytes)
        normalized_audio = self.store.audio_path(
            receipt.normalized.sha256,
            size_bytes=receipt.normalized.size_bytes,
        )
        evidence = tuple(
            RecognitionEvidence.model_validate(item)
            for item in json.loads(artifact(_EVIDENCE))
        )
        if not evidence:
            raise GenerationIsolationError("Create Checkpoint lacks Recognition Evidence")
        recognition_request_payload = artifact(_RECOGNITION_REQUEST)
        try:
            recognition_request_artifact = RecognitionRequestArtifactV1.model_validate_json(
                recognition_request_payload,
                strict=True,
            )
        except (TypeError, ValueError) as exc:
            raise GenerationIsolationError(
                "Create Checkpoint Recognition request artifact is invalid"
            ) from exc
        if canonical_json_bytes(recognition_request_artifact) != recognition_request_payload:
            raise GenerationIsolationError(
                "Create Checkpoint Recognition request bytes are not canonical"
            )
        expected_request_artifact = build_recognition_request_artifact(
            RecognitionRequest(
                episode_id=request.episode_id,
                invocation_id=checkpoint.invocation_id,
                normalized_audio=normalized_audio,
                expected_normalized_audio_hash=receipt.normalized.sha256,
                language_hint=request.language_hint or request.policy.language,
            )
        )
        if recognition_request_artifact != expected_request_artifact:
            raise GenerationIsolationError(
                "Create Checkpoint Recognition request differs from the exact create request"
            )
        independence_payload = artifact(_RECOGNITION_INDEPENDENCE)
        try:
            raw_independence = json.loads(independence_payload)
            recognition_independence = (
                None
                if raw_independence is None
                else RecognitionIndependenceReceiptV1.model_validate_json(
                    independence_payload,
                    strict=True,
                )
            )
        except (TypeError, ValueError) as exc:
            raise GenerationIsolationError(
                "Create Checkpoint Recognition independence receipt is invalid"
            ) from exc
        if canonical_json_bytes(recognition_independence) != independence_payload:
            raise GenerationIsolationError(
                "Create Checkpoint Recognition independence bytes are not canonical"
            )
        expected_independence = self._recognition_independence_receipt()
        if recognition_independence != expected_independence:
            raise GenerationIsolationError(
                "Create Checkpoint Recognition independence proof differs from runtime"
            )
        for item in evidence:
            if (
                item.episode_id != request.episode_id
                or item.invocation_id != checkpoint.invocation_id
                or item.normalized_audio_hash != receipt.normalized.sha256
            ):
                raise GenerationIsolationError(
                    "Create Checkpoint Recognition Evidence crossed invocation/audio"
                )
        base_evidence = tuple(
            RecognitionEvidence.model_validate(item)
            for item in json.loads(artifact(_BASE_PRIMARY_EVIDENCE))
        )
        if len(base_evidence) > 1:
            raise GenerationIsolationError(
                "Create Checkpoint has multiple speaker base Evidence streams"
            )
        provenance_payload = json.loads(artifact(_SPEAKER_PROVENANCE))
        speaker_provenance = (
            None
            if provenance_payload is None
            else SpeakerAttributionProvenance.from_payload(provenance_payload)
        )
        if bool(base_evidence) != (speaker_provenance is not None):
            raise GenerationIsolationError(
                "Create Checkpoint speaker Evidence/provenance is incomplete"
            )

        raw_outputs: dict[str, bytes] = {}
        for item in (*evidence, *base_evidence):
            name = f"raw_evidence/{item.raw_output.sha256}.json"
            payload = artifact(name)
            if (
                item.raw_output.uri != f"generation-artifact://{name}"
                or sha256_bytes(payload) != item.raw_output.sha256
                or len(payload) != item.raw_output.size_bytes
            ):
                raise GenerationIsolationError(
                    "Create Checkpoint Recognition raw output digest mismatch"
                )
            raw_outputs[name] = payload
        original_recognition_evidence = (
            (base_evidence[0], *evidence[1:]) if base_evidence else evidence
        )
        self._verify_recognition_derivations(
            original_recognition_evidence,
            request=materialize_recognition_request(
                recognition_request_artifact,
                normalized_audio=normalized_audio,
                raw_output_dir=self.store.root / "replay-only-recognition",
            ),
            raw_outputs=raw_outputs,
            context="Create Checkpoint",
        )
        projection_payloads = json.loads(artifact(_ORTHOGRAPHIC_PROJECTIONS))
        if not isinstance(projection_payloads, list):
            raise GenerationIsolationError(
                "Create Checkpoint Orthographic Projection Evidence is not a list"
            )
        try:
            orthographic_projections = tuple(
                OrthographicProjectionEvidenceV1.model_validate_json(
                    canonical_json_bytes(item)
                )
                for item in projection_payloads
            )
        except (TypeError, ValueError) as exc:
            raise GenerationIsolationError(
                "Create Checkpoint Orthographic Projection Evidence is invalid"
            ) from exc
        if len(orthographic_projections) != len(evidence):
            raise GenerationIsolationError(
                "Create Checkpoint lacks complete Orthographic Projection Evidence"
            )
        orthographic_raw_outputs: dict[str, bytes] = {}
        for projection in orthographic_projections:
            name = (
                "orthographic_projection/raw/"
                f"{projection.raw_output.sha256}.txt"
            )
            raw_payload = artifact(name)
            if projection.raw_output.uri != f"generation-artifact://{name}":
                raise GenerationIsolationError(
                    "Create Checkpoint Orthographic raw output URI mismatch"
                )
            try:
                verify_orthographic_projection(
                    projection,
                    evidence,
                    raw_output=raw_payload,
                )
            except (OrthographicProjectionError, ValueError) as exc:
                raise GenerationIsolationError(
                    "Create Checkpoint Orthographic Projection is not reproducible"
                ) from exc
            orthographic_raw_outputs[name] = raw_payload
        if base_evidence:
            if self._speaker_attributor is None or not speaker_bindings:
                raise GenerationIsolationError(
                    "Create Checkpoint speaker state lacks current tracks/Adapter"
                )
            identity = self._speaker_attributor_identity
            assert identity is not None and speaker_provenance is not None
            verified = self._speaker_attributor.verify(
                evidence[0],
                base_evidence=base_evidence[0],
                raw_output=raw_outputs[
                    f"raw_evidence/{evidence[0].raw_output.sha256}.json"
                ],
            )
            _assert_speaker_attribution_binding(
                attributed=evidence[0],
                base=base_evidence[0],
                provenance=verified,
                identity=identity,
                expected_tracks=speaker_bindings,
            )
            if verified != speaker_provenance:
                raise GenerationIsolationError(
                    "Create Checkpoint Speaker Attribution is not reproducible"
                )

        stored_enrollments = tuple(
            ReferenceArtifact.model_validate(item)
            for item in json.loads(artifact(_REFERENCE_ENROLLMENTS))
        )
        if stored_enrollments != enrolled_artifacts:
            raise GenerationIsolationError(
                "Create Checkpoint Reference Enrollments differ from current snapshots"
            )
        stored_reference_policy = ReferenceRetrievalPolicySnapshot.model_validate_json(
            artifact(_REFERENCE_RETRIEVAL_POLICY)
        )
        expected_reference_policy = request.policy.reference_retrieval_snapshot(
            tuple(request.vocabulary)
        )
        if stored_reference_policy != expected_reference_policy:
            raise GenerationIsolationError(
                "Create Checkpoint Reference retrieval policy differs from current request"
            )
        for name, expected in {
            **reference_source_snapshots,
            **reference_extraction_snapshots,
        }.items():
            if artifact(name) != expected:
                raise GenerationIsolationError(
                    f"Create Checkpoint Reference snapshot drifted: {name}"
                )

        coverage_payload = json.loads(artifact(_SPEECH_COVERAGE))
        speech_coverage_receipt = (
            None
            if coverage_payload is None
            else SpeechCoverageReceipt.model_validate(coverage_payload)
        )
        speech_coverage_raw_artifacts: dict[str, bytes] = {}
        if speech_coverage_receipt is None:
            if self._speech_coverage_analyzer is not None:
                raise GenerationIsolationError(
                    "Create Checkpoint lacks configured Speech Coverage evidence"
                )
        else:
            if self._speech_coverage_analyzer is None or receipt.normalized_duration_ms is None:
                raise GenerationIsolationError(
                    "Create Checkpoint Speech Coverage lacks its exact runtime/audio"
                )
            raw_name = (
                f"speech_coverage/raw/{speech_coverage_receipt.raw_output.sha256}.json"
            )
            raw_payload = artifact(raw_name)
            if (
                speech_coverage_receipt.raw_output.uri
                != f"generation-artifact://{raw_name}"
                or sha256_bytes(raw_payload)
                != speech_coverage_receipt.raw_output.sha256
                or len(raw_payload) != speech_coverage_receipt.raw_output.size_bytes
            ):
                raise GenerationIsolationError(
                    "Create Checkpoint Speech Coverage raw output digest mismatch"
                )
            coverage_request = SpeechCoverageRequest(
                episode_id=request.episode_id,
                invocation_id=checkpoint.invocation_id,
                normalized_audio=normalized_audio,
                expected_normalized_audio_hash=receipt.normalized.sha256,
                expected_normalized_audio_size_bytes=receipt.normalized.size_bytes,
                normalized_audio_duration_ms=receipt.normalized_duration_ms,
                recognition_evidence=evidence,
                raw_output_dir=self.store.root / "replay-only-speech-coverage",
            )
            try:
                verified_coverage = self._speech_coverage_analyzer.verify(
                    speech_coverage_receipt,
                    request=coverage_request,
                    raw_output=raw_payload,
                )
            except (AdapterIntegrityError, ValueError) as exc:
                raise GenerationIsolationError(
                    "Create Checkpoint Speech Coverage is not reproducible"
                ) from exc
            if verified_coverage != speech_coverage_receipt:
                raise GenerationIsolationError(
                    "Create Checkpoint Speech Coverage changed during replay"
                )
            speech_coverage_raw_artifacts[raw_name] = raw_payload

        expected_names = {
            _RECEIPT,
            _AUDIO_ARTIFACTS,
            _EVIDENCE,
            _RECOGNITION_REQUEST,
            _RECOGNITION_INDEPENDENCE,
            _ORTHOGRAPHIC_PROJECTIONS,
            _BASE_PRIMARY_EVIDENCE,
            _SPEAKER_PROVENANCE,
            _REFERENCE_ENROLLMENTS,
            _REFERENCE_RETRIEVAL_POLICY,
            _SPEECH_COVERAGE,
            *raw_outputs,
            *orthographic_raw_outputs,
            *speech_coverage_raw_artifacts,
            *reference_source_snapshots,
            *reference_extraction_snapshots,
        }
        if set(checkpoint.artifact_hashes) != expected_names:
            raise GenerationIsolationError(
                "Create Checkpoint evidence-stage artifact profile drifted"
            )
        return _CreateEvidenceState(
            source_hash=source_hash,
            receipt=receipt,
            receipt_hash=checkpoint.normalization_receipt_hash,
            invocation_id=checkpoint.invocation_id,
            recognition_request=recognition_request_artifact,
            recognition_independence=recognition_independence,
            evidence=evidence,
            orthographic_projections=orthographic_projections,
            orthographic_raw_outputs=orthographic_raw_outputs,
            base_primary_evidence=(base_evidence[0] if base_evidence else None),
            speaker_provenance=speaker_provenance,
            speech_coverage_receipt=speech_coverage_receipt,
            speech_coverage_raw_artifacts=speech_coverage_raw_artifacts,
            raw_outputs=raw_outputs,
            reference_retrieval_policy=stored_reference_policy,
            checkpoint=checkpoint,
        )

    @staticmethod
    def _native_basis_result(
        *,
        request: CreateRequest,
        evidence_state: _CreateEvidenceState,
        references: tuple[ReferenceEvidence, ...],
        retrievals: tuple[ReferenceRetrievalReceipt, ...],
    ) -> CanonicalBuildResult:
        result = reconcile_canonical(
            primary=evidence_state.evidence[0],
            corroborating=evidence_state.evidence[1:],
            source_audio_hash=evidence_state.source_hash,
            normalization_receipt_hash=evidence_state.receipt_hash,
            policy_hash=evidence_state.checkpoint.policy_hash,
            acceptance_policy=request.policy.acceptance_snapshot,
            references=references,
            orthographic_projections=evidence_state.orthographic_projections,
        )
        result = attest_speech_coverage(result, evidence_state.speech_coverage_receipt)
        retrieval_risks = _reference_retrieval_failure_risks(retrievals)
        if retrieval_risks:
            result = add_review_risks(result, retrieval_risks)
        return add_review_risks(result, (_native_full_audit_resolution_risk(result.transcript),))

    @staticmethod
    def _load_native_model(
        store: GenerationStore,
        stored: StoredCreateCheckpoint,
        name: str,
        model: type,
    ) -> object:
        payload = store.read_create_checkpoint_artifact(stored, name)
        try:
            value = model.model_validate_json(payload, strict=True)
        except (TypeError, ValueError) as exc:
            raise GenerationIsolationError(f"native audit artifact {name!r} is invalid") from exc
        if canonical_json_bytes(value) != payload:
            raise GenerationIsolationError(f"native audit artifact {name!r} is not canonical")
        return value

    def _commit_native_child_checkpoint(
        self,
        *,
        parent: CreateCheckpoint,
        stage: Literal[
            "native_audit_basis_ready",
            "native_text_audit_ready",
            "native_audio_audit_ready",
        ],
        artifacts: Mapping[str, bytes],
    ) -> CreateCheckpoint:
        checkpoint = build_create_checkpoint(
            operation_key=parent.operation_key,
            episode_id=parent.episode_id,
            stage=stage,
            invocation_id=parent.invocation_id,
            source=parent.source,
            normalized=parent.normalized,
            normalization_receipt_hash=parent.normalization_receipt_hash,
            input_binding_hash=parent.input_binding_hash,
            policy_hash=parent.policy_hash,
            code_hash=parent.code_hash,
            reference_enrollment_hash=parent.reference_enrollment_hash,
            speaker_track_binding_hash=parent.speaker_track_binding_hash,
            adapter_identities=parent.adapter_identities,
            artifact_hashes={
                name: sha256_bytes(payload) for name, payload in sorted(artifacts.items())
            },
            expected_active_generation_id=parent.expected_active_generation_id,
            previous_checkpoint_id=parent.id,
        )
        self.store.commit_create_checkpoint(checkpoint, artifacts=artifacts)
        return checkpoint

    def _native_source_sets(
        self,
        *,
        audit_plan: AuditPlan,
        candidate_signal_set: CandidateSignalSet,
        candidate_group_set: CandidateGroupSet,
        execution_plan: CorrectionAuditExecutionPlanV2,
        transcript: CanonicalTranscript,
        recognition_evidence: tuple[RecognitionEvidence, ...],
        reference_retrievals: tuple[ReferenceRetrievalReceipt, ...],
        normalized_audio_path: Path,
    ) -> tuple[dict[str, bytes], dict[str, bytes]]:
        text_sources: dict[str, bytes] = {}
        audio_sources: dict[str, bytes] = {}
        for packet in execution_plan.packets:
            if packet.modality == "text":
                materialized = materialize_text_correction_packet_sources(
                    execution_plan,
                    packet.id,
                    audit_plan,
                    candidate_signal_set,
                    candidate_group_set,
                    transcript,
                    recognition_evidence,
                    reference_retrievals=reference_retrievals,
                    seam_evidence=None,
                )
                _merge_exact_artifacts(text_sources, materialized, label="text source")
            else:
                materialized = materialize_audio_correction_packet_sources(
                    execution_plan,
                    packet.id,
                    audit_plan,
                    candidate_signal_set,
                    candidate_group_set,
                    transcript,
                    recognition_evidence,
                    normalized_audio_path=normalized_audio_path,
                    reference_retrievals=reference_retrievals,
                    seam_evidence=None,
                )
                _merge_exact_artifacts(audio_sources, materialized, label="audio source")
        return text_sources, audio_sources

    def _commit_native_audit_basis_checkpoint(
        self,
        *,
        evidence_state: _CreateEvidenceState,
        result: CanonicalBuildResult,
        references: tuple[ReferenceEvidence, ...],
        retrievals: tuple[ReferenceRetrievalReceipt, ...],
        references_enrolled: bool,
    ) -> _NativeAuditBasisState:
        bundle = self._native_full_audit
        if bundle is None:
            raise GenerationIsolationError("native audit basis requires native composition")
        audit_evidence = tuple(
            sorted(
                evidence_state.evidence,
                key=recognition_evidence_content_hash,
            )
        )
        audit_plan = build_audit_plan(
            result.transcript,
            audit_evidence,
            bundle.audit_policy,
            reference_retrievals=retrievals,
            references_enrolled=references_enrolled,
            speech_coverage=evidence_state.speech_coverage_receipt,
            boundary_constraints=None,
            seam_evidence=None,
        )
        signals = derive_candidate_signal_set(
            audit_plan,
            result.transcript,
            audit_evidence,
            reference_retrievals=retrievals,
            references_enrolled=references_enrolled,
            speech_coverage=evidence_state.speech_coverage_receipt,
            boundary_constraints=None,
            seam_evidence=None,
        )
        groups = derive_candidate_group_set(audit_plan, signals, result.transcript)
        normalized_audio = self.store.audio_path(
            evidence_state.receipt.normalized.sha256,
            size_bytes=evidence_state.receipt.normalized.size_bytes,
        )
        execution = build_correction_audit_execution_plan(
            audit_plan,
            signals,
            groups,
            result.transcript,
            audit_evidence,
            bundle.execution_policy,
            reference_retrievals=retrievals,
            seam_evidence=None,
            normalized_audio_path=normalized_audio,
        )
        text_sources, audio_sources = self._native_source_sets(
            audit_plan=audit_plan,
            candidate_signal_set=signals,
            candidate_group_set=groups,
            execution_plan=execution,
            transcript=result.transcript,
            recognition_evidence=audit_evidence,
            reference_retrievals=retrievals,
            normalized_audio_path=normalized_audio,
        )
        text_index, text_artifacts = _address_native_sources(
            text_sources, namespace="text"
        )
        audio_index, audio_artifacts = _address_native_sources(
            audio_sources, namespace="audio"
        )
        artifacts: dict[str, bytes] = {
            _NATIVE_AUDIT_PLAN: canonical_json_bytes(audit_plan),
            _NATIVE_CANDIDATE_SIGNALS: canonical_json_bytes(signals),
            _NATIVE_CANDIDATE_GROUPS: canonical_json_bytes(groups),
            _NATIVE_EXECUTION_PLAN: canonical_json_bytes(execution),
            _CHECKPOINT_CANONICAL: canonical_json_bytes(result.transcript),
            _CHECKPOINT_RISKS: canonical_json_bytes(
                tuple(_risk_payload(item) for item in result.risks)
            ),
            _REFERENCES: canonical_json_bytes(references),
            _RETRIEVALS: canonical_json_bytes(retrievals),
            _NATIVE_TEXT_SOURCE_INDEX: text_index,
            _NATIVE_AUDIO_SOURCE_INDEX: audio_index,
            **text_artifacts,
            **audio_artifacts,
        }
        checkpoint = self._commit_native_child_checkpoint(
            parent=evidence_state.checkpoint,
            stage="native_audit_basis_ready",
            artifacts=artifacts,
        )
        return _NativeAuditBasisState(
            evidence_state=evidence_state,
            result=result,
            references=references,
            retrievals=retrievals,
            audit_plan=audit_plan,
            candidate_signal_set=signals,
            candidate_group_set=groups,
            execution_plan=execution,
            text_source_artifacts=text_sources,
            audio_source_artifacts=audio_sources,
            checkpoint=checkpoint,
        )

    def _load_native_source_set(
        self,
        stored: StoredCreateCheckpoint,
        index_name: str,
    ) -> tuple[dict[str, bytes], set[str]]:
        index_bytes = self.store.read_create_checkpoint_artifact(stored, index_name)
        index = _canonical_json_value(index_bytes, label=index_name)
        if not isinstance(index, dict) or set(index) != {"schema_version", "entries"}:
            raise GenerationIsolationError("native source index schema is invalid")
        if index["schema_version"] != 1 or not isinstance(index["entries"], list):
            raise GenerationIsolationError("native source index version is invalid")
        sources: dict[str, bytes] = {}
        names: set[str] = {index_name}
        for entry in index["entries"]:
            if not isinstance(entry, dict) or set(entry) != {
                "uri",
                "artifact_name",
                "sha256",
                "size_bytes",
            }:
                raise GenerationIsolationError("native source index entry is invalid")
            uri = entry["uri"]
            name = entry["artifact_name"]
            if not isinstance(uri, str) or not isinstance(name, str) or uri in sources:
                raise GenerationIsolationError("native source index identity is invalid")
            payload = self.store.read_create_checkpoint_artifact(stored, name)
            if (
                entry["sha256"] != sha256_bytes(payload)
                or entry["size_bytes"] != len(payload)
            ):
                raise GenerationIsolationError("native source index digest mismatch")
            sources[uri] = payload
            if name in names:
                raise GenerationIsolationError("native source index repeats an artifact")
            names.add(name)
        return sources, names

    def _load_native_audit_basis_checkpoint(
        self,
        stored: StoredCreateCheckpoint,
        *,
        evidence_state: _CreateEvidenceState,
        request: CreateRequest,
    ) -> _NativeAuditBasisState:
        bundle = self._native_full_audit
        if bundle is None:
            raise GenerationIsolationError("native audit basis requires native composition")
        checkpoint = stored.checkpoint
        self._assert_checkpoint_inherits(
            checkpoint,
            evidence_state.checkpoint,
            expected_child_stage="native_audit_basis_ready",
            expected_parent_stage="evidence_ready",
        )
        audit_plan = self._load_native_model(self.store, stored, _NATIVE_AUDIT_PLAN, AuditPlan)
        signals = self._load_native_model(
            self.store, stored, _NATIVE_CANDIDATE_SIGNALS, CandidateSignalSet
        )
        groups = self._load_native_model(
            self.store, stored, _NATIVE_CANDIDATE_GROUPS, CandidateGroupSet
        )
        execution = self._load_native_model(
            self.store, stored, _NATIVE_EXECUTION_PLAN, CorrectionAuditExecutionPlanV2
        )
        transcript = self._load_native_model(
            self.store, stored, _CHECKPOINT_CANONICAL, CanonicalTranscript
        )
        assert isinstance(audit_plan, AuditPlan)
        assert isinstance(signals, CandidateSignalSet)
        assert isinstance(groups, CandidateGroupSet)
        assert isinstance(execution, CorrectionAuditExecutionPlanV2)
        assert isinstance(transcript, CanonicalTranscript)
        reference_payload = _canonical_json_value(
            self.store.read_create_checkpoint_artifact(stored, _REFERENCES),
            label=_REFERENCES,
        )
        retrieval_payload = _canonical_json_value(
            self.store.read_create_checkpoint_artifact(stored, _RETRIEVALS),
            label=_RETRIEVALS,
        )
        risk_payload = _canonical_json_value(
            self.store.read_create_checkpoint_artifact(stored, _CHECKPOINT_RISKS),
            label=_CHECKPOINT_RISKS,
        )
        collections = (reference_payload, retrieval_payload, risk_payload)
        if not all(isinstance(item, list) for item in collections):
            raise GenerationIsolationError("native audit basis collection schema is invalid")
        references = tuple(ReferenceEvidence.model_validate(item) for item in reference_payload)
        retrievals = tuple(
            ReferenceRetrievalReceipt.model_validate(item) for item in retrieval_payload
        )
        risks = tuple(_risk_from_payload(item) for item in risk_payload)
        expected_result = self._native_basis_result(
            request=request,
            evidence_state=evidence_state,
            references=references,
            retrievals=retrievals,
        )
        if transcript != expected_result.transcript or risks != expected_result.risks:
            raise GenerationIsolationError("native audit Canonical basis is not reproducible")
        references_enrolled = checkpoint.reference_enrollment_hash != hash_object(())
        audit_evidence = tuple(
            sorted(
                evidence_state.evidence,
                key=recognition_evidence_content_hash,
            )
        )
        expected_plan = build_audit_plan(
            transcript,
            audit_evidence,
            bundle.audit_policy,
            reference_retrievals=retrievals,
            references_enrolled=references_enrolled,
            speech_coverage=evidence_state.speech_coverage_receipt,
            boundary_constraints=None,
            seam_evidence=None,
        )
        expected_signals = derive_candidate_signal_set(
            expected_plan,
            transcript,
            audit_evidence,
            reference_retrievals=retrievals,
            references_enrolled=references_enrolled,
            speech_coverage=evidence_state.speech_coverage_receipt,
            boundary_constraints=None,
            seam_evidence=None,
        )
        expected_groups = derive_candidate_group_set(
            expected_plan, expected_signals, transcript
        )
        normalized_audio = self.store.audio_path(
            evidence_state.receipt.normalized.sha256,
            size_bytes=evidence_state.receipt.normalized.size_bytes,
        )
        expected_execution = build_correction_audit_execution_plan(
            expected_plan,
            expected_signals,
            expected_groups,
            transcript,
            audit_evidence,
            bundle.execution_policy,
            reference_retrievals=retrievals,
            seam_evidence=None,
            normalized_audio_path=normalized_audio,
        )
        if (audit_plan, signals, groups, execution) != (
            expected_plan,
            expected_signals,
            expected_groups,
            expected_execution,
        ):
            raise GenerationIsolationError("native audit basis parents are not reproducible")
        text_sources, text_names = self._load_native_source_set(
            stored, _NATIVE_TEXT_SOURCE_INDEX
        )
        audio_sources, audio_names = self._load_native_source_set(
            stored, _NATIVE_AUDIO_SOURCE_INDEX
        )
        expected_text, expected_audio = self._native_source_sets(
            audit_plan=audit_plan,
            candidate_signal_set=signals,
            candidate_group_set=groups,
            execution_plan=execution,
            transcript=transcript,
            recognition_evidence=audit_evidence,
            reference_retrievals=retrievals,
            normalized_audio_path=normalized_audio,
        )
        fixed_names = {
            _NATIVE_AUDIT_PLAN,
            _NATIVE_CANDIDATE_SIGNALS,
            _NATIVE_CANDIDATE_GROUPS,
            _NATIVE_EXECUTION_PLAN,
            _CHECKPOINT_CANONICAL,
            _CHECKPOINT_RISKS,
            _REFERENCES,
            _RETRIEVALS,
        }
        if (
            text_sources != expected_text
            or audio_sources != expected_audio
            or set(checkpoint.artifact_hashes) != fixed_names | text_names | audio_names
        ):
            raise GenerationIsolationError("native audit source artifacts are not reproducible")
        return _NativeAuditBasisState(
            evidence_state=evidence_state,
            result=expected_result,
            references=references,
            retrievals=retrievals,
            audit_plan=audit_plan,
            candidate_signal_set=signals,
            candidate_group_set=groups,
            execution_plan=execution,
            text_source_artifacts=text_sources,
            audio_source_artifacts=audio_sources,
            checkpoint=checkpoint,
        )

    def _load_native_run_bytes(
        self,
        stored: StoredCreateCheckpoint,
        *,
        index_name: str,
        categories: tuple[str, ...],
    ) -> tuple[dict[str, tuple[bytes, ...]], set[str]]:
        index_payload = self.store.read_create_checkpoint_artifact(stored, index_name)
        index = _canonical_json_value(index_payload, label=index_name)
        if not isinstance(index, dict) or set(index) != {"schema_version", *categories}:
            raise GenerationIsolationError("native audit run index schema is invalid")
        if index["schema_version"] != 1:
            raise GenerationIsolationError("native audit run index version is invalid")
        result: dict[str, tuple[bytes, ...]] = {}
        names: set[str] = {index_name}
        for category in categories:
            category_names = index[category]
            if not isinstance(category_names, list) or any(
                not isinstance(item, str) for item in category_names
            ):
                raise GenerationIsolationError("native audit run index paths are invalid")
            payloads: list[bytes] = []
            for name in category_names:
                if name in names:
                    raise GenerationIsolationError("native audit run index repeats an artifact")
                names.add(name)
                payloads.append(self.store.read_create_checkpoint_artifact(stored, name))
            result[category] = tuple(payloads)
        return result, names

    def _commit_native_text_audit_checkpoint(
        self,
        *,
        basis: _NativeAuditBasisState,
        run: TextAuditRunResult,
    ) -> _NativeTextAuditState:
        index, run_artifacts = _address_native_run_bytes(
            prefix="text",
            categories={
                "requests": run.request_bytes,
                "responses": run.response_bytes,
            },
        )
        artifacts = {
            _NATIVE_TEXT_RECORD: canonical_json_bytes(run.record),
            _NATIVE_TEXT_RUN_INDEX: index,
            **run_artifacts,
        }
        checkpoint = self._commit_native_child_checkpoint(
            parent=basis.checkpoint,
            stage="native_text_audit_ready",
            artifacts=artifacts,
        )
        return _NativeTextAuditState(basis=basis, run=run, checkpoint=checkpoint)

    def _load_native_text_audit_checkpoint(
        self,
        stored: StoredCreateCheckpoint,
        *,
        basis: _NativeAuditBasisState,
    ) -> _NativeTextAuditState:
        bundle = self._native_full_audit
        if bundle is None:
            raise GenerationIsolationError("native text audit requires native composition")
        self._assert_checkpoint_inherits(
            stored.checkpoint,
            basis.checkpoint,
            expected_child_stage="native_text_audit_ready",
            expected_parent_stage="native_audit_basis_ready",
        )
        record = self._load_native_model(
            self.store, stored, _NATIVE_TEXT_RECORD, TextAuditExecutionRecordV2
        )
        assert isinstance(record, TextAuditExecutionRecordV2)
        raw, names = self._load_native_run_bytes(
            stored,
            index_name=_NATIVE_TEXT_RUN_INDEX,
            categories=("requests", "responses"),
        )
        run = TextAuditRunResult(
            record=record,
            request_bytes=raw["requests"],
            response_bytes=raw["responses"],
        )
        try:
            replayed = bundle.text_executor.replay(
                basis.execution_plan,
                basis.audit_plan,
                basis.text_source_artifacts,
                run,
            )
        except ValueError as exc:
            raise GenerationIsolationError("native text audit is not replayable") from exc
        if replayed != run or set(stored.checkpoint.artifact_hashes) != {
            _NATIVE_TEXT_RECORD,
            *names,
        }:
            raise GenerationIsolationError("native text audit artifact set drifted")
        return _NativeTextAuditState(basis=basis, run=run, checkpoint=stored.checkpoint)

    def _continue_native_text_audit(
        self,
        *,
        state: _NativeAuditBasisState,
        active_speaker_identity: AdapterIdentity | None,
        enrolled_artifacts: tuple[ReferenceArtifact, ...],
        reference_source_snapshots: dict[str, bytes],
        reference_extraction_snapshots: dict[str, bytes],
    ) -> GenerationOutcome:
        bundle = self._native_full_audit
        if bundle is None:
            raise GenerationIsolationError("native text audit requires native composition")
        try:
            run = bundle.text_executor.execute(
                state.execution_plan,
                state.audit_plan,
                state.text_source_artifacts,
            )
        except TextAuditWorkPending as pending:
            generation_id = state.result.transcript.generation_id
            return Interrupted(
                operation="create",
                reason=str(pending),
                generation_id=generation_id,
                audit_basis_generation_id=generation_id,
                packet_paths=pending.request_paths,
                response_paths=pending.response_paths,
            )
        text_state = self._commit_native_text_audit_checkpoint(basis=state, run=run)
        return self._continue_native_audio_audit(
            state=text_state,
            active_speaker_identity=active_speaker_identity,
            enrolled_artifacts=enrolled_artifacts,
            reference_source_snapshots=reference_source_snapshots,
            reference_extraction_snapshots=reference_extraction_snapshots,
        )

    def _commit_native_audio_audit_checkpoint(
        self,
        *,
        text: _NativeTextAuditState,
        run: AudioAuditRunResult,
        aggregate: FullAuditAggregateAttestationV2,
    ) -> _NativeAudioAuditState:
        journal_bytes = tuple(
            canonical_json_bytes(item) for item in run.record.invocation_journals
        )
        index, run_artifacts = _address_native_run_bytes(
            prefix="audio",
            categories={
                "requests": run.request_bytes,
                "responses": run.response_bytes,
                "clips": run.clip_bytes,
                "journals": journal_bytes,
            },
        )
        artifacts = {
            _NATIVE_AUDIO_RECORD: canonical_json_bytes(run.record),
            _NATIVE_AGGREGATE: canonical_json_bytes(aggregate),
            _NATIVE_AUDIO_RUN_INDEX: index,
            **run_artifacts,
        }
        checkpoint = self._commit_native_child_checkpoint(
            parent=text.checkpoint,
            stage="native_audio_audit_ready",
            artifacts=artifacts,
        )
        return _NativeAudioAuditState(
            text=text,
            run=run,
            aggregate=aggregate,
            checkpoint=checkpoint,
        )

    def _load_native_audio_audit_checkpoint(
        self,
        stored: StoredCreateCheckpoint,
        *,
        text: _NativeTextAuditState,
    ) -> _NativeAudioAuditState:
        bundle = self._native_full_audit
        if bundle is None:
            raise GenerationIsolationError("native audio audit requires native composition")
        self._assert_checkpoint_inherits(
            stored.checkpoint,
            text.checkpoint,
            expected_child_stage="native_audio_audit_ready",
            expected_parent_stage="native_text_audit_ready",
        )
        record = self._load_native_model(
            self.store, stored, _NATIVE_AUDIO_RECORD, AudioAuditExecutionRecordV2
        )
        aggregate = self._load_native_model(
            self.store, stored, _NATIVE_AGGREGATE, FullAuditAggregateAttestationV2
        )
        assert isinstance(record, AudioAuditExecutionRecordV2)
        assert isinstance(aggregate, FullAuditAggregateAttestationV2)
        raw, names = self._load_native_run_bytes(
            stored,
            index_name=_NATIVE_AUDIO_RUN_INDEX,
            categories=("requests", "responses", "clips", "journals"),
        )
        if raw["journals"] != tuple(
            canonical_json_bytes(item) for item in record.invocation_journals
        ):
            raise GenerationIsolationError("native audio invocation journal bytes drifted")
        run = AudioAuditRunResult(
            record=record,
            request_bytes=raw["requests"],
            response_bytes=raw["responses"],
            clip_bytes=raw["clips"],
        )
        basis = text.basis
        try:
            replayed = bundle.audio_executor.replay(
                basis.execution_plan,
                basis.audit_plan,
                basis.audio_source_artifacts,
                run,
            )
            verified_aggregate = verify_full_audit_aggregate(
                canonical_json_bytes(aggregate),
                audit_plan=basis.audit_plan,
                candidate_signal_set=basis.candidate_signal_set,
                candidate_group_set=basis.candidate_group_set,
                execution_plan=basis.execution_plan,
                text_record=text.run.record,
                audio_record=record,
                text_request_bytes=text.run.request_bytes,
                text_response_bytes=text.run.response_bytes,
                audio_request_bytes=run.request_bytes,
                audio_response_bytes=run.response_bytes,
                audio_clip_bytes=run.clip_bytes,
            )
        except ValueError as exc:
            raise GenerationIsolationError("native audio audit is not replayable") from exc
        if (
            replayed != run
            or verified_aggregate != aggregate
            or set(stored.checkpoint.artifact_hashes)
            != {_NATIVE_AUDIO_RECORD, _NATIVE_AGGREGATE, *names}
        ):
            raise GenerationIsolationError("native audio audit artifact set drifted")
        return _NativeAudioAuditState(
            text=text,
            run=run,
            aggregate=aggregate,
            checkpoint=stored.checkpoint,
        )

    def _continue_native_audio_audit(
        self,
        *,
        state: _NativeTextAuditState,
        active_speaker_identity: AdapterIdentity | None,
        enrolled_artifacts: tuple[ReferenceArtifact, ...],
        reference_source_snapshots: dict[str, bytes],
        reference_extraction_snapshots: dict[str, bytes],
    ) -> GenerationOutcome:
        bundle = self._native_full_audit
        if bundle is None:
            raise GenerationIsolationError("native audio audit requires native composition")
        basis = state.basis
        try:
            run = bundle.audio_executor.execute(
                basis.execution_plan,
                basis.audit_plan,
                basis.audio_source_artifacts,
            )
        except AudioAuditWorkPending as pending:
            generation_id = basis.result.transcript.generation_id
            return Interrupted(
                operation="create",
                reason=str(pending),
                generation_id=generation_id,
                audit_basis_generation_id=generation_id,
                packet_paths=pending.request_paths,
                clip_paths=pending.clip_paths,
                response_paths=pending.response_paths,
            )
        except AudioAuditInvocationAmbiguous as ambiguous:
            generation_id = basis.result.transcript.generation_id
            return Interrupted(
                operation="create",
                reason=str(ambiguous),
                generation_id=generation_id,
                audit_basis_generation_id=generation_id,
            )
        aggregate = build_full_audit_aggregate(
            audit_plan=basis.audit_plan,
            candidate_signal_set=basis.candidate_signal_set,
            candidate_group_set=basis.candidate_group_set,
            execution_plan=basis.execution_plan,
            text_record=state.run.record,
            audio_record=run.record,
            text_request_bytes=state.run.request_bytes,
            text_response_bytes=state.run.response_bytes,
            audio_request_bytes=run.request_bytes,
            audio_response_bytes=run.response_bytes,
            audio_clip_bytes=run.clip_bytes,
        )
        audio_state = self._commit_native_audio_audit_checkpoint(
            text=state,
            run=run,
            aggregate=aggregate,
        )
        return self._finish_native_full_audit(
            state=audio_state,
            active_speaker_identity=active_speaker_identity,
            enrolled_artifacts=enrolled_artifacts,
            reference_source_snapshots=reference_source_snapshots,
            reference_extraction_snapshots=reference_extraction_snapshots,
        )

    def _finish_native_full_audit(
        self,
        *,
        state: _NativeAudioAuditState,
        active_speaker_identity: AdapterIdentity | None,
        enrolled_artifacts: tuple[ReferenceArtifact, ...],
        reference_source_snapshots: dict[str, bytes],
        reference_extraction_snapshots: dict[str, bytes],
    ) -> GenerationOutcome:
        outcome = self._commit_native_generation(
            state=state,
            active_speaker_identity=active_speaker_identity,
            enrolled_artifacts=enrolled_artifacts,
            reference_source_snapshots=reference_source_snapshots,
            reference_extraction_snapshots=reference_extraction_snapshots,
        )
        return self._seal_and_activate_create(
            audio_checkpoint=state.checkpoint,
            outcome=outcome,
        )

    def _commit_native_generation(
        self,
        *,
        state: _NativeAudioAuditState,
        active_speaker_identity: AdapterIdentity | None,
        enrolled_artifacts: tuple[ReferenceArtifact, ...],
        reference_source_snapshots: dict[str, bytes],
        reference_extraction_snapshots: dict[str, bytes],
    ) -> NeedsReview:
        bundle = self._native_full_audit
        if bundle is None:
            raise GenerationIsolationError("native Generation requires native composition")
        basis = state.text.basis
        evidence_state = basis.evidence_state
        result = basis.result
        transcript = result.transcript
        _validate_canonical_acceptance_identity(transcript)
        if result.outcome != "needs_review" or transcript.status == "accepted":
            raise GenerationIsolationError(
                "native Full Audit cannot approve a Canonical Generation"
            )
        if transcript.full_audit_receipt_set_hash is not None:
            raise GenerationIsolationError(
                "native Full Audit aggregate cannot impersonate legacy release approval"
            )
        verify_full_audit_aggregate(
            canonical_json_bytes(state.aggregate),
            audit_plan=basis.audit_plan,
            candidate_signal_set=basis.candidate_signal_set,
            candidate_group_set=basis.candidate_group_set,
            execution_plan=basis.execution_plan,
            text_record=state.text.run.record,
            audio_record=state.run.record,
            text_request_bytes=state.text.run.request_bytes,
            text_response_bytes=state.text.run.response_bytes,
            audio_request_bytes=state.run.request_bytes,
            audio_response_bytes=state.run.response_bytes,
            audio_clip_bytes=state.run.clip_bytes,
        )
        _validate_canonical_timing_provenance(
            transcript,
            evidence_state.evidence,
            evidence_state.orthographic_projections,
        )
        _validate_review_risk_evidence_membership(
            transcript=transcript,
            risks=result.risks,
            evidence=evidence_state.evidence,
            references=basis.references,
            speech_coverage_receipt=evidence_state.speech_coverage_receipt,
            context="native Full Audit Generation commit",
        )
        artifacts = self._evidence_checkpoint_artifacts(
            evidence_state,
            enrolled_artifacts=enrolled_artifacts,
            reference_source_snapshots=reference_source_snapshots,
            reference_extraction_snapshots=reference_extraction_snapshots,
        )
        for stored_checkpoint in (
            self.store.load_create_checkpoint_id(basis.checkpoint.id),
            self.store.load_create_checkpoint_id(state.text.checkpoint.id),
            self.store.load_create_checkpoint_id(state.checkpoint.id),
        ):
            for name in stored_checkpoint.checkpoint.artifact_hashes:
                payload = self.store.read_create_checkpoint_artifact(
                    stored_checkpoint, name
                )
                existing = artifacts.get(name)
                if existing is not None and existing != payload:
                    raise GenerationIsolationError(
                        "native Generation artifact has conflicting checkpoint bytes"
                    )
                artifacts[name] = payload
        artifacts[_CANONICAL] = canonical_json_bytes(transcript)
        artifacts[_RISKS] = canonical_json_bytes(
            tuple(_risk_payload(item) for item in result.risks)
        )
        adapter_payload = {
            "recognizers": self._runtime_recognizer_identities(),
            "speech_coverage_analyzer": self._speech_coverage_analyzer_identity,
            "speaker_attributor": active_speaker_identity,
            "reference_retriever": self._reference_retriever_identity,
            "native_text_full_audit": bundle.text_executor.identity,
            "native_text_policy": bundle.text_executor.policy,
            "native_audio_full_audit": bundle.audio_executor.identity,
            "native_audio_policy": bundle.audio_executor.policy,
            "native_audit_policy": bundle.audit_policy,
            "native_execution_policy": bundle.execution_policy,
        }
        artifacts[_ADAPTER_IDENTITIES] = canonical_json_bytes(adapter_payload)
        stage_hashes = {
            "adapter_identities": sha256_bytes(artifacts[_ADAPTER_IDENTITIES]),
            "normalization_receipt": hash_object(evidence_state.receipt),
            "recognition_request": evidence_state.recognition_request.content_hash,
            "recognition_independence": (
                evidence_state.recognition_independence.content_hash
                if evidence_state.recognition_independence is not None
                else hash_object(None)
            ),
            "recognition_evidence": hash_object(evidence_state.evidence),
            "orthographic_projection_evidence": hash_object(
                evidence_state.orthographic_projections
            ),
            "reference_evidence": reference_evidence_set_hash(basis.references),
            "reference_retrieval_receipts": hash_object(basis.retrievals),
            "canonical_transcript": hash_object(transcript),
            "risks": hash_object(tuple(_risk_payload(item) for item in result.risks)),
            "native_audit_plan": sha256_bytes(artifacts[_NATIVE_AUDIT_PLAN]),
            "native_candidate_signal_set": sha256_bytes(
                artifacts[_NATIVE_CANDIDATE_SIGNALS]
            ),
            "native_candidate_group_set": sha256_bytes(
                artifacts[_NATIVE_CANDIDATE_GROUPS]
            ),
            "native_execution_plan": sha256_bytes(artifacts[_NATIVE_EXECUTION_PLAN]),
            "native_text_audit_record": sha256_bytes(artifacts[_NATIVE_TEXT_RECORD]),
            "native_audio_audit_record": sha256_bytes(artifacts[_NATIVE_AUDIO_RECORD]),
            "native_full_audit_aggregate": sha256_bytes(artifacts[_NATIVE_AGGREGATE]),
        }
        manifest = GenerationManifest(
            id=f"manifest-{transcript.generation_id.removeprefix('generation-')}",
            episode_id=transcript.episode_id,
            generation_id=transcript.generation_id,
            parent_manifest_hash=None,
            input_artifact_hashes={
                "source_audio": transcript.source_audio_hash,
                "normalized_audio": transcript.normalized_audio_hash,
                "normalization_receipt": transcript.normalization_receipt_hash,
                "recognition_request": evidence_state.recognition_request.content_hash,
                "recognition_independence": (
                    evidence_state.recognition_independence.content_hash
                    if evidence_state.recognition_independence is not None
                    else hash_object(None)
                ),
                "recognition_evidence": transcript.evidence_hash,
                "orthographic_projection_evidence": (
                    transcript.orthographic_projection_evidence_hash
                ),
                "reference_enrollments": hash_object(enrolled_artifacts),
                "references": transcript.reference_evidence_hash,
                "ledger_head": transcript.ledger_hash,
            },
            code_hash=self._code_hash,
            policy_hash=transcript.policy_hash,
            adapter_hash=hash_object(adapter_payload),
            stage_artifact_hashes=stage_hashes,
            status="complete",
        )
        artifacts[_MANIFEST] = canonical_json_bytes(manifest)
        stored = self.store.commit(
            logical_generation_id=transcript.generation_id,
            manifest=manifest,
            artifacts=artifacts,
        )
        if stored.generation_id != transcript.generation_id:
            raise GenerationIsolationError("store changed native Canonical Generation ID")
        issues = tuple(
            item for item in transcript.review_issues if item.status == "unresolved"
        )
        return NeedsReview(stored.generation_id, transcript, issues, manifest)

    def _commit_correction_checkpoint(
        self,
        *,
        evidence_state: _CreateEvidenceState,
        enrolled_artifacts: tuple[ReferenceArtifact, ...],
        reference_source_snapshots: dict[str, bytes],
        reference_extraction_snapshots: dict[str, bytes],
        result: CanonicalBuildResult,
        references: tuple[ReferenceEvidence, ...],
        retrievals: tuple[ReferenceRetrievalReceipt, ...],
        proposals: tuple[CorrectionProposal, ...],
        correction_audits: tuple[CorrectionAuditReceipt, ...],
        targeted_correction_audits: tuple[_TargetedCorrectionAuditReceipt, ...],
        correction_execution_artifacts: dict[str, bytes],
        preaudit_transcript: CanonicalTranscript,
    ) -> _CreateCorrectionState:
        self._validate_reference_retrieval_state(
            transcript=preaudit_transcript,
            policy=evidence_state.reference_retrieval_policy,
            invocation_id=evidence_state.invocation_id,
            enrolled_artifacts=enrolled_artifacts,
            references=references,
            retrievals=retrievals,
            reference_extraction_snapshots=reference_extraction_snapshots,
            context="Create Checkpoint commit",
        )
        proposal_by_id = {proposal.id: proposal for proposal in proposals}
        for audit in correction_audits:
            try:
                audited = tuple(proposal_by_id[item] for item in audit.proposal_ids)
            except KeyError as exc:
                raise GenerationIsolationError(
                    "Create Checkpoint Correction Audit cites an unknown Proposal"
                ) from exc
            self._verify_correction_execution_replay(
                audit=audit,
                transcript=preaudit_transcript,
                evidence=evidence_state.evidence,
                references=references,
                artifacts=correction_execution_artifacts,
                audited=audited,
                context="Create Checkpoint commit",
            )
        if targeted_correction_audits:
            raise GenerationIsolationError(
                "Create Checkpoint cannot commit targeted Correction audits without "
                "an explicit targeted pre-audit state"
            )
        artifacts = self._evidence_checkpoint_artifacts(
            evidence_state,
            enrolled_artifacts=enrolled_artifacts,
            reference_source_snapshots=reference_source_snapshots,
            reference_extraction_snapshots=reference_extraction_snapshots,
        )
        values: dict[str, object] = {
            _REFERENCES: references,
            _RETRIEVALS: retrievals,
            _PROPOSALS: proposals,
            _CORRECTION_AUDITS: correction_audits,
            _TARGETED_CORRECTION_AUDITS: targeted_correction_audits,
            _CHECKPOINT_CANONICAL: result.transcript,
            _CHECKPOINT_RISKS: tuple(_risk_payload(risk) for risk in result.risks),
            **correction_execution_artifacts,
        }
        artifacts.update(
            {
                name: _checkpoint_artifact_bytes(value)
                for name, value in values.items()
            }
        )
        checkpoint = build_create_checkpoint(
            operation_key=evidence_state.checkpoint.operation_key,
            episode_id=evidence_state.checkpoint.episode_id,
            stage="correction_ready",
            invocation_id=evidence_state.invocation_id,
            source=evidence_state.receipt.source,
            normalized=evidence_state.receipt.normalized,
            normalization_receipt_hash=evidence_state.receipt_hash,
            input_binding_hash=evidence_state.checkpoint.input_binding_hash,
            policy_hash=evidence_state.checkpoint.policy_hash,
            code_hash=evidence_state.checkpoint.code_hash,
            reference_enrollment_hash=(
                evidence_state.checkpoint.reference_enrollment_hash
            ),
            speaker_track_binding_hash=(
                evidence_state.checkpoint.speaker_track_binding_hash
            ),
            adapter_identities=evidence_state.checkpoint.adapter_identities,
            artifact_hashes={
                name: sha256_bytes(payload) for name, payload in sorted(artifacts.items())
            },
            previous_checkpoint_id=evidence_state.checkpoint.id,
            expected_active_generation_id=(
                evidence_state.checkpoint.expected_active_generation_id
            ),
        )
        self.store.commit_create_checkpoint(checkpoint, artifacts=artifacts)
        return _CreateCorrectionState(
            evidence_state=evidence_state,
            result=result,
            references=references,
            retrievals=retrievals,
            proposals=proposals,
            correction_audits=correction_audits,
            targeted_correction_audits=targeted_correction_audits,
            correction_execution_artifacts=correction_execution_artifacts,
            checkpoint=checkpoint,
        )

    def _load_correction_checkpoint(
        self,
        stored: StoredCreateCheckpoint,
        *,
        evidence_state: _CreateEvidenceState,
        request: CreateRequest,
    ) -> _CreateCorrectionState:
        checkpoint = stored.checkpoint

        def load_json(name: str) -> object:
            return json.loads(self.store.read_create_checkpoint_artifact(stored, name))

        references = tuple(
            ReferenceEvidence.model_validate(item) for item in load_json(_REFERENCES)
        )
        retrievals = tuple(
            ReferenceRetrievalReceipt.model_validate(item)
            for item in load_json(_RETRIEVALS)
        )
        proposals = tuple(
            CorrectionProposal(**item) for item in load_json(_PROPOSALS)
        )
        correction_audits = tuple(
            CorrectionAuditReceipt.model_validate(item)
            for item in load_json(_CORRECTION_AUDITS)
        )
        targeted_audits = tuple(
            _TargetedCorrectionAuditReceipt(**item)
            for item in load_json(_TARGETED_CORRECTION_AUDITS)
        )
        if len({item.id for item in references}) != len(references):
            raise GenerationIsolationError(
                "Create Checkpoint Reference Evidence IDs are not unique"
            )
        enrollment_by_source = {
            item.artifact.source_id: item for item in request.reference_enrollments
        }
        for item in references:
            enrollment = enrollment_by_source.get(item.artifact.source_id)
            if enrollment is None or _portable_reference_artifact(
                enrollment.artifact
            ) != item.artifact:
                raise GenerationIsolationError(
                    "Create Checkpoint Reference Evidence crossed enrollment lineage"
                )
            verify_reference_evidence_membership(
                item,
                enrollment.extraction_snapshot,
                enrolled_artifact=enrollment.artifact,
            )
        reference_ids = {item.id for item in references}
        for retrieval in retrievals:
            if (
                retrieval.episode_id != request.episode_id
                or retrieval.invocation_id != evidence_state.invocation_id
                or any(item.id not in reference_ids for item in retrieval.evidence)
            ):
                raise GenerationIsolationError(
                    "Create Checkpoint Reference Retrieval crossed invocation/evidence"
                )
            identity = self._reference_retriever_identity
            if identity is None or (
                retrieval.retriever != identity.name
                or retrieval.retriever_version != identity.version
            ):
                raise GenerationIsolationError(
                    "Create Checkpoint Reference Retrieval Adapter identity drifted"
                )
            runtime_index = getattr(self._reference_retriever, "index", None)
            if runtime_index is not None and retrieval.index_hash != getattr(
                runtime_index, "index_hash", None
            ):
                raise GenerationIsolationError(
                    "Create Checkpoint Reference index identity drifted"
                )

        full_ids = tuple(
            proposal_id for audit in correction_audits for proposal_id in audit.proposal_ids
        )
        proposal_by_id = {item.id: item for item in proposals}
        if len(proposal_by_id) != len(proposals) or any(
            proposal_id not in proposal_by_id for proposal_id in full_ids
        ):
            raise GenerationIsolationError(
                "Create Checkpoint correction receipts cite unknown proposals"
            )
        initial_proposals = tuple(proposal_by_id[item] for item in full_ids)
        rebuilt = reconcile_canonical(
            primary=evidence_state.evidence[0],
            corroborating=evidence_state.evidence[1:],
            source_audio_hash=evidence_state.source_hash,
            normalization_receipt_hash=evidence_state.receipt_hash,
            policy_hash=checkpoint.policy_hash,
            acceptance_policy=request.policy.acceptance_snapshot,
            references=references,
            orthographic_projections=evidence_state.orthographic_projections,
        )
        rebuilt = attest_speech_coverage(
            rebuilt, evidence_state.speech_coverage_receipt
        )
        checkpoint_reference_failure_risks = _reference_retrieval_failure_risks(
            retrievals
        )
        if checkpoint_reference_failure_risks:
            rebuilt = add_review_risks(rebuilt, checkpoint_reference_failure_risks)
        preaudit_result = rebuilt
        self._validate_reference_retrieval_state(
            transcript=preaudit_result.transcript,
            policy=evidence_state.reference_retrieval_policy,
            invocation_id=evidence_state.invocation_id,
            enrolled_artifacts=tuple(
                _portable_reference_artifact(item) for item in request.reference_artifacts
            ),
            references=references,
            retrievals=retrievals,
            reference_extraction_snapshots={
                f"reference_extractions/{item.artifact.extracted_text.sha256}.json": (
                    item.extraction_snapshot
                )
                for item in request.reference_enrollments
            },
            context="Create Checkpoint reload",
        )
        result_with_initial = (
            add_correction_proposals(rebuilt, initial_proposals)
            if initial_proposals
            else rebuilt
        )
        rebuilt = (
            add_correction_proposals(
                rebuilt,
                proposals,
                allowed_reference_ids=tuple(item.id for item in references),
            )
            if proposals
            else rebuilt
        )
        stored_transcript = CanonicalTranscript.model_validate(
            load_json(_CHECKPOINT_CANONICAL)
        )
        stored_risks = tuple(
            _risk_from_payload(item) for item in load_json(_CHECKPOINT_RISKS)
        )
        if rebuilt.transcript != stored_transcript or rebuilt.risks != stored_risks:
            raise GenerationIsolationError(
                "Create Checkpoint correction state is not deterministically reproducible"
            )

        execution_artifacts: dict[str, bytes] = {}
        for audit in (*correction_audits, *targeted_audits):
            for digest in (audit.request, audit.response):
                name = digest.uri.removeprefix("generation-artifact://")
                payload = self.store.read_create_checkpoint_artifact(stored, name)
                if sha256_bytes(payload) != digest.sha256 or len(payload) != digest.size_bytes:
                    raise GenerationIsolationError(
                        "Create Checkpoint correction execution bytes drifted"
                    )
                execution_artifacts[name] = payload
            audited = tuple(proposal_by_id[item] for item in audit.proposal_ids)
            self._verify_correction_execution_replay(
                audit=audit,
                transcript=(
                    preaudit_result.transcript
                    if isinstance(audit, CorrectionAuditReceipt)
                    else result_with_initial.transcript
                ),
                evidence=evidence_state.evidence,
                references=references,
                artifacts=execution_artifacts,
                audited=audited,
                context="Create Checkpoint",
            )
        expected_incremental = {
            _REFERENCES,
            _RETRIEVALS,
            _PROPOSALS,
            _CORRECTION_AUDITS,
            _TARGETED_CORRECTION_AUDITS,
            _CHECKPOINT_CANONICAL,
            _CHECKPOINT_RISKS,
            *execution_artifacts,
        }
        evidence_names = set(evidence_state.checkpoint.artifact_hashes)
        if set(checkpoint.artifact_hashes) != evidence_names | expected_incremental:
            raise GenerationIsolationError(
                "Create Checkpoint correction stage artifact set is not closed"
            )
        return _CreateCorrectionState(
            evidence_state=evidence_state,
            result=rebuilt,
            references=references,
            retrievals=retrievals,
            proposals=proposals,
            correction_audits=correction_audits,
            targeted_correction_audits=targeted_audits,
            correction_execution_artifacts=execution_artifacts,
            checkpoint=checkpoint,
        )

    def _correction_checkpoint_artifacts(
        self,
        state: _CreateCorrectionState,
        *,
        enrolled_artifacts: tuple[ReferenceArtifact, ...],
        reference_source_snapshots: dict[str, bytes],
        reference_extraction_snapshots: dict[str, bytes],
    ) -> dict[str, bytes]:
        artifacts = self._evidence_checkpoint_artifacts(
            state.evidence_state,
            enrolled_artifacts=enrolled_artifacts,
            reference_source_snapshots=reference_source_snapshots,
            reference_extraction_snapshots=reference_extraction_snapshots,
        )
        values: dict[str, object] = {
            _REFERENCES: state.references,
            _RETRIEVALS: state.retrievals,
            _PROPOSALS: state.proposals,
            _CORRECTION_AUDITS: state.correction_audits,
            _TARGETED_CORRECTION_AUDITS: state.targeted_correction_audits,
            _CHECKPOINT_CANONICAL: state.result.transcript,
            _CHECKPOINT_RISKS: tuple(
                _risk_payload(risk) for risk in state.result.risks
            ),
            **state.correction_execution_artifacts,
        }
        artifacts.update(
            {
                name: _checkpoint_artifact_bytes(value)
                for name, value in values.items()
            }
        )
        return artifacts

    def _commit_audio_checkpoint(
        self,
        *,
        correction_state: _CreateCorrectionState,
        enrolled_artifacts: tuple[ReferenceArtifact, ...],
        reference_source_snapshots: dict[str, bytes],
        reference_extraction_snapshots: dict[str, bytes],
        result: CanonicalBuildResult,
        proposals: tuple[CorrectionProposal, ...],
        audio_audits: tuple[AudioAuditReceipt, ...],
        audio_execution_artifacts: dict[str, bytes],
    ) -> _CreateAudioState:
        evidence_state = correction_state.evidence_state
        self._verify_audio_audit_replays(
            transcript=result.transcript,
            normalization_receipt=evidence_state.receipt,
            evidence=evidence_state.evidence,
            references=correction_state.references,
            retrievals=correction_state.retrievals,
            proposals=proposals,
            receipts=audio_audits,
            artifacts=audio_execution_artifacts,
            context="Create Checkpoint",
        )
        artifacts = self._correction_checkpoint_artifacts(
            correction_state,
            enrolled_artifacts=enrolled_artifacts,
            reference_source_snapshots=reference_source_snapshots,
            reference_extraction_snapshots=reference_extraction_snapshots,
        )
        values: dict[str, object] = {
            _AUDIO_AUDITS: audio_audits,
            _CHECKPOINT_AUDIO_PROPOSALS: proposals,
            _CHECKPOINT_AUDIO_CANONICAL: result.transcript,
            _CHECKPOINT_AUDIO_RISKS: tuple(
                _risk_payload(risk) for risk in result.risks
            ),
            **audio_execution_artifacts,
        }
        artifacts.update(
            {
                name: _checkpoint_artifact_bytes(value)
                for name, value in values.items()
            }
        )
        previous = correction_state.checkpoint
        checkpoint = build_create_checkpoint(
            operation_key=previous.operation_key,
            episode_id=previous.episode_id,
            stage="audio_audit_ready",
            invocation_id=previous.invocation_id,
            source=previous.source,
            normalized=previous.normalized,
            normalization_receipt_hash=previous.normalization_receipt_hash,
            input_binding_hash=previous.input_binding_hash,
            policy_hash=previous.policy_hash,
            code_hash=previous.code_hash,
            reference_enrollment_hash=previous.reference_enrollment_hash,
            speaker_track_binding_hash=previous.speaker_track_binding_hash,
            adapter_identities=previous.adapter_identities,
            artifact_hashes={
                name: sha256_bytes(payload) for name, payload in sorted(artifacts.items())
            },
            previous_checkpoint_id=previous.id,
            expected_active_generation_id=previous.expected_active_generation_id,
        )
        self.store.commit_create_checkpoint(checkpoint, artifacts=artifacts)
        return _CreateAudioState(
            correction_state=correction_state,
            result=result,
            proposals=proposals,
            audio_audits=audio_audits,
            audio_execution_artifacts=audio_execution_artifacts,
            checkpoint=checkpoint,
        )

    def _load_audio_checkpoint(
        self,
        stored: StoredCreateCheckpoint,
        *,
        correction_state: _CreateCorrectionState,
    ) -> _CreateAudioState:
        checkpoint = stored.checkpoint

        def load_json(name: str) -> object:
            return json.loads(self.store.read_create_checkpoint_artifact(stored, name))

        audio_audits = tuple(
            AudioAuditReceipt.model_validate(item) for item in load_json(_AUDIO_AUDITS)
        )
        proposals = tuple(
            CorrectionProposal(**item)
            for item in load_json(_CHECKPOINT_AUDIO_PROPOSALS)
        )
        proposal_by_id = {item.id: item for item in proposals}
        if len(proposal_by_id) != len(proposals):
            raise GenerationIsolationError(
                "Create Checkpoint Audio Audit proposals are not unique"
            )
        audio_ids = tuple(
            proposal_id for audit in audio_audits for proposal_id in audit.proposal_ids
        )
        if any(item not in proposal_by_id for item in audio_ids):
            raise GenerationIsolationError(
                "Create Checkpoint Audio Audit cites unknown proposals"
            )
        audio_proposals = tuple(proposal_by_id[item] for item in audio_ids)
        rebuilt = attest_full_audit(correction_state.result, audio_audits)
        if audio_proposals:
            rebuilt = add_correction_proposals(
                rebuilt,
                audio_proposals,
                allowed_reference_ids=tuple(
                    item.id for item in correction_state.references
                ),
            )
        if proposals != _merge_proposal_batches(
            (correction_state.proposals, audio_proposals)
        ):
            raise GenerationIsolationError(
                "Create Checkpoint Audio Audit proposal merge is not reproducible"
            )
        stored_transcript = CanonicalTranscript.model_validate(
            load_json(_CHECKPOINT_AUDIO_CANONICAL)
        )
        stored_risks = tuple(
            _risk_from_payload(item) for item in load_json(_CHECKPOINT_AUDIO_RISKS)
        )
        if rebuilt.transcript != stored_transcript or rebuilt.risks != stored_risks:
            raise GenerationIsolationError(
                "Create Checkpoint Audio Audit state is not reproducible"
            )
        execution_artifacts: dict[str, bytes] = {}
        for audit in audio_audits:
            for digest in (audit.request, audit.response, audit.clip):
                name = digest.uri.removeprefix("generation-artifact://")
                payload = self.store.read_create_checkpoint_artifact(stored, name)
                if sha256_bytes(payload) != digest.sha256 or len(payload) != digest.size_bytes:
                    raise GenerationIsolationError(
                        "Create Checkpoint Audio Audit exact bytes drifted"
                    )
                execution_artifacts[name] = payload
        self._verify_audio_audit_replays(
            transcript=rebuilt.transcript,
            normalization_receipt=correction_state.evidence_state.receipt,
            evidence=correction_state.evidence_state.evidence,
            references=correction_state.references,
            retrievals=correction_state.retrievals,
            proposals=proposals,
            receipts=audio_audits,
            artifacts=execution_artifacts,
            context="Create Checkpoint reload",
        )
        expected_incremental = {
            _AUDIO_AUDITS,
            _CHECKPOINT_AUDIO_PROPOSALS,
            _CHECKPOINT_AUDIO_CANONICAL,
            _CHECKPOINT_AUDIO_RISKS,
            *execution_artifacts,
        }
        if set(checkpoint.artifact_hashes) != (
            set(correction_state.checkpoint.artifact_hashes) | expected_incremental
        ):
            raise GenerationIsolationError(
                "Create Checkpoint Audio Audit artifact set is not closed"
            )
        return _CreateAudioState(
            correction_state=correction_state,
            result=rebuilt,
            proposals=proposals,
            audio_audits=audio_audits,
            audio_execution_artifacts=execution_artifacts,
            checkpoint=checkpoint,
        )

    def create(self, request: CreateRequest) -> GenerationOutcome:
        """Create or resume one generation under the episode execution lease.

        The lease prevents two live processes from entering paid create work for
        the same episode.  It deliberately does not claim provider exactly-once:
        crash recovery inside a paid Adapter still depends on that Adapter's
        durable intent/reconciliation protocol.
        """

        with self.store.create_execution_lease():
            return self._create_under_execution_lease(request)

    def _create_under_execution_lease(
        self, request: CreateRequest
    ) -> GenerationOutcome:
        source = request.source_audio.resolve()
        if not source.is_file():
            raise ModuleInvariantError(f"source audio is not a file: {source}")
        # Bind every optional mic byte before normalization can start external
        # work.  The Attributor rechecks these digests before and after DSP.
        speaker_bindings = _bind_speaker_tracks(request.speaker_tracks)
        if speaker_bindings and self._speaker_attributor is None:
            raise ModuleInvariantError(
                "speaker tracks require a configured Speaker Attributor"
            )
        # Reference parsing and lineage validation can fail.  It must do so
        # before normalization initializes any external provider or uploads
        # audio, even for non-CLI callers.
        (
            enrolled_artifacts,
            reference_source_snapshots,
            reference_extraction_snapshots,
        ) = _snapshot_reference_enrollments(
            request.reference_enrollments,
            parser_registry=self._reference_parser_registry,
        )
        reference_retrieval_policy = request.policy.reference_retrieval_snapshot(
            tuple(request.vocabulary)
        )
        source_snapshot = self.store.snapshot_audio(source)
        source_hash = source_snapshot.sha256
        active_speaker_identity = (
            self._speaker_attributor_identity if speaker_bindings else None
        )
        effective_policy_hash = hash_object(
            {
                "subtitle_policy": request.policy.content_hash,
                "module_code": self._code_hash,
                "editorial_detector": editorial_detector_identity(),
                "orthographic_projection": measure_opencc_s2tw_identity(),
                "audit_mode": (
                    "native_full_audit"
                    if self._native_full_audit is not None
                    else "legacy_corrector_audio_auditor"
                ),
                "corrector": (
                    self._corrector_identity.content_hash
                    if self._native_full_audit is None
                    and self._corrector_identity is not None
                    else None
                ),
                "corrector_execution": (
                    self._corrector_execution_identity.content_hash
                    if self._native_full_audit is None
                    and self._corrector_execution_identity is not None
                    else None
                ),
                "audio_auditor": (
                    self._audio_auditor.identity.content_hash
                    if self._native_full_audit is None
                    and self._audio_auditor is not None
                    else None
                ),
                "native_full_audit": (
                    {
                        "audit_policy": self._native_full_audit.audit_policy,
                        "execution_policy": self._native_full_audit.execution_policy,
                        "text_identity": self._native_full_audit.text_executor.identity,
                        "text_policy": self._native_full_audit.text_executor.policy,
                        "audio_identity": self._native_full_audit.audio_executor.identity,
                        "audio_policy": self._native_full_audit.audio_executor.policy,
                    }
                    if self._native_full_audit is not None
                    else None
                ),
                "speech_coverage_analyzer": self._speech_coverage_analyzer_identity,
                "recognition_independence_policy": (
                    self._recognition_independence_policy.content_hash
                    if self._recognition_independence_policy is not None
                    else None
                ),
                "arbiter": (
                    self._arbiter.identity.content_hash
                    if self._arbiter is not None
                    else None
                ),
                "speaker_attributor": (
                    active_speaker_identity.content_hash
                    if active_speaker_identity is not None
                    else None
                ),
                "speaker_tracks": tuple(
                    binding.portable_identity for binding in speaker_bindings
                ),
                "reference_retriever": (
                    self._reference_retriever_identity.content_hash
                    if self._reference_retriever_identity is not None
                    else None
                ),
                "reference_enrollments": hash_object(enrolled_artifacts),
                "reference_retrieval_policy": reference_retrieval_policy,
            }
        )
        adapter_identities = self._create_checkpoint_adapter_identities(
            active_speaker_identity=active_speaker_identity
        )
        recognition_independence = self._recognition_independence_receipt()
        input_binding_hash = hash_object(
            {
                "episode_id": request.episode_id,
                "source_sha256": source_hash,
                "source_size_bytes": source_snapshot.size_bytes,
                "language": request.language_hint or request.policy.language,
                "vocabulary": request.vocabulary,
                "reference_retrieval_policy": reference_retrieval_policy,
                "effective_policy_hash": effective_policy_hash,
                "reference_enrollments": enrolled_artifacts,
                "reference_sources": tuple(
                    (name, sha256_bytes(payload))
                    for name, payload in sorted(reference_source_snapshots.items())
                ),
                "reference_extractions": tuple(
                    (name, sha256_bytes(payload))
                    for name, payload in sorted(reference_extraction_snapshots.items())
                ),
                "speaker_tracks": tuple(
                    binding.portable_identity for binding in speaker_bindings
                ),
                "adapter_identities": adapter_identities,
            }
        )
        operation_key = hash_object(
            {"operation": "PodcastSubtitleV2.create", "input": input_binding_hash}
        )
        source_digest = ArtifactDigest(
            uri=f"sha256://{source_hash}",
            sha256=source_hash,
            size_bytes=source_snapshot.size_bytes,
        )
        latest_checkpoint = self.store.load_latest_create_checkpoint()
        if latest_checkpoint is None:
            stored_checkpoint = None
        elif latest_checkpoint.checkpoint.operation_key == operation_key:
            stored_checkpoint = latest_checkpoint
        elif latest_checkpoint.checkpoint.stage == "complete":
            # A completed operation does not own the episode forever.  The
            # execution lease serializes capture of a new started intent whose
            # expected parent is the currently authenticated active pointer.
            stored_checkpoint = None
        else:
            raise GenerationIsolationError(
                "Create Checkpoint belongs to different source, references, policy, "
                "microphone tracks, code, or Adapter identities; an incomplete "
                "create operation already owns this episode"
            )
        started_checkpoint: CreateCheckpoint
        if stored_checkpoint is not None:
            if stored_checkpoint.checkpoint.stage == "complete":
                source_snapshot.discard()
                return self._replay_complete_create(
                    stored_checkpoint,
                    request=request,
                    source=source_digest,
                    effective_policy_hash=effective_policy_hash,
                    input_binding_hash=input_binding_hash,
                    adapter_identities=adapter_identities,
                    speaker_bindings=speaker_bindings,
                    enrolled_artifacts=enrolled_artifacts,
                )
            if stored_checkpoint.checkpoint.stage == "started":
                started_checkpoint = self._load_started_checkpoint(
                    stored_checkpoint,
                    request=request,
                    source=source_digest,
                    effective_policy_hash=effective_policy_hash,
                    input_binding_hash=input_binding_hash,
                    adapter_identities=adapter_identities,
                    speaker_bindings=speaker_bindings,
                    enrolled_artifacts=enrolled_artifacts,
                )
            else:
                source_snapshot.discard()
                evidence_checkpoint = stored_checkpoint
                if stored_checkpoint.checkpoint.stage == "correction_ready":
                    previous_id = stored_checkpoint.checkpoint.previous_checkpoint_id
                    assert previous_id is not None
                    evidence_checkpoint = self.store.load_create_checkpoint_id(previous_id)
                elif stored_checkpoint.checkpoint.stage == "audio_audit_ready":
                    correction_id = stored_checkpoint.checkpoint.previous_checkpoint_id
                    assert correction_id is not None
                    correction_checkpoint = self.store.load_create_checkpoint_id(
                        correction_id
                    )
                    evidence_id = correction_checkpoint.checkpoint.previous_checkpoint_id
                    assert evidence_id is not None
                    evidence_checkpoint = self.store.load_create_checkpoint_id(evidence_id)
                elif stored_checkpoint.checkpoint.stage == "native_audit_basis_ready":
                    evidence_id = stored_checkpoint.checkpoint.previous_checkpoint_id
                    assert evidence_id is not None
                    evidence_checkpoint = self.store.load_create_checkpoint_id(evidence_id)
                elif stored_checkpoint.checkpoint.stage == "native_text_audit_ready":
                    basis_id = stored_checkpoint.checkpoint.previous_checkpoint_id
                    assert basis_id is not None
                    basis_checkpoint = self.store.load_create_checkpoint_id(basis_id)
                    evidence_id = basis_checkpoint.checkpoint.previous_checkpoint_id
                    assert evidence_id is not None
                    evidence_checkpoint = self.store.load_create_checkpoint_id(evidence_id)
                elif stored_checkpoint.checkpoint.stage == "native_audio_audit_ready":
                    text_id = stored_checkpoint.checkpoint.previous_checkpoint_id
                    assert text_id is not None
                    text_checkpoint = self.store.load_create_checkpoint_id(text_id)
                    basis_id = text_checkpoint.checkpoint.previous_checkpoint_id
                    assert basis_id is not None
                    basis_checkpoint = self.store.load_create_checkpoint_id(basis_id)
                    evidence_id = basis_checkpoint.checkpoint.previous_checkpoint_id
                    assert evidence_id is not None
                    evidence_checkpoint = self.store.load_create_checkpoint_id(evidence_id)
                started_id = evidence_checkpoint.checkpoint.previous_checkpoint_id
                if started_id is None:
                    raise GenerationIsolationError(
                        "Create Checkpoint evidence stage lacks its started intent"
                    )
                started_stored = self.store.load_create_checkpoint_id(started_id)
                self._load_started_checkpoint(
                    started_stored,
                    request=request,
                    source=source_digest,
                    effective_policy_hash=effective_policy_hash,
                    input_binding_hash=input_binding_hash,
                    adapter_identities=adapter_identities,
                    speaker_bindings=speaker_bindings,
                    enrolled_artifacts=enrolled_artifacts,
                )
                state = self._load_evidence_checkpoint(
                    evidence_checkpoint,
                    request=request,
                    source_hash=source_hash,
                    source_size_bytes=source_digest.size_bytes,
                    effective_policy_hash=effective_policy_hash,
                    input_binding_hash=input_binding_hash,
                    adapter_identities=adapter_identities,
                    speaker_bindings=speaker_bindings,
                    enrolled_artifacts=enrolled_artifacts,
                    reference_source_snapshots=reference_source_snapshots,
                    reference_extraction_snapshots=reference_extraction_snapshots,
                )
                return self._continue_create_from_evidence(
                    request=request,
                    state=state,
                    active_speaker_identity=active_speaker_identity,
                    enrolled_artifacts=enrolled_artifacts,
                    reference_source_snapshots=reference_source_snapshots,
                    reference_extraction_snapshots=reference_extraction_snapshots,
                )
        else:
            started_checkpoint = self._commit_started_checkpoint(
                operation_key=operation_key,
                input_binding_hash=input_binding_hash,
                request=request,
                source=source_digest,
                effective_policy_hash=effective_policy_hash,
                adapter_identities=adapter_identities,
                speaker_bindings=speaker_bindings,
                enrolled_artifacts=enrolled_artifacts,
            )
        normalized_snapshot = None
        try:
            normalized = self._normalizer.normalize(
                NormalizeRequest(
                    source_audio=source_snapshot.path,
                    output_dir=self.store.root / "normalization",
                    expected_source_hash=source_hash,
                )
            )
            receipt = normalized.receipt
            if receipt.status != "accepted":
                raise ModuleInvariantError("create requires an accepted Normalization Receipt")
            try:
                source_snapshot.verify()
            except ArtifactHashMismatchError as exc:
                raise GenerationIsolationError(
                    "source audio changed during normalization"
                ) from exc
            if (
                receipt.source.sha256 != source_hash
                or receipt.source.size_bytes != source_snapshot.size_bytes
            ):
                raise GenerationIsolationError("Normalization Receipt source identity mismatch")
            normalized_path = normalized.normalized_audio.resolve()
            normalized_transport_is_source_snapshot = (
                normalized_path == source_snapshot.path.resolve()
            )
            normalized_snapshot = self.store.snapshot_audio(
                normalized_path,
                expected_sha256=receipt.normalized.sha256,
                expected_size_bytes=receipt.normalized.size_bytes,
            )
            source_snapshot.commit()
        except Exception:
            source_snapshot.discard()
            if normalized_snapshot is not None:
                normalized_snapshot.discard()
            raise

        receipt_hash = normalization_receipt_content_hash(receipt)
        invocation_id = "recognition-" + hash_object(
            {
                "episode_id": request.episode_id,
                "receipt": receipt_hash,
                "policy": effective_policy_hash,
                "language": request.language_hint or request.policy.language,
            }
        )
        raw_output_dir = self.store.root / "invocations" / invocation_id / "raw-evidence"
        recognition_request = RecognitionRequest(
            episode_id=request.episode_id,
            invocation_id=invocation_id,
            normalized_audio=normalized_snapshot.path,
            expected_normalized_audio_hash=receipt.normalized.sha256,
            raw_output_dir=raw_output_dir,
            language_hint=request.language_hint or request.policy.language,
        )
        recognition_request_artifact = build_recognition_request_artifact(
            recognition_request
        )
        invocation_evidence = tuple(
            recognizer.recognize(recognition_request)
            for recognizer in self._recognizers
        )
        try:
            normalized_snapshot.verify()
        except ArtifactHashMismatchError as exc:
            raise GenerationIsolationError(
                "normalized audio changed during recognition"
            ) from exc
        if not normalized_transport_is_source_snapshot and (
            not normalized_path.is_file()
            or hash_file(normalized_path) != receipt.normalized.sha256
            or normalized_path.stat().st_size != receipt.normalized.size_bytes
        ):
            raise GenerationIsolationError("normalized audio changed during recognition")
        for item in invocation_evidence:
            if item.episode_id != request.episode_id or item.invocation_id != invocation_id:
                raise GenerationIsolationError("Recognition Evidence belongs to another invocation")
            if item.normalized_audio_hash != receipt.normalized.sha256:
                raise GenerationIsolationError("Recognition Evidence audio hash mismatch")
        self._verify_recognition_derivations(
            invocation_evidence,
            request=recognition_request,
            context="create",
        )
        base_primary_evidence: RecognitionEvidence | None = None
        speaker_provenance: SpeakerAttributionProvenance | None = None
        canonical_evidence = invocation_evidence
        if speaker_bindings:
            assert self._speaker_attributor is not None
            base_primary_evidence = invocation_evidence[0]
            attributed = self._speaker_attributor.attribute(
                SpeakerAttributionRequest(
                    recognition_request=recognition_request,
                    base_evidence=base_primary_evidence,
                    tracks=speaker_bindings,
                )
            )
            if (
                attributed.episode_id != base_primary_evidence.episode_id
                or attributed.invocation_id != base_primary_evidence.invocation_id
                or attributed.normalized_audio_hash
                != base_primary_evidence.normalized_audio_hash
                or len(attributed.tokens) != len(base_primary_evidence.tokens)
            ):
                raise GenerationIsolationError(
                    "Speaker Attribution changed base Recognition lineage or token count"
                )
            for base_token, attributed_token in zip(
                base_primary_evidence.tokens, attributed.tokens
            ):
                if (
                    attributed_token.text != base_token.text
                    or attributed_token.start_ms != base_token.start_ms
                    or attributed_token.end_ms != base_token.end_ms
                    or attributed_token.confidence != base_token.confidence
                ):
                    raise GenerationIsolationError(
                        "Speaker Attribution changed Recognition truth or timing"
                    )
                if not (attributed_token.speaker or "").strip():
                    raise GenerationIsolationError(
                        "Speaker Attribution left an unknown speaker"
                    )
            try:
                normalized_snapshot.verify()
            except ArtifactHashMismatchError as exc:
                raise GenerationIsolationError(
                    "normalized audio changed during speaker attribution"
                ) from exc
            if any(
                    hash_file(binding.path) != binding.sha256
                    or binding.path.stat().st_size != binding.size_bytes
                    for binding in speaker_bindings
                ):
                raise GenerationIsolationError(
                    "microphone track changed during speaker attribution"
                )
            attributed_raw = _file_uri_path(attributed.raw_output.uri).read_bytes()
            speaker_provenance = self._speaker_attributor.verify(
                attributed,
                base_evidence=base_primary_evidence,
                raw_output=attributed_raw,
            )
            identity = active_speaker_identity
            assert identity is not None
            if speaker_provenance.adapter != identity.name:
                raise GenerationIsolationError(
                    "Speaker Attribution provenance differs from configured Adapter identity"
                )
            if str(speaker_provenance.adapter_version) != identity.version:
                raise GenerationIsolationError(
                    "Speaker Attribution version differs from configured Adapter identity"
                )
            _assert_speaker_attribution_binding(
                attributed=attributed,
                base=base_primary_evidence,
                provenance=speaker_provenance,
                identity=identity,
                expected_tracks=speaker_bindings,
            )
            canonical_evidence = (attributed, *invocation_evidence[1:])
        snapshot_evidence = (
            invocation_evidence
            if base_primary_evidence is None
            else (*invocation_evidence, canonical_evidence[0])
        )
        raw_outputs, portable_evidence = _snapshot_raw_outputs(snapshot_evidence)
        evidence_by_adapter_hash = {
            (item.adapter, item.raw_output.sha256): item for item in portable_evidence
        }
        canonical_evidence = tuple(
            evidence_by_adapter_hash[(item.adapter, item.raw_output.sha256)]
            for item in canonical_evidence
        )
        if base_primary_evidence is not None:
            base_primary_evidence = evidence_by_adapter_hash[
                (base_primary_evidence.adapter, base_primary_evidence.raw_output.sha256)
            ]
            attributed_raw = raw_outputs[
                f"raw_evidence/{canonical_evidence[0].raw_output.sha256}.json"
            ]
            portable_provenance = self._speaker_attributor.verify(
                canonical_evidence[0],
                base_evidence=base_primary_evidence,
                raw_output=attributed_raw,
            )
            if portable_provenance != speaker_provenance:
                raise GenerationIsolationError(
                    "portable Speaker Attribution provenance changed after snapshot"
                )
        evidence = canonical_evidence
        try:
            orthographic_projections = project_recognition_evidence_set(evidence)
        except OrthographicProjectionError as exc:
            raise GenerationIsolationError(
                "Recognition orthographic projection is not reproducible"
            ) from exc
        orthographic_raw_outputs = {
            (
                "orthographic_projection/raw/"
                f"{projection.raw_output.sha256}.txt"
            ): orthographic_projection_raw_output_bytes(projection)
            for projection in orthographic_projections
        }
        if receipt.normalized_duration_ms is None:
            raise ModuleInvariantError(
                "accepted Normalization Receipt lacks normalized-audio duration"
            )
        speech_coverage_receipt: SpeechCoverageReceipt | None = None
        speech_coverage_raw_artifacts: dict[str, bytes] = {}
        speech_coverage_request = SpeechCoverageRequest(
            episode_id=request.episode_id,
            invocation_id=invocation_id,
            normalized_audio=normalized_snapshot.path,
            expected_normalized_audio_hash=receipt.normalized.sha256,
            expected_normalized_audio_size_bytes=receipt.normalized.size_bytes,
            normalized_audio_duration_ms=receipt.normalized_duration_ms,
            recognition_evidence=evidence,
            raw_output_dir=(
                self.store.root / "invocations" / invocation_id / "speech-coverage"
            ),
        )
        if self._speech_coverage_analyzer is not None:
            local_coverage_receipt = self._speech_coverage_analyzer.analyze(
                speech_coverage_request
            )
            speech_coverage_receipt, speech_coverage_raw_artifacts = (
                _snapshot_speech_coverage(local_coverage_receipt)
            )
            verified_coverage = self._speech_coverage_analyzer.verify(
                speech_coverage_receipt,
                request=speech_coverage_request,
                raw_output=next(iter(speech_coverage_raw_artifacts.values())),
            )
            if verified_coverage != speech_coverage_receipt:
                raise GenerationIsolationError(
                    "Speech Coverage Receipt changed during portable verification"
                )
            try:
                normalized_snapshot.verify()
            except ArtifactHashMismatchError as exc:
                raise GenerationIsolationError(
                    "normalized audio changed during speech coverage analysis"
                ) from exc
        normalized_snapshot.commit()
        receipt = _portable_receipt(receipt)
        checkpoint = self._commit_evidence_checkpoint(
            started_checkpoint=started_checkpoint,
            operation_key=operation_key,
            input_binding_hash=input_binding_hash,
            request=request,
            source_hash=source_hash,
            receipt=receipt,
            receipt_hash=receipt_hash,
            invocation_id=invocation_id,
            recognition_request=recognition_request_artifact,
            recognition_independence=recognition_independence,
            effective_policy_hash=effective_policy_hash,
            adapter_identities=adapter_identities,
            speaker_bindings=speaker_bindings,
            evidence=evidence,
            base_primary_evidence=base_primary_evidence,
            speaker_provenance=speaker_provenance,
            orthographic_projections=orthographic_projections,
            orthographic_raw_outputs=orthographic_raw_outputs,
            enrolled_artifacts=enrolled_artifacts,
            speech_coverage_receipt=speech_coverage_receipt,
            speech_coverage_raw_artifacts=speech_coverage_raw_artifacts,
            raw_outputs=raw_outputs,
            reference_retrieval_policy=reference_retrieval_policy,
            reference_source_snapshots=reference_source_snapshots,
            reference_extraction_snapshots=reference_extraction_snapshots,
        )
        return self._continue_create_from_evidence(
            request=request,
            state=_CreateEvidenceState(
                source_hash=source_hash,
                receipt=receipt,
                receipt_hash=receipt_hash,
                invocation_id=invocation_id,
                recognition_request=recognition_request_artifact,
                recognition_independence=recognition_independence,
                evidence=evidence,
                orthographic_projections=orthographic_projections,
                orthographic_raw_outputs=orthographic_raw_outputs,
                base_primary_evidence=base_primary_evidence,
                speaker_provenance=speaker_provenance,
                speech_coverage_receipt=speech_coverage_receipt,
                speech_coverage_raw_artifacts=speech_coverage_raw_artifacts,
                raw_outputs=raw_outputs,
                reference_retrieval_policy=reference_retrieval_policy,
                checkpoint=checkpoint,
            ),
            active_speaker_identity=active_speaker_identity,
            enrolled_artifacts=enrolled_artifacts,
            reference_source_snapshots=reference_source_snapshots,
            reference_extraction_snapshots=reference_extraction_snapshots,
        )

    def _continue_create_from_evidence(
        self,
        *,
        request: CreateRequest,
        state: _CreateEvidenceState,
        active_speaker_identity: AdapterIdentity | None,
        enrolled_artifacts: tuple[ReferenceArtifact, ...],
        reference_source_snapshots: dict[str, bytes],
        reference_extraction_snapshots: dict[str, bytes],
    ) -> GenerationOutcome:
        evidence = state.evidence
        latest = self.store.load_create_checkpoint(
            expected_operation_key=state.checkpoint.operation_key
        )
        if latest is None:
            raise GenerationIsolationError("Create Checkpoint pointer disappeared")
        if latest.checkpoint.stage in {"correction_ready", "audio_audit_ready"}:
            correction_stored = latest
            if latest.checkpoint.stage == "audio_audit_ready":
                correction_id = latest.checkpoint.previous_checkpoint_id
                assert correction_id is not None
                correction_stored = self.store.load_create_checkpoint_id(
                    correction_id
                )
            correction_state = self._load_correction_checkpoint(
                correction_stored,
                evidence_state=state,
                request=request,
            )
            if latest.checkpoint.stage == "audio_audit_ready":
                audio_state = self._load_audio_checkpoint(
                    latest,
                    correction_state=correction_state,
                )
                return self._continue_create_from_audio(
                    request=request,
                    state=audio_state,
                    active_speaker_identity=active_speaker_identity,
                    enrolled_artifacts=enrolled_artifacts,
                    reference_source_snapshots=reference_source_snapshots,
                    reference_extraction_snapshots=reference_extraction_snapshots,
                )
            return self._continue_create_from_correction(
                request=request,
                state=correction_state,
                active_speaker_identity=active_speaker_identity,
                enrolled_artifacts=enrolled_artifacts,
                reference_source_snapshots=reference_source_snapshots,
                reference_extraction_snapshots=reference_extraction_snapshots,
            )
        if latest.checkpoint.stage in {
            "native_audit_basis_ready",
            "native_text_audit_ready",
            "native_audio_audit_ready",
        }:
            if self._native_full_audit is None:
                raise GenerationIsolationError(
                    "native Full Audit checkpoint cannot resume in legacy composition"
                )
            basis_stored = latest
            if latest.checkpoint.stage == "native_text_audit_ready":
                basis_id = latest.checkpoint.previous_checkpoint_id
                assert basis_id is not None
                basis_stored = self.store.load_create_checkpoint_id(basis_id)
            elif latest.checkpoint.stage == "native_audio_audit_ready":
                text_id = latest.checkpoint.previous_checkpoint_id
                assert text_id is not None
                text_stored = self.store.load_create_checkpoint_id(text_id)
                basis_id = text_stored.checkpoint.previous_checkpoint_id
                assert basis_id is not None
                basis_stored = self.store.load_create_checkpoint_id(basis_id)
            basis = self._load_native_audit_basis_checkpoint(
                basis_stored,
                evidence_state=state,
                request=request,
            )
            if latest.checkpoint.stage == "native_audio_audit_ready":
                text_id = latest.checkpoint.previous_checkpoint_id
                assert text_id is not None
                text_stored = self.store.load_create_checkpoint_id(text_id)
                text_state = self._load_native_text_audit_checkpoint(
                    text_stored,
                    basis=basis,
                )
                audio_state = self._load_native_audio_audit_checkpoint(
                    latest,
                    text=text_state,
                )
                return self._finish_native_full_audit(
                    state=audio_state,
                    active_speaker_identity=active_speaker_identity,
                    enrolled_artifacts=enrolled_artifacts,
                    reference_source_snapshots=reference_source_snapshots,
                    reference_extraction_snapshots=reference_extraction_snapshots,
                )
            if latest.checkpoint.stage == "native_text_audit_ready":
                text_state = self._load_native_text_audit_checkpoint(
                    latest,
                    basis=basis,
                )
                return self._continue_native_audio_audit(
                    state=text_state,
                    active_speaker_identity=active_speaker_identity,
                    enrolled_artifacts=enrolled_artifacts,
                    reference_source_snapshots=reference_source_snapshots,
                    reference_extraction_snapshots=reference_extraction_snapshots,
                )
            return self._continue_native_text_audit(
                state=basis,
                active_speaker_identity=active_speaker_identity,
                enrolled_artifacts=enrolled_artifacts,
                reference_source_snapshots=reference_source_snapshots,
                reference_extraction_snapshots=reference_extraction_snapshots,
            )
        preliminary = reconcile_canonical(
            primary=evidence[0],
            corroborating=evidence[1:],
            source_audio_hash=state.source_hash,
            normalization_receipt_hash=state.receipt_hash,
            policy_hash=state.checkpoint.policy_hash,
            acceptance_policy=request.policy.acceptance_snapshot,
            references=(),
            orthographic_projections=state.orthographic_projections,
        )
        preliminary = attest_speech_coverage(
            preliminary,
            state.speech_coverage_receipt,
        )
        references, retrievals = self._retrieve_references(
            request=request,
            invocation_id=state.invocation_id,
            preliminary=preliminary,
        )
        result = reconcile_canonical(
            primary=evidence[0],
            corroborating=evidence[1:],
            source_audio_hash=state.source_hash,
            normalization_receipt_hash=state.receipt_hash,
            policy_hash=state.checkpoint.policy_hash,
            acceptance_policy=request.policy.acceptance_snapshot,
            references=references,
            orthographic_projections=state.orthographic_projections,
        )
        result = attest_speech_coverage(result, state.speech_coverage_receipt)
        retrieval_failure_risks = _reference_retrieval_failure_risks(retrievals)
        if retrieval_failure_risks:
            result = add_review_risks(result, retrieval_failure_risks)
        if self._native_full_audit is not None:
            result = add_review_risks(
                result,
                (_native_full_audit_resolution_risk(result.transcript),),
            )
            basis = self._commit_native_audit_basis_checkpoint(
                evidence_state=state,
                result=result,
                references=references,
                retrievals=retrievals,
                references_enrolled=bool(enrolled_artifacts),
            )
            return self._continue_native_text_audit(
                state=basis,
                active_speaker_identity=active_speaker_identity,
                enrolled_artifacts=enrolled_artifacts,
                reference_source_snapshots=reference_source_snapshots,
                reference_extraction_snapshots=reference_extraction_snapshots,
            )
        references_by_span = _reference_evidence_by_span(references, retrievals)
        preaudit_transcript = result.transcript
        try:
            (
                proposals,
                correction_audits,
                targeted_correction_audits,
                correction_execution_artifacts,
            ) = self._run_corrector(
                episode_id=request.episode_id,
                result=result,
                mode="full_audit",
                target_windows=_full_audit_windows(result.transcript, request.policy),
                evidence=evidence,
                references=references,
                references_by_span=references_by_span,
            )
        except AdapterWorkPending as pending:
            return Interrupted(
                operation="create",
                reason=str(pending),
                generation_id=result.transcript.generation_id,
                packet_paths=pending.packet_paths,
                response_paths=pending.response_paths,
            )
        if proposals:
            result = add_correction_proposals(
                result,
                proposals,
                allowed_reference_ids=tuple(item.id for item in references),
            )
        correction_state = self._commit_correction_checkpoint(
            evidence_state=state,
            enrolled_artifacts=enrolled_artifacts,
            reference_source_snapshots=reference_source_snapshots,
            reference_extraction_snapshots=reference_extraction_snapshots,
            result=result,
            references=references,
            retrievals=retrievals,
            proposals=proposals,
            correction_audits=correction_audits,
            targeted_correction_audits=targeted_correction_audits,
            correction_execution_artifacts=correction_execution_artifacts,
            preaudit_transcript=preaudit_transcript,
        )
        return self._continue_create_from_correction(
            request=request,
            state=correction_state,
            active_speaker_identity=active_speaker_identity,
            enrolled_artifacts=enrolled_artifacts,
            reference_source_snapshots=reference_source_snapshots,
            reference_extraction_snapshots=reference_extraction_snapshots,
        )

    def _continue_create_from_correction(
        self,
        *,
        request: CreateRequest,
        state: _CreateCorrectionState,
        active_speaker_identity: AdapterIdentity | None,
        enrolled_artifacts: tuple[ReferenceArtifact, ...],
        reference_source_snapshots: dict[str, bytes],
        reference_extraction_snapshots: dict[str, bytes],
    ) -> GenerationOutcome:
        receipt = state.evidence_state.receipt
        evidence = state.evidence_state.evidence
        result = state.result
        try:
            audio_proposals, audio_audits, audio_audit_artifacts = (
                self._run_audio_audit(
                    result=result,
                    target_windows=_full_audit_windows(
                        result.transcript, request.policy
                    ),
                    normalized_audio=self.store.audio_path(
                        receipt.normalized.sha256,
                        size_bytes=receipt.normalized.size_bytes,
                    ),
                    normalized_audio_hash=receipt.normalized.sha256,
                    normalized_audio_duration_ms=receipt.normalized_duration_ms,
                    evidence=evidence,
                    references=state.references,
                    references_by_span=_reference_evidence_by_span(
                        state.references,
                        state.retrievals,
                    ),
                )
            )
        except AdapterWorkPending as pending:
            return Interrupted(
                operation="create",
                reason=str(pending),
                generation_id=result.transcript.generation_id,
                packet_paths=pending.packet_paths,
                response_paths=pending.response_paths,
            )
        result = attest_full_audit(result, audio_audits)
        if audio_proposals:
            proposals = _merge_proposal_batches((state.proposals, audio_proposals))
            result = add_correction_proposals(
                result,
                audio_proposals,
                allowed_reference_ids=tuple(item.id for item in state.references),
            )
        else:
            proposals = state.proposals
        audio_state = self._commit_audio_checkpoint(
            correction_state=state,
            enrolled_artifacts=enrolled_artifacts,
            reference_source_snapshots=reference_source_snapshots,
            reference_extraction_snapshots=reference_extraction_snapshots,
            result=result,
            proposals=proposals,
            audio_audits=audio_audits,
            audio_execution_artifacts=audio_audit_artifacts,
        )
        return self._continue_create_from_audio(
            request=request,
            state=audio_state,
            active_speaker_identity=active_speaker_identity,
            enrolled_artifacts=enrolled_artifacts,
            reference_source_snapshots=reference_source_snapshots,
            reference_extraction_snapshots=reference_extraction_snapshots,
        )

    def _continue_create_from_audio(
        self,
        *,
        request: CreateRequest,
        state: _CreateAudioState,
        active_speaker_identity: AdapterIdentity | None,
        enrolled_artifacts: tuple[ReferenceArtifact, ...],
        reference_source_snapshots: dict[str, bytes],
        reference_extraction_snapshots: dict[str, bytes],
    ) -> GenerationOutcome:
        correction = state.correction_state
        evidence_state = correction.evidence_state
        receipt = evidence_state.receipt
        evidence = evidence_state.evidence
        result = state.result
        proposals = state.proposals
        # A process may have stopped after the immutable Generation directory
        # was committed but before the terminal checkpoint pointer was
        # published.  The canonical Generation ID is already known from the
        # audio checkpoint; verify that orphan with the pure replay path and
        # seal it without invoking any new paid Adapter work.
        try:
            self.store.load(result.transcript.generation_id)
        except GenerationNotFoundError:
            pass
        else:
            recovered = self._load_generation(
                result.transcript.generation_id,
                require_active=False,
            ).result
            if recovered.transcript != result.transcript:
                raise GenerationIsolationError(
                    "inactive Generation differs from Audio Checkpoint transcript"
                )
            record = self.store.load(result.transcript.generation_id)
            manifest = GenerationManifest.model_validate(record.manifest)
            recovered_outcome: GenerationOutcome
            if recovered.outcome == "accepted":
                recovered_outcome = AcceptedGeneration(
                    record.generation_id,
                    recovered.transcript,
                    manifest,
                )
            else:
                recovered_outcome = NeedsReview(
                    record.generation_id,
                    recovered.transcript,
                    tuple(
                        issue
                        for issue in recovered.transcript.review_issues
                        if issue.status == "unresolved"
                    ),
                    manifest,
                )
            return self._seal_and_activate_create(
                audio_checkpoint=state.checkpoint,
                outcome=recovered_outcome,
            )
        try:
            arbitration_receipts, arbitration_artifacts = self._run_arbitration(
                result=result,
                proposals=proposals,
                normalized_audio=self.store.audio_path(
                    receipt.normalized.sha256,
                    size_bytes=receipt.normalized.size_bytes,
                ),
                normalized_audio_hash=receipt.normalized.sha256,
                normalized_audio_duration_ms=receipt.normalized_duration_ms,
                references=correction.references,
                retrievals=correction.retrievals,
            )
        except AdapterWorkPending as pending:
            return Interrupted(
                operation="create",
                reason=str(pending),
                generation_id=result.transcript.generation_id,
                packet_paths=pending.packet_paths,
                response_paths=pending.response_paths,
            )
        for name, payload in arbitration_artifacts.items():
            existing = state.audio_execution_artifacts.get(name)
            if existing is not None and existing != payload:
                raise GenerationIsolationError(
                    "audio execution artifact digest has conflicting bytes"
                )
            state.audio_execution_artifacts[name] = payload
        result = replay_correction_ledger(
            result,
            self.ledger,
            allowed_reference_ids=tuple(item.id for item in correction.references),
        )
        outcome = self._commit_generation(
            result=result,
            receipt=receipt,
            recognition_request=evidence_state.recognition_request,
            recognition_independence=evidence_state.recognition_independence,
            evidence=evidence,
            orthographic_projections=evidence_state.orthographic_projections,
            orthographic_raw_outputs=evidence_state.orthographic_raw_outputs,
            speaker_base_evidence=(
                (evidence_state.base_primary_evidence,)
                if evidence_state.base_primary_evidence is not None
                else ()
            ),
            speaker_provenance=evidence_state.speaker_provenance,
            speaker_attributor_identity=active_speaker_identity,
            enrolled_artifacts=enrolled_artifacts,
            reference_retrieval_policy=evidence_state.reference_retrieval_policy,
            references=correction.references,
            retrievals=correction.retrievals,
            proposals=proposals,
            correction_audits=correction.correction_audits,
            targeted_correction_audits=correction.targeted_correction_audits,
            audio_audits=state.audio_audits,
            arbitration_receipts=arbitration_receipts,
            correction_execution_artifacts=correction.correction_execution_artifacts,
            audio_execution_artifacts=state.audio_execution_artifacts,
            speech_coverage_receipt=evidence_state.speech_coverage_receipt,
            speech_coverage_raw_artifacts=(
                evidence_state.speech_coverage_raw_artifacts
            ),
            raw_outputs=evidence_state.raw_outputs,
            reference_source_snapshots=reference_source_snapshots,
            reference_extraction_snapshots=reference_extraction_snapshots,
            parent_manifest_hash=None,
            activate=False,
        )
        return self._seal_and_activate_create(
            audio_checkpoint=state.checkpoint,
            outcome=outcome,
        )

    def resolve(self, request: ResolveRequest) -> GenerationOutcome:
        """Publish one reviewed decision as an episode-level durable transaction."""

        with self.store.episode_transaction():
            self._recover_resolution_transaction_locked()
            parent = self._load_generation(
                request.generation_id,
                require_active=True,
            )
            if isinstance(parent.audit, LoadedNativeAuditState):
                raise NativeAcceptanceRequiredError(
                    "native acceptance integration is required before resolving "
                    "a native Full Audit Generation"
                )
            return self._resolve_locked(request, parent=parent)

    def _authorize_native_resolution(
        self,
        request: ResolveNativeRequest,
        *,
        require_active: bool,
    ) -> _NativeResolveAuthorizationState:
        """Rebuild one exact native authorization from its stored Generation parents."""

        if self._native_full_audit is None:
            raise NativeAcceptanceRequiredError(
                "native resolution requires the native Full Audit runtime"
            )
        parent = self._load_generation(
            request.generation_id, require_active=require_active
        )
        parent_result = parent.result
        if not isinstance(parent.audit, LoadedNativeAuditState):
            raise GenerationIsolationError(
                "native authorization targets a non-native audit Generation"
            )
        aggregate = parent.audit.aggregate
        text_record = parent.audit.text_run.record
        audio_record = parent.audit.audio_run.record
        authorization_bytes = (
            request.correction_acceptance_verdict
            if request.correction_acceptance_verdict is not None
            else request.original_confirmation_authorization
        )
        assert authorization_bytes is not None
        authorization_model = (
            CorrectionAcceptanceVerdictV2
            if request.correction_acceptance_verdict is not None
            else OriginalConfirmationAuthorizationV2
        )
        try:
            declared = authorization_model.model_validate_json(
                authorization_bytes,
                strict=True,
            )
        except ValueError as exc:
            raise GenerationIsolationError(
                "native authorization bytes are invalid"
            ) from exc
        if canonical_json_bytes(declared) != authorization_bytes:
            raise GenerationIsolationError("native authorization bytes are not canonical")
        candidates: tuple[NativeDiscoveredCandidateV2, ...] = (
            *text_record.candidate_discovery_set.candidates,
            *audio_record.candidate_discovery_set.candidates,
        )
        matching = tuple(
            item for item in candidates if item.id == declared.candidate_discovery_id
        )
        if len(matching) != 1:
            raise GenerationIsolationError(
                "native authorization names zero or multiple stored discoveries"
            )
        candidate = matching[0]
        reference_proof = None
        if request.reference_authority_proof is not None:
            if self._reference_retriever is None:
                raise GenerationIsolationError(
                    "native authorization Reference proof lacks configured Retriever"
                )
            reference_proof = verify_native_reference_authority_proof(
                request.reference_authority_proof,
                retriever=self._reference_retriever,  # type: ignore[arg-type]
                resolution_key=declared.resolution_key,
                reference_scope=declared.reference_scope,
                stored_reference_evidence=parent.references.evidence,
            )
        if request.correction_acceptance_verdict is not None:
            assert isinstance(declared, CorrectionAcceptanceVerdictV2)
            assert request.correction_acceptance_policy is not None
            policy = verify_correction_acceptance_policy(
                request.correction_acceptance_policy
            )
            human_receipts = tuple(
                verify_human_audio_review_receipt(payload)
                for payload in request.human_audio_receipts
            )
            reference_adjudication = (
                None
                if request.human_reference_adjudication is None
                else verify_human_reference_adjudication_receipt(
                    request.human_reference_adjudication
                )
            )
            authorization = verify_correction_acceptance_verdict(
                authorization_bytes,
                candidate=candidate,
                full_audit_aggregate=aggregate,
                recognition_evidence=parent.recognition.evidence,
                resolution_key=declared.resolution_key,
                reference_scope=declared.reference_scope,
                reference_proof=reference_proof,
                human_audio_receipts=human_receipts,
                human_reference_adjudication=reference_adjudication,
                policy=policy,
            )
            policy_bytes = request.correction_acceptance_policy
            receipt_bytes = request.human_audio_receipts
            receipt_index_name = _NATIVE_RESOLUTION_HUMAN_AUDIO_INDEX
            receipt_namespace: Literal[
                "human_audio", "human_original_confirmation"
            ] = "human_audio"
            policy_name = _NATIVE_RESOLUTION_ACCEPTANCE_POLICY
        else:
            assert isinstance(declared, OriginalConfirmationAuthorizationV2)
            assert request.original_confirmation_policy is not None
            policy = verify_original_confirmation_policy(
                request.original_confirmation_policy
            )
            human_original_receipts = tuple(
                verify_human_original_confirmation_receipt(payload)
                for payload in request.human_original_confirmation_receipts
            )
            authorization = verify_original_confirmation_authorization(
                authorization_bytes,
                candidate=candidate,
                full_audit_aggregate=aggregate,
                recognition_evidence=parent.recognition.evidence,
                resolution_key=declared.resolution_key,
                reference_scope=declared.reference_scope,
                reference_proof=reference_proof,
                human_original_receipts=human_original_receipts,
                policy=policy,
            )
            policy_bytes = request.original_confirmation_policy
            receipt_bytes = request.human_original_confirmation_receipts
            receipt_index_name = _NATIVE_RESOLUTION_HUMAN_ORIGINAL_INDEX
            receipt_namespace = "human_original_confirmation"
            policy_name = _NATIVE_RESOLUTION_ORIGINAL_POLICY
        span_by_id = {span.id: span for span in parent_result.transcript.spans}
        try:
            cited_spans = tuple(
                span_by_id[span_id] for span_id in candidate.cited_span_ids
            )
        except KeyError as exc:
            raise GenerationIsolationError(
                "native discovery cites a missing parent span"
            ) from exc
        fingerprint = review_target_fingerprint(
            parent_result.transcript,
            candidate.cited_span_ids,
        )
        decision = build_native_correction_decision(
            episode_id=parent_result.transcript.episode_id,
            parent_generation_id=parent_result.transcript.generation_id,
            reviewed_fingerprint=fingerprint,
            target_start_ms=cited_spans[0].start_ms,
            target_end_ms=cited_spans[-1].end_ms,
            candidate=candidate,
            authorization=authorization,
        )
        ledger_entry = self.ledger.prepare(
            decision,
            current_fingerprint=fingerprint,
            expected_head=parent_result.transcript.ledger_hash,
        )
        try:
            typed_decision = decode_ledger_entry(ledger_entry).decision
        except LedgerDecisionCodecError as exc:
            raise GenerationIsolationError("native Ledger preparation is invalid") from exc
        if typed_decision != decision:
            raise GenerationIsolationError("native Ledger preparation changed its Decision")
        receipt_index, receipt_artifacts = _address_native_resolution_receipts(
            receipt_bytes,
            namespace=receipt_namespace,
        )
        resolution_artifacts = {
            _NATIVE_RESOLUTION_DECISION: native_correction_decision_bytes(decision),
            _NATIVE_RESOLUTION_CANDIDATE: canonical_json_bytes(candidate),
            _NATIVE_RESOLUTION_AUTHORIZATION: authorization_bytes,
            policy_name: policy_bytes,
            receipt_index_name: receipt_index,
            _NATIVE_RESOLUTION_LEDGER_ENTRY: canonical_json_bytes(
                _ledger_entry_payload(ledger_entry)
            ),
            **receipt_artifacts,
        }
        if request.reference_authority_proof is not None:
            resolution_artifacts[_NATIVE_RESOLUTION_REFERENCE_PROOF] = (
                request.reference_authority_proof
            )
        if request.human_reference_adjudication is not None:
            resolution_artifacts[_NATIVE_RESOLUTION_REFERENCE_ADJUDICATION] = (
                request.human_reference_adjudication
            )
        request_hashes = {
            "generation_id": request.generation_id,
            "authorization_kind": decision.authorization_kind,
            "authorization": sha256_bytes(authorization_bytes),
            "policy": sha256_bytes(policy_bytes),
            "human_audio": tuple(
                sha256_bytes(payload) for payload in request.human_audio_receipts
            ),
            "human_original": tuple(
                sha256_bytes(payload)
                for payload in request.human_original_confirmation_receipts
            ),
            "reference_proof": (
                sha256_bytes(request.reference_authority_proof)
                if request.reference_authority_proof is not None
                else None
            ),
            "reference_adjudication": (
                sha256_bytes(request.human_reference_adjudication)
                if request.human_reference_adjudication is not None
                else None
            ),
        }
        operation_key = hash_object(
            {
                "operation": "native_resolution_v2",
                "request": request_hashes,
                "parent_manifest_hash": hash_object(
                    parent.storage.manifest.materialize()
                ),
                "parent_ledger_hash": parent_result.transcript.ledger_hash,
                "prepared_ledger_entry_hash": ledger_entry.entry_hash,
                "code_hash": self._code_hash,
                "adapter_identities_hash": sha256_bytes(
                    parent.audit.runtime.identities_artifact.payload
                ),
            }
        )
        return _NativeResolveAuthorizationState(
            parent=parent,
            candidate=candidate,
            authorization=authorization,
            decision=decision,
            ledger_entry=ledger_entry,
            operation_key=operation_key,
            resolution_artifacts=resolution_artifacts,
        )

    def _build_native_resolve_audit_basis(
        self,
        authorization: _NativeResolveAuthorizationState,
    ) -> _NativeResolveAuditBasisState:
        bundle = self._native_full_audit
        if bundle is None:
            raise GenerationIsolationError("native child audit requires native composition")
        parent = authorization.parent
        parent_result = parent.result
        result = derive_native_canonical_resolution(
            parent_result,
            authorization.decision,
            authorization.candidate,
            ledger_hash=authorization.ledger_entry.entry_hash,
        )
        references, retrievals = self._reretrieve_child_references(
            transcript=result.transcript,
            policy=parent.references.retrieval_policy,
            enrolled_artifacts=parent.references.enrollments,
            parent_references=parent.references.evidence,
            parent_retrievals=parent.references.retrievals,
            reference_extraction_snapshots={
                item.name: item.payload
                for item in parent.references.extraction_snapshots.artifacts
            },
        )
        retrieval_risks = _reference_retrieval_failure_risks(retrievals)
        if retrieval_risks:
            result = add_review_risks(result, retrieval_risks)
        audit_evidence = tuple(
            sorted(parent.recognition.evidence, key=recognition_evidence_content_hash)
        )
        audit_plan = build_audit_plan(
            result.transcript,
            audit_evidence,
            bundle.audit_policy,
            reference_retrievals=retrievals,
            references_enrolled=bool(parent.references.enrollments),
            speech_coverage=parent.speech_coverage.receipt,
            boundary_constraints=None,
            seam_evidence=None,
        )
        signals = derive_candidate_signal_set(
            audit_plan,
            result.transcript,
            audit_evidence,
            reference_retrievals=retrievals,
            references_enrolled=bool(parent.references.enrollments),
            speech_coverage=parent.speech_coverage.receipt,
            boundary_constraints=None,
            seam_evidence=None,
        )
        groups = derive_candidate_group_set(audit_plan, signals, result.transcript)
        normalized_audio = parent.normalization.normalized_audio_path
        execution = build_correction_audit_execution_plan(
            audit_plan,
            signals,
            groups,
            result.transcript,
            audit_evidence,
            bundle.execution_policy,
            reference_retrievals=retrievals,
            seam_evidence=None,
            normalized_audio_path=normalized_audio,
        )
        text_sources, audio_sources = self._native_source_sets(
            audit_plan=audit_plan,
            candidate_signal_set=signals,
            candidate_group_set=groups,
            execution_plan=execution,
            transcript=result.transcript,
            recognition_evidence=audit_evidence,
            reference_retrievals=retrievals,
            normalized_audio_path=normalized_audio,
        )
        text_index, text_artifacts = _address_native_sources(
            text_sources, namespace="text"
        )
        audio_index, audio_artifacts = _address_native_sources(
            audio_sources, namespace="audio"
        )
        final_artifacts = {
            **authorization.resolution_artifacts,
            _CHECKPOINT_CANONICAL: canonical_json_bytes(result.transcript),
            _CHECKPOINT_RISKS: canonical_json_bytes(
                tuple(_risk_payload(item) for item in result.risks)
            ),
            _REFERENCES: canonical_json_bytes(references),
            _RETRIEVALS: canonical_json_bytes(retrievals),
            _NATIVE_AUDIT_PLAN: canonical_json_bytes(audit_plan),
            _NATIVE_CANDIDATE_SIGNALS: canonical_json_bytes(signals),
            _NATIVE_CANDIDATE_GROUPS: canonical_json_bytes(groups),
            _NATIVE_EXECUTION_PLAN: canonical_json_bytes(execution),
            _NATIVE_TEXT_SOURCE_INDEX: text_index,
            _NATIVE_AUDIO_SOURCE_INDEX: audio_index,
            **text_artifacts,
            **audio_artifacts,
        }
        checkpoint_artifacts = _native_resolve_checkpoint_artifacts(final_artifacts)
        checkpoint = build_native_resolve_checkpoint(
            operation_key=authorization.operation_key,
            episode_id=result.transcript.episode_id,
            stage="audit_basis_ready",
            parent_generation_id=parent_result.transcript.generation_id,
            parent_manifest_hash=hash_object(parent.storage.manifest.materialize()),
            expected_active_generation_id=parent_result.transcript.generation_id,
            expected_ledger_head=parent_result.transcript.ledger_hash,
            prepared_ledger_entry_hash=authorization.ledger_entry.entry_hash,
            decision_event_id=authorization.decision.event_id,
            decision_content_hash=authorization.decision.content_hash,
            authorization_kind="correction_acceptance",
            authorization_id=authorization.authorization.id,
            authorization_hash=authorization.authorization.content_hash,
            audit_basis_generation_id=result.transcript.generation_id,
            audit_basis_transcript_hash=hash_object(result.transcript),
            policy_hash=result.transcript.policy_hash,
            code_hash=self._code_hash,
            reference_enrollment_hash=hash_object(parent.references.enrollments),
            adapter_identities_hash=sha256_bytes(
                parent.audit.runtime.identities_artifact.payload
            ),
            artifact_hashes={
                name: sha256_bytes(payload)
                for name, payload in sorted(checkpoint_artifacts.items())
            },
        )
        self.store.commit_native_resolve_checkpoint(
            checkpoint,
            artifacts=checkpoint_artifacts,
        )
        return _NativeResolveAuditBasisState(
            authorization=authorization,
            result=result,
            references=references,
            retrievals=retrievals,
            audit_plan=audit_plan,
            candidate_signal_set=signals,
            candidate_group_set=groups,
            execution_plan=execution,
            text_source_artifacts=text_sources,
            audio_source_artifacts=audio_sources,
            checkpoint=checkpoint,
        )

    def _native_resolve_checkpoint_bytes(
        self,
        stored: StoredNativeResolveCheckpoint,
        final_name: str,
    ) -> bytes:
        return self.store.read_native_resolve_checkpoint_artifact(
            stored,
            f"{_NATIVE_RESOLVE_PREFIX}{final_name}",
        )

    def _load_native_resolve_audit_basis(
        self,
        stored: StoredNativeResolveCheckpoint,
        *,
        authorization: _NativeResolveAuthorizationState,
    ) -> _NativeResolveAuditBasisState:
        bundle = self._native_full_audit
        if bundle is None:
            raise GenerationIsolationError("native child audit requires native composition")
        checkpoint = stored.checkpoint
        parent = authorization.parent
        parent_result = parent.result
        expected_bindings = (
            checkpoint.stage == "audit_basis_ready",
            checkpoint.operation_key == authorization.operation_key,
            checkpoint.parent_generation_id == parent_result.transcript.generation_id,
            checkpoint.parent_manifest_hash
            == hash_object(parent.storage.manifest.materialize()),
            checkpoint.expected_active_generation_id
            == parent_result.transcript.generation_id,
            checkpoint.expected_ledger_head == parent_result.transcript.ledger_hash,
            checkpoint.prepared_ledger_entry_hash
            == authorization.ledger_entry.entry_hash,
            checkpoint.decision_event_id == authorization.decision.event_id,
            checkpoint.decision_content_hash == authorization.decision.content_hash,
            checkpoint.authorization_id == authorization.authorization.id,
            checkpoint.authorization_hash == authorization.authorization.content_hash,
            checkpoint.code_hash == self._code_hash,
            checkpoint.reference_enrollment_hash
            == hash_object(parent.references.enrollments),
            checkpoint.adapter_identities_hash
            == sha256_bytes(
                parent.audit.runtime.identities_artifact.payload
            ),
        )
        if not all(expected_bindings):
            raise GenerationIsolationError(
                "native resolve checkpoint differs from exact parent or authorization"
            )
        result = derive_native_canonical_resolution(
            parent_result,
            authorization.decision,
            authorization.candidate,
            ledger_hash=authorization.ledger_entry.entry_hash,
        )
        references_payload = _canonical_json_value(
            self._native_resolve_checkpoint_bytes(stored, _REFERENCES),
            label=_REFERENCES,
        )
        retrievals_payload = _canonical_json_value(
            self._native_resolve_checkpoint_bytes(stored, _RETRIEVALS),
            label=_RETRIEVALS,
        )
        if not isinstance(references_payload, list) or not isinstance(
            retrievals_payload, list
        ):
            raise GenerationIsolationError(
                "native resolve Reference checkpoint collections are invalid"
            )
        references = tuple(
            ReferenceEvidence.model_validate(item) for item in references_payload
        )
        retrievals = tuple(
            ReferenceRetrievalReceipt.model_validate(item) for item in retrievals_payload
        )
        if references != parent.references.evidence:
            raise GenerationIsolationError(
                "native resolve fresh Reference Evidence differs from parent reconciliation"
            )
        invocations = {item.invocation_id for item in retrievals}
        if len(invocations) > 1:
            raise GenerationIsolationError(
                "native resolve Reference receipts cross invocations"
            )
        self._validate_reference_retrieval_state(
            transcript=result.transcript,
            policy=parent.references.retrieval_policy,
            invocation_id=(next(iter(invocations)) if invocations else "no-reference-retrieval"),
            enrolled_artifacts=parent.references.enrollments,
            references=references,
            retrievals=retrievals,
            reference_extraction_snapshots={
                item.name: item.payload
                for item in parent.references.extraction_snapshots.artifacts
            },
            context="native resolve checkpoint",
        )
        retrieval_risks = _reference_retrieval_failure_risks(retrievals)
        if retrieval_risks:
            result = add_review_risks(result, retrieval_risks)
        transcript = CanonicalTranscript.model_validate_json(
            self._native_resolve_checkpoint_bytes(stored, _CHECKPOINT_CANONICAL)
        )
        risks_payload = _canonical_json_value(
            self._native_resolve_checkpoint_bytes(stored, _CHECKPOINT_RISKS),
            label=_CHECKPOINT_RISKS,
        )
        if not isinstance(risks_payload, list):
            raise GenerationIsolationError("native resolve checkpoint risk set is invalid")
        risks = tuple(_risk_from_payload(item) for item in risks_payload)
        if transcript != result.transcript or risks != result.risks:
            raise GenerationIsolationError(
                "native resolve Canonical audit basis is not reproducible"
            )
        audit_plan = AuditPlan.model_validate_json(
            self._native_resolve_checkpoint_bytes(stored, _NATIVE_AUDIT_PLAN)
        )
        signals = CandidateSignalSet.model_validate_json(
            self._native_resolve_checkpoint_bytes(stored, _NATIVE_CANDIDATE_SIGNALS)
        )
        groups = CandidateGroupSet.model_validate_json(
            self._native_resolve_checkpoint_bytes(stored, _NATIVE_CANDIDATE_GROUPS)
        )
        execution = CorrectionAuditExecutionPlanV2.model_validate_json(
            self._native_resolve_checkpoint_bytes(stored, _NATIVE_EXECUTION_PLAN)
        )
        audit_evidence = tuple(
            sorted(parent.recognition.evidence, key=recognition_evidence_content_hash)
        )
        expected_plan = build_audit_plan(
            transcript,
            audit_evidence,
            bundle.audit_policy,
            reference_retrievals=retrievals,
            references_enrolled=bool(parent.references.enrollments),
            speech_coverage=parent.speech_coverage.receipt,
            boundary_constraints=None,
            seam_evidence=None,
        )
        expected_signals = derive_candidate_signal_set(
            expected_plan,
            transcript,
            audit_evidence,
            reference_retrievals=retrievals,
            references_enrolled=bool(parent.references.enrollments),
            speech_coverage=parent.speech_coverage.receipt,
            boundary_constraints=None,
            seam_evidence=None,
        )
        expected_groups = derive_candidate_group_set(
            expected_plan, expected_signals, transcript
        )
        normalized_audio = parent.normalization.normalized_audio_path
        expected_execution = build_correction_audit_execution_plan(
            expected_plan,
            expected_signals,
            expected_groups,
            transcript,
            audit_evidence,
            bundle.execution_policy,
            reference_retrievals=retrievals,
            seam_evidence=None,
            normalized_audio_path=normalized_audio,
        )
        if (audit_plan, signals, groups, execution) != (
            expected_plan,
            expected_signals,
            expected_groups,
            expected_execution,
        ):
            raise GenerationIsolationError(
                "native resolve child audit parents are not reproducible"
            )
        text_sources, audio_sources = self._native_source_sets(
            audit_plan=audit_plan,
            candidate_signal_set=signals,
            candidate_group_set=groups,
            execution_plan=execution,
            transcript=transcript,
            recognition_evidence=audit_evidence,
            reference_retrievals=retrievals,
            normalized_audio_path=normalized_audio,
        )
        text_index, text_artifacts = _address_native_sources(
            text_sources, namespace="text"
        )
        audio_index, audio_artifacts = _address_native_sources(
            audio_sources, namespace="audio"
        )
        expected_final_artifacts = {
            **authorization.resolution_artifacts,
            _CHECKPOINT_CANONICAL: canonical_json_bytes(transcript),
            _CHECKPOINT_RISKS: canonical_json_bytes(
                tuple(_risk_payload(item) for item in risks)
            ),
            _REFERENCES: canonical_json_bytes(references),
            _RETRIEVALS: canonical_json_bytes(retrievals),
            _NATIVE_AUDIT_PLAN: canonical_json_bytes(audit_plan),
            _NATIVE_CANDIDATE_SIGNALS: canonical_json_bytes(signals),
            _NATIVE_CANDIDATE_GROUPS: canonical_json_bytes(groups),
            _NATIVE_EXECUTION_PLAN: canonical_json_bytes(execution),
            _NATIVE_TEXT_SOURCE_INDEX: text_index,
            _NATIVE_AUDIO_SOURCE_INDEX: audio_index,
            **text_artifacts,
            **audio_artifacts,
        }
        expected_checkpoint_artifacts = _native_resolve_checkpoint_artifacts(
            expected_final_artifacts
        )
        if set(checkpoint.artifact_hashes) != set(expected_checkpoint_artifacts) or any(
            self.store.read_native_resolve_checkpoint_artifact(stored, name) != payload
            for name, payload in expected_checkpoint_artifacts.items()
        ):
            raise GenerationIsolationError(
                "native resolve audit-basis artifact profile is not exact"
            )
        if (
            checkpoint.audit_basis_generation_id != transcript.generation_id
            or checkpoint.audit_basis_transcript_hash != hash_object(transcript)
            or checkpoint.policy_hash != transcript.policy_hash
        ):
            raise GenerationIsolationError(
                "native resolve checkpoint crossed its child Canonical identity"
            )
        return _NativeResolveAuditBasisState(
            authorization=authorization,
            result=result,
            references=references,
            retrievals=retrievals,
            audit_plan=audit_plan,
            candidate_signal_set=signals,
            candidate_group_set=groups,
            execution_plan=execution,
            text_source_artifacts=text_sources,
            audio_source_artifacts=audio_sources,
            checkpoint=checkpoint,
        )

    def _advance_native_resolve_checkpoint(
        self,
        *,
        parent: NativeResolveCheckpointV2,
        stage: Literal["text_audit_ready", "audio_audit_ready"],
        final_artifacts: Mapping[str, bytes],
    ) -> NativeResolveCheckpointV2:
        checkpoint_artifacts = _native_resolve_checkpoint_artifacts(final_artifacts)
        checkpoint = build_native_resolve_checkpoint(
            operation_key=parent.operation_key,
            episode_id=parent.episode_id,
            stage=stage,
            parent_generation_id=parent.parent_generation_id,
            parent_manifest_hash=parent.parent_manifest_hash,
            expected_active_generation_id=parent.expected_active_generation_id,
            expected_ledger_head=parent.expected_ledger_head,
            prepared_ledger_entry_hash=parent.prepared_ledger_entry_hash,
            decision_event_id=parent.decision_event_id,
            decision_content_hash=parent.decision_content_hash,
            authorization_kind=parent.authorization_kind,
            authorization_id=parent.authorization_id,
            authorization_hash=parent.authorization_hash,
            audit_basis_generation_id=parent.audit_basis_generation_id,
            audit_basis_transcript_hash=parent.audit_basis_transcript_hash,
            policy_hash=parent.policy_hash,
            code_hash=parent.code_hash,
            reference_enrollment_hash=parent.reference_enrollment_hash,
            adapter_identities_hash=parent.adapter_identities_hash,
            artifact_hashes={
                name: sha256_bytes(payload)
                for name, payload in sorted(checkpoint_artifacts.items())
            },
            previous_checkpoint_id=parent.id,
        )
        self.store.commit_native_resolve_checkpoint(
            checkpoint,
            artifacts=checkpoint_artifacts,
        )
        return checkpoint

    def _commit_native_resolve_text_audit(
        self,
        *,
        basis: _NativeResolveAuditBasisState,
        run: TextAuditRunResult,
    ) -> _NativeResolveTextAuditState:
        index, run_artifacts = _address_native_run_bytes(
            prefix="text",
            categories={"requests": run.request_bytes, "responses": run.response_bytes},
        )
        checkpoint = self._advance_native_resolve_checkpoint(
            parent=basis.checkpoint,
            stage="text_audit_ready",
            final_artifacts={
                _NATIVE_TEXT_RECORD: canonical_json_bytes(run.record),
                _NATIVE_TEXT_RUN_INDEX: index,
                **run_artifacts,
            },
        )
        return _NativeResolveTextAuditState(
            basis=basis,
            run=run,
            checkpoint=checkpoint,
        )

    def _load_native_resolve_text_audit(
        self,
        stored: StoredNativeResolveCheckpoint,
        *,
        basis: _NativeResolveAuditBasisState,
    ) -> _NativeResolveTextAuditState:
        bundle = self._native_full_audit
        if bundle is None:
            raise GenerationIsolationError("native child audit requires native composition")
        checkpoint = stored.checkpoint
        if (
            checkpoint.stage != "text_audit_ready"
            or checkpoint.previous_checkpoint_id != basis.checkpoint.id
        ):
            raise GenerationIsolationError(
                "native resolve text checkpoint predecessor differs"
            )
        record_bytes = self._native_resolve_checkpoint_bytes(
            stored, _NATIVE_TEXT_RECORD
        )
        record = TextAuditExecutionRecordV2.model_validate_json(record_bytes, strict=True)
        index_bytes = self._native_resolve_checkpoint_bytes(
            stored, _NATIVE_TEXT_RUN_INDEX
        )
        index = _canonical_json_value(index_bytes, label=_NATIVE_TEXT_RUN_INDEX)
        if not isinstance(index, dict) or set(index) != {
            "schema_version",
            "requests",
            "responses",
        }:
            raise GenerationIsolationError("native resolve text run index is invalid")
        request_names = index["requests"]
        response_names = index["responses"]
        if not isinstance(request_names, list) or not isinstance(response_names, list):
            raise GenerationIsolationError("native resolve text run index paths are invalid")
        requests = tuple(
            self._native_resolve_checkpoint_bytes(stored, str(name))
            for name in request_names
        )
        responses = tuple(
            self._native_resolve_checkpoint_bytes(stored, str(name))
            for name in response_names
        )
        run = TextAuditRunResult(
            record=record,
            request_bytes=requests,
            response_bytes=responses,
        )
        replayed = bundle.text_executor.replay(
            basis.execution_plan,
            basis.audit_plan,
            basis.text_source_artifacts,
            run,
        )
        expected_index, expected_runs = _address_native_run_bytes(
            prefix="text",
            categories={"requests": requests, "responses": responses},
        )
        expected_final = {
            _NATIVE_TEXT_RECORD: canonical_json_bytes(record),
            _NATIVE_TEXT_RUN_INDEX: expected_index,
            **expected_runs,
        }
        expected_checkpoint = _native_resolve_checkpoint_artifacts(expected_final)
        if replayed != run or set(checkpoint.artifact_hashes) != set(expected_checkpoint) or any(
            self.store.read_native_resolve_checkpoint_artifact(stored, name) != payload
            for name, payload in expected_checkpoint.items()
        ):
            raise GenerationIsolationError(
                "native resolve text audit artifact set is not replayable"
            )
        return _NativeResolveTextAuditState(
            basis=basis,
            run=run,
            checkpoint=checkpoint,
        )

    def _commit_native_resolve_audio_audit(
        self,
        *,
        text: _NativeResolveTextAuditState,
        run: AudioAuditRunResult,
        aggregate: FullAuditAggregateAttestationV2,
    ) -> _NativeResolveAudioAuditState:
        index, run_artifacts = _address_native_run_bytes(
            prefix="audio",
            categories={
                "requests": run.request_bytes,
                "responses": run.response_bytes,
                "clips": run.clip_bytes,
                "journals": tuple(
                    canonical_json_bytes(item)
                    for item in run.record.invocation_journals
                ),
            },
        )
        checkpoint = self._advance_native_resolve_checkpoint(
            parent=text.checkpoint,
            stage="audio_audit_ready",
            final_artifacts={
                _NATIVE_AUDIO_RECORD: canonical_json_bytes(run.record),
                _NATIVE_AGGREGATE: canonical_json_bytes(aggregate),
                _NATIVE_AUDIO_RUN_INDEX: index,
                **run_artifacts,
            },
        )
        return _NativeResolveAudioAuditState(
            text=text,
            run=run,
            aggregate=aggregate,
            checkpoint=checkpoint,
        )

    def _load_native_resolve_audio_audit(
        self,
        stored: StoredNativeResolveCheckpoint,
        *,
        text: _NativeResolveTextAuditState,
    ) -> _NativeResolveAudioAuditState:
        bundle = self._native_full_audit
        if bundle is None:
            raise GenerationIsolationError("native child audit requires native composition")
        checkpoint = stored.checkpoint
        if (
            checkpoint.stage != "audio_audit_ready"
            or checkpoint.previous_checkpoint_id != text.checkpoint.id
        ):
            raise GenerationIsolationError(
                "native resolve audio checkpoint predecessor differs"
            )
        record = AudioAuditExecutionRecordV2.model_validate_json(
            self._native_resolve_checkpoint_bytes(stored, _NATIVE_AUDIO_RECORD),
            strict=True,
        )
        aggregate = FullAuditAggregateAttestationV2.model_validate_json(
            self._native_resolve_checkpoint_bytes(stored, _NATIVE_AGGREGATE),
            strict=True,
        )
        index_bytes = self._native_resolve_checkpoint_bytes(
            stored, _NATIVE_AUDIO_RUN_INDEX
        )
        index = _canonical_json_value(index_bytes, label=_NATIVE_AUDIO_RUN_INDEX)
        categories = ("requests", "responses", "clips", "journals")
        if not isinstance(index, dict) or set(index) != {"schema_version", *categories}:
            raise GenerationIsolationError("native resolve audio run index is invalid")
        raw: dict[str, tuple[bytes, ...]] = {}
        for category in categories:
            names = index[category]
            if not isinstance(names, list):
                raise GenerationIsolationError(
                    "native resolve audio run index paths are invalid"
                )
            raw[category] = tuple(
                self._native_resolve_checkpoint_bytes(stored, str(name))
                for name in names
            )
        if raw["journals"] != tuple(
            canonical_json_bytes(item) for item in record.invocation_journals
        ):
            raise GenerationIsolationError(
                "native resolve audio invocation journals changed"
            )
        run = AudioAuditRunResult(
            record=record,
            request_bytes=raw["requests"],
            response_bytes=raw["responses"],
            clip_bytes=raw["clips"],
        )
        basis = text.basis
        replayed = bundle.audio_executor.replay(
            basis.execution_plan,
            basis.audit_plan,
            basis.audio_source_artifacts,
            run,
        )
        verified_aggregate = verify_full_audit_aggregate(
            canonical_json_bytes(aggregate),
            audit_plan=basis.audit_plan,
            candidate_signal_set=basis.candidate_signal_set,
            candidate_group_set=basis.candidate_group_set,
            execution_plan=basis.execution_plan,
            text_record=text.run.record,
            audio_record=record,
            text_request_bytes=text.run.request_bytes,
            text_response_bytes=text.run.response_bytes,
            audio_request_bytes=run.request_bytes,
            audio_response_bytes=run.response_bytes,
            audio_clip_bytes=run.clip_bytes,
        )
        expected_index, expected_runs = _address_native_run_bytes(
            prefix="audio",
            categories=raw,
        )
        expected_final = {
            _NATIVE_AUDIO_RECORD: canonical_json_bytes(record),
            _NATIVE_AGGREGATE: canonical_json_bytes(aggregate),
            _NATIVE_AUDIO_RUN_INDEX: expected_index,
            **expected_runs,
        }
        expected_checkpoint = _native_resolve_checkpoint_artifacts(expected_final)
        if (
            replayed != run
            or verified_aggregate != aggregate
            or set(checkpoint.artifact_hashes) != set(expected_checkpoint)
            or any(
                self.store.read_native_resolve_checkpoint_artifact(stored, name)
                != payload
                for name, payload in expected_checkpoint.items()
            )
        ):
            raise GenerationIsolationError(
                "native resolve audio audit artifact set is not replayable"
            )
        return _NativeResolveAudioAuditState(
            text=text,
            run=run,
            aggregate=aggregate,
            checkpoint=checkpoint,
        )

    def _commit_native_resolved_generation(
        self,
        state: _NativeResolveAudioAuditState,
    ) -> NeedsReview:
        bundle = self._native_full_audit
        if bundle is None:
            raise GenerationIsolationError("native child audit requires native composition")
        basis = state.text.basis
        authorization = basis.authorization
        parent = authorization.parent
        result = _native_resolved_after_fresh_audit(
            basis.result,
            text_record=state.text.run.record,
            audio_record=state.run.record,
        )
        transcript = result.transcript
        _validate_canonical_acceptance_identity(transcript)
        if result.outcome != "needs_review" or transcript.status == "accepted":
            raise GenerationIsolationError(
                "native resolution child cannot bypass explicit resolution review"
            )
        verify_full_audit_aggregate(
            canonical_json_bytes(state.aggregate),
            audit_plan=basis.audit_plan,
            candidate_signal_set=basis.candidate_signal_set,
            candidate_group_set=basis.candidate_group_set,
            execution_plan=basis.execution_plan,
            text_record=state.text.run.record,
            audio_record=state.run.record,
            text_request_bytes=state.text.run.request_bytes,
            text_response_bytes=state.text.run.response_bytes,
            audio_request_bytes=state.run.request_bytes,
            audio_response_bytes=state.run.response_bytes,
            audio_clip_bytes=state.run.clip_bytes,
        )
        parent_orthographic_projections = parent.recognition.orthographic_projections
        parent_orthographic_raw_outputs = {
            item.name: item.payload
            for item in parent.recognition.orthographic_raw_outputs.artifacts
        }
        _validate_canonical_timing_provenance(
            transcript,
            parent.recognition.evidence,
            parent_orthographic_projections,
        )
        _validate_review_risk_evidence_membership(
            transcript=transcript,
            risks=result.risks,
            evidence=parent.recognition.evidence,
            references=basis.references,
            speech_coverage_receipt=parent.speech_coverage.receipt,
            context="native resolved Generation commit",
        )
        text_source_index, text_source_artifacts = _address_native_sources(
            basis.text_source_artifacts, namespace="text"
        )
        audio_source_index, audio_source_artifacts = _address_native_sources(
            basis.audio_source_artifacts, namespace="audio"
        )
        text_run_index, text_run_artifacts = _address_native_run_bytes(
            prefix="text",
            categories={
                "requests": state.text.run.request_bytes,
                "responses": state.text.run.response_bytes,
            },
        )
        audio_run_index, audio_run_artifacts = _address_native_run_bytes(
            prefix="audio",
            categories={
                "requests": state.run.request_bytes,
                "responses": state.run.response_bytes,
                "clips": state.run.clip_bytes,
                "journals": tuple(
                    canonical_json_bytes(item)
                    for item in state.run.record.invocation_journals
                ),
            },
        )
        inherited_raw_artifacts = {
            item.name: item.payload
            for item in parent.recognition.raw_outputs.artifacts
        }
        inherited_coverage_artifacts = {
            item.name: item.payload
            for item in parent.speech_coverage.raw_outputs.artifacts
        }
        inherited_reference_sources = {
            item.name: item.payload
            for item in parent.references.source_snapshots.artifacts
        }
        inherited_reference_extractions = {
            item.name: item.payload
            for item in parent.references.extraction_snapshots.artifacts
        }
        artifacts: dict[str, bytes] = {
            _RECEIPT: parent.normalization.receipt_artifact.payload,
            _AUDIO_ARTIFACTS: parent.normalization.audio_bindings_artifact.payload,
            _RECOGNITION_REQUEST: parent.recognition.request_bytes.payload,
            _RECOGNITION_INDEPENDENCE: parent.recognition.independence_bytes.payload,
            _EVIDENCE: parent.recognition.evidence_bytes.payload,
            _BASE_PRIMARY_EVIDENCE: (
                parent.recognition.speaker_base_evidence_bytes.payload
            ),
            _SPEAKER_PROVENANCE: parent.recognition.speaker_provenance_bytes.payload,
            _REFERENCE_ENROLLMENTS: parent.references.enrollments_bytes.payload,
            _REFERENCE_RETRIEVAL_POLICY: (
                parent.references.retrieval_policy_bytes.payload
            ),
            _SPEECH_COVERAGE: parent.speech_coverage.receipt_bytes.payload,
            _REFERENCES: canonical_json_bytes(basis.references),
            _RETRIEVALS: canonical_json_bytes(basis.retrievals),
            _CHECKPOINT_CANONICAL: canonical_json_bytes(transcript),
            _CHECKPOINT_RISKS: canonical_json_bytes(
                tuple(_risk_payload(item) for item in result.risks)
            ),
            _NATIVE_AUDIT_PLAN: canonical_json_bytes(basis.audit_plan),
            _NATIVE_CANDIDATE_SIGNALS: canonical_json_bytes(
                basis.candidate_signal_set
            ),
            _NATIVE_CANDIDATE_GROUPS: canonical_json_bytes(
                basis.candidate_group_set
            ),
            _NATIVE_EXECUTION_PLAN: canonical_json_bytes(basis.execution_plan),
            _NATIVE_TEXT_SOURCE_INDEX: text_source_index,
            _NATIVE_AUDIO_SOURCE_INDEX: audio_source_index,
            _NATIVE_TEXT_RECORD: canonical_json_bytes(state.text.run.record),
            _NATIVE_AUDIO_RECORD: canonical_json_bytes(state.run.record),
            _NATIVE_AGGREGATE: canonical_json_bytes(state.aggregate),
            _NATIVE_TEXT_RUN_INDEX: text_run_index,
            _NATIVE_AUDIO_RUN_INDEX: audio_run_index,
            _CANONICAL: canonical_json_bytes(transcript),
            _RISKS: canonical_json_bytes(
                tuple(_risk_payload(item) for item in result.risks)
            ),
            **inherited_coverage_artifacts,
            **inherited_raw_artifacts,
            **inherited_reference_sources,
            **inherited_reference_extractions,
            **authorization.resolution_artifacts,
            **text_source_artifacts,
            **audio_source_artifacts,
            **text_run_artifacts,
            **audio_run_artifacts,
        }
        if parent_orthographic_projections:
            assert parent.recognition.orthographic_projection_bytes is not None
            artifacts[_ORTHOGRAPHIC_PROJECTIONS] = (
                parent.recognition.orthographic_projection_bytes.payload
            )
            artifacts.update(parent_orthographic_raw_outputs)
        adapter_bytes = parent.audit.runtime.identities_artifact.payload
        artifacts[_ADAPTER_IDENTITIES] = adapter_bytes
        adapter_payload = _canonical_json_value(
            adapter_bytes, label=_ADAPTER_IDENTITIES
        )
        stage_hashes = {
            "adapter_identities": sha256_bytes(adapter_bytes),
            "normalization_receipt": hash_object(parent.normalization.receipt),
            "recognition_request": RecognitionRequestArtifactV1.model_validate_json(
                artifacts[_RECOGNITION_REQUEST],
                strict=True,
            ).content_hash,
            "recognition_independence": (
                RecognitionIndependenceReceiptV1.model_validate_json(
                    artifacts[_RECOGNITION_INDEPENDENCE], strict=True
                ).content_hash
                if json.loads(artifacts[_RECOGNITION_INDEPENDENCE]) is not None
                else hash_object(None)
            ),
            "recognition_evidence": hash_object(parent.recognition.evidence),
            "reference_evidence": reference_evidence_set_hash(basis.references),
            "reference_retrieval_receipts": hash_object(basis.retrievals),
            "canonical_transcript": hash_object(transcript),
            "risks": hash_object(tuple(_risk_payload(item) for item in result.risks)),
            "native_audit_plan": sha256_bytes(artifacts[_NATIVE_AUDIT_PLAN]),
            "native_candidate_signal_set": sha256_bytes(
                artifacts[_NATIVE_CANDIDATE_SIGNALS]
            ),
            "native_candidate_group_set": sha256_bytes(
                artifacts[_NATIVE_CANDIDATE_GROUPS]
            ),
            "native_execution_plan": sha256_bytes(artifacts[_NATIVE_EXECUTION_PLAN]),
            "native_text_audit_record": sha256_bytes(artifacts[_NATIVE_TEXT_RECORD]),
            "native_audio_audit_record": sha256_bytes(artifacts[_NATIVE_AUDIO_RECORD]),
            "native_full_audit_aggregate": sha256_bytes(artifacts[_NATIVE_AGGREGATE]),
            "native_resolution_decision": sha256_bytes(
                artifacts[_NATIVE_RESOLUTION_DECISION]
            ),
            "native_resolution_authorization": sha256_bytes(
                artifacts[_NATIVE_RESOLUTION_AUTHORIZATION]
            ),
            "native_resolution_evidence": hash_object(
                tuple(
                    (name, sha256_bytes(payload))
                    for name, payload in sorted(authorization.resolution_artifacts.items())
                )
            ),
        }
        if transcript.orthographic_projection_evidence_hash is not None:
            stage_hashes["orthographic_projection_evidence"] = hash_object(
                parent_orthographic_projections
            )
        parent_manifest_hash = hash_object(parent.storage.manifest.materialize())
        input_artifact_hashes = {
            "source_audio": transcript.source_audio_hash,
            "normalized_audio": transcript.normalized_audio_hash,
            "normalization_receipt": transcript.normalization_receipt_hash,
            "recognition_request": RecognitionRequestArtifactV1.model_validate_json(
                artifacts[_RECOGNITION_REQUEST],
                strict=True,
            ).content_hash,
            "recognition_independence": (
                RecognitionIndependenceReceiptV1.model_validate_json(
                    artifacts[_RECOGNITION_INDEPENDENCE], strict=True
                ).content_hash
                if json.loads(artifacts[_RECOGNITION_INDEPENDENCE]) is not None
                else hash_object(None)
            ),
            "recognition_evidence": transcript.evidence_hash,
            "reference_enrollments": hash_object(parent.references.enrollments),
            "references": transcript.reference_evidence_hash,
            "ledger_head": transcript.ledger_hash,
            "parent_manifest": parent_manifest_hash,
            "native_resolution_decision": authorization.decision.content_hash,
        }
        if transcript.orthographic_projection_evidence_hash is not None:
            input_artifact_hashes["orthographic_projection_evidence"] = (
                transcript.orthographic_projection_evidence_hash
            )
        manifest = GenerationManifest(
            id=f"manifest-{transcript.generation_id.removeprefix('generation-')}",
            episode_id=transcript.episode_id,
            generation_id=transcript.generation_id,
            parent_manifest_hash=parent_manifest_hash,
            input_artifact_hashes=input_artifact_hashes,
            code_hash=self._code_hash,
            policy_hash=transcript.policy_hash,
            adapter_hash=hash_object(adapter_payload),
            stage_artifact_hashes=stage_hashes,
            status="complete",
        )
        artifacts[_MANIFEST] = canonical_json_bytes(manifest)
        stored = self.store.commit(
            logical_generation_id=transcript.generation_id,
            manifest=manifest,
            artifacts=artifacts,
        )
        issues = tuple(
            item for item in transcript.review_issues if item.status == "unresolved"
        )
        return NeedsReview(stored.generation_id, transcript, issues, manifest)

    def _publish_native_resolution(
        self,
        *,
        state: _NativeResolveAudioAuditState,
        outcome: NeedsReview,
    ) -> NeedsReview:
        authorization = state.text.basis.authorization
        parent_result = authorization.parent.result
        child_record = self.store.load(outcome.generation_id)
        journal_identity = {
            "schema_version": 2,
            "episode_id": parent_result.transcript.episode_id,
            "event_id": authorization.decision.event_id,
            "parent_generation_id": parent_result.transcript.generation_id,
            "parent_manifest_hash": hash_object(
                authorization.parent.storage.manifest.materialize()
            ),
            "parent_ledger_hash": parent_result.transcript.ledger_hash,
            "child_generation_id": outcome.generation_id,
            "child_manifest_hash": hash_object(child_record.manifest),
            "child_ledger_hash": outcome.transcript.ledger_hash,
            "ledger_entry": _ledger_entry_payload(authorization.ledger_entry),
        }
        journal = {
            **journal_identity,
            "transaction_id": f"resolution-{hash_object(journal_identity)}",
            "state": "prepared",
        }
        with self.store.episode_transaction():
            self._recover_resolution_transaction_locked()
            active_generation_id = self.store.active_generation_id()
            if active_generation_id == outcome.generation_id:
                recovered = self._completed_native_resolution_outcome_locked(
                    authorization
                )
                if recovered is None or recovered.generation_id != outcome.generation_id:
                    raise GenerationIsolationError(
                        "active native child does not match the exact completed request"
                    )
                return recovered
            if active_generation_id != parent_result.transcript.generation_id:
                raise GenerationIsolationError(
                    "active Generation changed before native resolution publication"
                )
            if self.ledger.head_hash != parent_result.transcript.ledger_hash:
                raise GenerationIsolationError(
                    "Correction Ledger changed before native resolution publication"
                )
            self.store.write_resolution_journal_locked(journal)
            appended = self.ledger.append_prepared(authorization.ledger_entry)
            if appended != authorization.ledger_entry:
                raise ResolutionTransactionError(
                    "Correction Ledger changed the native resolution entry"
                )
            self.store.set_active_cas_locked(
                outcome.generation_id,
                expected_generation_id=parent_result.transcript.generation_id,
            )
            self.store.write_resolution_journal_locked(
                {**journal, "state": "completed"}
            )
        return outcome

    def _completed_native_resolution_outcome_locked(
        self,
        authorization: _NativeResolveAuthorizationState,
    ) -> NeedsReview | None:
        """Return an exact completed retry, never an unrelated active child."""

        validated = self._validated_resolution_journal_locked()
        if validated is None:
            return None
        payload, entry, decision = validated
        parent_result = authorization.parent.result
        if (
            payload["schema_version"] != 2
            or payload["state"] != "completed"
            or decision != authorization.decision
            or entry != authorization.ledger_entry
            or payload["parent_generation_id"]
            != parent_result.transcript.generation_id
            or payload["parent_manifest_hash"]
            != hash_object(authorization.parent.storage.manifest.materialize())
        ):
            return None
        child_generation_id = str(payload["child_generation_id"])
        if (
            self.store.active_generation_id() != child_generation_id
            or self.ledger.head_hash != authorization.ledger_entry.entry_hash
        ):
            raise ResolutionTransactionError(
                "completed native resolution is not the active Ledger head"
            )
        loaded = self._load_generation(child_generation_id, require_active=True)
        result = loaded.result
        manifest = loaded.storage.manifest.materialize()
        issues = tuple(
            issue for issue in result.transcript.review_issues if issue.status == "unresolved"
        )
        return NeedsReview(
            child_generation_id,
            result.transcript,
            issues,
            manifest,
        )

    def resolve_native(self, request: ResolveNativeRequest) -> GenerationOutcome:
        """Resolve one stored native discovery, then audit its child from scratch."""

        authorization = self._authorize_native_resolution(
            request,
            require_active=False,
        )
        with self.store.episode_transaction():
            self._recover_resolution_transaction_locked()
            active_generation_id = self.store.active_generation_id()
            parent_generation_id = authorization.parent.result.transcript.generation_id
            if active_generation_id != parent_generation_id:
                completed = self._completed_native_resolution_outcome_locked(authorization)
                if completed is not None:
                    return completed
                raise GenerationIsolationError(
                    "native resolution parent is no longer active and no exact completed "
                    "retry exists"
                )
        stored = self.store.load_native_resolve_checkpoint(
            expected_operation_key=authorization.operation_key
        )
        if stored is None:
            basis = self._build_native_resolve_audit_basis(authorization)
            stage = "audit_basis_ready"
        else:
            stage = stored.checkpoint.stage
            if stage == "audit_basis_ready":
                basis = self._load_native_resolve_audit_basis(
                    stored, authorization=authorization
                )
            else:
                predecessor_id = stored.checkpoint.previous_checkpoint_id
                if predecessor_id is None:
                    raise GenerationIsolationError(
                        "native resolve checkpoint lacks its predecessor"
                    )
                if stage == "text_audit_ready":
                    basis_stored = self.store.load_native_resolve_checkpoint_id(
                        predecessor_id
                    )
                else:
                    text_stored = self.store.load_native_resolve_checkpoint_id(
                        predecessor_id
                    )
                    basis_id = text_stored.checkpoint.previous_checkpoint_id
                    if basis_id is None:
                        raise GenerationIsolationError(
                            "native resolve text checkpoint lacks audit basis"
                        )
                    basis_stored = self.store.load_native_resolve_checkpoint_id(
                        basis_id
                    )
                basis = self._load_native_resolve_audit_basis(
                    basis_stored,
                    authorization=authorization,
                )
        bundle = self._native_full_audit
        assert bundle is not None
        if stage == "audit_basis_ready":
            try:
                text_run = bundle.text_executor.execute(
                    basis.execution_plan,
                    basis.audit_plan,
                    basis.text_source_artifacts,
                )
            except TextAuditWorkPending as pending:
                return Interrupted(
                    operation="resolve",
                    reason=str(pending),
                    generation_id=request.generation_id,
                    audit_basis_generation_id=basis.result.transcript.generation_id,
                    packet_paths=pending.request_paths,
                    response_paths=pending.response_paths,
                )
            text = self._commit_native_resolve_text_audit(
                basis=basis,
                run=text_run,
            )
        else:
            assert stored is not None
            text_stored = (
                stored
                if stage == "text_audit_ready"
                else self.store.load_native_resolve_checkpoint_id(
                    stored.checkpoint.previous_checkpoint_id or ""
                )
            )
            text = self._load_native_resolve_text_audit(
                text_stored,
                basis=basis,
            )
        if stage != "audio_audit_ready":
            try:
                audio_run = bundle.audio_executor.execute(
                    basis.execution_plan,
                    basis.audit_plan,
                    basis.audio_source_artifacts,
                )
            except AudioAuditWorkPending as pending:
                return Interrupted(
                    operation="resolve",
                    reason=str(pending),
                    generation_id=request.generation_id,
                    audit_basis_generation_id=basis.result.transcript.generation_id,
                    packet_paths=pending.request_paths,
                    clip_paths=pending.clip_paths,
                    response_paths=pending.response_paths,
                )
            except AudioAuditInvocationAmbiguous as ambiguous:
                return Interrupted(
                    operation="resolve",
                    reason=str(ambiguous),
                    generation_id=request.generation_id,
                    audit_basis_generation_id=basis.result.transcript.generation_id,
                )
            aggregate = build_full_audit_aggregate(
                audit_plan=basis.audit_plan,
                candidate_signal_set=basis.candidate_signal_set,
                candidate_group_set=basis.candidate_group_set,
                execution_plan=basis.execution_plan,
                text_record=text.run.record,
                audio_record=audio_run.record,
                text_request_bytes=text.run.request_bytes,
                text_response_bytes=text.run.response_bytes,
                audio_request_bytes=audio_run.request_bytes,
                audio_response_bytes=audio_run.response_bytes,
                audio_clip_bytes=audio_run.clip_bytes,
            )
            audio = self._commit_native_resolve_audio_audit(
                text=text,
                run=audio_run,
                aggregate=aggregate,
            )
        else:
            assert stored is not None
            audio = self._load_native_resolve_audio_audit(stored, text=text)
        outcome = self._commit_native_resolved_generation(audio)
        return self._publish_native_resolution(state=audio, outcome=outcome)

    def _resolve_locked(
        self,
        request: ResolveRequest,
        *,
        parent: LoadedGenerationState,
    ) -> GenerationOutcome:
        if parent.storage.generation_id != request.generation_id:
            raise GenerationIsolationError(
                "legacy resolution parent state crossed Generation identity"
            )
        if not isinstance(parent.audit, LoadedLegacyAuditState):
            raise NativeAcceptanceRequiredError(
                "legacy resolve cannot consume a native Full Audit Generation"
            )
        current = parent.result
        receipt = parent.normalization.receipt
        evidence = parent.recognition.evidence
        speaker_base_evidence = parent.recognition.speaker_base_evidence
        speaker_provenance = parent.recognition.speaker_provenance
        enrolled_artifacts = parent.references.enrollments
        references = parent.references.evidence
        retrievals = parent.references.retrievals
        proposals = parent.audit.proposals
        correction_audits = parent.audit.correction_audits
        targeted_correction_audits = parent.audit.targeted_correction_audits
        audio_audits = parent.audit.audio_audits
        arbitration_receipts = parent.audit.arbitrations
        correction_execution_artifacts = {
            item.name: item.payload
            for item in parent.audit.correction_execution_artifacts.artifacts
        }
        audio_execution_artifacts = {
            item.name: item.payload
            for item in parent.audit.audio_execution_artifacts.artifacts
        }
        speech_coverage_receipt = parent.speech_coverage.receipt
        speech_coverage_raw_artifacts = {
            item.name: item.payload
            for item in parent.speech_coverage.raw_outputs.artifacts
        }
        raw_outputs = {
            item.name: item.payload for item in parent.recognition.raw_outputs.artifacts
        }
        reference_source_snapshots = {
            item.name: item.payload
            for item in parent.references.source_snapshots.artifacts
        }
        reference_extraction_snapshots = {
            item.name: item.payload
            for item in parent.references.extraction_snapshots.artifacts
        }
        reference_retrieval_policy = parent.references.retrieval_policy
        result = current
        if len(request.decisions) != 1:
            raise ModuleInvariantError(
                "resolve accepts exactly one Correction Decision per immutable generation"
            )
        decision = request.decisions[0]
        if decision.generation_id != result.transcript.generation_id:
            raise GenerationIsolationError(
                "Correction Decision targets a stale or cross-generation transcript"
            )
        self._validate_decision_provenance(
            decision,
            transcript=result.transcript,
            proposals=proposals,
            references=references,
            retrievals=retrievals,
            arbitration_receipts=arbitration_receipts,
        )
        result, ledger_entry = prepare_canonical_resolution(
            result,
            decision,
            ledger=self.ledger,
        )
        # Every Decision creates another immutable revision.  The child must
        # earn its own complete proof chain even when the rendered text did not
        # change: parent Corrector/AudioAuditor/Arbiter receipts and artifacts
        # are never retained, relabelled, or resealed as child evidence.
        references, retrievals = self._reretrieve_child_references(
            transcript=result.transcript,
            policy=reference_retrieval_policy,
            enrolled_artifacts=enrolled_artifacts,
            parent_references=references,
            parent_retrievals=retrievals,
            reference_extraction_snapshots=reference_extraction_snapshots,
        )
        child_reference_failure_risks = _reference_retrieval_failure_risks(retrievals)
        if child_reference_failure_risks:
            result = add_review_risks(result, child_reference_failure_risks)
        references_by_child_span = _reference_evidence_by_span(references, retrievals)
        child_windows = _single_span_audit_windows(result.transcript)
        try:
            (
                text_proposals,
                correction_audits,
                _child_targeted_audits,
                correction_execution_artifacts,
            ) = self._run_corrector(
                episode_id=result.transcript.episode_id,
                result=result,
                mode="full_audit",
                target_windows=child_windows,
                evidence=evidence,
                references=references,
                references_by_span=references_by_child_span,
            )
        except AdapterWorkPending as pending:
            return Interrupted(
                operation="resolve",
                reason=str(pending),
                generation_id=result.transcript.generation_id,
                packet_paths=pending.packet_paths,
                response_paths=pending.response_paths,
            )
        if _child_targeted_audits:
            raise GenerationIsolationError(
                "child full audit unexpectedly produced targeted-review receipts"
            )
        _validate_text_full_audit_attestation(
            result.transcript,
            correction_audits,
            self._corrector_execution_identity,
            references,
            retrievals,
        )
        targeted_correction_audits = ()
        proposals = text_proposals
        if text_proposals:
            result = add_correction_proposals(
                result,
                text_proposals,
                allowed_reference_ids=tuple(item.id for item in references),
            )

        if receipt.normalized_duration_ms is None:
            raise ModuleInvariantError(
                "accepted normalization receipt lacks normalized audio duration"
            )
        normalized_audio = self.store.audio_path(
            receipt.normalized.sha256,
            size_bytes=receipt.normalized.size_bytes,
        )
        try:
            (
                audio_proposals,
                audio_audits,
                audio_execution_artifacts,
            ) = self._run_audio_audit(
                result=result,
                target_windows=_single_span_audit_windows(result.transcript),
                normalized_audio=normalized_audio,
                normalized_audio_hash=receipt.normalized.sha256,
                normalized_audio_duration_ms=receipt.normalized_duration_ms,
                evidence=evidence,
                references=references,
                references_by_span=references_by_child_span,
            )
        except AdapterWorkPending as pending:
            return Interrupted(
                operation="resolve",
                reason=str(pending),
                generation_id=result.transcript.generation_id,
                packet_paths=pending.packet_paths,
                response_paths=pending.response_paths,
            )
        result = attest_full_audit(result, audio_audits)
        if audio_proposals:
            proposals = _merge_proposal_batches((text_proposals, audio_proposals))
            result = add_correction_proposals(
                result,
                audio_proposals,
                allowed_reference_ids=tuple(item.id for item in references),
            )
        try:
            arbitration_receipts, arbitration_artifacts = self._run_arbitration(
                result=result,
                proposals=proposals,
                normalized_audio=normalized_audio,
                normalized_audio_hash=receipt.normalized.sha256,
                normalized_audio_duration_ms=receipt.normalized_duration_ms,
                references=references,
                retrievals=retrievals,
            )
        except AdapterWorkPending as pending:
            return Interrupted(
                operation="resolve",
                reason=str(pending),
                generation_id=result.transcript.generation_id,
                packet_paths=pending.packet_paths,
                response_paths=pending.response_paths,
            )
        audio_execution_artifacts = _merge_execution_artifacts(
            audio_execution_artifacts,
            arbitration_artifacts,
        )
        _validate_proposal_ownership(
            proposals,
            correction_audits,
            audio_audits,
            arbitration_receipts,
            require_arbitration=self._arbiter is not None,
        )
        orthographic_projections = parent.recognition.orthographic_projections
        orthographic_raw_outputs = {
            item.name: item.payload
            for item in parent.recognition.orthographic_raw_outputs.artifacts
        }
        parent_manifest_hash = hash_object(parent.storage.manifest.materialize())
        recognition_request_artifact = parent.recognition.request_artifact
        recognition_independence = parent.recognition.independence
        outcome = self._commit_generation(
            result=result,
            receipt=receipt,
            recognition_request=recognition_request_artifact,
            recognition_independence=recognition_independence,
            evidence=evidence,
            orthographic_projections=orthographic_projections,
            orthographic_raw_outputs=orthographic_raw_outputs,
            speaker_base_evidence=speaker_base_evidence,
            speaker_provenance=speaker_provenance,
            speaker_attributor_identity=(
                self._speaker_attributor_identity
                if speaker_provenance is not None
                else None
            ),
            enrolled_artifacts=enrolled_artifacts,
            reference_retrieval_policy=reference_retrieval_policy,
            references=references,
            retrievals=retrievals,
            proposals=proposals,
            correction_audits=correction_audits,
            targeted_correction_audits=targeted_correction_audits,
            audio_audits=audio_audits,
            arbitration_receipts=arbitration_receipts,
            correction_execution_artifacts=correction_execution_artifacts,
            audio_execution_artifacts=audio_execution_artifacts,
            speech_coverage_receipt=speech_coverage_receipt,
            speech_coverage_raw_artifacts=speech_coverage_raw_artifacts,
            raw_outputs=raw_outputs,
            reference_source_snapshots=reference_source_snapshots,
            reference_extraction_snapshots=reference_extraction_snapshots,
            parent_manifest_hash=parent_manifest_hash,
            activate=False,
        )
        child_record = self.store.load(outcome.generation_id)
        journal_identity = {
            "schema_version": _RESOLUTION_TRANSACTION_VERSION,
            "episode_id": current.transcript.episode_id,
            "event_id": decision.event_id,
            "parent_generation_id": current.transcript.generation_id,
            "parent_manifest_hash": parent_manifest_hash,
            "parent_ledger_hash": current.transcript.ledger_hash,
            "child_generation_id": result.transcript.generation_id,
            "child_manifest_hash": hash_object(child_record.manifest),
            "child_ledger_hash": result.transcript.ledger_hash,
            "ledger_entry": _ledger_entry_payload(ledger_entry),
        }
        journal = {
            **journal_identity,
            "transaction_id": f"resolution-{hash_object(journal_identity)}",
            "state": "prepared",
        }
        self.store.write_resolution_journal_locked(journal)
        appended = self.ledger.append_prepared(ledger_entry)
        if appended != ledger_entry:  # pragma: no cover - Ledger enforces equality
            raise ResolutionTransactionError(
                "Correction Ledger changed the prepared resolution entry"
            )
        self.store.set_active_cas_locked(
            outcome.generation_id,
            expected_generation_id=request.generation_id,
        )
        self.store.write_resolution_journal_locked({**journal, "state": "completed"})
        return outcome

    def load_verified_projection(
        self,
        projection_id: str,
        *,
        expected_episode_id: str,
        expected_generation_id: str,
        expected_manifest_sha256: str,
    ) -> VerifiedProjection:
        """Reopen one projection as a typed, exact-byte Stage 5 handoff.

        This read-only operation delegates to the same exhaustive disk verifier
        used at projection commit.  It reopens the Canonical Generation and
        replays stored Semantic execution receipts without starting recognition,
        correction, audio audit, or other provider work.
        """

        if not expected_episode_id.strip():
            raise ValueError("verified projection handoff requires an episode ID")
        if not expected_generation_id.strip():
            raise ValueError("verified projection handoff requires a Generation ID")
        if not re.fullmatch(r"[0-9a-f]{64}", expected_manifest_sha256):
            raise ValueError("verified projection handoff requires a manifest SHA-256")
        artifacts = self._verify_projection_directory(
            projection_id,
            expected_generation_id=expected_generation_id,
        )
        try:
            projection = SubtitleProjection.model_validate_json(artifacts[_PROJECTION])
            manifest = ProjectionManifest.model_validate_json(
                artifacts[_PROJECTION_MANIFEST]
            )
            quality_report = QualityReport.model_validate_json(artifacts[_QUALITY])
        except ValueError as exc:  # exhaustive verifier already parsed these
            raise GenerationIsolationError(
                "verified projection handoff contains an invalid typed artifact"
            ) from exc
        if {
            projection.episode_id,
            manifest.episode_id,
            quality_report.episode_id,
        } != {expected_episode_id}:
            raise GenerationIsolationError(
                "verified projection episode does not match the Stage 5 binding"
            )
        if {
            projection.generation_id,
            manifest.generation_id,
            quality_report.generation_id,
        } != {expected_generation_id}:
            raise GenerationIsolationError(
                "verified projection Generation does not match the Stage 5 binding"
            )
        manifest_sha256 = sha256_bytes(artifacts[_PROJECTION_MANIFEST])
        if manifest_sha256 != expected_manifest_sha256:
            raise GenerationIsolationError(
                "verified Projection Manifest digest does not match the Stage 5 binding"
            )
        if not quality_report.passed:
            raise GenerationIsolationError(
                "Stage 5 handoff requires a mechanically passing Quality Report"
            )
        return VerifiedProjection(
            projection_id=projection_id,
            episode_id=expected_episode_id,
            generation_id=expected_generation_id,
            projection=projection,
            manifest=manifest,
            quality_report=quality_report,
            projection_bytes=artifacts[_PROJECTION],
            manifest_bytes=artifacts[_PROJECTION_MANIFEST],
            quality_report_bytes=artifacts[_QUALITY],
            srt_bytes=artifacts[_SRT],
            sidecar_bytes=artifacts[_SIDECAR],
            projection_sha256=sha256_bytes(artifacts[_PROJECTION]),
            manifest_sha256=manifest_sha256,
            quality_report_sha256=sha256_bytes(artifacts[_QUALITY]),
            srt_sha256=sha256_bytes(artifacts[_SRT]),
            sidecar_sha256=sha256_bytes(artifacts[_SIDECAR]),
        )

    def project(self, request: ProjectRequest) -> ProjectOutcome:
        loaded = self._load_generation(request.generation_id, require_active=True)
        result = loaded.result
        receipt = loaded.normalization.receipt
        transcript = result.transcript
        if transcript.status != "accepted" or result.outcome != "accepted":
            raise ModuleInvariantError("project requires an accepted active Canonical Transcript")
        protected_ranges = _semantic_protected_ranges(
            transcript,
            loaded.references.retrieval_policy,
            loaded.references.retrievals,
        )
        semantic_request = SemanticAnalysisRequest(
            transcript=transcript,
            language=request.profile.language,
            policy_hash=transcript.policy_hash,
            protected_ranges=protected_ranges,
        )
        try:
            semantic_run = self._semantic_analyzer.partition_with_receipts(semantic_request)
            units = _validate_semantic_partition(
                transcript,
                semantic_run.units,
            )
        except AdapterWorkPending as pending:
            return Interrupted(
                operation="project",
                reason=str(pending),
                generation_id=transcript.generation_id,
                packet_paths=pending.packet_paths,
                response_paths=pending.response_paths,
            )
        if receipt.normalized_duration_ms is None:
            raise ModuleInvariantError("accepted receipt lacks normalized audio duration")
        semantic_adapter_hash = semantic_run.execution_receipts[0].adapter_identity_hash
        boundary_constraints = build_boundary_constraint_receipt(
            transcript,
            units,
            request.profile,
            protected_ranges=protected_ranges,
            semantic_adapter_identity_hash=semantic_adapter_hash,
        )
        mandatory_cue_boundaries: tuple[int, ...] = ()
        allowed_cue_boundaries: tuple[int, ...] | None = None
        authoritative_cues: tuple[tuple[int, int, int, int], ...] = ()
        memo_boundary_authority_hash: str | None = None
        if self._memo_boundary_authority_factory is not None:
            authority = self._memo_boundary_authority_factory(
                loaded.recognition.evidence[0]
            )
            memo_boundary_authority_hash = authority.content_hash
            mandatory_cue_boundaries, authoritative_cues = authority.projection_contract(
                transcript
            )
            allowed_cue_boundaries = mandatory_cue_boundaries
        projected = project_semantic_units(
            transcript.tokens,
            units,
            request.profile,
            episode_id=transcript.episode_id,
            generation_id=transcript.generation_id,
            audio_start_ms=0,
            audio_end_ms=receipt.normalized_duration_ms,
            canonical_spans=transcript.spans,
            boundary_constraints=boundary_constraints,
            mandatory_cue_boundaries=mandatory_cue_boundaries,
            allowed_cue_boundaries=allowed_cue_boundaries,
            authoritative_cues=authoritative_cues,
        )
        report = assert_projection_quality(
            transcript.tokens,
            units,
            projected.projection,
            request.profile,
            audio_start_ms=0,
            audio_end_ms=receipt.normalized_duration_ms,
            canonical_spans=transcript.spans,
            boundary_constraints=boundary_constraints,
        )
        srt = render_srt(projected)
        sidecar = render_sidecar_json(projected)
        profile_hash = hash_object(request.profile)
        semantic_units_hash = hash_object(units)
        semantic_receipts_hash = hash_object(semantic_run.execution_receipts)
        boundary_constraints_hash = hash_object(boundary_constraints)
        output_lineage = {
            "projection": hash_object(projected.projection),
            "quality_report": hash_object(report),
            "semantic_units": semantic_units_hash,
            "semantic_analyzer": semantic_adapter_hash,
            "semantic_execution_receipts": semantic_receipts_hash,
            "boundary_constraints": boundary_constraints_hash,
            "sidecar": sha256_bytes(sidecar.encode("utf-8")),
            "srt": sha256_bytes(srt.encode("utf-8")),
        }
        if memo_boundary_authority_hash is not None:
            output_lineage["memo_boundary_authority"] = memo_boundary_authority_hash
        output_hash = hash_object(output_lineage)
        projection_manifest = ProjectionManifest(
            episode_id=transcript.episode_id,
            generation_id=transcript.generation_id,
            canonical_hash=transcript.content_hash,
            profile_hash=profile_hash,
            token_sequence_hash=token_sequence_hash(
                [token.id for token in transcript.tokens]
            ),
            output_hash=output_hash,
        )
        projection_id = "projection-" + hash_object(
            {
                "projection": projected.projection,
                "manifest": projection_manifest,
                "quality": report,
                "semantic_units_hash": semantic_units_hash,
                "semantic_analyzer_hash": semantic_adapter_hash,
                "semantic_execution_receipts_hash": semantic_receipts_hash,
                "boundary_constraints_hash": boundary_constraints_hash,
            }
        )
        self._commit_projection(
            projection_id=projection_id,
            generation_id=transcript.generation_id,
            projection=projected.projection,
            manifest=projection_manifest,
            report=report,
            units=units,
            profile=request.profile,
            srt=srt,
            sidecar=sidecar,
            semantic_units_hash=semantic_units_hash,
            semantic_analyzer_hash=semantic_adapter_hash,
            semantic_execution_receipts_hash=semantic_receipts_hash,
            boundary_constraints_hash=boundary_constraints_hash,
            boundary_constraints=boundary_constraints,
            semantic_run=semantic_run,
        )
        return self.load_verified_projection(
            projection_id,
            expected_episode_id=transcript.episode_id,
            expected_generation_id=transcript.generation_id,
            expected_manifest_sha256=sha256_bytes(_model_bytes(projection_manifest)),
        )

    def _run_corrector(
        self,
        *,
        episode_id: str,
        result: CanonicalBuildResult,
        mode: Literal["full_audit", "targeted_review"],
        target_windows: tuple[tuple[str, ...], ...],
        evidence: tuple[RecognitionEvidence, ...],
        references: tuple[ReferenceEvidence, ...],
        references_by_span: Mapping[str, tuple[ReferenceEvidence, ...]] | None = None,
    ) -> tuple[
        tuple[CorrectionProposal, ...],
        tuple[CorrectionAuditReceipt, ...],
        tuple[_TargetedCorrectionAuditReceipt, ...],
        dict[str, bytes],
    ]:
        batches: list[tuple[CorrectionProposal, ...]] = []
        full_audits: list[CorrectionAuditReceipt] = []
        targeted_audits: list[_TargetedCorrectionAuditReceipt] = []
        execution_artifacts: dict[str, bytes] = {}
        material = _material_issues(result.transcript)
        for target_span_ids in target_windows:
            target_set = set(target_span_ids)
            issues = tuple(
                issue
                for issue in material
                if set(issue.span_ids).intersection(target_set)
            )
            window_references = references
            if references_by_span is not None:
                by_id: dict[str, ReferenceEvidence] = {}
                for span_id in target_span_ids:
                    for item in references_by_span.get(span_id, ()):
                        by_id[item.id] = item
                window_references = tuple(sorted(by_id.values(), key=lambda item: item.id))
            request = CorrectionRequest(
                episode_id=episode_id,
                generation_id=result.transcript.generation_id,
                mode=mode,
                transcript=result.transcript,
                target_span_ids=target_span_ids,
                review_issues=issues,
                evidence=evidence,
                reference_evidence=window_references,
                available_reference_evidence=references,
            )
            run = self._corrector.propose_with_receipt(request)
            proposed = tuple(run.proposals)
            if any(
                proposal.evidence_basis not in {
                    "recognition",
                    "recognition_and_reference",
                    "reference_only",
                }
                for proposal in proposed
            ):
                raise GenerationIsolationError(
                    "text Corrector cannot claim that it heard Audio Evidence"
                )
            for proposal in proposed:
                unknown = set(proposal.audio_span_ids) - target_set
                if unknown:
                    raise GenerationIsolationError(
                        "Corrector returned a proposal outside its exact target window: "
                        f"{sorted(unknown)!r}"
                    )
            batch = _merge_proposal_batches((proposed,))
            if len(run.execution_receipts) != 1:
                raise GenerationIsolationError(
                    "one bounded CorrectionRequest must yield exactly one execution receipt"
                )
            execution = run.execution_receipts[0]
            if execution.adapter_identity != self._corrector_execution_identity:
                raise GenerationIsolationError(
                    "Correction execution receipt differs from runtime Adapter identity"
                )
            if execution.target_span_ids != target_span_ids:
                raise GenerationIsolationError(
                    "Correction execution receipt targets another audit window"
                )
            expected_presented_ids = tuple(item.id for item in window_references)
            expected_presented_hash = reference_evidence_set_hash(window_references)
            packet_presented_ids, packet_presented_hash = (
                _correction_packet_reference_binding(run.request_bytes[0])
            )
            if (
                packet_presented_ids != expected_presented_ids
                or packet_presented_hash != expected_presented_hash
                or
                execution.presented_reference_evidence_ids != expected_presented_ids
                or execution.presented_reference_evidence_hash != expected_presented_hash
            ):
                raise GenerationIsolationError(
                    "Correction execution receipt does not bind the Module's exact "
                    "least-context Reference Evidence tuple"
                )
            proposal_ids = tuple(proposal.id for proposal in batch)
            if tuple(execution.proposal_ids) != proposal_ids:
                raise GenerationIsolationError(
                    "Correction execution receipt does not bind exact proposal order"
                )
            try:
                replayed = self._corrector.replay(
                    request,
                    proposals=batch,
                    execution_receipts=(execution,),
                    request_bytes=run.request_bytes,
                    response_bytes=run.response_bytes,
                )
            except (AdapterIntegrityError, AttributeError, TypeError, ValueError) as exc:
                raise GenerationIsolationError(
                    "Corrector execution proof is not reproducible before checkpoint commit"
                ) from exc
            if replayed != run:
                raise GenerationIsolationError(
                    "Corrector replay changed its complete typed result before checkpoint commit"
                )
            batches.append(batch)
            for name, payload in run.artifacts.items():
                existing = execution_artifacts.get(name)
                if existing is not None and existing != payload:
                    raise GenerationIsolationError(
                        "Correction execution artifact digest has conflicting bytes"
                    )
                execution_artifacts[name] = payload
            identity = execution.adapter_identity
            if mode == "full_audit":
                full_audits.append(
                    CorrectionAuditReceipt(
                        episode_id=episode_id,
                        preaudit_generation_id=result.transcript.generation_id,
                        preaudit_content_hash=result.transcript.content_hash,
                        evidence_hash=recognition_evidence_set_hash(evidence),
                        reference_evidence_hash=result.transcript.reference_evidence_hash,
                        available_reference_evidence_ids=tuple(
                            item.id for item in references
                        ),
                        available_reference_evidence_hash=reference_evidence_set_hash(
                            references
                        ),
                        presented_reference_evidence_ids=tuple(
                            execution.presented_reference_evidence_ids
                        ),
                        presented_reference_evidence_hash=(
                            execution.presented_reference_evidence_hash
                        ),
                        policy_hash=result.transcript.policy_hash,
                        target_span_ids=target_span_ids,
                        work_packet_id=execution.work_packet_id,
                        request=execution.request,
                        response=execution.response,
                        request_hash=execution.request_hash,
                        response_hash=execution.response_hash,
                        adapter_name=identity.adapter_name,
                        adapter_version=identity.adapter_version,
                        model=identity.model,
                        model_version=identity.model_version,
                        runtime_hash=identity.runtime_hash,
                        adapter_code_hash=identity.adapter_code_hash,
                        config_hash=identity.config_hash,
                        execution_mode=identity.execution_mode,
                        adapter_identity_hash=identity.content_hash,
                        proposal_ids=proposal_ids,
                        proposal_set_hash=hash_object(batch),
                    )
                )
            else:
                targeted_audits.append(
                    _TargetedCorrectionAuditReceipt(
                        schema_version=1,
                        mode="targeted_review",
                        generation_id=result.transcript.generation_id,
                        canonical_content_hash=result.transcript.content_hash,
                        evidence_hash=recognition_evidence_set_hash(evidence),
                        policy_hash=result.transcript.policy_hash,
                        reference_evidence_hash=result.transcript.reference_evidence_hash,
                        available_reference_evidence_ids=tuple(
                            item.id for item in references
                        ),
                        available_reference_evidence_hash=reference_evidence_set_hash(
                            references
                        ),
                        presented_reference_evidence_ids=tuple(
                            execution.presented_reference_evidence_ids
                        ),
                        presented_reference_evidence_hash=(
                            execution.presented_reference_evidence_hash
                        ),
                        work_packet_id=execution.work_packet_id,
                        request=execution.request,
                        request_hash=execution.request_hash,
                        target_span_ids=target_span_ids,
                        response=execution.response,
                        response_hash=execution.response_hash,
                        adapter_name=identity.adapter_name,
                        adapter_version=identity.adapter_version,
                        model=identity.model,
                        model_version=identity.model_version,
                        runtime_hash=identity.runtime_hash,
                        adapter_code_hash=identity.adapter_code_hash,
                        config_hash=identity.config_hash,
                        execution_mode=identity.execution_mode,
                        adapter_identity_hash=identity.content_hash,
                        proposal_ids=proposal_ids,
                        proposal_set_hash=hash_object(batch),
                    )
                )
        return (
            _merge_proposal_batches(batches),
            tuple(full_audits),
            tuple(targeted_audits),
            execution_artifacts,
        )

    def _run_audio_audit(
        self,
        *,
        result: CanonicalBuildResult,
        target_windows: tuple[tuple[str, ...], ...],
        normalized_audio: Path,
        normalized_audio_hash: str,
        normalized_audio_duration_ms: int,
        evidence: tuple[RecognitionEvidence, ...],
        references: tuple[ReferenceEvidence, ...],
        references_by_span: Mapping[str, tuple[ReferenceEvidence, ...]] | None = None,
    ) -> tuple[
        tuple[CorrectionProposal, ...],
        tuple[AudioAuditReceipt, ...],
        dict[str, bytes],
    ]:
        batches: list[tuple[CorrectionProposal, ...]] = []
        receipts: list[AudioAuditReceipt] = []
        artifacts: dict[str, bytes] = {}
        for target_span_ids in target_windows:
            window_references = references
            if references_by_span is not None:
                by_id: dict[str, ReferenceEvidence] = {}
                for span_id in target_span_ids:
                    for item in references_by_span.get(span_id, ()):
                        by_id[item.id] = item
                window_references = tuple(
                    sorted(by_id.values(), key=lambda item: item.id)
                )
            run = self._audio_auditor.audit(
                AudioAuditRequest(
                    transcript=result.transcript,
                    normalized_audio=normalized_audio,
                    expected_normalized_audio_hash=normalized_audio_hash,
                    normalized_audio_duration_ms=normalized_audio_duration_ms,
                    target_span_ids=target_span_ids,
                    evidence=evidence,
                    reference_evidence=window_references,
                )
            )
            receipt = run.receipt
            if (
                receipt.target_span_ids != target_span_ids
                or receipt.adapter_identity_hash
                != self._audio_auditor.identity.content_hash
                or receipt.adapter_name != self._audio_auditor.identity.adapter_name
                or receipt.adapter_version != self._audio_auditor.identity.adapter_version
                or receipt.model != self._audio_auditor.identity.model
                or receipt.model_version != self._audio_auditor.identity.model_version
                or receipt.runtime_hash != self._audio_auditor.identity.runtime_hash
                or receipt.adapter_code_hash
                != self._audio_auditor.identity.adapter_code_hash
                or receipt.config_hash != self._audio_auditor.identity.config_hash
            ):
                raise GenerationIsolationError(
                    "Audio Audit Receipt differs from configured runtime identity or window"
                )
            for proposal in run.proposals:
                if proposal.evidence_basis not in {"audio", "audio_and_reference"}:
                    raise GenerationIsolationError(
                        "Audio Auditor proposal lacks an audio evidence basis"
                    )
                if not set(proposal.audio_span_ids).issubset(target_span_ids):
                    raise GenerationIsolationError(
                        "Audio Auditor proposal escaped its exact window"
                    )
            for name, payload in run.artifacts.items():
                existing = artifacts.get(name)
                if existing is not None and existing != payload:
                    raise GenerationIsolationError(
                        "Audio Audit artifact digest has conflicting bytes"
                    )
                artifacts[name] = payload
            batches.append(run.proposals)
            receipts.append(receipt)
        proposals = _merge_proposal_batches(batches)
        receipt_proposal_ids = tuple(
            proposal_id for receipt in receipts for proposal_id in receipt.proposal_ids
        )
        if receipt_proposal_ids != tuple(proposal.id for proposal in proposals):
            raise GenerationIsolationError(
                "Audio Audit receipts do not bind the exact merged proposal order"
            )
        return proposals, tuple(receipts), artifacts

    @staticmethod
    def _audio_execution_bytes(
        artifact: ArtifactDigest,
        artifacts: Mapping[str, bytes],
        *,
        context: str,
    ) -> bytes:
        """Resolve one portable execution artifact and recheck its exact bytes."""

        name = artifact.uri.removeprefix("generation-artifact://")
        if name == artifact.uri:
            raise GenerationIsolationError(
                f"{context} execution artifact URI is not portable"
            )
        try:
            payload = artifacts[name]
        except KeyError as exc:
            raise GenerationIsolationError(
                f"{context} execution artifact is missing: {name!r}"
            ) from exc
        if sha256_bytes(payload) != artifact.sha256 or len(payload) != artifact.size_bytes:
            raise GenerationIsolationError(
                f"{context} execution bytes differ from their typed digest"
            )
        return payload

    @staticmethod
    def _audio_audit_reference_context(
        transcript: CanonicalTranscript,
        target_span_ids: tuple[str, ...],
        references: tuple[ReferenceEvidence, ...],
        retrievals: tuple[ReferenceRetrievalReceipt, ...],
    ) -> tuple[ReferenceEvidence, ...]:
        """Recreate the least-context reference set supplied to one audit window."""

        references_by_span = _reference_evidence_by_span(references, retrievals)
        span_by_id = {span.id: span for span in transcript.spans}
        selected: dict[str, ReferenceEvidence] = {}
        try:
            target_spans = tuple(span_by_id[span_id] for span_id in target_span_ids)
        except KeyError as exc:
            raise GenerationIsolationError(
                f"Audio Audit targets unknown current span {exc.args[0]!r}"
            ) from exc
        for span in target_spans:
            for lineage_span_id in (span.id, *span.lineage):
                for item in references_by_span.get(lineage_span_id, ()):
                    selected[item.id] = item
        return tuple(sorted(selected.values(), key=lambda item: item.id))

    def _verify_audio_audit_replays(
        self,
        *,
        transcript: CanonicalTranscript,
        normalization_receipt: NormalizationReceipt,
        evidence: tuple[RecognitionEvidence, ...],
        references: tuple[ReferenceEvidence, ...],
        retrievals: tuple[ReferenceRetrievalReceipt, ...],
        proposals: tuple[CorrectionProposal, ...],
        receipts: tuple[AudioAuditReceipt, ...],
        artifacts: Mapping[str, bytes],
        context: str,
    ) -> None:
        """Replay every audio full-audit proof from current immutable inputs."""

        preaudit_transcript = _derive_audio_preaudit_transcript(
            transcript,
            proposals=proposals,
            audio_audits=receipts,
        )
        _validate_audio_full_audit_attestation(
            transcript,
            receipts,
            self._audio_auditor.identity,
            preaudit_transcript=preaudit_transcript,
        )
        if normalization_receipt.normalized_duration_ms is None:
            raise GenerationIsolationError(
                f"{context} Audio Audit lacks Normalized Audio duration"
            )
        normalized_audio = self.store.audio_path(
            normalization_receipt.normalized.sha256,
            size_bytes=normalization_receipt.normalized.size_bytes,
        )
        proposal_by_id = {proposal.id: proposal for proposal in proposals}
        if len(proposal_by_id) != len(proposals):
            raise GenerationIsolationError(
                f"{context} Correction Proposal IDs are not unique"
            )
        for receipt in receipts:
            try:
                audited = tuple(proposal_by_id[item] for item in receipt.proposal_ids)
            except KeyError as exc:
                raise GenerationIsolationError(
                    f"{context} Audio Audit cites unknown Proposal {exc.args[0]!r}"
                ) from exc
            try:
                request = AudioAuditRequest(
                    transcript=preaudit_transcript,
                    normalized_audio=normalized_audio,
                    expected_normalized_audio_hash=normalization_receipt.normalized.sha256,
                    normalized_audio_duration_ms=normalization_receipt.normalized_duration_ms,
                    target_span_ids=receipt.target_span_ids,
                    evidence=evidence,
                    reference_evidence=self._audio_audit_reference_context(
                        transcript,
                        receipt.target_span_ids,
                        references,
                        retrievals,
                    ),
                )
            except (TypeError, ValueError) as exc:
                raise GenerationIsolationError(
                    f"{context} Audio Audit request cannot be reconstructed"
                ) from exc
            request_bytes = self._audio_execution_bytes(
                receipt.request,
                artifacts,
                context=f"{context} Audio Audit request",
            )
            response_bytes = self._audio_execution_bytes(
                receipt.response,
                artifacts,
                context=f"{context} Audio Audit response",
            )
            clip_bytes = self._audio_execution_bytes(
                receipt.clip,
                artifacts,
                context=f"{context} Audio Audit clip",
            )
            try:
                replayed = self._audio_auditor.replay(
                    request,
                    proposals=audited,
                    receipt=receipt,
                    request_bytes=request_bytes,
                    response_bytes=response_bytes,
                    clip_bytes=clip_bytes,
                )
            except (AdapterIntegrityError, TypeError, ValueError) as exc:
                raise GenerationIsolationError(
                    f"{context} Audio Audit proof is not reproducible"
                ) from exc
            if replayed.receipt != receipt or replayed.proposals != audited:
                raise GenerationIsolationError(
                    f"{context} Audio Audit replay changed its typed result"
                )

    def _verify_arbitration_replays(
        self,
        *,
        transcript: CanonicalTranscript,
        normalization_receipt: NormalizationReceipt,
        references: tuple[ReferenceEvidence, ...],
        retrievals: tuple[ReferenceRetrievalReceipt, ...],
        proposals: tuple[CorrectionProposal, ...],
        receipts: tuple[ArbitrationReceipt, ...],
        artifacts: Mapping[str, bytes],
        context: str,
    ) -> None:
        """Replay every stored Arbiter verdict without invoking its provider runner."""

        if not receipts:
            return
        if self._arbiter is None:
            raise GenerationIsolationError(
                f"{context} Arbitration proofs lack their configured Arbiter"
            )
        if normalization_receipt.normalized_duration_ms is None:
            raise GenerationIsolationError(
                f"{context} Arbitration lacks Normalized Audio duration"
            )
        normalized_audio = self.store.audio_path(
            normalization_receipt.normalized.sha256,
            size_bytes=normalization_receipt.normalized.size_bytes,
        )
        proposal_by_id = {proposal.id: proposal for proposal in proposals}
        if len(proposal_by_id) != len(proposals):
            raise GenerationIsolationError(
                f"{context} Correction Proposal IDs are not unique"
            )
        for receipt in receipts:
            try:
                proposal = proposal_by_id[receipt.proposal_id]
            except KeyError as exc:
                raise GenerationIsolationError(
                    f"{context} Arbitration cites unknown Proposal {exc.args[0]!r}"
                ) from exc
            if (
                receipt.generation_id != transcript.generation_id
                or receipt.canonical_content_hash != transcript.content_hash
                or receipt.evidence_hash != transcript.evidence_hash
                or receipt.reference_evidence_hash
                != transcript.reference_evidence_hash
                or receipt.policy_hash != transcript.policy_hash
                or receipt.normalized_audio_hash != transcript.normalized_audio_hash
            ):
                raise GenerationIsolationError(
                    f"{context} Arbitration receipt is not bound to current child truth"
                )
            try:
                request = ArbitrationRequest(
                    episode_id=receipt.episode_id,
                    generation_id=receipt.generation_id,
                    transcript=transcript,
                    normalized_audio=normalized_audio,
                    expected_normalized_audio_hash=normalization_receipt.normalized.sha256,
                    normalized_audio_duration_ms=normalization_receipt.normalized_duration_ms,
                    proposal=proposal,
                    reference_evidence=self._audio_audit_reference_context(
                        transcript,
                        proposal.audio_span_ids,
                        references,
                        retrievals,
                    ),
                )
                verdict = ArbitrationVerdict(
                    proposal_id=receipt.proposal_id,
                    status=receipt.status,
                    selected_text=receipt.selected_text,
                    confidence=receipt.confidence,
                    rationale=receipt.rationale,
                )
            except (TypeError, ValueError) as exc:
                raise GenerationIsolationError(
                    f"{context} Arbitration request cannot be reconstructed"
                ) from exc
            request_bytes = self._audio_execution_bytes(
                receipt.request,
                artifacts,
                context=f"{context} Arbitration request",
            )
            response_bytes = self._audio_execution_bytes(
                receipt.response,
                artifacts,
                context=f"{context} Arbitration response",
            )
            clip_bytes = self._audio_execution_bytes(
                receipt.clip,
                artifacts,
                context=f"{context} Arbitration clip",
            )
            try:
                replayed = self._arbiter.replay(
                    request,
                    verdict=verdict,
                    receipt=receipt,
                    request_bytes=request_bytes,
                    response_bytes=response_bytes,
                    clip_bytes=clip_bytes,
                )
            except (AdapterIntegrityError, TypeError, ValueError) as exc:
                raise GenerationIsolationError(
                    f"{context} Arbitration proof is not reproducible"
                ) from exc
            if replayed.receipt != receipt or replayed.verdict != verdict:
                raise GenerationIsolationError(
                    f"{context} Arbitration replay changed its typed result"
                )

    def _verify_correction_execution_replay(
        self,
        *,
        audit: CorrectionAuditReceipt | _TargetedCorrectionAuditReceipt,
        transcript: CanonicalTranscript,
        evidence: tuple[RecognitionEvidence, ...],
        references: tuple[ReferenceEvidence, ...],
        artifacts: Mapping[str, bytes],
        audited: tuple[CorrectionProposal, ...],
        context: str,
    ) -> None:
        """Replay one Corrector proof from current typed inputs and exact bytes."""

        request_name = audit.request.uri.removeprefix("generation-artifact://")
        response_name = audit.response.uri.removeprefix("generation-artifact://")
        try:
            request_bytes = artifacts[request_name]
            response_bytes = artifacts[response_name]
        except KeyError as exc:
            raise GenerationIsolationError(
                f"{context} Correction execution artifact is missing"
            ) from exc
        if (
            sha256_bytes(request_bytes) != audit.request.sha256
            or len(request_bytes) != audit.request.size_bytes
            or sha256_bytes(response_bytes) != audit.response.sha256
            or len(response_bytes) != audit.response.size_bytes
        ):
            raise GenerationIsolationError(
                f"{context} Correction request/response bytes differ from their receipt"
            )

        expected_generation_id = (
            audit.preaudit_generation_id
            if isinstance(audit, CorrectionAuditReceipt)
            else audit.generation_id
        )
        expected_content_hash = (
            audit.preaudit_content_hash
            if isinstance(audit, CorrectionAuditReceipt)
            else audit.canonical_content_hash
        )
        if isinstance(audit, CorrectionAuditReceipt) and (
            transcript.episode_id != audit.episode_id
        ):
            raise GenerationIsolationError(
                f"{context} Correction audit belongs to another episode"
            )
        if (
            transcript.generation_id != expected_generation_id
            or transcript.content_hash != expected_content_hash
            or transcript.evidence_hash != audit.evidence_hash
            or transcript.reference_evidence_hash != audit.reference_evidence_hash
            or transcript.policy_hash != audit.policy_hash
        ):
            raise GenerationIsolationError(
                f"{context} Correction audit crossed its immutable pre-audit transcript"
            )
        references_by_id = {item.id: item for item in references}
        try:
            available = tuple(
                references_by_id[item]
                for item in audit.available_reference_evidence_ids
            )
            available_by_id = {item.id: item for item in available}
            presented = tuple(
                available_by_id[item]
                for item in audit.presented_reference_evidence_ids
            )
        except KeyError as exc:
            raise GenerationIsolationError(
                "Correction receipt presents unknown Reference Evidence"
            ) from exc
        if (
            audit.available_reference_evidence_hash
            != reference_evidence_set_hash(available)
            or audit.presented_reference_evidence_hash
            != reference_evidence_set_hash(presented)
        ):
            raise GenerationIsolationError(
                "Correction receipt available/presented Reference Evidence hash mismatch"
            )
        packet_presented_ids, packet_presented_hash = (
            _correction_packet_reference_binding(request_bytes)
        )
        if (
            packet_presented_ids != audit.presented_reference_evidence_ids
            or packet_presented_hash != audit.presented_reference_evidence_hash
        ):
            raise GenerationIsolationError(
                f"{context} Correction request bytes do not bind the receipt's exact "
                "presented Reference Evidence tuple"
            )
        identity = CorrectionModelIdentity(
            adapter_name=audit.adapter_name,
            adapter_version=audit.adapter_version,
            model=audit.model,
            model_version=audit.model_version,
            runtime_hash=audit.runtime_hash,
            adapter_code_hash=audit.adapter_code_hash,
            config_hash=audit.config_hash,
            execution_mode=audit.execution_mode,
        )
        if identity != self._corrector_execution_identity:
            raise GenerationIsolationError(
                f"{context} Correction execution identity differs from the runtime Adapter"
            )
        proposal_ids = tuple(item.id for item in audited)
        if proposal_ids != audit.proposal_ids or hash_object(audited) != audit.proposal_set_hash:
            raise GenerationIsolationError(
                f"{context} Correction execution does not bind its exact Proposal set"
            )
        target_set = set(audit.target_span_ids)
        review_issues = tuple(
            issue
            for issue in _material_issues(transcript)
            if set(issue.span_ids).intersection(target_set)
        )
        mode: Literal["full_audit", "targeted_review"] = (
            "full_audit"
            if isinstance(audit, CorrectionAuditReceipt)
            else "targeted_review"
        )
        request = CorrectionRequest(
            episode_id=transcript.episode_id,
            generation_id=transcript.generation_id,
            mode=mode,
            transcript=transcript,
            target_span_ids=audit.target_span_ids,
            evidence=evidence,
            review_issues=review_issues,
            reference_evidence=presented,
            available_reference_evidence=available,
        )
        receipt = CorrectionExecutionReceipt(
            work_packet_id=audit.work_packet_id,
            target_span_ids=audit.target_span_ids,
            presented_reference_evidence_ids=audit.presented_reference_evidence_ids,
            presented_reference_evidence_hash=audit.presented_reference_evidence_hash,
            request=audit.request,
            response=audit.response,
            adapter_identity=identity,
            proposal_ids=audit.proposal_ids,
        )
        try:
            replayed = self._corrector.replay(
                request,
                proposals=audited,
                execution_receipts=(receipt,),
                request_bytes=(request_bytes,),
                response_bytes=(response_bytes,),
            )
        except (AdapterIntegrityError, AttributeError, TypeError, ValueError) as exc:
            raise GenerationIsolationError(
                f"{context} Correction execution proof is not reproducible"
            ) from exc
        if (
            replayed.proposals != audited
            or replayed.execution_receipts != (receipt,)
            or replayed.request_bytes != (request_bytes,)
            or replayed.response_bytes != (response_bytes,)
        ):
            raise GenerationIsolationError(
                f"{context} Correction replay changed its complete typed result"
            )

    def _verify_correction_audit_replays(
        self,
        *,
        transcript: CanonicalTranscript,
        evidence: tuple[RecognitionEvidence, ...],
        references: tuple[ReferenceEvidence, ...],
        proposals: tuple[CorrectionProposal, ...],
        correction_audits: tuple[CorrectionAuditReceipt, ...],
        targeted_correction_audits: tuple[_TargetedCorrectionAuditReceipt, ...],
        audio_audits: tuple[AudioAuditReceipt, ...],
        artifacts: Mapping[str, bytes],
        context: str,
    ) -> None:
        """Derive each audit-time transcript and replay every stored execution."""

        proposal_by_id = {proposal.id: proposal for proposal in proposals}
        if len(proposal_by_id) != len(proposals):
            raise GenerationIsolationError(
                f"{context} Correction Proposal IDs are not unique"
            )
        full_proposal_ids = tuple(
            proposal_id
            for audit in correction_audits
            for proposal_id in audit.proposal_ids
        )
        targeted_proposal_ids = tuple(
            proposal_id
            for audit in targeted_correction_audits
            for proposal_id in audit.proposal_ids
        )
        audio_proposal_ids = tuple(
            proposal_id
            for audit in audio_audits
            for proposal_id in audit.proposal_ids
        )
        full_preaudit = _derive_correction_preaudit_transcript(
            transcript,
            proposals=proposals,
            remove_proposal_ids=(
                *full_proposal_ids,
                *targeted_proposal_ids,
                *audio_proposal_ids,
            ),
            audio_audits=audio_audits,
        )
        targeted_preaudit = _derive_correction_preaudit_transcript(
            transcript,
            proposals=proposals,
            remove_proposal_ids=(*targeted_proposal_ids, *audio_proposal_ids),
            audio_audits=audio_audits,
        )
        for audit in correction_audits:
            try:
                audited = tuple(proposal_by_id[item] for item in audit.proposal_ids)
            except KeyError as exc:
                raise GenerationIsolationError(
                    f"{context} Correction Audit cites unknown Proposal {exc.args[0]!r}"
                ) from exc
            self._verify_correction_execution_replay(
                audit=audit,
                transcript=full_preaudit,
                evidence=evidence,
                references=references,
                artifacts=artifacts,
                audited=audited,
                context=context,
            )
        for audit in targeted_correction_audits:
            try:
                audited = tuple(proposal_by_id[item] for item in audit.proposal_ids)
            except KeyError as exc:
                raise GenerationIsolationError(
                    f"{context} Targeted Correction Audit cites unknown Proposal "
                    f"{exc.args[0]!r}"
                ) from exc
            self._verify_correction_execution_replay(
                audit=audit,
                transcript=targeted_preaudit,
                evidence=evidence,
                references=references,
                artifacts=artifacts,
                audited=audited,
                context=context,
            )

    def _run_arbitration(
        self,
        *,
        result: CanonicalBuildResult,
        proposals: tuple[CorrectionProposal, ...],
        normalized_audio: Path,
        normalized_audio_hash: str,
        normalized_audio_duration_ms: int,
        references: tuple[ReferenceEvidence, ...],
        retrievals: tuple[ReferenceRetrievalReceipt, ...],
    ) -> tuple[tuple[ArbitrationReceipt, ...], dict[str, bytes]]:
        if not proposals or self._arbiter is None:
            return (), {}
        receipts: list[ArbitrationReceipt] = []
        artifacts: dict[str, bytes] = {}
        for proposal in proposals:
            run = self._arbiter.decide_with_receipt(
                ArbitrationRequest(
                    episode_id=result.transcript.episode_id,
                    generation_id=result.transcript.generation_id,
                    transcript=result.transcript,
                    normalized_audio=normalized_audio,
                    expected_normalized_audio_hash=normalized_audio_hash,
                    normalized_audio_duration_ms=normalized_audio_duration_ms,
                    proposal=proposal,
                    reference_evidence=self._audio_audit_reference_context(
                        result.transcript,
                        proposal.audio_span_ids,
                        references,
                        retrievals,
                    ),
                )
            )
            receipt = run.receipt
            identity = self._arbiter.identity
            if (
                receipt.proposal_id != proposal.id
                or receipt.proposal_hash != hash_object(proposal)
                or receipt.generation_id != result.transcript.generation_id
                or receipt.canonical_content_hash != result.transcript.content_hash
                or receipt.adapter_identity_hash != identity.content_hash
                or receipt.adapter_name != identity.adapter_name
                or receipt.adapter_version != identity.adapter_version
                or receipt.model != identity.model
                or receipt.model_version != identity.model_version
                or receipt.runtime_hash != identity.runtime_hash
                or receipt.adapter_code_hash != identity.adapter_code_hash
                or receipt.config_hash != identity.config_hash
            ):
                raise GenerationIsolationError(
                    "Arbitration Receipt crossed proposal, Generation, or runtime identity"
                )
            if receipt.status == "accepted" and receipt.selected_text != proposal.candidate_text:
                raise GenerationIsolationError(
                    "accepted Arbiter verdict selected text outside the supplied candidate"
                )
            if receipt.status == "rejected" and receipt.selected_text != proposal.observed_text:
                raise GenerationIsolationError(
                    "rejected Arbiter verdict did not preserve observed text"
                )
            for name, payload in run.artifacts.items():
                existing = artifacts.get(name)
                if existing is not None and existing != payload:
                    raise GenerationIsolationError(
                        "Arbitration artifact digest has conflicting bytes"
                    )
                artifacts[name] = payload
            receipts.append(receipt)
        return tuple(receipts), artifacts

    @staticmethod
    def _validate_decision_provenance(
        decision: CorrectionDecision,
        *,
        transcript: CanonicalTranscript,
        proposals: tuple[CorrectionProposal, ...],
        references: tuple[ReferenceEvidence, ...],
        retrievals: tuple[ReferenceRetrievalReceipt, ...],
        arbitration_receipts: tuple[ArbitrationReceipt, ...],
    ) -> None:
        if decision.replacement_alignment:
            raise ModuleInvariantError(
                "replacement_alignment is disabled until a typed "
                "ReplacementAlignmentReceipt contract is implemented"
            )
        proposal_by_id = {proposal.id: proposal for proposal in proposals}
        unknown_proposals = set(decision.proposal_ids) - set(proposal_by_id)
        if unknown_proposals:
            raise GenerationIsolationError(
                f"Correction Decision cites unknown Proposals: {sorted(unknown_proposals)!r}"
            )
        known_references = {item.id for item in references}
        unknown_references = set(decision.reference_evidence_ids) - known_references
        if unknown_references:
            raise GenerationIsolationError(
                "Correction Decision cites unknown Reference Evidence: "
                f"{sorted(unknown_references)!r}"
            )
        scoped_references = set(
            _reference_evidence_ids_for_target(
                transcript,
                references,
                retrievals,
                decision.target_span_ids,
            )
        )
        out_of_scope_references = (
            set(decision.reference_evidence_ids) - scoped_references
        )
        if out_of_scope_references:
            raise GenerationIsolationError(
                "Correction Decision cites Reference Evidence not retrieved for its "
                f"target span lineage: {sorted(out_of_scope_references)!r}"
            )
        receipt_by_id = {receipt.id: receipt for receipt in arbitration_receipts}
        if len(receipt_by_id) != len(arbitration_receipts):
            raise GenerationIsolationError("stored Arbitration Receipt IDs are not unique")
        unknown_arbitrations = set(decision.arbitration_receipt_ids) - set(receipt_by_id)
        if unknown_arbitrations:
            raise GenerationIsolationError(
                "Correction Decision cites unknown Arbitration Receipts: "
                f"{sorted(unknown_arbitrations)!r}"
            )
        if decision.action == "accept_candidate":
            selected_receipts = tuple(
                receipt_by_id[receipt_id]
                for receipt_id in decision.arbitration_receipt_ids
            )
            if len(selected_receipts) != len(decision.proposal_ids):
                raise ModuleInvariantError(
                    "accept_candidate requires one audio verdict per selected proposal"
                )
            by_proposal = {receipt.proposal_id: receipt for receipt in selected_receipts}
            if set(by_proposal) != set(decision.proposal_ids):
                raise GenerationIsolationError(
                    "Arbitration Receipts do not bind the Decision proposal set"
                )
            for proposal_id in decision.proposal_ids:
                receipt = by_proposal[proposal_id]
                proposal = proposal_by_id[proposal_id]
                if (
                    receipt.status != "accepted"
                    or receipt.selected_text != decision.selected_candidate
                    or receipt.selected_text != proposal.candidate_text
                    or receipt.proposal_hash != hash_object(proposal)
                    or receipt.episode_id != transcript.episode_id
                    or receipt.generation_id != transcript.generation_id
                    or receipt.canonical_content_hash != transcript.content_hash
                    or receipt.evidence_hash != transcript.evidence_hash
                    or receipt.reference_evidence_hash
                    != transcript.reference_evidence_hash
                    or receipt.policy_hash != transcript.policy_hash
                    or receipt.normalized_audio_hash
                    != transcript.normalized_audio_hash
                ):
                    raise GenerationIsolationError(
                        "accept_candidate is not authorized by an exact accepted audio verdict"
                    )
        unresolved_by_id = {
            issue.id: issue
            for issue in transcript.review_issues
            if issue.status == "unresolved"
        }
        unknown_issues = set(decision.issue_ids) - set(unresolved_by_id)
        if unknown_issues:
            raise GenerationIsolationError(
                f"Correction Decision cites unknown or resolved Issues: {sorted(unknown_issues)!r}"
            )
        omitted_target_issues = {
            issue.id
            for issue in unresolved_by_id.values()
            if set(issue.span_ids).issubset(decision.target_span_ids)
            and _is_material_issue(transcript, issue)
        } - set(decision.issue_ids)
        if omitted_target_issues:
            raise ModuleInvariantError(
                "Correction Decision must explicitly name every material target Issue: "
                f"{sorted(omitted_target_issues)!r}"
            )
        cross_span_issues = {
            issue.id
            for issue in (unresolved_by_id[item] for item in decision.issue_ids)
            if not set(issue.span_ids).issubset(decision.target_span_ids)
        }
        if cross_span_issues:
            raise GenerationIsolationError(
                "Correction Decision cites Issues outside its target spans: "
                f"{sorted(cross_span_issues)!r}"
            )
        span_by_id = {span.id: span for span in transcript.spans}
        token_by_id = {token.id: token for token in transcript.tokens}
        try:
            target_tokens = tuple(
                token_by_id[token_id]
                for span_id in decision.target_span_ids
                for token_id in span_by_id[span_id].token_ids
            )
        except KeyError as exc:
            raise GenerationIsolationError(
                f"Correction Decision targets unknown canonical identity {exc.args[0]!r}"
            ) from exc
        observed_text = "".join(token.text for token in target_tokens)
        linked = tuple(proposal_by_id[proposal_id] for proposal_id in decision.proposal_ids)
        proposal_reference_ids = {
            evidence_id
            for proposal in linked
            for evidence_id in proposal.reference_evidence_ids
        }
        if linked and set(decision.reference_evidence_ids) != proposal_reference_ids:
            raise GenerationIsolationError(
                "proposal-backed Correction Decision must cite exactly the Reference "
                "Evidence used by its selected Proposal set"
            )
        for proposal in linked:
            if proposal.audio_span_ids != decision.target_span_ids:
                raise GenerationIsolationError(
                    "Correction Decision and Proposal target different Audio Spans"
                )
            if (
                proposal.start_ms != decision.target_start_ms
                or proposal.end_ms != decision.target_end_ms
                or proposal.observed_text != observed_text
            ):
                raise GenerationIsolationError(
                    "Correction Decision Proposal does not match the reviewed canonical target"
                )
        if decision.action in {"accept_candidate", "reject_candidate"}:
            if len(linked) != 1 or decision.selected_candidate != linked[0].candidate_text:
                raise GenerationIsolationError(
                    "candidate Decision must exactly select one stored Correction Proposal"
                )
        if decision.action == "replace":
            if linked and (
                len(linked) != 1 or decision.replacement_text != linked[0].candidate_text
            ):
                raise GenerationIsolationError(
                    "proposal-backed replacement must exactly match one stored Proposal"
                )
            if not linked and decision.actor_kind != "human":
                raise ModuleInvariantError(
                    "a direct replacement without a Proposal requires a human reviewer"
                )
            if not linked and decision.reference_evidence_ids:
                raise ModuleInvariantError(
                    "a direct human replacement cannot claim Reference Evidence until a "
                    "typed HumanReferenceReviewReceipt contract is implemented"
                )

    def _validated_resolution_journal_locked(
        self,
    ) -> tuple[
        dict[str, object],
        LedgerEntry,
        CorrectionDecision | NativeCorrectionDecisionV2,
    ] | None:
        payload = self.store.read_resolution_journal_locked()
        if payload is None:
            return None
        required = {
            "schema_version",
            "transaction_id",
            "state",
            "episode_id",
            "event_id",
            "parent_generation_id",
            "parent_manifest_hash",
            "parent_ledger_hash",
            "child_generation_id",
            "child_manifest_hash",
            "child_ledger_hash",
            "ledger_entry",
        }
        if set(payload) != required:
            raise ResolutionTransactionError(
                "resolution transaction journal has an invalid payload shape"
            )
        string_fields = required - {"schema_version", "ledger_entry"}
        if (
            payload["schema_version"] not in {_RESOLUTION_TRANSACTION_VERSION, 2}
            or any(
                not isinstance(payload[field], str) or not payload[field]
                for field in string_fields
            )
            or payload["state"] not in {"prepared", "completed"}
        ):
            raise ResolutionTransactionError(
                "resolution transaction journal has invalid field values"
            )
        entry = _ledger_entry_from_payload(payload["ledger_entry"])
        try:
            decoded = decode_ledger_entry(entry)
            decision = decoded.decision
        except LedgerDecisionCodecError as exc:
            raise ResolutionTransactionError(
                "resolution transaction carries an invalid Correction Decision"
            ) from exc
        expected_family = (
            "native_correction_v2"
            if payload["schema_version"] == 2
            else "legacy_correction_v1"
        )
        if decoded.family != expected_family:
            raise ResolutionTransactionError(
                "resolution transaction schema differs from its Decision family"
            )
        if (
            payload["event_id"] != decision.event_id
            or payload["episode_id"] != decision.episode_id
            or payload["parent_generation_id"] != decision.generation_id
            or payload["parent_ledger_hash"] != entry.previous_hash
            or payload["child_ledger_hash"] != entry.entry_hash
        ):
            raise ResolutionTransactionError(
                "resolution transaction crossed Decision, Generation, or Ledger identity"
            )
        identity = {
            key: value
            for key, value in payload.items()
            if key not in {"transaction_id", "state"}
        }
        expected_id = f"resolution-{hash_object(identity)}"
        if payload["transaction_id"] != expected_id:
            raise ResolutionTransactionError(
                "resolution transaction ID does not match its immutable identity"
            )
        return payload, entry, decision

    def _verify_resolution_transition_locked(
        self,
        payload: Mapping[str, object],
        entry: LedgerEntry,
        decision: CorrectionDecision | NativeCorrectionDecisionV2,
    ) -> None:
        parent = self._load_generation(
            str(payload["parent_generation_id"]), require_active=False
        )
        child = self._load_generation(
            str(payload["child_generation_id"]), require_active=False
        )
        parent_result = parent.result
        child_result = child.result
        if (
            hash_object(parent.storage.manifest.materialize())
            != payload["parent_manifest_hash"]
            or hash_object(child.storage.manifest.materialize())
            != payload["child_manifest_hash"]
        ):
            raise ResolutionTransactionError(
                "resolution transaction Generation manifest hash mismatch"
            )
        child_manifest = child.storage.manifest.materialize()
        if child_manifest.parent_manifest_hash != payload["parent_manifest_hash"]:
            raise ResolutionTransactionError(
                "resolution transaction child does not name the exact parent manifest"
            )
        if (
            parent_result.transcript.ledger_hash != entry.previous_hash
            or child_result.transcript.ledger_hash != entry.entry_hash
            or child_result.transcript.episode_id != decision.episode_id
        ):
            raise ResolutionTransactionError(
                "resolution transaction transcript Ledger lineage mismatch"
            )
        if isinstance(decision, NativeCorrectionDecisionV2):
            if (
                parent_result.transcript.ledger_hash != entry.previous_hash
                or child_result.transcript.ledger_hash != entry.entry_hash
                or child_result.transcript.episode_id != decision.episode_id
            ):
                raise ResolutionTransactionError(
                    "native resolution transaction transcript Ledger lineage mismatch"
                )
            # The native Generation loader above already replayed the exact
            # authorization, candidate projection, fresh references, and both
            # child Full Audit terminal records.
            return
        if not isinstance(parent.audit, LoadedLegacyAuditState) or not isinstance(
            child.audit, LoadedLegacyAuditState
        ):
            raise ResolutionTransactionError(
                "legacy resolution transaction crossed an audit profile"
            )
        try:
            self._validate_decision_provenance(
                decision,
                transcript=parent_result.transcript,
                proposals=parent.audit.proposals,
                references=parent.references.evidence,
                retrievals=parent.references.retrievals,
                arbitration_receipts=parent.audit.arbitrations,
            )
            derived = _derive_resolution_with_child_audit(
                parent_result,
                decision,
                ledger_hash=entry.entry_hash,
                child_correction_audits=child.audit.correction_audits,
                child_audio_audits=child.audit.audio_audits,
                child_proposals=child.audit.proposals,
                child_retrievals=child.references.retrievals,
            )
        except (KeyError, ValueError, SubtitleV2Error) as exc:
            raise ResolutionTransactionError(
                "resolution transaction cannot replay its reviewed parent transition"
            ) from exc
        if derived.transcript != child_result.transcript:
            raise ResolutionTransactionError(
                "resolution transaction child is not the replayed Decision result"
            )

    def _recover_resolution_transaction_locked(self) -> None:
        validated = self._validated_resolution_journal_locked()
        if validated is None:
            return
        payload, entry, decision = validated
        self._verify_resolution_transition_locked(payload, entry, decision)
        entries = self.ledger.entries()
        matching = tuple(item for item in entries if item.entry_hash == entry.entry_hash)
        if len(matching) > 1 or (matching and matching[0] != entry):
            raise ResolutionTransactionError(
                "resolution transaction Ledger entry conflicts with persisted history"
            )
        if payload["state"] == "completed":
            if not matching:
                raise ResolutionTransactionError(
                    "completed resolution transaction is absent from the Ledger"
                )
            try:
                active_generation_id = self.store.active_generation_id()
            except SubtitleV2Error as exc:
                raise ResolutionTransactionError(
                    "completed resolution transaction has no active child"
                ) from exc
            if (
                active_generation_id != payload["child_generation_id"]
                or self.ledger.head_hash != entry.entry_hash
            ):
                raise ResolutionTransactionError(
                    "completed resolution transaction is not the active Ledger head"
                )
            return

        actual_head = entries[-1].entry_hash if entries else GENESIS_HASH
        if matching:
            if actual_head != entry.entry_hash:
                raise ResolutionTransactionError(
                    "new Ledger history appeared after an incomplete resolution transaction"
                )
        elif actual_head != entry.previous_hash:
            raise ResolutionTransactionError(
                "incomplete resolution transaction no longer follows the Ledger head"
            )

        try:
            active_generation_id = self.store.active_generation_id()
        except SubtitleV2Error as exc:
            raise ResolutionTransactionError(
                "incomplete resolution transaction has no verifiable active parent"
            ) from exc
        parent_id = str(payload["parent_generation_id"])
        child_id = str(payload["child_generation_id"])
        if active_generation_id not in {parent_id, child_id}:
            raise ResolutionTransactionError(
                "active pointer diverged from an incomplete resolution transaction"
            )
        if active_generation_id == child_id and not matching:
            raise ResolutionTransactionError(
                "active child was published before its Correction Ledger entry"
            )
        if not matching:
            appended = self.ledger.append_prepared(entry)
            if appended != entry:  # pragma: no cover - Ledger enforces equality
                raise ResolutionTransactionError(
                    "recovery changed the prepared Correction Ledger entry"
                )
        if active_generation_id == parent_id:
            self.store.set_active_cas_locked(
                child_id,
                expected_generation_id=parent_id,
            )
        self.store.write_resolution_journal_locked({**payload, "state": "completed"})

    def _validate_active_ledger_lineage(
        self,
        active: LoadedGenerationState,
    ) -> None:
        """Replay every parent Decision proving active text equals the Ledger chain."""

        active_result = active.result
        entries = self._require_current_ledger_head(active_result.transcript)
        head_hash = active_result.transcript.ledger_hash
        by_hash = {entry.entry_hash: entry for entry in entries}
        expected_child = active_result
        expected_child_storage = active.storage
        expected_child_correction_audits = (
            active.audit.correction_audits
            if isinstance(active.audit, LoadedLegacyAuditState)
            else ()
        )
        expected_child_audio_audits = (
            active.audit.audio_audits
            if isinstance(active.audit, LoadedLegacyAuditState)
            else ()
        )
        expected_child_proposals = (
            active.audit.proposals
            if isinstance(active.audit, LoadedLegacyAuditState)
            else ()
        )
        expected_child_retrievals = active.references.retrievals
        cursor = head_hash
        visited: set[str] = set()
        while cursor != GENESIS_HASH:
            if cursor in visited:
                raise GenerationIsolationError("Correction Ledger ancestry contains a cycle")
            visited.add(cursor)
            try:
                entry = by_hash[cursor]
                decoded = decode_ledger_entry(entry)
                decision = decoded.decision
            except (KeyError, LedgerDecisionCodecError) as exc:
                raise GenerationIsolationError(
                    "active transcript names an unknown or invalid Correction Ledger entry"
                ) from exc
            if decision.episode_id != active_result.transcript.episode_id:
                raise GenerationIsolationError(
                    "Correction Ledger mixes multiple episodes in one active ancestry"
                )
            parent = self._load_generation(
                decision.generation_id,
                require_active=False,
            )
            parent_result = parent.result
            if parent_result.transcript.ledger_hash != entry.previous_hash:
                raise GenerationIsolationError(
                    "Correction Ledger Decision does not descend from its stored parent"
                )
            child_manifest = expected_child_storage.manifest.materialize()
            if child_manifest.parent_manifest_hash != hash_object(
                parent.storage.manifest.materialize()
            ):
                raise GenerationIsolationError(
                    "active Generation manifest ancestry differs from Correction Ledger ancestry"
                )
            if isinstance(decision, NativeCorrectionDecisionV2):
                if (
                    expected_child.transcript.ledger_hash != entry.entry_hash
                    or expected_child.transcript.episode_id != decision.episode_id
                ):
                    raise GenerationIsolationError(
                        "active native child differs from Correction Ledger lineage"
                    )
                # Loading this native child already replayed its exact persisted
                # discovery, authorization, Decision, parent, and fresh Full Audit.
                # Here the mixed-family walker additionally proves that verified
                # transition occupies this exact Ledger edge.
            else:
                try:
                    self._validate_decision_provenance(
                        decision,
                        transcript=parent_result.transcript,
                        proposals=(
                            parent.audit.proposals
                            if isinstance(parent.audit, LoadedLegacyAuditState)
                            else ()
                        ),
                        references=parent.references.evidence,
                        retrievals=parent.references.retrievals,
                        arbitration_receipts=(
                            parent.audit.arbitrations
                            if isinstance(parent.audit, LoadedLegacyAuditState)
                            else ()
                        ),
                    )
                    derived = _derive_resolution_with_child_audit(
                        parent_result,
                        decision,
                        ledger_hash=entry.entry_hash,
                        child_correction_audits=expected_child_correction_audits,
                        child_audio_audits=expected_child_audio_audits,
                        child_proposals=expected_child_proposals,
                        child_retrievals=expected_child_retrievals,
                    )
                except (KeyError, ValueError, SubtitleV2Error) as exc:
                    raise GenerationIsolationError(
                        "active Correction Ledger Decision cannot replay from its parent"
                    ) from exc
                if derived.transcript != expected_child.transcript:
                    raise GenerationIsolationError(
                        "active Canonical Transcript differs from Correction Ledger replay"
                    )
            expected_child = parent_result
            expected_child_storage = parent.storage
            expected_child_correction_audits = (
                parent.audit.correction_audits
                if isinstance(parent.audit, LoadedLegacyAuditState)
                else ()
            )
            expected_child_audio_audits = (
                parent.audit.audio_audits
                if isinstance(parent.audit, LoadedLegacyAuditState)
                else ()
            )
            expected_child_proposals = (
                parent.audit.proposals
                if isinstance(parent.audit, LoadedLegacyAuditState)
                else ()
            )
            expected_child_retrievals = parent.references.retrievals
            cursor = entry.previous_hash

        root_manifest = expected_child_storage.manifest.materialize()
        if root_manifest.parent_manifest_hash is not None:
            raise GenerationIsolationError(
                "Correction Ledger root Generation unexpectedly names another parent"
            )

    def _generation_descends_from(
        self,
        descendant: CanonicalTranscript,
        *,
        ancestor_generation_id: str,
    ) -> bool:
        """Return whether a verified transcript lineage contains ``ancestor``.

        Callers must first load ``descendant`` with ``require_active=True`` so the
        full artifact and Decision replay has already succeeded.  This reader then
        follows only the validated Ledger identities; it never infers ancestry from
        revision numbers or from the latest create checkpoint.
        """

        if descendant.generation_id == ancestor_generation_id:
            return True
        entries = self.ledger.entries()
        by_hash = {entry.entry_hash: entry for entry in entries}
        cursor = descendant.ledger_hash
        visited: set[str] = set()
        while cursor != GENESIS_HASH:
            if cursor in visited:
                raise GenerationIsolationError("Correction Ledger ancestry contains a cycle")
            visited.add(cursor)
            try:
                entry = by_hash[cursor]
                decision = decode_ledger_entry(entry).decision
            except (KeyError, LedgerDecisionCodecError) as exc:
                raise GenerationIsolationError(
                    "Generation ancestry names an unknown or invalid Correction Ledger entry"
                ) from exc
            if decision.episode_id != descendant.episode_id:
                raise GenerationIsolationError(
                    "Correction Ledger mixes multiple episodes in one Generation ancestry"
                )
            if decision.generation_id == ancestor_generation_id:
                return True
            cursor = entry.previous_hash
        return False

    def _validate_reference_retrieval_state(
        self,
        *,
        transcript: CanonicalTranscript,
        policy: ReferenceRetrievalPolicySnapshot,
        invocation_id: str,
        enrolled_artifacts: tuple[ReferenceArtifact, ...],
        references: tuple[ReferenceEvidence, ...],
        retrievals: tuple[ReferenceRetrievalReceipt, ...],
        reference_extraction_snapshots: Mapping[str, bytes],
        context: str,
    ) -> None:
        """Rebuild and replay the exact current-generation Reference query set."""

        try:
            verify_reference_query_contexts(
                transcript,
                policy,
                tuple(item.context for item in retrievals),
            ) if retrievals else build_reference_query_contexts(transcript, policy)
        except (ReferenceContextError, ValueError) as exc:
            raise GenerationIsolationError(
                f"{context} Reference query contexts are not reproducible"
            ) from exc
        if not enrolled_artifacts:
            if references or retrievals:
                raise GenerationIsolationError(
                    f"{context} Reference state exists without Enrollments"
                )
            return
        if self._reference_retriever is None or self._reference_retriever_identity is None:
            raise GenerationIsolationError(
                f"{context} lacks the exact Reference Retriever runtime"
            )
        allowed_source_ids = tuple(sorted(item.source_id for item in enrolled_artifacts))
        expected_requests = _reference_retrieval_requests(
            transcript=transcript,
            policy=policy,
            invocation_id=invocation_id,
            allowed_source_ids=allowed_source_ids,
        )
        if len(retrievals) != len(expected_requests):
            raise GenerationIsolationError(
                f"{context} does not have exactly one Reference receipt per current span"
            )
        if len({item.query_id for item in retrievals}) != len(retrievals):
            raise GenerationIsolationError(f"{context} duplicates a Reference query ID")

        reference_by_id = {item.id: item for item in references}
        if len(reference_by_id) != len(references):
            raise GenerationIsolationError(f"{context} duplicates Reference Evidence IDs")
        enrolled_by_source = {item.source_id: item for item in enrolled_artifacts}
        receipt_evidence: dict[str, ReferenceEvidence] = {}
        runtime_index = getattr(self._reference_retriever, "index", None)
        identity = self._reference_retriever_identity
        for request, receipt in zip(expected_requests, retrievals, strict=True):
            if (
                receipt.episode_id != request.episode_id
                or receipt.invocation_id != request.invocation_id
                or receipt.audio_span_id != request.audio_span_id
                or receipt.query != request.observed_text
                or receipt.context != request.context
                or receipt.policy != request.policy
                or receipt.candidate_terms != request.candidate_terms
                or receipt.allowed_source_ids != allowed_source_ids
                or receipt.max_results != request.max_results
                or receipt.retriever != identity.name
                or receipt.retriever_version != identity.version
            ):
                raise GenerationIsolationError(
                    f"{context} Reference receipt differs from its exact current request"
                )
            if runtime_index is not None and any(
                value != getattr(runtime_index, name, None)
                for name, value in {
                    "index_hash": receipt.index_hash,
                    "retriever_config_hash": receipt.retriever_config_hash,
                    "retriever_code_hash": receipt.retriever_code_hash,
                    "retriever_runtime_hash": receipt.retriever_runtime_hash,
                }.items()
            ):
                raise GenerationIsolationError(
                    f"{context} Reference receipt differs from the exact runtime index"
                )
            try:
                replayed = self._reference_retriever.replay(request, receipt)
            except (AdapterIntegrityError, AttributeError, TypeError, ValueError) as exc:
                raise GenerationIsolationError(
                    f"{context} Reference receipt does not replay offline"
                ) from exc
            if replayed != receipt:
                raise GenerationIsolationError(
                    f"{context} Reference replay changed its typed receipt"
                )
            for item in receipt.evidence:
                stored = reference_by_id.get(item.id)
                if stored is None or stored != item:
                    raise GenerationIsolationError(
                        f"{context} Reference receipt names unavailable Evidence"
                    )
                enrollment = enrolled_by_source.get(item.artifact.source_id)
                if enrollment is None or enrollment != item.artifact:
                    raise GenerationIsolationError(
                        f"{context} Reference Evidence crossed Enrollment identity"
                    )
                snapshot_name = (
                    f"reference_extractions/{enrollment.extracted_text.sha256}.json"
                )
                try:
                    snapshot = reference_extraction_snapshots[snapshot_name]
                except KeyError as exc:
                    raise GenerationIsolationError(
                        f"{context} lacks a Reference extraction snapshot"
                    ) from exc
                verify_reference_evidence_membership(
                    item,
                    snapshot,
                    enrolled_artifact=item.artifact,
                )
                prior = receipt_evidence.get(item.id)
                if prior is not None and prior != item:
                    raise GenerationIsolationError(
                        f"{context} Reference receipts conflict on one Evidence ID"
                    )
                receipt_evidence[item.id] = item
        if receipt_evidence != reference_by_id:
            raise GenerationIsolationError(
                f"{context} Reference receipts do not bind the exact Evidence union"
            )

    def _reretrieve_child_references(
        self,
        *,
        transcript: CanonicalTranscript,
        policy: ReferenceRetrievalPolicySnapshot,
        enrolled_artifacts: tuple[ReferenceArtifact, ...],
        parent_references: tuple[ReferenceEvidence, ...],
        parent_retrievals: tuple[ReferenceRetrievalReceipt, ...],
        reference_extraction_snapshots: Mapping[str, bytes],
    ) -> tuple[tuple[ReferenceEvidence, ...], tuple[ReferenceRetrievalReceipt, ...]]:
        """Execute fresh child requests from persisted policy and child truth."""

        if not enrolled_artifacts:
            if parent_references or parent_retrievals:
                raise GenerationIsolationError(
                    "child Reference state exists without persisted Enrollments"
                )
            return (), ()
        if self._reference_retriever is None or self._reference_retriever_identity is None:
            raise ModuleInvariantError(
                "child re-audit requires the persisted Reference Retriever Adapter"
            )
        if not parent_retrievals:
            raise GenerationIsolationError(
                "child cannot re-retrieve References without parent query policy receipts"
            )
        expected_allowed = tuple(sorted(item.source_id for item in enrolled_artifacts))
        invocation_id = "resolution-reference-" + hash_object(
            {
                "generation_id": transcript.generation_id,
                "retriever_identity": self._reference_retriever_identity,
                "policy": policy,
                "parent_query_ids": tuple(item.query_id for item in parent_retrievals),
            }
        )
        prepared = _reference_retrieval_requests(
            transcript=transcript,
            policy=policy,
            invocation_id=invocation_id,
            allowed_source_ids=expected_allowed,
        )

        retrieve_many = getattr(self._reference_retriever, "retrieve_many", None)
        if callable(retrieve_many):
            retrieved = tuple(retrieve_many(prepared))
        else:
            retrieved = tuple(
                self._reference_retriever.retrieve(item) for item in prepared
            )
        if len(retrieved) != len(prepared):
            raise GenerationIsolationError(
                "Reference Retriever did not return one fresh receipt per child span"
            )

        enrolled_by_source = {item.source_id: item for item in enrolled_artifacts}
        evidence_by_id: dict[str, ReferenceEvidence] = {}
        receipts: list[ReferenceRetrievalReceipt] = []
        for receipt in retrieved:
            portable_evidence: list[ReferenceEvidence] = []
            for item in receipt.evidence:
                stored_artifact = enrolled_by_source.get(item.artifact.source_id)
                if stored_artifact is None or (
                    _portable_reference_artifact(item.artifact) != stored_artifact
                ):
                    raise GenerationIsolationError(
                        "fresh child Reference Evidence crossed its Enrollment"
                    )
                snapshot_name = (
                    f"reference_extractions/{stored_artifact.extracted_text.sha256}.json"
                )
                try:
                    snapshot = reference_extraction_snapshots[snapshot_name]
                except KeyError as exc:
                    raise GenerationIsolationError(
                        "fresh child Reference Evidence lacks its extraction snapshot"
                    ) from exc
                verify_reference_evidence_membership(
                    item,
                    snapshot,
                    enrolled_artifact=item.artifact,
                )
                portable = _portable_reference(item)
                existing = evidence_by_id.get(portable.id)
                if existing is not None and existing != portable:
                    raise GenerationIsolationError(
                        "fresh child Reference receipts conflict on one Evidence ID"
                    )
                evidence_by_id[portable.id] = portable
                portable_evidence.append(portable)
            receipts.append(
                receipt.model_copy(update={"evidence": tuple(portable_evidence)})
            )
        references = tuple(sorted(evidence_by_id.values(), key=lambda item: item.id))
        receipt_tuple = tuple(receipts)
        self._validate_reference_retrieval_state(
            transcript=transcript,
            policy=policy,
            invocation_id=invocation_id,
            enrolled_artifacts=enrolled_artifacts,
            references=references,
            retrievals=receipt_tuple,
            reference_extraction_snapshots=reference_extraction_snapshots,
            context="fresh child Reference retrieval",
        )
        if references != parent_references:
            raise GenerationIsolationError(
                "fresh child Reference retrieval changed the reconciled Evidence set; "
                "an explicit re-reconciliation contract is required"
            )
        return references, receipt_tuple

    def _retrieve_references(
        self,
        *,
        request: CreateRequest,
        invocation_id: str,
        preliminary: CanonicalBuildResult,
    ) -> tuple[tuple[ReferenceEvidence, ...], tuple[ReferenceRetrievalReceipt, ...]]:
        if not request.reference_artifacts:
            return (), ()
        if self._reference_retriever is None:
            raise ModuleInvariantError(
                "reference artifacts were supplied without a Reference Retriever Adapter"
            )
        receipts: list[ReferenceRetrievalReceipt] = []
        evidence_by_id: dict[str, ReferenceEvidence] = {}
        declared_artifacts = {
            artifact.source_id: artifact for artifact in request.reference_artifacts
        }
        enrollments = {
            enrollment.artifact.source_id: enrollment
            for enrollment in request.reference_enrollments
        }
        portable_artifacts = {
            source_id: _portable_reference_artifact(artifact)
            for source_id, artifact in declared_artifacts.items()
        }
        reference_policy = request.policy.reference_retrieval_snapshot(
            tuple(request.vocabulary)
        )
        prepared = _reference_retrieval_requests(
            transcript=preliminary.transcript,
            policy=reference_policy,
            invocation_id=invocation_id,
            allowed_source_ids=tuple(sorted(declared_artifacts)),
        )

        retrieve_many = getattr(self._reference_retriever, "retrieve_many", None)
        if callable(retrieve_many):
            retrieved = tuple(retrieve_many(prepared))
        else:
            retrieved = tuple(
                self._reference_retriever.retrieve(item) for item in prepared
            )
        if len(retrieved) != len(prepared):
            raise GenerationIsolationError(
                "Reference Retriever did not return exactly one receipt per canonical span"
            )

        for retrieval_request, receipt in zip(
            prepared,
            retrieved,
            strict=True,
        ):
            if (
                receipt.invocation_id != invocation_id
                or receipt.audio_span_id != retrieval_request.audio_span_id
            ):
                raise GenerationIsolationError(
                    "Reference Retrieval Receipt crossed invocation/span"
                )
            if (
                receipt.episode_id != request.episode_id
                or receipt.query != retrieval_request.observed_text
                or receipt.context != retrieval_request.context
                or receipt.policy != retrieval_request.policy
                or receipt.candidate_terms != retrieval_request.candidate_terms
                or receipt.allowed_source_ids
                != tuple(sorted(declared_artifacts))
                or receipt.max_results
                != reference_policy.max_results
                or len(receipt.evidence) > receipt.max_results
            ):
                raise GenerationIsolationError(
                    "Reference Retrieval Receipt does not exactly bind its request"
                )
            identity = self._reference_retriever_identity
            assert identity is not None
            if receipt.retriever != identity.name or receipt.retriever_version != identity.version:
                raise GenerationIsolationError(
                    "Reference Retrieval Receipt differs from configured Adapter identity"
                )
            runtime_index = getattr(self._reference_retriever, "index", None)
            if runtime_index is not None:
                measured_bindings = {
                    "index_hash": receipt.index_hash,
                    "retriever_config_hash": receipt.retriever_config_hash,
                    "retriever_code_hash": receipt.retriever_code_hash,
                    "retriever_runtime_hash": receipt.retriever_runtime_hash,
                }
                if any(
                    value != getattr(runtime_index, name, None)
                    for name, value in measured_bindings.items()
                ):
                    raise GenerationIsolationError(
                        "Reference Retrieval Receipt differs from the configured exact "
                        "index/retriever runtime"
                    )
            portable_evidence: list[ReferenceEvidence] = []
            for item in receipt.evidence:
                declared = declared_artifacts.get(item.artifact.source_id)
                if declared is None or declared != item.artifact:
                    raise GenerationIsolationError(
                        "Reference Evidence artifact differs from the explicitly "
                        f"declared snapshot: {item.artifact.source_id!r}"
                    )
                enrollment = enrollments.get(item.artifact.source_id)
                if enrollment is None:
                    raise GenerationIsolationError(
                        "Reference Evidence lacks a caller-owned Enrollment trust root"
                    )
                verify_reference_evidence_membership(
                    item,
                    enrollment.extraction_snapshot,
                    enrolled_artifact=enrollment.artifact,
                )
                portable = _portable_reference(item)
                if portable.artifact != portable_artifacts[item.artifact.source_id]:
                    raise GenerationIsolationError(
                        "portable Reference Evidence differs from declared source identity"
                    )
                existing = evidence_by_id.get(portable.id)
                if existing is not None and existing != portable:
                    raise GenerationIsolationError(
                        f"Reference Evidence ID {item.id!r} has conflicting content"
                    )
                evidence_by_id[portable.id] = portable
                portable_evidence.append(portable)
            receipts.append(receipt.model_copy(update={"evidence": tuple(portable_evidence)}))
        references = tuple(sorted(evidence_by_id.values(), key=lambda item: item.id))
        receipt_tuple = tuple(receipts)
        portable_enrollments = tuple(
            portable_artifacts[item.source_id] for item in request.reference_artifacts
        )
        extraction_snapshots = {
            f"reference_extractions/{item.artifact.extracted_text.sha256}.json": (
                item.extraction_snapshot
            )
            for item in request.reference_enrollments
        }
        self._validate_reference_retrieval_state(
            transcript=preliminary.transcript,
            policy=reference_policy,
            invocation_id=invocation_id,
            enrolled_artifacts=portable_enrollments,
            references=references,
            retrievals=receipt_tuple,
            reference_extraction_snapshots=extraction_snapshots,
            context="initial Reference retrieval",
        )
        return references, receipt_tuple

    def _commit_generation(
        self,
        *,
        result: CanonicalBuildResult,
        receipt: NormalizationReceipt,
        recognition_request: RecognitionRequestArtifactV1,
        recognition_independence: RecognitionIndependenceReceiptV1 | None,
        evidence: tuple[RecognitionEvidence, ...],
        orthographic_projections: tuple[OrthographicProjectionEvidenceV1, ...],
        orthographic_raw_outputs: dict[str, bytes],
        speaker_base_evidence: tuple[RecognitionEvidence, ...],
        speaker_provenance: SpeakerAttributionProvenance | None,
        speaker_attributor_identity: AdapterIdentity | None,
        enrolled_artifacts: tuple[ReferenceArtifact, ...],
        reference_retrieval_policy: ReferenceRetrievalPolicySnapshot,
        references: tuple[ReferenceEvidence, ...],
        retrievals: tuple[ReferenceRetrievalReceipt, ...],
        proposals: tuple[CorrectionProposal, ...],
        correction_audits: tuple[CorrectionAuditReceipt, ...],
        targeted_correction_audits: tuple[_TargetedCorrectionAuditReceipt, ...],
        audio_audits: tuple[AudioAuditReceipt, ...],
        arbitration_receipts: tuple[ArbitrationReceipt, ...],
        correction_execution_artifacts: dict[str, bytes],
        audio_execution_artifacts: dict[str, bytes],
        speech_coverage_receipt: SpeechCoverageReceipt | None,
        speech_coverage_raw_artifacts: dict[str, bytes],
        raw_outputs: dict[str, bytes],
        reference_source_snapshots: dict[str, bytes],
        reference_extraction_snapshots: dict[str, bytes],
        parent_manifest_hash: str | None,
        activate: bool = True,
    ) -> GenerationOutcome:
        transcript = result.transcript
        _validate_canonical_acceptance_identity(transcript)
        expected_outcome = (
            "accepted" if transcript.status == "accepted" else "needs_review"
        )
        if result.outcome != expected_outcome:
            raise GenerationIsolationError(
                "Canonical Build outcome differs from persisted acceptance status"
            )
        _validate_canonical_timing_provenance(
            transcript,
            evidence,
            orthographic_projections,
        )
        if transcript.normalized_audio_hash != receipt.normalized.sha256:
            raise GenerationIsolationError("Canonical Transcript and receipt audio differ")
        evidence_invocation_ids = {item.invocation_id for item in evidence}
        if (
            recognition_request.episode_id != transcript.episode_id
            or recognition_request.normalized_audio_sha256 != receipt.normalized.sha256
            or recognition_request.normalized_audio_size_bytes != receipt.normalized.size_bytes
            or evidence_invocation_ids != {recognition_request.invocation_id}
        ):
            raise GenerationIsolationError(
                "persisted Recognition request differs from Canonical/Evidence lineage"
            )
        if recognition_independence != self._recognition_independence_receipt():
            raise GenerationIsolationError(
                "Recognition independence proof differs from the executable composition"
            )
        if transcript.normalization_receipt_hash != normalization_receipt_content_hash(receipt):
            raise GenerationIsolationError("Canonical Transcript and receipt identity differ")
        expected_speech_hash = verified_speech_coverage_hash(speech_coverage_receipt)
        if transcript.verified_speech_coverage_receipt_hash != expected_speech_hash:
            raise GenerationIsolationError(
                "Canonical Transcript and Speech Coverage attestation differ"
            )
        if (speech_coverage_receipt is None) != (not speech_coverage_raw_artifacts):
            raise GenerationIsolationError(
                "Speech Coverage Receipt and raw artifact must be persisted together"
            )
        if speech_coverage_receipt is not None:
            expected_identity = self._speech_coverage_analyzer_identity
            actual_identity = {
                "adapter_name": speech_coverage_receipt.analyzer,
                "adapter_version": speech_coverage_receipt.analyzer_version,
                "config_hash": speech_coverage_receipt.analyzer_config_hash,
                "code_hash": speech_coverage_receipt.analyzer_code_hash,
                "runtime_hash": speech_coverage_receipt.analyzer_runtime_hash,
            }
            if expected_identity is None or actual_identity != expected_identity:
                raise GenerationIsolationError(
                    "Speech Coverage Receipt differs from runtime Analyzer identity"
                )
        reference_invocations = {item.invocation_id for item in retrievals}
        if len(reference_invocations) > 1:
            raise GenerationIsolationError(
                "Generation Reference receipts cross retrieval invocations"
            )
        self._validate_reference_retrieval_state(
            transcript=_derive_correction_preaudit_transcript(
                transcript,
                proposals=proposals,
                remove_proposal_ids=tuple(proposal.id for proposal in proposals),
                audio_audits=audio_audits,
            ),
            policy=reference_retrieval_policy,
            invocation_id=(
                next(iter(reference_invocations))
                if reference_invocations
                else "no-reference-retrieval"
            ),
            enrolled_artifacts=enrolled_artifacts,
            references=references,
            retrievals=retrievals,
            reference_extraction_snapshots=reference_extraction_snapshots,
            context="Generation commit",
        )
        _validate_review_risk_evidence_membership(
            transcript=transcript,
            risks=result.risks,
            evidence=evidence,
            references=references,
            speech_coverage_receipt=speech_coverage_receipt,
            context="Generation commit",
        )
        _validate_proposal_ownership(
            proposals,
            correction_audits,
            audio_audits,
            arbitration_receipts,
            require_arbitration=self._arbiter is not None,
        )
        text_preaudit_transcript = _derive_correction_preaudit_transcript(
            transcript,
            proposals=proposals,
            remove_proposal_ids=tuple(proposal.id for proposal in proposals),
            audio_audits=audio_audits,
        )
        _validate_text_full_audit_attestation(
            text_preaudit_transcript,
            correction_audits,
            self._corrector_execution_identity,
            references,
            retrievals,
        )
        self._verify_correction_audit_replays(
            transcript=transcript,
            evidence=evidence,
            references=references,
            proposals=proposals,
            correction_audits=correction_audits,
            targeted_correction_audits=targeted_correction_audits,
            audio_audits=audio_audits,
            artifacts=correction_execution_artifacts,
            context="Generation commit",
        )
        self._verify_audio_audit_replays(
            transcript=transcript,
            normalization_receipt=receipt,
            evidence=evidence,
            references=references,
            retrievals=retrievals,
            proposals=proposals,
            receipts=audio_audits,
            artifacts=audio_execution_artifacts,
            context="Generation commit",
        )
        self._verify_arbitration_replays(
            transcript=transcript,
            normalization_receipt=receipt,
            references=references,
            retrievals=retrievals,
            proposals=proposals,
            receipts=arbitration_receipts,
            artifacts=audio_execution_artifacts,
            context="Generation commit",
        )
        audio_artifacts = _audio_artifacts_payload(receipt)
        self.store.audio_path(
            receipt.source.sha256, size_bytes=receipt.source.size_bytes
        )
        self.store.audio_path(
            receipt.normalized.sha256, size_bytes=receipt.normalized.size_bytes
        )
        stage_hashes = {
            "adapter_identities": hash_object(
                {
                    "recognizers": self._runtime_recognizer_identities(),
                    "corrector": self._corrector_identity,
                    "corrector_execution": self._corrector_execution_identity,
                    "audio_auditor": self._audio_auditor.identity,
                    "speech_coverage_analyzer": (
                        self._speech_coverage_analyzer_identity
                    ),
                    "arbiter": self._arbiter.identity if self._arbiter is not None else None,
                    "reference_retriever": self._reference_retriever_identity,
                    "speaker_attributor": speaker_attributor_identity,
                }
            ),
            "normalization_receipt": hash_object(receipt),
            "recognition_request": recognition_request.content_hash,
            "recognition_independence": (
                recognition_independence.content_hash
                if recognition_independence is not None
                else hash_object(None)
            ),
            "audio_artifacts": hash_object(audio_artifacts),
            "recognition_evidence": hash_object(evidence),
            "speech_coverage_receipt": hash_object(speech_coverage_receipt),
            "speech_coverage_raw_artifacts": hash_object(
                tuple(
                    (name, sha256_bytes(payload))
                    for name, payload in sorted(
                        speech_coverage_raw_artifacts.items()
                    )
                )
            ),
            "speaker_base_recognition_evidence": hash_object(
                speaker_base_evidence
            ),
            "speaker_attribution_provenance": hash_object(speaker_provenance),
            "reference_enrollments": hash_object(enrolled_artifacts),
            "reference_retrieval_policy": hash_object(reference_retrieval_policy),
            "reference_evidence": reference_evidence_set_hash(references),
            "reference_source_snapshots": hash_object(
                tuple(
                    (name, sha256_bytes(payload))
                    for name, payload in sorted(reference_source_snapshots.items())
                )
            ),
            "reference_extraction_snapshots": hash_object(
                tuple(
                    (name, sha256_bytes(payload))
                    for name, payload in sorted(reference_extraction_snapshots.items())
                )
            ),
            "reference_retrieval_receipts": hash_object(retrievals),
            "correction_audit_receipts": hash_object(correction_audits),
            "targeted_correction_audit_receipts": hash_object(
                targeted_correction_audits
            ),
            "correction_execution_artifacts": hash_object(
                tuple(
                    (name, sha256_bytes(payload))
                    for name, payload in sorted(correction_execution_artifacts.items())
                )
            ),
            "audio_audit_receipts": hash_object(audio_audits),
            "arbitration_receipts": hash_object(arbitration_receipts),
            "audio_execution_artifacts": hash_object(
                tuple(
                    (name, sha256_bytes(payload))
                    for name, payload in sorted(audio_execution_artifacts.items())
                )
            ),
            "correction_proposals": hash_object(proposals),
            "canonical_transcript": hash_object(transcript),
            "risks": hash_object(tuple(_risk_payload(risk) for risk in result.risks)),
            "raw_recognition_outputs": hash_object(
                tuple(
                    (name, sha256_bytes(payload))
                    for name, payload in sorted(raw_outputs.items())
                )
            ),
        }
        if transcript.orthographic_projection_evidence_hash is not None:
            stage_hashes["orthographic_projection_evidence"] = hash_object(
                orthographic_projections
            )
        input_artifact_hashes = {
            "source_audio": transcript.source_audio_hash,
            "normalized_audio": transcript.normalized_audio_hash,
            "normalization_receipt": transcript.normalization_receipt_hash,
            "recognition_request": recognition_request.content_hash,
            "recognition_independence": (
                recognition_independence.content_hash
                if recognition_independence is not None
                else hash_object(None)
            ),
            "recognition_evidence": transcript.evidence_hash,
            "speech_coverage_receipt": (
                speech_coverage_receipt_content_hash(speech_coverage_receipt)
                if speech_coverage_receipt is not None
                else hash_object(None)
            ),
            "speaker_base_recognition_evidence": hash_object(
                speaker_base_evidence
            ),
            "speaker_attribution_provenance": hash_object(speaker_provenance),
            "ledger_head": transcript.ledger_hash,
            "reference_enrollments": hash_object(enrolled_artifacts),
            "reference_retrieval_policy": hash_object(reference_retrieval_policy),
            "references": transcript.reference_evidence_hash,
        }
        if transcript.orthographic_projection_evidence_hash is not None:
            input_artifact_hashes["orthographic_projection_evidence"] = (
                transcript.orthographic_projection_evidence_hash
            )
        manifest = GenerationManifest(
            id=f"manifest-{transcript.generation_id.removeprefix('generation-')}",
            episode_id=transcript.episode_id,
            generation_id=transcript.generation_id,
            parent_manifest_hash=parent_manifest_hash,
            input_artifact_hashes=input_artifact_hashes,
            code_hash=self._code_hash,
            policy_hash=transcript.policy_hash,
            adapter_hash=hash_object(
                {
                    "recognizers": self._runtime_recognizer_identities(),
                    "speaker_base_recognizers": tuple(
                        (item.adapter, item.model, item.config_hash)
                        for item in speaker_base_evidence
                    ),
                    "corrector": self._corrector_identity,
                    "corrector_execution": self._corrector_execution_identity,
                    "audio_auditor": self._audio_auditor.identity,
                    "speech_coverage_analyzer": (
                        self._speech_coverage_analyzer_identity
                    ),
                    "arbiter": self._arbiter.identity if self._arbiter is not None else None,
                    "reference_retriever": self._reference_retriever_identity,
                    "speaker_attributor": speaker_attributor_identity,
                    "speaker_provenance": speaker_provenance,
                }
            ),
            stage_artifact_hashes=stage_hashes,
            status="complete",
        )
        artifacts: dict[str, object] = {
            _RECEIPT: receipt,
            _AUDIO_ARTIFACTS: audio_artifacts,
            _RECOGNITION_REQUEST: recognition_request,
            _RECOGNITION_INDEPENDENCE: recognition_independence,
            _EVIDENCE: evidence,
            _SPEECH_COVERAGE: speech_coverage_receipt,
            _BASE_PRIMARY_EVIDENCE: speaker_base_evidence,
            _SPEAKER_PROVENANCE: speaker_provenance,
            _REFERENCE_ENROLLMENTS: enrolled_artifacts,
            _REFERENCE_RETRIEVAL_POLICY: reference_retrieval_policy,
            _REFERENCES: references,
            _RETRIEVALS: retrievals,
            _PROPOSALS: proposals,
            _CORRECTION_AUDITS: correction_audits,
            _TARGETED_CORRECTION_AUDITS: targeted_correction_audits,
            _AUDIO_AUDITS: audio_audits,
            _ARBITRATIONS: arbitration_receipts,
            _ADAPTER_IDENTITIES: {
                "recognizers": self._runtime_recognizer_identities(),
                "corrector": self._corrector_identity,
                "corrector_execution": self._corrector_execution_identity,
                "audio_auditor": self._audio_auditor.identity,
                "speech_coverage_analyzer": (
                    self._speech_coverage_analyzer_identity
                ),
                "arbiter": self._arbiter.identity if self._arbiter is not None else None,
                "reference_retriever": self._reference_retriever_identity,
                "speaker_attributor": speaker_attributor_identity,
            },
            _CANONICAL: transcript,
            _RISKS: tuple(_risk_payload(risk) for risk in result.risks),
            _MANIFEST: manifest,
            **raw_outputs,
            **correction_execution_artifacts,
            **audio_execution_artifacts,
            **speech_coverage_raw_artifacts,
            **reference_source_snapshots,
            **reference_extraction_snapshots,
        }
        if orthographic_projections:
            artifacts[_ORTHOGRAPHIC_PROJECTIONS] = orthographic_projections
            artifacts.update(orthographic_raw_outputs)
        stored = self.store.commit(
            logical_generation_id=transcript.generation_id,
            manifest=manifest,
            artifacts=artifacts,
        )
        if stored.generation_id != transcript.generation_id:
            raise GenerationIsolationError("store changed the canonical Generation ID")
        if activate:
            self.store.set_active(stored.generation_id)
        if result.outcome == "accepted":
            return AcceptedGeneration(stored.generation_id, transcript, manifest)
        issues = tuple(
            issue for issue in transcript.review_issues if issue.status == "unresolved"
        )
        return NeedsReview(stored.generation_id, transcript, issues, manifest)

    def _load_native_generation_model(
        self,
        generation_id: str,
        name: str,
        model: type,
        *,
        read_artifact: Callable[[str], bytes] | None = None,
    ) -> object:
        payload = (
            self.store.read_artifact(generation_id, name)
            if read_artifact is None
            else read_artifact(name)
        )
        try:
            value = model.model_validate_json(payload, strict=True)
        except (TypeError, ValueError) as exc:
            raise GenerationIsolationError(
                f"native Generation artifact {name!r} is invalid"
            ) from exc
        if canonical_json_bytes(value) != payload:
            raise GenerationIsolationError(
                f"native Generation artifact {name!r} is not canonical"
            )
        return value

    def _load_native_generation_source_set(
        self,
        generation_id: str,
        index_name: str,
        *,
        read_artifact: Callable[[str], bytes] | None = None,
    ) -> tuple[dict[str, bytes], set[str]]:
        load = (
            (lambda name: self.store.read_artifact(generation_id, name))
            if read_artifact is None
            else read_artifact
        )
        index_bytes = load(index_name)
        index = _canonical_json_value(index_bytes, label=index_name)
        if not isinstance(index, dict) or set(index) != {"schema_version", "entries"}:
            raise GenerationIsolationError("native Generation source index schema is invalid")
        if index["schema_version"] != 1 or not isinstance(index["entries"], list):
            raise GenerationIsolationError("native Generation source index version is invalid")
        sources: dict[str, bytes] = {}
        names = {index_name}
        for entry in index["entries"]:
            if not isinstance(entry, dict) or set(entry) != {
                "uri",
                "artifact_name",
                "sha256",
                "size_bytes",
            }:
                raise GenerationIsolationError("native Generation source index entry is invalid")
            uri = entry["uri"]
            name = entry["artifact_name"]
            if (
                not isinstance(uri, str)
                or not isinstance(name, str)
                or uri in sources
                or name in names
            ):
                raise GenerationIsolationError("native Generation source identity is invalid")
            payload = load(name)
            if entry["sha256"] != sha256_bytes(payload) or entry["size_bytes"] != len(
                payload
            ):
                raise GenerationIsolationError("native Generation source digest mismatch")
            sources[uri] = payload
            names.add(name)
        namespace = {
            _NATIVE_TEXT_SOURCE_INDEX: "text",
            _NATIVE_AUDIO_SOURCE_INDEX: "audio",
        }.get(index_name)
        if namespace is None:
            raise GenerationIsolationError("native Generation source index role is unknown")
        expected_index, expected_artifacts = _address_native_sources(
            sources, namespace=namespace
        )
        if (
            index_bytes != expected_index
            or names != {index_name, *expected_artifacts}
            or any(
                load(name) != payload
                for name, payload in expected_artifacts.items()
            )
        ):
            raise GenerationIsolationError(
                "native Generation source index is not canonical"
            )
        return sources, names

    def _load_native_generation_run_bytes(
        self,
        generation_id: str,
        *,
        index_name: str,
        categories: tuple[str, ...],
        read_artifact: Callable[[str], bytes] | None = None,
    ) -> tuple[dict[str, tuple[bytes, ...]], set[str]]:
        load = (
            (lambda name: self.store.read_artifact(generation_id, name))
            if read_artifact is None
            else read_artifact
        )
        index_bytes = load(index_name)
        index = _canonical_json_value(index_bytes, label=index_name)
        if not isinstance(index, dict) or set(index) != {"schema_version", *categories}:
            raise GenerationIsolationError("native Generation run index schema is invalid")
        if index["schema_version"] != 1:
            raise GenerationIsolationError("native Generation run index version is invalid")
        result: dict[str, tuple[bytes, ...]] = {}
        names = {index_name}
        for category in categories:
            category_names = index[category]
            if not isinstance(category_names, list) or any(
                not isinstance(item, str) for item in category_names
            ):
                raise GenerationIsolationError("native Generation run index paths are invalid")
            payloads: list[bytes] = []
            for name in category_names:
                if name in names:
                    raise GenerationIsolationError(
                        "native Generation run index repeats an artifact"
                    )
                payloads.append(load(name))
                names.add(name)
            result[category] = tuple(payloads)
        prefix = {
            _NATIVE_TEXT_RUN_INDEX: "text",
            _NATIVE_AUDIO_RUN_INDEX: "audio",
        }.get(index_name)
        if prefix is None:
            raise GenerationIsolationError("native Generation run index role is unknown")
        expected_index, expected_artifacts = _address_native_run_bytes(
            prefix=prefix,
            categories={category: result[category] for category in categories},
        )
        if (
            index_bytes != expected_index
            or names != {index_name, *expected_artifacts}
            or any(
                load(name) != payload
                for name, payload in expected_artifacts.items()
            )
        ):
            raise GenerationIsolationError("native Generation run index is not canonical")
        return result, names

    def _verify_native_resolution_child(
        self,
        *,
        generation_id: str,
        manifest: GenerationManifest,
        transcript: CanonicalTranscript,
        risks: tuple[RiskRecord, ...],
        references: tuple[ReferenceEvidence, ...],
        retrievals: tuple[ReferenceRetrievalReceipt, ...],
        text_record: TextAuditExecutionRecordV2,
        audio_record: AudioAuditExecutionRecordV2,
        aggregate: FullAuditAggregateAttestationV2,
        read_artifact: Callable[[str], bytes],
        verified_artifact: Callable[[str], VerifiedArtifactBytes],
    ) -> tuple[set[str], LoadedNativeResolutionState]:
        """Replay a native child's exact authorization and parent transition."""

        if manifest.parent_manifest_hash is None:
            raise GenerationIsolationError("native resolution child lacks a parent manifest")
        decision_bytes = read_artifact(_NATIVE_RESOLUTION_DECISION)
        # The Decision chooses the one closed authorization branch before any
        # branch-specific artifact path is followed.
        try:
            parsed_decision = NativeCorrectionDecisionV2.model_validate_json(
                decision_bytes, strict=True
            )
        except ValueError as exc:
            raise GenerationIsolationError("native resolution Decision is invalid") from exc
        if canonical_json_bytes(parsed_decision) != decision_bytes:
            raise GenerationIsolationError("native resolution Decision is not canonical")
        candidate_bytes = read_artifact(_NATIVE_RESOLUTION_CANDIDATE)
        authorization_bytes = read_artifact(_NATIVE_RESOLUTION_AUTHORIZATION)
        if parsed_decision.authorization_kind == "correction_acceptance":
            authorization_model = CorrectionAcceptanceVerdictV2
            policy_name = _NATIVE_RESOLUTION_ACCEPTANCE_POLICY
            receipt_index_name = _NATIVE_RESOLUTION_HUMAN_AUDIO_INDEX
            receipt_namespace = "human_audio"
        else:
            authorization_model = OriginalConfirmationAuthorizationV2
            policy_name = _NATIVE_RESOLUTION_ORIGINAL_POLICY
            receipt_index_name = _NATIVE_RESOLUTION_HUMAN_ORIGINAL_INDEX
            receipt_namespace = "human_original_confirmation"
        policy_bytes = read_artifact(policy_name)
        try:
            declared = authorization_model.model_validate_json(
                authorization_bytes, strict=True
            )
            candidate_model = (
                TextDiscoveredCandidateV2
                if declared.candidate_modality == "text"
                else AudioDiscoveredCandidateV2
            )
            candidate = candidate_model.model_validate_json(candidate_bytes, strict=True)
        except ValueError as exc:
            raise GenerationIsolationError(
                "native resolution authorization or candidate is invalid"
            ) from exc
        if (
            canonical_json_bytes(declared) != authorization_bytes
            or canonical_json_bytes(candidate) != candidate_bytes
        ):
            raise GenerationIsolationError(
                "native resolution authorization parents are not canonical"
            )
        if parsed_decision.authorization_id != declared.id:
            raise GenerationIsolationError(
                "native resolution Decision crossed authorization identity"
            )
        parent = self._load_generation(
            parsed_decision.generation_id,
            require_active=False,
        )
        parent_result = parent.result
        if (
            hash_object(parent.storage.manifest.materialize())
            != manifest.parent_manifest_hash
        ):
            raise GenerationIsolationError(
                "native resolution child names a different exact parent manifest"
            )
        if not isinstance(parent.audit, LoadedNativeAuditState):
            raise GenerationIsolationError(
                "native resolution Decision does not target a native audit Generation"
            )
        parent_aggregate = parent.audit.aggregate
        parent_text = parent.audit.text_run.record
        parent_audio = parent.audit.audio_run.record
        parent_candidates = (
            *parent_text.candidate_discovery_set.candidates,
            *parent_audio.candidate_discovery_set.candidates,
        )
        matching = tuple(item for item in parent_candidates if item.id == candidate.id)
        if len(matching) != 1 or matching[0] != candidate:
            raise GenerationIsolationError(
                "native resolution candidate differs from the stored parent discovery"
            )
        index_bytes = read_artifact(receipt_index_name)
        index = _canonical_json_value(
            index_bytes, label=receipt_index_name
        )
        if not isinstance(index, dict) or set(index) != {"schema_version", "receipts"}:
            raise GenerationIsolationError(
                "native resolution human-review index is invalid"
            )
        receipt_names = index["receipts"]
        if index["schema_version"] != 2 or not isinstance(receipt_names, list):
            raise GenerationIsolationError(
                "native resolution human-review index version is invalid"
            )
        receipt_bytes: list[bytes] = []
        for ordinal, name in enumerate(receipt_names):
            if not isinstance(name, str):
                raise GenerationIsolationError(
                    "native resolution human-review artifact name is invalid"
                )
            payload = read_artifact(name)
            expected_name = (
                f"native_resolution/{receipt_namespace}/{ordinal:04d}-"
                f"{sha256_bytes(payload)}.json"
            )
            if name != expected_name:
                raise GenerationIsolationError(
                    "native resolution human-review artifact is not addressed"
                )
            receipt_bytes.append(payload)
        resolution_names = {
            _NATIVE_RESOLUTION_DECISION,
            _NATIVE_RESOLUTION_CANDIDATE,
            _NATIVE_RESOLUTION_AUTHORIZATION,
            policy_name,
            receipt_index_name,
            _NATIVE_RESOLUTION_LEDGER_ENTRY,
            *receipt_names,
        }
        proof = None
        if declared.reference_proof_id is not None:
            proof_bytes = self.store.read_artifact(
                generation_id, _NATIVE_RESOLUTION_REFERENCE_PROOF
            )
            if self._reference_retriever is None:
                raise GenerationIsolationError(
                    "native resolution Reference proof lacks configured Retriever"
                )
            proof = verify_native_reference_authority_proof(
                proof_bytes,
                retriever=self._reference_retriever,  # type: ignore[arg-type]
                resolution_key=declared.resolution_key,
                reference_scope=declared.reference_scope,
                stored_reference_evidence=parent.references.evidence,
            )
            resolution_names.add(_NATIVE_RESOLUTION_REFERENCE_PROOF)
        if isinstance(declared, CorrectionAcceptanceVerdictV2):
            policy = verify_correction_acceptance_policy(policy_bytes)
            human_receipts = tuple(
                verify_human_audio_review_receipt(payload) for payload in receipt_bytes
            )
            adjudication = None
            if declared.human_reference_adjudication_receipt_id is not None:
                adjudication = verify_human_reference_adjudication_receipt(
                    read_artifact(_NATIVE_RESOLUTION_REFERENCE_ADJUDICATION)
                )
                resolution_names.add(_NATIVE_RESOLUTION_REFERENCE_ADJUDICATION)
            authorization = verify_correction_acceptance_verdict(
                authorization_bytes,
                candidate=candidate,
                full_audit_aggregate=parent_aggregate,
                recognition_evidence=parent.recognition.evidence,
                resolution_key=declared.resolution_key,
                reference_scope=declared.reference_scope,
                reference_proof=proof,
                human_audio_receipts=human_receipts,
                human_reference_adjudication=adjudication,
                policy=policy,
            )
        else:
            policy = verify_original_confirmation_policy(policy_bytes)
            human_original_receipts = tuple(
                verify_human_original_confirmation_receipt(payload)
                for payload in receipt_bytes
            )
            authorization = verify_original_confirmation_authorization(
                authorization_bytes,
                candidate=candidate,
                full_audit_aggregate=parent_aggregate,
                recognition_evidence=parent.recognition.evidence,
                resolution_key=declared.resolution_key,
                reference_scope=declared.reference_scope,
                reference_proof=proof,
                human_original_receipts=human_original_receipts,
                policy=policy,
            )
        span_by_id = {span.id: span for span in parent_result.transcript.spans}
        try:
            cited_spans = tuple(
                span_by_id[span_id] for span_id in candidate.cited_span_ids
            )
        except KeyError as exc:
            raise GenerationIsolationError(
                "native resolution candidate cites a missing parent span"
            ) from exc
        fingerprint = review_target_fingerprint(
            parent_result.transcript, candidate.cited_span_ids
        )
        decision = verify_native_correction_decision(
            decision_bytes,
            episode_id=parent_result.transcript.episode_id,
            parent_generation_id=parent_result.transcript.generation_id,
            reviewed_fingerprint=fingerprint,
            target_start_ms=cited_spans[0].start_ms,
            target_end_ms=cited_spans[-1].end_ms,
            candidate=candidate,
            authorization=authorization,
        )
        ledger_payload = _canonical_json_value(
            read_artifact(_NATIVE_RESOLUTION_LEDGER_ENTRY),
            label=_NATIVE_RESOLUTION_LEDGER_ENTRY,
        )
        entry = _ledger_entry_from_payload(ledger_payload)
        try:
            decoded_entry = decode_ledger_entry(entry)
        except LedgerDecisionCodecError as exc:
            raise GenerationIsolationError(
                "native resolution Ledger decision is invalid"
            ) from exc
        if (
            decoded_entry.decision != decision
            or entry.previous_hash != parent_result.transcript.ledger_hash
            or entry.entry_hash != transcript.ledger_hash
        ):
            raise GenerationIsolationError(
                "native resolution Ledger entry differs from parent/child lineage"
            )
        derived = derive_native_canonical_resolution(
            parent_result,
            decision,
            candidate,
            ledger_hash=entry.entry_hash,
        )
        if references != parent.references.evidence:
            raise GenerationIsolationError(
                "native resolution child changed reconciled Reference Evidence"
            )
        retrieval_risks = _reference_retrieval_failure_risks(retrievals)
        if retrieval_risks:
            derived = add_review_risks(derived, retrieval_risks)
        completed = _native_resolved_after_fresh_audit(
            derived,
            text_record=text_record,
            audio_record=audio_record,
        )
        if completed.transcript != transcript or completed.risks != risks:
            raise GenerationIsolationError(
                "native resolution child does not replay from exact authorization"
            )
        if aggregate.generation_id != derived.transcript.generation_id:
            raise GenerationIsolationError(
                "native resolution child aggregate crossed its audit basis Generation"
            )
        human_audio_receipts = (
            human_receipts
            if isinstance(declared, CorrectionAcceptanceVerdictV2)
            else ()
        )
        human_original_confirmation_receipts = (
            ()
            if isinstance(declared, CorrectionAcceptanceVerdictV2)
            else human_original_receipts
        )
        resolution = LoadedNativeResolutionState(
            authorization_kind=parsed_decision.authorization_kind,
            candidate=candidate,
            authorization=authorization,
            policy=policy,
            decision=decision,
            ledger_entry=LoadedLedgerEntrySnapshot(
                sequence=entry.sequence,
                previous_hash=entry.previous_hash,
                decision_hash=entry.decision_hash,
                entry_hash=entry.entry_hash,
            ),
            human_audio_receipts=human_audio_receipts,
            human_original_confirmation_receipts=(
                human_original_confirmation_receipts
            ),
            reference_proof=proof,
            reference_adjudication=(
                adjudication
                if isinstance(declared, CorrectionAcceptanceVerdictV2)
                else None
            ),
            artifacts=LoadedNativeResolutionArtifacts(
                decision=verified_artifact(_NATIVE_RESOLUTION_DECISION),
                candidate=verified_artifact(_NATIVE_RESOLUTION_CANDIDATE),
                authorization=verified_artifact(_NATIVE_RESOLUTION_AUTHORIZATION),
                policy=verified_artifact(policy_name),
                human_receipt_index=verified_artifact(receipt_index_name),
                ledger_entry=verified_artifact(_NATIVE_RESOLUTION_LEDGER_ENTRY),
                human_receipts=VerifiedArtifactSet(
                    tuple(verified_artifact(name) for name in sorted(receipt_names))
                ),
                reference_proof=(
                    verified_artifact(_NATIVE_RESOLUTION_REFERENCE_PROOF)
                    if proof is not None
                    else None
                ),
                reference_adjudication=(
                    verified_artifact(_NATIVE_RESOLUTION_REFERENCE_ADJUDICATION)
                    if isinstance(declared, CorrectionAcceptanceVerdictV2)
                    and adjudication is not None
                    else None
                ),
            ),
        )
        return resolution_names, resolution

    def _load_native_generation(
        self,
        generation_id: str,
        *,
        require_active: bool,
        record: StoredGeneration,
        manifest: GenerationManifest,
        read_artifact: Callable[[str], bytes],
        verified_artifact: Callable[[str], VerifiedArtifactBytes],
    ) -> LoadedGenerationState:
        bundle = self._native_full_audit
        if bundle is None:
            raise GenerationIsolationError(
                "stored native Full Audit Generation requires native runtime composition"
            )

        def load_value(name: str) -> object:
            return _canonical_json_value(read_artifact(name), label=name)

        artifact_manifest = self._load_native_generation_model(
            generation_id,
            _MANIFEST,
            GenerationManifest,
            read_artifact=read_artifact,
        )
        if artifact_manifest != manifest or manifest.generation_id != generation_id:
            raise GenerationIsolationError("native Generation Manifest identity mismatch")

        adapter_payload = load_value(_ADAPTER_IDENTITIES)
        expected_identity_keys = {
            "recognizers",
            "speech_coverage_analyzer",
            "speaker_attributor",
            "reference_retriever",
            "native_text_full_audit",
            "native_text_policy",
            "native_audio_full_audit",
            "native_audio_policy",
            "native_audit_policy",
            "native_execution_policy",
        }
        if not isinstance(adapter_payload, dict) or set(adapter_payload) != expected_identity_keys:
            raise GenerationIsolationError("native Generation Adapter identity set is incomplete")
        recognizer_payload = adapter_payload["recognizers"]
        if not isinstance(recognizer_payload, list):
            raise GenerationIsolationError("native recognizer identity set has invalid shape")
        stored_recognizers = tuple(
            _recognition_identity_from_payload(item) for item in recognizer_payload
        )
        stored_speech = _speech_coverage_identity_from_payload(
            adapter_payload["speech_coverage_analyzer"]
        )
        stored_speaker = _adapter_identity_from_payload(adapter_payload["speaker_attributor"])
        stored_reference = _adapter_identity_from_payload(adapter_payload["reference_retriever"])
        try:
            stored_text_identity = type(bundle.text_executor.identity).model_validate_json(
                canonical_json_bytes(adapter_payload["native_text_full_audit"])
            )
            stored_text_policy = type(bundle.text_executor.policy).model_validate_json(
                canonical_json_bytes(adapter_payload["native_text_policy"])
            )
            stored_audio_identity = type(bundle.audio_executor.identity).model_validate_json(
                canonical_json_bytes(adapter_payload["native_audio_full_audit"])
            )
            stored_audio_policy = type(bundle.audio_executor.policy).model_validate_json(
                canonical_json_bytes(adapter_payload["native_audio_policy"])
            )
            stored_audit_policy = CorrectionAuditPolicySnapshot.model_validate_json(
                canonical_json_bytes(adapter_payload["native_audit_policy"])
            )
            stored_execution_policy = CorrectionAuditExecutionPolicyV2.model_validate_json(
                canonical_json_bytes(adapter_payload["native_execution_policy"])
            )
        except (TypeError, ValueError) as exc:
            raise GenerationIsolationError("native audit runtime identity is invalid") from exc
        if (
            stored_recognizers != self._runtime_recognizer_identities()
            or stored_speech != self._speech_coverage_analyzer_identity
            or stored_reference != self._reference_retriever_identity
            or (
                stored_speaker is not None
                and stored_speaker != self._speaker_attributor_identity
            )
            or stored_text_identity != bundle.text_executor.identity
            or stored_text_policy != bundle.text_executor.policy
            or stored_audio_identity != bundle.audio_executor.identity
            or stored_audio_policy != bundle.audio_executor.policy
            or stored_audit_policy != bundle.audit_policy
            or stored_execution_policy != bundle.execution_policy
        ):
            raise GenerationIsolationError(
                "runtime Adapter identities or native audit policies differ from the "
                "stored Generation"
            )
        expected_adapter_payload = {
            "recognizers": stored_recognizers,
            "speech_coverage_analyzer": stored_speech,
            "speaker_attributor": stored_speaker,
            "reference_retriever": stored_reference,
            "native_text_full_audit": stored_text_identity,
            "native_text_policy": stored_text_policy,
            "native_audio_full_audit": stored_audio_identity,
            "native_audio_policy": stored_audio_policy,
            "native_audit_policy": stored_audit_policy,
            "native_execution_policy": stored_execution_policy,
        }
        if adapter_payload != json.loads(canonical_json_bytes(expected_adapter_payload)):
            raise GenerationIsolationError("native Adapter identity bytes are not exact")

        transcript = self._load_native_generation_model(
            generation_id,
            _CANONICAL,
            CanonicalTranscript,
            read_artifact=read_artifact,
        )
        assert isinstance(transcript, CanonicalTranscript)
        checkpoint_transcript = self._load_native_generation_model(
            generation_id,
            _CHECKPOINT_CANONICAL,
            CanonicalTranscript,
            read_artifact=read_artifact,
        )
        if transcript != checkpoint_transcript or transcript.generation_id != generation_id:
            raise GenerationIsolationError("native Canonical checkpoint identity mismatch")
        _validate_acceptance_policy_not_rewritten(transcript)
        _validate_canonical_acceptance_identity(transcript)
        if transcript.status == "accepted" or transcript.full_audit_receipt_set_hash is not None:
            raise GenerationIsolationError(
                "native Full Audit Generation cannot claim legacy release acceptance"
            )
        risks_payload = load_value(_RISKS)
        checkpoint_risks_payload = load_value(_CHECKPOINT_RISKS)
        if not isinstance(risks_payload, list) or risks_payload != checkpoint_risks_payload:
            raise GenerationIsolationError("native risk checkpoint changed")
        risks = tuple(_risk_from_payload(item) for item in risks_payload)
        stored_result = CanonicalBuildResult(
            outcome="needs_review",
            transcript=transcript,
            candidates=(),
            risks=risks,
            reference_evidence_hash=transcript.reference_evidence_hash,
            generation_warnings=transcript.generation_warnings,
        )
        resolution_artifact_names = {
            name
            for name in record.artifact_hashes
            if name.startswith("native_resolution/")
        }
        is_resolution_child = bool(resolution_artifact_names)
        if is_resolution_child and _NATIVE_RESOLUTION_DECISION not in resolution_artifact_names:
            raise GenerationIsolationError(
                "native resolution artifact profile lacks its Decision discriminant"
            )
        audit_basis = (
            _native_resolved_audit_basis(stored_result)
            if is_resolution_child
            else stored_result
        )
        audit_transcript = audit_basis.transcript

        receipt = self._load_native_generation_model(
            generation_id,
            _RECEIPT,
            NormalizationReceipt,
            read_artifact=read_artifact,
        )
        assert isinstance(receipt, NormalizationReceipt)
        if (
            receipt.status != "accepted"
            or receipt.source.sha256 != transcript.source_audio_hash
            or receipt.normalized.sha256 != transcript.normalized_audio_hash
            or normalization_receipt_content_hash(receipt)
            != transcript.normalization_receipt_hash
        ):
            raise GenerationIsolationError("native Normalization Receipt lineage mismatch")
        audio_artifacts = load_value(_AUDIO_ARTIFACTS)
        if audio_artifacts != _audio_artifacts_payload(receipt):
            raise GenerationIsolationError("native audio artifact identities changed")
        source_audio = self.store.audio_path(
            receipt.source.sha256, size_bytes=receipt.source.size_bytes
        )
        normalized_audio = self.store.audio_path(
            receipt.normalized.sha256, size_bytes=receipt.normalized.size_bytes
        )
        recognition_request_artifact, recognition_request = (
            self._load_stored_recognition_request(
                generation_id,
                episode_id=transcript.episode_id,
                normalized_audio=normalized_audio,
                normalized_sha256=receipt.normalized.sha256,
                normalized_size_bytes=receipt.normalized.size_bytes,
                read_artifact=read_artifact,
            )
        )
        recognition_independence = self._load_stored_recognition_independence(
            generation_id,
            read_artifact=read_artifact,
        )

        evidence_payload = load_value(_EVIDENCE)
        base_payload = load_value(_BASE_PRIMARY_EVIDENCE)
        if not isinstance(evidence_payload, list) or not isinstance(base_payload, list):
            raise GenerationIsolationError("native Recognition Evidence collection is invalid")
        evidence = tuple(RecognitionEvidence.model_validate(item) for item in evidence_payload)
        speaker_base_evidence = tuple(
            RecognitionEvidence.model_validate(item) for item in base_payload
        )
        if not evidence or len(speaker_base_evidence) > 1:
            raise GenerationIsolationError("native Recognition Evidence set is incomplete")
        if transcript.evidence_hash != recognition_evidence_set_hash(evidence):
            raise GenerationIsolationError("native Recognition Evidence hash mismatch")
        if any(
            item.episode_id != transcript.episode_id
            or item.normalized_audio_hash != transcript.normalized_audio_hash
            for item in evidence
        ):
            raise GenerationIsolationError("native Recognition Evidence lineage mismatch")
        if transcript.orthographic_projection_evidence_hash is None:
            orthographic_projections: tuple[OrthographicProjectionEvidenceV1, ...] = ()
            orthographic_raw_outputs: dict[str, bytes] = {}
        else:
            orthographic_projections, orthographic_raw_outputs = (
                self._load_orthographic_projection_artifacts(
                    generation_id,
                    evidence=evidence,
                    expected_set_hash=transcript.orthographic_projection_evidence_hash,
                    read_artifact=read_artifact,
                )
            )
        _validate_canonical_timing_provenance(
            transcript,
            evidence,
            orthographic_projections,
        )

        provenance_payload = load_value(_SPEAKER_PROVENANCE)
        speaker_provenance = (
            None
            if provenance_payload is None
            else SpeakerAttributionProvenance.from_payload(provenance_payload)
        )
        if bool(speaker_base_evidence) != (speaker_provenance is not None):
            raise GenerationIsolationError("native Speaker Attribution proof is incomplete")
        raw_outputs: dict[str, bytes] = {}
        for item in (*evidence, *speaker_base_evidence):
            name = f"raw_evidence/{item.raw_output.sha256}.json"
            payload = read_artifact(name)
            if (
                item.raw_output.uri != f"generation-artifact://{name}"
                or sha256_bytes(payload) != item.raw_output.sha256
                or len(payload) != item.raw_output.size_bytes
            ):
                raise GenerationIsolationError("native Recognition raw output mismatch")
            existing = raw_outputs.get(name)
            if existing is not None and existing != payload:
                raise GenerationIsolationError("native Recognition raw digest conflicts")
            raw_outputs[name] = payload
        original_evidence = (
            (speaker_base_evidence[0], *evidence[1:])
            if speaker_base_evidence
            else evidence
        )
        invocation_ids = {item.invocation_id for item in original_evidence}
        if len(invocation_ids) != 1:
            raise GenerationIsolationError("native Recognition Evidence crosses invocations")
        self._verify_recognition_derivations(
            original_evidence,
            request=recognition_request,
            raw_outputs=raw_outputs,
            context="native stored Generation",
        )
        if recognition_request_artifact.invocation_id != next(iter(invocation_ids)):
            raise GenerationIsolationError(
                "native Recognition request invocation differs from Evidence"
            )
        if speaker_base_evidence:
            if self._speaker_attributor is None or stored_speaker is None:
                raise GenerationIsolationError("native Speaker Attribution runtime is absent")
            assert speaker_provenance is not None
            verified_provenance = self._speaker_attributor.verify(
                evidence[0],
                base_evidence=speaker_base_evidence[0],
                raw_output=raw_outputs[f"raw_evidence/{evidence[0].raw_output.sha256}.json"],
            )
            _assert_speaker_attribution_binding(
                attributed=evidence[0],
                base=speaker_base_evidence[0],
                provenance=verified_provenance,
                identity=stored_speaker,
            )
            if verified_provenance != speaker_provenance:
                raise GenerationIsolationError("native Speaker Attribution is not reproducible")

        speech_payload = load_value(_SPEECH_COVERAGE)
        speech_coverage_receipt = (
            None
            if speech_payload is None
            else SpeechCoverageReceipt.model_validate(speech_payload)
        )
        speech_coverage_raw_artifacts: dict[str, bytes] = {}
        if speech_coverage_receipt is None:
            if stored_speech is not None:
                raise GenerationIsolationError("native Speech Coverage identity lacks receipt")
        else:
            if self._speech_coverage_analyzer is None or receipt.normalized_duration_ms is None:
                raise GenerationIsolationError("native Speech Coverage runtime is absent")
            raw_name = speech_coverage_receipt.raw_output.uri.removeprefix(
                "generation-artifact://"
            )
            if raw_name != (
                f"speech_coverage/raw/{speech_coverage_receipt.raw_output.sha256}.json"
            ):
                raise GenerationIsolationError("native Speech Coverage URI is not canonical")
            raw_payload = read_artifact(raw_name)
            if (
                sha256_bytes(raw_payload) != speech_coverage_receipt.raw_output.sha256
                or len(raw_payload) != speech_coverage_receipt.raw_output.size_bytes
            ):
                raise GenerationIsolationError("native Speech Coverage raw digest mismatch")
            coverage_request = SpeechCoverageRequest(
                episode_id=transcript.episode_id,
                invocation_id=next(iter(invocation_ids)),
                normalized_audio=normalized_audio,
                expected_normalized_audio_hash=receipt.normalized.sha256,
                expected_normalized_audio_size_bytes=receipt.normalized.size_bytes,
                normalized_audio_duration_ms=receipt.normalized_duration_ms,
                recognition_evidence=evidence,
                raw_output_dir=self.store.root / "replay-only-speech-coverage",
            )
            try:
                verified_coverage = self._speech_coverage_analyzer.verify(
                    speech_coverage_receipt,
                    request=coverage_request,
                    raw_output=raw_payload,
                )
            except (AdapterIntegrityError, ValueError) as exc:
                raise GenerationIsolationError(
                    "native Speech Coverage receipt is not reproducible"
                ) from exc
            if verified_coverage != speech_coverage_receipt:
                raise GenerationIsolationError("native Speech Coverage verification drifted")
            speech_coverage_raw_artifacts[raw_name] = raw_payload
        if transcript.verified_speech_coverage_receipt_hash != verified_speech_coverage_hash(
            speech_coverage_receipt
        ):
            raise GenerationIsolationError("native Speech Coverage attestation mismatch")

        enrollment_payload = load_value(_REFERENCE_ENROLLMENTS)
        retrieval_policy_payload = load_value(_REFERENCE_RETRIEVAL_POLICY)
        reference_payload = load_value(_REFERENCES)
        retrieval_payload = load_value(_RETRIEVALS)
        if not all(
            isinstance(item, list)
            for item in (enrollment_payload, reference_payload, retrieval_payload)
        ):
            raise GenerationIsolationError("native Reference collection schema is invalid")
        enrolled_artifacts = tuple(
            ReferenceArtifact.model_validate(item) for item in enrollment_payload
        )
        reference_retrieval_policy = ReferenceRetrievalPolicySnapshot.model_validate(
            retrieval_policy_payload
        )
        references = tuple(
            ReferenceEvidence.model_validate(item) for item in reference_payload
        )
        retrievals = tuple(
            ReferenceRetrievalReceipt.model_validate(item) for item in retrieval_payload
        )
        if len({item.source_id for item in enrolled_artifacts}) != len(enrolled_artifacts):
            raise GenerationIsolationError("native Reference Enrollments repeat source IDs")
        enrolled_by_source = {item.source_id: item for item in enrolled_artifacts}
        reference_source_snapshots: dict[str, bytes] = {}
        reference_extraction_snapshots: dict[str, bytes] = {}
        extraction_by_digest: dict[str, bytes] = {}
        for artifact in enrolled_artifacts:
            if artifact != _portable_reference_artifact(artifact):
                raise GenerationIsolationError("native Reference Enrollment URI is not portable")
            source_name = f"reference_sources/{artifact.digest.sha256}.bin"
            extraction_name = f"reference_extractions/{artifact.extracted_text.sha256}.json"
            source_bytes = read_artifact(source_name)
            extraction_bytes = read_artifact(extraction_name)
            ReferenceEnrollment(
                artifact=artifact,
                source_snapshot=source_bytes,
                extraction_snapshot=extraction_bytes,
            )
            verify_reference_extraction_derivation(
                source_bytes,
                extraction_bytes,
                enrolled_artifact=artifact,
                parser_registry=self._reference_parser_registry,
            )
            reference_source_snapshots[source_name] = source_bytes
            reference_extraction_snapshots[extraction_name] = extraction_bytes
            extraction_by_digest[artifact.extracted_text.sha256] = extraction_bytes
        if transcript.reference_evidence_hash != reference_evidence_set_hash(references):
            raise GenerationIsolationError("native Reference Evidence hash mismatch")
        for item in references:
            enrolled = enrolled_by_source.get(item.artifact.source_id)
            if enrolled is None:
                raise GenerationIsolationError("native Reference Evidence lacks Enrollment")
            verify_reference_evidence_membership(
                item,
                extraction_by_digest[enrolled.extracted_text.sha256],
                enrolled_artifact=enrolled,
            )
        retrieval_invocations = {item.invocation_id for item in retrievals}
        if len(retrieval_invocations) > 1:
            raise GenerationIsolationError("native Reference receipts cross invocations")
        self._validate_reference_retrieval_state(
            transcript=audit_transcript,
            policy=reference_retrieval_policy,
            invocation_id=(
                next(iter(retrieval_invocations))
                if retrieval_invocations
                else "no-reference-retrieval"
            ),
            enrolled_artifacts=enrolled_artifacts,
            references=references,
            retrievals=retrievals,
            reference_extraction_snapshots=reference_extraction_snapshots,
            context="native stored Generation",
        )

        _validate_review_risk_evidence_membership(
            transcript=transcript,
            risks=risks,
            evidence=evidence,
            references=references,
            speech_coverage_receipt=speech_coverage_receipt,
            context="native stored Generation",
        )
        if not is_resolution_child:
            expected_native_risk = _native_full_audit_resolution_risk(transcript)
            if expected_native_risk not in risks:
                raise GenerationIsolationError("native acceptance-required risk is absent")

        audit_plan = self._load_native_generation_model(
            generation_id,
            _NATIVE_AUDIT_PLAN,
            AuditPlan,
            read_artifact=read_artifact,
        )
        signals = self._load_native_generation_model(
            generation_id,
            _NATIVE_CANDIDATE_SIGNALS,
            CandidateSignalSet,
            read_artifact=read_artifact,
        )
        groups = self._load_native_generation_model(
            generation_id,
            _NATIVE_CANDIDATE_GROUPS,
            CandidateGroupSet,
            read_artifact=read_artifact,
        )
        execution = self._load_native_generation_model(
            generation_id,
            _NATIVE_EXECUTION_PLAN,
            CorrectionAuditExecutionPlanV2,
            read_artifact=read_artifact,
        )
        assert isinstance(audit_plan, AuditPlan)
        assert isinstance(signals, CandidateSignalSet)
        assert isinstance(groups, CandidateGroupSet)
        assert isinstance(execution, CorrectionAuditExecutionPlanV2)
        audit_evidence = tuple(sorted(evidence, key=recognition_evidence_content_hash))
        expected_plan = build_audit_plan(
            audit_transcript,
            audit_evidence,
            stored_audit_policy,
            reference_retrievals=retrievals,
            references_enrolled=bool(enrolled_artifacts),
            speech_coverage=speech_coverage_receipt,
            boundary_constraints=None,
            seam_evidence=None,
        )
        expected_signals = derive_candidate_signal_set(
            expected_plan,
            audit_transcript,
            audit_evidence,
            reference_retrievals=retrievals,
            references_enrolled=bool(enrolled_artifacts),
            speech_coverage=speech_coverage_receipt,
            boundary_constraints=None,
            seam_evidence=None,
        )
        expected_groups = derive_candidate_group_set(
            expected_plan, expected_signals, audit_transcript
        )
        expected_execution = build_correction_audit_execution_plan(
            expected_plan,
            expected_signals,
            expected_groups,
            audit_transcript,
            audit_evidence,
            stored_execution_policy,
            reference_retrievals=retrievals,
            seam_evidence=None,
            normalized_audio_path=normalized_audio,
        )
        if (audit_plan, signals, groups, execution) != (
            expected_plan,
            expected_signals,
            expected_groups,
            expected_execution,
        ):
            raise GenerationIsolationError("native audit parents are not reproducible")
        text_sources, text_source_names = self._load_native_generation_source_set(
            generation_id,
            _NATIVE_TEXT_SOURCE_INDEX,
            read_artifact=read_artifact,
        )
        audio_sources, audio_source_names = self._load_native_generation_source_set(
            generation_id,
            _NATIVE_AUDIO_SOURCE_INDEX,
            read_artifact=read_artifact,
        )
        expected_text_sources, expected_audio_sources = self._native_source_sets(
            audit_plan=audit_plan,
            candidate_signal_set=signals,
            candidate_group_set=groups,
            execution_plan=execution,
            transcript=audit_transcript,
            recognition_evidence=audit_evidence,
            reference_retrievals=retrievals,
            normalized_audio_path=normalized_audio,
        )
        if text_sources != expected_text_sources or audio_sources != expected_audio_sources:
            raise GenerationIsolationError("native source artifacts are not reproducible")

        text_record = self._load_native_generation_model(
            generation_id,
            _NATIVE_TEXT_RECORD,
            TextAuditExecutionRecordV2,
            read_artifact=read_artifact,
        )
        assert isinstance(text_record, TextAuditExecutionRecordV2)
        text_raw, text_run_names = self._load_native_generation_run_bytes(
            generation_id,
            index_name=_NATIVE_TEXT_RUN_INDEX,
            categories=("requests", "responses"),
            read_artifact=read_artifact,
        )
        text_run = TextAuditRunResult(
            record=text_record,
            request_bytes=text_raw["requests"],
            response_bytes=text_raw["responses"],
        )
        try:
            replayed_text = bundle.text_executor.replay(
                execution, audit_plan, text_sources, text_run
            )
        except ValueError as exc:
            raise GenerationIsolationError("native text audit is not replayable") from exc
        if replayed_text != text_run:
            raise GenerationIsolationError("native text audit replay changed")

        audio_record = self._load_native_generation_model(
            generation_id,
            _NATIVE_AUDIO_RECORD,
            AudioAuditExecutionRecordV2,
            read_artifact=read_artifact,
        )
        aggregate = self._load_native_generation_model(
            generation_id,
            _NATIVE_AGGREGATE,
            FullAuditAggregateAttestationV2,
            read_artifact=read_artifact,
        )
        assert isinstance(audio_record, AudioAuditExecutionRecordV2)
        assert isinstance(aggregate, FullAuditAggregateAttestationV2)
        audio_raw, audio_run_names = self._load_native_generation_run_bytes(
            generation_id,
            index_name=_NATIVE_AUDIO_RUN_INDEX,
            categories=("requests", "responses", "clips", "journals"),
            read_artifact=read_artifact,
        )
        if audio_raw["journals"] != tuple(
            canonical_json_bytes(item) for item in audio_record.invocation_journals
        ):
            raise GenerationIsolationError("native audio invocation journals changed")
        audio_run = AudioAuditRunResult(
            record=audio_record,
            request_bytes=audio_raw["requests"],
            response_bytes=audio_raw["responses"],
            clip_bytes=audio_raw["clips"],
        )
        try:
            replayed_audio = bundle.audio_executor.replay(
                execution, audit_plan, audio_sources, audio_run
            )
            replayed_aggregate = verify_full_audit_aggregate(
                read_artifact(_NATIVE_AGGREGATE),
                audit_plan=audit_plan,
                candidate_signal_set=signals,
                candidate_group_set=groups,
                execution_plan=execution,
                text_record=text_record,
                audio_record=audio_record,
                text_request_bytes=text_run.request_bytes,
                text_response_bytes=text_run.response_bytes,
                audio_request_bytes=audio_run.request_bytes,
                audio_response_bytes=audio_run.response_bytes,
                audio_clip_bytes=audio_run.clip_bytes,
            )
        except ValueError as exc:
            raise GenerationIsolationError("native audio audit is not replayable") from exc
        if replayed_audio != audio_run or replayed_aggregate != aggregate:
            raise GenerationIsolationError("native audio or aggregate replay changed")

        resolution_names: set[str] = set()
        resolution: LoadedNativeResolutionState | None = None
        if is_resolution_child:
            resolution_names, resolution = self._verify_native_resolution_child(
                generation_id=generation_id,
                manifest=manifest,
                transcript=transcript,
                risks=risks,
                references=references,
                retrievals=retrievals,
                text_record=text_record,
                audio_record=audio_record,
                aggregate=aggregate,
                read_artifact=read_artifact,
                verified_artifact=verified_artifact,
            )

        expected_inputs = {
            "source_audio": transcript.source_audio_hash,
            "normalized_audio": transcript.normalized_audio_hash,
            "normalization_receipt": transcript.normalization_receipt_hash,
            "recognition_request": recognition_request_artifact.content_hash,
            "recognition_independence": (
                recognition_independence.content_hash
                if recognition_independence is not None
                else hash_object(None)
            ),
            "recognition_evidence": transcript.evidence_hash,
            "reference_enrollments": hash_object(enrolled_artifacts),
            "references": transcript.reference_evidence_hash,
            "ledger_head": transcript.ledger_hash,
        }
        if transcript.orthographic_projection_evidence_hash is not None:
            expected_inputs["orthographic_projection_evidence"] = (
                transcript.orthographic_projection_evidence_hash
            )
        if is_resolution_child:
            decision = NativeCorrectionDecisionV2.model_validate_json(
                read_artifact(_NATIVE_RESOLUTION_DECISION),
                strict=True,
            )
            assert manifest.parent_manifest_hash is not None
            expected_inputs.update(
                {
                    "parent_manifest": manifest.parent_manifest_hash,
                    "native_resolution_decision": decision.content_hash,
                }
            )
        expected_stage_hashes = {
            "adapter_identities": sha256_bytes(
                read_artifact(_ADAPTER_IDENTITIES)
            ),
            "normalization_receipt": hash_object(receipt),
            "recognition_request": recognition_request_artifact.content_hash,
            "recognition_independence": (
                recognition_independence.content_hash
                if recognition_independence is not None
                else hash_object(None)
            ),
            "recognition_evidence": hash_object(evidence),
            "reference_evidence": reference_evidence_set_hash(references),
            "reference_retrieval_receipts": hash_object(retrievals),
            "canonical_transcript": hash_object(transcript),
            "risks": hash_object(tuple(_risk_payload(item) for item in risks)),
            "native_audit_plan": sha256_bytes(
                read_artifact(_NATIVE_AUDIT_PLAN)
            ),
            "native_candidate_signal_set": sha256_bytes(
                read_artifact(_NATIVE_CANDIDATE_SIGNALS)
            ),
            "native_candidate_group_set": sha256_bytes(
                read_artifact(_NATIVE_CANDIDATE_GROUPS)
            ),
            "native_execution_plan": sha256_bytes(
                read_artifact(_NATIVE_EXECUTION_PLAN)
            ),
            "native_text_audit_record": sha256_bytes(
                read_artifact(_NATIVE_TEXT_RECORD)
            ),
            "native_audio_audit_record": sha256_bytes(
                read_artifact(_NATIVE_AUDIO_RECORD)
            ),
            "native_full_audit_aggregate": sha256_bytes(
                read_artifact(_NATIVE_AGGREGATE)
            ),
        }
        if transcript.orthographic_projection_evidence_hash is not None:
            expected_stage_hashes["orthographic_projection_evidence"] = hash_object(
                orthographic_projections
            )
        if is_resolution_child:
            expected_stage_hashes.update(
                {
                    "native_resolution_decision": sha256_bytes(
                        read_artifact(_NATIVE_RESOLUTION_DECISION)
                    ),
                    "native_resolution_authorization": sha256_bytes(
                        read_artifact(_NATIVE_RESOLUTION_AUTHORIZATION)
                    ),
                    "native_resolution_evidence": hash_object(
                        tuple(
                            (
                                name,
                                sha256_bytes(
                                    read_artifact(name)
                                ),
                            )
                            for name in sorted(resolution_names)
                        )
                    ),
                }
            )
        if (
            manifest.status != "complete"
            or manifest.episode_id != transcript.episode_id
            or manifest.code_hash != self._code_hash
            or manifest.policy_hash != transcript.policy_hash
            or manifest.input_artifact_hashes != expected_inputs
            or manifest.adapter_hash != hash_object(expected_adapter_payload)
            or manifest.stage_artifact_hashes != expected_stage_hashes
        ):
            raise GenerationIsolationError("native Generation Manifest lineage mismatch")

        fixed_names = {
            _RECEIPT,
            _AUDIO_ARTIFACTS,
            _EVIDENCE,
            _RECOGNITION_REQUEST,
            _RECOGNITION_INDEPENDENCE,
            _BASE_PRIMARY_EVIDENCE,
            _SPEAKER_PROVENANCE,
            _REFERENCE_ENROLLMENTS,
            _REFERENCE_RETRIEVAL_POLICY,
            _SPEECH_COVERAGE,
            _REFERENCES,
            _RETRIEVALS,
            _CHECKPOINT_CANONICAL,
            _CHECKPOINT_RISKS,
            _NATIVE_AUDIT_PLAN,
            _NATIVE_CANDIDATE_SIGNALS,
            _NATIVE_CANDIDATE_GROUPS,
            _NATIVE_EXECUTION_PLAN,
            _NATIVE_TEXT_RECORD,
            _NATIVE_AUDIO_RECORD,
            _NATIVE_AGGREGATE,
            _CANONICAL,
            _RISKS,
            _ADAPTER_IDENTITIES,
            _MANIFEST,
            *resolution_names,
        }
        if transcript.orthographic_projection_evidence_hash is not None:
            fixed_names.add(_ORTHOGRAPHIC_PROJECTIONS)
        expected_artifact_names = (
            fixed_names
            | set(raw_outputs)
            | set(orthographic_raw_outputs)
            | set(speech_coverage_raw_artifacts)
            | set(reference_source_snapshots)
            | set(reference_extraction_snapshots)
            | text_source_names
            | audio_source_names
            | text_run_names
            | audio_run_names
        )
        if set(record.artifact_hashes) != expected_artifact_names:
            raise GenerationIsolationError(
                "native Generation artifact profile is incomplete or extra"
            )

        result = CanonicalBuildResult(
            outcome="needs_review",
            transcript=transcript,
            candidates=(),
            risks=risks,
            reference_evidence_hash=transcript.reference_evidence_hash,
            generation_warnings=transcript.generation_warnings,
        )
        def verified_set(names: set[str] | tuple[str, ...]) -> VerifiedArtifactSet:
            return VerifiedArtifactSet(
                tuple(verified_artifact(name) for name in sorted(names))
            )

        loaded_speech_identity = (
            None
            if stored_speech is None
            else LoadedSpeechCoverageAdapterIdentity(
                adapter_name=stored_speech["adapter_name"],
                adapter_version=stored_speech["adapter_version"],
                config_hash=stored_speech["config_hash"],
                code_hash=stored_speech["code_hash"],
                runtime_hash=stored_speech["runtime_hash"],
            )
        )

        def loaded_adapter(identity: AdapterIdentity | None) -> LoadedAdapterIdentity | None:
            return (
                None
                if identity is None
                else LoadedAdapterIdentity(
                    name=identity.name,
                    version=identity.version,
                    config_hash=identity.config_hash,
                    execution_mode=identity.execution_mode,
                )
            )

        common_runtime = LoadedCommonRuntimeState(
            recognizers=stored_recognizers,
            speech_coverage_analyzer=loaded_speech_identity,
            speaker_attributor=loaded_adapter(stored_speaker),
            reference_retriever=loaded_adapter(stored_reference),
        )

        def native_sources(
            modality: Literal["text", "audio"],
            sources: dict[str, bytes],
            index_name: str,
        ) -> LoadedNativeSourceSet:
            bindings = tuple(
                VerifiedNativeSource(
                    uri=uri,
                    artifact=verified_artifact(
                        f"native_full_audit/sources/{modality}/{ordinal:04d}-"
                        f"{sha256_bytes(payload)}.bin"
                    ),
                )
                for ordinal, (uri, payload) in enumerate(sorted(sources.items()))
            )
            return LoadedNativeSourceSet(
                modality=modality,
                index_artifact=verified_artifact(index_name),
                sources=bindings,
            )

        text_request_names = {
            name
            for name in text_run_names
            if name.startswith("native_full_audit/text/requests/")
        }
        text_response_names = {
            name
            for name in text_run_names
            if name.startswith("native_full_audit/text/responses/")
        }
        audio_request_names = {
            name
            for name in audio_run_names
            if name.startswith("native_full_audit/audio/requests/")
        }
        audio_response_names = {
            name
            for name in audio_run_names
            if name.startswith("native_full_audit/audio/responses/")
        }
        audio_clip_names = {
            name
            for name in audio_run_names
            if name.startswith("native_full_audit/audio/clips/")
        }
        audio_journal_names = {
            name
            for name in audio_run_names
            if name.startswith("native_full_audit/audio/journals/")
        }
        loaded = LoadedGenerationState(
            result=result,
            storage=LoadedStorageSnapshot.capture(
                generation_id=record.generation_id,
                artifact_set_hash=record.artifact_set_hash,
                manifest=manifest,
                artifact_hashes=record.artifact_hashes,
                directory=record.directory,
            ),
            normalization=LoadedNormalizationState(
                receipt=receipt,
                source_audio_path=source_audio,
                normalized_audio_path=normalized_audio,
                receipt_artifact=verified_artifact(_RECEIPT),
                audio_bindings_artifact=verified_artifact(_AUDIO_ARTIFACTS),
            ),
            recognition=LoadedRecognitionState(
                request_artifact=recognition_request_artifact,
                request=recognition_request,
                independence=recognition_independence,
                evidence=evidence,
                speaker_base_evidence=speaker_base_evidence,
                speaker_provenance=speaker_provenance,
                orthographic_projections=orthographic_projections,
                request_bytes=verified_artifact(_RECOGNITION_REQUEST),
                independence_bytes=verified_artifact(_RECOGNITION_INDEPENDENCE),
                evidence_bytes=verified_artifact(_EVIDENCE),
                speaker_base_evidence_bytes=verified_artifact(_BASE_PRIMARY_EVIDENCE),
                speaker_provenance_bytes=verified_artifact(_SPEAKER_PROVENANCE),
                orthographic_projection_bytes=(
                    verified_artifact(_ORTHOGRAPHIC_PROJECTIONS)
                    if orthographic_projections
                    else None
                ),
                raw_outputs=verified_set(set(raw_outputs)),
                orthographic_raw_outputs=verified_set(set(orthographic_raw_outputs)),
            ),
            references=LoadedReferenceState(
                enrollments=enrolled_artifacts,
                evidence=references,
                retrievals=retrievals,
                retrieval_policy=reference_retrieval_policy,
                enrollments_bytes=verified_artifact(_REFERENCE_ENROLLMENTS),
                evidence_bytes=verified_artifact(_REFERENCES),
                retrievals_bytes=verified_artifact(_RETRIEVALS),
                retrieval_policy_bytes=verified_artifact(_REFERENCE_RETRIEVAL_POLICY),
                source_snapshots=verified_set(set(reference_source_snapshots)),
                extraction_snapshots=verified_set(set(reference_extraction_snapshots)),
            ),
            speech_coverage=LoadedSpeechCoverageState(
                receipt=speech_coverage_receipt,
                receipt_bytes=verified_artifact(_SPEECH_COVERAGE),
                raw_outputs=verified_set(set(speech_coverage_raw_artifacts)),
            ),
            audit=LoadedNativeAuditState(
                audit_basis=audit_basis,
                audit_plan=audit_plan,
                candidate_signals=signals,
                candidate_groups=groups,
                execution_plan=execution,
                text_sources=native_sources(
                    "text", text_sources, _NATIVE_TEXT_SOURCE_INDEX
                ),
                audio_sources=native_sources(
                    "audio", audio_sources, _NATIVE_AUDIO_SOURCE_INDEX
                ),
                text_run=text_run,
                audio_run=audio_run,
                aggregate=aggregate,
                audit_artifacts=LoadedNativeAuditArtifacts(
                    audit_plan=verified_artifact(_NATIVE_AUDIT_PLAN),
                    candidate_signals=verified_artifact(_NATIVE_CANDIDATE_SIGNALS),
                    candidate_groups=verified_artifact(_NATIVE_CANDIDATE_GROUPS),
                    execution_plan=verified_artifact(_NATIVE_EXECUTION_PLAN),
                    text_record=verified_artifact(_NATIVE_TEXT_RECORD),
                    audio_record=verified_artifact(_NATIVE_AUDIO_RECORD),
                    aggregate=verified_artifact(_NATIVE_AGGREGATE),
                ),
                text_run_artifacts=LoadedNativeTextRunArtifacts(
                    index_artifact=verified_artifact(_NATIVE_TEXT_RUN_INDEX),
                    requests=verified_set(text_request_names),
                    responses=verified_set(text_response_names),
                ),
                audio_run_artifacts=LoadedNativeAudioRunArtifacts(
                    index_artifact=verified_artifact(_NATIVE_AUDIO_RUN_INDEX),
                    requests=verified_set(audio_request_names),
                    responses=verified_set(audio_response_names),
                    clips=verified_set(audio_clip_names),
                    journals=verified_set(audio_journal_names),
                ),
                runtime=LoadedNativeRuntimeState(
                    common=common_runtime,
                    text_identity=stored_text_identity,
                    text_policy=stored_text_policy,
                    audio_identity=stored_audio_identity,
                    audio_policy=stored_audio_policy,
                    audit_policy=stored_audit_policy,
                    execution_policy=stored_execution_policy,
                    identities_artifact=verified_artifact(_ADAPTER_IDENTITIES),
                ),
                resolution=resolution,
            ),
        )
        if require_active:
            self._validate_active_ledger_lineage(loaded)
        return loaded

    def _load_generation(
        self,
        generation_id: str,
        *,
        require_active: bool,
        _artifact_cache: Mapping[str, bytes] | None = None,
    ) -> LoadedGenerationState:
        if require_active and self.store.active_generation_id() != generation_id:
            raise GenerationIsolationError(f"generation {generation_id} is not active")
        record = self.store.load(generation_id)
        manifest = GenerationManifest.model_validate(record.manifest)
        if manifest.generation_id != generation_id:
            raise GenerationIsolationError("manifest and requested Generation ID differ")

        artifact_cache = dict(_artifact_cache or {})
        for name, payload in artifact_cache.items():
            expected_hash = record.artifact_hashes.get(name)
            if expected_hash is None or sha256_bytes(payload) != expected_hash:
                raise GenerationIsolationError(
                    f"preloaded Generation artifact {name!r} is not record-bound"
                )

        def read_artifact(name: str) -> bytes:
            if name not in artifact_cache:
                artifact_cache[name] = self.store.read_artifact(generation_id, name)
            return artifact_cache[name]

        def verified_artifact(name: str) -> VerifiedArtifactBytes:
            try:
                expected_hash = record.artifact_hashes[name]
            except KeyError as exc:
                raise GenerationIsolationError(
                    f"loaded Generation lacks exact artifact {name!r}"
                ) from exc
            return VerifiedArtifactBytes(
                name=name,
                sha256=expected_hash,
                payload=read_artifact(name),
            )

        adapter_identity_bytes = read_artifact(_ADAPTER_IDENTITIES)
        try:
            identity_discriminant = json.loads(adapter_identity_bytes)
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise GenerationIsolationError(
                "Generation Adapter identity artifact is invalid JSON"
            ) from exc
        native_stage = "native_full_audit_aggregate" in manifest.stage_artifact_hashes
        native_artifact = _NATIVE_AGGREGATE in record.artifact_hashes
        native_identity = (
            isinstance(identity_discriminant, dict)
            and "native_text_full_audit" in identity_discriminant
        )
        if len({native_stage, native_artifact, native_identity}) != 1:
            raise GenerationIsolationError(
                "Generation native Full Audit profile is incomplete or ambiguous"
            )
        if native_stage:
            return self._load_native_generation(
                generation_id,
                require_active=require_active,
                record=record,
                manifest=manifest,
                read_artifact=read_artifact,
                verified_artifact=verified_artifact,
            )

        def load_json(name: str) -> object:
            return json.loads(read_artifact(name))

        adapter_identities_payload = load_json(_ADAPTER_IDENTITIES)
        if not isinstance(adapter_identities_payload, dict) or set(
            adapter_identities_payload
        ) != {
            "recognizers",
            "corrector",
            "corrector_execution",
            "audio_auditor",
            "speech_coverage_analyzer",
            "arbiter",
            "reference_retriever",
            "speaker_attributor",
        }:
            raise GenerationIsolationError("stored Adapter identity set is incomplete")
        recognizer_payload = adapter_identities_payload["recognizers"]
        if not isinstance(recognizer_payload, list):
            raise GenerationIsolationError(
                "stored Recognition Adapter identity set has invalid shape"
            )
        stored_recognizer_identities = tuple(
            _recognition_identity_from_payload(item) for item in recognizer_payload
        )
        stored_corrector_identity = _adapter_identity_from_payload(
            adapter_identities_payload["corrector"]
        )
        stored_corrector_execution_identity = _correction_identity_from_payload(
            adapter_identities_payload["corrector_execution"]
        )
        stored_audio_auditor_identity = _audio_identity_from_payload(
            adapter_identities_payload["audio_auditor"]
        )
        stored_speech_coverage_identity = _speech_coverage_identity_from_payload(
            adapter_identities_payload["speech_coverage_analyzer"]
        )
        stored_arbiter_identity = _audio_identity_from_payload(
            adapter_identities_payload["arbiter"]
        )
        stored_reference_identity = _adapter_identity_from_payload(
            adapter_identities_payload["reference_retriever"]
        )
        stored_speaker_identity = _adapter_identity_from_payload(
            adapter_identities_payload["speaker_attributor"]
        )
        if (
            stored_recognizer_identities != self._runtime_recognizer_identities()
            or stored_corrector_identity != self._corrector_identity
            or stored_corrector_execution_identity
            != self._corrector_execution_identity
            or stored_audio_auditor_identity != self._audio_auditor.identity
            or stored_speech_coverage_identity
            != self._speech_coverage_analyzer_identity
            or stored_arbiter_identity
            != (self._arbiter.identity if self._arbiter is not None else None)
            or stored_reference_identity != self._reference_retriever_identity
            or (
                stored_speaker_identity is not None
                and stored_speaker_identity != self._speaker_attributor_identity
            )
        ):
            raise GenerationIsolationError(
                "runtime Adapter identities differ from the stored Generation"
            )

        transcript = CanonicalTranscript.model_validate(load_json(_CANONICAL))
        if transcript.generation_id != generation_id:
            raise GenerationIsolationError("Canonical Transcript crossed Generation boundary")
        _validate_acceptance_policy_not_rewritten(transcript)
        receipt = NormalizationReceipt.model_validate(load_json(_RECEIPT))
        if receipt.status != "accepted":
            raise GenerationIsolationError("stored generation has an unaccepted receipt")
        if receipt.source.sha256 != transcript.source_audio_hash:
            raise GenerationIsolationError("stored receipt source lineage mismatch")
        if receipt.normalized.sha256 != transcript.normalized_audio_hash:
            raise GenerationIsolationError("stored receipt normalized lineage mismatch")
        if (
            normalization_receipt_content_hash(receipt)
            != transcript.normalization_receipt_hash
        ):
            raise GenerationIsolationError("stored receipt content identity mismatch")
        audio_artifacts = load_json(_AUDIO_ARTIFACTS)
        if audio_artifacts != _audio_artifacts_payload(receipt):
            raise GenerationIsolationError(
                "stored audio artifact identities differ from Normalization Receipt"
            )
        source_audio = self.store.audio_path(
            receipt.source.sha256, size_bytes=receipt.source.size_bytes
        )
        normalized_audio = self.store.audio_path(
            receipt.normalized.sha256, size_bytes=receipt.normalized.size_bytes
        )
        recognition_request_artifact, recognition_request = (
            self._load_stored_recognition_request(
                generation_id,
                episode_id=transcript.episode_id,
                normalized_audio=normalized_audio,
                normalized_sha256=receipt.normalized.sha256,
                normalized_size_bytes=receipt.normalized.size_bytes,
                read_artifact=read_artifact,
            )
        )
        recognition_independence = self._load_stored_recognition_independence(
            generation_id,
            read_artifact=read_artifact,
        )
        evidence = tuple(
            RecognitionEvidence.model_validate(item) for item in load_json(_EVIDENCE)
        )
        if not evidence:
            raise GenerationIsolationError("stored generation lacks Recognition Evidence")
        if transcript.orthographic_projection_evidence_hash is None:
            orthographic_projections: tuple[OrthographicProjectionEvidenceV1, ...] = ()
            orthographic_raw_outputs: dict[str, bytes] = {}
        else:
            orthographic_projections, orthographic_raw_outputs = (
                self._load_orthographic_projection_artifacts(
                    generation_id,
                    evidence=evidence,
                    expected_set_hash=transcript.orthographic_projection_evidence_hash,
                    read_artifact=read_artifact,
                )
            )
        _validate_canonical_timing_provenance(
            transcript,
            evidence,
            orthographic_projections,
        )
        speech_coverage_payload = load_json(_SPEECH_COVERAGE)
        speech_coverage_receipt = (
            None
            if speech_coverage_payload is None
            else SpeechCoverageReceipt.model_validate(speech_coverage_payload)
        )
        speech_coverage_raw_artifacts: dict[str, bytes] = {}
        if speech_coverage_receipt is None:
            if stored_speech_coverage_identity is not None:
                raise GenerationIsolationError(
                    "stored Speech Coverage Analyzer identity lacks a receipt"
                )
        else:
            if self._speech_coverage_analyzer is None:
                raise GenerationIsolationError(
                    "stored Speech Coverage Receipt requires its exact Analyzer runtime"
                )
            if receipt.normalized_duration_ms is None:
                raise GenerationIsolationError(
                    "stored normalized audio lacks duration for Speech Coverage replay"
                )
            raw_name = speech_coverage_receipt.raw_output.uri.removeprefix(
                "generation-artifact://"
            )
            expected_raw_name = (
                "speech_coverage/raw/"
                f"{speech_coverage_receipt.raw_output.sha256}.json"
            )
            if raw_name != expected_raw_name:
                raise GenerationIsolationError(
                    "stored Speech Coverage raw artifact URI is not canonical"
                )
            raw_payload = read_artifact(raw_name)
            if (
                sha256_bytes(raw_payload) != speech_coverage_receipt.raw_output.sha256
                or len(raw_payload) != speech_coverage_receipt.raw_output.size_bytes
                or speech_coverage_receipt.raw_output_hash
                != speech_coverage_receipt.raw_output.sha256
            ):
                raise GenerationIsolationError(
                    "stored Speech Coverage raw artifact digest mismatch"
                )
            speech_coverage_raw_artifacts[raw_name] = raw_payload
            invocation_ids = {item.invocation_id for item in evidence}
            if len(invocation_ids) != 1:
                raise GenerationIsolationError(
                    "stored Recognition Evidence crosses invocation boundaries"
                )
            coverage_request = SpeechCoverageRequest(
                episode_id=transcript.episode_id,
                invocation_id=next(iter(invocation_ids)),
                normalized_audio=self.store.audio_path(
                    receipt.normalized.sha256,
                    size_bytes=receipt.normalized.size_bytes,
                ),
                expected_normalized_audio_hash=receipt.normalized.sha256,
                expected_normalized_audio_size_bytes=receipt.normalized.size_bytes,
                normalized_audio_duration_ms=receipt.normalized_duration_ms,
                recognition_evidence=evidence,
                raw_output_dir=self.store.root / "replay-only-speech-coverage",
            )
            try:
                verified_coverage = self._speech_coverage_analyzer.verify(
                    speech_coverage_receipt,
                    request=coverage_request,
                    raw_output=raw_payload,
                )
            except (AdapterIntegrityError, ValueError) as exc:
                raise GenerationIsolationError(
                    "stored Speech Coverage Receipt is not independently reproducible"
                ) from exc
            if verified_coverage != speech_coverage_receipt:
                raise GenerationIsolationError(
                    "stored Speech Coverage verification changed the receipt"
                )
        if transcript.verified_speech_coverage_receipt_hash != (
            verified_speech_coverage_hash(speech_coverage_receipt)
        ):
            raise GenerationIsolationError(
                "Canonical Transcript Speech Coverage attestation mismatch"
            )
        speaker_base_evidence = tuple(
            RecognitionEvidence.model_validate(item)
            for item in load_json(_BASE_PRIMARY_EVIDENCE)
        )
        speaker_provenance_payload = load_json(_SPEAKER_PROVENANCE)
        speaker_provenance = (
            None
            if speaker_provenance_payload is None
            else SpeakerAttributionProvenance.from_payload(
                speaker_provenance_payload
            )
        )
        raw_outputs: dict[str, bytes] = {}
        seen_raw_digests: set[str] = set()
        for item in (*evidence, *speaker_base_evidence):
            if item.raw_output.sha256 in seen_raw_digests:
                continue
            seen_raw_digests.add(item.raw_output.sha256)
            name = f"raw_evidence/{item.raw_output.sha256}.json"
            payload = read_artifact(name)
            if (
                sha256_bytes(payload) != item.raw_output.sha256
                or len(payload) != item.raw_output.size_bytes
            ):
                raise GenerationIsolationError("stored Recognition raw output mismatch")
            raw_outputs[name] = payload
        original_recognition_evidence = (
            (speaker_base_evidence[0], *evidence[1:])
            if speaker_base_evidence
            else evidence
        )
        invocation_ids = {item.invocation_id for item in original_recognition_evidence}
        if len(invocation_ids) != 1:
            raise GenerationIsolationError(
                "stored Recognition Evidence crosses invocation boundaries"
            )
        self._verify_recognition_derivations(
            original_recognition_evidence,
            request=recognition_request,
            raw_outputs=raw_outputs,
            context="stored Generation",
        )
        if recognition_request_artifact.invocation_id != next(iter(invocation_ids)):
            raise GenerationIsolationError(
                "stored Recognition request invocation differs from Evidence"
            )
        if bool(speaker_base_evidence) != (speaker_provenance is not None):
            raise GenerationIsolationError(
                "stored Speaker Attribution base Evidence/provenance is incomplete"
            )
        if speaker_base_evidence:
            if len(speaker_base_evidence) != 1 or self._speaker_attributor is None:
                raise GenerationIsolationError(
                    "stored Speaker Attribution requires one base Evidence and Adapter"
                )
            identity = self._speaker_attributor_identity
            assert identity is not None
            if (
                speaker_provenance.adapter != identity.name
                or str(speaker_provenance.adapter_version) != identity.version
            ):
                raise GenerationIsolationError(
                    "stored Speaker Attribution Adapter identity mismatch"
                )
            attribution_raw = raw_outputs[
                f"raw_evidence/{evidence[0].raw_output.sha256}.json"
            ]
            verified_speaker_provenance = self._speaker_attributor.verify(
                evidence[0],
                base_evidence=speaker_base_evidence[0],
                raw_output=attribution_raw,
            )
            _assert_speaker_attribution_binding(
                attributed=evidence[0],
                base=speaker_base_evidence[0],
                provenance=verified_speaker_provenance,
                identity=identity,
            )
            if verified_speaker_provenance != speaker_provenance:
                raise GenerationIsolationError(
                    "stored Speaker Attribution provenance is not reproducible"
                )
        elif self._speaker_attributor is not None and evidence[0].adapter == (
            self._speaker_attributor_identity.name
        ):
            raise GenerationIsolationError(
                "attributed primary Evidence lacks persisted base provenance"
            )
        enrolled_artifacts = tuple(
            ReferenceArtifact.model_validate(item)
            for item in load_json(_REFERENCE_ENROLLMENTS)
        )
        reference_retrieval_policy = ReferenceRetrievalPolicySnapshot.model_validate(
            load_json(_REFERENCE_RETRIEVAL_POLICY)
        )
        if len({item.source_id for item in enrolled_artifacts}) != len(
            enrolled_artifacts
        ):
            raise GenerationIsolationError(
                "stored Reference Enrollments have duplicate source IDs"
            )
        enrolled_by_source = {
            artifact.source_id: artifact for artifact in enrolled_artifacts
        }
        reference_source_snapshots: dict[str, bytes] = {}
        reference_extraction_snapshots: dict[str, bytes] = {}
        source_snapshots_by_digest: dict[str, bytes] = {}
        extraction_snapshots_by_digest: dict[str, bytes] = {}
        for artifact in enrolled_artifacts:
            expected_artifact = _portable_reference_artifact(artifact)
            if artifact != expected_artifact:
                raise GenerationIsolationError(
                    "stored Reference Enrollment contains a machine-local URI"
                )
            source_name = f"reference_sources/{artifact.digest.sha256}.bin"
            source_payload = read_artifact(source_name)
            extraction_name = (
                f"reference_extractions/{artifact.extracted_text.sha256}.json"
            )
            extraction_payload = read_artifact(extraction_name)
            ReferenceEnrollment(
                artifact=artifact,
                source_snapshot=source_payload,
                extraction_snapshot=extraction_payload,
            )
            verify_reference_extraction_derivation(
                source_payload,
                extraction_payload,
                enrolled_artifact=artifact,
                parser_registry=self._reference_parser_registry,
            )
            previous_source = source_snapshots_by_digest.get(artifact.digest.sha256)
            if previous_source is not None and previous_source != source_payload:
                raise GenerationIsolationError(
                    "stored Reference source digest has conflicting bytes"
                )
            source_snapshots_by_digest[artifact.digest.sha256] = source_payload
            reference_source_snapshots[source_name] = source_payload
            previous = extraction_snapshots_by_digest.get(
                artifact.extracted_text.sha256
            )
            if previous is not None and previous != extraction_payload:
                raise GenerationIsolationError(
                    "stored Reference extraction digest has conflicting bytes"
                )
            extraction_snapshots_by_digest[
                artifact.extracted_text.sha256
            ] = extraction_payload
            reference_extraction_snapshots[extraction_name] = extraction_payload
        references = tuple(
            ReferenceEvidence.model_validate(item) for item in load_json(_REFERENCES)
        )
        retrievals = tuple(
            ReferenceRetrievalReceipt.model_validate(item) for item in load_json(_RETRIEVALS)
        )
        proposals = tuple(
            CorrectionProposal(**item) for item in load_json(_PROPOSALS)
        )
        correction_audits = tuple(
            CorrectionAuditReceipt.model_validate(item)
            for item in load_json(_CORRECTION_AUDITS)
        )
        targeted_correction_audits = tuple(
            _TargetedCorrectionAuditReceipt(**item)
            for item in load_json(_TARGETED_CORRECTION_AUDITS)
        )
        audio_audits = tuple(
            AudioAuditReceipt.model_validate(item) for item in load_json(_AUDIO_AUDITS)
        )
        arbitration_receipts = tuple(
            ArbitrationReceipt.model_validate(item) for item in load_json(_ARBITRATIONS)
        )
        correction_execution_artifacts: dict[str, bytes] = {}
        for artifact in (
            artifact
            for audit in (*correction_audits, *targeted_correction_audits)
            for artifact in (audit.request, audit.response)
        ):
            name = artifact.uri.removeprefix("generation-artifact://")
            if name == artifact.uri:
                raise GenerationIsolationError(
                    "stored Correction execution artifact URI is not portable"
                )
            payload = read_artifact(name)
            if sha256_bytes(payload) != artifact.sha256 or len(payload) != artifact.size_bytes:
                raise GenerationIsolationError(
                    "stored Correction execution bytes differ from their receipt"
                )
            existing = correction_execution_artifacts.get(name)
            if existing is not None and existing != payload:
                raise GenerationIsolationError(
                    "stored Correction execution digest has conflicting bytes"
                )
            correction_execution_artifacts[name] = payload
        audio_execution_artifacts: dict[str, bytes] = {}
        for artifact in (
            *(
                artifact
                for receipt in audio_audits
                for artifact in (receipt.request, receipt.response, receipt.clip)
            ),
            *(
                artifact
                for receipt in arbitration_receipts
                for artifact in (receipt.request, receipt.response, receipt.clip)
            ),
        ):
            name = artifact.uri.removeprefix("generation-artifact://")
            if name == artifact.uri:
                raise GenerationIsolationError(
                    "stored audio execution artifact URI is not portable"
                )
            payload = read_artifact(name)
            if sha256_bytes(payload) != artifact.sha256 or len(payload) != artifact.size_bytes:
                raise GenerationIsolationError(
                    "stored audio execution bytes differ from their receipt"
                )
            existing = audio_execution_artifacts.get(name)
            if existing is not None and existing != payload:
                raise GenerationIsolationError(
                    "stored audio execution digest has conflicting bytes"
                )
            audio_execution_artifacts[name] = payload
        risks = tuple(_risk_from_payload(item) for item in load_json(_RISKS))
        if transcript.revision == 1:
            stored_risks_by_id = {risk.issue.id: risk for risk in risks}
            expected_coverage_risks = speech_coverage_risks(
                transcript,
                speech_coverage_receipt,
            )
            if any(
                stored_risks_by_id.get(expected.issue.id) != expected
                for expected in expected_coverage_risks
            ):
                raise GenerationIsolationError(
                    "stored Generation omits or changes independently derived Speech Coverage risk"
                )
        if transcript.reference_evidence_hash != reference_evidence_set_hash(references):
            raise GenerationIsolationError("Reference Evidence snapshot hash mismatch")
        for item in references:
            enrolled = enrolled_by_source.get(item.artifact.source_id)
            if enrolled is None:
                raise GenerationIsolationError(
                    "stored Reference Evidence lacks a persisted Enrollment"
                )
            try:
                snapshot = extraction_snapshots_by_digest[
                    enrolled.extracted_text.sha256
                ]
            except KeyError as exc:
                raise GenerationIsolationError(
                    "stored Reference Evidence extraction snapshot is missing"
                ) from exc
            verify_reference_evidence_membership(
                item,
                snapshot,
                enrolled_artifact=enrolled,
            )
        reference_invocations = {item.invocation_id for item in retrievals}
        if len(reference_invocations) > 1:
            raise GenerationIsolationError(
                "stored Reference Retrieval Receipts cross invocations"
            )
        reference_preaudit = _derive_correction_preaudit_transcript(
            transcript,
            proposals=proposals,
            remove_proposal_ids=tuple(proposal.id for proposal in proposals),
            audio_audits=audio_audits,
        )
        self._validate_reference_retrieval_state(
            transcript=reference_preaudit,
            policy=reference_retrieval_policy,
            invocation_id=(
                next(iter(reference_invocations))
                if reference_invocations
                else "no-reference-retrieval"
            ),
            enrolled_artifacts=enrolled_artifacts,
            references=references,
            retrievals=retrievals,
            reference_extraction_snapshots=reference_extraction_snapshots,
            context="stored Generation",
        )
        if transcript.evidence_hash != recognition_evidence_set_hash(evidence):
            raise GenerationIsolationError("Recognition Evidence set hash mismatch")
        if any(
            item.episode_id != transcript.episode_id
            or item.normalized_audio_hash != transcript.normalized_audio_hash
            for item in evidence
        ):
            raise GenerationIsolationError("stored Recognition Evidence lineage mismatch")
        _validate_review_risk_evidence_membership(
            transcript=transcript,
            risks=risks,
            evidence=evidence,
            references=references,
            speech_coverage_receipt=speech_coverage_receipt,
            context="stored Generation",
        )
        assert stored_corrector_execution_identity is not None
        _validate_proposal_ownership(
            proposals,
            correction_audits,
            audio_audits,
            arbitration_receipts,
            require_arbitration=stored_arbiter_identity is not None,
        )
        text_preaudit_transcript = _derive_correction_preaudit_transcript(
            transcript,
            proposals=proposals,
            remove_proposal_ids=tuple(proposal.id for proposal in proposals),
            audio_audits=audio_audits,
        )
        _validate_text_full_audit_attestation(
            text_preaudit_transcript,
            correction_audits,
            stored_corrector_execution_identity,
            references,
            retrievals,
        )
        assert stored_audio_auditor_identity is not None
        audio_preaudit_transcript = _derive_audio_preaudit_transcript(
            transcript,
            proposals=proposals,
            audio_audits=audio_audits,
        )
        _validate_audio_full_audit_attestation(
            transcript,
            audio_audits,
            stored_audio_auditor_identity,
            preaudit_transcript=audio_preaudit_transcript,
        )
        for audit in audio_audits:
            request_name = audit.request.uri.removeprefix("generation-artifact://")
            response_name = audit.response.uri.removeprefix("generation-artifact://")
            try:
                request_payload = json.loads(audio_execution_artifacts[request_name])
                response_payload = json.loads(audio_execution_artifacts[response_name])
            except (KeyError, UnicodeDecodeError, json.JSONDecodeError) as exc:
                raise GenerationIsolationError(
                    "stored Audio Audit request/response bytes are not replayable JSON"
                ) from exc
            if not isinstance(request_payload, dict) or (
                request_payload.get("work_packet_id") != audit.work_packet_id
                or request_payload.get("episode_id") != audit.episode_id
                or request_payload.get("generation_id") != audit.preaudit_generation_id
                or request_payload.get("canonical_content_hash")
                != audit.preaudit_content_hash
                or request_payload.get("recognition_evidence_hash") != audit.evidence_hash
                or request_payload.get("reference_evidence_hash")
                != audit.reference_evidence_hash
                or request_payload.get("policy_hash") != audit.policy_hash
                or request_payload.get("normalized_audio_hash")
                != audit.normalized_audio_hash
                or tuple(request_payload.get("target_span_ids", ()))
                != audit.target_span_ids
                or request_payload.get("adapter_identity_hash")
                != audit.adapter_identity_hash
            ):
                raise GenerationIsolationError(
                    "stored Audio Audit request bytes differ from their typed receipt"
                )
            if not isinstance(response_payload, dict) or (
                response_payload.get("work_packet_id") != audit.work_packet_id
                or response_payload.get("confidence") != audit.confidence
            ):
                raise GenerationIsolationError(
                    "stored Audio Audit response bytes differ from their typed receipt"
                )
            response_status = response_payload.get("verdict")
            if response_status == "refused":
                response_status = "unresolved"
            if response_status != audit.status:
                raise GenerationIsolationError(
                    "stored Audio Audit response verdict differs from its receipt"
                )
        proposal_by_id = {proposal.id: proposal for proposal in proposals}
        if len(proposal_by_id) != len(proposals):
            raise GenerationIsolationError("stored Correction Proposals have duplicate IDs")
        for audit in audio_audits:
            try:
                audited = tuple(proposal_by_id[item] for item in audit.proposal_ids)
            except KeyError as exc:
                raise GenerationIsolationError(
                    f"Audio Audit cites unknown Proposal {exc.args[0]!r}"
                ) from exc
            if audit.proposal_set_hash != hash_object(audited):
                raise GenerationIsolationError("Audio Audit proposal set hash mismatch")
            if any(
                proposal.evidence_basis not in {"audio", "audio_and_reference"}
                for proposal in audited
            ):
                raise GenerationIsolationError(
                    "Audio Audit receipt cites a non-audio Correction Proposal"
                )
        arbitration_by_id = {item.id: item for item in arbitration_receipts}
        if len(arbitration_by_id) != len(arbitration_receipts):
            raise GenerationIsolationError("stored Arbitration Receipt IDs are not unique")
        for arbitration in arbitration_receipts:
            try:
                proposal = proposal_by_id[arbitration.proposal_id]
            except KeyError as exc:
                raise GenerationIsolationError(
                    f"Arbitration cites unknown Proposal {exc.args[0]!r}"
                ) from exc
            if (
                arbitration.proposal_hash != hash_object(proposal)
                or arbitration.episode_id != transcript.episode_id
                or arbitration.generation_id != transcript.generation_id
                or arbitration.canonical_content_hash != transcript.content_hash
                or arbitration.evidence_hash != transcript.evidence_hash
                or arbitration.reference_evidence_hash
                != transcript.reference_evidence_hash
                or arbitration.policy_hash != transcript.policy_hash
                or arbitration.normalized_audio_hash != transcript.normalized_audio_hash
                or stored_arbiter_identity is None
                or arbitration.adapter_identity_hash
                != stored_arbiter_identity.content_hash
                or arbitration.adapter_name != stored_arbiter_identity.adapter_name
                or arbitration.adapter_version != stored_arbiter_identity.adapter_version
                or arbitration.model != stored_arbiter_identity.model
                or arbitration.model_version != stored_arbiter_identity.model_version
                or arbitration.runtime_hash != stored_arbiter_identity.runtime_hash
                or arbitration.adapter_code_hash
                != stored_arbiter_identity.adapter_code_hash
                or arbitration.config_hash != stored_arbiter_identity.config_hash
            ):
                raise GenerationIsolationError(
                    "stored Arbitration Receipt crossed proposal, truth, or Adapter identity"
                )
            request_name = arbitration.request.uri.removeprefix(
                "generation-artifact://"
            )
            response_name = arbitration.response.uri.removeprefix(
                "generation-artifact://"
            )
            try:
                request_payload = json.loads(audio_execution_artifacts[request_name])
                response_payload = json.loads(audio_execution_artifacts[response_name])
            except (KeyError, UnicodeDecodeError, json.JSONDecodeError) as exc:
                raise GenerationIsolationError(
                    "stored Arbitration request/response bytes are not replayable JSON"
                ) from exc
            if not isinstance(request_payload, dict) or (
                request_payload.get("work_packet_id") != arbitration.work_packet_id
                or request_payload.get("episode_id") != arbitration.episode_id
                or request_payload.get("generation_id") != arbitration.generation_id
                or request_payload.get("canonical_content_hash")
                != arbitration.canonical_content_hash
                or request_payload.get("recognition_evidence_hash")
                != arbitration.evidence_hash
                or request_payload.get("reference_evidence_hash")
                != arbitration.reference_evidence_hash
                or request_payload.get("policy_hash") != arbitration.policy_hash
                or request_payload.get("normalized_audio_hash")
                != arbitration.normalized_audio_hash
                or request_payload.get("adapter_identity_hash")
                != arbitration.adapter_identity_hash
                or not isinstance(request_payload.get("proposal"), dict)
                or request_payload["proposal"].get("id") != arbitration.proposal_id
            ):
                raise GenerationIsolationError(
                    "stored Arbitration request bytes differ from their typed receipt"
                )
            response_status = None
            if isinstance(response_payload, dict):
                response_status = {
                    "accept_candidate": "accepted",
                    "keep_observed": "rejected",
                    "unresolved": "unresolved",
                    "refused": "unresolved",
                }.get(response_payload.get("verdict"), response_payload.get("status"))
            if not isinstance(response_payload, dict) or (
                response_payload.get("work_packet_id") != arbitration.work_packet_id
                or response_status != arbitration.status
                or response_payload.get("selected_text") != arbitration.selected_text
                or response_payload.get("confidence") != arbitration.confidence
                or response_payload.get("rationale") != arbitration.rationale
            ):
                raise GenerationIsolationError(
                    "stored Arbitration response bytes differ from its typed receipt"
                )
        self._verify_audio_audit_replays(
            transcript=transcript,
            normalization_receipt=receipt,
            evidence=evidence,
            references=references,
            retrievals=retrievals,
            proposals=proposals,
            receipts=audio_audits,
            artifacts=audio_execution_artifacts,
            context="stored Generation",
        )
        self._verify_arbitration_replays(
            transcript=transcript,
            normalization_receipt=receipt,
            references=references,
            retrievals=retrievals,
            proposals=proposals,
            receipts=arbitration_receipts,
            artifacts=audio_execution_artifacts,
            context="stored Generation",
        )
        for audit in correction_audits:
            try:
                audited = tuple(proposal_by_id[item] for item in audit.proposal_ids)
            except KeyError as exc:
                raise GenerationIsolationError(
                    f"Correction Audit cites unknown Proposal {exc.args[0]!r}"
                ) from exc
            if audit.proposal_set_hash != hash_object(audited):
                raise GenerationIsolationError("Correction Audit proposal set hash mismatch")
            if (
                audit.episode_id != transcript.episode_id
                or audit.evidence_hash != transcript.evidence_hash
                or audit.reference_evidence_hash != transcript.reference_evidence_hash
                or audit.policy_hash != transcript.policy_hash
                or audit.adapter_identity_hash
                != stored_corrector_execution_identity.content_hash
                or audit.adapter_name != stored_corrector_execution_identity.adapter_name
                or audit.adapter_version
                != stored_corrector_execution_identity.adapter_version
                or audit.model != stored_corrector_execution_identity.model
                or audit.model_version != stored_corrector_execution_identity.model_version
                or audit.runtime_hash != stored_corrector_execution_identity.runtime_hash
                or audit.adapter_code_hash
                != stored_corrector_execution_identity.adapter_code_hash
                or audit.config_hash != stored_corrector_execution_identity.config_hash
                or audit.execution_mode
                != stored_corrector_execution_identity.execution_mode
            ):
                raise GenerationIsolationError(
                    "Correction Audit immutable input or executable identity mismatch"
                )
        for audit in targeted_correction_audits:
            try:
                audited = tuple(proposal_by_id[item] for item in audit.proposal_ids)
            except KeyError as exc:
                raise GenerationIsolationError(
                    f"Targeted Correction Audit cites unknown Proposal {exc.args[0]!r}"
                ) from exc
            if audit.proposal_set_hash != hash_object(audited):
                raise GenerationIsolationError(
                    "Targeted Correction Audit proposal set hash mismatch"
                )
            if (
                audit.evidence_hash != transcript.evidence_hash
                or audit.policy_hash != transcript.policy_hash
                or audit.reference_evidence_hash != transcript.reference_evidence_hash
            ):
                raise GenerationIsolationError(
                    "Targeted Correction Audit immutable input mismatch"
                )
            if (
                audit.adapter_identity_hash
                != stored_corrector_execution_identity.content_hash
                or audit.adapter_name != stored_corrector_execution_identity.adapter_name
                or audit.adapter_version
                != stored_corrector_execution_identity.adapter_version
                or audit.model != stored_corrector_execution_identity.model
                or audit.model_version != stored_corrector_execution_identity.model_version
                or audit.runtime_hash != stored_corrector_execution_identity.runtime_hash
                or audit.adapter_code_hash
                != stored_corrector_execution_identity.adapter_code_hash
                or audit.config_hash != stored_corrector_execution_identity.config_hash
                or audit.execution_mode
                != stored_corrector_execution_identity.execution_mode
            ):
                raise GenerationIsolationError(
                    "Targeted Correction Audit Adapter identity mismatch"
                )
        self._verify_correction_audit_replays(
            transcript=transcript,
            evidence=evidence,
            references=references,
            proposals=proposals,
            correction_audits=correction_audits,
            targeted_correction_audits=targeted_correction_audits,
            audio_audits=audio_audits,
            artifacts=correction_execution_artifacts,
            context="stored Generation",
        )
        expected_inputs = {
            "source_audio": transcript.source_audio_hash,
            "normalized_audio": transcript.normalized_audio_hash,
            "normalization_receipt": transcript.normalization_receipt_hash,
            "recognition_request": recognition_request_artifact.content_hash,
            "recognition_independence": (
                recognition_independence.content_hash
                if recognition_independence is not None
                else hash_object(None)
            ),
            "recognition_evidence": transcript.evidence_hash,
            "speech_coverage_receipt": (
                speech_coverage_receipt_content_hash(speech_coverage_receipt)
                if speech_coverage_receipt is not None
                else hash_object(None)
            ),
            "speaker_base_recognition_evidence": hash_object(
                speaker_base_evidence
            ),
            "speaker_attribution_provenance": hash_object(speaker_provenance),
            "ledger_head": transcript.ledger_hash,
            "reference_enrollments": hash_object(enrolled_artifacts),
            "reference_retrieval_policy": hash_object(reference_retrieval_policy),
            "references": transcript.reference_evidence_hash,
        }
        if transcript.orthographic_projection_evidence_hash is not None:
            expected_inputs["orthographic_projection_evidence"] = (
                transcript.orthographic_projection_evidence_hash
            )
        if manifest.input_artifact_hashes != expected_inputs:
            raise GenerationIsolationError("Generation Manifest input lineage mismatch")
        if manifest.policy_hash != transcript.policy_hash:
            raise GenerationIsolationError("Generation Manifest policy mismatch")
        expected_adapter_hash = hash_object(
            {
                "recognizers": stored_recognizer_identities,
                "speaker_base_recognizers": tuple(
                    (item.adapter, item.model, item.config_hash)
                    for item in speaker_base_evidence
                ),
                "corrector": stored_corrector_identity,
                "corrector_execution": stored_corrector_execution_identity,
                "audio_auditor": stored_audio_auditor_identity,
                "speech_coverage_analyzer": stored_speech_coverage_identity,
                "arbiter": stored_arbiter_identity,
                "reference_retriever": stored_reference_identity,
                "speaker_attributor": stored_speaker_identity,
                "speaker_provenance": speaker_provenance,
            }
        )
        if manifest.adapter_hash != expected_adapter_hash:
            raise GenerationIsolationError("Generation Manifest Adapter provenance mismatch")
        expected_stage_hashes = {
            "adapter_identities": hash_object(
                {
                    "recognizers": stored_recognizer_identities,
                    "corrector": stored_corrector_identity,
                    "corrector_execution": stored_corrector_execution_identity,
                    "audio_auditor": stored_audio_auditor_identity,
                    "speech_coverage_analyzer": stored_speech_coverage_identity,
                    "arbiter": stored_arbiter_identity,
                    "reference_retriever": stored_reference_identity,
                    "speaker_attributor": stored_speaker_identity,
                }
            ),
            "normalization_receipt": hash_object(receipt),
            "recognition_request": recognition_request_artifact.content_hash,
            "recognition_independence": (
                recognition_independence.content_hash
                if recognition_independence is not None
                else hash_object(None)
            ),
            "audio_artifacts": hash_object(audio_artifacts),
            "recognition_evidence": hash_object(evidence),
            "speech_coverage_receipt": hash_object(speech_coverage_receipt),
            "speech_coverage_raw_artifacts": hash_object(
                tuple(
                    (name, sha256_bytes(payload))
                    for name, payload in sorted(speech_coverage_raw_artifacts.items())
                )
            ),
            "speaker_base_recognition_evidence": hash_object(
                speaker_base_evidence
            ),
            "speaker_attribution_provenance": hash_object(speaker_provenance),
            "reference_enrollments": hash_object(enrolled_artifacts),
            "reference_retrieval_policy": hash_object(reference_retrieval_policy),
            "reference_evidence": reference_evidence_set_hash(references),
            "reference_source_snapshots": hash_object(
                tuple(
                    (name, sha256_bytes(payload))
                    for name, payload in sorted(reference_source_snapshots.items())
                )
            ),
            "reference_extraction_snapshots": hash_object(
                tuple(
                    (name, sha256_bytes(payload))
                    for name, payload in sorted(
                        reference_extraction_snapshots.items()
                    )
                )
            ),
            "reference_retrieval_receipts": hash_object(retrievals),
            "correction_audit_receipts": hash_object(correction_audits),
            "targeted_correction_audit_receipts": hash_object(
                targeted_correction_audits
            ),
            "correction_execution_artifacts": hash_object(
                tuple(
                    (name, sha256_bytes(payload))
                    for name, payload in sorted(correction_execution_artifacts.items())
                )
            ),
            "audio_audit_receipts": hash_object(audio_audits),
            "arbitration_receipts": hash_object(arbitration_receipts),
            "audio_execution_artifacts": hash_object(
                tuple(
                    (name, sha256_bytes(payload))
                    for name, payload in sorted(audio_execution_artifacts.items())
                )
            ),
            "correction_proposals": hash_object(proposals),
            "canonical_transcript": hash_object(transcript),
            "risks": hash_object(tuple(_risk_payload(risk) for risk in risks)),
            "raw_recognition_outputs": hash_object(
                tuple(
                    (name, sha256_bytes(payload))
                    for name, payload in sorted(raw_outputs.items())
                )
            ),
        }
        if transcript.orthographic_projection_evidence_hash is not None:
            expected_stage_hashes["orthographic_projection_evidence"] = hash_object(
                orthographic_projections
            )
        if manifest.stage_artifact_hashes != expected_stage_hashes:
            raise GenerationIsolationError("Generation Manifest stage hashes mismatch")
        fixed_names = {
            _RECEIPT,
            _AUDIO_ARTIFACTS,
            _EVIDENCE,
            _RECOGNITION_REQUEST,
            _RECOGNITION_INDEPENDENCE,
            _BASE_PRIMARY_EVIDENCE,
            _SPEAKER_PROVENANCE,
            _REFERENCE_ENROLLMENTS,
            _REFERENCE_RETRIEVAL_POLICY,
            _SPEECH_COVERAGE,
            _REFERENCES,
            _RETRIEVALS,
            _PROPOSALS,
            _CORRECTION_AUDITS,
            _TARGETED_CORRECTION_AUDITS,
            _AUDIO_AUDITS,
            _ARBITRATIONS,
            _ADAPTER_IDENTITIES,
            _CANONICAL,
            _RISKS,
            _MANIFEST,
        }
        if orthographic_projections:
            fixed_names.add(_ORTHOGRAPHIC_PROJECTIONS)
        expected_artifact_names = (
            fixed_names
            | set(raw_outputs)
            | set(orthographic_raw_outputs)
            | set(speech_coverage_raw_artifacts)
            | set(reference_source_snapshots)
            | set(reference_extraction_snapshots)
            | set(correction_execution_artifacts)
            | set(audio_execution_artifacts)
        )
        if set(record.artifact_hashes) != expected_artifact_names:
            raise GenerationIsolationError(
                "legacy Generation artifact profile is incomplete or extra"
            )
        _validate_canonical_acceptance_identity(transcript)
        result = CanonicalBuildResult(
            outcome="accepted" if transcript.status == "accepted" else "needs_review",
            transcript=transcript,
            candidates=(),
            risks=risks,
            reference_evidence_hash=transcript.reference_evidence_hash,
            generation_warnings=transcript.generation_warnings,
        )
        assert stored_corrector_identity is not None
        assert stored_corrector_execution_identity is not None
        assert stored_audio_auditor_identity is not None

        def verified_set(names: set[str] | tuple[str, ...]) -> VerifiedArtifactSet:
            return VerifiedArtifactSet(
                tuple(verified_artifact(name) for name in sorted(names))
            )

        def loaded_adapter(identity: AdapterIdentity | None) -> LoadedAdapterIdentity | None:
            return (
                None
                if identity is None
                else LoadedAdapterIdentity(
                    name=identity.name,
                    version=identity.version,
                    config_hash=identity.config_hash,
                    execution_mode=identity.execution_mode,
                )
            )

        loaded_speech_identity = (
            None
            if stored_speech_coverage_identity is None
            else LoadedSpeechCoverageAdapterIdentity(
                adapter_name=stored_speech_coverage_identity["adapter_name"],
                adapter_version=stored_speech_coverage_identity["adapter_version"],
                config_hash=stored_speech_coverage_identity["config_hash"],
                code_hash=stored_speech_coverage_identity["code_hash"],
                runtime_hash=stored_speech_coverage_identity["runtime_hash"],
            )
        )
        targeted_snapshots = tuple(
            LoadedTargetedCorrectionAuditReceipt(
                schema_version=item.schema_version,
                mode=item.mode,
                generation_id=item.generation_id,
                canonical_content_hash=item.canonical_content_hash,
                evidence_hash=item.evidence_hash,
                policy_hash=item.policy_hash,
                reference_evidence_hash=item.reference_evidence_hash,
                available_reference_evidence_ids=(
                    item.available_reference_evidence_ids
                ),
                available_reference_evidence_hash=(
                    item.available_reference_evidence_hash
                ),
                presented_reference_evidence_ids=(
                    item.presented_reference_evidence_ids
                ),
                presented_reference_evidence_hash=(
                    item.presented_reference_evidence_hash
                ),
                work_packet_id=item.work_packet_id,
                request=item.request,
                request_hash=item.request_hash,
                target_span_ids=item.target_span_ids,
                response=item.response,
                response_hash=item.response_hash,
                adapter_name=item.adapter_name,
                adapter_version=item.adapter_version,
                model=item.model,
                model_version=item.model_version,
                runtime_hash=item.runtime_hash,
                adapter_code_hash=item.adapter_code_hash,
                config_hash=item.config_hash,
                execution_mode=item.execution_mode,
                adapter_identity_hash=item.adapter_identity_hash,
                proposal_ids=item.proposal_ids,
                proposal_set_hash=item.proposal_set_hash,
            )
            for item in targeted_correction_audits
        )
        loaded = LoadedGenerationState(
            result=result,
            storage=LoadedStorageSnapshot.capture(
                generation_id=record.generation_id,
                artifact_set_hash=record.artifact_set_hash,
                manifest=manifest,
                artifact_hashes=record.artifact_hashes,
                directory=record.directory,
            ),
            normalization=LoadedNormalizationState(
                receipt=receipt,
                source_audio_path=source_audio,
                normalized_audio_path=normalized_audio,
                receipt_artifact=verified_artifact(_RECEIPT),
                audio_bindings_artifact=verified_artifact(_AUDIO_ARTIFACTS),
            ),
            recognition=LoadedRecognitionState(
                request_artifact=recognition_request_artifact,
                request=recognition_request,
                independence=recognition_independence,
                evidence=evidence,
                speaker_base_evidence=speaker_base_evidence,
                speaker_provenance=speaker_provenance,
                orthographic_projections=orthographic_projections,
                request_bytes=verified_artifact(_RECOGNITION_REQUEST),
                independence_bytes=verified_artifact(_RECOGNITION_INDEPENDENCE),
                evidence_bytes=verified_artifact(_EVIDENCE),
                speaker_base_evidence_bytes=verified_artifact(_BASE_PRIMARY_EVIDENCE),
                speaker_provenance_bytes=verified_artifact(_SPEAKER_PROVENANCE),
                orthographic_projection_bytes=(
                    verified_artifact(_ORTHOGRAPHIC_PROJECTIONS)
                    if orthographic_projections
                    else None
                ),
                raw_outputs=verified_set(set(raw_outputs)),
                orthographic_raw_outputs=verified_set(set(orthographic_raw_outputs)),
            ),
            references=LoadedReferenceState(
                enrollments=enrolled_artifacts,
                evidence=references,
                retrievals=retrievals,
                retrieval_policy=reference_retrieval_policy,
                enrollments_bytes=verified_artifact(_REFERENCE_ENROLLMENTS),
                evidence_bytes=verified_artifact(_REFERENCES),
                retrievals_bytes=verified_artifact(_RETRIEVALS),
                retrieval_policy_bytes=verified_artifact(_REFERENCE_RETRIEVAL_POLICY),
                source_snapshots=verified_set(set(reference_source_snapshots)),
                extraction_snapshots=verified_set(set(reference_extraction_snapshots)),
            ),
            speech_coverage=LoadedSpeechCoverageState(
                receipt=speech_coverage_receipt,
                receipt_bytes=verified_artifact(_SPEECH_COVERAGE),
                raw_outputs=verified_set(set(speech_coverage_raw_artifacts)),
            ),
            audit=LoadedLegacyAuditState(
                proposals=proposals,
                correction_audits=correction_audits,
                targeted_correction_audits=targeted_snapshots,
                audio_audits=audio_audits,
                arbitrations=arbitration_receipts,
                collection_artifacts=LoadedLegacyCollectionArtifacts(
                    proposals=verified_artifact(_PROPOSALS),
                    correction_audits=verified_artifact(_CORRECTION_AUDITS),
                    targeted_correction_audits=verified_artifact(
                        _TARGETED_CORRECTION_AUDITS
                    ),
                    audio_audits=verified_artifact(_AUDIO_AUDITS),
                    arbitrations=verified_artifact(_ARBITRATIONS),
                ),
                correction_execution_artifacts=verified_set(
                    set(correction_execution_artifacts)
                ),
                audio_execution_artifacts=verified_set(
                    set(audio_execution_artifacts)
                ),
                runtime=LoadedLegacyRuntimeState(
                    common=LoadedCommonRuntimeState(
                        recognizers=stored_recognizer_identities,
                        speech_coverage_analyzer=loaded_speech_identity,
                        speaker_attributor=loaded_adapter(stored_speaker_identity),
                        reference_retriever=loaded_adapter(stored_reference_identity),
                    ),
                    corrector=loaded_adapter(stored_corrector_identity),  # type: ignore[arg-type]
                    corrector_execution=stored_corrector_execution_identity,
                    audio_auditor=stored_audio_auditor_identity,
                    arbiter=stored_arbiter_identity,
                    identities_artifact=verified_artifact(_ADAPTER_IDENTITIES),
                ),
            ),
        )
        if require_active:
            self._validate_active_ledger_lineage(loaded)
        return loaded

    def _commit_projection(
        self,
        *,
        projection_id: str,
        generation_id: str,
        projection: SubtitleProjection,
        manifest: ProjectionManifest,
        report: QualityReport,
        units: tuple[SemanticUnit, ...],
        profile: ProjectionProfile,
        srt: str,
        sidecar: str,
        semantic_units_hash: str,
        semantic_analyzer_hash: str,
        semantic_execution_receipts_hash: str,
        boundary_constraints_hash: str,
        boundary_constraints: BoundaryConstraintReceipt,
        semantic_run: SemanticRunResult,
    ) -> None:
        directory = self.store.root / "projections" / projection_id
        payloads = {
            _PROJECTION: _model_bytes(projection),
            _PROJECTION_MANIFEST: _model_bytes(manifest),
            _QUALITY: _model_bytes(report),
            _PROFILE: _model_bytes(profile),
            _SEMANTIC: json.dumps(
                [unit.model_dump(mode="json") for unit in units],
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            ).encode("utf-8"),
            _SEMANTIC_ADAPTER: canonical_json_bytes(
                semantic_run.execution_receipts[0].adapter_identity
            ),
            _SEMANTIC_RECEIPTS: canonical_json_bytes(semantic_run.execution_receipts),
            _BOUNDARY_CONSTRAINTS: _model_bytes(boundary_constraints),
            _SRT: srt.encode("utf-8"),
            _SIDECAR: sidecar.encode("utf-8"),
            **semantic_run.artifacts,
        }
        projection_record = {
            "schema_version": 3,
            "projection_id": projection_id,
            "generation_id": generation_id,
            "semantic_units_hash": semantic_units_hash,
            "semantic_analyzer_hash": semantic_analyzer_hash,
            "semantic_execution_receipts_hash": semantic_execution_receipts_hash,
            "boundary_constraints_hash": boundary_constraints_hash,
            "artifact_hashes": {
                name: sha256_bytes(payload) for name, payload in sorted(payloads.items())
            },
        }
        projection_record["record_hash"] = hash_object(projection_record)
        payloads["projection_record.json"] = json.dumps(
            projection_record,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")

        if directory.exists():
            self._verify_projection_directory(
                projection_id, expected_generation_id=generation_id, expected=payloads
            )
            return
        projections_root = directory.parent
        projections_root.mkdir(parents=True, exist_ok=True)
        temporary = Path(
            tempfile.mkdtemp(prefix=f".{projection_id[:24]}-", dir=projections_root)
        )
        try:
            for name, payload in payloads.items():
                path = temporary / name
                path.parent.mkdir(parents=True, exist_ok=True)
                with path.open("wb") as stream:
                    stream.write(payload)
                    stream.flush()
                    os.fsync(stream.fileno())
            try:
                os.replace(temporary, directory)
            except OSError:
                if directory.exists():
                    self._verify_projection_directory(
                        projection_id,
                        expected_generation_id=generation_id,
                        expected=payloads,
                    )
                    shutil.rmtree(temporary, ignore_errors=True)
                    return
                raise
        except Exception:
            if temporary.exists():
                shutil.rmtree(temporary, ignore_errors=True)
            raise
        self._verify_projection_directory(
            projection_id, expected_generation_id=generation_id, expected=payloads
        )

    def _verify_projection_directory(
        self,
        projection_id: str,
        *,
        expected_generation_id: str | None = None,
        expected: dict[str, bytes] | None = None,
    ) -> dict[str, bytes]:
        if not _PROJECTION_ID_RE.fullmatch(projection_id):
            raise ValueError(f"invalid projection id: {projection_id!r}")
        directory = self.store.root / "projections" / projection_id
        record_path = directory / "projection_record.json"
        if not record_path.is_file():
            raise GenerationIsolationError(f"projection {projection_id} is incomplete")
        try:
            record_bytes = record_path.read_bytes()
            record = json.loads(record_bytes)
        except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise GenerationIsolationError(f"projection {projection_id} record is invalid") from exc
        record_hash = record.pop("record_hash", None)
        if record_hash != hash_object(record):
            raise GenerationIsolationError(f"projection {projection_id} record hash mismatch")
        if record_bytes != canonical_json_bytes({**record, "record_hash": record_hash}):
            raise GenerationIsolationError(
                f"projection {projection_id} record bytes are not canonical"
            )
        if record.get("projection_id") != projection_id:
            raise GenerationIsolationError("projection record identity mismatch")
        if (
            expected_generation_id is not None
            and record.get("generation_id") != expected_generation_id
        ):
            raise GenerationIsolationError("projection crossed Canonical Generation")
        if record.get("schema_version") != 3:
            raise GenerationIsolationError("projection record schema version is unsupported")
        result: dict[str, bytes] = {}
        required_artifacts = {
            _PROJECTION,
            _PROJECTION_MANIFEST,
            _QUALITY,
            _PROFILE,
            _SEMANTIC,
            _SEMANTIC_ADAPTER,
            _SEMANTIC_RECEIPTS,
            _BOUNDARY_CONSTRAINTS,
            _SRT,
            _SIDECAR,
        }
        artifact_hashes = record.get("artifact_hashes")
        if not isinstance(artifact_hashes, dict) or not required_artifacts.issubset(
            artifact_hashes
        ):
            raise GenerationIsolationError("projection record artifact set is incomplete")
        semantic_artifacts = set(artifact_hashes) - required_artifacts
        if not semantic_artifacts or any(
            not name.startswith(("semantic/requests/", "semantic/responses/"))
            for name in semantic_artifacts
        ):
            raise GenerationIsolationError("projection Semantic execution artifact set is invalid")
        for name, declared_hash in artifact_hashes.items():
            path = directory / name
            if not path.is_file():
                raise GenerationIsolationError(f"projection artifact missing: {name}")
            payload = path.read_bytes()
            if sha256_bytes(payload) != declared_hash:
                raise GenerationIsolationError(f"projection artifact hash mismatch: {name}")
            result[name] = payload

        try:
            projection = SubtitleProjection.model_validate_json(result[_PROJECTION])
            manifest = ProjectionManifest.model_validate_json(result[_PROJECTION_MANIFEST])
            report = QualityReport.model_validate_json(result[_QUALITY])
            profile = ProjectionProfile.model_validate_json(result[_PROFILE])
            units = tuple(
                SemanticUnit.model_validate(item)
                for item in json.loads(result[_SEMANTIC])
            )
            semantic_identity = _semantic_identity_from_payload(
                json.loads(result[_SEMANTIC_ADAPTER])
            )
            semantic_receipts = tuple(
                _semantic_execution_receipt_from_payload(item)
                for item in json.loads(result[_SEMANTIC_RECEIPTS])
            )
            boundary_constraints = BoundaryConstraintReceipt.model_validate_json(
                result[_BOUNDARY_CONSTRAINTS]
            )
        except (TypeError, ValueError, json.JSONDecodeError) as exc:
            raise GenerationIsolationError(
                f"projection {projection_id} contains an invalid typed artifact"
            ) from exc
        generation_id = record.get("generation_id")
        if not isinstance(generation_id, str):
            raise GenerationIsolationError("projection record lacks a Generation ID")
        loaded = self._load_generation(generation_id, require_active=False)
        canonical = loaded.result.transcript
        receipt = loaded.normalization.receipt
        if canonical.status != "accepted":
            raise GenerationIsolationError("projection targets a non-accepted Canonical Transcript")
        if receipt.normalized_duration_ms is None:
            raise GenerationIsolationError("projection lineage lacks normalized audio duration")
        typed_units = _validate_semantic_partition(canonical, units)
        semantic_units_hash = hash_object(typed_units)
        semantic_analyzer_hash = semantic_identity.content_hash
        semantic_receipts_hash = hash_object(semantic_receipts)
        boundary_constraints_hash = hash_object(boundary_constraints)
        if (
            record.get("semantic_units_hash") != semantic_units_hash
            or record.get("semantic_analyzer_hash") != semantic_analyzer_hash
            or record.get("semantic_execution_receipts_hash") != semantic_receipts_hash
            or record.get("boundary_constraints_hash") != boundary_constraints_hash
        ):
            raise GenerationIsolationError("projection semantic provenance hash mismatch")
        if semantic_identity != self._semantic_execution_identity:
            raise GenerationIsolationError(
                "projection Semantic Analyzer executable identity drifted"
            )
        request_payloads: list[bytes] = []
        response_payloads: list[bytes] = []
        for receipt_item in semantic_receipts:
            for artifact, output in (
                (receipt_item.request, request_payloads),
                (receipt_item.response, response_payloads),
            ):
                artifact_name = artifact.uri.removeprefix("projection-artifact://")
                payload = result.get(artifact_name)
                if payload is None:
                    raise GenerationIsolationError(
                        f"projection Semantic execution artifact missing: {artifact_name}"
                    )
                output.append(payload)
        protected_ranges = _semantic_protected_ranges(
            canonical,
            loaded.references.retrieval_policy,
            loaded.references.retrievals,
        )
        semantic_request = SemanticAnalysisRequest(
            transcript=canonical,
            language=profile.language,
            policy_hash=canonical.policy_hash,
            protected_ranges=protected_ranges,
        )
        try:
            replayed_semantic = self._semantic_analyzer.replay(
                semantic_request,
                units=typed_units,
                execution_receipts=semantic_receipts,
                request_bytes=tuple(request_payloads),
                response_bytes=tuple(response_payloads),
            )
        except (AdapterIntegrityError, TypeError, ValueError) as exc:
            raise GenerationIsolationError(
                "projection Semantic execution is not replayable"
            ) from exc
        if replayed_semantic.units != typed_units:
            raise GenerationIsolationError("projection Semantic replay changed its units")
        try:
            assert_boundary_constraint_receipt(
                boundary_constraints,
                canonical,
                typed_units,
                profile,
                protected_ranges=protected_ranges,
                semantic_adapter_identity_hash=semantic_analyzer_hash,
            )
        except BoundaryConstraintError as exc:
            raise GenerationIsolationError(
                "projection Boundary Constraint Receipt is not reproducible"
            ) from exc
        mandatory_cue_boundaries: tuple[int, ...] = ()
        allowed_cue_boundaries: tuple[int, ...] | None = None
        authoritative_cues: tuple[tuple[int, int, int, int], ...] = ()
        memo_boundary_authority_hash: str | None = None
        if self._memo_boundary_authority_factory is not None:
            authority = self._memo_boundary_authority_factory(
                loaded.recognition.evidence[0]
            )
            memo_boundary_authority_hash = authority.content_hash
            mandatory_cue_boundaries, authoritative_cues = authority.projection_contract(
                canonical
            )
            allowed_cue_boundaries = mandatory_cue_boundaries
        derived = project_semantic_units(
            canonical.tokens,
            typed_units,
            profile,
            episode_id=canonical.episode_id,
            generation_id=canonical.generation_id,
            audio_start_ms=0,
            audio_end_ms=receipt.normalized_duration_ms,
            canonical_spans=canonical.spans,
            boundary_constraints=boundary_constraints,
            mandatory_cue_boundaries=mandatory_cue_boundaries,
            allowed_cue_boundaries=allowed_cue_boundaries,
            authoritative_cues=authoritative_cues,
        )
        if derived.projection != projection:
            raise GenerationIsolationError("projection does not derive from Canonical truth")
        derived_report = assert_projection_quality(
            canonical.tokens,
            typed_units,
            projection,
            profile,
            audio_start_ms=0,
            audio_end_ms=receipt.normalized_duration_ms,
            canonical_spans=canonical.spans,
            boundary_constraints=boundary_constraints,
        )
        if derived_report != report:
            raise GenerationIsolationError("projection Quality Report is not reproducible")
        if (
            render_srt(derived).encode("utf-8") != result[_SRT]
            or render_sidecar_json(derived).encode("utf-8") != result[_SIDECAR]
        ):
            raise GenerationIsolationError(
                "rendered projection artifacts are not reproducible"
            )
        output_lineage = {
            "projection": hash_object(projection),
            "quality_report": hash_object(report),
            "semantic_units": semantic_units_hash,
            "semantic_analyzer": semantic_analyzer_hash,
            "semantic_execution_receipts": semantic_receipts_hash,
            "boundary_constraints": boundary_constraints_hash,
            "sidecar": sha256_bytes(result[_SIDECAR]),
            "srt": sha256_bytes(result[_SRT]),
        }
        if memo_boundary_authority_hash is not None:
            output_lineage["memo_boundary_authority"] = memo_boundary_authority_hash
        output_hash = hash_object(output_lineage)
        expected_manifest = ProjectionManifest(
            episode_id=canonical.episode_id,
            generation_id=canonical.generation_id,
            canonical_hash=canonical.content_hash,
            profile_hash=hash_object(profile),
            token_sequence_hash=token_sequence_hash(
                [token.id for token in canonical.tokens]
            ),
            output_hash=output_hash,
        )
        if manifest != expected_manifest:
            raise GenerationIsolationError("Projection Manifest lineage is not reproducible")
        derived_projection_id = "projection-" + hash_object(
            {
                "projection": projection,
                "manifest": manifest,
                "quality": report,
                "semantic_units_hash": semantic_units_hash,
                "semantic_analyzer_hash": semantic_analyzer_hash,
                "semantic_execution_receipts_hash": semantic_receipts_hash,
                "boundary_constraints_hash": boundary_constraints_hash,
            }
        )
        if derived_projection_id != projection_id:
            raise GenerationIsolationError(
                "projection ID is not content-addressed to its artifacts"
            )
        if expected is not None:
            for name, payload in expected.items():
                actual = (
                    record_path.read_bytes()
                    if name == "projection_record.json"
                    else result.get(name)
                )
                if actual != payload:
                    raise GenerationIsolationError(
                        f"projection {projection_id} exists with conflicting bytes"
                    )
        return result


__all__ = [
    "AcceptedGeneration",
    "CreateRequest",
    "GenerationOutcome",
    "Interrupted",
    "ModuleInvariantError",
    "NeedsReview",
    "PodcastSubtitleV2",
    "ProjectRequest",
    "ReferenceEnrollment",
    "ResolveNativeRequest",
    "ResolveRequest",
    "VerifiedProjection",
]
