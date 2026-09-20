"""Minimal, replay-safe normalized-WAV to accurate-SRT orchestration.

This entry point intentionally starts *after* audio normalization.  It joins
the two offline recognizers, conservative reference-aware correction, and the
text-preserving Chinese semantic projection without importing any production
or normalization workflow.
"""

from __future__ import annotations

import json
import os
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Literal, Sequence

from shared.schemas.podcast_subtitles_v2 import (
    RecognitionEvidence,
    recognition_evidence_content_hash,
)

from .accurate_correction import (
    AccurateCorrectionResult,
    CorrectionReferenceSource,
    CorrectionReviewSelection,
    apply_review_selections,
    bounded_review_packets,
    correct_recognition,
    render_accurate_correction_json,
)
from .accurate_punctuation import derive_qwen_sentence_hints
from .accurate_recognition import (
    ACCURATE_RECOGNITION_RUNNER_VERSION,
    AccurateRecognitionResult,
    run_accurate_recognition,
)
from .accurate_segmentation import segment_accurate_subtitles
from .episode_edits import EpisodeTranscriptEdit, apply_episode_transcript_edits
from .hashing import canonical_json_bytes, hash_object, measure_regular_file, sha256_bytes
from .profiles import HORIZONTAL_16X9

ACCURATE_SUBTITLE_PIPELINE_VERSION = 2
_REVIEW_CONTEXT_RADIUS = 40
_PAUSE_PREFERENCE_MS = 240
_STRONG_PAUSE_MS = 450


@dataclass(frozen=True, slots=True)
class AccurateSubtitlePipelineResult:
    """Published artifacts from one completed, possibly-reviewable run."""

    status: Literal["completed", "completed_with_review"]
    episode_id: str
    normalized_audio_hash: str
    output_dir: Path
    recognition_path: Path
    correction_path: Path
    review_path: Path
    srt_path: Path
    manifest_path: Path
    unresolved_correction_count: int
    seam_conflict_count: int
    owned_range_repair_count: int
    corroborating_segment_boundary_repair_count: int
    corroborating_seam_conflict_count: int
    corroborating_owned_range_repair_count: int
    boundary_review_count: int

    @property
    def qwen_seam_conflict_count(self) -> int:
        return self.seam_conflict_count

    @property
    def qwen_owned_range_repair_count(self) -> int:
        return self.owned_range_repair_count

    @property
    def faster_segment_boundary_repair_count(self) -> int:
        return self.corroborating_segment_boundary_repair_count

    @property
    def faster_seam_conflict_count(self) -> int:
        return self.corroborating_seam_conflict_count

    @property
    def faster_owned_range_repair_count(self) -> int:
        return self.corroborating_owned_range_repair_count


@dataclass(frozen=True, slots=True)
class _LoadedReference:
    kind: Literal["book", "outline"]
    original_path: Path
    title: str
    payload: bytes
    sha256: str


@dataclass(frozen=True, slots=True)
class _PreparedInputs:
    episode_id: str
    audio_path: Path
    audio_hash: str
    audio_size: int
    root: Path
    terms: tuple[str, ...]
    loaded_references: tuple[_LoadedReference, ...]


def _required_identity(value: str, *, label: str) -> str:
    if not isinstance(value, str) or not value.strip() or value != value.strip():
        raise ValueError(f"{label} must be non-blank and trimmed")
    return value


def _normalise_glossary(values: Sequence[str]) -> tuple[str, ...]:
    terms: list[str] = []
    seen: set[str] = set()
    for index, value in enumerate(values):
        if not isinstance(value, str) or not value.strip():
            raise ValueError(f"glossary[{index}] must be a non-blank string")
        term = value.strip()
        if term in seen:
            continue
        seen.add(term)
        terms.append(term)
    return tuple(terms)


def _load_reference(
    path: Path,
    *,
    kind: Literal["book", "outline"],
) -> _LoadedReference:
    resolved = Path(path).resolve()
    if not resolved.is_file():
        raise FileNotFoundError(f"{kind} reference does not exist: {resolved}")
    digest, size = measure_regular_file(resolved)
    payload = resolved.read_bytes()
    if len(payload) != size or sha256_bytes(payload) != digest:
        raise ValueError(f"{kind} reference changed while it was read: {resolved}")
    try:
        decoded = payload.decode("utf-8-sig")
    except UnicodeDecodeError as exc:
        raise ValueError(f"{kind} reference must be UTF-8 text: {resolved}") from exc
    if not decoded.strip():
        raise ValueError(f"{kind} reference must not be empty: {resolved}")
    return _LoadedReference(
        kind=kind,
        original_path=resolved,
        title=resolved.stem,
        payload=payload,
        sha256=digest,
    )


def _atomic_publish(path: Path, payload: bytes) -> None:
    """Publish named output last-write-atomically and skip identical replay."""

    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        if not path.is_file():
            raise ValueError(f"artifact target is not a regular file: {path}")
        if path.read_bytes() == payload:
            return
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


def _publish_snapshot(root: Path, *, label: str, digest: str, payload: bytes) -> Path:
    path = root / "references" / f"{label}-{digest}.txt"
    if path.exists():
        if not path.is_file() or path.read_bytes() != payload:
            raise ValueError(f"content-addressed reference snapshot is corrupt: {path}")
        return path
    _atomic_publish(path, payload)
    return path


def _build_reference_sources(
    root: Path,
    *,
    loaded_references: Sequence[_LoadedReference],
    glossary_terms: Sequence[str],
) -> tuple[tuple[CorrectionReferenceSource, ...], tuple[dict[str, object], ...]]:
    sources: list[CorrectionReferenceSource] = []
    records: list[dict[str, object]] = []
    kind_counts: dict[str, int] = {"book": 0, "outline": 0}
    for loaded in loaded_references:
        kind_counts[loaded.kind] += 1
        ordinal = kind_counts[loaded.kind]
        snapshot = _publish_snapshot(
            root,
            label=loaded.kind,
            digest=loaded.sha256,
            payload=loaded.payload,
        )
        text = loaded.payload.decode("utf-8-sig")
        source_id = f"{loaded.kind}-{ordinal:03d}-{loaded.sha256[:12]}"
        sources.append(
            CorrectionReferenceSource(
                source_id=source_id,
                kind=loaded.kind,
                locator=snapshot.resolve().as_uri(),
                text=text,
                title=loaded.title,
            )
        )
        records.append(
            {
                "source_id": source_id,
                "kind": loaded.kind,
                "title": loaded.title,
                "original_uri": loaded.original_path.as_uri(),
                "snapshot_uri": snapshot.resolve().as_uri(),
                "sha256": loaded.sha256,
                "size_bytes": len(loaded.payload),
            }
        )

    if glossary_terms:
        glossary_payload = ("\n".join(glossary_terms) + "\n").encode("utf-8")
        glossary_hash = sha256_bytes(glossary_payload)
        snapshot = _publish_snapshot(
            root,
            label="glossary",
            digest=glossary_hash,
            payload=glossary_payload,
        )
        source_id = f"glossary-001-{glossary_hash[:12]}"
        sources.append(
            CorrectionReferenceSource(
                source_id=source_id,
                kind="glossary",
                locator=snapshot.resolve().as_uri(),
                text=glossary_payload.decode("utf-8"),
                title="curated episode glossary",
            )
        )
        records.append(
            {
                "source_id": source_id,
                "kind": "glossary",
                "title": "curated episode glossary",
                "original_uri": "argument:glossary",
                "snapshot_uri": snapshot.resolve().as_uri(),
                "sha256": glossary_hash,
                "size_bytes": len(glossary_payload),
            }
        )
    return tuple(sources), tuple(records)


def _prepare_inputs(
    *,
    audio: Path,
    output_dir: Path,
    episode_id: str,
    book_paths: Sequence[Path],
    outline_paths: Sequence[Path],
    glossary_terms: Sequence[str],
) -> _PreparedInputs:
    episode_id = _required_identity(episode_id, label="episode_id")
    audio_path = Path(audio).resolve()
    if not audio_path.is_file():
        raise FileNotFoundError(f"normalized WAV does not exist: {audio_path}")
    audio_hash, audio_size = measure_regular_file(audio_path)
    terms = _normalise_glossary(glossary_terms)
    loaded_references = tuple(
        [
            *(_load_reference(path, kind="book") for path in book_paths),
            *(_load_reference(path, kind="outline") for path in outline_paths),
        ]
    )
    root = Path(output_dir).resolve()
    root.mkdir(parents=True, exist_ok=True)
    return _PreparedInputs(
        episode_id=episode_id,
        audio_path=audio_path,
        audio_hash=audio_hash,
        audio_size=audio_size,
        root=root,
        terms=terms,
        loaded_references=loaded_references,
    )


def _validated_evidence_payload(
    path: Path,
    evidence: RecognitionEvidence,
    *,
    role: str,
) -> bytes:
    resolved = Path(path).resolve()
    if not resolved.is_file():
        raise FileNotFoundError(f"{role} recognition Evidence does not exist: {resolved}")
    measured_hash, measured_size = measure_regular_file(resolved)
    expected = canonical_json_bytes(evidence)
    if (measured_hash, measured_size) != (sha256_bytes(expected), len(expected)):
        raise ValueError(f"{role} recognition Evidence path does not contain its typed evidence")
    return expected


def _validate_recognition_result(
    prepared: _PreparedInputs,
    recognition: AccurateRecognitionResult,
) -> tuple[bytes, bytes]:
    if not isinstance(recognition, AccurateRecognitionResult):
        raise TypeError("recognition must be an AccurateRecognitionResult")
    if recognition.episode_id != prepared.episode_id:
        raise ValueError("recognition result belongs to a different episode")
    if recognition.audio_sha256 != prepared.audio_hash:
        raise ValueError("recognition result is not bound to the measured normalized WAV")
    _required_identity(recognition.invocation_id, label="recognition invocation_id")
    for role, evidence in (
        ("primary", recognition.primary_evidence),
        ("corroborating", recognition.corroborating_evidence),
    ):
        if evidence.episode_id != recognition.episode_id:
            raise ValueError(f"{role} recognition Evidence belongs to a different episode")
        if evidence.invocation_id != recognition.invocation_id:
            raise ValueError(f"{role} recognition Evidence belongs to a different invocation")
        if evidence.normalized_audio_hash != recognition.audio_sha256:
            raise ValueError(f"{role} recognition Evidence crossed the normalized audio clock")
    primary_payload = _validated_evidence_payload(
        recognition.primary_evidence_path,
        recognition.primary_evidence,
        role="primary",
    )
    corroborating_payload = _validated_evidence_payload(
        recognition.corroborating_evidence_path,
        recognition.corroborating_evidence,
        role="corroborating",
    )
    return primary_payload, corroborating_payload


def _artifact_record(path: Path, payload: bytes) -> dict[str, object]:
    return {
        "uri": path.resolve().as_uri(),
        "sha256": sha256_bytes(payload),
        "size_bytes": len(payload),
    }


def _evidence_pointer(
    evidence: RecognitionEvidence,
    payload: bytes,
) -> dict[str, object]:
    return {
        # This pointer is content-addressed rather than workspace-addressed so
        # injecting the same completed Evidence cannot change recognition.json.
        "sha256": sha256_bytes(payload),
        "size_bytes": len(payload),
        "evidence_hash": recognition_evidence_content_hash(evidence),
        "adapter": getattr(evidence, "adapter"),
        "model": getattr(evidence, "model"),
        "token_count": len(getattr(evidence, "tokens")),
    }


def _recognition_roles(recognition: AccurateRecognitionResult) -> dict[str, object]:
    def record(provider: str, evidence: RecognitionEvidence) -> dict[str, object]:
        return {
            "provider": provider,
            "adapter": evidence.adapter,
            "model": evidence.model,
            "evidence_hash": recognition_evidence_content_hash(evidence),
        }

    return {
        "execution_order": ("qwen", "faster_whisper"),
        "correction_base": record("faster_whisper", recognition.faster_evidence),
        "corroborator": record("qwen", recognition.qwen_evidence),
        "punctuation_source": record("qwen", recognition.qwen_evidence),
    }


def _segment_boundary_repair_review(
    recognition: AccurateRecognitionResult,
) -> dict[str, object]:
    repairs = recognition.faster_segment_boundary_repairs
    return {
        "type": "faster_segment_boundary_repair",
        "status": "review_recommended" if repairs else "clear",
        "count": len(repairs),
        "items": repairs,
    }


def _normalise_review_selections(
    values: Sequence[CorrectionReviewSelection],
) -> tuple[CorrectionReviewSelection, ...]:
    """Validate and canonically order the closed-vocabulary review response."""

    selections = tuple(values)
    for index, selection in enumerate(selections):
        if not isinstance(selection, CorrectionReviewSelection):
            raise TypeError(
                f"review_selections[{index}] must be a CorrectionReviewSelection"
            )
        if selection.choice not in {"current", "candidate", "defer"}:
            raise ValueError(
                f"review_selections[{index}] has unsupported choice: {selection.choice}"
            )
    decision_ids = [selection.decision_id for selection in selections]
    if len(set(decision_ids)) != len(decision_ids):
        raise ValueError("review selection decision_id values must be unique")
    return tuple(sorted(selections, key=lambda item: item.decision_id))


def _normalise_episode_edits(
    values: Sequence[EpisodeTranscriptEdit],
) -> tuple[EpisodeTranscriptEdit, ...]:
    edits = tuple(values)
    for index, edit in enumerate(edits):
        if not isinstance(edit, EpisodeTranscriptEdit):
            raise TypeError(f"transcript_edits[{index}] must be an EpisodeTranscriptEdit")
    ids = [edit.id for edit in edits]
    if len(ids) != len(set(ids)):
        raise ValueError("transcript edit ids must be unique")
    return tuple(sorted(edits, key=lambda item: (item.start_ms, item.end_ms, item.id)))


def _episode_edit_receipt(
    edits: Sequence[EpisodeTranscriptEdit],
) -> dict[str, object]:
    records = tuple(
        {
            "id": edit.id,
            "start_ms": edit.start_ms,
            "end_ms": edit.end_ms,
            "current": edit.current,
            "replacement": edit.replacement,
            "evidence": edit.evidence,
            "confidence": edit.confidence,
        }
        for edit in edits
    )
    return {
        "schema_version": 1,
        "identity": f"episode-transcript-edits-{hash_object(records)}",
        "edit_count": len(records),
        "items": records,
    }


def _review_selection_receipt(
    *,
    source_correction_payload: bytes,
    selections: Sequence[CorrectionReviewSelection],
) -> dict[str, object]:
    records = tuple(
        {
            "decision_id": selection.decision_id,
            "choice": selection.choice,
            "candidate_index": selection.candidate_index,
        }
        for selection in selections
    )
    selection_hash = hash_object(
        {
            "schema_version": 1,
            "selections": records,
        }
    )
    source_correction_hash = sha256_bytes(source_correction_payload)
    identity_hash = hash_object(
        {
            "source_correction_sha256": source_correction_hash,
            "selection_sha256": selection_hash,
        }
    )
    return {
        "schema_version": 1,
        "identity": f"correction-review-selections-{identity_hash}",
        "selection_count": len(records),
        "selection_sha256": selection_hash,
        "source_correction_sha256": source_correction_hash,
        "selections": records,
    }


def _render_correction_with_review_receipt(
    correction: AccurateCorrectionResult,
    *,
    receipt: dict[str, object],
    transcript_edit_receipt: dict[str, object],
) -> bytes:
    payload = json.loads(render_accurate_correction_json(correction))
    payload["review_selection_receipt"] = receipt
    payload["transcript_edit_receipt"] = transcript_edit_receipt
    return (
        json.dumps(payload, ensure_ascii=False, sort_keys=True, indent=2) + "\n"
    ).encode("utf-8")


def _publish_prepared_artifacts(
    *,
    prepared: _PreparedInputs,
    recognition: AccurateRecognitionResult,
    review_selections: Sequence[CorrectionReviewSelection],
    transcript_edits: Sequence[EpisodeTranscriptEdit],
) -> AccurateSubtitlePipelineResult:
    episode_id = prepared.episode_id
    audio_path = prepared.audio_path
    audio_hash = prepared.audio_hash
    audio_size = prepared.audio_size
    terms = prepared.terms
    root = prepared.root
    qwen_evidence_payload, faster_evidence_payload = _validate_recognition_result(
        prepared, recognition
    )
    segment_boundary_repairs = _segment_boundary_repair_review(recognition)
    recognition_roles = _recognition_roles(recognition)

    references, reference_records = _build_reference_sources(
        root,
        loaded_references=prepared.loaded_references,
        glossary_terms=terms,
    )
    base_correction = correct_recognition(
        primary=recognition.faster_evidence,
        corroborating=recognition.qwen_evidence,
        references=references,
    )
    selection_items = _normalise_review_selections(review_selections)
    source_correction_payload = render_accurate_correction_json(base_correction).encode(
        "utf-8"
    )
    selection_receipt = _review_selection_receipt(
        source_correction_payload=source_correction_payload,
        selections=selection_items,
    )
    correction = (
        apply_review_selections(base_correction, selection_items)
        if selection_items
        else base_correction
    )
    edit_items = _normalise_episode_edits(transcript_edits)
    transcript_edit_receipt = _episode_edit_receipt(edit_items)
    correction = (
        apply_episode_transcript_edits(correction, edit_items)
        if edit_items
        else correction
    )
    provider_boundaries = derive_qwen_sentence_hints(
        primary=recognition.qwen_evidence,
        primary_owned_range_repairs=recognition.qwen_owned_range_repairs,
        corrected_tokens=correction.tokens,
    )
    correction_payload = _render_correction_with_review_receipt(
        correction,
        receipt=selection_receipt,
        transcript_edit_receipt=transcript_edit_receipt,
    )
    correction_path = root / "correction.json"
    _atomic_publish(correction_path, correction_payload)

    generation_id = "accurate-subtitle-" + hash_object(
        {
            "pipeline_version": ACCURATE_SUBTITLE_PIPELINE_VERSION,
            "episode_id": episode_id,
            "audio_sha256": audio_hash,
            "correction_sha256": sha256_bytes(correction_payload),
            "protected_terms": terms,
            "profile": HORIZONTAL_16X9,
            "pause_preference_ms": _PAUSE_PREFERENCE_MS,
            "strong_pause_ms": _STRONG_PAUSE_MS,
            "provider_sentence_boundaries": provider_boundaries.identity,
            "review_selection_identity": selection_receipt["identity"],
            "transcript_edit_identity": transcript_edit_receipt["identity"],
        }
    )
    segmentation = segment_accurate_subtitles(
        correction.tokens,
        episode_id=episode_id,
        generation_id=generation_id,
        protected_terms=terms,
        pause_preference_ms=_PAUSE_PREFERENCE_MS,
        strong_pause_ms=_STRONG_PAUSE_MS,
        sentence_hints=provider_boundaries.sentence_hints,
    )
    rendered_text = "".join(line for cue in segmentation.projection.cues for line in cue.lines)
    if rendered_text != correction.text:
        raise RuntimeError("final SRT projection is not an exact copy of corrected text")

    recognition_payload = (
        canonical_json_bytes(
            {
                "schema_version": 3,
                "episode_id": episode_id,
                "invocation_id": recognition.invocation_id,
                "normalized_audio_sha256": audio_hash,
                "roles": recognition_roles,
                "qwen": _evidence_pointer(
                    recognition.qwen_evidence,
                    qwen_evidence_payload,
                ),
                "faster_whisper": _evidence_pointer(
                    recognition.faster_evidence,
                    faster_evidence_payload,
                ),
                "qwen_seam_conflicts": recognition.qwen_seam_conflicts,
                "qwen_owned_range_repairs": recognition.qwen_owned_range_repairs,
                "qwen_provider_sentence_boundaries": (
                    provider_boundaries.recognition_receipt()
                ),
                "faster_segment_boundary_repairs": segment_boundary_repairs,
                "faster_seam_conflicts": (
                    recognition.faster_seam_conflicts
                ),
                "faster_owned_range_repairs": (
                    recognition.faster_owned_range_repairs
                ),
            }
        )
        + b"\n"
    )
    recognition_path = root / "recognition.json"
    _atomic_publish(recognition_path, recognition_payload)

    review_packets = bounded_review_packets(correction, radius=_REVIEW_CONTEXT_RADIUS)
    applied_selection_ids = {
        selection.decision_id
        for selection in selection_items
        if selection.choice != "defer"
    }
    applied_review_decisions = tuple(
        decision
        for decision in correction.applied
        if decision.id in applied_selection_ids
    )
    review_payload = (
        canonical_json_bytes(
            {
                "schema_version": 2,
                "status": (
                    "review_recommended"
                    if (
                        recognition.qwen_seam_conflicts
                        or recognition.qwen_owned_range_repairs
                        or recognition.faster_segment_boundary_repairs
                        or recognition.faster_seam_conflicts
                        or recognition.faster_owned_range_repairs
                        or correction.unresolved
                        or segmentation.boundary_reviews
                    )
                    else "clear"
                ),
                "counts": {
                    "qwen_seam_conflicts": len(recognition.qwen_seam_conflicts),
                    "qwen_owned_range_repairs": len(recognition.qwen_owned_range_repairs),
                    "faster_segment_boundary_repairs": len(
                        recognition.faster_segment_boundary_repairs
                    ),
                    "faster_seam_conflicts": len(
                        recognition.faster_seam_conflicts
                    ),
                    "faster_owned_range_repairs": len(
                        recognition.faster_owned_range_repairs
                    ),
                    "unresolved_corrections": len(correction.unresolved),
                    "boundary_reviews": len(segmentation.boundary_reviews),
                },
                "qwen_seam_conflicts": recognition.qwen_seam_conflicts,
                "qwen_owned_range_repairs": recognition.qwen_owned_range_repairs,
                "faster_segment_boundary_repairs": segment_boundary_repairs,
                "faster_seam_conflicts": recognition.faster_seam_conflicts,
                "faster_owned_range_repairs": (
                    recognition.faster_owned_range_repairs
                ),
                "unresolved_corrections": correction.unresolved,
                "correction_review_packets": review_packets,
                "review_selection_receipt": selection_receipt,
                "transcript_edit_receipt": transcript_edit_receipt,
                "applied_review_decisions": applied_review_decisions,
                "boundary_reviews": segmentation.boundary_reviews,
            }
        )
        + b"\n"
    )
    review_path = root / "review.json"
    _atomic_publish(review_path, review_payload)

    srt_payload = segmentation.srt_text.encode("utf-8")
    srt_path = root / "final.srt"
    _atomic_publish(srt_path, srt_payload)

    config = {
        "pipeline_version": ACCURATE_SUBTITLE_PIPELINE_VERSION,
        "recognition_runner_version": ACCURATE_RECOGNITION_RUNNER_VERSION,
        "reference_policy": "audio-and-two-asr-before-reference",
        "review_context_radius": _REVIEW_CONTEXT_RADIUS,
        "segmentation_profile": HORIZONTAL_16X9,
        "pause_preference_ms": _PAUSE_PREFERENCE_MS,
        "strong_pause_ms": _STRONG_PAUSE_MS,
        "protected_terms": terms,
        "provider_sentence_boundaries": provider_boundaries.identity,
        "review_selection_identity": selection_receipt["identity"],
        "transcript_edit_identity": transcript_edit_receipt["identity"],
        "recognition_roles": recognition_roles,
    }
    status: Literal["completed", "completed_with_review"] = (
        "completed_with_review"
        if (
            recognition.qwen_seam_conflicts
            or recognition.qwen_owned_range_repairs
            or recognition.faster_segment_boundary_repairs
            or recognition.faster_seam_conflicts
            or recognition.faster_owned_range_repairs
            or correction.unresolved
            or segmentation.boundary_reviews
        )
        else "completed"
    )
    manifest_payload = (
        canonical_json_bytes(
            {
                "schema_version": 2,
                "pipeline": "podcast-subtitle-accurate",
                "status": status,
                "episode_id": episode_id,
                "invocation_id": recognition.invocation_id,
                "generation_id": generation_id,
                "input": {
                    "normalized_audio_uri": audio_path.as_uri(),
                    "normalized_audio_sha256": audio_hash,
                    "normalized_audio_size_bytes": audio_size,
                },
                "references": reference_records,
                "glossary_terms": terms,
                "review_selection_receipt": selection_receipt,
                "transcript_edit_receipt": transcript_edit_receipt,
                "recognition_roles": recognition_roles,
                "config": config,
                "config_hash": hash_object(config),
                "review_counts": {
                    "qwen_seam_conflicts": len(recognition.qwen_seam_conflicts),
                    "qwen_owned_range_repairs": len(recognition.qwen_owned_range_repairs),
                    "faster_segment_boundary_repairs": len(
                        recognition.faster_segment_boundary_repairs
                    ),
                    "faster_seam_conflicts": len(
                        recognition.faster_seam_conflicts
                    ),
                    "faster_owned_range_repairs": len(
                        recognition.faster_owned_range_repairs
                    ),
                    "unresolved_corrections": len(correction.unresolved),
                    "boundary_reviews": len(segmentation.boundary_reviews),
                },
                "artifacts": {
                    "recognition": _artifact_record(recognition_path, recognition_payload),
                    "correction": _artifact_record(correction_path, correction_payload),
                    "review": _artifact_record(review_path, review_payload),
                    "srt": _artifact_record(srt_path, srt_payload),
                },
                "invariants": {
                    "audio_and_two_asr_are_transcript_truth": True,
                    "references_cannot_insert_unrecognised_text": True,
                    "final_srt_is_exact_copy_of_corrected_text": True,
                    "provider_punctuation_is_boundary_signal_only": True,
                    "faster_whisper_is_correction_base": True,
                    "qwen_is_corroborator_and_punctuation_source": True,
                    "unresolved_items_are_non_blocking": True,
                },
            }
        )
        + b"\n"
    )
    manifest_path = root / "manifest.json"
    _atomic_publish(manifest_path, manifest_payload)

    return AccurateSubtitlePipelineResult(
        status=status,
        episode_id=episode_id,
        normalized_audio_hash=audio_hash,
        output_dir=root,
        recognition_path=recognition_path,
        correction_path=correction_path,
        review_path=review_path,
        srt_path=srt_path,
        manifest_path=manifest_path,
        unresolved_correction_count=len(correction.unresolved),
        seam_conflict_count=len(recognition.primary_seam_conflicts),
        owned_range_repair_count=len(recognition.primary_owned_range_repairs),
        corroborating_segment_boundary_repair_count=len(
            recognition.corroborating_segment_boundary_repairs
        ),
        corroborating_seam_conflict_count=len(
            recognition.corroborating_seam_conflicts
        ),
        corroborating_owned_range_repair_count=len(
            recognition.corroborating_owned_range_repairs
        ),
        boundary_review_count=len(segmentation.boundary_reviews),
    )


def publish_accurate_subtitle_artifacts(
    *,
    recognition: AccurateRecognitionResult,
    audio: Path,
    output_dir: Path,
    episode_id: str,
    book_paths: Sequence[Path] = (),
    outline_paths: Sequence[Path] = (),
    glossary_terms: Sequence[str] = (),
    review_selections: Sequence[CorrectionReviewSelection] = (),
    transcript_edits: Sequence[EpisodeTranscriptEdit] = (),
) -> AccurateSubtitlePipelineResult:
    """Publish correction, review, and SRT from completed immutable ASR evidence.

    This path performs no recognition call.  It authenticates the audio clock
    and both Evidence files before using the same post-processing implementation
    as :func:`run_accurate_subtitle_pipeline`.
    """

    prepared = _prepare_inputs(
        audio=audio,
        output_dir=output_dir,
        episode_id=episode_id,
        book_paths=book_paths,
        outline_paths=outline_paths,
        glossary_terms=glossary_terms,
    )
    return _publish_prepared_artifacts(
        prepared=prepared,
        recognition=recognition,
        review_selections=review_selections,
        transcript_edits=transcript_edits,
    )


def run_accurate_subtitle_pipeline(
    *,
    audio: Path,
    output_dir: Path,
    episode_id: str,
    book_paths: Sequence[Path] = (),
    outline_paths: Sequence[Path] = (),
    glossary_terms: Sequence[str] = (),
    review_selections: Sequence[CorrectionReviewSelection] = (),
    transcript_edits: Sequence[EpisodeTranscriptEdit] = (),
    invocation_id: str | None = None,
) -> AccurateSubtitlePipelineResult:
    """Run both recognizers once, then publish the authenticated subtitle artifacts."""

    prepared = _prepare_inputs(
        audio=audio,
        output_dir=output_dir,
        episode_id=episode_id,
        book_paths=book_paths,
        outline_paths=outline_paths,
        glossary_terms=glossary_terms,
    )
    recognition = run_accurate_recognition(
        audio=prepared.audio_path,
        # Keep the durable ASR workspace at the established V2 checkpoint root.
        # Together with an explicit invocation_id this lets an interrupted run
        # replay completed chunks instead of submitting them to either model.
        output_dir=prepared.root / ".subtitle-v2",
        episode_id=prepared.episode_id,
        invocation_id=invocation_id,
    )
    return _publish_prepared_artifacts(
        prepared=prepared,
        recognition=recognition,
        review_selections=review_selections,
        transcript_edits=transcript_edits,
    )


__all__ = [
    "ACCURATE_SUBTITLE_PIPELINE_VERSION",
    "AccurateSubtitlePipelineResult",
    "publish_accurate_subtitle_artifacts",
    "run_accurate_subtitle_pipeline",
]
