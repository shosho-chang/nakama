"""Production Auphonic provider/media implementations for NormalizationExecutor."""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from importlib.metadata import PackageNotFoundError, version
from pathlib import Path

from shared import auphonic

from ..hashing import hash_file, hash_object
from ..normalization_execution import (
    AlignmentResultV1,
    AuphonicCreateResultV1,
    AuphonicCredentialV1,
    AuphonicMutationResultV1,
    AuphonicPollResultV1,
    AuphonicProductionPageV1,
    AuphonicProductionProjectionV1,
    AuphonicProviderFailure,
    AuphonicUploadInspectionV1,
    MediaProbeV1,
)
from ..normalization_run import (
    NormalizationAlignmentPolicyV1,
    NormalizationOutputContractV1,
    NormalizationSettingV1,
)

_EXECUTABLE_SEARCH_PATH = os.environ.get("PATH")


def effective_normalization_settings(
    overrides: dict[str, object] | None = None,
) -> tuple[NormalizationSettingV1, ...]:
    """Load and freeze exact provider parameters before any external action."""

    raw = auphonic.load_effective_settings(**dict(overrides or {}))
    payload = auphonic._production_payload(raw)
    algorithms = payload["algorithms"]
    outputs = payload["output_files"]
    if not isinstance(algorithms, dict) or not isinstance(outputs, list) or not outputs:
        raise ValueError("Auphonic effective settings are malformed")
    output = outputs[0]
    if not isinstance(output, dict):
        raise ValueError("Auphonic effective output settings are malformed")
    # Auphonic's JSON API identifies PCM depth in the format slug.  In
    # particular, 24-bit PCM is ``wav-24bit``; ``wav`` means 16-bit PCM.
    # Keep the public high-level config compatible while freezing the exact
    # provider payload here.
    output = dict(output)
    if raw["output_format"] == "wav":
        bit_depth = raw["output_bitdepth"]
        if bit_depth == 24:
            output = {"format": "wav-24bit"}
        elif bit_depth == 16:
            output = {"format": "wav"}
        else:
            raise ValueError("Auphonic WAV bit depth is unsupported")
    values = [
        NormalizationSettingV1(scope="algorithm", name=str(name), value=value)
        for name, value in algorithms.items()
    ]
    values.extend(
        NormalizationSettingV1(scope="output", name=str(name), value=value)
        for name, value in output.items()
    )
    return tuple(sorted(values, key=lambda item: (item.scope, item.name)))


@dataclass(frozen=True, slots=True)
class EffectiveNormalizationPolicyV1:
    settings: tuple[NormalizationSettingV1, ...]
    output_contract: NormalizationOutputContractV1
    alignment_policy: NormalizationAlignmentPolicyV1


def effective_normalization_policy(
    overrides: dict[str, object] | None = None,
) -> EffectiveNormalizationPolicyV1:
    raw = auphonic.load_effective_settings(**dict(overrides or {}))
    if raw["output_format"] != "wav" or raw["output_bitdepth"] != 24:
        raise ValueError("Podcast Subtitle V2 requires Auphonic WAV PCM 24-bit output")
    if raw["trim_jingle"] is not True:
        raise ValueError("Podcast Subtitle V2 requires verified jingle alignment")
    jingle_seconds = raw["jingle_seconds"]
    if type(jingle_seconds) not in {int, float} or not 0 <= float(jingle_seconds) <= 120:
        raise ValueError("Auphonic jingle duration is outside V2 policy")
    return EffectiveNormalizationPolicyV1(
        settings=effective_normalization_settings(overrides),
        output_contract=NormalizationOutputContractV1(
            container="wav",
            codec="pcm_s24le",
            bit_depth=24,
            audio_stream_count=1,
            channel_policy="preserve",
            sample_rate_policy="preserve",
        ),
        alignment_policy=NormalizationAlignmentPolicyV1(
            algorithm="cross_correlation_v1",
            trim_jingle=True,
            requested_jingle_ms=round(float(jingle_seconds) * 1000),
            minimum_head_correlation=0.5,
            minimum_mid_correlation=0.5,
            maximum_drift_ms=50,
        ),
    )


def _params(settings: tuple[NormalizationSettingV1, ...]) -> dict[str, object]:
    algorithms = {item.name: item.value for item in settings if item.scope == "algorithm"}
    output = {item.name: item.value for item in settings if item.scope == "output"}
    return {"algorithms": algorithms, "output_files": [output]}


def _parse_time(value: object) -> datetime | None:
    if not isinstance(value, str) or not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        return None
    return parsed.astimezone(timezone.utc)


def _credential_ref(email: str) -> str:
    return "cred_" + hash_object({"provider": "auphonic", "account": email.casefold()})[:32]


def _provider_source_sha256(value: object) -> str | None:
    """Accept only an explicitly SHA-256-shaped source binding.

    Auphonic documents ``output_files[].checksum`` as MD5.  A 32-hex provider
    checksum therefore cannot establish identity with our source CAS and is
    intentionally projected as unavailable.
    """

    if isinstance(value, str) and re.fullmatch(r"[0-9a-f]{64}", value):
        return value
    return None


class SharedAuphonicStepwiseProvider:
    """Thin redacting Adapter over shared Auphonic HTTP step functions."""

    def __init__(self) -> None:
        self._download_urls: dict[tuple[str, str], str] = {}

    @staticmethod
    def _account(credential: AuphonicCredentialV1) -> auphonic.AuphonicAccount:
        account = credential.handle
        if not isinstance(account, auphonic.AuphonicAccount):
            raise AuphonicProviderFailure("credentials", category="malformed", definitive=True)
        return account

    def credentials(self, *, source_duration_ms: int) -> tuple[AuphonicCredentialV1, ...]:
        if type(source_duration_ms) is not int or source_duration_ms <= 0:
            raise AuphonicProviderFailure(
                "credentials", category="malformed", definitive=True
            )
        try:
            accounts = tuple(auphonic._load_accounts())
        except Exception:
            raise AuphonicProviderFailure(
                "credentials", category="malformed", definitive=True
            ) from None
        required_hours = max(source_duration_ms / 3_600_000, 0.05)
        observations: list[tuple[auphonic.AuphonicAccount, str, bool, datetime | None]] = []
        for account in accounts:
            ref = _credential_ref(account.email)
            try:
                observation = auphonic.query_user_credit_step(account.api_key)
                credits = observation.get("credits")
                if type(credits) not in {int, float}:
                    raise ValueError("credits")
                recharge = _parse_time(observation.get("recharge_date"))
                observations.append((account, ref, float(credits) >= required_hours, recharge))
            except Exception:
                # A failed or malformed read-only preflight cannot authorize a
                # new paid run.  The handle remains available for replay of an
                # already credential-bound run.
                observations.append((account, ref, False, None))
        eligible = sorted(
            (item for item in observations if item[2]),
            key=lambda item: (
                item[3] is None,
                item[3] or datetime.max.replace(tzinfo=timezone.utc),
                item[1],
            ),
        )
        ranks = {item[1]: rank for rank, item in enumerate(eligible)}
        return tuple(
            AuphonicCredentialV1(
                credential_ref=ref,
                handle=account,
                available_for_new_run=available,
                selection_rank=ranks.get(ref, 2_147_483_647),
            )
            for account, ref, available, _recharge in observations
        )

    def create(self, credential, *, external_anchor, settings, preset):
        if preset is not None:
            raise AuphonicProviderFailure(
                "create",
                category="malformed",
                definitive=True,
            )
        account = self._account(credential)
        try:
            uuid, status = auphonic.create_production_payload_step(
                account.api_key,
                submitted_payload=_params(settings),
                external_anchor=external_anchor,
            )
        except TimeoutError:
            raise AuphonicProviderFailure("create", category="timeout") from None
        except Exception:
            raise AuphonicProviderFailure("create", category="transport") from None
        record, _ = auphonic.inspect_production_step(account.api_key, uuid)
        created = _parse_time(record.get("creation_time"))
        if created is None:
            raise AuphonicProviderFailure("create", category="malformed", definitive=True)
        return AuphonicCreateResultV1(uuid, status, created)

    def reconcile_page(self, credential, *, offset, limit):
        account = self._account(credential)
        try:
            records, _status = auphonic.list_productions_minimal_step(
                account.api_key,
                offset=offset,
                limit=limit,
            )
            projections = tuple(
                AuphonicProductionProjectionV1(
                    production_uuid=str(item.get("uuid") or ""),
                    title=str(item.get("title") or ""),
                    provider_created_at=_parse_time(item.get("creation_time")),
                )
                for item in records
            )
            return AuphonicProductionPageV1(offset, limit, projections)
        except TimeoutError:
            raise AuphonicProviderFailure("reconcile", category="timeout") from None
        except Exception:
            raise AuphonicProviderFailure("reconcile", category="malformed") from None

    def upload(self, credential, *, production_uuid, source_audio):
        account = self._account(credential)
        try:
            status = auphonic.upload_production_step(
                account.api_key,
                production_uuid,
                source_audio,
            )
        except TimeoutError:
            raise AuphonicProviderFailure("upload", category="timeout") from None
        except Exception:
            raise AuphonicProviderFailure("upload", category="transport") from None
        return AuphonicMutationResultV1(production_uuid, status)

    def inspect_upload(self, credential, *, production_uuid):
        account = self._account(credential)
        try:
            record, status = auphonic.inspect_production_step(account.api_key, production_uuid)
        except TimeoutError:
            raise AuphonicProviderFailure("inspect_upload", category="timeout") from None
        except Exception:
            raise AuphonicProviderFailure("inspect_upload", category="transport") from None
        checksum = _provider_source_sha256(record.get("source_checksum"))
        return AuphonicUploadInspectionV1(
            production_uuid,
            status,
            checksum,
        )

    def start(self, credential, *, production_uuid):
        account = self._account(credential)
        try:
            status, provider_status = auphonic.start_production_step(
                account.api_key, production_uuid
            )
        except TimeoutError:
            raise AuphonicProviderFailure("start", category="timeout") from None
        except Exception:
            raise AuphonicProviderFailure("start", category="transport") from None
        return AuphonicMutationResultV1(production_uuid, status, provider_status)

    def poll(self, credential, *, production_uuid):
        account = self._account(credential)
        try:
            record, status = auphonic.inspect_production_step(account.api_key, production_uuid)
            provider_status = record.get("status")
            if type(provider_status) is not int:
                raise ValueError("status")
            settings_hash = None
            output_hash = None
            if provider_status == 3:
                algorithms = record.get("algorithms")
                output_file = record.get("output_file")
                if not isinstance(algorithms, dict) or not isinstance(output_file, dict):
                    raise ValueError("settings")
                settings = tuple(
                    sorted(
                        (
                            *(
                                NormalizationSettingV1(
                                    scope="algorithm", name=str(name), value=value
                                )
                                for name, value in algorithms.items()
                            ),
                            *(
                                NormalizationSettingV1(scope="output", name=str(name), value=value)
                                for name, value in output_file.items()
                                if name in {"format", "bitdepth", "bitrate"}
                            ),
                        ),
                        key=lambda item: (item.scope, item.name),
                    )
                )
                from shared.schemas.podcast_subtitles_v2 import (
                    NormalizationParameter,
                    normalization_settings_hash,
                )

                settings_hash = normalization_settings_hash(
                    tuple(
                        NormalizationParameter(scope=item.scope, name=item.name, value=item.value)
                        for item in settings
                    ),
                    preset=None,
                )
                provider_format = str(output_file.get("format") or "")
                if provider_format == "wav-24bit":
                    container = "wav"
                    bit_depth = 24
                elif provider_format == "wav":
                    container = "wav"
                    bit_depth = 16
                else:
                    container = provider_format
                    raw_bit_depth = output_file.get("bitdepth")
                    if type(raw_bit_depth) is not int:
                        raise ValueError("output bit depth")
                    bit_depth = raw_bit_depth
                output_hash = NormalizationOutputContractV1(
                    container=container,
                    codec=f"pcm_s{bit_depth}le",
                    bit_depth=bit_depth,
                    audio_stream_count=1,
                    channel_policy=(
                        "mono" if output_file.get("mono_mixdown") is True else "preserve"
                    ),
                    sample_rate_policy="preserve",
                ).content_hash
                download_url = record.get("download_url")
                if isinstance(download_url, str) and download_url:
                    self._download_urls[(credential.credential_ref, production_uuid)] = download_url
            checksum = _provider_source_sha256(record.get("source_checksum"))
            return AuphonicPollResultV1(
                production_uuid=production_uuid,
                http_status_code=status,
                provider_status_code=provider_status,
                submitted_settings_hash=settings_hash,
                source_checksum=checksum,
                output_contract_hash=output_hash,
                provider_created_at=_parse_time(record.get("creation_time")),
                provider_completed_at=_parse_time(record.get("completion_time")),
            )
        except TimeoutError:
            raise AuphonicProviderFailure("poll", category="timeout") from None
        except Exception:
            raise AuphonicProviderFailure("poll", category="malformed") from None

    def download(self, credential, *, production_uuid, destination):
        account = self._account(credential)
        key = (credential.credential_ref, production_uuid)
        url = self._download_urls.pop(key, None)
        if url is None:
            try:
                record, _ = auphonic.inspect_production_step(account.api_key, production_uuid)
                candidate = record.get("download_url")
                url = candidate if isinstance(candidate, str) else None
            except Exception:
                url = None
        if not url:
            raise AuphonicProviderFailure("download", category="malformed", definitive=True)
        try:
            auphonic.download_production_step(account.api_key, url, destination)
        except TimeoutError:
            raise AuphonicProviderFailure("download", category="timeout") from None
        except Exception:
            raise AuphonicProviderFailure("download", category="transport") from None


class FFprobeMediaInspector:
    """Parse allowlisted deterministic ffprobe audio facts."""

    def __init__(self, executable: str = "ffprobe") -> None:
        self._executable = executable

    def probe(self, path: Path) -> MediaProbeV1:
        result = subprocess.run(
            [
                self._executable,
                "-v",
                "error",
                "-print_format",
                "json",
                "-show_format",
                "-show_streams",
                str(path),
            ],
            capture_output=True,
            check=True,
            timeout=60,
        )
        data = json.loads(result.stdout)
        streams = [item for item in data.get("streams", []) if item.get("codec_type") == "audio"]
        if not streams:
            raise ValueError("ffprobe found no audio stream")
        stream = streams[0]
        format_data = data.get("format") or {}
        container = str(format_data.get("format_name") or "").split(",", 1)[0]
        codec = str(stream.get("codec_name") or "")
        if codec.startswith("pcm_s") and codec.endswith("le"):
            codec = codec
        bit_depth = int(stream.get("bits_per_raw_sample") or stream.get("bits_per_sample") or 0)
        return MediaProbeV1(
            duration_ms=round(float(format_data["duration"]) * 1000),
            container=container,
            codec=codec,
            bit_depth=bit_depth,
            audio_stream_count=len(streams),
            channels=int(stream["channels"]),
            sample_rate_hz=int(stream["sample_rate"]),
        )


class SharedCrossCorrelationAligner:
    """Fail-closed bridge to retained Auphonic cross-correlation utility."""

    def align(self, raw_audio: Path, source_audio: Path, *, policy):
        result = auphonic._align_trim_detailed(
            raw_audio,
            source_audio,
            policy.requested_jingle_ms / 1000,
        )
        method = result.method
        if method not in {"cross_correlation", "identity", "fixed_seconds", "not_requested"}:
            method = "not_requested"
        return AlignmentResultV1(
            output_path=result.output_path,
            method=method,
            verified=result.verified,
            source_origin_ms=0,
            normalized_origin_ms=0,
            drift_ms=float(result.drift_seconds or 0.0) * 1000,
            head_correlation=result.head_correlation,
            mid_correlation=result.mid_correlation,
            failure_code=(
                re.sub(r"[^A-Za-z0-9._-]", "_", str(result.fallback_reason))[:128]
                if result.fallback_reason
                else None
            ),
        )


def normalizer_identity(settings: tuple[NormalizationSettingV1, ...]):
    def package_version(name: str) -> str:
        try:
            return version(name)
        except PackageNotFoundError:
            return "unavailable"

    def executable_identity(command: str) -> dict[str, object]:
        candidate = Path(command)
        resolved_value = (
            str(candidate)
            if candidate.is_absolute()
            else shutil.which(command, path=_EXECUTABLE_SEARCH_PATH)
        )
        if not resolved_value:
            raise ValueError("normalization executable is unavailable")
        resolved = Path(resolved_value).resolve(strict=True)
        if not resolved.is_file():
            raise ValueError("normalization executable is not a regular file")
        try:
            completed = subprocess.run(
                [str(resolved), "-version"],
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=10,
                check=False,
            )
        except (OSError, subprocess.SubprocessError):
            raise ValueError("normalization executable identity probe failed") from None
        if completed.returncode != 0:
            raise ValueError("normalization executable identity probe failed")
        first_line = (completed.stdout or completed.stderr).splitlines()
        if not first_line or not first_line[0].strip():
            raise ValueError("normalization executable version is unavailable")
        return {
            "sha256": hash_file(resolved),
            "size_bytes": resolved.stat().st_size,
            "version_line": first_line[0].strip()[:512],
        }

    runtime = {
        "python": sys.version.split()[0],
        "platform": sys.platform,
        "ffmpeg": executable_identity(
            os.environ.get("PODCAST_SUBTITLE_V2_FFMPEG", "ffmpeg")
        ),
        "ffprobe": executable_identity(
            os.environ.get("PODCAST_SUBTITLE_V2_FFPROBE", "ffprobe")
        ),
        "httpx": package_version("httpx"),
        "pydantic": package_version("pydantic"),
    }
    from ..normalization_run import NormalizationAdapterIdentityV1

    implementation_files = (
        Path(__file__),
        Path(__file__).parents[1] / "normalization_execution.py",
        Path(__file__).parents[1] / "normalization_run.py",
        Path(auphonic.__file__ or ""),
    )

    return NormalizationAdapterIdentityV1(
        name="auphonic-normalizer-v2",
        version="2",
        config_hash=hash_object(settings),
        code_hash=hash_object(tuple(hash_file(path) for path in implementation_files)),
        runtime_hash=hash_object(runtime),
    )


__all__ = [
    "EffectiveNormalizationPolicyV1",
    "FFprobeMediaInspector",
    "SharedAuphonicStepwiseProvider",
    "SharedCrossCorrelationAligner",
    "effective_normalization_settings",
    "effective_normalization_policy",
    "normalizer_identity",
]
