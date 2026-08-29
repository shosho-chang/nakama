#!/usr/bin/env python3
"""Materialize the production Memo Dual-Audit subtitle release.

The runner is deliberately independent from the legacy deep-checkpoint module.
It consumes sealed evidence,
freshly replays the two text audits plus arbitration, verifies dual-ASR coverage
for every major-risk component, and commits a release plus Stage 5 handoff.
"""

from __future__ import annotations

import argparse
import hashlib
import importlib.metadata
import io
import json
import os
import sys
import tempfile
import unicodedata
import wave
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Mapping, Sequence

_REPO_ROOT = Path(__file__).resolve().parents[1]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from agents.brook.podcast_subtitles.adapters.memo_recognition import (  # noqa: E402
    MemoExecutionReceiptRefV1,
)
from agents.brook.podcast_subtitles.hashing import (  # noqa: E402
    measure_regular_file,
)
from agents.brook.podcast_subtitles.memo_bundled_runner import (  # noqa: E402
    MemoBundledRunnerRequest,
    load_memo_bundled_runner_execution_receipt,
    load_verified_memo_bundled_runner_execution,
)
from agents.brook.podcast_subtitles.ports import (  # noqa: E402
    AdapterInputError,
    AdapterIntegrityError,
)
from scripts.podcast_subtitle_v2_simple_step7 import (  # noqa: E402
    SimpleStep7Error,
    _parse_srt,
    _render_srt,
    apply_official_arbitration,
)

RELEASE_CONTRACT = "podcast-subtitle-memo-dual-audit-release-v1"
EXPORT_CONTRACT = "podcast-subtitle-memo-dual-audit-release-export-v1"
REQUEST_CONTRACT = "podcast-subtitle-memo-dual-audit-release-request-v1"
STATUS_CONTRACT = "podcast-subtitle-memo-dual-audit-release-status-v1"
STAGE5_HANDOFF_CONTRACT = "podcast-subtitle-stage5-memo-dual-audit-handoff-v1"
AUDIO_DECISIONS_CONTRACT = "podcast-subtitle-memo-dual-audit-audio-decisions-v1"
ASR_EVIDENCE_CONTRACT = "podcast-subtitle-memo-dual-audit-asr-evidence-v1"
ASR_RAW_RESULT_CONTRACT = "podcast-subtitle-memo-dual-audit-asr-raw-result-v1"
ASR_PROVIDER_OUTPUT_CONTRACT = "podcast-subtitle-memo-dual-audit-asr-provider-output-v1"
MAJOR_AUDIO_PLAN_CONTRACT = "podcast-subtitle-memo-dual-audit-major-audio-plan-v1"
MAJOR_ASR_RUN_CONTRACT = "podcast-subtitle-memo-dual-audit-major-asr-run-v1"
DEFAULT_MAJOR_AUDIO_PLAN = "subtitle-work/memo-dual-audit-v1/major-audio/plan.json"
POLICY_VERSION = "memo-dual-audit-release-v1"

_REQUIRED_INPUT_ROLES = (
    "normalized_audio",
    "normalized_handoff",
    "memo_srt",
    "memo_recognition_evidence",
    "memo_recognition_acceptance",
    "memo_cue_acceptance",
    "text_audit_a",
    "text_audit_b",
    "base_corrected_srt",
    "base_consensus_ledger",
    "base_needs_audio",
    "arbitration",
    "text_corrected_srt",
    "text_arbitration_ledger",
    "unresolved_components",
    "audio_decisions",
)

_PHASE_ROLES = (
    ("awaiting_normalized_audio", _REQUIRED_INPUT_ROLES[:2]),
    ("awaiting_memo_acceptance", _REQUIRED_INPUT_ROLES[2:6]),
    ("awaiting_text_audits", _REQUIRED_INPUT_ROLES[6:8]),
    ("awaiting_arbitration", _REQUIRED_INPUT_ROLES[8:15]),
    ("awaiting_major_dual_asr", _REQUIRED_INPUT_ROLES[15:]),
)

_AUDIO_DECISIONS = frozenset({"accept_replacement", "retain_memo_original"})
_AUDIO_REASON_CODES = frozenset(
    {
        "dual_asr_consensus",
        "dual_asr_consensus_plus_reference",
        "dual_asr_conflict",
        "dual_asr_unresolved",
    }
)


class SubtitleReleaseError(ValueError):
    """A sealed input or destination violated the release contract."""


@dataclass(frozen=True, slots=True)
class FileRef:
    path: str
    sha256: str | None
    size_bytes: int | None


@dataclass(frozen=True, slots=True)
class ReleaseRequest:
    request_path: Path
    root: Path
    episode_id: str
    output_directory: str
    refs: Mapping[str, FileRef]


@dataclass(frozen=True, slots=True)
class ReleaseBundle:
    release_srt: bytes
    release_ledger: bytes
    export_manifest: bytes
    stage5_handoff: bytes


_DEFAULT_INPUT_PATHS = {
    "normalized_audio": "normalized.wav",
    "normalized_handoff": "normalized-handoff.v1.json",
    "memo_srt": "subtitle-v2/memo-recognition.composite.execution.srt",
    "memo_recognition_evidence": "subtitle-v2/memo-recognition.v1.json",
    "memo_recognition_acceptance": "subtitle-v2/memo-recognition-acceptance.v1.json",
    "memo_cue_acceptance": "subtitle-v2/memo-cue-acceptance.v1.json",
    "text_audit_a": "subtitle-work/memo-dual-audit-v1/audit-a.json",
    "text_audit_b": "subtitle-work/memo-dual-audit-v1/audit-b.json",
    "base_corrected_srt": "subtitle-work/memo-dual-audit-v1/base-corrected.srt",
    "base_consensus_ledger": ("subtitle-work/memo-dual-audit-v1/base-consensus-ledger.json"),
    "base_needs_audio": "subtitle-work/memo-dual-audit-v1/base-needs-audio.json",
    "arbitration": "subtitle-work/memo-dual-audit-v1/arbitration.json",
    "text_corrected_srt": "subtitle-work/memo-dual-audit-v1/text-corrected.srt",
    "text_arbitration_ledger": ("subtitle-work/memo-dual-audit-v1/text-arbitration-ledger.json"),
    "unresolved_components": ("subtitle-work/memo-dual-audit-v1/unresolved-components.json"),
    "audio_decisions": "subtitle-work/memo-dual-audit-v1/audio-decisions.json",
}


def _canonical_bytes(value: object) -> bytes:
    return (
        json.dumps(
            value,
            ensure_ascii=False,
            allow_nan=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        + "\n"
    ).encode("utf-8")


def _sha256(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def _is_sha256(value: object) -> bool:
    return (
        isinstance(value, str)
        and len(value) == 64
        and all(character in "0123456789abcdef" for character in value)
    )


def _strict_object(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise SubtitleReleaseError(f"duplicate JSON key: {key}")
        result[key] = value
    return result


def _load_json(raw: bytes, *, label: str) -> dict[str, Any]:
    try:
        value = json.loads(
            raw.decode("utf-8-sig"),
            object_pairs_hook=_strict_object,
            parse_constant=lambda token: (_ for _ in ()).throw(
                SubtitleReleaseError(f"{label} contains invalid constant: {token}")
            ),
        )
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise SubtitleReleaseError(f"{label} is not valid UTF-8 JSON") from exc
    if not isinstance(value, dict):
        raise SubtitleReleaseError(f"{label} must be a JSON object")
    return value


def _require_keys(
    value: Mapping[str, object],
    *,
    label: str,
    required: set[str],
    optional: set[str] = frozenset(),
) -> None:
    missing = required - value.keys()
    unknown = value.keys() - required - optional
    if missing:
        raise SubtitleReleaseError(f"{label} missing keys: {sorted(missing)}")
    if unknown:
        raise SubtitleReleaseError(f"{label} has unknown keys: {sorted(unknown)}")


def _relative_path(value: object, *, label: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise SubtitleReleaseError(f"{label} must be a non-empty relative path")
    candidate = Path(value)
    if candidate.is_absolute() or candidate.drive or ".." in candidate.parts:
        raise SubtitleReleaseError(f"{label} escapes the request root")
    normalized = candidate.as_posix()
    if normalized in {"", "."}:
        raise SubtitleReleaseError(f"{label} must name a file or directory")
    return normalized


def _resolve(root: Path, relative: str, *, label: str) -> Path:
    candidate = (root / Path(relative)).resolve()
    try:
        candidate.relative_to(root)
    except ValueError as exc:
        raise SubtitleReleaseError(f"{label} escapes the request root") from exc
    return candidate


def _file_ref(value: object, *, label: str) -> FileRef:
    if not isinstance(value, dict):
        raise SubtitleReleaseError(f"{label} must be an object")
    _require_keys(
        value,
        label=label,
        required={"path", "sha256", "size_bytes"},
    )
    path = _relative_path(value["path"], label=f"{label}.path")
    digest = value["sha256"]
    size = value["size_bytes"]
    if digest is not None:
        if not isinstance(digest, str) or len(digest) != 64:
            raise SubtitleReleaseError(f"{label}.sha256 must be null or SHA-256")
        try:
            int(digest, 16)
        except ValueError as exc:
            raise SubtitleReleaseError(f"{label}.sha256 must be hexadecimal") from exc
        digest = digest.lower()
    if size is not None and (not isinstance(size, int) or isinstance(size, bool) or size < 0):
        raise SubtitleReleaseError(f"{label}.size_bytes must be null or non-negative")
    if (digest is None) != (size is None):
        raise SubtitleReleaseError(f"{label} hash and size must be sealed together")
    return FileRef(path=path, sha256=digest, size_bytes=size)


def load_request(path: Path) -> ReleaseRequest:
    request_path = path.resolve()
    root = request_path.parent.resolve()
    payload = _load_json(request_path.read_bytes(), label="release request")
    _require_keys(
        payload,
        label="release request",
        required={
            "schema_version",
            "contract",
            "episode_id",
            "output_directory",
            "inputs",
        },
    )
    if payload["schema_version"] != 1 or payload["contract"] != REQUEST_CONTRACT:
        raise SubtitleReleaseError("unsupported release request contract")
    episode_id = payload["episode_id"]
    if not isinstance(episode_id, str) or not episode_id.strip():
        raise SubtitleReleaseError("episode_id must be non-empty")
    if episode_id != root.name:
        raise SubtitleReleaseError("episode_id must exactly match the episode directory")
    output_directory = _relative_path(payload["output_directory"], label="output_directory")
    inputs = payload["inputs"]
    if not isinstance(inputs, dict):
        raise SubtitleReleaseError("release request inputs must be an object")
    missing = set(_REQUIRED_INPUT_ROLES) - inputs.keys()
    unknown = inputs.keys() - set(_REQUIRED_INPUT_ROLES)
    if missing:
        raise SubtitleReleaseError(f"release request missing inputs: {sorted(missing)}")
    if unknown:
        raise SubtitleReleaseError(f"release request has unknown inputs: {sorted(unknown)}")
    refs = {role: _file_ref(inputs[role], label=f"inputs.{role}") for role in _REQUIRED_INPUT_ROLES}
    _resolve(root, output_directory, label="output_directory")
    return ReleaseRequest(
        request_path=request_path,
        root=root,
        episode_id=episode_id,
        output_directory=output_directory,
        refs=refs,
    )


def _read_ref(request: ReleaseRequest, role: str) -> bytes:
    ref = request.refs[role]
    if ref.sha256 is None or ref.size_bytes is None:
        raise SubtitleReleaseError(f"unsealed input: {role}")
    path = _resolve(request.root, ref.path, label=f"inputs.{role}.path")
    try:
        payload = path.read_bytes()
    except FileNotFoundError as exc:
        raise SubtitleReleaseError(f"missing sealed input: {role}") from exc
    if len(payload) != ref.size_bytes or _sha256(payload) != ref.sha256:
        raise SubtitleReleaseError(f"sealed input drift: {role}")
    return payload


def _missing_inputs(request: ReleaseRequest) -> list[str]:
    missing: list[str] = []
    for role in _REQUIRED_INPUT_ROLES:
        path = _resolve(request.root, request.refs[role].path, label=role)
        ref = request.refs[role]
        if not path.is_file() or ref.sha256 is None or ref.size_bytes is None:
            missing.append(role)
    return missing


def _ref_payload(ref: FileRef) -> dict[str, object]:
    return {"path": ref.path, "sha256": ref.sha256, "size_bytes": ref.size_bytes}


def init_request(
    episode_root: Path,
    *,
    episode_id: str,
    request_path: Path | None = None,
    overrides: Sequence[str] = (),
) -> Path:
    root = episode_root.resolve()
    if not root.is_dir():
        raise SubtitleReleaseError("episode root does not exist")
    if not episode_id.strip():
        raise SubtitleReleaseError("episode_id must be non-empty")
    if episode_id != root.name:
        raise SubtitleReleaseError("episode_id must exactly match the episode directory")
    paths = dict(_DEFAULT_INPUT_PATHS)
    for override in overrides:
        role, separator, relative = override.partition("=")
        if separator != "=" or role not in paths:
            raise SubtitleReleaseError("--path must use a known ROLE=RELATIVE_PATH")
        paths[role] = _relative_path(relative, label=f"--path {role}")
    inputs: dict[str, object] = {}
    for role in _REQUIRED_INPUT_ROLES:
        relative = paths[role]
        path = _resolve(root, relative, label=role)
        if path.is_file():
            raw = path.read_bytes()
            digest, size = _sha256(raw), len(raw)
        else:
            digest, size = None, None
        inputs[role] = {"path": relative, "sha256": digest, "size_bytes": size}
    payload = _canonical_bytes(
        {
            "schema_version": 1,
            "contract": REQUEST_CONTRACT,
            "episode_id": episode_id,
            "output_directory": "subtitle-release/memo-dual-audit-v1",
            "inputs": inputs,
        }
    )
    destination = (
        request_path.resolve()
        if request_path is not None
        else root / "subtitle-release-request.v1.json"
    )
    if destination.parent != root:
        raise SubtitleReleaseError("request path must be directly inside episode root")
    _write_atomic(destination, payload)
    return destination


def seal_request(path: Path) -> ReleaseRequest:
    request = load_request(path)
    inputs: dict[str, object] = {}
    for role, ref in request.refs.items():
        input_path = _resolve(request.root, ref.path, label=role)
        if input_path.is_file():
            raw = input_path.read_bytes()
            digest, size = _sha256(raw), len(raw)
        else:
            digest, size = None, None
        inputs[role] = {"path": ref.path, "sha256": digest, "size_bytes": size}
    payload = _canonical_bytes(
        {
            "schema_version": 1,
            "contract": REQUEST_CONTRACT,
            "episode_id": request.episode_id,
            "output_directory": request.output_directory,
            "inputs": inputs,
        }
    )
    if path.read_bytes() != payload:
        # The plan/request is mutable only before the terminal bundle exists.
        if any(output.exists() for output in _output_paths(request).values()):
            raise SubtitleReleaseError("cannot reseal request after release output exists")
        _replace_atomic(path, payload)
    return load_request(path)


def _component_cues(value: object, *, label: str, cue_count: int) -> tuple[int, ...]:
    if not isinstance(value, list) or not value:
        raise SubtitleReleaseError(f"{label} must be a non-empty cue list")
    if any(not isinstance(item, int) or isinstance(item, bool) for item in value):
        raise SubtitleReleaseError(f"{label} contains a non-integer cue")
    cues = tuple(value)
    if tuple(sorted(set(cues))) != cues:
        raise SubtitleReleaseError(f"{label} must be sorted and unique")
    if cues[0] < 1 or cues[-1] > cue_count:
        raise SubtitleReleaseError(f"{label} is outside the source SRT")
    if cues != tuple(range(cues[0], cues[-1] + 1)):
        raise SubtitleReleaseError(f"{label} must be contiguous")
    return cues


def _unresolved_population(
    raw: bytes, *, cue_count: int
) -> tuple[dict[tuple[int, ...], Mapping[str, Any]], dict[tuple[int, ...], Mapping[str, Any]]]:
    payload = _load_json(raw, label="unresolved components")
    items = payload.get("items")
    if not isinstance(items, list):
        raise SubtitleReleaseError("unresolved components items must be an array")
    if payload.get("item_count") != len(items):
        raise SubtitleReleaseError("unresolved component count mismatch")
    major: dict[tuple[int, ...], Mapping[str, Any]] = {}
    nonmajor: dict[tuple[int, ...], Mapping[str, Any]] = {}
    for index, item in enumerate(items):
        if not isinstance(item, dict):
            raise SubtitleReleaseError(f"unresolved item {index} must be an object")
        decision = item.get("arbitration", item)
        if not isinstance(decision, dict):
            raise SubtitleReleaseError(f"unresolved item {index} arbitration must be an object")
        cues = _component_cues(
            decision.get("cue_numbers"),
            label=f"unresolved item {index}",
            cue_count=cue_count,
        )
        target = major if decision.get("major_risk") is True else nonmajor
        if cues in major or cues in nonmajor:
            raise SubtitleReleaseError("duplicate unresolved cue component")
        target[cues] = item
    return major, nonmajor


def _evidence_ref(request: ReleaseRequest, value: object, *, label: str) -> tuple[FileRef, bytes]:
    ref = _file_ref(value, label=label)
    path = _resolve(request.root, ref.path, label=f"{label}.path")
    try:
        raw = path.read_bytes()
    except FileNotFoundError as exc:
        raise SubtitleReleaseError(f"missing major audio evidence: {label}") from exc
    if len(raw) != ref.size_bytes or _sha256(raw) != ref.sha256:
        raise SubtitleReleaseError(f"major audio evidence drift: {label}")
    return ref, raw


def _validate_model_evidence(
    request: ReleaseRequest,
    value: object,
    *,
    label: str,
    expected_family: str,
    component_id: str,
    component: tuple[int, ...],
    target_start_ms: int,
    target_end_ms: int,
    normalized_audio_sha256: str,
    normalized_audio_size_bytes: int,
) -> dict[str, object]:
    if not isinstance(value, dict):
        raise SubtitleReleaseError(f"{label} must be an object")
    _require_keys(value, label=label, required={"file"})
    ref, raw = _evidence_ref(request, value["file"], label=f"{label}.file")
    evidence = _load_json(raw, label=label)
    _require_keys(
        evidence,
        label=label,
        required={
            "schema_version",
            "contract",
            "episode_id",
            "component_id",
            "cue_numbers",
            "normalized_audio",
            "clip",
            "target",
            "engine",
            "raw_result",
            "execution",
            "recognition",
        },
    )
    if evidence["schema_version"] != 1 or evidence["contract"] != ASR_EVIDENCE_CONTRACT:
        raise SubtitleReleaseError(f"{label} has unsupported evidence contract")
    if evidence["episode_id"] != request.episode_id:
        raise SubtitleReleaseError(f"{label} belongs to another episode")
    if evidence["component_id"] != component_id or evidence["cue_numbers"] != list(component):
        raise SubtitleReleaseError(f"{label} belongs to another component")
    normalized = evidence["normalized_audio"]
    if not isinstance(normalized, dict) or normalized != {
        "sha256": normalized_audio_sha256,
        "size_bytes": normalized_audio_size_bytes,
    }:
        raise SubtitleReleaseError(f"{label} normalized audio binding failed")
    target = evidence["target"]
    if not isinstance(target, dict) or target != {
        "start_ms": target_start_ms,
        "end_ms": target_end_ms,
    }:
        raise SubtitleReleaseError(f"{label} target window differs from exact cues")
    clip = evidence["clip"]
    if not isinstance(clip, dict):
        raise SubtitleReleaseError(f"{label}.clip must be an object")
    _require_keys(
        clip,
        label=f"{label}.clip",
        required={"file", "start_ms", "end_ms", "pcm"},
    )
    clip_ref, clip_raw = _evidence_ref(request, clip["file"], label=f"{label}.clip.file")
    clip_start, clip_end = clip["start_ms"], clip["end_ms"]
    if (
        not isinstance(clip_start, int)
        or isinstance(clip_start, bool)
        or not isinstance(clip_end, int)
        or isinstance(clip_end, bool)
        or clip_start < 0
        or clip_end <= clip_start
        or clip_start > target_start_ms
        or clip_end < target_end_ms
    ):
        raise SubtitleReleaseError(f"{label} clip window does not cover target")
    pcm = _validate_source_clip(
        normalized_raw=_read_ref(request, "normalized_audio"),
        clip_raw=clip_raw,
        clip_start_ms=clip_start,
        clip_end_ms=clip_end,
    )
    if (
        pcm["channels"] not in {1, 2}
        or pcm["sample_rate_hz"] < 8_000
        or pcm["sample_width_bytes"] not in {2, 3}
        or pcm["frame_count"] <= 0
        or clip["pcm"] != pcm
    ):
        raise SubtitleReleaseError(f"{label} PCM identity mismatch")
    duration_ms = round(pcm["frame_count"] * 1000 / pcm["sample_rate_hz"])
    if abs(duration_ms - (clip_end - clip_start)) > 2:
        raise SubtitleReleaseError(f"{label} clip duration differs from window")
    engine = evidence["engine"]
    if not isinstance(engine, dict):
        raise SubtitleReleaseError(f"{label}.engine must be an object")
    _require_keys(
        engine,
        label=f"{label}.engine",
        required={"family", "model", "revision", "adapter", "runtime"},
    )
    if engine["family"] != expected_family:
        raise SubtitleReleaseError(f"{label} has wrong ASR family")
    for field in ("model", "revision", "adapter", "runtime"):
        if not isinstance(engine[field], str) or not engine[field].strip():
            raise SubtitleReleaseError(f"{label}.engine.{field} must be non-empty")
    if len(engine["revision"]) != 40 or any(
        character not in "0123456789abcdef" for character in engine["revision"].lower()
    ):
        raise SubtitleReleaseError(f"{label}.engine.revision is not an immutable commit")
    identity = f"{engine['model']} {engine['adapter']}".casefold()
    required_identity = "faster-whisper" if expected_family == "faster" else "qwen3-asr"
    if required_identity not in identity:
        raise SubtitleReleaseError(f"{label} has wrong model family identity")
    raw_ref, raw_result_raw = _evidence_ref(
        request, evidence["raw_result"], label=f"{label}.raw_result"
    )
    raw_result = _load_json(raw_result_raw, label=f"{label}.raw_result")
    _require_keys(
        raw_result,
        label=f"{label}.raw_result",
        required={
            "schema_version",
            "contract",
            "episode_id",
            "component_id",
            "engine_family",
            "completed",
            "exit_code",
            "provider_output",
            "transcript",
            "segments",
        },
    )
    if (
        raw_result["schema_version"] != 1
        or raw_result["contract"] != ASR_RAW_RESULT_CONTRACT
        or raw_result["episode_id"] != request.episode_id
        or raw_result["component_id"] != component_id
        or raw_result["engine_family"] != expected_family
        or raw_result["completed"] is not True
        or raw_result["exit_code"] != 0
    ):
        raise SubtitleReleaseError(f"{label} raw execution did not complete correctly")
    transcript, segments = raw_result["transcript"], raw_result["segments"]
    provider_ref, provider_raw = _evidence_ref(
        request,
        raw_result["provider_output"],
        label=f"{label}.raw_result.provider_output",
    )
    if not isinstance(transcript, str) or not transcript.strip():
        raise SubtitleReleaseError(f"{label} raw transcript is empty")
    if not isinstance(segments, list) or not segments:
        raise SubtitleReleaseError(f"{label} raw recognition segments are empty")
    for index, segment in enumerate(segments):
        if not isinstance(segment, dict) or set(segment) != {
            "start_ms",
            "end_ms",
            "text",
        }:
            raise SubtitleReleaseError(f"{label} raw segment {index} is malformed")
        if (
            not isinstance(segment["start_ms"], int)
            or not isinstance(segment["end_ms"], int)
            or segment["start_ms"] < 0
            or segment["end_ms"] <= segment["start_ms"]
            or segment["end_ms"] > clip_end - clip_start + 2
            or not isinstance(segment["text"], str)
            or not segment["text"].strip()
        ):
            raise SubtitleReleaseError(f"{label} raw segment {index} is invalid")
    provider = _load_json(provider_raw, label=f"{label}.provider_output")
    _require_keys(
        provider,
        label=f"{label}.provider_output",
        required={
            "schema_version",
            "contract",
            "episode_id",
            "component_id",
            "engine",
            "clip",
            "completed",
            "exit_code",
            "transcript",
            "segments",
            "provider_result",
        },
    )
    if (
        provider["schema_version"] != 1
        or provider["contract"] != ASR_PROVIDER_OUTPUT_CONTRACT
        or provider["episode_id"] != request.episode_id
        or provider["component_id"] != component_id
        or provider["engine"] != engine
        or provider["clip"] != _ref_payload(clip_ref)
        or provider["completed"] is not True
        or provider["exit_code"] != 0
        or provider["transcript"] != transcript
        or provider["segments"] != segments
    ):
        raise SubtitleReleaseError(f"{label} provider output differs from recognition")
    execution = evidence["execution"]
    if execution != {"completed": True, "exit_code": 0}:
        raise SubtitleReleaseError(f"{label} execution receipt is incomplete")
    recognition = evidence["recognition"]
    if recognition != {
        "text": transcript,
        "segments": segments,
        "raw_result_sha256": _sha256(raw_result_raw),
    }:
        raise SubtitleReleaseError(f"{label} recognition differs from raw result")
    relative_target_start = target_start_ms - clip_start
    relative_target_end = target_end_ms - clip_start
    target_observation = "".join(
        segment["text"]
        for segment in segments
        if segment["end_ms"] > relative_target_start and segment["start_ms"] < relative_target_end
    )
    if not target_observation.strip():
        raise SubtitleReleaseError(f"{label} has no recognition over the target window")
    return {
        "file": _ref_payload(ref),
        "clip": _ref_payload(clip_ref),
        "raw_result": _ref_payload(raw_ref),
        "provider_output": _ref_payload(provider_ref),
        "engine": engine,
        "target_observation": target_observation,
    }


def _validate_acceptance_worker_audits(
    request: ReleaseRequest,
    receipt: Mapping[str, Any],
    *,
    kind: str,
    expected: Mapping[str, object],
) -> None:
    contract = (
        "memo-recognition-worker-audit-v1" if kind == "recognition" else "memo-cue-worker-audit-v1"
    )
    if receipt.get("episode_id") != request.episode_id:
        raise SubtitleReleaseError(f"Memo {kind} acceptance belongs to another episode")
    refs = receipt.get("agent_audits")
    if not isinstance(refs, list) or len(refs) != 2:
        raise SubtitleReleaseError(f"Memo {kind} acceptance requires two worker audits")
    workers: set[str] = set()
    common = {
        "schema_version",
        "contract",
        "episode_id",
        "worker_id",
        "normalized_audio_sha256",
        "normalized_audio_size_bytes",
        "source_export_sha256",
        "source_export_size_bytes",
        "review_manifest_sha256",
        "reviewed_item_count",
        "qc_passed",
        "accepted",
        "unresolved_findings",
    }
    required = common | (
        {"token_export_sha256", "memo_execution_receipt_sha256"}
        if kind == "recognition"
        else {"recognition_manifest_sha256"}
    )
    for index, value in enumerate(refs):
        label = f"Memo {kind} worker audit {index}"
        if not isinstance(value, dict):
            raise SubtitleReleaseError(f"{label} reference must be an object")
        _require_keys(
            value,
            label=f"{label} reference",
            required={"contract", "worker_id", "path", "sha256", "size_bytes"},
        )
        if value["contract"] != contract:
            raise SubtitleReleaseError(f"{label} has wrong contract")
        ref, raw = _evidence_ref(
            request,
            {key: value[key] for key in ("path", "sha256", "size_bytes")},
            label=label,
        )
        audit = _load_json(raw, label=label)
        _require_keys(audit, label=label, required=required)
        worker_id = audit["worker_id"]
        mismatched = [
            key for key, expected_value in expected.items() if audit[key] != expected_value
        ]
        if (
            audit["schema_version"] != 1
            or audit["contract"] != contract
            or audit["episode_id"] != request.episode_id
            or value["worker_id"] != worker_id
            or ref.sha256 != value["sha256"]
            or not isinstance(worker_id, str)
            or not worker_id.strip()
            or audit["accepted"] is not True
            or audit["qc_passed"] is not True
            or audit["unresolved_findings"] != []
            or mismatched
        ):
            raise SubtitleReleaseError(f"{label} source/review binding failed: {mismatched}")
        workers.add(worker_id)
    if len(workers) != 2:
        raise SubtitleReleaseError(f"Memo {kind} audits are not independent")


def _resolve_execution_path(root: Path, value: str, *, label: str) -> Path:
    path = Path(value)
    if path.is_absolute() or path.drive or ".." in path.parts:
        raise SubtitleReleaseError(f"{label} must be an episode-relative path")
    resolved = (root / path).resolve()
    try:
        resolved.relative_to(root)
    except ValueError as exc:
        raise SubtitleReleaseError(f"{label} escapes the episode root") from exc
    return resolved


def _fresh_verify_memo_execution(
    request: ReleaseRequest, raw_ref: object
) -> MemoExecutionReceiptRefV1:
    try:
        ref = MemoExecutionReceiptRefV1.model_validate(raw_ref, strict=True)
        receipt_path = _resolve_execution_path(
            request.root, ref.path, label="Memo execution receipt"
        )
        input_wav = _resolve_execution_path(
            request.root, ref.input_wav_path, label="Memo execution input"
        )
        output_srt = _resolve_execution_path(
            request.root, ref.output_srt_path, label="Memo execution output"
        )
        stdout = _resolve_execution_path(
            request.root, ref.stdout_path, label="Memo execution stdout"
        )
        stderr = _resolve_execution_path(
            request.root, ref.stderr_path, label="Memo execution stderr"
        )
        if measure_regular_file(receipt_path) != (ref.sha256, ref.size_bytes):
            raise SubtitleReleaseError("Memo execution receipt bytes drifted")
        declaration = load_memo_bundled_runner_execution_receipt(receipt_path)
        verified = load_verified_memo_bundled_runner_execution(
            request=MemoBundledRunnerRequest(
                runner=Path(ref.runner_path),
                model=Path(ref.model_path),
                input_wav=input_wav,
                output_srt=output_srt,
                stdout_output=stdout,
                stderr_output=stderr,
                receipt_output=receipt_path,
                gpu=declaration.gpu,
                language=declaration.language,
                prompt=declaration.prompt,
                max_context=declaration.max_context,
                max_len=declaration.max_len,
            )
        )
    except SubtitleReleaseError:
        raise
    except (
        AdapterInputError,
        AdapterIntegrityError,
        OSError,
        TypeError,
        ValueError,
    ) as exc:
        raise SubtitleReleaseError(f"invalid Memo execution lineage: {exc}") from exc
    actual = {
        "contract": verified.contract,
        "path": ref.path,
        "runner_path": str(Path(ref.runner_path).resolve()),
        "model_path": str(Path(ref.model_path).resolve()),
        "input_wav_path": ref.input_wav_path,
        "output_srt_path": ref.output_srt_path,
        "stdout_path": ref.stdout_path,
        "stderr_path": ref.stderr_path,
        "sha256": ref.sha256,
        "size_bytes": ref.size_bytes,
        "runner_sha256": verified.runner_sha256,
        "model_sha256": verified.model_sha256,
        "input_wav_sha256": verified.input_wav_sha256,
        "output_srt_sha256": verified.output_srt_sha256,
        "stdout_sha256": verified.stdout_sha256,
        "stderr_sha256": verified.stderr_sha256,
        "exit_code": verified.exit_code,
    }
    actual_ref = MemoExecutionReceiptRefV1.model_validate(actual, strict=True)
    if actual_ref != ref:
        raise SubtitleReleaseError("Memo execution reference differs from fresh replay")
    return ref


def _validate_episode_bindings(
    request: ReleaseRequest, inputs: Mapping[str, bytes], memo_cue_count: int
) -> tuple[str, str]:
    handoff = _load_json(inputs["normalized_handoff"], label="normalized handoff")
    audio_digest = _sha256(inputs["normalized_audio"])
    if handoff.get("contract") != "normalized-audio-handoff-v1":
        raise SubtitleReleaseError("unsupported normalized handoff contract")
    if handoff.get("normalized_audio_sha256") != audio_digest or handoff.get(
        "normalized_audio_size_bytes"
    ) != len(inputs["normalized_audio"]):
        raise SubtitleReleaseError("normalized audio differs from handoff")
    memo_digest = _sha256(inputs["memo_srt"])
    recognition = _load_json(inputs["memo_recognition_evidence"], label="Memo recognition evidence")
    if recognition.get("contract") != "memo-recognition-evidence-v1":
        raise SubtitleReleaseError("unsupported Memo recognition evidence contract")
    if (
        recognition.get("normalized_audio_sha256") != audio_digest
        or recognition.get("normalized_audio_size_bytes") != len(inputs["normalized_audio"])
        or recognition.get("source_export_sha256") != memo_digest
        or recognition.get("source_export_size_bytes") != len(inputs["memo_srt"])
        or recognition.get("model") != "ggml-large-v2.bin"
        or recognition.get("memo_version") != "1.7.5"
        or recognition.get("unresolved_findings") != []
    ):
        raise SubtitleReleaseError("Memo recognition evidence binding failed")
    execution_ref = _fresh_verify_memo_execution(request, recognition.get("memo_execution_receipt"))
    if execution_ref.input_wav_sha256 != audio_digest:
        raise SubtitleReleaseError("Memo execution used another normalized audio file")
    if recognition.get("raw_source_export_sha256") is None:
        if execution_ref.output_srt_sha256 != memo_digest:
            raise SubtitleReleaseError("Memo SRT is not the sealed execution output")
    elif execution_ref.output_srt_sha256 != recognition.get("raw_source_export_sha256"):
        raise SubtitleReleaseError("Memo repaired source is not based on the execution output")
    if (
        not isinstance(recognition.get("tokens"), list)
        or len(recognition["tokens"]) != memo_cue_count
    ):
        raise SubtitleReleaseError("Memo recognition coverage differs from cue count")
    recognition_acceptance = _load_json(
        inputs["memo_recognition_acceptance"], label="Memo recognition acceptance"
    )
    cue_acceptance = _load_json(inputs["memo_cue_acceptance"], label="Memo cue acceptance")
    if (
        recognition_acceptance.get("contract") != "accepted-memo-recognition-v1"
        or recognition_acceptance.get("accepted") is not True
        or recognition_acceptance.get("normalized_handoff_manifest_sha256")
        != _sha256(inputs["normalized_handoff"])
        or recognition_acceptance.get("normalized_audio_sha256") != audio_digest
        or recognition_acceptance.get("source_export_sha256") != memo_digest
        or recognition_acceptance.get("source_export_size_bytes") != len(inputs["memo_srt"])
        or not _is_sha256(recognition_acceptance.get("review_manifest_sha256"))
        or not _is_sha256(recognition_acceptance.get("token_export_sha256"))
        or recognition_acceptance.get("reviewer") != "agent-quorum"
        or recognition_acceptance.get("unresolved_findings") != []
        or recognition_acceptance.get("memo_execution_receipt")
        != recognition.get("memo_execution_receipt")
    ):
        raise SubtitleReleaseError("Memo recognition acceptance binding failed")
    _validate_acceptance_worker_audits(
        request,
        recognition_acceptance,
        kind="recognition",
        expected={
            "normalized_audio_sha256": audio_digest,
            "normalized_audio_size_bytes": len(inputs["normalized_audio"]),
            "source_export_sha256": memo_digest,
            "source_export_size_bytes": len(inputs["memo_srt"]),
            "review_manifest_sha256": recognition_acceptance.get("review_manifest_sha256"),
            "token_export_sha256": recognition_acceptance.get("token_export_sha256"),
            "memo_execution_receipt_sha256": execution_ref.sha256,
            "reviewed_item_count": memo_cue_count,
        },
    )
    if (
        cue_acceptance.get("contract") != "accepted-memo-gui-srt-v1"
        or cue_acceptance.get("accepted") is not True
        or cue_acceptance.get("source_export_sha256") != memo_digest
        or cue_acceptance.get("source_export_size_bytes") != len(inputs["memo_srt"])
        or cue_acceptance.get("recognition_manifest_sha256")
        != _sha256(inputs["memo_recognition_evidence"])
        or not _is_sha256(cue_acceptance.get("review_manifest_sha256"))
        or cue_acceptance.get("reviewer") != "agent-quorum"
        or cue_acceptance.get("unresolved_findings") != []
    ):
        raise SubtitleReleaseError("Memo cue acceptance binding failed")
    _validate_acceptance_worker_audits(
        request,
        cue_acceptance,
        kind="cue",
        expected={
            "normalized_audio_sha256": audio_digest,
            "normalized_audio_size_bytes": len(inputs["normalized_audio"]),
            "source_export_sha256": memo_digest,
            "source_export_size_bytes": len(inputs["memo_srt"]),
            "review_manifest_sha256": cue_acceptance.get("review_manifest_sha256"),
            "recognition_manifest_sha256": _sha256(inputs["memo_recognition_evidence"]),
            "reviewed_item_count": memo_cue_count,
        },
    )
    return audio_digest, memo_digest


def _fresh_text_replay(
    inputs: Mapping[str, bytes], *, cue_count: int, episode_id: str
) -> tuple[bytes, bytes, bytes, tuple[str, str]]:
    audit_agents: list[str] = []
    for role in ("text_audit_a", "text_audit_b"):
        audit = _load_json(inputs[role], label=role)
        if audit.get("cues_reviewed") != cue_count:
            raise SubtitleReleaseError(f"{role} does not cover every Memo cue")
        agent = audit.get("agent")
        if not isinstance(agent, str) or not agent.strip():
            raise SubtitleReleaseError(f"{role} has no agent identity")
        audit_agents.append(agent)
    if audit_agents[0] == audit_agents[1]:
        raise SubtitleReleaseError("text audits must come from independent agents")
    try:
        replay = apply_official_arbitration(
            episode_id=episode_id,
            srt_bytes=inputs["memo_srt"],
            audit_a_bytes=inputs["text_audit_a"],
            audit_b_bytes=inputs["text_audit_b"],
            base_corrected_bytes=inputs["base_corrected_srt"],
            base_ledger_bytes=inputs["base_consensus_ledger"],
            base_needs_audio_bytes=inputs["base_needs_audio"],
            arbitration_bytes=inputs["arbitration"],
        )
    except SimpleStep7Error as exc:
        raise SubtitleReleaseError(f"fresh text audit replay failed: {exc}") from exc
    expected = (
        inputs["text_corrected_srt"],
        inputs["text_arbitration_ledger"],
        inputs["unresolved_components"],
    )
    if replay != expected:
        raise SubtitleReleaseError("sealed text audit outputs differ from fresh replay")
    return (*replay, (audit_agents[0], audit_agents[1]))


def _mapping_text(
    value: object, *, label: str, cues: tuple[int, ...], current: Mapping[int, str]
) -> dict[int, str]:
    if not isinstance(value, dict):
        raise SubtitleReleaseError(f"{label} must be an object")
    result: dict[int, str] = {}
    for key, text in value.items():
        try:
            cue = int(key)
        except (TypeError, ValueError) as exc:
            raise SubtitleReleaseError(f"{label} contains an invalid cue key") from exc
        if str(cue) != str(key) or cue not in cues:
            raise SubtitleReleaseError(f"{label} contains an out-of-component cue")
        if not isinstance(text, str) or not text or "\r" in text or "\n\n" in text:
            raise SubtitleReleaseError(f"{label} contains invalid SRT text")
        result[cue] = text
    if label.endswith("original") and result != {cue: current[cue] for cue in cues}:
        raise SubtitleReleaseError(f"{label} differs from text-audited SRT")
    return result


def _normalize_recognition_text(value: str) -> str:
    normalized = unicodedata.normalize("NFKC", value).casefold()
    return "".join(
        character
        for character in normalized
        if not character.isspace() and unicodedata.category(character)[0] not in {"P", "S"}
    )


def _audit_candidates(unresolved_item: Mapping[str, Any]) -> tuple[str, ...]:
    decision = unresolved_item.get("arbitration", unresolved_item)
    if not isinstance(decision, dict):
        raise SubtitleReleaseError("unresolved arbitration is malformed")
    candidates: list[str] = []
    for field in ("a_proposals", "b_proposals"):
        proposals = decision.get(field)
        if not isinstance(proposals, list) or any(
            value is not None and (not isinstance(value, str) or not value.strip())
            for value in proposals
        ):
            raise SubtitleReleaseError("unresolved audit proposals are malformed")
        candidates.extend(value for value in proposals if value is not None)
    return tuple(dict.fromkeys(candidates))


def _dual_asr_supported_candidate(
    unresolved_item: Mapping[str, Any],
    *,
    faster_observation: str,
    qwen_observation: str,
) -> str | None:
    faster = _normalize_recognition_text(faster_observation)
    qwen = _normalize_recognition_text(qwen_observation)
    supported = [
        candidate
        for candidate in _audit_candidates(unresolved_item)
        if _normalize_recognition_text(candidate) == faster == qwen
    ]
    unique = tuple(dict.fromkeys(supported))
    return unique[0] if len(unique) == 1 else None


def _apply_audio_decisions(
    request: ReleaseRequest,
    *,
    text_srt: bytes,
    unresolved_raw: bytes,
    audio_decisions_raw: bytes,
    normalized_audio_sha256: str,
    normalized_audio_size_bytes: int,
) -> tuple[bytes, dict[str, object]]:
    cues = _parse_srt(text_srt)
    cue_text = {cue.number: cue.text for cue in cues}
    major, nonmajor = _unresolved_population(unresolved_raw, cue_count=len(cues))
    ledger = _load_json(audio_decisions_raw, label="audio decisions")
    _require_keys(
        ledger,
        label="audio decisions",
        required={
            "schema_version",
            "contract",
            "episode_id",
            "source_srt_sha256",
            "source_unresolved_sha256",
            "major_components",
            "nonmajor_retained_original_components",
        },
    )
    if ledger["schema_version"] != 1 or ledger["contract"] != AUDIO_DECISIONS_CONTRACT:
        raise SubtitleReleaseError("unsupported audio decisions contract")
    if ledger["episode_id"] != request.episode_id:
        raise SubtitleReleaseError("audio decisions belong to another episode")
    if ledger["source_srt_sha256"] != _sha256(text_srt) or ledger[
        "source_unresolved_sha256"
    ] != _sha256(unresolved_raw):
        raise SubtitleReleaseError("audio decisions source binding failed")
    major_items = ledger["major_components"]
    nonmajor_items = ledger["nonmajor_retained_original_components"]
    if not isinstance(major_items, list) or not isinstance(nonmajor_items, list):
        raise SubtitleReleaseError("audio decision populations must be arrays")
    replacements: dict[int, str] = {}
    reviewed: set[tuple[int, ...]] = set()
    retained_major: list[dict[str, object]] = []
    evidence_summary: list[dict[str, object]] = []
    for index, item in enumerate(major_items):
        if not isinstance(item, dict):
            raise SubtitleReleaseError(f"major component {index} must be an object")
        _require_keys(
            item,
            label=f"major component {index}",
            required={
                "component_id",
                "cue_numbers",
                "decision",
                "reason_code",
                "original",
                "replacements",
                "evidence",
            },
        )
        component_id = item["component_id"]
        if not isinstance(component_id, str) or not component_id.strip():
            raise SubtitleReleaseError("major component_id must be non-empty")
        component = _component_cues(
            item["cue_numbers"], label=f"major component {index}", cue_count=len(cues)
        )
        if component not in major or component in reviewed:
            raise SubtitleReleaseError("major audio population differs from unresolved risks")
        expected_component_id = "cue-" + "-".join(str(number) for number in component)
        if component_id != expected_component_id:
            raise SubtitleReleaseError("major component_id differs from cue identity")
        reviewed.add(component)
        decision, reason_code = item["decision"], item["reason_code"]
        if decision not in _AUDIO_DECISIONS:
            raise SubtitleReleaseError(f"unknown audio decision: {decision}")
        if reason_code not in _AUDIO_REASON_CODES:
            raise SubtitleReleaseError(f"unknown audio reason category: {reason_code}")
        _mapping_text(
            item["original"],
            label=f"major component {index}.original",
            cues=component,
            current=cue_text,
        )
        replacement = _mapping_text(
            item["replacements"],
            label=f"major component {index}.replacements",
            cues=component,
            current=cue_text,
        )
        evidence = item["evidence"]
        if not isinstance(evidence, dict):
            raise SubtitleReleaseError("major component evidence must be an object")
        _require_keys(
            evidence,
            label=f"major component {index}.evidence",
            required={"clip", "faster", "qwen"},
        )
        clip_ref, _ = _evidence_ref(
            request, evidence["clip"], label=f"major component {index}.evidence.clip"
        )
        faster = _validate_model_evidence(
            request,
            evidence["faster"],
            label=f"major component {index}.evidence.faster",
            expected_family="faster",
            component_id=component_id,
            component=component,
            target_start_ms=cues[component[0] - 1].start_ms,
            target_end_ms=cues[component[-1] - 1].end_ms,
            normalized_audio_sha256=normalized_audio_sha256,
            normalized_audio_size_bytes=normalized_audio_size_bytes,
        )
        qwen = _validate_model_evidence(
            request,
            evidence["qwen"],
            label=f"major component {index}.evidence.qwen",
            expected_family="qwen",
            component_id=component_id,
            component=component,
            target_start_ms=cues[component[0] - 1].start_ms,
            target_end_ms=cues[component[-1] - 1].end_ms,
            normalized_audio_sha256=normalized_audio_sha256,
            normalized_audio_size_bytes=normalized_audio_size_bytes,
        )
        if faster["clip"] != _ref_payload(clip_ref) or qwen["clip"] != _ref_payload(clip_ref):
            raise SubtitleReleaseError("dual-ASR evidence does not share exact clip")
        supported = (
            _dual_asr_supported_candidate(
                major[component],
                faster_observation=str(faster["target_observation"]),
                qwen_observation=str(qwen["target_observation"]),
            )
            if len(component) == 1
            else None
        )
        expected_decision = (
            "accept_replacement" if supported is not None else "retain_memo_original"
        )
        expected_reason = "dual_asr_consensus" if supported is not None else "dual_asr_conflict"
        expected_replacement = {component[0]: supported} if supported is not None else {}
        if (
            decision != expected_decision
            or reason_code != expected_reason
            or replacement != expected_replacement
        ):
            raise SubtitleReleaseError("audio decision differs from fresh audit/dual-ASR replay")
        if decision == "accept_replacement":
            if reason_code != "dual_asr_consensus" or not replacement:
                raise SubtitleReleaseError("accepted audio correction lacks safe quorum")
            for cue, text in replacement.items():
                if cue in replacements:
                    raise SubtitleReleaseError("overlapping accepted audio corrections")
                replacements[cue] = text
        else:
            if reason_code not in {"dual_asr_conflict", "dual_asr_unresolved"} or replacement:
                raise SubtitleReleaseError("retained Memo decision is not fail-closed")
            retained_major.append(
                {
                    "component_id": component_id,
                    "cue_numbers": list(component),
                    "reason_code": reason_code,
                }
            )
        evidence_summary.append(
            {
                "component_id": component_id,
                "cue_numbers": list(component),
                "clip": _ref_payload(clip_ref),
                "faster": faster,
                "qwen": qwen,
            }
        )
    if reviewed != set(major):
        raise SubtitleReleaseError("not every major-risk component has dual-ASR evidence")
    retained_nonmajor: set[tuple[int, ...]] = set()
    for index, item in enumerate(nonmajor_items):
        if not isinstance(item, dict):
            raise SubtitleReleaseError(f"nonmajor component {index} must be an object")
        _require_keys(
            item,
            label=f"nonmajor component {index}",
            required={"component_id", "cue_numbers", "original"},
        )
        component = _component_cues(
            item["cue_numbers"],
            label=f"nonmajor component {index}",
            cue_count=len(cues),
        )
        if component not in nonmajor or component in retained_nonmajor:
            raise SubtitleReleaseError("nonmajor retained population differs from unresolved risks")
        retained_nonmajor.add(component)
        _mapping_text(
            item["original"],
            label=f"nonmajor component {index}.original",
            cues=component,
            current=cue_text,
        )
    if retained_nonmajor != set(nonmajor):
        raise SubtitleReleaseError("not every nonmajor unresolved component retained Memo text")
    release_srt = _render_srt(cues, replacements)
    release_cues = _parse_srt(release_srt)
    if [(cue.start_ms, cue.end_ms) for cue in release_cues] != [
        (cue.start_ms, cue.end_ms) for cue in cues
    ]:
        raise SubtitleReleaseError("audio correction changed accepted Memo timing")
    summary: dict[str, object] = {
        "major_component_count": len(major),
        "major_audio_reviewed_count": len(reviewed),
        "accepted_major_component_count": sum(
            1 for item in major_items if item["decision"] == "accept_replacement"
        ),
        "retained_major_component_count": len(retained_major),
        "nonmajor_retained_original_count": len(retained_nonmajor),
        "changed_cue_count": len(replacements),
        "changed_cue_ids": sorted(replacements),
        "retained_major": retained_major,
        "evidence": evidence_summary,
    }
    return release_srt, summary


def build_release(request: ReleaseRequest) -> ReleaseBundle:
    inputs = {role: _read_ref(request, role) for role in _REQUIRED_INPUT_ROLES}
    memo_cues = _parse_srt(inputs["memo_srt"])
    audio_digest, memo_digest = _validate_episode_bindings(request, inputs, len(memo_cues))
    text_srt, text_ledger, unresolved, audit_agents = _fresh_text_replay(
        inputs, cue_count=len(memo_cues), episode_id=request.episode_id
    )
    text_cues = _parse_srt(text_srt)
    if [(cue.start_ms, cue.end_ms) for cue in text_cues] != [
        (cue.start_ms, cue.end_ms) for cue in memo_cues
    ]:
        raise SubtitleReleaseError("text correction changed accepted Memo timing")
    release_srt, audio_summary = _apply_audio_decisions(
        request,
        text_srt=text_srt,
        unresolved_raw=unresolved,
        audio_decisions_raw=inputs["audio_decisions"],
        normalized_audio_sha256=audio_digest,
        normalized_audio_size_bytes=len(inputs["normalized_audio"]),
    )
    release_cues = _parse_srt(release_srt)
    input_files = {role: _ref_payload(request.refs[role]) for role in _REQUIRED_INPUT_ROLES}
    ledger_payload = {
        "schema_version": 1,
        "contract": RELEASE_CONTRACT,
        "policy_version": POLICY_VERSION,
        "episode_id": request.episode_id,
        "status": "complete",
        "inputs": input_files,
        "normalized_audio_sha256": audio_digest,
        "memo_srt_sha256": memo_digest,
        "text_audit": {
            "independent_agent_count": 2,
            "agents": list(audit_agents),
            "cue_coverage_count": len(memo_cues),
            "complete_coverage": True,
            "fresh_arbitration_replay": True,
            "text_ledger_sha256": _sha256(text_ledger),
            "unresolved_components_sha256": _sha256(unresolved),
        },
        "audio_audit": audio_summary,
        "release_policy": {
            "primary_text_authority": "accepted Memo large-v2",
            "text_correction": "two independent audits plus strict arbitration",
            "major_risk_audio": "Faster plus Qwen dual-ASR evidence required",
            "major_conflict": "retain Memo text",
            "nonmajor_unresolved": "retain Memo text",
        },
        "cue_count": len(release_cues),
        "non_positive_duration_count": 0,
        "overlap_count": 0,
        "byte_identical_rerun": True,
        "release_srt": {
            "path": "release.srt",
            "sha256": _sha256(release_srt),
            "size_bytes": len(release_srt),
        },
    }
    ledger = _canonical_bytes(ledger_payload)
    manifest_payload = {
        "schema_version": 1,
        "contract": EXPORT_CONTRACT,
        "episode_id": request.episode_id,
        "canonical_release_srt": "release.srt",
        "canonical_release_srt_sha256": _sha256(release_srt),
        "release_ledger": "release-ledger.json",
        "release_ledger_sha256": _sha256(ledger),
        "file_count": 2,
        "files": [
            {
                "path": "release.srt",
                "sha256": _sha256(release_srt),
                "size_bytes": len(release_srt),
            },
            {
                "path": "release-ledger.json",
                "sha256": _sha256(ledger),
                "size_bytes": len(ledger),
            },
        ],
    }
    manifest = _canonical_bytes(manifest_payload)
    handoff_payload = {
        "schema_version": 1,
        "contract": STAGE5_HANDOFF_CONTRACT,
        "episode_id": request.episode_id,
        "release_srt": {
            "path": "release.srt",
            "sha256": _sha256(release_srt),
            "size_bytes": len(release_srt),
        },
        "release_ledger": {
            "path": "release-ledger.json",
            "sha256": _sha256(ledger),
            "size_bytes": len(ledger),
        },
        "export_manifest": {
            "path": "export-manifest.json",
            "sha256": _sha256(manifest),
            "size_bytes": len(manifest),
        },
        "gates": {
            "major_component_count": audio_summary["major_component_count"],
            "major_audio_reviewed_count": audio_summary["major_audio_reviewed_count"],
            "nonmajor_retained_original_count": audio_summary["nonmajor_retained_original_count"],
            "cue_count": len(release_cues),
            "non_positive_duration_count": 0,
            "overlap_count": 0,
            "byte_identical_rerun": True,
        },
    }
    return ReleaseBundle(
        release_srt=release_srt,
        release_ledger=ledger,
        export_manifest=manifest,
        stage5_handoff=_canonical_bytes(handoff_payload),
    )


def _output_paths(request: ReleaseRequest) -> dict[str, Path]:
    root = _resolve(request.root, request.output_directory, label="output_directory")
    return {
        "release_srt": root / "release.srt",
        "release_ledger": root / "release-ledger.json",
        "export_manifest": root / "export-manifest.json",
        "stage5_handoff": root / "STAGE5-HANDOFF.json",
    }


def _bundle_payloads(bundle: ReleaseBundle) -> dict[str, bytes]:
    return {
        "release_srt": bundle.release_srt,
        "release_ledger": bundle.release_ledger,
        "export_manifest": bundle.export_manifest,
        "stage5_handoff": bundle.stage5_handoff,
    }


def _preflight_outputs(paths: Mapping[str, Path], payloads: Mapping[str, bytes]) -> None:
    for role, path in paths.items():
        if path.exists() and (not path.is_file() or path.read_bytes() != payloads[role]):
            raise SubtitleReleaseError(f"release overwrite refused: {path}")


def _write_atomic(path: Path, payload: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        if path.read_bytes() != payload:
            raise SubtitleReleaseError(f"release overwrite refused: {path}")
        return
    fd, temp_name = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp_name, path)
    finally:
        try:
            Path(temp_name).unlink()
        except FileNotFoundError:
            pass


def _replace_atomic(path: Path, payload: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temp_name = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp_name, path)
    finally:
        try:
            Path(temp_name).unlink()
        except FileNotFoundError:
            pass


def finalize(request: ReleaseRequest) -> ReleaseBundle:
    bundle = build_release(request)
    paths = _output_paths(request)
    payloads = _bundle_payloads(bundle)
    _preflight_outputs(paths, payloads)
    # Content commits first; the ledger, manifest, and Stage 5 handoff commit last.
    _write_atomic(paths["release_srt"], payloads["release_srt"])
    _write_atomic(paths["release_ledger"], payloads["release_ledger"])
    _write_atomic(paths["export_manifest"], payloads["export_manifest"])
    _write_atomic(paths["stage5_handoff"], payloads["stage5_handoff"])
    return bundle


def status(request: ReleaseRequest) -> tuple[int, bytes]:
    for role, ref in request.refs.items():
        path = _resolve(request.root, ref.path, label=role)
        if path.is_file() and ref.sha256 is not None and ref.size_bytes is not None:
            _read_ref(request, role)
    missing = _missing_inputs(request)
    paths = _output_paths(request)
    if missing:
        phase = next(
            phase for phase, roles in _PHASE_ROLES if any(role in missing for role in roles)
        )
        payload = {
            "schema_version": 1,
            "contract": STATUS_CONTRACT,
            "episode_id": request.episode_id,
            "phase": phase,
            "ready": False,
            "complete": False,
            "missing_inputs": missing,
            "expected_inputs": {role: request.refs[role].path for role in missing},
            "expected_outputs": {
                role: path.relative_to(request.root).as_posix() for role, path in paths.items()
            },
        }
        return 3, _canonical_bytes(payload)
    bundle = build_release(request)
    payloads = _bundle_payloads(bundle)
    conflicts = [
        role
        for role, path in paths.items()
        if path.exists() and (not path.is_file() or path.read_bytes() != payloads[role])
    ]
    if conflicts:
        raise SubtitleReleaseError(f"release output drift: {sorted(conflicts)}")
    complete = all(path.is_file() for path in paths.values())
    payload = {
        "schema_version": 1,
        "contract": STATUS_CONTRACT,
        "episode_id": request.episode_id,
        "phase": "complete" if complete else "ready_to_finalize",
        "ready": True,
        "complete": complete,
        "missing_inputs": [],
        "expected_inputs": {},
        "expected_outputs": {
            role: path.relative_to(request.root).as_posix() for role, path in paths.items()
        },
        "release_srt_sha256": _sha256(bundle.release_srt),
    }
    return 0, _canonical_bytes(payload)


def verify_legacy_bundle(root: Path, *, expected_sha256: str | None = None) -> bytes:
    legacy_root = root.resolve()
    release = legacy_root / "release" / "release-v1-corrected.srt"
    ledger_path = legacy_root / "release" / "release-v1-ledger.json"
    manifest_path = legacy_root / "EXPORT-MANIFEST.json"
    release_raw, ledger_raw, manifest_raw = (
        release.read_bytes(),
        ledger_path.read_bytes(),
        manifest_path.read_bytes(),
    )
    digest = _sha256(release_raw)
    ledger = _load_json(ledger_raw, label="legacy release ledger")
    manifest = _load_json(manifest_raw, label="legacy export manifest")
    if ledger.get("output_srt_sha256") != digest:
        raise SubtitleReleaseError("legacy ledger does not bind release SRT")
    if manifest.get("canonical_release_srt_sha256") != digest:
        raise SubtitleReleaseError("legacy manifest does not bind release SRT")
    if expected_sha256 is not None and digest != expected_sha256:
        raise SubtitleReleaseError("legacy release SRT digest differs from expected")
    cues = _parse_srt(release_raw)
    payload = {
        "schema_version": 1,
        "contract": "podcast-subtitle-memo-dual-audit-legacy-read-compatibility-v1",
        "compatible": True,
        "cue_count": len(cues),
        "release_srt_sha256": digest,
        "release_srt_size_bytes": len(release_raw),
        "legacy_ledger_sha256": _sha256(ledger_raw),
        "legacy_manifest_sha256": _sha256(manifest_raw),
    }
    return _canonical_bytes(payload)


def _existing_path_ref(root: Path, path: Path, *, label: str) -> dict[str, object]:
    resolved = path.resolve()
    try:
        relative = resolved.relative_to(root).as_posix()
    except ValueError as exc:
        raise SubtitleReleaseError(f"{label} must stay inside episode root") from exc
    if not resolved.is_file():
        raise SubtitleReleaseError(f"{label} does not exist")
    raw = resolved.read_bytes()
    return {"path": relative, "sha256": _sha256(raw), "size_bytes": len(raw)}


def _read_pcm_wav(raw: bytes, *, label: str) -> tuple[dict[str, int], bytes]:
    try:
        with wave.open(io.BytesIO(raw), "rb") as handle:
            if handle.getcomptype() != "NONE":
                raise SubtitleReleaseError(f"{label} is not PCM WAV")
            pcm = {
                "channels": handle.getnchannels(),
                "sample_rate_hz": handle.getframerate(),
                "sample_width_bytes": handle.getsampwidth(),
                "frame_count": handle.getnframes(),
            }
            frames = handle.readframes(handle.getnframes())
    except (EOFError, wave.Error) as exc:
        raise SubtitleReleaseError(f"{label} is not a valid PCM WAV") from exc
    if (
        pcm["channels"] not in {1, 2}
        or pcm["sample_rate_hz"] < 8_000
        or pcm["sample_width_bytes"] not in {2, 3}
        or pcm["frame_count"] <= 0
    ):
        raise SubtitleReleaseError(f"{label} PCM format is unsupported")
    return pcm, frames


def _extract_pcm_window(
    normalized_raw: bytes, *, start_ms: int, end_ms: int
) -> tuple[bytes, dict[str, int], int, int]:
    pcm, frames = _read_pcm_wav(normalized_raw, label="normalized audio")
    rate = pcm["sample_rate_hz"]
    duration_ms = round(pcm["frame_count"] * 1000 / rate)
    if start_ms < 0 or end_ms <= start_ms or end_ms > duration_ms:
        raise SubtitleReleaseError("clip window is outside normalized audio")
    start_frame = start_ms * rate // 1000
    end_frame = (end_ms * rate + 999) // 1000
    frame_width = pcm["channels"] * pcm["sample_width_bytes"]
    selected = frames[start_frame * frame_width : end_frame * frame_width]
    buffer = io.BytesIO()
    with wave.open(buffer, "wb") as handle:
        handle.setnchannels(pcm["channels"])
        handle.setsampwidth(pcm["sample_width_bytes"])
        handle.setframerate(rate)
        handle.writeframes(selected)
    actual_start_ms = round(start_frame * 1000 / rate)
    actual_end_ms = round(end_frame * 1000 / rate)
    return buffer.getvalue(), pcm, actual_start_ms, actual_end_ms


def _validate_source_clip(
    *, normalized_raw: bytes, clip_raw: bytes, clip_start_ms: int, clip_end_ms: int
) -> dict[str, int]:
    pcm, _ = _read_pcm_wav(clip_raw, label="clip")
    expected, _, actual_start, actual_end = _extract_pcm_window(
        normalized_raw, start_ms=clip_start_ms, end_ms=clip_end_ms
    )
    if actual_start != clip_start_ms or actual_end != clip_end_ms:
        raise SubtitleReleaseError("clip window is not exactly representable by audio clock")
    if clip_raw != expected:
        raise SubtitleReleaseError("clip bytes are not the declared normalized-audio window")
    return pcm


def prepare_major_audio(
    request: ReleaseRequest,
    *,
    padding_ms: int = 5000,
    plan_output: Path | None = None,
) -> bytes:
    """Deterministically cut every major-risk component from normalized audio."""

    if not isinstance(padding_ms, int) or isinstance(padding_ms, bool) or padding_ms < 0:
        raise SubtitleReleaseError("padding_ms must be a non-negative integer")
    normalized_raw = _read_ref(request, "normalized_audio")
    text_raw = _read_ref(request, "text_corrected_srt")
    unresolved_raw = _read_ref(request, "unresolved_components")
    cues = _parse_srt(text_raw)
    cue_by_number = {cue.number: cue for cue in cues}
    major, _ = _unresolved_population(unresolved_raw, cue_count=len(cues))
    audio_pcm, _ = _read_pcm_wav(normalized_raw, label="normalized audio")
    audio_duration_ms = round(audio_pcm["frame_count"] * 1000 / audio_pcm["sample_rate_hz"])
    jobs: list[dict[str, object]] = []
    clip_payloads: list[tuple[Path, bytes]] = []
    for component in sorted(major):
        component_id = "cue-" + "-".join(str(number) for number in component)
        target_start = cue_by_number[component[0]].start_ms
        target_end = cue_by_number[component[-1]].end_ms
        requested_start = max(0, target_start - padding_ms)
        requested_end = min(audio_duration_ms, target_end + padding_ms)
        clip_raw, _, clip_start, clip_end = _extract_pcm_window(
            normalized_raw, start_ms=requested_start, end_ms=requested_end
        )
        if clip_start > target_start or clip_end < target_end:
            raise SubtitleReleaseError("prepared clip does not cover the major component")
        relative = f"subtitle-work/memo-dual-audit-v1/major-audio/clips/{component_id}.wav"
        clip_path = _resolve(request.root, relative, label="major clip")
        clip_payloads.append((clip_path, clip_raw))
        jobs.append(
            {
                "component_id": component_id,
                "cue_numbers": list(component),
                "target": {"start_ms": target_start, "end_ms": target_end},
                "clip": {
                    "path": relative,
                    "sha256": _sha256(clip_raw),
                    "size_bytes": len(clip_raw),
                    "start_ms": clip_start,
                    "end_ms": clip_end,
                },
            }
        )
    payload = _canonical_bytes(
        {
            "schema_version": 1,
            "contract": MAJOR_AUDIO_PLAN_CONTRACT,
            "policy_version": POLICY_VERSION,
            "episode_id": request.episode_id,
            "normalized_audio": _ref_payload(request.refs["normalized_audio"]),
            "source_text_srt": _ref_payload(request.refs["text_corrected_srt"]),
            "source_unresolved": _ref_payload(request.refs["unresolved_components"]),
            "padding_ms": padding_ms,
            "major_component_count": len(jobs),
            "jobs": jobs,
        }
    )
    destination = (
        plan_output.resolve()
        if plan_output is not None
        else _resolve(request.root, DEFAULT_MAJOR_AUDIO_PLAN, label="major audio plan")
    )
    try:
        destination.relative_to(request.root)
    except ValueError as exc:
        raise SubtitleReleaseError("major audio plan must stay inside episode root") from exc
    for path, raw in [*clip_payloads, (destination, payload)]:
        if path.exists() and (not path.is_file() or path.read_bytes() != raw):
            raise SubtitleReleaseError(f"major audio overwrite refused: {path}")
    for path, raw in clip_payloads:
        _write_atomic(path, raw)
    _write_atomic(destination, payload)
    return payload


def _package_version(distribution: str) -> str:
    try:
        return importlib.metadata.version(distribution)
    except importlib.metadata.PackageNotFoundError:
        return "unknown"


def _local_snapshot(model: str, revision: str) -> Path:
    candidate = Path(model)
    if candidate.is_dir():
        return candidate.resolve()
    try:
        from huggingface_hub import snapshot_download
    except ImportError as exc:  # pragma: no cover - production dependency
        raise SubtitleReleaseError("huggingface-hub is required for ASR execution") from exc
    try:
        return Path(
            snapshot_download(repo_id=model, revision=revision, local_files_only=True)
        ).resolve()
    except Exception as exc:  # pragma: no cover - depends on configured cache
        raise SubtitleReleaseError(
            f"pinned local model snapshot is unavailable: {model}@{revision}"
        ) from exc


def _create_major_provider_runner(
    *,
    family: str,
    model: str,
    revision: str,
    device: str,
    compute_type: str,
    forced_aligner: str | None,
    forced_aligner_revision: str | None,
) -> Callable[..., Mapping[str, object]]:
    """Load one local ASR model and return a reusable per-clip runner."""

    snapshot = _local_snapshot(model, revision)
    if family == "faster":
        try:
            from faster_whisper import WhisperModel
        except ImportError as exc:  # pragma: no cover - production dependency
            raise SubtitleReleaseError("faster-whisper runtime is unavailable") from exc
        recognizer = WhisperModel(
            str(snapshot),
            device=device,
            compute_type=compute_type,
            local_files_only=True,
        )

        def run_faster(**kwargs: object) -> Mapping[str, object]:
            clip = kwargs["clip"]
            generated, info = recognizer.transcribe(str(clip), language="zh", word_timestamps=True)
            segments = [
                {
                    "start_ms": round(segment.start * 1000),
                    "end_ms": round(segment.end * 1000),
                    "text": segment.text.strip(),
                }
                for segment in generated
                if segment.text.strip() and segment.end > segment.start
            ]
            transcript = "".join(item["text"] for item in segments)
            return {
                "transcript": transcript,
                "segments": segments,
                "provider_result": {
                    "detected_language": getattr(info, "language", None),
                    "language_probability": getattr(info, "language_probability", None),
                },
                "runtime": f"faster-whisper/{_package_version('faster-whisper')}",
            }

        return run_faster
    if forced_aligner is None or forced_aligner_revision is None:
        raise SubtitleReleaseError(
            "Qwen execution requires forced_aligner and forced_aligner_revision"
        )
    try:
        import torch
        from qwen_asr import Qwen3ASRModel
    except ImportError as exc:  # pragma: no cover - production dependency
        raise SubtitleReleaseError("qwen-asr GPU runtime is unavailable") from exc
    aligner_snapshot = _local_snapshot(forced_aligner, forced_aligner_revision)
    dtype = getattr(torch, compute_type, None)
    if dtype is None:
        raise SubtitleReleaseError(f"unsupported Qwen dtype: {compute_type}")
    recognizer = Qwen3ASRModel.from_pretrained(
        str(snapshot),
        revision=revision,
        dtype=dtype,
        device_map=device,
        forced_aligner=str(aligner_snapshot),
        forced_aligner_kwargs={
            "revision": forced_aligner_revision,
            "dtype": dtype,
            "device_map": device,
            "local_files_only": True,
        },
        max_inference_batch_size=8,
        max_new_tokens=4096,
        local_files_only=True,
    )

    def run_qwen(**kwargs: object) -> Mapping[str, object]:
        clip = kwargs["clip"]
        result = recognizer.transcribe(audio=str(clip), language=None, return_time_stamps=True)[0]
        segments = [
            {
                "start_ms": round(segment.start_time * 1000),
                "end_ms": round(segment.end_time * 1000),
                "text": segment.text.strip(),
            }
            for segment in (getattr(result, "time_stamps", None) or [])
            if segment.text.strip() and segment.end_time > segment.start_time
        ]
        transcript = "".join(item["text"] for item in segments)
        return {
            "transcript": transcript,
            "segments": segments,
            "provider_result": {"detected_language": getattr(result, "language", None)},
            "runtime": f"qwen-asr/{_package_version('qwen-asr')}",
        }

    return run_qwen


def run_major_asr(
    *,
    episode_root: Path,
    plan_path: Path,
    family: str,
    model: str,
    revision: str,
    device: str,
    compute_type: str,
    forced_aligner: str | None = None,
    forced_aligner_revision: str | None = None,
    provider_runner: Callable[..., Mapping[str, object]] | None = None,
) -> bytes:
    """Execute one ASR family for every job and emit typed evidence."""

    root = episode_root.resolve()
    if family not in {"faster", "qwen"}:
        raise SubtitleReleaseError("ASR family must be faster or qwen")
    if len(revision) != 40 or any(
        character not in "0123456789abcdef" for character in revision.lower()
    ):
        raise SubtitleReleaseError("model revision must be an immutable 40-hex commit")
    if family == "qwen" and (
        forced_aligner_revision is None
        or len(forced_aligner_revision) != 40
        or any(character not in "0123456789abcdef" for character in forced_aligner_revision.lower())
    ):
        raise SubtitleReleaseError(
            "Qwen forced-aligner revision must be an immutable 40-hex commit"
        )
    plan_resolved = plan_path.resolve()
    try:
        plan_resolved.relative_to(root)
    except ValueError as exc:
        raise SubtitleReleaseError("major audio plan must stay inside episode root") from exc
    plan_raw = plan_resolved.read_bytes()
    plan = _load_json(plan_raw, label="major audio plan")
    _require_keys(
        plan,
        label="major audio plan",
        required={
            "schema_version",
            "contract",
            "policy_version",
            "episode_id",
            "normalized_audio",
            "source_text_srt",
            "source_unresolved",
            "padding_ms",
            "major_component_count",
            "jobs",
        },
    )
    if (
        plan["schema_version"] != 1
        or plan["contract"] != MAJOR_AUDIO_PLAN_CONTRACT
        or plan["policy_version"] != POLICY_VERSION
    ):
        raise SubtitleReleaseError("unsupported major audio plan")
    for label, value in (
        ("normalized_audio", plan["normalized_audio"]),
        ("source_text_srt", plan["source_text_srt"]),
        ("source_unresolved", plan["source_unresolved"]),
    ):
        ref = _file_ref(value, label=f"plan.{label}")
        path = _resolve(root, ref.path, label=f"plan.{label}")
        raw = path.read_bytes()
        if len(raw) != ref.size_bytes or _sha256(raw) != ref.sha256:
            raise SubtitleReleaseError(f"major audio plan source drift: {label}")
    jobs = plan["jobs"]
    if not isinstance(jobs, list) or plan["major_component_count"] != len(jobs):
        raise SubtitleReleaseError("major audio plan job count mismatch")
    adapter = (
        "faster-whisper-word-timestamps-v1"
        if family == "faster"
        else "qwen3-asr-forced-alignment-v1"
    )
    emitted: list[dict[str, object]] = []
    active_runner = provider_runner
    for index, job in enumerate(jobs):
        if not isinstance(job, dict):
            raise SubtitleReleaseError(f"major audio job {index} is malformed")
        _require_keys(
            job,
            label=f"major audio job {index}",
            required={"component_id", "cue_numbers", "target", "clip"},
        )
        component_id = job["component_id"]
        if not isinstance(component_id, str) or component_id != (
            "cue-" + "-".join(str(number) for number in job["cue_numbers"])
        ):
            raise SubtitleReleaseError("major audio component identity is invalid")
        clip_value = job["clip"]
        target = job["target"]
        if not isinstance(clip_value, dict) or not isinstance(target, dict):
            raise SubtitleReleaseError("major audio job window is malformed")
        _require_keys(
            clip_value,
            label=f"major audio job {index}.clip",
            required={"path", "sha256", "size_bytes", "start_ms", "end_ms"},
        )
        clip_ref = _file_ref(
            {key: clip_value[key] for key in ("path", "sha256", "size_bytes")},
            label=f"major audio job {index}.clip",
        )
        clip_path = _resolve(root, clip_ref.path, label="major audio clip")
        clip_raw = clip_path.read_bytes()
        if len(clip_raw) != clip_ref.size_bytes or _sha256(clip_raw) != clip_ref.sha256:
            raise SubtitleReleaseError("major audio clip drift")
        output_root = _resolve(
            root,
            f"subtitle-work/memo-dual-audit-v1/major-audio/asr/{family}/{component_id}",
            label="ASR output root",
        )
        provider_path = output_root / "provider-output.json"
        segments_path = output_root / "segments.json"
        raw_result_path = output_root / "raw-result.json"
        evidence_path = output_root / "evidence.json"
        existing_provider = (
            _load_json(provider_path.read_bytes(), label="existing provider output")
            if provider_path.is_file()
            else None
        )
        if existing_provider is not None:
            _require_keys(
                existing_provider,
                label="existing provider output",
                required={
                    "schema_version",
                    "contract",
                    "episode_id",
                    "component_id",
                    "engine",
                    "clip",
                    "completed",
                    "exit_code",
                    "transcript",
                    "segments",
                    "provider_result",
                },
            )
            existing_engine = existing_provider["engine"]
            if not isinstance(existing_engine, dict):
                raise SubtitleReleaseError("existing provider engine is malformed")
            runtime = existing_engine.get("runtime")
            if (
                existing_provider["schema_version"] != 1
                or existing_provider["contract"] != ASR_PROVIDER_OUTPUT_CONTRACT
                or existing_provider["episode_id"] != plan["episode_id"]
                or existing_provider["component_id"] != component_id
                or existing_provider["clip"] != _ref_payload(clip_ref)
                or existing_provider["completed"] is not True
                or existing_provider["exit_code"] != 0
                or not isinstance(runtime, str)
                or not runtime.strip()
                or existing_engine
                != {
                    "family": family,
                    "model": model,
                    "revision": revision.lower(),
                    "adapter": adapter,
                    "runtime": runtime,
                }
            ):
                raise SubtitleReleaseError("existing provider output binding failed")
            result: Mapping[str, object] = {
                "transcript": existing_provider["transcript"],
                "segments": existing_provider["segments"],
                "provider_result": existing_provider["provider_result"],
                "runtime": runtime,
            }
        else:
            if active_runner is None:
                active_runner = _create_major_provider_runner(
                    family=family,
                    model=model,
                    revision=revision,
                    device=device,
                    compute_type=compute_type,
                    forced_aligner=forced_aligner,
                    forced_aligner_revision=forced_aligner_revision,
                )
            result = active_runner(
                family=family,
                clip=clip_path,
                model=model,
                revision=revision,
                device=device,
                compute_type=compute_type,
                forced_aligner=forced_aligner,
                forced_aligner_revision=forced_aligner_revision,
            )
        if not isinstance(result, Mapping) or set(result) != {
            "transcript",
            "segments",
            "provider_result",
            "runtime",
        }:
            raise SubtitleReleaseError("ASR runner returned an invalid result")
        runtime = result["runtime"]
        transcript = result["transcript"]
        segments = result["segments"]
        if not isinstance(runtime, str) or not runtime.strip():
            raise SubtitleReleaseError("ASR runtime identity is empty")
        engine = {
            "family": family,
            "model": model,
            "revision": revision.lower(),
            "adapter": adapter,
            "runtime": runtime,
        }
        provider_payload = _canonical_bytes(
            {
                "schema_version": 1,
                "contract": ASR_PROVIDER_OUTPUT_CONTRACT,
                "episode_id": plan["episode_id"],
                "component_id": component_id,
                "engine": engine,
                "clip": _ref_payload(clip_ref),
                "completed": True,
                "exit_code": 0,
                "transcript": transcript,
                "segments": segments,
                "provider_result": result["provider_result"],
            }
        )
        segments_payload = _canonical_bytes({"segments": segments})
        _write_atomic(provider_path, provider_payload)
        _write_atomic(segments_path, segments_payload)
        _, evidence_raw = build_asr_evidence(
            episode_root=root,
            episode_id=str(plan["episode_id"]),
            component_id=component_id,
            cue_numbers=tuple(job["cue_numbers"]),
            normalized_audio=_resolve(
                root, str(plan["normalized_audio"]["path"]), label="normalized audio"
            ),
            clip=clip_path,
            clip_start_ms=clip_value["start_ms"],
            clip_end_ms=clip_value["end_ms"],
            target_start_ms=target["start_ms"],
            target_end_ms=target["end_ms"],
            family=family,
            model=model,
            revision=revision.lower(),
            adapter=adapter,
            runtime=runtime,
            provider_output=provider_path,
            transcript=str(transcript),
            segments_json=segments_path,
            raw_result_output=raw_result_path,
            evidence_output=evidence_path,
        )
        emitted.append(
            {
                "component_id": component_id,
                "file": _existing_path_ref(root, evidence_path, label="ASR evidence"),
            }
        )
    manifest = _canonical_bytes(
        {
            "schema_version": 1,
            "contract": MAJOR_ASR_RUN_CONTRACT,
            "episode_id": plan["episode_id"],
            "family": family,
            "model": model,
            "revision": revision.lower(),
            "plan": _existing_path_ref(root, plan_resolved, label="major audio plan"),
            "evidence_count": len(emitted),
            "evidence": emitted,
        }
    )
    manifest_path = _resolve(
        root,
        f"subtitle-work/memo-dual-audit-v1/major-audio/asr/{family}/manifest.json",
        label="ASR manifest",
    )
    _write_atomic(manifest_path, manifest)
    return manifest


def _manifest_evidence(
    request: ReleaseRequest, path: Path, *, family: str
) -> tuple[dict[str, FileRef], dict[str, object]]:
    manifest_ref = _existing_path_ref(request.root, path, label=f"{family} manifest")
    manifest = _load_json(path.resolve().read_bytes(), label=f"{family} manifest")
    _require_keys(
        manifest,
        label=f"{family} manifest",
        required={
            "schema_version",
            "contract",
            "episode_id",
            "family",
            "model",
            "revision",
            "plan",
            "evidence_count",
            "evidence",
        },
    )
    if (
        manifest["schema_version"] != 1
        or manifest["contract"] != MAJOR_ASR_RUN_CONTRACT
        or manifest["episode_id"] != request.episode_id
        or manifest["family"] != family
    ):
        raise SubtitleReleaseError(f"{family} manifest binding failed")
    plan_ref = _file_ref(manifest["plan"], label=f"{family} manifest.plan")
    plan_path = _resolve(request.root, plan_ref.path, label=f"{family} manifest.plan")
    plan_raw = plan_path.read_bytes()
    if len(plan_raw) != plan_ref.size_bytes or _sha256(plan_raw) != plan_ref.sha256:
        raise SubtitleReleaseError(f"{family} major audio plan drift")
    entries = manifest["evidence"]
    if not isinstance(entries, list) or manifest["evidence_count"] != len(entries):
        raise SubtitleReleaseError(f"{family} manifest evidence count mismatch")
    mapped: dict[str, FileRef] = {}
    for index, entry in enumerate(entries):
        if not isinstance(entry, dict):
            raise SubtitleReleaseError(f"{family} evidence entry {index} is malformed")
        _require_keys(
            entry,
            label=f"{family} evidence entry {index}",
            required={"component_id", "file"},
        )
        component_id = entry["component_id"]
        if not isinstance(component_id, str) or component_id in mapped:
            raise SubtitleReleaseError(f"{family} manifest component identity is invalid")
        mapped[component_id] = _file_ref(
            entry["file"], label=f"{family} evidence entry {index}.file"
        )
    return mapped, {
        "file": manifest_ref,
        "plan": _ref_payload(plan_ref),
        "model": manifest["model"],
        "revision": manifest["revision"],
    }


def build_audio_decisions(
    request: ReleaseRequest,
    *,
    faster_manifest: Path,
    qwen_manifest: Path,
    output: Path | None = None,
) -> bytes:
    """Deterministically adjudicate all unresolved items from exact dual-ASR evidence."""

    text_srt = _read_ref(request, "text_corrected_srt")
    unresolved_raw = _read_ref(request, "unresolved_components")
    normalized_raw = _read_ref(request, "normalized_audio")
    cues = _parse_srt(text_srt)
    cue_text = {cue.number: cue.text for cue in cues}
    major, nonmajor = _unresolved_population(unresolved_raw, cue_count=len(cues))
    faster_refs, faster_source = _manifest_evidence(request, faster_manifest, family="faster")
    qwen_refs, qwen_source = _manifest_evidence(request, qwen_manifest, family="qwen")
    if faster_source["plan"] != qwen_source["plan"]:
        raise SubtitleReleaseError("dual-ASR manifests use different major audio plans")
    expected_ids = {"cue-" + "-".join(str(number) for number in component) for component in major}
    if set(faster_refs) != expected_ids or set(qwen_refs) != expected_ids:
        raise SubtitleReleaseError("dual-ASR manifest population differs from major risks")
    major_decisions: list[dict[str, object]] = []
    for component in sorted(major):
        component_id = "cue-" + "-".join(str(number) for number in component)
        target_start = cues[component[0] - 1].start_ms
        target_end = cues[component[-1] - 1].end_ms
        family_results: dict[str, dict[str, object]] = {}
        for family, ref in (
            ("faster", faster_refs[component_id]),
            ("qwen", qwen_refs[component_id]),
        ):
            family_results[family] = _validate_model_evidence(
                request,
                {"file": _ref_payload(ref)},
                label=f"{component_id}.{family}",
                expected_family=family,
                component_id=component_id,
                component=component,
                target_start_ms=target_start,
                target_end_ms=target_end,
                normalized_audio_sha256=_sha256(normalized_raw),
                normalized_audio_size_bytes=len(normalized_raw),
            )
        faster = family_results["faster"]
        qwen = family_results["qwen"]
        if (
            faster["engine"]["model"] != faster_source["model"]
            or faster["engine"]["revision"] != faster_source["revision"]
            or qwen["engine"]["model"] != qwen_source["model"]
            or qwen["engine"]["revision"] != qwen_source["revision"]
        ):
            raise SubtitleReleaseError("ASR manifest model identity differs from evidence")
        if faster["clip"] != qwen["clip"]:
            raise SubtitleReleaseError("dual-ASR evidence does not share exact clip")
        supported = (
            _dual_asr_supported_candidate(
                major[component],
                faster_observation=str(faster["target_observation"]),
                qwen_observation=str(qwen["target_observation"]),
            )
            if len(component) == 1
            else None
        )
        accepted = supported is not None
        major_decisions.append(
            {
                "component_id": component_id,
                "cue_numbers": list(component),
                "decision": ("accept_replacement" if accepted else "retain_memo_original"),
                "reason_code": ("dual_asr_consensus" if accepted else "dual_asr_conflict"),
                "original": {str(number): cue_text[number] for number in component},
                "replacements": ({str(component[0]): supported} if accepted else {}),
                "evidence": {
                    "clip": faster["clip"],
                    "faster": {"file": _ref_payload(faster_refs[component_id])},
                    "qwen": {"file": _ref_payload(qwen_refs[component_id])},
                },
            }
        )
    nonmajor_decisions = [
        {
            "component_id": "cue-" + "-".join(str(number) for number in component),
            "cue_numbers": list(component),
            "original": {str(number): cue_text[number] for number in component},
        }
        for component in sorted(nonmajor)
    ]
    payload = _canonical_bytes(
        {
            "schema_version": 1,
            "contract": AUDIO_DECISIONS_CONTRACT,
            "episode_id": request.episode_id,
            "source_srt_sha256": _sha256(text_srt),
            "source_unresolved_sha256": _sha256(unresolved_raw),
            "major_components": major_decisions,
            "nonmajor_retained_original_components": nonmajor_decisions,
        }
    )
    destination = (
        output.resolve()
        if output is not None
        else _resolve(request.root, request.refs["audio_decisions"].path, label="audio decisions")
    )
    try:
        destination.relative_to(request.root)
    except ValueError as exc:
        raise SubtitleReleaseError("audio decisions output must stay inside episode root") from exc
    _write_atomic(destination, payload)
    return payload


def build_asr_evidence(
    *,
    episode_root: Path,
    episode_id: str,
    component_id: str,
    cue_numbers: Sequence[int],
    normalized_audio: Path,
    clip: Path,
    clip_start_ms: int,
    clip_end_ms: int,
    target_start_ms: int,
    target_end_ms: int,
    family: str,
    model: str,
    revision: str,
    adapter: str,
    runtime: str,
    provider_output: Path,
    transcript: str | None,
    segments_json: Path | None,
    raw_result_output: Path,
    evidence_output: Path,
) -> tuple[bytes, bytes]:
    """Wrap one completed provider run in typed, source-bound evidence."""

    root = episode_root.resolve()
    if family not in {"faster", "qwen"}:
        raise SubtitleReleaseError("ASR family must be faster or qwen")
    for label, value in {
        "episode_id": episode_id,
        "component_id": component_id,
        "model": model,
        "revision": revision,
        "adapter": adapter,
        "runtime": runtime,
    }.items():
        if not value.strip():
            raise SubtitleReleaseError(f"{label} must be non-empty")
    if len(revision) != 40 or any(
        character not in "0123456789abcdef" for character in revision.lower()
    ):
        raise SubtitleReleaseError("revision must be an immutable 40-hex commit")
    if not cue_numbers or tuple(sorted(set(cue_numbers))) != tuple(cue_numbers):
        raise SubtitleReleaseError("cue_numbers must be sorted and unique")
    audio_ref = _existing_path_ref(root, normalized_audio, label="normalized audio")
    clip_ref = _existing_path_ref(root, clip, label="clip")
    provider_ref = _existing_path_ref(root, provider_output, label="provider output")
    normalized_raw = normalized_audio.resolve().read_bytes()
    clip_raw = clip.resolve().read_bytes()
    if (
        clip_start_ms < 0
        or clip_end_ms <= clip_start_ms
        or clip_start_ms > target_start_ms
        or clip_end_ms < target_end_ms
    ):
        raise SubtitleReleaseError("clip window does not cover target")
    pcm = _validate_source_clip(
        normalized_raw=normalized_raw,
        clip_raw=clip_raw,
        clip_start_ms=clip_start_ms,
        clip_end_ms=clip_end_ms,
    )
    duration_ms = round(pcm["frame_count"] * 1000 / pcm["sample_rate_hz"])
    if abs(duration_ms - (clip_end_ms - clip_start_ms)) > 2:
        raise SubtitleReleaseError("clip duration differs from declared window")
    provider = _load_json(provider_output.resolve().read_bytes(), label="provider output")
    _require_keys(
        provider,
        label="provider output",
        required={
            "schema_version",
            "contract",
            "episode_id",
            "component_id",
            "engine",
            "clip",
            "completed",
            "exit_code",
            "transcript",
            "segments",
            "provider_result",
        },
    )
    expected_engine = {
        "family": family,
        "model": model,
        "revision": revision,
        "adapter": adapter,
        "runtime": runtime,
    }
    if (
        provider["schema_version"] != 1
        or provider["contract"] != ASR_PROVIDER_OUTPUT_CONTRACT
        or provider["episode_id"] != episode_id
        or provider["component_id"] != component_id
        or provider["engine"] != expected_engine
        or provider["clip"] != clip_ref
        or provider["completed"] is not True
        or provider["exit_code"] != 0
    ):
        raise SubtitleReleaseError("provider output binding or execution failed")
    provider_transcript = provider["transcript"]
    segments = provider["segments"]
    if not isinstance(provider_transcript, str) or not provider_transcript.strip():
        raise SubtitleReleaseError("provider output transcript is empty")
    if not isinstance(segments, list) or not segments:
        raise SubtitleReleaseError("provider output must contain non-empty segments")
    if transcript is not None and transcript != provider_transcript:
        raise SubtitleReleaseError("transcript differs from provider output")
    if segments_json is not None:
        supplied_segments = _load_json(
            segments_json.resolve().read_bytes(), label="segments JSON"
        ).get("segments")
        if supplied_segments != segments:
            raise SubtitleReleaseError("segments differ from provider output")
    transcript = provider_transcript
    raw_payload = {
        "schema_version": 1,
        "contract": ASR_RAW_RESULT_CONTRACT,
        "episode_id": episode_id,
        "component_id": component_id,
        "engine_family": family,
        "completed": True,
        "exit_code": 0,
        "provider_output": provider_ref,
        "transcript": transcript,
        "segments": segments,
    }
    raw_result = _canonical_bytes(raw_payload)
    try:
        raw_relative = raw_result_output.resolve().relative_to(root).as_posix()
        evidence_output.resolve().relative_to(root)
    except ValueError as exc:
        raise SubtitleReleaseError("evidence outputs must stay inside episode root") from exc
    evidence_payload = {
        "schema_version": 1,
        "contract": ASR_EVIDENCE_CONTRACT,
        "episode_id": episode_id,
        "component_id": component_id,
        "cue_numbers": list(cue_numbers),
        "normalized_audio": {
            "sha256": audio_ref["sha256"],
            "size_bytes": audio_ref["size_bytes"],
        },
        "clip": {
            "file": clip_ref,
            "start_ms": clip_start_ms,
            "end_ms": clip_end_ms,
            "pcm": pcm,
        },
        "target": {"start_ms": target_start_ms, "end_ms": target_end_ms},
        "engine": {
            "family": family,
            "model": model,
            "revision": revision,
            "adapter": adapter,
            "runtime": runtime,
        },
        "raw_result": {
            "path": raw_relative,
            "sha256": _sha256(raw_result),
            "size_bytes": len(raw_result),
        },
        "execution": {"completed": True, "exit_code": 0},
        "recognition": {
            "text": transcript,
            "segments": segments,
            "raw_result_sha256": _sha256(raw_result),
        },
    }
    evidence = _canonical_bytes(evidence_payload)
    for path, payload in (
        (raw_result_output.resolve(), raw_result),
        (evidence_output.resolve(), evidence),
    ):
        if path.exists() and path.read_bytes() != payload:
            raise SubtitleReleaseError(f"evidence overwrite refused: {path}")
    _write_atomic(raw_result_output.resolve(), raw_result)
    _write_atomic(evidence_output.resolve(), evidence)
    return raw_result, evidence


def _emit(payload: bytes, output: Path | None) -> None:
    if output is None:
        sys.stdout.buffer.write(payload)
        return
    # Operator status receipts are mutable phase snapshots. Terminal release
    # artifacts remain immutable and continue to use _write_atomic/finalize.
    _replace_atomic(output.resolve(), payload)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)
    init = subparsers.add_parser(
        "init", help="create a resumable request with standard expected paths"
    )
    init.add_argument("--episode-root", type=Path, required=True)
    init.add_argument("--episode-id", required=True)
    init.add_argument("--request-output", type=Path)
    init.add_argument(
        "--path",
        action="append",
        default=[],
        metavar="ROLE=RELATIVE_PATH",
        help="override one standard input path",
    )
    status_parser = subparsers.add_parser("status", help="report the resumable phase")
    status_parser.add_argument("--request", type=Path, required=True)
    status_parser.add_argument("--status-output", type=Path)
    finalize_parser = subparsers.add_parser(
        "finalize", help="verify all evidence and commit the release bundle"
    )
    finalize_parser.add_argument("--request", type=Path, required=True)
    finalize_parser.add_argument("--status-output", type=Path)
    seal = subparsers.add_parser("seal", help="seal hashes for every input that currently exists")
    seal.add_argument("--request", type=Path, required=True)
    seal.add_argument("--status-output", type=Path)
    legacy = subparsers.add_parser(
        "verify-legacy", help="read-only verification for the former degraded bundle"
    )
    legacy.add_argument("--legacy-root", type=Path, required=True)
    legacy.add_argument("--expected-srt-sha256")
    legacy.add_argument("--status-output", type=Path)
    evidence = subparsers.add_parser(
        "build-asr-evidence", help="wrap one completed ASR run in typed evidence"
    )
    evidence.add_argument("--episode-root", type=Path, required=True)
    evidence.add_argument("--episode-id", required=True)
    evidence.add_argument("--component-id", required=True)
    evidence.add_argument("--cue-numbers", required=True)
    evidence.add_argument("--normalized-audio", type=Path, required=True)
    evidence.add_argument("--clip", type=Path, required=True)
    evidence.add_argument("--clip-start-ms", type=int, required=True)
    evidence.add_argument("--clip-end-ms", type=int, required=True)
    evidence.add_argument("--target-start-ms", type=int, required=True)
    evidence.add_argument("--target-end-ms", type=int, required=True)
    evidence.add_argument("--family", choices=("faster", "qwen"), required=True)
    evidence.add_argument("--model", required=True)
    evidence.add_argument("--revision", required=True)
    evidence.add_argument("--adapter", required=True)
    evidence.add_argument("--runtime", required=True)
    evidence.add_argument("--provider-output", type=Path, required=True)
    evidence.add_argument("--transcript")
    evidence.add_argument("--segments-json", type=Path)
    evidence.add_argument("--raw-result-output", type=Path, required=True)
    evidence.add_argument("--evidence-output", type=Path, required=True)
    prepare = subparsers.add_parser(
        "prepare-major-audio",
        help="deterministically cut normalized-audio clips for all major risks",
    )
    prepare.add_argument("--request", type=Path, required=True)
    prepare.add_argument("--padding-ms", type=int, default=5000)
    prepare.add_argument("--plan-output", type=Path)
    run_asr = subparsers.add_parser(
        "run-major-asr", help="run Faster-Whisper or Qwen3-ASR over a major-audio plan"
    )
    run_asr.add_argument("--episode-root", type=Path, required=True)
    run_asr.add_argument("--plan", type=Path, required=True)
    run_asr.add_argument("--family", choices=("faster", "qwen"), required=True)
    run_asr.add_argument("--model", required=True)
    run_asr.add_argument("--revision", required=True)
    run_asr.add_argument("--device", default="cuda")
    run_asr.add_argument("--compute-type", default="float16")
    run_asr.add_argument("--forced-aligner")
    run_asr.add_argument("--forced-aligner-revision")
    decisions = subparsers.add_parser(
        "build-audio-decisions",
        help="deterministically adjudicate major risks from both ASR manifests",
    )
    decisions.add_argument("--request", type=Path, required=True)
    decisions.add_argument("--faster-manifest", type=Path, required=True)
    decisions.add_argument("--qwen-manifest", type=Path, required=True)
    decisions.add_argument("--output", type=Path)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    if args.command == "prepare-major-audio":
        request = seal_request(args.request)
        payload = prepare_major_audio(
            request, padding_ms=args.padding_ms, plan_output=args.plan_output
        )
        _emit(payload, None)
        return 0
    if args.command == "run-major-asr":
        payload = run_major_asr(
            episode_root=args.episode_root,
            plan_path=args.plan,
            family=args.family,
            model=args.model,
            revision=args.revision,
            device=args.device,
            compute_type=args.compute_type,
            forced_aligner=args.forced_aligner,
            forced_aligner_revision=args.forced_aligner_revision,
        )
        _emit(payload, None)
        return 0
    if args.command == "build-audio-decisions":
        request = seal_request(args.request)
        payload = build_audio_decisions(
            request,
            faster_manifest=args.faster_manifest,
            qwen_manifest=args.qwen_manifest,
            output=args.output,
        )
        _emit(payload, None)
        return 0
    if args.command == "build-asr-evidence":
        try:
            cue_numbers = tuple(int(value) for value in args.cue_numbers.split(","))
        except ValueError as exc:
            raise SubtitleReleaseError("--cue-numbers must be comma-separated integers") from exc
        _, evidence = build_asr_evidence(
            episode_root=args.episode_root,
            episode_id=args.episode_id,
            component_id=args.component_id,
            cue_numbers=cue_numbers,
            normalized_audio=args.normalized_audio,
            clip=args.clip,
            clip_start_ms=args.clip_start_ms,
            clip_end_ms=args.clip_end_ms,
            target_start_ms=args.target_start_ms,
            target_end_ms=args.target_end_ms,
            family=args.family,
            model=args.model,
            revision=args.revision,
            adapter=args.adapter,
            runtime=args.runtime,
            provider_output=args.provider_output,
            transcript=args.transcript,
            segments_json=args.segments_json,
            raw_result_output=args.raw_result_output,
            evidence_output=args.evidence_output,
        )
        _emit(
            _canonical_bytes(
                {
                    "contract": ASR_EVIDENCE_CONTRACT,
                    "evidence_sha256": _sha256(evidence),
                    "status": "complete",
                }
            ),
            None,
        )
        return 0
    if args.command == "init":
        request_path = init_request(
            args.episode_root,
            episode_id=args.episode_id,
            request_path=args.request_output,
            overrides=args.path,
        )
        exit_code, payload = status(load_request(request_path))
        _emit(payload, None)
        return exit_code
    if args.command == "verify-legacy":
        payload = verify_legacy_bundle(args.legacy_root, expected_sha256=args.expected_srt_sha256)
        _emit(payload, args.status_output)
        return 0
    request = load_request(args.request)
    if args.command == "status":
        exit_code, payload = status(request)
        _emit(payload, args.status_output)
        return exit_code
    if args.command == "seal":
        request = seal_request(args.request)
        exit_code, payload = status(request)
        _emit(payload, args.status_output)
        return exit_code
    request = seal_request(args.request)
    pending_code, pending_payload = status(request)
    if pending_code != 0:
        _emit(pending_payload, args.status_output)
        return pending_code
    finalize(request)
    exit_code, payload = status(request)
    _emit(payload, args.status_output)
    return exit_code


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except SubtitleReleaseError as exc:
        print(f"subtitle release failed: {exc}", file=sys.stderr)
        raise SystemExit(2) from exc
