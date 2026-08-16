"""Adapter seams used inside the Podcast Subtitle V2 deep Module.

These Interfaces expose domain requests and results only.  Provider payloads,
prompts, polling, retries, model loading, and cache layouts belong to concrete
Adapters and must not leak through this file.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal, Mapping, Protocol, runtime_checkable

from shared.schemas.podcast_subtitles_v2 import (
    ArbitrationReceipt,
    ArtifactDigest,
    AudioAuditReceipt,
    CanonicalTranscript,
    CorrectionMode,
    NormalizationReceipt,
    ProtectedTokenRange,
    RecognitionEvidence,
    ReferenceArtifact,
    ReferenceEvidence,
    ReferenceQueryContext,
    ReferenceRetrievalPolicySnapshot,
    ReferenceRetrievalReceipt,
    ReviewIssue,
    SemanticUnit,
    SpeechCoverageReceipt,
    recognition_evidence_content_hash,
    reference_retrieval_policy_hash,
)

from .hashing import hash_object, sha256_bytes


class AdapterError(RuntimeError):
    """Base error exposed by an Adapter seam."""


class AdapterInputError(AdapterError, ValueError):
    """An Adapter input is malformed, unsupported, or internally inconsistent."""


class AdapterIntegrityError(AdapterError):
    """Input bytes do not match the content identity carried by the request."""


class AdapterUnavailableError(AdapterError):
    """A production Adapter cannot run because its optional runtime is unavailable."""


class AdapterWorkPending(AdapterError):
    """Subscription work packets exist and await strict external results."""

    def __init__(
        self,
        message: str,
        *,
        packet_paths: tuple[Path, ...],
        response_paths: tuple[Path, ...],
    ) -> None:
        super().__init__(message)
        self.packet_paths = packet_paths
        self.response_paths = response_paths


@dataclass(frozen=True, slots=True)
class NormalizeRequest:
    """Normalize one source file without exposing provider-specific controls."""

    source_audio: Path
    output_dir: Path | None = None
    expected_source_hash: str | None = None


@dataclass(frozen=True, slots=True)
class NormalizationResult:
    """Normalized bytes and the receipt that proves their lineage."""

    normalized_audio: Path
    receipt: NormalizationReceipt


@dataclass(frozen=True, slots=True)
class RecognitionContextPolicyV1:
    """Closed recognition request policy: language guidance, never lexical bias."""

    id: Literal["nakama-verbatim-no-lexical-context-v1"] = "nakama-verbatim-no-lexical-context-v1"
    context_bytes: bytes = b""

    def __post_init__(self) -> None:
        if self.context_bytes != b"":
            raise ValueError("production Recognition context must be exact empty bytes")

    @property
    def context_sha256(self) -> str:
        return sha256_bytes(self.context_bytes)

    @property
    def content_hash(self) -> str:
        return hash_object(
            {
                "schema_version": 1,
                "id": self.id,
                "context_sha256": self.context_sha256,
                "context_size_bytes": len(self.context_bytes),
            }
        )


NO_LEXICAL_BIAS_CONTEXT = RecognitionContextPolicyV1()


@dataclass(frozen=True, slots=True)
class RecognitionRequest:
    """Domain input for recognition on the normalized-audio clock."""

    episode_id: str
    invocation_id: str
    normalized_audio: Path
    expected_normalized_audio_hash: str | None = None
    raw_output_dir: Path | None = None
    language_hint: str | None = None
    context_policy: RecognitionContextPolicyV1 = NO_LEXICAL_BIAS_CONTEXT


@dataclass(frozen=True, slots=True)
class RecognitionModelIdentity:
    """Measured executable identity of one recognition Adapter.

    ASR output depends on substantially more than a model label.  The exact
    adapter code, pinned model and aligner revisions, Python / package / CUDA
    runtime, configuration, and execution mode are all part of the executable
    identity that must survive a fresh-process replay.
    """

    adapter_name: str
    adapter_version: str
    model: str
    model_version: str
    aligner: str
    aligner_version: str
    runtime_components: tuple[tuple[str, str], ...]
    runtime_hash: str
    adapter_code_hash: str
    config_hash: str
    execution_mode: Literal["fixture", "local", "import", "other"]

    def __post_init__(self) -> None:
        object.__setattr__(self, "runtime_components", tuple(self.runtime_components))
        for label, value in (
            ("adapter_name", self.adapter_name),
            ("adapter_version", self.adapter_version),
            ("model", self.model),
            ("model_version", self.model_version),
            ("aligner", self.aligner),
            ("aligner_version", self.aligner_version),
        ):
            if not isinstance(value, str) or not value.strip() or value != value.strip():
                raise ValueError(f"Recognition identity {label} must be non-blank and trimmed")
        if not self.runtime_components:
            raise ValueError("Recognition identity requires measured runtime components")
        component_names = tuple(name for name, _value in self.runtime_components)
        if len(set(component_names)) != len(component_names):
            raise ValueError("Recognition runtime component names must be unique")
        if self.runtime_components != tuple(sorted(self.runtime_components)):
            raise ValueError("Recognition runtime components must use canonical name order")
        for name, value in self.runtime_components:
            if (
                not isinstance(name, str)
                or not name.strip()
                or name != name.strip()
                or not isinstance(value, str)
                or not value.strip()
                or value != value.strip()
            ):
                raise ValueError(
                    "Recognition runtime components require non-blank trimmed names and values"
                )
        expected_runtime_hash = hash_object({"runtime_components": self.runtime_components})
        if self.runtime_hash != expected_runtime_hash:
            raise ValueError("Recognition runtime_hash must cover the exact runtime components")
        for label, value in (
            ("runtime_hash", self.runtime_hash),
            ("adapter_code_hash", self.adapter_code_hash),
            ("config_hash", self.config_hash),
        ):
            if not isinstance(value, str) or not re.fullmatch(r"[0-9a-f]{64}", value):
                raise ValueError(f"Recognition identity {label} must be lowercase SHA-256")

    @property
    def content_hash(self) -> str:
        return hash_object(self)


@dataclass(frozen=True, slots=True)
class SpeechCoverageRequest:
    """Exact audio plus ordered ASR Evidence for independent coverage.

    ``recognition_evidence[0]`` is the canonical primary stream.  Later items
    are bound as corroborating Evidence but cannot satisfy primary coverage.
    """

    episode_id: str
    invocation_id: str
    normalized_audio: Path
    expected_normalized_audio_hash: str
    expected_normalized_audio_size_bytes: int
    normalized_audio_duration_ms: int
    recognition_evidence: tuple[RecognitionEvidence, ...]
    raw_output_dir: Path

    def __post_init__(self) -> None:
        object.__setattr__(self, "normalized_audio", Path(self.normalized_audio))
        object.__setattr__(self, "raw_output_dir", Path(self.raw_output_dir))
        if not self.episode_id.strip() or not self.invocation_id.strip():
            raise ValueError("speech coverage requires episode and invocation IDs")
        if not re.fullmatch(r"[0-9a-f]{64}", self.expected_normalized_audio_hash):
            raise ValueError("speech coverage audio hash must be lowercase SHA-256")
        if (
            not isinstance(self.expected_normalized_audio_size_bytes, int)
            or isinstance(self.expected_normalized_audio_size_bytes, bool)
            or self.expected_normalized_audio_size_bytes <= 0
        ):
            raise ValueError("speech coverage audio size must be positive")
        if (
            not isinstance(self.normalized_audio_duration_ms, int)
            or isinstance(self.normalized_audio_duration_ms, bool)
            or self.normalized_audio_duration_ms <= 0
        ):
            raise ValueError("speech coverage audio duration must be positive")
        if not self.recognition_evidence:
            raise ValueError("speech coverage requires Recognition Evidence")
        for evidence in self.recognition_evidence:
            if evidence.episode_id != self.episode_id:
                raise ValueError("speech coverage Evidence belongs to another episode")
            if evidence.invocation_id != self.invocation_id:
                raise ValueError("speech coverage Evidence belongs to another invocation")
            if evidence.normalized_audio_hash != self.expected_normalized_audio_hash:
                raise ValueError("speech coverage Evidence uses another audio clock")
            if any(token.end_ms > self.normalized_audio_duration_ms for token in evidence.tokens):
                raise ValueError("speech coverage Evidence exceeds normalized audio duration")


@dataclass(frozen=True, slots=True)
class SpeakerTrackInput:
    """Operator-selected isolated microphone track and its stable speaker label.

    This is a transport input only.  Paths are deliberately excluded from
    logical identity; the Module resolves the path and creates a
    :class:`SpeakerTrackBinding` from the exact bytes before attribution.
    """

    path: Path
    speaker_label: str

    def __post_init__(self) -> None:
        object.__setattr__(self, "path", Path(self.path))
        if (
            not isinstance(self.speaker_label, str)
            or not self.speaker_label
            or self.speaker_label != self.speaker_label.strip()
        ):
            raise ValueError("speaker track requires a stable non-blank label")
        if any(ord(character) < 32 for character in self.speaker_label):
            raise ValueError("speaker label must not contain control characters")


@dataclass(frozen=True, slots=True)
class SpeakerTrackBinding:
    """Content identity of one microphone track resolved by the Module.

    ``path`` remains transport-only.  Ordered ``speaker_label``/``sha256``/
    ``size_bytes`` values are the portable identity persisted by the
    attribution artifact and Generation manifest.
    """

    path: Path
    speaker_label: str
    sha256: str
    size_bytes: int

    def __post_init__(self) -> None:
        object.__setattr__(self, "path", Path(self.path))
        SpeakerTrackInput(path=self.path, speaker_label=self.speaker_label)
        if not isinstance(self.sha256, str) or not re.fullmatch(r"[0-9a-f]{64}", self.sha256):
            raise ValueError("speaker track sha256 must be lowercase SHA-256")
        if (
            not isinstance(self.size_bytes, int)
            or isinstance(self.size_bytes, bool)
            or self.size_bytes <= 0
        ):
            raise ValueError("speaker track size_bytes must be positive")

    @property
    def portable_identity(self) -> dict[str, object]:
        """Return the machine-independent identity used in ordered hashes."""

        return {
            "speaker_label": self.speaker_label,
            "sha256": self.sha256,
            "size_bytes": self.size_bytes,
        }


@dataclass(frozen=True, slots=True)
class SpeakerTrackProvenance:
    """Portable, ordered microphone identity recovered from stored Evidence."""

    slot: int
    speaker_label: str
    sha256: str
    size_bytes: int

    def __post_init__(self) -> None:
        if not isinstance(self.slot, int) or isinstance(self.slot, bool) or self.slot < 0:
            raise ValueError("speaker track slot must be non-negative")
        SpeakerTrackBinding(
            path=Path(f"speaker-track-{self.slot}"),
            speaker_label=self.speaker_label,
            sha256=self.sha256,
            size_bytes=self.size_bytes,
        )

    @classmethod
    def from_payload(cls, payload: object) -> SpeakerTrackProvenance:
        if not isinstance(payload, Mapping) or set(payload) != {
            "slot",
            "speaker_label",
            "sha256",
            "size_bytes",
        }:
            raise ValueError("invalid speaker track provenance payload")
        return cls(
            slot=payload["slot"],
            speaker_label=payload["speaker_label"],
            sha256=payload["sha256"],
            size_bytes=payload["size_bytes"],
        )


@dataclass(frozen=True, slots=True)
class SpeakerAttributionProvenance:
    """Verified link from attributed Evidence back to base ASR and mic bytes."""

    adapter: str
    adapter_version: int
    base_recognition_evidence_hash: str
    config_hash: str
    tracks: tuple[SpeakerTrackProvenance, ...]

    def __post_init__(self) -> None:
        if (
            not isinstance(self.adapter, str)
            or not self.adapter.strip()
            or not isinstance(self.adapter_version, int)
            or isinstance(self.adapter_version, bool)
            or self.adapter_version < 1
        ):
            raise ValueError("speaker attribution provenance requires Adapter identity")
        for label, digest in (
            ("base_recognition_evidence_hash", self.base_recognition_evidence_hash),
            ("config_hash", self.config_hash),
        ):
            if not isinstance(digest, str) or not re.fullmatch(r"[0-9a-f]{64}", digest):
                raise ValueError(f"{label} must be lowercase SHA-256")
        if tuple(track.slot for track in self.tracks) != tuple(range(len(self.tracks))):
            raise ValueError("speaker track provenance slots must be contiguous and ordered")
        if len(self.tracks) != 2:
            raise ValueError("speaker attribution provenance requires exactly two tracks")
        if len({track.speaker_label for track in self.tracks}) != len(self.tracks):
            raise ValueError("speaker attribution provenance labels must be unique")
        if len({track.sha256 for track in self.tracks}) != len(self.tracks):
            raise ValueError("speaker attribution provenance track hashes must be unique")

    @classmethod
    def from_payload(cls, payload: object) -> SpeakerAttributionProvenance:
        if not isinstance(payload, Mapping) or set(payload) != {
            "adapter",
            "adapter_version",
            "base_recognition_evidence_hash",
            "config_hash",
            "tracks",
        }:
            raise ValueError("invalid speaker attribution provenance payload")
        raw_tracks = payload["tracks"]
        if not isinstance(raw_tracks, (list, tuple)):
            raise ValueError("speaker attribution provenance tracks must be a sequence")
        return cls(
            adapter=payload["adapter"],
            adapter_version=payload["adapter_version"],
            base_recognition_evidence_hash=payload["base_recognition_evidence_hash"],
            config_hash=payload["config_hash"],
            tracks=tuple(SpeakerTrackProvenance.from_payload(item) for item in raw_tracks),
        )


@dataclass(frozen=True, slots=True)
class SpeakerAttributionRequest:
    """Bind base ASR Evidence to exact isolated-track bytes for attribution."""

    recognition_request: RecognitionRequest
    base_evidence: RecognitionEvidence
    tracks: tuple[SpeakerTrackBinding, ...]

    def __post_init__(self) -> None:
        recognition = self.recognition_request
        base = self.base_evidence
        if recognition.raw_output_dir is None:
            raise ValueError("speaker attribution requires generation-owned raw_output_dir")
        if recognition.expected_normalized_audio_hash is None:
            raise ValueError("speaker attribution requires an expected normalized-audio hash")
        if base.episode_id != recognition.episode_id:
            raise ValueError("base Recognition Evidence belongs to another episode")
        if base.invocation_id != recognition.invocation_id:
            raise ValueError("base Recognition Evidence belongs to another invocation")
        if base.normalized_audio_hash != recognition.expected_normalized_audio_hash:
            raise ValueError("base Recognition Evidence uses another normalized-audio clock")
        if len(self.tracks) != 2:
            raise ValueError("speaker attribution requires exactly two bound mic tracks")
        labels = tuple(track.speaker_label for track in self.tracks)
        if len(set(labels)) != len(labels):
            raise ValueError("speaker attribution track labels must be unique")
        resolved_paths = tuple(str(track.path.resolve()) for track in self.tracks)
        if len(set(resolved_paths)) != len(resolved_paths):
            raise ValueError("speaker attribution tracks must be distinct")
        digests = tuple(track.sha256 for track in self.tracks)
        if len(set(digests)) != len(digests):
            raise ValueError("speaker attribution track byte identities must be distinct")

    @property
    def portable_track_identity(self) -> tuple[dict[str, object], ...]:
        """Ordered track identity with no machine-local URI."""

        return tuple(track.portable_identity for track in self.tracks)


@dataclass(frozen=True, slots=True)
class DiarizationPolicyV1:
    """Fail-closed thresholds for opaque audio diarization Evidence."""

    minimum_segment_confidence: float = 0.80
    minimum_assignment_margin: float = 0.10

    def __post_init__(self) -> None:
        for label, value in (
            ("minimum_segment_confidence", self.minimum_segment_confidence),
            ("minimum_assignment_margin", self.minimum_assignment_margin),
        ):
            if (
                not isinstance(value, (int, float))
                or isinstance(value, bool)
                or not 0.0 <= float(value) <= 1.0
            ):
                raise ValueError(f"Diarization policy {label} must be within [0, 1]")
        if self.minimum_assignment_margin <= 0:
            raise ValueError("Diarization policy requires a positive assignment margin")

    @property
    def content_hash(self) -> str:
        return hash_object(
            {
                "schema_version": 1,
                "minimum_segment_confidence": float(self.minimum_segment_confidence),
                "minimum_assignment_margin": float(self.minimum_assignment_margin),
            }
        )


@dataclass(frozen=True, slots=True)
class DiarizationModelIdentity:
    """Pinned executable identity of an audio diarization Adapter.

    A provider label is insufficient evidence.  The model revision is a
    content revision, runtime components are measured and canonically ordered,
    and code/config hashes are mandatory so ``latest`` can never enter a
    persisted speaker claim.
    """

    adapter_name: str
    adapter_version: str
    model: str
    model_revision: str
    runtime_components: tuple[tuple[str, str], ...]
    runtime_hash: str
    adapter_code_hash: str
    config_hash: str
    execution_mode: Literal["local", "import", "subscription", "paid_api", "other"]

    def __post_init__(self) -> None:
        object.__setattr__(self, "runtime_components", tuple(self.runtime_components))
        floating = {"latest", "main", "master", "head", "floating", "unknown", "unpinned"}
        for label, value in (
            ("adapter_name", self.adapter_name),
            ("adapter_version", self.adapter_version),
            ("model", self.model),
        ):
            if not isinstance(value, str) or not value.strip() or value != value.strip():
                raise ValueError(f"Diarization identity {label} must be non-blank and trimmed")
            if value.casefold() in floating:
                raise ValueError(f"Diarization identity {label} must not float")
        if not isinstance(self.model_revision, str) or not re.fullmatch(
            r"(?:[0-9a-f]{40}|[0-9a-f]{64})", self.model_revision
        ):
            raise ValueError("Diarization model_revision must be a pinned content revision")
        if not self.runtime_components:
            raise ValueError("Diarization identity requires measured runtime components")
        if self.runtime_components != tuple(sorted(self.runtime_components)):
            raise ValueError("Diarization runtime components must use canonical name order")
        names: list[str] = []
        for name, value in self.runtime_components:
            if (
                not isinstance(name, str)
                or not name.strip()
                or name != name.strip()
                or not isinstance(value, str)
                or not value.strip()
                or value != value.strip()
                or value.casefold() in floating
            ):
                raise ValueError("Diarization runtime components must be pinned and trimmed")
            names.append(name)
        if len(set(names)) != len(names):
            raise ValueError("Diarization runtime component names must be unique")
        if self.runtime_hash != hash_object({"runtime_components": self.runtime_components}):
            raise ValueError("Diarization runtime_hash must cover exact runtime components")
        for label, digest in (
            ("runtime_hash", self.runtime_hash),
            ("adapter_code_hash", self.adapter_code_hash),
            ("config_hash", self.config_hash),
        ):
            if not isinstance(digest, str) or not re.fullmatch(r"[0-9a-f]{64}", digest):
                raise ValueError(f"Diarization identity {label} must be lowercase SHA-256")
        if self.execution_mode not in {
            "local",
            "import",
            "subscription",
            "paid_api",
            "other",
        }:
            raise ValueError("Diarization identity execution_mode is invalid")

    @property
    def content_hash(self) -> str:
        return hash_object(self)


@dataclass(frozen=True, slots=True)
class DiarizationRequest:
    """Exact normalized audio and base Recognition Evidence to attribute."""

    episode_id: str
    invocation_id: str
    normalized_audio: Path
    expected_normalized_audio_hash: str
    expected_normalized_audio_size_bytes: int
    normalized_audio_duration_ms: int
    base_evidence: RecognitionEvidence
    raw_output_dir: Path

    def __post_init__(self) -> None:
        object.__setattr__(self, "normalized_audio", Path(self.normalized_audio))
        object.__setattr__(self, "raw_output_dir", Path(self.raw_output_dir))
        if not self.episode_id.strip() or not self.invocation_id.strip():
            raise ValueError("Diarization requires episode and invocation IDs")
        if not re.fullmatch(r"[0-9a-f]{64}", self.expected_normalized_audio_hash):
            raise ValueError("Diarization audio hash must be lowercase SHA-256")
        if (
            type(self.expected_normalized_audio_size_bytes) is not int
            or self.expected_normalized_audio_size_bytes <= 0
        ):
            raise ValueError("Diarization audio size must be positive")
        if (
            type(self.normalized_audio_duration_ms) is not int
            or self.normalized_audio_duration_ms <= 0
        ):
            raise ValueError("Diarization audio duration must be positive")
        base = self.base_evidence
        if base.episode_id != self.episode_id or base.invocation_id != self.invocation_id:
            raise ValueError("Diarization base Evidence belongs to another invocation")
        if base.normalized_audio_hash != self.expected_normalized_audio_hash:
            raise ValueError("Diarization base Evidence uses another normalized-audio clock")
        if any(token.end_ms > self.normalized_audio_duration_ms for token in base.tokens):
            raise ValueError("Diarization base Evidence exceeds normalized audio duration")
        if any(token.speaker is not None for token in base.tokens):
            raise ValueError("Diarization cannot overwrite existing speaker Evidence")

    @property
    def base_evidence_hash(self) -> str:
        return recognition_evidence_content_hash(self.base_evidence)


@dataclass(frozen=True, slots=True)
class DiarizationExecutionReceipt:
    """Portable proof of one complete opaque-speaker materialization."""

    id: str
    episode_id: str
    invocation_id: str
    normalized_audio: ArtifactDigest
    normalized_audio_duration_ms: int
    base_recognition_evidence_hash: str
    base_token_ids: tuple[str, ...]
    request: ArtifactDigest
    response: ArtifactDigest
    adapter_identity: DiarizationModelIdentity
    policy_hash: str
    speaker_labels: tuple[str, ...]
    token_assignment_hash: str
    materialized_recognition_evidence_hash: str

    def __post_init__(self) -> None:
        object.__setattr__(self, "base_token_ids", tuple(self.base_token_ids))
        object.__setattr__(self, "speaker_labels", tuple(self.speaker_labels))
        for label, value in (
            ("id", self.id),
            ("episode_id", self.episode_id),
            ("invocation_id", self.invocation_id),
        ):
            if not isinstance(value, str) or not value.strip():
                raise ValueError(f"Diarization receipt {label} must not be blank")
        if not self.base_token_ids or len(set(self.base_token_ids)) != len(self.base_token_ids):
            raise ValueError("Diarization receipt requires unique base token IDs")
        if len(self.speaker_labels) < 2 or len(set(self.speaker_labels)) != len(
            self.speaker_labels
        ):
            raise ValueError("Diarization receipt requires distinct opaque speakers")
        if self.speaker_labels != tuple(
            f"speaker_{index:04d}" for index in range(len(self.speaker_labels))
        ):
            raise ValueError("Diarization receipt speakers must be canonical opaque labels")
        if (
            type(self.normalized_audio_duration_ms) is not int
            or self.normalized_audio_duration_ms <= 0
        ):
            raise ValueError("Diarization receipt audio duration must be positive")
        for label, digest in (
            ("base_recognition_evidence_hash", self.base_recognition_evidence_hash),
            ("policy_hash", self.policy_hash),
            ("token_assignment_hash", self.token_assignment_hash),
            (
                "materialized_recognition_evidence_hash",
                self.materialized_recognition_evidence_hash,
            ),
        ):
            if not isinstance(digest, str) or not re.fullmatch(r"[0-9a-f]{64}", digest):
                raise ValueError(f"Diarization receipt {label} must be lowercase SHA-256")
        if self.policy_hash != self.adapter_identity.config_hash:
            raise ValueError("Diarization receipt policy differs from Adapter identity")
        expected_id = "speaker-diarization-receipt-" + hash_object(
            {
                "request": self.request.sha256,
                "response": self.response.sha256,
                "adapter_identity_hash": self.adapter_identity.content_hash,
                "policy_hash": self.policy_hash,
                "base_recognition_evidence_hash": self.base_recognition_evidence_hash,
                "token_assignment_hash": self.token_assignment_hash,
            }
        )
        if self.id != expected_id:
            raise ValueError("Diarization receipt ID does not cover its exact provenance")

    @property
    def content_hash(self) -> str:
        return hash_object(
            {
                "id": self.id,
                "episode_id": self.episode_id,
                "invocation_id": self.invocation_id,
                "normalized_audio": {
                    "sha256": self.normalized_audio.sha256,
                    "size_bytes": self.normalized_audio.size_bytes,
                },
                "normalized_audio_duration_ms": self.normalized_audio_duration_ms,
                "base_recognition_evidence_hash": self.base_recognition_evidence_hash,
                "base_token_ids": self.base_token_ids,
                "request": {
                    "sha256": self.request.sha256,
                    "size_bytes": self.request.size_bytes,
                },
                "response": {
                    "sha256": self.response.sha256,
                    "size_bytes": self.response.size_bytes,
                },
                "adapter_identity_hash": self.adapter_identity.content_hash,
                "policy_hash": self.policy_hash,
                "speaker_labels": self.speaker_labels,
                "token_assignment_hash": self.token_assignment_hash,
                "materialized_recognition_evidence_hash": (
                    self.materialized_recognition_evidence_hash
                ),
            }
        )


@dataclass(frozen=True, slots=True)
class DiarizationRunResult:
    """New immutable Recognition Evidence plus all bytes needed for replay."""

    evidence: RecognitionEvidence
    receipt: DiarizationExecutionReceipt
    request_bytes: bytes = field(repr=False, metadata={"canonical_json": False})
    response_bytes: bytes = field(repr=False, metadata={"canonical_json": False})

    def __post_init__(self) -> None:
        if (
            sha256_bytes(self.request_bytes) != self.receipt.request.sha256
            or len(self.request_bytes) != self.receipt.request.size_bytes
        ):
            raise ValueError("Diarization request bytes differ from receipt")
        if (
            sha256_bytes(self.response_bytes) != self.receipt.response.sha256
            or len(self.response_bytes) != self.receipt.response.size_bytes
        ):
            raise ValueError("Diarization response bytes differ from receipt")
        if (
            recognition_evidence_content_hash(self.evidence)
            != self.receipt.materialized_recognition_evidence_hash
        ):
            raise ValueError("Diarization Evidence differs from receipt")
        if (
            self.evidence.episode_id != self.receipt.episode_id
            or self.evidence.invocation_id != self.receipt.invocation_id
            or self.evidence.normalized_audio_hash != self.receipt.normalized_audio.sha256
            or self.evidence.raw_output.sha256 != self.receipt.response.sha256
        ):
            raise ValueError("Diarization Evidence crossed receipt lineage")

    @property
    def evidence_hash(self) -> str:
        return recognition_evidence_content_hash(self.evidence)


@dataclass(frozen=True, slots=True)
class ReferenceRetrievalRequest:
    """Versioned exact per-anchor query reconstructed from Canonical truth."""

    schema_version: Literal[2] = field(default=2, init=False)
    episode_id: str
    invocation_id: str
    context: ReferenceQueryContext
    policy: ReferenceRetrievalPolicySnapshot
    candidate_terms: tuple[str, ...] = ()
    allowed_artifact_ids: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if not self.episode_id.strip() or not self.invocation_id.strip():
            raise ValueError("reference retrieval requires episode and invocation IDs")
        if self.context.policy_hash != reference_retrieval_policy_hash(self.policy):
            raise ValueError("reference retrieval context differs from its policy")
        if len(self.candidate_terms) > self.policy.max_candidate_terms:
            raise ValueError("reference retrieval candidate terms exceed policy")
        for label, values in (
            ("candidate_terms", self.candidate_terms),
            ("allowed_artifact_ids", self.allowed_artifact_ids),
        ):
            if any(not item.strip() or item != item.strip() for item in values):
                raise ValueError(f"reference retrieval {label} must be non-blank and trimmed")
            if len({item.casefold() for item in values}) != len(values):
                raise ValueError(f"reference retrieval {label} must be case-insensitively unique")

    @property
    def audio_span_id(self) -> str:
        return self.context.anchor_span_id

    @property
    def observed_text(self) -> str:
        return self.context.exact_query

    @property
    def max_results(self) -> int:
        return self.policy.max_results

    @property
    def content_hash(self) -> str:
        return hash_object(self)


# Transitional names remain aliases only; the shared frozen contracts above are
# the single source of truth across the Module, adapters, and manifests.
ReferenceSnippet = ReferenceEvidence
ReferenceRetrievalResult = ReferenceRetrievalReceipt


@dataclass(frozen=True, slots=True)
class CorrectionRequest:
    """Evidence plus editorial context from which correction risks are proposed."""

    episode_id: str
    generation_id: str
    mode: CorrectionMode
    transcript: CanonicalTranscript
    target_span_ids: tuple[str, ...]
    evidence: tuple[RecognitionEvidence, ...]
    review_issues: tuple[ReviewIssue, ...] = ()
    reference_evidence: tuple[ReferenceEvidence, ...] = ()
    available_reference_evidence: tuple[ReferenceEvidence, ...] = ()

    def __post_init__(self) -> None:
        if not self.available_reference_evidence and self.reference_evidence:
            object.__setattr__(
                self,
                "available_reference_evidence",
                tuple(self.reference_evidence),
            )
        available = {item.id: item for item in self.available_reference_evidence}
        if len(available) != len(self.available_reference_evidence):
            raise ValueError("CorrectionRequest available Reference Evidence IDs must be unique")
        if any(available.get(item.id) != item for item in self.reference_evidence):
            raise ValueError(
                "CorrectionRequest presented Reference Evidence must be an exact available subset"
            )
        if self.episode_id != self.transcript.episode_id:
            raise ValueError("CorrectionRequest episode differs from Canonical Transcript")
        if self.generation_id != self.transcript.generation_id:
            raise ValueError("CorrectionRequest generation differs from Canonical Transcript")
        if not self.evidence:
            raise ValueError("CorrectionRequest requires Recognition Evidence")
        if any(item.episode_id != self.episode_id for item in self.evidence):
            raise ValueError("CorrectionRequest Evidence belongs to another episode")
        if not self.target_span_ids:
            raise ValueError("CorrectionRequest requires target_span_ids")
        if len(set(self.target_span_ids)) != len(self.target_span_ids):
            raise ValueError("CorrectionRequest target_span_ids must be unique")
        span_positions = {span.id: index for index, span in enumerate(self.transcript.spans)}
        unknown_spans = set(self.target_span_ids) - set(span_positions)
        if unknown_spans:
            raise ValueError(
                f"CorrectionRequest targets unknown Transcript spans: {sorted(unknown_spans)!r}"
            )
        target_positions = [span_positions[span_id] for span_id in self.target_span_ids]
        if target_positions != sorted(target_positions):
            raise ValueError("CorrectionRequest target_span_ids must follow Transcript order")
        issue_ids = [issue.id for issue in self.review_issues]
        if len(set(issue_ids)) != len(issue_ids):
            raise ValueError("CorrectionRequest review issue IDs must be unique")
        unresolved = {
            issue.id: issue
            for issue in self.transcript.review_issues
            if issue.status == "unresolved"
        }
        for issue in self.review_issues:
            if unresolved.get(issue.id) != issue:
                raise ValueError(
                    "CorrectionRequest review_issues must be exact unresolved Transcript issues"
                )
        if self.mode == "targeted_review":
            if not any(
                issue.severity in {"medium", "high", "blocking"} for issue in self.review_issues
            ):
                raise ValueError("targeted_review requires at least one material unresolved issue")
            issue_spans = {span_id for issue in self.review_issues for span_id in issue.span_ids}
            uncovered = issue_spans - set(self.target_span_ids)
            if uncovered:
                raise ValueError(
                    f"targeted_review target_span_ids must cover its issues: {sorted(uncovered)!r}"
                )


@dataclass(frozen=True, slots=True)
class CorrectionProposal:
    """Provider-neutral proposed reading for one stable audio span.

    A text Corrector may cite only Recognition/Reference Evidence.  The
    ``audio*`` bases are reserved for an AudioAuditor that actually received a
    clip; the Module enforces that role separation.
    """

    id: str
    audio_span_ids: tuple[str, ...]
    start_ms: int
    end_ms: int
    evidence_token_ids: tuple[str, ...]
    observed_text: str
    candidate_text: str
    confidence: float | None
    rationale: str
    source: str
    reference_evidence_ids: tuple[str, ...] = ()
    evidence_basis: Literal[
        "recognition",
        "recognition_and_reference",
        "reference_only",
        "audio",
        "audio_and_reference",
    ] = "recognition"

    def __post_init__(self) -> None:
        for field_name in (
            "audio_span_ids",
            "evidence_token_ids",
            "reference_evidence_ids",
        ):
            object.__setattr__(self, field_name, tuple(getattr(self, field_name)))
        if not self.id or not self.audio_span_ids:
            raise ValueError("correction proposal requires stable IDs")
        if len(set(self.audio_span_ids)) != len(self.audio_span_ids):
            raise ValueError("correction proposal audio_span_ids must be unique")
        if self.start_ms < 0 or self.end_ms <= self.start_ms:
            raise ValueError("correction proposal requires a positive audio range")
        if not self.evidence_token_ids:
            raise ValueError("correction proposal requires evidence_token_ids")
        if not self.observed_text.strip() or not self.candidate_text.strip():
            raise ValueError("correction proposal text must not be blank")
        if self.confidence is not None and not 0.0 <= self.confidence <= 1.0:
            raise ValueError("correction proposal confidence must be between 0 and 1")
        if len(set(self.reference_evidence_ids)) != len(self.reference_evidence_ids):
            raise ValueError("reference_evidence_ids must be unique")
        if self.evidence_basis in {
            "recognition_and_reference",
            "audio_and_reference",
            "reference_only",
        } and not (self.reference_evidence_ids):
            raise ValueError("reference-backed proposal requires reference_evidence_ids")
        if self.evidence_basis in {"audio", "recognition"} and self.reference_evidence_ids:
            raise ValueError(
                "proposal carrying Reference Evidence must declare a reference-backed basis"
            )


@dataclass(frozen=True, slots=True)
class CorrectionModelIdentity:
    """Exact executable identity of one text Corrector Adapter.

    Unlike the transitional module-level ``AdapterIdentity``, every field is
    measured by the production Adapter and accompanies each execution receipt.
    The model name alone is not an executable identity: code, runtime, prompt /
    packet policy, and execution mode can all change the result bytes.
    """

    adapter_name: str
    adapter_version: str
    model: str
    model_version: str
    runtime_hash: str
    adapter_code_hash: str
    config_hash: str
    execution_mode: Literal["fixture", "local", "paid_api", "subscription", "other"]

    def __post_init__(self) -> None:
        for label, value in (
            ("adapter_name", self.adapter_name),
            ("adapter_version", self.adapter_version),
            ("model", self.model),
            ("model_version", self.model_version),
        ):
            if not isinstance(value, str) or not value.strip():
                raise ValueError(f"{label} must not be blank")
        for label, value in (
            ("runtime_hash", self.runtime_hash),
            ("adapter_code_hash", self.adapter_code_hash),
            ("config_hash", self.config_hash),
        ):
            if not isinstance(value, str) or not re.fullmatch(r"[0-9a-f]{64}", value):
                raise ValueError(f"{label} must be lowercase SHA-256")

    @property
    def content_hash(self) -> str:
        return hash_object(self)


@dataclass(frozen=True, slots=True)
class CorrectionExecutionReceipt:
    """Strict-import proof for one deterministic Corrector work packet.

    The two locators identify the exact bytes delivered to and returned by the
    runner.  Pretty-printing, reparsing, or hashing only the logical JSON object
    would lose that evidence and is therefore deliberately insufficient.
    """

    work_packet_id: str
    target_span_ids: tuple[str, ...]
    presented_reference_evidence_ids: tuple[str, ...]
    presented_reference_evidence_hash: str
    request: ArtifactDigest
    response: ArtifactDigest
    adapter_identity: CorrectionModelIdentity
    proposal_ids: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if not self.work_packet_id.strip() or not self.target_span_ids:
            raise ValueError("Correction execution receipt requires packet and span IDs")
        if len(set(self.target_span_ids)) != len(self.target_span_ids):
            raise ValueError("Correction execution target span IDs must be unique")
        if len(set(self.presented_reference_evidence_ids)) != len(
            self.presented_reference_evidence_ids
        ):
            raise ValueError("Correction execution presented Reference Evidence IDs must be unique")
        if not re.fullmatch(r"[0-9a-f]{64}", self.presented_reference_evidence_hash):
            raise ValueError(
                "Correction execution presented Reference Evidence hash must be lowercase SHA-256"
            )
        if len(set(self.proposal_ids)) != len(self.proposal_ids):
            raise ValueError("Correction execution proposal IDs must be unique")
        for label, artifact in (("request", self.request), ("response", self.response)):
            expected_prefix = f"generation-artifact://correction/{label}s/"
            if not artifact.uri.startswith(expected_prefix):
                raise ValueError(f"Corrector {label} must use a portable Generation artifact URI")

    @property
    def request_hash(self) -> str:
        """Compatibility name used by the typed Canonical audit receipt."""

        return self.request.sha256

    @property
    def response_hash(self) -> str:
        """Compatibility name used by the typed Canonical audit receipt."""

        return self.response.sha256

    @property
    def adapter_identity_hash(self) -> str:
        return self.adapter_identity.content_hash


@dataclass(frozen=True, slots=True)
class CorrectionRunResult:
    """Correction proposals plus mandatory proof that every response was imported."""

    proposals: tuple[CorrectionProposal, ...]
    execution_receipts: tuple[CorrectionExecutionReceipt, ...]
    request_bytes: tuple[bytes, ...] = field(
        repr=False,
        metadata={"canonical_json": False},
    )
    response_bytes: tuple[bytes, ...] = field(
        repr=False,
        metadata={"canonical_json": False},
    )

    def __post_init__(self) -> None:
        if not self.execution_receipts:
            raise ValueError("Correction run requires at least one execution receipt")
        proposal_ids = tuple(proposal.id for proposal in self.proposals)
        if len(set(proposal_ids)) != len(proposal_ids):
            raise ValueError("Correction run proposal IDs must be unique")
        packet_ids = tuple(receipt.work_packet_id for receipt in self.execution_receipts)
        if len(set(packet_ids)) != len(packet_ids):
            raise ValueError("Correction execution work packet IDs must be unique")
        if len(self.request_bytes) != len(self.execution_receipts) or len(
            self.response_bytes
        ) != len(self.execution_receipts):
            raise ValueError("Correction run bytes must match its ordered receipt set")
        for receipt, request_bytes, response_bytes in zip(
            self.execution_receipts,
            self.request_bytes,
            self.response_bytes,
        ):
            _verify_execution_artifact(receipt.request, request_bytes, label="request")
            _verify_execution_artifact(receipt.response, response_bytes, label="response")
        receipt_proposal_ids = tuple(
            proposal_id
            for receipt in self.execution_receipts
            for proposal_id in receipt.proposal_ids
        )
        if sorted(proposal_ids) != sorted(receipt_proposal_ids):
            raise ValueError("Correction execution receipts must account for every proposal")

    @property
    def artifacts(self) -> dict[str, bytes]:
        """Portable content-addressed bytes ready for a Generation commit."""

        result: dict[str, bytes] = {}
        for receipt, request_bytes, response_bytes in zip(
            self.execution_receipts,
            self.request_bytes,
            self.response_bytes,
        ):
            for artifact, payload in (
                (receipt.request, request_bytes),
                (receipt.response, response_bytes),
            ):
                name = artifact.uri.removeprefix("generation-artifact://")
                existing = result.get(name)
                if existing is not None and existing != payload:
                    raise ValueError("Correction artifact digest has conflicting bytes")
                result[name] = payload
        return result


ArbitrationStatus = Literal["accepted", "rejected", "unresolved"]


@dataclass(frozen=True, slots=True)
class AudioModelIdentity:
    """Exact executable identity of one audio-capable Adapter."""

    adapter_name: str
    adapter_version: str
    model: str
    model_version: str
    runtime_hash: str
    adapter_code_hash: str
    config_hash: str
    execution_mode: Literal["fixture", "local", "paid_api", "subscription", "other"]

    def __post_init__(self) -> None:
        for label, value in (
            ("adapter_name", self.adapter_name),
            ("adapter_version", self.adapter_version),
            ("model", self.model),
            ("model_version", self.model_version),
        ):
            if not isinstance(value, str) or not value.strip():
                raise ValueError(f"{label} must not be blank")
        for label, value in (
            ("runtime_hash", self.runtime_hash),
            ("adapter_code_hash", self.adapter_code_hash),
            ("config_hash", self.config_hash),
        ):
            if not isinstance(value, str) or not re.fullmatch(r"[0-9a-f]{64}", value):
                raise ValueError(f"{label} must be lowercase SHA-256")

    @property
    def content_hash(self) -> str:
        return hash_object(self)


@dataclass(frozen=True, slots=True)
class AudioAuditRequest:
    """One complete Canonical window plus its exact normalized-audio CAS path."""

    transcript: CanonicalTranscript
    normalized_audio: Path
    expected_normalized_audio_hash: str
    normalized_audio_duration_ms: int
    target_span_ids: tuple[str, ...]
    evidence: tuple[RecognitionEvidence, ...]
    reference_evidence: tuple[ReferenceEvidence, ...] = ()

    def __post_init__(self) -> None:
        object.__setattr__(self, "normalized_audio", Path(self.normalized_audio))
        if self.expected_normalized_audio_hash != self.transcript.normalized_audio_hash:
            raise ValueError("audio audit normalized-audio hash crossed Canonical lineage")
        if self.normalized_audio_duration_ms <= 0:
            raise ValueError("audio audit requires positive normalized-audio duration")
        if not self.target_span_ids or len(set(self.target_span_ids)) != len(self.target_span_ids):
            raise ValueError("audio audit requires unique target span IDs")
        positions = {span.id: index for index, span in enumerate(self.transcript.spans)}
        unknown = set(self.target_span_ids) - set(positions)
        if unknown:
            raise ValueError(f"audio audit targets unknown spans: {sorted(unknown)!r}")
        indices = [positions[span_id] for span_id in self.target_span_ids]
        if indices != list(range(indices[0], indices[-1] + 1)):
            raise ValueError("audio audit target spans must be ordered and contiguous")
        if not self.evidence:
            raise ValueError("audio audit requires Recognition Evidence")
        if any(
            item.episode_id != self.transcript.episode_id
            or item.normalized_audio_hash != self.expected_normalized_audio_hash
            for item in self.evidence
        ):
            raise ValueError("audio audit Recognition Evidence crossed transcript lineage")
        if len({item.id for item in self.reference_evidence}) != len(self.reference_evidence):
            raise ValueError("audio audit Reference Evidence IDs must be unique")

    @property
    def window_start_ms(self) -> int:
        spans = {span.id: span for span in self.transcript.spans}
        return spans[self.target_span_ids[0]].start_ms

    @property
    def window_end_ms(self) -> int:
        spans = {span.id: span for span in self.transcript.spans}
        return spans[self.target_span_ids[-1]].end_ms


def _verify_execution_artifact(
    artifact: ArtifactDigest,
    payload: bytes,
    *,
    label: str,
) -> None:
    if not isinstance(payload, bytes):
        raise TypeError(f"{label} payload must be immutable bytes")
    if sha256_bytes(payload) != artifact.sha256 or len(payload) != artifact.size_bytes:
        raise ValueError(f"{label} payload differs from its ArtifactDigest")


@dataclass(frozen=True, slots=True)
class AudioAuditRunResult:
    """Typed receipt plus every byte required to replay one audio review."""

    proposals: tuple[CorrectionProposal, ...]
    receipt: AudioAuditReceipt
    request_bytes: bytes = field(repr=False, metadata={"canonical_json": False})
    response_bytes: bytes = field(repr=False, metadata={"canonical_json": False})
    clip_bytes: bytes = field(repr=False, metadata={"canonical_json": False})

    def __post_init__(self) -> None:
        _verify_execution_artifact(self.receipt.request, self.request_bytes, label="request")
        _verify_execution_artifact(self.receipt.response, self.response_bytes, label="response")
        _verify_execution_artifact(self.receipt.clip, self.clip_bytes, label="clip")
        proposal_ids = tuple(item.id for item in self.proposals)
        if proposal_ids != self.receipt.proposal_ids:
            raise ValueError("audio audit receipt does not list its exact proposals")
        if self.receipt.proposal_set_hash != hash_object(self.proposals):
            raise ValueError("audio audit proposal-set hash mismatch")

    @property
    def artifacts(self) -> dict[str, bytes]:
        return {
            self.receipt.request.uri.removeprefix("generation-artifact://"): self.request_bytes,
            self.receipt.response.uri.removeprefix("generation-artifact://"): self.response_bytes,
            self.receipt.clip.uri.removeprefix("generation-artifact://"): self.clip_bytes,
        }


@dataclass(frozen=True, slots=True)
class ArbitrationRequest:
    """One disputed proposal plus the audio needed for evidence-backed review."""

    episode_id: str
    generation_id: str
    transcript: CanonicalTranscript
    normalized_audio: Path
    expected_normalized_audio_hash: str
    proposal: CorrectionProposal
    reference_evidence: tuple[ReferenceEvidence, ...] = ()
    normalized_audio_duration_ms: int | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "normalized_audio", Path(self.normalized_audio))
        if self.episode_id != self.transcript.episode_id:
            raise ValueError("arbitration episode differs from Canonical Transcript")
        if self.generation_id != self.transcript.generation_id:
            raise ValueError("arbitration Generation differs from Canonical Transcript")
        if self.expected_normalized_audio_hash != self.transcript.normalized_audio_hash:
            raise ValueError("arbitration normalized-audio hash crossed Canonical lineage")
        if len(self.expected_normalized_audio_hash) != 64 or any(
            character not in "0123456789abcdef" for character in self.expected_normalized_audio_hash
        ):
            raise ValueError("expected_normalized_audio_hash must be lowercase SHA-256")
        if self.normalized_audio_duration_ms is not None and self.normalized_audio_duration_ms <= 0:
            raise ValueError("normalized_audio_duration_ms must be positive")
        if (
            self.normalized_audio_duration_ms is not None
            and self.proposal.end_ms > self.normalized_audio_duration_ms
        ):
            raise ValueError("arbitration proposal exceeds Normalized Audio duration")


@dataclass(frozen=True, slots=True)
class ArbitrationVerdict:
    """Resolved reading or an explicit unresolved result; never an implicit guess."""

    proposal_id: str
    status: ArbitrationStatus
    selected_text: str | None
    confidence: float | None
    rationale: str

    def __post_init__(self) -> None:
        if not self.proposal_id:
            raise ValueError("arbitration verdict requires proposal_id")
        if self.status == "unresolved" and self.selected_text is not None:
            raise ValueError("unresolved verdict cannot select text")
        if self.status != "unresolved" and not (self.selected_text or "").strip():
            raise ValueError("resolved verdict requires selected_text")
        if self.confidence is not None and not 0.0 <= self.confidence <= 1.0:
            raise ValueError("arbitration confidence must be between 0 and 1")


@dataclass(frozen=True, slots=True)
class ArbitrationRunResult:
    """One verdict with replayable request, response, and heard clip bytes."""

    verdict: ArbitrationVerdict
    receipt: ArbitrationReceipt
    request_bytes: bytes = field(repr=False, metadata={"canonical_json": False})
    response_bytes: bytes = field(repr=False, metadata={"canonical_json": False})
    clip_bytes: bytes = field(repr=False, metadata={"canonical_json": False})

    def __post_init__(self) -> None:
        _verify_execution_artifact(self.receipt.request, self.request_bytes, label="request")
        _verify_execution_artifact(self.receipt.response, self.response_bytes, label="response")
        _verify_execution_artifact(self.receipt.clip, self.clip_bytes, label="clip")
        if self.verdict.proposal_id != self.receipt.proposal_id:
            raise ValueError("Arbitration Receipt belongs to another proposal")
        if (
            self.verdict.status != self.receipt.status
            or self.verdict.selected_text != self.receipt.selected_text
            or self.verdict.confidence != self.receipt.confidence
            or self.verdict.rationale != self.receipt.rationale
        ):
            raise ValueError("Arbitration Receipt differs from its verdict")

    @property
    def artifacts(self) -> dict[str, bytes]:
        return {
            self.receipt.request.uri.removeprefix("generation-artifact://"): self.request_bytes,
            self.receipt.response.uri.removeprefix("generation-artifact://"): self.response_bytes,
            self.receipt.clip.uri.removeprefix("generation-artifact://"): self.clip_bytes,
        }


@dataclass(frozen=True, slots=True)
class SemanticAnalysisRequest:
    """Canonical truth to partition into semantic ranges and forbidden cuts."""

    transcript: CanonicalTranscript
    language: str = "zh-Hant-TW"
    policy_hash: str | None = None
    protected_ranges: tuple[ProtectedTokenRange, ...] = ()

    def __post_init__(self) -> None:
        if not self.language.strip():
            raise ValueError("Semantic Analysis language must not be blank")
        effective_policy_hash = self.policy_hash or self.transcript.policy_hash
        if effective_policy_hash != self.transcript.policy_hash:
            raise ValueError("Semantic Analysis policy crossed Canonical Generation")
        if not re.fullmatch(r"[0-9a-f]{64}", effective_policy_hash):
            raise ValueError("Semantic Analysis policy_hash must be lowercase SHA-256")
        object.__setattr__(self, "policy_hash", effective_policy_hash)
        object.__setattr__(self, "protected_ranges", tuple(self.protected_ranges))
        positions = {token.id: index for index, token in enumerate(self.transcript.tokens)}
        seen_ids: set[str] = set()
        for protected in self.protected_ranges:
            if protected.id in seen_ids:
                raise ValueError("Semantic Analysis protected range IDs must be unique")
            seen_ids.add(protected.id)
            try:
                indices = [positions[token_id] for token_id in protected.token_ids]
            except KeyError as exc:
                raise ValueError(
                    f"Semantic protected range references unknown token {exc.args[0]!r}"
                ) from exc
            if indices != list(range(indices[0], indices[-1] + 1)):
                raise ValueError("Semantic protected ranges must be ordered and contiguous")
            text = "".join(self.transcript.tokens[index].text for index in indices)
            if text != protected.canonical_text:
                raise ValueError("Semantic protected range text differs from Canonical truth")

    @property
    def protected_range_set_hash(self) -> str:
        return hash_object(self.protected_ranges)


@dataclass(frozen=True, slots=True)
class SemanticModelIdentity:
    """Measured executable identity of one Semantic Analyzer Adapter."""

    adapter_name: str
    adapter_version: str
    model: str
    model_version: str
    runtime_hash: str
    adapter_code_hash: str
    config_hash: str
    execution_mode: Literal["fixture", "local", "paid_api", "subscription", "other"]

    def __post_init__(self) -> None:
        for label, value in (
            ("adapter_name", self.adapter_name),
            ("adapter_version", self.adapter_version),
            ("model", self.model),
            ("model_version", self.model_version),
        ):
            if not isinstance(value, str) or not value.strip():
                raise ValueError(f"{label} must not be blank")
        for label, value in (
            ("runtime_hash", self.runtime_hash),
            ("adapter_code_hash", self.adapter_code_hash),
            ("config_hash", self.config_hash),
        ):
            if not isinstance(value, str) or not re.fullmatch(r"[0-9a-f]{64}", value):
                raise ValueError(f"{label} must be lowercase SHA-256")

    @property
    def content_hash(self) -> str:
        return hash_object(self)


@dataclass(frozen=True, slots=True)
class SemanticExecutionReceipt:
    """Replay proof for one bounded Semantic Analyzer packet.

    Target tokens may overlap adjacent packets so a boundary is never hidden at
    a packet seam.  ``owned_token_ids`` remain an exact, disjoint Canonical token
    partition. Production boundary-centric receipts additionally use
    ``owned_boundary_indices`` to partition every adjacent edge exactly once;
    legacy token-only ownership is retained only for test/migration Adapters.
    """

    episode_id: str
    generation_id: str
    canonical_content_hash: str
    work_packet_id: str
    packet_index: int
    packet_count: int
    target_token_ids: tuple[str, ...]
    owned_token_ids: tuple[str, ...]
    request: ArtifactDigest
    response: ArtifactDigest
    adapter_identity: SemanticModelIdentity
    semantic_unit_ids: tuple[str, ...]
    owned_boundary_indices: tuple[int, ...] = ()
    boundary_ownership_contract: Literal["legacy-token-only", "canonical-adjacent-edge-v1"] = (
        "legacy-token-only"
    )

    def __post_init__(self) -> None:
        for field_name in (
            "target_token_ids",
            "owned_token_ids",
            "semantic_unit_ids",
            "owned_boundary_indices",
        ):
            object.__setattr__(self, field_name, tuple(getattr(self, field_name)))
        for label, value in (
            ("episode_id", self.episode_id),
            ("generation_id", self.generation_id),
            ("work_packet_id", self.work_packet_id),
        ):
            if not isinstance(value, str) or not value.strip():
                raise ValueError(f"Semantic execution {label} must not be blank")
        if not re.fullmatch(r"[0-9a-f]{64}", self.canonical_content_hash):
            raise ValueError("Semantic execution canonical hash must be lowercase SHA-256")
        if self.packet_count < 1 or not 0 <= self.packet_index < self.packet_count:
            raise ValueError("Semantic execution packet position is invalid")
        if not self.target_token_ids or len(set(self.target_token_ids)) != len(
            self.target_token_ids
        ):
            raise ValueError("Semantic execution requires unique target token IDs")
        if not self.owned_token_ids or len(set(self.owned_token_ids)) != len(self.owned_token_ids):
            raise ValueError("Semantic execution requires unique owned token IDs")
        if not set(self.owned_token_ids).issubset(self.target_token_ids):
            raise ValueError("Semantic execution may own only target tokens")
        if not self.semantic_unit_ids or len(set(self.semantic_unit_ids)) != len(
            self.semantic_unit_ids
        ):
            raise ValueError("Semantic execution requires unique parsed Semantic Unit IDs")
        if len(set(self.owned_boundary_indices)) != len(self.owned_boundary_indices) or any(
            type(index) is not int or index < 1 for index in self.owned_boundary_indices
        ):
            raise ValueError("Semantic execution boundary ownership must be positive and unique")
        if tuple(sorted(self.owned_boundary_indices)) != self.owned_boundary_indices:
            raise ValueError("Semantic execution boundary ownership must be ordered")
        if self.boundary_ownership_contract == "legacy-token-only" and self.owned_boundary_indices:
            raise ValueError("legacy Semantic execution cannot claim boundary ownership")
        for label, artifact in (("request", self.request), ("response", self.response)):
            match = re.fullmatch(
                rf"projection-artifact://semantic/{label}s/([0-9a-f]{{64}})",
                artifact.uri,
            )
            if match is None or match.group(1) != artifact.sha256:
                raise ValueError(
                    f"Semantic {label} must use its exact digest as a portable "
                    "Projection artifact URI"
                )

    @property
    def adapter_identity_hash(self) -> str:
        return self.adapter_identity.content_hash


@dataclass(frozen=True, slots=True)
class SemanticRunResult:
    """Semantic Units plus every byte required to replay their derivation."""

    canonical_token_ids: tuple[str, ...]
    units: tuple[SemanticUnit, ...]
    execution_receipts: tuple[SemanticExecutionReceipt, ...]
    request_bytes: tuple[bytes, ...] = field(
        repr=False,
        metadata={"canonical_json": False},
    )
    response_bytes: tuple[bytes, ...] = field(
        repr=False,
        metadata={"canonical_json": False},
    )

    def __post_init__(self) -> None:
        if not self.canonical_token_ids or len(set(self.canonical_token_ids)) != len(
            self.canonical_token_ids
        ):
            raise ValueError("Semantic run requires unique Canonical token IDs")
        if not self.units or len({unit.id for unit in self.units}) != len(self.units):
            raise ValueError("Semantic run requires unique Semantic Units")
        if not self.execution_receipts:
            raise ValueError("Semantic run requires at least one execution receipt")
        if len(self.request_bytes) != len(self.execution_receipts) or len(
            self.response_bytes
        ) != len(self.execution_receipts):
            raise ValueError("Semantic run bytes must match its ordered receipt set")

        token_positions = {
            token_id: index for index, token_id in enumerate(self.canonical_token_ids)
        }
        packet_ids: set[str] = set()
        owned: list[str] = []
        target_coverage: set[str] = set()
        receipt_unit_ids: set[str] = set()
        owned_boundaries: list[int] = []
        boundary_contracts: set[str] = set()
        first = self.execution_receipts[0]
        expected_count = len(self.execution_receipts)
        for index, (receipt, request_bytes, response_bytes) in enumerate(
            zip(self.execution_receipts, self.request_bytes, self.response_bytes)
        ):
            if receipt.work_packet_id in packet_ids:
                raise ValueError("Semantic execution work packet IDs must be unique")
            packet_ids.add(receipt.work_packet_id)
            if receipt.packet_index != index or receipt.packet_count != expected_count:
                raise ValueError("Semantic execution receipts are reordered or incomplete")
            if (
                receipt.episode_id != first.episode_id
                or receipt.generation_id != first.generation_id
                or receipt.canonical_content_hash != first.canonical_content_hash
                or receipt.adapter_identity != first.adapter_identity
            ):
                raise ValueError("Semantic execution receipts crossed lineage or identity")
            _verify_execution_artifact(receipt.request, request_bytes, label="request")
            _verify_execution_artifact(receipt.response, response_bytes, label="response")
            try:
                indices = [token_positions[token_id] for token_id in receipt.target_token_ids]
            except KeyError as exc:
                raise ValueError(
                    f"Semantic execution targets unknown Canonical token {exc.args[0]!r}"
                ) from exc
            if indices != list(range(indices[0], indices[-1] + 1)):
                raise ValueError("Semantic execution targets must be ordered and contiguous")
            target_coverage.update(receipt.target_token_ids)
            owned.extend(receipt.owned_token_ids)
            receipt_unit_ids.update(receipt.semantic_unit_ids)
            boundary_contracts.add(receipt.boundary_ownership_contract)
            owned_boundaries.extend(receipt.owned_boundary_indices)
            for boundary_index in receipt.owned_boundary_indices:
                if boundary_index >= len(self.canonical_token_ids):
                    raise ValueError("Semantic execution owns an unknown Canonical boundary")
                endpoints = {
                    self.canonical_token_ids[boundary_index - 1],
                    self.canonical_token_ids[boundary_index],
                }
                if not endpoints.issubset(receipt.target_token_ids):
                    raise ValueError("Semantic execution owns a boundary outside its target tokens")

        if tuple(owned) != self.canonical_token_ids:
            raise ValueError("Semantic execution ownership is not an exact Canonical partition")
        if target_coverage != set(self.canonical_token_ids):
            raise ValueError("Semantic execution target coverage is incomplete")
        if receipt_unit_ids != {unit.id for unit in self.units}:
            raise ValueError("Semantic execution receipts do not account for every Semantic Unit")
        if len(boundary_contracts) != 1:
            raise ValueError("Semantic execution mixed boundary ownership contracts")
        boundary_contract = next(iter(boundary_contracts))
        if boundary_contract == "canonical-adjacent-edge-v1":
            if tuple(owned_boundaries) != tuple(range(1, len(self.canonical_token_ids))):
                raise ValueError(
                    "Semantic execution boundary ownership is not an exact adjacent-edge partition"
                )
        elif owned_boundaries:
            raise ValueError("legacy Semantic execution cannot own Canonical boundaries")

    @property
    def artifacts(self) -> dict[str, bytes]:
        result: dict[str, bytes] = {}
        for receipt, request_bytes, response_bytes in zip(
            self.execution_receipts,
            self.request_bytes,
            self.response_bytes,
        ):
            for artifact, payload in (
                (receipt.request, request_bytes),
                (receipt.response, response_bytes),
            ):
                name = artifact.uri.removeprefix("projection-artifact://")
                existing = result.get(name)
                if existing is not None and existing != payload:
                    raise ValueError("Semantic artifact digest has conflicting bytes")
                result[name] = payload
        return result


@runtime_checkable
class Normalizer(Protocol):
    """Seam that hides normalization provider orchestration."""

    def normalize(self, request: NormalizeRequest) -> NormalizationResult: ...


@runtime_checkable
class Recognizer(Protocol):
    """Seam that emits and replays immutable recognition Evidence."""

    @property
    def identity(self) -> RecognitionModelIdentity: ...

    def recognize(self, request: RecognitionRequest) -> RecognitionEvidence: ...

    def verify(
        self,
        evidence: RecognitionEvidence,
        *,
        request: RecognitionRequest,
        raw_output: bytes,
    ) -> RecognitionEvidence: ...


@runtime_checkable
class SpeechCoverageAnalyzer(Protocol):
    """Seam for audio activity evidence that never emits transcript text."""

    @property
    def adapter_name(self) -> str: ...

    @property
    def adapter_version(self) -> str: ...

    @property
    def adapter_config_hash(self) -> str: ...

    @property
    def adapter_code_hash(self) -> str: ...

    @property
    def adapter_runtime_hash(self) -> str: ...

    def analyze(self, request: SpeechCoverageRequest) -> SpeechCoverageReceipt: ...

    def verify(
        self,
        receipt: SpeechCoverageReceipt,
        *,
        request: SpeechCoverageRequest,
        raw_output: bytes,
    ) -> SpeechCoverageReceipt: ...


@runtime_checkable
class SpeakerAttributor(Protocol):
    """Seam that creates new speaker Evidence from one existing ASR result."""

    @property
    def adapter_name(self) -> str: ...

    @property
    def adapter_version(self) -> str: ...

    @property
    def adapter_config_hash(self) -> str: ...

    def attribute(self, request: SpeakerAttributionRequest) -> RecognitionEvidence: ...

    def verify(
        self,
        attributed: RecognitionEvidence,
        *,
        base_evidence: RecognitionEvidence,
        raw_output: bytes,
    ) -> SpeakerAttributionProvenance: ...


@runtime_checkable
class SpeakerDiarizer(Protocol):
    """Audio-only seam that attributes canonical opaque speaker labels."""

    @property
    def identity(self) -> DiarizationModelIdentity: ...

    @property
    def policy(self) -> DiarizationPolicyV1: ...

    def prepare(self, request: DiarizationRequest) -> bytes: ...

    def materialize(
        self,
        request: DiarizationRequest,
        *,
        response_bytes: bytes,
    ) -> DiarizationRunResult: ...

    def verify(
        self,
        request: DiarizationRequest,
        *,
        result: DiarizationRunResult,
    ) -> DiarizationRunResult: ...


@runtime_checkable
class ReferenceRetriever(Protocol):
    """Seam that retrieves exact, content-addressed Reference Evidence passages."""

    def retrieve(self, request: ReferenceRetrievalRequest) -> ReferenceRetrievalReceipt: ...

    def replay(
        self,
        request: ReferenceRetrievalRequest,
        stored: ReferenceRetrievalReceipt,
    ) -> ReferenceRetrievalReceipt: ...


@runtime_checkable
class Corrector(Protocol):
    """Seam that proposes corrections without mutating Evidence or Canonical Transcript."""

    @property
    def identity(self) -> CorrectionModelIdentity: ...

    def propose(self, request: CorrectionRequest) -> tuple[CorrectionProposal, ...]: ...

    def propose_with_receipt(self, request: CorrectionRequest) -> CorrectionRunResult: ...

    def replay(
        self,
        request: CorrectionRequest,
        *,
        proposals: tuple[CorrectionProposal, ...],
        execution_receipts: tuple[CorrectionExecutionReceipt, ...],
        request_bytes: tuple[bytes, ...],
        response_bytes: tuple[bytes, ...],
    ) -> CorrectionRunResult: ...


@runtime_checkable
class Arbiter(Protocol):
    """Seam that resolves a correction dispute or returns unresolved."""

    def decide(self, request: ArbitrationRequest) -> ArbitrationVerdict: ...

    @property
    def identity(self) -> AudioModelIdentity: ...

    def decide_with_receipt(self, request: ArbitrationRequest) -> ArbitrationRunResult: ...

    def replay(
        self,
        request: ArbitrationRequest,
        *,
        verdict: ArbitrationVerdict,
        receipt: ArbitrationReceipt,
        request_bytes: bytes,
        response_bytes: bytes,
        clip_bytes: bytes,
    ) -> ArbitrationRunResult: ...


@runtime_checkable
class AudioAuditor(Protocol):
    """Seam that must hear each complete Canonical window."""

    @property
    def identity(self) -> AudioModelIdentity: ...

    def audit(self, request: AudioAuditRequest) -> AudioAuditRunResult: ...

    def replay(
        self,
        request: AudioAuditRequest,
        *,
        proposals: tuple[CorrectionProposal, ...],
        receipt: AudioAuditReceipt,
        request_bytes: bytes,
        response_bytes: bytes,
        clip_bytes: bytes,
    ) -> AudioAuditRunResult: ...


@runtime_checkable
class SemanticAnalyzer(Protocol):
    """Seam that describes meaning without changing canonical lexemes."""

    @property
    def identity(self) -> SemanticModelIdentity: ...

    def partition(self, request: SemanticAnalysisRequest) -> tuple[SemanticUnit, ...]: ...

    def partition_with_receipts(self, request: SemanticAnalysisRequest) -> SemanticRunResult: ...

    def replay(
        self,
        request: SemanticAnalysisRequest,
        *,
        units: tuple[SemanticUnit, ...],
        execution_receipts: tuple[SemanticExecutionReceipt, ...],
        request_bytes: tuple[bytes, ...],
        response_bytes: tuple[bytes, ...],
    ) -> SemanticRunResult: ...


__all__ = [
    "AdapterError",
    "AdapterInputError",
    "AdapterIntegrityError",
    "AdapterUnavailableError",
    "AdapterWorkPending",
    "Arbiter",
    "ArbitrationRunResult",
    "ArbitrationRequest",
    "ArbitrationStatus",
    "ArbitrationVerdict",
    "AudioAuditRequest",
    "AudioAuditRunResult",
    "AudioAuditor",
    "AudioModelIdentity",
    "CorrectionProposal",
    "CorrectionRequest",
    "CorrectionExecutionReceipt",
    "CorrectionModelIdentity",
    "CorrectionRunResult",
    "Corrector",
    "DiarizationExecutionReceipt",
    "DiarizationModelIdentity",
    "DiarizationPolicyV1",
    "DiarizationRequest",
    "DiarizationRunResult",
    "NormalizationResult",
    "NormalizeRequest",
    "Normalizer",
    "NO_LEXICAL_BIAS_CONTEXT",
    "RecognitionContextPolicyV1",
    "RecognitionRequest",
    "RecognitionModelIdentity",
    "Recognizer",
    "SpeechCoverageAnalyzer",
    "SpeechCoverageRequest",
    "SpeakerAttributionRequest",
    "SpeakerAttributionProvenance",
    "SpeakerAttributor",
    "SpeakerDiarizer",
    "SpeakerTrackBinding",
    "SpeakerTrackInput",
    "SpeakerTrackProvenance",
    "ReferenceArtifact",
    "ReferenceEvidence",
    "ReferenceRetrievalRequest",
    "ReferenceRetrievalReceipt",
    "ReferenceRetrievalResult",
    "ReferenceRetriever",
    "ReferenceSnippet",
    "SemanticAnalysisRequest",
    "SemanticAnalyzer",
    "SemanticExecutionReceipt",
    "SemanticModelIdentity",
    "SemanticRunResult",
]
