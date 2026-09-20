"""Signed append-only custody for :mod:`transcript_gold` artifacts.

The custody ledger establishes one narrow fact: exact annotation-packet and
candidate bytes were sealed in a signed hash chain before exact gold bytes
were admitted.  It deliberately does not use filesystem mtimes or the
self-attested timestamps carried by legacy gold/candidate schemas.

Every mutating operation requires an operator-supplied existing Ed25519
OpenSSH signing key.  The corresponding trusted public key is supplied again
when the workspace is opened; the workspace copy is only a snapshot, never a
self-authorising trust root.
"""

from __future__ import annotations

import base64
import binascii
import io
import json
import os
import re
import shutil
import stat
import struct
import subprocess
import tempfile
import wave
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Any, ClassVar, Literal, Sequence, TypeVar

from filelock import FileLock
from pydantic import BaseModel, ConfigDict, field_validator, model_validator

from .candidate_materialization import (
    SourceBoundTranscriptCandidateArtifactV3,
    verify_source_bound_transcript_candidate,
)
from .hashing import canonical_json_bytes, hash_object, sha256_bytes
from .transcript_gold import (
    AudioOnlyTranscriptSubmission,
    MetricRate,
    TranscriptAdjudicationRecord,
    TranscriptAnnotationPacket,
    TranscriptCandidateArtifact,
    TranscriptEvaluationResult,
    TranscriptEvaluationStatus,
    TranscriptGoldSuite,
    TranscriptMetrics,
    evaluate_transcript_candidate,
    load_annotation_packet,
    load_transcript_candidate,
    load_transcript_evaluation,
    load_transcript_gold_suite,
    measure_lexical_evaluator_identity,
    verify_annotation_packet_blinding,
    verify_gold_blinding,
)

_SHA256_RE = re.compile(r"[0-9a-f]{64}\Z")
_SAFE_ID_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}\Z")
_PARTICIPANT_ID_RE = re.compile(r"[a-z0-9][a-z0-9._-]{0,127}\Z")
_EVENT_DIR_RE = re.compile(
    r"(?P<sequence>[0-9]{6})-(?P<phase>[a-z_]+)-(?P<digest>[0-9a-f]{64})\Z"
)

_PROTOCOL_ID = "transcript-gold-custody-v1"
_SIGNATURE_NAMESPACE = "nakama-transcript-gold-custody-v1"
_SIGNATURE_ALGORITHM = "openssh-sshsig-ed25519-v1"
_SSHSIG_BEGIN = b"-----BEGIN SSH SIGNATURE-----"
_SSHSIG_END = b"-----END SSH SIGNATURE-----"


class _StrictFrozenModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)


def _require_sha256(label: str, value: str) -> str:
    if not _SHA256_RE.fullmatch(value):
        raise ValueError(f"{label} must be lowercase SHA-256")
    return value


def _require_safe_id(label: str, value: str) -> str:
    if not _SAFE_ID_RE.fullmatch(value):
        raise ValueError(f"{label} must be a filesystem-safe opaque identifier")
    return value


def _require_participant_id(label: str, value: str) -> str:
    if not _PARTICIPANT_ID_RE.fullmatch(value):
        raise ValueError(
            f"{label} must be a canonical lowercase opaque participant identifier"
        )
    return value


def _artifact_hash(model: BaseModel, *, field: str, kind: str) -> str:
    return hash_object(
        {
            "artifact_kind": kind,
            **model.model_dump(mode="json", exclude={field}),
        }
    )


def _prospective_hash(payload: dict[str, Any], *, kind: str) -> str:
    return hash_object({"artifact_kind": kind, **payload})


class CustodyPhase(str, Enum):
    INITIALIZED = "initialized"
    PACKETS_SEALED = "packets_sealed"
    CANDIDATES_SEALED = "candidates_sealed"
    SUBMISSIONS_IMPORTED = "submissions_imported"
    GOLD_SEALED = "gold_sealed"
    EVALUATION_EXPORTED = "evaluation_exported"


_PHASES = (
    CustodyPhase.INITIALIZED,
    CustodyPhase.PACKETS_SEALED,
    CustodyPhase.CANDIDATES_SEALED,
    CustodyPhase.SUBMISSIONS_IMPORTED,
    CustodyPhase.GOLD_SEALED,
    CustodyPhase.EVALUATION_EXPORTED,
)

ArtifactRole = Literal[
    "annotation_packet",
    "normalized_audio",
    "annotation_audio_clip",
    "transcript_candidate",
    "audio_only_submission",
    "transcript_adjudication",
    "transcript_gold_suite",
    "custody_evaluation_manifest",
    "transcript_evaluation",
]

_ROLE_ORDER = {
    role: index
    for index, role in enumerate(
        (
            "annotation_packet",
            "normalized_audio",
            "annotation_audio_clip",
            "transcript_candidate",
            "audio_only_submission",
            "transcript_adjudication",
            "transcript_gold_suite",
            "custody_evaluation_manifest",
            "transcript_evaluation",
        )
    )
}


class CustodyArtifactReferenceV1(_StrictFrozenModel):
    schema_version: Literal[1] = 1
    role: ArtifactRole
    artifact_id: str
    content_sha256: str
    size_bytes: int
    object_relpath: str

    @field_validator("artifact_id", "content_sha256")
    @classmethod
    def _hashes(cls, value: str, info: Any) -> str:
        return _require_sha256(info.field_name, value)

    @model_validator(mode="after")
    def _valid(self) -> CustodyArtifactReferenceV1:
        if self.size_bytes < 1:
            raise ValueError("custody artifact size must be positive")
        expected = _object_relpath(self.content_sha256)
        if self.object_relpath != expected:
            raise ValueError("custody artifact object_relpath is not content-addressed")
        return self


class GoldCustodyWorkspaceManifestV1(_StrictFrozenModel):
    _HASH_KIND: ClassVar[str] = "gold_custody_workspace_manifest"

    schema_version: Literal[1] = 1
    workspace_id: str
    protocol_id: Literal["transcript-gold-custody-v1"] = _PROTOCOL_ID
    signer_id: str
    signature_namespace: Literal[
        "nakama-transcript-gold-custody-v1"
    ] = _SIGNATURE_NAMESPACE
    signature_algorithm: Literal[
        "openssh-sshsig-ed25519-v1"
    ] = _SIGNATURE_ALGORITHM
    human_identity_authority: Literal[
        "external_collection_process"
    ] = "external_collection_process"
    participant_role_attestation: Literal[
        "operator_attests_canonical_ids_and_role_distinctness"
    ] = "operator_attests_canonical_ids_and_role_distinctness"
    filesystem_threat_model: Literal[
        "trusted_acl_single_writer_local"
    ] = "trusted_acl_single_writer_local"
    concurrent_same_privilege_attacker_resistance: Literal[False] = False
    trusted_public_key_sha256: str
    manifest_hash: str

    @field_validator("workspace_id", "signer_id")
    @classmethod
    def _ids(cls, value: str, info: Any) -> str:
        return _require_safe_id(info.field_name, value)

    @field_validator("trusted_public_key_sha256", "manifest_hash")
    @classmethod
    def _hashes(cls, value: str, info: Any) -> str:
        return _require_sha256(info.field_name, value)

    @model_validator(mode="after")
    def _valid(self) -> GoldCustodyWorkspaceManifestV1:
        if self.manifest_hash != _artifact_hash(
            self,
            field="manifest_hash",
            kind=self._HASH_KIND,
        ):
            raise ValueError("gold custody workspace manifest_hash mismatch")
        return self

    @classmethod
    def build(
        cls,
        *,
        workspace_id: str,
        signer_id: str,
        trusted_public_key_sha256: str,
    ) -> GoldCustodyWorkspaceManifestV1:
        payload = {
            "schema_version": 1,
            "workspace_id": workspace_id,
            "protocol_id": _PROTOCOL_ID,
            "signer_id": signer_id,
            "signature_namespace": _SIGNATURE_NAMESPACE,
            "signature_algorithm": _SIGNATURE_ALGORITHM,
            "human_identity_authority": "external_collection_process",
            "participant_role_attestation": (
                "operator_attests_canonical_ids_and_role_distinctness"
            ),
            "filesystem_threat_model": "trusted_acl_single_writer_local",
            "concurrent_same_privilege_attacker_resistance": False,
            "trusted_public_key_sha256": trusted_public_key_sha256,
        }
        return cls(
            **payload,
            manifest_hash=_prospective_hash(payload, kind=cls._HASH_KIND),
        )


def _artifact_ref_key(value: CustodyArtifactReferenceV1) -> tuple[int, str, str]:
    return (_ROLE_ORDER[value.role], value.artifact_id, value.content_sha256)


class GoldCustodyEventV1(_StrictFrozenModel):
    _HASH_KIND: ClassVar[str] = "gold_custody_event"

    schema_version: Literal[1] = 1
    workspace_id: str
    workspace_manifest_hash: str
    sequence: int
    phase: CustodyPhase
    previous_event_hash: str | None
    signer_id: str
    human_identity_authority: Literal[
        "external_collection_process"
    ] = "external_collection_process"
    participant_role_attestation: Literal[
        "operator_attests_canonical_ids_and_role_distinctness"
    ] = "operator_attests_canonical_ids_and_role_distinctness"
    artifacts: tuple[CustodyArtifactReferenceV1, ...]
    event_hash: str

    @field_validator("workspace_id", "signer_id")
    @classmethod
    def _ids(cls, value: str, info: Any) -> str:
        return _require_safe_id(info.field_name, value)

    @field_validator("workspace_manifest_hash", "previous_event_hash", "event_hash")
    @classmethod
    def _hashes(cls, value: str | None, info: Any) -> str | None:
        if value is not None:
            _require_sha256(info.field_name, value)
        return value

    @model_validator(mode="after")
    def _valid(self) -> GoldCustodyEventV1:
        expected_sequence = _PHASES.index(self.phase)
        if self.sequence != expected_sequence:
            raise ValueError("custody phase has a noncanonical sequence")
        if (self.sequence == 0) != (self.previous_event_hash is None):
            raise ValueError("only the initialization event may omit previous_event_hash")
        if self.artifacts != tuple(sorted(self.artifacts, key=_artifact_ref_key)):
            raise ValueError("custody event artifacts must be in canonical order")
        pairs = [(item.role, item.artifact_id) for item in self.artifacts]
        if len(set(pairs)) != len(pairs):
            raise ValueError("custody event contains duplicate artifact roles/identities")
        roles = tuple(item.role for item in self.artifacts)
        if self.phase is CustodyPhase.INITIALIZED and roles:
            raise ValueError("initialization event cannot carry phase artifacts")
        if self.phase is CustodyPhase.PACKETS_SEALED and (
            roles.count("annotation_packet") != 1
            or roles.count("normalized_audio") != 1
            or roles.count("annotation_audio_clip") < 1
            or set(roles)
            != {"annotation_packet", "normalized_audio", "annotation_audio_clip"}
        ):
            raise ValueError("packet seal requires one packet and every exact audio clip")
        if self.phase is CustodyPhase.CANDIDATES_SEALED and (
            not roles or set(roles) != {"transcript_candidate"}
        ):
            raise ValueError("candidate seal requires only one or more candidates")
        if self.phase is CustodyPhase.SUBMISSIONS_IMPORTED and any(
            role not in {"audio_only_submission", "transcript_adjudication"}
            for role in roles
        ):
            raise ValueError("submission import contains an invalid artifact role")
        if self.phase is CustodyPhase.GOLD_SEALED and roles != (
            "transcript_gold_suite",
        ):
            raise ValueError("gold seal requires exactly one gold suite")
        if self.phase is CustodyPhase.EVALUATION_EXPORTED and (
            roles.count("custody_evaluation_manifest") != 1
            or roles.count("transcript_evaluation") < 1
            or set(roles)
            != {"custody_evaluation_manifest", "transcript_evaluation"}
        ):
            raise ValueError("evaluation export requires its manifest and evaluations")
        if self.event_hash != _artifact_hash(
            self,
            field="event_hash",
            kind=self._HASH_KIND,
        ):
            raise ValueError("gold custody event_hash mismatch")
        return self

    @classmethod
    def build(
        cls,
        *,
        manifest: GoldCustodyWorkspaceManifestV1,
        phase: CustodyPhase,
        previous_event_hash: str | None,
        artifacts: Sequence[CustodyArtifactReferenceV1],
    ) -> GoldCustodyEventV1:
        payload = {
            "schema_version": 1,
            "workspace_id": manifest.workspace_id,
            "workspace_manifest_hash": manifest.manifest_hash,
            "sequence": _PHASES.index(phase),
            "phase": phase,
            "previous_event_hash": previous_event_hash,
            "signer_id": manifest.signer_id,
            "human_identity_authority": manifest.human_identity_authority,
            "participant_role_attestation": manifest.participant_role_attestation,
            "artifacts": tuple(sorted(artifacts, key=_artifact_ref_key)),
        }
        return cls(
            **payload,
            event_hash=_prospective_hash(payload, kind=cls._HASH_KIND),
        )

    def canonical_bytes(self) -> bytes:
        return canonical_json_bytes(self)


class CustodyEvaluationBindingV1(_StrictFrozenModel):
    candidate_artifact_hash: str
    evaluation_hash: str
    evaluation_content_sha256: str
    status: TranscriptEvaluationStatus
    correction_metrics_status: TranscriptEvaluationStatus

    @field_validator(
        "candidate_artifact_hash",
        "evaluation_hash",
        "evaluation_content_sha256",
    )
    @classmethod
    def _hashes(cls, value: str, info: Any) -> str:
        return _require_sha256(info.field_name, value)


class CustodyEvaluationStatus(str, Enum):
    EVALUATED = "evaluated"
    NOT_EVALUATED = "not_evaluated"
    MIXED = "mixed"


class GoldCustodyEvaluationManifestV1(_StrictFrozenModel):
    _HASH_KIND: ClassVar[str] = "gold_custody_evaluation_manifest"

    schema_version: Literal[1] = 1
    workspace_id: str
    workspace_manifest_hash: str
    order_authority: Literal[
        "signed_append_only_hash_chain_v1"
    ] = "signed_append_only_hash_chain_v1"
    human_identity_authority: Literal[
        "external_collection_process"
    ] = "external_collection_process"
    phase_event_hashes: tuple[str, str, str, str, str]
    annotation_packet_hash: str
    candidate_artifact_hashes: tuple[str, ...]
    audio_only_submission_hashes: tuple[str, ...]
    adjudication_record_hashes: tuple[str, ...]
    gold_suite_hash: str
    status: CustodyEvaluationStatus
    evaluations: tuple[CustodyEvaluationBindingV1, ...]
    evaluation_manifest_hash: str

    @field_validator("workspace_id")
    @classmethod
    def _workspace_id(cls, value: str) -> str:
        return _require_safe_id("workspace_id", value)

    @field_validator(
        "workspace_manifest_hash",
        "annotation_packet_hash",
        "gold_suite_hash",
        "evaluation_manifest_hash",
    )
    @classmethod
    def _hashes(cls, value: str, info: Any) -> str:
        return _require_sha256(info.field_name, value)

    @field_validator(
        "phase_event_hashes",
        "candidate_artifact_hashes",
        "audio_only_submission_hashes",
        "adjudication_record_hashes",
    )
    @classmethod
    def _hash_collections(cls, values: tuple[str, ...], info: Any) -> tuple[str, ...]:
        for value in values:
            _require_sha256(info.field_name, value)
        if info.field_name != "phase_event_hashes":
            if values != tuple(sorted(set(values))):
                raise ValueError(f"{info.field_name} must be unique canonical order")
        return values

    @model_validator(mode="after")
    def _valid(self) -> GoldCustodyEvaluationManifestV1:
        if not self.candidate_artifact_hashes:
            raise ValueError("custody evaluation manifest requires candidates")
        if tuple(item.candidate_artifact_hash for item in self.evaluations) != (
            self.candidate_artifact_hashes
        ):
            raise ValueError("custody evaluation bindings differ from sealed candidates")
        statuses = {item.status for item in self.evaluations}
        expected_status = (
            CustodyEvaluationStatus.EVALUATED
            if statuses == {TranscriptEvaluationStatus.EVALUATED}
            else CustodyEvaluationStatus.NOT_EVALUATED
            if statuses == {TranscriptEvaluationStatus.NOT_EVALUATED}
            else CustodyEvaluationStatus.MIXED
        )
        if self.status is not expected_status:
            raise ValueError("custody evaluation manifest aggregate status mismatch")
        if self.evaluation_manifest_hash != _artifact_hash(
            self,
            field="evaluation_manifest_hash",
            kind=self._HASH_KIND,
        ):
            raise ValueError("custody evaluation manifest hash mismatch")
        return self

    @classmethod
    def build(
        cls,
        *,
        workspace: GoldCustodyWorkspaceManifestV1,
        phase_event_hashes: Sequence[str],
        annotation_packet_hash: str,
        candidate_artifact_hashes: Sequence[str],
        audio_only_submission_hashes: Sequence[str],
        adjudication_record_hashes: Sequence[str],
        gold_suite_hash: str,
        evaluations: Sequence[CustodyEvaluationBindingV1],
    ) -> GoldCustodyEvaluationManifestV1:
        selected = tuple(evaluations)
        statuses = {item.status for item in selected}
        status = (
            CustodyEvaluationStatus.EVALUATED
            if statuses == {TranscriptEvaluationStatus.EVALUATED}
            else CustodyEvaluationStatus.NOT_EVALUATED
            if statuses == {TranscriptEvaluationStatus.NOT_EVALUATED}
            else CustodyEvaluationStatus.MIXED
        )
        payload = {
            "schema_version": 1,
            "workspace_id": workspace.workspace_id,
            "workspace_manifest_hash": workspace.manifest_hash,
            "order_authority": "signed_append_only_hash_chain_v1",
            "human_identity_authority": "external_collection_process",
            "phase_event_hashes": tuple(phase_event_hashes),
            "annotation_packet_hash": annotation_packet_hash,
            "candidate_artifact_hashes": tuple(sorted(candidate_artifact_hashes)),
            "audio_only_submission_hashes": tuple(
                sorted(audio_only_submission_hashes)
            ),
            "adjudication_record_hashes": tuple(sorted(adjudication_record_hashes)),
            "gold_suite_hash": gold_suite_hash,
            "status": status,
            "evaluations": selected,
        }
        return cls(
            **payload,
            evaluation_manifest_hash=_prospective_hash(payload, kind=cls._HASH_KIND),
        )

    def canonical_bytes(self) -> bytes:
        return canonical_json_bytes(self)


class CustodyCommandResultV1(_StrictFrozenModel):
    schema_version: Literal[1] = 1
    workspace_id: str
    workspace_manifest_hash: str
    phase: CustodyPhase
    phase_event_hash: str
    current_head_hash: str
    event_count: int
    replay_status: Literal["committed", "replayed"]
    evaluation_status: Literal[
        "not_applicable", "evaluated", "not_evaluated", "mixed"
    ]
    artifact_ids: tuple[str, ...]
    human_identity_authority: Literal[
        "external_collection_process"
    ] = "external_collection_process"
    human_identity_attestations_custodied: Literal[False] = False
    filesystem_threat_model: Literal[
        "trusted_acl_single_writer_local"
    ] = "trusted_acl_single_writer_local"
    concurrent_same_privilege_attacker_resistance: Literal[False] = False

    @field_validator("workspace_id")
    @classmethod
    def _workspace_id(cls, value: str) -> str:
        return _require_safe_id("workspace_id", value)

    @field_validator("workspace_manifest_hash", "phase_event_hash", "current_head_hash")
    @classmethod
    def _hashes(cls, value: str, info: Any) -> str:
        return _require_sha256(info.field_name, value)

    def canonical_bytes(self) -> bytes:
        return canonical_json_bytes(self)


class VerifiedGoldCustodyStateV1(_StrictFrozenModel):
    manifest: GoldCustodyWorkspaceManifestV1
    events: tuple[GoldCustodyEventV1, ...]
    phase: CustodyPhase

    @model_validator(mode="after")
    def _valid(self) -> VerifiedGoldCustodyStateV1:
        if not self.events or self.phase is not self.events[-1].phase:
            raise ValueError("verified custody state phase mismatch")
        return self


@dataclass(frozen=True)
class _LoadedArtifacts:
    packet: TranscriptAnnotationPacket | None = None
    normalized_audio: CustodyArtifactReferenceV1 | None = None
    annotation_audio_clips: tuple[CustodyArtifactReferenceV1, ...] = ()
    candidates: tuple[TranscriptCandidateArtifact, ...] = ()
    submissions: tuple[AudioOnlyTranscriptSubmission, ...] = ()
    adjudications: tuple[TranscriptAdjudicationRecord, ...] = ()
    gold: TranscriptGoldSuite | None = None
    evaluations: tuple[TranscriptEvaluationResult, ...] = ()
    evaluation_manifest: GoldCustodyEvaluationManifestV1 | None = None
    uncommitted_object_hashes: tuple[str, ...] = ()


_ModelT = TypeVar("_ModelT", bound=BaseModel)


def _load_exact_model(raw: bytes, model: type[_ModelT], *, label: str) -> _ModelT:
    try:
        untyped = json.loads(raw)
        if not isinstance(untyped, dict):
            raise ValueError("top-level artifact must be an object")
        expected = set(model.model_fields)
        if set(untyped) != expected:
            raise ValueError("top-level fields are not exact")
        value = model.model_validate_json(raw, strict=True)
    except (json.JSONDecodeError, TypeError, ValueError) as exc:
        raise ValueError(f"invalid {label}") from exc
    if canonical_json_bytes(value) != raw:
        raise ValueError(f"{label} bytes are not exact canonical JSON")
    return value


def load_gold_custody_workspace_manifest(
    source: bytes | bytearray | Path,
) -> GoldCustodyWorkspaceManifestV1:
    raw = (
        _read_regular_file(source, label="workspace manifest")
        if isinstance(source, Path)
        else bytes(source)
    )
    return _load_exact_model(raw, GoldCustodyWorkspaceManifestV1, label="gold custody manifest")


def load_gold_custody_event(source: bytes | bytearray | Path) -> GoldCustodyEventV1:
    raw = (
        _read_regular_file(source, label="custody event")
        if isinstance(source, Path)
        else bytes(source)
    )
    return _load_exact_model(raw, GoldCustodyEventV1, label="gold custody event")


def load_gold_custody_evaluation_manifest(
    source: bytes | bytearray | Path,
) -> GoldCustodyEvaluationManifestV1:
    raw = (
        _read_regular_file(source, label="evaluation manifest")
        if isinstance(source, Path)
        else bytes(source)
    )
    return _load_exact_model(
        raw,
        GoldCustodyEvaluationManifestV1,
        label="gold custody evaluation manifest",
    )


def _is_reparse(path: Path) -> bool:
    value = path.lstat()
    flag = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)
    return stat.S_ISLNK(value.st_mode) or bool(
        int(getattr(value, "st_file_attributes", 0)) & flag
    )


def _assert_no_link_ancestors(path: Path, *, stop_at: Path | None = None) -> None:
    current = path.absolute()
    stop = None if stop_at is None else stop_at.absolute()
    while True:
        if current.exists() and _is_reparse(current):
            raise ValueError(f"custody path cannot use a symlink or reparse point: {current}")
        if current == current.parent or current == stop:
            return
        current = current.parent


def _safe_workspace(path: Path) -> Path:
    root = path.absolute()
    if str(root).startswith("\\\\"):
        raise ValueError("custody workspace must use a trusted local filesystem")
    _assert_no_link_ancestors(root)
    if root.exists() and not root.is_dir():
        raise ValueError("custody workspace must be a directory")
    return root


def _verify_workspace_layout(root: Path) -> None:
    allowed_root = {
        "workspace-manifest.json",
        "trust",
        "events",
        "objects",
        "exports",
        "custody.lock",
    }
    entries = {item.name: item for item in root.iterdir()}
    unknown = set(entries) - allowed_root
    if unknown:
        raise ValueError(f"custody workspace contains extra root entries: {sorted(unknown)}")
    lock = entries.get("custody.lock")
    if lock is not None and (_is_reparse(lock) or not lock.is_file()):
        raise ValueError("custody lock path is not a regular file")
    trust = entries.get("trust")
    if trust is not None:
        if _is_reparse(trust) or not trust.is_dir():
            raise ValueError("custody trust path is not a regular directory")
        if {item.name for item in trust.iterdir()} != {"public-key.pub"}:
            raise ValueError("custody trust directory contains a missing or extra file")
    exports = entries.get("exports")
    if exports is not None:
        if _is_reparse(exports) or not exports.is_dir():
            raise ValueError("custody exports path is not a regular directory")
        for entry in exports.iterdir():
            if (
                entry.name not in {"annotation", "evaluation"}
                or _is_reparse(entry)
                or not entry.is_dir()
            ):
                raise ValueError("custody exports contain an extra or malformed entry")


def _require_external_key(root: Path, path: Path, *, label: str) -> Path:
    key = path.absolute()
    try:
        key.relative_to(root.absolute())
    except ValueError:
        return key
    raise ValueError(f"{label} must remain outside the custody workspace")


def _read_regular_file(path: Path, *, label: str) -> bytes:
    candidate = path.absolute()
    _assert_no_link_ancestors(candidate)
    before = candidate.lstat()
    if _is_reparse(candidate) or not stat.S_ISREG(before.st_mode):
        raise ValueError(f"{label} must be a non-reparse regular file")
    with candidate.open("rb") as stream:
        opened_before = os.fstat(stream.fileno())
        raw = stream.read()
        opened_after = os.fstat(stream.fileno())
    after = candidate.lstat()
    path_identity = lambda item: (  # noqa: E731 - compact immutable stat projection
        item.st_dev,
        item.st_ino,
        item.st_mode,
        int(getattr(item, "st_file_attributes", 0)),
        item.st_size,
        item.st_mtime_ns,
        item.st_ctime_ns,
    )
    stream_identity = lambda item: (  # noqa: E731
        item.st_dev,
        item.st_ino,
        item.st_mode,
        item.st_size,
        item.st_mtime_ns,
        item.st_ctime_ns,
    )
    core_identity = lambda item: (item.st_dev, item.st_ino, item.st_size)  # noqa: E731
    if (
        path_identity(before) != path_identity(after)
        or stream_identity(opened_before) != stream_identity(opened_after)
        or core_identity(before) != core_identity(opened_before)
        or core_identity(after) != core_identity(opened_after)
    ):
        raise ValueError(f"{label} changed while being read")
    return raw


def _canonical_public_key(path: Path) -> bytes:
    raw = _read_regular_file(path, label="trusted public key")
    try:
        text = raw.decode("ascii")
    except UnicodeDecodeError as exc:
        raise ValueError("trusted public key must be ASCII OpenSSH format") from exc
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    if len(lines) != 1:
        raise ValueError("trusted public key must contain exactly one key")
    parts = lines[0].split()
    if len(parts) not in {2, 3} or parts[0] != "ssh-ed25519":
        raise ValueError("trusted public key must be one Ed25519 OpenSSH key")
    try:
        blob = base64.b64decode(parts[1], validate=True)
        offset = 0

        def field() -> bytes:
            nonlocal offset
            if offset + 4 > len(blob):
                raise ValueError
            length = struct.unpack(">I", blob[offset : offset + 4])[0]
            offset += 4
            result = blob[offset : offset + length]
            if len(result) != length:
                raise ValueError
            offset += length
            return result

        key_type = field()
        public = field()
    except (ValueError, struct.error) as exc:
        raise ValueError("trusted public key has an invalid OpenSSH payload") from exc
    if key_type != b"ssh-ed25519" or len(public) != 32 or offset != len(blob):
        raise ValueError("trusted public key is not a canonical Ed25519 key")
    return f"ssh-ed25519 {parts[1]}\n".encode("ascii")


def _ssh_keygen() -> str:
    executable = shutil.which("ssh-keygen")
    if executable is None:
        raise ValueError("trusted OpenSSH ssh-keygen executable is unavailable")
    return executable


def _verify_signature(
    payload: bytes,
    signature: bytes,
    *,
    public_key: bytes,
    signer_id: str,
) -> None:
    _validate_canonical_sshsig(signature)
    with tempfile.TemporaryDirectory(prefix="nakama-gold-custody-verify-") as temporary:
        root = Path(temporary)
        allowed = root / "allowed_signers"
        signature_path = root / "event.sshsig"
        allowed.write_bytes(signer_id.encode("ascii") + b" " + public_key)
        signature_path.write_bytes(signature)
        result = subprocess.run(
            [
                _ssh_keygen(),
                "-Y",
                "verify",
                "-f",
                str(allowed),
                "-I",
                signer_id,
                "-n",
                _SIGNATURE_NAMESPACE,
                "-s",
                str(signature_path),
            ],
            input=payload,
            capture_output=True,
            timeout=30,
            check=False,
        )
    if result.returncode != 0:
        raise ValueError("gold custody event signature verification failed")


def _validate_canonical_sshsig(signature: bytes) -> None:
    """Reject bytes OpenSSH ignores outside the canonical ASCII armour."""

    try:
        text = signature.decode("ascii")
    except UnicodeDecodeError as exc:
        raise ValueError("custody signature is not canonical OpenSSH SSHSIG armour") from exc
    lines = text.splitlines(keepends=True)
    if (
        not lines
        or lines[0] != _SSHSIG_BEGIN.decode() + "\n"
        or lines[-1] != _SSHSIG_END.decode() + "\n"
        or len(lines) < 3
        or any(not line.endswith("\n") for line in lines)
    ):
        raise ValueError("custody signature is not canonical OpenSSH SSHSIG armour")
    encoded_lines = [line[:-1] for line in lines[1:-1]]
    if any(not line or len(line) > 70 for line in encoded_lines):
        raise ValueError("custody signature is not canonical OpenSSH SSHSIG armour")
    encoded = "".join(encoded_lines)
    try:
        decoded = base64.b64decode(encoded, validate=True)
    except (ValueError, binascii.Error) as exc:
        raise ValueError("custody signature is not canonical OpenSSH SSHSIG armour") from exc
    canonical_body = base64.b64encode(decoded).decode("ascii")
    canonical_lines = [
        canonical_body[index : index + 70]
        for index in range(0, len(canonical_body), 70)
    ]
    canonical = (
        _SSHSIG_BEGIN
        + b"\n"
        + "\n".join(canonical_lines).encode("ascii")
        + b"\n"
        + _SSHSIG_END
        + b"\n"
    )
    if signature != canonical:
        raise ValueError("custody signature is not canonical OpenSSH SSHSIG armour")


def _sign_payload(
    payload: bytes,
    *,
    signing_key_path: Path,
    public_key: bytes,
    signer_id: str,
) -> bytes:
    key = signing_key_path.absolute()
    _read_regular_file(key, label="trusted signing key")
    with tempfile.TemporaryDirectory(prefix="nakama-gold-custody-sign-") as temporary:
        source = Path(temporary) / "event"
        source.write_bytes(payload)
        result = subprocess.run(
            [
                _ssh_keygen(),
                "-Y",
                "sign",
                "-f",
                str(key),
                "-n",
                _SIGNATURE_NAMESPACE,
                str(source),
            ],
            stdin=subprocess.DEVNULL,
            capture_output=True,
            timeout=30,
            check=False,
        )
        signature_path = Path(f"{source}.sig")
        if result.returncode != 0 or not signature_path.is_file():
            raise ValueError("trusted signing key could not sign the custody event")
        signature = signature_path.read_bytes()
    _verify_signature(
        payload,
        signature,
        public_key=public_key,
        signer_id=signer_id,
    )
    return signature


def _object_relpath(content_sha256: str) -> str:
    return f"objects/sha256/{content_sha256[:2]}/{content_sha256}"


def _atomic_write_immutable(path: Path, payload: bytes, *, root: Path) -> None:
    absolute = path.absolute()
    try:
        absolute.relative_to(root.absolute())
    except ValueError as exc:
        raise ValueError("custody output path escapes workspace") from exc
    _assert_no_link_ancestors(absolute.parent, stop_at=root)
    absolute.parent.mkdir(parents=True, exist_ok=True)
    _assert_no_link_ancestors(absolute.parent, stop_at=root)
    digest = sha256_bytes(payload)
    temporary_prefix = f".custody-{digest}-"
    residues = sorted(
        item
        for item in absolute.parent.iterdir()
        if item.name.startswith(temporary_prefix)
    )
    for residue in residues:
        if (
            _is_reparse(residue)
            or not residue.is_file()
            or _read_regular_file(residue, label="temporary custody artifact") != payload
        ):
            raise ValueError("temporary custody artifact differs from requested exact bytes")
    if absolute.exists():
        if _is_reparse(absolute) or not absolute.is_file() or absolute.read_bytes() != payload:
            raise ValueError(f"immutable custody artifact conflict: {absolute}")
        for residue in residues:
            if (residue.stat().st_dev, residue.stat().st_ino) != (
                absolute.stat().st_dev,
                absolute.stat().st_ino,
            ):
                raise ValueError("temporary custody artifact is not an exact hard-link alias")
            residue.unlink()
        return
    if len(residues) > 1:
        raise ValueError("multiple temporary custody artifacts conflict")
    if residues:
        temporary = residues[0]
        os.link(temporary, absolute)
        temporary.unlink()
        return
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=temporary_prefix,
        dir=absolute.parent,
    )
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
        try:
            os.link(temporary, absolute)
        except FileExistsError:
            if (
                _is_reparse(absolute)
                or not absolute.is_file()
                or absolute.read_bytes() != payload
            ):
                raise ValueError(f"immutable custody artifact conflict: {absolute}")
    finally:
        if temporary.exists():
            temporary.unlink()


def _make_reference(
    *,
    role: ArtifactRole,
    artifact_id: str,
    raw: bytes,
) -> CustodyArtifactReferenceV1:
    content_hash = sha256_bytes(raw)
    return CustodyArtifactReferenceV1(
        role=role,
        artifact_id=artifact_id,
        content_sha256=content_hash,
        size_bytes=len(raw),
        object_relpath=_object_relpath(content_hash),
    )


def _put_object(root: Path, reference: CustodyArtifactReferenceV1, raw: bytes) -> None:
    if sha256_bytes(raw) != reference.content_sha256 or len(raw) != reference.size_bytes:
        raise ValueError("custody object bytes differ from their reference")
    _atomic_write_immutable(root / reference.object_relpath, raw, root=root)


def _extract_canonical_pcm_wav(
    normalized_audio: bytes,
    *,
    start_ms: int,
    end_ms: int,
) -> bytes:
    """Replay the frozen v1 clip extraction policy from exact PCM WAV bytes."""

    source = io.BytesIO(normalized_audio)
    try:
        with wave.open(source, "rb") as reader:
            if reader.getcomptype() != "NONE":
                raise ValueError("normalized audio must be uncompressed PCM WAV")
            rate = reader.getframerate()
            if rate <= 0 or (start_ms * rate) % 1000 or (end_ms * rate) % 1000:
                raise ValueError("clip millisecond boundaries must align to PCM WAV frames")
            start_frame = start_ms * rate // 1000
            end_frame = end_ms * rate // 1000
            if start_frame < 0 or end_frame <= start_frame or end_frame > reader.getnframes():
                raise ValueError("clip interval escapes normalized PCM WAV frames")
            channels = reader.getnchannels()
            sample_width = reader.getsampwidth()
            reader.setpos(start_frame)
            frames = reader.readframes(end_frame - start_frame)
            expected_size = (end_frame - start_frame) * channels * sample_width
            if len(frames) != expected_size:
                raise ValueError("normalized PCM WAV ended before the requested clip")
    except (EOFError, wave.Error) as exc:
        raise ValueError("normalized audio must be a valid uncompressed PCM WAV") from exc

    output = io.BytesIO()
    with wave.open(output, "wb") as writer:
        writer.setnchannels(channels)
        writer.setsampwidth(sample_width)
        writer.setframerate(rate)
        writer.setcomptype("NONE", "not compressed")
        writer.writeframes(frames)
    return output.getvalue()


def _read_object(root: Path, reference: CustodyArtifactReferenceV1) -> bytes:
    path = root / reference.object_relpath
    raw = _read_regular_file(path, label=f"{reference.role} custody object")
    if len(raw) != reference.size_bytes or sha256_bytes(raw) != reference.content_sha256:
        raise ValueError("content-addressed custody object mismatch")
    return raw


def _scan_object_hashes(
    root: Path,
    *,
    recover_temporary_aliases: bool,
) -> tuple[str, ...]:
    store = root / "objects" / "sha256"
    if not store.exists():
        return ()
    if _is_reparse(store) or not store.is_dir():
        raise ValueError("custody object store is not a regular directory")
    digests: list[str] = []
    for prefix in sorted(store.iterdir(), key=lambda item: item.name):
        if (
            not re.fullmatch(r"[0-9a-f]{2}", prefix.name)
            or _is_reparse(prefix)
            or not prefix.is_dir()
        ):
            raise ValueError("custody object store contains an extra entry")
        for path in sorted(prefix.iterdir(), key=lambda item: item.name):
            if path.name.startswith(".custody-"):
                if not recover_temporary_aliases:
                    raise ValueError("custody object store contains a temporary artifact")
                raw = _read_regular_file(path, label="temporary custody object alias")
                digest = sha256_bytes(raw)
                if not path.name.startswith(f".custody-{digest}-"):
                    raise ValueError("temporary custody object name differs from exact bytes")
                destination = prefix / digest
                if destination.exists():
                    if (
                        not destination.is_file()
                        or _is_reparse(destination)
                        or _read_regular_file(destination, label="custody object") != raw
                        or (path.stat().st_dev, path.stat().st_ino)
                        != (destination.stat().st_dev, destination.stat().st_ino)
                    ):
                        raise ValueError(
                            "temporary custody object is not an exact hard-link alias"
                        )
                    path.unlink()
                else:
                    digests.append(digest)
                continue
            if (
                not _SHA256_RE.fullmatch(path.name)
                or not path.name.startswith(prefix.name)
            ):
                raise ValueError("custody object store contains a malformed object path")
            raw = _read_regular_file(path, label="custody object")
            if sha256_bytes(raw) != path.name:
                raise ValueError("custody object path differs from its exact bytes")
            digests.append(path.name)
    if len(set(digests)) != len(digests):
        raise ValueError("custody object store contains duplicate content identities")
    return tuple(digests)


def _event_directory_name(event: GoldCustodyEventV1) -> str:
    return f"{event.sequence:06d}-{event.phase.value}-{event.event_hash}"


def _commit_event_directory(
    root: Path,
    event: GoldCustodyEventV1,
    signature: bytes,
) -> None:
    events_root = root / "events"
    events_root.mkdir(parents=True, exist_ok=True)
    destination = events_root / _event_directory_name(event)
    if destination.exists():
        if not destination.is_dir() or set(item.name for item in destination.iterdir()) != {
            "event.json",
            "signature.sshsig",
        }:
            raise ValueError("immutable custody event directory conflict")
        if (
            (destination / "event.json").read_bytes() != event.canonical_bytes()
            or (destination / "signature.sshsig").read_bytes() != signature
        ):
            raise ValueError("immutable custody event conflict")
        return
    temporary = Path(
        tempfile.mkdtemp(prefix=f".{root.name}-custody-event-", dir=root.parent)
    )
    try:
        for name, payload in (
            ("event.json", event.canonical_bytes()),
            ("signature.sshsig", signature),
        ):
            path = temporary / name
            with path.open("wb") as stream:
                stream.write(payload)
                stream.flush()
                os.fsync(stream.fileno())
        os.rename(temporary, destination)
    finally:
        if temporary.exists():
            shutil.rmtree(temporary)


def _validate_candidate_bindings(
    packet: TranscriptAnnotationPacket,
    candidate: TranscriptCandidateArtifact,
) -> None:
    if (
        candidate.episode_id != packet.episode_id
        or candidate.normalized_audio_hash != packet.normalized_audio_hash
        or candidate.normalized_audio_size_bytes != packet.normalized_audio_size_bytes
        or candidate.annotation_packet_hash != packet.packet_hash
        or candidate.expected_clip_count != len(packet.clips)
    ):
        raise ValueError("candidate differs from the sealed annotation packet lineage")
    plan_by_id = {item.clip_id: item for item in packet.clips}
    order = {item.clip_id: index for index, item in enumerate(packet.clips)}
    ids = [item.clip.clip_id for item in candidate.clips]
    if len(set(ids)) != len(ids) or set(ids) - set(plan_by_id):
        raise ValueError("candidate contains duplicate or cross-packet clips")
    if [order[item] for item in ids] != sorted(order[item] for item in ids):
        raise ValueError("candidate clips differ from packet canonical order")
    for output in candidate.clips:
        if output.clip != plan_by_id[output.clip.clip_id]:
            raise ValueError("candidate clip binding differs from the sealed packet")
    if isinstance(candidate, SourceBoundTranscriptCandidateArtifactV3):
        verify_source_bound_transcript_candidate(candidate, packet)


def _load_submission(raw: bytes) -> AudioOnlyTranscriptSubmission:
    return _load_exact_model(
        raw,
        AudioOnlyTranscriptSubmission,
        label="audio-only transcript submission",
    )


def _load_adjudication(raw: bytes) -> TranscriptAdjudicationRecord:
    return _load_exact_model(
        raw,
        TranscriptAdjudicationRecord,
        label="transcript adjudication record",
    )


def _validate_submission_lineage(
    packet: TranscriptAnnotationPacket,
    submissions: Sequence[AudioOnlyTranscriptSubmission],
    adjudications: Sequence[TranscriptAdjudicationRecord],
) -> None:
    clip_by_id = {item.clip_id: item for item in packet.clips}
    submission_by_hash = {item.submission_hash: item for item in submissions}
    if len(submission_by_hash) != len(submissions):
        raise ValueError("submission import contains duplicate artifacts")
    grouped: dict[str, list[AudioOnlyTranscriptSubmission]] = {}
    for submission in submissions:
        _require_participant_id("annotator_id", submission.annotator_id)
        expected = clip_by_id.get(submission.clip.clip_id)
        if submission.annotation_packet_hash != packet.packet_hash or submission.clip != expected:
            raise ValueError("audio-only submission has cross-packet or cross-clip lineage")
        grouped.setdefault(submission.clip.clip_id, []).append(submission)
    for values in grouped.values():
        if len(values) > 2 or len({item.annotator_id for item in values}) != len(values):
            raise ValueError("a clip may have at most two distinct audio-only annotators")

    seen_clips: set[str] = set()
    for record in adjudications:
        _require_participant_id("adjudicator_id", record.adjudicator_id)
        expected = clip_by_id.get(record.clip.clip_id)
        if record.annotation_packet_hash != packet.packet_hash or record.clip != expected:
            raise ValueError("adjudication has cross-packet or cross-clip lineage")
        if record.clip.clip_id in seen_clips:
            raise ValueError("a clip may have only one third-person adjudication")
        seen_clips.add(record.clip.clip_id)
        exact = tuple(
            submission_by_hash.get(value)
            for value in record.first_pass_submission_hashes
        )
        if any(item is None for item in exact):
            raise ValueError("adjudication does not bind imported audio-only submissions")
        selected = tuple(item for item in exact if item is not None)
        if any(item.clip != record.clip for item in selected):
            raise ValueError("adjudication binds a submission from another clip")
        annotators = tuple(item.annotator_id for item in selected)
        if (
            len(set(annotators)) != 2
            or annotators != tuple(sorted(annotators))
            or record.adjudicator_id in annotators
        ):
            raise ValueError("adjudication requires two distinct annotators and a third person")


def _validate_gold_lineage(
    *,
    packet: TranscriptAnnotationPacket,
    candidates: Sequence[TranscriptCandidateArtifact],
    submissions: Sequence[AudioOnlyTranscriptSubmission],
    adjudications: Sequence[TranscriptAdjudicationRecord],
    gold: TranscriptGoldSuite,
) -> None:
    if (
        gold.episode_id != packet.episode_id
        or gold.normalized_audio_hash != packet.normalized_audio_hash
        or gold.normalized_audio_size_bytes != packet.normalized_audio_size_bytes
        or gold.annotation_packet_hash != packet.packet_hash
        or gold.expected_clip_count != len(packet.clips)
        or gold.protocol != packet.protocol
        or gold.clips != packet.clips
    ):
        raise ValueError("gold suite differs from the sealed annotation packet")
    candidate_hashes = tuple(item.artifact_hash for item in candidates)
    verify_annotation_packet_blinding(packet, candidate_hashes)
    verify_gold_blinding(gold, candidate_hashes)

    imported_submissions = {item.submission_hash: item for item in submissions}
    imported_adjudications = {item.record_hash: item for item in adjudications}
    used_submissions: set[str] = set()
    used_adjudications: set[str] = set()
    for label in gold.labels:
        for submission in label.provenance.first_pass_submissions:
            if imported_submissions.get(submission.submission_hash) != submission:
                raise ValueError("gold embeds an unimported or changed audio-only submission")
            used_submissions.add(submission.submission_hash)
        record = label.provenance.adjudication_record
        if record is not None:
            if imported_adjudications.get(record.record_hash) != record:
                raise ValueError("gold embeds an unimported or changed adjudication")
            used_adjudications.add(record.record_hash)
    if used_submissions != set(imported_submissions):
        raise ValueError("imported audio-only submissions are not exactly represented in gold")
    if used_adjudications != set(imported_adjudications):
        raise ValueError("imported adjudications are not exactly represented in gold")


def _evaluate_with_signed_custody(
    suite: TranscriptGoldSuite,
    candidate: TranscriptCandidateArtifact,
    *,
    all_candidate_hashes: Sequence[str],
) -> TranscriptEvaluationResult:
    result = evaluate_transcript_candidate(
        suite,
        candidate,
        all_candidate_artifact_hashes=all_candidate_hashes,
    )
    if result.status is not TranscriptEvaluationStatus.EVALUATED:
        return result
    remaining_reasons = {
        reason
        for reason in result.correction_metrics_reason_codes
        if reason != "candidate_generated_before_target_reveal_unverified"
    }
    # Signed chronology proves the system candidate preceded target reveal.  It
    # does not custody the external books/reports/interview briefs behind a
    # spelling-authority source ID, so source precision (and therefore the
    # aggregate correction block in the v1 schema) must remain fail-closed.
    remaining_reasons.add("source_authority_evidence_not_custodied")
    if result.metrics is None:
        raise ValueError("evaluated transcript result unexpectedly lacks metrics")
    metrics = TranscriptMetrics(
        **result.metrics.model_dump(
            exclude={
                "correction_detection_recall",
                "correction_apply_recall",
                "false_keep_original_rate",
                "harmful_apply_rate",
                "source_precision",
            }
        ),
        correction_detection_recall=MetricRate.of(0, 0),
        correction_apply_recall=MetricRate.of(0, 0),
        false_keep_original_rate=MetricRate.of(0, 0),
        harmful_apply_rate=MetricRate.of(0, 0),
        source_precision=MetricRate.of(0, 0),
    )
    return TranscriptEvaluationResult.build(
        status=result.status,
        reason_codes=result.reason_codes,
        gold_suite_hash=result.gold_suite_hash,
        candidate_artifact_hash=result.candidate_artifact_hash,
        normalized_audio_hash=result.normalized_audio_hash,
        annotation_packet_hash=result.annotation_packet_hash,
        metrics=metrics,
        correction_metrics_status=TranscriptEvaluationStatus.NOT_EVALUATED,
        correction_metrics_reason_codes=tuple(sorted(remaining_reasons)),
        clip_results=result.clip_results,
    )


class GoldCustodyWorkspace:
    """Deep public interface for one trusted TranscriptGoldSuite custody run."""

    def __init__(
        self,
        root: Path,
        *,
        public_key: bytes,
        expected_head_hash: str | None,
        allow_one_successor_recovery: bool = False,
    ) -> None:
        self.root = _safe_workspace(root)
        self._public_key = public_key
        self._expected_head_hash = expected_head_hash
        self._allow_one_successor_recovery = allow_one_successor_recovery

    @classmethod
    def initialize(
        cls,
        root: Path,
        *,
        workspace_id: str,
        signer_id: str,
        signing_key_path: Path,
        public_key_path: Path,
        expected_head_hash: str | None = None,
    ) -> CustodyCommandResultV1:
        workspace_root = _safe_workspace(root)
        workspace_root.mkdir(parents=True, exist_ok=True)
        public_key = _canonical_public_key(
            _require_external_key(
                workspace_root,
                public_key_path,
                label="trusted public key",
            )
        )
        signing_key_path = _require_external_key(
            workspace_root,
            signing_key_path,
            label="trusted signing key",
        )
        if expected_head_hash is not None:
            _require_sha256("expected_head_hash", expected_head_hash)
        workspace = cls(
            workspace_root,
            public_key=public_key,
            expected_head_hash=expected_head_hash,
        )
        with workspace._lock():
            manifest = GoldCustodyWorkspaceManifestV1.build(
                workspace_id=workspace_id,
                signer_id=signer_id,
                trusted_public_key_sha256=sha256_bytes(public_key),
            )
            _atomic_write_immutable(
                workspace._manifest_path,
                canonical_json_bytes(manifest),
                root=workspace.root,
            )
            _atomic_write_immutable(
                workspace._public_key_path,
                public_key,
                root=workspace.root,
            )
            events = workspace._read_events(manifest)
            if events:
                if expected_head_hash is None:
                    raise ValueError(
                        "replaying an existing custody workspace requires expected_head_hash"
                    )
                if len(events) < 1 or events[0].phase is not CustodyPhase.INITIALIZED:
                    raise ValueError("custody workspace lacks its initialization event")
                event = events[0]
                replayed = True
            else:
                event = GoldCustodyEventV1.build(
                    manifest=manifest,
                    phase=CustodyPhase.INITIALIZED,
                    previous_event_hash=None,
                    artifacts=(),
                )
                signature = _sign_payload(
                    event.canonical_bytes(),
                    signing_key_path=signing_key_path,
                    public_key=public_key,
                    signer_id=manifest.signer_id,
                )
                _commit_event_directory(workspace.root, event, signature)
                workspace._expected_head_hash = event.event_hash
                replayed = False
            state, _ = workspace._read_verified()
            return workspace._command_result(
                state,
                event,
                replayed=replayed,
                evaluation_status="not_applicable",
            )

    @classmethod
    def open(
        cls,
        root: Path,
        *,
        public_key_path: Path,
        expected_head_hash: str,
        recover_incomplete_commit: bool = False,
    ) -> GoldCustodyWorkspace:
        workspace_root = _safe_workspace(root)
        public_key = _canonical_public_key(
            _require_external_key(
                workspace_root,
                public_key_path,
                label="trusted public key",
            )
        )
        _require_sha256("expected_head_hash", expected_head_hash)
        workspace = cls(
            workspace_root,
            public_key=public_key,
            expected_head_hash=expected_head_hash,
            allow_one_successor_recovery=recover_incomplete_commit,
        )
        with workspace._lock():
            workspace._read_verified(
                allow_uncommitted_artifacts=recover_incomplete_commit,
            )
        return workspace

    @property
    def _manifest_path(self) -> Path:
        return self.root / "workspace-manifest.json"

    @property
    def _public_key_path(self) -> Path:
        return self.root / "trust" / "public-key.pub"

    def _lock(self) -> FileLock:
        return FileLock(str(self.root / "custody.lock"), timeout=30)

    def verify(self) -> VerifiedGoldCustodyStateV1:
        with self._lock():
            state, _ = self._read_verified()
            return state

    def _read_manifest(self) -> GoldCustodyWorkspaceManifestV1:
        manifest = load_gold_custody_workspace_manifest(self._manifest_path)
        stored_key = _read_regular_file(self._public_key_path, label="stored trusted public key")
        if stored_key != self._public_key:
            raise ValueError("workspace public key differs from the external trusted key")
        if manifest.trusted_public_key_sha256 != sha256_bytes(self._public_key):
            raise ValueError("workspace manifest public-key binding mismatch")
        return manifest

    def _read_events(
        self,
        manifest: GoldCustodyWorkspaceManifestV1,
    ) -> tuple[GoldCustodyEventV1, ...]:
        events_root = self.root / "events"
        if not events_root.exists():
            return ()
        if _is_reparse(events_root) or not events_root.is_dir():
            raise ValueError("custody events root is not a regular directory")
        directories = sorted(events_root.iterdir(), key=lambda item: item.name)
        events: list[GoldCustodyEventV1] = []
        previous: str | None = None
        for expected_sequence, directory in enumerate(directories):
            match = _EVENT_DIR_RE.fullmatch(directory.name)
            if match is None or _is_reparse(directory) or not directory.is_dir():
                raise ValueError("custody ledger contains an extra or malformed event")
            names = {item.name for item in directory.iterdir()}
            if names != {"event.json", "signature.sshsig"}:
                raise ValueError("custody event contains a missing or extra file")
            event_raw = _read_regular_file(directory / "event.json", label="custody event")
            event = load_gold_custody_event(event_raw)
            if (
                event.sequence != expected_sequence
                or event.sequence >= len(_PHASES)
                or event.phase is not _PHASES[expected_sequence]
                or int(match.group("sequence")) != event.sequence
                or match.group("phase") != event.phase.value
                or match.group("digest") != event.event_hash
                or directory.name != _event_directory_name(event)
            ):
                raise ValueError("custody event reorder, deletion, or filename mismatch")
            if (
                event.workspace_id != manifest.workspace_id
                or event.workspace_manifest_hash != manifest.manifest_hash
                or event.signer_id != manifest.signer_id
                or event.human_identity_authority != manifest.human_identity_authority
                or event.participant_role_attestation
                != manifest.participant_role_attestation
                or event.previous_event_hash != previous
            ):
                raise ValueError("custody event cross-workspace or hash-chain mismatch")
            signature = _read_regular_file(
                directory / "signature.sshsig",
                label="custody event signature",
            )
            _verify_signature(
                event_raw,
                signature,
                public_key=self._public_key,
                signer_id=manifest.signer_id,
            )
            for reference in event.artifacts:
                _read_object(self.root, reference)
            events.append(event)
            previous = event.event_hash
        return tuple(events)

    def _read_verified(
        self,
        *,
        allow_uncommitted_artifacts: bool = False,
    ) -> tuple[VerifiedGoldCustodyStateV1, _LoadedArtifacts]:
        _verify_workspace_layout(self.root)
        manifest = self._read_manifest()
        events = self._read_events(manifest)
        if not events or events[0].phase is not CustodyPhase.INITIALIZED:
            raise ValueError("custody workspace is not initialized")
        if self._expected_head_hash != events[-1].event_hash:
            recoverable_successor = (
                self._allow_one_successor_recovery
                and len(events) >= 2
                and events[-1].previous_event_hash == self._expected_head_hash
            )
            if not recoverable_successor:
                raise ValueError("custody ledger head differs from external expected_head_hash")

        packet: TranscriptAnnotationPacket | None = None
        normalized_audio: CustodyArtifactReferenceV1 | None = None
        annotation_audio_clips: tuple[CustodyArtifactReferenceV1, ...] = ()
        candidates: tuple[TranscriptCandidateArtifact, ...] = ()
        submissions: tuple[AudioOnlyTranscriptSubmission, ...] = ()
        adjudications: tuple[TranscriptAdjudicationRecord, ...] = ()
        gold: TranscriptGoldSuite | None = None
        evaluations: tuple[TranscriptEvaluationResult, ...] = ()
        evaluation_manifest: GoldCustodyEvaluationManifestV1 | None = None
        for event in events:
            if event.phase is CustodyPhase.PACKETS_SEALED:
                reference = next(
                    item for item in event.artifacts if item.role == "annotation_packet"
                )
                raw = _read_object(self.root, reference)
                packet = load_annotation_packet(raw)
                if reference.artifact_id != packet.packet_hash:
                    raise ValueError("sealed annotation packet identity mismatch")
                normalized_audio = next(
                    item for item in event.artifacts if item.role == "normalized_audio"
                )
                normalized_raw = _read_object(self.root, normalized_audio)
                if (
                    normalized_audio.artifact_id != packet.normalized_audio_hash
                    or normalized_audio.content_sha256 != packet.normalized_audio_hash
                    or normalized_audio.size_bytes != packet.normalized_audio_size_bytes
                ):
                    raise ValueError("sealed normalized audio differs from packet lineage")
                annotation_audio_clips = tuple(
                    item
                    for item in event.artifacts
                    if item.role == "annotation_audio_clip"
                )
                expected_clips = {
                    clip.binding_hash: (clip.clip_audio_hash, clip.clip_audio_size_bytes)
                    for clip in packet.clips
                }
                observed_clips = {
                    item.artifact_id: (item.content_sha256, item.size_bytes)
                    for item in annotation_audio_clips
                }
                if observed_clips != expected_clips:
                    raise ValueError("sealed annotation audio clips differ from packet bindings")
                for clip in packet.clips:
                    derived = _extract_canonical_pcm_wav(
                        normalized_raw,
                        start_ms=clip.start_ms,
                        end_ms=clip.end_ms,
                    )
                    if (
                        sha256_bytes(derived) != clip.clip_audio_hash
                        or len(derived) != clip.clip_audio_size_bytes
                    ):
                        raise ValueError(
                            "annotation clip is not the deterministic normalized-audio slice"
                        )
            elif event.phase is CustodyPhase.CANDIDATES_SEALED:
                values = []
                for reference in event.artifacts:
                    value = load_transcript_candidate(_read_object(self.root, reference))
                    if reference.artifact_id != value.artifact_hash:
                        raise ValueError("sealed candidate identity mismatch")
                    values.append(value)
                candidates = tuple(values)
            elif event.phase is CustodyPhase.SUBMISSIONS_IMPORTED:
                submission_values = []
                adjudication_values = []
                for reference in event.artifacts:
                    raw = _read_object(self.root, reference)
                    if reference.role == "audio_only_submission":
                        value = _load_submission(raw)
                        if reference.artifact_id != value.submission_hash:
                            raise ValueError("imported submission identity mismatch")
                        submission_values.append(value)
                    else:
                        value = _load_adjudication(raw)
                        if reference.artifact_id != value.record_hash:
                            raise ValueError("imported adjudication identity mismatch")
                        adjudication_values.append(value)
                submissions = tuple(submission_values)
                adjudications = tuple(adjudication_values)
            elif event.phase is CustodyPhase.GOLD_SEALED:
                reference = event.artifacts[0]
                gold = load_transcript_gold_suite(_read_object(self.root, reference))
                if reference.artifact_id != gold.suite_hash:
                    raise ValueError("sealed gold identity mismatch")
            elif event.phase is CustodyPhase.EVALUATION_EXPORTED:
                evaluation_values = []
                for reference in event.artifacts:
                    raw = _read_object(self.root, reference)
                    if reference.role == "custody_evaluation_manifest":
                        evaluation_manifest = load_gold_custody_evaluation_manifest(raw)
                        if reference.artifact_id != evaluation_manifest.evaluation_manifest_hash:
                            raise ValueError("custody evaluation manifest identity mismatch")
                    else:
                        value = load_transcript_evaluation(raw)
                        if reference.artifact_id != value.evaluation_hash:
                            raise ValueError("custodied evaluation identity mismatch")
                        evaluation_values.append(value)
                evaluations = tuple(evaluation_values)

        referenced_objects = {
            reference.content_sha256
            for event in events
            for reference in event.artifacts
        }
        object_hashes = set(
            _scan_object_hashes(
                self.root,
                recover_temporary_aliases=allow_uncommitted_artifacts,
            )
        )
        if not referenced_objects.issubset(object_hashes):
            raise ValueError("signed custody event references a missing object")
        uncommitted_objects = tuple(sorted(object_hashes - referenced_objects))
        if uncommitted_objects and not allow_uncommitted_artifacts:
            raise ValueError("custody store contains an uncommitted or extra object")

        annotation_export = self.root / "exports" / "annotation"
        evaluation_export = self.root / "exports" / "evaluation"
        if (
            not allow_uncommitted_artifacts
            and packet is None
            and annotation_export.exists()
            and any(annotation_export.iterdir())
        ):
            raise ValueError("annotation export exists without its signed custody event")
        if (
            not allow_uncommitted_artifacts
            and evaluation_manifest is None
            and evaluation_export.exists()
            and any(evaluation_export.iterdir())
        ):
            raise ValueError("evaluation export exists without its signed custody event")
        if packet is not None and normalized_audio is not None:
            self._verify_annotation_export(
                packet,
                normalized_audio,
                annotation_audio_clips,
                allow_temporary_artifacts=allow_uncommitted_artifacts,
            )
        if packet is not None and candidates:
            for candidate in candidates:
                _validate_candidate_bindings(packet, candidate)
            verify_annotation_packet_blinding(
                packet,
                tuple(item.artifact_hash for item in candidates),
            )
        if packet is not None and len(events) > _PHASES.index(CustodyPhase.SUBMISSIONS_IMPORTED):
            _validate_submission_lineage(packet, submissions, adjudications)
        if gold is not None:
            if packet is None:
                raise ValueError("gold exists without a sealed annotation packet")
            _validate_gold_lineage(
                packet=packet,
                candidates=candidates,
                submissions=submissions,
                adjudications=adjudications,
                gold=gold,
            )
        if evaluation_manifest is not None:
            if packet is None or gold is None:
                raise ValueError("evaluation exists without packet and gold")
            self._validate_evaluation_lineage(
                manifest=manifest,
                events=events,
                packet=packet,
                candidates=candidates,
                submissions=submissions,
                adjudications=adjudications,
                gold=gold,
                evaluations=evaluations,
                evaluation_manifest=evaluation_manifest,
            )
            self._verify_evaluation_exports(
                evaluations,
                evaluation_manifest,
                allow_temporary_artifacts=allow_uncommitted_artifacts,
            )
        state = VerifiedGoldCustodyStateV1(
            manifest=manifest,
            events=events,
            phase=events[-1].phase,
        )
        return state, _LoadedArtifacts(
            packet=packet,
            normalized_audio=normalized_audio,
            annotation_audio_clips=annotation_audio_clips,
            candidates=candidates,
            submissions=submissions,
            adjudications=adjudications,
            gold=gold,
            evaluations=evaluations,
            evaluation_manifest=evaluation_manifest,
            uncommitted_object_hashes=uncommitted_objects,
        )

    @staticmethod
    def _assert_recovery_objects(
        artifacts: _LoadedArtifacts,
        references: Sequence[CustodyArtifactReferenceV1],
    ) -> None:
        expected = {item.content_sha256 for item in references}
        unexpected = set(artifacts.uncommitted_object_hashes) - expected
        if unexpected:
            raise ValueError("custody recovery found objects outside the requested exact phase")

    def _validate_evaluation_lineage(
        self,
        *,
        manifest: GoldCustodyWorkspaceManifestV1,
        events: Sequence[GoldCustodyEventV1],
        packet: TranscriptAnnotationPacket,
        candidates: Sequence[TranscriptCandidateArtifact],
        submissions: Sequence[AudioOnlyTranscriptSubmission],
        adjudications: Sequence[TranscriptAdjudicationRecord],
        gold: TranscriptGoldSuite,
        evaluations: Sequence[TranscriptEvaluationResult],
        evaluation_manifest: GoldCustodyEvaluationManifestV1,
    ) -> None:
        expected_candidates = tuple(sorted(item.artifact_hash for item in candidates))
        evaluation_by_candidate = {item.candidate_artifact_hash: item for item in evaluations}
        if len(evaluation_by_candidate) != len(evaluations):
            raise ValueError("custody export has duplicate candidate evaluations")
        if (
            evaluation_manifest.workspace_id != manifest.workspace_id
            or evaluation_manifest.workspace_manifest_hash != manifest.manifest_hash
            or evaluation_manifest.phase_event_hashes
            != tuple(item.event_hash for item in events[:5])
            or evaluation_manifest.annotation_packet_hash != packet.packet_hash
            or evaluation_manifest.candidate_artifact_hashes != expected_candidates
            or evaluation_manifest.audio_only_submission_hashes
            != tuple(sorted(item.submission_hash for item in submissions))
            or evaluation_manifest.adjudication_record_hashes
            != tuple(sorted(item.record_hash for item in adjudications))
            or evaluation_manifest.gold_suite_hash != gold.suite_hash
            or tuple(sorted(evaluation_by_candidate)) != expected_candidates
        ):
            raise ValueError("custody evaluation manifest lineage mismatch")
        bindings = {item.candidate_artifact_hash: item for item in evaluation_manifest.evaluations}
        if set(bindings) != set(evaluation_by_candidate):
            raise ValueError("custody evaluation binding coverage mismatch")
        for candidate_hash, evaluation in evaluation_by_candidate.items():
            binding = bindings[candidate_hash]
            raw = evaluation.canonical_bytes()
            if (
                binding.evaluation_hash != evaluation.evaluation_hash
                or binding.evaluation_content_sha256 != sha256_bytes(raw)
                or binding.status is not evaluation.status
                or binding.correction_metrics_status is not evaluation.correction_metrics_status
                or evaluation.gold_suite_hash != gold.suite_hash
                or evaluation.normalized_audio_hash != packet.normalized_audio_hash
                or evaluation.annotation_packet_hash != packet.packet_hash
            ):
                raise ValueError("custodied evaluation binding mismatch")
        for candidate in candidates:
            stored = evaluation_by_candidate[candidate.artifact_hash]
            if stored.metrics is not None:
                measured_identity = measure_lexical_evaluator_identity()
                if stored.metrics.lexical_evaluator_identity != measured_identity:
                    raise ValueError(
                        "custodied lexical evaluator identity differs from current runtime"
                    )
            expected = _evaluate_with_signed_custody(
                gold,
                candidate,
                all_candidate_hashes=expected_candidates,
            )
            if evaluation_by_candidate[candidate.artifact_hash] != expected:
                raise ValueError(
                    "custodied evaluation differs from deterministic sealed-artifact replay"
                )

    def _verify_annotation_export(
        self,
        packet: TranscriptAnnotationPacket,
        normalized_audio: CustodyArtifactReferenceV1,
        clip_references: Sequence[CustodyArtifactReferenceV1],
        *,
        allow_temporary_artifacts: bool,
    ) -> None:
        directory = self.root / "exports" / "annotation"
        expected = directory / f"{packet.packet_hash}.annotation-packet.json"
        raw = _read_regular_file(expected, label="candidate-free annotation export")
        if raw != packet.canonical_bytes():
            raise ValueError("annotation export differs from the sealed candidate-free packet")
        expected_names = {expected.name}
        normalized_path = directory / f"normalized.{normalized_audio.content_sha256}.wav"
        if _read_regular_file(normalized_path, label="normalized audio export") != _read_object(
            self.root, normalized_audio
        ):
            raise ValueError("normalized audio export differs from sealed exact bytes")
        expected_names.add(normalized_path.name)
        references = {item.artifact_id: item for item in clip_references}
        for ordinal, clip in enumerate(packet.clips):
            reference = references[clip.binding_hash]
            clip_path = directory / (
                f"{ordinal:04d}.{clip.binding_hash}.{reference.content_sha256}.wav"
            )
            raw = _read_regular_file(clip_path, label="annotation audio export")
            if len(raw) != clip.clip_audio_size_bytes or sha256_bytes(raw) != clip.clip_audio_hash:
                raise ValueError("annotation audio export differs from packet clip binding")
            expected_names.add(clip_path.name)
        observed_names = {item.name for item in directory.iterdir()}
        if allow_temporary_artifacts:
            observed_names = {
                name for name in observed_names if not name.startswith(".custody-")
            }
        if observed_names != expected_names:
            raise ValueError("annotation export contains an extra artifact")

    def _verify_evaluation_exports(
        self,
        evaluations: Sequence[TranscriptEvaluationResult],
        manifest: GoldCustodyEvaluationManifestV1,
        *,
        allow_temporary_artifacts: bool,
    ) -> None:
        directory = self.root / "exports" / "evaluation"
        expected: dict[str, bytes] = {
            f"custody-evaluation-manifest.{manifest.evaluation_manifest_hash}.json": (
                manifest.canonical_bytes()
            )
        }
        binding_by_evaluation = {
            item.evaluation_hash: item for item in manifest.evaluations
        }
        for evaluation in evaluations:
            binding = binding_by_evaluation[evaluation.evaluation_hash]
            expected[
                f"{binding.candidate_artifact_hash}.transcript-evaluation.json"
            ] = evaluation.canonical_bytes()
        observed_names = {item.name for item in directory.iterdir()}
        if allow_temporary_artifacts:
            observed_names = {
                name for name in observed_names if not name.startswith(".custody-")
            }
        if observed_names != set(expected):
            raise ValueError("evaluation export contains a missing or extra artifact")
        for name, raw in expected.items():
            if _read_regular_file(directory / name, label="evaluation export") != raw:
                raise ValueError("evaluation export bytes differ from signed custody objects")

    def _existing_phase_event(
        self,
        state: VerifiedGoldCustodyStateV1,
        phase: CustodyPhase,
        artifacts: Sequence[CustodyArtifactReferenceV1],
    ) -> GoldCustodyEventV1 | None:
        index = _PHASES.index(phase)
        expected = tuple(sorted(artifacts, key=_artifact_ref_key))
        if state.events[-1].event_hash != self._expected_head_hash:
            successor = state.events[-1]
            if successor.phase is not phase or successor.artifacts != expected:
                raise ValueError(
                    "custody recovery successor differs from requested exact phase"
                )
            self._expected_head_hash = successor.event_hash
            self._allow_one_successor_recovery = False
            return successor
        if len(state.events) > index:
            event = state.events[index]
            if event.artifacts != expected:
                raise ValueError("custody phase replay conflicts with immutable artifacts")
            return event
        if len(state.events) != index:
            raise ValueError("custody phase command is out of order")
        return None

    def _append_phase(
        self,
        *,
        state: VerifiedGoldCustodyStateV1,
        phase: CustodyPhase,
        artifacts: Sequence[CustodyArtifactReferenceV1],
        signing_key_path: Path,
    ) -> GoldCustodyEventV1:
        signing_key_path = _require_external_key(
            self.root,
            signing_key_path,
            label="trusted signing key",
        )
        event = GoldCustodyEventV1.build(
            manifest=state.manifest,
            phase=phase,
            previous_event_hash=state.events[-1].event_hash,
            artifacts=artifacts,
        )
        signature = _sign_payload(
            event.canonical_bytes(),
            signing_key_path=signing_key_path,
            public_key=self._public_key,
            signer_id=state.manifest.signer_id,
        )
        _commit_event_directory(self.root, event, signature)
        self._expected_head_hash = event.event_hash
        return event

    def _command_result(
        self,
        state: VerifiedGoldCustodyStateV1,
        event: GoldCustodyEventV1,
        *,
        replayed: bool,
        evaluation_status: Literal[
            "not_applicable", "evaluated", "not_evaluated", "mixed"
        ],
    ) -> CustodyCommandResultV1:
        return CustodyCommandResultV1(
            workspace_id=state.manifest.workspace_id,
            workspace_manifest_hash=state.manifest.manifest_hash,
            phase=event.phase,
            phase_event_hash=event.event_hash,
            current_head_hash=state.events[-1].event_hash,
            event_count=len(state.events),
            replay_status="replayed" if replayed else "committed",
            evaluation_status=evaluation_status,
            artifact_ids=tuple(item.artifact_id for item in event.artifacts),
        )

    def seal_packets(
        self,
        packet_paths: Sequence[Path],
        normalized_audio_path: Path,
        clip_audio_paths: Sequence[Path],
        *,
        signing_key_path: Path,
    ) -> CustodyCommandResultV1:
        if len(packet_paths) != 1:
            raise ValueError("this custody workspace requires exactly one annotation packet")
        raw = _read_regular_file(Path(packet_paths[0]), label="annotation packet input")
        packet = load_annotation_packet(raw)
        packet_reference = _make_reference(
            role="annotation_packet",
            artifact_id=packet.packet_hash,
            raw=raw,
        )
        normalized_raw = _read_regular_file(
            Path(normalized_audio_path), label="normalized audio input"
        )
        if (
            sha256_bytes(normalized_raw) != packet.normalized_audio_hash
            or len(normalized_raw) != packet.normalized_audio_size_bytes
        ):
            raise ValueError("normalized audio bytes differ from packet lineage")
        normalized_reference = _make_reference(
            role="normalized_audio",
            artifact_id=packet.normalized_audio_hash,
            raw=normalized_raw,
        )
        if len(clip_audio_paths) != len(packet.clips):
            raise ValueError("seal-packets requires clips in exact packet order")
        loaded_clips: list[tuple[Path, bytes, CustodyArtifactReferenceV1]] = []
        for clip, path in zip(packet.clips, clip_audio_paths, strict=True):
            clip_raw = _read_regular_file(Path(path), label="annotation audio clip input")
            derived = _extract_canonical_pcm_wav(
                normalized_raw,
                start_ms=clip.start_ms,
                end_ms=clip.end_ms,
            )
            if (
                clip_raw != derived
                or sha256_bytes(clip_raw) != clip.clip_audio_hash
                or len(clip_raw) != clip.clip_audio_size_bytes
            ):
                raise ValueError("annotation audio clip bytes differ from packet binding")
            loaded_clips.append(
                (
                    Path(path),
                    clip_raw,
                    _make_reference(
                        role="annotation_audio_clip",
                        artifact_id=clip.binding_hash,
                        raw=clip_raw,
                    ),
                )
            )
        references = (
            packet_reference,
            normalized_reference,
            *(item[2] for item in loaded_clips),
        )
        with self._lock():
            state, artifacts = self._read_verified(allow_uncommitted_artifacts=True)
            existing = self._existing_phase_event(
                state,
                CustodyPhase.PACKETS_SEALED,
                references,
            )
            self._assert_recovery_objects(artifacts, references)
            _put_object(self.root, packet_reference, raw)
            _put_object(self.root, normalized_reference, normalized_raw)
            for _, clip_raw, reference in loaded_clips:
                _put_object(self.root, reference, clip_raw)
            _atomic_write_immutable(
                self.root
                / "exports"
                / "annotation"
                / f"{packet.packet_hash}.annotation-packet.json",
                raw,
                root=self.root,
            )
            _atomic_write_immutable(
                self.root
                / "exports"
                / "annotation"
                / f"normalized.{normalized_reference.content_sha256}.wav",
                normalized_raw,
                root=self.root,
            )
            for ordinal, (_, clip_raw, reference) in enumerate(loaded_clips):
                _atomic_write_immutable(
                    self.root
                    / "exports"
                    / "annotation"
                    / (
                        f"{ordinal:04d}.{reference.artifact_id}."
                        f"{reference.content_sha256}.wav"
                    ),
                    clip_raw,
                    root=self.root,
                )
            event = existing or self._append_phase(
                state=state,
                phase=CustodyPhase.PACKETS_SEALED,
                artifacts=references,
                signing_key_path=signing_key_path,
            )
            verified, _ = self._read_verified()
            return self._command_result(
                verified,
                event,
                replayed=existing is not None,
                evaluation_status="not_applicable",
            )

    def seal_candidates(
        self,
        candidate_paths: Sequence[Path],
        *,
        signing_key_path: Path,
    ) -> CustodyCommandResultV1:
        if not candidate_paths:
            raise ValueError("candidate seal requires at least one candidate")
        loaded: list[tuple[TranscriptCandidateArtifact, bytes, CustodyArtifactReferenceV1]] = []
        for path in candidate_paths:
            raw = _read_regular_file(Path(path), label="candidate input")
            candidate = load_transcript_candidate(raw)
            loaded.append(
                (
                    candidate,
                    raw,
                    _make_reference(
                        role="transcript_candidate",
                        artifact_id=candidate.artifact_hash,
                        raw=raw,
                    ),
                )
            )
        loaded.sort(key=lambda item: item[0].artifact_hash)
        if len({item[0].artifact_hash for item in loaded}) != len(loaded):
            raise ValueError("candidate seal contains duplicate artifacts")
        references = tuple(item[2] for item in loaded)
        with self._lock():
            state, artifacts = self._read_verified(allow_uncommitted_artifacts=True)
            if artifacts.packet is None:
                raise ValueError("candidate seal requires a sealed annotation packet")
            for candidate, _, _ in loaded:
                _validate_candidate_bindings(artifacts.packet, candidate)
            verify_annotation_packet_blinding(
                artifacts.packet,
                tuple(item[0].artifact_hash for item in loaded),
            )
            existing = self._existing_phase_event(
                state,
                CustodyPhase.CANDIDATES_SEALED,
                references,
            )
            self._assert_recovery_objects(artifacts, references)
            for _, raw, reference in loaded:
                _put_object(self.root, reference, raw)
            event = existing or self._append_phase(
                state=state,
                phase=CustodyPhase.CANDIDATES_SEALED,
                artifacts=references,
                signing_key_path=signing_key_path,
            )
            verified, _ = self._read_verified()
            return self._command_result(
                verified,
                event,
                replayed=existing is not None,
                evaluation_status="not_applicable",
            )

    def import_submissions(
        self,
        submission_paths: Sequence[Path],
        adjudication_paths: Sequence[Path],
        *,
        signing_key_path: Path,
    ) -> CustodyCommandResultV1:
        submissions: list[
            tuple[AudioOnlyTranscriptSubmission, bytes, CustodyArtifactReferenceV1]
        ] = []
        for path in submission_paths:
            raw = _read_regular_file(Path(path), label="audio-only submission input")
            value = _load_submission(raw)
            submissions.append(
                (
                    value,
                    raw,
                    _make_reference(
                        role="audio_only_submission",
                        artifact_id=value.submission_hash,
                        raw=raw,
                    ),
                )
            )
        adjudications: list[
            tuple[TranscriptAdjudicationRecord, bytes, CustodyArtifactReferenceV1]
        ] = []
        for path in adjudication_paths:
            raw = _read_regular_file(Path(path), label="adjudication input")
            value = _load_adjudication(raw)
            adjudications.append(
                (
                    value,
                    raw,
                    _make_reference(
                        role="transcript_adjudication",
                        artifact_id=value.record_hash,
                        raw=raw,
                    ),
                )
            )
        submissions.sort(key=lambda item: item[0].submission_hash)
        adjudications.sort(key=lambda item: item[0].record_hash)
        references = tuple(item[2] for item in (*submissions, *adjudications))
        with self._lock():
            state, artifacts = self._read_verified(allow_uncommitted_artifacts=True)
            if artifacts.packet is None:
                raise ValueError("submission import requires a sealed packet")
            _validate_submission_lineage(
                artifacts.packet,
                tuple(item[0] for item in submissions),
                tuple(item[0] for item in adjudications),
            )
            existing = self._existing_phase_event(
                state,
                CustodyPhase.SUBMISSIONS_IMPORTED,
                references,
            )
            self._assert_recovery_objects(artifacts, references)
            for _, raw, reference in (*submissions, *adjudications):
                _put_object(self.root, reference, raw)
            event = existing or self._append_phase(
                state=state,
                phase=CustodyPhase.SUBMISSIONS_IMPORTED,
                artifacts=references,
                signing_key_path=signing_key_path,
            )
            verified, _ = self._read_verified()
            return self._command_result(
                verified,
                event,
                replayed=existing is not None,
                evaluation_status="not_applicable",
            )

    def seal_gold(
        self,
        gold_paths: Sequence[Path],
        *,
        signing_key_path: Path,
    ) -> CustodyCommandResultV1:
        if len(gold_paths) != 1:
            raise ValueError("this custody workspace requires exactly one gold suite")
        raw = _read_regular_file(Path(gold_paths[0]), label="gold suite input")
        gold = load_transcript_gold_suite(raw)
        reference = _make_reference(
            role="transcript_gold_suite",
            artifact_id=gold.suite_hash,
            raw=raw,
        )
        with self._lock():
            state, artifacts = self._read_verified(allow_uncommitted_artifacts=True)
            if artifacts.packet is None:
                raise ValueError("gold seal requires a sealed packet")
            _validate_gold_lineage(
                packet=artifacts.packet,
                candidates=artifacts.candidates,
                submissions=artifacts.submissions,
                adjudications=artifacts.adjudications,
                gold=gold,
            )
            existing = self._existing_phase_event(
                state,
                CustodyPhase.GOLD_SEALED,
                (reference,),
            )
            self._assert_recovery_objects(artifacts, (reference,))
            _put_object(self.root, reference, raw)
            event = existing or self._append_phase(
                state=state,
                phase=CustodyPhase.GOLD_SEALED,
                artifacts=(reference,),
                signing_key_path=signing_key_path,
            )
            verified, _ = self._read_verified()
            return self._command_result(
                verified,
                event,
                replayed=existing is not None,
                evaluation_status="not_applicable",
            )

    def export_evaluation(
        self,
        *,
        signing_key_path: Path,
    ) -> CustodyCommandResultV1:
        with self._lock():
            state, artifacts = self._read_verified(allow_uncommitted_artifacts=True)
            if artifacts.evaluation_manifest is not None:
                event = state.events[_PHASES.index(CustodyPhase.EVALUATION_EXPORTED)]
                self._existing_phase_event(
                    state,
                    CustodyPhase.EVALUATION_EXPORTED,
                    event.artifacts,
                )
                manifest = artifacts.evaluation_manifest
                export_root = self.root / "exports" / "evaluation"
                _atomic_write_immutable(
                    export_root
                    / (
                        "custody-evaluation-manifest."
                        f"{manifest.evaluation_manifest_hash}.json"
                    ),
                    manifest.canonical_bytes(),
                    root=self.root,
                )
                binding_by_hash = {
                    item.evaluation_hash: item for item in manifest.evaluations
                }
                for evaluation in artifacts.evaluations:
                    binding = binding_by_hash[evaluation.evaluation_hash]
                    _atomic_write_immutable(
                        export_root
                        / (
                            f"{binding.candidate_artifact_hash}."
                            "transcript-evaluation.json"
                        ),
                        evaluation.canonical_bytes(),
                        root=self.root,
                    )
                verified, _ = self._read_verified()
                return self._command_result(
                    verified,
                    event,
                    replayed=True,
                    evaluation_status=manifest.status.value,
                )
            if artifacts.packet is None or artifacts.gold is None:
                raise ValueError("evaluation export requires sealed packet, candidates, and gold")
            if len(state.events) != _PHASES.index(CustodyPhase.EVALUATION_EXPORTED):
                raise ValueError("evaluation export command is out of order")
            candidate_hashes = tuple(
                sorted(item.artifact_hash for item in artifacts.candidates)
            )
            evaluations = tuple(
                _evaluate_with_signed_custody(
                    artifacts.gold,
                    candidate,
                    all_candidate_hashes=candidate_hashes,
                )
                for candidate in sorted(
                    artifacts.candidates,
                    key=lambda item: item.artifact_hash,
                )
            )
            evaluation_raw = tuple(item.canonical_bytes() for item in evaluations)
            bindings = tuple(
                CustodyEvaluationBindingV1(
                    candidate_artifact_hash=candidate_hash,
                    evaluation_hash=evaluation.evaluation_hash,
                    evaluation_content_sha256=sha256_bytes(raw),
                    status=evaluation.status,
                    correction_metrics_status=evaluation.correction_metrics_status,
                )
                for candidate_hash, evaluation, raw in zip(
                    candidate_hashes,
                    evaluations,
                    evaluation_raw,
                    strict=True,
                )
            )
            evaluation_manifest = GoldCustodyEvaluationManifestV1.build(
                workspace=state.manifest,
                phase_event_hashes=tuple(item.event_hash for item in state.events),
                annotation_packet_hash=artifacts.packet.packet_hash,
                candidate_artifact_hashes=candidate_hashes,
                audio_only_submission_hashes=tuple(
                    item.submission_hash for item in artifacts.submissions
                ),
                adjudication_record_hashes=tuple(
                    item.record_hash for item in artifacts.adjudications
                ),
                gold_suite_hash=artifacts.gold.suite_hash,
                evaluations=bindings,
            )
            manifest_raw = evaluation_manifest.canonical_bytes()
            references = [
                _make_reference(
                    role="custody_evaluation_manifest",
                    artifact_id=evaluation_manifest.evaluation_manifest_hash,
                    raw=manifest_raw,
                )
            ]
            references.extend(
                _make_reference(
                    role="transcript_evaluation",
                    artifact_id=evaluation.evaluation_hash,
                    raw=raw,
                )
                for evaluation, raw in zip(evaluations, evaluation_raw, strict=True)
            )
            self._assert_recovery_objects(artifacts, references)
            for reference, raw in zip(
                references,
                (manifest_raw, *evaluation_raw),
                strict=True,
            ):
                _put_object(self.root, reference, raw)
            export_root = self.root / "exports" / "evaluation"
            _atomic_write_immutable(
                export_root
                / (
                    "custody-evaluation-manifest."
                    f"{evaluation_manifest.evaluation_manifest_hash}.json"
                ),
                manifest_raw,
                root=self.root,
            )
            for binding, raw in zip(bindings, evaluation_raw, strict=True):
                _atomic_write_immutable(
                    export_root
                    / f"{binding.candidate_artifact_hash}.transcript-evaluation.json",
                    raw,
                    root=self.root,
                )
            event = self._append_phase(
                state=state,
                phase=CustodyPhase.EVALUATION_EXPORTED,
                artifacts=references,
                signing_key_path=signing_key_path,
            )
            verified, _ = self._read_verified()
            return self._command_result(
                verified,
                event,
                replayed=False,
                evaluation_status=evaluation_manifest.status.value,
            )


__all__ = [
    "CustodyArtifactReferenceV1",
    "CustodyCommandResultV1",
    "CustodyEvaluationBindingV1",
    "CustodyEvaluationStatus",
    "CustodyPhase",
    "GoldCustodyEvaluationManifestV1",
    "GoldCustodyEventV1",
    "GoldCustodyWorkspace",
    "GoldCustodyWorkspaceManifestV1",
    "VerifiedGoldCustodyStateV1",
    "load_gold_custody_evaluation_manifest",
    "load_gold_custody_event",
    "load_gold_custody_workspace_manifest",
]
