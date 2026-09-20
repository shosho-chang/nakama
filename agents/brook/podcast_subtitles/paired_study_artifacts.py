"""Fail-closed artifact builder for the paired blind boundary study.

This module turns exact local inputs into a reproducible A/B workspace.  It
does not execute a renderer and it does not authenticate humans.  Hashes bind
operator attestations and local bytes; they are not proof that the
attestations are true.
"""

from __future__ import annotations

import hashlib
import io
import json
import os
import re
import tempfile
import wave
import zipfile
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Literal, Mapping, Sequence

from .benchmark import (
    PairedBoundaryCandidate,
    PairedBoundaryJudgement,
    PairedBoundaryMapping,
    PairedBoundaryMappingEntry,
    PairedBoundaryMappingReveal,
    PairedBoundaryOutcome,
    PairedBoundaryPredeclaration,
    PairedBoundaryStudy,
    PairedClipPresentation,
    PairedStudyClip,
    load_paired_boundary_study,
    paired_boundary_candidate_record_hash,
    paired_boundary_mapping_commitment_hash,
    paired_boundary_predeclaration_hash,
    paired_boundary_study_hash,
)
from .hashing import canonical_json_bytes, hash_object, sha256_bytes

SAMPLING_FRAME_SCHEMA_VERSION = 1
SELECTION_ALGORITHM = "frozen_stratified_nonoverlap_v1"
SELECTION_METHOD = "predeclared_nonoverlapping_episode_windows_v1"
RANDOMIZATION_METHOD = "opaque_balanced_per_clip_v1"
PROTOCOL_ID = "podcast-subtitle-v2-paired-blind-v1"
CANONICAL_RENDERER_ID = "paired-canonical-text-audio-v1"
CANONICAL_RENDERER_IDENTITY_BYTES = canonical_json_bytes(
    {
        "schema_version": 1,
        "renderer_id": CANONICAL_RENDERER_ID,
        "implementation": "builtin_canonical_json_cues_plus_audio_hash",
        "algorithm_hash": hash_object(
            {
                "ordered_fields": [
                    "schema_version",
                    "renderer_id",
                    "renderer_identity_hash",
                    "clip_id",
                    "start_ms",
                    "end_ms",
                    "audio_clip_hash",
                    "cues",
                ],
                "serialization": "nakama_canonical_json_v1",
            }
        ),
    }
)
CANONICAL_RENDERER_IDENTITY_HASH = sha256_bytes(CANONICAL_RENDERER_IDENTITY_BYTES)

_SAFE_ID_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}\Z")
_SHA256_RE = re.compile(r"[0-9a-f]{64}\Z")
_SRT_TIME_RE = re.compile(
    r"(?P<sh>\d{2}):(?P<sm>\d{2}):(?P<ss>\d{2}),(?P<sms>\d{3})"
    r" --> "
    r"(?P<eh>\d{2}):(?P<em>\d{2}):(?P<es>\d{2}),(?P<ems>\d{3})\Z"
)
_SYSTEM_LEAK_RE = re.compile(rb"(?i)(?:^|[^a-z0-9])v[12](?:[^a-z0-9]|$)")

LIMITATIONS = (
    "SHA-256 binds exact bytes and attestations; it does not authenticate a human, "
    "prove wall-clock time, or prove that the labelling UI was blind.",
    "Candidate absence is checked only at the two predeclared input roots; it cannot "
    "prove that no candidate copy existed elsewhere.",
    "Presentation bytes and renderer identity are measured inputs.  This builder does "
    "not execute a renderer or prove that the renderer produced those bytes.",
    "A party able to rewrite the complete workspace and every external audit anchor can "
    "construct a new internally consistent history; use independent custody/signing for "
    "adversarial operator threat models.",
)


@dataclass(frozen=True)
class SamplingFrameWindow:
    window_id: str
    start_frame: int
    end_frame: int
    stratum: str


@dataclass(frozen=True)
class SamplingFrame:
    schema_version: int
    episode_id: str
    normalized_audio_hash: str
    sample_rate_hz: int
    channel_count: int
    sample_width_bytes: int
    frame_count: int
    selection_algorithm: str
    selection_count: int
    eligible_windows: tuple[SamplingFrameWindow, ...]
    artifact_hash: str


@dataclass(frozen=True)
class GeneratorIdentity:
    schema_version: int
    system: Literal["v1", "v2"]
    generator_id: str
    code_hash: str
    config_hash: str
    model_identity_hash: str
    generation_protocol_id: str
    projection_protocol_id: str

    def __post_init__(self) -> None:
        if self.schema_version != 1 or isinstance(self.schema_version, bool):
            raise ValueError("generator identity schema_version must be 1")
        if self.system not in {"v1", "v2"}:
            raise ValueError("generator identity system must be v1 or v2")
        _require_safe_id("generator_id", self.generator_id)
        _require_safe_id("generation_protocol_id", self.generation_protocol_id)
        _require_safe_id("projection_protocol_id", self.projection_protocol_id)
        for field in ("code_hash", "config_hash", "model_identity_hash"):
            _require_sha256(f"generator identity {field}", getattr(self, field))

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "system": self.system,
            "generator_id": self.generator_id,
            "code_hash": self.code_hash,
            "config_hash": self.config_hash,
            "model_identity_hash": self.model_identity_hash,
            "generation_protocol_id": self.generation_protocol_id,
            "projection_protocol_id": self.projection_protocol_id,
        }

    @property
    def identity_hash(self) -> str:
        return hash_object(self.to_dict())


@dataclass(frozen=True)
class PredeclareRequest:
    workspace_root: Path
    normalized_wav_path: Path
    sampling_frame_path: Path
    planned_v1_root: Path
    planned_v2_root: Path
    study_id: str
    episode_id: str
    lineage_id: str
    benchmark_suite_hash: str
    frozen_at_utc: str
    selection_seed: str
    selection_nonce: str
    v1_generator: GeneratorIdentity
    v2_generator: GeneratorIdentity
    minimum_clip_count: int = 20
    minimum_decisive_count: int = 12
    one_sided_alpha: float = 0.05
    minimum_v2_decisive_win_rate: float = 0.65
    maximum_v2_unacceptable_rate: float = 0.05
    selection_independent_of_candidates_attestation: bool = True


@dataclass(frozen=True)
class PredeclarationResult:
    predeclaration: PairedBoundaryPredeclaration
    predeclaration_path: Path
    sampling_frame_snapshot_path: Path
    clip_paths: tuple[Path, ...]
    limitations: tuple[str, ...] = LIMITATIONS


@dataclass(frozen=True)
class CandidateClipInput:
    clip_id: str
    cue_set_relpath: str


@dataclass(frozen=True)
class CandidateInput:
    system: Literal["v1", "v2"]
    input_root: Path
    normalized_wav_path: Path
    candidate_artifact_relpath: str
    subtitle_relpath: str
    canonical_content_relpath: str
    token_sequence_relpath: str
    clip_inputs: tuple[CandidateClipInput, ...]


@dataclass(frozen=True)
class MaterializeRequest:
    workspace_root: Path
    v1: CandidateInput
    v2: CandidateInput
    mapping_secret: str
    committed_at_utc: str
    candidate_identity_hidden_attestation: bool = True


@dataclass(frozen=True)
class MaterializationResult:
    candidates: tuple[PairedBoundaryCandidate, ...]
    mapping_commitment_hash: str
    blinded_workspace: Path
    blinded_manifest_path: Path
    blinded_archive_path: Path
    private_materialization_path: Path
    limitations: tuple[str, ...] = LIMITATIONS


@dataclass(frozen=True)
class BlindWorkspaceVerification:
    study_id: str
    predeclaration_hash: str
    mapping_commitment_hash: str
    clip_count: int
    blinded_manifest_hash: str
    limitations: tuple[str, ...] = LIMITATIONS


@dataclass(frozen=True)
class SealHumanLabelsRequest:
    workspace_root: Path
    raw_human_labels_path: Path
    labels_completed_at_utc: str
    revealed_at_utc: str


@dataclass(frozen=True)
class StudySealResult:
    study: PairedBoundaryStudy
    study_path: Path
    labels_snapshot_path: Path
    limitations: tuple[str, ...] = LIMITATIONS


@dataclass(frozen=True)
class HumanLabelSealResult:
    labels_file_hash: str
    label_seal_hash: str
    labels_completed_at_utc: str
    labels_snapshot_path: Path
    seal_anchor_path: Path
    limitations: tuple[str, ...] = LIMITATIONS


@dataclass(frozen=True)
class _WavInfo:
    channels: int
    sample_width: int
    sample_rate: int
    frame_count: int


@dataclass(frozen=True)
class _SrtCue:
    cue_id: str
    start_ms: int
    end_ms: int
    text: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "cue_id": self.cue_id,
            "start_ms": self.start_ms,
            "end_ms": self.end_ms,
            "text": self.text,
        }


def _render_canonical_presentation(*, clip: PairedStudyClip, cues: Sequence[_SrtCue]) -> bytes:
    if not cues:
        raise ValueError("canonical presentation requires the complete non-empty clip cue set")
    return canonical_json_bytes(
        {
            "schema_version": 1,
            "renderer_id": CANONICAL_RENDERER_ID,
            "renderer_identity_hash": CANONICAL_RENDERER_IDENTITY_HASH,
            "clip_id": clip.clip_id,
            "start_ms": clip.start_ms,
            "end_ms": clip.end_ms,
            "audio_clip_hash": clip.audio_clip_hash,
            "cues": [item.to_dict() for item in cues],
        }
    )


def _require_safe_id(label: str, value: object) -> str:
    if not isinstance(value, str) or not _SAFE_ID_RE.fullmatch(value):
        raise ValueError(f"{label} must be a filesystem-safe opaque identifier")
    return value


def _require_sha256(label: str, value: object) -> str:
    if not isinstance(value, str) or not _SHA256_RE.fullmatch(value):
        raise ValueError(f"{label} must be lowercase SHA-256")
    return value


def _parse_utc(label: str, value: object) -> datetime:
    if not isinstance(value, str) or not value.endswith("Z"):
        raise ValueError(f"{label} must be an RFC3339 UTC timestamp ending in Z")
    try:
        parsed = datetime.fromisoformat(value[:-1] + "+00:00")
    except ValueError as exc:
        raise ValueError(f"{label} must be an RFC3339 UTC timestamp") from exc
    if parsed.utcoffset() != timezone.utc.utcoffset(parsed):
        raise ValueError(f"{label} must be UTC")
    return parsed


def _strict_object(value: object, *, required: set[str], label: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ValueError(f"{label} must be an object")
    unknown = set(value) - required
    missing = required - set(value)
    if unknown or missing:
        raise ValueError(
            f"{label} fields mismatch; missing={sorted(missing)}, unknown={sorted(unknown)}"
        )
    return value


def _strict_array(value: object, *, label: str, allow_empty: bool = False) -> list[Any]:
    if not isinstance(value, list) or (not allow_empty and not value):
        qualifier = "an array" if allow_empty else "a non-empty array"
        raise ValueError(f"{label} must be {qualifier}")
    return value


def _is_link_or_junction(path: Path) -> bool:
    if path.is_symlink():
        return True
    is_junction = getattr(path, "is_junction", None)
    return bool(is_junction and is_junction())


def _assert_no_link_ancestors(path: Path, *, stop_at: Path | None = None) -> None:
    current = path.absolute()
    stop = stop_at.absolute() if stop_at is not None else None
    while True:
        if current.exists() and _is_link_or_junction(current):
            raise ValueError(f"symlink or junction is forbidden: {current}")
        if stop is not None and current == stop:
            return
        if current.parent == current:
            return
        current = current.parent


def _resolved_non_strict(path: Path) -> Path:
    _assert_no_link_ancestors(path.absolute())
    return path.resolve(strict=False)


def _paths_overlap(left: Path, right: Path) -> bool:
    left_resolved = _resolved_non_strict(left)
    right_resolved = _resolved_non_strict(right)
    return (
        left_resolved == right_resolved
        or left_resolved in right_resolved.parents
        or right_resolved in left_resolved.parents
    )


def _assert_disjoint_study_roots(root: Path, v1_root: Path, v2_root: Path) -> None:
    named = (("workspace custody", root), ("V1 candidate", v1_root), ("V2 candidate", v2_root))
    for index, (left_label, left) in enumerate(named):
        for right_label, right in named[index + 1 :]:
            if _paths_overlap(left, right):
                raise ValueError(
                    f"{left_label} and {right_label} roots must be resolved and disjoint"
                )


def _windows_stream_names(path: Path) -> tuple[str, ...]:
    """Enumerate every NTFS stream for a file/directory; fail closed on API errors."""

    if os.name != "nt":
        return ("::$DATA",)
    import ctypes
    from ctypes import wintypes

    class Win32FindStreamData(ctypes.Structure):
        _fields_ = [
            ("StreamSize", ctypes.c_longlong),
            ("cStreamName", wintypes.WCHAR * 296),
        ]

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    find_first = kernel32.FindFirstStreamW
    find_first.argtypes = [
        wintypes.LPCWSTR,
        wintypes.DWORD,
        ctypes.POINTER(Win32FindStreamData),
        wintypes.DWORD,
    ]
    find_first.restype = wintypes.HANDLE
    find_next = kernel32.FindNextStreamW
    find_next.argtypes = [wintypes.HANDLE, ctypes.POINTER(Win32FindStreamData)]
    find_next.restype = wintypes.BOOL
    find_close = kernel32.FindClose
    find_close.argtypes = [wintypes.HANDLE]
    find_close.restype = wintypes.BOOL
    data = Win32FindStreamData()
    handle = find_first(str(path), 0, ctypes.byref(data), 0)
    invalid = wintypes.HANDLE(-1).value
    if handle == invalid:
        error = ctypes.get_last_error()
        if error == 38:  # ERROR_HANDLE_EOF: no streams on this filesystem object.
            return ()
        raise ValueError(f"cannot enumerate alternate data streams for {path}: winerror {error}")
    names: list[str] = []
    try:
        names.append(data.cStreamName)
        while find_next(handle, ctypes.byref(data)):
            names.append(data.cStreamName)
        error = ctypes.get_last_error()
        if error != 38:
            raise ValueError(
                f"cannot finish alternate data stream enumeration for {path}: winerror {error}"
            )
    finally:
        find_close(handle)
    return tuple(names)


def _assert_no_named_streams(path: Path) -> None:
    if not path.exists():
        return
    streams = _windows_stream_names(path)
    named = [item for item in streams if item not in {"", "::$DATA"}]
    if named:
        raise ValueError(f"alternate data stream (ADS) is forbidden on {path}: {named}")


def _assert_custody_no_ads(root: Path) -> None:
    if os.name != "nt":
        return
    for custody in (root / "blinded", root / "private", root / "sealed", root / "exports"):
        if not custody.exists():
            continue
        _assert_no_named_streams(custody)
        for path in custody.rglob("*"):
            _assert_no_named_streams(path)


def _safe_workspace(root: Path) -> Path:
    absolute = root.absolute()
    _assert_no_link_ancestors(absolute)
    if absolute.exists() and not absolute.is_dir():
        raise ValueError("workspace_root must be a directory")
    return absolute


def _safe_existing_file(path: Path, *, label: str) -> Path:
    absolute = path.absolute()
    _assert_no_link_ancestors(absolute)
    if not absolute.is_file():
        raise ValueError(f"{label} must be an existing regular file")
    return absolute


def _safe_relative_file(root: Path, relpath: str, *, label: str) -> Path:
    if not isinstance(relpath, str) or not relpath or "\\" in relpath or ":" in relpath:
        raise ValueError(f"{label} must be a non-empty POSIX relative path")
    relative = Path(relpath)
    if relative.is_absolute() or ".." in relative.parts:
        raise ValueError(f"{label} escapes candidate input root")
    path = (root / relative).absolute()
    try:
        path.relative_to(root.absolute())
    except ValueError as exc:
        raise ValueError(f"{label} escapes candidate input root") from exc
    return _safe_existing_file(path, label=label)


def _atomic_write_immutable(path: Path, data: bytes, *, workspace_root: Path) -> None:
    root = workspace_root.absolute()
    absolute = path.absolute()
    try:
        absolute.relative_to(root)
    except ValueError as exc:
        raise ValueError(f"output path escapes workspace: {absolute}") from exc
    _assert_no_link_ancestors(absolute.parent, stop_at=root)
    absolute.parent.mkdir(parents=True, exist_ok=True)
    _assert_no_link_ancestors(absolute.parent, stop_at=root)
    if absolute.exists():
        if _is_link_or_junction(absolute) or not absolute.is_file():
            raise ValueError(f"immutable output target is not a regular file: {absolute}")
        if absolute.read_bytes() != data:
            raise ValueError(f"immutable artifact conflict: {absolute}")
        return
    fd, temporary_name = tempfile.mkstemp(prefix=".paired-", dir=absolute.parent)
    temporary = Path(temporary_name)
    try:
        with os.fdopen(fd, "wb") as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        try:
            os.link(temporary, absolute)
        except FileExistsError:
            if (
                _is_link_or_junction(absolute)
                or not absolute.is_file()
                or absolute.read_bytes() != data
            ):
                raise ValueError(f"immutable artifact conflict: {absolute}")
    finally:
        if temporary.exists():
            temporary.unlink()


def _canonical_blinded_archive_bytes(blind_root: Path, *, allowed_files: Sequence[str]) -> bytes:
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w", compression=zipfile.ZIP_STORED, allowZip64=True) as archive:
        for relpath in sorted(allowed_files):
            if (
                not relpath
                or "\\" in relpath
                or ":" in relpath
                or Path(relpath).is_absolute()
                or ".." in Path(relpath).parts
            ):
                raise ValueError("blinded archive entry path is unsafe")
            source = _safe_relative_file(blind_root, relpath, label="blinded archive source")
            info = zipfile.ZipInfo(relpath, date_time=(1980, 1, 1, 0, 0, 0))
            info.compress_type = zipfile.ZIP_STORED
            info.create_system = 3
            info.external_attr = 0o100644 << 16
            info.flag_bits = 0x800
            archive.writestr(info, source.read_bytes())
    return output.getvalue()


def _verify_canonical_blinded_archive(
    path: Path, *, blind_root: Path, allowed_files: Sequence[str]
) -> str:
    archive_path = _safe_existing_file(path, label="canonical blinded archive")
    raw = archive_path.read_bytes()
    try:
        with zipfile.ZipFile(io.BytesIO(raw), "r") as archive:
            names = archive.namelist()
            if len(names) != len(set(names)):
                raise ValueError("canonical blinded archive contains duplicate entries")
            for name in names:
                if (
                    not name
                    or "\\" in name
                    or ":" in name
                    or Path(name).is_absolute()
                    or ".." in Path(name).parts
                ):
                    raise ValueError("canonical blinded archive contains path traversal")
            if names != sorted(allowed_files):
                raise ValueError("canonical blinded archive has extra or missing entries")
    except zipfile.BadZipFile as exc:
        raise ValueError("canonical blinded archive is not a valid ZIP") from exc
    expected = _canonical_blinded_archive_bytes(blind_root, allowed_files=allowed_files)
    if raw != expected:
        raise ValueError("blinded archive bytes are noncanonical or contain altered content")
    return sha256_bytes(raw)


def _read_exact_canonical_json(path: Path, *, label: str) -> dict[str, Any]:
    file_path = _safe_existing_file(path, label=label)
    raw = file_path.read_bytes()
    return _decode_exact_canonical_json(raw, label=label)


def _decode_exact_canonical_json(raw: bytes, *, label: str) -> dict[str, Any]:
    try:
        payload = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError(f"{label} must be UTF-8 JSON") from exc
    if not isinstance(payload, dict):
        raise ValueError(f"{label} must be a JSON object")
    if raw != canonical_json_bytes(payload):
        raise ValueError(f"{label} must use exact canonical JSON bytes")
    return payload


def _load_wav(path: Path, *, label: str) -> tuple[str, _WavInfo]:
    file_path = _safe_existing_file(path, label=label)
    digest = hashlib.sha256()
    try:
        with file_path.open("rb") as stream:
            before = os.fstat(stream.fileno())
            while chunk := stream.read(1024 * 1024):
                digest.update(chunk)
            stream.seek(0)
            with wave.open(stream, "rb") as reader:
                if reader.getcomptype() != "NONE":
                    raise ValueError(f"{label} must be uncompressed PCM WAV")
                channels = reader.getnchannels()
                sample_width = reader.getsampwidth()
                sample_rate = reader.getframerate()
                frame_count = reader.getnframes()
                expected_bytes = channels * sample_width * frame_count
                observed_bytes = 0
                frames_per_chunk = max(1, (1024 * 1024) // (channels * sample_width))
                while observed_bytes < expected_bytes:
                    pcm_chunk = reader.readframes(frames_per_chunk)
                    if not pcm_chunk:
                        break
                    observed_bytes += len(pcm_chunk)
                if observed_bytes != expected_bytes or reader.readframes(1):
                    raise ValueError(f"{label} PCM payload is truncated or exceeds frame count")
            after = os.fstat(stream.fileno())
            if (
                before.st_size != after.st_size
                or before.st_mtime_ns != after.st_mtime_ns
                or before.st_ctime_ns != after.st_ctime_ns
            ):
                raise ValueError(f"{label} changed during streaming validation")
    except (wave.Error, EOFError) as exc:
        raise ValueError(f"{label} must be a complete uncompressed PCM WAV") from exc
    if channels <= 0 or sample_width not in {1, 2, 3, 4} or sample_rate <= 0 or frame_count <= 0:
        raise ValueError(f"{label} has invalid PCM parameters")
    return digest.hexdigest(), _WavInfo(channels, sample_width, sample_rate, frame_count)


def _wav_clip_bytes(path: Path, info: _WavInfo, *, start_frame: int, end_frame: int) -> bytes:
    with wave.open(str(path), "rb") as reader:
        if (
            reader.getnchannels() != info.channels
            or reader.getsampwidth() != info.sample_width
            or reader.getframerate() != info.sample_rate
            or reader.getnframes() != info.frame_count
            or reader.getcomptype() != "NONE"
        ):
            raise ValueError("normalized WAV topology changed before clip extraction")
        reader.setpos(start_frame)
        pcm = reader.readframes(end_frame - start_frame)
    expected = (end_frame - start_frame) * info.channels * info.sample_width
    if len(pcm) != expected:
        raise ValueError("normalized WAV selected clip is truncated")
    output = io.BytesIO()
    with wave.open(output, "wb") as writer:
        writer.setnchannels(info.channels)
        writer.setsampwidth(info.sample_width)
        writer.setframerate(info.sample_rate)
        writer.setcomptype("NONE", "not compressed")
        writer.writeframes(pcm)
    return output.getvalue()


def _load_sampling_frame(path: Path) -> SamplingFrame:
    raw = _safe_existing_file(path, label="sampling frame").read_bytes()
    try:
        decoded = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError("sampling frame must be UTF-8 JSON") from exc
    payload = _strict_object(
        decoded,
        required={
            "schema_version",
            "episode_id",
            "normalized_audio_hash",
            "sample_rate_hz",
            "channel_count",
            "sample_width_bytes",
            "frame_count",
            "selection_algorithm",
            "selection_count",
            "eligible_windows",
        },
        label="sampling frame",
    )
    if raw != canonical_json_bytes(payload):
        raise ValueError("sampling frame must use exact canonical JSON bytes")
    if payload["schema_version"] != SAMPLING_FRAME_SCHEMA_VERSION:
        raise ValueError("unsupported sampling frame schema_version")
    _require_safe_id("sampling frame episode_id", payload["episode_id"])
    _require_sha256("sampling frame normalized_audio_hash", payload["normalized_audio_hash"])
    for field in (
        "sample_rate_hz",
        "channel_count",
        "sample_width_bytes",
        "frame_count",
        "selection_count",
    ):
        value = payload[field]
        if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
            raise ValueError(f"sampling frame {field} must be a positive integer")
    if payload["selection_algorithm"] != SELECTION_ALGORITHM:
        raise ValueError("unsupported sampling frame selection_algorithm")
    windows: list[SamplingFrameWindow] = []
    seen: set[str] = set()
    for item in _strict_array(payload["eligible_windows"], label="sampling frame windows"):
        value = _strict_object(
            item,
            required={"window_id", "start_frame", "end_frame", "stratum"},
            label="sampling frame window",
        )
        window_id = _require_safe_id("sampling frame window_id", value["window_id"])
        if window_id in seen:
            raise ValueError("sampling frame window_ids must be unique")
        seen.add(window_id)
        _require_safe_id("sampling frame stratum", value["stratum"])
        start_frame = value["start_frame"]
        end_frame = value["end_frame"]
        if (
            isinstance(start_frame, bool)
            or not isinstance(start_frame, int)
            or isinstance(end_frame, bool)
            or not isinstance(end_frame, int)
            or start_frame < 0
            or end_frame <= start_frame
            or end_frame > payload["frame_count"]
        ):
            raise ValueError("sampling frame window has invalid frame bounds")
        windows.append(
            SamplingFrameWindow(
                window_id=window_id,
                start_frame=start_frame,
                end_frame=end_frame,
                stratum=value["stratum"],
            )
        )
    if payload["selection_count"] > len(windows):
        raise ValueError("sampling frame selection_count exceeds eligible window count")
    return SamplingFrame(
        schema_version=payload["schema_version"],
        episode_id=payload["episode_id"],
        normalized_audio_hash=payload["normalized_audio_hash"],
        sample_rate_hz=payload["sample_rate_hz"],
        channel_count=payload["channel_count"],
        sample_width_bytes=payload["sample_width_bytes"],
        frame_count=payload["frame_count"],
        selection_algorithm=payload["selection_algorithm"],
        selection_count=payload["selection_count"],
        eligible_windows=tuple(windows),
        artifact_hash=sha256_bytes(raw),
    )


def _select_windows(frame: SamplingFrame, *, seed: str) -> tuple[SamplingFrameWindow, ...]:
    strata: dict[str, list[SamplingFrameWindow]] = {}
    for window in frame.eligible_windows:
        strata.setdefault(window.stratum, []).append(window)
    for stratum, windows in strata.items():
        windows.sort(
            key=lambda item: hash_object(
                {
                    "algorithm": SELECTION_ALGORITHM,
                    "seed": seed,
                    "stratum": stratum,
                    "window": {
                        "window_id": item.window_id,
                        "start_frame": item.start_frame,
                        "end_frame": item.end_frame,
                    },
                }
            )
        )
    ordered_strata = sorted(
        strata,
        key=lambda item: hash_object(
            {"algorithm": SELECTION_ALGORITHM, "seed": seed, "stratum": item}
        ),
    )
    positions = {item: 0 for item in ordered_strata}
    selected: list[SamplingFrameWindow] = []
    while len(selected) < frame.selection_count:
        progressed = False
        for stratum in ordered_strata:
            windows = strata[stratum]
            while positions[stratum] < len(windows):
                candidate = windows[positions[stratum]]
                positions[stratum] += 1
                overlaps = any(
                    candidate.start_frame < current.end_frame
                    and current.start_frame < candidate.end_frame
                    for current in selected
                )
                if overlaps:
                    continue
                selected.append(candidate)
                progressed = True
                break
            if len(selected) == frame.selection_count:
                break
        if not progressed:
            raise ValueError("sampling frame cannot yield enough non-overlapping windows")
    return tuple(
        sorted(selected, key=lambda item: (item.start_frame, item.end_frame, item.window_id))
    )


def _predecl_anchor_path(root: Path) -> Path:
    return root / "predeclaration" / "anchor.json"


def _materialization_anchor_path(root: Path) -> Path:
    return root / "private" / "materialization-anchor.json"


def _seal_anchor_path(root: Path) -> Path:
    return root / "sealed" / "label-seal-anchor.json"


def _reveal_anchor_path(root: Path) -> Path:
    return root / "sealed" / "reveal-anchor.json"


def _path_commitment(path: Path) -> str:
    return hash_object({"resolved_absolute_path": str(path.absolute())})


def _predeclaration_dict(value: PairedBoundaryPredeclaration) -> dict[str, Any]:
    return {**value.hash_payload(), "predeclaration_hash": value.predeclaration_hash}


def _candidate_dict(value: PairedBoundaryCandidate) -> dict[str, Any]:
    return {**value.hash_payload(), "candidate_record_hash": value.candidate_record_hash}


def _load_predecl_state(root: Path) -> tuple[dict[str, Any], PairedBoundaryPredeclaration]:
    anchor = _read_exact_canonical_json(_predecl_anchor_path(root), label="predeclaration anchor")
    anchor = _strict_object(
        anchor,
        required={
            "schema_version",
            "phase",
            "predeclaration_hash",
            "predeclaration_relpath",
            "sampling_frame_hash",
            "sampling_frame_relpath",
            "normalized_audio_hash",
            "normalized_audio_source_path",
            "planned_candidate_root_commitments",
            "selection_secret_record_hash",
            "clip_artifacts",
            "episode_id",
            "lineage_id",
            "benchmark_suite_hash",
            "generator_identities",
        },
        label="predeclaration anchor",
    )
    if anchor["schema_version"] != 1 or anchor["phase"] != "predeclared":
        raise ValueError("invalid predeclaration anchor")
    predecl_path = _safe_relative_file(
        root,
        anchor["predeclaration_relpath"],
        label="predeclaration artifact",
    )
    payload = _read_exact_canonical_json(predecl_path, label="predeclaration artifact")
    loaded = _predeclaration_from_dict(payload)
    if loaded.predeclaration_hash != anchor["predeclaration_hash"]:
        raise ValueError("predeclaration anchor hash mismatch")
    sampling_path = _safe_relative_file(
        root,
        anchor["sampling_frame_relpath"],
        label="sampling frame snapshot",
    )
    if sha256_bytes(sampling_path.read_bytes()) != anchor["sampling_frame_hash"]:
        raise ValueError("sampling frame snapshot hash mismatch")
    _load_selection_secret(root, anchor=anchor, declaration=loaded)
    return anchor, loaded


def _load_selection_secret(
    root: Path,
    *,
    anchor: Mapping[str, Any],
    declaration: PairedBoundaryPredeclaration,
) -> dict[str, Any]:
    record_hash = anchor["selection_secret_record_hash"]
    _require_sha256("selection secret record hash", record_hash)
    path = _safe_relative_file(
        root,
        f"private/selection-secrets/{record_hash}.json",
        label="selection secret record",
    )
    payload = _read_exact_canonical_json(path, label="selection secret record")
    payload = _strict_object(
        payload,
        required={
            "schema_version",
            "study_id",
            "sampling_frame_hash",
            "selection_seed",
            "selection_nonce",
            "selection_seed_commitment",
        },
        label="selection secret record",
    )
    if hash_object(payload) != record_hash:
        raise ValueError("selection secret record content address mismatch")
    expected_commitment = hash_object(
        {
            "schema_version": 1,
            "sampling_frame_hash": anchor["sampling_frame_hash"],
            "selection_seed": payload["selection_seed"],
            "selection_nonce": payload["selection_nonce"],
        }
    )
    if (
        payload["schema_version"] != 1
        or payload["study_id"] != declaration.study_id
        or payload["sampling_frame_hash"] != anchor["sampling_frame_hash"]
        or payload["selection_seed_commitment"] != expected_commitment
        or declaration.selection_seed_commitment != expected_commitment
    ):
        raise ValueError("selection secret record lineage or commitment mismatch")
    return payload


def _predeclaration_from_dict(payload: Mapping[str, Any]) -> PairedBoundaryPredeclaration:
    value = _strict_object(
        dict(payload),
        required={
            "schema_version",
            "study_id",
            "frozen_at_utc",
            "selection_method",
            "selection_independent_of_candidates",
            "sampling_frame_hash",
            "selection_seed_commitment",
            "minimum_clip_count",
            "minimum_decisive_count",
            "one_sided_alpha",
            "minimum_v2_decisive_win_rate",
            "maximum_v2_unacceptable_rate",
            "clips",
            "predeclaration_hash",
        },
        label="predeclaration artifact",
    )
    clips = tuple(
        PairedStudyClip(
            **_strict_object(
                item,
                required={
                    "clip_id",
                    "start_ms",
                    "end_ms",
                    "normalized_audio_hash",
                    "audio_clip_hash",
                    "selection_stratum",
                },
                label="predeclaration clip",
            )
        )
        for item in _strict_array(value["clips"], label="predeclaration clips")
    )
    return PairedBoundaryPredeclaration(
        **{key: item for key, item in value.items() if key != "clips"},
        clips=clips,
    )


def predeclare(request: PredeclareRequest) -> PredeclarationResult:
    """Freeze a candidate-independent frame and exact PCM clips before candidates exist."""

    root = _safe_workspace(request.workspace_root)
    _assert_disjoint_study_roots(root, request.planned_v1_root, request.planned_v2_root)
    _assert_custody_no_ads(root)
    _require_safe_id("study_id", request.study_id)
    _require_safe_id("episode_id", request.episode_id)
    _require_safe_id("lineage_id", request.lineage_id)
    _require_sha256("benchmark_suite_hash", request.benchmark_suite_hash)
    if request.v1_generator.system != "v1" or request.v2_generator.system != "v2":
        raise ValueError("predeclaration requires frozen V1 and V2 generator identities")
    generator_identities = [
        request.v1_generator.to_dict(),
        request.v2_generator.to_dict(),
    ]
    _parse_utc("frozen_at_utc", request.frozen_at_utc)
    if not request.selection_seed or not request.selection_nonce:
        raise ValueError("selection seed and nonce must be non-empty secrets")
    if not request.selection_independent_of_candidates_attestation:
        raise ValueError("candidate-independent selection attestation is required")

    anchor_path = _predecl_anchor_path(root)
    if anchor_path.exists():
        anchor, declaration = _load_predecl_state(root)
        expected_roots = [
            _path_commitment(request.planned_v1_root),
            _path_commitment(request.planned_v2_root),
        ]
        expected_commitment = hash_object(
            {
                "schema_version": 1,
                "sampling_frame_hash": anchor["sampling_frame_hash"],
                "selection_seed": request.selection_seed,
                "selection_nonce": request.selection_nonce,
            }
        )
        if (
            declaration.study_id != request.study_id
            or anchor["episode_id"] != request.episode_id
            or anchor["lineage_id"] != request.lineage_id
            or anchor["benchmark_suite_hash"] != request.benchmark_suite_hash
            or anchor["generator_identities"] != generator_identities
            or declaration.selection_seed_commitment != expected_commitment
            or anchor["planned_candidate_root_commitments"] != expected_roots
        ):
            raise ValueError("predeclare replay conflicts with immutable predeclaration")
        current_wav_hash, _ = _load_wav(request.normalized_wav_path, label="normalized audio")
        current_frame = _safe_existing_file(
            request.sampling_frame_path, label="sampling frame"
        ).read_bytes()
        if (
            current_wav_hash != anchor["normalized_audio_hash"]
            or sha256_bytes(current_frame) != anchor["sampling_frame_hash"]
        ):
            raise ValueError("predeclare replay input bytes differ from immutable snapshots")
        clip_paths = tuple(
            _safe_relative_file(root, item["relpath"], label="predeclared audio clip")
            for item in anchor["clip_artifacts"]
        )
        return PredeclarationResult(
            predeclaration=declaration,
            predeclaration_path=_safe_relative_file(
                root, anchor["predeclaration_relpath"], label="predeclaration artifact"
            ),
            sampling_frame_snapshot_path=_safe_relative_file(
                root, anchor["sampling_frame_relpath"], label="sampling frame snapshot"
            ),
            clip_paths=clip_paths,
        )

    for label, path in (
        ("planned V1 candidate root", request.planned_v1_root),
        ("planned V2 candidate root", request.planned_v2_root),
    ):
        _assert_no_link_ancestors(path.absolute())
        if path.exists():
            raise ValueError(f"{label} already exists; candidates must not exist before predeclare")

    audio_hash, wav_info = _load_wav(request.normalized_wav_path, label="normalized audio")
    frame_path = _safe_existing_file(request.sampling_frame_path, label="sampling frame")
    frame_raw = frame_path.read_bytes()
    frame = _load_sampling_frame(frame_path)
    if frame.episode_id != request.episode_id:
        raise ValueError("sampling frame episode_id mismatch")
    if frame.normalized_audio_hash != audio_hash:
        raise ValueError("sampling frame normalized audio hash mismatch")
    if (
        frame.sample_rate_hz != wav_info.sample_rate
        or frame.channel_count != wav_info.channels
        or frame.sample_width_bytes != wav_info.sample_width
        or frame.frame_count != wav_info.frame_count
    ):
        raise ValueError("sampling frame PCM parameters mismatch normalized WAV")
    if frame.selection_count < request.minimum_clip_count:
        raise ValueError("sampling frame selection_count is below minimum_clip_count")

    selected = _select_windows(frame, seed=request.selection_seed)
    clips: list[PairedStudyClip] = []
    clip_artifacts: list[dict[str, Any]] = []
    clip_paths: list[Path] = []
    for index, window in enumerate(selected, start=1):
        if (
            window.start_frame * 1000 % wav_info.sample_rate
            or window.end_frame * 1000 % wav_info.sample_rate
        ):
            raise ValueError("selected frame bounds must map exactly to integer milliseconds")
        clip_id = f"clip-{index:04d}"
        clip_bytes = _wav_clip_bytes(
            request.normalized_wav_path,
            wav_info,
            start_frame=window.start_frame,
            end_frame=window.end_frame,
        )
        clip_hash = sha256_bytes(clip_bytes)
        relpath = f"predeclaration/clips/{clip_id}.{clip_hash}.wav"
        clip_path = root / relpath
        _atomic_write_immutable(clip_path, clip_bytes, workspace_root=root)
        clips.append(
            PairedStudyClip(
                clip_id=clip_id,
                start_ms=window.start_frame * 1000 // wav_info.sample_rate,
                end_ms=window.end_frame * 1000 // wav_info.sample_rate,
                normalized_audio_hash=audio_hash,
                audio_clip_hash=clip_hash,
                selection_stratum=window.stratum,
            )
        )
        clip_artifacts.append(
            {
                "clip_id": clip_id,
                "source_window_id": window.window_id,
                "start_frame": window.start_frame,
                "end_frame": window.end_frame,
                "audio_clip_hash": clip_hash,
                "relpath": relpath,
            }
        )
        clip_paths.append(clip_path)

    post_clip_audio_hash, post_clip_info = _load_wav(
        request.normalized_wav_path, label="normalized audio"
    )
    if post_clip_audio_hash != audio_hash or post_clip_info != wav_info:
        raise ValueError("normalized audio changed during frame-exact clip extraction")

    seed_commitment = hash_object(
        {
            "schema_version": 1,
            "sampling_frame_hash": frame.artifact_hash,
            "selection_seed": request.selection_seed,
            "selection_nonce": request.selection_nonce,
        }
    )
    declaration_payload = {
        "schema_version": 1,
        "study_id": request.study_id,
        "frozen_at_utc": request.frozen_at_utc,
        "selection_method": SELECTION_METHOD,
        "selection_independent_of_candidates": True,
        "sampling_frame_hash": frame.artifact_hash,
        "selection_seed_commitment": seed_commitment,
        "minimum_clip_count": request.minimum_clip_count,
        "minimum_decisive_count": request.minimum_decisive_count,
        "one_sided_alpha": request.one_sided_alpha,
        "minimum_v2_decisive_win_rate": request.minimum_v2_decisive_win_rate,
        "maximum_v2_unacceptable_rate": request.maximum_v2_unacceptable_rate,
        "clips": [clip.to_dict() for clip in clips],
    }
    declaration_payload["predeclaration_hash"] = paired_boundary_predeclaration_hash(
        declaration_payload
    )
    declaration = _predeclaration_from_dict(declaration_payload)
    declaration_relpath = f"predeclaration/records/{declaration.predeclaration_hash}.json"
    sampling_relpath = f"predeclaration/sampling-frames/{frame.artifact_hash}.json"
    secret_payload = {
        "schema_version": 1,
        "study_id": request.study_id,
        "sampling_frame_hash": frame.artifact_hash,
        "selection_seed": request.selection_seed,
        "selection_nonce": request.selection_nonce,
        "selection_seed_commitment": seed_commitment,
    }
    secret_hash = hash_object(secret_payload)
    _atomic_write_immutable(
        root / declaration_relpath,
        canonical_json_bytes(declaration_payload),
        workspace_root=root,
    )
    _atomic_write_immutable(root / sampling_relpath, frame_raw, workspace_root=root)
    _atomic_write_immutable(
        root / f"private/selection-secrets/{secret_hash}.json",
        canonical_json_bytes(secret_payload),
        workspace_root=root,
    )
    anchor = {
        "schema_version": 1,
        "phase": "predeclared",
        "predeclaration_hash": declaration.predeclaration_hash,
        "predeclaration_relpath": declaration_relpath,
        "sampling_frame_hash": frame.artifact_hash,
        "sampling_frame_relpath": sampling_relpath,
        "normalized_audio_hash": audio_hash,
        "normalized_audio_source_path": str(request.normalized_wav_path.absolute()),
        "planned_candidate_root_commitments": [
            _path_commitment(request.planned_v1_root),
            _path_commitment(request.planned_v2_root),
        ],
        "selection_secret_record_hash": secret_hash,
        "clip_artifacts": clip_artifacts,
        "episode_id": request.episode_id,
        "lineage_id": request.lineage_id,
        "benchmark_suite_hash": request.benchmark_suite_hash,
        "generator_identities": generator_identities,
    }
    _atomic_write_immutable(anchor_path, canonical_json_bytes(anchor), workspace_root=root)
    return PredeclarationResult(
        predeclaration=declaration,
        predeclaration_path=root / declaration_relpath,
        sampling_frame_snapshot_path=root / sampling_relpath,
        clip_paths=tuple(clip_paths),
    )


def _timestamp_ms(match: re.Match[str], prefix: str) -> int:
    hours = int(match.group(prefix + "h"))
    minutes = int(match.group(prefix + "m"))
    seconds = int(match.group(prefix + "s"))
    millis = int(match.group(prefix + "ms"))
    if minutes >= 60 or seconds >= 60:
        raise ValueError("SRT timestamp component is out of range")
    return ((hours * 60 + minutes) * 60 + seconds) * 1000 + millis


def _parse_srt(raw: bytes) -> tuple[_SrtCue, ...]:
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise ValueError("candidate subtitle must be UTF-8 SRT") from exc
    if text.startswith("\ufeff"):
        raise ValueError("candidate subtitle must not contain a UTF-8 BOM")
    normalised = text.replace("\r\n", "\n")
    blocks = [item for item in normalised.strip().split("\n\n") if item]
    cues: list[_SrtCue] = []
    previous_end = -1
    for expected_index, block in enumerate(blocks, start=1):
        lines = block.split("\n")
        if len(lines) < 3 or lines[0] != str(expected_index):
            raise ValueError("candidate subtitle requires contiguous numeric SRT cue IDs")
        match = _SRT_TIME_RE.fullmatch(lines[1])
        if match is None:
            raise ValueError("candidate subtitle contains an invalid SRT timestamp")
        start_ms = _timestamp_ms(match, "s")
        end_ms = _timestamp_ms(match, "e")
        cue_text = "".join(lines[2:])
        if not cue_text or end_ms <= start_ms or start_ms < previous_end:
            raise ValueError(
                "candidate subtitle cues must be non-empty, ordered, and non-overlapping"
            )
        cues.append(_SrtCue(str(expected_index), start_ms, end_ms, cue_text))
        previous_end = end_ms
    if not cues:
        raise ValueError("candidate subtitle requires at least one cue")
    return tuple(cues)


def _load_canonical_and_tokens(
    root: Path, candidate: CandidateInput
) -> tuple[str, str, str, bytes, bytes]:
    canonical_path = _safe_relative_file(
        root, candidate.canonical_content_relpath, label="canonical content artifact"
    )
    canonical_raw = canonical_path.read_bytes()
    canonical_payload = _decode_exact_canonical_json(
        canonical_raw, label="canonical content artifact"
    )
    canonical_payload = _strict_object(
        canonical_payload,
        required={"schema_version", "content"},
        label="canonical content artifact",
    )
    if canonical_payload["schema_version"] != 1:
        raise ValueError("unsupported canonical content schema_version")
    content = canonical_payload["content"]
    if not isinstance(content, str) or not content:
        raise ValueError("canonical content must be non-empty text")

    token_path = _safe_relative_file(
        root, candidate.token_sequence_relpath, label="canonical token artifact"
    )
    token_raw = token_path.read_bytes()
    token_payload = _decode_exact_canonical_json(token_raw, label="canonical token artifact")
    token_payload = _strict_object(
        token_payload,
        required={"schema_version", "tokens"},
        label="canonical token artifact",
    )
    if token_payload["schema_version"] != 1:
        raise ValueError("unsupported canonical token schema_version")
    pairs: list[list[str]] = []
    seen: set[str] = set()
    for raw_token in _strict_array(token_payload["tokens"], label="canonical tokens"):
        token = _strict_object(
            raw_token,
            required={"token_id", "text"},
            label="canonical token",
        )
        token_id = token["token_id"]
        token_text = token["text"]
        if not isinstance(token_id, str) or not token_id or token_id in seen:
            raise ValueError("canonical token IDs must be non-empty and unique")
        if not isinstance(token_text, str) or not token_text:
            raise ValueError("canonical token text must be non-empty")
        seen.add(token_id)
        pairs.append([token_id, token_text])
    if "".join(item[1] for item in pairs) != content:
        raise ValueError("canonical token sequence does not exactly reproduce canonical content")
    return (
        content,
        sha256_bytes(content.encode("utf-8")),
        hash_object(pairs),
        canonical_raw,
        token_raw,
    )


def _load_cue_set(path: Path, *, clip: PairedStudyClip, expected: Sequence[_SrtCue]) -> bytes:
    raw = _safe_existing_file(path, label="candidate clip cue set").read_bytes()
    try:
        decoded = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError("candidate clip cue set must be UTF-8 JSON") from exc
    payload = _strict_object(
        decoded,
        required={"schema_version", "clip_id", "cues"},
        label="candidate clip cue set",
    )
    if raw != canonical_json_bytes(payload):
        raise ValueError("candidate clip cue set must use exact canonical JSON bytes")
    if payload["schema_version"] != 1 or payload["clip_id"] != clip.clip_id:
        raise ValueError("candidate clip cue set identity mismatch")
    actual = _strict_array(payload["cues"], label="candidate clip cues")
    expected_payload = [cue.to_dict() for cue in expected]
    for item in actual:
        _strict_object(
            item,
            required={"cue_id", "start_ms", "end_ms", "text"},
            label="candidate clip cue",
        )
    if actual != expected_payload:
        raise ValueError(
            "candidate clip cue set omits or changes cues from the full SRT projection"
        )
    return raw


def _generator_identity_from_dict(value: object) -> GeneratorIdentity:
    payload = _strict_object(
        value,
        required={
            "schema_version",
            "system",
            "generator_id",
            "code_hash",
            "config_hash",
            "model_identity_hash",
            "generation_protocol_id",
            "projection_protocol_id",
        },
        label="generator identity",
    )
    return GeneratorIdentity(**payload)


def _load_candidate_provenance(
    raw: bytes,
    *,
    expected_generator: GeneratorIdentity,
    normalized_audio_hash: str,
    canonical_content_hash: str,
    token_sequence_hash: str,
    subtitle_bytes_hash: str,
) -> dict[str, Any]:
    payload = _strict_object(
        _decode_exact_canonical_json(raw, label="candidate generation provenance"),
        required={
            "schema_version",
            "system",
            "generator_identity",
            "generator_identity_hash",
            "generation_id",
            "projection_id",
            "generated_at_utc",
            "normalized_audio_hash",
            "canonical_content_hash",
            "token_sequence_hash",
            "subtitle_bytes_hash",
            "provenance_record_hash",
        },
        label="candidate generation provenance",
    )
    generator = _generator_identity_from_dict(payload["generator_identity"])
    record_payload = dict(payload)
    record_hash = record_payload.pop("provenance_record_hash")
    _require_sha256("candidate provenance record hash", record_hash)
    if hash_object(record_payload) != record_hash:
        raise ValueError("candidate generation provenance record hash mismatch")
    if (
        generator != expected_generator
        or payload["generator_identity_hash"] != generator.identity_hash
    ):
        raise ValueError("candidate generator provenance differs from frozen identity")
    if payload["system"] != expected_generator.system:
        raise ValueError("candidate system differs from frozen generator slot")
    _require_safe_id("candidate generation_id", payload["generation_id"])
    _require_safe_id("candidate projection_id", payload["projection_id"])
    _parse_utc("candidate generated_at_utc", payload["generated_at_utc"])
    expected_hashes = {
        "normalized_audio_hash": normalized_audio_hash,
        "canonical_content_hash": canonical_content_hash,
        "token_sequence_hash": token_sequence_hash,
        "subtitle_bytes_hash": subtitle_bytes_hash,
    }
    for field, expected in expected_hashes.items():
        _require_sha256(f"candidate provenance {field}", payload[field])
        if payload[field] != expected:
            raise ValueError(f"candidate provenance {field} mismatch")
    return payload


def _reject_identity_leak(
    data: bytes,
    *,
    forbidden_values: Sequence[str],
    label: str,
    forbid_system_markers: bool = True,
) -> None:
    lowered = data.lower()
    if forbid_system_markers and _SYSTEM_LEAK_RE.search(data):
        raise ValueError(f"{label} leaks V1/V2 system identity")
    for value in forbidden_values:
        encoded = value.encode("utf-8", errors="ignore").lower()
        if encoded and len(encoded) >= 3 and encoded in lowered:
            raise ValueError(f"{label} leaks candidate identity or source path")


def _candidate_from_input(
    candidate: CandidateInput,
    *,
    root: Path,
    declaration: PairedBoundaryPredeclaration,
    generator_identity: GeneratorIdentity,
    normalized_audio_hash: str,
    forbidden_values: Sequence[str],
) -> tuple[PairedBoundaryCandidate, dict[str, bytes], dict[str, bytes], dict[str, bytes]]:
    candidate_root = candidate.input_root.absolute()
    _assert_no_link_ancestors(candidate_root)
    if not candidate_root.is_dir():
        raise ValueError(f"{candidate.system} candidate input root must exist")
    if candidate.system not in {"v1", "v2"}:
        raise ValueError("candidate system must be v1 or v2")
    if candidate.system != generator_identity.system:
        raise ValueError("candidate input system differs from frozen generator slot")
    audio_hash, _ = _load_wav(candidate.normalized_wav_path, label="candidate normalized audio")
    if audio_hash != normalized_audio_hash:
        raise ValueError("candidate normalized audio differs from predeclared normalized WAV")

    content, canonical_hash, token_hash, canonical_raw, token_raw = _load_canonical_and_tokens(
        candidate_root, candidate
    )
    candidate_artifact = _safe_relative_file(
        candidate_root, candidate.candidate_artifact_relpath, label="candidate artifact"
    ).read_bytes()
    subtitle_raw = _safe_relative_file(
        candidate_root, candidate.subtitle_relpath, label="candidate subtitle"
    ).read_bytes()
    if not candidate_artifact:
        raise ValueError("candidate artifact must be non-empty")
    provenance = _load_candidate_provenance(
        candidate_artifact,
        expected_generator=generator_identity,
        normalized_audio_hash=normalized_audio_hash,
        canonical_content_hash=canonical_hash,
        token_sequence_hash=token_hash,
        subtitle_bytes_hash=sha256_bytes(subtitle_raw),
    )
    generated = _parse_utc("candidate generated_at_utc", provenance["generated_at_utc"])
    frozen = _parse_utc("predeclaration frozen_at_utc", declaration.frozen_at_utc)
    if generated <= frozen:
        raise ValueError("candidate must be generated after predeclaration freeze")
    cues = _parse_srt(subtitle_raw)
    if "".join(item.text for item in cues) != content:
        raise ValueError("candidate subtitle does not preserve exact canonical content")
    clip_inputs = tuple(candidate.clip_inputs)
    if tuple(item.clip_id for item in clip_inputs) != tuple(
        item.clip_id for item in declaration.clips
    ):
        raise ValueError("candidate clip inputs must cover every predeclared clip in exact order")

    presentations: list[PairedClipPresentation] = []
    presentation_bytes: dict[str, bytes] = {}
    cue_bytes: dict[str, bytes] = {}
    for clip, clip_input in zip(declaration.clips, clip_inputs, strict=True):
        expected_cues = tuple(
            cue for cue in cues if cue.end_ms > clip.start_ms and cue.start_ms < clip.end_ms
        )
        if not expected_cues:
            raise ValueError("predeclared clip has no complete candidate cue sequence")
        cue_path = _safe_relative_file(
            candidate_root, clip_input.cue_set_relpath, label="candidate clip cue set"
        )
        cues_raw = _load_cue_set(cue_path, clip=clip, expected=expected_cues)
        presentation = _render_canonical_presentation(clip=clip, cues=expected_cues)
        _reject_identity_leak(
            presentation,
            forbidden_values=forbidden_values,
            label="canonical presentation bytes",
            forbid_system_markers=False,
        )
        presentation_hash = sha256_bytes(presentation)
        cue_set_hash = hash_object([item.to_dict() for item in expected_cues])
        presentations.append(
            PairedClipPresentation(
                clip_id=clip.clip_id,
                presentation_artifact_hash=presentation_hash,
                cue_set_hash=cue_set_hash,
                cue_count=len(expected_cues),
            )
        )
        presentation_bytes[clip.clip_id] = presentation
        cue_bytes[clip.clip_id] = cues_raw

    payload = {
        "system": provenance["system"],
        "candidate_id": provenance["projection_id"],
        "generation_id": provenance["generation_id"],
        "canonical_content_hash": canonical_hash,
        "token_sequence_hash": token_hash,
        "candidate_artifact_hash": sha256_bytes(candidate_artifact),
        "subtitle_bytes_hash": sha256_bytes(subtitle_raw),
        "renderer_identity_hash": CANONICAL_RENDERER_IDENTITY_HASH,
        "normalized_audio_hash": normalized_audio_hash,
        "predeclaration_hash": declaration.predeclaration_hash,
        "generated_at_utc": provenance["generated_at_utc"],
        "clip_presentations": [item.to_dict() for item in presentations],
    }
    payload["candidate_record_hash"] = paired_boundary_candidate_record_hash(payload)
    record = PairedBoundaryCandidate(
        **{key: value for key, value in payload.items() if key != "clip_presentations"},
        clip_presentations=tuple(presentations),
    )
    source_bytes = {
        "candidate_artifact": candidate_artifact,
        "subtitle": subtitle_raw,
        "renderer_identity": CANONICAL_RENDERER_IDENTITY_BYTES,
        "canonical_content": canonical_raw,
        "token_sequence": token_raw,
    }
    source_bytes.update(
        {f"presentation.{clip_id}": data for clip_id, data in presentation_bytes.items()}
    )
    source_bytes.update({f"cues.{clip_id}": data for clip_id, data in cue_bytes.items()})
    return record, source_bytes, presentation_bytes, cue_bytes


def _mapping_entries(
    *,
    declaration: PairedBoundaryPredeclaration,
    v1: PairedBoundaryCandidate,
    v2: PairedBoundaryCandidate,
    secret: str,
) -> tuple[PairedBoundaryMappingEntry, ...]:
    if not secret:
        raise ValueError("mapping_secret must be non-empty")
    ranked = sorted(
        (clip.clip_id for clip in declaration.clips),
        key=lambda clip_id: hash_object(
            {
                "randomization_method": RANDOMIZATION_METHOD,
                "mapping_secret": secret,
                "clip_id": clip_id,
            }
        ),
    )
    v2_a = set(ranked[: (len(ranked) + 1) // 2])
    entries: list[PairedBoundaryMappingEntry] = []
    for clip in declaration.clips:
        a, b = (v2, v1) if clip.clip_id in v2_a else (v1, v2)
        nonce = hash_object(
            {
                "randomization_method": RANDOMIZATION_METHOD,
                "mapping_secret": secret,
                "clip_id": clip.clip_id,
                "predeclaration_hash": declaration.predeclaration_hash,
            }
        )
        entries.append(
            PairedBoundaryMappingEntry(
                clip_id=clip.clip_id,
                a_candidate_record_hash=a.candidate_record_hash,
                b_candidate_record_hash=b.candidate_record_hash,
                nonce=nonce,
            )
        )
    return tuple(entries)


def materialize_candidates_and_commit_mapping(
    request: MaterializeRequest,
) -> MaterializationResult:
    """Snapshot two candidates and create an opaque A/B workspace plus hidden mapping."""

    root = _safe_workspace(request.workspace_root)
    _assert_disjoint_study_roots(root, request.v1.input_root, request.v2.input_root)
    _assert_custody_no_ads(root)
    anchor, declaration = _load_predecl_state(root)
    if request.v1.system != "v1" or request.v2.system != "v2":
        raise ValueError("materialization requires explicit V1 and V2 inputs")
    if not request.candidate_identity_hidden_attestation:
        raise ValueError("candidate identity hidden attestation is required")
    committed = _parse_utc("committed_at_utc", request.committed_at_utc)
    expected_roots = [
        _path_commitment(request.v1.input_root),
        _path_commitment(request.v2.input_root),
    ]
    if anchor["planned_candidate_root_commitments"] != expected_roots:
        raise ValueError("candidate roots differ from predeclared future roots")
    frozen_generators = tuple(
        _generator_identity_from_dict(item) for item in anchor["generator_identities"]
    )
    if tuple(item.system for item in frozen_generators) != ("v1", "v2"):
        raise ValueError("predeclaration generator slots are invalid")
    v1_generator, v2_generator = frozen_generators

    materialization_anchor = _materialization_anchor_path(root)
    if materialization_anchor.exists():
        state, candidates, mapping_entries, stored_mapping_secret = _load_materialization_state(
            root
        )
        if (
            state["committed_at_utc"] != request.committed_at_utc
            or stored_mapping_secret != request.mapping_secret
        ):
            raise ValueError("materialization replay conflicts with immutable phase")
        replay_forbidden = (
            v1_generator.generator_id,
            v2_generator.generator_id,
            str(request.v1.input_root.absolute()),
            str(request.v2.input_root.absolute()),
        )
        replay_v1, _, _, _ = _candidate_from_input(
            request.v1,
            root=root,
            declaration=declaration,
            generator_identity=v1_generator,
            normalized_audio_hash=anchor["normalized_audio_hash"],
            forbidden_values=replay_forbidden,
        )
        replay_v2, _, _, _ = _candidate_from_input(
            request.v2,
            root=root,
            declaration=declaration,
            generator_identity=v2_generator,
            normalized_audio_hash=anchor["normalized_audio_hash"],
            forbidden_values=replay_forbidden,
        )
        if tuple(item.candidate_record_hash for item in candidates) != (
            replay_v1.candidate_record_hash,
            replay_v2.candidate_record_hash,
        ):
            raise ValueError("materialization replay candidate bytes have drifted")
        if mapping_entries != _mapping_entries(
            declaration=declaration,
            v1=replay_v1,
            v2=replay_v2,
            secret=request.mapping_secret,
        ):
            raise ValueError("materialization replay mapping differs from immutable phase")
        verification = verify_blinded_workspace(root)
        return MaterializationResult(
            candidates=candidates,
            mapping_commitment_hash=verification.mapping_commitment_hash,
            blinded_workspace=root / "blinded",
            blinded_manifest_path=root / "blinded" / "manifest.json",
            blinded_archive_path=_safe_relative_file(
                root, state["blinded_archive_relpath"], label="canonical blinded archive"
            ),
            private_materialization_path=_safe_relative_file(
                root, state["record_relpath"], label="private materialization record"
            ),
        )
    if _seal_anchor_path(root).exists():
        raise ValueError("sealed study is missing its immutable materialization")

    selection_secret = _load_selection_secret(root, anchor=anchor, declaration=declaration)
    forbidden_values = (
        v1_generator.generator_id,
        v2_generator.generator_id,
        str(request.v1.input_root.absolute()),
        str(request.v2.input_root.absolute()),
        request.mapping_secret,
        selection_secret["selection_seed"],
        selection_secret["selection_nonce"],
    )
    v1, v1_sources, v1_presentations, v1_cues = _candidate_from_input(
        request.v1,
        root=root,
        declaration=declaration,
        generator_identity=v1_generator,
        normalized_audio_hash=anchor["normalized_audio_hash"],
        forbidden_values=forbidden_values,
    )
    v2, v2_sources, v2_presentations, v2_cues = _candidate_from_input(
        request.v2,
        root=root,
        declaration=declaration,
        generator_identity=v2_generator,
        normalized_audio_hash=anchor["normalized_audio_hash"],
        forbidden_values=forbidden_values,
    )
    if committed <= max(
        _parse_utc("V1 generated_at_utc", v1.generated_at_utc),
        _parse_utc("V2 generated_at_utc", v2.generated_at_utc),
    ):
        raise ValueError("mapping commitment must follow both candidate artifacts")
    for field in ("canonical_content_hash", "token_sequence_hash", "renderer_identity_hash"):
        if getattr(v1, field) != getattr(v2, field):
            raise ValueError(f"candidate {field} differs; boundary isolation is invalid")

    entries = _mapping_entries(
        declaration=declaration,
        v1=v1,
        v2=v2,
        secret=request.mapping_secret,
    )
    commitment = paired_boundary_mapping_commitment_hash(
        study_id=declaration.study_id,
        predeclaration_hash=declaration.predeclaration_hash,
        entries=entries,
    )
    candidates_by_hash = {v1.candidate_record_hash: v1, v2.candidate_record_hash: v2}
    presentations_by_hash = {
        v1.candidate_record_hash: v1_presentations,
        v2.candidate_record_hash: v2_presentations,
    }
    cues_by_hash = {v1.candidate_record_hash: v1_cues, v2.candidate_record_hash: v2_cues}

    clip_source_by_id = {item["clip_id"]: item for item in anchor["clip_artifacts"]}
    blind_clips: list[dict[str, Any]] = []
    allowed_blind_files = {"manifest.json"}
    for entry in entries:
        clip = next(item for item in declaration.clips if item.clip_id == entry.clip_id)
        clip_dir = root / "blinded" / "clips" / clip.clip_id
        source_audio = _safe_relative_file(
            root,
            clip_source_by_id[clip.clip_id]["relpath"],
            label="predeclared audio clip",
        ).read_bytes()
        _atomic_write_immutable(clip_dir / "audio.wav", source_audio, workspace_root=root)
        allowed_blind_files.add(f"clips/{clip.clip_id}/audio.wav")
        view: dict[str, Any] = {
            "clip_id": clip.clip_id,
            "start_ms": clip.start_ms,
            "end_ms": clip.end_ms,
            "selection_stratum": clip.selection_stratum,
            "audio_relpath": f"clips/{clip.clip_id}/audio.wav",
            "audio_clip_hash": clip.audio_clip_hash,
        }
        for side, record_hash in (
            ("A", entry.a_candidate_record_hash),
            ("B", entry.b_candidate_record_hash),
        ):
            candidate = candidates_by_hash[record_hash]
            presentation = presentations_by_hash[record_hash][clip.clip_id]
            cue_set = cues_by_hash[record_hash][clip.clip_id]
            presentation_name = f"{side}.presentation.bin"
            cues_name = f"{side}.cues.json"
            _atomic_write_immutable(clip_dir / presentation_name, presentation, workspace_root=root)
            _atomic_write_immutable(clip_dir / cues_name, cue_set, workspace_root=root)
            allowed_blind_files.add(f"clips/{clip.clip_id}/{presentation_name}")
            allowed_blind_files.add(f"clips/{clip.clip_id}/{cues_name}")
            presentation_record = next(
                item for item in candidate.clip_presentations if item.clip_id == clip.clip_id
            )
            view[side] = {
                "presentation_relpath": f"clips/{clip.clip_id}/{presentation_name}",
                "presentation_artifact_hash": presentation_record.presentation_artifact_hash,
                "cue_set_relpath": f"clips/{clip.clip_id}/{cues_name}",
                "cue_set_file_hash": sha256_bytes(cue_set),
                "cue_set_hash": presentation_record.cue_set_hash,
                "cue_count": presentation_record.cue_count,
            }
        blind_clips.append(view)

    blind_manifest = {
        "schema_version": 1,
        "study_id": declaration.study_id,
        "predeclaration_hash": declaration.predeclaration_hash,
        "mapping_commitment_hash": commitment,
        "candidate_identity_hidden_during_labelling": True,
        "normalized_audio_hash": anchor["normalized_audio_hash"],
        "renderer_identity_hash": v1.renderer_identity_hash,
        "clips": blind_clips,
    }
    blind_bytes = canonical_json_bytes(blind_manifest)
    _reject_identity_leak(
        blind_bytes,
        forbidden_values=forbidden_values,
        label="blinded manifest",
    )
    _atomic_write_immutable(root / "blinded" / "manifest.json", blind_bytes, workspace_root=root)
    blind_archive_bytes = _canonical_blinded_archive_bytes(
        root / "blinded", allowed_files=sorted(allowed_blind_files)
    )
    blind_archive_hash = sha256_bytes(blind_archive_bytes)
    blind_archive_relpath = f"exports/blinded/{blind_archive_hash}.zip"
    _atomic_write_immutable(root / blind_archive_relpath, blind_archive_bytes, workspace_root=root)

    source_snapshots: list[dict[str, Any]] = []
    for candidate, sources in ((v1, v1_sources), (v2, v2_sources)):
        objects: list[dict[str, str]] = []
        for label, data in sources.items():
            digest = sha256_bytes(data)
            relpath = f"private/objects/{digest}.bin"
            _atomic_write_immutable(root / relpath, data, workspace_root=root)
            objects.append({"role": label, "sha256": digest, "relpath": relpath})
        _atomic_write_immutable(
            root / f"private/candidates/{candidate.candidate_record_hash}.json",
            canonical_json_bytes(_candidate_dict(candidate)),
            workspace_root=root,
        )
        source_snapshots.append(
            {
                "candidate_record_hash": candidate.candidate_record_hash,
                "objects": objects,
            }
        )

    mapping_secret_record = {
        "schema_version": 1,
        "study_id": declaration.study_id,
        "predeclaration_hash": declaration.predeclaration_hash,
        "mapping_secret": request.mapping_secret,
        "mapping_secret_commitment": hash_object({"mapping_secret": request.mapping_secret}),
        "mapping_commitment_hash": commitment,
        "entries": [entry.to_dict() for entry in entries],
    }
    mapping_secret_hash = hash_object(mapping_secret_record)
    mapping_relpath = f"private/mappings/{mapping_secret_hash}.json"
    _atomic_write_immutable(
        root / mapping_relpath,
        canonical_json_bytes(mapping_secret_record),
        workspace_root=root,
    )
    record = {
        "schema_version": 1,
        "phase": "materialized",
        "study_id": declaration.study_id,
        "predeclaration_hash": declaration.predeclaration_hash,
        "committed_at_utc": request.committed_at_utc,
        "mapping_commitment_hash": commitment,
        "mapping_secret_commitment": hash_object({"mapping_secret": request.mapping_secret}),
        "mapping_secret_relpath": mapping_relpath,
        "candidate_record_hashes": [v1.candidate_record_hash, v2.candidate_record_hash],
        "candidate_record_relpaths": [
            f"private/candidates/{v1.candidate_record_hash}.json",
            f"private/candidates/{v2.candidate_record_hash}.json",
        ],
        "source_snapshots": source_snapshots,
        "blinded_manifest_hash": sha256_bytes(blind_bytes),
        "blinded_manifest_relpath": "blinded/manifest.json",
        "allowed_blinded_files": sorted(allowed_blind_files),
        "blinded_archive_hash": blind_archive_hash,
        "blinded_archive_relpath": blind_archive_relpath,
        "operator_attestations": {
            "candidate_identity_hidden_during_labelling": True,
            "presentations_built_by_pinned_canonical_renderer": True,
        },
        "limitations": list(LIMITATIONS),
    }
    record_hash = hash_object(record)
    record_relpath = f"private/materializations/{record_hash}.json"
    record_bytes = canonical_json_bytes(record)
    _atomic_write_immutable(root / record_relpath, record_bytes, workspace_root=root)
    materialization_anchor_payload = {
        "schema_version": 1,
        "phase": "materialized",
        "record_hash": sha256_bytes(record_bytes),
        "record_relpath": record_relpath,
        "committed_at_utc": request.committed_at_utc,
        "mapping_secret_commitment": hash_object({"mapping_secret": request.mapping_secret}),
    }
    _atomic_write_immutable(
        materialization_anchor,
        canonical_json_bytes(materialization_anchor_payload),
        workspace_root=root,
    )
    verify_blinded_workspace(root)
    return MaterializationResult(
        candidates=(v1, v2),
        mapping_commitment_hash=commitment,
        blinded_workspace=root / "blinded",
        blinded_manifest_path=root / "blinded" / "manifest.json",
        blinded_archive_path=root / blind_archive_relpath,
        private_materialization_path=root / record_relpath,
    )


def _candidate_from_dict(payload: Mapping[str, Any]) -> PairedBoundaryCandidate:
    value = dict(payload)
    raw_presentations = value.pop("clip_presentations", None)
    presentations = tuple(
        PairedClipPresentation(
            **_strict_object(
                item,
                required={
                    "clip_id",
                    "presentation_artifact_hash",
                    "cue_set_hash",
                    "cue_count",
                },
                label="private candidate presentation",
            )
        )
        for item in _strict_array(raw_presentations, label="private candidate presentations")
    )
    return PairedBoundaryCandidate(**value, clip_presentations=presentations)


def _rebuild_candidate_from_snapshot_objects(
    *,
    candidate: PairedBoundaryCandidate,
    expected_generator: GeneratorIdentity,
    objects: Mapping[str, bytes],
    declaration: PairedBoundaryPredeclaration,
) -> PairedBoundaryCandidate:
    canonical = _strict_object(
        _decode_exact_canonical_json(
            objects["canonical_content"], label="snapshotted canonical content"
        ),
        required={"schema_version", "content"},
        label="snapshotted canonical content",
    )
    if canonical["schema_version"] != 1 or not isinstance(canonical["content"], str):
        raise ValueError("snapshotted canonical content schema is invalid")
    content = canonical["content"]
    tokens = _strict_object(
        _decode_exact_canonical_json(objects["token_sequence"], label="snapshotted token sequence"),
        required={"schema_version", "tokens"},
        label="snapshotted token sequence",
    )
    if tokens["schema_version"] != 1:
        raise ValueError("snapshotted token sequence schema is invalid")
    pairs: list[list[str]] = []
    seen: set[str] = set()
    for raw_token in _strict_array(tokens["tokens"], label="snapshotted canonical tokens"):
        token = _strict_object(
            raw_token,
            required={"token_id", "text"},
            label="snapshotted canonical token",
        )
        if (
            not isinstance(token["token_id"], str)
            or not token["token_id"]
            or token["token_id"] in seen
            or not isinstance(token["text"], str)
            or not token["text"]
        ):
            raise ValueError("snapshotted canonical tokens have invalid identity or text")
        seen.add(token["token_id"])
        pairs.append([token["token_id"], token["text"]])
    if "".join(item[1] for item in pairs) != content:
        raise ValueError("snapshotted canonical/token semantic relationship mismatch")
    cues = _parse_srt(objects["subtitle"])
    if "".join(item.text for item in cues) != content:
        raise ValueError("snapshotted subtitle does not preserve canonical content")
    provenance = _load_candidate_provenance(
        objects["candidate_artifact"],
        expected_generator=expected_generator,
        normalized_audio_hash=candidate.normalized_audio_hash,
        canonical_content_hash=sha256_bytes(content.encode("utf-8")),
        token_sequence_hash=hash_object(pairs),
        subtitle_bytes_hash=sha256_bytes(objects["subtitle"]),
    )

    presentations: list[PairedClipPresentation] = []
    for clip in declaration.clips:
        expected_cues = tuple(
            cue for cue in cues if cue.end_ms > clip.start_ms and cue.start_ms < clip.end_ms
        )
        if not expected_cues:
            raise ValueError("snapshotted subtitle has no cue sequence for a frozen clip")
        cue_role = f"cues.{clip.clip_id}"
        cue_payload = _strict_object(
            _decode_exact_canonical_json(objects[cue_role], label="snapshotted clip cue set"),
            required={"schema_version", "clip_id", "cues"},
            label="snapshotted clip cue set",
        )
        if (
            cue_payload["schema_version"] != 1
            or cue_payload["clip_id"] != clip.clip_id
            or cue_payload["cues"] != [item.to_dict() for item in expected_cues]
        ):
            raise ValueError("snapshotted cue set differs from complete subtitle clip sequence")
        presentation_bytes = objects[f"presentation.{clip.clip_id}"]
        expected_presentation = _render_canonical_presentation(clip=clip, cues=expected_cues)
        if presentation_bytes != expected_presentation:
            raise ValueError("snapshotted presentation differs from pinned canonical renderer")
        presentations.append(
            PairedClipPresentation(
                clip_id=clip.clip_id,
                presentation_artifact_hash=sha256_bytes(presentation_bytes),
                cue_set_hash=hash_object([item.to_dict() for item in expected_cues]),
                cue_count=len(expected_cues),
            )
        )
    rebuilt_payload = {
        "system": provenance["system"],
        "candidate_id": provenance["projection_id"],
        "generation_id": provenance["generation_id"],
        "canonical_content_hash": sha256_bytes(content.encode("utf-8")),
        "token_sequence_hash": hash_object(pairs),
        "candidate_artifact_hash": sha256_bytes(objects["candidate_artifact"]),
        "subtitle_bytes_hash": sha256_bytes(objects["subtitle"]),
        "renderer_identity_hash": CANONICAL_RENDERER_IDENTITY_HASH,
        "normalized_audio_hash": candidate.normalized_audio_hash,
        "predeclaration_hash": declaration.predeclaration_hash,
        "generated_at_utc": provenance["generated_at_utc"],
        "clip_presentations": [item.to_dict() for item in presentations],
    }
    rebuilt_payload["candidate_record_hash"] = paired_boundary_candidate_record_hash(
        rebuilt_payload
    )
    if objects["renderer_identity"] != CANONICAL_RENDERER_IDENTITY_BYTES:
        raise ValueError("snapshotted renderer identity is not the pinned canonical renderer")
    return PairedBoundaryCandidate(
        **{key: value for key, value in rebuilt_payload.items() if key != "clip_presentations"},
        clip_presentations=tuple(presentations),
    )


def _load_materialization_state(
    root: Path,
) -> tuple[
    dict[str, Any],
    tuple[PairedBoundaryCandidate, ...],
    tuple[PairedBoundaryMappingEntry, ...],
    str,
]:
    anchor = _read_exact_canonical_json(
        _materialization_anchor_path(root), label="materialization anchor"
    )
    anchor = _strict_object(
        anchor,
        required={
            "schema_version",
            "phase",
            "record_hash",
            "record_relpath",
            "committed_at_utc",
            "mapping_secret_commitment",
        },
        label="materialization anchor",
    )
    if anchor["schema_version"] != 1 or anchor["phase"] != "materialized":
        raise ValueError("invalid materialization anchor")
    record_path = _safe_relative_file(
        root, anchor["record_relpath"], label="private materialization record"
    )
    record_raw = record_path.read_bytes()
    if sha256_bytes(record_raw) != anchor["record_hash"]:
        raise ValueError("private materialization record hash mismatch")
    state = _read_exact_canonical_json(record_path, label="private materialization record")
    state = _strict_object(
        state,
        required={
            "schema_version",
            "phase",
            "study_id",
            "predeclaration_hash",
            "committed_at_utc",
            "mapping_commitment_hash",
            "mapping_secret_commitment",
            "mapping_secret_relpath",
            "candidate_record_hashes",
            "candidate_record_relpaths",
            "source_snapshots",
            "blinded_manifest_hash",
            "blinded_manifest_relpath",
            "allowed_blinded_files",
            "blinded_archive_hash",
            "blinded_archive_relpath",
            "operator_attestations",
            "limitations",
        },
        label="private materialization record",
    )
    if (
        state["schema_version"] != 1
        or state["phase"] != "materialized"
        or hash_object(state) != Path(anchor["record_relpath"]).stem
    ):
        raise ValueError("private materialization content address or phase mismatch")
    state["record_relpath"] = anchor["record_relpath"]
    candidates: list[PairedBoundaryCandidate] = []
    for relpath, expected_hash in zip(
        state.get("candidate_record_relpaths", []),
        state.get("candidate_record_hashes", []),
        strict=True,
    ):
        payload = _read_exact_canonical_json(
            _safe_relative_file(root, relpath, label="private candidate record"),
            label="private candidate record",
        )
        candidate = _candidate_from_dict(payload)
        if candidate.candidate_record_hash != expected_hash:
            raise ValueError("private candidate record identity mismatch")
        candidates.append(candidate)
    if len(candidates) != 2 or {item.system for item in candidates} != {"v1", "v2"}:
        raise ValueError("private materialization requires exact V1/V2 candidate records")
    _, declaration = _load_predecl_state(root)
    predecl_anchor, _ = _load_predecl_state(root)
    generators = {
        item.system: item
        for item in (
            _generator_identity_from_dict(value) for value in predecl_anchor["generator_identities"]
        )
    }
    raw_snapshots = _strict_array(state.get("source_snapshots"), label="private source snapshots")
    if [item.get("candidate_record_hash") for item in raw_snapshots] != [
        item.candidate_record_hash for item in candidates
    ]:
        raise ValueError("private source snapshots do not match candidate order")
    for raw_snapshot, candidate in zip(raw_snapshots, candidates, strict=True):
        snapshot = _strict_object(
            raw_snapshot,
            required={"candidate_record_hash", "objects"},
            label="private source snapshot",
        )
        objects = tuple(
            _strict_object(
                item,
                required={"role", "sha256", "relpath"},
                label="private source object",
            )
            for item in _strict_array(snapshot["objects"], label="private source objects")
        )
        if len({item["role"] for item in objects}) != len(objects):
            raise ValueError("private source object roles must be unique")
        object_hashes: dict[str, str] = {}
        object_bytes: dict[str, bytes] = {}
        for item in objects:
            _require_sha256("private source object hash", item["sha256"])
            object_path = _safe_relative_file(root, item["relpath"], label="private source object")
            raw_object = object_path.read_bytes()
            if sha256_bytes(raw_object) != item["sha256"]:
                raise ValueError("private source object hash mismatch")
            object_hashes[item["role"]] = item["sha256"]
            object_bytes[item["role"]] = raw_object
        if object_hashes.get("candidate_artifact") != candidate.candidate_artifact_hash:
            raise ValueError("candidate artifact snapshot hash mismatch")
        if object_hashes.get("subtitle") != candidate.subtitle_bytes_hash:
            raise ValueError("candidate subtitle snapshot hash mismatch")
        if object_hashes.get("renderer_identity") != candidate.renderer_identity_hash:
            raise ValueError("renderer identity snapshot hash mismatch")
        expected_roles = {
            "candidate_artifact",
            "subtitle",
            "renderer_identity",
            "canonical_content",
            "token_sequence",
            *{f"presentation.{item.clip_id}" for item in candidate.clip_presentations},
            *{f"cues.{item.clip_id}" for item in candidate.clip_presentations},
        }
        if set(object_hashes) != expected_roles:
            raise ValueError("private source snapshot role coverage mismatch")
        for presentation in candidate.clip_presentations:
            if (
                object_hashes[f"presentation.{presentation.clip_id}"]
                != presentation.presentation_artifact_hash
            ):
                raise ValueError("candidate presentation snapshot hash mismatch")
        rebuilt = _rebuild_candidate_from_snapshot_objects(
            candidate=candidate,
            expected_generator=generators[candidate.system],
            objects=object_bytes,
            declaration=declaration,
        )
        if rebuilt != candidate:
            raise ValueError("candidate record differs from fresh semantic snapshot replay")
    mapping_path = _safe_relative_file(
        root, state["mapping_secret_relpath"], label="private mapping secret"
    )
    mapping_payload = _read_exact_canonical_json(
        mapping_path,
        label="private mapping secret",
    )
    mapping_payload = _strict_object(
        mapping_payload,
        required={
            "schema_version",
            "study_id",
            "predeclaration_hash",
            "mapping_secret",
            "mapping_secret_commitment",
            "mapping_commitment_hash",
            "entries",
        },
        label="private mapping secret",
    )
    mapping_secret = mapping_payload["mapping_secret"]
    if not isinstance(mapping_secret, str) or not mapping_secret:
        raise ValueError("private mapping secret must be non-empty")
    expected_secret_commitment = hash_object({"mapping_secret": mapping_secret})
    expected_record_hash = Path(state["mapping_secret_relpath"]).stem
    if (
        mapping_payload["schema_version"] != 1
        or mapping_payload["study_id"] != state["study_id"]
        or mapping_payload["predeclaration_hash"] != state["predeclaration_hash"]
        or mapping_payload["mapping_secret_commitment"] != expected_secret_commitment
        or state["mapping_secret_commitment"] != expected_secret_commitment
        or hash_object(mapping_payload) != expected_record_hash
    ):
        raise ValueError("private mapping secret content address or lineage mismatch")
    entries = tuple(
        PairedBoundaryMappingEntry(
            **_strict_object(
                item,
                required={
                    "clip_id",
                    "a_candidate_record_hash",
                    "b_candidate_record_hash",
                    "nonce",
                },
                label="private mapping entry",
            )
        )
        for item in _strict_array(mapping_payload.get("entries"), label="private mapping entries")
    )
    expected_commitment = paired_boundary_mapping_commitment_hash(
        study_id=state["study_id"],
        predeclaration_hash=state["predeclaration_hash"],
        entries=entries,
    )
    if expected_commitment != state["mapping_commitment_hash"]:
        raise ValueError("private mapping commitment mismatch")
    if mapping_payload["mapping_commitment_hash"] != expected_commitment:
        raise ValueError("private mapping record commitment mismatch")
    return state, tuple(candidates), entries, mapping_secret


def verify_blinded_workspace(workspace_root: Path) -> BlindWorkspaceVerification:
    """Verify the complete blinded tree from local snapshots without external calls."""

    root = _safe_workspace(workspace_root)
    _assert_custody_no_ads(root)
    predecl_anchor, declaration = _load_predecl_state(root)
    state, candidates, entries, mapping_secret = _load_materialization_state(root)
    candidate_by_system = {item.system: item for item in candidates}
    if entries != _mapping_entries(
        declaration=declaration,
        v1=candidate_by_system["v1"],
        v2=candidate_by_system["v2"],
        secret=mapping_secret,
    ):
        raise ValueError("private mapping entries do not follow frozen randomization")
    manifest_path = _safe_relative_file(
        root, state["blinded_manifest_relpath"], label="blinded manifest"
    )
    manifest_raw = manifest_path.read_bytes()
    if sha256_bytes(manifest_raw) != state["blinded_manifest_hash"]:
        raise ValueError("blinded manifest hash mismatch")
    manifest = _read_exact_canonical_json(manifest_path, label="blinded manifest")
    expected_top = {
        "schema_version",
        "study_id",
        "predeclaration_hash",
        "mapping_commitment_hash",
        "candidate_identity_hidden_during_labelling",
        "normalized_audio_hash",
        "renderer_identity_hash",
        "clips",
    }
    _strict_object(manifest, required=expected_top, label="blinded manifest")
    if (
        manifest["schema_version"] != 1
        or manifest["study_id"] != declaration.study_id
        or manifest["predeclaration_hash"] != declaration.predeclaration_hash
        or manifest["mapping_commitment_hash"] != state["mapping_commitment_hash"]
        or manifest["candidate_identity_hidden_during_labelling"] is not True
        or manifest["normalized_audio_hash"] != predecl_anchor["normalized_audio_hash"]
    ):
        raise ValueError("blinded manifest lineage or protocol mismatch")
    if len({item.renderer_identity_hash for item in candidates}) != 1:
        raise ValueError("private candidates differ in renderer identity")
    if manifest["renderer_identity_hash"] != candidates[0].renderer_identity_hash:
        raise ValueError("blinded manifest renderer identity mismatch")

    raw_clips = _strict_array(manifest["clips"], label="blinded clips")
    if [item.get("clip_id") for item in raw_clips] != [item.clip_id for item in declaration.clips]:
        raise ValueError("blinded workspace clip coverage/order mismatch")
    entries_by_clip = {item.clip_id: item for item in entries}
    candidates_by_hash = {item.candidate_record_hash: item for item in candidates}
    clip_source_by_id = {item["clip_id"]: item for item in predecl_anchor["clip_artifacts"]}
    for raw_clip, clip in zip(raw_clips, declaration.clips, strict=True):
        value = _strict_object(
            raw_clip,
            required={
                "clip_id",
                "start_ms",
                "end_ms",
                "selection_stratum",
                "audio_relpath",
                "audio_clip_hash",
                "A",
                "B",
            },
            label="blinded clip",
        )
        if (
            value["start_ms"] != clip.start_ms
            or value["end_ms"] != clip.end_ms
            or value["selection_stratum"] != clip.selection_stratum
            or value["audio_clip_hash"] != clip.audio_clip_hash
        ):
            raise ValueError("blinded clip differs from predeclaration")
        audio_path = _safe_relative_file(
            root / "blinded", value["audio_relpath"], label="blind audio"
        )
        if sha256_bytes(audio_path.read_bytes()) != clip.audio_clip_hash:
            raise ValueError("blinded audio clip hash mismatch")
        source_path = _safe_relative_file(
            root, clip_source_by_id[clip.clip_id]["relpath"], label="predeclared audio clip"
        )
        if audio_path.read_bytes() != source_path.read_bytes():
            raise ValueError("blinded audio is not exact predeclared clip bytes")
        entry = entries_by_clip[clip.clip_id]
        for side, record_hash in (
            ("A", entry.a_candidate_record_hash),
            ("B", entry.b_candidate_record_hash),
        ):
            side_value = _strict_object(
                value[side],
                required={
                    "presentation_relpath",
                    "presentation_artifact_hash",
                    "cue_set_relpath",
                    "cue_set_file_hash",
                    "cue_set_hash",
                    "cue_count",
                },
                label=f"blinded {side} view",
            )
            presentation_path = _safe_relative_file(
                root / "blinded",
                side_value["presentation_relpath"],
                label=f"blind {side} presentation",
            )
            cues_path = _safe_relative_file(
                root / "blinded", side_value["cue_set_relpath"], label=f"blind {side} cues"
            )
            if (
                sha256_bytes(presentation_path.read_bytes())
                != side_value["presentation_artifact_hash"]
            ):
                raise ValueError("blinded presentation hash mismatch")
            if sha256_bytes(cues_path.read_bytes()) != side_value["cue_set_file_hash"]:
                raise ValueError("blinded cue-set file hash mismatch")
            candidate = candidates_by_hash[record_hash]
            presentation = next(
                item for item in candidate.clip_presentations if item.clip_id == clip.clip_id
            )
            if (
                side_value["presentation_artifact_hash"] != presentation.presentation_artifact_hash
                or side_value["cue_set_hash"] != presentation.cue_set_hash
                or side_value["cue_count"] != presentation.cue_count
            ):
                raise ValueError("blinded view differs from committed candidate record")

    blind_root = root / "blinded"
    actual_files: set[str] = set()
    for path in blind_root.rglob("*"):
        if _is_link_or_junction(path):
            raise ValueError("blinded workspace must not contain symlinks or junctions")
        if path.is_file():
            actual_files.add(path.relative_to(blind_root).as_posix())
    expected_files = set(state["allowed_blinded_files"])
    if actual_files != expected_files:
        raise ValueError(
            "blinded workspace file set mismatch; secret, extra, or missing file detected"
        )
    archive_path = _safe_relative_file(
        root, state["blinded_archive_relpath"], label="canonical blinded archive"
    )
    rebuilt_archive_hash = _verify_canonical_blinded_archive(
        archive_path,
        blind_root=blind_root,
        allowed_files=state["allowed_blinded_files"],
    )
    if rebuilt_archive_hash != state["blinded_archive_hash"]:
        raise ValueError("canonical blinded archive hash mismatch")
    selection_secret = _load_selection_secret(root, anchor=predecl_anchor, declaration=declaration)
    forbidden = [
        *(item.candidate_id for item in candidates),
        mapping_secret,
        selection_secret["selection_seed"],
        selection_secret["selection_nonce"],
    ]
    for path in blind_root.rglob("*"):
        relative = path.relative_to(blind_root).as_posix()
        _reject_identity_leak(relative.encode(), forbidden_values=forbidden, label="blind path")
        if path.is_file():
            _reject_identity_leak(
                path.read_bytes(),
                forbidden_values=forbidden,
                label="blind artifact",
                forbid_system_markers=False,
            )
    return BlindWorkspaceVerification(
        study_id=declaration.study_id,
        predeclaration_hash=declaration.predeclaration_hash,
        mapping_commitment_hash=state["mapping_commitment_hash"],
        clip_count=len(declaration.clips),
        blinded_manifest_hash=state["blinded_manifest_hash"],
    )


def _load_human_labels(
    path: Path,
    *,
    declaration: PairedBoundaryPredeclaration,
    mapping_commitment_hash: str,
) -> tuple[bytes, tuple[dict[str, Any], ...]]:
    labels_path = _safe_existing_file(path, label="raw human labels")
    raw = labels_path.read_bytes()
    try:
        decoded = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError("raw human labels must be UTF-8 JSON") from exc
    payload = _strict_object(
        decoded,
        required={
            "schema_version",
            "study_id",
            "mapping_commitment_hash",
            "labels_created_by_humans",
            "candidate_identity_hidden_during_labelling",
            "model_generated",
            "operator_attestation",
            "judgements",
        },
        label="raw human labels",
    )
    if raw != canonical_json_bytes(payload):
        raise ValueError("raw human labels must use exact canonical JSON bytes")
    if payload["schema_version"] != 1 or payload["study_id"] != declaration.study_id:
        raise ValueError("raw human labels identity mismatch")
    if payload["mapping_commitment_hash"] != mapping_commitment_hash:
        raise ValueError("raw human labels mapping commitment mismatch")
    if payload["labels_created_by_humans"] is not True or payload["model_generated"] is not False:
        raise ValueError("raw labels require explicit human=true and model_generated=false")
    if payload["candidate_identity_hidden_during_labelling"] is not True:
        raise ValueError("raw labels require explicit blind identity attestation")
    if (
        not isinstance(payload["operator_attestation"], str)
        or not payload["operator_attestation"].strip()
    ):
        raise ValueError("raw labels require a non-empty operator attestation")
    required_judgement = {
        "clip_id",
        "evaluator_id",
        "outcome",
        "a_unacceptable",
        "b_unacceptable",
        "a_presentation_artifact_hash",
        "b_presentation_artifact_hash",
        "submitted_at_utc",
    }
    judgements = tuple(
        _strict_object(item, required=required_judgement, label="raw human judgement")
        for item in _strict_array(payload["judgements"], label="raw human judgements")
    )
    expected_clip_ids = [item.clip_id for item in declaration.clips]
    if [item["clip_id"] for item in judgements] != expected_clip_ids:
        raise ValueError("raw human labels must contain each clip exactly once in frozen order")
    timestamps = [
        _parse_utc("judgement submitted_at_utc", item["submitted_at_utc"]) for item in judgements
    ]
    if timestamps != sorted(timestamps):
        raise ValueError("raw human labels must use nondecreasing submission timestamps")
    return raw, judgements


def _assert_labels_outside_study_custody(root: Path, labels_path: Path) -> None:
    absolute = labels_path.absolute()
    try:
        absolute.relative_to(root.absolute())
    except ValueError:
        return
    raise ValueError("raw human labels must remain outside study custody roots")


def _label_seal_hash(payload: Mapping[str, Any]) -> str:
    value = dict(payload)
    value.pop("label_seal_hash", None)
    return hash_object(value)


def _load_label_seal(root: Path) -> tuple[dict[str, Any], bytes]:
    anchor_path = _seal_anchor_path(root)
    anchor = _read_exact_canonical_json(anchor_path, label="human label seal anchor")
    anchor = _strict_object(
        anchor,
        required={
            "schema_version",
            "phase",
            "study_id",
            "predeclaration_hash",
            "mapping_commitment_hash",
            "blinded_manifest_hash",
            "labels_file_hash",
            "labels_relpath",
            "labels_completed_at_utc",
            "label_seal_hash",
            "limitations",
        },
        label="human label seal anchor",
    )
    if (
        anchor["schema_version"] != 1
        or anchor["phase"] != "human_labels_sealed"
        or anchor["label_seal_hash"] != _label_seal_hash(anchor)
    ):
        raise ValueError("human label seal anchor identity mismatch")
    labels_path = _safe_relative_file(root, anchor["labels_relpath"], label="sealed human labels")
    raw = labels_path.read_bytes()
    if sha256_bytes(raw) != anchor["labels_file_hash"]:
        raise ValueError("sealed human labels hash mismatch")
    return anchor, raw


def seal_human_labels(
    *,
    workspace_root: Path,
    raw_human_labels_path: Path,
    labels_completed_at_utc: str,
) -> HumanLabelSealResult:
    """Durably freeze exact human-label bytes without reading or writing a reveal."""

    root = _safe_workspace(workspace_root)
    _assert_labels_outside_study_custody(root, raw_human_labels_path)
    verification = verify_blinded_workspace(root)
    _, declaration = _load_predecl_state(root)
    state, _candidates, _entries, _mapping_secret = _load_materialization_state(root)
    completed = _parse_utc("labels_completed_at_utc", labels_completed_at_utc)
    raw_labels, raw_judgements = _load_human_labels(
        raw_human_labels_path,
        declaration=declaration,
        mapping_commitment_hash=verification.mapping_commitment_hash,
    )
    committed = _parse_utc("mapping committed_at_utc", state["committed_at_utc"])
    manifest = _read_exact_canonical_json(
        root / "blinded" / "manifest.json", label="blinded manifest"
    )
    blind_by_clip = {item["clip_id"]: item for item in manifest["clips"]}
    for judgement in raw_judgements:
        submitted = _parse_utc("judgement submitted_at_utc", judgement["submitted_at_utc"])
        if submitted <= committed or submitted > completed:
            raise ValueError("human judgement timestamp is outside committed/sealed interval")
        blind = blind_by_clip[judgement["clip_id"]]
        if (
            judgement["a_presentation_artifact_hash"] != blind["A"]["presentation_artifact_hash"]
            or judgement["b_presentation_artifact_hash"] != blind["B"]["presentation_artifact_hash"]
        ):
            raise ValueError("human judgement does not bind exact blinded presentations")

    labels_hash = sha256_bytes(raw_labels)
    labels_relpath = f"sealed/labels/{labels_hash}.json"
    seal_payload = {
        "schema_version": 1,
        "phase": "human_labels_sealed",
        "study_id": declaration.study_id,
        "predeclaration_hash": declaration.predeclaration_hash,
        "mapping_commitment_hash": verification.mapping_commitment_hash,
        "blinded_manifest_hash": verification.blinded_manifest_hash,
        "labels_file_hash": labels_hash,
        "labels_relpath": labels_relpath,
        "labels_completed_at_utc": labels_completed_at_utc,
        "limitations": list(LIMITATIONS),
    }
    seal_payload["label_seal_hash"] = _label_seal_hash(seal_payload)
    seal_anchor = _seal_anchor_path(root)
    # The fixed immutable anchor is the commit point.  Writing it first means a
    # crash cannot let a later retry bind different votes; the same in-memory
    # bytes then complete the content-addressed snapshot.
    _atomic_write_immutable(seal_anchor, canonical_json_bytes(seal_payload), workspace_root=root)
    _atomic_write_immutable(root / labels_relpath, raw_labels, workspace_root=root)
    loaded, loaded_raw = _load_label_seal(root)
    if loaded_raw != raw_labels:
        raise ValueError("human label seal replay conflicts with immutable labels")
    return HumanLabelSealResult(
        labels_file_hash=labels_hash,
        label_seal_hash=loaded["label_seal_hash"],
        labels_completed_at_utc=labels_completed_at_utc,
        labels_snapshot_path=root / labels_relpath,
        seal_anchor_path=seal_anchor,
    )


def _judgements_from_sealed_labels(
    *,
    raw_judgements: Sequence[Mapping[str, Any]],
    entries: Sequence[PairedBoundaryMappingEntry],
    candidates: Sequence[PairedBoundaryCandidate],
    mapping_commitment_hash: str,
) -> tuple[PairedBoundaryJudgement, ...]:
    candidate_by_hash = {item.candidate_record_hash: item for item in candidates}
    entry_by_clip = {item.clip_id: item for item in entries}
    results: list[PairedBoundaryJudgement] = []
    for raw in raw_judgements:
        entry = entry_by_clip[raw["clip_id"]]
        a_candidate = candidate_by_hash[entry.a_candidate_record_hash]
        b_candidate = candidate_by_hash[entry.b_candidate_record_hash]
        a_presentation = next(
            item for item in a_candidate.clip_presentations if item.clip_id == raw["clip_id"]
        )
        b_presentation = next(
            item for item in b_candidate.clip_presentations if item.clip_id == raw["clip_id"]
        )
        if (
            raw["a_presentation_artifact_hash"] != a_presentation.presentation_artifact_hash
            or raw["b_presentation_artifact_hash"] != b_presentation.presentation_artifact_hash
        ):
            raise ValueError("sealed judgement presentation hashes do not match reveal mapping")
        results.append(
            PairedBoundaryJudgement(
                clip_id=raw["clip_id"],
                evaluator_id=raw["evaluator_id"],
                outcome=PairedBoundaryOutcome(raw["outcome"]),
                a_unacceptable=raw["a_unacceptable"],
                b_unacceptable=raw["b_unacceptable"],
                a_presentation_artifact_hash=raw["a_presentation_artifact_hash"],
                b_presentation_artifact_hash=raw["b_presentation_artifact_hash"],
                mapping_commitment_hash=mapping_commitment_hash,
                submitted_at_utc=raw["submitted_at_utc"],
            )
        )
    return tuple(results)


def reveal_sealed_human_labels(
    *,
    workspace_root: Path,
    raw_human_labels_path: Path,
    revealed_at_utc: str,
) -> StudySealResult:
    """Reveal only the already-terminal label seal and build a loader-valid study."""

    root = _safe_workspace(workspace_root)
    _assert_labels_outside_study_custody(root, raw_human_labels_path)
    verification = verify_blinded_workspace(root)
    predecl_anchor, declaration = _load_predecl_state(root)
    state, candidates, entries, _mapping_secret = _load_materialization_state(root)
    seal_anchor, sealed_raw = _load_label_seal(root)
    supplied_path = _safe_existing_file(raw_human_labels_path, label="raw human labels")
    supplied_raw = supplied_path.read_bytes()
    if supplied_raw != sealed_raw:
        raise ValueError("raw human labels conflict with the immutable sealed labels")
    if (
        seal_anchor["study_id"] != declaration.study_id
        or seal_anchor["predeclaration_hash"] != declaration.predeclaration_hash
        or seal_anchor["mapping_commitment_hash"] != verification.mapping_commitment_hash
        or seal_anchor["blinded_manifest_hash"] != verification.blinded_manifest_hash
    ):
        raise ValueError("human label seal lineage differs from verified blinded workspace")
    completed_at = seal_anchor["labels_completed_at_utc"]
    if _parse_utc("revealed_at_utc", revealed_at_utc) <= _parse_utc(
        "labels_completed_at_utc", completed_at
    ):
        raise ValueError("mapping reveal must occur after labels are sealed")
    snapshot_path = root / seal_anchor["labels_relpath"]
    _, raw_judgements = _load_human_labels(
        snapshot_path,
        declaration=declaration,
        mapping_commitment_hash=verification.mapping_commitment_hash,
    )
    judgements = _judgements_from_sealed_labels(
        raw_judgements=raw_judgements,
        entries=entries,
        candidates=candidates,
        mapping_commitment_hash=verification.mapping_commitment_hash,
    )
    reveal = PairedBoundaryMappingReveal(
        revealed_at_utc=revealed_at_utc,
        labels_completed_at_utc=completed_at,
        entries=tuple(entries),
    )
    mapping = PairedBoundaryMapping(
        commitment_hash=verification.mapping_commitment_hash,
        committed_at_utc=state["committed_at_utc"],
        randomization_method=RANDOMIZATION_METHOD,
        reveal=reveal,
    )
    study_payload = {
        "schema_version": 1,
        "evaluation_kind": "paired_boundary_superiority",
        "study_id": declaration.study_id,
        "protocol_id": PROTOCOL_ID,
        "episode_id": predecl_anchor["episode_id"],
        "lineage_id": predecl_anchor["lineage_id"],
        "normalized_audio_hash": predecl_anchor["normalized_audio_hash"],
        "benchmark_suite_hash": predecl_anchor["benchmark_suite_hash"],
        "complete": True,
        "candidate_identity_hidden_during_labelling": True,
        "labels_created_by_humans": True,
        "predeclaration": _predeclaration_dict(declaration),
        "candidates": [_candidate_dict(item) for item in candidates],
        "mapping": {
            "commitment_hash": mapping.commitment_hash,
            "committed_at_utc": mapping.committed_at_utc,
            "randomization_method": mapping.randomization_method,
            "reveal": {
                "revealed_at_utc": reveal.revealed_at_utc,
                "labels_completed_at_utc": reveal.labels_completed_at_utc,
                "entries": [entry.to_dict() for entry in reveal.entries],
            },
        },
        "judgements": [
            {
                "clip_id": item.clip_id,
                "evaluator_id": item.evaluator_id,
                "outcome": item.outcome.value,
                "a_unacceptable": item.a_unacceptable,
                "b_unacceptable": item.b_unacceptable,
                "a_presentation_artifact_hash": item.a_presentation_artifact_hash,
                "b_presentation_artifact_hash": item.b_presentation_artifact_hash,
                "mapping_commitment_hash": item.mapping_commitment_hash,
                "submitted_at_utc": item.submitted_at_utc,
            }
            for item in judgements
        ],
    }
    study_payload["study_hash"] = paired_boundary_study_hash(study_payload)
    study = load_paired_boundary_study(study_payload)
    study_relpath = f"sealed/studies/{study.study_hash}.json"
    reveal_payload = {
        "schema_version": 1,
        "phase": "mapping_revealed",
        "label_seal_hash": seal_anchor["label_seal_hash"],
        "labels_file_hash": seal_anchor["labels_file_hash"],
        "revealed_at_utc": revealed_at_utc,
        "study_hash": study.study_hash,
        "study_relpath": study_relpath,
        "limitations": list(LIMITATIONS),
    }
    reveal_anchor = _reveal_anchor_path(root)
    # This immutable anchor is the reveal commit point.  It freezes the study
    # identity before a crash can leave a partial content-addressed study file.
    _atomic_write_immutable(
        reveal_anchor, canonical_json_bytes(reveal_payload), workspace_root=root
    )
    _atomic_write_immutable(
        root / study_relpath, canonical_json_bytes(study_payload), workspace_root=root
    )
    loaded_anchor = _read_exact_canonical_json(reveal_anchor, label="mapping reveal anchor")
    if loaded_anchor != reveal_payload:
        raise ValueError("mapping reveal replay conflicts with immutable reveal anchor")
    loaded_study = load_paired_boundary_study(root / study_relpath)
    return StudySealResult(
        study=loaded_study,
        study_path=root / study_relpath,
        labels_snapshot_path=snapshot_path,
    )


def seal_human_labels_and_reveal(request: SealHumanLabelsRequest) -> StudySealResult:
    """Compatibility composition of the two durable public phases."""

    seal_human_labels(
        workspace_root=request.workspace_root,
        raw_human_labels_path=request.raw_human_labels_path,
        labels_completed_at_utc=request.labels_completed_at_utc,
    )
    return reveal_sealed_human_labels(
        workspace_root=request.workspace_root,
        raw_human_labels_path=request.raw_human_labels_path,
        revealed_at_utc=request.revealed_at_utc,
    )


__all__ = [
    "BlindWorkspaceVerification",
    "CandidateClipInput",
    "CandidateInput",
    "GeneratorIdentity",
    "HumanLabelSealResult",
    "LIMITATIONS",
    "MaterializationResult",
    "MaterializeRequest",
    "PredeclarationResult",
    "PredeclareRequest",
    "SAMPLING_FRAME_SCHEMA_VERSION",
    "SELECTION_ALGORITHM",
    "SamplingFrame",
    "SamplingFrameWindow",
    "SealHumanLabelsRequest",
    "StudySealResult",
    "materialize_candidates_and_commit_mapping",
    "predeclare",
    "reveal_sealed_human_labels",
    "seal_human_labels",
    "seal_human_labels_and_reveal",
    "verify_blinded_workspace",
]
