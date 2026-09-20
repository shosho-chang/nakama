"""Fail-closed proof that Auphonic input is the complete rendered program.

The local path is transport only.  Logical identity is the canonical receipt,
which binds one completed Resolve render job, its complete timeline clock, and
the exact lossless WAV bytes observed at the boundary.
"""

from __future__ import annotations

import hashlib
import math
import os
import re
import stat
import struct
from collections.abc import Mapping
from datetime import datetime, timedelta
from pathlib import Path
from typing import BinaryIO, Literal

from pydantic import (
    AwareDatetime,
    BaseModel,
    ConfigDict,
    Field,
    field_validator,
    model_validator,
)

from shared.schemas.podcast_subtitles_v2 import ArtifactDigest

from .hashing import canonical_json_bytes, hash_object

_SAFE_ID_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:-]{7,127}")
_EPISODE_ID_RE = re.compile(r"[a-z0-9][a-z0-9._-]{2,127}")
_VERSION_RE = re.compile(r"[0-9]+(?:\.[0-9]+){1,4}")
_TIMECODE_RE = re.compile(
    r"(?P<hour>[0-9]{2}):(?P<minute>[0-5][0-9]):(?P<second>[0-5][0-9])"
    r"(?P<separator>[:;])(?P<frame>[0-9]{2})"
)
_FLOATING_ID_WORDS = frozenset(
    {"active", "current", "default", "latest", "none", "null", "selected", "unknown"}
)
_PCM_SUBTYPE_GUID = bytes.fromhex("0100000000001000800000aa00389b71")


class SourceProgramIntegrityError(ValueError):
    """The capture, receipt, clock, or rendered bytes cannot be trusted."""


class _Contract(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)


def _exact_int(value: object, label: str) -> object:
    if type(value) is not int:
        raise ValueError(f"{label} must be an exact integer")
    return value


def _safe_stable_id(value: str, label: str) -> str:
    if _SAFE_ID_RE.fullmatch(value) is None:
        raise ValueError(f"{label} must be a stable safe identifier")
    words = {word for word in re.split(r"[^A-Za-z0-9]+", value.casefold()) if word}
    if words & _FLOATING_ID_WORDS:
        raise ValueError(f"{label} must not use a floating identity")
    return value


def _safe_name(value: str, label: str) -> str:
    if value != value.strip() or not value or len(value) > 256:
        raise ValueError(f"{label} must be trimmed and non-empty")
    if any(ord(character) < 32 or ord(character) == 127 for character in value):
        raise ValueError(f"{label} contains a control character")
    return value


class FrameRateV1(_Contract):
    schema_version: Literal[1] = 1
    numerator: int = Field(gt=0, le=240_000)
    denominator: int = Field(gt=0, le=10_000)

    @field_validator("numerator", "denominator", mode="before")
    @classmethod
    def _integers_are_exact(cls, value: object, info: object) -> object:
        return _exact_int(value, f"frame rate {getattr(info, 'field_name', 'value')}")

    @model_validator(mode="after")
    def _rate_is_reduced_and_sane(self) -> "FrameRateV1":
        if math.gcd(self.numerator, self.denominator) != 1:
            raise ValueError("frame rate rational must be reduced")
        if not 1 <= self.numerator / self.denominator <= 240:
            raise ValueError("frame rate must be between 1 and 240 fps")
        return self


class ResolveRendererIdentityV1(_Contract):
    schema_version: Literal[1] = 1
    product: Literal["DaVinci Resolve"]
    version: str

    @field_validator("version")
    @classmethod
    def _version_is_pinned(cls, value: str) -> str:
        if _VERSION_RE.fullmatch(value) is None:
            raise ValueError("renderer version must be a pinned numeric version")
        return value


class ResolveProjectIdentityV1(_Contract):
    schema_version: Literal[1] = 1
    stable_id: str
    name: str

    @field_validator("stable_id")
    @classmethod
    def _id_is_stable(cls, value: str) -> str:
        return _safe_stable_id(value, "project stable_id")

    @field_validator("name")
    @classmethod
    def _name_is_safe(cls, value: str) -> str:
        return _safe_name(value, "project name")


class ResolveTimelineIdentityV1(_Contract):
    schema_version: Literal[1] = 1
    stable_id: str
    name: str
    frame_rate: FrameRateV1
    start_frame: int = Field(ge=0)
    end_frame: int = Field(ge=0)
    start_timecode: str
    end_timecode: str

    @field_validator("stable_id")
    @classmethod
    def _id_is_stable(cls, value: str) -> str:
        return _safe_stable_id(value, "timeline stable_id")

    @field_validator("name")
    @classmethod
    def _name_is_safe(cls, value: str) -> str:
        return _safe_name(value, "timeline name")

    @field_validator("start_frame", "end_frame", mode="before")
    @classmethod
    def _frames_are_exact(cls, value: object, info: object) -> object:
        return _exact_int(value, f"timeline {getattr(info, 'field_name', 'frame')}")

    @field_validator("start_timecode", "end_timecode")
    @classmethod
    def _timecode_is_closed(cls, value: str) -> str:
        if _TIMECODE_RE.fullmatch(value) is None:
            raise ValueError("timeline timecode must be HH:MM:SS:FF or HH:MM:SS;FF")
        return value

    @model_validator(mode="after")
    def _range_and_timecodes_match(self) -> "ResolveTimelineIdentityV1":
        if self.end_frame < self.start_frame:
            raise ValueError("timeline end_frame must not precede start_frame")
        if _timecode_frame(self.start_timecode, self.frame_rate) != self.start_frame:
            raise ValueError("timeline start timecode does not match start_frame")
        if _timecode_frame(self.end_timecode, self.frame_rate) != self.end_frame:
            raise ValueError("timeline end timecode does not match end_frame")
        return self

    @property
    def frame_count(self) -> int:
        """Resolve MarkIn/MarkOut are inclusive, so both boundary frames render."""

        return self.end_frame - self.start_frame + 1


class ResolveLosslessAudioSettingsV1(_Contract):
    schema_version: Literal[1] = 1
    container: Literal["wav"]
    codec: Literal["pcm_s24le"]
    sample_rate_hz: Literal[48_000]
    bit_depth: Literal[24]
    channels: Literal[2]
    audio_stream_count: Literal[1]

    @field_validator(
        "sample_rate_hz", "bit_depth", "channels", "audio_stream_count", mode="before"
    )
    @classmethod
    def _numeric_settings_are_exact(cls, value: object, info: object) -> object:
        return _exact_int(value, f"render audio {getattr(info, 'field_name', 'value')}")


class ResolveRenderJobV1(_Contract):
    schema_version: Literal[1] = 1
    stable_id: str
    status: Literal["Complete"]
    requested_at: AwareDatetime
    completed_at: AwareDatetime
    mark_in_frame: int = Field(ge=0)
    mark_out_frame: int = Field(ge=0)
    target_type: Literal["single_clip"]
    audio: ResolveLosslessAudioSettingsV1

    @field_validator("stable_id")
    @classmethod
    def _id_is_stable(cls, value: str) -> str:
        return _safe_stable_id(value, "render job stable_id")

    @field_validator("mark_in_frame", "mark_out_frame", mode="before")
    @classmethod
    def _frames_are_exact(cls, value: object, info: object) -> object:
        return _exact_int(value, f"render job {getattr(info, 'field_name', 'frame')}")

    @field_validator("requested_at", "completed_at")
    @classmethod
    def _timestamps_are_utc(cls, value: datetime) -> datetime:
        if value.utcoffset() != timedelta(0):
            raise ValueError("render timestamps must use UTC")
        return value

    @model_validator(mode="after")
    def _completion_is_after_request(self) -> "ResolveRenderJobV1":
        if self.completed_at < self.requested_at:
            raise ValueError("render completion cannot precede request")
        if self.mark_out_frame < self.mark_in_frame:
            raise ValueError("render MarkOut cannot precede MarkIn")
        return self


class ResolveSourceProgramCaptureV1(_Contract):
    schema_version: Literal[1] = 1
    source_kind: Literal["resolve_direct_lossless_render"] = "resolve_direct_lossless_render"
    source_quality: Literal["lossless_timeline_render"] = "lossless_timeline_render"
    episode_id: str
    renderer: ResolveRendererIdentityV1
    project: ResolveProjectIdentityV1
    timeline: ResolveTimelineIdentityV1
    render_job: ResolveRenderJobV1

    @field_validator("episode_id")
    @classmethod
    def _episode_id_is_stable(cls, value: str) -> str:
        if _EPISODE_ID_RE.fullmatch(value) is None:
            raise ValueError("episode_id must be a stable lowercase slug")
        return value

    @model_validator(mode="after")
    def _job_requests_the_complete_timeline(self) -> "ResolveSourceProgramCaptureV1":
        if (
            self.render_job.mark_in_frame != self.timeline.start_frame
            or self.render_job.mark_out_frame != self.timeline.end_frame
        ):
            raise ValueError("render job must request the complete timeline frame range")
        return self


class LosslessWavProbeV1(_Contract):
    schema_version: Literal[1] = 1
    container: Literal["wav"]
    codec: Literal["pcm_s24le"]
    format_tag: Literal["pcm", "wave_format_extensible_pcm"]
    sample_rate_hz: Literal[48_000]
    bit_depth: Literal[24]
    channels: Literal[2]
    audio_stream_count: Literal[1]
    block_align_bytes: Literal[6]
    byte_rate: Literal[288_000]
    sample_frames: int = Field(gt=0)
    data_bytes: int = Field(gt=0)

    @field_validator(
        "sample_rate_hz",
        "bit_depth",
        "channels",
        "audio_stream_count",
        "block_align_bytes",
        "byte_rate",
        "sample_frames",
        "data_bytes",
        mode="before",
    )
    @classmethod
    def _probe_numbers_are_exact(cls, value: object, info: object) -> object:
        return _exact_int(value, f"WAV probe {getattr(info, 'field_name', 'value')}")

    @model_validator(mode="after")
    def _sample_topology_is_exact(self) -> "LosslessWavProbeV1":
        if self.data_bytes != self.sample_frames * self.block_align_bytes:
            raise ValueError("WAV data bytes do not equal exact sample topology")
        return self


class SourceProgramReceiptV1(_Contract):
    schema_version: Literal[1] = 1
    id: str
    capture: ResolveSourceProgramCaptureV1
    output: ArtifactDigest
    probe: LosslessWavProbeV1
    content_hash: str = Field(pattern=r"^[0-9a-f]{64}$")

    @field_validator("output", mode="before")
    @classmethod
    def _artifact_numbers_are_exact(cls, value: object) -> object:
        if isinstance(value, ArtifactDigest):
            size: object = value.size_bytes
        elif isinstance(value, Mapping):
            size = value.get("size_bytes")
        else:
            return value
        if type(size) is not int:
            raise ValueError("Source Program output size_bytes must be an exact integer")
        return value

    @model_validator(mode="after")
    def _identity_and_clock_are_closed(self) -> "SourceProgramReceiptV1":
        if self.output.uri != f"sha256://{self.output.sha256}":
            raise ValueError("Source Program output URI must be content-addressed")
        expected_hash = hash_object(_receipt_payload(self))
        if self.content_hash != expected_hash:
            raise ValueError("Source Program receipt content hash mismatch")
        if self.id != f"source-program-receipt-{expected_hash}":
            raise ValueError("Source Program receipt id mismatch")
        _assert_program_clock(self.capture.timeline, self.probe.sample_frames)
        return self


class VerifiedSourceProgram(_Contract):
    """Provider-facing capability created only by a fresh byte/probe replay."""

    schema_version: Literal[1] = 1
    path: Path
    source: ArtifactDigest
    receipt_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    sample_frames: int = Field(gt=0)
    duration_ms: int = Field(gt=0)


def _timecode_frame(value: str, frame_rate: FrameRateV1) -> int:
    match = _TIMECODE_RE.fullmatch(value)
    if match is None:  # pragma: no cover - field validation owns syntax
        raise ValueError("invalid timeline timecode")
    nominal_fps = (frame_rate.numerator + frame_rate.denominator // 2) // frame_rate.denominator
    frame = int(match.group("frame"))
    if frame >= nominal_fps:
        raise ValueError("timeline timecode frame exceeds nominal frame rate")
    hour = int(match.group("hour"))
    minute = int(match.group("minute"))
    second = int(match.group("second"))
    result = ((hour * 60 + minute) * 60 + second) * nominal_fps + frame
    if match.group("separator") == ";":
        if (frame_rate.numerator, frame_rate.denominator) not in {
            (30_000, 1_001),
            (60_000, 1_001),
            (120_000, 1_001),
        }:
            raise ValueError("drop-frame timecode requires a supported 1000/1001 rate")
        dropped_per_minute = nominal_fps // 15
        if minute % 10 and second == 0 and frame < dropped_per_minute:
            raise ValueError("timeline timecode names a dropped frame")
        total_minutes = hour * 60 + minute
        result -= dropped_per_minute * (total_minutes - total_minutes // 10)
    return result


def _receipt_payload(receipt: SourceProgramReceiptV1) -> dict[str, object]:
    return receipt.model_dump(mode="json", exclude={"id", "content_hash"})


def _assert_program_clock(timeline: ResolveTimelineIdentityV1, sample_frames: int) -> None:
    ideal_numerator = timeline.frame_count * 48_000 * timeline.frame_rate.denominator
    ideal_denominator = timeline.frame_rate.numerator
    lower, remainder = divmod(ideal_numerator, ideal_denominator)
    allowed = {lower} if remainder == 0 else {lower, lower + 1}
    if sample_frames not in allowed:
        expected = str(lower) if remainder == 0 else f"{lower} or {lower + 1}"
        raise SourceProgramIntegrityError(
            "rendered WAV does not match the complete program clock: "
            f"expected {expected} sample frames, observed {sample_frames}"
        )


def _probe_wav(stream: BinaryIO, *, size_bytes: int) -> LosslessWavProbeV1:
    stream.seek(0)
    header = stream.read(12)
    if len(header) != 12 or header[:4] != b"RIFF" or header[8:] != b"WAVE":
        raise SourceProgramIntegrityError("Source Program must be a RIFF/WAVE file")
    declared_size = struct.unpack("<I", header[4:8])[0] + 8
    if declared_size != size_bytes:
        raise SourceProgramIntegrityError("WAV RIFF size does not match exact output bytes")

    fmt: bytes | None = None
    data_bytes: int | None = None
    cursor = 12
    while cursor < size_bytes:
        stream.seek(cursor)
        chunk_header = stream.read(8)
        if len(chunk_header) != 8:
            raise SourceProgramIntegrityError("WAV contains a truncated chunk header")
        chunk_id = chunk_header[:4]
        chunk_size = struct.unpack("<I", chunk_header[4:])[0]
        payload_start = cursor + 8
        payload_end = payload_start + chunk_size
        padded_end = payload_end + (chunk_size & 1)
        if payload_end > size_bytes or padded_end > size_bytes:
            raise SourceProgramIntegrityError("WAV chunk exceeds exact output bytes")
        if chunk_id == b"fmt ":
            if fmt is not None or not 16 <= chunk_size <= 1_024:
                raise SourceProgramIntegrityError("WAV must contain one bounded fmt chunk")
            stream.seek(payload_start)
            fmt = stream.read(chunk_size)
        elif chunk_id == b"data":
            if data_bytes is not None:
                raise SourceProgramIntegrityError("WAV must contain exactly one data chunk")
            data_bytes = chunk_size
        cursor = padded_end
    if cursor != size_bytes or fmt is None or data_bytes is None or data_bytes == 0:
        raise SourceProgramIntegrityError("WAV chunk topology is incomplete")

    format_tag, channels, sample_rate, byte_rate, block_align, bits = struct.unpack(
        "<HHIIHH", fmt[:16]
    )
    if format_tag == 1:
        probe_format: Literal["pcm", "wave_format_extensible_pcm"] = "pcm"
    elif format_tag == 0xFFFE:
        extension_size = struct.unpack("<H", fmt[16:18])[0] if len(fmt) >= 18 else 0
        if len(fmt) < 40 or extension_size < 22 or fmt[24:40] != _PCM_SUBTYPE_GUID:
            raise SourceProgramIntegrityError("WAV extensible subtype is not integer PCM")
        valid_bits = struct.unpack("<H", fmt[18:20])[0]
        if valid_bits != 24:
            raise SourceProgramIntegrityError("WAV extensible valid bits must be 24")
        probe_format = "wave_format_extensible_pcm"
    else:
        raise SourceProgramIntegrityError("Source Program WAV codec must be integer PCM")
    observed = (channels, sample_rate, byte_rate, block_align, bits)
    if observed != (2, 48_000, 288_000, 6, 24):
        raise SourceProgramIntegrityError(
            "Source Program must be WAV PCM 48 kHz, 24-bit, stereo"
        )
    if data_bytes % block_align:
        raise SourceProgramIntegrityError("WAV data bytes do not align to complete samples")
    return LosslessWavProbeV1(
        container="wav",
        codec="pcm_s24le",
        format_tag=probe_format,
        sample_rate_hz=sample_rate,
        bit_depth=bits,
        channels=channels,
        audio_stream_count=1,
        block_align_bytes=block_align,
        byte_rate=byte_rate,
        sample_frames=data_bytes // block_align,
        data_bytes=data_bytes,
    )


def _measure_and_probe(path: Path) -> tuple[str, int, LosslessWavProbeV1]:
    candidate = Path(path)
    try:
        before_path = candidate.lstat()
    except OSError as exc:
        raise SourceProgramIntegrityError(f"cannot inspect Source Program output: {exc}") from exc
    reparse_flag = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)
    before_attributes = int(getattr(before_path, "st_file_attributes", 0))
    if (
        stat.S_ISLNK(before_path.st_mode)
        or before_attributes & reparse_flag
        or not stat.S_ISREG(before_path.st_mode)
    ):
        raise SourceProgramIntegrityError("Source Program path must be a non-reparse file")

    digest = hashlib.sha256()
    try:
        with candidate.open("rb") as stream:
            before_stream = os.fstat(stream.fileno())
            while chunk := stream.read(1024 * 1024):
                digest.update(chunk)
            probe = _probe_wav(stream, size_bytes=before_stream.st_size)
            after_stream = os.fstat(stream.fileno())
        after_path = candidate.lstat()
    except (OSError, struct.error) as exc:
        raise SourceProgramIntegrityError(f"cannot measure Source Program output: {exc}") from exc

    def identity(value: os.stat_result) -> tuple[int, int, int, int, int, int]:
        return (
            value.st_dev,
            value.st_ino,
            value.st_size,
            value.st_mtime_ns,
            value.st_ctime_ns,
            int(getattr(value, "st_file_attributes", 0)),
        )

    after_attributes = int(getattr(after_path, "st_file_attributes", 0))
    if (
        stat.S_ISLNK(after_path.st_mode)
        or after_attributes & reparse_flag
        or not stat.S_ISREG(after_path.st_mode)
        or identity(before_path) != identity(after_path)
        or identity(before_stream) != identity(after_stream)
        or before_path.st_dev != before_stream.st_dev
        or before_path.st_ino != before_stream.st_ino
    ):
        raise SourceProgramIntegrityError("Source Program output changed while measured")
    return digest.hexdigest(), before_stream.st_size, probe


def seal_source_program(
    *, capture: ResolveSourceProgramCaptureV1, output_path: str | Path
) -> SourceProgramReceiptV1:
    """Seal a completed direct Resolve render after exact byte/probe/clock checks."""

    digest, size_bytes, probe = _measure_and_probe(Path(output_path))
    _assert_program_clock(capture.timeline, probe.sample_frames)
    output = ArtifactDigest(
        uri=f"sha256://{digest}",
        sha256=digest,
        size_bytes=size_bytes,
    )
    logical = {
        "schema_version": 1,
        "capture": capture.model_dump(mode="json"),
        "output": output.model_dump(mode="json"),
        "probe": probe.model_dump(mode="json"),
    }
    content_hash = hash_object(logical)
    return SourceProgramReceiptV1(
        id=f"source-program-receipt-{content_hash}",
        capture=capture,
        output=output,
        probe=probe,
        content_hash=content_hash,
    )


def source_program_receipt_bytes(receipt: SourceProgramReceiptV1) -> bytes:
    """Return canonical portable receipt bytes after replaying logical identity."""

    verified = SourceProgramReceiptV1.model_validate(receipt)
    return canonical_json_bytes(verified)


def verify_source_program_receipt(
    *, receipt: SourceProgramReceiptV1 | bytes, output_path: str | Path
) -> VerifiedSourceProgram:
    """Replay receipt bytes and current media before granting provider capability."""

    if isinstance(receipt, bytes):
        try:
            parsed = SourceProgramReceiptV1.model_validate_json(receipt)
        except Exception as exc:
            raise SourceProgramIntegrityError("Source Program receipt bytes are invalid") from exc
        if canonical_json_bytes(parsed) != receipt:
            raise SourceProgramIntegrityError("Source Program receipt bytes are not canonical")
    else:
        parsed = SourceProgramReceiptV1.model_validate(receipt)

    path = Path(output_path)
    digest, size_bytes, probe = _measure_and_probe(path)
    if digest != parsed.output.sha256 or size_bytes != parsed.output.size_bytes:
        raise SourceProgramIntegrityError("Source Program output bytes do not match receipt")
    if probe != parsed.probe:
        raise SourceProgramIntegrityError("Source Program WAV probe does not match receipt")
    _assert_program_clock(parsed.capture.timeline, probe.sample_frames)
    duration_ms = (probe.sample_frames * 1_000 + 24_000) // 48_000
    return VerifiedSourceProgram(
        path=path.resolve(),
        source=parsed.output,
        receipt_hash=parsed.content_hash,
        sample_frames=probe.sample_frames,
        duration_ms=duration_ms,
    )


__all__ = [
    "FrameRateV1",
    "LosslessWavProbeV1",
    "ResolveLosslessAudioSettingsV1",
    "ResolveProjectIdentityV1",
    "ResolveRenderJobV1",
    "ResolveRendererIdentityV1",
    "ResolveSourceProgramCaptureV1",
    "ResolveTimelineIdentityV1",
    "SourceProgramIntegrityError",
    "SourceProgramReceiptV1",
    "VerifiedSourceProgram",
    "seal_source_program",
    "source_program_receipt_bytes",
    "verify_source_program_receipt",
]
