"""Replayable recognition-only transcript-candidate materialization.

This slice verifies stored request/evidence semantics and deterministic
derivation. Re-executing a recognition adapter and custody of provider raw
bytes are deliberately outside its scope; raw bytes remain digest-addressed.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, ClassVar, Literal, Sequence

from pydantic import BaseModel, ConfigDict, field_validator, model_validator

from shared.schemas.podcast_subtitles_v2 import (
    EvidenceToken,
    RecognitionEvidence,
    recognition_evidence_content_hash,
)

from .hashing import canonical_json_bytes, hash_file, hash_object
from .recognition_request import RecognitionRequestArtifactV1
from .transcript_gold import (
    AudioClipBinding,
    CandidateClipOutput,
    TranscriptAnnotationPacket,
)

CANDIDATE_MATERIALIZER_ID = "nakama-source-bound-transcript-candidate-v3"
CANDIDATE_MATERIALIZER_VERSION = "3.0.0"


def candidate_materializer_code_hash() -> str:
    """Return the exact executable source identity required for V3 replay."""

    return hash_file(Path(__file__))


class _StrictFrozenModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)


def _artifact_hash(model: BaseModel, *, field: str, kind: str) -> str:
    return hash_object(
        {"artifact_kind": kind, **model.model_dump(mode="json", exclude={field})}
    )


class CandidateSourceSpanV3(_StrictFrozenModel):
    schema_version: Literal[3] = 3
    span_id: str
    source_kind: Literal[
        "recognition_token", "no_text_emitted_by_recognizer"
    ]
    start_ms: int
    end_ms: int
    text: str
    evidence_token_id: str | None

    @model_validator(mode="after")
    def _valid(self) -> "CandidateSourceSpanV3":
        if not self.span_id or self.start_ms < 0 or self.end_ms <= self.start_ms:
            raise ValueError("candidate source span has invalid identity or interval")
        if self.source_kind == "recognition_token":
            if not self.text or not self.evidence_token_id:
                raise ValueError("recognition-token span requires source token and text")
        elif self.text or self.evidence_token_id is not None:
            raise ValueError("no-output span must carry empty text and no token identity")
        return self


class SourceBoundCandidateClipV3(_StrictFrozenModel):
    _HASH_KIND: ClassVar[str] = "source_bound_candidate_clip_v3"

    schema_version: Literal[3] = 3
    clip: AudioClipBinding
    recognition_request: RecognitionRequestArtifactV1
    request_canonical_hash: str
    request_content_hash: str
    recognition_evidence: RecognitionEvidence
    evidence_canonical_hash: str
    evidence_content_hash: str
    spans: tuple[CandidateSourceSpanV3, ...]
    coverage_hash: str
    output: CandidateClipOutput
    materialization_hash: str

    @field_validator(
        "request_canonical_hash",
        "request_content_hash",
        "evidence_canonical_hash",
        "evidence_content_hash",
        "coverage_hash",
        "materialization_hash",
    )
    @classmethod
    def _hash(cls, value: str) -> str:
        if len(value) != 64 or any(c not in "0123456789abcdef" for c in value):
            raise ValueError("candidate materialization hashes must be lowercase SHA-256")
        return value

    @model_validator(mode="after")
    def _valid(self) -> "SourceBoundCandidateClipV3":
        request = self.recognition_request
        evidence = self.recognition_evidence
        if (
            request.normalized_audio_sha256 != self.clip.clip_audio_hash
            or request.normalized_audio_size_bytes != self.clip.clip_audio_size_bytes
        ):
            raise ValueError("recognition request is not bound to exact clip bytes")
        if request.episode_id != evidence.episode_id:
            raise ValueError("recognition request/evidence episode mismatch")
        if request.invocation_id != evidence.invocation_id:
            raise ValueError("recognition request/evidence invocation mismatch")
        if evidence.normalized_audio_hash != self.clip.clip_audio_hash:
            raise ValueError("recognition evidence is not bound to exact clip bytes")
        if self.request_canonical_hash != hash_object(request):
            raise ValueError("recognition request canonical hash mismatch")
        if self.request_content_hash != request.content_hash:
            raise ValueError("recognition request content hash mismatch")
        if self.evidence_canonical_hash != hash_object(evidence):
            raise ValueError("recognition evidence canonical hash mismatch")
        if self.evidence_content_hash != recognition_evidence_content_hash(evidence):
            raise ValueError("recognition evidence content hash mismatch")
        expected = _derive_spans(self.clip, evidence.tokens)
        if self.spans != expected:
            raise ValueError("candidate source spans do not replay from recognition evidence")
        if self.coverage_hash != hash_object(self.spans):
            raise ValueError("candidate source coverage hash mismatch")
        text = "".join(span.text for span in self.spans)
        tokens = tuple(
            span.text for span in self.spans if span.source_kind == "recognition_token"
        )
        if self.output != CandidateClipOutput.build(
            clip=self.clip,
            outcome="accepted",
            text=text,
            tokens=tokens,
        ):
            raise ValueError("candidate output does not replay from recognition evidence")
        if self.output.corrections:
            raise ValueError("recognition-only candidate cannot carry corrections")
        if self.materialization_hash != _artifact_hash(
            self, field="materialization_hash", kind=self._HASH_KIND
        ):
            raise ValueError("candidate clip materialization hash mismatch")
        return self


class CandidateMaterializationReceiptV3(_StrictFrozenModel):
    _HASH_KIND: ClassVar[str] = "candidate_materialization_receipt_v3"

    schema_version: Literal[3] = 3
    materializer_id: str
    materializer_version: str
    materializer_code_hash: str
    annotation_packet_hash: str
    request_canonical_hashes: tuple[str, ...]
    request_content_hashes: tuple[str, ...]
    evidence_canonical_hashes: tuple[str, ...]
    evidence_content_hashes: tuple[str, ...]
    coverage_hashes: tuple[str, ...]
    output_hashes: tuple[str, ...]
    materialization_hashes: tuple[str, ...]
    receipt_hash: str

    @field_validator(
        "materializer_code_hash",
        "annotation_packet_hash",
        "receipt_hash",
    )
    @classmethod
    def _single_hash(cls, value: str) -> str:
        if len(value) != 64 or any(c not in "0123456789abcdef" for c in value):
            raise ValueError("receipt identities must be lowercase SHA-256")
        return value

    @field_validator(
        "request_canonical_hashes",
        "request_content_hashes",
        "evidence_canonical_hashes",
        "evidence_content_hashes",
        "coverage_hashes",
        "output_hashes",
        "materialization_hashes",
    )
    @classmethod
    def _hash_array(cls, values: tuple[str, ...]) -> tuple[str, ...]:
        if any(
            len(value) != 64 or any(c not in "0123456789abcdef" for c in value)
            for value in values
        ):
            raise ValueError("receipt hash arrays must contain lowercase SHA-256")
        return values

    @model_validator(mode="after")
    def _valid(self) -> "CandidateMaterializationReceiptV3":
        if (
            self.materializer_id != CANDIDATE_MATERIALIZER_ID
            or self.materializer_version != CANDIDATE_MATERIALIZER_VERSION
            or self.materializer_code_hash != candidate_materializer_code_hash()
        ):
            raise ValueError("candidate materializer executable identity mismatch")
        lengths = {
            len(self.request_canonical_hashes),
            len(self.request_content_hashes),
            len(self.evidence_canonical_hashes),
            len(self.evidence_content_hashes),
            len(self.coverage_hashes),
            len(self.output_hashes),
            len(self.materialization_hashes),
        }
        if len(lengths) != 1 or lengths == {0}:
            raise ValueError("materialization receipt arrays must be non-empty and aligned")
        if self.receipt_hash != _artifact_hash(
            self, field="receipt_hash", kind=self._HASH_KIND
        ):
            raise ValueError("candidate materialization receipt hash mismatch")
        return self


class SourceBoundTranscriptCandidateArtifactV3(_StrictFrozenModel):
    _HASH_KIND: ClassVar[str] = "transcript_candidate_artifact"

    schema_version: Literal[3] = 3
    evaluation_scope: Literal["recognition_only"] = "recognition_only"
    candidate_id: str
    system_id: str
    generation_id: str
    episode_id: str
    normalized_audio_hash: str
    normalized_audio_size_bytes: int
    annotation_packet_hash: str
    generated_at_utc: Literal["1970-01-01T00:00:00Z"] = "1970-01-01T00:00:00Z"
    expected_clip_count: int
    complete: Literal[True] = True
    clips: tuple[CandidateClipOutput, ...]
    source_materializations: tuple[SourceBoundCandidateClipV3, ...]
    materialization_receipt: CandidateMaterializationReceiptV3
    artifact_hash: str

    @field_validator("candidate_id", "system_id", "episode_id")
    @classmethod
    def _nonblank_id(cls, value: str) -> str:
        if not value.strip() or value != value.strip():
            raise ValueError("V3 candidate identifiers must be non-blank and trimmed")
        return value

    @field_validator(
        "generation_id",
        "normalized_audio_hash",
        "annotation_packet_hash",
        "artifact_hash",
    )
    @classmethod
    def _top_hash(cls, value: str) -> str:
        if len(value) != 64 or any(c not in "0123456789abcdef" for c in value):
            raise ValueError("V3 candidate hashes must be lowercase SHA-256")
        return value

    @model_validator(mode="after")
    def _valid(self) -> "SourceBoundTranscriptCandidateArtifactV3":
        mats = self.source_materializations
        if self.expected_clip_count < 1 or len(mats) != self.expected_clip_count:
            raise ValueError("complete V3 candidate must materialize every clip")
        if self.clips != tuple(item.output for item in mats):
            raise ValueError("V3 candidate outputs drift from source materializations")
        bindings = tuple(item.clip for item in mats)
        expected_bindings = tuple(
            sorted(bindings, key=lambda item: (item.start_ms, item.end_ms, item.clip_id))
        )
        if bindings != expected_bindings:
            raise ValueError("V3 source materializations are not in canonical clip order")
        if len({item.clip_id for item in bindings}) != len(bindings):
            raise ValueError("V3 source materializations contain duplicate clip_id")
        if any(
            item.normalized_audio_hash != self.normalized_audio_hash
            or item.normalized_audio_size_bytes != self.normalized_audio_size_bytes
            for item in bindings
        ):
            raise ValueError("V3 source clip normalized-audio lineage mismatch")
        if any(
            item.recognition_request.episode_id != self.episode_id
            or item.recognition_evidence.episode_id != self.episode_id
            for item in mats
        ):
            raise ValueError("V3 recognition source episode differs from candidate")
        receipt = _build_receipt(self.annotation_packet_hash, mats)
        if self.materialization_receipt != receipt:
            raise ValueError("V3 materialization receipt does not replay")
        if self.generation_id != receipt.receipt_hash:
            raise ValueError("V3 generation_id must equal materialization receipt hash")
        if self.artifact_hash != _artifact_hash(
            self, field="artifact_hash", kind=self._HASH_KIND
        ):
            raise ValueError("transcript candidate artifact_hash mismatch")
        return self

    def canonical_bytes(self) -> bytes:
        return canonical_json_bytes(self)


def _derive_spans(
    clip: AudioClipBinding, tokens: Sequence[EvidenceToken]
) -> tuple[CandidateSourceSpanV3, ...]:
    cursor = 0
    duration = clip.end_ms - clip.start_ms
    result: list[CandidateSourceSpanV3] = []
    for index, token in enumerate(tokens):
        if token.start_ms < cursor:
            raise ValueError("recognition tokens overlap")
        if token.end_ms > duration:
            raise ValueError("recognition token escapes exact clip")
        if token.start_ms > cursor:
            result.append(CandidateSourceSpanV3(
                span_id=f"{clip.clip_id}:gap:{cursor}:{token.start_ms}",
                source_kind="no_text_emitted_by_recognizer",
                start_ms=clip.start_ms + cursor,
                end_ms=clip.start_ms + token.start_ms,
                text="",
                evidence_token_id=None,
            ))
        result.append(CandidateSourceSpanV3(
            span_id=f"{clip.clip_id}:token:{index}:{token.id}",
            source_kind="recognition_token",
            start_ms=clip.start_ms + token.start_ms,
            end_ms=clip.start_ms + token.end_ms,
            text=token.text,
            evidence_token_id=token.id,
        ))
        cursor = token.end_ms
    if cursor < duration:
        result.append(CandidateSourceSpanV3(
            span_id=f"{clip.clip_id}:gap:{cursor}:{duration}",
            source_kind="no_text_emitted_by_recognizer",
            start_ms=clip.start_ms + cursor,
            end_ms=clip.end_ms,
            text="",
            evidence_token_id=None,
        ))
    return tuple(result)


def _build_clip(
    clip: AudioClipBinding,
    request: RecognitionRequestArtifactV1,
    evidence: RecognitionEvidence,
) -> SourceBoundCandidateClipV3:
    spans = _derive_spans(clip, evidence.tokens)
    output = CandidateClipOutput.build(
        clip=clip,
        outcome="accepted",
        text="".join(item.text for item in spans),
        tokens=tuple(item.text for item in spans if item.source_kind == "recognition_token"),
    )
    payload: dict[str, Any] = {
        "schema_version": 3,
        "clip": clip,
        "recognition_request": request,
        "request_canonical_hash": hash_object(request),
        "request_content_hash": request.content_hash,
        "recognition_evidence": evidence,
        "evidence_canonical_hash": hash_object(evidence),
        "evidence_content_hash": recognition_evidence_content_hash(evidence),
        "spans": spans,
        "coverage_hash": hash_object(spans),
        "output": output,
    }
    return SourceBoundCandidateClipV3(
        **payload,
        materialization_hash=hash_object(
            {"artifact_kind": SourceBoundCandidateClipV3._HASH_KIND, **payload}
        ),
    )


def _build_receipt(
    packet_hash: str, mats: Sequence[SourceBoundCandidateClipV3]
) -> CandidateMaterializationReceiptV3:
    payload = {
        "schema_version": 3,
        "materializer_id": CANDIDATE_MATERIALIZER_ID,
        "materializer_version": CANDIDATE_MATERIALIZER_VERSION,
        "materializer_code_hash": candidate_materializer_code_hash(),
        "annotation_packet_hash": packet_hash,
        "request_canonical_hashes": tuple(item.request_canonical_hash for item in mats),
        "request_content_hashes": tuple(item.request_content_hash for item in mats),
        "evidence_canonical_hashes": tuple(item.evidence_canonical_hash for item in mats),
        "evidence_content_hashes": tuple(item.evidence_content_hash for item in mats),
        "coverage_hashes": tuple(item.coverage_hash for item in mats),
        "output_hashes": tuple(hash_object(item.output) for item in mats),
        "materialization_hashes": tuple(item.materialization_hash for item in mats),
    }
    return CandidateMaterializationReceiptV3(
        **payload,
        receipt_hash=hash_object(
            {"artifact_kind": CandidateMaterializationReceiptV3._HASH_KIND, **payload}
        ),
    )


def build_source_bound_transcript_candidate(
    *,
    candidate_id: str,
    system_id: str,
    annotation_packet: TranscriptAnnotationPacket,
    requests: Sequence[RecognitionRequestArtifactV1],
    evidence: Sequence[RecognitionEvidence],
) -> SourceBoundTranscriptCandidateArtifactV3:
    if len(requests) != len(annotation_packet.clips) or len(evidence) != len(requests):
        raise ValueError("V3 candidate requires one request and evidence per packet clip")
    mats = tuple(
        _build_clip(clip, request, result)
        for clip, request, result in zip(annotation_packet.clips, requests, evidence, strict=True)
    )
    receipt = _build_receipt(annotation_packet.packet_hash, mats)
    payload: dict[str, Any] = {
        "schema_version": 3,
        "evaluation_scope": "recognition_only",
        "candidate_id": candidate_id,
        "system_id": system_id,
        "generation_id": receipt.receipt_hash,
        "episode_id": annotation_packet.episode_id,
        "normalized_audio_hash": annotation_packet.normalized_audio_hash,
        "normalized_audio_size_bytes": annotation_packet.normalized_audio_size_bytes,
        "annotation_packet_hash": annotation_packet.packet_hash,
        "generated_at_utc": "1970-01-01T00:00:00Z",
        "expected_clip_count": len(annotation_packet.clips),
        "complete": True,
        "clips": tuple(item.output for item in mats),
        "source_materializations": mats,
        "materialization_receipt": receipt,
    }
    return SourceBoundTranscriptCandidateArtifactV3(
        **payload,
        artifact_hash=hash_object(
            {"artifact_kind": SourceBoundTranscriptCandidateArtifactV3._HASH_KIND, **payload}
        ),
    )


def verify_source_bound_transcript_candidate(
    candidate: SourceBoundTranscriptCandidateArtifactV3,
    annotation_packet: TranscriptAnnotationPacket,
) -> SourceBoundTranscriptCandidateArtifactV3:
    replayed = build_source_bound_transcript_candidate(
        candidate_id=candidate.candidate_id,
        system_id=candidate.system_id,
        annotation_packet=annotation_packet,
        requests=tuple(item.recognition_request for item in candidate.source_materializations),
        evidence=tuple(item.recognition_evidence for item in candidate.source_materializations),
    )
    if replayed != candidate:
        raise ValueError("V3 candidate differs from deterministic source-bound replay")
    return candidate


__all__ = [
    "CANDIDATE_MATERIALIZER_ID",
    "CANDIDATE_MATERIALIZER_VERSION",
    "CandidateMaterializationReceiptV3",
    "CandidateSourceSpanV3",
    "SourceBoundCandidateClipV3",
    "SourceBoundTranscriptCandidateArtifactV3",
    "build_source_bound_transcript_candidate",
    "candidate_materializer_code_hash",
    "verify_source_bound_transcript_candidate",
]
