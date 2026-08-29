"""Single hash-bound authority for Highlight -> Resolve materialization lineage."""

from __future__ import annotations

import hashlib
import json
import os
import re
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping

from agents.brook.script_video.editorial_master import EditorialMasterContractError

CONTRACT = "podcast-highlight-materialization-v1"
RELATIVE_DIR = Path("highlights") / "materialization"
EDITORIAL_MASTER_LINEAGE_KEY = "editorial_master_lineage"


@dataclass(frozen=True, slots=True)
class HighlightSource:
    """One verified subtitle/media timebase selected for a Highlight operation."""

    srt_path: Path
    media_path: Path | None
    lineage: dict[str, object]
    legacy: bool = False


def _sha256(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def _canonical(payload: Mapping[str, object]) -> bytes:
    return json.dumps(
        payload,
        ensure_ascii=False,
        allow_nan=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def _atomic_json_write(path: Path, payload: Mapping[str, object]) -> None:
    encoded = _canonical(payload) + b"\n"
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{path.name}.",
        suffix=".tmp",
        dir=path.parent,
    )
    try:
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(encoded)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary_name, path)
    finally:
        Path(temporary_name).unlink(missing_ok=True)


def _timeline_uid(timeline: Any) -> str:
    for method in ("GetUniqueId", "GetUniqueID"):
        function = getattr(timeline, method, None)
        value = function() if callable(function) else None
        if isinstance(value, str) and value.strip():
            return value.strip()
    raise EditorialMasterContractError("materialized timeline has no stable unique ID")


def _verify_live_av_sources(timeline: Any, master_media: Path) -> None:
    """Require sacred A-roll and program audio on track 1 to come from Master."""

    getter = getattr(timeline, "GetItemListInTrack", None)
    if not callable(getter):
        raise EditorialMasterContractError("live materialized timeline cannot expose track items")
    expected = os.path.normcase(str(master_media.resolve()))
    for track_type in ("video", "audio"):
        items = list(getter(track_type, 1) or [])
        if not items:
            raise EditorialMasterContractError(
                f"live materialized timeline {track_type} track 1 is empty"
            )
        for index, item in enumerate(items, 1):
            media_pool_item = getattr(item, "GetMediaPoolItem", lambda: None)()
            if media_pool_item is None:
                raise EditorialMasterContractError(
                    f"live {track_type} track 1 item {index} has no media identity"
                )
            raw_path = media_pool_item.GetClipProperty("File Path")
            actual = os.path.normcase(str(Path(raw_path or "").resolve()))
            if actual != expected:
                raise EditorialMasterContractError(
                    f"live {track_type} track 1 item {index} is not exact master media"
                )


def materialization_path(episode_dir: Path, cut_id: str) -> Path:
    if not re.fullmatch(r"[A-Za-z0-9_-]+", cut_id):
        raise EditorialMasterContractError("materialization cut_id is unsafe")
    return episode_dir / RELATIVE_DIR / f"{cut_id}.json"


def _source_lineage(source: HighlightSource) -> dict[str, object]:
    return dict(source.lineage)


def build_materialization_receipt(
    episode_dir: Path,
    *,
    cut_id: str,
    cut_format: str,
    timeline: Any,
    source_range: Mapping[str, int | float],
    source: HighlightSource,
) -> dict[str, object]:
    """Build the deterministic marker proving one Resolve timeline came from Master."""

    if source.legacy or source.media_path is None:
        raise EditorialMasterContractError(
            "legacy Highlight sources cannot produce production materialization receipts"
        )
    required_range = {"start_sec", "end_sec", "start_frame", "end_frame"}
    if set(source_range) != required_range:
        raise EditorialMasterContractError("materialization source_range schema drift")
    root = episode_dir.resolve()

    def relative(path: Path) -> str:
        resolved = path.resolve()
        try:
            return resolved.relative_to(root).as_posix()
        except ValueError as exc:
            raise EditorialMasterContractError(
                "materialization source escapes episode root"
            ) from exc

    lineage = _source_lineage(source)
    payload: dict[str, object] = {
        "schema_version": 1,
        "contract": CONTRACT,
        "cut_id": cut_id,
        "format": cut_format,
        "timeline": {"name": timeline.GetName(), "uid": _timeline_uid(timeline)},
        "source_range": dict(source_range),
        EDITORIAL_MASTER_LINEAGE_KEY: lineage,
        "source_media": {
            "path": relative(source.media_path),
            "sha256": lineage["master_media_sha256"],
        },
        "source_subtitles": {
            "path": relative(source.srt_path),
            "sha256": lineage["master_srt_sha256"],
        },
    }
    payload["content_hash"] = _sha256(_canonical(payload))
    return payload


def _verify_payload_hash(payload: Mapping[str, object]) -> None:
    unsigned = dict(payload)
    supplied_hash = unsigned.pop("content_hash", None)
    if supplied_hash != _sha256(_canonical(unsigned)):
        raise EditorialMasterContractError("materialization receipt content hash mismatch")


def write_materialization_receipt(
    episode_dir: Path,
    payload: dict[str, object],
    *,
    replace: bool = False,
) -> Path:
    """Commit marker last; identical is idempotent, differing fails unless promoted."""

    cut_id = payload.get("cut_id")
    if not isinstance(cut_id, str):
        raise EditorialMasterContractError("materialization receipt cut_id is missing")
    if payload.get("contract") != CONTRACT or payload.get("schema_version") != 1:
        raise EditorialMasterContractError("materialization receipt contract drift")
    _verify_payload_hash(payload)
    path = materialization_path(episode_dir, cut_id)
    if path.exists():
        try:
            existing = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise EditorialMasterContractError(
                "existing materialization receipt is unreadable"
            ) from exc
        if not isinstance(existing, dict):
            raise EditorialMasterContractError("existing materialization receipt is not an object")
        _verify_payload_hash(existing)
        if existing.get("contract") != CONTRACT or existing.get("schema_version") != 1:
            raise EditorialMasterContractError("existing materialization receipt contract drift")
        if existing == payload:
            return path
        if not replace:
            raise EditorialMasterContractError(
                f"materialization receipt conflicts with existing cut: {cut_id}"
            )
        if existing.get(EDITORIAL_MASTER_LINEAGE_KEY) != payload.get(
            EDITORIAL_MASTER_LINEAGE_KEY
        ) or existing.get("source_range") != payload.get("source_range"):
            raise EditorialMasterContractError(
                "materialization promotion changed Master identity or source range"
            )
    _atomic_json_write(path, payload)
    return path


def verify_materialization_receipt(
    episode_dir: Path,
    cut_id: str,
    *,
    source: HighlightSource,
    timeline: Any | None = None,
    expected_timeline_name: str | None = None,
    expected_format: str | None = None,
    expected_source_range: Mapping[str, int | float] | None = None,
) -> dict[str, object]:
    """Freshly verify marker bytes, Master identity and optional live Resolve UID."""

    if source.legacy or source.media_path is None:
        raise EditorialMasterContractError(
            "legacy Highlight source has no production materialization receipt"
        )
    path = materialization_path(episode_dir, cut_id)
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise EditorialMasterContractError(
            f"materialization receipt is missing or unreadable: {path}"
        ) from exc
    required = {
        "schema_version",
        "contract",
        "cut_id",
        "format",
        "timeline",
        "source_range",
        EDITORIAL_MASTER_LINEAGE_KEY,
        "source_media",
        "source_subtitles",
        "content_hash",
    }
    if not isinstance(payload, dict) or set(payload) != required:
        raise EditorialMasterContractError("materialization receipt schema drift")
    _verify_payload_hash(payload)
    if (
        payload["schema_version"] != 1
        or payload["contract"] != CONTRACT
        or payload["cut_id"] != cut_id
        or payload[EDITORIAL_MASTER_LINEAGE_KEY] != _source_lineage(source)
    ):
        raise EditorialMasterContractError("materialization identity differs from Editorial Master")
    root = episode_dir.resolve()
    expected_media = {
        "path": source.media_path.resolve().relative_to(root).as_posix(),
        "sha256": source.lineage["master_media_sha256"],
    }
    expected_subtitles = {
        "path": source.srt_path.resolve().relative_to(root).as_posix(),
        "sha256": source.lineage["master_srt_sha256"],
    }
    if (
        payload["source_media"] != expected_media
        or payload["source_subtitles"] != expected_subtitles
    ):
        raise EditorialMasterContractError("materialization source artifact identity drift")
    timeline_identity = payload.get("timeline")
    if not isinstance(timeline_identity, dict) or set(timeline_identity) != {"name", "uid"}:
        raise EditorialMasterContractError("materialization timeline identity is invalid")
    if expected_timeline_name and timeline_identity["name"] != expected_timeline_name:
        raise EditorialMasterContractError("materialization timeline name drift")
    if expected_format is not None and payload["format"] != expected_format:
        raise EditorialMasterContractError("materialization format drift")
    if expected_source_range is not None and payload["source_range"] != dict(expected_source_range):
        raise EditorialMasterContractError("materialization source range drift")
    if timeline is not None and timeline_identity != {
        "name": timeline.GetName(),
        "uid": _timeline_uid(timeline),
    }:
        raise EditorialMasterContractError(
            "live Resolve timeline differs from materialization receipt"
        )
    if timeline is not None:
        _verify_live_av_sources(timeline, source.media_path)
    return payload


__all__ = [
    "CONTRACT",
    "EDITORIAL_MASTER_LINEAGE_KEY",
    "HighlightSource",
    "build_materialization_receipt",
    "materialization_path",
    "verify_materialization_receipt",
    "write_materialization_receipt",
]
