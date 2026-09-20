"""Replayable proof for decoding one published MP4 audio stream to PCM.

This is deliberately a *lossy-source* Source Program path.  Decoding AAC into
PCM prevents another lossy generation, but it cannot restore information lost
by the published export.  The receipt therefore uses the immutable literal
``lossy_published_export_decoded_to_pcm`` and never represents the result as a
lossless timeline render.

Local paths are transport parameters only.  The logical request and receipt
bind content digests, exact tool binaries/version output, allowlisted probe
facts, canonical argv templates, raw process output, and the decoded bytes.
Verification freshly probes the materialized output and replays the complete
decode into a temporary directory.
"""

from __future__ import annotations

import base64
import binascii
import json
import os
import re
import subprocess
import tempfile
from collections.abc import Callable, Mapping, Sequence
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any, Literal, TypeAlias

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from shared.schemas.podcast_subtitles_v2 import ArtifactDigest

from .hashing import canonical_json_bytes, hash_object, measure_regular_file, sha256_bytes

SOURCE_QUALITY = "lossy_published_export_decoded_to_pcm"
_MAX_PROCESS_OUTPUT_BYTES = 4 * 1024 * 1024
_MAX_RECEIPT_BYTES = 32 * 1024 * 1024
_CONTENT_URI_RE = re.compile(r"urn:sha256:([0-9a-f]{64})")
_DURATION_RE = re.compile(r"(?:0|[1-9][0-9]*)\.[0-9]{6}")
_FLOATING_VERSION_WORDS = frozenset({"active", "current", "default", "latest", "unknown"})

_VERSION_ARGV: tuple[str, ...] = ("@tool", "-version")
_PROBE_ARGV: tuple[str, ...] = (
    "@ffprobe",
    "-v",
    "error",
    "-print_format",
    "json",
    "-show_entries",
    (
        "stream=index,codec_type,codec_name,sample_rate,channels,channel_layout,"
        "bits_per_sample,time_base,duration_ts:format=format_name,duration,size"
    ),
    "@media",
)
_DECODE_ARGV_PREFIX: tuple[str, ...] = (
    "@ffmpeg",
    "-hide_banner",
    "-v",
    "error",
    "-n",
    "-i",
    "@source",
    "-map",
)
_DECODE_ARGV_SUFFIX: tuple[str, ...] = (
    "-vn",
    "-c:a",
    "pcm_s24le",
    "-ar",
    "48000",
    "-ac",
    "2",
    "@output",
)


class PublishedSourceProgramIntegrityError(ValueError):
    """Published-source content, process execution, or receipt is untrustworthy."""


class _Contract(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)


def _content_artifact(*, sha256: str, size_bytes: int) -> ArtifactDigest:
    return ArtifactDigest(
        uri=f"urn:sha256:{sha256}",
        sha256=sha256,
        size_bytes=size_bytes,
    )


def _validate_content_artifact(value: ArtifactDigest, label: str) -> ArtifactDigest:
    matched = _CONTENT_URI_RE.fullmatch(value.uri)
    if matched is None or matched.group(1) != value.sha256:
        raise ValueError(f"{label} URI must be the matching canonical SHA-256 URN")
    return value


def _exact_int(value: object, label: str) -> object:
    if type(value) is not int:
        raise ValueError(f"{label} must be an exact integer")
    return value


class PinnedMediaExecutableV1(_Contract):
    """One exact executable build, including the exact ``-version`` response."""

    schema_version: Literal[1] = 1
    role: Literal["ffmpeg", "ffprobe"]
    artifact: ArtifactDigest
    version_stdout: bytes
    version_stderr: bytes = b""

    @field_validator("artifact")
    @classmethod
    def _artifact_is_content_only(cls, value: ArtifactDigest) -> ArtifactDigest:
        return _validate_content_artifact(value, "executable artifact")

    @model_validator(mode="after")
    def _version_is_exact_and_pinned(self) -> "PinnedMediaExecutableV1":
        if not self.version_stdout or len(self.version_stdout) > _MAX_PROCESS_OUTPUT_BYTES:
            raise ValueError("version stdout must be present and bounded")
        if self.version_stderr:
            raise ValueError("version stderr must be empty for a pinned healthy executable")
        first_line = self.version_stdout.splitlines()[0].decode("utf-8", errors="strict")
        prefix = f"{self.role} version "
        if not first_line.startswith(prefix):
            raise ValueError("version stdout does not identify the declared executable role")
        version_token = first_line[len(prefix) :].split(maxsplit=1)[0].casefold()
        if not version_token or not any(character.isdigit() for character in version_token):
            raise ValueError("executable version must contain a pinned version/build token")
        words = {word for word in re.split(r"[^a-z0-9]+", version_token) if word}
        if words & _FLOATING_VERSION_WORDS:
            raise ValueError("executable version must not use a floating identity")
        return self


class PublishedAudioExpectationV1(_Contract):
    """Expected clock and encoding of the sole published audio stream."""

    schema_version: Literal[1] = 1
    container: Literal["mov,mp4,m4a,3gp,3g2,mj2"] = "mov,mp4,m4a,3gp,3g2,mj2"
    stream_index: int = Field(ge=0, le=4095)
    codec: Literal["aac"] = "aac"
    sample_rate_hz: Literal[48_000] = 48_000
    channels: Literal[2] = 2
    channel_layout: Literal["stereo"] = "stereo"
    time_base: Literal["1/48000"] = "1/48000"
    duration_ts: int = Field(gt=0)

    @field_validator("stream_index", "sample_rate_hz", "channels", "duration_ts", mode="before")
    @classmethod
    def _integers_are_exact(cls, value: object, info: object) -> object:
        return _exact_int(value, f"published audio {getattr(info, 'field_name', 'value')}")


class PublishedExportDecodeRequestV1(_Contract):
    """Path-independent, content-addressed execution request."""

    schema_version: Literal[1] = 1
    operation: Literal["published_export_audio_decode"] = "published_export_audio_decode"
    source_quality: Literal["lossy_published_export_decoded_to_pcm"] = SOURCE_QUALITY
    source: ArtifactDigest
    expected_audio: PublishedAudioExpectationV1
    ffmpeg: PinnedMediaExecutableV1
    ffprobe: PinnedMediaExecutableV1
    probe_timeout_seconds: Literal[60] = 60
    decode_timeout_seconds: Literal[3600] = 3600
    locale: Literal["C"] = "C"
    content_hash: str = Field(pattern=r"^[0-9a-f]{64}$")

    @field_validator("source")
    @classmethod
    def _source_is_content_only(cls, value: ArtifactDigest) -> ArtifactDigest:
        return _validate_content_artifact(value, "published source")

    @model_validator(mode="after")
    def _identity_is_closed(self) -> "PublishedExportDecodeRequestV1":
        if self.expected_audio.stream_index < 0:
            raise ValueError("selected stream index must be non-negative")
        if self.ffmpeg.role != "ffmpeg" or self.ffprobe.role != "ffprobe":
            raise ValueError("request executable roles are mismatched")
        if self.ffmpeg.artifact == self.ffprobe.artifact:
            raise ValueError("ffmpeg and ffprobe must be separately pinned executables")
        if self.content_hash != _request_content_hash(self):
            raise ValueError("published decode request content_hash mismatch")
        return self


class MediaProcessCaptureV1(_Contract):
    """Exact bounded bytes and canonical path-free argv for one process."""

    schema_version: Literal[1] = 1
    step: Literal[
        "ffmpeg_version",
        "ffprobe_version",
        "source_probe",
        "decode",
        "output_probe",
    ]
    argv_template: tuple[str, ...]
    returncode: Literal[0]
    stdout: bytes
    stderr: bytes
    stdout_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    stderr_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")

    @model_validator(mode="after")
    def _capture_is_consistent(self) -> "MediaProcessCaptureV1":
        if not self.argv_template or any(not item for item in self.argv_template):
            raise ValueError("process argv template must be non-empty")
        if (
            len(self.stdout) > _MAX_PROCESS_OUTPUT_BYTES
            or len(self.stderr) > _MAX_PROCESS_OUTPUT_BYTES
        ):
            raise ValueError("process output exceeds the receipt bound")
        if self.stdout_sha256 != sha256_bytes(self.stdout):
            raise ValueError("process stdout hash mismatch")
        if self.stderr_sha256 != sha256_bytes(self.stderr):
            raise ValueError("process stderr hash mismatch")
        return self


class PublishedAudioProbeV1(_Contract):
    schema_version: Literal[1] = 1
    format_name: Literal["mov,mp4,m4a,3gp,3g2,mj2"]
    stream_index: int = Field(ge=0, le=4095)
    codec_name: Literal["aac"]
    sample_rate_hz: Literal[48_000]
    channels: Literal[2]
    channel_layout: Literal["stereo"]
    time_base: Literal["1/48000"]
    duration_ts: int = Field(gt=0)
    format_duration: str = Field(pattern=r"^(?:0|[1-9][0-9]*)\.[0-9]{6}$")
    audio_stream_count: Literal[1]

    @field_validator(
        "stream_index",
        "sample_rate_hz",
        "channels",
        "duration_ts",
        "audio_stream_count",
        mode="before",
    )
    @classmethod
    def _integers_are_exact(cls, value: object, info: object) -> object:
        return _exact_int(value, f"source probe {getattr(info, 'field_name', 'value')}")

    @model_validator(mode="after")
    def _format_clock_is_exact(self) -> "PublishedAudioProbeV1":
        _format_duration(self.format_duration, self.duration_ts, "format duration")
        return self


class PcmWavProbeV1(_Contract):
    schema_version: Literal[1] = 1
    format_name: Literal["wav"]
    stream_index: Literal[0]
    codec_name: Literal["pcm_s24le"]
    sample_rate_hz: Literal[48_000]
    channels: Literal[2]
    channel_layout: Literal["stereo"]
    bits_per_sample: Literal[24]
    time_base: Literal["1/48000"]
    duration_ts: int = Field(gt=0)
    format_duration: str = Field(pattern=r"^(?:0|[1-9][0-9]*)\.[0-9]{6}$")
    sample_frames: int = Field(gt=0)
    audio_stream_count: Literal[1]
    total_stream_count: Literal[1]

    @field_validator(
        "stream_index",
        "sample_rate_hz",
        "channels",
        "bits_per_sample",
        "duration_ts",
        "sample_frames",
        "audio_stream_count",
        "total_stream_count",
        mode="before",
    )
    @classmethod
    def _integers_are_exact(cls, value: object, info: object) -> object:
        return _exact_int(value, f"output probe {getattr(info, 'field_name', 'value')}")

    @model_validator(mode="after")
    def _clock_is_sample_exact(self) -> "PcmWavProbeV1":
        if self.duration_ts != self.sample_frames:
            raise ValueError("PCM WAV duration_ts must equal its exact sample-frame count")
        _format_duration(self.format_duration, self.duration_ts, "format duration")
        return self


class PublishedExportDecodeReceiptV1(_Contract):
    """Immutable execution evidence for one exact published-source decode."""

    schema_version: Literal[1] = 1
    operation: Literal["published_export_audio_decode"] = "published_export_audio_decode"
    source_quality: Literal["lossy_published_export_decoded_to_pcm"] = SOURCE_QUALITY
    request: PublishedExportDecodeRequestV1
    ffmpeg_version: MediaProcessCaptureV1
    ffprobe_version: MediaProcessCaptureV1
    source_probe: MediaProcessCaptureV1
    decode: MediaProcessCaptureV1
    output_probe: MediaProcessCaptureV1
    source_facts: PublishedAudioProbeV1
    output_facts: PcmWavProbeV1
    output: ArtifactDigest
    content_hash: str = Field(pattern=r"^[0-9a-f]{64}$")

    @field_validator("output")
    @classmethod
    def _output_is_content_only(cls, value: ArtifactDigest) -> ArtifactDigest:
        return _validate_content_artifact(value, "decoded output")

    @model_validator(mode="after")
    def _receipt_is_closed(self) -> "PublishedExportDecodeReceiptV1":
        expected_steps = (
            (self.ffmpeg_version, "ffmpeg_version", _VERSION_ARGV),
            (self.ffprobe_version, "ffprobe_version", _VERSION_ARGV),
            (self.source_probe, "source_probe", _PROBE_ARGV),
            (self.decode, "decode", _decode_argv(self.request.expected_audio.stream_index)),
            (self.output_probe, "output_probe", _PROBE_ARGV),
        )
        for capture, step, argv in expected_steps:
            if capture.step != step or capture.argv_template != argv:
                raise ValueError(f"{step} process identity or argv template drifted")
        if self.source_probe.stderr or self.output_probe.stderr:
            raise ValueError("successful ffprobe stderr must be empty")
        if self.decode.stdout or self.decode.stderr:
            raise ValueError("successful quiet decode output must be empty")
        if self.ffmpeg_version.stdout != self.request.ffmpeg.version_stdout:
            raise ValueError("ffmpeg version response differs from pinned request")
        if self.ffmpeg_version.stderr != self.request.ffmpeg.version_stderr:
            raise ValueError("ffmpeg version stderr differs from pinned request")
        if self.ffprobe_version.stdout != self.request.ffprobe.version_stdout:
            raise ValueError("ffprobe version response differs from pinned request")
        if self.ffprobe_version.stderr != self.request.ffprobe.version_stderr:
            raise ValueError("ffprobe version stderr differs from pinned request")
        expected = self.request.expected_audio
        if (
            self.source_facts.format_name != expected.container
            or self.source_facts.stream_index != expected.stream_index
            or self.source_facts.codec_name != expected.codec
            or self.source_facts.sample_rate_hz != expected.sample_rate_hz
            or self.source_facts.channels != expected.channels
            or self.source_facts.channel_layout != expected.channel_layout
            or self.source_facts.time_base != expected.time_base
            or self.source_facts.duration_ts != expected.duration_ts
        ):
            raise ValueError("source probe facts differ from the accepted published clock")
        if self.output_facts.duration_ts != expected.duration_ts:
            raise ValueError("decoded PCM clock differs from the published audio clock")
        if self.content_hash != _receipt_content_hash(self):
            raise ValueError("published decode receipt content_hash mismatch")
        return self


def _request_content_hash(request: PublishedExportDecodeRequestV1) -> str:
    return hash_object(request.model_dump(mode="python", exclude={"content_hash"}))


def _receipt_content_hash(receipt: PublishedExportDecodeReceiptV1) -> str:
    return hash_object(receipt.model_dump(mode="python", exclude={"content_hash"}))


def seal_request(
    *,
    source: ArtifactDigest,
    expected_audio: PublishedAudioExpectationV1,
    ffmpeg: PinnedMediaExecutableV1,
    ffprobe: PinnedMediaExecutableV1,
) -> PublishedExportDecodeRequestV1:
    """Create the only valid request form and seal its canonical identity."""

    payload: dict[str, Any] = {
        "source": source,
        "expected_audio": expected_audio,
        "ffmpeg": ffmpeg,
        "ffprobe": ffprobe,
    }
    provisional = PublishedExportDecodeRequestV1.model_construct(
        **payload,
        content_hash="0" * 64,
    )
    return PublishedExportDecodeRequestV1(
        **payload,
        content_hash=_request_content_hash(provisional),
    )


def canonical_receipt_bytes(receipt: PublishedExportDecodeReceiptV1) -> bytes:
    """Return deterministic path-free receipt bytes after strict revalidation."""

    validated = PublishedExportDecodeReceiptV1.model_validate(
        receipt.model_dump(mode="python"), strict=True
    )
    # ``BaseModel.model_dump(mode="json")`` would coerce UTF-8-compatible bytes
    # to JSON strings before our canonical serializer sees them.  Dump in
    # Python mode so canonical_json_bytes emits unambiguous ``$bytes`` markers.
    return canonical_json_bytes(validated.model_dump(mode="python"))


def load_receipt_bytes(payload: bytes) -> PublishedExportDecodeReceiptV1:
    """Load only the module's exact canonical representation, without coercion."""

    if not isinstance(payload, bytes) or not payload or len(payload) > _MAX_RECEIPT_BYTES:
        raise PublishedSourceProgramIntegrityError("receipt bytes must be present and bounded")
    document = _json_without_duplicates(payload)

    def decode(value: Any) -> Any:
        if isinstance(value, Mapping):
            if set(value) == {"$bytes"}:
                encoded = value["$bytes"]
                if not isinstance(encoded, str) or not encoded.isascii():
                    raise PublishedSourceProgramIntegrityError(
                        "receipt contains an invalid canonical byte marker"
                    )
                try:
                    decoded = base64.b64decode(encoded, validate=True)
                except (ValueError, binascii.Error) as exc:
                    raise PublishedSourceProgramIntegrityError(
                        "receipt contains invalid base64 bytes"
                    ) from exc
                if base64.b64encode(decoded).decode("ascii") != encoded:
                    raise PublishedSourceProgramIntegrityError(
                        "receipt byte marker is not canonical base64"
                    )
                return decoded
            decoded_mapping = {key: decode(item) for key, item in value.items()}
            if "argv_template" in decoded_mapping and isinstance(
                decoded_mapping["argv_template"], list
            ):
                decoded_mapping["argv_template"] = tuple(decoded_mapping["argv_template"])
            return decoded_mapping
        if isinstance(value, list):
            return [decode(item) for item in value]
        return value

    try:
        receipt = PublishedExportDecodeReceiptV1.model_validate(decode(document))
    except ValueError as exc:
        raise PublishedSourceProgramIntegrityError("receipt contract validation failed") from exc
    if canonical_receipt_bytes(receipt) != payload:
        raise PublishedSourceProgramIntegrityError("receipt bytes are not canonical")
    return receipt


def receipt_content_hash(receipt: PublishedExportDecodeReceiptV1) -> str:
    """Return the sealed receipt hash after validating every invariant."""

    validated = PublishedExportDecodeReceiptV1.model_validate(
        receipt.model_dump(mode="python"), strict=True
    )
    return validated.content_hash


def _decode_argv(stream_index: int) -> tuple[str, ...]:
    return _DECODE_ARGV_PREFIX + (f"0:{stream_index}",) + _DECODE_ARGV_SUFFIX


def _stable_environment() -> dict[str, str]:
    environment = dict(os.environ)
    environment.update({"LC_ALL": "C", "LANG": "C", "AV_LOG_FORCE_NOCOLOR": "1"})
    return environment


ProcessRunner: TypeAlias = Callable[..., subprocess.CompletedProcess[bytes]]


def _run(
    runner: ProcessRunner,
    command: Sequence[str],
    *,
    timeout_seconds: int,
    step: str,
    argv_template: tuple[str, ...],
) -> MediaProcessCaptureV1:
    try:
        completed = runner(
            list(command),
            capture_output=True,
            check=False,
            shell=False,
            timeout=timeout_seconds,
            env=_stable_environment(),
        )
    except (OSError, subprocess.SubprocessError) as exc:
        raise PublishedSourceProgramIntegrityError(f"{step} process could not complete") from exc
    if type(completed.returncode) is not int:
        raise PublishedSourceProgramIntegrityError(f"{step} returned an invalid status")
    stdout = completed.stdout
    stderr = completed.stderr
    if not isinstance(stdout, bytes) or not isinstance(stderr, bytes):
        raise PublishedSourceProgramIntegrityError(f"{step} must return raw bytes")
    if len(stdout) > _MAX_PROCESS_OUTPUT_BYTES or len(stderr) > _MAX_PROCESS_OUTPUT_BYTES:
        raise PublishedSourceProgramIntegrityError(f"{step} process output exceeds safety bound")
    if completed.returncode != 0:
        raise PublishedSourceProgramIntegrityError(
            f"{step} failed with status {completed.returncode}; no receipt was sealed"
        )
    return MediaProcessCaptureV1(
        step=step,
        argv_template=argv_template,
        returncode=0,
        stdout=stdout,
        stderr=stderr,
        stdout_sha256=sha256_bytes(stdout),
        stderr_sha256=sha256_bytes(stderr),
    )


def _json_without_duplicates(payload: bytes) -> Mapping[str, Any]:
    def pairs(values: list[tuple[str, Any]]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for key, value in values:
            if key in result:
                raise PublishedSourceProgramIntegrityError("ffprobe JSON contains duplicate keys")
            result[key] = value
        return result

    try:
        parsed = json.loads(
            payload.decode("utf-8", errors="strict"),
            object_pairs_hook=pairs,
            parse_constant=lambda value: (_ for _ in ()).throw(
                PublishedSourceProgramIntegrityError(f"ffprobe JSON contains {value}")
            ),
        )
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise PublishedSourceProgramIntegrityError(
            "ffprobe did not return strict UTF-8 JSON"
        ) from exc
    if not isinstance(parsed, Mapping):
        raise PublishedSourceProgramIntegrityError("ffprobe response root must be an object")
    return parsed


def _required_mapping(value: object, label: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise PublishedSourceProgramIntegrityError(f"ffprobe {label} must be an object")
    return value


def _required_list(value: object, label: str) -> list[Any]:
    if not isinstance(value, list):
        raise PublishedSourceProgramIntegrityError(f"ffprobe {label} must be an array")
    return value


def _validate_probe_shape(document: Mapping[str, Any]) -> None:
    allowed_root = {"programs", "stream_groups", "streams", "format"}
    if not {"streams", "format"}.issubset(document) or set(document) - allowed_root:
        raise PublishedSourceProgramIntegrityError(
            "ffprobe response contains missing or non-allowlisted root fields"
        )
    for label in ("programs", "stream_groups"):
        if label in document and _required_list(document[label], label):
            raise PublishedSourceProgramIntegrityError(
                f"ffprobe {label} must be empty for the accepted media shape"
            )
    streams = _required_list(document["streams"], "streams")
    allowed_stream = {
        "index",
        "codec_type",
        "codec_name",
        "sample_rate",
        "channels",
        "channel_layout",
        "bits_per_sample",
        "time_base",
        "duration_ts",
    }
    for stream in streams:
        typed = _required_mapping(stream, "stream")
        if set(typed) - allowed_stream:
            raise PublishedSourceProgramIntegrityError(
                "ffprobe stream contains a non-allowlisted field"
            )
    media_format = _required_mapping(document["format"], "format")
    if set(media_format) != {"format_name", "duration", "size"}:
        raise PublishedSourceProgramIntegrityError(
            "ffprobe format fields must match the path-free allowlist exactly"
        )


def _integer(value: object, label: str) -> int:
    if type(value) is int:
        return value
    if isinstance(value, str) and value and value.isascii() and value.isdecimal():
        return int(value)
    raise PublishedSourceProgramIntegrityError(f"ffprobe {label} must be an exact integer")


def _string(value: object, label: str) -> str:
    if not isinstance(value, str) or not value:
        raise PublishedSourceProgramIntegrityError(f"ffprobe {label} must be a non-empty string")
    return value


def _format_duration(value: object, duration_ts: int, label: str) -> str:
    raw = _string(value, label)
    if _DURATION_RE.fullmatch(raw) is None:
        raise PublishedSourceProgramIntegrityError(
            f"ffprobe {label} must use exact six-place decimal seconds"
        )
    try:
        samples = Decimal(raw) * Decimal(48_000)
    except InvalidOperation as exc:
        raise PublishedSourceProgramIntegrityError(f"ffprobe {label} is invalid") from exc
    if samples != Decimal(duration_ts):
        raise PublishedSourceProgramIntegrityError(
            f"ffprobe {label} differs from the exact 48 kHz stream clock"
        )
    return raw


def _parse_source_probe(payload: bytes, source: ArtifactDigest) -> PublishedAudioProbeV1:
    document = _json_without_duplicates(payload)
    _validate_probe_shape(document)
    streams = _required_list(document.get("streams"), "streams")
    audio = [
        _required_mapping(stream, "stream")
        for stream in streams
        if isinstance(stream, Mapping) and stream.get("codec_type") == "audio"
    ]
    if len(audio) != 1:
        raise PublishedSourceProgramIntegrityError(
            "published export must contain exactly one audio stream"
        )
    stream = audio[0]
    media_format = _required_mapping(document.get("format"), "format")
    if _integer(media_format.get("size"), "format size") != source.size_bytes:
        raise PublishedSourceProgramIntegrityError(
            "ffprobe source size differs from content digest"
        )
    duration_ts = _integer(stream.get("duration_ts"), "duration_ts")
    try:
        return PublishedAudioProbeV1(
            format_name=_string(media_format.get("format_name"), "format name"),
            stream_index=_integer(stream.get("index"), "stream index"),
            codec_name=_string(stream.get("codec_name"), "codec name"),
            sample_rate_hz=_integer(stream.get("sample_rate"), "sample rate"),
            channels=_integer(stream.get("channels"), "channels"),
            channel_layout=_string(stream.get("channel_layout"), "channel layout"),
            time_base=_string(stream.get("time_base"), "time base"),
            duration_ts=duration_ts,
            format_duration=_format_duration(
                media_format.get("duration"), duration_ts, "format duration"
            ),
            audio_stream_count=len(audio),
        )
    except PublishedSourceProgramIntegrityError:
        raise
    except ValueError as exc:
        raise PublishedSourceProgramIntegrityError(
            "published source probe violates policy"
        ) from exc


def _parse_output_probe(payload: bytes, output: ArtifactDigest) -> PcmWavProbeV1:
    document = _json_without_duplicates(payload)
    _validate_probe_shape(document)
    streams = _required_list(document.get("streams"), "streams")
    typed_streams = [_required_mapping(stream, "stream") for stream in streams]
    audio = [stream for stream in typed_streams if stream.get("codec_type") == "audio"]
    if len(audio) != 1 or len(typed_streams) != 1:
        raise PublishedSourceProgramIntegrityError(
            "decoded WAV must contain exactly one audio stream"
        )
    stream = audio[0]
    media_format = _required_mapping(document.get("format"), "format")
    if _integer(media_format.get("size"), "format size") != output.size_bytes:
        raise PublishedSourceProgramIntegrityError("ffprobe output size differs from decoded bytes")
    duration_ts = _integer(stream.get("duration_ts"), "duration_ts")
    try:
        return PcmWavProbeV1(
            format_name=_string(media_format.get("format_name"), "format name"),
            stream_index=_integer(stream.get("index"), "stream index"),
            codec_name=_string(stream.get("codec_name"), "codec name"),
            sample_rate_hz=_integer(stream.get("sample_rate"), "sample rate"),
            channels=_integer(stream.get("channels"), "channels"),
            channel_layout=_string(stream.get("channel_layout"), "channel layout"),
            bits_per_sample=_integer(stream.get("bits_per_sample"), "bits per sample"),
            time_base=_string(stream.get("time_base"), "time base"),
            duration_ts=duration_ts,
            format_duration=_format_duration(
                media_format.get("duration"), duration_ts, "format duration"
            ),
            sample_frames=duration_ts,
            audio_stream_count=len(audio),
            total_stream_count=len(typed_streams),
        )
    except PublishedSourceProgramIntegrityError:
        raise
    except ValueError as exc:
        raise PublishedSourceProgramIntegrityError(
            "decoded output probe violates PCM WAV policy"
        ) from exc


def _measure_matches(path: Path, expected: ArtifactDigest, label: str) -> None:
    try:
        digest, size = measure_regular_file(path)
    except (OSError, ValueError) as exc:
        raise PublishedSourceProgramIntegrityError(f"{label} is not a stable regular file") from exc
    if digest != expected.sha256 or size != expected.size_bytes:
        raise PublishedSourceProgramIntegrityError(
            f"{label} content differs from its pinned digest"
        )


def _probe(
    *,
    runner: ProcessRunner,
    executable: Path,
    media_path: Path,
    timeout_seconds: int,
    step: Literal["source_probe", "output_probe"],
) -> MediaProcessCaptureV1:
    command = (str(executable),) + _PROBE_ARGV[1:-1] + (str(media_path),)
    return _run(
        runner,
        command,
        timeout_seconds=timeout_seconds,
        step=step,
        argv_template=_PROBE_ARGV,
    )


def execute_and_seal(
    request: PublishedExportDecodeRequestV1,
    *,
    source_path: str | Path,
    output_path: str | Path,
    ffmpeg_executable: str | Path,
    ffprobe_executable: str | Path,
    _runner: ProcessRunner = subprocess.run,
) -> PublishedExportDecodeReceiptV1:
    """Freshly probe, decode, probe again, and seal a path-free receipt."""

    request = PublishedExportDecodeRequestV1.model_validate(
        request.model_dump(mode="python"), strict=True
    )
    source_candidate = Path(source_path)
    output_candidate = Path(output_path)
    ffmpeg_candidate = Path(ffmpeg_executable)
    ffprobe_candidate = Path(ffprobe_executable)
    if os.path.lexists(output_candidate):
        raise PublishedSourceProgramIntegrityError("decoded output path already exists")
    if source_candidate.absolute() == output_candidate.absolute():
        raise PublishedSourceProgramIntegrityError("source and output paths must differ")
    if ffmpeg_candidate.absolute() == ffprobe_candidate.absolute():
        raise PublishedSourceProgramIntegrityError("ffmpeg and ffprobe paths must differ")
    if not output_candidate.parent.is_dir():
        raise PublishedSourceProgramIntegrityError("decoded output parent directory does not exist")

    _measure_matches(source_candidate, request.source, "published source")
    _measure_matches(ffmpeg_candidate, request.ffmpeg.artifact, "ffmpeg executable")
    _measure_matches(ffprobe_candidate, request.ffprobe.artifact, "ffprobe executable")

    ffmpeg_version = _run(
        _runner,
        (str(ffmpeg_candidate), "-version"),
        timeout_seconds=request.probe_timeout_seconds,
        step="ffmpeg_version",
        argv_template=_VERSION_ARGV,
    )
    ffprobe_version = _run(
        _runner,
        (str(ffprobe_candidate), "-version"),
        timeout_seconds=request.probe_timeout_seconds,
        step="ffprobe_version",
        argv_template=_VERSION_ARGV,
    )
    if (
        ffmpeg_version.stdout != request.ffmpeg.version_stdout
        or ffmpeg_version.stderr != request.ffmpeg.version_stderr
        or ffprobe_version.stdout != request.ffprobe.version_stdout
        or ffprobe_version.stderr != request.ffprobe.version_stderr
    ):
        raise PublishedSourceProgramIntegrityError("fresh executable version output drifted")

    source_probe = _probe(
        runner=_runner,
        executable=ffprobe_candidate,
        media_path=source_candidate,
        timeout_seconds=request.probe_timeout_seconds,
        step="source_probe",
    )
    if source_probe.stderr:
        raise PublishedSourceProgramIntegrityError("successful source probe emitted stderr")
    source_facts = _parse_source_probe(source_probe.stdout, request.source)
    if source_facts.stream_index != request.expected_audio.stream_index:
        raise PublishedSourceProgramIntegrityError("selected audio stream index differs from probe")
    expected = request.expected_audio
    if (
        source_facts.format_name != expected.container
        or source_facts.codec_name != expected.codec
        or source_facts.sample_rate_hz != expected.sample_rate_hz
        or source_facts.channels != expected.channels
        or source_facts.channel_layout != expected.channel_layout
        or source_facts.time_base != expected.time_base
        or source_facts.duration_ts != expected.duration_ts
    ):
        raise PublishedSourceProgramIntegrityError(
            "published source does not match accepted audio facts"
        )

    decode_template = _decode_argv(expected.stream_index)
    decode_command = (
        (str(ffmpeg_candidate),)
        + decode_template[1:6]
        + (str(source_candidate),)
        + decode_template[7:-1]
        + (str(output_candidate),)
    )
    decode = _run(
        _runner,
        decode_command,
        timeout_seconds=request.decode_timeout_seconds,
        step="decode",
        argv_template=decode_template,
    )
    if decode.stdout or decode.stderr:
        raise PublishedSourceProgramIntegrityError("successful quiet decode emitted process output")
    if not output_candidate.exists():
        raise PublishedSourceProgramIntegrityError("ffmpeg reported success without decoded output")
    try:
        output_sha256, output_size = measure_regular_file(output_candidate)
    except (OSError, ValueError) as exc:
        raise PublishedSourceProgramIntegrityError(
            "decoded output is not a stable regular file"
        ) from exc
    output = _content_artifact(sha256=output_sha256, size_bytes=output_size)

    output_probe = _probe(
        runner=_runner,
        executable=ffprobe_candidate,
        media_path=output_candidate,
        timeout_seconds=request.probe_timeout_seconds,
        step="output_probe",
    )
    if output_probe.stderr:
        raise PublishedSourceProgramIntegrityError("successful output probe emitted stderr")
    output_facts = _parse_output_probe(output_probe.stdout, output)
    if output_facts.duration_ts != expected.duration_ts:
        raise PublishedSourceProgramIntegrityError(
            "decoded output changed the published sample clock"
        )

    # Close replacement races across probe/decode.  A file that changes at any
    # point after the initial measurement cannot acquire a successful receipt.
    _measure_matches(source_candidate, request.source, "published source")
    _measure_matches(ffmpeg_candidate, request.ffmpeg.artifact, "ffmpeg executable")
    _measure_matches(ffprobe_candidate, request.ffprobe.artifact, "ffprobe executable")
    _measure_matches(output_candidate, output, "decoded output")

    payload: dict[str, Any] = {
        "request": request,
        "ffmpeg_version": ffmpeg_version,
        "ffprobe_version": ffprobe_version,
        "source_probe": source_probe,
        "decode": decode,
        "output_probe": output_probe,
        "source_facts": source_facts,
        "output_facts": output_facts,
        "output": output,
    }
    provisional = PublishedExportDecodeReceiptV1.model_construct(
        **payload,
        content_hash="0" * 64,
    )
    return PublishedExportDecodeReceiptV1(
        **payload,
        content_hash=_receipt_content_hash(provisional),
    )


def verify_and_replay(
    receipt: PublishedExportDecodeReceiptV1,
    *,
    source_path: str | Path,
    output_path: str | Path,
    ffmpeg_executable: str | Path,
    ffprobe_executable: str | Path,
    _runner: ProcessRunner = subprocess.run,
) -> PublishedExportDecodeReceiptV1:
    """Verify materialized bytes and reproduce the complete receipt from scratch."""

    accepted = PublishedExportDecodeReceiptV1.model_validate(
        receipt.model_dump(mode="python"), strict=True
    )
    output_candidate = Path(output_path)
    _measure_matches(Path(source_path), accepted.request.source, "published source")
    _measure_matches(Path(ffmpeg_executable), accepted.request.ffmpeg.artifact, "ffmpeg executable")
    _measure_matches(
        Path(ffprobe_executable), accepted.request.ffprobe.artifact, "ffprobe executable"
    )
    _measure_matches(output_candidate, accepted.output, "materialized decoded output")

    fresh_output_probe = _probe(
        runner=_runner,
        executable=Path(ffprobe_executable),
        media_path=output_candidate,
        timeout_seconds=accepted.request.probe_timeout_seconds,
        step="output_probe",
    )
    fresh_output_facts = _parse_output_probe(fresh_output_probe.stdout, accepted.output)
    if fresh_output_probe != accepted.output_probe or fresh_output_facts != accepted.output_facts:
        raise PublishedSourceProgramIntegrityError("materialized output probe differs from receipt")

    with tempfile.TemporaryDirectory(prefix="podcast-subtitle-v2-published-replay-") as temp:
        replay_output = Path(temp) / "decoded-source-program.wav"
        replayed = execute_and_seal(
            accepted.request,
            source_path=source_path,
            output_path=replay_output,
            ffmpeg_executable=ffmpeg_executable,
            ffprobe_executable=ffprobe_executable,
            _runner=_runner,
        )
    if canonical_receipt_bytes(replayed) != canonical_receipt_bytes(accepted):
        raise PublishedSourceProgramIntegrityError(
            "fresh decode replay differs from sealed receipt"
        )
    return accepted


__all__ = [
    "MediaProcessCaptureV1",
    "PcmWavProbeV1",
    "PinnedMediaExecutableV1",
    "PublishedAudioExpectationV1",
    "PublishedAudioProbeV1",
    "PublishedExportDecodeReceiptV1",
    "PublishedExportDecodeRequestV1",
    "PublishedSourceProgramIntegrityError",
    "SOURCE_QUALITY",
    "canonical_receipt_bytes",
    "execute_and_seal",
    "load_receipt_bytes",
    "receipt_content_hash",
    "seal_request",
    "verify_and_replay",
]
