"""Deeply typed, immutable in-memory view of one verified Generation.

These value objects are deliberately separate from the persisted artifact
schemas.  Loaders construct them only after verifying the store; constructing a
state neither serializes nor changes any Generation artifact.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath
from typing import Literal

from shared.schemas.podcast_subtitles_v2 import (
    ArbitrationReceipt,
    ArtifactDigest,
    AudioAuditReceipt,
    AuditPlan,
    CandidateGroupSet,
    CandidateSignalSet,
    CorrectionAuditPolicySnapshot,
    CorrectionAuditReceipt,
    GenerationManifest,
    GenerationStatus,
    NormalizationReceipt,
    RecognitionEvidence,
    ReferenceArtifact,
    ReferenceEvidence,
    ReferenceRetrievalPolicySnapshot,
    ReferenceRetrievalReceipt,
    SpeechCoverageReceipt,
    normalization_receipt_content_hash,
    recognition_evidence_set_hash,
    reference_evidence_set_hash,
)
from shared.schemas.podcast_subtitles_v2_audio_audit import (
    AudioAuditAdapterIdentityV2,
    AudioAuditExecutionPolicyV2,
    AudioDiscoveredCandidateV2,
)
from shared.schemas.podcast_subtitles_v2_correction import (
    CorrectionAuditExecutionPlanV2,
    CorrectionAuditExecutionPolicyV2,
)
from shared.schemas.podcast_subtitles_v2_selective_audio import (
    ModalityAuditExecutionPlanV3,
    SelectiveAudioAuditRoundReceiptV3,
)
from shared.schemas.podcast_subtitles_v2_text_audit import (
    TextAuditAdapterIdentityV2,
    TextAuditExecutionPolicyV2,
    TextDiscoveredCandidateV2,
)

from .audio_audit_execution import AudioAuditRunResult
from .canonical import CanonicalBuildResult
from .correction_acceptance import (
    CorrectionAcceptancePolicyV2,
    CorrectionAcceptanceVerdictV2,
    HumanAudioReviewReceiptV2,
    HumanReferenceAdjudicationReceiptV2,
)
from .full_audit_attestation import (
    FullAuditAggregateAttestationV2,
    SelectiveAuditAggregateAttestationV3,
)
from .hashing import hash_object, sha256_bytes
from .native_resolution import (
    HumanOriginalConfirmationReceiptV2,
    NativeCorrectionDecisionV2,
    OriginalConfirmationAuthorizationV2,
    OriginalConfirmationPolicyV2,
)
from .orthographic_projection import (
    OrthographicProjectionEvidenceV1,
    orthographic_projection_evidence_set_hash,
)
from .ports import (
    AudioModelIdentity,
    CorrectionModelIdentity,
    CorrectionProposal,
    RecognitionModelIdentity,
    RecognitionRequest,
    SpeakerAttributionProvenance,
)
from .recognition_policy import RecognitionIndependenceReceiptV1
from .recognition_request import RecognitionRequestArtifactV1
from .reference_claims import ReferenceAuthorityProofV2
from .speech_coverage import verified_speech_coverage_hash
from .text_audit_execution import TextAuditRunResult

_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")


def _require_names(artifacts: VerifiedArtifactSet, expected: tuple[str, ...], label: str) -> None:
    if artifacts.names != tuple(sorted(set(expected))):
        raise ValueError(f"{label} exact artifact set is incomplete or extra")


@dataclass(frozen=True, slots=True)
class VerifiedArtifactBytes:
    """One relative artifact name bound to exact immutable bytes and digest."""

    name: str
    sha256: str
    payload: bytes

    def __post_init__(self) -> None:
        if (
            not isinstance(self.name, str)
            or not self.name.strip()
            or self.name != self.name.strip()
        ):
            raise ValueError("verified artifact name must be non-blank and trimmed")
        path = PurePosixPath(self.name)
        if (
            path.is_absolute()
            or "\\" in self.name
            or self.name != path.as_posix()
            or any(part in {"", ".", ".."} for part in path.parts)
        ):
            raise ValueError("verified artifact name must be a canonical relative POSIX path")
        if not isinstance(self.sha256, str) or not _SHA256_RE.fullmatch(self.sha256):
            raise ValueError("verified artifact sha256 must be lowercase SHA-256")
        if type(self.payload) is not bytes:
            raise TypeError("verified artifact payload requires exact immutable bytes")
        if sha256_bytes(self.payload) != self.sha256:
            raise ValueError("verified artifact payload digest mismatch")

    @property
    def size_bytes(self) -> int:
        return len(self.payload)


@dataclass(frozen=True, slots=True)
class VerifiedArtifactSet:
    """Canonical immutable collection of verified artifact bindings."""

    artifacts: tuple[VerifiedArtifactBytes, ...] = ()

    def __post_init__(self) -> None:
        if type(self.artifacts) is not tuple:
            raise TypeError("verified artifact set requires an immutable tuple")
        if any(not isinstance(item, VerifiedArtifactBytes) for item in self.artifacts):
            raise TypeError("verified artifact set contains an invalid binding")
        names = self.names
        if len(set(names)) != len(names):
            raise ValueError("verified artifact set contains a duplicate name")
        if names != tuple(sorted(names)):
            raise ValueError("verified artifact set must use canonical name order")

    @property
    def names(self) -> tuple[str, ...]:
        return tuple(item.name for item in self.artifacts)

    def require(self, name: str) -> VerifiedArtifactBytes:
        matches = tuple(item for item in self.artifacts if item.name == name)
        if len(matches) != 1:
            raise KeyError(name)
        return matches[0]


@dataclass(frozen=True, slots=True, order=True)
class ArtifactHashBinding:
    """Immutable replacement for a persisted artifact-name/hash mapping entry."""

    name: str
    sha256: str

    def __post_init__(self) -> None:
        if (
            not isinstance(self.name, str)
            or not self.name.strip()
            or self.name != self.name.strip()
        ):
            raise ValueError("artifact hash binding name must be non-blank and trimmed")
        if not isinstance(self.sha256, str) or not _SHA256_RE.fullmatch(self.sha256):
            raise ValueError("artifact hash binding sha256 must be lowercase SHA-256")


def _capture_hash_bindings(values: Mapping[str, str]) -> tuple[ArtifactHashBinding, ...]:
    return tuple(
        ArtifactHashBinding(name=name, sha256=digest) for name, digest in sorted(values.items())
    )


@dataclass(frozen=True, slots=True)
class LoadedManifestSnapshot:
    """Deeply immutable value copy of a verified Generation Manifest."""

    schema_version: int
    id: str
    episode_id: str
    generation_id: str
    parent_manifest_hash: str | None
    input_artifact_hashes: tuple[ArtifactHashBinding, ...]
    code_hash: str
    policy_hash: str
    adapter_hash: str
    stage_artifact_hashes: tuple[ArtifactHashBinding, ...]
    status: GenerationStatus

    @classmethod
    def capture(cls, manifest: GenerationManifest) -> LoadedManifestSnapshot:
        return cls(
            schema_version=manifest.schema_version,
            id=manifest.id,
            episode_id=manifest.episode_id,
            generation_id=manifest.generation_id,
            parent_manifest_hash=manifest.parent_manifest_hash,
            input_artifact_hashes=_capture_hash_bindings(manifest.input_artifact_hashes),
            code_hash=manifest.code_hash,
            policy_hash=manifest.policy_hash,
            adapter_hash=manifest.adapter_hash,
            stage_artifact_hashes=_capture_hash_bindings(manifest.stage_artifact_hashes),
            status=manifest.status,
        )

    def __post_init__(self) -> None:
        for label, bindings in (
            ("input", self.input_artifact_hashes),
            ("stage", self.stage_artifact_hashes),
        ):
            if type(bindings) is not tuple:
                raise TypeError(f"manifest {label} bindings require an immutable tuple")
            if bindings != tuple(sorted(bindings)):
                raise ValueError(f"manifest {label} bindings must use canonical name order")
            names = tuple(item.name for item in bindings)
            if len(set(names)) != len(names):
                raise ValueError(f"manifest {label} bindings contain a duplicate name")
        for label, digest in (
            ("code_hash", self.code_hash),
            ("policy_hash", self.policy_hash),
            ("adapter_hash", self.adapter_hash),
        ):
            if not _SHA256_RE.fullmatch(digest):
                raise ValueError(f"manifest {label} must be lowercase SHA-256")
        if self.parent_manifest_hash is not None and not _SHA256_RE.fullmatch(
            self.parent_manifest_hash
        ):
            raise ValueError("manifest parent hash must be lowercase SHA-256")

    def materialize(self) -> GenerationManifest:
        """Return a fresh persistence model without exposing mutable aliases."""

        return GenerationManifest(
            schema_version=self.schema_version,
            id=self.id,
            episode_id=self.episode_id,
            generation_id=self.generation_id,
            parent_manifest_hash=self.parent_manifest_hash,
            input_artifact_hashes={item.name: item.sha256 for item in self.input_artifact_hashes},
            code_hash=self.code_hash,
            policy_hash=self.policy_hash,
            adapter_hash=self.adapter_hash,
            stage_artifact_hashes={item.name: item.sha256 for item in self.stage_artifact_hashes},
            status=self.status,
        )


@dataclass(frozen=True, slots=True)
class LoadedStorageSnapshot:
    """Immutable store record with no aliases to ``StoredGeneration`` dictionaries."""

    generation_id: str
    artifact_set_hash: str
    manifest: LoadedManifestSnapshot
    artifact_hashes: tuple[ArtifactHashBinding, ...]
    directory: Path

    @classmethod
    def capture(
        cls,
        *,
        generation_id: str,
        artifact_set_hash: str,
        manifest: GenerationManifest,
        artifact_hashes: Mapping[str, str],
        directory: str | Path,
    ) -> LoadedStorageSnapshot:
        return cls(
            generation_id=generation_id,
            artifact_set_hash=artifact_set_hash,
            manifest=LoadedManifestSnapshot.capture(manifest),
            artifact_hashes=_capture_hash_bindings(artifact_hashes),
            directory=Path(directory),
        )

    def __post_init__(self) -> None:
        if self.generation_id != self.manifest.generation_id:
            raise ValueError("loaded storage and manifest Generation IDs differ")
        if not _SHA256_RE.fullmatch(self.artifact_set_hash):
            raise ValueError("loaded storage artifact-set hash must be lowercase SHA-256")
        if type(self.artifact_hashes) is not tuple:
            raise TypeError("loaded storage artifact hashes require an immutable tuple")
        if self.artifact_hashes != tuple(sorted(self.artifact_hashes)):
            raise ValueError("loaded storage artifact hashes must use canonical name order")
        names = tuple(item.name for item in self.artifact_hashes)
        if len(set(names)) != len(names):
            raise ValueError("loaded storage artifact hashes contain a duplicate name")
        object.__setattr__(self, "directory", Path(self.directory))

    def artifact_hash(self, name: str) -> str:
        matches = tuple(item.sha256 for item in self.artifact_hashes if item.name == name)
        if len(matches) != 1:
            raise KeyError(name)
        return matches[0]


@dataclass(frozen=True, slots=True)
class LoadedNormalizationState:
    receipt: NormalizationReceipt
    source_audio_path: Path
    normalized_audio_path: Path
    receipt_artifact: VerifiedArtifactBytes
    audio_bindings_artifact: VerifiedArtifactBytes

    def __post_init__(self) -> None:
        object.__setattr__(self, "source_audio_path", Path(self.source_audio_path))
        object.__setattr__(self, "normalized_audio_path", Path(self.normalized_audio_path))
        if self.receipt.status != "accepted":
            raise ValueError("loaded Normalization state requires an accepted receipt")
        if self.receipt_artifact.name != "normalization_receipt.json":
            raise ValueError("Normalization Receipt artifact has the wrong role name")
        if self.audio_bindings_artifact.name != "audio_artifacts.json":
            raise ValueError("Normalization audio binding artifact has the wrong role name")


@dataclass(frozen=True, slots=True)
class LoadedRecognitionState:
    request_artifact: RecognitionRequestArtifactV1
    request: RecognitionRequest
    independence: RecognitionIndependenceReceiptV1 | None
    evidence: tuple[RecognitionEvidence, ...]
    speaker_base_evidence: tuple[RecognitionEvidence, ...]
    speaker_provenance: SpeakerAttributionProvenance | None
    orthographic_projections: tuple[OrthographicProjectionEvidenceV1, ...]
    request_bytes: VerifiedArtifactBytes
    independence_bytes: VerifiedArtifactBytes
    evidence_bytes: VerifiedArtifactBytes
    speaker_base_evidence_bytes: VerifiedArtifactBytes
    speaker_provenance_bytes: VerifiedArtifactBytes
    orthographic_projection_bytes: VerifiedArtifactBytes | None
    raw_outputs: VerifiedArtifactSet
    orthographic_raw_outputs: VerifiedArtifactSet

    def __post_init__(self) -> None:
        for label, values in (
            ("Recognition Evidence", self.evidence),
            ("speaker-base Recognition Evidence", self.speaker_base_evidence),
            ("orthographic projections", self.orthographic_projections),
        ):
            if type(values) is not tuple:
                raise TypeError(f"{label} requires an immutable tuple")
        if not self.evidence:
            raise ValueError("loaded Recognition state requires Recognition Evidence")
        if bool(self.speaker_base_evidence) != (self.speaker_provenance is not None):
            raise ValueError("speaker-base Evidence and provenance must be present together")
        if bool(self.orthographic_projections) != (self.orthographic_projection_bytes is not None):
            raise ValueError("orthographic Evidence and its exact collection bytes are incomplete")
        if not self.orthographic_projections and self.orthographic_raw_outputs.artifacts:
            raise ValueError("orthographic raw outputs require Orthographic Evidence")
        invocation_ids = {item.invocation_id for item in self.evidence}
        if len(invocation_ids) != 1 or self.request_artifact.invocation_id not in invocation_ids:
            raise ValueError("Recognition request and Evidence invocation IDs differ")
        if self.request.invocation_id != self.request_artifact.invocation_id:
            raise ValueError("materialized Recognition request crossed artifact identity")
        if self.request_bytes.name != "recognition_request.json":
            raise ValueError("Recognition request artifact has the wrong role name")
        if self.independence_bytes.name != "recognition_independence_receipt.json":
            raise ValueError("Recognition independence artifact has the wrong role name")
        raw_evidence = (*self.evidence, *self.speaker_base_evidence)
        _require_names(
            self.raw_outputs,
            tuple(f"raw_evidence/{item.raw_output.sha256}.json" for item in raw_evidence),
            "Recognition raw output",
        )
        _require_names(
            self.orthographic_raw_outputs,
            tuple(
                f"orthographic_projection/raw/{item.raw_output.sha256}.txt"
                for item in self.orthographic_projections
            ),
            "Orthographic Projection raw output",
        )
        for item in raw_evidence:
            raw = self.raw_outputs.require(f"raw_evidence/{item.raw_output.sha256}.json")
            if raw.sha256 != item.raw_output.sha256 or raw.size_bytes != item.raw_output.size_bytes:
                raise ValueError("Recognition raw output differs from its Evidence digest")
        for item in self.orthographic_projections:
            raw = self.orthographic_raw_outputs.require(
                f"orthographic_projection/raw/{item.raw_output.sha256}.txt"
            )
            if raw.sha256 != item.raw_output.sha256 or raw.size_bytes != item.raw_output.size_bytes:
                raise ValueError("Orthographic raw output differs from its Evidence digest")


@dataclass(frozen=True, slots=True)
class LoadedReferenceState:
    enrollments: tuple[ReferenceArtifact, ...]
    evidence: tuple[ReferenceEvidence, ...]
    retrievals: tuple[ReferenceRetrievalReceipt, ...]
    retrieval_policy: ReferenceRetrievalPolicySnapshot
    enrollments_bytes: VerifiedArtifactBytes
    evidence_bytes: VerifiedArtifactBytes
    retrievals_bytes: VerifiedArtifactBytes
    retrieval_policy_bytes: VerifiedArtifactBytes
    source_snapshots: VerifiedArtifactSet
    extraction_snapshots: VerifiedArtifactSet

    def __post_init__(self) -> None:
        for label, values in (
            ("Reference Enrollments", self.enrollments),
            ("Reference Evidence", self.evidence),
            ("Reference Retrieval Receipts", self.retrievals),
        ):
            if type(values) is not tuple:
                raise TypeError(f"{label} requires an immutable tuple")
        source_ids = tuple(item.source_id for item in self.enrollments)
        if len(set(source_ids)) != len(source_ids):
            raise ValueError("Reference Enrollments contain duplicate source IDs")
        _require_names(
            self.source_snapshots,
            tuple(f"reference_sources/{item.digest.sha256}.bin" for item in self.enrollments),
            "Reference source snapshot",
        )
        _require_names(
            self.extraction_snapshots,
            tuple(
                f"reference_extractions/{item.extracted_text.sha256}.json"
                for item in self.enrollments
            ),
            "Reference extraction snapshot",
        )


@dataclass(frozen=True, slots=True)
class LoadedSpeechCoverageState:
    receipt: SpeechCoverageReceipt | None
    receipt_bytes: VerifiedArtifactBytes
    raw_outputs: VerifiedArtifactSet

    def __post_init__(self) -> None:
        if self.receipt_bytes.name != "speech_coverage_receipt.json":
            raise ValueError("Speech Coverage receipt artifact has the wrong role name")
        if self.receipt is None and self.raw_outputs.artifacts:
            raise ValueError("Speech Coverage raw outputs require a receipt")
        if self.receipt is not None:
            expected_name = self.receipt.raw_output.uri.removeprefix("generation-artifact://")
            raw = self.raw_outputs.require(expected_name)
            if (
                raw.sha256 != self.receipt.raw_output.sha256
                or raw.size_bytes != self.receipt.raw_output.size_bytes
            ):
                raise ValueError("Speech Coverage raw output differs from its receipt")


@dataclass(frozen=True, slots=True)
class LoadedAdapterIdentity:
    """Value copy of the module-private explicit Adapter identity."""

    name: str
    version: str
    config_hash: str
    execution_mode: Literal["fixture", "local", "paid_api", "subscription", "other"]

    def __post_init__(self) -> None:
        if (
            not isinstance(self.name, str)
            or not self.name.strip()
            or self.name != self.name.strip()
            or not isinstance(self.version, str)
            or not self.version.strip()
            or self.version != self.version.strip()
        ):
            raise ValueError("loaded Adapter identity requires non-blank trimmed text")
        if not _SHA256_RE.fullmatch(self.config_hash):
            raise ValueError("loaded Adapter config hash must be lowercase SHA-256")


@dataclass(frozen=True, slots=True)
class LoadedSpeechCoverageAdapterIdentity:
    adapter_name: str
    adapter_version: str
    config_hash: str
    code_hash: str
    runtime_hash: str

    def __post_init__(self) -> None:
        if (
            not self.adapter_name.strip()
            or self.adapter_name != self.adapter_name.strip()
            or not self.adapter_version.strip()
            or self.adapter_version != self.adapter_version.strip()
        ):
            raise ValueError("Speech Coverage Adapter identity text must be non-blank and trimmed")
        for label, digest in (
            ("config_hash", self.config_hash),
            ("code_hash", self.code_hash),
            ("runtime_hash", self.runtime_hash),
        ):
            if not _SHA256_RE.fullmatch(digest):
                raise ValueError(f"Speech Coverage Adapter {label} must be lowercase SHA-256")


@dataclass(frozen=True, slots=True)
class LoadedCommonRuntimeState:
    recognizers: tuple[RecognitionModelIdentity, ...]
    speech_coverage_analyzer: LoadedSpeechCoverageAdapterIdentity | None
    speaker_attributor: LoadedAdapterIdentity | None
    reference_retriever: LoadedAdapterIdentity | None

    def __post_init__(self) -> None:
        if type(self.recognizers) is not tuple or not self.recognizers:
            raise ValueError("loaded runtime requires an immutable non-empty recognizer set")


@dataclass(frozen=True, slots=True)
class LoadedLegacyRuntimeState:
    common: LoadedCommonRuntimeState
    corrector: LoadedAdapterIdentity
    corrector_execution: CorrectionModelIdentity
    audio_auditor: AudioModelIdentity
    arbiter: AudioModelIdentity | None
    identities_artifact: VerifiedArtifactBytes

    def __post_init__(self) -> None:
        if self.identities_artifact.name != "adapter_identities.json":
            raise ValueError("legacy runtime identities artifact has the wrong role name")


@dataclass(frozen=True, slots=True)
class LoadedNativeRuntimeState:
    common: LoadedCommonRuntimeState
    text_identity: TextAuditAdapterIdentityV2
    text_policy: TextAuditExecutionPolicyV2
    audio_identity: AudioAuditAdapterIdentityV2
    audio_policy: AudioAuditExecutionPolicyV2
    audit_policy: CorrectionAuditPolicySnapshot
    execution_policy: CorrectionAuditExecutionPolicyV2
    identities_artifact: VerifiedArtifactBytes

    def __post_init__(self) -> None:
        if self.identities_artifact.name != "adapter_identities.json":
            raise ValueError("native runtime identities artifact has the wrong role name")


@dataclass(frozen=True, slots=True)
class LoadedTargetedCorrectionAuditReceipt:
    """Immutable snapshot of the legacy module-private targeted-audit receipt.

    Phase A deliberately does not move or import the persistence-facing private
    class.  The loader will copy its already-verified values into this state in
    Phase B; the exact collection artifact remains available separately.
    """

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
        for label, values in (
            ("available Reference Evidence IDs", self.available_reference_evidence_ids),
            ("presented Reference Evidence IDs", self.presented_reference_evidence_ids),
            ("target span IDs", self.target_span_ids),
            ("proposal IDs", self.proposal_ids),
        ):
            if type(values) is not tuple:
                raise TypeError(f"{label} require an immutable tuple")
            if len(set(values)) != len(values):
                raise ValueError(f"{label} contain duplicates")


@dataclass(frozen=True, slots=True)
class LoadedLegacyCollectionArtifacts:
    proposals: VerifiedArtifactBytes
    correction_audits: VerifiedArtifactBytes
    targeted_correction_audits: VerifiedArtifactBytes
    audio_audits: VerifiedArtifactBytes
    arbitrations: VerifiedArtifactBytes

    def __post_init__(self) -> None:
        actual = (
            self.proposals.name,
            self.correction_audits.name,
            self.targeted_correction_audits.name,
            self.audio_audits.name,
            self.arbitrations.name,
        )
        expected = (
            "correction_proposals.json",
            "correction_audit_receipts.json",
            "targeted_correction_audit_receipts.json",
            "audio_audit_receipts.json",
            "arbitration_receipts.json",
        )
        if actual != expected:
            raise ValueError("legacy audit collection artifacts have incorrect role names")


@dataclass(frozen=True, slots=True)
class LoadedLegacyAuditState:
    """All verified legacy Full Audit values and their exact byte artifacts."""

    proposals: tuple[CorrectionProposal, ...]
    correction_audits: tuple[CorrectionAuditReceipt, ...]
    targeted_correction_audits: tuple[LoadedTargetedCorrectionAuditReceipt, ...]
    audio_audits: tuple[AudioAuditReceipt, ...]
    arbitrations: tuple[ArbitrationReceipt, ...]
    collection_artifacts: LoadedLegacyCollectionArtifacts
    correction_execution_artifacts: VerifiedArtifactSet
    audio_execution_artifacts: VerifiedArtifactSet
    runtime: LoadedLegacyRuntimeState
    profile: Literal["legacy_v1"] = field(default="legacy_v1", init=False)

    def __post_init__(self) -> None:
        for label, values in (
            ("Correction Proposals", self.proposals),
            ("Correction Audit Receipts", self.correction_audits),
            ("targeted Correction Audit Receipts", self.targeted_correction_audits),
            ("Audio Audit Receipts", self.audio_audits),
            ("Arbitration Receipts", self.arbitrations),
        ):
            if type(values) is not tuple:
                raise TypeError(f"{label} require an immutable tuple")


@dataclass(frozen=True, slots=True)
class VerifiedNativeSource:
    """One logical native audit source URI bound to its addressed artifact."""

    uri: str
    artifact: VerifiedArtifactBytes

    def __post_init__(self) -> None:
        if not isinstance(self.uri, str) or not self.uri.strip() or self.uri != self.uri.strip():
            raise ValueError("native source URI must be non-blank and trimmed")


@dataclass(frozen=True, slots=True)
class LoadedNativeSourceSet:
    modality: Literal["text", "audio"]
    index_artifact: VerifiedArtifactBytes
    sources: tuple[VerifiedNativeSource, ...]

    def __post_init__(self) -> None:
        if type(self.sources) is not tuple:
            raise TypeError("native source set requires an immutable tuple")
        selective_reconstructible = (
            self.modality == "audio"
            and self.index_artifact.name == "native_full_audit/audio_execution_plans_v3.json"
        )
        if not self.sources and not selective_reconstructible:
            raise ValueError("native source set requires at least one exact source")
        expected_indexes = {f"native_full_audit/{self.modality}_source_artifact_index.json"}
        if self.modality == "audio":
            expected_indexes.add("native_full_audit/audio_execution_plans_v3.json")
        if self.index_artifact.name not in expected_indexes:
            raise ValueError("native source set has the wrong index role name")
        uris = tuple(item.uri for item in self.sources)
        names = tuple(item.artifact.name for item in self.sources)
        if len(set(uris)) != len(uris) or len(set(names)) != len(names):
            raise ValueError("native source set contains a duplicate URI or artifact name")
        if uris != tuple(sorted(uris)):
            raise ValueError("native source set must use canonical URI order")
        expected_prefix = f"native_full_audit/sources/{self.modality}/"
        if any(not name.startswith(expected_prefix) for name in names):
            raise ValueError("native source artifact crossed modality namespace")


@dataclass(frozen=True, slots=True)
class LoadedNativeTextRunArtifacts:
    index_artifact: VerifiedArtifactBytes
    requests: VerifiedArtifactSet
    responses: VerifiedArtifactSet

    def __post_init__(self) -> None:
        if self.index_artifact.name != "native_full_audit/text_run_index.json":
            raise ValueError("native text run has the wrong index role name")
        for label, artifacts in (("requests", self.requests), ("responses", self.responses)):
            prefix = f"native_full_audit/text/{label}/"
            if any(not item.name.startswith(prefix) for item in artifacts.artifacts):
                raise ValueError(f"native text {label} crossed artifact namespace")

    def verify_run(self, run: TextAuditRunResult) -> None:
        if tuple(item.payload for item in self.requests.artifacts) != run.request_bytes:
            raise ValueError("native text request bytes differ from the verified run")
        if tuple(item.payload for item in self.responses.artifacts) != run.response_bytes:
            raise ValueError("native text response bytes differ from the verified run")


@dataclass(frozen=True, slots=True)
class LoadedNativeAudioRunArtifacts:
    index_artifact: VerifiedArtifactBytes
    requests: VerifiedArtifactSet
    responses: VerifiedArtifactSet
    clips: VerifiedArtifactSet
    journals: VerifiedArtifactSet

    def __post_init__(self) -> None:
        if self.index_artifact.name != "native_full_audit/audio_run_index.json":
            raise ValueError("native audio run has the wrong index role name")
        for label, artifacts in (
            ("requests", self.requests),
            ("responses", self.responses),
            ("clips", self.clips),
            ("journals", self.journals),
        ):
            prefix = f"native_full_audit/audio/{label}/"
            if any(not item.name.startswith(prefix) for item in artifacts.artifacts):
                raise ValueError(f"native audio {label} crossed artifact namespace")

    def verify_run(self, run: AudioAuditRunResult) -> None:
        comparisons = (
            ("request", self.requests, run.request_bytes),
            ("response", self.responses, run.response_bytes),
            ("clip", self.clips, run.clip_bytes),
        )
        for label, artifacts, payloads in comparisons:
            if tuple(item.payload for item in artifacts.artifacts) != payloads:
                raise ValueError(f"native audio {label} bytes differ from the verified run")

    def verify_runs(self, runs: tuple[AudioAuditRunResult, ...]) -> None:
        comparisons = (
            ("request", self.requests, tuple(item for run in runs for item in run.request_bytes)),
            (
                "response",
                self.responses,
                tuple(item for run in runs for item in run.response_bytes),
            ),
            ("clip", self.clips, tuple(item for run in runs for item in run.clip_bytes)),
        )
        for label, artifacts, payloads in comparisons:
            if tuple(item.payload for item in artifacts.artifacts) != payloads:
                raise ValueError(f"native selective audio {label} bytes differ")


@dataclass(frozen=True, slots=True)
class LoadedLedgerEntrySnapshot:
    sequence: int
    previous_hash: str
    decision_hash: str
    entry_hash: str

    def __post_init__(self) -> None:
        if self.sequence < 1:
            raise ValueError("Ledger sequence must be positive")
        for label, digest in (
            ("previous_hash", self.previous_hash),
            ("decision_hash", self.decision_hash),
            ("entry_hash", self.entry_hash),
        ):
            if not _SHA256_RE.fullmatch(digest):
                raise ValueError(f"Ledger {label} must be lowercase SHA-256")


NativeDiscoveredCandidate = TextDiscoveredCandidateV2 | AudioDiscoveredCandidateV2
NativeAuthorization = CorrectionAcceptanceVerdictV2 | OriginalConfirmationAuthorizationV2
NativeAuthorizationPolicy = CorrectionAcceptancePolicyV2 | OriginalConfirmationPolicyV2


@dataclass(frozen=True, slots=True)
class LoadedNativeResolutionArtifacts:
    decision: VerifiedArtifactBytes
    candidate: VerifiedArtifactBytes
    authorization: VerifiedArtifactBytes
    policy: VerifiedArtifactBytes
    human_receipt_index: VerifiedArtifactBytes
    ledger_entry: VerifiedArtifactBytes
    human_receipts: VerifiedArtifactSet
    reference_proof: VerifiedArtifactBytes | None
    reference_adjudication: VerifiedArtifactBytes | None


@dataclass(frozen=True, slots=True)
class LoadedNativeResolutionState:
    """One fully verified, branch-discriminated native resolution transition."""

    authorization_kind: Literal["correction_acceptance", "original_confirmation"]
    candidate: NativeDiscoveredCandidate
    authorization: NativeAuthorization
    policy: NativeAuthorizationPolicy
    decision: NativeCorrectionDecisionV2
    ledger_entry: LoadedLedgerEntrySnapshot
    human_audio_receipts: tuple[HumanAudioReviewReceiptV2, ...]
    human_original_confirmation_receipts: tuple[HumanOriginalConfirmationReceiptV2, ...]
    reference_proof: ReferenceAuthorityProofV2 | None
    reference_adjudication: HumanReferenceAdjudicationReceiptV2 | None
    artifacts: LoadedNativeResolutionArtifacts

    def __post_init__(self) -> None:
        for label, values in (
            ("human audio receipts", self.human_audio_receipts),
            ("human original-confirmation receipts", self.human_original_confirmation_receipts),
        ):
            if type(values) is not tuple:
                raise TypeError(f"{label} require an immutable tuple")
        if self.authorization_kind != self.decision.authorization_kind:
            raise ValueError("native resolution branch differs from its Decision")
        if self.decision.authorization_id != self.authorization.id:
            raise ValueError("native resolution authorization differs from its Decision")
        if (
            self.decision.authorization_hash != self.authorization.content_hash
            or self.decision.candidate_discovery_id != self.candidate.id
            or self.decision.candidate_discovery_hash != self.candidate.content_hash
            or self.authorization.candidate_discovery_id != self.candidate.id
            or self.authorization.candidate_discovery_hash != self.candidate.content_hash
            or self.authorization.policy_hash != self.policy.content_hash
        ):
            raise ValueError("native resolution parents crossed content lineage")
        if self.ledger_entry.decision_hash != hash_object(self.decision.model_dump(mode="json")):
            raise ValueError("native resolution Ledger entry differs from its Decision")
        if (
            self.artifacts.decision.name != "native_resolution/decision.json"
            or self.artifacts.candidate.name != "native_resolution/candidate_discovery.json"
            or self.artifacts.authorization.name != "native_resolution/authorization.json"
            or self.artifacts.ledger_entry.name != "native_resolution/prepared_ledger_entry.json"
        ):
            raise ValueError("native resolution artifacts have incorrect role names")
        if self.authorization_kind == "correction_acceptance":
            if not isinstance(self.authorization, CorrectionAcceptanceVerdictV2) or not isinstance(
                self.policy, CorrectionAcceptancePolicyV2
            ):
                raise TypeError("correction acceptance requires its typed verdict and policy")
            if self.human_original_confirmation_receipts:
                raise ValueError(
                    "correction acceptance cannot contain original-confirmation receipts"
                )
            if (
                self.artifacts.policy.name != "native_resolution/acceptance_policy.json"
                or self.artifacts.human_receipt_index.name
                != "native_resolution/human_audio_index.json"
                or any(
                    not item.name.startswith("native_resolution/human_audio/")
                    for item in self.artifacts.human_receipts.artifacts
                )
                or len(self.artifacts.human_receipts.artifacts) != len(self.human_audio_receipts)
            ):
                raise ValueError("correction acceptance artifacts crossed branch roles")
        else:
            if not isinstance(
                self.authorization, OriginalConfirmationAuthorizationV2
            ) or not isinstance(self.policy, OriginalConfirmationPolicyV2):
                raise TypeError("original confirmation requires its typed authorization and policy")
            if self.human_audio_receipts or self.reference_adjudication is not None:
                raise ValueError("original confirmation cannot contain acceptance-only receipts")
            if (
                self.artifacts.policy.name != "native_resolution/original_confirmation_policy.json"
                or self.artifacts.human_receipt_index.name
                != "native_resolution/human_original_confirmation_index.json"
                or any(
                    not item.name.startswith("native_resolution/human_original_confirmation/")
                    for item in self.artifacts.human_receipts.artifacts
                )
                or len(self.artifacts.human_receipts.artifacts)
                != len(self.human_original_confirmation_receipts)
            ):
                raise ValueError("original confirmation artifacts crossed branch roles")
        if (self.reference_proof is None) != (self.artifacts.reference_proof is None):
            raise ValueError("native resolution Reference proof artifacts are incomplete")
        if (
            self.artifacts.reference_proof is not None
            and self.artifacts.reference_proof.name
            != "native_resolution/reference_authority_proof.json"
        ):
            raise ValueError("native resolution Reference proof has the wrong role name")
        if (self.reference_adjudication is None) != (self.artifacts.reference_adjudication is None):
            raise ValueError("native resolution Reference adjudication artifacts are incomplete")
        if (
            self.artifacts.reference_adjudication is not None
            and self.artifacts.reference_adjudication.name
            != "native_resolution/human_reference_adjudication.json"
        ):
            raise ValueError("native resolution Reference adjudication has the wrong role name")


@dataclass(frozen=True, slots=True)
class LoadedNativeAuditArtifacts:
    audit_plan: VerifiedArtifactBytes
    candidate_signals: VerifiedArtifactBytes
    candidate_groups: VerifiedArtifactBytes
    execution_plan: VerifiedArtifactBytes
    text_record: VerifiedArtifactBytes
    audio_record: VerifiedArtifactBytes
    aggregate: VerifiedArtifactBytes

    def __post_init__(self) -> None:
        actual = (
            self.audit_plan.name,
            self.candidate_signals.name,
            self.candidate_groups.name,
            self.execution_plan.name,
            self.text_record.name,
            self.audio_record.name,
            self.aggregate.name,
        )
        legacy = (
            "native_full_audit/audit_plan.json",
            "native_full_audit/candidate_signal_set.json",
            "native_full_audit/candidate_group_set.json",
            "native_full_audit/correction_audit_execution_plan.json",
            "native_full_audit/text_audit_execution_record.json",
            "native_full_audit/audio_audit_execution_record.json",
            "native_full_audit/full_audit_aggregate.json",
        )
        selective = (
            "native_full_audit/audit_plan.json",
            "native_full_audit/candidate_signal_set.json",
            "native_full_audit/candidate_group_set.json",
            "native_full_audit/text_execution_plan_v3.json",
            "native_full_audit/text_audit_execution_record.json",
            "native_full_audit/audio_execution_records_v3.json",
            "native_full_audit/selective_audit_aggregate_v3.json",
        )
        if actual not in (legacy, selective):
            raise ValueError("native Full Audit artifacts have incorrect role names")


@dataclass(frozen=True, slots=True)
class LoadedNativeAuditState:
    """All verified native Full Audit parents, runs, and optional resolution."""

    audit_basis: CanonicalBuildResult
    audit_plan: AuditPlan
    candidate_signals: CandidateSignalSet
    candidate_groups: CandidateGroupSet
    execution_plan: CorrectionAuditExecutionPlanV2 | ModalityAuditExecutionPlanV3
    text_sources: LoadedNativeSourceSet
    audio_sources: LoadedNativeSourceSet
    text_run: TextAuditRunResult
    audio_run: AudioAuditRunResult
    aggregate: FullAuditAggregateAttestationV2 | SelectiveAuditAggregateAttestationV3
    audit_artifacts: LoadedNativeAuditArtifacts
    text_run_artifacts: LoadedNativeTextRunArtifacts
    audio_run_artifacts: LoadedNativeAudioRunArtifacts
    runtime: LoadedNativeRuntimeState
    resolution: LoadedNativeResolutionState | None
    audio_runs: tuple[AudioAuditRunResult, ...] = ()
    audio_execution_plans: tuple[ModalityAuditExecutionPlanV3, ...] = ()
    audio_round_receipts: tuple[SelectiveAudioAuditRoundReceiptV3, ...] = ()
    profile: Literal["native_v2"] = field(default="native_v2", init=False)

    def __post_init__(self) -> None:
        self.text_run_artifacts.verify_run(self.text_run)
        if isinstance(self.aggregate, SelectiveAuditAggregateAttestationV3):
            if not self.audio_runs or self.audio_run != self.audio_runs[-1]:
                raise ValueError("selective native audit lacks exact audio rounds")
            self.audio_run_artifacts.verify_runs(self.audio_runs)
        else:
            self.audio_run_artifacts.verify_run(self.audio_run)
        generation_id = self.audit_basis.transcript.generation_id
        if (
            self.audit_plan.generation_id != generation_id
            or self.execution_plan.generation_id != generation_id
            or self.aggregate.generation_id != generation_id
        ):
            raise ValueError("native aggregate crossed its audit-basis Generation")
        if (
            self.candidate_signals.audit_plan_content_hash != self.audit_plan.content_hash
            or self.candidate_groups.audit_plan_content_hash != self.audit_plan.content_hash
            or self.candidate_groups.signal_set_content_hash != self.candidate_signals.content_hash
            or self.execution_plan.audit_plan_content_hash != self.audit_plan.content_hash
            or self.execution_plan.candidate_signal_set_content_hash
            != self.candidate_signals.content_hash
            or self.execution_plan.candidate_group_set_content_hash
            != self.candidate_groups.content_hash
            or self.text_run.record.execution_plan_content_hash != self.execution_plan.content_hash
            or (
                isinstance(self.aggregate, FullAuditAggregateAttestationV2)
                and self.audio_run.record.execution_plan_content_hash
                != self.execution_plan.content_hash
            )
        ):
            raise ValueError("native Full Audit parents crossed content lineage")


LoadedAuditState = LoadedLegacyAuditState | LoadedNativeAuditState
LoadedGenerationProfile = Literal["legacy_v1", "native_v2"]


@dataclass(frozen=True, slots=True)
class LoadedGenerationState:
    """Named, immutable result of loading and fully verifying one Generation."""

    result: CanonicalBuildResult
    storage: LoadedStorageSnapshot
    normalization: LoadedNormalizationState
    recognition: LoadedRecognitionState
    references: LoadedReferenceState
    speech_coverage: LoadedSpeechCoverageState
    audit: LoadedAuditState

    def __post_init__(self) -> None:
        transcript = self.result.transcript
        if (
            transcript.generation_id != self.storage.generation_id
            or transcript.generation_id != self.storage.manifest.generation_id
        ):
            raise ValueError("loaded state crosses a Generation boundary")
        if (
            transcript.episode_id != self.storage.manifest.episode_id
            or transcript.episode_id != self.recognition.request_artifact.episode_id
        ):
            raise ValueError("loaded state crosses an episode boundary")
        receipt = self.normalization.receipt
        if (
            transcript.source_audio_hash != receipt.source.sha256
            or transcript.normalized_audio_hash != receipt.normalized.sha256
            or transcript.normalization_receipt_hash != normalization_receipt_content_hash(receipt)
        ):
            raise ValueError("loaded Normalization state crossed Canonical lineage")
        if transcript.evidence_hash != recognition_evidence_set_hash(self.recognition.evidence):
            raise ValueError("loaded Recognition Evidence crossed Canonical lineage")
        projected_hash = (
            orthographic_projection_evidence_set_hash(self.recognition.orthographic_projections)
            if self.recognition.orthographic_projections
            else None
        )
        if transcript.orthographic_projection_evidence_hash != projected_hash:
            raise ValueError("loaded Orthographic Evidence crossed Canonical lineage")
        if transcript.reference_evidence_hash != reference_evidence_set_hash(
            self.references.evidence
        ):
            raise ValueError("loaded Reference Evidence crossed Canonical lineage")
        if transcript.verified_speech_coverage_receipt_hash != verified_speech_coverage_hash(
            self.speech_coverage.receipt
        ):
            raise ValueError("loaded Speech Coverage crossed Canonical lineage")
        # ``parent_manifest_hash`` proves ancestry only.  It cannot discriminate
        # a native resolution child from future release-attestation derivatives;
        # Phase B binds ``audit.resolution`` from the exact resolution artifacts.

    @property
    def profile(self) -> LoadedGenerationProfile:
        return self.audit.profile


__all__ = [
    "ArtifactHashBinding",
    "LoadedAdapterIdentity",
    "LoadedAuditState",
    "LoadedCommonRuntimeState",
    "LoadedGenerationProfile",
    "LoadedGenerationState",
    "LoadedLedgerEntrySnapshot",
    "LoadedLegacyAuditState",
    "LoadedLegacyCollectionArtifacts",
    "LoadedLegacyRuntimeState",
    "LoadedManifestSnapshot",
    "LoadedNativeAudioRunArtifacts",
    "LoadedNativeAuditState",
    "LoadedNativeAuditArtifacts",
    "LoadedNativeResolutionState",
    "LoadedNativeResolutionArtifacts",
    "LoadedNativeRuntimeState",
    "LoadedNativeSourceSet",
    "LoadedNativeTextRunArtifacts",
    "LoadedNormalizationState",
    "LoadedRecognitionState",
    "LoadedReferenceState",
    "LoadedSpeechCoverageState",
    "LoadedSpeechCoverageAdapterIdentity",
    "LoadedStorageSnapshot",
    "LoadedTargetedCorrectionAuditReceipt",
    "NativeAuthorization",
    "NativeAuthorizationPolicy",
    "NativeDiscoveredCandidate",
    "VerifiedArtifactBytes",
    "VerifiedArtifactSet",
    "VerifiedNativeSource",
]
