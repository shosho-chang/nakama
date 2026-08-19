"""Fail-closed Subtitle V2 handoff for Stage 5 video consumers."""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from agents.brook.podcast_subtitles.handoff import (
    ProjectionVerifierFactory,
    open_verified_projection,
)
from agents.brook.podcast_subtitles.module import VerifiedProjection


@dataclass(frozen=True, slots=True)
class Stage5SubtitleHandoff:
    """Exact verified subtitle bytes plus their persisted downstream receipt."""

    srt_path: Path
    receipt_path: Path
    verified: VerifiedProjection


class Stage5SubtitleContractError(ValueError):
    """The Stage 5 caller omitted or mixed formal/legacy subtitle identity."""


class Stage5SubtitleArtifactConflictError(Stage5SubtitleContractError):
    """An immutable Stage 5 artifact path already contains different bytes."""


@dataclass(frozen=True, slots=True)
class Stage5SubtitleSelection:
    """Subtitle source selected explicitly for one Stage 5 operation."""

    mode: Literal["verified-v2", "legacy-v1"]
    srt_path: Path
    handoff: Stage5SubtitleHandoff | None


@dataclass(frozen=True, slots=True)
class Stage5SubtitleRequest:
    """Serializable operator intent for selecting one Stage 5 subtitle source."""

    legacy_v1: bool = False
    projection_id: str | None = None
    expected_episode_id: str | None = None
    expected_generation_id: str | None = None
    expected_manifest_sha256: str | None = None
    reference_manifest: str | Path | None = None

    def open(
        self,
        episode_root: str | Path,
        *,
        factory: ProjectionVerifierFactory | None = None,
    ) -> Stage5SubtitleSelection:
        return select_stage5_subtitle(
            episode_root=episode_root,
            legacy_v1=self.legacy_v1,
            projection_id=self.projection_id,
            expected_episode_id=self.expected_episode_id,
            expected_generation_id=self.expected_generation_id,
            expected_manifest_sha256=self.expected_manifest_sha256,
            reference_manifest=self.reference_manifest,
            factory=factory,
        )


def _write_immutable(path: Path, payload: bytes) -> None:
    """Create one artifact exactly once; identical repeats are no-ops."""

    try:
        with path.open("xb") as stream:
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
        return
    except FileExistsError:
        pass
    if path.read_bytes() != payload:
        raise Stage5SubtitleArtifactConflictError(
            f"immutable Stage 5 artifact conflicts with verified bytes: {path}"
        )


def open_stage5_subtitle(
    *,
    episode_root: str | Path,
    projection_id: str,
    expected_episode_id: str,
    expected_generation_id: str,
    expected_manifest_sha256: str,
    reference_manifest: str | Path | None = None,
    factory: ProjectionVerifierFactory | None = None,
) -> Stage5SubtitleHandoff:
    """Verify one Projection and materialize only its exact SRT bytes for Stage 5."""

    root = Path(episode_root).resolve()
    verified = open_verified_projection(
        episode_root=root,
        projection_id=projection_id,
        expected_episode_id=expected_episode_id,
        expected_generation_id=expected_generation_id,
        expected_manifest_sha256=expected_manifest_sha256,
        reference_manifest=reference_manifest,
        factory=factory,
    )
    out_dir = root / ".stage5" / "verified-projections" / verified.projection_id
    out_dir.mkdir(parents=True, exist_ok=True)
    srt_path = out_dir / "transcript.srt"
    _write_immutable(srt_path, verified.srt_bytes)
    receipt_path = out_dir / "handoff.json"
    receipt = {
        "schema_version": 1,
        "episode_id": verified.episode_id,
        "generation_id": verified.generation_id,
        "manifest_sha256": verified.manifest_sha256,
        "projection_id": verified.projection_id,
        "srt_path": str(srt_path),
        "srt_sha256": verified.srt_sha256,
    }
    receipt_bytes = (
        json.dumps(receipt, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    ).encode("utf-8")
    _write_immutable(
        receipt_path,
        receipt_bytes,
    )
    return Stage5SubtitleHandoff(
        srt_path=srt_path,
        receipt_path=receipt_path,
        verified=verified,
    )


def select_stage5_subtitle(
    *,
    episode_root: str | Path,
    legacy_v1: bool = False,
    projection_id: str | None = None,
    expected_episode_id: str | None = None,
    expected_generation_id: str | None = None,
    expected_manifest_sha256: str | None = None,
    reference_manifest: str | Path | None = None,
    factory: ProjectionVerifierFactory | None = None,
) -> Stage5SubtitleSelection:
    """Select verified V2 by default; bare V1 is available only by explicit opt-in."""

    root = Path(episode_root).resolve()
    identities = {
        "projection_id": projection_id,
        "expected_episode_id": expected_episode_id,
        "expected_generation_id": expected_generation_id,
        "expected_manifest_sha256": expected_manifest_sha256,
    }
    supplied = [name for name, value in identities.items() if value is not None]
    if legacy_v1:
        if supplied or reference_manifest is not None:
            raise Stage5SubtitleContractError(
                "--legacy-v1 cannot be combined with Verified Projection identity"
            )
        srt_path = root / "transcript.srt"
        if not srt_path.is_file():
            raise FileNotFoundError(f"legacy transcript does not exist: {srt_path}")
        return Stage5SubtitleSelection(
            mode="legacy-v1",
            srt_path=srt_path,
            handoff=None,
        )

    missing = [name for name, value in identities.items() if not value]
    if missing:
        raise Stage5SubtitleContractError(
            "formal Stage 5 requires Verified Projection identity: " + ", ".join(missing)
        )
    handoff = open_stage5_subtitle(
        episode_root=root,
        projection_id=projection_id or "",
        expected_episode_id=expected_episode_id or "",
        expected_generation_id=expected_generation_id or "",
        expected_manifest_sha256=expected_manifest_sha256 or "",
        reference_manifest=reference_manifest,
        factory=factory,
    )
    return Stage5SubtitleSelection(
        mode="verified-v2",
        srt_path=handoff.srt_path,
        handoff=handoff,
    )


__all__ = [
    "Stage5SubtitleContractError",
    "Stage5SubtitleArtifactConflictError",
    "Stage5SubtitleHandoff",
    "Stage5SubtitleRequest",
    "Stage5SubtitleSelection",
    "open_stage5_subtitle",
    "select_stage5_subtitle",
]
