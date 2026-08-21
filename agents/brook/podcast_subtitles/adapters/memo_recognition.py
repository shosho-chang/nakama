"""Strict import adapter for accepted Memo large-v2 recognition evidence.

Memo is an external desktop application.  V2 therefore does not pretend that
it launched or measured Memo's private runtime.  Instead it imports one exact,
operator-accepted recognition manifest and makes those bytes the replay
boundary.  Subtitle cue authority is deliberately a separate artifact; raw
Memo micro-segments never become display boundaries by accident.
"""

from __future__ import annotations

import json
import platform
from datetime import datetime
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

from shared.schemas.podcast_subtitles_v2 import ArtifactDigest, EvidenceToken, RecognitionEvidence

from ..hashing import canonical_json_bytes, hash_file, hash_object, sha256_bytes
from ..ports import (
    AdapterInputError,
    AdapterIntegrityError,
    RecognitionModelIdentity,
    RecognitionRequest,
)


class _StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)


class MemoRecognitionTokenV1(_StrictModel):
    id: str
    text: str
    start_ms: int = Field(ge=0)
    end_ms: int = Field(gt=0)
    confidence: float | None = Field(default=None, ge=0.0, le=1.0)
    speaker: str | None = None

    @model_validator(mode="after")
    def _valid(self) -> "MemoRecognitionTokenV1":
        if not self.id.strip() or not self.text.strip():
            raise ValueError("Memo recognition token id/text must not be blank")
        if self.end_ms <= self.start_ms:
            raise ValueError("Memo recognition token range must be positive")
        return self


class MemoExecutionReceiptRefV1(_StrictModel):
    """Episode-local reference plus the execution identities needed downstream."""

    contract: Literal["memo-bundled-runner-execution-v1"] = (
        "memo-bundled-runner-execution-v1"
    )
    path: str
    runner_path: str
    model_path: str
    input_wav_path: str
    output_srt_path: str
    stdout_path: str
    stderr_path: str
    sha256: str
    size_bytes: int = Field(gt=0)
    runner_sha256: str
    model_sha256: str
    input_wav_sha256: str
    output_srt_sha256: str
    stdout_sha256: str
    stderr_sha256: str
    exit_code: Literal[0] = 0

    @model_validator(mode="after")
    def _valid(self) -> "MemoExecutionReceiptRefV1":
        episode_paths = tuple(
            Path(value)
            for value in (
                self.path,
                self.input_wav_path,
                self.output_srt_path,
                self.stdout_path,
                self.stderr_path,
            )
        )
        digests = (
            self.sha256,
            self.runner_sha256,
            self.model_sha256,
            self.input_wav_sha256,
            self.output_srt_sha256,
            self.stdout_sha256,
            self.stderr_sha256,
        )
        if (
            any(
                candidate.is_absolute()
                or candidate.drive
                or ".." in candidate.parts
                for candidate in episode_paths
            )
            or not Path(self.runner_path).is_absolute()
            or not Path(self.model_path).is_absolute()
            or any(
                len(value) != 64
                or any(char not in "0123456789abcdef" for char in value)
                for value in digests
            )
        ):
            raise ValueError("Memo execution receipt reference is invalid")
        return self


class MemoRecognitionManifestV1(_StrictModel):
    """Canonical import envelope derived from one accepted Memo raw export."""

    schema_version: Literal[1] = 1
    contract: Literal["memo-recognition-evidence-v1"] = "memo-recognition-evidence-v1"
    memo_version: str
    model: Literal["ggml-large-v2.bin"] = "ggml-large-v2.bin"
    language: str
    prompt: str
    normalized_audio_sha256: str
    normalized_audio_size_bytes: int = Field(gt=0)
    normalized_audio_duration_ms: int = Field(gt=0)
    source_export_sha256: str
    source_export_size_bytes: int = Field(gt=0)
    source_export_kind: Literal["memo_stdout", "memo_srt", "memo_json"]
    raw_source_export_sha256: str | None = None
    raw_source_export_size_bytes: int | None = Field(default=None, gt=0)
    source_repair_receipt_sha256: str | None = None
    memo_execution_receipt: MemoExecutionReceiptRefV1 | None = None
    accepted_by_receipt_sha256: str
    unresolved_findings: tuple[str, ...] = ()
    tokens: tuple[MemoRecognitionTokenV1, ...]

    @model_validator(mode="after")
    def _complete(self) -> "MemoRecognitionManifestV1":
        for label, value in (
            ("memo_version", self.memo_version),
            ("language", self.language),
            ("prompt", self.prompt),
        ):
            if not value.strip() or value != value.strip():
                raise ValueError(f"Memo {label} must be non-blank and trimmed")
        for label, value in (
            ("normalized_audio_sha256", self.normalized_audio_sha256),
            ("source_export_sha256", self.source_export_sha256),
            ("accepted_by_receipt_sha256", self.accepted_by_receipt_sha256),
        ):
            if len(value) != 64 or any(char not in "0123456789abcdef" for char in value):
                raise ValueError(f"Memo {label} must be lowercase SHA-256")
        repair_lineage = (
            self.raw_source_export_sha256,
            self.raw_source_export_size_bytes,
            self.source_repair_receipt_sha256,
        )
        if any(value is not None for value in repair_lineage) != all(
            value is not None for value in repair_lineage
        ):
            raise ValueError("Memo recognition source repair lineage must be complete")
        for value in (self.raw_source_export_sha256, self.source_repair_receipt_sha256):
            if value is not None and (
                len(value) != 64 or any(char not in "0123456789abcdef" for char in value)
            ):
                raise ValueError("Memo recognition source repair digest must be lowercase SHA-256")
        if self.unresolved_findings:
            raise ValueError("accepted Memo recognition cannot contain unresolved findings")
        if not self.tokens:
            raise ValueError("Memo recognition requires at least one token")
        ids = tuple(token.id for token in self.tokens)
        if len(set(ids)) != len(ids):
            raise ValueError("Memo recognition token IDs must be unique")
        previous_end = -1
        for token in self.tokens:
            if token.start_ms < previous_end:
                raise ValueError("Memo recognition tokens must be ordered and non-overlapping")
            if token.end_ms > self.normalized_audio_duration_ms:
                raise ValueError("Memo recognition token exceeds normalized audio")
            previous_end = token.end_ms
        return self


class MemoAgentAuditRefV1(_StrictModel):
    """Episode-local immutable reference to one independent worker audit."""

    contract: Literal[
        "memo-recognition-worker-audit-v1",
        "memo-cue-worker-audit-v1",
    ]
    worker_id: str
    path: str
    sha256: str
    size_bytes: int = Field(gt=0)

    @model_validator(mode="after")
    def _valid(self) -> "MemoAgentAuditRefV1":
        candidate = Path(self.path)
        if (
            not self.worker_id.strip()
            or self.worker_id != self.worker_id.strip()
            or candidate.is_absolute()
            or candidate.drive
            or ".." in candidate.parts
            or len(self.sha256) != 64
            or any(char not in "0123456789abcdef" for char in self.sha256)
        ):
            raise ValueError("Memo worker audit reference is invalid")
        return self


class MemoRecognitionAcceptanceReceiptV1(_StrictModel):
    """Acceptance of one prepared Memo recognition review, including agent quorum."""

    schema_version: Literal[1] = 1
    contract: Literal["accepted-memo-recognition-v1"] = "accepted-memo-recognition-v1"
    accepted: Literal[True] = True
    normalized_audio_sha256: str
    normalized_audio_size_bytes: int = Field(gt=0)
    normalized_audio_duration_ms: int = Field(gt=0)
    normalized_handoff_manifest_sha256: str
    source_export_sha256: str
    source_export_size_bytes: int = Field(gt=0)
    raw_source_export_sha256: str | None = None
    raw_source_export_size_bytes: int | None = Field(default=None, gt=0)
    source_repair_receipt_sha256: str | None = None
    memo_execution_receipt: MemoExecutionReceiptRefV1 | None = None
    review_manifest_sha256: str
    token_export_sha256: str
    reviewer: str
    accepted_at: datetime
    unresolved_findings: tuple[str, ...] = ()
    episode_id: str | None = None
    agent_audits: tuple[MemoAgentAuditRefV1, ...] = ()

    @model_validator(mode="after")
    def _accepted(self) -> "MemoRecognitionAcceptanceReceiptV1":
        for label, value in (
            ("normalized audio", self.normalized_audio_sha256),
            ("normalized handoff", self.normalized_handoff_manifest_sha256),
            ("source export", self.source_export_sha256),
            ("review manifest", self.review_manifest_sha256),
            ("token export", self.token_export_sha256),
        ):
            if len(value) != 64 or any(char not in "0123456789abcdef" for char in value):
                raise ValueError(f"Memo recognition {label} digest must be lowercase SHA-256")
        repair_lineage = (
            self.raw_source_export_sha256,
            self.raw_source_export_size_bytes,
            self.source_repair_receipt_sha256,
        )
        if any(value is not None for value in repair_lineage) != all(
            value is not None for value in repair_lineage
        ):
            raise ValueError("Memo recognition source repair lineage must be complete")
        for value in (self.raw_source_export_sha256, self.source_repair_receipt_sha256):
            if value is not None and (
                len(value) != 64 or any(char not in "0123456789abcdef" for char in value)
            ):
                raise ValueError("Memo recognition source repair digest must be lowercase SHA-256")
        if (
            not self.reviewer.strip()
            or self.reviewer != self.reviewer.strip()
            or self.accepted_at.tzinfo is None
            or self.unresolved_findings
        ):
            raise ValueError(
                "Memo recognition acceptance must be explicit, identified, and resolved"
            )
        if bool(self.episode_id) != bool(self.agent_audits):
            raise ValueError("Memo recognition agent-quorum lineage must be complete")
        if self.agent_audits and (
            self.reviewer != "agent-quorum"
            or len(self.agent_audits) != 2
            or len({audit.worker_id for audit in self.agent_audits}) != 2
            or any(
                audit.contract != "memo-recognition-worker-audit-v1"
                for audit in self.agent_audits
            )
        ):
            raise ValueError("Memo recognition agent quorum requires two independent audits")
        return self


def _reject_duplicate_keys(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"duplicate JSON key: {key}")
        result[key] = value
    return result


def load_memo_recognition_manifest(path: str | Path) -> tuple[MemoRecognitionManifestV1, bytes]:
    manifest_path = Path(path)
    try:
        payload = manifest_path.read_bytes()
        json.loads(payload, object_pairs_hook=_reject_duplicate_keys)
        manifest = MemoRecognitionManifestV1.model_validate_json(payload, strict=True)
    except (OSError, UnicodeDecodeError, json.JSONDecodeError, ValidationError, ValueError) as exc:
        raise AdapterInputError(f"invalid Memo recognition manifest: {exc}") from exc
    if canonical_json_bytes(manifest) != payload:
        raise AdapterIntegrityError("Memo recognition manifest must use canonical JSON bytes")
    return manifest, payload


class MemoRecognizerAdapter:
    """Import one immutable Memo large-v2 recognition stream as primary Evidence."""

    ADAPTER_NAME = "memo-large-v2-recognition-import"
    ADAPTER_VERSION = "1"

    def __init__(
        self,
        manifest_path: str | Path,
        *,
        source_export: str | Path,
        acceptance_receipt: str | Path,
    ) -> None:
        self._manifest_path = Path(manifest_path)
        self._source_export = Path(source_export)
        self._acceptance_receipt = Path(acceptance_receipt)

    def _verified_manifest(self) -> tuple[MemoRecognitionManifestV1, bytes]:
        manifest, payload = load_memo_recognition_manifest(self._manifest_path)
        if not self._source_export.is_file() or not self._acceptance_receipt.is_file():
            raise AdapterInputError(
                "Memo source export and acceptance receipt must be readable files"
            )
        if (
            hash_file(self._source_export) != manifest.source_export_sha256
            or self._source_export.stat().st_size != manifest.source_export_size_bytes
        ):
            raise AdapterIntegrityError("Memo source export differs from recognition manifest")
        acceptance_bytes = self._acceptance_receipt.read_bytes()
        if sha256_bytes(acceptance_bytes) != manifest.accepted_by_receipt_sha256:
            raise AdapterIntegrityError("Memo acceptance receipt differs from recognition manifest")
        if manifest.memo_execution_receipt is not None:
            try:
                json.loads(acceptance_bytes, object_pairs_hook=_reject_duplicate_keys)
                acceptance = MemoRecognitionAcceptanceReceiptV1.model_validate_json(
                    acceptance_bytes, strict=True
                )
            except (json.JSONDecodeError, ValidationError, ValueError) as exc:
                raise AdapterInputError(
                    f"invalid Memo recognition acceptance: {exc}"
                ) from exc
            if canonical_json_bytes(acceptance) != acceptance_bytes:
                raise AdapterIntegrityError(
                    "Memo recognition acceptance must use canonical JSON bytes"
                )
            if acceptance.memo_execution_receipt != manifest.memo_execution_receipt:
                raise AdapterIntegrityError(
                    "Memo execution receipt differs between acceptance and recognition manifest"
                )
        return manifest, payload

    @property
    def identity(self) -> RecognitionModelIdentity:
        manifest, payload = self._verified_manifest()
        components = tuple(
            sorted(
                (
                    ("import_contract", manifest.contract),
                    ("manifest_sha256", sha256_bytes(payload)),
                    ("memo_version_claim", manifest.memo_version),
                    ("python_implementation", platform.python_implementation()),
                    ("python_version", platform.python_version()),
                    ("source_export_sha256", manifest.source_export_sha256),
                )
            )
        )
        return RecognitionModelIdentity(
            adapter_name=self.ADAPTER_NAME,
            adapter_version=self.ADAPTER_VERSION,
            model=manifest.model,
            model_version=f"memo-{manifest.memo_version}",
            aligner="memo-export-timestamps",
            aligner_version=manifest.memo_version,
            runtime_components=components,
            runtime_hash=hash_object({"runtime_components": components}),
            adapter_code_hash=hash_file(Path(__file__)),
            config_hash=hash_object({"manifest_sha256": sha256_bytes(payload)}),
            execution_mode="import",
        )

    def _evidence(self, request: RecognitionRequest) -> RecognitionEvidence:
        manifest, payload = self._verified_manifest()
        audio = Path(request.normalized_audio)
        if not audio.is_file():
            raise AdapterInputError(f"normalized audio is not a file: {audio}")
        actual_hash = hash_file(audio)
        actual_size = audio.stat().st_size
        if request.expected_normalized_audio_hash not in {None, actual_hash}:
            raise AdapterIntegrityError("Memo request normalized-audio hash mismatch")
        if (actual_hash, actual_size) != (
            manifest.normalized_audio_sha256,
            manifest.normalized_audio_size_bytes,
        ):
            raise AdapterIntegrityError("Memo manifest belongs to different normalized audio")
        digest = sha256_bytes(payload)
        raw = ArtifactDigest(
            uri=self._manifest_path.resolve().as_uri(),
            sha256=digest,
            size_bytes=len(payload),
        )
        return RecognitionEvidence(
            episode_id=request.episode_id,
            invocation_id=request.invocation_id,
            adapter=self.ADAPTER_NAME,
            model=manifest.model,
            language=manifest.language,
            config_hash=self.identity.config_hash,
            raw_output=raw,
            raw_output_hash=digest,
            normalized_audio_hash=actual_hash,
            tokens=tuple(
                EvidenceToken(
                    id=token.id,
                    text=token.text,
                    start_ms=token.start_ms,
                    end_ms=token.end_ms,
                    confidence=token.confidence,
                    speaker=token.speaker,
                    evidence_refs=(
                        f"memo-manifest:{digest}",
                        f"memo-source-token:{token.id}",
                    ),
                )
                for token in manifest.tokens
            ),
        )

    def recognize(self, request: RecognitionRequest) -> RecognitionEvidence:
        return self._evidence(request)

    def verify(
        self,
        evidence: RecognitionEvidence,
        *,
        request: RecognitionRequest,
        raw_output: bytes,
    ) -> RecognitionEvidence:
        manifest, payload = self._verified_manifest()
        if raw_output != payload or sha256_bytes(raw_output) != evidence.raw_output.sha256:
            raise AdapterIntegrityError("Memo recognition raw bytes do not replay")
        expected = self._evidence(request).model_copy(
            update={
                "raw_output": self._evidence(request).raw_output.model_copy(
                    update={"uri": evidence.raw_output.uri}
                )
            }
        )
        if evidence != expected:
            raise AdapterIntegrityError("Memo Recognition Evidence does not replay")
        if manifest.unresolved_findings:
            raise AdapterIntegrityError("Memo Recognition Evidence remains unresolved")
        return evidence


__all__ = [
    "MemoExecutionReceiptRefV1",
    "MemoAgentAuditRefV1",
    "MemoRecognitionAcceptanceReceiptV1",
    "MemoRecognitionManifestV1",
    "MemoRecognitionTokenV1",
    "MemoRecognizerAdapter",
    "load_memo_recognition_manifest",
]
