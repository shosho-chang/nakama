"""Fail-closed Verified Projection handoff for the Stage 5 video line."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, Field, ValidationError

from agents.brook.podcast_subtitles import VerifiedProjection
from agents.brook.podcast_subtitles.handoff import open_verified_projection

_SHA256_PATTERN = r"^[0-9a-f]{64}$"
_GENERATION_PATTERN = r"^(?:generation-)?[0-9a-f]{64}$"
_PROJECTION_PATTERN = r"^projection-[0-9a-f]{64}$"


class VerifiedProjectionHandoffError(ValueError):
    """The Stage 5 episode is not bound to one fully verified projection."""


class SubtitleV2HandoffBinding(BaseModel):
    """Closed operator binding stored under ``episode.yaml``."""

    model_config = {"extra": "forbid"}

    schema_version: Literal[1] = 1
    episode_root: Path
    projection_id: str = Field(pattern=_PROJECTION_PATTERN)
    generation_id: str = Field(pattern=_GENERATION_PATTERN)
    projection_manifest_sha256: str = Field(pattern=_SHA256_PATTERN)
    reference_manifest: Path | None = None


class StoryboardProjectionProvenance(BaseModel):
    """Exact immutable Stage 4 identity consumed to create a storyboard."""

    model_config = {"extra": "forbid"}

    schema_version: Literal[1] = 1
    episode_id: str
    projection_id: str = Field(pattern=_PROJECTION_PATTERN)
    generation_id: str = Field(pattern=_GENERATION_PATTERN)
    projection_sha256: str = Field(pattern=_SHA256_PATTERN)
    projection_manifest_sha256: str = Field(pattern=_SHA256_PATTERN)
    quality_report_sha256: str = Field(pattern=_SHA256_PATTERN)
    srt_sha256: str = Field(pattern=_SHA256_PATTERN)
    canonical_hash: str = Field(pattern=_SHA256_PATTERN)
    profile_hash: str = Field(pattern=_SHA256_PATTERN)
    token_sequence_hash: str = Field(pattern=_SHA256_PATTERN)
    output_hash: str = Field(pattern=_SHA256_PATTERN)

    @classmethod
    def from_verified(
        cls,
        verified: VerifiedProjection,
    ) -> StoryboardProjectionProvenance:
        return cls(
            episode_id=verified.episode_id,
            projection_id=verified.projection_id,
            generation_id=verified.generation_id,
            projection_sha256=verified.projection_sha256,
            projection_manifest_sha256=verified.manifest_sha256,
            quality_report_sha256=verified.quality_report_sha256,
            srt_sha256=verified.srt_sha256,
            canonical_hash=verified.manifest.canonical_hash,
            profile_hash=verified.manifest.profile_hash,
            token_sequence_hash=verified.manifest.token_sequence_hash,
            output_hash=verified.manifest.output_hash,
        )


def _resolve_from(base: Path, value: Path) -> Path:
    return value.resolve() if value.is_absolute() else (base / value).resolve()


def require_verified_projection(
    *,
    ep_dir: Path,
    expected_episode_id: str,
    episode_meta: dict,
) -> VerifiedProjection:
    """Parse the closed episode binding, then invoke the full V2 disk verifier."""

    if episode_meta.get("id") != expected_episode_id or ep_dir.name != expected_episode_id:
        raise VerifiedProjectionHandoffError(
            "episode.yaml id, directory name, and requested episode must match"
        )
    raw_binding = episode_meta.get("subtitle_v2_handoff")
    if raw_binding is None:
        raise SystemExit(
            "episode.yaml requires subtitle_v2_handoff; a bare transcript.srt is "
            "legacy/unverified input and cannot enter Stage 5 production"
        )
    try:
        binding = SubtitleV2HandoffBinding.model_validate(raw_binding)
    except ValidationError as exc:
        raise VerifiedProjectionHandoffError(
            f"episode.yaml subtitle_v2_handoff is invalid: {exc}"
        ) from exc
    episode_root = _resolve_from(ep_dir, binding.episode_root)
    if not episode_root.is_dir():
        raise VerifiedProjectionHandoffError(
            f"subtitle_v2_handoff episode_root is not a directory: {episode_root}"
        )
    reference_manifest = (
        _resolve_from(episode_root, binding.reference_manifest)
        if binding.reference_manifest is not None
        else None
    )
    return open_verified_projection(
        episode_root=episode_root,
        projection_id=binding.projection_id,
        expected_episode_id=expected_episode_id,
        expected_generation_id=binding.generation_id,
        expected_manifest_sha256=binding.projection_manifest_sha256,
        reference_manifest=reference_manifest,
    )


def write_storyboard_provenance(ep_dir: Path, verified: VerifiedProjection) -> Path:
    path = ep_dir / "storyboard.provenance.json"
    provenance = StoryboardProjectionProvenance.from_verified(verified)
    path.write_text(
        json.dumps(
            provenance.model_dump(mode="json"),
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ),
        encoding="utf-8",
    )
    return path


def assert_storyboard_provenance(ep_dir: Path, verified: VerifiedProjection) -> None:
    path = ep_dir / "storyboard.provenance.json"
    try:
        payload = path.read_bytes()
        stored = StoryboardProjectionProvenance.model_validate_json(payload)
    except (OSError, ValidationError, ValueError) as exc:
        raise VerifiedProjectionHandoffError(
            f"storyboard provenance is missing or invalid: {path}"
        ) from exc
    expected = StoryboardProjectionProvenance.from_verified(verified)
    if stored != expected:
        raise VerifiedProjectionHandoffError(
            "storyboard provenance drifted from the freshly verified projection handoff"
        )


__all__ = [
    "StoryboardProjectionProvenance",
    "SubtitleV2HandoffBinding",
    "VerifiedProjectionHandoffError",
    "assert_storyboard_provenance",
    "require_verified_projection",
    "write_storyboard_provenance",
]
