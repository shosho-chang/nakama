"""Asset resolution boundary for Finished Cut Production.

The Active Asset Store may retain every byte needed to rebuild an existing
Release, while the much smaller Worker Selection Catalog exposes only neutral
acquisition media to a fresh DP request.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from math import isfinite
from pathlib import Path
from typing import Iterable, Literal, Protocol
from urllib.parse import urlsplit

_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
_FORENSIC_REFERENCE_RE = re.compile(r"^forensic-sha256:[0-9a-f]{64}$")
_UTC_SECONDS_RE = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$")
_PEXELS_LICENSE = "Pexels license: https://www.pexels.com/license/"
_ENVATO_LICENSE = "Envato Elements license: https://elements.envato.com/license-terms"
_ACQUISITION_SOURCE_CLASSES = frozenset(
    {
        "licensed_stock",
        "official_archive",
        "public_domain",
        "provided_self_archive",
        "provided_screen_demo",
        "provided_evidence_doc",
        "provided_general",
    }
)


class AssetContractError(ValueError):
    """An asset record or reference violates the production boundary."""


class AssetKind(str, Enum):
    """Closed provenance/meaning class used by the core asset registry."""

    STOCK = "stock"
    PHOTO = "photo"
    NON_EDITORIAL_CLIP = "non_editorial_clip"
    TITLE_RENDER = "title_render"
    CHAPTER_RENDER = "chapter_render"
    CONCEPT_RENDER = "concept_render"
    COMPOSITE = "composite"


_WORKER_SELECTABLE_KINDS = frozenset(
    {AssetKind.STOCK, AssetKind.PHOTO, AssetKind.NON_EDITORIAL_CLIP}
)
_SEMANTIC_RENDER_KINDS = frozenset(
    {
        AssetKind.TITLE_RENDER,
        AssetKind.CHAPTER_RENDER,
        AssetKind.CONCEPT_RENDER,
        AssetKind.COMPOSITE,
    }
)


@dataclass(frozen=True, slots=True)
class CompactAssetReceipt:
    """Sanitized Active Store provenance; it never carries a source path or old run identity."""

    origin: Literal["neutral_acquisition", "current_generated"]
    media_sha256: str
    media_bytes: int
    source_class: str | None = None
    provider: str | None = None
    provider_item_id: str | None = None
    source_url: str | None = None
    license: str | None = None
    acquired_at: str | None = None
    forensic_receipt_ref: str | None = None

    def __post_init__(self) -> None:
        if not _SHA256_RE.fullmatch(self.media_sha256):
            raise AssetContractError("compact receipt media digest must be lowercase SHA-256")
        if type(self.media_bytes) is not int or self.media_bytes <= 0:
            raise AssetContractError("compact receipt media bytes must be positive")
        source_facts = (
            self.source_class,
            self.provider,
            self.provider_item_id,
            self.source_url,
            self.license,
            self.acquired_at,
            self.forensic_receipt_ref,
        )
        if self.origin == "current_generated":
            if any(value is not None for value in source_facts):
                raise AssetContractError(
                    "current-generated receipt cannot invent acquisition facts"
                )
            return
        if self.origin != "neutral_acquisition":
            raise AssetContractError("compact receipt origin is invalid")
        if any(
            not isinstance(value, str) or not value or value != value.strip()
            for value in source_facts
        ):
            raise AssetContractError("neutral compact receipt requires sanitized source facts")
        assert self.source_url is not None
        parsed_url = urlsplit(self.source_url)
        if (
            parsed_url.scheme not in {"http", "https"}
            or not parsed_url.netloc
            or parsed_url.username is not None
            or parsed_url.password is not None
        ):
            raise AssetContractError("neutral compact receipt source URL is invalid")
        assert self.acquired_at is not None
        try:
            timestamp_is_valid = (
                _UTC_SECONDS_RE.fullmatch(self.acquired_at) is not None
                and datetime.strptime(self.acquired_at, "%Y-%m-%dT%H:%M:%SZ") is not None
            )
        except ValueError:
            timestamp_is_valid = False
        if not timestamp_is_valid:
            raise AssetContractError("neutral compact receipt acquisition time is invalid")
        assert self.forensic_receipt_ref is not None
        if _FORENSIC_REFERENCE_RE.fullmatch(self.forensic_receipt_ref) is None:
            raise AssetContractError("neutral compact receipt forensic reference is invalid")
        if self.source_class not in _ACQUISITION_SOURCE_CLASSES:
            raise AssetContractError("neutral compact receipt source class is invalid")
        if self.source_class == "licensed_stock":
            if self.provider == "pexels":
                expected_profile = (
                    re.fullmatch(r"[0-9]+", self.provider_item_id or "") is not None
                    and self.source_url == f"https://www.pexels.com/video/{self.provider_item_id}/"
                    and self.license == _PEXELS_LICENSE
                )
            elif self.provider == "envato-elements":
                escaped_item_id = re.escape(self.provider_item_id or "")
                expected_profile = (
                    re.fullmatch(r"[a-z0-9]+", self.provider_item_id or "") is not None
                    and re.fullmatch(
                        rf"https://elements\.envato\.com/[a-z0-9-]+-{escaped_item_id}",
                        self.source_url,
                    )
                    is not None
                    and self.license == _ENVATO_LICENSE
                )
            else:
                expected_profile = False
            if not expected_profile:
                raise AssetContractError(
                    "neutral compact receipt licensed source profile is invalid"
                )


@dataclass(frozen=True, slots=True)
class AssetRecord:
    """Core-owned metadata for one content-addressed media object."""

    digest: str
    extension: str
    kind: AssetKind
    visual_summary: str | None = None
    width: int | None = None
    height: int | None = None
    duration_sec: float | None = None
    recipe_identity: str | None = None
    release_ids: frozenset[str] = field(default_factory=frozenset)
    compact_receipt: CompactAssetReceipt | None = None

    def __post_init__(self) -> None:
        if not _SHA256_RE.fullmatch(self.digest):
            raise AssetContractError("asset digest must be lowercase SHA-256")
        if not re.fullmatch(r"\.[a-z0-9]+", self.extension):
            raise AssetContractError("asset extension must be a normalized suffix")
        if self.kind in _SEMANTIC_RENDER_KINDS and not self.recipe_identity:
            raise AssetContractError("semantic render must bind a recipe identity")
        if self.kind in _WORKER_SELECTABLE_KINDS and self.recipe_identity is not None:
            raise AssetContractError("neutral acquisition cannot bind a semantic recipe identity")
        if self.kind in _WORKER_SELECTABLE_KINDS:
            if (
                self.visual_summary is None
                or not self.visual_summary.strip()
                or type(self.width) is not int
                or self.width <= 0
                or type(self.height) is not int
                or self.height <= 0
            ):
                raise AssetContractError(
                    "worker-selectable asset requires usable acquisition metadata"
                )
            if self.kind is not AssetKind.PHOTO and (
                isinstance(self.duration_sec, bool)
                or not isinstance(self.duration_sec, (int, float))
                or not isfinite(self.duration_sec)
                or self.duration_sec <= 0
            ):
                raise AssetContractError(
                    "worker-selectable video requires usable acquisition metadata"
                )
            if self.kind is AssetKind.PHOTO and self.duration_sec is not None:
                raise AssetContractError("photo acquisition duration must be absent")
        if any(not release_id.strip() for release_id in self.release_ids):
            raise AssetContractError("release identity must not be empty")
        if self.compact_receipt is not None:
            if self.compact_receipt.media_sha256 != self.digest:
                raise AssetContractError("compact receipt media digest differs from asset")
            expected_origin = (
                "neutral_acquisition"
                if self.kind in _WORKER_SELECTABLE_KINDS
                else "current_generated"
            )
            if self.compact_receipt.origin != expected_origin:
                raise AssetContractError("compact receipt origin differs from asset kind")

    @property
    def reference(self) -> str:
        return f"asset-sha256:{self.digest}"


@dataclass(frozen=True, slots=True)
class WorkerCatalogItem:
    """Metadata a fresh DP is permitted to see."""

    reference: str
    kind: AssetKind
    visual_summary: str
    width: int
    height: int
    duration_sec: float | None

    def __post_init__(self) -> None:
        if not self.reference.startswith("asset-sha256:"):
            raise AssetContractError("worker catalog requires an opaque asset reference")
        if self.kind not in _WORKER_SELECTABLE_KINDS:
            raise AssetContractError("worker catalog cannot expose a semantic render")
        if (
            self.visual_summary is None
            or not self.visual_summary.strip()
            or type(self.width) is not int
            or self.width <= 0
            or type(self.height) is not int
            or self.height <= 0
        ):
            raise AssetContractError("worker catalog requires usable acquisition metadata")
        if self.kind is not AssetKind.PHOTO and (
            isinstance(self.duration_sec, bool)
            or not isinstance(self.duration_sec, (int, float))
            or not isfinite(self.duration_sec)
            or self.duration_sec <= 0
        ):
            raise AssetContractError("worker catalog video duration is invalid")
        if self.kind is AssetKind.PHOTO and self.duration_sec is not None:
            raise AssetContractError("worker catalog photo duration must be absent")


@dataclass(frozen=True, slots=True)
class ResolvedAsset:
    """A core-authorized object resolution; fixtures may omit a filesystem path."""

    record: AssetRecord
    path: Path | None = None


class AssetResolver(Protocol):
    """Small core-facing port shared by fixture and filesystem adapters."""

    def worker_selection_catalog(self) -> WorkerSelectionCatalog: ...

    def resolve_worker_asset(self, reference: str) -> ResolvedAsset: ...

    def resolve_active_asset(self, reference: str) -> ResolvedAsset: ...

    def resolve_for_release(self, release_id: str, reference: str) -> ResolvedAsset: ...

    def resolve_exact_recipe(self, recipe_identity: str) -> ResolvedAsset: ...


class WorkerSelectionCatalog:
    """Immutable allowlist of opaque references available to a fresh DP."""

    def __init__(self, items: Iterable[WorkerCatalogItem]) -> None:
        self._items = tuple(items)
        self._by_reference = {item.reference: item for item in self._items}

    def items(self) -> tuple[WorkerCatalogItem, ...]:
        return self._items

    def item(self, reference: str) -> WorkerCatalogItem:
        try:
            return self._by_reference[reference]
        except KeyError as error:
            raise AssetContractError("asset reference is not in the worker catalog") from error

    def resolve_dp_reference(self, reference: str) -> WorkerCatalogItem:
        """Resolve only references minted into this exact catalog view."""

        return self.item(reference)


class InMemoryAssetResolver:
    """Deterministic fixture adapter with the same catalog boundary as production."""

    def __init__(self, records: Iterable[AssetRecord]) -> None:
        self._records = tuple(records)
        if len({record.digest for record in self._records}) != len(self._records):
            raise AssetContractError("duplicate asset digest is not allowed")
        recipe_identities = [
            record.recipe_identity for record in self._records if record.recipe_identity is not None
        ]
        if len(set(recipe_identities)) != len(recipe_identities):
            raise AssetContractError("duplicate recipe identity is ambiguous")
        self._by_reference = {record.reference: record for record in self._records}
        self._by_recipe = {
            record.recipe_identity: record
            for record in self._records
            if record.recipe_identity is not None
        }

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
            if record.kind in _WORKER_SELECTABLE_KINDS
        )

    def resolve_worker_asset(self, reference: str) -> ResolvedAsset:
        self.worker_selection_catalog().resolve_dp_reference(reference)
        return ResolvedAsset(record=self._by_reference[reference])

    def resolve_active_asset(self, reference: str) -> ResolvedAsset:
        try:
            record = self._by_reference[reference]
        except KeyError as error:
            raise AssetContractError("asset reference is not in the active store") from error
        return ResolvedAsset(record=record)

    def publish_derived(self, record: AssetRecord) -> ResolvedAsset:
        """Managed fixture writer; intentionally absent from ``AssetResolver``."""

        if record.kind not in _SEMANTIC_RENDER_KINDS:
            raise AssetContractError("fixture publisher accepts only derived assets")
        if record.reference in self._by_reference:
            if self._by_reference[record.reference] != record:
                raise AssetContractError("asset digest has conflicting metadata")
            return ResolvedAsset(record=record)
        if record.recipe_identity in self._by_recipe:
            raise AssetContractError("duplicate recipe identity is ambiguous")
        self._records = (*self._records, record)
        self._by_reference[record.reference] = record
        if record.recipe_identity is not None:
            self._by_recipe[record.recipe_identity] = record
        return ResolvedAsset(record=record)

    def resolve_for_release(self, release_id: str, reference: str) -> ResolvedAsset:
        try:
            record = self._by_reference[reference]
        except KeyError as error:
            raise AssetContractError("asset reference is not in the active store") from error
        if release_id not in record.release_ids:
            raise AssetContractError("asset reference is not bound to this Release")
        return ResolvedAsset(record=record)

    def resolve_exact_recipe(self, recipe_identity: str) -> ResolvedAsset:
        try:
            record = self._by_recipe[recipe_identity]
        except KeyError as error:
            raise AssetContractError("no asset matches the exact recipe identity") from error
        return ResolvedAsset(record=record)


class ContentAddressedAssetResolver:
    """Filesystem adapter for ``assets-v2/sha256/<prefix>/<digest>.<ext>``."""

    def __init__(self, store_root: str | Path, records: Iterable[AssetRecord]) -> None:
        self._store_root = Path(store_root).resolve()
        self._index = InMemoryAssetResolver(records)

    def worker_selection_catalog(self) -> WorkerSelectionCatalog:
        catalog = self._index.worker_selection_catalog()
        for item in catalog.items():
            resolution = self._index.resolve_worker_asset(item.reference)
            self._object_path(resolution.record)
        return catalog

    def resolve_worker_asset(self, reference: str) -> ResolvedAsset:
        resolution = self._index.resolve_worker_asset(reference)
        return ResolvedAsset(
            record=resolution.record,
            path=self._object_path(resolution.record),
        )

    def resolve_active_asset(self, reference: str) -> ResolvedAsset:
        resolution = self._index.resolve_active_asset(reference)
        return ResolvedAsset(
            record=resolution.record,
            path=self._object_path(resolution.record),
        )

    def resolve_for_release(self, release_id: str, reference: str) -> ResolvedAsset:
        resolution = self._index.resolve_for_release(release_id, reference)
        return ResolvedAsset(
            record=resolution.record,
            path=self._object_path(resolution.record),
        )

    def resolve_exact_recipe(self, recipe_identity: str) -> ResolvedAsset:
        resolution = self._index.resolve_exact_recipe(recipe_identity)
        return ResolvedAsset(
            record=resolution.record,
            path=self._object_path(resolution.record),
        )

    def _object_path(self, record: AssetRecord) -> Path:
        candidate = (
            self._store_root / "sha256" / record.digest[:2] / f"{record.digest}{record.extension}"
        )
        try:
            resolved = candidate.resolve(strict=True)
            resolved.relative_to(self._store_root)
        except (FileNotFoundError, ValueError) as error:
            raise AssetContractError(
                "content-addressed asset is missing or outside the store"
            ) from error
        if not resolved.is_file():
            raise AssetContractError("content-addressed asset is not a regular file")
        return resolved
