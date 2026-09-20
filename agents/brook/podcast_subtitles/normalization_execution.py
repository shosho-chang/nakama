"""Crash-recoverable Auphonic execution over the durable normalization FSM.

The provider interface in this module is deliberately stepwise.  Every
non-repeatable provider call is preceded by a durable intent transition and a
fresh process can continue solely from the authenticated run prefix.  Provider
payloads, titles, signed URLs, credentials, and exception bodies never enter
the repository.
"""

from __future__ import annotations

import math
import os
import re
import secrets
import stat
import threading
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Literal, Protocol, runtime_checkable

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from shared.schemas.podcast_subtitles_v2 import (
    ArtifactDigest,
    AudioClockMap,
    NormalizationMetric,
    NormalizationParameter,
    NormalizationReceipt,
)

from .hashing import canonical_json_bytes, hash_file, hash_object, sha256_bytes
from .normalization_run import (
    ExternalAnchorReconciliationPageV1,
    NormalizationAdapterIdentityV1,
    NormalizationAlignmentPolicyV1,
    NormalizationArtifactBindingV1,
    NormalizationContentDigestV1,
    NormalizationOutputContractV1,
    NormalizationRunIntegrityError,
    NormalizationRunRepository,
    NormalizationRunRequestV1,
    NormalizationSettingV1,
    ProviderObservationV1,
    StoredNormalizationRunV1,
    build_external_anchor_reconciliation,
    build_normalization_run_proof,
    build_normalization_run_request,
    build_provider_observation,
    normalization_recovery_key,
)
from .ports import (
    AdapterInputError,
    AdapterIntegrityError,
    AdapterUnavailableError,
    NormalizationResult,
    NormalizeRequest,
)
from .store import GenerationStore, _replace_fsynced, _write_fsynced

_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
_CREDENTIAL_RE = re.compile(r"^cred_[0-9a-f]{32}$")
_PRODUCTION_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9-]{7,127}$")
_COPY_CHUNK_SIZE = 4 * 1024 * 1024


def _validate_uuid_value(value: object) -> None:
    if not isinstance(value, str) or _PRODUCTION_RE.fullmatch(value) is None:
        raise ValueError("Auphonic production UUID is malformed")


def _validate_http_status(value: object) -> None:
    if type(value) is not int or not 100 <= value <= 599:
        raise ValueError("Auphonic HTTP status must be an exact integer")


def _validate_optional_exact_int(value: object, label: str) -> None:
    if value is not None and type(value) is not int:
        raise ValueError(f"Auphonic {label} must be an exact integer")


def _validate_optional_hash(value: object, label: str) -> None:
    if value is not None and (not isinstance(value, str) or _SHA256_RE.fullmatch(value) is None):
        raise ValueError(f"Auphonic {label} must be lowercase SHA-256")


def _validate_datetime_value(value: object) -> None:
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("Auphonic provider timestamp must be timezone-aware")


class _ExecutionContract(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)


class MediaProbeV1(_ExecutionContract):
    """Safe deterministic media facts; paths and raw ffprobe output are excluded."""

    schema_version: Literal[1] = 1
    duration_ms: int = Field(gt=0)
    container: str = Field(min_length=1, max_length=64)
    codec: str = Field(min_length=1, max_length=64)
    bit_depth: int = Field(gt=0, le=64)
    audio_stream_count: int = Field(ge=1, le=8)
    channels: int = Field(ge=1, le=32)
    sample_rate_hz: int = Field(ge=8_000, le=384_000)

    @field_validator(
        "duration_ms",
        "bit_depth",
        "audio_stream_count",
        "channels",
        "sample_rate_hz",
        mode="before",
    )
    @classmethod
    def _integers_are_exact(cls, value: object, info: object) -> object:
        if type(value) is not int:
            raise ValueError(f"media probe {getattr(info, 'field_name', 'value')} must be exact")
        return value

    @field_validator("container", "codec")
    @classmethod
    def _media_text_is_safe(cls, value: str) -> str:
        if value != value.strip() or re.fullmatch(r"[A-Za-z0-9._-]+", value) is None:
            raise ValueError("media probe text must be a safe identifier")
        return value


class AlignmentEvidenceV1(_ExecutionContract):
    """Persisted local evidence used to construct the accepted clock receipt."""

    schema_version: Literal[1] = 1
    method: Literal["cross_correlation", "identity"]
    clock_map: AudioClockMap
    head_correlation: float | None = Field(default=None, ge=0.0, le=1.0)
    mid_correlation: float | None = Field(default=None, ge=0.0, le=1.0)

    @model_validator(mode="after")
    def _method_has_required_evidence(self) -> "AlignmentEvidenceV1":
        if not self.clock_map.verified:
            raise ValueError("alignment evidence requires a verified clock map")
        if self.method == "cross_correlation" and (
            self.head_correlation is None or self.mid_correlation is None
        ):
            raise ValueError("cross-correlation evidence requires both measured peaks")
        return self


@dataclass(frozen=True, slots=True)
class AuphonicCredentialV1:
    """In-memory credential handle; only ``credential_ref`` may be persisted."""

    credential_ref: str
    handle: object = field(repr=False, compare=False)
    available_for_new_run: bool = True
    selection_rank: int = 0

    def __post_init__(self) -> None:
        if _CREDENTIAL_RE.fullmatch(self.credential_ref) is None:
            raise ValueError("Auphonic credential_ref must be opaque")
        if type(self.available_for_new_run) is not bool or type(self.selection_rank) is not int:
            raise ValueError("Auphonic credential availability/rank must be exact")


@dataclass(frozen=True, slots=True)
class AuphonicCreateResultV1:
    production_uuid: str
    http_status_code: int
    provider_created_at: datetime

    def __post_init__(self) -> None:
        _validate_uuid_value(self.production_uuid)
        _validate_http_status(self.http_status_code)
        _validate_datetime_value(self.provider_created_at)


@dataclass(frozen=True, slots=True)
class AuphonicMutationResultV1:
    production_uuid: str
    http_status_code: int
    provider_status_code: int | None = None

    def __post_init__(self) -> None:
        _validate_uuid_value(self.production_uuid)
        _validate_http_status(self.http_status_code)
        _validate_optional_exact_int(self.provider_status_code, "provider status")


@dataclass(frozen=True, slots=True)
class AuphonicProductionProjectionV1:
    production_uuid: str
    title: str = field(repr=False)
    provider_created_at: datetime | None = None

    def __post_init__(self) -> None:
        _validate_uuid_value(self.production_uuid)
        if not isinstance(self.title, str) or len(self.title) > 512:
            raise ValueError("Auphonic production title is malformed")
        if self.provider_created_at is not None:
            _validate_datetime_value(self.provider_created_at)


@dataclass(frozen=True, slots=True)
class AuphonicProductionPageV1:
    offset: int
    limit: int
    records: tuple[AuphonicProductionProjectionV1, ...]

    def __post_init__(self) -> None:
        if type(self.offset) is not int or self.offset < 0:
            raise ValueError("Auphonic page offset must be an exact non-negative integer")
        if type(self.limit) is not int or not 1 <= self.limit <= 1_000:
            raise ValueError("Auphonic page limit is outside contract")
        if not isinstance(self.records, tuple) or len(self.records) > self.limit:
            raise ValueError("Auphonic page records exceed the page contract")


@dataclass(frozen=True, slots=True)
class AuphonicUploadInspectionV1:
    production_uuid: str
    http_status_code: int
    source_checksum: str | None

    def __post_init__(self) -> None:
        _validate_uuid_value(self.production_uuid)
        _validate_http_status(self.http_status_code)
        _validate_optional_hash(self.source_checksum, "source checksum")


@dataclass(frozen=True, slots=True)
class AuphonicPollResultV1:
    production_uuid: str
    http_status_code: int
    provider_status_code: int
    submitted_settings_hash: str | None = None
    source_checksum: str | None = None
    output_contract_hash: str | None = None
    provider_created_at: datetime | None = None
    provider_completed_at: datetime | None = None

    def __post_init__(self) -> None:
        _validate_uuid_value(self.production_uuid)
        _validate_http_status(self.http_status_code)
        _validate_optional_exact_int(self.provider_status_code, "provider status")
        for value, label in (
            (self.submitted_settings_hash, "submitted settings hash"),
            (self.source_checksum, "source checksum"),
            (self.output_contract_hash, "output contract hash"),
        ):
            _validate_optional_hash(value, label)
        for value in (self.provider_created_at, self.provider_completed_at):
            if value is not None:
                _validate_datetime_value(value)
        if self.provider_created_at and self.provider_completed_at:
            if self.provider_completed_at < self.provider_created_at:
                raise ValueError("Auphonic provider time order is invalid")


@dataclass(frozen=True, slots=True)
class AlignmentResultV1:
    output_path: Path
    method: Literal["cross_correlation", "identity", "fixed_seconds", "not_requested"]
    verified: bool
    source_origin_ms: int
    normalized_origin_ms: int
    drift_ms: float
    head_correlation: float | None = None
    mid_correlation: float | None = None
    failure_code: str | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.output_path, Path):
            raise ValueError("alignment output_path must be Path")
        if type(self.verified) is not bool:
            raise ValueError("alignment verified must be exact bool")
        if type(self.source_origin_ms) is not int or self.source_origin_ms < 0:
            raise ValueError("alignment source origin is invalid")
        if type(self.normalized_origin_ms) is not int or self.normalized_origin_ms < 0:
            raise ValueError("alignment normalized origin is invalid")
        for value, label in (
            (self.drift_ms, "drift"),
            (self.head_correlation, "head correlation"),
            (self.mid_correlation, "mid correlation"),
        ):
            if value is not None and (
                type(value) not in {int, float} or not math.isfinite(float(value))
            ):
                raise ValueError(f"alignment {label} must be finite")
        for value in (self.head_correlation, self.mid_correlation):
            if value is not None and not 0.0 <= float(value) <= 1.0:
                raise ValueError("alignment correlation is outside [0, 1]")
        if self.failure_code is not None and (
            not self.failure_code
            or len(self.failure_code) > 128
            or re.fullmatch(r"[A-Za-z0-9._-]+", self.failure_code) is None
        ):
            raise ValueError("alignment failure code is unsafe")


class AuphonicProviderFailure(RuntimeError):
    """Redacted provider failure; no response body, URL, title, or credential."""

    def __init__(
        self,
        operation: Literal[
            "credentials",
            "create",
            "reconcile",
            "upload",
            "inspect_upload",
            "start",
            "poll",
            "download",
        ],
        *,
        category: Literal["transport", "timeout", "http", "malformed"],
        http_status_code: int | None = None,
        definitive: bool = False,
    ) -> None:
        self.operation = operation
        self.category = category
        self.http_status_code = http_status_code
        self.definitive = definitive
        suffix = f" HTTP {http_status_code}" if http_status_code is not None else ""
        super().__init__(f"Auphonic {operation} failed ({category}{suffix})")


@runtime_checkable
class AuphonicStepwiseProvider(Protocol):
    """Provider operations with secrets and raw payloads confined to the Adapter."""

    def credentials(self, *, source_duration_ms: int) -> tuple[AuphonicCredentialV1, ...]: ...

    def create(
        self,
        credential: AuphonicCredentialV1,
        *,
        external_anchor: str,
        settings: tuple[NormalizationSettingV1, ...],
        preset: str | None,
    ) -> AuphonicCreateResultV1: ...

    def reconcile_page(
        self,
        credential: AuphonicCredentialV1,
        *,
        offset: int,
        limit: int,
    ) -> AuphonicProductionPageV1: ...

    def upload(
        self,
        credential: AuphonicCredentialV1,
        *,
        production_uuid: str,
        source_audio: Path,
    ) -> AuphonicMutationResultV1: ...

    def inspect_upload(
        self,
        credential: AuphonicCredentialV1,
        *,
        production_uuid: str,
    ) -> AuphonicUploadInspectionV1: ...

    def start(
        self,
        credential: AuphonicCredentialV1,
        *,
        production_uuid: str,
    ) -> AuphonicMutationResultV1: ...

    def poll(
        self,
        credential: AuphonicCredentialV1,
        *,
        production_uuid: str,
    ) -> AuphonicPollResultV1: ...

    def download(
        self,
        credential: AuphonicCredentialV1,
        *,
        production_uuid: str,
        destination: Path,
    ) -> None: ...


@runtime_checkable
class MediaInspector(Protocol):
    def probe(self, path: Path) -> MediaProbeV1: ...


@runtime_checkable
class AudioAligner(Protocol):
    def align(
        self,
        raw_audio: Path,
        source_audio: Path,
        *,
        policy: NormalizationAlignmentPolicyV1,
    ) -> AlignmentResultV1: ...


def _link_like(path: Path) -> bool:
    try:
        attrs = getattr(path.lstat(), "st_file_attributes", 0)
    except OSError:
        return False
    reparse = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)
    return path.is_symlink() or bool(attrs & reparse)


@contextmanager
def _file_lock(path: Path):
    try:
        from filelock import FileLock, Timeout
    except ImportError as exc:  # pragma: no cover - core dependency
        raise NormalizationRunIntegrityError("normalization execution requires filelock") from exc
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        with FileLock(str(path)).acquire(timeout=30):
            yield
    except Timeout as exc:
        raise AdapterUnavailableError("normalization execution lease is busy") from exc


class NormalizationArtifactStore:
    """Strict content-addressed store for probes, clock maps, and receipts."""

    def __init__(self, subtitle_root: Path) -> None:
        self.subtitle_root = Path(subtitle_root).absolute()
        self.root = self.subtitle_root / "normalization-artifacts"
        self.blobs = self.root / "sha256"
        self.locks = self.root / "locks"
        self.execution_locks = self.root / "execution-locks"
        self.downloads = self.root / "downloads"
        self._lock = threading.RLock()

    def _validate(self, *, create: bool) -> None:
        for path in (
            self.subtitle_root,
            self.root,
            self.blobs,
            self.locks,
            self.execution_locks,
            self.downloads,
        ):
            if _link_like(path):
                raise NormalizationRunIntegrityError(
                    "normalization artifact topology contains a link"
                )
            if path.exists() and not path.is_dir():
                raise NormalizationRunIntegrityError(
                    "normalization artifact topology is not a directory"
                )
        if create:
            self.subtitle_root.mkdir(parents=True, exist_ok=True)
            self.root.mkdir(exist_ok=True)
            self.blobs.mkdir(exist_ok=True)
            self.locks.mkdir(exist_ok=True)
            self.execution_locks.mkdir(exist_ok=True)
            self.downloads.mkdir(exist_ok=True)
        if (
            not self.root.is_dir()
            or self.root.resolve(strict=True)
            != self.subtitle_root.resolve(strict=True) / self.root.name
        ):
            raise NormalizationRunIntegrityError(
                "normalization artifact root escaped subtitle root"
            )
        allowed = {"sha256", "locks", "execution-locks", "downloads"}
        if {item.name for item in self.root.iterdir()} != allowed:
            raise NormalizationRunIntegrityError("normalization artifact root has unknown entries")
        blob_entries = tuple(self.blobs.iterdir())
        if len(blob_entries) > 100_000:
            raise NormalizationRunIntegrityError("normalization artifact inventory is unbounded")
        for entry in blob_entries:
            final_name = _SHA256_RE.fullmatch(entry.name) is not None
            tail_name = re.fullmatch(r"\.[0-9a-f]{64}\.[0-9a-f]{16}\.tmp", entry.name) is not None
            if _link_like(entry) or not entry.is_file() or not (final_name or tail_name):
                raise NormalizationRunIntegrityError("normalization artifact inventory is invalid")
        for directory in (self.locks, self.execution_locks):
            for entry in directory.iterdir():
                if (
                    _link_like(entry)
                    or not entry.is_file()
                    or re.fullmatch(r"[0-9a-f]{64}\.lock", entry.name) is None
                ):
                    raise NormalizationRunIntegrityError(
                        "normalization artifact lock inventory is invalid"
                    )
        downloads = tuple(self.downloads.iterdir())
        if len(downloads) > 128:
            raise NormalizationRunIntegrityError(
                "normalization download tail inventory is unbounded"
            )
        for entry in downloads:
            if (
                _link_like(entry)
                or not entry.is_file()
                or re.fullmatch(r"\.download-[A-Za-z0-9_-]+\.media", entry.name) is None
            ):
                raise NormalizationRunIntegrityError(
                    "normalization download tail inventory is invalid"
                )

    @contextmanager
    def execution_lease(self, request_key: str):
        if _SHA256_RE.fullmatch(request_key) is None:
            raise ValueError("normalization execution key is invalid")
        self._validate(create=True)
        with _file_lock(self.execution_locks / f"{request_key}.lock"):
            self._validate(create=False)
            yield

    def put_bytes(self, payload: bytes) -> NormalizationContentDigestV1:
        digest = NormalizationContentDigestV1(sha256=sha256_bytes(payload), size_bytes=len(payload))
        with self._lock:
            self._validate(create=True)
            destination = self.blobs / digest.sha256
            with _file_lock(self.locks / f"{digest.sha256}.lock"):
                self._recover_blob_tail(digest)
                if destination.exists():
                    existing = destination.read_bytes()
                    if existing != payload:
                        raise NormalizationRunIntegrityError(
                            "normalization artifact hash collision"
                        )
                else:
                    temporary = self.blobs / f".{digest.sha256}.{secrets.token_hex(8)}.tmp"
                    _write_fsynced(temporary, payload)
                    _replace_fsynced(temporary, destination)
                self._verify_path(destination, digest)
        return digest

    def read(self, digest: NormalizationContentDigestV1) -> bytes:
        with self._lock:
            self._validate(create=False)
            with _file_lock(self.locks / f"{digest.sha256}.lock"):
                self._recover_blob_tail(digest)
                path = self.blobs / digest.sha256
                self._verify_path(path, digest)
                return path.read_bytes()

    def read_hash(self, sha256: str) -> bytes:
        """Read one known-hash blob without exposing filesystem errors or paths."""

        if _SHA256_RE.fullmatch(sha256) is None:
            raise ValueError("normalization artifact hash is invalid")
        self._validate(create=False)
        path = self.blobs / sha256
        if _link_like(path) or not path.is_file():
            raise NormalizationRunIntegrityError("normalization artifact is missing or linked")
        try:
            payload = path.read_bytes()
        except OSError as exc:
            raise NormalizationRunIntegrityError("normalization artifact is unreadable") from exc
        if sha256_bytes(payload) != sha256:
            raise NormalizationRunIntegrityError("normalization artifact bytes changed")
        return payload

    def _recover_blob_tail(self, digest: NormalizationContentDigestV1) -> None:
        destination = self.blobs / digest.sha256
        tails = tuple(self.blobs.glob(f".{digest.sha256}.*.tmp"))
        if len(tails) > 1:
            raise NormalizationRunIntegrityError(
                "normalization artifact has multiple publication tails"
            )
        if not tails:
            return
        tail = tails[0]
        self._verify_path(tail, digest)
        if destination.exists():
            self._verify_path(destination, digest)
            tail.unlink()
        else:
            _replace_fsynced(tail, destination)

    def download_path(self, request_key: str) -> Path:
        if _SHA256_RE.fullmatch(request_key) is None:
            raise ValueError("normalization download request key is invalid")
        self._validate(create=True)
        path = self.downloads / f".download-{request_key}.media"
        if not path.exists():
            descriptor = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
            os.close(descriptor)
        if _link_like(path) or not path.is_file():  # pragma: no cover - hostile FS race
            raise NormalizationRunIntegrityError("normalization download temp is unsafe")
        return path

    def _verify_path(self, path: Path, digest: NormalizationContentDigestV1) -> None:
        if _link_like(path) or not path.is_file():
            raise NormalizationRunIntegrityError("normalization artifact is missing or linked")
        if path.stat().st_size != digest.size_bytes or hash_file(path) != digest.sha256:
            raise NormalizationRunIntegrityError("normalization artifact bytes changed")


def _binding(name: str, digest: NormalizationContentDigestV1) -> NormalizationArtifactBindingV1:
    return NormalizationArtifactBindingV1(name=name, digest=digest)  # type: ignore[arg-type]


def _observation(
    operation: Literal["create", "reconcile", "upload", "inspect_upload", "start", "poll"],
    *,
    outcome: Literal["acknowledged", "failed"],
    http_status_code: int | None,
    production_uuid: str | None,
    provider_status_code: int | None = None,
    submitted_settings_hash: str | None = None,
    source_checksum: str | None = None,
    output_contract_hash: str | None = None,
    provider_created_at: datetime | None = None,
    provider_completed_at: datetime | None = None,
) -> ProviderObservationV1:
    return build_provider_observation(
        operation=operation,
        outcome=outcome,
        http_status_code=http_status_code,
        provider_status_code=provider_status_code,
        production_uuid=production_uuid,
        submitted_settings_hash=submitted_settings_hash,
        source_checksum=source_checksum,
        output_contract_hash=output_contract_hash,
        provider_created_at=provider_created_at,
        provider_completed_at=provider_completed_at,
    )


def _utc(value: datetime | None, *, label: str) -> datetime:
    if value is None or value.tzinfo is None or value.utcoffset() is None:
        raise AdapterIntegrityError(f"{label} is missing or not timezone-aware")
    converted = value.astimezone(timezone.utc)
    if converted.utcoffset() != timezone.utc.utcoffset(converted):  # pragma: no cover
        raise AdapterIntegrityError(f"{label} is not UTC-normalizable")
    return converted


def _portable_audio_artifact(
    path: Path,
    digest: NormalizationContentDigestV1,
) -> ArtifactDigest:
    if path.stat().st_size != digest.size_bytes or hash_file(path) != digest.sha256:
        raise AdapterIntegrityError("audio CAS bytes changed before receipt")
    return ArtifactDigest(
        uri=f"normalization-audio://sha256/{digest.sha256}",
        sha256=digest.sha256,
        size_bytes=digest.size_bytes,
    )


def _safe_uuid(value: str, *, label: str = "production UUID") -> str:
    if _PRODUCTION_RE.fullmatch(value) is None:
        raise AdapterIntegrityError(f"{label} is malformed")
    return value


def _settings_parameters(
    settings: tuple[NormalizationSettingV1, ...],
) -> tuple[NormalizationParameter, ...]:
    parameters: list[NormalizationParameter] = []
    for item in settings:
        if item.scope not in {"algorithm", "output"}:
            raise AdapterInputError("provider effective settings cannot contain alignment scope")
        parameters.append(
            NormalizationParameter(scope=item.scope, name=item.name, value=item.value)
        )
    return tuple(parameters)


def _artifact_map(stored: StoredNormalizationRunV1) -> dict[str, NormalizationContentDigestV1]:
    result: dict[str, NormalizationContentDigestV1] = {}
    for event in stored.events:
        for item in event.artifacts:
            if item.name in result:
                raise NormalizationRunIntegrityError("normalization artifact kind was rebound")
            result[item.name] = item.digest
    return result


class NormalizationExecutor:
    """Drive one exact run until complete, interrupted, or terminally rejected."""

    def __init__(
        self,
        *,
        generation_store: GenerationStore,
        repository: NormalizationRunRepository,
        provider: AuphonicStepwiseProvider,
        media_inspector: MediaInspector,
        aligner: AudioAligner,
        effective_settings: tuple[NormalizationSettingV1, ...],
        preset: str | None,
        output_contract: NormalizationOutputContractV1,
        alignment_policy: NormalizationAlignmentPolicyV1,
        normalizer_identity: NormalizationAdapterIdentityV1,
        provider_protocol_version: str = "auphonic-rest-v1",
        reconciliation_page_size: int = 100,
        maximum_polls_per_call: int = 120,
    ) -> None:
        self._store = generation_store
        self._repository = repository
        self._provider = provider
        self._inspector = media_inspector
        self._aligner = aligner
        self._settings = tuple(effective_settings)
        self._preset = preset
        self._output_contract = output_contract
        self._alignment_policy = alignment_policy
        self._identity = normalizer_identity
        self._protocol = provider_protocol_version
        self._page_size = reconciliation_page_size
        self._maximum_polls = maximum_polls_per_call
        self._artifacts = NormalizationArtifactStore(repository.subtitle_root)
        if not 1 <= reconciliation_page_size <= 1_000:
            raise ValueError("reconciliation page size is outside contract")
        if not 1 <= maximum_polls_per_call <= 10_000:
            raise ValueError("maximum polls is outside contract")
        # Validate and sort before any provider method is reachable.
        if (
            tuple(sorted(self._settings, key=lambda item: (item.scope, item.name)))
            != self._settings
        ):
            raise ValueError("effective normalization settings must be sorted")
        _settings_parameters(self._settings)

    def normalize(self, request: NormalizeRequest) -> NormalizationResult:
        source = Path(request.source_audio)
        if not source.is_file() or _link_like(source):
            raise AdapterInputError("normalization source must be one regular non-link file")
        with self._store.snapshot_audio(
            source, expected_sha256=request.expected_source_hash
        ) as snapshot:
            stored_source = snapshot.commit()
        source_path = stored_source.path
        source_probe = self._inspector.probe(source_path)
        probe_digest = self._artifacts.put_bytes(canonical_json_bytes(source_probe))
        recovery_template = self._request_for(
            source_sha256=stored_source.sha256,
            source_size_bytes=stored_source.size_bytes,
            source_probe_hash=probe_digest.sha256,
            credential_ref="cred_" + "0" * 32,
        )
        recovered = self._repository.find_by_recovery_key(
            normalization_recovery_key(recovery_template)
        )
        if len(recovered) > 1:
            raise AdapterIntegrityError("multiple credential-bound normalization runs match input")
        if recovered and recovered[0].event.state == "complete":
            with self._artifacts.execution_lease(recovered[0].plan.request_key):
                return self._replay_complete(self._repository.load(recovered[0].plan.request_key))
        credentials = self._safe_credentials(source_probe.duration_ms)
        run_request, credential = self._select_run_request(
            source_sha256=stored_source.sha256,
            source_size_bytes=stored_source.size_bytes,
            source_probe_hash=probe_digest.sha256,
            credentials=credentials,
            recovered=recovered,
        )
        with self._artifacts.execution_lease(run_request.request_key):
            try:
                stored = self._repository.prepare(run_request)
            except NormalizationRunIntegrityError:
                # The only mutation recovery accepted here is publication of one
                # already-complete, authenticated local tail.  Any other damage
                # is re-raised by repair_published_tail without provider work.
                stored = self._repository.repair_published_tail(
                    run_request.request_key,
                    expected_request=run_request,
                )
            return self._drive(
                stored,
                credential=credential,
                source_path=source_path,
                source_probe=source_probe,
            )

    def _safe_credentials(self, duration_ms: int) -> tuple[AuphonicCredentialV1, ...]:
        try:
            values = tuple(self._provider.credentials(source_duration_ms=duration_ms))
        except AuphonicProviderFailure as exc:
            raise AdapterUnavailableError(str(exc)) from None
        except Exception:
            raise AdapterUnavailableError(
                "Auphonic credential discovery failed (redacted)"
            ) from None
        refs = [item.credential_ref for item in values]
        if len(refs) != len(set(refs)):
            raise AdapterIntegrityError("Auphonic credential discovery returned duplicate refs")
        return tuple(sorted(values, key=lambda item: item.credential_ref))

    @staticmethod
    def _require_2xx(value: object, *, operation: str) -> int:
        if type(value) is not int or not 200 <= value < 300:
            raise AdapterIntegrityError(
                f"Auphonic {operation} response lacks an acknowledged HTTP 2xx"
            )
        return value

    @staticmethod
    def _require_known_status(value: object, *, operation: str) -> int:
        if type(value) is not int or value not in {0, 1, 2, 3}:
            raise AdapterIntegrityError(f"Auphonic {operation} returned an unknown status")
        return value

    def _request_for(
        self,
        *,
        source_sha256: str,
        source_size_bytes: int,
        source_probe_hash: str,
        credential_ref: str,
    ) -> NormalizationRunRequestV1:
        return build_normalization_run_request(
            source_sha256=source_sha256,
            source_size_bytes=source_size_bytes,
            source_probe_hash=source_probe_hash,
            effective_settings=self._settings,
            preset=self._preset,
            output_contract=self._output_contract,
            alignment_policy=self._alignment_policy,
            normalizer_identity=self._identity,
            provider_protocol_version=self._protocol,
            credential_ref=credential_ref,
        )

    def _select_run_request(
        self,
        *,
        source_sha256: str,
        source_size_bytes: int,
        source_probe_hash: str,
        credentials: tuple[AuphonicCredentialV1, ...],
        recovered: tuple[StoredNormalizationRunV1, ...],
    ) -> tuple[NormalizationRunRequestV1, AuphonicCredentialV1]:
        if recovered:
            stored = recovered[0]
            matches = tuple(
                item
                for item in credentials
                if item.credential_ref == stored.plan.request.credential_ref
            )
            if len(matches) != 1:
                raise AdapterUnavailableError(
                    "existing normalization run credential is unavailable"
                )
            return stored.plan.request, matches[0]
        available = sorted(
            (item for item in credentials if item.available_for_new_run),
            key=lambda item: (item.selection_rank, item.credential_ref),
        )
        if not available:
            raise AdapterUnavailableError("no Auphonic credential is available for a new run")
        credential = available[0]
        return (
            self._request_for(
                source_sha256=source_sha256,
                source_size_bytes=source_size_bytes,
                source_probe_hash=source_probe_hash,
                credential_ref=credential.credential_ref,
            ),
            credential,
        )

    def _drive(
        self,
        stored: StoredNormalizationRunV1,
        *,
        credential: AuphonicCredentialV1,
        source_path: Path,
        source_probe: MediaProbeV1,
    ) -> NormalizationResult:
        polls = 0
        while True:
            state = stored.event.state
            if state == "complete":
                return self._replay_complete(stored)
            if not stored.permitted_actions:
                raise AdapterIntegrityError(f"normalization run is terminal: {state}")
            if state == "prepared":
                stored = self._create(stored, credential)
            elif state in {"create_in_flight", "ambiguous_create"}:
                stored = self._reconcile(stored, credential)
            elif state == "production_bound":
                stored = self._upload(stored, credential, source_path)
            elif state in {"upload_in_flight", "ambiguous_upload"}:
                stored = self._inspect_upload(stored, credential)
            elif state == "upload_acknowledged":
                stored = self._start(stored, credential)
            elif state in {"start_in_flight", "ambiguous_start", "processing"}:
                if polls >= self._maximum_polls:
                    raise AdapterUnavailableError("Auphonic processing remains pending")
                polls += 1
                stored = self._poll(stored, credential)
            elif state in {"provider_completed", "download_in_flight"}:
                stored = self._download(stored, credential)
            elif state in {"downloaded_verified", "alignment_in_flight"}:
                stored = self._align(stored, source_path)
            elif state == "alignment_verified":
                stored = self._receipt(stored, source_path, source_probe)
            elif state == "receipt_ready":
                proof_method = self._proof_binding_method(stored)
                proof = build_normalization_run_proof(stored, source_binding_method=proof_method)
                stored = self._repository.append(stored.plan, state="complete", proof=proof)
            else:  # pragma: no cover - exhaustive guard
                raise AdapterIntegrityError("unknown normalization execution state")

    def _provider_failure(
        self,
        stored: StoredNormalizationRunV1,
        exc: AuphonicProviderFailure,
        *,
        operation: Literal["create", "upload", "start", "poll"],
        ambiguous_state: Literal["ambiguous_create", "ambiguous_upload", "ambiguous_start"] | None,
        production_uuid: str | None,
    ) -> StoredNormalizationRunV1:
        if exc.definitive:
            return self._repository.append(
                stored.plan,
                state="provider_failed",
                observation=_observation(
                    operation,
                    outcome="failed",
                    http_status_code=exc.http_status_code,
                    production_uuid=production_uuid,
                ),
            )
        if ambiguous_state is None:
            raise AdapterUnavailableError(str(exc)) from None
        return self._repository.append(stored.plan, state=ambiguous_state)

    def _create(
        self, stored: StoredNormalizationRunV1, credential: AuphonicCredentialV1
    ) -> StoredNormalizationRunV1:
        stored = self._repository.append(stored.plan, state="create_in_flight")
        try:
            result = self._provider.create(
                credential,
                external_anchor=stored.plan.external_anchor,
                settings=stored.plan.request.effective_settings,
                preset=stored.plan.request.preset,
            )
        except AuphonicProviderFailure as exc:
            return self._provider_failure(
                stored,
                exc,
                operation="create",
                ambiguous_state="ambiguous_create",
                production_uuid=None,
            )
        except Exception:
            return self._repository.append(stored.plan, state="ambiguous_create")
        uuid = _safe_uuid(result.production_uuid)
        http_status = self._require_2xx(result.http_status_code, operation="create")
        created = _utc(result.provider_created_at, label="provider creation time")
        return self._repository.append(
            stored.plan,
            state="production_bound",
            observation=_observation(
                "create",
                outcome="acknowledged",
                http_status_code=http_status,
                production_uuid=uuid,
                provider_created_at=created,
            ),
        )

    def _reconcile(
        self, stored: StoredNormalizationRunV1, credential: AuphonicCredentialV1
    ) -> StoredNormalizationRunV1:
        if stored.event.state == "create_in_flight":
            stored = self._repository.append(stored.plan, state="ambiguous_create")
        pages: list[ExternalAnchorReconciliationPageV1] = []
        matches: list[AuphonicProductionProjectionV1] = []
        for index in range(10_000):
            offset = index * self._page_size
            try:
                page = self._provider.reconcile_page(
                    credential, offset=offset, limit=self._page_size
                )
            except AuphonicProviderFailure as exc:
                raise AdapterUnavailableError(str(exc)) from None
            except Exception:
                raise AdapterUnavailableError("Auphonic reconciliation failed (redacted)") from None
            if page.offset != offset or page.limit != self._page_size:
                raise AdapterIntegrityError("Auphonic reconciliation pagination mismatch")
            projection: list[dict[str, object]] = []
            for record in page.records:
                uuid = _safe_uuid(record.production_uuid)
                match = record.title == stored.plan.external_anchor
                projection.append({"production_uuid": uuid, "anchor_match": match})
                if match:
                    matches.append(record)
            pages.append(
                ExternalAnchorReconciliationPageV1(
                    offset=offset,
                    limit=self._page_size,
                    result_count=len(page.records),
                    projection_hash=hash_object(tuple(projection)),
                )
            )
            if len(page.records) < self._page_size:
                break
        else:
            raise AdapterIntegrityError("Auphonic reconciliation exceeded bounded pagination")
        reconciliation = build_external_anchor_reconciliation(
            external_anchor=stored.plan.external_anchor,
            pages=tuple(pages),
            matched_production_uuids=tuple(sorted(item.production_uuid for item in matches)),
        )
        if not matches:
            self._repository.append(
                stored.plan,
                state="ambiguous_create",
                reconciliation=reconciliation,
            )
            raise AdapterUnavailableError(
                "Auphonic create outcome remains ambiguous; no anchor match"
            )
        if len(matches) > 1:
            return self._repository.append(
                stored.plan,
                state="multi_record_conflict",
                reconciliation=reconciliation,
            )
        match = matches[0]
        created = _utc(match.provider_created_at, label="reconciled provider creation time")
        return self._repository.append(
            stored.plan,
            state="production_bound",
            observation=_observation(
                "reconcile",
                outcome="acknowledged",
                http_status_code=200,
                production_uuid=match.production_uuid,
                provider_created_at=created,
            ),
            reconciliation=reconciliation,
        )

    @staticmethod
    def _uuid(stored: StoredNormalizationRunV1) -> str:
        values = {
            event.observation.production_uuid
            for event in stored.events
            if event.observation is not None and event.observation.production_uuid is not None
        }
        if len(values) != 1:
            raise AdapterIntegrityError("normalization run has no unique production UUID")
        return next(iter(values))

    def _upload(
        self,
        stored: StoredNormalizationRunV1,
        credential: AuphonicCredentialV1,
        source_path: Path,
    ) -> StoredNormalizationRunV1:
        uuid = self._uuid(stored)
        source_path = self._store.audio_path(
            stored.plan.request.source.sha256,
            size_bytes=stored.plan.request.source.size_bytes,
        )
        stored = self._repository.append(stored.plan, state="upload_in_flight")
        try:
            result = self._provider.upload(
                credential, production_uuid=uuid, source_audio=source_path
            )
        except AuphonicProviderFailure as exc:
            return self._provider_failure(
                stored,
                exc,
                operation="upload",
                ambiguous_state="ambiguous_upload",
                production_uuid=uuid,
            )
        except Exception:
            return self._repository.append(stored.plan, state="ambiguous_upload")
        if _safe_uuid(result.production_uuid) != uuid:
            raise AdapterIntegrityError("Auphonic upload crossed production UUID")
        http_status = self._require_2xx(result.http_status_code, operation="upload")
        self._store.audio_path(
            stored.plan.request.source.sha256,
            size_bytes=stored.plan.request.source.size_bytes,
        )
        return self._repository.append(
            stored.plan,
            state="upload_acknowledged",
            observation=_observation(
                "upload",
                outcome="acknowledged",
                http_status_code=http_status,
                production_uuid=uuid,
            ),
        )

    def _inspect_upload(
        self,
        stored: StoredNormalizationRunV1,
        credential: AuphonicCredentialV1,
    ) -> StoredNormalizationRunV1:
        uuid = self._uuid(stored)
        if stored.event.state == "upload_in_flight":
            stored = self._repository.append(stored.plan, state="ambiguous_upload")
        try:
            result = self._provider.inspect_upload(credential, production_uuid=uuid)
        except AuphonicProviderFailure as exc:
            raise AdapterUnavailableError(str(exc)) from None
        except Exception:
            raise AdapterUnavailableError("Auphonic upload inspection failed (redacted)") from None
        if _safe_uuid(result.production_uuid) != uuid:
            raise AdapterIntegrityError("Auphonic upload inspection crossed production UUID")
        http_status = self._require_2xx(
            result.http_status_code,
            operation="upload inspection",
        )
        if result.source_checksum != stored.plan.request.source.sha256:
            return self._repository.append(
                stored.plan,
                state="source_binding_unproven",
                observation=_observation(
                    "inspect_upload",
                    outcome="acknowledged",
                    http_status_code=http_status,
                    production_uuid=uuid,
                    source_checksum=result.source_checksum,
                ),
            )
        self._store.audio_path(
            stored.plan.request.source.sha256,
            size_bytes=stored.plan.request.source.size_bytes,
        )
        return self._repository.append(
            stored.plan,
            state="upload_acknowledged",
            observation=_observation(
                "inspect_upload",
                outcome="acknowledged",
                http_status_code=http_status,
                production_uuid=uuid,
                source_checksum=result.source_checksum,
            ),
        )

    def _start(
        self, stored: StoredNormalizationRunV1, credential: AuphonicCredentialV1
    ) -> StoredNormalizationRunV1:
        uuid = self._uuid(stored)
        stored = self._repository.append(stored.plan, state="start_in_flight")
        try:
            result = self._provider.start(credential, production_uuid=uuid)
        except AuphonicProviderFailure as exc:
            return self._provider_failure(
                stored,
                exc,
                operation="start",
                ambiguous_state="ambiguous_start",
                production_uuid=uuid,
            )
        except Exception:
            return self._repository.append(stored.plan, state="ambiguous_start")
        if _safe_uuid(result.production_uuid) != uuid:
            raise AdapterIntegrityError("Auphonic start crossed production UUID")
        http_status = self._require_2xx(result.http_status_code, operation="start")
        provider_status = self._require_known_status(
            result.provider_status_code,
            operation="start",
        )
        if provider_status == 3:
            # A start response alone does not bind settings/output/time; poll once.
            return self._repository.append(
                stored.plan,
                state="processing",
                observation=_observation(
                    "start",
                    outcome="acknowledged",
                    http_status_code=http_status,
                    production_uuid=uuid,
                    provider_status_code=3,
                ),
            )
        if provider_status == 2:
            return self._repository.append(
                stored.plan,
                state="provider_failed",
                observation=_observation(
                    "start",
                    outcome="failed",
                    http_status_code=http_status,
                    production_uuid=uuid,
                    provider_status_code=2,
                ),
            )
        return self._repository.append(
            stored.plan,
            state="processing",
            observation=_observation(
                "start",
                outcome="acknowledged",
                http_status_code=http_status,
                production_uuid=uuid,
                provider_status_code=provider_status,
            ),
        )

    def _poll(
        self, stored: StoredNormalizationRunV1, credential: AuphonicCredentialV1
    ) -> StoredNormalizationRunV1:
        uuid = self._uuid(stored)
        if stored.event.state == "start_in_flight":
            stored = self._repository.append(stored.plan, state="ambiguous_start")
        try:
            result = self._provider.poll(credential, production_uuid=uuid)
        except AuphonicProviderFailure as exc:
            raise AdapterUnavailableError(str(exc)) from None
        except Exception:
            raise AdapterUnavailableError("Auphonic poll failed (redacted)") from None
        if _safe_uuid(result.production_uuid) != uuid:
            raise AdapterIntegrityError("Auphonic poll crossed production UUID")
        http_status = self._require_2xx(result.http_status_code, operation="poll")
        provider_status = self._require_known_status(
            result.provider_status_code,
            operation="poll",
        )
        if provider_status == 2:
            return self._repository.append(
                stored.plan,
                state="provider_failed",
                observation=_observation(
                    "poll",
                    outcome="failed",
                    http_status_code=http_status,
                    production_uuid=uuid,
                    provider_status_code=2,
                ),
            )
        if provider_status != 3:
            return self._repository.append(
                stored.plan,
                state="processing",
                observation=_observation(
                    "poll",
                    outcome="acknowledged",
                    http_status_code=http_status,
                    production_uuid=uuid,
                    provider_status_code=provider_status,
                ),
            )
        valid = (
            result.submitted_settings_hash == stored.plan.request.settings_hash
            and result.output_contract_hash == stored.plan.request.output_contract.content_hash
            and result.source_checksum in {None, stored.plan.request.source.sha256}
        )
        if not valid:
            return self._repository.append(
                stored.plan,
                state="contract_rejected",
                observation=_observation(
                    "poll",
                    outcome="acknowledged",
                    http_status_code=http_status,
                    production_uuid=uuid,
                    provider_status_code=3,
                    submitted_settings_hash=result.submitted_settings_hash,
                    source_checksum=result.source_checksum,
                    output_contract_hash=result.output_contract_hash,
                ),
            )
        return self._repository.append(
            stored.plan,
            state="provider_completed",
            observation=_observation(
                "poll",
                outcome="acknowledged",
                http_status_code=http_status,
                production_uuid=uuid,
                provider_status_code=3,
                submitted_settings_hash=result.submitted_settings_hash,
                source_checksum=result.source_checksum,
                output_contract_hash=result.output_contract_hash,
                provider_created_at=_utc(
                    result.provider_created_at, label="provider creation time"
                ),
                provider_completed_at=_utc(
                    result.provider_completed_at, label="provider completion time"
                ),
            ),
        )

    def _validate_output_probe(
        self, probe: MediaProbeV1, source_probe: MediaProbeV1 | None = None
    ) -> None:
        contract = self._output_contract
        if (
            probe.container != contract.container
            or probe.codec != contract.codec
            or probe.bit_depth != contract.bit_depth
            or probe.audio_stream_count != contract.audio_stream_count
        ):
            raise AdapterIntegrityError("downloaded media differs from output contract")
        if source_probe is not None:
            expected_channels = {
                "preserve": source_probe.channels,
                "mono": 1,
                "stereo": 2,
            }[contract.channel_policy]
            if probe.channels != expected_channels:
                raise AdapterIntegrityError("downloaded media channel policy mismatch")
            if (
                contract.sample_rate_policy == "preserve"
                and probe.sample_rate_hz != source_probe.sample_rate_hz
            ):
                raise AdapterIntegrityError("downloaded media sample-rate policy mismatch")
            if (
                contract.sample_rate_policy == "fixed"
                and probe.sample_rate_hz != contract.fixed_sample_rate_hz
            ):
                raise AdapterIntegrityError("downloaded media fixed sample rate mismatch")

    def _source_probe(self, stored: StoredNormalizationRunV1) -> MediaProbeV1:
        payload = self._artifacts.read_hash(stored.plan.request.source_probe_hash)
        probe = MediaProbeV1.model_validate_json(payload)
        if canonical_json_bytes(probe) != payload:
            raise NormalizationRunIntegrityError("source media probe is not canonical")
        return probe

    def _download(
        self, stored: StoredNormalizationRunV1, credential: AuphonicCredentialV1
    ) -> StoredNormalizationRunV1:
        uuid = self._uuid(stored)
        if stored.event.state == "provider_completed":
            stored = self._repository.append(stored.plan, state="download_in_flight")
        temporary = self._artifacts.download_path(stored.plan.request_key)
        try:
            self._provider.download(
                credential,
                production_uuid=uuid,
                destination=temporary,
            )
        except AuphonicProviderFailure as exc:
            raise AdapterUnavailableError(str(exc)) from None
        except Exception:
            raise AdapterUnavailableError("Auphonic download failed (redacted)") from None
        try:
            if _link_like(temporary) or not temporary.is_file() or temporary.stat().st_size <= 0:
                raise AdapterIntegrityError("Auphonic download is empty, missing, or linked")
            raw_probe = self._inspector.probe(temporary)
            self._validate_output_probe(raw_probe, self._source_probe(stored))
            with self._store.snapshot_audio(temporary) as snapshot:
                raw = snapshot.commit()
            committed_raw_probe = self._inspector.probe(raw.path)
            if committed_raw_probe != raw_probe:
                raise AdapterIntegrityError(
                    "downloaded media changed between probe and CAS promotion"
                )
            self._validate_output_probe(committed_raw_probe, self._source_probe(stored))
            raw_probe_digest = self._artifacts.put_bytes(canonical_json_bytes(raw_probe))
            return self._repository.append(
                stored.plan,
                state="downloaded_verified",
                artifacts=(
                    _binding(
                        "raw_audio",
                        NormalizationContentDigestV1(sha256=raw.sha256, size_bytes=raw.size_bytes),
                    ),
                    _binding("raw_probe", raw_probe_digest),
                ),
            )
        except Exception as exc:
            self._repository.append(stored.plan, state="output_rejected")
            if isinstance(exc, (AdapterIntegrityError, NormalizationRunIntegrityError)):
                raise
            raise AdapterIntegrityError("downloaded media verification failed (redacted)") from None
        finally:
            if temporary.exists() and not _link_like(temporary):
                temporary.unlink()

    def _align(
        self, stored: StoredNormalizationRunV1, source_path: Path
    ) -> StoredNormalizationRunV1:
        if stored.event.state == "downloaded_verified":
            stored = self._repository.append(stored.plan, state="alignment_in_flight")
        artifacts = _artifact_map(stored)
        raw_digest = artifacts["raw_audio"]
        raw_path = self._store.audio_path(raw_digest.sha256, size_bytes=raw_digest.size_bytes)
        try:
            result = self._aligner.align(
                raw_path,
                source_path,
                policy=stored.plan.request.alignment_policy,
            )
            if (
                not result.verified
                or result.method not in {"cross_correlation", "identity"}
                or abs(result.drift_ms) > stored.plan.request.alignment_policy.maximum_drift_ms
                or (
                    result.method == "cross_correlation"
                    and (
                        result.head_correlation is None
                        or result.mid_correlation is None
                        or result.head_correlation
                        < stored.plan.request.alignment_policy.minimum_head_correlation
                        or result.mid_correlation
                        < stored.plan.request.alignment_policy.minimum_mid_correlation
                    )
                )
            ):
                self._repository.append(stored.plan, state="alignment_rejected")
                raise AdapterIntegrityError("normalization clock alignment was not verified")
            aligned_path = Path(result.output_path)
            if _link_like(aligned_path) or not aligned_path.is_file():
                raise AdapterIntegrityError("aligned output is missing or linked")
            aligned_probe = self._inspector.probe(aligned_path)
            self._validate_output_probe(aligned_probe, self._source_probe(stored))
            with self._store.snapshot_audio(aligned_path) as snapshot:
                aligned = snapshot.commit()
            committed_aligned_probe = self._inspector.probe(aligned.path)
            if committed_aligned_probe != aligned_probe:
                raise AdapterIntegrityError("aligned media changed between probe and CAS promotion")
            self._validate_output_probe(committed_aligned_probe, self._source_probe(stored))
            clock_map = AudioClockMap(
                source_origin_ms=result.source_origin_ms,
                normalized_origin_ms=result.normalized_origin_ms,
                verified=True,
                drift_ms=result.drift_ms,
            )
            alignment_evidence = AlignmentEvidenceV1(
                method=result.method,
                clock_map=clock_map,
                head_correlation=result.head_correlation,
                mid_correlation=result.mid_correlation,
            )
            aligned_probe_digest = self._artifacts.put_bytes(canonical_json_bytes(aligned_probe))
            clock_digest = self._artifacts.put_bytes(canonical_json_bytes(alignment_evidence))
            return self._repository.append(
                stored.plan,
                state="alignment_verified",
                artifacts=(
                    _binding(
                        "aligned_audio",
                        NormalizationContentDigestV1(
                            sha256=aligned.sha256, size_bytes=aligned.size_bytes
                        ),
                    ),
                    _binding("aligned_probe", aligned_probe_digest),
                    _binding("clock_map", clock_digest),
                ),
            )
        except AdapterIntegrityError:
            if self._repository.load(stored.plan.request_key).event.state == "alignment_in_flight":
                self._repository.append(stored.plan, state="alignment_rejected")
            raise
        except Exception:
            self._repository.append(stored.plan, state="alignment_rejected")
            raise AdapterIntegrityError("normalization alignment failed (redacted)") from None

    def _receipt(
        self,
        stored: StoredNormalizationRunV1,
        source_path: Path,
        source_probe: MediaProbeV1,
    ) -> StoredNormalizationRunV1:
        artifacts = _artifact_map(stored)
        aligned_digest = artifacts["aligned_audio"]
        aligned_path = self._store.audio_path(
            aligned_digest.sha256, size_bytes=aligned_digest.size_bytes
        )
        aligned_probe_payload = self._artifacts.read(artifacts["aligned_probe"])
        aligned_probe = MediaProbeV1.model_validate_json(aligned_probe_payload)
        clock_payload = self._artifacts.read(artifacts["clock_map"])
        alignment_evidence = AlignmentEvidenceV1.model_validate_json(clock_payload)
        clock_map = alignment_evidence.clock_map
        observations = tuple(
            event.observation for event in stored.events if event.observation is not None
        )
        created_times = tuple(
            item.provider_created_at
            for item in observations
            if item.provider_created_at is not None
        )
        completed_times = tuple(
            item.provider_completed_at
            for item in observations
            if item.provider_completed_at is not None
        )
        if not created_times or len(set(created_times)) != 1 or len(completed_times) != 1:
            raise AdapterIntegrityError("normalization receipt lacks unique durable time evidence")
        parameters = _settings_parameters(stored.plan.request.effective_settings)
        source_artifact = _portable_audio_artifact(source_path, stored.plan.request.source)
        metrics: list[NormalizationMetric] = [
            NormalizationMetric(name="drift", value=float(clock_map.drift_ms), unit="milliseconds")
        ]
        for name, value in (
            ("head_correlation", alignment_evidence.head_correlation),
            ("mid_correlation", alignment_evidence.mid_correlation),
        ):
            if value is not None:
                metrics.append(NormalizationMetric(name=name, value=value, unit="ratio"))
        receipt = NormalizationReceipt(
            status="accepted",
            provider="auphonic",
            production_id=self._uuid(stored),
            production_source="created",
            source_identity_verified=True,
            source_binding_method=(
                "provider_checksum"
                if self._proof_binding_method(stored) == "provider_checksum"
                else "upload_in_current_request"
            ),
            provider_outcome="completed",
            provider_status_code=3,
            provider_status="completed",
            source=source_artifact,
            normalized=_portable_audio_artifact(aligned_path, aligned_digest),
            source_duration_ms=source_probe.duration_ms,
            normalized_duration_ms=aligned_probe.duration_ms,
            request_started_at=stored.plan.request_started_at,
            completed_at=completed_times[0],
            provider_created_at=created_times[0],
            provider_completed_at=completed_times[0],
            requested_parameters=parameters,
            submitted_parameters=parameters,
            preset=stored.plan.request.preset,
            settings_hash=stored.plan.request.settings_hash,
            clock_map=clock_map,
            alignment_method=alignment_evidence.method,
            alignment_metrics=tuple(metrics),
        )
        receipt_bytes = canonical_json_bytes(receipt)
        receipt_digest = self._artifacts.put_bytes(receipt_bytes)
        return self._repository.append(
            stored.plan,
            state="receipt_ready",
            artifacts=(_binding("normalization_receipt", receipt_digest),),
        )

    @staticmethod
    def _proof_binding_method(
        stored: StoredNormalizationRunV1,
    ) -> Literal["upload_acknowledged", "provider_checksum"]:
        if any(
            event.observation is not None
            and event.observation.operation == "inspect_upload"
            and event.observation.source_checksum == stored.plan.request.source.sha256
            for event in stored.events
        ):
            return "provider_checksum"
        return "upload_acknowledged"

    def _replay_complete(self, stored: StoredNormalizationRunV1) -> NormalizationResult:
        if stored.proof is None:
            raise NormalizationRunIntegrityError("complete normalization lacks proof")
        artifacts = _artifact_map(stored)
        receipt_payload = self._artifacts.read(artifacts["normalization_receipt"])
        receipt = NormalizationReceipt.model_validate_json(receipt_payload)
        if canonical_json_bytes(receipt) != receipt_payload:
            raise NormalizationRunIntegrityError("normalization receipt is not canonical")
        aligned = stored.proof.aligned_audio
        path = self._store.audio_path(aligned.sha256, size_bytes=aligned.size_bytes)
        if (
            receipt.normalized.sha256 != aligned.sha256
            or receipt.normalized.size_bytes != aligned.size_bytes
        ):
            raise NormalizationRunIntegrityError("normalization receipt crossed accepted audio")
        if receipt.settings_hash != stored.plan.request.settings_hash:
            raise NormalizationRunIntegrityError("normalization receipt crossed settings")
        return NormalizationResult(normalized_audio=path, receipt=receipt)


__all__ = [
    "AlignmentEvidenceV1",
    "AlignmentResultV1",
    "AuphonicCreateResultV1",
    "AuphonicCredentialV1",
    "AuphonicMutationResultV1",
    "AuphonicPollResultV1",
    "AuphonicProductionPageV1",
    "AuphonicProductionProjectionV1",
    "AuphonicProviderFailure",
    "AuphonicStepwiseProvider",
    "AuphonicUploadInspectionV1",
    "AudioAligner",
    "MediaInspector",
    "MediaProbeV1",
    "NormalizationArtifactStore",
    "NormalizationExecutor",
]
