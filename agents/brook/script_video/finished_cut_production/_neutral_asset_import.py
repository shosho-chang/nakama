"""Removable one-shot Adapter for verified neutral acquisition migration."""

from __future__ import annotations

import hashlib
import json
import math
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Mapping, Protocol

from ._active_store import ActiveAssetPublication, ActiveAssetStore, ActiveAssetStoreError
from ._assets import AssetContractError, AssetKind, CompactAssetReceipt

_ACQUISITION_CONTRACT = "podcast-highlight-asset-acquisition-receipt-v1"
_RECEIPT_KEYS = {
    "contract",
    "episode_id",
    "cut_id",
    "revision_id",
    "attempt",
    "asset_id",
    "source_class",
    "provider",
    "provider_item_id",
    "source_url",
    "license",
    "acquired_at",
    "original_media",
    "content_hash",
}
_MEDIA_KEYS = {"path", "bytes", "sha256"}
_NEUTRAL_KINDS = frozenset({AssetKind.STOCK, AssetKind.PHOTO, AssetKind.NON_EDITORIAL_CLIP})
_MAX_PROBE_OUTPUT_CHARS = 65_536


class NeutralAssetImportError(AssetContractError):
    """One explicitly named legacy neutral acquisition cannot be proven safe to import."""


@dataclass(frozen=True, slots=True)
class NeutralMediaProbe:
    width: int
    height: int
    duration_sec: float | None

    def __post_init__(self) -> None:
        if type(self.width) is not int or self.width <= 0:
            raise NeutralAssetImportError("neutral media probe width is invalid")
        if type(self.height) is not int or self.height <= 0:
            raise NeutralAssetImportError("neutral media probe height is invalid")
        if self.duration_sec is not None and (
            isinstance(self.duration_sec, bool)
            or not isinstance(self.duration_sec, (int, float))
            or not math.isfinite(self.duration_sec)
            or self.duration_sec <= 0
        ):
            raise NeutralAssetImportError("neutral media probe duration is invalid")


class NeutralMediaInspector(Protocol):
    def inspect(self, path: Path) -> NeutralMediaProbe: ...


@dataclass(frozen=True, slots=True)
class NeutralProbeProcessResult:
    returncode: int
    stdout: str


class NeutralProbeProcessRunner(Protocol):
    def run(
        self,
        argv: tuple[str, ...],
        *,
        timeout_sec: float,
    ) -> NeutralProbeProcessResult: ...


class SubprocessNeutralProbeProcessRunner:
    """Production ffprobe process Adapter with no shell and no retained diagnostics."""

    def run(
        self,
        argv: tuple[str, ...],
        *,
        timeout_sec: float,
    ) -> NeutralProbeProcessResult:
        try:
            completed = subprocess.run(
                argv,
                check=False,
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=timeout_sec,
                shell=False,
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise NeutralAssetImportError("ffprobe could not inspect neutral media") from exc
        return NeutralProbeProcessResult(
            returncode=completed.returncode,
            stdout=completed.stdout[:_MAX_PROBE_OUTPUT_CHARS],
        )


class FfprobeNeutralMediaInspector:
    """Inspect one explicitly named migration source without discovering adjacent media."""

    def __init__(
        self,
        *,
        runner: NeutralProbeProcessRunner,
        executable: str = "ffprobe",
        timeout_sec: float = 30.0,
    ) -> None:
        if not executable.strip() or timeout_sec <= 0:
            raise NeutralAssetImportError("neutral media probe configuration is invalid")
        self._runner = runner
        self._executable = executable
        self._timeout_sec = timeout_sec

    def inspect(self, path: Path) -> NeutralMediaProbe:
        media = _regular_file(path, label="acquisition media")
        result = self._runner.run(
            (
                self._executable,
                "-v",
                "error",
                "-select_streams",
                "v:0",
                "-show_entries",
                "stream=width,height:format=duration",
                "-of",
                "json",
                str(media),
            ),
            timeout_sec=self._timeout_sec,
        )
        if result.returncode != 0:
            raise NeutralAssetImportError("ffprobe rejected neutral acquisition media")
        try:
            payload = json.loads(result.stdout)
            streams = payload["streams"]
            stream = streams[0] if len(streams) == 1 else None
            format_row = payload["format"]
            duration_value = format_row.get("duration")
            duration_sec = float(duration_value) if duration_value is not None else None
            return NeutralMediaProbe(
                width=stream["width"],
                height=stream["height"],
                duration_sec=duration_sec,
            )
        except (KeyError, IndexError, TypeError, ValueError, json.JSONDecodeError) as exc:
            raise NeutralAssetImportError("ffprobe returned invalid neutral media facts") from exc


@dataclass(frozen=True, slots=True)
class NeutralAssetImport:
    receipt_path: Path
    media_path: Path
    forensic_receipt_ref: str
    kind: AssetKind
    visual_summary: str
    width: int
    height: int
    duration_sec: float | None

    def __post_init__(self) -> None:
        object.__setattr__(self, "receipt_path", Path(self.receipt_path))
        object.__setattr__(self, "media_path", Path(self.media_path))
        if self.kind not in _NEUTRAL_KINDS:
            raise NeutralAssetImportError("migration importer accepts only neutral acquisitions")
        if not isinstance(self.visual_summary, str) or not self.visual_summary.strip():
            raise NeutralAssetImportError("neutral import visual summary is empty")
        if type(self.width) is not int or self.width <= 0:
            raise NeutralAssetImportError("neutral import width is invalid")
        if type(self.height) is not int or self.height <= 0:
            raise NeutralAssetImportError("neutral import height is invalid")
        if self.kind is AssetKind.PHOTO:
            if self.duration_sec is not None:
                raise NeutralAssetImportError("neutral photo import cannot declare duration")
        elif (
            isinstance(self.duration_sec, bool)
            or not isinstance(self.duration_sec, (int, float))
            or not math.isfinite(self.duration_sec)
            or self.duration_sec <= 0
        ):
            raise NeutralAssetImportError("neutral video import duration is invalid")


class LegacyNeutralAssetImporter:
    """Verify one named legacy receipt/media pair, then publish only compact provenance."""

    def __init__(self, *, store: ActiveAssetStore, inspector: NeutralMediaInspector) -> None:
        self._store = store
        self._inspector = inspector

    def import_one(self, request: NeutralAssetImport) -> str:
        receipt_path = _regular_file(request.receipt_path, label="acquisition receipt")
        media_path = _regular_file(request.media_path, label="acquisition media")
        try:
            receipt_bytes = receipt_path.read_bytes()
            receipt = json.loads(receipt_bytes)
        except (OSError, UnicodeError, json.JSONDecodeError) as exc:
            raise NeutralAssetImportError("acquisition receipt is unreadable") from exc
        if not isinstance(receipt, dict) or set(receipt) != _RECEIPT_KEYS:
            raise NeutralAssetImportError("acquisition receipt fields are invalid")
        if receipt.get("contract") != _ACQUISITION_CONTRACT:
            raise NeutralAssetImportError("acquisition receipt contract is invalid")
        if receipt.get("episode_id") != self._store.episode_id:
            raise NeutralAssetImportError("acquisition receipt belongs to another episode")
        for key in ("cut_id", "revision_id", "asset_id"):
            _required_text(receipt, key)
        attempt = receipt.get("attempt")
        if type(attempt) is not int or attempt <= 0:
            raise NeutralAssetImportError("acquisition receipt attempt is invalid")
        claimed_content_hash = receipt.get("content_hash")
        receipt_body = {key: value for key, value in receipt.items() if key != "content_hash"}
        if claimed_content_hash != _canonical_digest(receipt_body):
            raise NeutralAssetImportError("acquisition receipt content hash differs")
        expected_forensic_ref = "forensic-sha256:" + hashlib.sha256(receipt_bytes).hexdigest()
        if request.forensic_receipt_ref != expected_forensic_ref:
            raise NeutralAssetImportError("forensic acquisition receipt reference differs")

        media_identity = receipt.get("original_media")
        if not isinstance(media_identity, dict) or set(media_identity) != _MEDIA_KEYS:
            raise NeutralAssetImportError("acquisition receipt media identity is invalid")
        _required_text(media_identity, "path")
        media_sha256, media_bytes = _file_identity(media_path)
        if (
            media_identity.get("sha256") != media_sha256
            or media_identity.get("bytes") != media_bytes
        ):
            raise NeutralAssetImportError("acquisition media identity differs from receipt")

        try:
            probe = self._inspector.inspect(media_path)
        except (OSError, ValueError) as exc:
            raise NeutralAssetImportError("neutral acquisition media probe failed") from exc
        if (
            probe.width != request.width
            or probe.height != request.height
            or probe.duration_sec != request.duration_sec
        ):
            raise NeutralAssetImportError("neutral acquisition media facts differ")
        if request.kind is not AssetKind.PHOTO and probe.width <= probe.height:
            raise NeutralAssetImportError("neutral video acquisition is not native landscape")

        try:
            compact_receipt = CompactAssetReceipt(
                origin="neutral_acquisition",
                media_sha256=media_sha256,
                media_bytes=media_bytes,
                source_class=_required_text(receipt, "source_class"),
                provider=_required_text(receipt, "provider"),
                provider_item_id=_required_text(receipt, "provider_item_id"),
                source_url=_required_text(receipt, "source_url"),
                license=_required_text(receipt, "license"),
                acquired_at=_required_text(receipt, "acquired_at"),
                forensic_receipt_ref=request.forensic_receipt_ref,
            )
            resolution = self._store.publish(
                ActiveAssetPublication(
                    source_path=media_path,
                    kind=request.kind,
                    visual_summary=request.visual_summary,
                    width=request.width,
                    height=request.height,
                    duration_sec=request.duration_sec,
                    compact_receipt=compact_receipt,
                )
            )
        except (AssetContractError, ActiveAssetStoreError) as exc:
            raise NeutralAssetImportError("neutral acquisition publication was rejected") from exc
        return resolution.record.reference


def _regular_file(path: Path, *, label: str) -> Path:
    try:
        resolved = Path(path).resolve(strict=True)
    except (OSError, RuntimeError) as exc:
        raise NeutralAssetImportError(f"{label} is missing") from exc
    if not resolved.is_file():
        raise NeutralAssetImportError(f"{label} is not a regular file")
    return resolved


def _required_text(value: Mapping[str, object], key: str) -> str:
    item = value.get(key)
    if not isinstance(item, str) or not item or item != item.strip():
        raise NeutralAssetImportError(f"acquisition receipt field is invalid: {key}")
    return item


def _canonical_digest(value: Mapping[str, object]) -> str:
    try:
        encoded = json.dumps(
            value,
            ensure_ascii=False,
            allow_nan=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    except (TypeError, ValueError) as exc:
        raise NeutralAssetImportError("acquisition receipt is not canonical JSON") from exc
    return hashlib.sha256(encoded).hexdigest()


def _file_identity(path: Path) -> tuple[str, int]:
    digest = hashlib.sha256()
    byte_count = 0
    try:
        with path.open("rb") as handle:
            for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                digest.update(chunk)
                byte_count += len(chunk)
    except OSError as exc:
        raise NeutralAssetImportError("acquisition media is unreadable") from exc
    return digest.hexdigest(), byte_count
