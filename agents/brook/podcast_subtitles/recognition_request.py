"""Immutable Recognition request evidence for exact fresh-process replay."""

from __future__ import annotations

from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from .hashing import hash_object, measure_regular_file, sha256_bytes
from .ports import NO_LEXICAL_BIAS_CONTEXT, RecognitionRequest


class RecognitionRequestArtifactV1(BaseModel):
    """Provider-relevant request fields, excluding filesystem transport paths."""

    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    schema_version: Literal[1] = 1
    episode_id: str
    invocation_id: str
    normalized_audio_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    normalized_audio_size_bytes: int = Field(gt=0)
    language_hint: str
    context_policy_id: Literal["nakama-verbatim-no-lexical-context-v1"]
    context_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    context_size_bytes: Literal[0]
    content_hash: str = Field(pattern=r"^[0-9a-f]{64}$")

    @field_validator("episode_id", "invocation_id", "language_hint")
    @classmethod
    def _nonblank_trimmed(cls, value: str) -> str:
        if not value.strip() or value != value.strip():
            raise ValueError("Recognition request text fields must be non-blank and trimmed")
        return value

    @field_validator("language_hint")
    @classmethod
    def _chinese_language_only(cls, value: str) -> str:
        normalized = value.casefold().replace("_", "-")
        if normalized not in {"zh", "zh-cn", "zh-tw", "zh-hant", "zh-hant-tw"}:
            raise ValueError("Podcast Subtitle V2 Recognition requires a Chinese language hint")
        return value

    @model_validator(mode="after")
    def _closed_context_and_content_identity(self) -> RecognitionRequestArtifactV1:
        if self.context_sha256 != sha256_bytes(b""):
            raise ValueError("Recognition request context must be exact empty bytes")
        payload = self.model_dump(mode="json", exclude={"content_hash"})
        if self.content_hash != hash_object(payload):
            raise ValueError("Recognition request artifact content hash mismatch")
        return self


def build_recognition_request_artifact(
    request: RecognitionRequest,
) -> RecognitionRequestArtifactV1:
    audio = Path(request.normalized_audio)
    try:
        audio_hash, audio_size = measure_regular_file(audio)
    except (OSError, ValueError) as exc:
        raise ValueError("Recognition request normalized audio is unavailable") from exc
    if (
        request.expected_normalized_audio_hash is not None
        and request.expected_normalized_audio_hash != audio_hash
    ):
        raise ValueError("Recognition request normalized audio digest mismatch")
    context = request.context_policy
    payload = {
        "schema_version": 1,
        "episode_id": request.episode_id,
        "invocation_id": request.invocation_id,
        "normalized_audio_sha256": audio_hash,
        "normalized_audio_size_bytes": audio_size,
        "language_hint": request.language_hint or "",
        "context_policy_id": context.id,
        "context_sha256": context.context_sha256,
        "context_size_bytes": len(context.context_bytes),
    }
    return RecognitionRequestArtifactV1(
        **payload,
        content_hash=hash_object(payload),
    )


def materialize_recognition_request(
    artifact: RecognitionRequestArtifactV1,
    *,
    normalized_audio: str | Path,
    raw_output_dir: str | Path,
) -> RecognitionRequest:
    path = Path(normalized_audio)
    try:
        audio_hash, audio_size = measure_regular_file(path)
    except (OSError, ValueError) as exc:
        raise ValueError("stored Recognition request normalized audio is unavailable") from exc
    if (
        audio_size != artifact.normalized_audio_size_bytes
        or audio_hash != artifact.normalized_audio_sha256
    ):
        raise ValueError("stored Recognition request normalized audio binding mismatch")
    return RecognitionRequest(
        episode_id=artifact.episode_id,
        invocation_id=artifact.invocation_id,
        normalized_audio=path,
        expected_normalized_audio_hash=artifact.normalized_audio_sha256,
        raw_output_dir=Path(raw_output_dir),
        language_hint=artifact.language_hint,
        context_policy=NO_LEXICAL_BIAS_CONTEXT,
    )


__all__ = [
    "RecognitionRequestArtifactV1",
    "build_recognition_request_artifact",
    "materialize_recognition_request",
]
