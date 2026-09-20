"""Thin, offline dual-ASR entry point for an already-normalized PCM WAV.

This module deliberately owns only recognition.  It runs Qwen first and
Faster-Whisper second on the same immutable audio clock, persists their
canonical Evidence, and relies on the existing RecognitionRunRepository for
chunk-level resume after interruption.  Execution order is intentionally
separate from downstream transcript roles: Faster-Whisper is the correction
base, while Qwen is the independent corroborator and punctuation source.
"""

from __future__ import annotations

import gc
import json
import math
import os
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Literal, Protocol

from shared.schemas.podcast_subtitles_v2 import ArtifactDigest, RecognitionEvidence

from .adapters import faster_whisper_recognition as faster_recognition
from .adapters import recognition as qwen_recognition
from .adapters.faster_whisper_recognition import FasterWhisperRecognizerAdapter
from .adapters.recognition import Qwen3ASRRecognizerAdapter
from .hashing import (
    canonical_json_bytes,
    hash_file,
    hash_object,
    measure_regular_file,
    sha256_bytes,
)
from .ports import (
    NO_LEXICAL_BIAS_CONTEXT,
    AdapterInputError,
    AdapterIntegrityError,
    RecognitionRequest,
)
from .recognition_pilot import (
    FASTER_MODEL_REVISION,
    QWEN_ALIGNER_REVISION,
    QWEN_MODEL_REVISION,
)
from .recognition_run import RecognitionRunRepository, build_recognition_audio_binding

ACCURATE_RECOGNITION_RUNNER_VERSION = 1
_INVOCATION_NAMESPACE = "podcast-subtitle-v2/accurate-recognition/v1"
_FASTER_SEGMENT_BOUNDARY_RECOVERY_NAMESPACE = (
    "podcast-subtitle-v2/faster-segment-boundary-recovery/v1"
)


class Recognizer(Protocol):
    """Minimum Adapter surface required by the orchestration entry point."""

    def recognize(self, request: RecognitionRequest) -> RecognitionEvidence: ...


@dataclass(frozen=True, slots=True)
class AccurateRecognitionResult:
    """The two hypotheses in GPU execution order plus provider-safe accessors.

    ``primary_*`` and ``corroborating_*`` are retained as compatibility names
    for the original execution topology (Qwen then Faster-Whisper).  New code
    must use the provider-named properties below so that execution order is not
    confused with the correction-base decision.
    """

    episode_id: str
    invocation_id: str
    audio_sha256: str
    primary_evidence: RecognitionEvidence
    corroborating_evidence: RecognitionEvidence
    primary_evidence_path: Path
    corroborating_evidence_path: Path
    primary_seam_conflicts: tuple[QwenSeamConflict, ...] = ()
    primary_owned_range_repairs: tuple[QwenOwnedRangeRepair, ...] = ()
    corroborating_segment_boundary_repairs: tuple[
        FasterSegmentBoundaryRepair, ...
    ] = ()
    corroborating_seam_conflicts: tuple[FasterSeamConflict, ...] = ()
    corroborating_owned_range_repairs: tuple[FasterOwnedRangeRepair, ...] = ()

    @property
    def qwen_evidence(self) -> RecognitionEvidence:
        return self.primary_evidence

    @property
    def qwen_evidence_path(self) -> Path:
        return self.primary_evidence_path

    @property
    def qwen_seam_conflicts(self) -> tuple[QwenSeamConflict, ...]:
        return self.primary_seam_conflicts

    @property
    def qwen_owned_range_repairs(self) -> tuple[QwenOwnedRangeRepair, ...]:
        return self.primary_owned_range_repairs

    @property
    def faster_evidence(self) -> RecognitionEvidence:
        return self.corroborating_evidence

    @property
    def faster_evidence_path(self) -> Path:
        return self.corroborating_evidence_path

    @property
    def faster_segment_boundary_repairs(
        self,
    ) -> tuple[FasterSegmentBoundaryRepair, ...]:
        return self.corroborating_segment_boundary_repairs

    @property
    def faster_seam_conflicts(self) -> tuple[FasterSeamConflict, ...]:
        return self.corroborating_seam_conflicts

    @property
    def faster_owned_range_repairs(self) -> tuple[FasterOwnedRangeRepair, ...]:
        return self.corroborating_owned_range_repairs


@dataclass(frozen=True, slots=True)
class QwenSeamConflict:
    """One verified disagreement in Qwen's overlapping seam observations."""

    id: str
    seam_sample: int
    seam_ms: int
    comparison_start_sample: int
    comparison_end_sample: int
    comparison_start_ms: int
    comparison_end_ms: int
    left_chunk_id: str
    right_chunk_id: str
    left_text: str
    right_text: str
    left_normalized: str
    right_normalized: str


@dataclass(frozen=True, slots=True)
class QwenOwnedWordSnapshot:
    """Exact immutable word state before or after one bounded seam repair."""

    source_item_id: str
    source_chunk_id: str
    text: str
    start_ms: int
    end_ms: int


@dataclass(frozen=True, slots=True)
class QwenOwnedRangeRepair:
    """Auditable repair of one deterministic cross-chunk seam overlap."""

    id: str
    seam_id: str
    reason: Literal[
        "drop_right_duplicate_left_suffix",
        "drop_left_duplicate_right_prefix",
        "clamp_left_end_cross_seam_timestamp_spill",
    ]
    overlap_ms: int
    before_tokens: tuple[QwenOwnedWordSnapshot, QwenOwnedWordSnapshot]
    after_tokens: tuple[QwenOwnedWordSnapshot, ...]


@dataclass(frozen=True, slots=True)
class FasterSegmentBoundaryRepair:
    """One authenticated, start-only provider segment boundary repair."""

    id: str
    chunk_id: str
    chunk_index: int
    segment_id: int
    word_id: str
    word_index: int
    word_text: str
    provider_observation_sha256: str
    before_segment_start_seconds: float
    after_segment_start_seconds: float
    delta_ms: int


@dataclass(frozen=True, slots=True)
class FasterSeamConflict:
    """One real disagreement between adjacent shared-context observations."""

    id: str
    seam_sample: int
    seam_ms: int
    comparison_start_sample: int
    comparison_end_sample: int
    comparison_start_ms: int
    comparison_end_ms: int
    left_chunk_id: str
    right_chunk_id: str
    left_text: str
    right_text: str
    left_normalized: str
    right_normalized: str


@dataclass(frozen=True, slots=True)
class FasterOwnedWordSnapshot:
    """Exact owned word state around a corroborating timing-only repair."""

    source_chunk_id: str
    source_chunk_index: int
    source_word_index: int
    text: str
    start_sample: int
    end_sample: int
    start_ms: int
    end_ms: int


@dataclass(frozen=True, slots=True)
class FasterOwnedRangeRepair:
    """Auditable clamp of a bounded distinct-text cross-chunk overlap."""

    id: str
    seam_id: str
    reason: Literal["clamp_left_end_cross_chunk_timestamp_spill"]
    overlap_samples: int
    overlap_ms: int
    before_tokens: tuple[FasterOwnedWordSnapshot, FasterOwnedWordSnapshot]
    after_tokens: tuple[FasterOwnedWordSnapshot, FasterOwnedWordSnapshot]


_MAX_QWEN_DUPLICATE_SEAM_OVERLAP_MS = 320
_MAX_QWEN_DISTINCT_TEXT_CLAMP_MS = 80
_MIN_FASTER_SEGMENT_START_REPAIR_MS = 20
_MAX_FASTER_SEGMENT_START_REPAIR_MS = 200
_MAX_FASTER_CHUNK_DURATION_DRIFT_SECONDS = 0.001
_MAX_FASTER_DISTINCT_TEXT_CLAMP_MS = 40


def _required_identity(value: str, *, label: str) -> str:
    if not isinstance(value, str) or not value.strip() or value != value.strip():
        raise ValueError(f"{label} must be non-blank and trimmed")
    return value


def stable_recognition_invocation_id(*, episode_id: str, audio_sha256: str) -> str:
    """Derive a replay-stable invocation from the input and runner contract."""

    episode_id = _required_identity(episode_id, label="episode_id")
    return "accurate-recognition-" + hash_object(
        {
            "namespace": _INVOCATION_NAMESPACE,
            "runner_version": ACCURATE_RECOGNITION_RUNNER_VERSION,
            "episode_id": episode_id,
            "audio_sha256": audio_sha256,
        }
    )


def build_accurate_recognizers(output_dir: Path) -> tuple[Recognizer, Recognizer]:
    """Build pinned, local-only, checkpoint-aware production recognizers."""

    repository = RecognitionRunRepository(Path(output_dir))
    primary = Qwen3ASRRecognizerAdapter(
        model_revision=QWEN_MODEL_REVISION,
        forced_aligner_revision=QWEN_ALIGNER_REVISION,
        local_files_only=True,
        recognition_run_repository=repository,
        logical_namespace=Qwen3ASRRecognizerAdapter.RECOGNITION_RUN_NAMESPACE,
    )
    corroborating = FasterWhisperRecognizerAdapter(
        model_revision=FASTER_MODEL_REVISION,
        local_files_only=True,
        recognition_run_repository=repository,
        logical_namespace=FasterWhisperRecognizerAdapter.RECOGNITION_RUN_NAMESPACE,
    )
    return primary, corroborating


def _release_primary_gpu_memory() -> None:
    """Release Qwen references/cache before the CTranslate2 model is loaded."""

    gc.collect()
    torch = sys.modules.get("torch")
    cuda = getattr(torch, "cuda", None) if torch is not None else None
    empty_cache = getattr(cuda, "empty_cache", None)
    if callable(empty_cache):
        empty_cache()


def _validate_evidence(
    evidence: RecognitionEvidence,
    *,
    request: RecognitionRequest,
    audio_sha256: str,
    role: str,
) -> None:
    if not isinstance(evidence, RecognitionEvidence):
        raise TypeError(f"{role} recognizer did not return RecognitionEvidence")
    if (
        evidence.episode_id != request.episode_id
        or evidence.invocation_id != request.invocation_id
        or evidence.normalized_audio_hash != audio_sha256
    ):
        raise ValueError(f"{role} RecognitionEvidence is not bound to the shared request")


def _raw_directory(root: Path, *, role: str, invocation_id: str) -> Path:
    invocation_key = sha256_bytes(invocation_id.encode("utf-8"))
    return root / "raw" / role / invocation_key


def _request_for_role(
    *,
    root: Path,
    role: str,
    episode_id: str,
    invocation_id: str,
    audio_path: Path,
    audio_sha256: str,
) -> RecognitionRequest:
    return RecognitionRequest(
        episode_id=episode_id,
        invocation_id=invocation_id,
        normalized_audio=audio_path,
        expected_normalized_audio_hash=audio_sha256,
        raw_output_dir=_raw_directory(
            root,
            role=role,
            invocation_id=invocation_id,
        ),
        language_hint="zh-TW",
        context_policy=NO_LEXICAL_BIAS_CONTEXT,
    )


def _has_recorded_seam_conflict(
    adapter: Qwen3ASRRecognizerAdapter,
    *,
    request: RecognitionRequest,
) -> bool:
    """Authenticate the terminal failure without parsing exception prose."""

    repository = adapter._recognition_run_repository
    if repository is None:
        return False
    audio_path = Path(request.normalized_audio)
    topology = qwen_recognition._read_pcm_wav_topology(audio_path)
    plans = adapter._plan_chunks(topology)
    plan = adapter._build_recognition_run_plan(
        audio_path=audio_path,
        request=request,
        topology=topology,
        plans=plans,
    )
    finalization = repository.load_finalization(plan=plan)
    return bool(
        finalization is not None
        and finalization.status == "rejected"
        and finalization.failure is not None
        and finalization.failure.code == "seam_conflict"
    )


def _rebuild_qwen_raw_from_checkpoint(
    adapter: Qwen3ASRRecognizerAdapter,
    *,
    request: RecognitionRequest,
) -> None:
    """Materialize the current role-owned raw from already-durable observations."""

    repository = adapter._recognition_run_repository
    if repository is None:
        raise AdapterIntegrityError("Qwen seam recovery requires a checkpoint repository")
    audio_path = Path(request.normalized_audio)
    topology = qwen_recognition._read_pcm_wav_topology(audio_path)
    plans = adapter._plan_chunks(topology)
    plan = adapter._build_recognition_run_plan(
        audio_path=audio_path,
        request=request,
        topology=topology,
        plans=plans,
    )
    replay = repository.replay_prefix(plan=plan)
    if len(replay) != len(plans):
        raise AdapterIntegrityError(
            "Qwen seam recovery checkpoint does not contain the complete durable prefix"
        )
    try:
        adapter._assemble_checkpoint_evidence(
            audio_path=audio_path,
            request=request,
            topology=topology,
            plans=plans,
            provider_outputs=tuple(item.adapter_observation for item in replay),
        )
    except qwen_recognition._QwenDeterministicRejection as rejection:
        if rejection.failure_code != "seam_conflict":
            raise
    else:
        raise AdapterIntegrityError(
            "Qwen seam checkpoint rebuild did not reproduce its terminal conflict"
        )


def _read_exact_qwen_raw(request: RecognitionRequest) -> tuple[bytes, Path]:
    raw_dir = Path(request.raw_output_dir) if request.raw_output_dir is not None else None
    if raw_dir is None or not raw_dir.is_dir():
        raise AdapterIntegrityError("recoverable Qwen seam conflict has no role-specific raw dir")
    entries = tuple(sorted(raw_dir.iterdir(), key=lambda item: item.name))
    if len(entries) != 1 or not entries[0].is_file():
        raise AdapterIntegrityError(
            "recoverable Qwen seam conflict requires exactly one unambiguous raw artifact"
        )
    path = entries[0]
    before_hash, before_size = measure_regular_file(path)
    expected_name = f"qwen3-asr-{before_hash}.json"
    if path.name != expected_name:
        raise AdapterIntegrityError("Qwen seam-conflict raw artifact is not content-addressed")
    raw = path.read_bytes()
    if (sha256_bytes(raw), len(raw)) != (before_hash, before_size):
        raise AdapterIntegrityError("Qwen seam-conflict raw artifact changed during read")
    if measure_regular_file(path) != (before_hash, before_size):
        raise AdapterIntegrityError("Qwen seam-conflict raw artifact changed after read")
    return raw, path


def _conflicts_from_envelope(
    envelope: qwen_recognition._QwenRecognitionEnvelope,
) -> tuple[QwenSeamConflict, ...]:
    sample_rate = envelope.normalized_audio.sample_rate_hz

    def milliseconds(sample: int) -> int:
        return round(sample * 1_000 / sample_rate)

    return tuple(
        QwenSeamConflict(
            id=seam.id,
            seam_sample=seam.seam_sample,
            seam_ms=milliseconds(seam.seam_sample),
            comparison_start_sample=seam.comparison_start_sample,
            comparison_end_sample=seam.comparison_end_sample,
            comparison_start_ms=milliseconds(seam.comparison_start_sample),
            comparison_end_ms=milliseconds(seam.comparison_end_sample),
            left_chunk_id=seam.left_chunk_id,
            right_chunk_id=seam.right_chunk_id,
            left_text=seam.left_text,
            right_text=seam.right_text,
            left_normalized=seam.left_normalized,
            right_normalized=seam.right_normalized,
        )
        for seam in envelope.seams
        if seam.status == "conflict"
    )


def _owned_word_snapshot(
    word: qwen_recognition._QwenMergedWord,
) -> QwenOwnedWordSnapshot:
    return QwenOwnedWordSnapshot(
        source_item_id=word.source_item_id,
        source_chunk_id=word.source_chunk_id,
        text=word.word,
        start_ms=word.start_ms,
        end_ms=word.end_ms,
    )


def _owned_range_repair(
    *,
    seam_id: str,
    reason: Literal[
        "drop_right_duplicate_left_suffix",
        "drop_left_duplicate_right_prefix",
        "clamp_left_end_cross_seam_timestamp_spill",
    ],
    overlap_ms: int,
    before: tuple[qwen_recognition._QwenMergedWord, qwen_recognition._QwenMergedWord],
    after: tuple[qwen_recognition._QwenMergedWord, ...],
) -> QwenOwnedRangeRepair:
    before_tokens = (_owned_word_snapshot(before[0]), _owned_word_snapshot(before[1]))
    after_tokens = tuple(_owned_word_snapshot(word) for word in after)
    addressed = {
        "namespace": "podcast-subtitle-v2/qwen-owned-range-seam-repair/v1",
        "seam_id": seam_id,
        "reason": reason,
        "overlap_ms": overlap_ms,
        "before_tokens": before_tokens,
        "after_tokens": after_tokens,
    }
    return QwenOwnedRangeRepair(
        id="qwen-owned-range-repair-" + hash_object(addressed),
        seam_id=seam_id,
        reason=reason,
        overlap_ms=overlap_ms,
        before_tokens=before_tokens,
        after_tokens=after_tokens,
    )


def _repair_qwen_owned_range_overlaps(
    envelope: qwen_recognition._QwenRecognitionEnvelope,
) -> tuple[
    qwen_recognition._QwenRecognitionEnvelope,
    tuple[QwenOwnedRangeRepair, ...],
]:
    """Repair only bounded, provable duplicate/spill overlaps at Qwen seams."""

    if not envelope.merged_words:
        raise AdapterIntegrityError("Qwen seam conflict also has empty owned output")
    chunk_indices = {chunk.id: chunk.index for chunk in envelope.chunks}
    seams = {(seam.left_chunk_id, seam.right_chunk_id): seam for seam in envelope.seams}
    if len(chunk_indices) != len(envelope.chunks) or len(seams) != len(envelope.seams):
        raise AdapterIntegrityError("Qwen seam repair requires unique chunk and seam topology")

    words = list(envelope.merged_words)
    repairs: list[QwenOwnedRangeRepair] = []
    index = 1
    while index < len(words):
        left = words[index - 1]
        right = words[index]
        if right.start_ms >= left.end_ms:
            index += 1
            continue

        overlap_ms = left.end_ms - right.start_ms
        left_chunk_index = chunk_indices.get(left.source_chunk_id)
        right_chunk_index = chunk_indices.get(right.source_chunk_id)
        if (
            left_chunk_index is None
            or right_chunk_index is None
            or right_chunk_index != left_chunk_index + 1
        ):
            raise AdapterIntegrityError(
                "Qwen owned-range overlap is not across neighboring source chunks"
            )
        seam = seams.get((left.source_chunk_id, right.source_chunk_id))
        if seam is None:
            raise AdapterIntegrityError("Qwen owned-range overlap has no exact seam receipt")

        sample_rate = envelope.normalized_audio.sample_rate_hz
        comparison_start_ms = round(seam.comparison_start_sample * 1_000 / sample_rate)
        comparison_end_ms = round(seam.comparison_end_sample * 1_000 / sample_rate)
        overlap_start_ms = max(left.start_ms, right.start_ms)
        overlap_end_ms = min(left.end_ms, right.end_ms)
        if (
            overlap_end_ms <= overlap_start_ms
            or overlap_start_ms < comparison_start_ms
            or overlap_end_ms > comparison_end_ms
        ):
            raise AdapterIntegrityError(
                "Qwen owned-range overlap escapes its known seam comparison interval"
            )

        left_normalized = qwen_recognition._seam_text(left.word)
        right_normalized = qwen_recognition._seam_text(right.word)
        if not left_normalized or not right_normalized:
            raise AdapterIntegrityError("Qwen seam repair cannot compare empty normalized text")

        before = (left, right)
        if left_normalized.endswith(right_normalized):
            if overlap_ms > _MAX_QWEN_DUPLICATE_SEAM_OVERLAP_MS:
                raise AdapterIntegrityError(
                    "Qwen duplicate overlap exceeds the bounded seam-repair limit"
                )
            repairs.append(
                _owned_range_repair(
                    seam_id=seam.id,
                    reason="drop_right_duplicate_left_suffix",
                    overlap_ms=overlap_ms,
                    before=before,
                    after=(left,),
                )
            )
            words.pop(index)
            continue
        if right_normalized.startswith(left_normalized):
            if overlap_ms > _MAX_QWEN_DUPLICATE_SEAM_OVERLAP_MS:
                raise AdapterIntegrityError(
                    "Qwen duplicate overlap exceeds the bounded seam-repair limit"
                )
            repairs.append(
                _owned_range_repair(
                    seam_id=seam.id,
                    reason="drop_left_duplicate_right_prefix",
                    overlap_ms=overlap_ms,
                    before=before,
                    after=(right,),
                )
            )
            words.pop(index - 1)
            index = max(1, index - 1)
            continue

        if overlap_ms > _MAX_QWEN_DISTINCT_TEXT_CLAMP_MS:
            raise AdapterIntegrityError(
                "Qwen distinct-text timestamp spill exceeds the 80 ms clamp limit"
            )
        if right.start_ms <= left.start_ms:
            raise AdapterIntegrityError(
                "Qwen cross-seam timestamp clamp would make the left token non-positive"
            )
        clamped_left = left.model_copy(update={"end_ms": right.start_ms})
        repairs.append(
            _owned_range_repair(
                seam_id=seam.id,
                reason="clamp_left_end_cross_seam_timestamp_spill",
                overlap_ms=overlap_ms,
                before=before,
                after=(clamped_left, right),
            )
        )
        words[index - 1] = clamped_left
        index += 1

    previous_end = 0
    for word in words:
        if word.end_ms <= word.start_ms or word.start_ms < previous_end:
            raise AdapterIntegrityError("Qwen repaired owned words are not positive and monotonic")
        previous_end = word.end_ms
    payload = envelope.model_dump(mode="python")
    payload["merged_words"] = tuple(word.model_dump(mode="python") for word in words)
    try:
        repaired = qwen_recognition._QwenRecognitionEnvelope.model_validate(payload)
    except Exception as exc:
        raise AdapterIntegrityError("Qwen repaired envelope failed structural validation") from exc
    return repaired, tuple(repairs)


def _recover_verified_qwen_seam_conflict(
    adapter: Qwen3ASRRecognizerAdapter,
    *,
    request: RecognitionRequest,
) -> tuple[
    RecognitionEvidence,
    tuple[QwenSeamConflict, ...],
    tuple[QwenOwnedRangeRepair, ...],
]:
    """Recover owned words only after replay proves seam conflict is the sole defect."""

    raw_dir = Path(request.raw_output_dir) if request.raw_output_dir is not None else None
    if raw_dir is None or not raw_dir.is_dir() or not tuple(raw_dir.iterdir()):
        _rebuild_qwen_raw_from_checkpoint(adapter, request=request)
    raw, raw_path = _read_exact_qwen_raw(request)
    envelope = adapter._parse_envelope(raw)
    if canonical_json_bytes(envelope) != raw:
        raise AdapterIntegrityError("Qwen seam-conflict raw artifact is not canonical JSON")
    try:
        adapter._validate_envelope(envelope, request=request)
    except qwen_recognition._QwenDeterministicRejection as rejection:
        if rejection.failure_code != "seam_conflict":
            raise
    else:
        raise AdapterIntegrityError(
            "recorded Qwen seam conflict is not reproduced by its raw artifact"
        )

    conflicts = _conflicts_from_envelope(envelope)
    if not conflicts:
        raise AdapterIntegrityError("Qwen seam-conflict recovery found no conflicted seam")
    repaired_envelope, repairs = _repair_qwen_owned_range_overlaps(envelope)
    raw_hash = sha256_bytes(raw)
    raw_output = ArtifactDigest(
        uri=raw_path.resolve().as_uri(),
        sha256=raw_hash,
        size_bytes=len(raw),
    )
    evidence = adapter._evidence_from_envelope(
        repaired_envelope,
        request=request,
        raw_output=raw_output,
    )
    return evidence, conflicts, repairs


def _recognize_primary(
    recognizer: Recognizer,
    *,
    request: RecognitionRequest,
) -> tuple[
    RecognitionEvidence,
    tuple[QwenSeamConflict, ...],
    tuple[QwenOwnedRangeRepair, ...],
]:
    try:
        return recognizer.recognize(request), (), ()
    except AdapterIntegrityError:
        if not isinstance(recognizer, Qwen3ASRRecognizerAdapter) or not (
            _has_recorded_seam_conflict(recognizer, request=request)
        ):
            raise
        return _recover_verified_qwen_seam_conflict(recognizer, request=request)


def _finite_faster_timestamp(value: object, *, label: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise AdapterIntegrityError(f"Faster boundary recovery {label} is not numeric")
    result = float(value)
    if not math.isfinite(result) or result < 0:
        raise AdapterIntegrityError(
            f"Faster boundary recovery {label} is not finite and nonnegative"
        )
    return result


def _faster_repair_payload(repair: FasterSegmentBoundaryRepair) -> dict[str, object]:
    return {
        "id": repair.id,
        "chunk_id": repair.chunk_id,
        "chunk_index": repair.chunk_index,
        "segment_id": repair.segment_id,
        "word_id": repair.word_id,
        "word_index": repair.word_index,
        "word_text": repair.word_text,
        "provider_observation_sha256": repair.provider_observation_sha256,
        "before_segment_start_seconds": repair.before_segment_start_seconds,
        "after_segment_start_seconds": repair.after_segment_start_seconds,
        "delta_ms": repair.delta_ms,
    }


def _repair_faster_provider_observation(
    adapter: FasterWhisperRecognizerAdapter,
    *,
    observation_bytes: bytes,
    repository_chunk_id: str,
    adapter_chunk: faster_recognition._FasterChunkPlan,
    sample_rate_hz: int,
) -> tuple[dict[str, object], tuple[FasterSegmentBoundaryRepair, ...]]:
    """Copy and validate one checkpoint observation, changing only segment.start."""

    try:
        decoded = json.loads(observation_bytes)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise AdapterIntegrityError(
            "Faster boundary recovery checkpoint observation is not canonical JSON"
        ) from exc
    if not isinstance(decoded, dict) or canonical_json_bytes(decoded) != observation_bytes:
        raise AdapterIntegrityError(
            "Faster boundary recovery checkpoint observation is not canonical mapping bytes"
        )
    provider_observation_sha256 = sha256_bytes(observation_bytes)
    duration = _finite_faster_timestamp(decoded.get("duration"), label="duration")
    duration_after_vad = _finite_faster_timestamp(
        decoded.get("duration_after_vad"),
        label="duration_after_vad",
    )
    expected_duration = (
        adapter_chunk.inference_end_sample - adapter_chunk.inference_start_sample
    ) / sample_rate_hz
    if (
        duration <= 0
        or abs(duration - expected_duration)
        > _MAX_FASTER_CHUNK_DURATION_DRIFT_SECONDS
        or abs(duration_after_vad - duration)
        > _MAX_FASTER_CHUNK_DURATION_DRIFT_SECONDS
    ):
        raise AdapterIntegrityError(
            "Faster boundary recovery provider duration differs from its exact chunk"
        )

    segments = decoded.get("segments")
    if not isinstance(segments, list):
        raise AdapterIntegrityError(
            "Faster boundary recovery provider segments are not a JSON array"
        )
    repairs: list[FasterSegmentBoundaryRepair] = []
    previous_segment_end = 0.0
    for segment_index, segment in enumerate(segments):
        if not isinstance(segment, dict):
            raise AdapterIntegrityError(
                "Faster boundary recovery provider segment is not a mapping"
            )
        segment_id = segment.get("id")
        if isinstance(segment_id, bool) or not isinstance(segment_id, int):
            raise AdapterIntegrityError(
                "Faster boundary recovery provider segment id is invalid"
            )
        segment_start = _finite_faster_timestamp(
            segment.get("start"),
            label=f"segments[{segment_index}].start",
        )
        segment_end = _finite_faster_timestamp(
            segment.get("end"),
            label=f"segments[{segment_index}].end",
        )
        if (
            segment_end <= segment_start
            or segment_start > duration + _MAX_FASTER_CHUNK_DURATION_DRIFT_SECONDS
            or segment_end > duration + _MAX_FASTER_CHUNK_DURATION_DRIFT_SECONDS
        ):
            raise AdapterIntegrityError(
                "Faster boundary recovery provider segment exceeds its exact chunk duration"
            )
        words = segment.get("words")
        if not isinstance(words, list) or not words:
            raise AdapterIntegrityError(
                "Faster boundary recovery provider segment has no word array"
            )

        positive_words: list[tuple[int, dict[str, object], float, float]] = []
        for word_index, word in enumerate(words):
            if not isinstance(word, dict):
                raise AdapterIntegrityError(
                    "Faster boundary recovery provider word is not a mapping"
                )
            word_start = _finite_faster_timestamp(
                word.get("start"),
                label=f"segments[{segment_index}].words[{word_index}].start",
            )
            word_end = _finite_faster_timestamp(
                word.get("end"),
                label=f"segments[{segment_index}].words[{word_index}].end",
            )
            if (
                word_end < word_start
                or word_start > duration + _MAX_FASTER_CHUNK_DURATION_DRIFT_SECONDS
                or word_end > duration + _MAX_FASTER_CHUNK_DURATION_DRIFT_SECONDS
            ):
                raise AdapterIntegrityError(
                    "Faster boundary recovery provider word exceeds its exact chunk duration"
                )
            if word_end > word_start:
                positive_words.append((word_index, word, word_start, word_end))

        before_segment = [
            item for item in positive_words if item[2] < segment_start
        ]
        if before_segment:
            if (
                len(before_segment) != 1
                or not positive_words
                or before_segment[0][0] != positive_words[0][0]
            ):
                raise AdapterIntegrityError(
                    "Faster boundary recovery violation is not the first positive word"
                )
            word_index, word, word_start, word_end = before_segment[0]
            delta_seconds = segment_start - word_start
            delta_ms = round(delta_seconds * 1_000)
            if (
                not math.isclose(
                    delta_seconds,
                    delta_ms / 1_000,
                    rel_tol=0,
                    abs_tol=1e-9,
                )
                or not _MIN_FASTER_SEGMENT_START_REPAIR_MS
                <= delta_ms
                <= _MAX_FASTER_SEGMENT_START_REPAIR_MS
            ):
                raise AdapterIntegrityError(
                    "Faster boundary recovery segment-start delta is outside 20-200 ms"
                )
            if word_start < previous_segment_end:
                raise AdapterIntegrityError(
                    "Faster boundary recovery would overlap the previous provider segment"
                )
            if word_end > segment_end:
                raise AdapterIntegrityError(
                    "Faster boundary recovery provider word exceeds its segment end"
                )
            word_text = word.get("word")
            if not isinstance(word_text, str) or not word_text:
                raise AdapterIntegrityError(
                    "Faster boundary recovery provider word text is invalid"
                )
            word_identity_payload = {
                "namespace": "podcast-subtitle-v2/faster-provider-word/v1",
                "provider_observation_sha256": provider_observation_sha256,
                "chunk_id": repository_chunk_id,
                "chunk_index": adapter_chunk.index,
                "segment_id": segment_id,
                "word_index": word_index,
                "word_text": word_text,
                "word_start_seconds": word_start,
                "word_end_seconds": word_end,
            }
            word_id = "faster-provider-word-" + hash_object(word_identity_payload)
            addressed = {
                "namespace": _FASTER_SEGMENT_BOUNDARY_RECOVERY_NAMESPACE,
                "chunk_id": repository_chunk_id,
                "chunk_index": adapter_chunk.index,
                "segment_id": segment_id,
                "word_id": word_id,
                "word_index": word_index,
                "word_text": word_text,
                "provider_observation_sha256": provider_observation_sha256,
                "before_segment_start_seconds": segment_start,
                "after_segment_start_seconds": word_start,
                "delta_ms": delta_ms,
            }
            repairs.append(
                FasterSegmentBoundaryRepair(
                    id="faster-segment-boundary-repair-" + hash_object(addressed),
                    chunk_id=repository_chunk_id,
                    chunk_index=adapter_chunk.index,
                    segment_id=segment_id,
                    word_id=word_id,
                    word_index=word_index,
                    word_text=word_text,
                    provider_observation_sha256=provider_observation_sha256,
                    before_segment_start_seconds=segment_start,
                    after_segment_start_seconds=word_start,
                    delta_ms=delta_ms,
                )
            )
            segment["start"] = word_start
        previous_segment_end = segment_end

    # The mature Adapter remains the authority for every schema, text, ordering,
    # end-boundary, and positive-duration invariant after the one allowed copy edit.
    typed = adapter._typed_provider_observation(decoded)
    return typed.model_dump(mode="json"), tuple(repairs)


def _faster_seam_conflict_payload(conflict: FasterSeamConflict) -> dict[str, object]:
    return {
        "id": conflict.id,
        "seam_sample": conflict.seam_sample,
        "seam_ms": conflict.seam_ms,
        "comparison_start_sample": conflict.comparison_start_sample,
        "comparison_end_sample": conflict.comparison_end_sample,
        "comparison_start_ms": conflict.comparison_start_ms,
        "comparison_end_ms": conflict.comparison_end_ms,
        "left_chunk_id": conflict.left_chunk_id,
        "right_chunk_id": conflict.right_chunk_id,
        "left_text": conflict.left_text,
        "right_text": conflict.right_text,
        "left_normalized": conflict.left_normalized,
        "right_normalized": conflict.right_normalized,
    }


def _faster_owned_word_snapshot(
    word: faster_recognition._MergedWord,
    *,
    chunk_indices: dict[str, int],
    sample_rate_hz: int,
) -> FasterOwnedWordSnapshot:
    source_chunk_index = chunk_indices.get(word.source_chunk_id)
    if source_chunk_index is None:
        raise AdapterIntegrityError("Faster owned word references an unknown source chunk")
    start_sample = round(word.start * sample_rate_hz)
    end_sample = round(word.end * sample_rate_hz)
    return FasterOwnedWordSnapshot(
        source_chunk_id=word.source_chunk_id,
        source_chunk_index=source_chunk_index,
        source_word_index=word.source_word_index,
        text=word.word,
        start_sample=start_sample,
        end_sample=end_sample,
        start_ms=round(start_sample * 1_000 / sample_rate_hz),
        end_ms=round(end_sample * 1_000 / sample_rate_hz),
    )


def _faster_snapshot_payload(snapshot: FasterOwnedWordSnapshot) -> dict[str, object]:
    return {
        "source_chunk_id": snapshot.source_chunk_id,
        "source_chunk_index": snapshot.source_chunk_index,
        "source_word_index": snapshot.source_word_index,
        "text": snapshot.text,
        "start_sample": snapshot.start_sample,
        "end_sample": snapshot.end_sample,
        "start_ms": snapshot.start_ms,
        "end_ms": snapshot.end_ms,
    }


def _faster_owned_range_repair_payload(
    repair: FasterOwnedRangeRepair,
) -> dict[str, object]:
    return {
        "id": repair.id,
        "seam_id": repair.seam_id,
        "reason": repair.reason,
        "overlap_samples": repair.overlap_samples,
        "overlap_ms": repair.overlap_ms,
        "before_tokens": tuple(
            _faster_snapshot_payload(item) for item in repair.before_tokens
        ),
        "after_tokens": tuple(
            _faster_snapshot_payload(item) for item in repair.after_tokens
        ),
    }


def _validated_faster_chunk_receipts(
    adapter: FasterWhisperRecognizerAdapter,
    *,
    audio_path: Path,
    audio: object,
    repository_chunks: tuple[object, ...],
    adapter_chunks: tuple[faster_recognition._FasterChunkPlan, ...],
    provider_outputs: tuple[dict[str, object], ...],
) -> tuple[faster_recognition._ChunkReceipt, ...]:
    """Build every mature chunk receipt from one exact audio-derivation pass."""

    if not (
        len(repository_chunks) == len(adapter_chunks) == len(provider_outputs)
    ):
        raise AdapterIntegrityError("Faster secondary recovery chunk sets are incomplete")
    derived_chunks = iter(
        adapter._iter_chunk_bytes(
            audio_path,
            audio=audio,
            plans=adapter_chunks,
        )
    )
    receipts: list[faster_recognition._ChunkReceipt] = []
    sentinel = object()
    for repository_chunk, adapter_chunk, provider_output in zip(
        repository_chunks,
        adapter_chunks,
        provider_outputs,
    ):
        derived = next(derived_chunks, sentinel)
        if derived is sentinel:
            raise AdapterIntegrityError("Faster secondary recovery lacks a derived chunk")
        assert isinstance(derived, bytes)
        receipt = adapter._chunk_receipt(
            plan=adapter_chunk,
            audio=audio,
            chunk_bytes=derived,
            provider_output=provider_output,
        )
        if (
            receipt.inference_start_sample != repository_chunk.inference_start_sample
            or receipt.inference_end_sample != repository_chunk.inference_end_sample
            or receipt.owned_start_sample != repository_chunk.owned_start_sample
            or receipt.owned_end_sample != repository_chunk.owned_end_sample
            or receipt.derived_audio_sha256 != repository_chunk.derived_audio.sha256
            or receipt.derived_audio_size_bytes != repository_chunk.derived_audio.size_bytes
        ):
            raise AdapterIntegrityError(
                "Faster secondary recovery chunk differs from its immutable plan"
            )
        receipts.append(receipt)
        del derived
    if next(derived_chunks, sentinel) is not sentinel:
        raise AdapterIntegrityError("Faster secondary recovery has an excess derived chunk")
    return tuple(receipts)


def _faster_seams_and_conflicts(
    adapter: FasterWhisperRecognizerAdapter,
    *,
    chunks: tuple[faster_recognition._ChunkReceipt, ...],
    sample_rate_hz: int,
) -> tuple[
    tuple[faster_recognition._SeamReceipt, ...],
    tuple[FasterSeamConflict, ...],
]:
    seams: list[faster_recognition._SeamReceipt] = []
    conflicts: list[FasterSeamConflict] = []

    def milliseconds(sample: int) -> int:
        return round(sample * 1_000 / sample_rate_hz)

    for left, right in zip(chunks, chunks[1:]):
        comparison_start = max(
            left.inference_start_sample,
            right.inference_start_sample,
        )
        comparison_end = min(
            left.inference_end_sample,
            right.inference_end_sample,
        )
        if comparison_end <= comparison_start:
            raise AdapterIntegrityError(
                "Faster adjacent chunks have no shared comparison interval"
            )
        left_text = "".join(
            word.word
            for word in adapter._words_in_range(
                left,
                start_sample=comparison_start,
                end_sample=comparison_end,
            )
        )
        right_text = "".join(
            word.word
            for word in adapter._words_in_range(
                right,
                start_sample=comparison_start,
                end_sample=comparison_end,
            )
        )
        left_normalized = adapter._seam_text(left_text)
        right_normalized = adapter._seam_text(right_text)
        status: Literal["matched", "conflict"] = (
            "matched" if left_normalized == right_normalized else "conflict"
        )
        seam = faster_recognition._SeamReceipt(
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
            status=status,
        )
        seams.append(seam)
        if status == "conflict":
            addressed = {
                "namespace": "podcast-subtitle-v2/faster-seam-conflict/v1",
                "seam": seam,
            }
            conflicts.append(
                FasterSeamConflict(
                    id="faster-seam-conflict-" + hash_object(addressed),
                    seam_sample=seam.seam_sample,
                    seam_ms=milliseconds(seam.seam_sample),
                    comparison_start_sample=seam.comparison_start_sample,
                    comparison_end_sample=seam.comparison_end_sample,
                    comparison_start_ms=milliseconds(seam.comparison_start_sample),
                    comparison_end_ms=milliseconds(seam.comparison_end_sample),
                    left_chunk_id=seam.left_chunk_id,
                    right_chunk_id=seam.right_chunk_id,
                    left_text=seam.left_text,
                    right_text=seam.right_text,
                    left_normalized=seam.left_normalized,
                    right_normalized=seam.right_normalized,
                )
            )
    if len(seams) != max(0, len(chunks) - 1):
        raise AdapterIntegrityError("Faster secondary recovery seam inventory is incomplete")
    return tuple(seams), tuple(conflicts)


def _merge_faster_owned_words(
    adapter: FasterWhisperRecognizerAdapter,
    *,
    chunks: tuple[faster_recognition._ChunkReceipt, ...],
    sample_rate_hz: int,
) -> tuple[faster_recognition._MergedWord, ...]:
    merged: list[faster_recognition._MergedWord] = []
    for chunk in chunks:
        for word in adapter._words_in_range(
            chunk,
            start_sample=chunk.owned_start_sample,
            end_sample=chunk.owned_end_sample,
        ):
            merged.append(
                faster_recognition._MergedWord(
                    index=len(merged),
                    word=word.word,
                    start=word.global_start_sample / sample_rate_hz,
                    end=word.global_end_sample / sample_rate_hz,
                    probability=word.probability,
                    source_chunk_id=chunk.id,
                    source_word_index=word.index,
                    ownership="word_midpoint_in_half_open_owned_interval_v1",
                )
            )
    if not merged:
        raise AdapterIntegrityError(
            "Faster secondary recovery ownership produced no Recognition words"
        )
    return tuple(merged)


def _repair_faster_owned_range_overlaps(
    words: tuple[faster_recognition._MergedWord, ...],
    *,
    chunks: tuple[faster_recognition._ChunkReceipt, ...],
    seams: tuple[faster_recognition._SeamReceipt, ...],
    sample_rate_hz: int,
) -> tuple[
    tuple[faster_recognition._MergedWord, ...],
    tuple[FasterOwnedRangeRepair, ...],
]:
    """Clamp only 1..40 ms distinct-text spills in exact shared context."""

    chunk_indices = {chunk.id: chunk.index for chunk in chunks}
    if len(chunk_indices) != len(chunks):
        raise AdapterIntegrityError("Faster secondary recovery chunk IDs are duplicated")
    seam_by_chunks = {
        (seam.left_chunk_id, seam.right_chunk_id): seam for seam in seams
    }
    if len(seam_by_chunks) != len(seams):
        raise AdapterIntegrityError("Faster secondary recovery seam IDs are ambiguous")
    original_text = "".join(word.word for word in words)
    repaired = list(words)
    repairs: list[FasterOwnedRangeRepair] = []
    for index in range(1, len(repaired)):
        left = repaired[index - 1]
        right = repaired[index]
        left_end_sample = round(left.end * sample_rate_hz)
        right_start_sample = round(right.start * sample_rate_hz)
        if right_start_sample >= left_end_sample:
            continue
        left_chunk_index = chunk_indices.get(left.source_chunk_id)
        right_chunk_index = chunk_indices.get(right.source_chunk_id)
        if (
            left_chunk_index is None
            or right_chunk_index is None
            or right_chunk_index != left_chunk_index + 1
        ):
            raise AdapterIntegrityError(
                "Faster owned-range overlap is not across adjacent source chunks"
            )
        seam = seam_by_chunks.get((left.source_chunk_id, right.source_chunk_id))
        if seam is None:
            raise AdapterIntegrityError(
                "Faster owned-range overlap has no exact shared-context seam"
            )
        left_start_sample = round(left.start * sample_rate_hz)
        right_end_sample = round(right.end * sample_rate_hz)
        if not (
            seam.comparison_start_sample
            <= left_start_sample
            < left_end_sample
            <= seam.comparison_end_sample
            and seam.comparison_start_sample
            <= right_start_sample
            < right_end_sample
            <= seam.comparison_end_sample
        ):
            raise AdapterIntegrityError(
                "Faster owned-range overlap escapes its shared comparison interval"
            )
        left_normalized = FasterWhisperRecognizerAdapter._seam_text(left.word)
        right_normalized = FasterWhisperRecognizerAdapter._seam_text(right.word)
        if (
            not left_normalized
            or not right_normalized
            or left_normalized == right_normalized
        ):
            raise AdapterIntegrityError(
                "Faster owned-range clamp requires distinct nonempty normalized text"
            )
        overlap_samples = left_end_sample - right_start_sample
        if not (
            sample_rate_hz
            <= overlap_samples * 1_000
            <= _MAX_FASTER_DISTINCT_TEXT_CLAMP_MS * sample_rate_hz
        ):
            raise AdapterIntegrityError(
                "Faster owned-range overlap is outside the 1-40 ms clamp limit"
            )
        if right_start_sample <= left_start_sample:
            raise AdapterIntegrityError(
                "Faster owned-range clamp would make the left word non-positive"
            )
        clamped_left = left.model_copy(update={"end": right.start})
        before_tokens = (
            _faster_owned_word_snapshot(
                left,
                chunk_indices=chunk_indices,
                sample_rate_hz=sample_rate_hz,
            ),
            _faster_owned_word_snapshot(
                right,
                chunk_indices=chunk_indices,
                sample_rate_hz=sample_rate_hz,
            ),
        )
        after_tokens = (
            _faster_owned_word_snapshot(
                clamped_left,
                chunk_indices=chunk_indices,
                sample_rate_hz=sample_rate_hz,
            ),
            before_tokens[1],
        )
        overlap_ms = round(overlap_samples * 1_000 / sample_rate_hz)
        addressed = {
            "namespace": "podcast-subtitle-v2/faster-owned-range-repair/v1",
            "seam_id": seam.id,
            "reason": "clamp_left_end_cross_chunk_timestamp_spill",
            "overlap_samples": overlap_samples,
            "overlap_ms": overlap_ms,
            "before_tokens": before_tokens,
            "after_tokens": after_tokens,
        }
        repairs.append(
            FasterOwnedRangeRepair(
                id="faster-owned-range-repair-" + hash_object(addressed),
                seam_id=seam.id,
                reason="clamp_left_end_cross_chunk_timestamp_spill",
                overlap_samples=overlap_samples,
                overlap_ms=overlap_ms,
                before_tokens=before_tokens,
                after_tokens=after_tokens,
            )
        )
        repaired[index - 1] = clamped_left

    if "".join(word.word for word in repaired) != original_text:
        raise AdapterIntegrityError("Faster owned-range timing repair changed text")
    for left, right in zip(repaired, repaired[1:]):
        if left.end <= left.start or right.start < left.end:
            raise AdapterIntegrityError(
                "Faster recovered owned words are not positive and monotonic"
            )
    return tuple(repaired), tuple(repairs)


def _digest_payload(payload: bytes) -> dict[str, object]:
    return {"sha256": sha256_bytes(payload), "size_bytes": len(payload)}


def _recover_faster_segment_boundaries(
    adapter: FasterWhisperRecognizerAdapter,
    *,
    request: RecognitionRequest,
) -> tuple[
    RecognitionEvidence,
    tuple[FasterSegmentBoundaryRepair, ...],
    tuple[FasterSeamConflict, ...],
    tuple[FasterOwnedRangeRepair, ...],
]:
    """Assemble corroborating Evidence from one complete authenticated checkpoint."""

    repository = adapter._recognition_run_repository
    if repository is None:
        raise AdapterIntegrityError(
            "Faster boundary recovery requires an authenticated checkpoint repository"
        )
    plan, audio, adapter_chunks = adapter._build_recognition_run_plan(request=request)
    audio_path = Path(request.normalized_audio)
    with repository.execution_lease(plan.run_key):
        finalization = repository.load_finalization(plan=plan)
        if finalization is not None:
            raise AdapterIntegrityError(
                "Faster boundary recovery refuses a terminal Recognition run"
            )
        state = repository.load_state(
            plan.run_key,
            expected_plan=plan,
            expected_adapter_identity=plan.adapter_identity,
        )
        replay = repository.replay_prefix(plan=plan)
        if (
            state.completed_prefix != len(adapter_chunks)
            or len(replay) != len(adapter_chunks)
        ):
            raise AdapterIntegrityError(
                "Faster boundary recovery requires the complete durable chunk prefix"
            )

        provider_outputs: list[dict[str, object]] = []
        segment_repairs: list[FasterSegmentBoundaryRepair] = []
        for repository_chunk, adapter_chunk, replayed in zip(
            plan.chunks,
            adapter_chunks,
            replay,
        ):
            copied, chunk_repairs = _repair_faster_provider_observation(
                adapter,
                observation_bytes=replayed.adapter_observation_bytes,
                repository_chunk_id=repository_chunk.id,
                adapter_chunk=adapter_chunk,
                sample_rate_hz=audio.sample_rate_hz,
            )
            provider_outputs.append(copied)
            segment_repairs.extend(chunk_repairs)
        if not segment_repairs:
            raise AdapterIntegrityError(
                "Faster boundary recovery found no eligible segment-start violation"
            )

        chunks = _validated_faster_chunk_receipts(
            adapter,
            audio_path=audio_path,
            audio=audio,
            repository_chunks=plan.chunks,
            adapter_chunks=adapter_chunks,
            provider_outputs=tuple(provider_outputs),
        )
        languages = {chunk.provider_output.language for chunk in chunks}
        if len(languages) != 1:
            raise AdapterIntegrityError(
                "Faster secondary recovery chunks disagree on provider language"
            )
        seams, seam_conflicts = _faster_seams_and_conflicts(
            adapter,
            chunks=chunks,
            sample_rate_hz=audio.sample_rate_hz,
        )
        owned_words = _merge_faster_owned_words(
            adapter,
            chunks=chunks,
            sample_rate_hz=audio.sample_rate_hz,
        )
        recovered_words, range_repairs = _repair_faster_owned_range_overlaps(
            owned_words,
            chunks=chunks,
            seams=seams,
            sample_rate_hz=audio.sample_rate_hz,
        )

        plan_bytes = canonical_json_bytes(plan)
        checkpoint_bytes = canonical_json_bytes(state.checkpoint)
        head_bytes = canonical_json_bytes(state.head)
        recovery_envelope = {
            "schema_version": 2,
            "kind": "faster_whisper_secondary_recovery",
            "recovery_namespace": _FASTER_SEGMENT_BOUNDARY_RECOVERY_NAMESPACE,
            "recovery_code_hash": hash_file(Path(__file__)),
            "recognition_run_id": plan.id,
            "recognition_run_key": plan.run_key,
            "plan_id": plan.id,
            "plan_record": _digest_payload(plan_bytes),
            "chunk_plan_hash": plan.chunk_plan_hash,
            "checkpoint_id": state.checkpoint.id,
            "checkpoint_record": _digest_payload(checkpoint_bytes),
            "head_id": state.head.id,
            "head_record": _digest_payload(head_bytes),
            "audio_binding_hash": audio.content_hash,
            "checkpoint_observations": [
                {
                    "chunk_id": item.receipt.chunk_id,
                    "chunk_index": item.receipt.chunk_index,
                    "receipt_id": item.receipt.id,
                    "sha256": item.receipt.adapter_observation.sha256,
                    "size_bytes": item.receipt.adapter_observation.size_bytes,
                }
                for item in replay
            ],
            "validated_chunks": chunks,
            "matched_seam_ids": tuple(
                seam.id for seam in seams if seam.status == "matched"
            ),
            "seam_conflicts": tuple(
                _faster_seam_conflict_payload(item) for item in seam_conflicts
            ),
            "segment_boundary_repairs": tuple(
                _faster_repair_payload(item) for item in segment_repairs
            ),
            "owned_range_repairs": tuple(
                _faster_owned_range_repair_payload(item) for item in range_repairs
            ),
            "recovered_words": tuple(
                {
                    "index": word.index,
                    "word": word.word,
                    "start": word.start,
                    "end": word.end,
                    "probability": word.probability,
                    "source_chunk_id": word.source_chunk_id,
                    "source_word_index": word.source_word_index,
                    "ownership": word.ownership,
                }
                for word in recovered_words
            ),
            "language": next(iter(languages)),
        }
        expected_recovery = json.loads(canonical_json_bytes(recovery_envelope))
        raw_output = qwen_recognition._persist_raw_output(
            request=request,
            adapter_stem="faster-whisper-secondary-recovery",
            raw=expected_recovery,
        )
        raw_path = qwen_recognition._file_uri_path_for_adapter(raw_output.uri)
        raw_bytes = raw_path.read_bytes()
        try:
            replayed_recovery = json.loads(raw_bytes)
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise AdapterIntegrityError(
                "Faster secondary recovery raw envelope is not JSON"
            ) from exc
        if (
            canonical_json_bytes(replayed_recovery) != raw_bytes
            or replayed_recovery != expected_recovery
        ):
            raise AdapterIntegrityError(
                "Faster secondary recovery raw envelope does not replay exactly"
            )
        recovered_payload = replayed_recovery.get("recovered_words")
        if not isinstance(recovered_payload, list) or not recovered_payload:
            raise AdapterIntegrityError(
                "Faster secondary recovery has no replayed owned words"
            )
        if any(not isinstance(item, dict) for item in recovered_payload):
            raise AdapterIntegrityError(
                "Faster secondary recovery replayed word is not a mapping"
            )
        evidence = faster_recognition._evidence_from_payload(
            {
                "language": replayed_recovery.get("language"),
                "words": [
                    {
                        "word": item.get("word"),
                        "start": item.get("start"),
                        "end": item.get("end"),
                        "probability": item.get("probability"),
                    }
                    for item in recovered_payload
                    if isinstance(item, dict)
                ],
            },
            request=request,
            raw_output=raw_output,
            adapter=adapter.ADAPTER_NAME,
            model=f"{adapter._model}@{adapter._model_revision}",
            config=adapter._config(),
            timestamp_unit="seconds",
            expected_normalized_audio_size_bytes=audio.normalized_audio.size_bytes,
            expected_normalized_audio_hash=audio.normalized_audio.sha256,
        )
        return (
            evidence,
            tuple(segment_repairs),
            seam_conflicts,
            range_repairs,
        )


def _recognize_corroborating(
    recognizer: Recognizer,
    *,
    request: RecognitionRequest,
) -> tuple[
    RecognitionEvidence,
    tuple[FasterSegmentBoundaryRepair, ...],
    tuple[FasterSeamConflict, ...],
    tuple[FasterOwnedRangeRepair, ...],
]:
    try:
        return recognizer.recognize(request), (), (), ()
    except AdapterInputError:
        if not isinstance(recognizer, FasterWhisperRecognizerAdapter):
            raise
        return _recover_faster_segment_boundaries(recognizer, request=request)


def _publish_evidence(
    root: Path,
    *,
    role: str,
    evidence: RecognitionEvidence,
) -> Path:
    payload = canonical_json_bytes(evidence)
    digest = sha256_bytes(payload)
    path = root / "evidence" / f"{role}-{digest}.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        if not path.is_file() or path.read_bytes() != payload:
            raise ValueError(f"existing {role} Evidence artifact is corrupt: {path}")
        return path

    temporary_path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            dir=path.parent,
            prefix=f".{path.name}.",
            suffix=".tmp",
            delete=False,
        ) as temporary:
            temporary_path = Path(temporary.name)
            temporary.write(payload)
            temporary.flush()
            os.fsync(temporary.fileno())
        os.replace(temporary_path, path)
        temporary_path = None
    finally:
        if temporary_path is not None:
            temporary_path.unlink(missing_ok=True)
    return path


def run_accurate_recognition(
    *,
    audio: Path,
    output_dir: Path,
    episode_id: str,
    invocation_id: str | None = None,
    primary_recognizer: Recognizer | None = None,
    corroborating_recognizer: Recognizer | None = None,
) -> AccurateRecognitionResult:
    """Run Qwen then Faster-Whisper against one already-normalized PCM WAV.

    Supplying both recognizers is supported for deterministic fixture tests.
    Production callers omit both and receive the pinned local-only Adapters.
    """

    episode_id = _required_identity(episode_id, label="episode_id")
    audio_path = Path(audio).resolve()
    root = Path(output_dir).resolve()
    root.mkdir(parents=True, exist_ok=True)

    audio_binding = build_recognition_audio_binding(audio_path)
    audio_sha256 = audio_binding.normalized_audio.sha256
    effective_invocation = (
        stable_recognition_invocation_id(
            episode_id=episode_id,
            audio_sha256=audio_sha256,
        )
        if invocation_id is None
        else _required_identity(invocation_id, label="invocation_id")
    )

    supplied = (primary_recognizer is not None, corroborating_recognizer is not None)
    if supplied == (False, False):
        primary_recognizer, corroborating_recognizer = build_accurate_recognizers(root)
    elif supplied[0] != supplied[1]:
        raise ValueError("primary and corroborating recognizers must be supplied together")
    assert primary_recognizer is not None
    assert corroborating_recognizer is not None

    primary_request = _request_for_role(
        root=root,
        role="primary",
        episode_id=episode_id,
        invocation_id=effective_invocation,
        audio_path=audio_path,
        audio_sha256=audio_sha256,
    )
    corroborating_request = _request_for_role(
        root=root,
        role="corroborating",
        episode_id=episode_id,
        invocation_id=effective_invocation,
        audio_path=audio_path,
        audio_sha256=audio_sha256,
    )

    # These calls must remain sequential.  In particular, do not submit them to
    # an executor: both production models require most of the same 16 GB GPU.
    primary_evidence, primary_seam_conflicts, primary_owned_range_repairs = (
        _recognize_primary(
            primary_recognizer,
            request=primary_request,
        )
    )
    _validate_evidence(
        primary_evidence,
        request=primary_request,
        audio_sha256=audio_sha256,
        role="primary",
    )
    primary_path = _publish_evidence(root, role="primary", evidence=primary_evidence)
    _release_primary_gpu_memory()

    (
        corroborating_evidence,
        corroborating_segment_boundary_repairs,
        corroborating_seam_conflicts,
        corroborating_owned_range_repairs,
    ) = _recognize_corroborating(
        corroborating_recognizer,
        request=corroborating_request,
    )
    _validate_evidence(
        corroborating_evidence,
        request=corroborating_request,
        audio_sha256=audio_sha256,
        role="corroborating",
    )
    corroborating_path = _publish_evidence(
        root,
        role="corroborating",
        evidence=corroborating_evidence,
    )

    return AccurateRecognitionResult(
        episode_id=episode_id,
        invocation_id=effective_invocation,
        audio_sha256=audio_sha256,
        primary_evidence=primary_evidence,
        corroborating_evidence=corroborating_evidence,
        primary_evidence_path=primary_path,
        corroborating_evidence_path=corroborating_path,
        primary_seam_conflicts=primary_seam_conflicts,
        primary_owned_range_repairs=primary_owned_range_repairs,
        corroborating_segment_boundary_repairs=(
            corroborating_segment_boundary_repairs
        ),
        corroborating_seam_conflicts=corroborating_seam_conflicts,
        corroborating_owned_range_repairs=corroborating_owned_range_repairs,
    )
