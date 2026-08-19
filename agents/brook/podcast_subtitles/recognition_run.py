"""Crash-safe, non-canonical state for one bounded Recognition run.

This module deliberately sits below :class:`PodcastSubtitleV2.create`.  A
``RecognitionRunCheckpoint`` proves only that an exact prefix of one measured
Recognizer execution has been durably observed.  It is not a CreateCheckpoint,
Recognition Evidence, a Generation, or Canonical Transcript truth.

The filesystem repository persists adapter-observed SDK mappings as canonical
JSON.  Those bytes are *not* claimed to be provider wire bytes.  Derived chunk
WAV bytes are verified at commit time but are never copied into the repository;
only their content digest is retained.
"""

from __future__ import annotations

import io
import json
import re
import shutil
import tempfile
import threading
import wave
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal, Mapping, Sequence, TypeVar

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    ValidationError,
    field_validator,
    model_validator,
)

from .errors import IntegrityError
from .hashing import canonical_json_bytes, hash_file, hash_object, sha256_bytes
from .store import _replace_fsynced, _write_fsynced

_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
_RUN_ID_RE = re.compile(r"^recognition-run-[0-9a-f]{64}$")
_CHUNK_ID_RE = re.compile(r"^recognition-chunk-[0-9a-f]{64}$")
_RECEIPT_ID_RE = re.compile(r"^recognition-chunk-receipt-[0-9a-f]{64}$")
_CHECKPOINT_ID_RE = re.compile(r"^recognition-run-checkpoint-[0-9a-f]{64}$")
_HEAD_ID_RE = re.compile(r"^recognition-run-head-[0-9a-f]{64}$")
_FINALIZATION_ID_RE = re.compile(r"^recognition-run-finalization-[0-9a-f]{64}$")
_SLOT_NAME_RE = re.compile(r"^[0-9]{6}$")
_TEMP_FILE_RE = re.compile(
    r"^\.(?:active-head\.json|"
    r"recognition-run-(?:checkpoint|head|finalization)-[0-9a-f]{64}\.json)\.tmp$"
)
_TEMP_SLOT_RE = re.compile(r"^\.slot-[0-9]{6}-[A-Za-z0-9_-]+$")
_TEMP_RUN_RE = re.compile(r"^\.run-[A-Za-z0-9_-]+$")

_PLAN_NAME = "plan.json"
_ACTIVE_HEAD_NAME = "active-head.json"
_EXECUTION_NAME = "execution.json"
_OBSERVATION_NAME = "adapter-observation.json"
_DERIVED_CHUNK_NAME = "derived-chunk.json"

RecognitionExecutionMode = Literal["fixture", "local", "import", "other"]
RecognitionRunFailureCode = Literal[
    "seam_conflict",
    "empty_owned_output",
    "owned_output_overlap",
    "recognition_evidence_rejected",
    "deterministic_postprocessing_failure",
]
FailureScalar = str | int | bool | None


class RecognitionRunIntegrityError(IntegrityError):
    """A Recognition run record, inventory, binding, or chain did not replay."""


class RecognitionRunConflictError(RecognitionRunIntegrityError):
    """An immutable Recognition run slot was reused with different bytes."""


class RecognitionRunNotFoundError(RecognitionRunIntegrityError, FileNotFoundError):
    """The requested Recognition run key has no durable binding."""


class _RecognitionRunContract(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)


def _required_text(value: str, *, label: str) -> str:
    if not value or not value.strip() or value != value.strip():
        raise ValueError(f"{label} must be non-blank and trimmed")
    return value


def _required_sha256(value: str, *, label: str) -> str:
    if not _SHA256_RE.fullmatch(value):
        raise ValueError(f"{label} must be lowercase SHA-256")
    return value


def _addressed_payload(value: BaseModel, *, identity_field: str) -> dict[str, Any]:
    return value.model_dump(mode="json", exclude={identity_field}, exclude_none=False)


def _addressed_id(value: BaseModel, *, identity_field: str, prefix: str) -> str:
    return prefix + hash_object(_addressed_payload(value, identity_field=identity_field))


class RecognitionContentDigestV1(_RecognitionRunContract):
    """A URI-free digest for bytes observed or derived during Recognition."""

    schema_version: Literal[1] = 1
    sha256: str
    size_bytes: int = Field(ge=0)

    @field_validator("sha256")
    @classmethod
    def _valid_digest(cls, value: str) -> str:
        return _required_sha256(value, label="Recognition content digest")


def digest_recognition_content(payload: bytes) -> RecognitionContentDigestV1:
    """Build a digest from the exact bytes; callers never provide its hash."""

    if not isinstance(payload, bytes):
        raise TypeError("Recognition content digest requires exact bytes")
    return RecognitionContentDigestV1(
        sha256=sha256_bytes(payload),
        size_bytes=len(payload),
    )


def digest_recognition_file(path: str | Path) -> RecognitionContentDigestV1:
    """Stream one exact regular file into a URI-free Recognition digest."""

    candidate = Path(path)
    try:
        size_bytes = candidate.stat().st_size
    except OSError as exc:
        raise RecognitionRunIntegrityError("Recognition content file is unreadable") from exc
    if not candidate.is_file() or size_bytes < 0:
        raise RecognitionRunIntegrityError("Recognition content path is not a regular file")
    return RecognitionContentDigestV1(
        sha256=hash_file(candidate),
        size_bytes=size_bytes,
    )


class RecognitionAudioBindingV1(_RecognitionRunContract):
    """Exact normalized PCM bytes and sample-clock topology for one run."""

    schema_version: Literal[1] = 1
    normalized_audio: RecognitionContentDigestV1
    sample_rate_hz: int = Field(gt=0)
    channels: int = Field(gt=0)
    sample_width_bytes: int = Field(gt=0)
    frame_count: int = Field(gt=0)
    duration_ms: int = Field(gt=0)
    compression_type: str
    content_hash: str

    @field_validator("compression_type")
    @classmethod
    def _compression_is_explicit(cls, value: str) -> str:
        return _required_text(value, label="Recognition audio compression_type")

    @field_validator("content_hash")
    @classmethod
    def _content_hash_shape(cls, value: str) -> str:
        return _required_sha256(value, label="Recognition audio binding content_hash")

    @model_validator(mode="after")
    def _closed_audio_binding(self) -> "RecognitionAudioBindingV1":
        expected_duration = round(self.frame_count * 1_000 / self.sample_rate_hz)
        if self.duration_ms != expected_duration:
            raise ValueError("Recognition audio duration does not match its sample clock")
        expected = hash_object(_addressed_payload(self, identity_field="content_hash"))
        if self.content_hash != expected:
            raise ValueError("Recognition audio binding content_hash mismatch")
        return self


def build_recognition_audio_binding(
    normalized_audio: str | Path,
) -> RecognitionAudioBindingV1:
    """Measure a PCM WAV and build its exact audio binding from the same path."""

    path = Path(normalized_audio)
    before = digest_recognition_file(path)
    try:
        with wave.open(str(path), "rb") as reader:
            sample_rate_hz = reader.getframerate()
            channels = reader.getnchannels()
            sample_width_bytes = reader.getsampwidth()
            frame_count = reader.getnframes()
            compression_type = reader.getcomptype()
    except (EOFError, OSError, wave.Error) as exc:
        raise RecognitionRunIntegrityError(
            "Recognition run requires a readable PCM WAV normalized audio"
        ) from exc
    after = digest_recognition_file(path)
    if before != after:
        raise RecognitionRunIntegrityError(
            "normalized audio changed while its Recognition binding was measured"
        )
    if compression_type != "NONE":
        raise RecognitionRunIntegrityError(
            "Recognition chunk recovery currently requires uncompressed PCM WAV"
        )
    payload = {
        "schema_version": 1,
        "normalized_audio": before,
        "sample_rate_hz": sample_rate_hz,
        "channels": channels,
        "sample_width_bytes": sample_width_bytes,
        "frame_count": frame_count,
        "duration_ms": round(frame_count * 1_000 / sample_rate_hz),
        "compression_type": compression_type,
    }
    return RecognitionAudioBindingV1(
        **payload,
        content_hash=hash_object(payload),
    )


def rebuild_recognition_audio_binding(
    binding: RecognitionAudioBindingV1,
) -> RecognitionAudioBindingV1:
    payload = _addressed_payload(binding, identity_field="content_hash")
    return RecognitionAudioBindingV1(**payload, content_hash=hash_object(payload))


def _verify_normalized_audio_binding(
    normalized_audio: str | Path,
    audio: RecognitionAudioBindingV1,
) -> Path:
    path = Path(normalized_audio)
    measured = build_recognition_audio_binding(path)
    if measured != audio:
        raise RecognitionRunIntegrityError(
            "normalized PCM WAV differs from its immutable Recognition audio binding"
        )
    return path


def _derive_recognition_pcm_wav_chunk(
    normalized_audio: Path,
    *,
    audio: RecognitionAudioBindingV1,
    start_sample: int,
    end_sample: int,
) -> bytes:
    if not 0 <= start_sample < end_sample <= audio.frame_count:
        raise RecognitionRunIntegrityError(
            "Recognition chunk derivation range exceeds normalized sample clock"
        )
    try:
        with wave.open(str(normalized_audio), "rb") as reader:
            topology = (
                reader.getframerate(),
                reader.getnchannels(),
                reader.getsampwidth(),
                reader.getnframes(),
                reader.getcomptype(),
            )
            expected_topology = (
                audio.sample_rate_hz,
                audio.channels,
                audio.sample_width_bytes,
                audio.frame_count,
                audio.compression_type,
            )
            if topology != expected_topology:
                raise RecognitionRunIntegrityError(
                    "normalized PCM topology changed before chunk derivation"
                )
            reader.setpos(start_sample)
            frames = reader.readframes(end_sample - start_sample)
    except (EOFError, OSError, wave.Error) as exc:
        raise RecognitionRunIntegrityError(
            "Recognition chunk could not replay normalized PCM frames"
        ) from exc
    expected_size = (end_sample - start_sample) * audio.channels * audio.sample_width_bytes
    if len(frames) != expected_size:
        raise RecognitionRunIntegrityError(
            "Recognition chunk derivation returned an incomplete frame slice"
        )
    output = io.BytesIO()
    with wave.open(output, "wb") as writer:
        writer.setnchannels(audio.channels)
        writer.setsampwidth(audio.sample_width_bytes)
        writer.setframerate(audio.sample_rate_hz)
        writer.setcomptype(audio.compression_type, "not compressed")
        writer.writeframes(frames)
    return output.getvalue()


def derive_recognition_pcm_wav_chunk(
    normalized_audio: str | Path,
    *,
    audio: RecognitionAudioBindingV1,
    start_sample: int,
    end_sample: int,
) -> bytes:
    """Derive exact PCM frames only after remeasuring the complete source binding."""

    path = _verify_normalized_audio_binding(normalized_audio, audio)
    derived = _derive_recognition_pcm_wav_chunk(
        path,
        audio=audio,
        start_sample=start_sample,
        end_sample=end_sample,
    )
    if build_recognition_audio_binding(path) != audio:
        raise RecognitionRunIntegrityError(
            "normalized PCM WAV changed during Recognition chunk derivation"
        )
    return derived


class RecognitionRequestBindingV1(_RecognitionRunContract):
    """Logical request identity used for stable lookup across runtime drift."""

    schema_version: Literal[1] = 1
    episode_id: str
    invocation_id: str
    logical_namespace: str
    language_hint: str | None = None
    context_policy_id: Literal["nakama-verbatim-no-lexical-context-v1"]
    context_sha256: str
    context_size_bytes: int = Field(ge=0)
    audio_binding_hash: str
    content_hash: str

    @field_validator("episode_id", "invocation_id", "logical_namespace")
    @classmethod
    def _required_request_text(cls, value: str, info: Any) -> str:
        return _required_text(value, label=f"Recognition request {info.field_name}")

    @field_validator("language_hint")
    @classmethod
    def _language_is_exact(cls, value: str | None) -> str | None:
        if value is None:
            return None
        return _required_text(value, label="Recognition request language_hint")

    @field_validator("audio_binding_hash", "context_sha256", "content_hash")
    @classmethod
    def _request_hashes(cls, value: str, info: Any) -> str:
        return _required_sha256(value, label=f"Recognition request {info.field_name}")

    @model_validator(mode="after")
    def _closed_request_binding(self) -> "RecognitionRequestBindingV1":
        if self.context_sha256 != sha256_bytes(b"") or self.context_size_bytes != 0:
            raise ValueError("Recognition context policy requires exact empty context bytes")
        expected = hash_object(_addressed_payload(self, identity_field="content_hash"))
        if self.content_hash != expected:
            raise ValueError("Recognition request binding content_hash mismatch")
        return self


def build_recognition_request_binding(
    *,
    episode_id: str,
    invocation_id: str,
    logical_namespace: str,
    language_hint: str | None,
    context_policy_id: Literal["nakama-verbatim-no-lexical-context-v1"],
    context_bytes: bytes,
    audio: RecognitionAudioBindingV1,
) -> RecognitionRequestBindingV1:
    payload = {
        "schema_version": 1,
        "episode_id": episode_id,
        "invocation_id": invocation_id,
        "logical_namespace": logical_namespace,
        "language_hint": language_hint,
        "context_policy_id": context_policy_id,
        "context_sha256": sha256_bytes(context_bytes),
        "context_size_bytes": len(context_bytes),
        "audio_binding_hash": audio.content_hash,
    }
    return RecognitionRequestBindingV1(
        **payload,
        content_hash=hash_object(payload),
    )


def rebuild_recognition_request_binding(
    binding: RecognitionRequestBindingV1,
) -> RecognitionRequestBindingV1:
    payload = _addressed_payload(binding, identity_field="content_hash")
    return RecognitionRequestBindingV1(**payload, content_hash=hash_object(payload))


class RecognitionAdapterIdentitySnapshotV1(_RecognitionRunContract):
    """Full measured recognizer identity frozen into one run plan."""

    schema_version: Literal[1] = 1
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
    execution_mode: RecognitionExecutionMode
    content_hash: str

    @field_validator(
        "adapter_name",
        "adapter_version",
        "model",
        "model_version",
        "aligner",
        "aligner_version",
    )
    @classmethod
    def _required_identity_text(cls, value: str, info: Any) -> str:
        return _required_text(value, label=f"Recognition Adapter {info.field_name}")

    @field_validator("runtime_hash", "adapter_code_hash", "config_hash", "content_hash")
    @classmethod
    def _identity_hashes(cls, value: str, info: Any) -> str:
        return _required_sha256(value, label=f"Recognition Adapter {info.field_name}")

    @model_validator(mode="after")
    def _closed_adapter_identity(self) -> "RecognitionAdapterIdentitySnapshotV1":
        if not self.runtime_components:
            raise ValueError("Recognition Adapter requires measured runtime components")
        if self.runtime_components != tuple(sorted(self.runtime_components)):
            raise ValueError("Recognition runtime components must be canonically sorted")
        names = tuple(name for name, _value in self.runtime_components)
        if len(set(names)) != len(names):
            raise ValueError("Recognition runtime component names must be unique")
        for name, value in self.runtime_components:
            _required_text(name, label="Recognition runtime component name")
            _required_text(value, label="Recognition runtime component value")
        expected_runtime = hash_object({"runtime_components": self.runtime_components})
        if self.runtime_hash != expected_runtime:
            raise ValueError("Recognition Adapter runtime_hash mismatch")
        expected = hash_object(_addressed_payload(self, identity_field="content_hash"))
        if self.content_hash != expected:
            raise ValueError("Recognition Adapter identity content_hash mismatch")
        return self


def build_recognition_adapter_identity_snapshot(
    *,
    adapter_name: str,
    adapter_version: str,
    model: str,
    model_version: str,
    aligner: str,
    aligner_version: str,
    runtime_components: Sequence[tuple[str, str]],
    adapter_code_hash: str,
    config_hash: str,
    execution_mode: RecognitionExecutionMode,
) -> RecognitionAdapterIdentitySnapshotV1:
    components = tuple(sorted(tuple(item) for item in runtime_components))
    payload = {
        "schema_version": 1,
        "adapter_name": adapter_name,
        "adapter_version": adapter_version,
        "model": model,
        "model_version": model_version,
        "aligner": aligner,
        "aligner_version": aligner_version,
        "runtime_components": components,
        "runtime_hash": hash_object({"runtime_components": components}),
        "adapter_code_hash": adapter_code_hash,
        "config_hash": config_hash,
        "execution_mode": execution_mode,
    }
    return RecognitionAdapterIdentitySnapshotV1(
        **payload,
        content_hash=hash_object(payload),
    )


def snapshot_recognition_model_identity(identity: Any) -> RecognitionAdapterIdentitySnapshotV1:
    """Snapshot the existing ``RecognitionModelIdentity`` without trusting hashes."""

    return build_recognition_adapter_identity_snapshot(
        adapter_name=identity.adapter_name,
        adapter_version=identity.adapter_version,
        model=identity.model,
        model_version=identity.model_version,
        aligner=identity.aligner,
        aligner_version=identity.aligner_version,
        runtime_components=identity.runtime_components,
        adapter_code_hash=identity.adapter_code_hash,
        config_hash=identity.config_hash,
        execution_mode=identity.execution_mode,
    )


def rebuild_recognition_adapter_identity_snapshot(
    identity: RecognitionAdapterIdentitySnapshotV1,
) -> RecognitionAdapterIdentitySnapshotV1:
    return build_recognition_adapter_identity_snapshot(
        adapter_name=identity.adapter_name,
        adapter_version=identity.adapter_version,
        model=identity.model,
        model_version=identity.model_version,
        aligner=identity.aligner,
        aligner_version=identity.aligner_version,
        runtime_components=identity.runtime_components,
        adapter_code_hash=identity.adapter_code_hash,
        config_hash=identity.config_hash,
        execution_mode=identity.execution_mode,
    )


class RecognitionChunkPlannerV1(_RecognitionRunContract):
    """Measured deterministic planner semantics for bounded PCM chunks."""

    schema_version: Literal[1] = 1
    planner_name: str
    planner_version: str
    derivation: Literal["pcm_wav_frame_slice_v1"]
    ownership: Literal["alignment_midpoint_in_half_open_owned_interval_v1"]
    seam_comparison: Literal["normalized_alignment_text_in_shared_context_v1"]
    owned_chunk_samples: int = Field(gt=0)
    seam_context_samples: int = Field(gt=0)
    content_hash: str

    @field_validator("planner_name", "planner_version")
    @classmethod
    def _planner_text(cls, value: str, info: Any) -> str:
        return _required_text(value, label=f"Recognition planner {info.field_name}")

    @field_validator("content_hash")
    @classmethod
    def _planner_hash(cls, value: str) -> str:
        return _required_sha256(value, label="Recognition planner content_hash")

    @model_validator(mode="after")
    def _closed_planner(self) -> "RecognitionChunkPlannerV1":
        expected = hash_object(_addressed_payload(self, identity_field="content_hash"))
        if self.content_hash != expected:
            raise ValueError("Recognition planner content_hash mismatch")
        return self


def build_recognition_chunk_planner(
    *,
    planner_name: str,
    planner_version: str,
    owned_chunk_samples: int,
    seam_context_samples: int,
) -> RecognitionChunkPlannerV1:
    payload = {
        "schema_version": 1,
        "planner_name": planner_name,
        "planner_version": planner_version,
        "derivation": "pcm_wav_frame_slice_v1",
        "ownership": "alignment_midpoint_in_half_open_owned_interval_v1",
        "seam_comparison": "normalized_alignment_text_in_shared_context_v1",
        "owned_chunk_samples": owned_chunk_samples,
        "seam_context_samples": seam_context_samples,
    }
    return RecognitionChunkPlannerV1(**payload, content_hash=hash_object(payload))


def rebuild_recognition_chunk_planner(
    planner: RecognitionChunkPlannerV1,
) -> RecognitionChunkPlannerV1:
    return build_recognition_chunk_planner(
        planner_name=planner.planner_name,
        planner_version=planner.planner_version,
        owned_chunk_samples=planner.owned_chunk_samples,
        seam_context_samples=planner.seam_context_samples,
    )


class RecognitionChunkPlanV1(_RecognitionRunContract):
    """One inference interval plus its exact non-overlapping owned interval."""

    schema_version: Literal[1] = 1
    id: str
    index: int = Field(ge=0)
    count: int = Field(gt=0)
    inference_start_sample: int = Field(ge=0)
    inference_end_sample: int = Field(gt=0)
    owned_start_sample: int = Field(ge=0)
    owned_end_sample: int = Field(gt=0)
    derived_audio: RecognitionContentDigestV1

    @field_validator("id")
    @classmethod
    def _chunk_id_shape(cls, value: str) -> str:
        if not _CHUNK_ID_RE.fullmatch(value):
            raise ValueError("Recognition chunk ID is invalid")
        return value

    @model_validator(mode="after")
    def _closed_chunk_plan(self) -> "RecognitionChunkPlanV1":
        if not 0 <= self.index < self.count:
            raise ValueError("Recognition chunk index is outside its declared count")
        if self.inference_end_sample <= self.inference_start_sample:
            raise ValueError("Recognition inference interval must be positive")
        if self.owned_end_sample <= self.owned_start_sample:
            raise ValueError("Recognition ownership interval must be positive")
        if not (
            self.inference_start_sample
            <= self.owned_start_sample
            < self.owned_end_sample
            <= self.inference_end_sample
        ):
            raise ValueError("Recognition inference interval must contain ownership")
        expected = _addressed_id(
            self,
            identity_field="id",
            prefix="recognition-chunk-",
        )
        if self.id != expected:
            raise ValueError("Recognition chunk ID does not address its exact content")
        return self


def _build_recognition_chunk_plan_from_bytes(
    *,
    index: int,
    count: int,
    inference_start_sample: int,
    inference_end_sample: int,
    owned_start_sample: int,
    owned_end_sample: int,
    derived_chunk_bytes: bytes,
) -> RecognitionChunkPlanV1:
    payload = {
        "schema_version": 1,
        "index": index,
        "count": count,
        "inference_start_sample": inference_start_sample,
        "inference_end_sample": inference_end_sample,
        "owned_start_sample": owned_start_sample,
        "owned_end_sample": owned_end_sample,
        "derived_audio": digest_recognition_content(derived_chunk_bytes),
    }
    return RecognitionChunkPlanV1(
        **payload,
        id="recognition-chunk-" + hash_object(payload),
    )


def build_recognition_chunk_plan(
    *,
    index: int,
    count: int,
    inference_start_sample: int,
    inference_end_sample: int,
    owned_start_sample: int,
    owned_end_sample: int,
    normalized_audio: str | Path,
    audio: RecognitionAudioBindingV1,
) -> RecognitionChunkPlanV1:
    """Build one plan only from the exact deterministic normalized PCM slice."""

    derived = derive_recognition_pcm_wav_chunk(
        normalized_audio,
        audio=audio,
        start_sample=inference_start_sample,
        end_sample=inference_end_sample,
    )
    return _build_recognition_chunk_plan_from_bytes(
        index=index,
        count=count,
        inference_start_sample=inference_start_sample,
        inference_end_sample=inference_end_sample,
        owned_start_sample=owned_start_sample,
        owned_end_sample=owned_end_sample,
        derived_chunk_bytes=derived,
    )


def rebuild_recognition_chunk_plan(chunk: RecognitionChunkPlanV1) -> RecognitionChunkPlanV1:
    payload = _addressed_payload(chunk, identity_field="id")
    return RecognitionChunkPlanV1(
        **payload,
        id="recognition-chunk-" + hash_object(payload),
    )


class RecognitionRunPlanV1(_RecognitionRunContract):
    """Complete immutable identity and all planned chunks for one ASR run."""

    schema_version: Literal[1] = 1
    id: str
    run_key: str
    request: RecognitionRequestBindingV1
    audio: RecognitionAudioBindingV1
    adapter_identity: RecognitionAdapterIdentitySnapshotV1
    planner: RecognitionChunkPlannerV1
    chunk_plan_hash: str
    chunks: tuple[RecognitionChunkPlanV1, ...]

    @field_validator("id")
    @classmethod
    def _run_id_shape(cls, value: str) -> str:
        if not _RUN_ID_RE.fullmatch(value):
            raise ValueError("Recognition run ID is invalid")
        return value

    @field_validator("run_key", "chunk_plan_hash")
    @classmethod
    def _plan_hashes(cls, value: str, info: Any) -> str:
        return _required_sha256(value, label=f"Recognition run {info.field_name}")

    @model_validator(mode="after")
    def _closed_run_plan(self) -> "RecognitionRunPlanV1":
        if self.request.audio_binding_hash != self.audio.content_hash:
            raise ValueError("Recognition request crossed its exact audio binding")
        expected_run_key = recognition_run_key(self.request)
        if self.run_key != expected_run_key:
            raise ValueError("Recognition run_key does not address its logical request")
        if not self.chunks:
            raise ValueError("Recognition run plan requires at least one chunk")
        expected_count = len(self.chunks)
        if expected_count > 1_000_000:
            raise ValueError(
                "Recognition run plan exceeds the repository's six-digit slot inventory"
            )
        if tuple(chunk.index for chunk in self.chunks) != tuple(range(expected_count)):
            raise ValueError("Recognition chunks must be ordered exactly 0..N-1")
        if any(chunk.count != expected_count for chunk in self.chunks):
            raise ValueError("Recognition chunks disagree on count")
        if self.chunks[0].owned_start_sample != 0:
            raise ValueError("Recognition ownership must start at normalized sample zero")
        previous_end = 0
        for chunk in self.chunks:
            if chunk.owned_start_sample != previous_end:
                raise ValueError("Recognition ownership contains a gap or overlap")
            expected_inference_start = max(
                0,
                chunk.owned_start_sample - self.planner.seam_context_samples,
            )
            expected_inference_end = min(
                self.audio.frame_count,
                chunk.owned_end_sample + self.planner.seam_context_samples,
            )
            if (
                chunk.inference_start_sample != expected_inference_start
                or chunk.inference_end_sample != expected_inference_end
            ):
                raise ValueError("Recognition inference context does not replay planner policy")
            owned_size = chunk.owned_end_sample - chunk.owned_start_sample
            if chunk.index < expected_count - 1:
                if owned_size != self.planner.owned_chunk_samples:
                    raise ValueError("non-final Recognition ownership has wrong chunk size")
            elif not 0 < owned_size <= self.planner.owned_chunk_samples:
                raise ValueError("final Recognition ownership has invalid chunk size")
            previous_end = chunk.owned_end_sample
        if previous_end != self.audio.frame_count:
            raise ValueError("Recognition ownership must end at exact frame_count")
        if self.chunk_plan_hash != hash_object(self.chunks):
            raise ValueError("Recognition chunk_plan_hash mismatch")
        expected_id = _addressed_id(
            self,
            identity_field="id",
            prefix="recognition-run-",
        )
        if self.id != expected_id:
            raise ValueError("Recognition run ID does not address its complete plan")
        return self


def recognition_run_key(request: RecognitionRequestBindingV1) -> str:
    """Stable lookup excluding attempt IDs and executable Adapter identity.

    ``invocation_id`` remains bound inside the immutable plan, but is excluded
    here so a restart cannot evade an existing run merely by minting another
    invocation after runtime drift.  A genuinely separate benchmark recognizer
    must use a different explicit ``logical_namespace``.
    """

    return hash_object(
        {
            "schema_version": 1,
            "kind": "recognition-run-request-lookup",
            "episode_id": request.episode_id,
            "logical_namespace": request.logical_namespace,
            "language_hint": request.language_hint,
            "context_policy_id": request.context_policy_id,
            "context_sha256": request.context_sha256,
            "context_size_bytes": request.context_size_bytes,
            "audio_binding_hash": request.audio_binding_hash,
        }
    )


def build_recognition_run_plan(
    *,
    request: RecognitionRequestBindingV1,
    audio: RecognitionAudioBindingV1,
    adapter_identity: RecognitionAdapterIdentitySnapshotV1,
    planner: RecognitionChunkPlannerV1,
    chunks: Sequence[RecognitionChunkPlanV1],
) -> RecognitionRunPlanV1:
    typed_chunks = tuple(chunks)
    payload = {
        "schema_version": 1,
        "run_key": recognition_run_key(request),
        "request": request,
        "audio": audio,
        "adapter_identity": adapter_identity,
        "planner": planner,
        "chunk_plan_hash": hash_object(typed_chunks),
        "chunks": typed_chunks,
    }
    return RecognitionRunPlanV1(
        **payload,
        id="recognition-run-" + hash_object(payload),
    )


def rebuild_recognition_run_plan(plan: RecognitionRunPlanV1) -> RecognitionRunPlanV1:
    return build_recognition_run_plan(
        request=rebuild_recognition_request_binding(plan.request),
        audio=rebuild_recognition_audio_binding(plan.audio),
        adapter_identity=rebuild_recognition_adapter_identity_snapshot(plan.adapter_identity),
        planner=rebuild_recognition_chunk_planner(plan.planner),
        chunks=tuple(rebuild_recognition_chunk_plan(chunk) for chunk in plan.chunks),
    )


class RecognitionDerivedChunkV1(_RecognitionRunContract):
    """Digest-only persistence record; it never claims a local artifact URI."""

    schema_version: Literal[1] = 1
    run_id: str
    plan_id: str
    chunk_id: str
    chunk_index: int = Field(ge=0)
    derived_audio: RecognitionContentDigestV1
    persistence: Literal["digest_only_no_artifact_uri"]
    content_hash: str

    @field_validator("run_id", "plan_id", "chunk_id")
    @classmethod
    def _derived_ids(cls, value: str, info: Any) -> str:
        patterns = {
            "run_id": _RUN_ID_RE,
            "plan_id": _RUN_ID_RE,
            "chunk_id": _CHUNK_ID_RE,
        }
        if not patterns[info.field_name].fullmatch(value):
            raise ValueError(f"Recognition derived chunk {info.field_name} is invalid")
        return value

    @field_validator("content_hash")
    @classmethod
    def _derived_hash(cls, value: str) -> str:
        return _required_sha256(value, label="Recognition derived chunk content_hash")

    @model_validator(mode="after")
    def _closed_derived_chunk(self) -> "RecognitionDerivedChunkV1":
        if self.run_id != self.plan_id:
            raise ValueError("Recognition derived chunk run_id/plan_id mismatch")
        expected = hash_object(_addressed_payload(self, identity_field="content_hash"))
        if self.content_hash != expected:
            raise ValueError("Recognition derived chunk content_hash mismatch")
        return self


def build_recognition_derived_chunk(
    *,
    plan: RecognitionRunPlanV1,
    chunk: RecognitionChunkPlanV1,
    derived_chunk_bytes: bytes,
) -> RecognitionDerivedChunkV1:
    actual = digest_recognition_content(derived_chunk_bytes)
    if actual != chunk.derived_audio:
        raise RecognitionRunIntegrityError(
            "derived chunk bytes differ from the immutable Recognition plan"
        )
    payload = {
        "schema_version": 1,
        "run_id": plan.id,
        "plan_id": plan.id,
        "chunk_id": chunk.id,
        "chunk_index": chunk.index,
        "derived_audio": actual,
        "persistence": "digest_only_no_artifact_uri",
    }
    return RecognitionDerivedChunkV1(**payload, content_hash=hash_object(payload))


class ChunkExecutionReceiptV1(_RecognitionRunContract):
    """Exact successful observation of one planned bounded Adapter call."""

    schema_version: Literal[1] = 1
    id: str
    run_id: str
    run_key: str
    plan_id: str
    chunk_id: str
    chunk_index: int = Field(ge=0)
    previous_receipt_id: str | None = None
    request_binding_hash: str
    adapter_identity_hash: str
    derived_chunk_record: RecognitionContentDigestV1
    derived_audio: RecognitionContentDigestV1
    adapter_observation: RecognitionContentDigestV1
    observation_semantics: Literal["adapter_observed_sdk_mapping_canonical_json_v1"]
    provider_wire_bytes_claimed: Literal[False] = False
    execution_status: Literal["completed"] = "completed"

    @field_validator("id")
    @classmethod
    def _receipt_id_shape(cls, value: str) -> str:
        if not _RECEIPT_ID_RE.fullmatch(value):
            raise ValueError("Recognition chunk receipt ID is invalid")
        return value

    @field_validator("run_id", "plan_id")
    @classmethod
    def _receipt_run_ids(cls, value: str, info: Any) -> str:
        if not _RUN_ID_RE.fullmatch(value):
            raise ValueError(f"Recognition receipt {info.field_name} is invalid")
        return value

    @field_validator("chunk_id")
    @classmethod
    def _receipt_chunk_id(cls, value: str) -> str:
        if not _CHUNK_ID_RE.fullmatch(value):
            raise ValueError("Recognition receipt chunk_id is invalid")
        return value

    @field_validator("run_key", "request_binding_hash", "adapter_identity_hash")
    @classmethod
    def _receipt_hashes(cls, value: str, info: Any) -> str:
        return _required_sha256(value, label=f"Recognition receipt {info.field_name}")

    @field_validator("previous_receipt_id")
    @classmethod
    def _previous_receipt_shape(cls, value: str | None) -> str | None:
        if value is not None and not _RECEIPT_ID_RE.fullmatch(value):
            raise ValueError("previous Recognition receipt ID is invalid")
        return value

    @model_validator(mode="after")
    def _closed_execution_receipt(self) -> "ChunkExecutionReceiptV1":
        if self.run_id != self.plan_id:
            raise ValueError("Recognition receipt run_id/plan_id mismatch")
        if (self.chunk_index == 0) != (self.previous_receipt_id is None):
            raise ValueError("Recognition receipt predecessor does not match its index")
        expected = _addressed_id(
            self,
            identity_field="id",
            prefix="recognition-chunk-receipt-",
        )
        if self.id != expected:
            raise ValueError("Recognition chunk receipt ID does not address exact content")
        return self


def build_chunk_execution_receipt(
    *,
    plan: RecognitionRunPlanV1,
    chunk: RecognitionChunkPlanV1,
    previous_receipt_id: str | None,
    derived_chunk: RecognitionDerivedChunkV1,
    derived_chunk_bytes: bytes,
    adapter_observation_bytes: bytes,
) -> ChunkExecutionReceiptV1:
    if digest_recognition_content(derived_chunk_bytes) != chunk.derived_audio:
        raise RecognitionRunIntegrityError("Recognition derived chunk digest changed")
    derived_record_bytes = canonical_json_bytes(derived_chunk)
    payload = {
        "schema_version": 1,
        "run_id": plan.id,
        "run_key": plan.run_key,
        "plan_id": plan.id,
        "chunk_id": chunk.id,
        "chunk_index": chunk.index,
        "previous_receipt_id": previous_receipt_id,
        "request_binding_hash": plan.request.content_hash,
        "adapter_identity_hash": plan.adapter_identity.content_hash,
        "derived_chunk_record": digest_recognition_content(derived_record_bytes),
        "derived_audio": chunk.derived_audio,
        "adapter_observation": digest_recognition_content(adapter_observation_bytes),
        "observation_semantics": "adapter_observed_sdk_mapping_canonical_json_v1",
        "provider_wire_bytes_claimed": False,
        "execution_status": "completed",
    }
    return ChunkExecutionReceiptV1(
        **payload,
        id="recognition-chunk-receipt-" + hash_object(payload),
    )


class RecognitionRunCheckpointV1(_RecognitionRunContract):
    """Immutable completed-prefix state; never transcript or Generation truth."""

    schema_version: Literal[1] = 1
    id: str
    run_id: str
    run_key: str
    plan_id: str
    completed_prefix: int = Field(ge=0)
    receipt_ids: tuple[str, ...]
    last_receipt_id: str | None = None
    previous_checkpoint_id: str | None = None

    @field_validator("id")
    @classmethod
    def _checkpoint_id_shape(cls, value: str) -> str:
        if not _CHECKPOINT_ID_RE.fullmatch(value):
            raise ValueError("Recognition checkpoint ID is invalid")
        return value

    @field_validator("run_id", "plan_id")
    @classmethod
    def _checkpoint_run_ids(cls, value: str, info: Any) -> str:
        if not _RUN_ID_RE.fullmatch(value):
            raise ValueError(f"Recognition checkpoint {info.field_name} is invalid")
        return value

    @field_validator("run_key")
    @classmethod
    def _checkpoint_run_key(cls, value: str) -> str:
        return _required_sha256(value, label="Recognition checkpoint run_key")

    @field_validator("last_receipt_id")
    @classmethod
    def _checkpoint_last_receipt(cls, value: str | None) -> str | None:
        if value is not None and not _RECEIPT_ID_RE.fullmatch(value):
            raise ValueError("Recognition checkpoint last receipt ID is invalid")
        return value

    @field_validator("previous_checkpoint_id")
    @classmethod
    def _checkpoint_predecessor(cls, value: str | None) -> str | None:
        if value is not None and not _CHECKPOINT_ID_RE.fullmatch(value):
            raise ValueError("previous Recognition checkpoint ID is invalid")
        return value

    @model_validator(mode="after")
    def _closed_checkpoint(self) -> "RecognitionRunCheckpointV1":
        if self.run_id != self.plan_id:
            raise ValueError("Recognition checkpoint run_id/plan_id mismatch")
        if len(self.receipt_ids) != self.completed_prefix:
            raise ValueError("Recognition checkpoint prefix/receipt inventory mismatch")
        if len(set(self.receipt_ids)) != len(self.receipt_ids) or any(
            not _RECEIPT_ID_RE.fullmatch(item) for item in self.receipt_ids
        ):
            raise ValueError("Recognition checkpoint receipt IDs are invalid or duplicated")
        expected_last = self.receipt_ids[-1] if self.receipt_ids else None
        if self.last_receipt_id != expected_last:
            raise ValueError("Recognition checkpoint last receipt does not match exact prefix")
        if (self.completed_prefix == 0) != (self.previous_checkpoint_id is None):
            raise ValueError("Recognition checkpoint predecessor does not match its prefix")
        expected = _addressed_id(
            self,
            identity_field="id",
            prefix="recognition-run-checkpoint-",
        )
        if self.id != expected:
            raise ValueError("Recognition checkpoint ID does not address exact content")
        return self


def build_recognition_run_checkpoint(
    *,
    plan: RecognitionRunPlanV1,
    receipts: Sequence[ChunkExecutionReceiptV1],
    previous_checkpoint_id: str | None,
) -> RecognitionRunCheckpointV1:
    typed_receipts = tuple(receipts)
    payload = {
        "schema_version": 1,
        "run_id": plan.id,
        "run_key": plan.run_key,
        "plan_id": plan.id,
        "completed_prefix": len(typed_receipts),
        "receipt_ids": tuple(receipt.id for receipt in typed_receipts),
        "last_receipt_id": typed_receipts[-1].id if typed_receipts else None,
        "previous_checkpoint_id": previous_checkpoint_id,
    }
    return RecognitionRunCheckpointV1(
        **payload,
        id="recognition-run-checkpoint-" + hash_object(payload),
    )


class RecognitionRunHeadV1(_RecognitionRunContract):
    """Immutable publication record binding one checkpoint's exact bytes."""

    schema_version: Literal[1] = 1
    id: str
    run_id: str
    run_key: str
    plan_id: str
    completed_prefix: int = Field(ge=0)
    checkpoint_id: str
    checkpoint_record: RecognitionContentDigestV1
    previous_head_id: str | None = None

    @field_validator("id")
    @classmethod
    def _head_id_shape(cls, value: str) -> str:
        if not _HEAD_ID_RE.fullmatch(value):
            raise ValueError("Recognition run head ID is invalid")
        return value

    @field_validator("run_id", "plan_id")
    @classmethod
    def _head_run_ids(cls, value: str, info: Any) -> str:
        if not _RUN_ID_RE.fullmatch(value):
            raise ValueError(f"Recognition head {info.field_name} is invalid")
        return value

    @field_validator("run_key")
    @classmethod
    def _head_run_key(cls, value: str) -> str:
        return _required_sha256(value, label="Recognition head run_key")

    @field_validator("checkpoint_id")
    @classmethod
    def _head_checkpoint(cls, value: str) -> str:
        if not _CHECKPOINT_ID_RE.fullmatch(value):
            raise ValueError("Recognition head checkpoint_id is invalid")
        return value

    @field_validator("previous_head_id")
    @classmethod
    def _head_predecessor(cls, value: str | None) -> str | None:
        if value is not None and not _HEAD_ID_RE.fullmatch(value):
            raise ValueError("previous Recognition head ID is invalid")
        return value

    @model_validator(mode="after")
    def _closed_head(self) -> "RecognitionRunHeadV1":
        if self.run_id != self.plan_id:
            raise ValueError("Recognition head run_id/plan_id mismatch")
        if (self.completed_prefix == 0) != (self.previous_head_id is None):
            raise ValueError("Recognition head predecessor does not match its prefix")
        expected = _addressed_id(
            self,
            identity_field="id",
            prefix="recognition-run-head-",
        )
        if self.id != expected:
            raise ValueError("Recognition head ID does not address exact content")
        return self


def build_recognition_run_head(
    *,
    plan: RecognitionRunPlanV1,
    checkpoint: RecognitionRunCheckpointV1,
    previous_head_id: str | None,
) -> RecognitionRunHeadV1:
    checkpoint_bytes = canonical_json_bytes(checkpoint)
    payload = {
        "schema_version": 1,
        "run_id": plan.id,
        "run_key": plan.run_key,
        "plan_id": plan.id,
        "completed_prefix": checkpoint.completed_prefix,
        "checkpoint_id": checkpoint.id,
        "checkpoint_record": digest_recognition_content(checkpoint_bytes),
        "previous_head_id": previous_head_id,
    }
    return RecognitionRunHeadV1(
        **payload,
        id="recognition-run-head-" + hash_object(payload),
    )


class RecognitionRunFailureV1(_RecognitionRunContract):
    """Typed deterministic post-prefix failure used only by rejected finalization."""

    schema_version: Literal[1] = 1
    code: RecognitionRunFailureCode
    affected_chunk_ids: tuple[str, ...] = ()
    diagnostics: tuple[tuple[str, FailureScalar], ...]
    content_hash: str

    @field_validator("content_hash")
    @classmethod
    def _failure_hash(cls, value: str) -> str:
        return _required_sha256(value, label="Recognition failure content_hash")

    @model_validator(mode="after")
    def _closed_failure(self) -> "RecognitionRunFailureV1":
        if len(set(self.affected_chunk_ids)) != len(self.affected_chunk_ids) or any(
            not _CHUNK_ID_RE.fullmatch(item) for item in self.affected_chunk_ids
        ):
            raise ValueError("Recognition failure chunk IDs are invalid or duplicated")
        if self.affected_chunk_ids != tuple(sorted(self.affected_chunk_ids)):
            raise ValueError("Recognition failure chunk IDs must be canonically sorted")
        names = tuple(name for name, _value in self.diagnostics)
        if self.diagnostics != tuple(sorted(self.diagnostics, key=lambda item: item[0])):
            raise ValueError("Recognition failure diagnostics must be sorted by key")
        if len(set(names)) != len(names):
            raise ValueError("Recognition failure diagnostic keys must be unique")
        for name in names:
            _required_text(name, label="Recognition failure diagnostic key")
        expected = hash_object(_addressed_payload(self, identity_field="content_hash"))
        if self.content_hash != expected:
            raise ValueError("Recognition failure content_hash mismatch")
        return self


def build_recognition_run_failure(
    *,
    code: RecognitionRunFailureCode,
    affected_chunk_ids: Sequence[str] = (),
    diagnostics: Mapping[str, FailureScalar],
) -> RecognitionRunFailureV1:
    typed_chunk_ids = tuple(affected_chunk_ids)
    if len(set(typed_chunk_ids)) != len(typed_chunk_ids):
        raise ValueError("Recognition failure chunk IDs must be unique")
    typed_diagnostics = tuple(sorted(diagnostics.items()))
    payload = {
        "schema_version": 1,
        "code": code,
        "affected_chunk_ids": tuple(sorted(typed_chunk_ids)),
        "diagnostics": typed_diagnostics,
    }
    return RecognitionRunFailureV1(**payload, content_hash=hash_object(payload))


def _require_failure_plan_membership(
    *,
    plan: RecognitionRunPlanV1,
    failure: RecognitionRunFailureV1 | None,
) -> None:
    if failure is None:
        return
    planned_chunk_ids = {chunk.id for chunk in plan.chunks}
    if not set(failure.affected_chunk_ids).issubset(planned_chunk_ids):
        raise RecognitionRunIntegrityError(
            "Recognition failure affected chunks crossed the immutable plan"
        )


class RecognitionRunFinalizationV1(_RecognitionRunContract):
    """Immutable terminal claim made only after the full chunk prefix exists."""

    schema_version: Literal[1] = 1
    id: str
    run_id: str
    run_key: str
    plan_id: str
    head_id: str
    checkpoint_id: str
    completed_prefix: int = Field(gt=0)
    status: Literal["verified", "rejected"]
    recognition_evidence_hash: str | None = None
    failure: RecognitionRunFailureV1 | None = None

    @field_validator("id")
    @classmethod
    def _finalization_id_shape(cls, value: str) -> str:
        if not _FINALIZATION_ID_RE.fullmatch(value):
            raise ValueError("Recognition finalization ID is invalid")
        return value

    @field_validator("run_id", "plan_id")
    @classmethod
    def _finalization_run_ids(cls, value: str, info: Any) -> str:
        if not _RUN_ID_RE.fullmatch(value):
            raise ValueError(f"Recognition finalization {info.field_name} is invalid")
        return value

    @field_validator("run_key")
    @classmethod
    def _finalization_key(cls, value: str) -> str:
        return _required_sha256(value, label="Recognition finalization run_key")

    @field_validator("head_id")
    @classmethod
    def _finalization_head(cls, value: str) -> str:
        if not _HEAD_ID_RE.fullmatch(value):
            raise ValueError("Recognition finalization head_id is invalid")
        return value

    @field_validator("checkpoint_id")
    @classmethod
    def _finalization_checkpoint(cls, value: str) -> str:
        if not _CHECKPOINT_ID_RE.fullmatch(value):
            raise ValueError("Recognition finalization checkpoint_id is invalid")
        return value

    @field_validator("recognition_evidence_hash")
    @classmethod
    def _evidence_hash(cls, value: str | None) -> str | None:
        if value is None:
            return None
        return _required_sha256(value, label="Recognition Evidence hash")

    @model_validator(mode="after")
    def _closed_finalization(self) -> "RecognitionRunFinalizationV1":
        if self.run_id != self.plan_id:
            raise ValueError("Recognition finalization run_id/plan_id mismatch")
        if self.status == "verified":
            if self.recognition_evidence_hash is None or self.failure is not None:
                raise ValueError(
                    "verified Recognition finalization requires Evidence and no failure"
                )
        elif self.recognition_evidence_hash is not None or self.failure is None:
            raise ValueError(
                "rejected Recognition finalization requires typed failure and no Evidence"
            )
        expected = _addressed_id(
            self,
            identity_field="id",
            prefix="recognition-run-finalization-",
        )
        if self.id != expected:
            raise ValueError("Recognition finalization ID does not address exact content")
        return self


def build_recognition_run_finalization(
    *,
    plan: RecognitionRunPlanV1,
    head: RecognitionRunHeadV1,
    checkpoint: RecognitionRunCheckpointV1,
    status: Literal["verified", "rejected"],
    recognition_evidence_bytes: bytes | None = None,
    failure: RecognitionRunFailureV1 | None = None,
) -> RecognitionRunFinalizationV1:
    if checkpoint.completed_prefix != len(plan.chunks):
        raise RecognitionRunIntegrityError(
            "Recognition finalization requires the complete chunk prefix"
        )
    if head.checkpoint_id != checkpoint.id or head.completed_prefix != len(plan.chunks):
        raise RecognitionRunIntegrityError(
            "Recognition finalization head does not bind the complete checkpoint"
        )
    _require_failure_plan_membership(plan=plan, failure=failure)
    evidence_hash: str | None = None
    if recognition_evidence_bytes is not None:
        _parse_canonical_evidence_bytes(recognition_evidence_bytes)
        evidence_hash = sha256_bytes(recognition_evidence_bytes)
    payload = {
        "schema_version": 1,
        "run_id": plan.id,
        "run_key": plan.run_key,
        "plan_id": plan.id,
        "head_id": head.id,
        "checkpoint_id": checkpoint.id,
        "completed_prefix": checkpoint.completed_prefix,
        "status": status,
        "recognition_evidence_hash": evidence_hash,
        "failure": failure,
    }
    return RecognitionRunFinalizationV1(
        **payload,
        id="recognition-run-finalization-" + hash_object(payload),
    )


class _RecognitionRunKeyBindingV1(_RecognitionRunContract):
    schema_version: Literal[1] = 1
    run_key: str
    run_id: str
    plan_id: str
    plan_record: RecognitionContentDigestV1
    request_binding_hash: str
    adapter_identity_hash: str
    binding_hash: str

    @field_validator(
        "run_key",
        "request_binding_hash",
        "adapter_identity_hash",
        "binding_hash",
    )
    @classmethod
    def _binding_hashes(cls, value: str, info: Any) -> str:
        return _required_sha256(value, label=f"Recognition key binding {info.field_name}")

    @field_validator("run_id", "plan_id")
    @classmethod
    def _binding_run_ids(cls, value: str, info: Any) -> str:
        if not _RUN_ID_RE.fullmatch(value):
            raise ValueError(f"Recognition key binding {info.field_name} is invalid")
        return value

    @model_validator(mode="after")
    def _closed_key_binding(self) -> "_RecognitionRunKeyBindingV1":
        if self.run_id != self.plan_id:
            raise ValueError("Recognition key binding run_id/plan_id mismatch")
        expected = hash_object(_addressed_payload(self, identity_field="binding_hash"))
        if self.binding_hash != expected:
            raise ValueError("Recognition key binding hash mismatch")
        return self


def _build_key_binding(plan: RecognitionRunPlanV1) -> _RecognitionRunKeyBindingV1:
    payload = {
        "schema_version": 1,
        "run_key": plan.run_key,
        "run_id": plan.id,
        "plan_id": plan.id,
        "plan_record": digest_recognition_content(canonical_json_bytes(plan)),
        "request_binding_hash": plan.request.content_hash,
        "adapter_identity_hash": plan.adapter_identity.content_hash,
    }
    return _RecognitionRunKeyBindingV1(**payload, binding_hash=hash_object(payload))


class _RecognitionActiveHeadPointerV1(_RecognitionRunContract):
    schema_version: Literal[1] = 1
    run_key: str
    run_id: str
    plan_id: str
    completed_prefix: int = Field(ge=0)
    head_id: str
    head_record: RecognitionContentDigestV1
    finalization_id: str | None = None
    finalization_record: RecognitionContentDigestV1 | None = None
    pointer_hash: str

    @field_validator("run_key", "pointer_hash")
    @classmethod
    def _pointer_hashes(cls, value: str, info: Any) -> str:
        return _required_sha256(value, label=f"Recognition active pointer {info.field_name}")

    @field_validator("run_id", "plan_id")
    @classmethod
    def _pointer_run_ids(cls, value: str, info: Any) -> str:
        if not _RUN_ID_RE.fullmatch(value):
            raise ValueError(f"Recognition active pointer {info.field_name} is invalid")
        return value

    @field_validator("head_id")
    @classmethod
    def _pointer_head(cls, value: str) -> str:
        if not _HEAD_ID_RE.fullmatch(value):
            raise ValueError("Recognition active pointer head_id is invalid")
        return value

    @field_validator("finalization_id")
    @classmethod
    def _pointer_finalization(cls, value: str | None) -> str | None:
        if value is not None and not _FINALIZATION_ID_RE.fullmatch(value):
            raise ValueError("Recognition active pointer finalization_id is invalid")
        return value

    @model_validator(mode="after")
    def _closed_pointer(self) -> "_RecognitionActiveHeadPointerV1":
        if self.run_id != self.plan_id:
            raise ValueError("Recognition active pointer run_id/plan_id mismatch")
        if (self.finalization_id is None) != (self.finalization_record is None):
            raise ValueError("Recognition active pointer must bind both finalization ID and record")
        if self.finalization_id is not None and self.completed_prefix == 0:
            raise ValueError("Recognition terminal pointer cannot bind an empty prefix")
        expected = hash_object(_addressed_payload(self, identity_field="pointer_hash"))
        if self.pointer_hash != expected:
            raise ValueError("Recognition active-head pointer hash mismatch")
        return self


def _build_active_head_pointer(
    *,
    plan: RecognitionRunPlanV1,
    head: RecognitionRunHeadV1,
    finalization: RecognitionRunFinalizationV1 | None = None,
) -> _RecognitionActiveHeadPointerV1:
    if finalization is not None and (
        finalization.run_id != plan.id
        or finalization.plan_id != plan.id
        or finalization.run_key != plan.run_key
        or finalization.head_id != head.id
        or finalization.checkpoint_id != head.checkpoint_id
        or finalization.completed_prefix != head.completed_prefix
        or finalization.completed_prefix != len(plan.chunks)
    ):
        raise RecognitionRunIntegrityError(
            "Recognition terminal pointer crossed its plan or complete head"
        )
    payload = {
        "schema_version": 1,
        "run_key": plan.run_key,
        "run_id": plan.id,
        "plan_id": plan.id,
        "completed_prefix": head.completed_prefix,
        "head_id": head.id,
        "head_record": digest_recognition_content(canonical_json_bytes(head)),
        "finalization_id": finalization.id if finalization is not None else None,
        "finalization_record": (
            digest_recognition_content(canonical_json_bytes(finalization))
            if finalization is not None
            else None
        ),
    }
    return _RecognitionActiveHeadPointerV1(
        **payload,
        pointer_hash=hash_object(payload),
    )


@dataclass(frozen=True, slots=True)
class RecognitionChunkReplayV1:
    """One fully revalidated chunk slot returned in exact prefix order."""

    receipt: ChunkExecutionReceiptV1
    derived_chunk: RecognitionDerivedChunkV1
    adapter_observation_bytes: bytes
    adapter_observation: dict[str, Any]
    directory: Path


@dataclass(frozen=True, slots=True)
class StoredRecognitionRunV1:
    """One fully revalidated durable run head."""

    plan: RecognitionRunPlanV1
    checkpoint: RecognitionRunCheckpointV1
    head: RecognitionRunHeadV1
    completed_prefix: int
    directory: Path


@dataclass(frozen=True, slots=True)
class _LoadedRecognitionRun:
    plan: RecognitionRunPlanV1
    checkpoint: RecognitionRunCheckpointV1
    head: RecognitionRunHeadV1
    slots: tuple[RecognitionChunkReplayV1, ...]
    finalization: RecognitionRunFinalizationV1 | None
    repairable_finalization: RecognitionRunFinalizationV1 | None
    repairable_tail: RecognitionChunkReplayV1 | None
    directory: Path

    def stored(self) -> StoredRecognitionRunV1:
        return StoredRecognitionRunV1(
            plan=self.plan,
            checkpoint=self.checkpoint,
            head=self.head,
            completed_prefix=self.checkpoint.completed_prefix,
            directory=self.directory,
        )


ModelT = TypeVar("ModelT", bound=BaseModel)


def _read_canonical_model(
    path: Path,
    model: type[ModelT],
    *,
    label: str,
) -> tuple[ModelT, bytes]:
    if path.is_symlink() or not path.is_file():
        raise RecognitionRunIntegrityError(f"{label} is missing or is not a regular file")
    try:
        payload = path.read_bytes()
        typed = model.model_validate_json(payload)
    except (OSError, ValidationError, ValueError) as exc:
        raise RecognitionRunIntegrityError(f"{label} is unreadable or invalid") from exc
    if canonical_json_bytes(typed) != payload:
        raise RecognitionRunIntegrityError(f"{label} is not exact canonical JSON")
    return typed, payload


def _reject_json_constant(value: str) -> None:
    raise ValueError(f"non-finite JSON number is forbidden: {value}")


def _strict_json_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"duplicate JSON key is forbidden: {key!r}")
        result[key] = value
    return result


def _parse_adapter_observation(payload: bytes) -> dict[str, Any]:
    try:
        parsed = json.loads(
            payload.decode("utf-8"),
            parse_constant=_reject_json_constant,
            object_pairs_hook=_strict_json_object,
        )
    except (UnicodeDecodeError, json.JSONDecodeError, ValueError) as exc:
        raise RecognitionRunIntegrityError(
            "Recognition adapter observation is not strict UTF-8 JSON"
        ) from exc
    if not isinstance(parsed, dict):
        raise RecognitionRunIntegrityError(
            "Recognition adapter observation must be one SDK mapping object"
        )
    if canonical_json_bytes(parsed) != payload:
        raise RecognitionRunIntegrityError("Recognition adapter observation is not canonical JSON")
    return parsed


def _parse_canonical_evidence_bytes(payload: bytes) -> dict[str, Any]:
    """Validate only the canonical JSON boundary; Slice 2 replays typed Evidence."""

    try:
        parsed = json.loads(
            payload.decode("utf-8"),
            parse_constant=_reject_json_constant,
            object_pairs_hook=_strict_json_object,
        )
    except (UnicodeDecodeError, json.JSONDecodeError, ValueError) as exc:
        raise RecognitionRunIntegrityError(
            "Recognition Evidence binding requires strict canonical UTF-8 JSON bytes"
        ) from exc
    if not isinstance(parsed, dict) or canonical_json_bytes(parsed) != payload:
        raise RecognitionRunIntegrityError(
            "Recognition Evidence binding requires one canonical JSON object"
        )
    return parsed


def _is_allowed_temp(entry: Path, *, allow_slot: bool = False) -> bool:
    if entry.is_symlink():
        return False
    if entry.is_file() and _TEMP_FILE_RE.fullmatch(entry.name):
        return True
    return bool(allow_slot and entry.is_dir() and _TEMP_SLOT_RE.fullmatch(entry.name))


def _is_link_like(path: Path) -> bool:
    """Recognize symlinks and Windows junctions without following them."""

    try:
        if path.is_symlink():
            return True
        is_junction = getattr(path, "is_junction", None)
        return bool(is_junction is not None and is_junction())
    except OSError as exc:
        raise RecognitionRunIntegrityError(
            "Recognition repository path metadata is unreadable"
        ) from exc


def _strict_inventory(
    directory: Path,
    *,
    files: set[str],
    directories: set[str],
    label: str,
    allow_slot_temp: bool = False,
) -> None:
    if directory.is_symlink() or not directory.is_dir():
        raise RecognitionRunIntegrityError(f"{label} is missing or is not a directory")
    actual_files: set[str] = set()
    actual_directories: set[str] = set()
    for entry in directory.iterdir():
        if _is_allowed_temp(entry, allow_slot=allow_slot_temp):
            continue
        if entry.is_symlink():
            raise RecognitionRunIntegrityError(f"{label} contains a symbolic link")
        if entry.is_file():
            actual_files.add(entry.name)
        elif entry.is_dir():
            actual_directories.add(entry.name)
        else:
            raise RecognitionRunIntegrityError(f"{label} contains an unknown entry type")
    if actual_files != files or actual_directories != directories:
        raise RecognitionRunIntegrityError(
            f"{label} inventory mismatch: files={sorted(actual_files)!r}, "
            f"directories={sorted(actual_directories)!r}"
        )


def _publish_immutable_file(path: Path, payload: bytes, *, label: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        if path.is_symlink() or not path.is_file() or path.read_bytes() != payload:
            raise RecognitionRunConflictError(f"{label} already has conflicting bytes")
        return
    temporary = path.parent / f".{path.name}.tmp"
    _write_fsynced(temporary, payload)
    try:
        _replace_fsynced(temporary, path)
    except OSError:
        if not path.is_file() or path.read_bytes() != payload:
            raise
        temporary.unlink(missing_ok=True)


@contextmanager
def _exclusive_recognition_lock(path: Path):
    try:
        from filelock import FileLock, Timeout
    except ImportError as exc:  # pragma: no cover - declared core dependency
        raise RecognitionRunIntegrityError(
            "cross-process Recognition run locking requires filelock"
        ) from exc
    path.parent.mkdir(parents=True, exist_ok=True)
    if _is_link_like(path) or (path.exists() and not path.is_file()):
        raise RecognitionRunIntegrityError(
            "Recognition run lock path is a symbolic link, junction, or non-file"
        )
    try:
        with FileLock(str(path)).acquire(timeout=30):
            if _is_link_like(path) or not path.is_file():
                raise RecognitionRunIntegrityError(
                    "Recognition run lock path changed while acquiring the lock"
                )
            yield
    except Timeout as exc:
        raise RecognitionRunIntegrityError(
            f"timed out acquiring Recognition run lock: {path}"
        ) from exc


class RecognitionRunRepository:
    """Crash-safe filesystem repository for bounded Recognition recovery state.

    ``subtitle_root`` is the episode's existing ``.subtitle-v2`` directory.
    The repository owns only ``recognition-runs`` beneath it and never writes a
    Generation, RecognitionEvidence, CanonicalTranscript, or derived WAV copy.
    """

    def __init__(self, subtitle_root: str | Path) -> None:
        self.subtitle_root = Path(subtitle_root).resolve()
        self.root = self.subtitle_root / "recognition-runs"
        self.keys_dir = self.root / "keys"
        self.runs_dir = self.root / "runs"
        self.locks_dir = self.root / "locks"
        self._lock = threading.RLock()

    def _validate_repository_topology(self, *, create: bool) -> None:
        """Reject owned-directory links/escapes before any repository write."""

        owned = (
            (self.root, "Recognition repository root"),
            (self.keys_dir, "Recognition keys directory"),
            (self.runs_dir, "Recognition runs directory"),
            (self.locks_dir, "Recognition locks directory"),
        )
        for path, label in owned:
            if _is_link_like(path):
                raise RecognitionRunIntegrityError(f"{label} is a symbolic link or junction")
            if path.exists() and not path.is_dir():
                raise RecognitionRunIntegrityError(f"{label} is not a directory")

        if create:
            self.subtitle_root.mkdir(parents=True, exist_ok=True)
            self.root.mkdir(exist_ok=True)
            for path, _label in owned[1:]:
                path.mkdir(exist_ok=True)

        if _is_link_like(self.subtitle_root) or not self.subtitle_root.is_dir():
            raise RecognitionRunIntegrityError(
                "Recognition subtitle root is missing or is a symbolic link"
            )
        subtitle_root = self.subtitle_root.resolve(strict=True)
        expected_root = subtitle_root / "recognition-runs"
        if _is_link_like(self.root) or not self.root.is_dir():
            raise RecognitionRunIntegrityError("Recognition repository root is missing")
        root = self.root.resolve(strict=True)
        if root != expected_root:
            raise RecognitionRunIntegrityError("Recognition repository root escaped subtitle root")
        for path, label in owned[1:]:
            if _is_link_like(path) or not path.is_dir():
                raise RecognitionRunIntegrityError(f"{label} is missing or is a symbolic link")
            if path.resolve(strict=True) != root / path.name:
                raise RecognitionRunIntegrityError(f"{label} escaped repository root")

    def _key_path(self, run_key: str) -> Path:
        _required_sha256(run_key, label="Recognition run_key")
        return self.keys_dir / f"{run_key}.json"

    def _run_dir(self, run_id: str) -> Path:
        if not _RUN_ID_RE.fullmatch(run_id):
            raise ValueError("Recognition run ID is invalid")
        return self.runs_dir / run_id

    def _run_lock_path(self, run_key: str) -> Path:
        _required_sha256(run_key, label="Recognition run_key")
        return self.locks_dir / f"{run_key}.lock"

    def _execution_lock_path(self, run_key: str) -> Path:
        _required_sha256(run_key, label="Recognition run_key")
        return self.locks_dir / f"{run_key}.execution.lock"

    @contextmanager
    def execution_lease(self, run_key: str):
        """Serialize live provider execution for one stable logical request.

        The lease intentionally uses a different file from the short repository
        transaction lock.  An Adapter may therefore hold this lease while
        calling :meth:`prepare`, :meth:`commit_chunk`, or finalization methods
        without recursively acquiring the same OS lock.  Repository methods
        never acquire the execution lease, so the only supported lock order is
        execution lease -> repository transaction.

        This closes the concurrent-live-process duplicate-call race.  It cannot
        make a provider response durable if the process dies after the provider
        returns but before :meth:`commit_chunk` publishes the observation.
        """

        self._validate_repository_topology(create=True)
        with _exclusive_recognition_lock(self._execution_lock_path(run_key)):
            self._validate_repository_topology(create=False)
            yield

    @contextmanager
    def _run_transaction(self, run_key: str):
        self._validate_repository_topology(create=True)
        with self._lock, _exclusive_recognition_lock(self._run_lock_path(run_key)):
            self._validate_repository_topology(create=False)
            yield

    @staticmethod
    def _checkpoint_path(directory: Path, checkpoint_id: str) -> Path:
        return directory / "checkpoints" / f"{checkpoint_id}.json"

    @staticmethod
    def _head_path(directory: Path, head_id: str) -> Path:
        return directory / "heads" / f"{head_id}.json"

    @staticmethod
    def _slot_path(directory: Path, index: int) -> Path:
        if index < 0 or index > 999_999:
            raise ValueError("Recognition chunk index cannot be represented by the store")
        return directory / "chunks" / f"{index:06d}"

    def _read_key_binding(self, run_key: str) -> _RecognitionRunKeyBindingV1:
        path = self._key_path(run_key)
        if not path.exists():
            raise RecognitionRunNotFoundError(f"Recognition run key is not prepared: {run_key}")
        binding, _payload = _read_canonical_model(
            path,
            _RecognitionRunKeyBindingV1,
            label="Recognition run key binding",
        )
        if binding.run_key != run_key:
            raise RecognitionRunIntegrityError(
                "Recognition run key path crossed its authenticated binding"
            )
        return binding

    def _read_plan_for_binding(
        self,
        binding: _RecognitionRunKeyBindingV1,
    ) -> tuple[RecognitionRunPlanV1, Path]:
        directory = self._run_dir(binding.run_id)
        plan, plan_bytes = _read_canonical_model(
            directory / _PLAN_NAME,
            RecognitionRunPlanV1,
            label="Recognition run plan",
        )
        if (
            plan.id != binding.run_id
            or plan.id != binding.plan_id
            or plan.run_key != binding.run_key
            or digest_recognition_content(plan_bytes) != binding.plan_record
            or plan.request.content_hash != binding.request_binding_hash
            or plan.adapter_identity.content_hash != binding.adapter_identity_hash
            or rebuild_recognition_run_plan(plan) != plan
        ):
            raise RecognitionRunIntegrityError(
                "Recognition key binding does not replay its complete plan"
            )
        return plan, directory

    def _find_orphan_run_plan(
        self,
        run_key: str,
    ) -> tuple[RecognitionRunPlanV1, Path] | None:
        """Find the sole durable run whose key binding was lost after publication."""

        matches: list[tuple[RecognitionRunPlanV1, Path]] = []
        for entry in self.runs_dir.iterdir():
            if _is_link_like(entry):
                raise RecognitionRunIntegrityError(
                    "Recognition runs inventory contains a symbolic link"
                )
            if entry.is_dir() and _TEMP_RUN_RE.fullmatch(entry.name):
                continue
            if not entry.is_dir() or not _RUN_ID_RE.fullmatch(entry.name):
                raise RecognitionRunIntegrityError(
                    "Recognition runs inventory contains an unknown entry"
                )
            stored_plan, _plan_bytes = _read_canonical_model(
                entry / _PLAN_NAME,
                RecognitionRunPlanV1,
                label="orphan Recognition run plan",
            )
            if (
                stored_plan.id != entry.name
                or rebuild_recognition_run_plan(stored_plan) != stored_plan
            ):
                raise RecognitionRunIntegrityError(
                    "orphan Recognition run directory does not match its plan identity"
                )
            if stored_plan.run_key == run_key:
                matches.append((stored_plan, entry))
        if len(matches) > 1:
            raise RecognitionRunIntegrityError(
                "Recognition run key has multiple orphan run directories"
            )
        return matches[0] if matches else None

    @staticmethod
    def _build_chain(
        plan: RecognitionRunPlanV1,
        receipts: Sequence[ChunkExecutionReceiptV1],
    ) -> tuple[
        tuple[RecognitionRunCheckpointV1, ...],
        tuple[RecognitionRunHeadV1, ...],
    ]:
        checkpoints: list[RecognitionRunCheckpointV1] = []
        heads: list[RecognitionRunHeadV1] = []
        for prefix in range(len(receipts) + 1):
            checkpoint = build_recognition_run_checkpoint(
                plan=plan,
                receipts=receipts[:prefix],
                previous_checkpoint_id=(checkpoints[-1].id if checkpoints else None),
            )
            head = build_recognition_run_head(
                plan=plan,
                checkpoint=checkpoint,
                previous_head_id=heads[-1].id if heads else None,
            )
            checkpoints.append(checkpoint)
            heads.append(head)
        return tuple(checkpoints), tuple(heads)

    def _read_slot(
        self,
        *,
        directory: Path,
        plan: RecognitionRunPlanV1,
        index: int,
        previous_receipt_id: str | None,
    ) -> RecognitionChunkReplayV1:
        slot = self._slot_path(directory, index)
        _strict_inventory(
            slot,
            files={_EXECUTION_NAME, _OBSERVATION_NAME, _DERIVED_CHUNK_NAME},
            directories=set(),
            label=f"Recognition chunk slot {index}",
        )
        derived, derived_bytes = _read_canonical_model(
            slot / _DERIVED_CHUNK_NAME,
            RecognitionDerivedChunkV1,
            label=f"Recognition derived chunk record {index}",
        )
        receipt, _receipt_bytes = _read_canonical_model(
            slot / _EXECUTION_NAME,
            ChunkExecutionReceiptV1,
            label=f"Recognition chunk execution receipt {index}",
        )
        try:
            observation_bytes = (slot / _OBSERVATION_NAME).read_bytes()
        except OSError as exc:
            raise RecognitionRunIntegrityError(
                f"Recognition adapter observation {index} is unreadable"
            ) from exc
        observation = _parse_adapter_observation(observation_bytes)
        chunk = plan.chunks[index]
        expected_derived_payload = {
            "schema_version": 1,
            "run_id": plan.id,
            "plan_id": plan.id,
            "chunk_id": chunk.id,
            "chunk_index": chunk.index,
            "derived_audio": chunk.derived_audio,
            "persistence": "digest_only_no_artifact_uri",
        }
        expected_derived = RecognitionDerivedChunkV1(
            **expected_derived_payload,
            content_hash=hash_object(expected_derived_payload),
        )
        if derived != expected_derived:
            raise RecognitionRunIntegrityError(
                f"Recognition derived chunk {index} crossed its plan"
            )
        expected_receipt_payload = {
            "schema_version": 1,
            "run_id": plan.id,
            "run_key": plan.run_key,
            "plan_id": plan.id,
            "chunk_id": chunk.id,
            "chunk_index": chunk.index,
            "previous_receipt_id": previous_receipt_id,
            "request_binding_hash": plan.request.content_hash,
            "adapter_identity_hash": plan.adapter_identity.content_hash,
            "derived_chunk_record": digest_recognition_content(derived_bytes),
            "derived_audio": chunk.derived_audio,
            "adapter_observation": digest_recognition_content(observation_bytes),
            "observation_semantics": ("adapter_observed_sdk_mapping_canonical_json_v1"),
            "provider_wire_bytes_claimed": False,
            "execution_status": "completed",
        }
        expected_receipt = ChunkExecutionReceiptV1(
            **expected_receipt_payload,
            id="recognition-chunk-receipt-" + hash_object(expected_receipt_payload),
        )
        if receipt != expected_receipt:
            raise RecognitionRunIntegrityError(
                f"Recognition execution receipt {index} does not replay exact slot bytes"
            )
        return RecognitionChunkReplayV1(
            receipt=receipt,
            derived_chunk=derived,
            adapter_observation_bytes=observation_bytes,
            adapter_observation=observation,
            directory=slot,
        )

    def _read_all_slots(
        self,
        *,
        directory: Path,
        plan: RecognitionRunPlanV1,
    ) -> tuple[RecognitionChunkReplayV1, ...]:
        chunks_dir = directory / "chunks"
        if chunks_dir.is_symlink() or not chunks_dir.is_dir():
            raise RecognitionRunIntegrityError("Recognition chunks inventory is missing")
        names: list[str] = []
        for entry in chunks_dir.iterdir():
            if _is_allowed_temp(entry, allow_slot=True):
                continue
            if entry.is_symlink() or not entry.is_dir() or not _SLOT_NAME_RE.fullmatch(entry.name):
                raise RecognitionRunIntegrityError(
                    "Recognition chunks inventory contains an unknown entry"
                )
            names.append(entry.name)
        names.sort()
        expected_names = [f"{index:06d}" for index in range(len(names))]
        if names != expected_names:
            raise RecognitionRunIntegrityError(
                "Recognition chunk slots are duplicated, reordered, or gapped"
            )
        if len(names) > len(plan.chunks):
            raise RecognitionRunIntegrityError(
                "Recognition chunk slots exceed their immutable plan"
            )
        replay: list[RecognitionChunkReplayV1] = []
        previous_receipt_id: str | None = None
        for index in range(len(names)):
            item = self._read_slot(
                directory=directory,
                plan=plan,
                index=index,
                previous_receipt_id=previous_receipt_id,
            )
            replay.append(item)
            previous_receipt_id = item.receipt.id
        return tuple(replay)

    @staticmethod
    def _read_named_records(
        directory: Path,
        *,
        pattern: re.Pattern[str],
        label: str,
    ) -> dict[str, Path]:
        if directory.is_symlink() or not directory.is_dir():
            raise RecognitionRunIntegrityError(f"{label} directory is missing")
        result: dict[str, Path] = {}
        for entry in directory.iterdir():
            if _is_allowed_temp(entry):
                continue
            if entry.is_symlink() or not entry.is_file() or not entry.name.endswith(".json"):
                raise RecognitionRunIntegrityError(f"{label} contains an unknown entry")
            identity = entry.name[:-5]
            if not pattern.fullmatch(identity) or identity in result:
                raise RecognitionRunIntegrityError(f"{label} filename identity is invalid")
            result[identity] = entry
        return result

    def _load_run_directory(
        self,
        *,
        plan: RecognitionRunPlanV1,
        directory: Path,
        allow_repairable_tail: bool,
        allow_repairable_finalization: bool = False,
    ) -> _LoadedRecognitionRun:
        _strict_inventory(
            directory,
            files={_PLAN_NAME, _ACTIVE_HEAD_NAME},
            directories={"chunks", "checkpoints", "heads", "finalizations"},
            label="Recognition run directory",
        )
        stored_plan, stored_plan_bytes = _read_canonical_model(
            directory / _PLAN_NAME,
            RecognitionRunPlanV1,
            label="Recognition run plan",
        )
        if stored_plan != plan or rebuild_recognition_run_plan(stored_plan) != plan:
            raise RecognitionRunIntegrityError("Recognition run directory plan drifted")
        if digest_recognition_content(stored_plan_bytes) != digest_recognition_content(
            canonical_json_bytes(plan)
        ):
            raise RecognitionRunIntegrityError("Recognition run plan bytes drifted")

        pointer, _pointer_bytes = _read_canonical_model(
            directory / _ACTIVE_HEAD_NAME,
            _RecognitionActiveHeadPointerV1,
            label="Recognition active-head pointer",
        )
        if (
            pointer.run_id != plan.id
            or pointer.plan_id != plan.id
            or pointer.run_key != plan.run_key
            or pointer.completed_prefix > len(plan.chunks)
        ):
            raise RecognitionRunIntegrityError(
                "Recognition active-head pointer crossed its immutable plan"
            )

        slots = self._read_all_slots(directory=directory, plan=plan)
        prefix = pointer.completed_prefix
        if len(slots) == prefix:
            tail = None
        elif len(slots) == prefix + 1:
            if not allow_repairable_tail:
                raise RecognitionRunIntegrityError(
                    "Recognition run has a fully published slot awaiting head repair"
                )
            tail = slots[-1]
        else:
            raise RecognitionRunIntegrityError(
                "Recognition slot inventory is not the exact active prefix or one repairable tail"
            )

        active_receipts = tuple(item.receipt for item in slots[:prefix])
        checkpoints, heads = self._build_chain(plan, active_receipts)
        active_checkpoint = checkpoints[-1]
        active_head = heads[-1]
        expected_nonterminal_pointer = _build_active_head_pointer(
            plan=plan,
            head=active_head,
        )
        if (
            pointer.run_key != expected_nonterminal_pointer.run_key
            or pointer.run_id != expected_nonterminal_pointer.run_id
            or pointer.plan_id != expected_nonterminal_pointer.plan_id
            or pointer.completed_prefix != expected_nonterminal_pointer.completed_prefix
            or pointer.head_id != expected_nonterminal_pointer.head_id
            or pointer.head_record != expected_nonterminal_pointer.head_record
        ):
            raise RecognitionRunIntegrityError(
                "Recognition active pointer/head/checkpoint chain does not replay"
            )

        checkpoint_files = self._read_named_records(
            directory / "checkpoints",
            pattern=_CHECKPOINT_ID_RE,
            label="Recognition checkpoints",
        )
        head_files = self._read_named_records(
            directory / "heads",
            pattern=_HEAD_ID_RE,
            label="Recognition heads",
        )
        expected_checkpoints = {item.id: item for item in checkpoints}
        expected_heads = {item.id: item for item in heads}
        optional_checkpoint: RecognitionRunCheckpointV1 | None = None
        optional_head: RecognitionRunHeadV1 | None = None
        if tail is not None:
            all_receipts = tuple(item.receipt for item in slots)
            optional_checkpoint = build_recognition_run_checkpoint(
                plan=plan,
                receipts=all_receipts,
                previous_checkpoint_id=active_checkpoint.id,
            )
            optional_head = build_recognition_run_head(
                plan=plan,
                checkpoint=optional_checkpoint,
                previous_head_id=active_head.id,
            )
            if optional_head.id in head_files and optional_checkpoint.id not in checkpoint_files:
                raise RecognitionRunIntegrityError(
                    "Recognition repair tail published a head without its checkpoint"
                )

        allowed_checkpoint_ids = set(expected_checkpoints)
        allowed_head_ids = set(expected_heads)
        if optional_checkpoint is not None:
            allowed_checkpoint_ids.add(optional_checkpoint.id)
        if optional_head is not None:
            allowed_head_ids.add(optional_head.id)
        if not set(checkpoint_files).issubset(allowed_checkpoint_ids) or not set(
            head_files
        ).issubset(allowed_head_ids):
            raise RecognitionRunIntegrityError(
                "Recognition checkpoint/head inventory contains a fork or post-gap record"
            )
        if not set(expected_checkpoints).issubset(checkpoint_files) or not set(
            expected_heads
        ).issubset(head_files):
            raise RecognitionRunIntegrityError("Recognition checkpoint/head chain is truncated")

        for checkpoint_id, expected in expected_checkpoints.items():
            actual, _bytes = _read_canonical_model(
                checkpoint_files[checkpoint_id],
                RecognitionRunCheckpointV1,
                label=f"Recognition checkpoint {checkpoint_id}",
            )
            if actual != expected:
                raise RecognitionRunIntegrityError(
                    "Recognition checkpoint chain does not rebuild exactly"
                )
        for head_id, expected in expected_heads.items():
            actual, head_bytes = _read_canonical_model(
                head_files[head_id],
                RecognitionRunHeadV1,
                label=f"Recognition head {head_id}",
            )
            if actual != expected:
                raise RecognitionRunIntegrityError(
                    "Recognition head chain does not rebuild exactly"
                )
            if (
                head_id == active_head.id
                and digest_recognition_content(head_bytes) != pointer.head_record
            ):
                raise RecognitionRunIntegrityError(
                    "Recognition active pointer no longer binds exact head bytes"
                )
        if optional_checkpoint is not None and optional_checkpoint.id in checkpoint_files:
            actual, _bytes = _read_canonical_model(
                checkpoint_files[optional_checkpoint.id],
                RecognitionRunCheckpointV1,
                label="Recognition repair-tail checkpoint",
            )
            if actual != optional_checkpoint:
                raise RecognitionRunIntegrityError(
                    "Recognition repair-tail checkpoint does not replay"
                )
        if optional_head is not None and optional_head.id in head_files:
            actual, _bytes = _read_canonical_model(
                head_files[optional_head.id],
                RecognitionRunHeadV1,
                label="Recognition repair-tail head",
            )
            if actual != optional_head:
                raise RecognitionRunIntegrityError("Recognition repair-tail head does not replay")

        finalization_files = self._read_named_records(
            directory / "finalizations",
            pattern=_FINALIZATION_ID_RE,
            label="Recognition finalizations",
        )
        if len(finalization_files) > 1:
            raise RecognitionRunIntegrityError("Recognition run contains conflicting finalizations")
        parsed_finalization: RecognitionRunFinalizationV1 | None = None
        finalization_bytes: bytes | None = None
        if finalization_files:
            if tail is not None or prefix != len(plan.chunks):
                raise RecognitionRunIntegrityError(
                    "Recognition finalization exists before the complete stable prefix"
                )
            filename_id, path = next(iter(finalization_files.items()))
            parsed_finalization, finalization_bytes = _read_canonical_model(
                path,
                RecognitionRunFinalizationV1,
                label="Recognition run finalization",
            )
            if parsed_finalization.id != filename_id:
                raise RecognitionRunIntegrityError(
                    "Recognition finalization filename identity does not match typed ID"
                )
            if (
                parsed_finalization.run_id != plan.id
                or parsed_finalization.plan_id != plan.id
                or parsed_finalization.run_key != plan.run_key
                or parsed_finalization.head_id != active_head.id
                or parsed_finalization.checkpoint_id != active_checkpoint.id
                or parsed_finalization.completed_prefix != prefix
            ):
                raise RecognitionRunIntegrityError(
                    "Recognition finalization crossed its complete prefix"
                )
            _require_failure_plan_membership(
                plan=plan,
                failure=parsed_finalization.failure,
            )

        finalization: RecognitionRunFinalizationV1 | None = None
        repairable_finalization: RecognitionRunFinalizationV1 | None = None
        if pointer.finalization_id is None:
            if pointer != expected_nonterminal_pointer:
                raise RecognitionRunIntegrityError(
                    "Recognition nonterminal active pointer does not replay exactly"
                )
            if parsed_finalization is not None:
                if not allow_repairable_finalization:
                    raise RecognitionRunIntegrityError(
                        "Recognition finalization is awaiting terminal pointer repair"
                    )
                repairable_finalization = parsed_finalization
        else:
            if tail is not None or prefix != len(plan.chunks):
                raise RecognitionRunIntegrityError(
                    "Recognition terminal pointer exists before the complete stable prefix"
                )
            if parsed_finalization is None or finalization_bytes is None:
                raise RecognitionRunIntegrityError(
                    "Recognition terminal pointer binds a missing finalization"
                )
            expected_terminal_pointer = _build_active_head_pointer(
                plan=plan,
                head=active_head,
                finalization=parsed_finalization,
            )
            if (
                pointer != expected_terminal_pointer
                or pointer.finalization_record != digest_recognition_content(finalization_bytes)
            ):
                raise RecognitionRunIntegrityError(
                    "Recognition terminal pointer does not bind exact finalization bytes"
                )
            finalization = parsed_finalization
        return _LoadedRecognitionRun(
            plan=plan,
            checkpoint=active_checkpoint,
            head=active_head,
            slots=slots[:prefix],
            finalization=finalization,
            repairable_finalization=repairable_finalization,
            repairable_tail=tail,
            directory=directory,
        )

    def _load(
        self,
        run_key: str,
        *,
        expected_plan: RecognitionRunPlanV1 | None,
        expected_adapter_identity: RecognitionAdapterIdentitySnapshotV1 | None,
        allow_repairable_tail: bool,
        allow_repairable_finalization: bool = False,
    ) -> _LoadedRecognitionRun:
        self._validate_repository_topology(create=False)
        binding = self._read_key_binding(run_key)
        plan, directory = self._read_plan_for_binding(binding)
        if expected_plan is not None and plan != expected_plan:
            raise RecognitionRunIntegrityError(
                "Recognition run key already binds a different complete plan"
            )
        if (
            expected_adapter_identity is not None
            and plan.adapter_identity != expected_adapter_identity
        ):
            raise RecognitionRunIntegrityError(
                "Recognition runtime/model identity drifted for an existing run key"
            )
        return self._load_run_directory(
            plan=plan,
            directory=directory,
            allow_repairable_tail=allow_repairable_tail,
            allow_repairable_finalization=allow_repairable_finalization,
        )

    def _write_initial_run(self, directory: Path, plan: RecognitionRunPlanV1) -> None:
        for name in ("chunks", "checkpoints", "heads", "finalizations"):
            (directory / name).mkdir(parents=True, exist_ok=False)
        _write_fsynced(directory / _PLAN_NAME, canonical_json_bytes(plan))
        checkpoint = build_recognition_run_checkpoint(
            plan=plan,
            receipts=(),
            previous_checkpoint_id=None,
        )
        head = build_recognition_run_head(
            plan=plan,
            checkpoint=checkpoint,
            previous_head_id=None,
        )
        _write_fsynced(
            self._checkpoint_path(directory, checkpoint.id),
            canonical_json_bytes(checkpoint),
        )
        _write_fsynced(
            self._head_path(directory, head.id),
            canonical_json_bytes(head),
        )
        _write_fsynced(
            directory / _ACTIVE_HEAD_NAME,
            canonical_json_bytes(_build_active_head_pointer(plan=plan, head=head)),
        )

    @staticmethod
    def _verify_plan_derivation(
        plan: RecognitionRunPlanV1,
        normalized_audio: str | Path,
    ) -> None:
        path = _verify_normalized_audio_binding(normalized_audio, plan.audio)
        for chunk in plan.chunks:
            derived = _derive_recognition_pcm_wav_chunk(
                path,
                audio=plan.audio,
                start_sample=chunk.inference_start_sample,
                end_sample=chunk.inference_end_sample,
            )
            if digest_recognition_content(derived) != chunk.derived_audio:
                raise RecognitionRunIntegrityError(
                    f"Recognition chunk {chunk.index} digest does not derive from exact audio"
                )
        if build_recognition_audio_binding(path) != plan.audio:
            raise RecognitionRunIntegrityError(
                "normalized PCM WAV changed while the Recognition plan was verified"
            )

    def prepare(
        self,
        plan: RecognitionRunPlanV1,
        *,
        normalized_audio: str | Path,
    ) -> StoredRecognitionRunV1:
        """Prepare exactly one durable run for a stable request lookup key.

        Since ``run_key`` excludes executable identity, a new runtime/model for
        an existing logical request is rejected rather than silently forked.
        """

        rebuilt = rebuild_recognition_run_plan(plan)
        if rebuilt != plan:
            raise RecognitionRunIntegrityError("Recognition plan does not rebuild exactly")
        with self._run_transaction(plan.run_key):
            self._verify_plan_derivation(plan, normalized_audio)
            key_path = self._key_path(plan.run_key)
            if key_path.exists():
                return self._load(
                    plan.run_key,
                    expected_plan=plan,
                    expected_adapter_identity=plan.adapter_identity,
                    allow_repairable_tail=False,
                ).stored()

            orphan = self._find_orphan_run_plan(plan.run_key)
            if orphan is not None:
                orphan_plan, orphan_directory = orphan
                self._load_run_directory(
                    plan=orphan_plan,
                    directory=orphan_directory,
                    allow_repairable_tail=False,
                )
                if orphan_plan != plan:
                    raise RecognitionRunIntegrityError(
                        "orphan Recognition run key already binds a different complete plan"
                    )
                binding = _build_key_binding(orphan_plan)
                _publish_immutable_file(
                    key_path,
                    canonical_json_bytes(binding),
                    label="Recognition run key binding",
                )
                return self._load(
                    plan.run_key,
                    expected_plan=plan,
                    expected_adapter_identity=plan.adapter_identity,
                    allow_repairable_tail=False,
                ).stored()

            destination = self._run_dir(plan.id)
            if destination.exists():
                self._load_run_directory(
                    plan=plan,
                    directory=destination,
                    allow_repairable_tail=False,
                )
            else:
                temporary = Path(tempfile.mkdtemp(prefix=".run-", dir=self.runs_dir))
                try:
                    self._write_initial_run(temporary, plan)
                    try:
                        _replace_fsynced(temporary, destination)
                    except OSError:
                        if not destination.is_dir():
                            raise
                        self._load_run_directory(
                            plan=plan,
                            directory=destination,
                            allow_repairable_tail=False,
                        )
                        shutil.rmtree(temporary, ignore_errors=True)
                except Exception:
                    if temporary.exists():
                        shutil.rmtree(temporary, ignore_errors=True)
                    raise

            binding = _build_key_binding(plan)
            _publish_immutable_file(
                key_path,
                canonical_json_bytes(binding),
                label="Recognition run key binding",
            )
            return self._load(
                plan.run_key,
                expected_plan=plan,
                expected_adapter_identity=plan.adapter_identity,
                allow_repairable_tail=False,
            ).stored()

    def load_plan(
        self,
        run_key: str,
        *,
        expected_plan: RecognitionRunPlanV1 | None = None,
        expected_adapter_identity: RecognitionAdapterIdentitySnapshotV1 | None = None,
    ) -> RecognitionRunPlanV1:
        """Load a stable plan only after exact pointer, chain, and inventory replay."""

        return self._load(
            run_key,
            expected_plan=expected_plan,
            expected_adapter_identity=expected_adapter_identity,
            allow_repairable_tail=False,
        ).plan

    def load_state(
        self,
        run_key: str,
        *,
        expected_plan: RecognitionRunPlanV1 | None = None,
        expected_adapter_identity: RecognitionAdapterIdentitySnapshotV1 | None = None,
    ) -> StoredRecognitionRunV1:
        return self._load(
            run_key,
            expected_plan=expected_plan,
            expected_adapter_identity=expected_adapter_identity,
            allow_repairable_tail=False,
        ).stored()

    @staticmethod
    def _candidate_slot(
        *,
        plan: RecognitionRunPlanV1,
        index: int,
        previous_receipt_id: str | None,
        derived_chunk_bytes: bytes,
        adapter_observation: Mapping[str, Any],
    ) -> tuple[
        RecognitionDerivedChunkV1,
        bytes,
        bytes,
        ChunkExecutionReceiptV1,
    ]:
        if not isinstance(adapter_observation, Mapping) or not adapter_observation:
            raise RecognitionRunIntegrityError(
                "Recognition adapter observation must be a non-empty SDK mapping"
            )
        observation_bytes = canonical_json_bytes(dict(adapter_observation))
        _parse_adapter_observation(observation_bytes)
        chunk = plan.chunks[index]
        derived = build_recognition_derived_chunk(
            plan=plan,
            chunk=chunk,
            derived_chunk_bytes=derived_chunk_bytes,
        )
        derived_bytes = canonical_json_bytes(derived)
        receipt = build_chunk_execution_receipt(
            plan=plan,
            chunk=chunk,
            previous_receipt_id=previous_receipt_id,
            derived_chunk=derived,
            derived_chunk_bytes=derived_chunk_bytes,
            adapter_observation_bytes=observation_bytes,
        )
        return derived, derived_bytes, observation_bytes, receipt

    def _publish_slot(
        self,
        *,
        directory: Path,
        index: int,
        derived: RecognitionDerivedChunkV1,
        derived_bytes: bytes,
        observation_bytes: bytes,
        receipt: ChunkExecutionReceiptV1,
    ) -> None:
        destination = self._slot_path(directory, index)
        chunks_dir = directory / "chunks"
        temporary = Path(tempfile.mkdtemp(prefix=f".slot-{index:06d}-", dir=chunks_dir))
        try:
            _write_fsynced(temporary / _DERIVED_CHUNK_NAME, derived_bytes)
            _write_fsynced(temporary / _OBSERVATION_NAME, observation_bytes)
            _write_fsynced(temporary / _EXECUTION_NAME, canonical_json_bytes(receipt))
            try:
                _replace_fsynced(temporary, destination)
            except OSError:
                if not destination.is_dir():
                    raise
                shutil.rmtree(temporary, ignore_errors=True)
        except Exception:
            if temporary.exists():
                shutil.rmtree(temporary, ignore_errors=True)
            raise

    def _publish_active_pointer(
        self,
        directory: Path,
        pointer: _RecognitionActiveHeadPointerV1,
    ) -> None:
        temporary = directory / f".{_ACTIVE_HEAD_NAME}.tmp"
        _write_fsynced(temporary, canonical_json_bytes(pointer))
        _replace_fsynced(temporary, directory / _ACTIVE_HEAD_NAME)

    def _advance_repairable_tail(
        self,
        state: _LoadedRecognitionRun,
    ) -> _LoadedRecognitionRun:
        tail = state.repairable_tail
        if tail is None:
            return state
        receipts = tuple(item.receipt for item in (*state.slots, tail))
        checkpoint = build_recognition_run_checkpoint(
            plan=state.plan,
            receipts=receipts,
            previous_checkpoint_id=state.checkpoint.id,
        )
        head = build_recognition_run_head(
            plan=state.plan,
            checkpoint=checkpoint,
            previous_head_id=state.head.id,
        )
        _publish_immutable_file(
            self._checkpoint_path(state.directory, checkpoint.id),
            canonical_json_bytes(checkpoint),
            label="Recognition run checkpoint",
        )
        _publish_immutable_file(
            self._head_path(state.directory, head.id),
            canonical_json_bytes(head),
            label="Recognition run head",
        )
        self._publish_active_pointer(
            state.directory,
            _build_active_head_pointer(plan=state.plan, head=head),
        )
        return self._load(
            state.plan.run_key,
            expected_plan=state.plan,
            expected_adapter_identity=state.plan.adapter_identity,
            allow_repairable_tail=False,
        )

    def commit_chunk(
        self,
        *,
        plan: RecognitionRunPlanV1,
        chunk_index: int,
        derived_chunk_bytes: bytes,
        adapter_observation: Mapping[str, Any],
    ) -> RecognitionChunkReplayV1:
        """Publish and advance one exact next-prefix chunk atomically by pointer.

        An identical retry is idempotent.  A different observation or derived
        chunk for the same index is an integrity conflict, including when a
        previous process crashed after slot publication but before head advance.
        """

        if not 0 <= chunk_index < len(plan.chunks):
            raise ValueError("Recognition chunk index is outside its plan")
        with self._run_transaction(plan.run_key):
            state = self._load(
                plan.run_key,
                expected_plan=plan,
                expected_adapter_identity=plan.adapter_identity,
                allow_repairable_tail=True,
            )
            prefix = state.checkpoint.completed_prefix
            if chunk_index > prefix:
                raise RecognitionRunIntegrityError(
                    "Recognition chunk commit would create a prefix gap"
                )
            previous = state.slots[chunk_index - 1].receipt.id if chunk_index > 0 else None
            derived, derived_bytes, observation_bytes, candidate_receipt = self._candidate_slot(
                plan=plan,
                index=chunk_index,
                previous_receipt_id=previous,
                derived_chunk_bytes=derived_chunk_bytes,
                adapter_observation=adapter_observation,
            )
            if chunk_index < prefix:
                existing = state.slots[chunk_index]
                if (
                    existing.receipt != candidate_receipt
                    or existing.derived_chunk != derived
                    or existing.adapter_observation_bytes != observation_bytes
                ):
                    raise RecognitionRunConflictError(
                        "Recognition chunk slot already committed with different bytes"
                    )
                return existing
            if state.finalization is not None:
                raise RecognitionRunIntegrityError(
                    "Recognition run is already terminally finalized"
                )
            if state.repairable_tail is not None:
                existing = state.repairable_tail
                if (
                    existing.receipt != candidate_receipt
                    or existing.derived_chunk != derived
                    or existing.adapter_observation_bytes != observation_bytes
                ):
                    raise RecognitionRunConflictError(
                        "published Recognition tail conflicts with retry bytes"
                    )
            else:
                self._publish_slot(
                    directory=state.directory,
                    index=chunk_index,
                    derived=derived,
                    derived_bytes=derived_bytes,
                    observation_bytes=observation_bytes,
                    receipt=candidate_receipt,
                )
                state = self._load(
                    plan.run_key,
                    expected_plan=plan,
                    expected_adapter_identity=plan.adapter_identity,
                    allow_repairable_tail=True,
                )
            advanced = self._advance_repairable_tail(state)
            return advanced.slots[chunk_index]

    def repair_published_tail(
        self,
        *,
        plan: RecognitionRunPlanV1,
    ) -> StoredRecognitionRunV1:
        """Advance one fully verified slot left after a pointer-publication crash.

        This operation is disk-only: it has no provider abstraction and cannot
        make, retry, or account for an Adapter call.
        """

        with self._run_transaction(plan.run_key):
            state = self._load(
                plan.run_key,
                expected_plan=plan,
                expected_adapter_identity=plan.adapter_identity,
                allow_repairable_tail=True,
            )
            return self._advance_repairable_tail(state).stored()

    def replay_prefix(
        self,
        *,
        plan: RecognitionRunPlanV1,
    ) -> tuple[RecognitionChunkReplayV1, ...]:
        """Return only the exact verified 0..N-1 active prefix in fresh order."""

        return self._load(
            plan.run_key,
            expected_plan=plan,
            expected_adapter_identity=plan.adapter_identity,
            allow_repairable_tail=False,
        ).slots

    def commit_finalization(
        self,
        *,
        plan: RecognitionRunPlanV1,
        status: Literal["verified", "rejected"],
        recognition_evidence_bytes: bytes | None = None,
        failure: RecognitionRunFailureV1 | None = None,
    ) -> RecognitionRunFinalizationV1:
        """Commit one terminal outcome only after every planned chunk is durable."""

        with self._run_transaction(plan.run_key):
            state = self._load(
                plan.run_key,
                expected_plan=plan,
                expected_adapter_identity=plan.adapter_identity,
                allow_repairable_tail=False,
                allow_repairable_finalization=True,
            )
            if state.checkpoint.completed_prefix != len(plan.chunks):
                raise RecognitionRunIntegrityError(
                    "Recognition finalization requires the complete chunk prefix"
                )
            finalization = build_recognition_run_finalization(
                plan=plan,
                head=state.head,
                checkpoint=state.checkpoint,
                status=status,
                recognition_evidence_bytes=recognition_evidence_bytes,
                failure=failure,
            )
            if state.finalization is not None:
                if state.finalization != finalization:
                    raise RecognitionRunConflictError(
                        "Recognition run already has a different finalization"
                    )
                return state.finalization
            if state.repairable_finalization is not None:
                if state.repairable_finalization != finalization:
                    raise RecognitionRunConflictError(
                        "Recognition run already published a different finalization"
                    )
                self._publish_active_pointer(
                    state.directory,
                    _build_active_head_pointer(
                        plan=plan,
                        head=state.head,
                        finalization=state.repairable_finalization,
                    ),
                )
                loaded = self._load(
                    plan.run_key,
                    expected_plan=plan,
                    expected_adapter_identity=plan.adapter_identity,
                    allow_repairable_tail=False,
                ).finalization
                if loaded is None:
                    raise RecognitionRunIntegrityError(
                        "Recognition terminal pointer repair did not become durable"
                    )
                return loaded
            _publish_immutable_file(
                state.directory / "finalizations" / f"{finalization.id}.json",
                canonical_json_bytes(finalization),
                label="Recognition run finalization",
            )
            self._publish_active_pointer(
                state.directory,
                _build_active_head_pointer(
                    plan=plan,
                    head=state.head,
                    finalization=finalization,
                ),
            )
            loaded = self._load(
                plan.run_key,
                expected_plan=plan,
                expected_adapter_identity=plan.adapter_identity,
                allow_repairable_tail=False,
            ).finalization
            if loaded is None:
                raise RecognitionRunIntegrityError(
                    "Recognition finalization disappeared after publication"
                )
            return loaded

    def repair_published_finalization(
        self,
        *,
        plan: RecognitionRunPlanV1,
    ) -> RecognitionRunFinalizationV1:
        """Bind one exact immutable finalization left ahead of its pointer.

        This repair reads repository bytes only. It has no Adapter/provider
        abstraction and cannot choose or synthesize a different terminal outcome.
        """

        with self._run_transaction(plan.run_key):
            state = self._load(
                plan.run_key,
                expected_plan=plan,
                expected_adapter_identity=plan.adapter_identity,
                allow_repairable_tail=False,
                allow_repairable_finalization=True,
            )
            if state.finalization is not None:
                return state.finalization
            finalization = state.repairable_finalization
            if finalization is None:
                raise RecognitionRunIntegrityError(
                    "Recognition run has no published finalization to repair"
                )
            self._publish_active_pointer(
                state.directory,
                _build_active_head_pointer(
                    plan=plan,
                    head=state.head,
                    finalization=finalization,
                ),
            )
            loaded = self._load(
                plan.run_key,
                expected_plan=plan,
                expected_adapter_identity=plan.adapter_identity,
                allow_repairable_tail=False,
            ).finalization
            if loaded is None:
                raise RecognitionRunIntegrityError(
                    "Recognition terminal pointer repair did not become durable"
                )
            return loaded

    def load_finalization(
        self,
        *,
        plan: RecognitionRunPlanV1,
    ) -> RecognitionRunFinalizationV1 | None:
        return self._load(
            plan.run_key,
            expected_plan=plan,
            expected_adapter_identity=plan.adapter_identity,
            allow_repairable_tail=False,
        ).finalization


__all__ = [
    "ChunkExecutionReceiptV1",
    "RecognitionAdapterIdentitySnapshotV1",
    "RecognitionAudioBindingV1",
    "RecognitionChunkPlanV1",
    "RecognitionChunkPlannerV1",
    "RecognitionChunkReplayV1",
    "RecognitionContentDigestV1",
    "RecognitionDerivedChunkV1",
    "RecognitionRequestBindingV1",
    "RecognitionRunCheckpointV1",
    "RecognitionRunConflictError",
    "RecognitionRunFailureCode",
    "RecognitionRunFailureV1",
    "RecognitionRunFinalizationV1",
    "RecognitionRunHeadV1",
    "RecognitionRunIntegrityError",
    "RecognitionRunNotFoundError",
    "RecognitionRunPlanV1",
    "RecognitionRunRepository",
    "StoredRecognitionRunV1",
    "build_chunk_execution_receipt",
    "build_recognition_adapter_identity_snapshot",
    "build_recognition_audio_binding",
    "build_recognition_chunk_plan",
    "build_recognition_chunk_planner",
    "build_recognition_derived_chunk",
    "build_recognition_request_binding",
    "build_recognition_run_checkpoint",
    "build_recognition_run_failure",
    "build_recognition_run_finalization",
    "build_recognition_run_head",
    "build_recognition_run_plan",
    "digest_recognition_content",
    "digest_recognition_file",
    "derive_recognition_pcm_wav_chunk",
    "rebuild_recognition_adapter_identity_snapshot",
    "rebuild_recognition_audio_binding",
    "rebuild_recognition_chunk_plan",
    "rebuild_recognition_chunk_planner",
    "rebuild_recognition_request_binding",
    "rebuild_recognition_run_plan",
    "recognition_run_key",
    "snapshot_recognition_model_identity",
]
