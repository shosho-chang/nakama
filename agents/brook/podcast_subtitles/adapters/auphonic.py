"""Auphonic Adapter with fail-closed, audit-grade normalization provenance."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from shared.schemas.podcast_subtitles_v2 import (
    ArtifactDigest,
    AudioClockMap,
    NormalizationMetric,
    NormalizationParameter,
    NormalizationReceipt,
    normalization_settings_hash,
)

from ..hashing import hash_file, hash_object
from ..normalization_execution import NormalizationExecutor
from ..ports import (
    AdapterInputError,
    AdapterIntegrityError,
    NormalizationResult,
    NormalizeRequest,
)

NormalizeImplementation = Callable[..., str | Path]
DetailedNormalizeImplementation = Callable[..., object]
ClockMapVerifier = Callable[[Path, Path], AudioClockMap]


def _shared_normalize(source_audio: Path, **kwargs: Any) -> Path:
    from shared.auphonic import normalize

    return normalize(source_audio, **kwargs)


def _shared_normalize_detailed(source_audio: Path, **kwargs: Any) -> object:
    from shared.auphonic import normalize_detailed

    return normalize_detailed(source_audio, **kwargs)


def _artifact(path: Path) -> ArtifactDigest:
    resolved = path.resolve()
    return ArtifactDigest(
        uri=resolved.as_uri(),
        sha256=hash_file(resolved),
        size_bytes=resolved.stat().st_size,
    )


def _milliseconds(seconds: float) -> int:
    value = round(float(seconds) * 1000)
    if value <= 0:
        raise AdapterInputError("Auphonic result reported a non-positive audio duration")
    return value


def _utc_timestamp(value: datetime | str | None) -> datetime | None:
    if value is None:
        return None
    if isinstance(value, str):
        candidate = value.strip().replace("Z", "+00:00")
        if not candidate:
            return None
        try:
            value = datetime.fromisoformat(candidate)
        except ValueError as exc:
            raise AdapterInputError(
                f"Auphonic provider timestamp is not ISO-8601: {candidate!r}"
            ) from exc
    if value.tzinfo is None or value.utcoffset() is None:
        raise AdapterInputError("Auphonic timestamp must be timezone-aware")
    return value.astimezone(timezone.utc)


def _parameters(value: object | None) -> tuple[NormalizationParameter, ...] | None:
    if value is None:
        return None
    try:
        algorithms = tuple(getattr(value, "algorithms"))
        output_file = tuple(getattr(value, "output_file"))
    except (AttributeError, TypeError) as exc:
        raise AdapterInputError("Auphonic parameters have an unsupported shape") from exc
    result = [
        NormalizationParameter(scope="algorithm", name=str(name), value=parameter)
        for name, parameter in algorithms
    ]
    result.extend(
        NormalizationParameter(scope="output", name=str(name), value=parameter)
        for name, parameter in output_file
    )
    return tuple(sorted(result, key=lambda item: (item.scope, item.name)))


def _metrics(alignment: object) -> tuple[NormalizationMetric, ...]:
    values = (
        ("requested_jingle", getattr(alignment, "requested_jingle_seconds", None), "seconds"),
        ("head_offset", getattr(alignment, "head_offset_seconds", None), "seconds"),
        ("head_correlation", getattr(alignment, "head_correlation", None), "ratio"),
        ("mid_correlation", getattr(alignment, "mid_correlation", None), "ratio"),
        ("drift", getattr(alignment, "drift_seconds", None), "seconds"),
    )
    return tuple(
        NormalizationMetric(name=name, value=float(value), unit=unit)
        for name, value, unit in values
        if value is not None
    )


class AuphonicNormalizerAdapter:
    """Map retained Auphonic output into the V2 immutable receipt contract.

    The production factory injects the durable stepwise executor.  Direct legacy
    construction still defaults to ``shared.auphonic.normalize_detailed`` for
    compatibility, while the historical path-only seam remains draft-only.
    """

    def __init__(
        self,
        *,
        settings: Mapping[str, object] | None = None,
        normalize_fn: NormalizeImplementation | None = None,
        normalize_detailed_fn: DetailedNormalizeImplementation | None = None,
        clock_map_verifier: ClockMapVerifier | None = None,
        executor: NormalizationExecutor | None = None,
    ) -> None:
        if normalize_fn is not None and normalize_detailed_fn is not None:
            raise ValueError("choose normalize_fn or normalize_detailed_fn, not both")
        if executor is not None and (normalize_fn is not None or normalize_detailed_fn is not None):
            raise ValueError("executor cannot be combined with legacy normalization seams")
        self._settings = dict(settings or {})
        self._executor = executor
        self._normalize_fn = normalize_fn
        self._normalize_detailed_fn = (
            None
            if executor is not None
            else normalize_detailed_fn
            or (None if normalize_fn is not None else _shared_normalize_detailed)
        )
        self._clock_map_verifier = clock_map_verifier

    def normalize(self, request: NormalizeRequest) -> NormalizationResult:
        if self._executor is not None:
            return self._executor.normalize(request)
        source = Path(request.source_audio)
        if not source.is_file():
            raise AdapterInputError(f"source audio is not a file: {source}")

        source_artifact = _artifact(source)
        if (
            request.expected_source_hash is not None
            and request.expected_source_hash != source_artifact.sha256
        ):
            raise AdapterIntegrityError(
                "source audio hash does not match NormalizeRequest: "
                f"expected {request.expected_source_hash}, got {source_artifact.sha256}"
            )

        kwargs: dict[str, object] = dict(self._settings)
        if request.output_dir is not None:
            kwargs["output_dir"] = Path(request.output_dir)

        if self._detailed_mode:
            return self._normalize_detailed(source, source_artifact, kwargs)
        return self._normalize_legacy(source, source_artifact, kwargs)

    @property
    def _detailed_mode(self) -> bool:
        return self._normalize_detailed_fn is not None

    def _normalize_detailed(
        self,
        source: Path,
        source_artifact: ArtifactDigest,
        kwargs: dict[str, object],
    ) -> NormalizationResult:
        assert self._normalize_detailed_fn is not None
        detailed = self._normalize_detailed_fn(source, **kwargs)
        try:
            normalized = Path(getattr(detailed, "output_path"))
            alignment = getattr(detailed, "alignment")
            production_id = str(getattr(detailed, "production_uuid"))
            production_source = str(getattr(detailed, "production_source"))
            claimed_provider_outcome = str(getattr(detailed, "provider_outcome"))
        except (AttributeError, TypeError) as exc:
            raise AdapterInputError("normalize_detailed returned an unsupported result") from exc
        if not normalized.is_file():
            raise AdapterInputError(f"Auphonic normalization did not produce a file: {normalized}")
        if not production_id.strip():
            raise AdapterInputError("Auphonic detailed result is missing production UUID")

        requested_parameters = _parameters(getattr(detailed, "requested_parameters", None))
        submitted_parameters = _parameters(getattr(detailed, "submitted_parameters", None))
        preset = getattr(detailed, "preset", None)
        alignment_method = str(getattr(alignment, "method"))
        alignment_verified = bool(getattr(alignment, "verified"))
        claimed_source_identity_verified = bool(
            getattr(detailed, "source_identity_verified", False)
        )
        source_binding_method = str(getattr(detailed, "source_binding_method", "legacy_unknown"))
        # A provider/helper boolean cannot promote a heuristic match.  The
        # method is the independently typed proof category; sanitize an
        # inconsistent upstream result before constructing the strict receipt.
        source_identity_verified = claimed_source_identity_verified and (
            source_binding_method in {"upload_in_current_request", "provider_checksum"}
        )
        drift_seconds = getattr(alignment, "drift_seconds", None)
        raw_provider_status_code = getattr(detailed, "provider_status_code", None)
        provider_status_code = (
            raw_provider_status_code if type(raw_provider_status_code) is int else None
        )
        provider_outcome = (
            "completed"
            if claimed_provider_outcome == "completed" and provider_status_code == 3
            else "unknown"
        )
        clock_map = AudioClockMap(
            source_origin_ms=0,
            normalized_origin_ms=0,
            verified=alignment_verified,
            drift_ms=float(drift_seconds or 0.0) * 1000,
        )

        can_accept = (
            provider_outcome == "completed"
            and provider_status_code == 3
            and production_source in {"created", "reused"}
            and submitted_parameters is not None
            and bool(submitted_parameters)
            and source_identity_verified
            and source_binding_method in {"upload_in_current_request", "provider_checksum"}
            and alignment_verified
            and alignment_method in {"cross_correlation", "identity"}
        )
        settings_hash = (
            normalization_settings_hash(submitted_parameters, preset=preset)
            if submitted_parameters
            else hash_object(
                {
                    "adapter": "auphonic-normalizer-v2",
                    "implementation": "shared.auphonic.normalize_detailed",
                    "parameters": "unknown",
                }
            )
        )
        receipt = NormalizationReceipt(
            status="accepted" if can_accept else "draft",
            provider="auphonic",
            production_id=production_id,
            production_source=production_source,
            provider_outcome=provider_outcome,
            provider_status_code=provider_status_code,
            provider_status=str(getattr(detailed, "provider_status")),
            source=source_artifact,
            normalized=_artifact(normalized),
            source_duration_ms=_milliseconds(getattr(detailed, "source_duration_seconds")),
            normalized_duration_ms=_milliseconds(getattr(detailed, "normalized_duration_seconds")),
            request_started_at=_utc_timestamp(getattr(detailed, "request_started_at")),
            completed_at=_utc_timestamp(getattr(detailed, "completed_at")),
            provider_created_at=_utc_timestamp(getattr(detailed, "provider_created_at", None)),
            provider_completed_at=_utc_timestamp(getattr(detailed, "provider_completed_at", None)),
            requested_parameters=requested_parameters or (),
            submitted_parameters=submitted_parameters,
            preset=preset,
            settings_hash=settings_hash,
            reuse_reason=getattr(detailed, "reuse_reason", None),
            original_production_id=getattr(detailed, "original_production_uuid", None),
            source_identity_verified=source_identity_verified,
            source_binding_method=source_binding_method,
            clock_map=clock_map,
            alignment_method=alignment_method,
            alignment_metrics=_metrics(alignment),
            alignment_failure_reason=getattr(alignment, "fallback_reason", None),
        )
        return NormalizationResult(
            normalized_audio=normalized.resolve(),
            receipt=receipt,
        )

    def _normalize_legacy(
        self,
        source: Path,
        source_artifact: ArtifactDigest,
        kwargs: dict[str, object],
    ) -> NormalizationResult:
        implementation = self._normalize_fn or _shared_normalize
        normalized = Path(implementation(source, **kwargs))
        if not normalized.is_file():
            raise AdapterInputError(f"Auphonic normalization did not produce a file: {normalized}")
        if self._clock_map_verifier is None:
            clock_map = AudioClockMap(
                source_origin_ms=0,
                normalized_origin_ms=0,
                verified=False,
                drift_ms=0.0,
            )
            alignment_method = "legacy_unverified"
        else:
            clock_map = self._clock_map_verifier(source, normalized)
            if not isinstance(clock_map, AudioClockMap):
                raise AdapterInputError("clock_map_verifier must return AudioClockMap")
            alignment_method = "identity" if clock_map.verified else "legacy_unverified"

        receipt = NormalizationReceipt(
            status="draft",
            provider="auphonic",
            production_id=None,
            production_source="legacy_unknown",
            provider_outcome="unknown",
            source=source_artifact,
            normalized=_artifact(normalized),
            requested_parameters=tuple(
                sorted(
                    (
                        NormalizationParameter(scope="adapter", name=str(key), value=value)
                        for key, value in self._settings.items()
                        if value is None or isinstance(value, (str, int, float, bool))
                    ),
                    key=lambda item: (item.scope, item.name),
                )
            ),
            submitted_parameters=None,
            settings_hash=hash_object(
                {
                    "adapter": "auphonic-normalizer-v2",
                    "implementation": "shared.auphonic.normalize",
                    "settings": self._settings,
                }
            ),
            clock_map=clock_map,
            alignment_method=alignment_method,
        )
        return NormalizationResult(normalized_audio=normalized.resolve(), receipt=receipt)


__all__ = [
    "AuphonicNormalizerAdapter",
    "ClockMapVerifier",
    "DetailedNormalizeImplementation",
    "NormalizeImplementation",
]
