"""Replayable execution topology for Podcast Subtitle V2 correction quality.

The planner assigns every frozen :class:`AuditPlan` cell exactly once per
modality.  It creates no provider request, response, proposal, disposition, or
semantic-recall claim.  All JSON bindings name canonical serialized bytes;
normalized audio and derived clips name their exact media bytes.
"""

from __future__ import annotations

import io
import stat
import wave
from bisect import bisect_left, bisect_right
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from types import MappingProxyType
from typing import Sequence

from shared.schemas.podcast_subtitles_v2 import (
    AuditCell,
    AuditPlan,
    BoundaryAuditTarget,
    CandidateGroup,
    CandidateGroupSet,
    CandidateSignal,
    CandidateSignalSet,
    CanonicalSpan,
    CanonicalToken,
    CanonicalTranscript,
    EvidenceToken,
    RecognitionEvidence,
    RecognitionSeamEvidence,
    ReferenceEvidence,
    ReferenceRetrievalReceipt,
    SpanAuditTarget,
    recognition_evidence_content_hash,
    recognition_evidence_set_hash,
)
from shared.schemas.podcast_subtitles_v2_audio_selection import AudioAuditSelectionPlanV1
from shared.schemas.podcast_subtitles_v2_correction import (
    CorrectionAuditExecutionLimitsV2,
    CorrectionAuditExecutionPlanV2,
    CorrectionAuditExecutionPolicyV2,
    CorrectionAuditPacketV2,
    CorrectionAuditPlanSliceV2,
    CorrectionCandidateGroupSliceV2,
    CorrectionCandidateSignalSliceV2,
    CorrectionCanonicalTranscriptSliceV2,
    CorrectionPacketReferenceLocatorPartV2,
    CorrectionPacketReferenceLocatorV2,
    CorrectionRecognitionEvidenceItemSliceV2,
    CorrectionRecognitionEvidenceSliceV2,
    CorrectionRecognitionSeamSliceV2,
    CorrectionReferenceExcerptSliceV2,
    CorrectionSourceBindingV2,
    CorrectionSourceKindV2,
    CorrectionSplitReasonV2,
)
from shared.schemas.podcast_subtitles_v2_selective_audio import (
    ModalityAuditExecutionPlanV3,
)

from .candidate_generation import (
    candidate_group_set_digest,
    candidate_signal_set_digest,
)
from .hashing import canonical_json_bytes, hash_file, hash_object, sha256_bytes
from .profiles import DEFAULT_CORRECTION_AUDIT_EXECUTION

CORRECTION_EXECUTION_PLANNER_ID = "nakama-correction-audit-execution-planner"
CORRECTION_EXECUTION_PLANNER_VERSION = 2

_SOURCE_ORDER: tuple[CorrectionSourceKindV2, ...] = (
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
_REASON_ORDER: tuple[CorrectionSplitReasonV2, ...] = (
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


class CorrectionExecutionError(ValueError):
    """Execution topology or an exact input artifact failed closed."""


@dataclass(frozen=True, slots=True)
class PcmWavTopology:
    channels: int
    sample_width_bytes: int
    sample_rate_hz: int
    frame_count: int
    duration_ms: int


@dataclass(frozen=True, slots=True)
class _AudioFileSnapshot:
    size_bytes: int
    modified_ns: int
    device: int
    inode: int


@dataclass(frozen=True, slots=True)
class _ArtifactIdentities:
    audit_plan_hash: str
    candidate_signal_set_hash: str
    candidate_group_set_hash: str
    canonical_transcript_hash: str
    recognition_evidence_set_hash: str
    reference_retrieval_receipt_set_hash: str
    seam_evidence_hash: str | None


@dataclass(frozen=True, slots=True)
class _OwnedUnit:
    span_index: int
    cell_ids: tuple[str, ...]
    requested_cell_ids: tuple[str, ...]
    target_ids: tuple[str, ...]
    span_id: str
    token_ids: tuple[str, ...]
    window_start_ms: int
    window_end_ms: int


@dataclass(frozen=True, slots=True)
class _PacketGroup:
    span_indices: tuple[int, ...]
    split_before: tuple[CorrectionSplitReasonV2, ...]
    split_after: tuple[CorrectionSplitReasonV2, ...]


@dataclass(frozen=True, slots=True)
class _RecognitionIntervalIndex:
    source_hash: str
    tokens: tuple[EvidenceToken, ...]
    starts: tuple[int, ...]
    ends: tuple[int, ...]

    def overlapping(
        self,
        intervals: Sequence[tuple[int, int]],
    ) -> tuple[EvidenceToken, ...]:
        ranges: list[tuple[int, int]] = []
        for interval_start, interval_end in intervals:
            left = bisect_right(self.ends, interval_start)
            right = bisect_left(self.starts, interval_end)
            if left >= right:
                continue
            if ranges and left <= ranges[-1][1]:
                ranges[-1] = (ranges[-1][0], max(ranges[-1][1], right))
            else:
                ranges.append((left, right))
        return tuple(self.tokens[index] for left, right in ranges for index in range(left, right))


@dataclass(frozen=True, slots=True)
class _BindingIndices:
    cell_by_id: Mapping[str, AuditCell]
    cell_order: Mapping[str, int]
    span_target_by_id: Mapping[str, SpanAuditTarget]
    span_target_order: Mapping[str, int]
    boundary_target_by_id: Mapping[str, BoundaryAuditTarget]
    boundary_target_order: Mapping[str, int]
    span_by_id: Mapping[str, CanonicalSpan]
    span_order: Mapping[str, int]
    token_by_id: Mapping[str, CanonicalToken]
    token_order: Mapping[str, int]
    signal_by_id: Mapping[str, CandidateSignal]
    signal_order: Mapping[str, int]
    signals_by_cell_id: Mapping[str, tuple[str, ...]]
    group_by_id: Mapping[str, CandidateGroup]
    group_order: Mapping[str, int]
    groups_by_signal_id: Mapping[str, tuple[str, ...]]
    groups_by_span_id: Mapping[str, tuple[str, ...]]
    retrieval_by_span_id: Mapping[str, ReferenceRetrievalReceipt]
    retrieval_order: Mapping[str, int]
    recognitions: tuple[_RecognitionIntervalIndex, ...]


def correction_execution_planner_code_hash() -> str:
    return hash_file(Path(__file__))


def default_correction_audit_execution_policy() -> CorrectionAuditExecutionPolicyV2:
    """Build defaults with the measured planner code identity in the preimage."""

    defaults = DEFAULT_CORRECTION_AUDIT_EXECUTION
    text_limits = CorrectionAuditExecutionLimitsV2(
        modality="text",
        max_cells_per_packet=defaults.text_max_cells_per_packet,
        max_spans_per_packet=defaults.text_max_spans_per_packet,
        max_tokens_per_packet=defaults.text_max_tokens_per_packet,
        max_window_duration_ms=defaults.text_max_window_duration_ms,
        context_halo_spans_per_side=defaults.context_halo_spans_per_side,
        clip_padding_ms=0,
        max_clip_duration_ms=None,
    )
    audio_limits = CorrectionAuditExecutionLimitsV2(
        modality="audio",
        max_cells_per_packet=defaults.audio_max_cells_per_packet,
        max_spans_per_packet=defaults.audio_max_spans_per_packet,
        max_tokens_per_packet=defaults.audio_max_tokens_per_packet,
        max_window_duration_ms=defaults.audio_max_window_duration_ms,
        context_halo_spans_per_side=defaults.context_halo_spans_per_side,
        clip_padding_ms=defaults.audio_clip_padding_ms,
        max_clip_duration_ms=defaults.audio_max_clip_duration_ms,
    )
    payload = {
        "schema_version": 2,
        "policy_id": "nakama-correction-audit-execution",
        "policy_version": 2,
        "modalities": ("text", "audio"),
        "text": text_limits,
        "audio": audio_limits,
        "boundary_ownership": "right_span_then_final_left_v1",
        "split_on_known_speaker_transition": True,
        "split_on_recognition_seam": True,
        "context_selection_algorithm": "bounded-nearest-bilateral-v1",
        "audio_clip_derivation": "pcm-wav-frame-exact-v1",
        "unsplittable_target_mode": "fail_closed",
        "planner_id": CORRECTION_EXECUTION_PLANNER_ID,
        "planner_version": CORRECTION_EXECUTION_PLANNER_VERSION,
        "planner_code_hash": correction_execution_planner_code_hash(),
    }
    return CorrectionAuditExecutionPolicyV2(
        **payload,
        content_hash=hash_object(payload),
    )


def _audio_file_snapshot(path: Path) -> _AudioFileSnapshot:
    try:
        link_stat = path.lstat()
        if path.is_symlink() or bool(
            getattr(link_stat, "st_file_attributes", 0)
            & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0)
        ):
            raise CorrectionExecutionError(
                "normalized audio path cannot be a link or reparse point"
            )
        file_stat = path.stat()
    except OSError as exc:
        raise CorrectionExecutionError("normalized audio path is not readable") from exc
    if not path.is_file() or file_stat.st_size < 1:
        raise CorrectionExecutionError("normalized audio path must name a non-empty file")
    return _AudioFileSnapshot(
        size_bytes=file_stat.st_size,
        modified_ns=file_stat.st_mtime_ns,
        device=file_stat.st_dev,
        inode=file_stat.st_ino,
    )


def _probe_pcm_wav(path: Path) -> PcmWavTopology:
    """Read WAV metadata and one terminal frame, never the complete PCM payload."""

    try:
        with wave.open(str(path), "rb") as source:
            if source.getcomptype() != "NONE":
                raise CorrectionExecutionError("normalized WAV must use uncompressed PCM")
            channels = source.getnchannels()
            sample_width = source.getsampwidth()
            sample_rate = source.getframerate()
            frame_count = source.getnframes()
            if channels < 1 or sample_width < 1 or sample_rate < 1 or frame_count < 1:
                raise CorrectionExecutionError("normalized WAV topology must be positive")
            source.setpos(frame_count - 1)
            terminal_frame = source.readframes(1)
            if source.readframes(1):
                raise CorrectionExecutionError("normalized WAV frame count is inconsistent")
    except (EOFError, OSError, wave.Error) as exc:
        raise CorrectionExecutionError("normalized audio is not a valid PCM WAV") from exc
    if len(terminal_frame) != channels * sample_width:
        raise CorrectionExecutionError("normalized WAV PCM frame bytes are truncated")
    duration_ms = (frame_count * 1_000 + sample_rate - 1) // sample_rate
    return PcmWavTopology(
        channels=channels,
        sample_width_bytes=sample_width,
        sample_rate_hz=sample_rate,
        frame_count=frame_count,
        duration_ms=duration_ms,
    )


def derive_pcm_wav_clip_bytes(
    normalized_audio_path: str | Path,
    *,
    clip_start_ms: int,
    clip_end_ms: int,
    topology: PcmWavTopology | None = None,
) -> tuple[bytes, int, int]:
    """Seek/read only the exact PCM frame interval and wrap it as a WAV clip."""

    path = Path(normalized_audio_path)
    topology = topology or _probe_pcm_wav(path)
    if type(clip_start_ms) is not int or type(clip_end_ms) is not int:
        raise CorrectionExecutionError("clip millisecond bounds require exact integers")
    if clip_start_ms < 0 or clip_end_ms <= clip_start_ms:
        raise CorrectionExecutionError("clip bounds must have positive duration")
    if clip_end_ms > topology.duration_ms:
        raise CorrectionExecutionError("clip end exceeds normalized audio duration")
    start_frame = (clip_start_ms * topology.sample_rate_hz) // 1_000
    end_frame = min(
        topology.frame_count,
        (clip_end_ms * topology.sample_rate_hz + 999) // 1_000,
    )
    if end_frame <= start_frame:
        raise CorrectionExecutionError("clip bounds contain no PCM frames")
    frame_count = end_frame - start_frame
    try:
        with wave.open(str(path), "rb") as source:
            if (
                source.getcomptype() != "NONE"
                or source.getnchannels() != topology.channels
                or source.getsampwidth() != topology.sample_width_bytes
                or source.getframerate() != topology.sample_rate_hz
                or source.getnframes() != topology.frame_count
            ):
                raise CorrectionExecutionError("normalized WAV topology changed during build")
            source.setpos(start_frame)
            selected = source.readframes(frame_count)
    except (EOFError, OSError, wave.Error) as exc:
        raise CorrectionExecutionError("normalized WAV clip source is unreadable") from exc
    expected_size = frame_count * topology.channels * topology.sample_width_bytes
    if len(selected) != expected_size:
        raise CorrectionExecutionError("normalized WAV clip frame bytes are truncated")
    output = io.BytesIO()
    with wave.open(output, "wb") as target:
        target.setnchannels(topology.channels)
        target.setsampwidth(topology.sample_width_bytes)
        target.setframerate(topology.sample_rate_hz)
        target.setcomptype("NONE", "not compressed")
        target.writeframes(selected)
    return output.getvalue(), start_frame, end_frame


def _json_binding(
    *,
    kind: CorrectionSourceKindV2,
    source_id: str,
    value: object,
    internal_object_hash: str,
    record_ids: Sequence[str],
    artifact_scope: str = "inputs",
    artifact_id: str | None = None,
    artifact_sink: dict[str, bytes] | None = None,
) -> CorrectionSourceBindingV2:
    exact_bytes = canonical_json_bytes(value)
    payload = {
        "schema_version": 2,
        "kind": kind,
        "id": source_id,
        "content_hash": sha256_bytes(exact_bytes),
        "record_ids": tuple(sorted(record_ids)),
        "artifact_uri": (
            f"execution-artifact://{artifact_scope}/{kind}/{artifact_id or source_id}.json"
        ),
        "size_bytes": len(exact_bytes),
        "media_type": "application/json",
        "internal_object_hash": internal_object_hash,
    }
    binding = CorrectionSourceBindingV2(
        **payload,
        binding_hash=hash_object(payload),
    )
    if artifact_sink is not None:
        previous = artifact_sink.setdefault(binding.artifact_uri, exact_bytes)
        if previous != exact_bytes:
            raise CorrectionExecutionError(
                "packet input URI resolves to conflicting canonical bytes"
            )
    return binding


def _audio_binding(
    *,
    kind: CorrectionSourceKindV2,
    source_id: str,
    exact_bytes: bytes,
    record_ids: Sequence[str],
    artifact_sink: dict[str, bytes] | None = None,
) -> CorrectionSourceBindingV2:
    content_hash = sha256_bytes(exact_bytes)
    payload = {
        "schema_version": 2,
        "kind": kind,
        "id": source_id,
        "content_hash": content_hash,
        "record_ids": tuple(sorted(record_ids)),
        "artifact_uri": f"execution-artifact://audio/{kind}/{content_hash}.wav",
        "size_bytes": len(exact_bytes),
        "media_type": "audio/wav",
        "internal_object_hash": None,
    }
    binding = CorrectionSourceBindingV2(
        **payload,
        binding_hash=hash_object(payload),
    )
    if artifact_sink is not None:
        previous = artifact_sink.setdefault(binding.artifact_uri, exact_bytes)
        if previous != exact_bytes:
            raise CorrectionExecutionError("audio artifact URI resolves to conflicting exact bytes")
    return binding


def _normalized_audio_binding(
    *,
    content_hash: str,
    size_bytes: int,
) -> CorrectionSourceBindingV2:
    """Bind the streamed normalized source without materialising it as bytes."""

    payload = {
        "schema_version": 2,
        "kind": "normalized_audio",
        "id": content_hash,
        "content_hash": content_hash,
        "record_ids": (content_hash,),
        "artifact_uri": f"execution-artifact://audio/normalized_audio/{content_hash}.wav",
        "size_bytes": size_bytes,
        "media_type": "audio/wav",
        "internal_object_hash": None,
    }
    return CorrectionSourceBindingV2(
        **payload,
        binding_hash=hash_object(payload),
    )


def _ordered_bindings(
    bindings: Sequence[CorrectionSourceBindingV2],
) -> tuple[CorrectionSourceBindingV2, ...]:
    source_order = {kind: index for index, kind in enumerate(_SOURCE_ORDER)}
    return tuple(sorted(bindings, key=lambda item: (source_order[item.kind], item.id)))


def _exact_inputs(
    *,
    plan: AuditPlan,
    signal_set: CandidateSignalSet,
    group_set: CandidateGroupSet,
    transcript: CanonicalTranscript,
    recognition_evidence: Sequence[RecognitionEvidence],
    reference_retrievals: Sequence[ReferenceRetrievalReceipt],
    seam_evidence: RecognitionSeamEvidence | None,
) -> tuple[
    tuple[RecognitionEvidence, ...],
    tuple[ReferenceRetrievalReceipt, ...],
]:
    if plan.episode_id != transcript.episode_id or plan.generation_id != transcript.generation_id:
        raise CorrectionExecutionError("AuditPlan differs from Canonical generation")
    canonical_hash = sha256_bytes(canonical_json_bytes(transcript))
    if (
        plan.inputs.canonical_transcript_hash != canonical_hash
        or plan.inputs.canonical_content_hash != transcript.content_hash
        or plan.inputs.normalized_audio_hash != transcript.normalized_audio_hash
    ):
        raise CorrectionExecutionError("Canonical artifact differs from AuditPlan inputs")
    audit_plan_hash = sha256_bytes(canonical_json_bytes(plan))
    if (
        signal_set.audit_plan_hash != audit_plan_hash
        or signal_set.audit_plan_content_hash != plan.content_hash
        or signal_set.audit_input_hash != plan.inputs.content_hash
    ):
        raise CorrectionExecutionError("CandidateSignalSet differs from exact AuditPlan")
    signal_hash = candidate_signal_set_digest(signal_set)
    if (
        group_set.signal_set_hash != signal_hash
        or group_set.signal_set_content_hash != signal_set.content_hash
        or group_set.audit_plan_hash != audit_plan_hash
        or group_set.audit_plan_content_hash != plan.content_hash
        or group_set.audit_input_hash != plan.inputs.content_hash
    ):
        raise CorrectionExecutionError("CandidateGroupSet differs from exact signals/plan")

    recognitions = tuple(recognition_evidence)
    recognition_hashes = tuple(recognition_evidence_content_hash(item) for item in recognitions)
    if not recognitions or recognition_hashes != tuple(sorted(recognition_hashes)):
        raise CorrectionExecutionError("Recognition Evidence must use canonical content-hash order")
    if len(set(recognition_hashes)) != len(recognition_hashes):
        raise CorrectionExecutionError("Recognition Evidence contains duplicate content")
    if (
        recognition_hashes != plan.inputs.recognition_evidence_hashes
        or recognition_evidence_set_hash(list(recognitions))
        != plan.inputs.recognition_evidence_set_hash
    ):
        raise CorrectionExecutionError("Recognition Evidence set differs from AuditPlan")
    if any(
        item.episode_id != transcript.episode_id
        or item.normalized_audio_hash != transcript.normalized_audio_hash
        for item in recognitions
    ):
        raise CorrectionExecutionError("Recognition Evidence lineage differs from Canonical truth")

    retrievals = tuple(reference_retrievals)
    span_order = {span.id: index for index, span in enumerate(transcript.spans)}
    if any(item.audio_span_id not in span_order for item in retrievals):
        raise CorrectionExecutionError("Reference receipt targets an unknown Canonical span")
    if tuple(span_order[item.audio_span_id] for item in retrievals) != tuple(
        sorted(span_order[item.audio_span_id] for item in retrievals)
    ):
        raise CorrectionExecutionError("Reference receipts must use Canonical span order")
    if len({item.audio_span_id for item in retrievals}) != len(retrievals):
        raise CorrectionExecutionError("Reference receipts repeat a Canonical span")
    receipt_hashes = tuple(sorted(hash_object(item) for item in retrievals))
    if receipt_hashes != plan.inputs.reference_retrieval_receipt_hashes:
        raise CorrectionExecutionError("Reference receipt set differs from AuditPlan")
    if any(
        item.episode_id != transcript.episode_id
        or item.context.basis_content_hash != transcript.content_hash
        for item in retrievals
    ):
        raise CorrectionExecutionError("Reference receipt lineage differs from Canonical truth")

    if plan.inputs.seam_status == "absent":
        if seam_evidence is not None:
            raise CorrectionExecutionError("AuditPlan without seams cannot receive seam Evidence")
    else:
        if seam_evidence is None:
            raise CorrectionExecutionError("AuditPlan seam input is missing")
        if (
            hash_object(seam_evidence) != plan.inputs.seam_evidence_hash
            or seam_evidence.normalized_audio_hash != transcript.normalized_audio_hash
            or seam_evidence.recognition_evidence_hashes != recognition_hashes
        ):
            raise CorrectionExecutionError("Recognition seam Evidence differs from AuditPlan")

    return recognitions, retrievals


def _prepare_normalized_audio(
    *,
    normalized_audio_path: str | Path,
    transcript: CanonicalTranscript,
    plan: AuditPlan,
) -> tuple[Path, PcmWavTopology, _AudioFileSnapshot, str]:
    candidate = Path(normalized_audio_path)
    # Resolve only after rejecting caller-visible indirection.  Resolving first
    # would erase the fact that a mutable symlink/reparse point was supplied.
    _audio_file_snapshot(candidate)
    try:
        path = candidate.resolve(strict=True)
    except (OSError, TypeError) as exc:
        raise CorrectionExecutionError("normalized audio path cannot be resolved") from exc
    initial = _audio_file_snapshot(path)
    try:
        digest = hash_file(path)
    except OSError as exc:
        raise CorrectionExecutionError("normalized audio cannot be streamed for hashing") from exc
    if _audio_file_snapshot(path) != initial:
        raise CorrectionExecutionError("normalized audio mutated while hashing")
    if digest != transcript.normalized_audio_hash:
        raise CorrectionExecutionError("normalized audio exact bytes differ from Canonical lineage")
    topology = _probe_pcm_wav(path)
    if _audio_file_snapshot(path) != initial:
        raise CorrectionExecutionError("normalized audio mutated while probing WAV topology")
    if any(target.window_end_ms > topology.duration_ms for target in plan.boundary_targets):
        raise CorrectionExecutionError("AuditPlan boundary exceeds exact normalized WAV duration")
    if any(target.end_ms > topology.duration_ms for target in plan.span_targets):
        raise CorrectionExecutionError("AuditPlan span exceeds exact normalized WAV duration")
    return path, topology, initial, digest


def _assert_normalized_audio_unchanged(
    *,
    path: Path,
    initial: _AudioFileSnapshot,
    digest: str,
) -> None:
    if _audio_file_snapshot(path) != initial:
        raise CorrectionExecutionError("normalized audio mutated during execution planning")
    try:
        final_digest = hash_file(path)
    except OSError as exc:
        raise CorrectionExecutionError("normalized audio cannot be reverified") from exc
    if _audio_file_snapshot(path) != initial or final_digest != digest:
        raise CorrectionExecutionError("normalized audio mutated during execution planning")


def _artifact_identities(
    *,
    plan: AuditPlan,
    signal_set: CandidateSignalSet,
    group_set: CandidateGroupSet,
    transcript: CanonicalTranscript,
    recognitions: tuple[RecognitionEvidence, ...],
    retrievals: tuple[ReferenceRetrievalReceipt, ...],
    seam_evidence: RecognitionSeamEvidence | None,
) -> _ArtifactIdentities:
    return _ArtifactIdentities(
        audit_plan_hash=sha256_bytes(canonical_json_bytes(plan)),
        candidate_signal_set_hash=candidate_signal_set_digest(signal_set),
        candidate_group_set_hash=candidate_group_set_digest(group_set),
        canonical_transcript_hash=sha256_bytes(canonical_json_bytes(transcript)),
        recognition_evidence_set_hash=sha256_bytes(canonical_json_bytes(recognitions)),
        reference_retrieval_receipt_set_hash=sha256_bytes(canonical_json_bytes(retrievals)),
        seam_evidence_hash=(
            sha256_bytes(canonical_json_bytes(seam_evidence)) if seam_evidence is not None else None
        ),
    )


def _span_speaker(
    target: SpanAuditTarget,
    token_by_id: Mapping[str, CanonicalToken],
) -> str | None:
    speakers = {token_by_id[token_id].speaker for token_id in target.token_ids}
    if None in speakers or len(speakers) != 1:
        return None
    return next(iter(speakers))


def _seam_topology(
    plan: AuditPlan,
    seam_evidence: RecognitionSeamEvidence | None,
) -> dict[int, tuple[str, ...]]:
    result: dict[int, list[str]] = {}
    if seam_evidence is None or seam_evidence.status == "unchunked":
        return {}
    internal = plan.boundary_targets[1:-1]
    for observation in seam_evidence.observations:
        distances = tuple(
            (
                (
                    target.window_start_ms - observation.seam_ms
                    if observation.seam_ms < target.window_start_ms
                    else (
                        observation.seam_ms - target.window_end_ms
                        if observation.seam_ms > target.window_end_ms
                        else 0
                    )
                ),
                target,
            )
            for target in internal
        )
        if not distances:
            raise CorrectionExecutionError("seam observation has no internal packet boundary")
        nearest_distance = min(item[0] for item in distances)
        nearest = tuple(item for distance, item in distances if distance == nearest_distance)
        if len(nearest) != 1 or nearest_distance > plan.policy.seam_match_tolerance_ms:
            raise CorrectionExecutionError("seam observation has no unique bounded owner")
        result.setdefault(nearest[0].ordinal, []).append(observation.id)
    return {key: tuple(sorted(value)) for key, value in result.items()}


def _owned_units(plan: AuditPlan) -> tuple[_OwnedUnit, ...]:
    cell_order = {cell.id: index for index, cell in enumerate(plan.cells)}
    cells_by_target: dict[str, list[AuditCell]] = {}
    for cell in plan.cells:
        cells_by_target.setdefault(cell.target_id, []).append(cell)
    units: list[_OwnedUnit] = []
    for index, span in enumerate(plan.span_targets):
        boundaries = [plan.boundary_targets[index]]
        if index == len(plan.span_targets) - 1:
            boundaries.append(plan.boundary_targets[-1])
        targets: tuple[SpanAuditTarget | BoundaryAuditTarget, ...] = (span, *boundaries)
        target_ids = {item.id for item in targets}
        cells = tuple(
            sorted(
                (cell for target_id in target_ids for cell in cells_by_target[target_id]),
                key=lambda item: cell_order[item.id],
            )
        )
        units.append(
            _OwnedUnit(
                span_index=index,
                cell_ids=tuple(item.id for item in cells),
                requested_cell_ids=tuple(
                    item.id for item in cells if item.applicability == "required"
                ),
                target_ids=tuple(item.id for item in targets),
                span_id=span.span_id,
                token_ids=span.token_ids,
                window_start_ms=min(
                    item.window_start_ms if isinstance(item, BoundaryAuditTarget) else item.start_ms
                    for item in targets
                ),
                window_end_ms=max(
                    item.window_end_ms if isinstance(item, BoundaryAuditTarget) else item.end_ms
                    for item in targets
                ),
            )
        )
    return tuple(units)


def _violations(
    span_indices: Sequence[int],
    units: Sequence[_OwnedUnit],
    limits: CorrectionAuditExecutionLimitsV2,
) -> tuple[CorrectionSplitReasonV2, ...]:
    selected = tuple(units[index] for index in span_indices)
    cells = {value for item in selected for value in item.cell_ids}
    tokens = {value for item in selected for value in item.token_ids}
    duration_ms = max(item.window_end_ms for item in selected) - min(
        item.window_start_ms for item in selected
    )
    reasons: list[CorrectionSplitReasonV2] = []
    if len(cells) > limits.max_cells_per_packet:
        reasons.append("max_cells")
    if len(selected) > limits.max_spans_per_packet:
        reasons.append("max_spans")
    if len(tokens) > limits.max_tokens_per_packet:
        reasons.append("max_tokens")
    if duration_ms > limits.max_window_duration_ms:
        reasons.append("max_duration")
    return tuple(reasons)


def _mandatory_reasons(
    plan: AuditPlan,
    transcript: CanonicalTranscript,
    seam_by_boundary: dict[int, tuple[str, ...]],
) -> dict[int, tuple[CorrectionSplitReasonV2, ...]]:
    result: dict[int, tuple[CorrectionSplitReasonV2, ...]] = {}
    token_by_id = {item.id: item for item in transcript.tokens}
    for boundary_ordinal in range(1, len(plan.span_targets)):
        reasons: list[CorrectionSplitReasonV2] = []
        left = _span_speaker(plan.span_targets[boundary_ordinal - 1], token_by_id)
        right = _span_speaker(plan.span_targets[boundary_ordinal], token_by_id)
        if left is not None and right is not None and left != right:
            reasons.append("known_speaker_transition")
        if seam_by_boundary.get(boundary_ordinal):
            reasons.append("recognition_seam")
        if reasons:
            result[boundary_ordinal] = tuple(reasons)
    return result


def _packet_groups(
    *,
    units: Sequence[_OwnedUnit],
    limits: CorrectionAuditExecutionLimitsV2,
    mandatory: dict[int, tuple[CorrectionSplitReasonV2, ...]],
) -> tuple[_PacketGroup, ...]:
    if not units:
        raise CorrectionExecutionError("correction execution requires owned span units")
    for unit in units:
        violation = _violations((unit.span_index,), units, limits)
        if violation:
            raise CorrectionExecutionError(
                "unsplittable single audit target exceeds packet cap: " + ",".join(violation)
            )
    chunks: list[tuple[tuple[int, ...], tuple[CorrectionSplitReasonV2, ...]]] = []
    current: list[int] = [0]
    for span_index in range(1, len(units)):
        split = mandatory.get(span_index, ())
        if not split:
            split = _violations((*current, span_index), units, limits)
        if split:
            chunks.append((tuple(current), split))
            current = [span_index]
        else:
            current.append(span_index)
    chunks.append((tuple(current), ("episode_end",)))
    groups: list[_PacketGroup] = []
    before: tuple[CorrectionSplitReasonV2, ...] = ("episode_start",)
    for span_indices, after in chunks:
        groups.append(
            _PacketGroup(
                span_indices=span_indices,
                split_before=before,
                split_after=after,
            )
        )
        before = after
    return tuple(groups)


def _can_cross(
    left_span_index: int,
    right_span_index: int,
    mandatory: dict[int, tuple[CorrectionSplitReasonV2, ...]],
) -> bool:
    return not any(
        boundary in mandatory for boundary in range(left_span_index + 1, right_span_index + 1)
    )


def _context_indices(
    *,
    group: _PacketGroup,
    units: Sequence[_OwnedUnit],
    limits: CorrectionAuditExecutionLimitsV2,
    mandatory: dict[int, tuple[CorrectionSplitReasonV2, ...]],
) -> tuple[int, ...]:
    owned = set(group.span_indices)
    start = group.span_indices[0]
    end = group.span_indices[-1]
    candidates: list[tuple[int, int, int]] = []
    for distance in range(1, limits.context_halo_spans_per_side + 1):
        left = start - distance
        right = end + distance
        if left >= 0 and _can_cross(left, start, mandatory):
            candidates.append((distance, 0, left))
        if right < len(units) and _can_cross(end, right, mandatory):
            candidates.append((distance, 1, right))
    context: list[int] = []
    for _, _, candidate in sorted(candidates):
        visible = tuple(sorted((*owned, *context, candidate)))
        if not _violations(visible, units, limits):
            context.append(candidate)
    return tuple(sorted(context))


def _reference_evidence_by_span(
    retrievals: Sequence[ReferenceRetrievalReceipt],
) -> dict[str, tuple[ReferenceEvidence, ...]]:
    return {item.audio_span_id: item.evidence for item in retrievals}


def _binding_indices(
    *,
    plan: AuditPlan,
    signal_set: CandidateSignalSet,
    group_set: CandidateGroupSet,
    transcript: CanonicalTranscript,
    recognitions: tuple[RecognitionEvidence, ...],
    retrievals: tuple[ReferenceRetrievalReceipt, ...],
) -> _BindingIndices:
    signals_by_cell_id: dict[str, list[str]] = {}
    for signal in signal_set.signals:
        signals_by_cell_id.setdefault(signal.audit_cell_id, []).append(signal.id)
    groups_by_signal_id: dict[str, list[str]] = {}
    groups_by_span_id: dict[str, list[str]] = {}
    for group in group_set.groups:
        for signal_id in group.signal_ids:
            groups_by_signal_id.setdefault(signal_id, []).append(group.id)
        for span_id in group.target_span_ids:
            groups_by_span_id.setdefault(span_id, []).append(group.id)
    return _BindingIndices(
        cell_by_id=MappingProxyType({item.id: item for item in plan.cells}),
        cell_order=MappingProxyType({item.id: index for index, item in enumerate(plan.cells)}),
        span_target_by_id=MappingProxyType({item.id: item for item in plan.span_targets}),
        span_target_order=MappingProxyType(
            {item.id: index for index, item in enumerate(plan.span_targets)}
        ),
        boundary_target_by_id=MappingProxyType({item.id: item for item in plan.boundary_targets}),
        boundary_target_order=MappingProxyType(
            {item.id: index for index, item in enumerate(plan.boundary_targets)}
        ),
        span_by_id=MappingProxyType({item.id: item for item in transcript.spans}),
        span_order=MappingProxyType(
            {item.id: index for index, item in enumerate(transcript.spans)}
        ),
        token_by_id=MappingProxyType({item.id: item for item in transcript.tokens}),
        token_order=MappingProxyType(
            {item.id: index for index, item in enumerate(transcript.tokens)}
        ),
        signal_by_id=MappingProxyType({item.id: item for item in signal_set.signals}),
        signal_order=MappingProxyType(
            {item.id: index for index, item in enumerate(signal_set.signals)}
        ),
        signals_by_cell_id=MappingProxyType(
            {key: tuple(value) for key, value in signals_by_cell_id.items()}
        ),
        group_by_id=MappingProxyType({item.id: item for item in group_set.groups}),
        group_order=MappingProxyType(
            {item.id: index for index, item in enumerate(group_set.groups)}
        ),
        groups_by_signal_id=MappingProxyType(
            {key: tuple(value) for key, value in groups_by_signal_id.items()}
        ),
        groups_by_span_id=MappingProxyType(
            {key: tuple(value) for key, value in groups_by_span_id.items()}
        ),
        retrieval_by_span_id=MappingProxyType({item.audio_span_id: item for item in retrievals}),
        retrieval_order=MappingProxyType(
            {item.audio_span_id: index for index, item in enumerate(retrievals)}
        ),
        recognitions=tuple(
            _RecognitionIntervalIndex(
                source_hash=recognition_evidence_content_hash(recognition),
                tokens=tuple(recognition.tokens),
                starts=tuple(item.start_ms for item in recognition.tokens),
                ends=tuple(item.end_ms for item in recognition.tokens),
            )
            for recognition in recognitions
        ),
    )


def _build_bindings(
    *,
    plan: AuditPlan,
    signal_set: CandidateSignalSet,
    group_set: CandidateGroupSet,
    transcript: CanonicalTranscript,
    recognitions: tuple[RecognitionEvidence, ...],
    retrievals: tuple[ReferenceRetrievalReceipt, ...],
    seam_evidence: RecognitionSeamEvidence | None,
    identities: _ArtifactIdentities,
    normalized_audio_hash: str,
    normalized_audio_size_bytes: int,
    cell_ids: Sequence[str],
    span_ids: Sequence[str],
    token_ids: Sequence[str],
    include_retrieval_lineage: bool,
    seam_ids: Sequence[str] = (),
    clip: tuple[bytes, int, int] | None = None,
    clip_bounds_ms: tuple[int, int] | None = None,
    artifact_sink: dict[str, bytes] | None = None,
    indices: _BindingIndices | None = None,
) -> tuple[CorrectionSourceBindingV2, ...]:
    indices = indices or _binding_indices(
        plan=plan,
        signal_set=signal_set,
        group_set=group_set,
        transcript=transcript,
        recognitions=recognitions,
        retrievals=retrievals,
    )
    cell_set = set(cell_ids)
    span_set = set(span_ids)
    token_set = set(token_ids)
    selected_cells = tuple(
        indices.cell_by_id[item_id]
        for item_id in sorted(cell_set, key=indices.cell_order.__getitem__)
    )
    selected_target_ids = {item.target_id for item in selected_cells}
    selected_span_targets = tuple(
        indices.span_target_by_id[item_id]
        for item_id in sorted(
            selected_target_ids & indices.span_target_by_id.keys(),
            key=indices.span_target_order.__getitem__,
        )
    )
    selected_boundary_targets = tuple(
        indices.boundary_target_by_id[item_id]
        for item_id in sorted(
            selected_target_ids & indices.boundary_target_by_id.keys(),
            key=indices.boundary_target_order.__getitem__,
        )
    )
    for target in selected_boundary_targets:
        if target.left_span_id is not None:
            span_set.add(target.left_span_id)
        if target.right_span_id is not None:
            span_set.add(target.right_span_id)
        if target.left_token_id is not None:
            token_set.add(target.left_token_id)
        if target.right_token_id is not None:
            token_set.add(target.right_token_id)
    selected_spans = tuple(
        indices.span_by_id[item_id]
        for item_id in sorted(span_set, key=indices.span_order.__getitem__)
    )
    token_set.update(token_id for item in selected_spans for token_id in item.token_ids)
    selected_tokens = tuple(
        indices.token_by_id[item_id]
        for item_id in sorted(token_set, key=indices.token_order.__getitem__)
    )
    relevant_signal_id_set = {
        signal_id
        for cell_id in cell_set
        for signal_id in indices.signals_by_cell_id.get(cell_id, ())
    }
    relevant_signals = tuple(
        indices.signal_by_id[item_id]
        for item_id in sorted(relevant_signal_id_set, key=indices.signal_order.__getitem__)
    )
    relevant_signal_ids = tuple(item.id for item in relevant_signals)
    signal_records = relevant_signal_ids or (signal_set.content_hash,)
    relevant_group_id_set = {
        group_id
        for signal_id in relevant_signal_ids
        for group_id in indices.groups_by_signal_id.get(signal_id, ())
    }
    relevant_group_id_set.update(
        group_id for span_id in span_set for group_id in indices.groups_by_span_id.get(span_id, ())
    )
    relevant_groups = tuple(
        indices.group_by_id[item_id]
        for item_id in sorted(relevant_group_id_set, key=indices.group_order.__getitem__)
    )
    group_records = tuple(item.id for item in relevant_groups) or (group_set.content_hash,)
    selected_retrievals = tuple(
        indices.retrieval_by_span_id[span_id]
        for span_id in sorted(
            span_set & indices.retrieval_by_span_id.keys(),
            key=indices.retrieval_order.__getitem__,
        )
    )
    receipt_records = tuple(item.query_id for item in selected_retrievals) or (
        plan.inputs.reference_retrieval_receipt_set_hash,
    )
    evidence: dict[str, ReferenceEvidence] = {}
    evidence_membership: dict[str, list[str]] = {}
    for receipt in selected_retrievals:
        for item in receipt.evidence:
            previous = evidence.setdefault(item.id, item)
            if previous != item:
                raise CorrectionExecutionError(
                    "Reference Evidence ID resolves to conflicting exact records"
                )
            memberships = evidence_membership.setdefault(item.id, [])
            if receipt.audio_span_id not in memberships:
                memberships.append(receipt.audio_span_id)
    if include_retrieval_lineage:
        bindings = [
            _json_binding(
                kind="audit_plan",
                source_id=plan.basis_hash,
                value=plan,
                internal_object_hash=plan.content_hash,
                record_ids=tuple(item.id for item in plan.cells),
                artifact_sink=artifact_sink,
            ),
            _json_binding(
                kind="candidate_signal_set",
                source_id=signal_set.content_hash,
                value=signal_set,
                internal_object_hash=signal_set.content_hash,
                record_ids=tuple(item.id for item in signal_set.signals)
                or (signal_set.content_hash,),
                artifact_sink=artifact_sink,
            ),
            _json_binding(
                kind="candidate_group_set",
                source_id=group_set.content_hash,
                value=group_set,
                internal_object_hash=group_set.content_hash,
                record_ids=tuple(item.id for item in group_set.groups) or (group_set.content_hash,),
                artifact_sink=artifact_sink,
            ),
            _json_binding(
                kind="canonical_transcript",
                source_id=transcript.generation_id,
                value=transcript,
                internal_object_hash=transcript.content_hash,
                record_ids=tuple(
                    sorted(
                        (
                            *[item.id for item in transcript.tokens],
                            *[item.id for item in transcript.spans],
                        )
                    )
                ),
                artifact_sink=artifact_sink,
            ),
            _json_binding(
                kind="recognition_evidence_set",
                source_id=plan.inputs.recognition_evidence_set_hash,
                value=recognitions,
                internal_object_hash=plan.inputs.recognition_evidence_set_hash,
                record_ids=plan.inputs.recognition_evidence_hashes,
                artifact_sink=artifact_sink,
            ),
            _json_binding(
                kind="reference_retrieval_receipt_set",
                source_id=plan.inputs.reference_retrieval_receipt_set_hash,
                value=retrievals,
                internal_object_hash=plan.inputs.reference_retrieval_receipt_set_hash,
                record_ids=receipt_records,
                artifact_sink=artifact_sink,
            ),
            _normalized_audio_binding(
                content_hash=normalized_audio_hash,
                size_bytes=normalized_audio_size_bytes,
            ),
        ]
        if seam_evidence is not None:
            bindings.append(
                _json_binding(
                    kind="recognition_seam_evidence",
                    source_id=seam_evidence.content_hash,
                    value=seam_evidence,
                    internal_object_hash=seam_evidence.content_hash,
                    record_ids=tuple(item.id for item in seam_evidence.observations)
                    or (seam_evidence.content_hash,),
                    artifact_sink=artifact_sink,
                )
            )
    else:
        common = {
            "schema_version": 2,
            "episode_id": transcript.episode_id,
            "generation_id": transcript.generation_id,
        }
        audit_payload = {
            **common,
            "kind": "audit_plan",
            "parent_source_id": plan.basis_hash,
            "parent_artifact_hash": identities.audit_plan_hash,
            "parent_internal_object_hash": plan.content_hash,
            "record_ids": tuple(sorted(item.id for item in selected_cells)),
            "cells": selected_cells,
            "span_targets": selected_span_targets,
            "boundary_targets": selected_boundary_targets,
        }
        audit_slice = CorrectionAuditPlanSliceV2(
            **audit_payload,
            content_hash=hash_object(audit_payload),
        )
        signal_payload = {
            **common,
            "kind": "candidate_signal_set",
            "parent_source_id": signal_set.content_hash,
            "parent_artifact_hash": identities.candidate_signal_set_hash,
            "parent_internal_object_hash": signal_set.content_hash,
            "record_ids": tuple(sorted(signal_records)),
            "signals": relevant_signals,
        }
        signal_slice = CorrectionCandidateSignalSliceV2(
            **signal_payload,
            content_hash=hash_object(signal_payload),
        )
        group_payload = {
            **common,
            "kind": "candidate_group_set",
            "parent_source_id": group_set.content_hash,
            "parent_artifact_hash": identities.candidate_group_set_hash,
            "parent_internal_object_hash": group_set.content_hash,
            "record_ids": tuple(sorted(group_records)),
            "groups": relevant_groups,
        }
        group_slice = CorrectionCandidateGroupSliceV2(
            **group_payload,
            content_hash=hash_object(group_payload),
        )
        canonical_records = tuple(
            sorted((*[item.id for item in selected_tokens], *[item.id for item in selected_spans]))
        )
        canonical_payload = {
            **common,
            "kind": "canonical_transcript",
            "parent_source_id": transcript.generation_id,
            "parent_artifact_hash": identities.canonical_transcript_hash,
            "parent_internal_object_hash": transcript.content_hash,
            "record_ids": canonical_records,
            "tokens": selected_tokens,
            "spans": selected_spans,
        }
        canonical_slice = CorrectionCanonicalTranscriptSliceV2(
            **canonical_payload,
            content_hash=hash_object(canonical_payload),
        )
        selected_intervals = tuple((item.start_ms, item.end_ms) for item in selected_spans)
        recognition_sources: list[CorrectionRecognitionEvidenceItemSliceV2] = []
        recognition_records: list[str] = []
        for recognition, interval_index in zip(recognitions, indices.recognitions, strict=True):
            source_hash = interval_index.source_hash
            visible_evidence_tokens = interval_index.overlapping(selected_intervals)
            if not visible_evidence_tokens:
                continue
            recognition_sources.append(
                CorrectionRecognitionEvidenceItemSliceV2(
                    schema_version=2,
                    source_content_hash=source_hash,
                    episode_id=recognition.episode_id,
                    invocation_id=recognition.invocation_id,
                    adapter=recognition.adapter,
                    model=recognition.model,
                    language=recognition.language,
                    config_hash=recognition.config_hash,
                    raw_output_hash=recognition.raw_output_hash,
                    raw_output_size_bytes=recognition.raw_output.size_bytes,
                    normalized_audio_hash=recognition.normalized_audio_hash,
                    tokens=visible_evidence_tokens,
                )
            )
            recognition_records.extend(
                f"{source_hash}:{item.id}" for item in visible_evidence_tokens
            )
        recognition_payload = {
            **common,
            "kind": "recognition_evidence_set",
            "parent_source_id": plan.inputs.recognition_evidence_set_hash,
            "parent_artifact_hash": identities.recognition_evidence_set_hash,
            "parent_internal_object_hash": plan.inputs.recognition_evidence_set_hash,
            "record_ids": tuple(sorted(recognition_records)),
            "sources": tuple(recognition_sources),
        }
        recognition_slice = CorrectionRecognitionEvidenceSliceV2(
            **recognition_payload,
            content_hash=hash_object(recognition_payload),
        )
        bindings = [
            _json_binding(
                kind="audit_plan",
                source_id=audit_slice.content_hash,
                value=audit_slice,
                internal_object_hash=audit_slice.content_hash,
                record_ids=audit_slice.record_ids,
                artifact_scope="packet-inputs",
                artifact_sink=artifact_sink,
            ),
            _json_binding(
                kind="candidate_signal_set",
                source_id=signal_slice.content_hash,
                value=signal_slice,
                internal_object_hash=signal_slice.content_hash,
                record_ids=signal_slice.record_ids,
                artifact_scope="packet-inputs",
                artifact_sink=artifact_sink,
            ),
            _json_binding(
                kind="candidate_group_set",
                source_id=group_slice.content_hash,
                value=group_slice,
                internal_object_hash=group_slice.content_hash,
                record_ids=group_slice.record_ids,
                artifact_scope="packet-inputs",
                artifact_sink=artifact_sink,
            ),
            _json_binding(
                kind="canonical_transcript",
                source_id=canonical_slice.content_hash,
                value=canonical_slice,
                internal_object_hash=canonical_slice.content_hash,
                record_ids=canonical_slice.record_ids,
                artifact_scope="packet-inputs",
                artifact_sink=artifact_sink,
            ),
            _json_binding(
                kind="recognition_evidence_set",
                source_id=recognition_slice.content_hash,
                value=recognition_slice,
                internal_object_hash=recognition_slice.content_hash,
                record_ids=recognition_slice.record_ids,
                artifact_scope="packet-inputs",
                artifact_sink=artifact_sink,
            ),
        ]
        if seam_evidence is not None and seam_ids:
            seam_id_set = set(seam_ids)
            observations = tuple(
                item for item in seam_evidence.observations if item.id in seam_id_set
            )
            seam_payload = {
                **common,
                "kind": "recognition_seam_evidence",
                "parent_source_id": seam_evidence.content_hash,
                "parent_artifact_hash": identities.seam_evidence_hash,
                "parent_internal_object_hash": seam_evidence.content_hash,
                "record_ids": tuple(sorted(seam_ids)),
                "observations": observations,
            }
            seam_slice = CorrectionRecognitionSeamSliceV2(
                **seam_payload,
                content_hash=hash_object(seam_payload),
            )
            bindings.append(
                _json_binding(
                    kind="recognition_seam_evidence",
                    source_id=seam_slice.content_hash,
                    value=seam_slice,
                    internal_object_hash=seam_slice.content_hash,
                    record_ids=seam_slice.record_ids,
                    artifact_scope="packet-inputs",
                    artifact_sink=artifact_sink,
                )
            )
    for evidence_id in sorted(evidence):
        item = evidence[evidence_id]
        reference_value: object = item
        reference_scope = "inputs"
        if not include_retrieval_lineage:
            artifact = item.artifact
            reference_payload = {
                "schema_version": 2,
                "kind": "reference_excerpt",
                "parent_source_id": item.id,
                "parent_artifact_hash": sha256_bytes(canonical_json_bytes(item)),
                "parent_internal_object_hash": hash_object(item),
                "episode_id": transcript.episode_id,
                "generation_id": transcript.generation_id,
                "record_ids": (item.id,),
                "evidence_id": item.id,
                "retrieved_for_audio_span_ids": tuple(evidence_membership[item.id]),
                "source_id": artifact.source_id,
                "reference_kind": artifact.kind,
                "source_format": artifact.source_format,
                "source_sha256": artifact.digest.sha256,
                "source_size_bytes": artifact.digest.size_bytes,
                "extracted_text_sha256": artifact.extracted_text.sha256,
                "extracted_text_size_bytes": artifact.extracted_text.size_bytes,
                "extractor_name": artifact.extractor_name,
                "extractor_version": artifact.extractor_version,
                "extractor_config_hash": artifact.extractor_config_hash,
                "extractor_code_hash": artifact.extractor_code_hash,
                "extractor_runtime_hash": artifact.extractor_runtime_hash,
                "offset_unit": artifact.offset_unit,
                "extraction_block_count": artifact.extraction_block_count,
                "title": artifact.title,
                "author": artifact.author,
                "publisher": artifact.publisher,
                "source_version": artifact.version,
                "document_date": artifact.document_date,
                "enrollment_manifest_sha256": artifact.enrollment_manifest_sha256,
                "trust_tier": artifact.trust_tier,
                "locator": CorrectionPacketReferenceLocatorV2(
                    parts=tuple(
                        CorrectionPacketReferenceLocatorPartV2(
                            kind=part.kind,
                            value=part.value,
                        )
                        for part in item.locator.parts
                    )
                ),
                "extraction_block_index": item.extraction_block_index,
                "extraction_block_hash": item.extraction_block_hash,
                "excerpt_start": item.excerpt_start,
                "excerpt_end": item.excerpt_end,
                "excerpt": item.excerpt,
                "excerpt_hash": item.excerpt_hash,
            }
            reference_value = CorrectionReferenceExcerptSliceV2(
                **reference_payload,
                content_hash=hash_object(reference_payload),
            )
            reference_scope = "packet-inputs"
        bindings.append(
            _json_binding(
                kind="reference_excerpt",
                source_id=evidence_id,
                value=reference_value,
                internal_object_hash=hash_object(item),
                record_ids=(evidence_id,),
                artifact_scope=reference_scope,
                artifact_id=(
                    reference_value.content_hash
                    if isinstance(reference_value, CorrectionReferenceExcerptSliceV2)
                    else None
                ),
                artifact_sink=artifact_sink,
            )
        )
    if clip is not None:
        if clip_bounds_ms is None:
            raise CorrectionExecutionError("audio clip bytes require millisecond bounds")
        clip_bytes, start_frame, end_frame = clip
        clip_id = hash_object(
            {
                "normalized_audio_hash": transcript.normalized_audio_hash,
                "clip_start_ms": clip_bounds_ms[0],
                "clip_end_ms": clip_bounds_ms[1],
                "clip_start_frame": start_frame,
                "clip_end_frame": end_frame,
                "derivation": "pcm-wav-frame-exact-v1",
            }
        )
        bindings.append(
            _audio_binding(
                kind="audio_clip",
                source_id=clip_id,
                exact_bytes=clip_bytes,
                record_ids=(f"frames:{start_frame}:{end_frame}",),
                artifact_sink=artifact_sink,
            )
        )
    return _ordered_bindings(bindings)


def build_correction_audit_execution_plan(
    audit_plan: AuditPlan,
    candidate_signal_set: CandidateSignalSet,
    candidate_group_set: CandidateGroupSet,
    transcript: CanonicalTranscript,
    recognition_evidence: Sequence[RecognitionEvidence],
    policy: CorrectionAuditExecutionPolicyV2,
    *,
    reference_retrievals: Sequence[ReferenceRetrievalReceipt] = (),
    seam_evidence: RecognitionSeamEvidence | None = None,
    normalized_audio_path: str | Path,
) -> CorrectionAuditExecutionPlanV2:
    """Build both exact cell partitions from a streamed immutable WAV source."""

    if (
        policy.planner_id != CORRECTION_EXECUTION_PLANNER_ID
        or policy.planner_version != CORRECTION_EXECUTION_PLANNER_VERSION
        or policy.planner_code_hash != correction_execution_planner_code_hash()
    ):
        raise CorrectionExecutionError("correction execution planner identity drift")
    recognitions, retrievals = _exact_inputs(
        plan=audit_plan,
        signal_set=candidate_signal_set,
        group_set=candidate_group_set,
        transcript=transcript,
        recognition_evidence=recognition_evidence,
        reference_retrievals=reference_retrievals,
        seam_evidence=seam_evidence,
    )
    audio_path, audio_topology, audio_snapshot, audio_digest = _prepare_normalized_audio(
        normalized_audio_path=normalized_audio_path,
        transcript=transcript,
        plan=audit_plan,
    )
    identities = _artifact_identities(
        plan=audit_plan,
        signal_set=candidate_signal_set,
        group_set=candidate_group_set,
        transcript=transcript,
        recognitions=recognitions,
        retrievals=retrievals,
        seam_evidence=seam_evidence,
    )
    binding_indices = _binding_indices(
        plan=audit_plan,
        signal_set=candidate_signal_set,
        group_set=candidate_group_set,
        transcript=transcript,
        recognitions=recognitions,
        retrievals=retrievals,
    )
    units = _owned_units(audit_plan)
    seam_by_boundary = _seam_topology(audit_plan, seam_evidence)
    mandatory = _mandatory_reasons(audit_plan, transcript, seam_by_boundary)
    cell_order = {item.id: index for index, item in enumerate(audit_plan.cells)}
    target_order = {
        item.id: index
        for index, item in enumerate((*audit_plan.span_targets, *audit_plan.boundary_targets))
    }
    span_order = {item.span_id: index for index, item in enumerate(audit_plan.span_targets)}
    token_order = {item.id: index for index, item in enumerate(transcript.tokens)}
    cell_by_id = {item.id: item for item in audit_plan.cells}
    target_by_id = {
        item.id: item for item in (*audit_plan.span_targets, *audit_plan.boundary_targets)
    }

    packets: list[CorrectionAuditPacketV2] = []
    for modality, limits in (("text", policy.text), ("audio", policy.audio)):
        groups = _packet_groups(units=units, limits=limits, mandatory=mandatory)
        for packet_index, group in enumerate(groups):
            context_indices = _context_indices(
                group=group,
                units=units,
                limits=limits,
                mandatory=mandatory,
            )
            owned_units = tuple(units[index] for index in group.span_indices)
            context_units = tuple(units[index] for index in context_indices)
            owned_cells = tuple(
                sorted(
                    {value for item in owned_units for value in item.cell_ids},
                    key=cell_order.__getitem__,
                )
            )
            context_cells = tuple(
                sorted(
                    {value for item in context_units for value in item.cell_ids},
                    key=cell_order.__getitem__,
                )
            )
            requested_cells = tuple(
                item for item in owned_cells if cell_by_id[item].applicability == "required"
            )
            owned_targets = tuple(
                sorted(
                    {cell_by_id[item].target_id for item in owned_cells},
                    key=target_order.__getitem__,
                )
            )
            context_targets = tuple(
                sorted(
                    {cell_by_id[item].target_id for item in context_cells},
                    key=target_order.__getitem__,
                )
            )
            owned_spans = tuple(
                sorted((item.span_id for item in owned_units), key=span_order.__getitem__)
            )
            context_spans = tuple(
                sorted((item.span_id for item in context_units), key=span_order.__getitem__)
            )
            owned_tokens = tuple(
                sorted(
                    {value for item in owned_units for value in item.token_ids},
                    key=token_order.__getitem__,
                )
            )
            context_tokens = tuple(
                sorted(
                    {value for item in context_units for value in item.token_ids},
                    key=token_order.__getitem__,
                )
            )
            visible_target_ids = (*owned_targets, *context_targets)
            visible_targets = tuple(target_by_id[item] for item in visible_target_ids)
            window_start_ms = min(
                item.window_start_ms if isinstance(item, BoundaryAuditTarget) else item.start_ms
                for item in visible_targets
            )
            window_end_ms = max(
                item.window_end_ms if isinstance(item, BoundaryAuditTarget) else item.end_ms
                for item in visible_targets
            )
            if window_end_ms <= window_start_ms:
                raise CorrectionExecutionError("packet target window has no positive duration")
            visible_span_indices = tuple(sorted((*group.span_indices, *context_indices)))
            speakers = tuple(
                sorted(
                    {
                        transcript.tokens[token_order[token_id]].speaker
                        for index in visible_span_indices
                        for token_id in audit_plan.span_targets[index].token_ids
                        if transcript.tokens[token_order[token_id]].speaker is not None
                    }
                )
            )
            seam_ids = tuple(
                sorted(
                    {
                        seam_id
                        for ordinal in range(visible_span_indices[0], visible_span_indices[-1] + 2)
                        for seam_id in seam_by_boundary.get(ordinal, ())
                    }
                )
            )
            clip_start_ms: int | None = None
            clip_end_ms: int | None = None
            clip_start_frame: int | None = None
            clip_end_frame: int | None = None
            clip: tuple[bytes, int, int] | None = None
            if modality == "audio":
                clip_start_ms = max(0, window_start_ms - limits.clip_padding_ms)
                clip_end_ms = min(
                    audio_topology.duration_ms,
                    window_end_ms + limits.clip_padding_ms,
                )
                if (
                    limits.max_clip_duration_ms is None
                    or clip_end_ms - clip_start_ms > limits.max_clip_duration_ms
                ):
                    raise CorrectionExecutionError("audio packet clip exceeds exact duration cap")
                clip = derive_pcm_wav_clip_bytes(
                    audio_path,
                    clip_start_ms=clip_start_ms,
                    clip_end_ms=clip_end_ms,
                    topology=audio_topology,
                )
                clip_start_frame, clip_end_frame = clip[1], clip[2]
            all_packet_cells = tuple(
                sorted((*owned_cells, *context_cells), key=cell_order.__getitem__)
            )
            bindings = _build_bindings(
                plan=audit_plan,
                signal_set=candidate_signal_set,
                group_set=candidate_group_set,
                transcript=transcript,
                recognitions=recognitions,
                retrievals=retrievals,
                seam_evidence=seam_evidence,
                identities=identities,
                normalized_audio_hash=audio_digest,
                normalized_audio_size_bytes=audio_snapshot.size_bytes,
                indices=binding_indices,
                cell_ids=all_packet_cells,
                span_ids=(*owned_spans, *context_spans),
                token_ids=(*owned_tokens, *context_tokens),
                include_retrieval_lineage=False,
                seam_ids=seam_ids,
                clip=clip,
                clip_bounds_ms=(clip_start_ms, clip_end_ms)
                if clip_start_ms is not None and clip_end_ms is not None
                else None,
            )
            reference_ids = tuple(item.id for item in bindings if item.kind == "reference_excerpt")
            packet_payload = {
                "schema_version": 2,
                "packet_index": packet_index,
                "episode_id": transcript.episode_id,
                "generation_id": transcript.generation_id,
                "modality": modality,
                "audit_plan_hash": identities.audit_plan_hash,
                "audit_plan_content_hash": audit_plan.content_hash,
                "audit_input_hash": audit_plan.inputs.content_hash,
                "candidate_signal_set_hash": identities.candidate_signal_set_hash,
                "candidate_signal_set_content_hash": candidate_signal_set.content_hash,
                "candidate_group_set_hash": identities.candidate_group_set_hash,
                "candidate_group_set_content_hash": candidate_group_set.content_hash,
                "canonical_transcript_hash": identities.canonical_transcript_hash,
                "canonical_content_hash": transcript.content_hash,
                "normalized_audio_hash": transcript.normalized_audio_hash,
                "policy_hash": policy.content_hash,
                "owned_cell_ids": owned_cells,
                "requested_cell_ids": requested_cells,
                "context_cell_ids": context_cells,
                "owned_target_ids": owned_targets,
                "context_target_ids": context_targets,
                "owned_span_ids": owned_spans,
                "context_span_ids": context_spans,
                "owned_token_ids": owned_tokens,
                "context_token_ids": context_tokens,
                "window_start_ms": window_start_ms,
                "window_end_ms": window_end_ms,
                "clip_start_ms": clip_start_ms,
                "clip_end_ms": clip_end_ms,
                "clip_start_frame": clip_start_frame,
                "clip_end_frame": clip_end_frame,
                "speaker_labels": speakers,
                "recognition_seam_ids": seam_ids,
                "reference_evidence_ids": reference_ids,
                "split_before_reasons": group.split_before,
                "split_after_reasons": group.split_after,
                "source_bindings": bindings,
                "planner_id": policy.planner_id,
                "planner_version": policy.planner_version,
                "planner_code_hash": policy.planner_code_hash,
                "execution_status": "planned_not_executed",
            }
            packet_hash = hash_object(packet_payload)
            packets.append(
                CorrectionAuditPacketV2(
                    **packet_payload,
                    id=packet_hash,
                    content_hash=packet_hash,
                )
            )

    all_cell_ids = tuple(item.id for item in audit_plan.cells)
    required_cell_ids = tuple(
        item.id for item in audit_plan.cells if item.applicability == "required"
    )
    all_span_ids = tuple(item.span_id for item in audit_plan.span_targets)
    all_token_ids = tuple(item.id for item in transcript.tokens)
    source_bindings = _build_bindings(
        plan=audit_plan,
        signal_set=candidate_signal_set,
        group_set=candidate_group_set,
        transcript=transcript,
        recognitions=recognitions,
        retrievals=retrievals,
        seam_evidence=seam_evidence,
        identities=identities,
        normalized_audio_hash=audio_digest,
        normalized_audio_size_bytes=audio_snapshot.size_bytes,
        indices=binding_indices,
        cell_ids=all_cell_ids,
        span_ids=all_span_ids,
        token_ids=all_token_ids,
        include_retrieval_lineage=True,
        seam_ids=tuple(item.id for item in seam_evidence.observations)
        if seam_evidence is not None
        else (),
    )
    plan_payload = {
        "schema_version": 2,
        "episode_id": transcript.episode_id,
        "generation_id": transcript.generation_id,
        "policy": policy,
        "policy_hash": policy.content_hash,
        "audit_plan_hash": identities.audit_plan_hash,
        "audit_plan_content_hash": audit_plan.content_hash,
        "audit_input_hash": audit_plan.inputs.content_hash,
        "candidate_signal_set_hash": identities.candidate_signal_set_hash,
        "candidate_signal_set_content_hash": candidate_signal_set.content_hash,
        "candidate_group_set_hash": identities.candidate_group_set_hash,
        "candidate_group_set_content_hash": candidate_group_set.content_hash,
        "canonical_transcript_hash": identities.canonical_transcript_hash,
        "canonical_content_hash": transcript.content_hash,
        "recognition_evidence_set_hash": identities.recognition_evidence_set_hash,
        "reference_retrieval_receipt_set_hash": (identities.reference_retrieval_receipt_set_hash),
        "normalized_audio_hash": transcript.normalized_audio_hash,
        "all_cell_ids": all_cell_ids,
        "required_cell_ids": required_cell_ids,
        "source_bindings": source_bindings,
        "packets": tuple(packets),
        "planner_id": policy.planner_id,
        "planner_version": policy.planner_version,
        "planner_code_hash": policy.planner_code_hash,
        "execution_status": "planned_not_executed",
        "completeness_statement": ("cell_execution_topology_only_not_semantic_recall_proof"),
    }
    execution_hash = hash_object(plan_payload)
    result = CorrectionAuditExecutionPlanV2(
        **plan_payload,
        id=execution_hash,
        content_hash=execution_hash,
    )
    _assert_normalized_audio_unchanged(
        path=audio_path,
        initial=audio_snapshot,
        digest=audio_digest,
    )
    return result


def build_modality_audit_execution_plan_v3(
    audit_plan: AuditPlan,
    candidate_signal_set: CandidateSignalSet,
    candidate_group_set: CandidateGroupSet,
    transcript: CanonicalTranscript,
    recognition_evidence: Sequence[RecognitionEvidence],
    policy: CorrectionAuditExecutionPolicyV2,
    *,
    modality: str,
    selection_plan: AudioAuditSelectionPlanV1 | None = None,
    prior_audio_plans: Sequence[ModalityAuditExecutionPlanV3] = (),
    reference_retrievals: Sequence[ReferenceRetrievalReceipt] = (),
    seam_evidence: RecognitionSeamEvidence | None = None,
    normalized_audio_path: str | Path | None = None,
) -> ModalityAuditExecutionPlanV3:
    """Build text-only universal or audio-only selected-delta topology.

    Text planning deliberately never opens normalized audio.  Audio planning is
    therefore safe to defer until the completed text record has frozen one
    exact selection manifest.
    """

    if modality not in ("text", "audio"):
        raise CorrectionExecutionError("modality execution requires text or audio")
    if (
        policy.planner_id != CORRECTION_EXECUTION_PLANNER_ID
        or policy.planner_version != CORRECTION_EXECUTION_PLANNER_VERSION
        or policy.planner_code_hash != correction_execution_planner_code_hash()
    ):
        raise CorrectionExecutionError("correction execution planner identity drift")
    recognitions, retrievals = _exact_inputs(
        plan=audit_plan,
        signal_set=candidate_signal_set,
        group_set=candidate_group_set,
        transcript=transcript,
        recognition_evidence=recognition_evidence,
        reference_retrievals=reference_retrievals,
        seam_evidence=seam_evidence,
    )
    identities = _artifact_identities(
        plan=audit_plan,
        signal_set=candidate_signal_set,
        group_set=candidate_group_set,
        transcript=transcript,
        recognitions=recognitions,
        retrievals=retrievals,
        seam_evidence=seam_evidence,
    )
    binding_indices = _binding_indices(
        plan=audit_plan,
        signal_set=candidate_signal_set,
        group_set=candidate_group_set,
        transcript=transcript,
        recognitions=recognitions,
        retrievals=retrievals,
    )
    audio_path: Path | None = None
    audio_topology: PcmWavTopology | None = None
    audio_snapshot: _AudioFileSnapshot | None = None
    audio_digest = transcript.normalized_audio_hash
    if modality == "audio":
        if selection_plan is None or normalized_audio_path is None:
            raise CorrectionExecutionError("audio execution requires selection and normalized WAV")
        if (
            selection_plan.episode_id != transcript.episode_id
            or selection_plan.generation_id != transcript.generation_id
            or selection_plan.normalized_audio_hash != transcript.normalized_audio_hash
            or selection_plan.audit_plan_hash != identities.audit_plan_hash
            or selection_plan.audit_plan_content_hash != audit_plan.content_hash
            or selection_plan.canonical_transcript_hash != identities.canonical_transcript_hash
            or selection_plan.canonical_content_hash != transcript.content_hash
        ):
            raise CorrectionExecutionError("audio selection crosses execution lineage")
        audio_path, audio_topology, audio_snapshot, audio_digest = _prepare_normalized_audio(
            normalized_audio_path=normalized_audio_path,
            transcript=transcript,
            plan=audit_plan,
        )
    elif selection_plan is not None or prior_audio_plans or normalized_audio_path is not None:
        raise CorrectionExecutionError("text execution cannot claim audio-only inputs")

    units = _owned_units(audit_plan)
    prior_span_ids: set[str] = set()
    for prior in prior_audio_plans:
        if (
            prior.modality != "audio"
            or prior.episode_id != transcript.episode_id
            or prior.generation_id != transcript.generation_id
            or prior.audit_plan_hash != identities.audit_plan_hash
            or prior.normalized_audio_hash != transcript.normalized_audio_hash
            or set(prior_span_ids) & set(prior.owned_span_ids)
        ):
            raise CorrectionExecutionError("prior audio execution lineage or delta overlaps")
        prior_span_ids.update(prior.owned_span_ids)
    if modality == "text":
        selected_indices = tuple(range(len(units)))
    else:
        assert selection_plan is not None
        selected_set = set(selection_plan.selected_span_ids)
        if not prior_span_ids <= selected_set:
            raise CorrectionExecutionError("prior audio delta is outside cumulative selection")
        selected_indices = tuple(
            index
            for index, unit in enumerate(units)
            if unit.span_id in selected_set - prior_span_ids
        )
        if not selected_indices:
            raise CorrectionExecutionError("audio selection round has no new owned spans")

    seam_by_boundary = _seam_topology(audit_plan, seam_evidence)
    mandatory = _mandatory_reasons(audit_plan, transcript, seam_by_boundary)
    limits = policy.text if modality == "text" else policy.audio
    if modality == "text":
        packet_groups = _packet_groups(units=units, limits=limits, mandatory=mandatory)
    else:
        packet_groups = tuple(
            _PacketGroup(
                span_indices=(span_index,),
                split_before=("episode_start",) if ordinal == 0 else ("selection_gap",),
                split_after=("episode_end",)
                if ordinal == len(selected_indices) - 1
                else ("selection_gap",),
            )
            for ordinal, span_index in enumerate(selected_indices)
        )

    cell_order = {item.id: index for index, item in enumerate(audit_plan.cells)}
    target_order = {
        item.id: index
        for index, item in enumerate((*audit_plan.span_targets, *audit_plan.boundary_targets))
    }
    span_order = {item.span_id: index for index, item in enumerate(audit_plan.span_targets)}
    token_order = {item.id: index for index, item in enumerate(transcript.tokens)}
    cell_by_id = {item.id: item for item in audit_plan.cells}
    target_by_id = {
        item.id: item for item in (*audit_plan.span_targets, *audit_plan.boundary_targets)
    }
    packets: list[CorrectionAuditPacketV2] = []
    for packet_index, group in enumerate(packet_groups):
        context_indices = _context_indices(
            group=group,
            units=units,
            limits=limits,
            mandatory=mandatory,
        )
        owned_units = tuple(units[index] for index in group.span_indices)
        context_units = tuple(units[index] for index in context_indices)
        owned_cells = tuple(
            sorted(
                {value for item in owned_units for value in item.cell_ids},
                key=cell_order.__getitem__,
            )
        )
        context_cells = tuple(
            sorted(
                {value for item in context_units for value in item.cell_ids},
                key=cell_order.__getitem__,
            )
        )
        requested_cells = tuple(
            item for item in owned_cells if cell_by_id[item].applicability == "required"
        )
        owned_targets = tuple(
            sorted(
                {cell_by_id[item].target_id for item in owned_cells},
                key=target_order.__getitem__,
            )
        )
        context_targets = tuple(
            sorted(
                {cell_by_id[item].target_id for item in context_cells},
                key=target_order.__getitem__,
            )
        )
        owned_spans = tuple(
            sorted((item.span_id for item in owned_units), key=span_order.__getitem__)
        )
        context_spans = tuple(
            sorted((item.span_id for item in context_units), key=span_order.__getitem__)
        )
        owned_tokens = tuple(
            sorted(
                {value for item in owned_units for value in item.token_ids},
                key=token_order.__getitem__,
            )
        )
        context_tokens = tuple(
            sorted(
                {value for item in context_units for value in item.token_ids},
                key=token_order.__getitem__,
            )
        )
        visible_targets = tuple(target_by_id[item] for item in (*owned_targets, *context_targets))
        window_start_ms = min(
            item.window_start_ms if isinstance(item, BoundaryAuditTarget) else item.start_ms
            for item in visible_targets
        )
        window_end_ms = max(
            item.window_end_ms if isinstance(item, BoundaryAuditTarget) else item.end_ms
            for item in visible_targets
        )
        visible_span_indices = tuple(sorted((*group.span_indices, *context_indices)))
        speakers = tuple(
            sorted(
                {
                    transcript.tokens[token_order[token_id]].speaker
                    for index in visible_span_indices
                    for token_id in audit_plan.span_targets[index].token_ids
                    if transcript.tokens[token_order[token_id]].speaker is not None
                }
            )
        )
        seam_ids = tuple(
            sorted(
                {
                    seam_id
                    for ordinal in range(visible_span_indices[0], visible_span_indices[-1] + 2)
                    for seam_id in seam_by_boundary.get(ordinal, ())
                }
            )
        )
        clip_start_ms = clip_end_ms = clip_start_frame = clip_end_frame = None
        clip: tuple[bytes, int, int] | None = None
        if modality == "audio":
            assert audio_path is not None
            assert audio_topology is not None
            clip_start_ms = max(0, window_start_ms - limits.clip_padding_ms)
            clip_end_ms = min(
                audio_topology.duration_ms,
                window_end_ms + limits.clip_padding_ms,
            )
            if (
                limits.max_clip_duration_ms is None
                or clip_end_ms - clip_start_ms > limits.max_clip_duration_ms
            ):
                raise CorrectionExecutionError("audio selection clip exceeds duration cap")
            clip = derive_pcm_wav_clip_bytes(
                audio_path,
                clip_start_ms=clip_start_ms,
                clip_end_ms=clip_end_ms,
                topology=audio_topology,
            )
            clip_start_frame, clip_end_frame = clip[1], clip[2]
        bindings = _build_bindings(
            plan=audit_plan,
            signal_set=candidate_signal_set,
            group_set=candidate_group_set,
            transcript=transcript,
            recognitions=recognitions,
            retrievals=retrievals,
            seam_evidence=seam_evidence,
            identities=identities,
            normalized_audio_hash=audio_digest,
            normalized_audio_size_bytes=(audio_snapshot.size_bytes if audio_snapshot else 0),
            indices=binding_indices,
            cell_ids=tuple(sorted((*owned_cells, *context_cells), key=cell_order.__getitem__)),
            span_ids=(*owned_spans, *context_spans),
            token_ids=(*owned_tokens, *context_tokens),
            include_retrieval_lineage=False,
            seam_ids=seam_ids,
            clip=clip,
            clip_bounds_ms=(clip_start_ms, clip_end_ms)
            if clip_start_ms is not None and clip_end_ms is not None
            else None,
        )
        packet_payload = {
            "schema_version": 2,
            "packet_index": packet_index,
            "episode_id": transcript.episode_id,
            "generation_id": transcript.generation_id,
            "modality": modality,
            "audit_plan_hash": identities.audit_plan_hash,
            "audit_plan_content_hash": audit_plan.content_hash,
            "audit_input_hash": audit_plan.inputs.content_hash,
            "candidate_signal_set_hash": identities.candidate_signal_set_hash,
            "candidate_signal_set_content_hash": candidate_signal_set.content_hash,
            "candidate_group_set_hash": identities.candidate_group_set_hash,
            "candidate_group_set_content_hash": candidate_group_set.content_hash,
            "canonical_transcript_hash": identities.canonical_transcript_hash,
            "canonical_content_hash": transcript.content_hash,
            "normalized_audio_hash": transcript.normalized_audio_hash,
            "policy_hash": policy.content_hash,
            "owned_cell_ids": owned_cells,
            "requested_cell_ids": requested_cells,
            "context_cell_ids": context_cells,
            "owned_target_ids": owned_targets,
            "context_target_ids": context_targets,
            "owned_span_ids": owned_spans,
            "context_span_ids": context_spans,
            "owned_token_ids": owned_tokens,
            "context_token_ids": context_tokens,
            "window_start_ms": window_start_ms,
            "window_end_ms": window_end_ms,
            "clip_start_ms": clip_start_ms,
            "clip_end_ms": clip_end_ms,
            "clip_start_frame": clip_start_frame,
            "clip_end_frame": clip_end_frame,
            "speaker_labels": speakers,
            "recognition_seam_ids": seam_ids,
            "reference_evidence_ids": tuple(
                item.id for item in bindings if item.kind == "reference_excerpt"
            ),
            "split_before_reasons": group.split_before,
            "split_after_reasons": group.split_after,
            "source_bindings": bindings,
            "planner_id": policy.planner_id,
            "planner_version": policy.planner_version,
            "planner_code_hash": policy.planner_code_hash,
            "execution_status": "planned_not_executed",
        }
        packet_hash = hash_object(packet_payload)
        packets.append(
            CorrectionAuditPacketV2(
                **packet_payload,
                id=packet_hash,
                content_hash=packet_hash,
            )
        )

    owned_cell_ids = tuple(cell_id for packet in packets for cell_id in packet.owned_cell_ids)
    required_cell_ids = tuple(
        cell_id for packet in packets for cell_id in packet.requested_cell_ids
    )
    owned_span_id_set = {span_id for packet in packets for span_id in packet.owned_span_ids}
    owned_span_ids = tuple(
        item.span_id for item in audit_plan.span_targets if item.span_id in owned_span_id_set
    )
    source_bindings = _build_bindings(
        plan=audit_plan,
        signal_set=candidate_signal_set,
        group_set=candidate_group_set,
        transcript=transcript,
        recognitions=recognitions,
        retrievals=retrievals,
        seam_evidence=seam_evidence,
        identities=identities,
        normalized_audio_hash=audio_digest,
        normalized_audio_size_bytes=(audio_snapshot.size_bytes if audio_snapshot else 0),
        indices=binding_indices,
        cell_ids=owned_cell_ids,
        span_ids=owned_span_ids,
        token_ids=tuple(
            token_id
            for item in audit_plan.span_targets
            if item.span_id in owned_span_id_set
            for token_id in item.token_ids
        ),
        include_retrieval_lineage=True,
        seam_ids=tuple(item.id for item in seam_evidence.observations)
        if seam_evidence is not None
        else (),
    )
    selection_hash = sha256_bytes(canonical_json_bytes(selection_plan)) if selection_plan else None
    plan_payload = {
        "schema_version": 3,
        "episode_id": transcript.episode_id,
        "generation_id": transcript.generation_id,
        "modality": modality,
        "policy": policy,
        "policy_hash": policy.content_hash,
        "audit_plan_hash": identities.audit_plan_hash,
        "audit_plan_content_hash": audit_plan.content_hash,
        "audit_input_hash": audit_plan.inputs.content_hash,
        "candidate_signal_set_hash": identities.candidate_signal_set_hash,
        "candidate_signal_set_content_hash": candidate_signal_set.content_hash,
        "candidate_group_set_hash": identities.candidate_group_set_hash,
        "candidate_group_set_content_hash": candidate_group_set.content_hash,
        "canonical_transcript_hash": identities.canonical_transcript_hash,
        "canonical_content_hash": transcript.content_hash,
        "recognition_evidence_set_hash": identities.recognition_evidence_set_hash,
        "reference_retrieval_receipt_set_hash": identities.reference_retrieval_receipt_set_hash,
        "normalized_audio_hash": transcript.normalized_audio_hash,
        "all_cell_ids": tuple(item.id for item in audit_plan.cells),
        "owned_cell_ids": owned_cell_ids,
        "required_cell_ids": required_cell_ids,
        "owned_span_ids": owned_span_ids,
        "selection_plan_id": selection_plan.id if selection_plan else None,
        "selection_plan_content_hash": selection_plan.content_hash if selection_plan else None,
        "selection_plan_artifact_hash": selection_hash,
        "tier": selection_plan.tier if selection_plan else None,
        "prior_execution_plan_id": prior_audio_plans[-1].id if prior_audio_plans else None,
        "source_bindings": source_bindings,
        "packets": tuple(packets),
        "planner_id": policy.planner_id,
        "planner_version": policy.planner_version,
        "planner_code_hash": policy.planner_code_hash,
        "execution_status": "planned_not_executed",
        "completeness_statement": (
            "text_universal_or_audio_exact_selected_delta_not_semantic_recall_proof"
        ),
    }
    digest = hash_object(plan_payload)
    result = ModalityAuditExecutionPlanV3(**plan_payload, id=digest, content_hash=digest)
    if audio_path is not None and audio_snapshot is not None:
        _assert_normalized_audio_unchanged(
            path=audio_path,
            initial=audio_snapshot,
            digest=audio_digest,
        )
    return result


def _validate_execution_plan_artifact(
    execution_plan: CorrectionAuditExecutionPlanV2 | ModalityAuditExecutionPlanV3,
    *,
    label: str,
) -> None:
    model = (
        ModalityAuditExecutionPlanV3
        if isinstance(execution_plan, ModalityAuditExecutionPlanV3)
        else CorrectionAuditExecutionPlanV2
    )
    try:
        validated = model.model_validate_json(canonical_json_bytes(execution_plan))
    except ValueError as exc:
        raise CorrectionExecutionError(f"{label} execution plan is internally invalid") from exc
    if validated != execution_plan:
        raise CorrectionExecutionError(f"{label} execution plan is not an exact artifact")


def materialize_text_correction_packet_sources(
    execution_plan: CorrectionAuditExecutionPlanV2 | ModalityAuditExecutionPlanV3,
    packet_id: str,
    audit_plan: AuditPlan,
    candidate_signal_set: CandidateSignalSet,
    candidate_group_set: CandidateGroupSet,
    transcript: CanonicalTranscript,
    recognition_evidence: Sequence[RecognitionEvidence],
    *,
    reference_retrievals: Sequence[ReferenceRetrievalReceipt] = (),
    seam_evidence: RecognitionSeamEvidence | None = None,
) -> dict[str, bytes]:
    """Rebuild the exact bounded JSON bytes bound by one text packet.

    The topology stores byte digests and portable locators, not a second copy of
    every source slice.  A worker export must call this function from the frozen
    parent artifacts; accepting caller-authored JSON would make the packet
    bindings decorative instead of executable evidence.
    """

    return materialize_text_correction_packet_sources_batch(
        execution_plan,
        audit_plan,
        candidate_signal_set,
        candidate_group_set,
        transcript,
        recognition_evidence,
        packet_ids=(packet_id,),
        reference_retrievals=reference_retrievals,
        seam_evidence=seam_evidence,
    )


def materialize_text_correction_packet_sources_batch(
    execution_plan: CorrectionAuditExecutionPlanV2 | ModalityAuditExecutionPlanV3,
    audit_plan: AuditPlan,
    candidate_signal_set: CandidateSignalSet,
    candidate_group_set: CandidateGroupSet,
    transcript: CanonicalTranscript,
    recognition_evidence: Sequence[RecognitionEvidence],
    *,
    packet_ids: Sequence[str] | None = None,
    reference_retrievals: Sequence[ReferenceRetrievalReceipt] = (),
    seam_evidence: RecognitionSeamEvidence | None = None,
) -> dict[str, bytes]:
    """Rebuild an exact text-packet batch with one frozen-parent validation.

    Full parent validation, artifact hashing, and global binding indexes are
    episode-scale work.  They are intentionally prepared once for the batch;
    each packet then materializes only its bounded slices.  The single-packet
    API delegates here so both paths retain identical fail-closed semantics.
    """

    _validate_execution_plan_artifact(execution_plan, label="text packet")
    if isinstance(execution_plan, ModalityAuditExecutionPlanV3) and (
        execution_plan.modality != "text"
    ):
        raise CorrectionExecutionError("text materializer rejects audio-only execution")

    packet_by_id = {item.id: item for item in execution_plan.packets}
    if len(packet_by_id) != len(execution_plan.packets):
        raise CorrectionExecutionError("text packet identity is absent or ambiguous")
    selected_packet_ids = (
        tuple(item.id for item in execution_plan.packets)
        if packet_ids is None
        else tuple(packet_ids)
    )
    if not selected_packet_ids or len(set(selected_packet_ids)) != len(selected_packet_ids):
        raise CorrectionExecutionError("text packet batch is empty or contains duplicates")
    try:
        packets = tuple(packet_by_id[item_id] for item_id in selected_packet_ids)
    except KeyError as exc:
        raise CorrectionExecutionError("text packet identity is absent or ambiguous") from exc
    if any(packet.modality != "text" for packet in packets):
        raise CorrectionExecutionError("text packet materializer rejects audio packets")

    recognitions, retrievals = _exact_inputs(
        plan=audit_plan,
        signal_set=candidate_signal_set,
        group_set=candidate_group_set,
        transcript=transcript,
        recognition_evidence=recognition_evidence,
        reference_retrievals=reference_retrievals,
        seam_evidence=seam_evidence,
    )
    identities = _artifact_identities(
        plan=audit_plan,
        signal_set=candidate_signal_set,
        group_set=candidate_group_set,
        transcript=transcript,
        recognitions=recognitions,
        retrievals=retrievals,
        seam_evidence=seam_evidence,
    )
    expected_lineage = (
        execution_plan.episode_id == transcript.episode_id,
        execution_plan.generation_id == transcript.generation_id,
        execution_plan.audit_plan_hash == identities.audit_plan_hash,
        execution_plan.audit_plan_content_hash == audit_plan.content_hash,
        execution_plan.audit_input_hash == audit_plan.inputs.content_hash,
        execution_plan.candidate_signal_set_hash == identities.candidate_signal_set_hash,
        execution_plan.candidate_signal_set_content_hash == candidate_signal_set.content_hash,
        execution_plan.candidate_group_set_hash == identities.candidate_group_set_hash,
        execution_plan.candidate_group_set_content_hash == candidate_group_set.content_hash,
        execution_plan.canonical_transcript_hash == identities.canonical_transcript_hash,
        execution_plan.canonical_content_hash == transcript.content_hash,
        execution_plan.recognition_evidence_set_hash == identities.recognition_evidence_set_hash,
        execution_plan.reference_retrieval_receipt_set_hash
        == identities.reference_retrieval_receipt_set_hash,
        execution_plan.normalized_audio_hash == transcript.normalized_audio_hash,
    )
    if not all(expected_lineage):
        raise CorrectionExecutionError(
            "text packet source parents differ from the execution plan lineage"
        )

    indices = _binding_indices(
        plan=audit_plan,
        signal_set=candidate_signal_set,
        group_set=candidate_group_set,
        transcript=transcript,
        recognitions=recognitions,
        retrievals=retrievals,
    )
    batch_sources: dict[str, bytes] = {}
    for packet in packets:
        packet_sources: dict[str, bytes] = {}
        rebuilt = _build_bindings(
            plan=audit_plan,
            signal_set=candidate_signal_set,
            group_set=candidate_group_set,
            transcript=transcript,
            recognitions=recognitions,
            retrievals=retrievals,
            seam_evidence=seam_evidence,
            identities=identities,
            normalized_audio_hash=transcript.normalized_audio_hash,
            normalized_audio_size_bytes=0,
            cell_ids=tuple(
                sorted(
                    (*packet.owned_cell_ids, *packet.context_cell_ids),
                    key=indices.cell_order.__getitem__,
                )
            ),
            span_ids=tuple(
                sorted(
                    (*packet.owned_span_ids, *packet.context_span_ids),
                    key=indices.span_order.__getitem__,
                )
            ),
            token_ids=tuple(
                sorted(
                    (*packet.owned_token_ids, *packet.context_token_ids),
                    key=indices.token_order.__getitem__,
                )
            ),
            include_retrieval_lineage=False,
            seam_ids=packet.recognition_seam_ids,
            artifact_sink=packet_sources,
            indices=indices,
        )
        if rebuilt != packet.source_bindings:
            raise CorrectionExecutionError(
                "text packet source bindings are not reproducible from frozen parents"
            )
        expected_uris = tuple(item.artifact_uri for item in packet.source_bindings)
        if tuple(packet_sources) != expected_uris:
            raise CorrectionExecutionError("text packet source byte set is incomplete or reordered")
        for uri, exact_bytes in packet_sources.items():
            previous = batch_sources.setdefault(uri, exact_bytes)
            if previous != exact_bytes:
                raise CorrectionExecutionError(
                    "text artifact URI resolves to conflicting exact bytes"
                )
    return batch_sources


def materialize_audio_correction_packet_sources(
    execution_plan: CorrectionAuditExecutionPlanV2 | ModalityAuditExecutionPlanV3,
    packet_id: str,
    audit_plan: AuditPlan,
    candidate_signal_set: CandidateSignalSet,
    candidate_group_set: CandidateGroupSet,
    transcript: CanonicalTranscript,
    recognition_evidence: Sequence[RecognitionEvidence],
    *,
    normalized_audio_path: str | Path,
    reference_retrievals: Sequence[ReferenceRetrievalReceipt] = (),
    seam_evidence: RecognitionSeamEvidence | None = None,
) -> dict[str, bytes]:
    """Rebuild one audio packet's exact bounded JSON and frame-exact WAV clip.

    The caller supplies immutable parents and the Normalized Audio artifact, not
    caller-authored packet bytes.  The source file is hashed and snapshotted
    before derivation and revalidated after every byte has been materialized.
    """

    _validate_execution_plan_artifact(execution_plan, label="audio packet")
    if isinstance(execution_plan, ModalityAuditExecutionPlanV3) and (
        execution_plan.modality != "audio"
    ):
        raise CorrectionExecutionError("audio materializer rejects text-only execution")
    matches = tuple(item for item in execution_plan.packets if item.id == packet_id)
    if len(matches) != 1:
        raise CorrectionExecutionError("audio packet identity is absent or ambiguous")
    packet = matches[0]
    if packet.modality != "audio":
        raise CorrectionExecutionError("audio packet materializer rejects text packets")
    if (
        packet.clip_start_ms is None
        or packet.clip_end_ms is None
        or packet.clip_start_frame is None
        or packet.clip_end_frame is None
    ):
        raise CorrectionExecutionError("audio packet lacks exact clip bounds")

    recognitions, retrievals = _exact_inputs(
        plan=audit_plan,
        signal_set=candidate_signal_set,
        group_set=candidate_group_set,
        transcript=transcript,
        recognition_evidence=recognition_evidence,
        reference_retrievals=reference_retrievals,
        seam_evidence=seam_evidence,
    )
    audio_path, topology, snapshot, audio_digest = _prepare_normalized_audio(
        normalized_audio_path=normalized_audio_path,
        transcript=transcript,
        plan=audit_plan,
    )
    identities = _artifact_identities(
        plan=audit_plan,
        signal_set=candidate_signal_set,
        group_set=candidate_group_set,
        transcript=transcript,
        recognitions=recognitions,
        retrievals=retrievals,
        seam_evidence=seam_evidence,
    )
    expected_lineage = (
        execution_plan.episode_id == transcript.episode_id,
        execution_plan.generation_id == transcript.generation_id,
        execution_plan.audit_plan_hash == identities.audit_plan_hash,
        execution_plan.audit_plan_content_hash == audit_plan.content_hash,
        execution_plan.audit_input_hash == audit_plan.inputs.content_hash,
        execution_plan.candidate_signal_set_hash == identities.candidate_signal_set_hash,
        execution_plan.candidate_signal_set_content_hash == candidate_signal_set.content_hash,
        execution_plan.candidate_group_set_hash == identities.candidate_group_set_hash,
        execution_plan.candidate_group_set_content_hash == candidate_group_set.content_hash,
        execution_plan.canonical_transcript_hash == identities.canonical_transcript_hash,
        execution_plan.canonical_content_hash == transcript.content_hash,
        execution_plan.recognition_evidence_set_hash == identities.recognition_evidence_set_hash,
        execution_plan.reference_retrieval_receipt_set_hash
        == identities.reference_retrieval_receipt_set_hash,
        execution_plan.normalized_audio_hash == audio_digest,
    )
    if not all(expected_lineage):
        raise CorrectionExecutionError(
            "audio packet source parents differ from the execution plan lineage"
        )

    clip = derive_pcm_wav_clip_bytes(
        audio_path,
        clip_start_ms=packet.clip_start_ms,
        clip_end_ms=packet.clip_end_ms,
        topology=topology,
    )
    if clip[1:] != (packet.clip_start_frame, packet.clip_end_frame):
        raise CorrectionExecutionError("audio packet frame bounds drift from exact WAV topology")
    cell_order = {item.id: index for index, item in enumerate(audit_plan.cells)}
    span_order = {item.span_id: index for index, item in enumerate(audit_plan.span_targets)}
    token_order = {item.id: index for index, item in enumerate(transcript.tokens)}
    sources: dict[str, bytes] = {}
    rebuilt = _build_bindings(
        plan=audit_plan,
        signal_set=candidate_signal_set,
        group_set=candidate_group_set,
        transcript=transcript,
        recognitions=recognitions,
        retrievals=retrievals,
        seam_evidence=seam_evidence,
        identities=identities,
        normalized_audio_hash=audio_digest,
        normalized_audio_size_bytes=snapshot.size_bytes,
        cell_ids=tuple(
            sorted((*packet.owned_cell_ids, *packet.context_cell_ids), key=cell_order.__getitem__)
        ),
        span_ids=tuple(
            sorted((*packet.owned_span_ids, *packet.context_span_ids), key=span_order.__getitem__)
        ),
        token_ids=tuple(
            sorted(
                (*packet.owned_token_ids, *packet.context_token_ids),
                key=token_order.__getitem__,
            )
        ),
        include_retrieval_lineage=False,
        seam_ids=packet.recognition_seam_ids,
        clip=clip,
        clip_bounds_ms=(packet.clip_start_ms, packet.clip_end_ms),
        artifact_sink=sources,
    )
    if rebuilt != packet.source_bindings:
        raise CorrectionExecutionError(
            "audio packet source bindings are not reproducible from frozen parents"
        )
    expected_uris = tuple(item.artifact_uri for item in packet.source_bindings)
    if tuple(sources) != expected_uris:
        raise CorrectionExecutionError("audio packet source byte set is incomplete or reordered")
    _assert_normalized_audio_unchanged(path=audio_path, initial=snapshot, digest=audio_digest)
    return sources


def assert_correction_audit_execution_plan(
    stored: CorrectionAuditExecutionPlanV2,
    audit_plan: AuditPlan,
    candidate_signal_set: CandidateSignalSet,
    candidate_group_set: CandidateGroupSet,
    transcript: CanonicalTranscript,
    recognition_evidence: Sequence[RecognitionEvidence],
    policy: CorrectionAuditExecutionPolicyV2,
    *,
    reference_retrievals: Sequence[ReferenceRetrievalReceipt] = (),
    seam_evidence: RecognitionSeamEvidence | None = None,
    normalized_audio_path: str | Path,
) -> CorrectionAuditExecutionPlanV2:
    """Rebuild from every exact input; outer hash validation alone is insufficient."""

    expected = build_correction_audit_execution_plan(
        audit_plan,
        candidate_signal_set,
        candidate_group_set,
        transcript,
        recognition_evidence,
        policy,
        reference_retrievals=reference_retrievals,
        seam_evidence=seam_evidence,
        normalized_audio_path=normalized_audio_path,
    )
    if stored != expected:
        raise CorrectionExecutionError(
            "CorrectionAuditExecutionPlan is not reproducible from exact immutable inputs"
        )
    return expected


__all__ = [
    "CORRECTION_EXECUTION_PLANNER_ID",
    "CORRECTION_EXECUTION_PLANNER_VERSION",
    "CorrectionExecutionError",
    "PcmWavTopology",
    "assert_correction_audit_execution_plan",
    "build_correction_audit_execution_plan",
    "correction_execution_planner_code_hash",
    "default_correction_audit_execution_policy",
    "derive_pcm_wav_clip_bytes",
    "materialize_audio_correction_packet_sources",
    "materialize_text_correction_packet_sources",
    "materialize_text_correction_packet_sources_batch",
]
