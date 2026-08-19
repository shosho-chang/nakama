"""Fail-closed Subtitle V2 handoff for Stage 5 video consumers."""

from __future__ import annotations

import hashlib
import json
import os
import tempfile
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


_CURRENT_HANDOFF = Path(".stage5") / "verified-subtitle-handoff.v2.json"
_CURRENT_CONTRACT = "podcast-subtitle-v2-stage5-handoff"


def _canonical_json(payload: dict[str, object]) -> bytes:
    return json.dumps(
        payload,
        ensure_ascii=False,
        allow_nan=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def _sha256(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def current_stage5_handoff_path(episode_root: str | Path) -> Path:
    """Return the single episode-local persisted Verified Projection handoff."""

    return Path(episode_root).resolve() / _CURRENT_HANDOFF


def _relative_artifact(root: Path, path: Path) -> str:
    try:
        return path.resolve().relative_to(root).as_posix()
    except ValueError as exc:
        raise Stage5SubtitleContractError(
            "Stage 5 handoff artifacts must remain inside the episode root"
        ) from exc


def _atomic_replace(path: Path, payload: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.is_file() and path.read_bytes() == payload:
        return
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{path.name}.", suffix=".tmp", dir=path.parent
    )
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def _persist_current_handoff(
    root: Path,
    handoff: Stage5SubtitleHandoff,
    reference_manifest: str | Path | None,
) -> None:
    receipt_bytes = handoff.receipt_path.read_bytes()
    reference_path: str | None = None
    reference_sha256: str | None = None
    if reference_manifest is not None:
        resolved_reference = Path(reference_manifest).resolve()
        try:
            reference_bytes = resolved_reference.read_bytes()
        except OSError as exc:
            raise Stage5SubtitleContractError(
                "reference manifest cannot be persisted into the Stage 5 handoff"
            ) from exc
        reference_path = str(resolved_reference)
        reference_sha256 = _sha256(reference_bytes)
    body: dict[str, object] = {
        "schema_version": 2,
        "contract": _CURRENT_CONTRACT,
        "episode_id": handoff.verified.episode_id,
        "generation_id": handoff.verified.generation_id,
        "projection_id": handoff.verified.projection_id,
        "projection_manifest_sha256": handoff.verified.manifest_sha256,
        "reference_manifest_path": reference_path,
        "reference_manifest_sha256": reference_sha256,
        "srt_path": _relative_artifact(root, handoff.srt_path),
        "srt_sha256": handoff.verified.srt_sha256,
        "srt_size_bytes": len(handoff.verified.srt_bytes),
        "projection_handoff_receipt_path": _relative_artifact(root, handoff.receipt_path),
        "projection_handoff_receipt_sha256": _sha256(receipt_bytes),
        "projection_handoff_receipt_size_bytes": len(receipt_bytes),
    }
    body["content_hash"] = _sha256(_canonical_json(body))
    _atomic_replace(current_stage5_handoff_path(root), _canonical_json(body))


def _strict_current_handoff(raw: bytes) -> dict[str, object]:
    def strict_pairs(pairs: list[tuple[str, object]]) -> dict[str, object]:
        result: dict[str, object] = {}
        for key, value in pairs:
            if key in result:
                raise ValueError(f"duplicate key {key!r}")
            result[key] = value
        return result

    try:
        payload = json.loads(raw.decode("utf-8"), object_pairs_hook=strict_pairs)
    except (UnicodeDecodeError, json.JSONDecodeError, ValueError) as exc:
        raise Stage5SubtitleContractError("persisted Stage 5 handoff is not strict JSON") from exc
    if not isinstance(payload, dict) or _canonical_json(payload) != raw:
        raise Stage5SubtitleContractError("persisted Stage 5 handoff is not canonical exact bytes")
    required = {
        "schema_version",
        "contract",
        "episode_id",
        "generation_id",
        "projection_id",
        "projection_manifest_sha256",
        "reference_manifest_path",
        "reference_manifest_sha256",
        "srt_path",
        "srt_sha256",
        "srt_size_bytes",
        "projection_handoff_receipt_path",
        "projection_handoff_receipt_sha256",
        "projection_handoff_receipt_size_bytes",
        "content_hash",
    }
    if set(payload) != required or payload.get("schema_version") != 2:
        raise Stage5SubtitleContractError("persisted Stage 5 handoff schema drift")
    if payload.get("contract") != _CURRENT_CONTRACT:
        raise Stage5SubtitleContractError("persisted Stage 5 handoff contract drift")
    content_hash = payload.get("content_hash")
    unsigned = {key: value for key, value in payload.items() if key != "content_hash"}
    if content_hash != _sha256(_canonical_json(unsigned)):
        raise Stage5SubtitleContractError("persisted Stage 5 handoff content hash mismatch")
    return payload


def _bound_local_path(root: Path, value: object, *, label: str) -> Path:
    if not isinstance(value, str) or not value:
        raise Stage5SubtitleContractError(f"persisted Stage 5 handoff lacks {label}")
    path = (root / value).resolve()
    try:
        path.relative_to(root)
    except ValueError as exc:
        raise Stage5SubtitleContractError(
            f"persisted Stage 5 handoff {label} escapes the episode root"
        ) from exc
    return path


def _open_persisted_stage5_subtitle(
    root: Path,
    *,
    factory: ProjectionVerifierFactory | None,
) -> Stage5SubtitleHandoff:
    current = current_stage5_handoff_path(root)
    if not current.is_file():
        raise Stage5SubtitleContractError(
            "formal Stage 5 requires a persisted Verified Projection handoff; "
            "run project/resolve once with explicit projection lineage"
        )
    payload = _strict_current_handoff(current.read_bytes())
    srt_path = _bound_local_path(root, payload["srt_path"], label="srt_path")
    receipt_path = _bound_local_path(
        root,
        payload["projection_handoff_receipt_path"],
        label="projection_handoff_receipt_path",
    )
    try:
        srt_bytes = srt_path.read_bytes()
        receipt_bytes = receipt_path.read_bytes()
    except OSError as exc:
        raise Stage5SubtitleContractError(
            "persisted Stage 5 handoff artifact is missing or unreadable"
        ) from exc
    if (
        _sha256(srt_bytes) != payload["srt_sha256"]
        or len(srt_bytes) != payload["srt_size_bytes"]
        or _sha256(receipt_bytes) != payload["projection_handoff_receipt_sha256"]
        or len(receipt_bytes) != payload["projection_handoff_receipt_size_bytes"]
    ):
        raise Stage5SubtitleContractError("persisted Stage 5 handoff artifact hash mismatch")
    reference_manifest = payload["reference_manifest_path"]
    if reference_manifest is not None:
        if not isinstance(reference_manifest, str):
            raise Stage5SubtitleContractError("persisted reference manifest path is invalid")
        try:
            current_reference_hash = _sha256(Path(reference_manifest).resolve().read_bytes())
        except OSError as exc:
            raise Stage5SubtitleContractError(
                "persisted reference manifest is unavailable"
            ) from exc
        if current_reference_hash != payload["reference_manifest_sha256"]:
            raise Stage5SubtitleContractError("persisted reference manifest bytes drifted")
    handoff = open_stage5_subtitle(
        episode_root=root,
        projection_id=str(payload["projection_id"]),
        expected_episode_id=str(payload["episode_id"]),
        expected_generation_id=str(payload["generation_id"]),
        expected_manifest_sha256=str(payload["projection_manifest_sha256"]),
        reference_manifest=reference_manifest,
        factory=factory,
        persist_current=False,
    )
    if handoff.srt_path != srt_path or handoff.receipt_path != receipt_path:
        raise Stage5SubtitleContractError("persisted Stage 5 handoff path lineage drift")
    return handoff


def open_stage5_subtitle(
    *,
    episode_root: str | Path,
    projection_id: str,
    expected_episode_id: str,
    expected_generation_id: str,
    expected_manifest_sha256: str,
    reference_manifest: str | Path | None = None,
    factory: ProjectionVerifierFactory | None = None,
    persist_current: bool = True,
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
    handoff = Stage5SubtitleHandoff(
        srt_path=srt_path,
        receipt_path=receipt_path,
        verified=verified,
    )
    if persist_current:
        _persist_current_handoff(root, handoff, reference_manifest)
    return handoff


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

    if not supplied and reference_manifest is None:
        handoff = _open_persisted_stage5_subtitle(root, factory=factory)
        return Stage5SubtitleSelection(
            mode="verified-v2",
            srt_path=handoff.srt_path,
            handoff=handoff,
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
    "current_stage5_handoff_path",
    "open_stage5_subtitle",
    "select_stage5_subtitle",
]
