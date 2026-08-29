"""Atomic episode-level Active Asset Store writer and resolver."""

from __future__ import annotations

import hashlib
import json
import os
import shutil
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any, Mapping

from ._assets import (
    AssetContractError,
    AssetKind,
    AssetRecord,
    CompactAssetReceipt,
    ResolvedAsset,
    WorkerCatalogItem,
    WorkerSelectionCatalog,
)

_INDEX_SCHEMA = "nakama.finished-cut-active-assets.v1"
_NEUTRAL_KINDS = frozenset({AssetKind.STOCK, AssetKind.PHOTO, AssetKind.NON_EDITORIAL_CLIP})
_INDEX_KEYS = {"schema", "payload_sha256", "payload"}
_INDEX_PAYLOAD_KEYS = {"episode_id", "records"}
_RECORD_KEYS = {
    "digest",
    "extension",
    "kind",
    "visual_summary",
    "width",
    "height",
    "duration_sec",
    "recipe_identity",
    "release_ids",
    "compact_receipt",
}


class ActiveAssetStoreError(AssetContractError):
    """The Active Asset Store cannot prove an exact content or index identity."""


@dataclass(frozen=True, slots=True)
class ActiveAssetPublication:
    source_path: Path
    kind: AssetKind
    visual_summary: str | None = None
    width: int | None = None
    height: int | None = None
    duration_sec: float | None = None
    recipe_identity: str | None = None
    release_ids: frozenset[str] = frozenset()
    compact_receipt: CompactAssetReceipt | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "source_path", Path(self.source_path))


class ActiveAssetStore:
    """Hide content publication, atomic indexing and exact resolution behind one Interface."""

    def __init__(
        self,
        *,
        root: Path,
        episode_id: str,
        records: tuple[AssetRecord, ...],
    ) -> None:
        self._root = root
        self._episode_id = episode_id
        self._records = records
        self._verified_signatures: dict[str, tuple[int, int]] = {}
        self._reindex()

    @classmethod
    def open(cls, root: str | Path, *, episode_id: str) -> ActiveAssetStore:
        if not isinstance(episode_id, str) or not episode_id.strip():
            raise ActiveAssetStoreError("Active Asset Store episode identity is empty")
        resolved_root = Path(root).resolve()
        records = _read_index(resolved_root / "index.v1.json", episode_id=episode_id)
        return cls(root=resolved_root, episode_id=episode_id, records=records)

    def publish(self, publication: ActiveAssetPublication) -> ResolvedAsset:
        source = publication.source_path.resolve(strict=True)
        if not source.is_file():
            raise ActiveAssetStoreError("asset publication source is not a regular file")
        extension = source.suffix.lower()
        digest = _file_digest(source)
        try:
            media_bytes = source.stat().st_size
        except OSError as exc:
            raise ActiveAssetStoreError("asset publication source is unreadable") from exc
        if publication.kind in _NEUTRAL_KINDS:
            compact_receipt = publication.compact_receipt
            if compact_receipt is None or compact_receipt.origin != "neutral_acquisition":
                raise ActiveAssetStoreError(
                    "neutral asset publication requires acquisition provenance"
                )
            if compact_receipt.media_sha256 != digest or compact_receipt.media_bytes != media_bytes:
                raise ActiveAssetStoreError(
                    "neutral acquisition provenance differs from source content"
                )
        else:
            if publication.compact_receipt is not None:
                raise ActiveAssetStoreError(
                    "generated asset provenance is minted by the Active Asset Store"
                )
            compact_receipt = CompactAssetReceipt(
                origin="current_generated",
                media_sha256=digest,
                media_bytes=media_bytes,
            )
        record = AssetRecord(
            digest=digest,
            extension=extension,
            kind=publication.kind,
            visual_summary=publication.visual_summary,
            width=publication.width,
            height=publication.height,
            duration_sec=publication.duration_sec,
            recipe_identity=publication.recipe_identity,
            release_ids=publication.release_ids,
            compact_receipt=compact_receipt,
        )
        prior = self._by_digest.get(digest)
        if prior is not None:
            if replace(prior, release_ids=record.release_ids) != record:
                raise ActiveAssetStoreError("content digest already has conflicting asset metadata")
            merged_release_ids = prior.release_ids | record.release_ids
            if merged_release_ids != prior.release_ids:
                prior = self._replace_record(
                    prior,
                    replace(prior, release_ids=merged_release_ids),
                )
            return self._resolve_record(prior)
        if record.recipe_identity is not None and record.recipe_identity in self._by_recipe:
            raise ActiveAssetStoreError("recipe identity already resolves to different content")

        target = self._object_path(record)
        _publish_object(source, target, expected_digest=digest)
        updated = (*self._records, record)
        _write_index(
            self._root / "index.v1.json",
            episode_id=self._episode_id,
            records=updated,
        )
        self._records = updated
        self._reindex()
        return self._resolve_record(record)

    @property
    def episode_id(self) -> str:
        return self._episode_id

    def resolve_exact_recipe(self, recipe_identity: str) -> ResolvedAsset:
        try:
            record = self._by_recipe[recipe_identity]
        except KeyError as exc:
            raise ActiveAssetStoreError(
                "no asset matches the exact current recipe identity"
            ) from exc
        return self._resolve_record(record)

    def find_exact_recipe(self, recipe_identity: str) -> ResolvedAsset | None:
        record = self._by_recipe.get(recipe_identity)
        if record is None:
            return None
        return self._resolve_record(record)

    def worker_selection_catalog(self) -> WorkerSelectionCatalog:
        return WorkerSelectionCatalog(
            WorkerCatalogItem(
                reference=record.reference,
                kind=record.kind,
                visual_summary=record.visual_summary,
                width=record.width,
                height=record.height,
                duration_sec=record.duration_sec,
            )
            for record in self._records
            if record.kind in _NEUTRAL_KINDS
        )

    def resolve_worker_asset(self, reference: str) -> ResolvedAsset:
        self.worker_selection_catalog().resolve_dp_reference(reference)
        return self._resolve_record(self._record_for_reference(reference))

    def resolve_active_asset(self, reference: str) -> ResolvedAsset:
        return self._resolve_record(self._record_for_reference(reference))

    def resolve_for_release(self, release_id: str, reference: str) -> ResolvedAsset:
        record = self._record_for_reference(reference)
        if release_id not in record.release_ids:
            raise ActiveAssetStoreError("asset reference is not bound to this Release")
        return self._resolve_record(record)

    def bind_release(self, reference: str, *, release_id: str) -> ResolvedAsset:
        if not isinstance(release_id, str) or not release_id.strip():
            raise ActiveAssetStoreError("Release identity is empty")
        record = self._record_for_reference(reference)
        if release_id in record.release_ids:
            return self._resolve_record(record)
        updated_record = replace(record, release_ids=record.release_ids | {release_id})
        self._replace_record(record, updated_record)
        return self._resolve_record(updated_record)

    def _replace_record(self, prior: AssetRecord, updated_record: AssetRecord) -> AssetRecord:
        updated = tuple(
            updated_record if item.digest == prior.digest else item for item in self._records
        )
        _write_index(
            self._root / "index.v1.json",
            episode_id=self._episode_id,
            records=updated,
        )
        self._records = updated
        self._reindex()
        return updated_record

    def _reindex(self) -> None:
        if any(record.compact_receipt is None for record in self._records):
            raise ActiveAssetStoreError("Active Asset Store record lacks compact provenance")
        if len({record.digest for record in self._records}) != len(self._records):
            raise ActiveAssetStoreError("Active Asset Store index has duplicate content identity")
        recipe_ids = tuple(
            record.recipe_identity for record in self._records if record.recipe_identity is not None
        )
        if len(set(recipe_ids)) != len(recipe_ids):
            raise ActiveAssetStoreError("Active Asset Store index has duplicate recipe identity")
        self._by_digest = {record.digest: record for record in self._records}
        self._by_reference = {record.reference: record for record in self._records}
        self._by_recipe = {
            record.recipe_identity: record
            for record in self._records
            if record.recipe_identity is not None
        }

    def _record_for_reference(self, reference: str) -> AssetRecord:
        try:
            return self._by_reference[reference]
        except KeyError as exc:
            raise ActiveAssetStoreError("asset reference is not in the Active Asset Store") from exc

    def _object_path(self, record: AssetRecord) -> Path:
        return self._root / "sha256" / record.digest[:2] / f"{record.digest}{record.extension}"

    def _resolve_record(self, record: AssetRecord) -> ResolvedAsset:
        path = self._object_path(record)
        try:
            resolved = path.resolve(strict=True)
            resolved.relative_to(self._root)
        except (FileNotFoundError, ValueError) as exc:
            raise ActiveAssetStoreError(
                "active asset object is missing or outside its store"
            ) from exc
        if not resolved.is_file():
            raise ActiveAssetStoreError("active asset object content is corrupt")
        try:
            stat = resolved.stat()
        except OSError as exc:
            raise ActiveAssetStoreError("active asset object is unreadable") from exc
        signature = (stat.st_size, stat.st_mtime_ns)
        if self._verified_signatures.get(record.digest) != signature:
            if _file_digest(resolved) != record.digest:
                raise ActiveAssetStoreError("active asset object content is corrupt")
            self._verified_signatures[record.digest] = signature
        return ResolvedAsset(record=record, path=resolved)


def _publish_object(source: Path, target: Path, *, expected_digest: str) -> None:
    if target.exists():
        if not target.is_file() or _file_digest(target) != expected_digest:
            raise ActiveAssetStoreError("content-addressed target is corrupt")
        return
    staging = target.with_name(f".{target.name}.staging")
    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        with source.open("rb") as reader, staging.open("xb") as writer:
            shutil.copyfileobj(reader, writer, length=1024 * 1024)
            writer.flush()
            os.fsync(writer.fileno())
        if _file_digest(staging) != expected_digest:
            raise ActiveAssetStoreError("staged asset digest differs from source")
        os.replace(staging, target)
    except OSError as exc:
        raise ActiveAssetStoreError("atomic asset publication failed") from exc


def _read_index(path: Path, *, episode_id: str) -> tuple[AssetRecord, ...]:
    staging = _staging_path(path)
    if staging.exists():
        raise ActiveAssetStoreError("incomplete Active Asset Store index exists")
    if not path.exists():
        return ()
    try:
        envelope = json.loads(path.read_bytes())
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise ActiveAssetStoreError("Active Asset Store index is unreadable") from exc
    if (
        not isinstance(envelope, dict)
        or set(envelope) != _INDEX_KEYS
        or envelope.get("schema") != _INDEX_SCHEMA
    ):
        raise ActiveAssetStoreError("Active Asset Store index schema is invalid")
    payload = envelope.get("payload")
    if not isinstance(payload, dict) or set(payload) != _INDEX_PAYLOAD_KEYS:
        raise ActiveAssetStoreError("Active Asset Store index payload is invalid")
    digest = envelope.get("payload_sha256")
    if digest != hashlib.sha256(_canonical_json(payload)).hexdigest():
        raise ActiveAssetStoreError("Active Asset Store index checksum differs")
    if payload.get("episode_id") != episode_id:
        raise ActiveAssetStoreError("Active Asset Store index belongs to another episode")
    rows = payload.get("records")
    if not isinstance(rows, list) or any(not isinstance(row, dict) for row in rows):
        raise ActiveAssetStoreError("Active Asset Store records are invalid")
    if any(set(row) != _RECORD_KEYS for row in rows):
        raise ActiveAssetStoreError("Active Asset Store record fields are invalid")
    try:
        return tuple(
            AssetRecord(
                digest=_required_string(row, "digest"),
                extension=_required_string(row, "extension"),
                kind=AssetKind(_required_string(row, "kind")),
                visual_summary=_optional_string(row, "visual_summary"),
                width=_optional_integer(row, "width"),
                height=_optional_integer(row, "height"),
                duration_sec=_optional_number(row, "duration_sec"),
                recipe_identity=_optional_string(row, "recipe_identity"),
                release_ids=frozenset(_string_list(row, "release_ids")),
                compact_receipt=_compact_receipt_from_dict(row.get("compact_receipt")),
            )
            for row in rows
        )
    except (ValueError, TypeError) as exc:
        raise ActiveAssetStoreError("Active Asset Store record is invalid") from exc


def _write_index(path: Path, *, episode_id: str, records: tuple[AssetRecord, ...]) -> None:
    payload: dict[str, Any] = {
        "episode_id": episode_id,
        "records": [
            {
                "digest": record.digest,
                "extension": record.extension,
                "kind": record.kind.value,
                "visual_summary": record.visual_summary,
                "width": record.width,
                "height": record.height,
                "duration_sec": record.duration_sec,
                "recipe_identity": record.recipe_identity,
                "release_ids": sorted(record.release_ids),
                "compact_receipt": _compact_receipt_to_dict(record.compact_receipt),
            }
            for record in records
        ],
    }
    envelope = {
        "schema": _INDEX_SCHEMA,
        "payload_sha256": hashlib.sha256(_canonical_json(payload)).hexdigest(),
        "payload": payload,
    }
    encoded = _canonical_json(envelope) + b"\n"
    staging = _staging_path(path)
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with staging.open("xb") as handle:
            handle.write(encoded)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(staging, path)
    except OSError as exc:
        raise ActiveAssetStoreError("atomic Active Asset Store index write failed") from exc


def _staging_path(path: Path) -> Path:
    return path.with_name(f".{path.name}.staging")


def _file_digest(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _canonical_json(value: Mapping[str, Any]) -> bytes:
    try:
        return json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")
    except (TypeError, ValueError) as exc:
        raise ActiveAssetStoreError("Active Asset Store index is not canonical JSON") from exc


def _required_string(payload: Mapping[str, Any], key: str) -> str:
    value = payload.get(key)
    if not isinstance(value, str) or not value:
        raise ActiveAssetStoreError(f"Active Asset Store field is not a string: {key}")
    return value


def _optional_string(payload: Mapping[str, Any], key: str) -> str | None:
    value = payload.get(key)
    if value is None:
        return None
    if not isinstance(value, str) or not value:
        raise ActiveAssetStoreError(f"Active Asset Store field is not optional text: {key}")
    return value


def _string_list(payload: Mapping[str, Any], key: str) -> tuple[str, ...]:
    value = payload.get(key)
    if not isinstance(value, list) or any(not isinstance(item, str) or not item for item in value):
        raise ActiveAssetStoreError(f"Active Asset Store field is not a string list: {key}")
    return tuple(value)


def _optional_integer(payload: Mapping[str, Any], key: str) -> int | None:
    value = payload.get(key)
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, int):
        raise ActiveAssetStoreError(f"Active Asset Store field is not an optional integer: {key}")
    return value


def _optional_number(payload: Mapping[str, Any], key: str) -> float | None:
    value = payload.get(key)
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ActiveAssetStoreError(f"Active Asset Store field is not optional numeric: {key}")
    return float(value)


def _compact_receipt_to_dict(receipt: CompactAssetReceipt | None) -> dict[str, object]:
    if receipt is None:
        raise ActiveAssetStoreError("Active Asset Store record lacks compact provenance")
    result: dict[str, object] = {
        "origin": receipt.origin,
        "media_sha256": receipt.media_sha256,
        "media_bytes": receipt.media_bytes,
    }
    if receipt.origin == "neutral_acquisition":
        result.update(
            {
                "source_class": receipt.source_class,
                "provider": receipt.provider,
                "provider_item_id": receipt.provider_item_id,
                "source_url": receipt.source_url,
                "license": receipt.license,
                "acquired_at": receipt.acquired_at,
                "forensic_receipt_ref": receipt.forensic_receipt_ref,
            }
        )
    return result


def _compact_receipt_from_dict(value: object) -> CompactAssetReceipt:
    if not isinstance(value, dict):
        raise ActiveAssetStoreError("Active Asset Store compact receipt is invalid")
    origin = value.get("origin")
    common_keys = {"origin", "media_sha256", "media_bytes"}
    neutral_keys = common_keys | {
        "source_class",
        "provider",
        "provider_item_id",
        "source_url",
        "license",
        "acquired_at",
        "forensic_receipt_ref",
    }
    expected_keys = common_keys if origin == "current_generated" else neutral_keys
    if set(value) != expected_keys:
        raise ActiveAssetStoreError("Active Asset Store compact receipt fields are invalid")
    try:
        return CompactAssetReceipt(
            origin=origin,
            media_sha256=value["media_sha256"],
            media_bytes=value["media_bytes"],
            source_class=value.get("source_class"),
            provider=value.get("provider"),
            provider_item_id=value.get("provider_item_id"),
            source_url=value.get("source_url"),
            license=value.get("license"),
            acquired_at=value.get("acquired_at"),
            forensic_receipt_ref=value.get("forensic_receipt_ref"),
        )
    except (AssetContractError, KeyError, TypeError) as exc:
        raise ActiveAssetStoreError("Active Asset Store compact receipt is invalid") from exc
