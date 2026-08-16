"""Verified pass-through boundary for audio normalized before Subtitle V2."""

from __future__ import annotations

import json
import wave
from datetime import datetime, timezone
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator
from shared.schemas.podcast_subtitles_v2 import (
    ArtifactDigest,
    AudioClockMap,
    NormalizationParameter,
    NormalizationReceipt,
    normalization_settings_hash,
)

from ..hashing import canonical_json_bytes, hash_file, sha256_bytes
from ..ports import (
    AdapterInputError,
    AdapterIntegrityError,
    NormalizationResult,
    NormalizeRequest,
)


class NormalizedAudioHandoffManifestV1(BaseModel):
    """Operator seal for an already-normalized, program-clock audio artifact."""

    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)
    schema_version: Literal[1] = 1
    contract: Literal["normalized-audio-handoff-v1"] = "normalized-audio-handoff-v1"
    normalized_audio_sha256: str
    normalized_audio_size_bytes: int = Field(gt=0)
    normalized_audio_duration_ms: int = Field(gt=0)
    accepted_at: datetime

    @model_validator(mode="after")
    def _valid(self) -> "NormalizedAudioHandoffManifestV1":
        for label, value in (
            ("normalized_audio_sha256", self.normalized_audio_sha256),
        ):
            if len(value) != 64 or any(char not in "0123456789abcdef" for char in value):
                raise ValueError(f"{label} must be lowercase SHA-256")
        if self.accepted_at.tzinfo is None:
            raise ValueError("normalized handoff accepted_at must be timezone-aware")
        return self


def _pairs(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"duplicate JSON key: {key}")
        result[key] = value
    return result


def _load(path: Path) -> tuple[NormalizedAudioHandoffManifestV1, bytes]:
    try:
        payload = path.read_bytes()
        parsed = json.loads(payload, object_pairs_hook=_pairs)
        manifest = NormalizedAudioHandoffManifestV1.model_validate_json(payload, strict=True)
    except (OSError, UnicodeDecodeError, json.JSONDecodeError, ValidationError, ValueError) as exc:
        raise AdapterInputError(f"invalid normalized-audio handoff manifest: {exc}") from exc
    if canonical_json_bytes(manifest) != payload:
        raise AdapterIntegrityError("normalized-audio handoff manifest must be canonical JSON")
    return manifest, payload


def _wav_duration_ms(path: Path) -> int:
    try:
        with wave.open(str(path), "rb") as source:
            frame_rate = source.getframerate()
            frame_count = source.getnframes()
    except (OSError, EOFError, wave.Error) as exc:
        raise AdapterInputError("normalized audio must be a readable PCM WAV") from exc
    if frame_rate <= 0 or frame_count <= 0:
        raise AdapterIntegrityError("normalized audio has an invalid WAV clock")
    return (frame_count * 1_000 + frame_rate // 2) // frame_rate


class VerifiedNormalizedAudioHandoffAdapter:
    """No-network Normalizer seam that verifies and returns existing audio.

    This receipt describes the Subtitle V2 handoff itself.  It does not claim
    to be an Auphonic execution and never imports or calls Auphonic code.
    """

    def __init__(self, manifest_path: str | Path) -> None:
        self._manifest_path = Path(manifest_path)

    def normalize(self, request: NormalizeRequest) -> NormalizationResult:
        manifest, manifest_bytes = _load(self._manifest_path)
        audio = Path(request.source_audio)
        if not audio.is_file():
            raise AdapterInputError("normalized audio must exist")
        audio_hash = hash_file(audio)
        if request.expected_source_hash not in {None, audio_hash}:
            raise AdapterIntegrityError("normalized handoff request hash mismatch")
        if (audio_hash, audio.stat().st_size) != (
            manifest.normalized_audio_sha256,
            manifest.normalized_audio_size_bytes,
        ):
            raise AdapterIntegrityError("normalized handoff manifest belongs to other audio")
        normalized_duration_ms = _wav_duration_ms(audio)
        if normalized_duration_ms != manifest.normalized_audio_duration_ms:
            raise AdapterIntegrityError("normalized audio duration differs from handoff manifest")
        parameters = (
            NormalizationParameter(
                scope="adapter",
                name="handoff_manifest_sha256",
                value=sha256_bytes(manifest_bytes),
            ),
        )
        artifact = ArtifactDigest(
            uri=audio.resolve().as_uri(),
            sha256=audio_hash,
            size_bytes=audio.stat().st_size,
        )
        accepted_at = manifest.accepted_at.astimezone(timezone.utc)
        receipt = NormalizationReceipt(
            status="accepted",
            provider="verified-upstream-normalized-handoff",
            production_id=f"handoff-{sha256_bytes(manifest_bytes)}",
            production_source="reused",
            original_production_id=f"upstream-normalized-{audio_hash}",
            provider_outcome="completed",
            source_identity_verified=True,
            source_binding_method="verified_upstream_handoff",
            source=artifact,
            normalized=artifact,
            source_duration_ms=normalized_duration_ms,
            normalized_duration_ms=normalized_duration_ms,
            request_started_at=accepted_at,
            completed_at=accepted_at,
            requested_parameters=parameters,
            submitted_parameters=parameters,
            settings_hash=normalization_settings_hash(parameters, preset=None),
            reuse_reason="already-normalized audio accepted at the Subtitle V2 boundary",
            clock_map=AudioClockMap(
                source_origin_ms=0,
                normalized_origin_ms=0,
                verified=True,
                drift_ms=0.0,
            ),
            alignment_method="identity",
        )
        return NormalizationResult(normalized_audio=audio.resolve(), receipt=receipt)


__all__ = [
    "NormalizedAudioHandoffManifestV1",
    "VerifiedNormalizedAudioHandoffAdapter",
]
