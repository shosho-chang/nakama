"""Offline, replayable Faster-Whisper Recognition Adapter.

The Adapter seals the provider's complete segment/word observation before it
derives positive-duration Recognition Evidence.  It never injects lexical
prompts, downloads model files, silently enables VAD, or invents timestamps for
provider observations whose duration is zero.
"""

from __future__ import annotations

import importlib.metadata
import math
import platform
import tempfile
import unicodedata
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

from shared.schemas.podcast_subtitles_v2 import ArtifactDigest, RecognitionEvidence

from ..hashing import (
    canonical_json_bytes,
    hash_file,
    hash_object,
    sha256_bytes,
)
from ..ports import (
    AdapterInputError,
    AdapterIntegrityError,
    AdapterUnavailableError,
    RecognitionModelIdentity,
    RecognitionRequest,
)
from ..recognition_run import (
    RecognitionAudioBindingV1,
    RecognitionRunFinalizationV1,
    RecognitionRunIntegrityError,
    RecognitionRunPlanV1,
    RecognitionRunRepository,
    build_recognition_audio_binding,
    build_recognition_chunk_plan,
    build_recognition_chunk_planner,
    build_recognition_request_binding,
    build_recognition_run_plan,
    derive_recognition_pcm_wav_chunk,
    snapshot_recognition_model_identity,
)
from .recognition import (
    _evidence_from_payload,
    _file_uri_path_for_adapter,
    _normalized_audio_hash,
    _persist_raw_output,
)
from .snapshot_identity import SnapshotIdentityError, measure_huggingface_snapshot
from .timing_grouping import (
    FASTER_WHISPER_GROUPING_POLICY,
    TimedObservation,
    TimedObservationGroupingReceipt,
    build_timed_observation_grouping,
)

FasterWhisperRunner = Callable[[Path, RecognitionRequest], Mapping[str, object]]
_HEX_DIGITS = frozenset("0123456789abcdef")
_PROVIDER_TIMESTAMP_QUANTUM_SECONDS = 0.02
_FLOAT_COMPARISON_EPSILON_SECONDS = 1e-9


class _EnvelopeContract(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)


class _ProviderWord(_EnvelopeContract):
    word: str = Field(min_length=1)
    start: float = Field(ge=0, allow_inf_nan=False)
    end: float = Field(ge=0, allow_inf_nan=False)
    probability: float = Field(ge=0, le=1, allow_inf_nan=False)

    @model_validator(mode="after")
    def _nonnegative_duration(self) -> _ProviderWord:
        if self.end < self.start:
            raise ValueError("Faster-Whisper word has negative duration")
        return self


class _ProviderSegment(_EnvelopeContract):
    id: int = Field(ge=0)
    seek: int = Field(ge=0)
    start: float = Field(ge=0, allow_inf_nan=False)
    end: float = Field(ge=0, allow_inf_nan=False)
    text: str = Field(min_length=1)
    tokens: tuple[int, ...]
    avg_logprob: float = Field(allow_inf_nan=False)
    compression_ratio: float = Field(ge=0, allow_inf_nan=False)
    no_speech_prob: float = Field(ge=0, le=1, allow_inf_nan=False)
    temperature: float = Field(ge=0, allow_inf_nan=False)
    words: tuple[_ProviderWord, ...] = Field(min_length=1)

    @model_validator(mode="after")
    def _positive_duration(self) -> _ProviderSegment:
        if self.end <= self.start:
            raise ValueError("Faster-Whisper segment requires positive duration")
        return self


class _ProviderObservation(_EnvelopeContract):
    language: str = Field(min_length=1)
    language_probability: float = Field(ge=0, le=1, allow_inf_nan=False)
    duration: float = Field(gt=0, allow_inf_nan=False)
    duration_after_vad: float = Field(ge=0, allow_inf_nan=False)
    segments: tuple[_ProviderSegment, ...]

    @model_validator(mode="after")
    def _ordered_complete_segments(self) -> _ProviderObservation:
        segment_ids = tuple(segment.id for segment in self.segments)
        if len(set(segment_ids)) != len(segment_ids) or segment_ids != tuple(sorted(segment_ids)):
            raise ValueError("Faster-Whisper provider segment IDs must be unique and ordered")
        previous_segment_end = 0.0
        previous_word_end = 0.0
        for segment in self.segments:
            if segment.start < previous_segment_end:
                raise ValueError("Faster-Whisper segments overlap or move backwards")
            previous_segment_end = segment.end
            if "".join(word.word for word in segment.words) != segment.text:
                raise ValueError(
                    "Faster-Whisper segment text must equal its exact word observation text"
                )
            for word_index, word in enumerate(segment.words):
                if word.start < previous_word_end:
                    raise ValueError("Faster-Whisper words overlap or move backwards")
                # Faster-Whisper quantizes word timestamps to 20 ms.  Its
                # decoded segment boundary can therefore differ from the first
                # or last word by one provider quantum.  Preserve both raw
                # observations, but reject larger topology disagreements.
                tolerance = (
                    _PROVIDER_TIMESTAMP_QUANTUM_SECONDS
                    + _FLOAT_COMPARISON_EPSILON_SECONDS
                )
                starts_too_early = segment.start - word.start > tolerance
                first_word_crosses_segment_start = (
                    word_index == 0 and word.end + tolerance >= segment.start
                )
                if word.end > word.start and (
                    (starts_too_early and not first_word_crosses_segment_start)
                    or word.end - segment.end > tolerance
                ):
                    raise ValueError(
                        "Faster-Whisper word exceeds its provider segment: "
                        f"segment_id={segment.id}, "
                        f"segment=[{segment.start}, {segment.end}], "
                        f"word={word.word!r}, word_interval=[{word.start}, {word.end}], "
                        f"early_delta={max(0.0, segment.start - word.start)}, "
                        f"late_delta={max(0.0, word.end - segment.end)}"
                    )
                previous_word_end = word.end
        return self


class _ChunkWord(_EnvelopeContract):
    index: int = Field(ge=0)
    word: str = Field(min_length=1)
    local_start_seconds: float = Field(ge=0, allow_inf_nan=False)
    local_end_seconds: float = Field(gt=0, allow_inf_nan=False)
    local_start_sample: int = Field(ge=0)
    local_end_sample: int = Field(gt=0)
    global_start_sample: int = Field(ge=0)
    global_end_sample: int = Field(gt=0)
    probability: float = Field(ge=0, le=1, allow_inf_nan=False)
    source_observation_indices: tuple[int, ...] = Field(min_length=1)
    timing_basis: Literal[
        "provider_word_timestamp_exact",
        "provider_word_timestamp_bounded_group",
    ]

    @model_validator(mode="after")
    def _positive_duration(self) -> _ChunkWord:
        if (
            self.local_end_seconds <= self.local_start_seconds
            or self.local_end_sample <= self.local_start_sample
            or self.global_end_sample <= self.global_start_sample
            or self.global_end_sample - self.global_start_sample
            != self.local_end_sample - self.local_start_sample
        ):
            raise ValueError("Faster-Whisper chunk word requires coherent positive duration")
        return self


class _AudioBinding(_EnvelopeContract):
    sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    size_bytes: int = Field(gt=0)
    sample_rate_hz: int = Field(gt=0)
    channels: int = Field(gt=0)
    sample_width_bytes: int = Field(gt=0)
    frame_count: int = Field(gt=0)
    compression_type: Literal["NONE"]


class _PlannerBinding(_EnvelopeContract):
    schema_version: Literal[1] = 1
    derivation: Literal["pcm_wav_frame_slice_v1"]
    ownership: Literal["word_midpoint_in_half_open_owned_interval_v1"]
    seam_comparison: Literal["normalized_word_text_in_shared_context_v1"]
    owned_chunk_samples: int = Field(gt=0)
    seam_context_samples: int = Field(gt=0)


class _ChunkReceipt(_EnvelopeContract):
    id: str = Field(min_length=1)
    index: int = Field(ge=0)
    count: int = Field(gt=0)
    inference_start_sample: int = Field(ge=0)
    inference_end_sample: int = Field(gt=0)
    owned_start_sample: int = Field(ge=0)
    owned_end_sample: int = Field(gt=0)
    derived_audio_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    derived_audio_size_bytes: int = Field(gt=0)
    provider_output: _ProviderObservation
    timestamp_grouping: TimedObservationGroupingReceipt | None
    grouped_words: tuple[_ChunkWord, ...]

    @model_validator(mode="after")
    def _closed_chunk_topology(self) -> _ChunkReceipt:
        if not (
            self.inference_start_sample
            <= self.owned_start_sample
            < self.owned_end_sample
            <= self.inference_end_sample
        ):
            raise ValueError("Faster-Whisper chunk ownership exceeds inference bounds")
        if bool(self.grouped_words) != (self.timestamp_grouping is not None):
            raise ValueError("Faster-Whisper chunk grouping and words must be present together")
        return self


class _SeamReceipt(_EnvelopeContract):
    id: str = Field(min_length=1)
    seam_sample: int = Field(gt=0)
    left_chunk_id: str
    right_chunk_id: str
    comparison_start_sample: int = Field(ge=0)
    comparison_end_sample: int = Field(gt=0)
    left_text: str
    right_text: str
    left_normalized: str
    right_normalized: str
    status: Literal["matched", "conflict"]


class _MergedWord(_EnvelopeContract):
    index: int = Field(ge=0)
    word: str = Field(min_length=1)
    start: float = Field(ge=0, allow_inf_nan=False)
    end: float = Field(gt=0, allow_inf_nan=False)
    probability: float = Field(ge=0, le=1, allow_inf_nan=False)
    source_chunk_id: str
    source_word_index: int = Field(ge=0)
    ownership: Literal["word_midpoint_in_half_open_owned_interval_v1"]

    @model_validator(mode="after")
    def _positive_duration(self) -> _MergedWord:
        if self.end <= self.start:
            raise ValueError("Faster-Whisper merged word requires positive duration")
        return self


class _FasterWhisperEnvelope(_EnvelopeContract):
    schema_version: Literal[2]
    kind: Literal["faster_whisper_bounded_overlap_evidence"]
    identity_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    requested_language: str
    effective_request_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    normalized_audio: _AudioBinding
    language: str = Field(min_length=1)
    planner: _PlannerBinding
    chunks: tuple[_ChunkReceipt, ...] = Field(min_length=1)
    seams: tuple[_SeamReceipt, ...]
    merged_words: tuple[_MergedWord, ...] = Field(min_length=1)

    @model_validator(mode="after")
    def _complete_bounded_partition(self) -> _FasterWhisperEnvelope:
        count = len(self.chunks)
        if tuple(chunk.index for chunk in self.chunks) != tuple(range(count)) or any(
            chunk.count != count for chunk in self.chunks
        ):
            raise ValueError("Faster-Whisper bounded chunks must be ordered and complete")
        if len(self.seams) != count - 1:
            raise ValueError("Faster-Whisper seam count differs from bounded chunk count")
        if any(seam.status != "matched" for seam in self.seams):
            raise ValueError("Faster-Whisper bounded seam hypotheses conflict")
        return self


@dataclass(frozen=True, slots=True)
class _FasterChunkPlan:
    index: int
    count: int
    inference_start_sample: int
    inference_end_sample: int
    owned_start_sample: int
    owned_end_sample: int

    @property
    def id(self) -> str:
        return (
            f"faster-whisper-chunk-{self.index:04d}-"
            f"{self.inference_start_sample}-{self.inference_end_sample}-"
            f"own-{self.owned_start_sample}-{self.owned_end_sample}"
        )


def _flatten_provider_words(
    observation: _ProviderObservation,
) -> tuple[_ProviderWord, ...]:
    return tuple(word for segment in observation.segments for word in segment.words)


def _effective_request(*, language_hint: str | None) -> dict[str, object]:
    normalized = language_hint.strip().casefold().replace("_", "-") if language_hint else None
    if normalized not in {"zh", "zh-cn", "zh-tw", "zh-hant", "zh-hant-tw"}:
        raise AdapterInputError(
            "Faster-Whisper production request requires an explicit Chinese hint"
        )
    return {
        "language": "zh",
        "task": "transcribe",
        "log_progress": False,
        "beam_size": 5,
        "best_of": 5,
        "patience": 1.0,
        "length_penalty": 1.0,
        "repetition_penalty": 1.0,
        "no_repeat_ngram_size": 0,
        "temperature": 0.0,
        "compression_ratio_threshold": 2.4,
        "log_prob_threshold": -1.0,
        "no_speech_threshold": 0.6,
        "condition_on_previous_text": False,
        "prompt_reset_on_temperature": 0.5,
        "initial_prompt": None,
        "prefix": None,
        "suppress_blank": True,
        "suppress_tokens": [-1],
        "without_timestamps": False,
        "max_initial_timestamp": 1.0,
        "word_timestamps": True,
        "multilingual": False,
        "vad_filter": False,
        "vad_parameters": None,
        "max_new_tokens": None,
        "chunk_length": 30,
        "clip_timestamps": "0",
        "hallucination_silence_threshold": None,
        "hotwords": None,
        "language_detection_threshold": 0.5,
        "language_detection_segments": 1,
    }


class FasterWhisperRecognizerAdapter:
    """Produce an immutable independent Recognition hypothesis offline."""

    ADAPTER_NAME = "faster-whisper-word-timestamps"
    ADAPTER_VERSION = 2
    ENVELOPE_SCHEMA_VERSION = 2
    RECOGNITION_RUN_NAMESPACE = "podcast-subtitle-v2/faster-whisper-large-v3-envelope/v2"

    def __init__(
        self,
        *,
        model: str = "Systran/faster-whisper-large-v3",
        model_revision: str,
        device: str = "cuda",
        device_index: int = 0,
        compute_type: str = "float16",
        cpu_threads: int = 0,
        num_workers: int = 1,
        local_files_only: bool = True,
        owned_chunk_seconds: int = 174,
        seam_context_ms: int = 3_000,
        runner: FasterWhisperRunner | None = None,
        runner_runtime_components: Mapping[str, str] | None = None,
        runner_code_hash: str | None = None,
        runner_execution_mode: Literal["fixture", "local", "other"] | None = None,
        recognition_run_repository: RecognitionRunRepository | None = None,
        logical_namespace: str | None = None,
    ) -> None:
        if not model or model != model.strip() or not model_revision:
            raise ValueError("Faster-Whisper requires explicit model and revision")
        if device not in {"cuda", "cpu"} or device_index < 0:
            raise ValueError("Faster-Whisper device configuration is invalid")
        if cpu_threads < 0 or num_workers < 1:
            raise ValueError("Faster-Whisper worker configuration is invalid")
        if owned_chunk_seconds < 1 or seam_context_ms < 1:
            raise ValueError("Faster-Whisper chunk and seam context must be positive")
        if owned_chunk_seconds * 1_000 + 2 * seam_context_ms > 180_000:
            raise ValueError("Faster-Whisper bounded inference windows may not exceed 180 seconds")
        custom_identity_fields = (
            runner_runtime_components,
            runner_code_hash,
            runner_execution_mode,
        )
        if runner is None and any(value is not None for value in custom_identity_fields):
            raise ValueError("runner identity fields require a custom Faster-Whisper runner")
        if runner is not None and any(value is None for value in custom_identity_fields):
            raise ValueError(
                "custom Faster-Whisper runner requires explicit runtime, code, and "
                "execution identity"
            )
        if (recognition_run_repository is None) != (logical_namespace is None):
            raise ValueError(
                "recognition_run_repository and logical_namespace must be provided together"
            )
        if logical_namespace is not None and (
            not logical_namespace.strip() or logical_namespace != logical_namespace.strip()
        ):
            raise ValueError("Faster-Whisper logical_namespace must be non-blank and trimmed")
        self._model = model
        self._model_revision = model_revision
        self._device = device
        self._device_index = device_index
        self._compute_type = compute_type
        self._cpu_threads = cpu_threads
        self._num_workers = num_workers
        self._local_files_only = local_files_only
        self._owned_chunk_seconds = owned_chunk_seconds
        self._seam_context_ms = seam_context_ms
        self._runner = runner
        self._recognition_run_repository = recognition_run_repository
        self._logical_namespace = logical_namespace
        self._source_hash = hash_file(Path(__file__))
        self._dependency_source_hashes = {
            "evidence_helpers_source_hash": hash_file(Path(__file__).with_name("recognition.py")),
            "hashing_source_hash": hash_file(Path(__file__).parents[1] / "hashing.py"),
            "snapshot_identity_source_hash": hash_file(
                Path(__file__).with_name("snapshot_identity.py")
            ),
            "timing_grouping_source_hash": hash_file(
                Path(__file__).with_name("timing_grouping.py")
            ),
        }
        self._adapter_code_hash = hash_object(
            {
                "adapter_source_hash": self._source_hash,
                **self._dependency_source_hashes,
            }
        )
        self._identity_cache: RecognitionModelIdentity | None = None
        self._model_snapshot_path: Path | None = None
        self._model_snapshot_inventory_hash: str | None = None
        if runner is not None:
            assert runner_runtime_components is not None
            assert runner_code_hash is not None
            assert runner_execution_mode is not None
            self._identity_cache = self._make_identity(
                runtime_components=tuple(sorted(runner_runtime_components.items())),
                code_hash=hash_object(
                    {
                        "adapter_code_hash": self._adapter_code_hash,
                        "custom_runner_code_hash": runner_code_hash,
                    }
                ),
                execution_mode=runner_execution_mode,
            )

    @property
    def adapter_name(self) -> str:
        return self.ADAPTER_NAME

    @property
    def adapter_version(self) -> int:
        return self.ADAPTER_VERSION

    def _config(self) -> dict[str, object]:
        return {
            "adapter": self.ADAPTER_NAME,
            "adapter_version": self.ADAPTER_VERSION,
            "adapter_code_hash": self._adapter_code_hash,
            "model": self._model,
            "model_revision": self._model_revision,
            "device": self._device,
            "device_index": self._device_index,
            "compute_type": self._compute_type,
            "cpu_threads": self._cpu_threads,
            "num_workers": self._num_workers,
            "local_files_only": self._local_files_only,
            "owned_chunk_seconds": self._owned_chunk_seconds,
            "seam_context_ms": self._seam_context_ms,
            "chunk_derivation": "pcm_wav_frame_slice_v1",
            "ownership": "word_midpoint_in_half_open_owned_interval_v1",
            "seam_comparison": "normalized_word_text_in_shared_context_v1",
            "timestamp_grouping": FASTER_WHISPER_GROUPING_POLICY.method,
            "effective_request": _effective_request(language_hint="zh"),
            "envelope_schema_version": self.ENVELOPE_SCHEMA_VERSION,
        }

    @property
    def adapter_config_hash(self) -> str:
        return hash_object(self._config())

    def _make_identity(
        self,
        *,
        runtime_components: tuple[tuple[str, str], ...],
        code_hash: str,
        execution_mode: Literal["fixture", "local", "other"],
    ) -> RecognitionModelIdentity:
        return RecognitionModelIdentity(
            adapter_name=self.ADAPTER_NAME,
            adapter_version=str(self.ADAPTER_VERSION),
            model=self._model,
            model_version=self._model_revision,
            aligner="faster-whisper-integrated-word-timestamps",
            aligner_version=self._model_revision,
            runtime_components=runtime_components,
            runtime_hash=hash_object({"runtime_components": runtime_components}),
            adapter_code_hash=code_hash,
            config_hash=self.adapter_config_hash,
            execution_mode=execution_mode,
        )

    @property
    def identity(self) -> RecognitionModelIdentity:
        self._assert_source_identity()
        if self._identity_cache is None:
            self._identity_cache = self._measure_production_identity()
        return self._identity_cache

    def _assert_source_identity(self) -> None:
        current = {
            "evidence_helpers_source_hash": hash_file(Path(__file__).with_name("recognition.py")),
            "hashing_source_hash": hash_file(Path(__file__).parents[1] / "hashing.py"),
            "snapshot_identity_source_hash": hash_file(
                Path(__file__).with_name("snapshot_identity.py")
            ),
            "timing_grouping_source_hash": hash_file(
                Path(__file__).with_name("timing_grouping.py")
            ),
        }
        if (
            hash_file(Path(__file__)) != self._source_hash
            or current != self._dependency_source_hashes
        ):
            raise AdapterIntegrityError(
                "Faster-Whisper Adapter or an identity-critical dependency changed "
                "after initialization"
            )

    @staticmethod
    def _snapshot_inventory(snapshot: Path) -> tuple[tuple[str, int, str], ...]:
        """Bind real target bytes and reject snapshot links outside this repo cache."""
        try:
            _, inventory, _ = measure_huggingface_snapshot(snapshot)
        except SnapshotIdentityError as exc:
            raise AdapterUnavailableError(f"Faster-Whisper {exc}") from exc
        return inventory

    def _measure_production_identity(self) -> RecognitionModelIdentity:
        if not self._local_files_only:
            raise AdapterUnavailableError(
                "Faster-Whisper production Evidence requires local_files_only=True"
            )
        if len(self._model_revision) != 40 or any(
            character not in _HEX_DIGITS for character in self._model_revision
        ):
            raise AdapterUnavailableError(
                "Faster-Whisper production revision must be an immutable commit SHA"
            )
        try:
            import ctranslate2
            from huggingface_hub import snapshot_download
        except ImportError as exc:  # pragma: no cover - optional production dependency
            raise AdapterUnavailableError(
                "FasterWhisperRecognizerAdapter requires faster-whisper and CTranslate2"
            ) from exc
        try:
            snapshot = Path(
                snapshot_download(
                    repo_id=self._model,
                    revision=self._model_revision,
                    local_files_only=True,
                )
            )
        except (OSError, ValueError) as exc:
            raise AdapterUnavailableError(
                f"Faster-Whisper pinned local snapshot is unavailable: "
                f"{self._model}@{self._model_revision}"
            ) from exc
        resolved_revision = (
            snapshot.name if snapshot.parent.name == "snapshots" else self._model_revision
        )
        if resolved_revision != self._model_revision:
            raise AdapterUnavailableError(
                "Faster-Whisper local snapshot resolved to an unexpected revision"
            )
        required_files = {
            "config.json",
            "model.bin",
            "preprocessor_config.json",
            "tokenizer.json",
            "vocabulary.json",
        }
        if not snapshot.is_dir() or any(
            not (snapshot / name).is_file() or (snapshot / name).stat().st_size <= 0
            for name in required_files
        ):
            raise AdapterUnavailableError("Faster-Whisper pinned local snapshot is incomplete")
        try:
            resolved_snapshot, inventory, inventory_hash = measure_huggingface_snapshot(snapshot)
        except SnapshotIdentityError as exc:
            raise AdapterUnavailableError(f"Faster-Whisper {exc}") from exc
        components = {
            "ctranslate2": importlib.metadata.version("ctranslate2"),
            "device_request": self._device,
            "faster_whisper": importlib.metadata.version("faster-whisper"),
            "model_snapshot_inventory": inventory_hash,
            "model_snapshot_revision": resolved_revision,
            "platform": platform.platform(),
            "python_implementation": platform.python_implementation(),
            "python_version": platform.python_version(),
        }
        if self._device == "cuda":
            try:
                components["cuda_device_count"] = str(ctranslate2.get_cuda_device_count())
            except RuntimeError as exc:
                raise AdapterUnavailableError(
                    "Faster-Whisper cannot measure the configured CUDA runtime"
                ) from exc
            if int(components["cuda_device_count"]) <= self._device_index:
                raise AdapterUnavailableError(
                    f"Faster-Whisper configured CUDA device {self._device_index} is unavailable"
                )
        self._model_snapshot_path = resolved_snapshot
        self._model_snapshot_inventory_hash = inventory_hash
        return self._make_identity(
            runtime_components=tuple(sorted(components.items())),
            code_hash=self._adapter_code_hash,
            execution_mode="local",
        )

    @staticmethod
    def _typed_provider_observation(payload: Mapping[str, object]) -> _ProviderObservation:
        try:
            return _ProviderObservation.model_validate_json(canonical_json_bytes(payload))
        except ValidationError as exc:
            raise AdapterInputError(f"invalid Faster-Whisper provider output: {exc}") from exc

    @staticmethod
    def _transcribe_with_model(
        model: object,
        audio_path: Path,
        request: RecognitionRequest,
    ) -> dict[str, object]:
        """Materialize the provider generator without changing any observation."""

        effective = _effective_request(language_hint=request.language_hint)
        segments_iter, info = model.transcribe(  # type: ignore[attr-defined]
            str(audio_path),
            **effective,
        )
        segments: list[dict[str, object]] = []
        for segment_index, segment in enumerate(segments_iter):
            provider_words = getattr(segment, "words", None)
            if not provider_words:
                raise AdapterInputError(
                    f"Faster-Whisper segments[{segment_index}] has no word timestamps"
                )
            segments.append(
                {
                    "id": getattr(segment, "id", None),
                    "seek": getattr(segment, "seek", None),
                    "start": getattr(segment, "start", None),
                    "end": getattr(segment, "end", None),
                    "text": getattr(segment, "text", None),
                    "tokens": tuple(getattr(segment, "tokens", ())),
                    "avg_logprob": getattr(segment, "avg_logprob", None),
                    "compression_ratio": getattr(segment, "compression_ratio", None),
                    "no_speech_prob": getattr(segment, "no_speech_prob", None),
                    "temperature": getattr(segment, "temperature", None),
                    "words": [
                        {
                            "word": getattr(word, "word", None),
                            "start": getattr(word, "start", None),
                            "end": getattr(word, "end", None),
                            "probability": getattr(word, "probability", None),
                        }
                        for word in provider_words
                    ],
                }
            )
        return {
            "language": getattr(info, "language", None),
            "language_probability": getattr(info, "language_probability", None),
            "duration": getattr(info, "duration", None),
            "duration_after_vad": getattr(info, "duration_after_vad", None),
            "segments": segments,
        }

    def _plan_chunks(
        self,
        audio: RecognitionAudioBindingV1,
    ) -> tuple[_FasterChunkPlan, ...]:
        owned_samples = self._owned_chunk_seconds * audio.sample_rate_hz
        context_samples = round(self._seam_context_ms * audio.sample_rate_hz / 1_000)
        count = math.ceil(audio.frame_count / owned_samples)
        return tuple(
            _FasterChunkPlan(
                index=index,
                count=count,
                inference_start_sample=max(0, index * owned_samples - context_samples),
                inference_end_sample=min(
                    audio.frame_count,
                    min(audio.frame_count, (index + 1) * owned_samples) + context_samples,
                ),
                owned_start_sample=index * owned_samples,
                owned_end_sample=min(audio.frame_count, (index + 1) * owned_samples),
            )
            for index in range(count)
        )

    @staticmethod
    def _iter_chunk_bytes(
        audio_path: Path,
        *,
        audio: RecognitionAudioBindingV1,
        plans: Sequence[_FasterChunkPlan],
    ) -> Iterable[bytes]:
        for plan in plans:
            yield derive_recognition_pcm_wav_chunk(
                audio_path,
                audio=audio,
                start_sample=plan.inference_start_sample,
                end_sample=plan.inference_end_sample,
            )

    @staticmethod
    def _seam_text(value: str) -> str:
        normalized = unicodedata.normalize("NFKC", value).casefold()
        return "".join(
            character
            for character in normalized
            if not unicodedata.category(character).startswith(("P", "Z", "C"))
        )

    def _chunk_receipt(
        self,
        *,
        plan: _FasterChunkPlan,
        audio: RecognitionAudioBindingV1,
        chunk_bytes: bytes,
        provider_output: Mapping[str, object],
    ) -> _ChunkReceipt:
        observation = self._typed_provider_observation(provider_output)
        words = _flatten_provider_words(observation)
        grouping = (
            build_timed_observation_grouping(
                tuple(
                    TimedObservation(
                        index=index,
                        text=word.word,
                        start_seconds=word.start,
                        end_seconds=word.end,
                    )
                    for index, word in enumerate(words)
                ),
                policy=FASTER_WHISPER_GROUPING_POLICY,
            )
            if words
            else None
        )
        chunk_frame_count = plan.inference_end_sample - plan.inference_start_sample
        grouped_words: list[_ChunkWord] = []
        if grouping is not None:
            for group in grouping.groups:
                local_start = round(group.start_seconds * audio.sample_rate_hz)
                local_end = round(group.end_seconds * audio.sample_rate_hz)
                if not 0 <= local_start < local_end <= chunk_frame_count:
                    raise AdapterIntegrityError(
                        "Faster-Whisper grouped word exceeds its exact bounded chunk"
                    )
                grouped_words.append(
                    _ChunkWord(
                        index=group.index,
                        word=group.text,
                        local_start_seconds=group.start_seconds,
                        local_end_seconds=group.end_seconds,
                        local_start_sample=local_start,
                        local_end_sample=local_end,
                        global_start_sample=plan.inference_start_sample + local_start,
                        global_end_sample=plan.inference_start_sample + local_end,
                        probability=min(
                            words[index].probability for index in group.source_observation_indices
                        ),
                        source_observation_indices=group.source_observation_indices,
                        timing_basis=group.timing_basis,
                    )
                )
        return _ChunkReceipt(
            id=plan.id,
            index=plan.index,
            count=plan.count,
            inference_start_sample=plan.inference_start_sample,
            inference_end_sample=plan.inference_end_sample,
            owned_start_sample=plan.owned_start_sample,
            owned_end_sample=plan.owned_end_sample,
            derived_audio_sha256=sha256_bytes(chunk_bytes),
            derived_audio_size_bytes=len(chunk_bytes),
            provider_output=observation,
            timestamp_grouping=grouping,
            grouped_words=tuple(grouped_words),
        )

    @staticmethod
    def _words_in_range(
        chunk: _ChunkReceipt,
        *,
        start_sample: int,
        end_sample: int,
    ) -> tuple[_ChunkWord, ...]:
        return tuple(
            word
            for word in chunk.grouped_words
            if start_sample <= (word.global_start_sample + word.global_end_sample) // 2 < end_sample
        )

    def _build_envelope(
        self,
        *,
        request: RecognitionRequest,
        audio: RecognitionAudioBindingV1,
        plans: tuple[_FasterChunkPlan, ...],
        provider_outputs: Sequence[Mapping[str, object]],
        chunk_bytes_set: Iterable[bytes],
    ) -> _FasterWhisperEnvelope:
        if len(provider_outputs) != len(plans):
            raise AdapterIntegrityError(
                "Faster-Whisper bounded execution returned an incomplete chunk set"
            )
        chunk_iterator = iter(chunk_bytes_set)
        chunks: list[_ChunkReceipt] = []
        try:
            for plan, provider_output in zip(plans, provider_outputs):
                chunk_bytes = next(chunk_iterator)
                chunks.append(
                    self._chunk_receipt(
                        plan=plan,
                        audio=audio,
                        chunk_bytes=chunk_bytes,
                        provider_output=provider_output,
                    )
                )
                del chunk_bytes
        except StopIteration as exc:
            raise AdapterIntegrityError(
                "Faster-Whisper bounded execution lacks a derived chunk"
            ) from exc
        sentinel = object()
        if next(chunk_iterator, sentinel) is not sentinel:
            raise AdapterIntegrityError(
                "Faster-Whisper bounded execution has excess derived chunks"
            )
        languages = {chunk.provider_output.language for chunk in chunks}
        if len(languages) != 1:
            raise AdapterIntegrityError(
                "Faster-Whisper bounded chunks disagree on provider language"
            )

        seams: list[_SeamReceipt] = []
        for left, right in zip(chunks, chunks[1:]):
            comparison_start = max(
                left.inference_start_sample,
                right.inference_start_sample,
            )
            comparison_end = min(
                left.inference_end_sample,
                right.inference_end_sample,
            )
            left_text = "".join(
                word.word
                for word in self._words_in_range(
                    left,
                    start_sample=comparison_start,
                    end_sample=comparison_end,
                )
            )
            right_text = "".join(
                word.word
                for word in self._words_in_range(
                    right,
                    start_sample=comparison_start,
                    end_sample=comparison_end,
                )
            )
            left_normalized = self._seam_text(left_text)
            right_normalized = self._seam_text(right_text)
            seams.append(
                _SeamReceipt(
                    id=f"faster-whisper-seam-{left.owned_end_sample}",
                    seam_sample=left.owned_end_sample,
                    left_chunk_id=left.id,
                    right_chunk_id=right.id,
                    comparison_start_sample=comparison_start,
                    comparison_end_sample=comparison_end,
                    left_text=left_text,
                    right_text=right_text,
                    left_normalized=left_normalized,
                    right_normalized=right_normalized,
                    status=("matched" if left_normalized == right_normalized else "conflict"),
                )
            )

        merged: list[_MergedWord] = []
        for chunk in chunks:
            for word in self._words_in_range(
                chunk,
                start_sample=chunk.owned_start_sample,
                end_sample=chunk.owned_end_sample,
            ):
                merged.append(
                    _MergedWord(
                        index=len(merged),
                        word=word.word,
                        start=word.global_start_sample / audio.sample_rate_hz,
                        end=word.global_end_sample / audio.sample_rate_hz,
                        probability=word.probability,
                        source_chunk_id=chunk.id,
                        source_word_index=word.index,
                        ownership="word_midpoint_in_half_open_owned_interval_v1",
                    )
                )
        if not merged:
            raise AdapterIntegrityError(
                "Faster-Whisper bounded ownership produced no Recognition words"
            )
        if any(right.start < left.end for left, right in zip(merged, merged[1:])):
            raise AdapterIntegrityError(
                "Faster-Whisper bounded ownership produced overlapping words"
            )
        return _FasterWhisperEnvelope(
            schema_version=2,
            kind="faster_whisper_bounded_overlap_evidence",
            identity_hash=self.identity.content_hash,
            requested_language=request.language_hint or "",
            effective_request_hash=hash_object(
                _effective_request(language_hint=request.language_hint)
            ),
            normalized_audio=_AudioBinding(
                sha256=audio.normalized_audio.sha256,
                size_bytes=audio.normalized_audio.size_bytes,
                sample_rate_hz=audio.sample_rate_hz,
                channels=audio.channels,
                sample_width_bytes=audio.sample_width_bytes,
                frame_count=audio.frame_count,
                compression_type="NONE",
            ),
            language=next(iter(languages)),
            planner=_PlannerBinding(
                derivation="pcm_wav_frame_slice_v1",
                ownership="word_midpoint_in_half_open_owned_interval_v1",
                seam_comparison="normalized_word_text_in_shared_context_v1",
                owned_chunk_samples=self._owned_chunk_seconds * audio.sample_rate_hz,
                seam_context_samples=round(self._seam_context_ms * audio.sample_rate_hz / 1_000),
            ),
            chunks=tuple(chunks),
            seams=tuple(seams),
            merged_words=tuple(merged),
        )

    @staticmethod
    def _parse_envelope(raw_output: bytes) -> _FasterWhisperEnvelope:
        try:
            return _FasterWhisperEnvelope.model_validate_json(raw_output)
        except (UnicodeDecodeError, ValidationError, ValueError) as exc:
            raise AdapterInputError(f"invalid Faster-Whisper Evidence envelope: {exc}") from exc

    def _validate_envelope(
        self,
        envelope: _FasterWhisperEnvelope,
        *,
        request: RecognitionRequest,
    ) -> None:
        audio_path = Path(request.normalized_audio)
        if (
            envelope.identity_hash != self.identity.content_hash
            or envelope.requested_language != (request.language_hint or "")
            or envelope.effective_request_hash
            != hash_object(_effective_request(language_hint=request.language_hint))
        ):
            raise AdapterIntegrityError("Faster-Whisper request or executable identity mismatch")
        if (
            envelope.normalized_audio.sha256 != _normalized_audio_hash(request)
            or envelope.normalized_audio.size_bytes != audio_path.stat().st_size
        ):
            raise AdapterIntegrityError("Faster-Whisper normalized audio binding mismatch")
        audio = build_recognition_audio_binding(audio_path)
        plans = self._plan_chunks(audio)
        expected = self._build_envelope(
            request=request,
            audio=audio,
            plans=plans,
            provider_outputs=tuple(
                chunk.provider_output.model_dump(mode="json") for chunk in envelope.chunks
            ),
            chunk_bytes_set=self._iter_chunk_bytes(
                audio_path,
                audio=audio,
                plans=plans,
            ),
        )
        if envelope != expected:
            raise AdapterIntegrityError("Faster-Whisper envelope derivation does not replay")

    def _evidence_from_envelope(
        self,
        envelope: _FasterWhisperEnvelope,
        *,
        request: RecognitionRequest,
        raw_output: ArtifactDigest,
    ) -> RecognitionEvidence:
        payload = {
            "language": envelope.language,
            "words": [
                {
                    "word": word.word,
                    "start": word.start,
                    "end": word.end,
                    "probability": word.probability,
                }
                for word in envelope.merged_words
            ],
        }
        return _evidence_from_payload(
            payload,
            request=request,
            raw_output=raw_output,
            adapter=self.ADAPTER_NAME,
            model=f"{self._model}@{self._model_revision}",
            config=self._config(),
            timestamp_unit="seconds",
            expected_normalized_audio_size_bytes=envelope.normalized_audio.size_bytes,
            expected_normalized_audio_hash=envelope.normalized_audio.sha256,
        )

    def _build_recognition_run_plan(
        self,
        *,
        request: RecognitionRequest,
    ) -> tuple[RecognitionRunPlanV1, RecognitionAudioBindingV1, tuple[_FasterChunkPlan, ...]]:
        logical_namespace = self._logical_namespace
        if logical_namespace is None:
            raise AdapterIntegrityError(
                "Faster-Whisper checkpoint execution requires a logical namespace"
            )
        audio_path = Path(request.normalized_audio)
        audio = build_recognition_audio_binding(audio_path)
        request_binding = build_recognition_request_binding(
            episode_id=request.episode_id,
            invocation_id=request.invocation_id,
            logical_namespace=logical_namespace,
            language_hint=request.language_hint,
            context_policy_id=request.context_policy.id,
            context_bytes=request.context_policy.context_bytes,
            audio=audio,
        )
        plans = self._plan_chunks(audio)
        planner = build_recognition_chunk_planner(
            planner_name="faster-whisper-bounded-overlap",
            planner_version=str(self.ADAPTER_VERSION),
            owned_chunk_samples=self._owned_chunk_seconds * audio.sample_rate_hz,
            seam_context_samples=round(self._seam_context_ms * audio.sample_rate_hz / 1_000),
        )
        chunks = tuple(
            build_recognition_chunk_plan(
                index=item.index,
                count=item.count,
                inference_start_sample=item.inference_start_sample,
                inference_end_sample=item.inference_end_sample,
                owned_start_sample=item.owned_start_sample,
                owned_end_sample=item.owned_end_sample,
                normalized_audio=audio_path,
                audio=audio,
            )
            for item in plans
        )
        return (
            build_recognition_run_plan(
                request=request_binding,
                audio=audio,
                adapter_identity=snapshot_recognition_model_identity(self.identity),
                planner=planner,
                chunks=chunks,
            ),
            audio,
            plans,
        )

    def _load_model(self) -> object:
        _ = self.identity
        if self._model_snapshot_path is None:
            raise AdapterUnavailableError(
                "Faster-Whisper local snapshot path was not established by preflight"
            )
        if (
            self._model_snapshot_inventory_hash is None
            or hash_object(
                {"snapshot_inventory": self._snapshot_inventory(self._model_snapshot_path)}
            )
            != self._model_snapshot_inventory_hash
        ):
            raise AdapterIntegrityError(
                "Faster-Whisper local snapshot bytes changed after identity preflight"
            )
        try:
            from faster_whisper import WhisperModel

            model = WhisperModel(
                str(self._model_snapshot_path),
                device=self._device,
                device_index=self._device_index,
                compute_type=self._compute_type,
                cpu_threads=self._cpu_threads,
                num_workers=self._num_workers,
                local_files_only=True,
                revision=self._model_revision,
            )
            if (
                hash_object(
                    {"snapshot_inventory": self._snapshot_inventory(self._model_snapshot_path)}
                )
                != self._model_snapshot_inventory_hash
            ):
                del model
                raise AdapterIntegrityError(
                    "Faster-Whisper snapshot changed while the model was constructed"
                )
            return model
        except (AdapterInputError, AdapterIntegrityError, AdapterUnavailableError):
            raise
        except Exception as exc:
            raise AdapterUnavailableError("Faster-Whisper local recognition failed") from exc

    def _execute_chunk(
        self,
        audio_path: Path,
        request: RecognitionRequest,
        *,
        model: object | None,
    ) -> Mapping[str, object]:
        if self._runner is not None:
            output = self._runner(audio_path, request)
        else:
            if model is None:
                raise AdapterIntegrityError("Faster-Whisper bounded model was not loaded")
            output = self._transcribe_with_model(model, audio_path, request)
        if not isinstance(output, Mapping):
            raise AdapterInputError("Faster-Whisper runner must return a mapping")
        return output

    @staticmethod
    def _raise_terminal_rejection(finalization: RecognitionRunFinalizationV1) -> None:
        failure = finalization.failure
        code = failure.code if failure is not None else "missing_typed_failure"
        raise AdapterIntegrityError(
            f"Faster-Whisper Recognition run is terminally rejected: {code}"
        )

    @staticmethod
    def _repair_then_prepare(
        repository: RecognitionRunRepository,
        *,
        plan: RecognitionRunPlanV1,
        normalized_audio: Path,
    ) -> None:
        """Repair only authenticated publication tails before exact replay."""

        try:
            repository.prepare(plan, normalized_audio=normalized_audio)
            return
        except RecognitionRunIntegrityError as original:
            for repair in (
                repository.repair_published_tail,
                repository.repair_published_finalization,
            ):
                try:
                    repair(plan=plan)
                except RecognitionRunIntegrityError:
                    continue
                repository.prepare(plan, normalized_audio=normalized_audio)
                return
            raise original

    def _recognize_with_checkpoint(self, request: RecognitionRequest) -> RecognitionEvidence:
        repository = self._recognition_run_repository
        if repository is None:
            raise AdapterIntegrityError("Faster-Whisper checkpoint repository disappeared")
        plan, audio, chunk_plans = self._build_recognition_run_plan(request=request)
        audio_path = Path(request.normalized_audio)
        with repository.execution_lease(plan.run_key):
            self._repair_then_prepare(
                repository,
                plan=plan,
                normalized_audio=audio_path,
            )
            finalization = repository.load_finalization(plan=plan)
            if finalization is not None and finalization.status == "rejected":
                self._raise_terminal_rejection(finalization)
            replay = repository.replay_prefix(plan=plan)
            completed_prefix = len(replay)
            if completed_prefix < len(chunk_plans):
                model = None
                try:
                    model = None if self._runner is not None else self._load_model()
                    with tempfile.TemporaryDirectory(prefix="faster-whisper-bounded-") as temporary:
                        for index in range(completed_prefix, len(chunk_plans)):
                            chunk_plan = chunk_plans[index]
                            derived = derive_recognition_pcm_wav_chunk(
                                audio_path,
                                audio=audio,
                                start_sample=chunk_plan.inference_start_sample,
                                end_sample=chunk_plan.inference_end_sample,
                            )
                            chunk_path = Path(temporary) / f"{chunk_plan.id}.wav"
                            chunk_path.write_bytes(derived)
                            output = self._execute_chunk(
                                chunk_path,
                                request,
                                model=model,
                            )
                            repository.commit_chunk(
                                plan=plan,
                                chunk_index=index,
                                derived_chunk_bytes=derived,
                                adapter_observation=output,
                            )
                            del derived
                finally:
                    if model is not None:
                        # CTranslate2 releases model-owned CUDA allocations when
                        # the last reference drops. One live model serves only
                        # the pending bounded prefix in this recognizer role.
                        del model
                replay = repository.replay_prefix(plan=plan)
            if len(replay) != len(chunk_plans):
                raise RecognitionRunIntegrityError(
                    "Faster-Whisper Recognition run has an incomplete prefix"
                )
            envelope = self._build_envelope(
                request=request,
                audio=audio,
                plans=chunk_plans,
                provider_outputs=tuple(item.adapter_observation for item in replay),
                chunk_bytes_set=self._iter_chunk_bytes(
                    audio_path,
                    audio=audio,
                    plans=chunk_plans,
                ),
            )
            raw_output = _persist_raw_output(
                request=request,
                adapter_stem="faster-whisper",
                raw=envelope.model_dump(mode="json"),
            )
            raw_bytes = _file_uri_path_for_adapter(raw_output.uri).read_bytes()
            parsed = self._parse_envelope(raw_bytes)
            self._validate_envelope(parsed, request=request)
            evidence = self._evidence_from_envelope(
                parsed,
                request=request,
                raw_output=raw_output,
            )
            evidence_bytes = canonical_json_bytes(evidence)
            if finalization is None:
                repository.commit_finalization(
                    plan=plan,
                    status="verified",
                    recognition_evidence_bytes=evidence_bytes,
                )
            elif (
                finalization.status != "verified"
                or finalization.recognition_evidence_hash != sha256_bytes(evidence_bytes)
            ):
                raise RecognitionRunIntegrityError(
                    "Faster-Whisper replay differs from its terminal finalization"
                )
            return evidence

    def recognize(self, request: RecognitionRequest) -> RecognitionEvidence:
        _normalized_audio_hash(request)
        self._assert_source_identity()
        if self._recognition_run_repository is not None:
            return self._recognize_with_checkpoint(request)
        audio_path = Path(request.normalized_audio)
        audio = build_recognition_audio_binding(audio_path)
        plans = self._plan_chunks(audio)
        outputs: list[Mapping[str, object]] = []
        model = None
        try:
            model = None if self._runner is not None else self._load_model()
            with tempfile.TemporaryDirectory(prefix="faster-whisper-bounded-") as temporary:
                for plan in plans:
                    chunk_bytes = derive_recognition_pcm_wav_chunk(
                        audio_path,
                        audio=audio,
                        start_sample=plan.inference_start_sample,
                        end_sample=plan.inference_end_sample,
                    )
                    chunk_path = Path(temporary) / f"{plan.id}.wav"
                    chunk_path.write_bytes(chunk_bytes)
                    outputs.append(self._execute_chunk(chunk_path, request, model=model))
                    del chunk_bytes
        finally:
            if model is not None:
                del model
        envelope = self._build_envelope(
            request=request,
            audio=audio,
            plans=plans,
            provider_outputs=tuple(outputs),
            chunk_bytes_set=self._iter_chunk_bytes(
                audio_path,
                audio=audio,
                plans=plans,
            ),
        )
        raw_output = _persist_raw_output(
            request=request,
            adapter_stem="faster-whisper",
            raw=envelope.model_dump(mode="json"),
        )
        raw_bytes = _file_uri_path_for_adapter(raw_output.uri).read_bytes()
        parsed = self._parse_envelope(raw_bytes)
        self._validate_envelope(parsed, request=request)
        return self._evidence_from_envelope(
            parsed,
            request=request,
            raw_output=raw_output,
        )

    def verify(
        self,
        evidence: RecognitionEvidence,
        *,
        request: RecognitionRequest,
        raw_output: bytes,
    ) -> RecognitionEvidence:
        self._assert_source_identity()
        actual_digest = sha256_bytes(raw_output)
        if (
            evidence.raw_output.sha256 != actual_digest
            or evidence.raw_output_hash != actual_digest
            or evidence.raw_output.size_bytes != len(raw_output)
        ):
            raise AdapterIntegrityError("stored Faster-Whisper raw envelope digest mismatch")
        envelope = self._parse_envelope(raw_output)
        self._validate_envelope(envelope, request=request)
        replayed = self._evidence_from_envelope(
            envelope,
            request=request,
            raw_output=evidence.raw_output,
        )
        if replayed != evidence:
            raise AdapterIntegrityError("stored Faster-Whisper Evidence does not re-derive")
        return replayed


__all__ = ["FasterWhisperRecognizerAdapter", "FasterWhisperRunner"]
