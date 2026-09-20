"""Audio-bound opaque-speaker attribution from strict diarization bytes.

This Adapter is deliberately an import/materialization seam.  It performs no
network work and cannot use text or Reference Evidence to name a person.  A
fully pinned audio diarizer emits canonical response bytes; this module binds
those bytes to exact normalized audio and base Recognition Evidence, then
creates a new immutable Recognition Evidence artifact only when every token
has one unambiguous, sufficiently confident speaker observation.
"""

from __future__ import annotations

import json
import os
import re
import tempfile
from pathlib import Path

from shared.schemas.podcast_subtitles_v2 import (
    ArtifactDigest,
    EvidenceToken,
    RecognitionEvidence,
    recognition_evidence_content_hash,
)

from ..hashing import (
    canonical_json_bytes,
    hash_file,
    hash_object,
    measure_regular_file,
    sha256_bytes,
)
from ..ports import (
    AdapterInputError,
    AdapterIntegrityError,
    DiarizationExecutionReceipt,
    DiarizationModelIdentity,
    DiarizationPolicyV1,
    DiarizationRequest,
    DiarizationRunResult,
)

_ADAPTER = "audio-diarization-speaker-attribution"
_OPAQUE_LABEL = re.compile(r"speaker_[0-9]{4}")


def _strict_json_object(raw: bytes, *, label: str) -> dict[str, object]:
    duplicates: list[str] = []

    def reject_duplicates(pairs: list[tuple[str, object]]) -> dict[str, object]:
        result: dict[str, object] = {}
        for key, value in pairs:
            if key in result:
                duplicates.append(key)
            result[key] = value
        return result

    try:
        payload = json.loads(raw, object_pairs_hook=reject_duplicates)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise AdapterIntegrityError(f"{label} is not valid JSON") from exc
    if duplicates:
        raise AdapterIntegrityError(f"{label} has duplicate keys")
    if not isinstance(payload, dict) or canonical_json_bytes(payload) != raw:
        raise AdapterIntegrityError(f"{label} is not canonical JSON")
    return payload


def _write_artifact(output_dir: Path, *, stem: str, raw: bytes) -> ArtifactDigest:
    digest = sha256_bytes(raw)
    output_dir.mkdir(parents=True, exist_ok=True)
    target = output_dir / f"{stem}-{digest}.json"
    if target.exists():
        if hash_file(target) != digest or target.stat().st_size != len(raw):
            raise AdapterIntegrityError(f"Diarization artifact collision at {target}")
    else:
        descriptor, temporary_name = tempfile.mkstemp(
            prefix=f".{stem}-{digest}.", suffix=".tmp", dir=output_dir
        )
        temporary = Path(temporary_name)
        try:
            with os.fdopen(descriptor, "wb") as stream:
                stream.write(raw)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, target)
        finally:
            if temporary.exists():
                temporary.unlink()
    return ArtifactDigest(
        uri=target.resolve().as_uri(),
        sha256=digest,
        size_bytes=len(raw),
    )


def _opaque_labels(raw: object) -> tuple[str, ...]:
    if not isinstance(raw, list) or len(raw) < 2:
        raise AdapterInputError("Diarization requires at least two opaque speaker labels")
    labels = tuple(raw)
    expected = tuple(f"speaker_{index:04d}" for index in range(len(labels)))
    if labels != expected or any(
        not isinstance(item, str) or _OPAQUE_LABEL.fullmatch(item) is None for item in labels
    ):
        raise AdapterInputError(
            "Diarization speaker labels must be contiguous canonical opaque labels"
        )
    return labels


def _numeric_confidence(value: object) -> float:
    if type(value) not in {int, float} or not 0.0 <= float(value) <= 1.0:
        raise AdapterInputError("Diarization confidence must be within [0, 1]")
    return float(value)


class ContentAddressedDiarizationAttributor:
    """Materialize complete speaker Evidence from one strict diarization run."""

    def __init__(
        self,
        *,
        identity: DiarizationModelIdentity,
        policy: DiarizationPolicyV1,
    ) -> None:
        if identity.config_hash != policy.content_hash:
            raise ValueError("Diarization identity config_hash must bind the exact policy")
        self._identity = identity
        self._policy = policy

    @property
    def identity(self) -> DiarizationModelIdentity:
        return self._identity

    @property
    def policy(self) -> DiarizationPolicyV1:
        return self._policy

    def _measure_audio(self, request: DiarizationRequest) -> tuple[str, int]:
        try:
            digest, size = measure_regular_file(request.normalized_audio)
        except (OSError, ValueError) as exc:
            raise AdapterInputError("Diarization normalized audio is unavailable") from exc
        if (
            digest != request.expected_normalized_audio_hash
            or size != request.expected_normalized_audio_size_bytes
        ):
            raise AdapterIntegrityError(
                "Diarization normalized audio differs from the exact request binding"
            )
        return digest, size

    def prepare(self, request: DiarizationRequest) -> bytes:
        """Return the one canonical work packet an external audio runner receives."""

        digest, size = self._measure_audio(request)
        packet: dict[str, object] = {
            "schema_version": 1,
            "task": "podcast_subtitle_v2_opaque_speaker_diarization",
            "episode_id": request.episode_id,
            "invocation_id": request.invocation_id,
            "normalized_audio": {
                "sha256": digest,
                "size_bytes": size,
                "duration_ms": request.normalized_audio_duration_ms,
            },
            "base_recognition_evidence_hash": request.base_evidence_hash,
            "base_tokens": [
                {
                    "id": token.id,
                    "start_ms": token.start_ms,
                    "end_ms": token.end_ms,
                }
                for token in request.base_evidence.tokens
            ],
            "adapter_identity": self.identity,
            "adapter_identity_hash": self.identity.content_hash,
            "policy": self.policy,
            "policy_hash": self.policy.content_hash,
            "authority": "audio_observation_opaque_labels_only",
        }
        packet["work_packet_id"] = "speaker-diarization-" + hash_object(packet)
        return canonical_json_bytes(packet)

    def _parse_response(
        self,
        request: DiarizationRequest,
        *,
        request_bytes: bytes,
        response_bytes: bytes,
    ) -> tuple[tuple[str, ...], tuple[tuple[int, int, str], ...]]:
        packet = _strict_json_object(request_bytes, label="Diarization request")
        response = _strict_json_object(response_bytes, label="Diarization response")
        required = {
            "schema_version",
            "work_packet_id",
            "adapter_identity_hash",
            "normalized_audio_hash",
            "normalized_audio_duration_ms",
            "status",
            "speaker_labels",
            "segments",
        }
        if set(response) != required:
            raise AdapterIntegrityError("Diarization response has an invalid strict contract")
        if (
            response["schema_version"] != 1
            or response["work_packet_id"] != packet["work_packet_id"]
            or response["adapter_identity_hash"] != self.identity.content_hash
            or response["normalized_audio_hash"] != request.expected_normalized_audio_hash
            or response["normalized_audio_duration_ms"]
            != request.normalized_audio_duration_ms
        ):
            raise AdapterIntegrityError(
                "Diarization response crossed request, audio, or executable identity"
            )
        if response["status"] != "completed":
            raise AdapterInputError("Diarization response is unresolved")

        labels = _opaque_labels(response["speaker_labels"])
        raw_segments = response["segments"]
        if not isinstance(raw_segments, list) or not raw_segments:
            raise AdapterInputError("Diarization response requires audio segments")
        segments: list[tuple[int, int, str]] = []
        previous_end = 0
        used_labels: set[str] = set()
        first_seen_labels: list[str] = []
        for item in raw_segments:
            if not isinstance(item, dict) or set(item) != {"start_ms", "end_ms", "scores"}:
                raise AdapterIntegrityError("Diarization segment has an invalid strict contract")
            start = item["start_ms"]
            end = item["end_ms"]
            if (
                type(start) is not int
                or type(end) is not int
                or start < 0
                or end <= start
                or end > request.normalized_audio_duration_ms
            ):
                raise AdapterInputError("Diarization segment range is invalid")
            if start < previous_end:
                raise AdapterInputError("Diarization segments overlap or are reordered")
            previous_end = end
            raw_scores = item["scores"]
            if not isinstance(raw_scores, list) or len(raw_scores) != len(labels):
                raise AdapterInputError("Diarization segment scores must cover the speaker roster")
            scores: list[tuple[str, float]] = []
            for expected_label, score in zip(labels, raw_scores, strict=True):
                if not isinstance(score, dict) or set(score) != {"speaker", "confidence"}:
                    raise AdapterIntegrityError(
                        "Diarization speaker score has an invalid strict contract"
                    )
                if score["speaker"] != expected_label:
                    raise AdapterInputError(
                        "Diarization score references an unknown or reordered speaker label"
                    )
                scores.append((expected_label, _numeric_confidence(score["confidence"])))
            ranked = sorted(scores, key=lambda value: (-value[1], value[0]))
            winner, confidence = ranked[0]
            margin = confidence - ranked[1][1]
            if margin < self.policy.minimum_assignment_margin:
                raise AdapterInputError("Diarization segment speaker assignment is ambiguous")
            if confidence < self.policy.minimum_segment_confidence:
                raise AdapterInputError("Diarization segment confidence is below policy")
            if winner not in used_labels:
                first_seen_labels.append(winner)
            used_labels.add(winner)
            segments.append((start, end, winner))
        if used_labels != set(labels):
            raise AdapterInputError(
                "Diarization cannot accept a whole run as one speaker without observations"
            )
        if tuple(first_seen_labels) != labels:
            raise AdapterInputError(
                "Diarization opaque labels must follow speaker first-appearance order"
            )
        return labels, tuple(segments)

    def materialize(
        self,
        request: DiarizationRequest,
        *,
        response_bytes: bytes,
    ) -> DiarizationRunResult:
        request_bytes = self.prepare(request)
        labels, segments = self._parse_response(
            request,
            request_bytes=request_bytes,
            response_bytes=response_bytes,
        )

        assignments: list[tuple[str, str]] = []
        for token in request.base_evidence.tokens:
            containing = [
                speaker
                for start, end, speaker in segments
                if start <= token.start_ms and token.end_ms <= end
            ]
            if len(containing) != 1:
                overlapping = [
                    speaker
                    for start, end, speaker in segments
                    if start < token.end_ms and token.start_ms < end
                ]
                if len(set(overlapping)) > 1:
                    raise AdapterInputError(
                        f"Recognition token {token.id!r} crosses a diarization speaker boundary"
                    )
                raise AdapterInputError(
                    f"Recognition token {token.id!r} lacks complete diarization coverage"
                )
            assignments.append((token.id, containing[0]))
        if {speaker for _token_id, speaker in assignments} != set(labels):
            raise AdapterInputError(
                "Diarization cannot accept the whole token stream as one speaker"
            )

        request_artifact = _write_artifact(
            request.raw_output_dir, stem="speaker-diarization-request", raw=request_bytes
        )
        response_artifact = _write_artifact(
            request.raw_output_dir, stem="speaker-diarization-response", raw=response_bytes
        )
        assignment_hash = hash_object(assignments)
        receipt_id = "speaker-diarization-receipt-" + hash_object(
            {
                "request": request_artifact.sha256,
                "response": response_artifact.sha256,
                "adapter_identity_hash": self.identity.content_hash,
                "policy_hash": self.policy.content_hash,
                "base_recognition_evidence_hash": request.base_evidence_hash,
                "token_assignment_hash": assignment_hash,
            }
        )
        tokens = tuple(
            EvidenceToken(
                id="ev_"
                + hash_object(
                    {
                        "adapter": _ADAPTER,
                        "base_token_id": token.id,
                        "speaker": speaker,
                        "receipt_id": receipt_id,
                    }
                )[:32],
                text=token.text,
                start_ms=token.start_ms,
                end_ms=token.end_ms,
                confidence=token.confidence,
                speaker=speaker,
                evidence_refs=(
                    f"recognition:{request.base_evidence_hash}#{token.id}",
                    f"diarization-response:{response_artifact.sha256}",
                    f"diarization-receipt:{receipt_id}",
                ),
            )
            for token, (_token_id, speaker) in zip(
                request.base_evidence.tokens, assignments, strict=True
            )
        )
        evidence = RecognitionEvidence(
            episode_id=request.episode_id,
            invocation_id=request.invocation_id,
            adapter=_ADAPTER,
            model=(
                f"{request.base_evidence.adapter}/{request.base_evidence.model}+"
                f"{self.identity.model}@{self.identity.model_revision}"
            ),
            language=request.base_evidence.language,
            config_hash=hash_object(
                {
                    "base_recognition_evidence_hash": request.base_evidence_hash,
                    "adapter_identity_hash": self.identity.content_hash,
                    "policy_hash": self.policy.content_hash,
                }
            ),
            raw_output=response_artifact,
            raw_output_hash=response_artifact.sha256,
            normalized_audio_hash=request.expected_normalized_audio_hash,
            tokens=tokens,
        )
        evidence_hash = recognition_evidence_content_hash(evidence)
        normalized_digest, normalized_size = self._measure_audio(request)
        receipt = DiarizationExecutionReceipt(
            id=receipt_id,
            episode_id=request.episode_id,
            invocation_id=request.invocation_id,
            normalized_audio=ArtifactDigest(
                uri=request.normalized_audio.resolve().as_uri(),
                sha256=normalized_digest,
                size_bytes=normalized_size,
            ),
            normalized_audio_duration_ms=request.normalized_audio_duration_ms,
            base_recognition_evidence_hash=request.base_evidence_hash,
            base_token_ids=tuple(token.id for token in request.base_evidence.tokens),
            request=request_artifact,
            response=response_artifact,
            adapter_identity=self.identity,
            policy_hash=self.policy.content_hash,
            speaker_labels=labels,
            token_assignment_hash=assignment_hash,
            materialized_recognition_evidence_hash=evidence_hash,
        )
        return DiarizationRunResult(
            evidence=evidence,
            receipt=receipt,
            request_bytes=request_bytes,
            response_bytes=response_bytes,
        )

    def verify(
        self,
        request: DiarizationRequest,
        *,
        result: DiarizationRunResult,
    ) -> DiarizationRunResult:
        """Freshly parse and replay the complete stored proof."""

        try:
            stored = DiarizationRunResult(
                evidence=result.evidence,
                receipt=result.receipt,
                request_bytes=result.request_bytes,
                response_bytes=result.response_bytes,
            )
        except (TypeError, ValueError) as exc:
            raise AdapterIntegrityError("Stored Diarization proof is invalid") from exc
        expected = self.materialize(request, response_bytes=result.response_bytes)
        if stored != expected:
            raise AdapterIntegrityError("Stored Diarization proof does not replay exactly")
        return expected


__all__ = ["ContentAddressedDiarizationAttributor"]
