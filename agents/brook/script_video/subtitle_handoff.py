"""Fail-closed Subtitle V2 handoff for Stage 5 video consumers."""

from __future__ import annotations

import hashlib
import json
import os
import re
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, Field, ValidationError

from agents.brook.podcast_subtitles.handoff import (
    ProjectionVerifierFactory,
    open_verified_projection,
)
from agents.brook.podcast_subtitles.module import VerifiedProjection


@dataclass(frozen=True, slots=True)
class Stage5SubtitleHandoff:
    """Exact verified subtitle bytes plus their persisted downstream receipt."""

    srt_path: Path
    receipt_path: Path
    verified: VerifiedProjection


@dataclass(frozen=True, slots=True)
class Stage5DegradedReleaseHandoff:
    """Hash-bound degraded dual-ASR release identity for Stage 5 only."""

    srt_path: Path
    handoff_path: Path
    release_ledger_path: Path
    export_manifest_path: Path
    episode_id: str
    provenance_status: str
    srt_sha256: str
    handoff_sha256: str
    release_ledger_sha256: str
    export_manifest_sha256: str


@dataclass(frozen=True, slots=True)
class Stage5MemoDualAuditReleaseHandoff:
    """Hash-bound production Memo Dual-Audit release identity for Stage 5."""

    srt_path: Path
    handoff_path: Path
    release_ledger_path: Path
    export_manifest_path: Path
    episode_id: str
    srt_sha256: str
    handoff_sha256: str
    release_ledger_sha256: str
    export_manifest_sha256: str


class Stage5SubtitleContractError(ValueError):
    """The Stage 5 caller omitted or mixed formal/legacy subtitle identity."""


class Stage5SubtitleArtifactConflictError(Stage5SubtitleContractError):
    """An immutable Stage 5 artifact path already contains different bytes."""


@dataclass(frozen=True, slots=True)
class Stage5SubtitleSelection:
    """Subtitle source selected explicitly for one Stage 5 operation."""

    mode: Literal[
        "memo-dual-audit-v1",
        "verified-v2",
        "legacy-v1",
        "degraded-dual-asr-v1",
    ]
    srt_path: Path
    handoff: (
        Stage5SubtitleHandoff
        | Stage5DegradedReleaseHandoff
        | Stage5MemoDualAuditReleaseHandoff
        | None
    )

    def identity(self) -> dict[str, object]:
        """Return serializable lineage without fabricating Verified Projection fields."""

        if self.handoff is None:
            return {"subtitle_mode": self.mode}
        if isinstance(self.handoff, Stage5SubtitleHandoff):
            verified = self.handoff.verified
            return {
                "subtitle_mode": self.mode,
                "projection_id": verified.projection_id,
                "generation_id": verified.generation_id,
                "episode_id": verified.episode_id,
                "projection_manifest_sha256": verified.manifest_sha256,
                "subtitle_handoff_receipt": str(self.handoff.receipt_path),
                "subtitle_srt_sha256": verified.srt_sha256,
            }
        if isinstance(self.handoff, Stage5MemoDualAuditReleaseHandoff):
            release = self.handoff
            return {
                "subtitle_mode": self.mode,
                "episode_id": release.episode_id,
                "subtitle_release_handoff": str(release.handoff_path),
                "subtitle_release_handoff_sha256": release.handoff_sha256,
                "release_ledger": str(release.release_ledger_path),
                "release_ledger_sha256": release.release_ledger_sha256,
                "export_manifest": str(release.export_manifest_path),
                "export_manifest_sha256": release.export_manifest_sha256,
                "subtitle_srt_sha256": release.srt_sha256,
            }
        degraded = self.handoff
        return {
            "subtitle_mode": self.mode,
            "episode_id": degraded.episode_id,
            "provenance_status": degraded.provenance_status,
            "degraded_release_handoff": str(degraded.handoff_path),
            "degraded_release_handoff_sha256": degraded.handoff_sha256,
            "release_ledger": str(degraded.release_ledger_path),
            "release_ledger_sha256": degraded.release_ledger_sha256,
            "export_manifest": str(degraded.export_manifest_path),
            "export_manifest_sha256": degraded.export_manifest_sha256,
            "subtitle_srt_sha256": degraded.srt_sha256,
        }


@dataclass(frozen=True, slots=True)
class Stage5SubtitleRequest:
    """Serializable operator intent for selecting one Stage 5 subtitle source."""

    legacy_v1: bool = False
    subtitle_release_handoff: str | Path | None = None
    degraded_release_handoff: str | Path | None = None
    projection_id: str | None = None
    expected_episode_id: str | None = None
    expected_generation_id: str | None = None
    expected_manifest_sha256: str | None = None
    reference_manifest: str | Path | None = None

    def open(
        self,
        episode_root: str | Path,
        *,
        factory: ProjectionVerifierFactory | None = None,
    ) -> Stage5SubtitleSelection:
        return select_stage5_subtitle(
            episode_root=episode_root,
            legacy_v1=self.legacy_v1,
            subtitle_release_handoff=self.subtitle_release_handoff,
            degraded_release_handoff=self.degraded_release_handoff,
            projection_id=self.projection_id,
            expected_episode_id=self.expected_episode_id,
            expected_generation_id=self.expected_generation_id,
            expected_manifest_sha256=self.expected_manifest_sha256,
            reference_manifest=self.reference_manifest,
            factory=factory,
        )


def _write_immutable(path: Path, payload: bytes) -> None:
    """Create one artifact exactly once; identical repeats are no-ops."""

    try:
        with path.open("xb") as stream:
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
        return
    except FileExistsError:
        pass
    if path.read_bytes() != payload:
        raise Stage5SubtitleArtifactConflictError(
            f"immutable Stage 5 artifact conflicts with verified bytes: {path}"
        )


_CURRENT_HANDOFF = Path(".stage5") / "verified-subtitle-handoff.v2.json"
_CURRENT_CONTRACT = "podcast-subtitle-v2-stage5-handoff"
_MEMO_DUAL_AUDIT_DEFAULT_HANDOFF = (
    Path("subtitle-release") / "memo-dual-audit-v1" / "STAGE5-HANDOFF.json"
)
_MEMO_DUAL_AUDIT_HANDOFF_CONTRACT = "podcast-subtitle-stage5-memo-dual-audit-handoff-v1"
_MEMO_DUAL_AUDIT_LEDGER_CONTRACT = "podcast-subtitle-memo-dual-audit-release-v1"
_MEMO_DUAL_AUDIT_EXPORT_CONTRACT = "podcast-subtitle-memo-dual-audit-release-export-v1"
_MEMO_DUAL_AUDIT_INPUT_ROLES = {
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
}
_DEGRADED_HANDOFF_CONTRACT = "podcast-subtitle-v2-stage5-degraded-dual-asr-handoff-v1"
_DEGRADED_LEDGER_CONTRACT = "podcast-subtitle-v2-degraded-audio-release-v1"
_DEGRADED_EXPORT_CONTRACT = "podcast-subtitle-v2-degraded-audio-release-export-v1"
_DEGRADED_PROVENANCE = "degraded_dual_asr_major_complete_not_full_v2_checkpoint"
_DEGRADED_COUNT_GATES = (
    "major_component_count",
    "major_audio_reviewed_count",
    "nonmajor_retained_original_count",
    "cue_count",
    "non_positive_duration_count",
    "overlap_count",
)
_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
_SRT_TIME_RE = re.compile(
    r"^(\d{2}):(\d{2}):(\d{2}),(\d{3}) --> "
    r"(\d{2}):(\d{2}):(\d{2}),(\d{3})$"
)


def _canonical_json(payload: dict[str, object]) -> bytes:
    return json.dumps(
        payload,
        ensure_ascii=False,
        allow_nan=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def _canonical_json_line(payload: dict[str, object]) -> bytes:
    """Match the degraded release exporter's canonical JSONL file contract."""
    return _canonical_json(payload) + b"\n"


def _sha256(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def _strict_json_object(raw: bytes, *, label: str) -> dict[str, Any]:
    def strict_pairs(pairs: list[tuple[str, object]]) -> dict[str, object]:
        result: dict[str, object] = {}
        for key, value in pairs:
            if key in result:
                raise ValueError(f"duplicate key {key!r}")
            result[key] = value
        return result

    try:
        payload = json.loads(
            raw.decode("utf-8"),
            object_pairs_hook=strict_pairs,
            parse_constant=lambda value: (_ for _ in ()).throw(ValueError(value)),
        )
    except (UnicodeDecodeError, json.JSONDecodeError, ValueError) as exc:
        raise Stage5SubtitleContractError(f"{label} is not strict UTF-8 JSON") from exc
    if not isinstance(payload, dict):
        raise Stage5SubtitleContractError(f"{label} must be one JSON object")
    return payload


def _bound_relative_path(root: Path, value: object, *, label: str) -> Path:
    if not isinstance(value, str) or not value or Path(value).is_absolute():
        raise Stage5SubtitleContractError(f"{label} must be a relative episode-local path")
    resolved = (root / value).resolve()
    try:
        resolved.relative_to(root)
    except ValueError as exc:
        raise Stage5SubtitleContractError(f"{label} escapes the episode root") from exc
    return resolved


def _read_declared_artifact(root: Path, declaration: object, *, label: str) -> tuple[Path, bytes]:
    if not isinstance(declaration, dict) or set(declaration) != {
        "path",
        "sha256",
        "size_bytes",
    }:
        raise Stage5SubtitleContractError(f"{label} declaration schema drift")
    path = _bound_relative_path(root, declaration["path"], label=f"{label}.path")
    expected_hash = declaration["sha256"]
    expected_size = declaration["size_bytes"]
    if not isinstance(expected_hash, str) or _SHA256_RE.fullmatch(expected_hash) is None:
        raise Stage5SubtitleContractError(f"{label} SHA-256 is invalid")
    if type(expected_size) is not int or expected_size < 0:
        raise Stage5SubtitleContractError(f"{label} size is invalid")
    try:
        raw = path.read_bytes()
    except OSError as exc:
        raise Stage5SubtitleContractError(f"{label} is missing or unreadable") from exc
    if len(raw) != expected_size or _sha256(raw) != expected_hash:
        raise Stage5SubtitleContractError(f"{label} hash or size mismatch")
    return path, raw


def _timestamp_ms(parts: tuple[str, ...]) -> int:
    hours, minutes, seconds, milliseconds = (int(value) for value in parts)
    if minutes >= 60 or seconds >= 60:
        raise Stage5SubtitleContractError("degraded release SRT timestamp is invalid")
    return ((hours * 60 + minutes) * 60 + seconds) * 1000 + milliseconds


def _degraded_srt_metrics(raw: bytes) -> tuple[int, int, int]:
    try:
        text = raw.decode("utf-8-sig")
    except UnicodeDecodeError as exc:
        raise Stage5SubtitleContractError("degraded release SRT is not UTF-8") from exc
    blocks = re.split(r"\n{2,}", text.replace("\r\n", "\n").strip())
    previous_end = -1
    non_positive = 0
    overlaps = 0
    for expected, block in enumerate(blocks, start=1):
        lines = block.splitlines()
        if len(lines) < 3 or lines[0] != str(expected):
            raise Stage5SubtitleContractError("degraded release SRT cue identity is invalid")
        match = _SRT_TIME_RE.fullmatch(lines[1])
        if match is None:
            raise Stage5SubtitleContractError("degraded release SRT timing is invalid")
        values = match.groups()
        start = _timestamp_ms(values[:4])
        end = _timestamp_ms(values[4:])
        non_positive += int(end <= start)
        overlaps += int(start < previous_end)
        previous_end = end
    return len(blocks), non_positive, overlaps


def _validate_degraded_gates(value: object) -> dict[str, Any]:
    expected_fields = {*_DEGRADED_COUNT_GATES, "byte_identical_rerun"}
    if not isinstance(value, dict) or set(value) != expected_fields:
        raise Stage5SubtitleContractError("degraded handoff gate schema drift")
    for key in _DEGRADED_COUNT_GATES:
        if type(value[key]) is not int:
            raise Stage5SubtitleContractError(f"degraded handoff gate {key} is not an integer")
        if value[key] < 0:
            raise Stage5SubtitleContractError(f"degraded handoff gate {key} is negative")
    if value["cue_count"] == 0:
        raise Stage5SubtitleContractError("degraded handoff cue_count must be positive")
    if value["major_audio_reviewed_count"] != value["major_component_count"]:
        raise Stage5SubtitleContractError("degraded handoff major audio coverage is incomplete")
    if value["non_positive_duration_count"] != 0 or value["overlap_count"] != 0:
        raise Stage5SubtitleContractError("degraded handoff timing gates failed")
    if value["byte_identical_rerun"] is not True:
        raise Stage5SubtitleContractError("degraded handoff rerun is not byte-identical")
    return value


def _open_memo_dual_audit_release_handoff(
    root: Path, handoff_value: str | Path
) -> Stage5MemoDualAuditReleaseHandoff:
    """Open one official production handoff without consulting Formal V2 state."""

    handoff_path = _bound_relative_path(
        root,
        str(handoff_value),
        label="subtitle release handoff",
    )
    try:
        handoff_raw = handoff_path.read_bytes()
    except OSError as exc:
        raise Stage5SubtitleContractError(
            "official Memo Dual-Audit handoff is missing or unreadable"
        ) from exc
    payload = _strict_json_object(handoff_raw, label="Memo Dual-Audit handoff")
    required = {
        "schema_version",
        "contract",
        "episode_id",
        "release_srt",
        "release_ledger",
        "export_manifest",
        "gates",
    }
    if set(payload) != required or payload.get("schema_version") != 1:
        raise Stage5SubtitleContractError("Memo Dual-Audit handoff schema drift")
    if payload.get("contract") != _MEMO_DUAL_AUDIT_HANDOFF_CONTRACT:
        raise Stage5SubtitleContractError("Memo Dual-Audit handoff contract drift")
    episode_id = payload.get("episode_id")
    if not isinstance(episode_id, str) or not episode_id:
        raise Stage5SubtitleContractError("Memo Dual-Audit handoff episode_id is invalid")
    if episode_id != root.name:
        raise Stage5SubtitleContractError(
            "Memo Dual-Audit handoff episode_id differs from the episode directory"
        )
    srt_path, srt_raw = _read_declared_artifact(
        handoff_path.parent,
        payload["release_srt"],
        label="release_srt",
    )
    ledger_path, ledger_raw = _read_declared_artifact(
        handoff_path.parent,
        payload["release_ledger"],
        label="release_ledger",
    )
    manifest_path, manifest_raw = _read_declared_artifact(
        handoff_path.parent,
        payload["export_manifest"],
        label="export_manifest",
    )
    ledger = _strict_json_object(ledger_raw, label="release ledger")
    manifest = _strict_json_object(manifest_raw, label="export manifest")
    if _canonical_json_line(ledger) != ledger_raw or _canonical_json_line(manifest) != manifest_raw:
        raise Stage5SubtitleContractError("release ledger/export manifest is not canonical")
    gates = _validate_degraded_gates(payload["gates"])
    ledger_fields = {
        "schema_version",
        "contract",
        "policy_version",
        "episode_id",
        "status",
        "inputs",
        "normalized_audio_sha256",
        "memo_srt_sha256",
        "text_audit",
        "audio_audit",
        "release_policy",
        "cue_count",
        "non_positive_duration_count",
        "overlap_count",
        "byte_identical_rerun",
        "release_srt",
    }
    if set(ledger) != ledger_fields:
        raise Stage5SubtitleContractError("release ledger schema drift")
    if (
        ledger.get("schema_version") != 1
        or ledger.get("contract") != _MEMO_DUAL_AUDIT_LEDGER_CONTRACT
        or ledger.get("policy_version") != "memo-dual-audit-release-v1"
        or ledger.get("episode_id") != episode_id
        or ledger.get("status") != "complete"
        or ledger.get("byte_identical_rerun") is not True
    ):
        raise Stage5SubtitleContractError("release ledger contract/status/episode gate failed")
    for digest_field in ("normalized_audio_sha256", "memo_srt_sha256"):
        digest = ledger.get(digest_field)
        if not isinstance(digest, str) or _SHA256_RE.fullmatch(digest) is None:
            raise Stage5SubtitleContractError(f"release ledger {digest_field} is invalid")
    inputs = ledger.get("inputs")
    if not isinstance(inputs, dict) or set(inputs) != _MEMO_DUAL_AUDIT_INPUT_ROLES:
        raise Stage5SubtitleContractError("release ledger input schema drift")
    for role, declaration in inputs.items():
        if not isinstance(declaration, dict) or set(declaration) != {
            "path",
            "sha256",
            "size_bytes",
        }:
            raise Stage5SubtitleContractError(f"release ledger input declaration drift for {role}")
        input_path = _bound_relative_path(
            root,
            declaration.get("path"),
            label=f"inputs.{role}.path",
        )
        digest = declaration.get("sha256")
        size = declaration.get("size_bytes")
        if not isinstance(digest, str) or _SHA256_RE.fullmatch(digest) is None:
            raise Stage5SubtitleContractError(f"release ledger input SHA-256 is invalid for {role}")
        if type(size) is not int or size < 0:
            raise Stage5SubtitleContractError(f"release ledger input size is invalid for {role}")
        try:
            input_raw = input_path.read_bytes()
        except OSError as exc:
            raise Stage5SubtitleContractError(
                f"release ledger input is missing or unreadable for {role}"
            ) from exc
        if len(input_raw) != size or _sha256(input_raw) != digest:
            raise Stage5SubtitleContractError(
                f"release ledger input hash or size mismatch for {role}"
            )
    text_audit = ledger.get("text_audit")
    if not isinstance(text_audit, dict) or set(text_audit) != {
        "independent_agent_count",
        "agents",
        "cue_coverage_count",
        "complete_coverage",
        "fresh_arbitration_replay",
        "text_ledger_sha256",
        "unresolved_components_sha256",
    }:
        raise Stage5SubtitleContractError("release ledger text audit schema drift")
    agents = text_audit.get("agents")
    if (
        text_audit.get("independent_agent_count") != 2
        or not isinstance(agents, list)
        or len(agents) != 2
        or len(set(agents)) != 2
        or any(not isinstance(agent, str) or not agent for agent in agents)
        or text_audit.get("cue_coverage_count") != gates["cue_count"]
        or text_audit.get("complete_coverage") is not True
        or text_audit.get("fresh_arbitration_replay") is not True
    ):
        raise Stage5SubtitleContractError("release ledger text audit gate failed")
    for digest_field in ("text_ledger_sha256", "unresolved_components_sha256"):
        digest = text_audit.get(digest_field)
        if not isinstance(digest, str) or _SHA256_RE.fullmatch(digest) is None:
            raise Stage5SubtitleContractError(
                f"release ledger text audit {digest_field} is invalid"
            )
    audio_audit = ledger.get("audio_audit")
    if not isinstance(audio_audit, dict) or set(audio_audit) != {
        "major_component_count",
        "major_audio_reviewed_count",
        "accepted_major_component_count",
        "retained_major_component_count",
        "nonmajor_retained_original_count",
        "changed_cue_count",
        "changed_cue_ids",
        "retained_major",
        "evidence",
    }:
        raise Stage5SubtitleContractError("release ledger audio audit schema drift")
    accepted_major = audio_audit.get("accepted_major_component_count")
    retained_major_count = audio_audit.get("retained_major_component_count")
    changed_cue_count = audio_audit.get("changed_cue_count")
    changed_cue_ids = audio_audit.get("changed_cue_ids")
    retained_major = audio_audit.get("retained_major")
    evidence = audio_audit.get("evidence")
    major_count = audio_audit.get("major_component_count")
    if (
        type(accepted_major) is not int
        or type(retained_major_count) is not int
        or type(changed_cue_count) is not int
        or min(accepted_major, retained_major_count, changed_cue_count) < 0
        or type(major_count) is not int
        or accepted_major + retained_major_count != major_count
        or not isinstance(changed_cue_ids, list)
        or changed_cue_count != len(changed_cue_ids)
        or any(type(cue_id) is not int or cue_id < 1 for cue_id in changed_cue_ids)
        or changed_cue_ids != sorted(set(changed_cue_ids))
        or not isinstance(retained_major, list)
        or len(retained_major) != retained_major_count
        or not isinstance(evidence, list)
        or len(evidence) != major_count
    ):
        raise Stage5SubtitleContractError("release ledger audio audit gate failed")
    release_policy = ledger.get("release_policy")
    if release_policy != {
        "primary_text_authority": "accepted Memo large-v2",
        "text_correction": "two independent audits plus strict arbitration",
        "major_risk_audio": "Faster plus Qwen dual-ASR evidence required",
        "major_conflict": "retain Memo text",
        "nonmajor_unresolved": "retain Memo text",
    }:
        raise Stage5SubtitleContractError("release ledger policy drift")
    ledger_gates = {
        "major_component_count": audio_audit.get("major_component_count"),
        "major_audio_reviewed_count": audio_audit.get("major_audio_reviewed_count"),
        "nonmajor_retained_original_count": audio_audit.get("nonmajor_retained_original_count"),
        "cue_count": ledger.get("cue_count"),
        "non_positive_duration_count": ledger.get("non_positive_duration_count"),
        "overlap_count": ledger.get("overlap_count"),
    }
    for key in _DEGRADED_COUNT_GATES:
        if type(ledger_gates[key]) is not int or ledger_gates[key] != gates[key]:
            raise Stage5SubtitleContractError(f"release ledger/gates drift for {key}")
    release_srt_declaration = ledger.get("release_srt")
    if not isinstance(release_srt_declaration, dict) or set(release_srt_declaration) != {
        "path",
        "sha256",
        "size_bytes",
    }:
        raise Stage5SubtitleContractError("release ledger SRT declaration schema drift")
    if (
        release_srt_declaration.get("path") != srt_path.relative_to(ledger_path.parent).as_posix()
        or release_srt_declaration.get("sha256") != _sha256(srt_raw)
        or release_srt_declaration.get("size_bytes") != len(srt_raw)
    ):
        raise Stage5SubtitleContractError("release ledger SRT declaration drift")
    actual_metrics = _degraded_srt_metrics(srt_raw)
    expected_metrics = (
        gates["cue_count"],
        gates["non_positive_duration_count"],
        gates["overlap_count"],
    )
    if actual_metrics != expected_metrics:
        raise Stage5SubtitleContractError("Memo Dual-Audit release actual SRT metrics drift")
    try:
        canonical_srt = srt_path.relative_to(manifest_path.parent).as_posix()
        canonical_ledger = ledger_path.relative_to(manifest_path.parent).as_posix()
    except ValueError as exc:
        raise Stage5SubtitleContractError("release artifacts escape export bundle") from exc
    if set(manifest) != {
        "schema_version",
        "contract",
        "episode_id",
        "canonical_release_srt",
        "canonical_release_srt_sha256",
        "release_ledger",
        "release_ledger_sha256",
        "file_count",
        "files",
    }:
        raise Stage5SubtitleContractError("export manifest schema drift")
    if (
        manifest.get("schema_version") != 1
        or manifest.get("contract") != _MEMO_DUAL_AUDIT_EXPORT_CONTRACT
        or manifest.get("episode_id") != episode_id
        or manifest.get("release_ledger") != canonical_ledger
        or manifest.get("release_ledger_sha256") != _sha256(ledger_raw)
    ):
        raise Stage5SubtitleContractError("export manifest contract/episode drift")
    if manifest.get("canonical_release_srt") != canonical_srt or manifest.get(
        "canonical_release_srt_sha256"
    ) != _sha256(srt_raw):
        raise Stage5SubtitleContractError("export manifest canonical release SRT drift")
    files = manifest.get("files")
    if not isinstance(files, list) or manifest.get("file_count") != len(files):
        raise Stage5SubtitleContractError("export manifest files are invalid")
    entries: dict[str, dict[str, object]] = {}
    for item in files:
        if not isinstance(item, dict) or set(item) != {
            "path",
            "sha256",
            "size_bytes",
        }:
            raise Stage5SubtitleContractError("export manifest file entry schema drift")
        path_value = item.get("path")
        if not isinstance(path_value, str) or path_value in entries:
            raise Stage5SubtitleContractError("export manifest file paths are invalid")
        entries[path_value] = item
    expected_entries = {
        canonical_srt: (_sha256(srt_raw), len(srt_raw)),
        canonical_ledger: (_sha256(ledger_raw), len(ledger_raw)),
    }
    if manifest.get("file_count") != 2 or set(entries) != set(expected_entries):
        raise Stage5SubtitleContractError("export manifest file population drift")
    for path_value, (expected_hash, expected_size) in expected_entries.items():
        entry = entries.get(path_value)
        if entry is None or (
            entry.get("sha256"),
            entry.get("size_bytes"),
        ) != (expected_hash, expected_size):
            raise Stage5SubtitleContractError("export manifest release entry drift")
    return Stage5MemoDualAuditReleaseHandoff(
        srt_path=srt_path,
        handoff_path=handoff_path,
        release_ledger_path=ledger_path,
        export_manifest_path=manifest_path,
        episode_id=episode_id,
        srt_sha256=_sha256(srt_raw),
        handoff_sha256=_sha256(handoff_raw),
        release_ledger_sha256=_sha256(ledger_raw),
        export_manifest_sha256=_sha256(manifest_raw),
    )


def _open_degraded_release_handoff(
    root: Path, handoff_value: str | Path
) -> Stage5DegradedReleaseHandoff:
    handoff_path = _bound_relative_path(root, str(handoff_value), label="degraded handoff")
    try:
        handoff_raw = handoff_path.read_bytes()
    except OSError as exc:
        raise Stage5SubtitleContractError("degraded handoff is missing or unreadable") from exc
    payload = _strict_json_object(handoff_raw, label="degraded handoff")
    required = {
        "schema_version",
        "contract",
        "episode_id",
        "provenance_status",
        "release_srt",
        "release_ledger",
        "export_manifest",
        "gates",
    }
    if set(payload) != required or payload.get("schema_version") != 1:
        raise Stage5SubtitleContractError("degraded handoff schema drift")
    if payload.get("contract") != _DEGRADED_HANDOFF_CONTRACT:
        raise Stage5SubtitleContractError("degraded handoff contract drift")
    if payload.get("provenance_status") != _DEGRADED_PROVENANCE:
        raise Stage5SubtitleContractError("degraded handoff provenance drift")
    episode_id = payload.get("episode_id")
    if not isinstance(episode_id, str) or not episode_id:
        raise Stage5SubtitleContractError("degraded handoff episode_id is invalid")
    srt_path, srt_raw = _read_declared_artifact(root, payload["release_srt"], label="release_srt")
    ledger_path, ledger_raw = _read_declared_artifact(
        root, payload["release_ledger"], label="release_ledger"
    )
    manifest_path, manifest_raw = _read_declared_artifact(
        root, payload["export_manifest"], label="export_manifest"
    )
    ledger = _strict_json_object(ledger_raw, label="release ledger")
    manifest = _strict_json_object(manifest_raw, label="export manifest")
    if _canonical_json_line(ledger) != ledger_raw or _canonical_json_line(manifest) != manifest_raw:
        raise Stage5SubtitleContractError("release ledger/export manifest is not canonical")
    gates = _validate_degraded_gates(payload["gates"])
    expected_ledger = {
        "schema_version": 1,
        "contract": _DEGRADED_LEDGER_CONTRACT,
        "episode_id": episode_id,
        "provenance_status": _DEGRADED_PROVENANCE,
        "output_srt_sha256": _sha256(srt_raw),
    }
    for key, value in expected_ledger.items():
        if ledger.get(key) != value:
            raise Stage5SubtitleContractError(f"release ledger {key} gate failed")
    for key in _DEGRADED_COUNT_GATES:
        if type(ledger.get(key)) is not int or ledger[key] != gates[key]:
            raise Stage5SubtitleContractError(f"release ledger/gates drift for {key}")
    actual_metrics = _degraded_srt_metrics(srt_raw)
    expected_metrics = (
        gates["cue_count"],
        gates["non_positive_duration_count"],
        gates["overlap_count"],
    )
    if actual_metrics != expected_metrics:
        raise Stage5SubtitleContractError("degraded release actual SRT metrics drift")
    if (
        manifest.get("schema_version") != 1
        or manifest.get("contract") != _DEGRADED_EXPORT_CONTRACT
        or manifest.get("episode_id") != episode_id
        or manifest.get("provenance_status") != _DEGRADED_PROVENANCE
    ):
        raise Stage5SubtitleContractError("export manifest contract/status drift")
    try:
        canonical_srt = srt_path.relative_to(manifest_path.parent).as_posix()
        canonical_ledger = ledger_path.relative_to(manifest_path.parent).as_posix()
    except ValueError as exc:
        raise Stage5SubtitleContractError("release artifacts escape export bundle") from exc
    if manifest.get("canonical_release_srt") != canonical_srt or manifest.get(
        "canonical_release_srt_sha256"
    ) != _sha256(srt_raw):
        raise Stage5SubtitleContractError("export manifest canonical release SRT drift")
    files = manifest.get("files")
    if not isinstance(files, list) or manifest.get("file_count") != len(files):
        raise Stage5SubtitleContractError("export manifest files are invalid")
    entries: dict[str, dict[str, object]] = {}
    for item in files:
        if not isinstance(item, dict) or set(item) != {"path", "sha256", "size_bytes"}:
            raise Stage5SubtitleContractError("export manifest file entry schema drift")
        path_value = item.get("path")
        if not isinstance(path_value, str) or path_value in entries:
            raise Stage5SubtitleContractError("export manifest file paths are invalid")
        entries[path_value] = item
    expected_entries = {
        canonical_srt: (_sha256(srt_raw), len(srt_raw)),
        canonical_ledger: (_sha256(ledger_raw), len(ledger_raw)),
    }
    for path_value, (expected_hash, expected_size) in expected_entries.items():
        entry = entries.get(path_value)
        if entry is None or (entry.get("sha256"), entry.get("size_bytes")) != (
            expected_hash,
            expected_size,
        ):
            raise Stage5SubtitleContractError("export manifest release entry drift")
    return Stage5DegradedReleaseHandoff(
        srt_path=srt_path,
        handoff_path=handoff_path,
        release_ledger_path=ledger_path,
        export_manifest_path=manifest_path,
        episode_id=episode_id,
        provenance_status=_DEGRADED_PROVENANCE,
        srt_sha256=_sha256(srt_raw),
        handoff_sha256=_sha256(handoff_raw),
        release_ledger_sha256=_sha256(ledger_raw),
        export_manifest_sha256=_sha256(manifest_raw),
    )


def current_stage5_handoff_path(episode_root: str | Path) -> Path:
    """Return the single episode-local persisted Verified Projection handoff."""

    return Path(episode_root).resolve() / _CURRENT_HANDOFF


def _relative_artifact(root: Path, path: Path) -> str:
    try:
        return path.resolve().relative_to(root).as_posix()
    except ValueError as exc:
        raise Stage5SubtitleContractError(
            "Stage 5 handoff artifacts must remain inside the episode root"
        ) from exc


def _atomic_replace(path: Path, payload: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.is_file() and path.read_bytes() == payload:
        return
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{path.name}.", suffix=".tmp", dir=path.parent
    )
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def _persist_current_handoff(
    root: Path,
    handoff: Stage5SubtitleHandoff,
    reference_manifest: str | Path | None,
) -> None:
    receipt_bytes = handoff.receipt_path.read_bytes()
    reference_path: str | None = None
    reference_sha256: str | None = None
    if reference_manifest is not None:
        resolved_reference = Path(reference_manifest).resolve()
        try:
            reference_bytes = resolved_reference.read_bytes()
        except OSError as exc:
            raise Stage5SubtitleContractError(
                "reference manifest cannot be persisted into the Stage 5 handoff"
            ) from exc
        reference_path = str(resolved_reference)
        reference_sha256 = _sha256(reference_bytes)
    body: dict[str, object] = {
        "schema_version": 2,
        "contract": _CURRENT_CONTRACT,
        "episode_id": handoff.verified.episode_id,
        "generation_id": handoff.verified.generation_id,
        "projection_id": handoff.verified.projection_id,
        "projection_manifest_sha256": handoff.verified.manifest_sha256,
        "reference_manifest_path": reference_path,
        "reference_manifest_sha256": reference_sha256,
        "srt_path": _relative_artifact(root, handoff.srt_path),
        "srt_sha256": handoff.verified.srt_sha256,
        "srt_size_bytes": len(handoff.verified.srt_bytes),
        "projection_handoff_receipt_path": _relative_artifact(root, handoff.receipt_path),
        "projection_handoff_receipt_sha256": _sha256(receipt_bytes),
        "projection_handoff_receipt_size_bytes": len(receipt_bytes),
    }
    body["content_hash"] = _sha256(_canonical_json(body))
    _atomic_replace(current_stage5_handoff_path(root), _canonical_json(body))


def _strict_current_handoff(raw: bytes) -> dict[str, object]:
    def strict_pairs(pairs: list[tuple[str, object]]) -> dict[str, object]:
        result: dict[str, object] = {}
        for key, value in pairs:
            if key in result:
                raise ValueError(f"duplicate key {key!r}")
            result[key] = value
        return result

    try:
        payload = json.loads(raw.decode("utf-8"), object_pairs_hook=strict_pairs)
    except (UnicodeDecodeError, json.JSONDecodeError, ValueError) as exc:
        raise Stage5SubtitleContractError("persisted Stage 5 handoff is not strict JSON") from exc
    if not isinstance(payload, dict) or _canonical_json(payload) != raw:
        raise Stage5SubtitleContractError("persisted Stage 5 handoff is not canonical exact bytes")
    required = {
        "schema_version",
        "contract",
        "episode_id",
        "generation_id",
        "projection_id",
        "projection_manifest_sha256",
        "reference_manifest_path",
        "reference_manifest_sha256",
        "srt_path",
        "srt_sha256",
        "srt_size_bytes",
        "projection_handoff_receipt_path",
        "projection_handoff_receipt_sha256",
        "projection_handoff_receipt_size_bytes",
        "content_hash",
    }
    if set(payload) != required or payload.get("schema_version") != 2:
        raise Stage5SubtitleContractError("persisted Stage 5 handoff schema drift")
    if payload.get("contract") != _CURRENT_CONTRACT:
        raise Stage5SubtitleContractError("persisted Stage 5 handoff contract drift")
    content_hash = payload.get("content_hash")
    unsigned = {key: value for key, value in payload.items() if key != "content_hash"}
    if content_hash != _sha256(_canonical_json(unsigned)):
        raise Stage5SubtitleContractError("persisted Stage 5 handoff content hash mismatch")
    return payload


def _bound_local_path(root: Path, value: object, *, label: str) -> Path:
    if not isinstance(value, str) or not value:
        raise Stage5SubtitleContractError(f"persisted Stage 5 handoff lacks {label}")
    path = (root / value).resolve()
    try:
        path.relative_to(root)
    except ValueError as exc:
        raise Stage5SubtitleContractError(
            f"persisted Stage 5 handoff {label} escapes the episode root"
        ) from exc
    return path


def _open_persisted_stage5_subtitle(
    root: Path,
    *,
    factory: ProjectionVerifierFactory | None,
) -> Stage5SubtitleHandoff:
    current = current_stage5_handoff_path(root)
    if not current.is_file():
        raise Stage5SubtitleContractError(
            "formal Stage 5 requires a persisted Verified Projection handoff; "
            "run project/resolve once with explicit projection lineage"
        )
    payload = _strict_current_handoff(current.read_bytes())
    srt_path = _bound_local_path(root, payload["srt_path"], label="srt_path")
    receipt_path = _bound_local_path(
        root,
        payload["projection_handoff_receipt_path"],
        label="projection_handoff_receipt_path",
    )
    try:
        srt_bytes = srt_path.read_bytes()
        receipt_bytes = receipt_path.read_bytes()
    except OSError as exc:
        raise Stage5SubtitleContractError(
            "persisted Stage 5 handoff artifact is missing or unreadable"
        ) from exc
    if (
        _sha256(srt_bytes) != payload["srt_sha256"]
        or len(srt_bytes) != payload["srt_size_bytes"]
        or _sha256(receipt_bytes) != payload["projection_handoff_receipt_sha256"]
        or len(receipt_bytes) != payload["projection_handoff_receipt_size_bytes"]
    ):
        raise Stage5SubtitleContractError("persisted Stage 5 handoff artifact hash mismatch")
    reference_manifest = payload["reference_manifest_path"]
    if reference_manifest is not None:
        if not isinstance(reference_manifest, str):
            raise Stage5SubtitleContractError("persisted reference manifest path is invalid")
        try:
            current_reference_hash = _sha256(Path(reference_manifest).resolve().read_bytes())
        except OSError as exc:
            raise Stage5SubtitleContractError(
                "persisted reference manifest is unavailable"
            ) from exc
        if current_reference_hash != payload["reference_manifest_sha256"]:
            raise Stage5SubtitleContractError("persisted reference manifest bytes drifted")
    handoff = open_stage5_subtitle(
        episode_root=root,
        projection_id=str(payload["projection_id"]),
        expected_episode_id=str(payload["episode_id"]),
        expected_generation_id=str(payload["generation_id"]),
        expected_manifest_sha256=str(payload["projection_manifest_sha256"]),
        reference_manifest=reference_manifest,
        factory=factory,
        persist_current=False,
    )
    if handoff.srt_path != srt_path or handoff.receipt_path != receipt_path:
        raise Stage5SubtitleContractError("persisted Stage 5 handoff path lineage drift")
    return handoff


def open_stage5_subtitle(
    *,
    episode_root: str | Path,
    projection_id: str,
    expected_episode_id: str,
    expected_generation_id: str,
    expected_manifest_sha256: str,
    reference_manifest: str | Path | None = None,
    factory: ProjectionVerifierFactory | None = None,
    persist_current: bool = True,
) -> Stage5SubtitleHandoff:
    """Verify one Projection and materialize only its exact SRT bytes for Stage 5."""

    root = Path(episode_root).resolve()
    verified = open_verified_projection(
        episode_root=root,
        projection_id=projection_id,
        expected_episode_id=expected_episode_id,
        expected_generation_id=expected_generation_id,
        expected_manifest_sha256=expected_manifest_sha256,
        reference_manifest=reference_manifest,
        factory=factory,
    )
    out_dir = root / ".stage5" / "verified-projections" / verified.projection_id
    out_dir.mkdir(parents=True, exist_ok=True)
    srt_path = out_dir / "transcript.srt"
    _write_immutable(srt_path, verified.srt_bytes)
    receipt_path = out_dir / "handoff.json"
    receipt = {
        "schema_version": 1,
        "episode_id": verified.episode_id,
        "generation_id": verified.generation_id,
        "manifest_sha256": verified.manifest_sha256,
        "projection_id": verified.projection_id,
        "srt_path": str(srt_path),
        "srt_sha256": verified.srt_sha256,
    }
    receipt_bytes = (
        json.dumps(receipt, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    ).encode("utf-8")
    _write_immutable(
        receipt_path,
        receipt_bytes,
    )
    handoff = Stage5SubtitleHandoff(
        srt_path=srt_path,
        receipt_path=receipt_path,
        verified=verified,
    )
    if persist_current:
        _persist_current_handoff(root, handoff, reference_manifest)
    return handoff


def select_stage5_subtitle(
    *,
    episode_root: str | Path,
    legacy_v1: bool = False,
    subtitle_release_handoff: str | Path | None = None,
    degraded_release_handoff: str | Path | None = None,
    projection_id: str | None = None,
    expected_episode_id: str | None = None,
    expected_generation_id: str | None = None,
    expected_manifest_sha256: str | None = None,
    reference_manifest: str | Path | None = None,
    factory: ProjectionVerifierFactory | None = None,
) -> Stage5SubtitleSelection:
    """Select official Memo Dual-Audit by default; legacy modes require opt-in."""

    root = Path(episode_root).resolve()
    identities = {
        "projection_id": projection_id,
        "expected_episode_id": expected_episode_id,
        "expected_generation_id": expected_generation_id,
        "expected_manifest_sha256": expected_manifest_sha256,
    }
    supplied = [name for name, value in identities.items() if value is not None]
    if subtitle_release_handoff is not None:
        if legacy_v1 or degraded_release_handoff is not None or supplied or reference_manifest:
            raise Stage5SubtitleContractError(
                "--subtitle-release-handoff cannot be combined with formal, degraded, "
                "or legacy identity"
            )
        handoff = _open_memo_dual_audit_release_handoff(
            root,
            subtitle_release_handoff,
        )
        return Stage5SubtitleSelection(
            mode="memo-dual-audit-v1",
            srt_path=handoff.srt_path,
            handoff=handoff,
        )
    if degraded_release_handoff is not None:
        if legacy_v1 or supplied or reference_manifest is not None:
            raise Stage5SubtitleContractError(
                "--degraded-release-handoff cannot be combined with formal or legacy identity"
            )
        handoff = _open_degraded_release_handoff(root, degraded_release_handoff)
        return Stage5SubtitleSelection(
            mode="degraded-dual-asr-v1",
            srt_path=handoff.srt_path,
            handoff=handoff,
        )
    if legacy_v1:
        if supplied or reference_manifest is not None:
            raise Stage5SubtitleContractError(
                "--legacy-v1 cannot be combined with Verified Projection identity"
            )
        srt_path = root / "transcript.srt"
        if not srt_path.is_file():
            raise FileNotFoundError(f"legacy transcript does not exist: {srt_path}")
        return Stage5SubtitleSelection(
            mode="legacy-v1",
            srt_path=srt_path,
            handoff=None,
        )

    if not supplied and reference_manifest is None:
        handoff = _open_memo_dual_audit_release_handoff(
            root,
            _MEMO_DUAL_AUDIT_DEFAULT_HANDOFF,
        )
        return Stage5SubtitleSelection(
            mode="memo-dual-audit-v1",
            srt_path=handoff.srt_path,
            handoff=handoff,
        )

    missing = [name for name, value in identities.items() if not value]
    if missing:
        raise Stage5SubtitleContractError(
            "formal Stage 5 requires Verified Projection identity: " + ", ".join(missing)
        )
    handoff = open_stage5_subtitle(
        episode_root=root,
        projection_id=projection_id or "",
        expected_episode_id=expected_episode_id or "",
        expected_generation_id=expected_generation_id or "",
        expected_manifest_sha256=expected_manifest_sha256 or "",
        reference_manifest=reference_manifest,
        factory=factory,
    )
    return Stage5SubtitleSelection(
        mode="verified-v2",
        srt_path=handoff.srt_path,
        handoff=handoff,
    )


_SHA256_PATTERN = r"^[0-9a-f]{64}$"
_GENERATION_PATTERN = r"^(?:generation-)?[0-9a-f]{64}$"
_PROJECTION_PATTERN = r"^projection-[0-9a-f]{64}$"


class VerifiedProjectionHandoffError(ValueError):
    """The Stage 5 episode is not bound to one fully verified projection."""


class SubtitleV2HandoffBinding(BaseModel):
    """Closed operator binding stored under ``episode.yaml``."""

    model_config = {"extra": "forbid"}

    schema_version: Literal[1] = 1
    episode_root: Path
    projection_id: str = Field(pattern=_PROJECTION_PATTERN)
    generation_id: str = Field(pattern=_GENERATION_PATTERN)
    projection_manifest_sha256: str = Field(pattern=_SHA256_PATTERN)
    reference_manifest: Path | None = None


class StoryboardProjectionProvenance(BaseModel):
    """Exact immutable Stage 4 identity consumed to create a storyboard."""

    model_config = {"extra": "forbid"}

    schema_version: Literal[1] = 1
    episode_id: str
    projection_id: str = Field(pattern=_PROJECTION_PATTERN)
    generation_id: str = Field(pattern=_GENERATION_PATTERN)
    projection_sha256: str = Field(pattern=_SHA256_PATTERN)
    projection_manifest_sha256: str = Field(pattern=_SHA256_PATTERN)
    quality_report_sha256: str = Field(pattern=_SHA256_PATTERN)
    srt_sha256: str = Field(pattern=_SHA256_PATTERN)
    canonical_hash: str = Field(pattern=_SHA256_PATTERN)
    profile_hash: str = Field(pattern=_SHA256_PATTERN)
    token_sequence_hash: str = Field(pattern=_SHA256_PATTERN)
    output_hash: str = Field(pattern=_SHA256_PATTERN)

    @classmethod
    def from_verified(
        cls,
        verified: VerifiedProjection,
    ) -> StoryboardProjectionProvenance:
        return cls(
            episode_id=verified.episode_id,
            projection_id=verified.projection_id,
            generation_id=verified.generation_id,
            projection_sha256=verified.projection_sha256,
            projection_manifest_sha256=verified.manifest_sha256,
            quality_report_sha256=verified.quality_report_sha256,
            srt_sha256=verified.srt_sha256,
            canonical_hash=verified.manifest.canonical_hash,
            profile_hash=verified.manifest.profile_hash,
            token_sequence_hash=verified.manifest.token_sequence_hash,
            output_hash=verified.manifest.output_hash,
        )


def _resolve_from(base: Path, value: Path) -> Path:
    return value.resolve() if value.is_absolute() else (base / value).resolve()


def require_verified_projection(
    *,
    ep_dir: Path,
    expected_episode_id: str,
    episode_meta: dict,
) -> VerifiedProjection:
    """Parse the closed episode binding, then invoke the full V2 disk verifier."""

    if episode_meta.get("id") != expected_episode_id or ep_dir.name != expected_episode_id:
        raise VerifiedProjectionHandoffError(
            "episode.yaml id, directory name, and requested episode must match"
        )
    raw_binding = episode_meta.get("subtitle_v2_handoff")
    if raw_binding is None:
        raise SystemExit(
            "episode.yaml requires subtitle_v2_handoff; a bare transcript.srt is "
            "legacy/unverified input and cannot enter Stage 5 production"
        )
    try:
        binding = SubtitleV2HandoffBinding.model_validate(raw_binding)
    except ValidationError as exc:
        raise VerifiedProjectionHandoffError(
            f"episode.yaml subtitle_v2_handoff is invalid: {exc}"
        ) from exc
    episode_root = _resolve_from(ep_dir, binding.episode_root)
    if not episode_root.is_dir():
        raise VerifiedProjectionHandoffError(
            f"subtitle_v2_handoff episode_root is not a directory: {episode_root}"
        )
    reference_manifest = (
        _resolve_from(episode_root, binding.reference_manifest)
        if binding.reference_manifest is not None
        else None
    )
    return open_verified_projection(
        episode_root=episode_root,
        projection_id=binding.projection_id,
        expected_episode_id=expected_episode_id,
        expected_generation_id=binding.generation_id,
        expected_manifest_sha256=binding.projection_manifest_sha256,
        reference_manifest=reference_manifest,
    )


def write_storyboard_provenance(ep_dir: Path, verified: VerifiedProjection) -> Path:
    path = ep_dir / "storyboard.provenance.json"
    provenance = StoryboardProjectionProvenance.from_verified(verified)
    path.write_text(
        json.dumps(
            provenance.model_dump(mode="json"),
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ),
        encoding="utf-8",
    )
    return path


def assert_storyboard_provenance(ep_dir: Path, verified: VerifiedProjection) -> None:
    path = ep_dir / "storyboard.provenance.json"
    try:
        payload = path.read_bytes()
        stored = StoryboardProjectionProvenance.model_validate_json(payload)
    except (OSError, ValidationError, ValueError) as exc:
        raise VerifiedProjectionHandoffError(
            f"storyboard provenance is missing or invalid: {path}"
        ) from exc
    expected = StoryboardProjectionProvenance.from_verified(verified)
    if stored != expected:
        raise VerifiedProjectionHandoffError(
            "storyboard provenance drifted from the freshly verified projection handoff"
        )


__all__ = [
    "Stage5DegradedReleaseHandoff",
    "Stage5MemoDualAuditReleaseHandoff",
    "Stage5SubtitleArtifactConflictError",
    "Stage5SubtitleContractError",
    "Stage5SubtitleHandoff",
    "Stage5SubtitleRequest",
    "Stage5SubtitleSelection",
    "StoryboardProjectionProvenance",
    "SubtitleV2HandoffBinding",
    "VerifiedProjectionHandoffError",
    "assert_storyboard_provenance",
    "current_stage5_handoff_path",
    "open_stage5_subtitle",
    "require_verified_projection",
    "select_stage5_subtitle",
    "write_storyboard_provenance",
]
