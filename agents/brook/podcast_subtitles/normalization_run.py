"""Durable, provider-free recovery state for Auphonic normalization.

This module deliberately has no HTTP client and cannot create, upload, start,
poll, download, cancel, or delete an Auphonic production.  It records durable
intent before an external caller performs a side effect, and records an
acknowledgement only after that acknowledgement has itself been published.
An in-flight intent loaded by a fresh process therefore means ``unknown`` --
never permission to repeat the call.
"""

from __future__ import annotations

import math
import re
import secrets
import shutil
import tempfile
import threading
from collections.abc import Callable
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Literal, TypeAlias, TypeVar

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from .errors import IntegrityError
from .hashing import canonical_json_bytes, hash_object, sha256_bytes
from .store import _replace_fsynced, _write_fsynced

_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
_REQUEST_KEY_RE = _SHA256_RE
_PLAN_ID_RE = re.compile(r"^normalization-plan-[0-9a-f]{64}$")
_EVENT_ID_RE = re.compile(r"^normalization-event-[0-9a-f]{64}$")
_HEAD_ID_RE = re.compile(r"^normalization-head-[0-9a-f]{64}$")
_PROOF_ID_RE = re.compile(r"^normalization-proof-[0-9a-f]{64}$")
_OBSERVATION_ID_RE = re.compile(r"^provider-observation-[0-9a-f]{64}$")
_ANCHOR_RE = re.compile(r"^norm_[0-9a-f]{32}$")
_CREDENTIAL_REF_RE = re.compile(r"^cred_[0-9a-f]{32}$")
_PRODUCTION_UUID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9-]{7,127}$")
_SAFE_NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")
_TRANSITION_NAME_RE = re.compile(r"^[0-9]{6}$")
_TEMP_RUN_RE = re.compile(r"^\.run-[A-Za-z0-9_.-]+$")
_TEMP_TRANSITION_RE = re.compile(r"^\.transition-[A-Za-z0-9_.-]+$")
_TEMP_FILE_RE = re.compile(r"^\.[A-Za-z0-9_.-]+\.tmp$")
_MAX_TRANSITIONS = 1_000_000


ParameterScalar: TypeAlias = str | int | float | bool | None
NormalizationStage: TypeAlias = Literal[
    "prepared",
    "create_in_flight",
    "ambiguous_create",
    "production_bound",
    "upload_in_flight",
    "ambiguous_upload",
    "upload_acknowledged",
    "start_in_flight",
    "ambiguous_start",
    "processing",
    "provider_completed",
    "download_in_flight",
    "downloaded_verified",
    "alignment_in_flight",
    "alignment_verified",
    "receipt_ready",
    "complete",
    "multi_record_conflict",
    "provider_failed",
    "source_binding_unproven",
    "output_rejected",
    "alignment_rejected",
    "contract_rejected",
]
EffectCertainty: TypeAlias = Literal[
    "not_called",
    "intent_durable",
    "acknowledged",
    "unknown",
    "local_verified",
]
NormalizationOperation: TypeAlias = Literal[
    "none",
    "create",
    "reconcile",
    "upload",
    "inspect_upload",
    "start",
    "poll",
    "download",
    "align",
    "receipt",
    "complete",
]
NormalizationResumeAction: TypeAlias = Literal[
    "create_production_once",
    "reconcile_external_anchor",
    "upload_bound_production_once",
    "inspect_bound_production",
    "start_bound_production_once",
    "poll_bound_production",
    "download_bound_output_to_temp_and_verify",
    "verify_alignment_locally",
    "build_receipt_locally",
    "complete_locally",
]


class NormalizationRunIntegrityError(IntegrityError):
    """A normalization run cannot be replayed from its exact local records."""


class NormalizationRunConflictError(NormalizationRunIntegrityError):
    """An immutable identity or terminal normalization run was reused."""


class NormalizationRunNotFoundError(NormalizationRunIntegrityError, FileNotFoundError):
    """No durable normalization run is bound to the requested identity."""


class _NormalizationContract(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)


def _required_sha256(value: str, *, label: str) -> str:
    if not _SHA256_RE.fullmatch(value):
        raise ValueError(f"{label} must be lowercase SHA-256")
    return value


def _required_safe_name(value: str, *, label: str) -> str:
    if not _SAFE_NAME_RE.fullmatch(value):
        raise ValueError(f"{label} must be an opaque, path-free identifier")
    return value


def _addressed_payload(value: BaseModel, *, identity_field: str) -> dict[str, Any]:
    return value.model_dump(mode="json", exclude={identity_field}, exclude_none=False)


def _content_digest(payload: bytes) -> "NormalizationContentDigestV1":
    return NormalizationContentDigestV1(sha256=sha256_bytes(payload), size_bytes=len(payload))


class NormalizationContentDigestV1(_NormalizationContract):
    """URI-free identity for bytes held by another content-addressed store."""

    schema_version: Literal[1] = 1
    sha256: str
    size_bytes: int = Field(ge=0)

    @field_validator("sha256")
    @classmethod
    def _digest_is_sha256(cls, value: str) -> str:
        return _required_sha256(value, label="normalization content digest")


class NormalizationSettingV1(_NormalizationContract):
    """One exact, non-secret scalar effective normalization setting."""

    schema_version: Literal[1] = 1
    scope: Literal["algorithm", "output", "alignment"]
    name: str
    value: ParameterScalar

    @field_validator("name")
    @classmethod
    def _safe_setting_name(cls, value: str) -> str:
        lowered = value.casefold()
        if any(fragment in lowered for fragment in ("key", "secret", "token", "password")):
            raise ValueError("normalization setting name cannot describe a credential")
        return _required_safe_name(value, label="normalization setting name")

    @field_validator("value")
    @classmethod
    def _safe_setting_value(cls, value: ParameterScalar) -> ParameterScalar:
        if isinstance(value, float) and not math.isfinite(value):
            raise ValueError("normalization setting float must be finite")
        if isinstance(value, str) and (
            not value
            or value != value.strip()
            or len(value) > 256
            or any(marker in value for marker in ("\r", "\n", "://", "@", "\\"))
        ):
            raise ValueError(
                "normalization setting text must be trimmed, non-sensitive, "
                "and at most 256 characters"
            )
        return value


class NormalizationOutputContractV1(_NormalizationContract):
    """Exact media shape the future provider adapter must verify."""

    schema_version: Literal[1] = 1
    container: str
    codec: str
    bit_depth: int = Field(gt=0, le=64)
    audio_stream_count: int = Field(ge=1, le=8)
    channel_policy: Literal["preserve", "mono", "stereo"]
    sample_rate_policy: Literal["preserve", "provider_default", "fixed"]
    fixed_sample_rate_hz: int | None = Field(default=None, ge=8_000, le=384_000)

    @field_validator("container", "codec")
    @classmethod
    def _media_name_is_safe(cls, value: str, info: Any) -> str:
        return _required_safe_name(value, label=f"output {info.field_name}")

    @model_validator(mode="after")
    def _fixed_rate_is_closed(self) -> "NormalizationOutputContractV1":
        if (self.sample_rate_policy == "fixed") != (self.fixed_sample_rate_hz is not None):
            raise ValueError("fixed sample-rate policy must exactly bind a sample rate")
        return self

    @property
    def content_hash(self) -> str:
        return hash_object(self)


class NormalizationAlignmentPolicyV1(_NormalizationContract):
    """Pinned deterministic alignment acceptance policy."""

    schema_version: Literal[1] = 1
    algorithm: Literal["cross_correlation_v1"]
    trim_jingle: bool
    requested_jingle_ms: int = Field(ge=0, le=120_000)
    minimum_head_correlation: float = Field(ge=0.0, le=1.0, allow_inf_nan=False)
    minimum_mid_correlation: float = Field(ge=0.0, le=1.0, allow_inf_nan=False)
    maximum_drift_ms: int = Field(ge=0, le=60_000)

    @model_validator(mode="after")
    def _accepted_alignment_is_not_a_fallback(self) -> "NormalizationAlignmentPolicyV1":
        if not self.trim_jingle:
            raise ValueError("V1 normalization recovery requires verified jingle alignment")
        return self

    @property
    def content_hash(self) -> str:
        return hash_object(self)


class NormalizationAdapterIdentityV1(_NormalizationContract):
    """Measured executable identity of the normalization implementation."""

    schema_version: Literal[1] = 1
    name: str
    version: str
    config_hash: str
    code_hash: str
    runtime_hash: str

    @field_validator("name", "version")
    @classmethod
    def _identity_text_is_safe(cls, value: str, info: Any) -> str:
        return _required_safe_name(value, label=f"normalizer {info.field_name}")

    @field_validator("config_hash", "code_hash", "runtime_hash")
    @classmethod
    def _identity_hash_is_sha256(cls, value: str, info: Any) -> str:
        return _required_sha256(value, label=f"normalizer {info.field_name}")

    @property
    def content_hash(self) -> str:
        return hash_object(self)


class NormalizationRunRequestV1(_NormalizationContract):
    """Deterministic same-request identity; deliberately excludes external anchor."""

    schema_version: Literal[1] = 1
    request_key: str
    source: NormalizationContentDigestV1
    source_probe_hash: str
    provider: Literal["auphonic"] = "auphonic"
    effective_settings: tuple[NormalizationSettingV1, ...]
    settings_hash: str
    preset: str | None = None
    output_contract: NormalizationOutputContractV1
    alignment_policy: NormalizationAlignmentPolicyV1
    normalizer_identity: NormalizationAdapterIdentityV1
    provider_protocol_version: str
    credential_ref: str

    @field_validator(
        "request_key",
        "source_probe_hash",
        "settings_hash",
    )
    @classmethod
    def _request_hash_is_sha256(cls, value: str, info: Any) -> str:
        return _required_sha256(value, label=f"normalization request {info.field_name}")

    @field_validator("preset")
    @classmethod
    def _preset_is_safe(cls, value: str | None) -> str | None:
        if value is not None:
            return _required_safe_name(value, label="normalization preset")
        return None

    @field_validator("provider_protocol_version")
    @classmethod
    def _protocol_is_safe(cls, value: str) -> str:
        return _required_safe_name(value, label="provider protocol version")

    @field_validator("credential_ref")
    @classmethod
    def _credential_is_opaque(cls, value: str) -> str:
        if not _CREDENTIAL_REF_RE.fullmatch(value):
            raise ValueError("credential_ref must be an opaque cred_<32-hex> identifier")
        return value

    @model_validator(mode="after")
    def _closed_request_identity(self) -> "NormalizationRunRequestV1":
        keys = [(setting.scope, setting.name) for setting in self.effective_settings]
        if len(set(keys)) != len(keys):
            raise ValueError("effective_settings contains duplicate scope/name pairs")
        if keys != sorted(keys):
            raise ValueError("effective_settings must be sorted by scope/name")
        if not self.effective_settings:
            raise ValueError("effective_settings must not be empty")
        expected_settings_hash = _normalization_settings_hash(
            self.effective_settings,
            preset=self.preset,
        )
        if self.settings_hash != expected_settings_hash:
            raise ValueError("settings_hash does not bind exact effective settings")
        expected_key = hash_object(_request_identity_payload(self))
        if self.request_key != expected_key:
            raise ValueError("request_key does not bind the exact normalization request")
        return self


def _request_identity_payload(request: NormalizationRunRequestV1) -> dict[str, Any]:
    return request.model_dump(mode="json", exclude={"request_key"}, exclude_none=False)


def normalization_recovery_key(request: NormalizationRunRequestV1) -> str:
    """Identify exact execution inputs while excluding credential pool ordering."""

    return hash_object(
        request.model_dump(
            mode="json",
            exclude={"request_key", "credential_ref"},
            exclude_none=False,
        )
    )


def _normalization_settings_hash(
    settings: tuple[NormalizationSettingV1, ...],
    *,
    preset: str | None,
) -> str:
    """Match the provider-neutral NormalizationReceipt settings identity."""

    return hash_object(
        {
            "parameters": [
                {"scope": item.scope, "name": item.name, "value": item.value} for item in settings
            ],
            "preset": preset,
        }
    )


def build_normalization_run_request(
    *,
    source_sha256: str,
    source_size_bytes: int,
    source_probe_hash: str,
    effective_settings: tuple[NormalizationSettingV1, ...],
    preset: str | None,
    output_contract: NormalizationOutputContractV1,
    alignment_policy: NormalizationAlignmentPolicyV1,
    normalizer_identity: NormalizationAdapterIdentityV1,
    provider_protocol_version: str,
    credential_ref: str,
) -> NormalizationRunRequestV1:
    settings_hash = _normalization_settings_hash(effective_settings, preset=preset)
    payload = {
        "schema_version": 1,
        "source": NormalizationContentDigestV1(
            sha256=source_sha256,
            size_bytes=source_size_bytes,
        ),
        "source_probe_hash": source_probe_hash,
        "provider": "auphonic",
        "effective_settings": effective_settings,
        "settings_hash": settings_hash,
        "preset": preset,
        "output_contract": output_contract,
        "alignment_policy": alignment_policy,
        "normalizer_identity": normalizer_identity,
        "provider_protocol_version": provider_protocol_version,
        "credential_ref": credential_ref,
    }
    request_key = hash_object(payload)
    return NormalizationRunRequestV1(request_key=request_key, **payload)


class NormalizationRunPlanV1(_NormalizationContract):
    """Immutable plan that adds one opaque external anchor to a request."""

    schema_version: Literal[1] = 1
    id: str
    request_key: str
    request: NormalizationRunRequestV1
    external_anchor: str
    request_started_at: datetime

    @field_validator("id")
    @classmethod
    def _plan_id_is_addressed(cls, value: str) -> str:
        if not _PLAN_ID_RE.fullmatch(value):
            raise ValueError("normalization plan ID is invalid")
        return value

    @field_validator("request_key")
    @classmethod
    def _plan_key_is_sha256(cls, value: str) -> str:
        return _required_sha256(value, label="normalization plan request_key")

    @field_validator("external_anchor")
    @classmethod
    def _anchor_is_opaque(cls, value: str) -> str:
        if not _ANCHOR_RE.fullmatch(value):
            raise ValueError("external_anchor must be opaque norm_<32-hex>")
        return value

    @field_validator("request_started_at")
    @classmethod
    def _request_start_is_utc(cls, value: datetime) -> datetime:
        if (
            value.tzinfo is None
            or value.utcoffset() is None
            or value.utcoffset() != timezone.utc.utcoffset(value)
        ):
            raise ValueError("normalization request_started_at must use UTC")
        return value

    @model_validator(mode="after")
    def _closed_plan_identity(self) -> "NormalizationRunPlanV1":
        if self.request_key != self.request.request_key:
            raise ValueError("normalization plan crossed its request key")
        expected = "normalization-plan-" + hash_object(
            _addressed_payload(self, identity_field="id")
        )
        if self.id != expected:
            raise ValueError("normalization plan ID does not address its exact content")
        return self


def build_normalization_run_plan(
    *,
    request: NormalizationRunRequestV1,
    external_anchor: str,
    request_started_at: datetime | None = None,
) -> NormalizationRunPlanV1:
    started_at = request_started_at or datetime.now(timezone.utc)
    payload = {
        "schema_version": 1,
        "request_key": request.request_key,
        "request": request,
        "external_anchor": external_anchor,
        "request_started_at": started_at,
    }
    addressed = {
        **payload,
        "request_started_at": started_at.isoformat().replace("+00:00", "Z"),
    }
    return NormalizationRunPlanV1(
        **payload,
        id="normalization-plan-" + hash_object(addressed),
    )


class ExternalAnchorReconciliationPageV1(_NormalizationContract):
    """Digest-only evidence for one Auphonic minimal-data query page."""

    schema_version: Literal[1] = 1
    offset: int = Field(ge=0, le=10_000_000)
    limit: int = Field(ge=1, le=1_000)
    result_count: int = Field(ge=0, le=1_000)
    projection_hash: str

    @field_validator("offset", "limit", "result_count", mode="before")
    @classmethod
    def _pagination_integer_is_exact(cls, value: object, info: Any) -> object:
        if type(value) is not int:
            raise ValueError(f"reconciliation {info.field_name} must be an exact integer")
        return value

    @field_validator("projection_hash")
    @classmethod
    def _projection_hash_is_sha256(cls, value: str) -> str:
        return _required_sha256(value, label="reconciliation page projection_hash")

    @model_validator(mode="after")
    def _result_fits_page(self) -> "ExternalAnchorReconciliationPageV1":
        if self.result_count > self.limit:
            raise ValueError("reconciliation page result_count exceeds its limit")
        return self


class ExternalAnchorReconciliationV1(_NormalizationContract):
    """Content-addressed proof of an exhaustive opaque-title reconciliation scan."""

    schema_version: Literal[1] = 1
    id: str
    query_contract: Literal["auphonic-productions-minimal-data-v1"]
    external_anchor_hash: str
    pages: tuple[ExternalAnchorReconciliationPageV1, ...] = Field(
        min_length=1,
        max_length=10_000,
    )
    scanned_record_count: int = Field(ge=0, le=10_000_000)
    matched_production_uuids: tuple[str, ...] = Field(max_length=128)

    @field_validator("scanned_record_count", mode="before")
    @classmethod
    def _record_count_is_exact(cls, value: object) -> object:
        if type(value) is not int:
            raise ValueError("reconciliation scanned_record_count must be an exact integer")
        return value

    @field_validator("id")
    @classmethod
    def _id_is_valid(cls, value: str) -> str:
        if not re.fullmatch(r"reconciliation-[0-9a-f]{64}", value):
            raise ValueError("external-anchor reconciliation ID is invalid")
        return value

    @field_validator("external_anchor_hash")
    @classmethod
    def _anchor_hash_is_sha256(cls, value: str) -> str:
        return _required_sha256(value, label="reconciliation external_anchor_hash")

    @field_validator("matched_production_uuids")
    @classmethod
    def _matches_are_safe(
        cls,
        value: tuple[str, ...],
    ) -> tuple[str, ...]:
        if value != tuple(sorted(value)) or len(value) != len(set(value)):
            raise ValueError("reconciliation matches must be sorted and unique")
        if any(not _PRODUCTION_UUID_RE.fullmatch(item) for item in value):
            raise ValueError("reconciliation match contains an invalid production UUID")
        return value

    @model_validator(mode="after")
    def _scan_is_exhaustive_and_addressed(self) -> "ExternalAnchorReconciliationV1":
        page_limit = self.pages[0].limit
        for index, page in enumerate(self.pages):
            if page.limit != page_limit or page.offset != index * page_limit:
                raise ValueError(
                    "reconciliation pages must be contiguous from offset zero with one limit"
                )
            if index < len(self.pages) - 1 and page.result_count != page_limit:
                raise ValueError("reconciliation non-final pages must be full")
        if self.pages[-1].result_count >= page_limit:
            raise ValueError("reconciliation final page must be short to prove exhaustion")
        if self.scanned_record_count != sum(page.result_count for page in self.pages):
            raise ValueError("reconciliation scanned_record_count mismatch")
        if len(self.matched_production_uuids) > self.scanned_record_count:
            raise ValueError("reconciliation matches exceed scanned records")
        expected = "reconciliation-" + hash_object(_addressed_payload(self, identity_field="id"))
        if self.id != expected:
            raise ValueError("external-anchor reconciliation ID does not address its content")
        return self


def build_external_anchor_reconciliation(
    *,
    external_anchor: str,
    pages: tuple[ExternalAnchorReconciliationPageV1, ...],
    matched_production_uuids: tuple[str, ...],
) -> ExternalAnchorReconciliationV1:
    """Build digest-only reconciliation evidence without retaining provider titles."""

    if not _ANCHOR_RE.fullmatch(external_anchor):
        raise ValueError("external_anchor must be opaque norm_<32-hex>")
    payload = {
        "schema_version": 1,
        "query_contract": "auphonic-productions-minimal-data-v1",
        "external_anchor_hash": sha256_bytes(external_anchor.encode("utf-8")),
        "pages": pages,
        "scanned_record_count": sum(page.result_count for page in pages),
        "matched_production_uuids": matched_production_uuids,
    }
    return ExternalAnchorReconciliationV1(
        **payload,
        id="reconciliation-" + hash_object(payload),
    )


class ProviderObservationV1(_NormalizationContract):
    """Strict allowlisted projection of a provider response, never its body."""

    schema_version: Literal[1] = 1
    id: str
    operation: Literal["create", "reconcile", "upload", "inspect_upload", "start", "poll"]
    outcome: Literal["acknowledged", "failed"]
    http_status_code: int | None = Field(default=None, ge=100, le=599)
    provider_status_code: int | None = None
    production_uuid: str | None = None
    submitted_settings_hash: str | None = None
    source_checksum: str | None = None
    output_contract_hash: str | None = None
    provider_created_at: datetime | None = None
    provider_completed_at: datetime | None = None
    content_hash: str

    @field_validator("http_status_code", "provider_status_code", mode="before")
    @classmethod
    def _status_is_exact_integer(cls, value: object, info: Any) -> object:
        if value is not None and type(value) is not int:
            raise ValueError(f"{info.field_name} must be an exact integer")
        return value

    @field_validator("id")
    @classmethod
    def _observation_id_is_valid(cls, value: str) -> str:
        if not _OBSERVATION_ID_RE.fullmatch(value):
            raise ValueError("provider observation ID is invalid")
        return value

    @field_validator("production_uuid")
    @classmethod
    def _production_uuid_is_opaque(cls, value: str | None) -> str | None:
        if value is not None and not _PRODUCTION_UUID_RE.fullmatch(value):
            raise ValueError("production UUID must be one opaque provider identifier")
        return value

    @field_validator(
        "submitted_settings_hash", "source_checksum", "output_contract_hash", "content_hash"
    )
    @classmethod
    def _observation_hash_is_sha256(cls, value: str | None, info: Any) -> str | None:
        if value is not None:
            return _required_sha256(value, label=f"provider observation {info.field_name}")
        return None

    @field_validator("provider_created_at", "provider_completed_at")
    @classmethod
    def _provider_time_is_utc(
        cls,
        value: datetime | None,
        info: Any,
    ) -> datetime | None:
        if value is not None and (
            value.tzinfo is None
            or value.utcoffset() is None
            or value.utcoffset() != timezone.utc.utcoffset(value)
        ):
            raise ValueError(f"provider observation {info.field_name} must use UTC")
        return value

    @model_validator(mode="after")
    def _closed_observation(self) -> "ProviderObservationV1":
        if self.outcome == "acknowledged":
            if self.http_status_code is None or not 200 <= self.http_status_code < 300:
                raise ValueError("acknowledged provider observation requires HTTP 2xx")
            if self.production_uuid is None:
                raise ValueError("acknowledged provider observation requires production UUID")
            if self.provider_status_code is not None and self.provider_status_code not in {
                0,
                1,
                2,
                3,
            }:
                raise ValueError("provider observation contains an unknown Auphonic status")
        if self.operation != "poll" and self.submitted_settings_hash is not None:
            raise ValueError("only poll observation may record submitted settings")
        if self.operation not in {"inspect_upload", "poll"} and self.source_checksum is not None:
            raise ValueError("only upload inspection or poll may record a source checksum")
        if self.operation != "poll" and self.output_contract_hash is not None:
            raise ValueError("only poll observation may record an output contract hash")
        if self.operation not in {"create", "reconcile", "poll"} and self.provider_created_at:
            raise ValueError("provider creation time crossed its observation operation")
        if self.operation != "poll" and self.provider_completed_at:
            raise ValueError("only poll observation may record provider completion time")
        if self.provider_completed_at is not None and self.provider_created_at is not None:
            if self.provider_completed_at < self.provider_created_at:
                raise ValueError("provider completion time precedes provider creation time")
        expected_hash = hash_object(
            self.model_dump(mode="json", exclude={"id", "content_hash"}, exclude_none=False)
        )
        if self.content_hash != expected_hash:
            raise ValueError("provider observation content_hash mismatch")
        if self.id != "provider-observation-" + expected_hash:
            raise ValueError("provider observation ID does not address its content")
        return self


def build_provider_observation(
    *,
    operation: Literal[
        "create",
        "reconcile",
        "upload",
        "inspect_upload",
        "start",
        "poll",
    ],
    outcome: Literal["acknowledged", "failed"],
    http_status_code: int | None,
    provider_status_code: int | None,
    production_uuid: str | None,
    submitted_settings_hash: str | None,
    source_checksum: str | None,
    output_contract_hash: str | None,
    provider_created_at: datetime | None = None,
    provider_completed_at: datetime | None = None,
) -> ProviderObservationV1:
    payload = {
        "schema_version": 1,
        "operation": operation,
        "outcome": outcome,
        "http_status_code": http_status_code,
        "provider_status_code": provider_status_code,
        "production_uuid": production_uuid,
        "submitted_settings_hash": submitted_settings_hash,
        "source_checksum": source_checksum,
        "output_contract_hash": output_contract_hash,
        "provider_created_at": provider_created_at,
        "provider_completed_at": provider_completed_at,
    }
    addressed = {
        **payload,
        "provider_created_at": (
            provider_created_at.isoformat().replace("+00:00", "Z")
            if provider_created_at is not None
            else None
        ),
        "provider_completed_at": (
            provider_completed_at.isoformat().replace("+00:00", "Z")
            if provider_completed_at is not None
            else None
        ),
    }
    content_hash = hash_object(addressed)
    return ProviderObservationV1(
        **payload,
        content_hash=content_hash,
        id="provider-observation-" + content_hash,
    )


class NormalizationArtifactBindingV1(_NormalizationContract):
    """One typed digest introduced by a verified local stage."""

    schema_version: Literal[1] = 1
    name: Literal[
        "raw_audio",
        "raw_probe",
        "aligned_audio",
        "aligned_probe",
        "clock_map",
        "normalization_receipt",
    ]
    digest: NormalizationContentDigestV1


class NormalizationRunProofV1(_NormalizationContract):
    """Content-addressed proof over the complete pre-publication chain."""

    schema_version: Literal[1] = 1
    id: str
    request_key: str
    plan_id: str
    precomplete_event_id: str
    precomplete_head_id: str
    precomplete_event_count: int = Field(ge=1, le=_MAX_TRANSITIONS)
    event_chain_hash: str
    provider_observation_set_hash: str
    production_uuid: str
    source_binding_method: Literal["upload_acknowledged", "provider_checksum"]
    submitted_settings_hash: str
    raw_audio: NormalizationContentDigestV1
    raw_probe: NormalizationContentDigestV1
    aligned_audio: NormalizationContentDigestV1
    aligned_probe: NormalizationContentDigestV1
    clock_map: NormalizationContentDigestV1
    receipt_hash: str

    @field_validator("id")
    @classmethod
    def _proof_id_is_valid(cls, value: str) -> str:
        if not _PROOF_ID_RE.fullmatch(value):
            raise ValueError("normalization proof ID is invalid")
        return value

    @field_validator("plan_id")
    @classmethod
    def _proof_plan_id_is_valid(cls, value: str) -> str:
        if not _PLAN_ID_RE.fullmatch(value):
            raise ValueError("normalization proof plan_id is invalid")
        return value

    @field_validator("precomplete_event_id")
    @classmethod
    def _proof_event_id_is_valid(cls, value: str) -> str:
        if not _EVENT_ID_RE.fullmatch(value):
            raise ValueError("normalization proof event ID is invalid")
        return value

    @field_validator("precomplete_head_id")
    @classmethod
    def _proof_head_id_is_valid(cls, value: str) -> str:
        if not _HEAD_ID_RE.fullmatch(value):
            raise ValueError("normalization proof head ID is invalid")
        return value

    @field_validator(
        "request_key",
        "event_chain_hash",
        "provider_observation_set_hash",
        "submitted_settings_hash",
        "receipt_hash",
    )
    @classmethod
    def _proof_hash_is_sha256(cls, value: str, info: Any) -> str:
        return _required_sha256(value, label=f"normalization proof {info.field_name}")

    @field_validator("production_uuid")
    @classmethod
    def _proof_uuid_is_opaque(cls, value: str) -> str:
        if not _PRODUCTION_UUID_RE.fullmatch(value):
            raise ValueError("normalization proof production UUID is invalid")
        return value

    @model_validator(mode="after")
    def _proof_is_addressed(self) -> "NormalizationRunProofV1":
        expected = "normalization-proof-" + hash_object(
            _addressed_payload(self, identity_field="id")
        )
        if self.id != expected:
            raise ValueError("normalization proof ID does not address its exact content")
        return self


class NormalizationRunEventV1(_NormalizationContract):
    """One immutable intent, acknowledgement, ambiguity, or local proof step."""

    schema_version: Literal[1] = 1
    id: str
    request_key: str
    plan_id: str
    sequence: int = Field(ge=0, lt=_MAX_TRANSITIONS)
    previous_event_id: str | None = None
    previous_head_id: str | None = None
    state: NormalizationStage
    operation: NormalizationOperation
    effect_certainty: EffectCertainty
    observation: ProviderObservationV1 | None = None
    reconciliation: ExternalAnchorReconciliationV1 | None = None
    artifacts: tuple[NormalizationArtifactBindingV1, ...] = ()
    proof: NormalizationContentDigestV1 | None = None

    @field_validator("id")
    @classmethod
    def _event_id_is_valid(cls, value: str) -> str:
        if not _EVENT_ID_RE.fullmatch(value):
            raise ValueError("normalization event ID is invalid")
        return value

    @field_validator("request_key")
    @classmethod
    def _event_key_is_sha256(cls, value: str) -> str:
        return _required_sha256(value, label="normalization event request_key")

    @field_validator("plan_id")
    @classmethod
    def _event_plan_id_is_valid(cls, value: str) -> str:
        if not _PLAN_ID_RE.fullmatch(value):
            raise ValueError("normalization event plan_id is invalid")
        return value

    @field_validator("previous_event_id")
    @classmethod
    def _previous_event_id_is_valid(cls, value: str | None) -> str | None:
        if value is not None and not _EVENT_ID_RE.fullmatch(value):
            raise ValueError("previous normalization event ID is invalid")
        return value

    @field_validator("previous_head_id")
    @classmethod
    def _previous_head_id_is_valid(cls, value: str | None) -> str | None:
        if value is not None and not _HEAD_ID_RE.fullmatch(value):
            raise ValueError("previous normalization head ID is invalid")
        return value

    @model_validator(mode="after")
    def _event_is_closed_and_addressed(self) -> "NormalizationRunEventV1":
        artifact_names = [artifact.name for artifact in self.artifacts]
        if artifact_names != sorted(artifact_names) or len(set(artifact_names)) != len(
            artifact_names
        ):
            raise ValueError("normalization event artifacts must be unique and sorted")
        expected = "normalization-event-" + hash_object(
            _addressed_payload(self, identity_field="id")
        )
        if self.id != expected:
            raise ValueError("normalization event ID does not address its exact content")
        return self


class NormalizationRunHeadV1(_NormalizationContract):
    """Immutable authenticated head for one exact event prefix."""

    schema_version: Literal[1] = 1
    id: str
    request_key: str
    plan_id: str
    sequence: int = Field(ge=0, lt=_MAX_TRANSITIONS)
    event_id: str
    event_record: NormalizationContentDigestV1
    previous_head_id: str | None = None

    @field_validator("id")
    @classmethod
    def _head_id_is_valid(cls, value: str) -> str:
        if not _HEAD_ID_RE.fullmatch(value):
            raise ValueError("normalization head ID is invalid")
        return value

    @field_validator("request_key")
    @classmethod
    def _head_key_is_sha256(cls, value: str) -> str:
        return _required_sha256(value, label="normalization head request_key")

    @field_validator("plan_id")
    @classmethod
    def _head_plan_id_is_valid(cls, value: str) -> str:
        if not _PLAN_ID_RE.fullmatch(value):
            raise ValueError("normalization head plan_id is invalid")
        return value

    @field_validator("event_id")
    @classmethod
    def _head_event_id_is_valid(cls, value: str) -> str:
        if not _EVENT_ID_RE.fullmatch(value):
            raise ValueError("normalization head event_id is invalid")
        return value

    @field_validator("previous_head_id")
    @classmethod
    def _head_previous_id_is_valid(cls, value: str | None) -> str | None:
        if value is not None and not _HEAD_ID_RE.fullmatch(value):
            raise ValueError("previous normalization head ID is invalid")
        return value

    @model_validator(mode="after")
    def _head_is_addressed(self) -> "NormalizationRunHeadV1":
        expected = "normalization-head-" + hash_object(
            _addressed_payload(self, identity_field="id")
        )
        if self.id != expected:
            raise ValueError("normalization head ID does not address its exact content")
        return self


class _NormalizationActivePointerV1(_NormalizationContract):
    schema_version: Literal[1] = 1
    request_key: str
    plan_id: str
    sequence: int = Field(ge=0, lt=_MAX_TRANSITIONS)
    head_id: str
    head_record: NormalizationContentDigestV1
    pointer_hash: str

    @field_validator("request_key", "pointer_hash")
    @classmethod
    def _pointer_hash_is_sha256(cls, value: str, info: Any) -> str:
        return _required_sha256(value, label=f"normalization pointer {info.field_name}")

    @field_validator("plan_id")
    @classmethod
    def _pointer_plan_id_is_valid(cls, value: str) -> str:
        if not _PLAN_ID_RE.fullmatch(value):
            raise ValueError("normalization pointer plan_id is invalid")
        return value

    @field_validator("head_id")
    @classmethod
    def _pointer_head_id_is_valid(cls, value: str) -> str:
        if not _HEAD_ID_RE.fullmatch(value):
            raise ValueError("normalization pointer head_id is invalid")
        return value

    @model_validator(mode="after")
    def _pointer_is_addressed(self) -> "_NormalizationActivePointerV1":
        expected = hash_object(_addressed_payload(self, identity_field="pointer_hash"))
        if self.pointer_hash != expected:
            raise ValueError("normalization active pointer hash mismatch")
        return self


class _NormalizationKeyBindingV1(_NormalizationContract):
    schema_version: Literal[1] = 1
    request_key: str
    plan_id: str
    plan_record: NormalizationContentDigestV1
    binding_hash: str

    @field_validator("request_key", "binding_hash")
    @classmethod
    def _key_binding_hash_is_sha256(cls, value: str, info: Any) -> str:
        return _required_sha256(value, label=f"normalization key {info.field_name}")

    @field_validator("plan_id")
    @classmethod
    def _key_plan_id_is_valid(cls, value: str) -> str:
        if not _PLAN_ID_RE.fullmatch(value):
            raise ValueError("normalization key plan_id is invalid")
        return value

    @model_validator(mode="after")
    def _binding_is_addressed(self) -> "_NormalizationKeyBindingV1":
        expected = hash_object(_addressed_payload(self, identity_field="binding_hash"))
        if self.binding_hash != expected:
            raise ValueError("normalization key binding hash mismatch")
        return self


@dataclass(frozen=True, slots=True)
class StoredNormalizationRunV1:
    """One fully replayed local run prefix."""

    plan: NormalizationRunPlanV1
    event: NormalizationRunEventV1
    head: NormalizationRunHeadV1
    events: tuple[NormalizationRunEventV1, ...]
    heads: tuple[NormalizationRunHeadV1, ...]
    proof: NormalizationRunProofV1 | None
    directory: Path

    @property
    def effect_outcome(self) -> EffectCertainty:
        if self.event.state in {
            "create_in_flight",
            "upload_in_flight",
            "start_in_flight",
        }:
            return "unknown"
        return self.event.effect_certainty

    @property
    def permitted_actions(self) -> tuple[NormalizationResumeAction, ...]:
        actions: dict[NormalizationStage, tuple[NormalizationResumeAction, ...]] = {
            "prepared": ("create_production_once",),
            "create_in_flight": ("reconcile_external_anchor",),
            "ambiguous_create": ("reconcile_external_anchor",),
            "production_bound": ("upload_bound_production_once",),
            "upload_in_flight": ("inspect_bound_production",),
            "ambiguous_upload": ("inspect_bound_production",),
            "upload_acknowledged": ("start_bound_production_once",),
            "start_in_flight": ("poll_bound_production",),
            "ambiguous_start": ("poll_bound_production",),
            "processing": ("poll_bound_production",),
            "provider_completed": ("download_bound_output_to_temp_and_verify",),
            "download_in_flight": ("download_bound_output_to_temp_and_verify",),
            "downloaded_verified": ("verify_alignment_locally",),
            "alignment_in_flight": ("verify_alignment_locally",),
            "alignment_verified": ("build_receipt_locally",),
            "receipt_ready": ("complete_locally",),
        }
        return actions.get(self.event.state, ())


_TERMINAL_STATES: frozenset[NormalizationStage] = frozenset(
    {
        "complete",
        "multi_record_conflict",
        "provider_failed",
        "source_binding_unproven",
        "output_rejected",
        "alignment_rejected",
        "contract_rejected",
    }
)

_ALLOWED_TRANSITIONS: dict[NormalizationStage, frozenset[NormalizationStage]] = {
    "prepared": frozenset({"create_in_flight"}),
    "create_in_flight": frozenset({"ambiguous_create", "production_bound", "provider_failed"}),
    "ambiguous_create": frozenset(
        {"ambiguous_create", "production_bound", "multi_record_conflict"}
    ),
    "production_bound": frozenset({"upload_in_flight"}),
    "upload_in_flight": frozenset(
        {
            "ambiguous_upload",
            "upload_acknowledged",
            "provider_failed",
            "source_binding_unproven",
        }
    ),
    "ambiguous_upload": frozenset(
        {"upload_acknowledged", "provider_failed", "source_binding_unproven"}
    ),
    "upload_acknowledged": frozenset({"start_in_flight"}),
    "start_in_flight": frozenset(
        {"ambiguous_start", "processing", "provider_completed", "provider_failed"}
    ),
    "processing": frozenset(
        {"processing", "provider_completed", "provider_failed", "contract_rejected"}
    ),
    "ambiguous_start": frozenset(
        {"processing", "provider_completed", "provider_failed", "contract_rejected"}
    ),
    "provider_completed": frozenset({"download_in_flight", "output_rejected"}),
    "download_in_flight": frozenset({"downloaded_verified", "output_rejected"}),
    "downloaded_verified": frozenset({"alignment_in_flight"}),
    "alignment_in_flight": frozenset({"alignment_verified", "alignment_rejected"}),
    "alignment_verified": frozenset({"receipt_ready"}),
    "receipt_ready": frozenset({"complete"}),
}


def _stage_metadata(
    state: NormalizationStage,
    observation: ProviderObservationV1 | None,
) -> tuple[NormalizationOperation, EffectCertainty]:
    if state == "source_binding_unproven" and observation is not None:
        return ("inspect_upload", "acknowledged")
    fixed: dict[NormalizationStage, tuple[NormalizationOperation, EffectCertainty]] = {
        "prepared": ("none", "not_called"),
        "create_in_flight": ("create", "intent_durable"),
        "ambiguous_create": ("create", "unknown"),
        "production_bound": ("create", "acknowledged"),
        "upload_in_flight": ("upload", "intent_durable"),
        "ambiguous_upload": ("upload", "unknown"),
        "upload_acknowledged": ("upload", "acknowledged"),
        "start_in_flight": ("start", "intent_durable"),
        "ambiguous_start": ("start", "unknown"),
        "processing": ("start", "acknowledged"),
        "provider_completed": ("poll", "acknowledged"),
        "download_in_flight": ("download", "intent_durable"),
        "downloaded_verified": ("download", "local_verified"),
        "alignment_in_flight": ("align", "intent_durable"),
        "alignment_verified": ("align", "local_verified"),
        "receipt_ready": ("receipt", "local_verified"),
        "complete": ("complete", "local_verified"),
        "multi_record_conflict": ("reconcile", "unknown"),
        "source_binding_unproven": ("reconcile", "unknown"),
        "output_rejected": ("download", "local_verified"),
        "alignment_rejected": ("align", "local_verified"),
        "contract_rejected": ("poll", "local_verified"),
    }
    if state == "provider_failed":
        return (
            observation.operation if observation is not None else "poll",
            "acknowledged",
        )
    return fixed[state]


def _required_artifact_names(state: NormalizationStage) -> frozenset[str]:
    return {
        "downloaded_verified": frozenset({"raw_audio", "raw_probe"}),
        "alignment_verified": frozenset({"aligned_audio", "aligned_probe", "clock_map"}),
        "receipt_ready": frozenset({"normalization_receipt"}),
    }.get(state, frozenset())


def _validate_event_semantics(event: NormalizationRunEventV1) -> None:
    expected_operation, expected_certainty = _stage_metadata(event.state, event.observation)
    if (event.operation, event.effect_certainty) != (
        expected_operation,
        expected_certainty,
    ):
        raise NormalizationRunIntegrityError(
            "normalization event operation/effect certainty crossed its state"
        )
    observation_required = event.state in {
        "production_bound",
        "upload_acknowledged",
        "processing",
        "provider_completed",
        "provider_failed",
        "contract_rejected",
    }
    observation_optional = event.state == "source_binding_unproven"
    if (
        observation_required
        and event.observation is None
        or not observation_required
        and not observation_optional
        and event.observation is not None
    ):
        raise NormalizationRunIntegrityError(
            "normalization event observation presence crossed its state"
        )
    if event.observation is not None:
        expected_observation_operations: dict[NormalizationStage, frozenset[str]] = {
            "production_bound": frozenset({"create", "reconcile"}),
            "upload_acknowledged": frozenset({"upload", "inspect_upload"}),
            "processing": frozenset({"start", "poll"}),
            "provider_completed": frozenset({"poll"}),
            "provider_failed": frozenset(
                {"create", "reconcile", "upload", "inspect_upload", "start", "poll"}
            ),
            "contract_rejected": frozenset({"poll"}),
            "source_binding_unproven": frozenset({"inspect_upload"}),
        }
        if event.observation.operation not in expected_observation_operations[event.state]:
            raise NormalizationRunIntegrityError(
                "normalization event provider observation has the wrong operation"
            )
        expected_outcome = "failed" if event.state == "provider_failed" else "acknowledged"
        if event.observation.outcome != expected_outcome:
            raise NormalizationRunIntegrityError(
                "normalization event provider outcome crossed its state"
            )
        if event.state == "provider_completed" and event.observation.provider_status_code != 3:
            raise NormalizationRunIntegrityError(
                "provider_completed requires exact Auphonic status integer 3"
            )
    if event.reconciliation is not None and event.state not in {
        "ambiguous_create",
        "production_bound",
        "multi_record_conflict",
    }:
        raise NormalizationRunIntegrityError(
            "external-anchor reconciliation crossed its normalization state"
        )
    names = frozenset(binding.name for binding in event.artifacts)
    if names != _required_artifact_names(event.state):
        raise NormalizationRunIntegrityError("normalization event artifact set crossed its state")
    if (event.state == "complete") != (event.proof is not None):
        raise NormalizationRunIntegrityError("only complete normalization event may bind a proof")


def _validate_transition_semantics(
    *,
    plan: NormalizationRunPlanV1,
    previous: NormalizationRunEventV1,
    event: NormalizationRunEventV1,
) -> None:
    """Validate evidence that depends on both sides of one FSM edge."""

    reconciliation = event.reconciliation
    expected_anchor_hash = sha256_bytes(plan.external_anchor.encode("utf-8"))
    if reconciliation is not None and reconciliation.external_anchor_hash != expected_anchor_hash:
        raise NormalizationRunIntegrityError(
            "external-anchor reconciliation crossed its immutable plan anchor"
        )

    if event.state == "ambiguous_create" and previous.state == "ambiguous_create":
        if reconciliation is None or reconciliation.matched_production_uuids:
            raise NormalizationRunIntegrityError(
                "ambiguous create no-match scan must retain exhaustive zero-match evidence"
            )
    elif event.state == "production_bound":
        assert event.observation is not None
        if previous.state == "ambiguous_create":
            if reconciliation is None:
                raise NormalizationRunIntegrityError(
                    "ambiguous create requires durable external-anchor reconciliation"
                )
            if (
                event.observation.operation != "reconcile"
                or len(reconciliation.matched_production_uuids) != 1
                or reconciliation.matched_production_uuids[0] != event.observation.production_uuid
            ):
                raise NormalizationRunIntegrityError(
                    "ambiguous create reconciliation must bind one unique production UUID"
                )
        elif reconciliation is not None or event.observation.operation != "create":
            raise NormalizationRunIntegrityError(
                "direct create acknowledgement cannot carry reconciliation evidence"
            )
    elif event.state == "multi_record_conflict":
        if reconciliation is None or len(reconciliation.matched_production_uuids) < 2:
            raise NormalizationRunIntegrityError(
                "multi-record conflict requires at least two reconciled production UUIDs"
            )
    elif reconciliation is not None:
        raise NormalizationRunIntegrityError(
            "external-anchor reconciliation crossed its FSM transition"
        )

    if event.state == "upload_acknowledged":
        assert event.observation is not None
        if previous.state == "ambiguous_upload":
            if (
                event.observation.operation != "inspect_upload"
                or event.observation.source_checksum != plan.request.source.sha256
            ):
                raise NormalizationRunIntegrityError(
                    "ambiguous upload requires read-only inspection with exact source checksum"
                )
        elif event.observation.operation != "upload":
            raise NormalizationRunIntegrityError(
                "direct upload acknowledgement must come from the upload response"
            )
    elif event.state == "source_binding_unproven" and previous.state == "ambiguous_upload":
        if event.observation is None or event.observation.operation != "inspect_upload":
            raise NormalizationRunIntegrityError(
                "ambiguous upload rejection requires its read-only inspection observation"
            )


def _build_event(
    *,
    plan: NormalizationRunPlanV1,
    sequence: int,
    previous_event_id: str | None,
    previous_head_id: str | None,
    state: NormalizationStage,
    observation: ProviderObservationV1 | None = None,
    reconciliation: ExternalAnchorReconciliationV1 | None = None,
    artifacts: tuple[NormalizationArtifactBindingV1, ...] = (),
    proof: NormalizationRunProofV1 | None = None,
) -> NormalizationRunEventV1:
    operation, certainty = _stage_metadata(state, observation)
    proof_digest = _content_digest(canonical_json_bytes(proof)) if proof is not None else None
    payload = {
        "schema_version": 1,
        "request_key": plan.request_key,
        "plan_id": plan.id,
        "sequence": sequence,
        "previous_event_id": previous_event_id,
        "previous_head_id": previous_head_id,
        "state": state,
        "operation": operation,
        "effect_certainty": certainty,
        "observation": observation,
        "reconciliation": reconciliation,
        "artifacts": artifacts,
        "proof": proof_digest,
    }
    event = NormalizationRunEventV1(
        **payload,
        id="normalization-event-" + hash_object(payload),
    )
    _validate_event_semantics(event)
    return event


def _build_head(
    *,
    plan: NormalizationRunPlanV1,
    event: NormalizationRunEventV1,
    previous_head_id: str | None,
) -> NormalizationRunHeadV1:
    event_bytes = canonical_json_bytes(event)
    payload = {
        "schema_version": 1,
        "request_key": plan.request_key,
        "plan_id": plan.id,
        "sequence": event.sequence,
        "event_id": event.id,
        "event_record": _content_digest(event_bytes),
        "previous_head_id": previous_head_id,
    }
    return NormalizationRunHeadV1(
        **payload,
        id="normalization-head-" + hash_object(payload),
    )


def _build_pointer(
    plan: NormalizationRunPlanV1,
    head: NormalizationRunHeadV1,
) -> _NormalizationActivePointerV1:
    head_bytes = canonical_json_bytes(head)
    payload = {
        "schema_version": 1,
        "request_key": plan.request_key,
        "plan_id": plan.id,
        "sequence": head.sequence,
        "head_id": head.id,
        "head_record": _content_digest(head_bytes),
    }
    return _NormalizationActivePointerV1(
        **payload,
        pointer_hash=hash_object(payload),
    )


def _build_key_binding(plan: NormalizationRunPlanV1) -> _NormalizationKeyBindingV1:
    payload = {
        "schema_version": 1,
        "request_key": plan.request_key,
        "plan_id": plan.id,
        "plan_record": _content_digest(canonical_json_bytes(plan)),
    }
    return _NormalizationKeyBindingV1(**payload, binding_hash=hash_object(payload))


def _artifact_map(
    events: tuple[NormalizationRunEventV1, ...],
) -> dict[str, NormalizationContentDigestV1]:
    result: dict[str, NormalizationContentDigestV1] = {}
    for event in events:
        for binding in event.artifacts:
            if binding.name in result:
                raise NormalizationRunIntegrityError(
                    "normalization chain introduced one artifact kind more than once"
                )
            result[binding.name] = binding.digest
    return result


def _production_uuid(events: tuple[NormalizationRunEventV1, ...]) -> str:
    uuids = {
        event.observation.production_uuid
        for event in events
        if event.observation is not None and event.observation.production_uuid is not None
    }
    if len(uuids) != 1:
        raise NormalizationRunIntegrityError(
            "normalization chain must bind exactly one production UUID"
        )
    return next(iter(uuids))


def _build_proof_from_prefix(
    *,
    plan: NormalizationRunPlanV1,
    events: tuple[NormalizationRunEventV1, ...],
    heads: tuple[NormalizationRunHeadV1, ...],
    source_binding_method: Literal["upload_acknowledged", "provider_checksum"],
) -> NormalizationRunProofV1:
    if not events or not heads or events[-1].state != "receipt_ready":
        raise NormalizationRunIntegrityError(
            "normalization proof requires an exact receipt_ready prefix"
        )
    if len(events) != len(heads):
        raise NormalizationRunIntegrityError("normalization proof prefix is incomplete")
    artifacts = _artifact_map(events)
    required = {
        "raw_audio",
        "raw_probe",
        "aligned_audio",
        "aligned_probe",
        "clock_map",
        "normalization_receipt",
    }
    if set(artifacts) != required:
        raise NormalizationRunIntegrityError(
            "normalization proof prefix lacks exact output and receipt artifacts"
        )
    observations = tuple(event.observation for event in events if event.observation is not None)
    completed = tuple(
        observation
        for observation in observations
        if observation.operation == "poll"
        and observation.outcome == "acknowledged"
        and observation.provider_status_code == 3
    )
    if len(completed) != 1:
        raise NormalizationRunIntegrityError(
            "normalization proof requires exactly one completed provider observation"
        )
    submitted_settings_hash = completed[0].submitted_settings_hash
    if submitted_settings_hash != plan.request.settings_hash:
        raise NormalizationRunIntegrityError(
            "normalization proof submitted settings differ from the exact request"
        )
    if source_binding_method == "upload_acknowledged":
        if not any(
            observation.operation == "upload" and observation.outcome == "acknowledged"
            for observation in observations
        ):
            raise NormalizationRunIntegrityError(
                "upload_acknowledged proof lacks its provider acknowledgement"
            )
    elif not any(
        observation.operation in {"inspect_upload", "poll"}
        and observation.source_checksum == plan.request.source.sha256
        for observation in observations
    ):
        raise NormalizationRunIntegrityError(
            "provider_checksum proof lacks the exact source checksum"
        )
    payload = {
        "schema_version": 1,
        "request_key": plan.request_key,
        "plan_id": plan.id,
        "precomplete_event_id": events[-1].id,
        "precomplete_head_id": heads[-1].id,
        "precomplete_event_count": len(events),
        "event_chain_hash": hash_object(tuple(event.id for event in events)),
        "provider_observation_set_hash": hash_object(
            tuple(observation.id for observation in observations)
        ),
        "production_uuid": _production_uuid(events),
        "source_binding_method": source_binding_method,
        "submitted_settings_hash": submitted_settings_hash,
        "raw_audio": artifacts["raw_audio"],
        "raw_probe": artifacts["raw_probe"],
        "aligned_audio": artifacts["aligned_audio"],
        "aligned_probe": artifacts["aligned_probe"],
        "clock_map": artifacts["clock_map"],
        "receipt_hash": artifacts["normalization_receipt"].sha256,
    }
    return NormalizationRunProofV1(
        **payload,
        id="normalization-proof-" + hash_object(payload),
    )


def build_normalization_run_proof(
    stored: StoredNormalizationRunV1,
    *,
    source_binding_method: Literal["upload_acknowledged", "provider_checksum"],
) -> NormalizationRunProofV1:
    """Build a proof only from a fully replayed receipt-ready prefix."""

    return _build_proof_from_prefix(
        plan=stored.plan,
        events=stored.events,
        heads=stored.heads,
        source_binding_method=source_binding_method,
    )


ModelT = TypeVar("ModelT", bound=BaseModel)


def _read_canonical_model(
    path: Path,
    model: type[ModelT],
    *,
    label: str,
) -> tuple[ModelT, bytes]:
    if _is_link_like(path) or not path.is_file():
        raise NormalizationRunIntegrityError(f"{label} is missing or is not a regular file")
    try:
        payload = path.read_bytes()
        value = model.model_validate_json(payload)
    except (OSError, ValueError) as exc:
        raise NormalizationRunIntegrityError(f"{label} is unreadable or invalid") from exc
    if canonical_json_bytes(value) != payload:
        raise NormalizationRunIntegrityError(f"{label} is not canonical JSON")
    return value, payload


def _is_link_like(path: Path) -> bool:
    try:
        if path.is_symlink():
            return True
        is_junction = getattr(path, "is_junction", None)
        return bool(is_junction is not None and is_junction())
    except OSError as exc:
        raise NormalizationRunIntegrityError(
            "normalization repository path metadata is unreadable"
        ) from exc


def _strict_inventory(
    directory: Path,
    *,
    files: set[str],
    directories: set[str],
    label: str,
    allowed_temp: re.Pattern[str] | None = None,
) -> None:
    if _is_link_like(directory) or not directory.is_dir():
        raise NormalizationRunIntegrityError(f"{label} is missing or is a symbolic link")
    actual_files: set[str] = set()
    actual_directories: set[str] = set()
    for entry in directory.iterdir():
        if allowed_temp is not None and allowed_temp.fullmatch(entry.name):
            if _is_link_like(entry):
                raise NormalizationRunIntegrityError(f"{label} temp entry is a link")
            continue
        if _is_link_like(entry):
            raise NormalizationRunIntegrityError(f"{label} contains a symbolic link")
        if entry.is_file():
            actual_files.add(entry.name)
        elif entry.is_dir():
            actual_directories.add(entry.name)
        else:
            raise NormalizationRunIntegrityError(f"{label} contains an unknown entry")
    if actual_files != files or actual_directories != directories:
        raise NormalizationRunIntegrityError(
            f"{label} inventory mismatch: files={sorted(actual_files)!r}, "
            f"directories={sorted(actual_directories)!r}"
        )


def _publish_immutable_file(path: Path, payload: bytes, *, label: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        if _is_link_like(path) or not path.is_file() or path.read_bytes() != payload:
            raise NormalizationRunConflictError(f"{label} already has conflicting bytes")
        return
    temporary = path.parent / f".{path.name}.tmp"
    _write_fsynced(temporary, payload)
    try:
        _replace_fsynced(temporary, path)
    except OSError:
        if not path.is_file() or path.read_bytes() != payload:
            raise
        temporary.unlink(missing_ok=True)


@contextmanager
def _exclusive_normalization_lock(path: Path):
    try:
        from filelock import FileLock, Timeout
    except ImportError as exc:  # pragma: no cover - core dependency
        raise NormalizationRunIntegrityError(
            "cross-process normalization run locking requires filelock"
        ) from exc
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        with FileLock(str(path)).acquire(timeout=30):
            yield
    except Timeout as exc:
        raise NormalizationRunIntegrityError("timed out acquiring normalization run lock") from exc


class NormalizationRunRepository:
    """Strict filesystem repository for provider-free normalization state."""

    def __init__(
        self,
        subtitle_root: str | Path,
        *,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        # Preserve the caller's lexical root so a pre-existing symlink or junction
        # cannot disappear before the topology gate inspects it.
        self.subtitle_root = Path(subtitle_root).absolute()
        self.root = self.subtitle_root / "normalization-runs"
        self.keys_dir = self.root / "keys"
        self.runs_dir = self.root / "runs"
        self.locks_dir = self.root / "locks"
        self._lock = threading.RLock()
        self._clock = clock or (lambda: datetime.now(timezone.utc))

    def _validate_topology(self, *, create: bool) -> None:
        owned = (self.root, self.keys_dir, self.runs_dir, self.locks_dir)
        for path in owned:
            if _is_link_like(path):
                raise NormalizationRunIntegrityError(
                    "normalization repository path is a symbolic link or junction"
                )
            if path.exists() and not path.is_dir():
                raise NormalizationRunIntegrityError(
                    "normalization repository path is not a directory"
                )
        if create:
            if _is_link_like(self.subtitle_root):
                raise NormalizationRunIntegrityError(
                    "normalization subtitle root is a symbolic link or junction"
                )
            self.subtitle_root.mkdir(parents=True, exist_ok=True)
            self.root.mkdir(exist_ok=True)
            self.keys_dir.mkdir(exist_ok=True)
            self.runs_dir.mkdir(exist_ok=True)
            self.locks_dir.mkdir(exist_ok=True)
        if not self.subtitle_root.is_dir() or _is_link_like(self.subtitle_root):
            raise NormalizationRunIntegrityError("normalization subtitle root is missing")
        expected_root = self.subtitle_root.resolve(strict=True) / "normalization-runs"
        if not self.root.is_dir() or self.root.resolve(strict=True) != expected_root:
            raise NormalizationRunIntegrityError(
                "normalization repository escaped its subtitle root"
            )
        for path in (self.keys_dir, self.runs_dir, self.locks_dir):
            if (
                _is_link_like(path)
                or path.resolve(strict=True) != self.root.resolve(strict=True) / path.name
            ):
                raise NormalizationRunIntegrityError(
                    "normalization repository child escaped its root"
                )

    def _validate_root_inventory(self) -> None:
        _strict_inventory(
            self.root,
            files=set(),
            directories={"keys", "runs", "locks"},
            label="normalization repository root",
        )
        for entry in self.keys_dir.iterdir():
            if (
                _is_link_like(entry)
                or not entry.is_file()
                or not re.fullmatch(r"[0-9a-f]{64}\.json", entry.name)
            ):
                raise NormalizationRunIntegrityError(
                    "normalization keys inventory contains an unknown entry"
                )
        for entry in self.runs_dir.iterdir():
            if _TEMP_RUN_RE.fullmatch(entry.name) and entry.is_dir() and not _is_link_like(entry):
                continue
            if _is_link_like(entry) or not entry.is_dir() or not _PLAN_ID_RE.fullmatch(entry.name):
                raise NormalizationRunIntegrityError(
                    "normalization runs inventory contains an unknown entry"
                )
        for entry in self.locks_dir.iterdir():
            if (
                _is_link_like(entry)
                or not entry.is_file()
                or not re.fullmatch(r"[0-9a-f]{64}\.lock", entry.name)
            ):
                raise NormalizationRunIntegrityError(
                    "normalization locks inventory contains an unknown entry"
                )

    @contextmanager
    def _transaction(self, request_key: str):
        _required_sha256(request_key, label="normalization request key")
        self._validate_topology(create=True)
        with self._lock, _exclusive_normalization_lock(self.locks_dir / f"{request_key}.lock"):
            self._validate_topology(create=False)
            self._validate_root_inventory()
            yield

    def _key_path(self, request_key: str) -> Path:
        _required_sha256(request_key, label="normalization request key")
        return self.keys_dir / f"{request_key}.json"

    def _run_directory(self, plan_id: str) -> Path:
        if not _PLAN_ID_RE.fullmatch(plan_id):
            raise ValueError("normalization plan ID is invalid")
        return self.runs_dir / plan_id

    @staticmethod
    def _transition_directory(directory: Path, sequence: int) -> Path:
        if sequence < 0 or sequence >= _MAX_TRANSITIONS:
            raise ValueError("normalization event sequence is outside store bounds")
        return directory / "transitions" / f"{sequence:06d}"

    def _read_key_binding(self, request_key: str) -> _NormalizationKeyBindingV1:
        path = self._key_path(request_key)
        if not path.exists():
            raise NormalizationRunNotFoundError("normalization request key has no durable binding")
        binding, _ = _read_canonical_model(
            path,
            _NormalizationKeyBindingV1,
            label="normalization key binding",
        )
        if binding.request_key != request_key:
            raise NormalizationRunIntegrityError(
                "normalization key path crossed its authenticated request"
            )
        return binding

    def _read_plan(
        self, binding: _NormalizationKeyBindingV1
    ) -> tuple[NormalizationRunPlanV1, Path]:
        directory = self._run_directory(binding.plan_id)
        plan, plan_bytes = _read_canonical_model(
            directory / "plan.json",
            NormalizationRunPlanV1,
            label="normalization plan",
        )
        if (
            plan.id != binding.plan_id
            or plan.request_key != binding.request_key
            or _content_digest(plan_bytes) != binding.plan_record
            or build_normalization_run_plan(
                request=plan.request,
                external_anchor=plan.external_anchor,
                request_started_at=plan.request_started_at,
            )
            != plan
        ):
            raise NormalizationRunIntegrityError(
                "normalization key binding does not replay its exact plan"
            )
        return plan, directory

    def _transition_inventory(self, directory: Path) -> tuple[int, ...]:
        transitions = directory / "transitions"
        if _is_link_like(transitions) or not transitions.is_dir():
            raise NormalizationRunIntegrityError(
                "normalization transitions directory is missing or linked"
            )
        sequences: list[int] = []
        for entry in transitions.iterdir():
            if (
                _TEMP_TRANSITION_RE.fullmatch(entry.name)
                and entry.is_dir()
                and not _is_link_like(entry)
            ):
                continue
            if (
                _is_link_like(entry)
                or not entry.is_dir()
                or not _TRANSITION_NAME_RE.fullmatch(entry.name)
            ):
                raise NormalizationRunIntegrityError(
                    "normalization transition inventory contains an unknown entry"
                )
            sequences.append(int(entry.name))
        sequences.sort()
        if sequences != list(range(len(sequences))) or not sequences:
            raise NormalizationRunIntegrityError(
                "normalization transition inventory is not one contiguous prefix"
            )
        return tuple(sequences)

    def _read_transition(
        self,
        directory: Path,
        sequence: int,
    ) -> tuple[
        NormalizationRunEventV1,
        NormalizationRunHeadV1,
        NormalizationRunProofV1 | None,
    ]:
        transition = self._transition_directory(directory, sequence)
        proof_path = transition / "proof.json"
        expected_files = {"event.json", "head.json"}
        if proof_path.exists():
            expected_files.add("proof.json")
        _strict_inventory(
            transition,
            files=expected_files,
            directories=set(),
            label="normalization transition",
        )
        event, event_bytes = _read_canonical_model(
            transition / "event.json",
            NormalizationRunEventV1,
            label="normalization event",
        )
        head, _head_bytes = _read_canonical_model(
            transition / "head.json",
            NormalizationRunHeadV1,
            label="normalization head",
        )
        proof = None
        if proof_path.exists():
            proof, proof_bytes = _read_canonical_model(
                proof_path,
                NormalizationRunProofV1,
                label="normalization proof",
            )
            if event.proof != _content_digest(proof_bytes):
                raise NormalizationRunIntegrityError(
                    "normalization complete event crossed its proof bytes"
                )
        if head.event_record != _content_digest(event_bytes):
            raise NormalizationRunIntegrityError(
                "normalization head does not bind its exact event record"
            )
        _validate_event_semantics(event)
        return event, head, proof

    def _load_directory(
        self,
        *,
        plan: NormalizationRunPlanV1,
        directory: Path,
        allow_repairable_tail: bool,
    ) -> tuple[StoredNormalizationRunV1, StoredNormalizationRunV1 | None]:
        _strict_inventory(
            directory,
            files={"active-head.json", "plan.json"},
            directories={"transitions"},
            label="normalization run",
            allowed_temp=_TEMP_FILE_RE,
        )
        stored_plan, plan_bytes = _read_canonical_model(
            directory / "plan.json",
            NormalizationRunPlanV1,
            label="normalization plan",
        )
        if stored_plan != plan or directory.name != plan.id:
            raise NormalizationRunIntegrityError(
                "normalization run directory crossed its plan identity"
            )
        if canonical_json_bytes(plan) != plan_bytes:
            raise NormalizationRunIntegrityError("normalization plan is not canonical")
        pointer, _pointer_bytes = _read_canonical_model(
            directory / "active-head.json",
            _NormalizationActivePointerV1,
            label="normalization active head",
        )
        if pointer.request_key != plan.request_key or pointer.plan_id != plan.id:
            raise NormalizationRunIntegrityError("normalization active head crossed its plan")
        sequences = self._transition_inventory(directory)
        events: list[NormalizationRunEventV1] = []
        heads: list[NormalizationRunHeadV1] = []
        proofs: list[NormalizationRunProofV1 | None] = []
        production_uuid: str | None = None
        for sequence in sequences:
            event, head, proof = self._read_transition(directory, sequence)
            if (
                event.sequence != sequence
                or head.sequence != sequence
                or event.request_key != plan.request_key
                or head.request_key != plan.request_key
                or event.plan_id != plan.id
                or head.plan_id != plan.id
                or head.event_id != event.id
            ):
                raise NormalizationRunIntegrityError(
                    "normalization event/head crossed sequence or plan"
                )
            previous_event = events[-1] if events else None
            previous_head = heads[-1] if heads else None
            if (
                event.previous_event_id
                != (previous_event.id if previous_event is not None else None)
                or event.previous_head_id
                != (previous_head.id if previous_head is not None else None)
                or head.previous_head_id
                != (previous_head.id if previous_head is not None else None)
            ):
                raise NormalizationRunIntegrityError("normalization event/head chain is broken")
            if sequence == 0:
                if event.state != "prepared":
                    raise NormalizationRunIntegrityError(
                        "normalization chain must begin at prepared"
                    )
            else:
                assert previous_event is not None
                if event.state not in _ALLOWED_TRANSITIONS.get(previous_event.state, frozenset()):
                    raise NormalizationRunIntegrityError(
                        "normalization chain contains an illegal transition"
                    )
                _validate_transition_semantics(
                    plan=plan,
                    previous=previous_event,
                    event=event,
                )
            if event.observation is not None and event.observation.production_uuid is not None:
                if production_uuid is None:
                    production_uuid = event.observation.production_uuid
                elif production_uuid != event.observation.production_uuid:
                    raise NormalizationRunIntegrityError(
                        "normalization chain crossed its production UUID"
                    )
            if proof is not None:
                if event.state != "complete" or sequence == 0:
                    raise NormalizationRunIntegrityError(
                        "normalization proof is attached outside complete transition"
                    )
                expected_proof = _build_proof_from_prefix(
                    plan=plan,
                    events=tuple(events),
                    heads=tuple(heads),
                    source_binding_method=proof.source_binding_method,
                )
                if proof != expected_proof:
                    raise NormalizationRunIntegrityError(
                        "normalization proof does not replay its complete prefix"
                    )
            elif event.state == "complete":
                raise NormalizationRunIntegrityError(
                    "complete normalization transition lacks proof bytes"
                )
            events.append(event)
            heads.append(head)
            proofs.append(proof)
        if pointer.sequence >= len(heads):
            raise NormalizationRunIntegrityError(
                "normalization active head points outside event inventory"
            )
        active_head = heads[pointer.sequence]
        if pointer.head_id != active_head.id or pointer.head_record != _content_digest(
            canonical_json_bytes(active_head)
        ):
            raise NormalizationRunIntegrityError(
                "normalization active head does not bind its selected head"
            )
        tail_count = len(events) - (pointer.sequence + 1)
        if tail_count > 1:
            raise NormalizationRunIntegrityError(
                "normalization active head rollback left more than one published tail"
            )
        active = StoredNormalizationRunV1(
            plan=plan,
            event=events[pointer.sequence],
            head=heads[pointer.sequence],
            events=tuple(events[: pointer.sequence + 1]),
            heads=tuple(heads[: pointer.sequence + 1]),
            proof=proofs[pointer.sequence],
            directory=directory,
        )
        repairable = None
        if tail_count == 1:
            repairable = StoredNormalizationRunV1(
                plan=plan,
                event=events[-1],
                head=heads[-1],
                events=tuple(events),
                heads=tuple(heads),
                proof=proofs[-1],
                directory=directory,
            )
            if not allow_repairable_tail:
                raise NormalizationRunIntegrityError(
                    "normalization run has one fully published tail awaiting explicit repair"
                )
        return active, repairable

    def _load(
        self,
        request_key: str,
        *,
        expected_request: NormalizationRunRequestV1 | None,
        allow_repairable_tail: bool,
    ) -> tuple[StoredNormalizationRunV1, StoredNormalizationRunV1 | None]:
        self._validate_topology(create=False)
        self._validate_root_inventory()
        binding = self._read_key_binding(request_key)
        plan, directory = self._read_plan(binding)
        if expected_request is not None and plan.request != expected_request:
            raise NormalizationRunIntegrityError(
                "normalization plan differs from the expected request"
            )
        return self._load_directory(
            plan=plan,
            directory=directory,
            allow_repairable_tail=allow_repairable_tail,
        )

    def _find_orphan_plan(self, request_key: str) -> tuple[NormalizationRunPlanV1, Path] | None:
        matches: list[tuple[NormalizationRunPlanV1, Path]] = []
        for entry in self.runs_dir.iterdir():
            if _TEMP_RUN_RE.fullmatch(entry.name) and entry.is_dir() and not _is_link_like(entry):
                continue
            if _is_link_like(entry) or not entry.is_dir() or not _PLAN_ID_RE.fullmatch(entry.name):
                raise NormalizationRunIntegrityError(
                    "normalization orphan inventory contains an unknown entry"
                )
            plan, _ = _read_canonical_model(
                entry / "plan.json",
                NormalizationRunPlanV1,
                label="orphan normalization plan",
            )
            if plan.id != entry.name:
                raise NormalizationRunIntegrityError(
                    "orphan normalization directory crossed its plan"
                )
            if plan.request_key == request_key:
                self._load_directory(
                    plan=plan,
                    directory=entry,
                    allow_repairable_tail=False,
                )
                matches.append((plan, entry))
        if len(matches) > 1:
            raise NormalizationRunIntegrityError(
                "normalization request has multiple orphan run directories"
            )
        return matches[0] if matches else None

    def _write_transition(
        self,
        directory: Path,
        *,
        event: NormalizationRunEventV1,
        head: NormalizationRunHeadV1,
        proof: NormalizationRunProofV1 | None,
    ) -> None:
        transitions = directory / "transitions"
        transitions.mkdir(parents=True, exist_ok=True)
        destination = self._transition_directory(directory, event.sequence)
        if destination.exists():
            existing_event, existing_head, existing_proof = self._read_transition(
                directory, event.sequence
            )
            if (existing_event, existing_head, existing_proof) != (event, head, proof):
                raise NormalizationRunConflictError(
                    "normalization transition slot already has conflicting bytes"
                )
            return
        temporary = Path(tempfile.mkdtemp(prefix=".transition-", dir=transitions))
        try:
            _write_fsynced(temporary / "event.json", canonical_json_bytes(event))
            _write_fsynced(temporary / "head.json", canonical_json_bytes(head))
            if proof is not None:
                _write_fsynced(temporary / "proof.json", canonical_json_bytes(proof))
            _replace_fsynced(temporary, destination)
        except OSError:
            if destination.is_dir():
                existing = self._read_transition(directory, event.sequence)
                if existing != (event, head, proof):
                    raise NormalizationRunConflictError(
                        "normalization transition was concurrently published with conflict"
                    )
                if temporary.exists():
                    shutil.rmtree(temporary, ignore_errors=True)
            else:
                raise

    def _publish_active_pointer(
        self,
        directory: Path,
        pointer: _NormalizationActivePointerV1,
    ) -> None:
        temporary = directory / ".active-head.json.tmp"
        _write_fsynced(temporary, canonical_json_bytes(pointer))
        _replace_fsynced(temporary, directory / "active-head.json")

    def _write_initial_run(self, directory: Path, plan: NormalizationRunPlanV1) -> None:
        transitions = directory / "transitions"
        transitions.mkdir(parents=True)
        _write_fsynced(directory / "plan.json", canonical_json_bytes(plan))
        event = _build_event(
            plan=plan,
            sequence=0,
            previous_event_id=None,
            previous_head_id=None,
            state="prepared",
        )
        head = _build_head(plan=plan, event=event, previous_head_id=None)
        self._write_transition(directory, event=event, head=head, proof=None)
        self._publish_active_pointer(directory, _build_pointer(plan, head))

    def prepare(self, request: NormalizationRunRequestV1) -> StoredNormalizationRunV1:
        """Create or reopen one exact request without making a provider call."""

        validated = NormalizationRunRequestV1.model_validate(request.model_dump(mode="python"))
        with self._transaction(validated.request_key):
            key_path = self._key_path(validated.request_key)
            if key_path.exists():
                return self._load(
                    validated.request_key,
                    expected_request=validated,
                    allow_repairable_tail=False,
                )[0]
            orphan = self._find_orphan_plan(validated.request_key)
            if orphan is not None:
                plan, _directory = orphan
                if plan.request != validated:
                    raise NormalizationRunIntegrityError(
                        "orphan normalization plan differs from expected request"
                    )
                binding = _build_key_binding(plan)
                _publish_immutable_file(
                    key_path,
                    canonical_json_bytes(binding),
                    label="normalization key binding",
                )
                return self._load(
                    validated.request_key,
                    expected_request=validated,
                    allow_repairable_tail=False,
                )[0]
            plan = build_normalization_run_plan(
                request=validated,
                external_anchor="norm_" + secrets.token_hex(16),
                request_started_at=self._clock(),
            )
            destination = self._run_directory(plan.id)
            if destination.exists():
                raise NormalizationRunConflictError(
                    "normalization plan directory exists without its request binding"
                )
            temporary = Path(tempfile.mkdtemp(prefix=".run-", dir=self.runs_dir))
            try:
                self._write_initial_run(temporary, plan)
                _replace_fsynced(temporary, destination)
            except OSError:
                if destination.is_dir():
                    self._load_directory(
                        plan=plan,
                        directory=destination,
                        allow_repairable_tail=False,
                    )
                    if temporary.exists():
                        shutil.rmtree(temporary, ignore_errors=True)
                else:
                    raise
            binding = _build_key_binding(plan)
            _publish_immutable_file(
                key_path,
                canonical_json_bytes(binding),
                label="normalization key binding",
            )
            return self._load(
                validated.request_key,
                expected_request=validated,
                allow_repairable_tail=False,
            )[0]

    def load(
        self,
        request_key: str,
        *,
        expected_request: NormalizationRunRequestV1 | None = None,
    ) -> StoredNormalizationRunV1:
        """Fresh-process replay of the exact active prefix; performs no HTTP."""

        return self._load(
            request_key,
            expected_request=expected_request,
            allow_repairable_tail=False,
        )[0]

    def contains(
        self,
        request_key: str,
        *,
        expected_request: NormalizationRunRequestV1 | None = None,
    ) -> bool:
        """Validate the repository and report whether one exact request exists."""

        with self._transaction(request_key):
            if not self._key_path(request_key).exists():
                return False
            self._load(
                request_key,
                expected_request=expected_request,
                allow_repairable_tail=False,
            )
            return True

    def find_by_recovery_key(
        self,
        recovery_key: str,
    ) -> tuple[StoredNormalizationRunV1, ...]:
        """Find fully validated runs for one credential-independent identity."""

        _required_sha256(recovery_key, label="normalization recovery key")
        # A repository-wide lock is unnecessary: each returned run is loaded
        # under its own authenticated key transaction, while inventory checks
        # reject malformed/concurrent topology rather than guessing around it.
        self._validate_topology(create=True)
        self._validate_root_inventory()
        key_names = tuple(sorted(path.stem for path in self.keys_dir.iterdir()))
        if len(key_names) > _MAX_TRANSITIONS:
            raise NormalizationRunIntegrityError("normalization key inventory is unbounded")
        result: list[StoredNormalizationRunV1] = []
        for request_key in key_names:
            stored = self.load(request_key)
            if normalization_recovery_key(stored.plan.request) == recovery_key:
                result.append(stored)
        return tuple(result)

    def append(
        self,
        plan: NormalizationRunPlanV1,
        *,
        state: NormalizationStage,
        observation: ProviderObservationV1 | None = None,
        reconciliation: ExternalAnchorReconciliationV1 | None = None,
        artifacts: tuple[NormalizationArtifactBindingV1, ...] = (),
        proof: NormalizationRunProofV1 | None = None,
    ) -> StoredNormalizationRunV1:
        """Append one local record; callers perform external work elsewhere."""

        validated_plan = NormalizationRunPlanV1.model_validate(plan.model_dump(mode="python"))
        validated_observation = (
            ProviderObservationV1.model_validate(observation.model_dump(mode="python"))
            if observation is not None
            else None
        )
        validated_reconciliation = (
            ExternalAnchorReconciliationV1.model_validate(reconciliation.model_dump(mode="python"))
            if reconciliation is not None
            else None
        )
        validated_proof = (
            NormalizationRunProofV1.model_validate(proof.model_dump(mode="python"))
            if proof is not None
            else None
        )
        with self._transaction(validated_plan.request_key):
            current, _ = self._load(
                validated_plan.request_key,
                expected_request=validated_plan.request,
                allow_repairable_tail=False,
            )
            if current.plan != validated_plan:
                raise NormalizationRunIntegrityError("normalization append crossed its exact plan")
            if current.event.state in _TERMINAL_STATES:
                raise NormalizationRunConflictError("terminal normalization run is immutable")
            if state not in _ALLOWED_TRANSITIONS.get(current.event.state, frozenset()):
                raise NormalizationRunIntegrityError(
                    f"illegal transition from {current.event.state} to {state}"
                )
            if validated_observation is not None and validated_observation.production_uuid:
                previous_uuids = {
                    event.observation.production_uuid
                    for event in current.events
                    if event.observation is not None
                    and event.observation.production_uuid is not None
                }
                if previous_uuids and previous_uuids != {validated_observation.production_uuid}:
                    raise NormalizationRunIntegrityError(
                        "normalization event changed its production UUID"
                    )
            if state == "provider_completed" and (
                validated_observation is None
                or validated_observation.submitted_settings_hash
                != validated_plan.request.settings_hash
            ):
                raise NormalizationRunIntegrityError(
                    "provider completed observation differs from requested settings"
                )
            if state == "complete":
                if validated_proof is None:
                    raise NormalizationRunIntegrityError(
                        "complete normalization transition requires proof"
                    )
                expected_proof = _build_proof_from_prefix(
                    plan=validated_plan,
                    events=current.events,
                    heads=current.heads,
                    source_binding_method=validated_proof.source_binding_method,
                )
                if validated_proof != expected_proof:
                    raise NormalizationRunIntegrityError(
                        "normalization proof differs from its exact receipt prefix"
                    )
            elif validated_proof is not None:
                raise NormalizationRunIntegrityError(
                    "only complete normalization transition may bind proof"
                )
            event = _build_event(
                plan=validated_plan,
                sequence=current.event.sequence + 1,
                previous_event_id=current.event.id,
                previous_head_id=current.head.id,
                state=state,
                observation=validated_observation,
                reconciliation=validated_reconciliation,
                artifacts=artifacts,
                proof=validated_proof,
            )
            _validate_transition_semantics(
                plan=validated_plan,
                previous=current.event,
                event=event,
            )
            head = _build_head(
                plan=validated_plan,
                event=event,
                previous_head_id=current.head.id,
            )
            self._write_transition(
                current.directory,
                event=event,
                head=head,
                proof=validated_proof,
            )
            self._publish_active_pointer(
                current.directory,
                _build_pointer(validated_plan, head),
            )
            return self._load(
                validated_plan.request_key,
                expected_request=validated_plan.request,
                allow_repairable_tail=False,
            )[0]

    def repair_published_tail(
        self,
        request_key: str,
        *,
        expected_request: NormalizationRunRequestV1 | None = None,
    ) -> StoredNormalizationRunV1:
        """Publish the pointer for exactly one verified local transition tail.

        This is disk-only recovery.  It has no provider abstraction and cannot
        repeat, synthesize, acknowledge, or roll back an external side effect.
        """

        with self._transaction(request_key):
            active, repairable = self._load(
                request_key,
                expected_request=expected_request,
                allow_repairable_tail=True,
            )
            if repairable is None:
                return active
            self._publish_active_pointer(
                repairable.directory,
                _build_pointer(repairable.plan, repairable.head),
            )
            return self._load(
                request_key,
                expected_request=expected_request,
                allow_repairable_tail=False,
            )[0]


__all__ = [
    "EffectCertainty",
    "ExternalAnchorReconciliationPageV1",
    "ExternalAnchorReconciliationV1",
    "NormalizationAdapterIdentityV1",
    "NormalizationAlignmentPolicyV1",
    "NormalizationArtifactBindingV1",
    "NormalizationContentDigestV1",
    "NormalizationOperation",
    "NormalizationOutputContractV1",
    "NormalizationResumeAction",
    "NormalizationRunConflictError",
    "NormalizationRunEventV1",
    "NormalizationRunHeadV1",
    "NormalizationRunIntegrityError",
    "NormalizationRunNotFoundError",
    "NormalizationRunPlanV1",
    "NormalizationRunProofV1",
    "NormalizationRunRepository",
    "NormalizationRunRequestV1",
    "NormalizationSettingV1",
    "NormalizationStage",
    "ProviderObservationV1",
    "StoredNormalizationRunV1",
    "build_external_anchor_reconciliation",
    "build_normalization_run_plan",
    "build_normalization_run_proof",
    "build_normalization_run_request",
    "build_provider_observation",
    "normalization_recovery_key",
]
