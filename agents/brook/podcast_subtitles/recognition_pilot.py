"""Offline, resumable exact-clip Recognition pilot custody.

The pilot is intentionally smaller than a production Generation.  It binds a
formal annotation packet to exact local clips, snapshots provider bytes into
generation-owned content-addressed storage, and materializes Candidate V3.
Existing state is immutable: replay either verifies it byte-for-byte or fails.
"""

from __future__ import annotations

import os
import re
import tempfile
from dataclasses import asdict, is_dataclass
from pathlib import Path
from typing import Any, ClassVar, Literal, Protocol, Sequence
from urllib.parse import unquote, urlparse
from urllib.request import url2pathname

from pydantic import BaseModel, ConfigDict, field_validator, model_validator

from shared.schemas.podcast_subtitles_v2 import ArtifactDigest, RecognitionEvidence

from .candidate_materialization import (
    SourceBoundTranscriptCandidateArtifactV3,
    build_source_bound_transcript_candidate,
    verify_source_bound_transcript_candidate,
)
from .hashing import canonical_json_bytes, hash_object, measure_regular_file, sha256_bytes
from .ports import NO_LEXICAL_BIAS_CONTEXT, RecognitionModelIdentity, RecognitionRequest
from .recognition_request import (
    RecognitionRequestArtifactV1,
    build_recognition_request_artifact,
    materialize_recognition_request,
)
from .transcript_gold import AudioClipBinding, TranscriptAnnotationPacket, load_annotation_packet

QWEN_MODEL_REVISION = "7278e1e70fe206f11671096ffdd38061171dd6e5"
QWEN_ALIGNER_REVISION = "c7cbfc2048c462b0d63a45797104fc9db3ad62b7"
FASTER_MODEL_REVISION = "edaa852ec7e145841d8ffdb056a99866b5f0a478"
PILOT_SYSTEMS = ("qwen-primary", "faster-corroboration", "legacy-v1-whisperx")
_REVISION_RE = re.compile(r"^[0-9a-f]{40}$")
_CLIP_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")
_MANIFEST_NAME = "recognition-pilot-manifest.v1.json"


class _Recognizer(Protocol):
    @property
    def identity(self) -> RecognitionModelIdentity: ...

    def recognize(self, request: RecognitionRequest) -> RecognitionEvidence: ...

    def verify(
        self,
        evidence: RecognitionEvidence,
        *,
        request: RecognitionRequest,
        raw_output: bytes,
    ) -> RecognitionEvidence: ...


class _StrictFrozenModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)


def _sha(value: str, label: str = "hash") -> str:
    if len(value) != 64 or any(character not in "0123456789abcdef" for character in value):
        raise ValueError(f"{label} must be lowercase SHA-256")
    return value


class RecognitionPilotFileV1(_StrictFrozenModel):
    relative_path: str
    sha256: str
    size_bytes: int

    @field_validator("relative_path")
    @classmethod
    def _safe_path(cls, value: str) -> str:
        candidate = Path(value)
        if not value or "\\" in value or candidate.is_absolute() or ".." in candidate.parts:
            raise ValueError("pilot artifact path must be safe and relative")
        return value

    @field_validator("sha256")
    @classmethod
    def _hash(cls, value: str) -> str:
        return _sha(value)

    @model_validator(mode="after")
    def _positive_size(self) -> "RecognitionPilotFileV1":
        if self.size_bytes < 1:
            raise ValueError("pilot artifact must be non-empty")
        return self


class RecognitionPilotAdapterIdentityV1(_StrictFrozenModel):
    adapter_name: str
    adapter_version: str
    model: str
    model_version: str
    aligner: str
    aligner_version: str
    runtime_components: tuple[tuple[str, str], ...]
    runtime_hash: str
    adapter_code_hash: str
    config_hash: str
    execution_mode: Literal["fixture", "local", "import", "other"]

    @field_validator("runtime_hash", "adapter_code_hash", "config_hash")
    @classmethod
    def _hash(cls, value: str) -> str:
        return _sha(value)

    @model_validator(mode="after")
    def _canonical(self) -> "RecognitionPilotAdapterIdentityV1":
        if self.runtime_components != tuple(sorted(self.runtime_components)):
            raise ValueError("adapter runtime components are not canonical")
        if any(not value.strip() or value != value.strip() for value in (
            self.adapter_name,
            self.adapter_version,
            self.model,
            self.model_version,
            self.aligner,
            self.aligner_version,
        )):
            raise ValueError("adapter identity text must be non-blank and trimmed")
        return self


class RecognitionPilotClipV1(_StrictFrozenModel):
    ordinal: int
    clip: AudioClipBinding
    invocation_id: str
    request: RecognitionPilotFileV1
    request_content_hash: str
    evidence: RecognitionPilotFileV1
    evidence_content_hash: str
    provider_raw: RecognitionPilotFileV1

    @field_validator("request_content_hash", "evidence_content_hash")
    @classmethod
    def _hash(cls, value: str) -> str:
        return _sha(value)

    @model_validator(mode="after")
    def _valid(self) -> "RecognitionPilotClipV1":
        if self.ordinal < 0 or not self.invocation_id:
            raise ValueError("pilot clip ordinal and invocation identity are invalid")
        return self


class RecognitionPilotManifestV1(_StrictFrozenModel):
    _HASH_KIND: ClassVar[str] = "recognition_pilot_manifest_v1"

    schema_version: Literal[1] = 1
    status: Literal["complete"] = "complete"
    system_id: Literal[
        "qwen-primary", "faster-corroboration", "legacy-v1-whisperx"
    ]
    language_hint: Literal["zh-TW"] = "zh-TW"
    local_files_only: Literal[True] = True
    annotation_packet: RecognitionPilotFileV1
    annotation_packet_content_hash: str
    legacy_manifest: RecognitionPilotFileV1 | None = None
    legacy_initial_prompt_sha256: str | None = None
    adapter_identity: RecognitionPilotAdapterIdentityV1
    adapter_identity_hash: str
    clips: tuple[RecognitionPilotClipV1, ...]
    candidate: RecognitionPilotFileV1
    candidate_artifact_hash: str
    manifest_hash: str

    @field_validator(
        "annotation_packet_content_hash",
        "adapter_identity_hash",
        "candidate_artifact_hash",
        "manifest_hash",
    )
    @classmethod
    def _hash(cls, value: str) -> str:
        return _sha(value)

    @model_validator(mode="after")
    def _valid(self) -> "RecognitionPilotManifestV1":
        if not self.clips or tuple(item.ordinal for item in self.clips) != tuple(
            range(len(self.clips))
        ):
            raise ValueError("manifest clips must be non-empty and canonically ordered")
        if self.adapter_identity_hash != hash_object(self.adapter_identity):
            raise ValueError("manifest adapter identity hash mismatch")
        if self.system_id == "legacy-v1-whisperx":
            if self.legacy_manifest is None or self.legacy_initial_prompt_sha256 is None:
                raise ValueError("legacy V1 manifest and prompt bindings are required")
            _sha(self.legacy_initial_prompt_sha256, "legacy initial prompt hash")
        elif self.legacy_manifest is not None or self.legacy_initial_prompt_sha256 is not None:
            raise ValueError("non-legacy system cannot carry legacy V1 bindings")
        if self.manifest_hash != hash_object(
            {
                "artifact_kind": self._HASH_KIND,
                **self.model_dump(
                    mode="json", exclude={"manifest_hash"}, exclude_none=True
                ),
            }
        ):
            raise ValueError("recognition pilot manifest hash mismatch")
        return self

    def canonical_bytes(self) -> bytes:
        return canonical_json_bytes(self.model_dump(mode="json", exclude_none=True))


def _identity_model(identity: RecognitionModelIdentity) -> RecognitionPilotAdapterIdentityV1:
    if is_dataclass(identity):
        payload = asdict(identity)
    elif isinstance(identity, BaseModel):
        payload = identity.model_dump(mode="json")
    else:
        payload = {
            name: getattr(identity, name)
            for name in RecognitionPilotAdapterIdentityV1.model_fields
        }
    payload["runtime_components"] = tuple(tuple(item) for item in payload["runtime_components"])
    return RecognitionPilotAdapterIdentityV1.model_validate(payload, strict=True)


def _file_binding(root: Path, path: Path) -> RecognitionPilotFileV1:
    digest, size = measure_regular_file(path)
    return RecognitionPilotFileV1(
        relative_path=path.relative_to(root).as_posix(), sha256=digest, size_bytes=size
    )


def _read_measured(path: Path) -> tuple[bytes, str, int]:
    """Read exact bytes and reject replacement or mutation around the read."""

    before_hash, before_size = measure_regular_file(path)
    raw = path.read_bytes()
    if (sha256_bytes(raw), len(raw)) != (before_hash, before_size):
        raise ValueError(f"regular file changed between measurement and read: {path}")
    if measure_regular_file(path) != (before_hash, before_size):
        raise ValueError(f"regular file changed after read: {path}")
    return raw, before_hash, before_size


def _read_canonical(path: Path, model: type[BaseModel]) -> tuple[BaseModel, bytes]:
    raw, _digest, _size = _read_measured(path)
    try:
        value = model.model_validate_json(raw, strict=True)
    except Exception as exc:
        raise ValueError(f"invalid artifact JSON: {path}") from exc
    expected = (
        value.canonical_bytes()
        if isinstance(value, RecognitionPilotManifestV1)
        else canonical_json_bytes(value)
    )
    if expected != raw:
        raise ValueError(f"artifact is not exact canonical JSON: {path}")
    return value, raw


def _atomic_write_new(path: Path, content: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        existing, digest, size = _read_measured(path)
        if (digest, size) != (sha256_bytes(content), len(content)) or existing != content:
            raise ValueError(f"existing immutable artifact differs: {path}")
        return
    temporary_name: str | None = None
    try:
        with tempfile.NamedTemporaryFile(
            dir=path.parent,
            prefix=f".{path.name}.recognition-pilot-quarantine-",
            suffix=".tmp",
            delete=False,
        ) as temporary:
            temporary_name = temporary.name
            temporary.write(content)
            temporary.flush()
            os.fsync(temporary.fileno())
        if path.exists():
            raise ValueError(f"immutable artifact appeared during publication: {path}")
        os.replace(temporary_name, path)
        temporary_name = None
    except Exception as exc:
        if temporary_name is not None:
            raise ValueError(
                f"atomic publication failed; quarantine residue preserved at {temporary_name}"
            ) from exc
        raise


def _clip_path(clips_dir: Path, clip: AudioClipBinding) -> Path:
    if not _CLIP_ID_RE.fullmatch(clip.clip_id) or clip.clip_id in {".", ".."}:
        raise ValueError(
            f"clip_id cannot be represented as an exact WAV filename: {clip.clip_id!r}"
        )
    return clips_dir / f"{clip.clip_id}.wav"


def _preflight_clips(
    packet: TranscriptAnnotationPacket, clips_dir: Path
) -> tuple[Path, ...]:
    if not clips_dir.is_dir():
        raise ValueError("exact clips directory is unavailable")
    paths = tuple(_clip_path(clips_dir, clip) for clip in packet.clips)
    expected = {path.name for path in paths}
    actual = {item.name for item in clips_dir.iterdir()}
    if actual != expected:
        raise ValueError(
            f"exact clips directory file set mismatch; missing={sorted(expected - actual)}, "
            f"extra={sorted(actual - expected)}"
        )
    for clip, path in zip(packet.clips, paths, strict=True):
        if measure_regular_file(path) != (clip.clip_audio_hash, clip.clip_audio_size_bytes):
            raise ValueError(f"exact clip binding mismatch: {clip.clip_id}")
    return paths


def _invocation_id(
    *, packet: TranscriptAnnotationPacket, clip: AudioClipBinding, system_id: str,
    adapter_identity_hash: str,
) -> str:
    return "recognition-pilot-" + hash_object(
        {
            "namespace": "podcast-subtitle-v2/exact-clip-recognition-pilot/v1",
            "packet_hash": packet.packet_hash,
            "clip_binding_hash": clip.binding_hash,
            "system_id": system_id,
            "adapter_identity_hash": adapter_identity_hash,
        }
    )


def _request_for(
    *, packet: TranscriptAnnotationPacket, clip: AudioClipBinding, clip_path: Path,
    output_dir: Path, invocation_id: str,
) -> RecognitionRequest:
    return RecognitionRequest(
        episode_id=packet.episode_id,
        invocation_id=invocation_id,
        normalized_audio=clip_path,
        expected_normalized_audio_hash=clip.clip_audio_hash,
        raw_output_dir=output_dir / "quarantine" / invocation_id,
        language_hint="zh-TW",
        context_policy=NO_LEXICAL_BIAS_CONTEXT,
    )


def _artifact_path(root: Path, category: str, ordinal: int, clip_id: str, digest: str) -> Path:
    return root / category / f"{ordinal:04d}-{clip_id}-{digest}.json"


def _raw_path_from_uri(uri: str) -> Path:
    parsed = urlparse(uri)
    if parsed.scheme != "file":
        raise ValueError("Recognition provider raw output must be a local file URI")
    raw = url2pathname(unquote(parsed.path))
    if os.name == "nt" and len(raw) >= 3 and raw[0] in "/\\" and raw[2] == ":":
        raw = raw[1:]
    return Path(raw)


def _snapshot_raw(
    root: Path, evidence: RecognitionEvidence
) -> tuple[RecognitionEvidence, Path, bytes]:
    source = _raw_path_from_uri(evidence.raw_output.uri)
    raw_bytes, raw_hash, raw_size = _read_measured(source)
    if (raw_hash, raw_size) != (evidence.raw_output.sha256, evidence.raw_output.size_bytes):
        raise ValueError("Recognition Evidence provider raw binding mismatch")
    target = root / "raw" / f"{raw_hash}.bin"
    quarantine = (root / "quarantine").resolve()
    source_resolved = source.resolve()
    if source_resolved.is_relative_to(quarantine) and not target.exists():
        target.parent.mkdir(parents=True, exist_ok=True)
        os.replace(source_resolved, target)
    else:
        _atomic_write_new(target, raw_bytes)
    owned_digest = ArtifactDigest(
        uri=target.resolve().as_uri(), sha256=raw_hash, size_bytes=raw_size
    )
    owned = evidence.model_copy(
        update={"raw_output": owned_digest, "raw_output_hash": raw_hash}
    )
    # model_copy does not validate updates in Pydantic; force the full contract.
    owned = RecognitionEvidence.model_validate_json(canonical_json_bytes(owned), strict=True)
    return owned, target, raw_bytes


def _validate_evidence(
    evidence: RecognitionEvidence,
    *, request: RecognitionRequestArtifactV1, identity: RecognitionPilotAdapterIdentityV1,
) -> None:
    if (
        evidence.episode_id != request.episode_id
        or evidence.invocation_id != request.invocation_id
        or evidence.normalized_audio_hash != request.normalized_audio_sha256
        or evidence.adapter != identity.adapter_name
        or evidence.model != f"{identity.model}@{identity.model_version}"
        or evidence.config_hash != identity.config_hash
        or evidence.language.casefold().replace("_", "-")
        not in {"chinese", "zh", "zh-tw", "zh-hant", "zh-hant-tw"}
    ):
        raise ValueError("Recognition Evidence differs from the frozen pilot request/adapter")


def _expected_request(
    root: Path, packet: TranscriptAnnotationPacket, clip: AudioClipBinding, clip_path: Path,
    ordinal: int, system_id: str, identity_hash: str,
) -> tuple[RecognitionRequestArtifactV1, RecognitionRequest, Path]:
    invocation_id = _invocation_id(
        packet=packet, clip=clip, system_id=system_id, adapter_identity_hash=identity_hash
    )
    request = _request_for(
        packet=packet,
        clip=clip,
        clip_path=clip_path,
        output_dir=root,
        invocation_id=invocation_id,
    )
    artifact = build_recognition_request_artifact(request)
    digest = sha256_bytes(canonical_json_bytes(artifact))
    return artifact, request, _artifact_path(root, "requests", ordinal, clip.clip_id, digest)


def _existing_category_files(root: Path, category: str) -> set[Path]:
    directory = root / category
    if not directory.exists():
        return set()
    if not directory.is_dir():
        raise ValueError(f"pilot artifact category is not a directory: {directory}")
    result = set(directory.iterdir())
    if any(not item.is_file() for item in result):
        raise ValueError(f"pilot artifact category contains a non-file: {directory}")
    return result


def _assert_root_shape(root: Path, *, manifest_allowed: bool) -> None:
    if not root.exists():
        return
    if not root.is_dir():
        raise ValueError("recognition pilot output is not a directory")
    allowed = {"inputs", "requests", "evidence", "raw", "candidate", "quarantine"}
    if manifest_allowed:
        allowed.add(_MANIFEST_NAME)
    extra = {item.name for item in root.iterdir()} - allowed
    if extra:
        raise ValueError(f"recognition pilot contains unexpected root artifacts: {sorted(extra)}")


def _load_packet(path: Path) -> tuple[TranscriptAnnotationPacket, bytes]:
    raw, _digest, _size = _read_measured(path)
    packet = load_annotation_packet(raw)
    if packet.canonical_bytes() != raw:
        raise ValueError("annotation packet file is not exact canonical JSON")
    return packet, raw


def _real_adapter(
    system_id: str,
    *, qwen_model_revision: str, qwen_aligner_revision: str,
    faster_model_revision: str,
) -> _Recognizer:
    for label, value in (
        ("qwen_model_revision", qwen_model_revision),
        ("qwen_aligner_revision", qwen_aligner_revision),
        ("faster_model_revision", faster_model_revision),
    ):
        if not _REVISION_RE.fullmatch(value):
            raise ValueError(f"{label} must be exactly 40 lowercase hexadecimal characters")
    if system_id == "qwen-primary":
        from .adapters.recognition import Qwen3ASRRecognizerAdapter

        return Qwen3ASRRecognizerAdapter(
            model_revision=qwen_model_revision,
            forced_aligner_revision=qwen_aligner_revision,
            local_files_only=True,
        )
    if system_id == "faster-corroboration":
        from .adapters.faster_whisper_recognition import FasterWhisperRecognizerAdapter

        return FasterWhisperRecognizerAdapter(
            model_revision=faster_model_revision, local_files_only=True
        )
    if system_id == "legacy-v1-whisperx":
        raise ValueError("legacy-v1-whisperx requires an explicit legacy manifest")
    raise ValueError(f"unsupported recognition pilot system: {system_id}")


def _manifest_payload(
    *,
    system_id: str,
    packet_file: RecognitionPilotFileV1,
    packet: TranscriptAnnotationPacket,
    identity: RecognitionPilotAdapterIdentityV1,
    clips: Sequence[RecognitionPilotClipV1],
    candidate_file: RecognitionPilotFileV1,
    candidate: SourceBoundTranscriptCandidateArtifactV3,
    legacy_manifest_file: RecognitionPilotFileV1 | None = None,
    legacy_initial_prompt_sha256: str | None = None,
) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "schema_version": 1,
        "status": "complete",
        "system_id": system_id,
        "language_hint": "zh-TW",
        "local_files_only": True,
        "annotation_packet": packet_file,
        "annotation_packet_content_hash": packet.packet_hash,
        "adapter_identity": identity,
        "adapter_identity_hash": hash_object(identity),
        "clips": tuple(clips),
        "candidate": candidate_file,
        "candidate_artifact_hash": candidate.artifact_hash,
    }
    if legacy_manifest_file is not None:
        payload["legacy_manifest"] = legacy_manifest_file
        payload["legacy_initial_prompt_sha256"] = legacy_initial_prompt_sha256
    return payload


def _build_manifest(**kwargs: Any) -> RecognitionPilotManifestV1:
    payload = _manifest_payload(**kwargs)
    return RecognitionPilotManifestV1(
        **payload,
        manifest_hash=hash_object(
            {"artifact_kind": RecognitionPilotManifestV1._HASH_KIND, **payload}
        ),
    )


def _single_ordinal_file(
    files: set[Path], *, ordinal: int, clip_id: str, category: str
) -> Path | None:
    prefix = f"{ordinal:04d}-{clip_id}-"
    matches = tuple(path for path in files if path.name.startswith(prefix))
    if len(matches) > 1:
        raise ValueError(f"multiple immutable {category} artifacts exist for {clip_id}")
    return matches[0] if matches else None


def _assert_bound_file(root: Path, binding: RecognitionPilotFileV1) -> Path:
    path = root / binding.relative_path
    _raw, digest, size = _read_measured(path)
    if (digest, size) != (binding.sha256, binding.size_bytes):
        raise ValueError(f"manifest-bound artifact digest mismatch: {binding.relative_path}")
    return path


def _adapter_after_preflight(
    system_id: str,
    *,
    adapter: _Recognizer | None,
    qwen_model_revision: str,
    qwen_aligner_revision: str,
    faster_model_revision: str,
    legacy_manifest_path: Path | None,
) -> _Recognizer:
    if system_id not in PILOT_SYSTEMS:
        raise ValueError(f"unsupported recognition pilot system: {system_id}")
    if adapter is not None:
        return adapter
    if system_id == "legacy-v1-whisperx":
        if legacy_manifest_path is None:
            raise ValueError("legacy-v1-whisperx requires --legacy-manifest")
        from .legacy_v1_recognition import build_legacy_v1_whisperx_adapter

        return build_legacy_v1_whisperx_adapter(legacy_manifest_path)
    return _real_adapter(
        system_id,
        qwen_model_revision=qwen_model_revision,
        qwen_aligner_revision=qwen_aligner_revision,
        faster_model_revision=faster_model_revision,
    )


def _legacy_context(
    system_id: str, legacy_manifest_path: str | Path | None
) -> tuple[bytes, str, str] | None:
    if system_id != "legacy-v1-whisperx":
        if legacy_manifest_path is not None:
            raise ValueError("--legacy-manifest is only valid for legacy-v1-whisperx")
        return None
    if legacy_manifest_path is None:
        raise ValueError("legacy-v1-whisperx requires --legacy-manifest")
    from .legacy_v1_recognition import (
        legacy_v1_initial_prompt,
        load_legacy_v1_manifest,
    )

    manifest, raw, digest = load_legacy_v1_manifest(legacy_manifest_path)
    prompt_hash = sha256_bytes(legacy_v1_initial_prompt(manifest).encode("utf-8"))
    return raw, digest, prompt_hash


def _assert_legacy_identity(
    identity: RecognitionPilotAdapterIdentityV1,
    legacy: tuple[bytes, str, str] | None,
) -> None:
    if legacy is None:
        return
    _raw, manifest_hash, prompt_hash = legacy
    components = dict(identity.runtime_components)
    if (
        components.get("legacy_manifest_sha256") != manifest_hash
        or components.get("legacy_initial_prompt_sha256") != prompt_hash
        or "legacy_runner_source_sha256" not in components
        or "legacy_transcriber_source_sha256" not in components
    ):
        raise ValueError("legacy adapter identity does not bind manifest, prompt, and sources")


def run_recognition_pilot(
    *,
    annotation_packet_path: str | Path,
    clips_dir: str | Path,
    output_dir: str | Path,
    system_id: Literal[
        "qwen-primary", "faster-corroboration", "legacy-v1-whisperx"
    ],
    adapter: _Recognizer | None = None,
    legacy_manifest_path: str | Path | None = None,
    qwen_model_revision: str = QWEN_MODEL_REVISION,
    qwen_aligner_revision: str = QWEN_ALIGNER_REVISION,
    faster_model_revision: str = FASTER_MODEL_REVISION,
) -> RecognitionPilotManifestV1:
    """Run missing exact clips or verify and return an immutable completed pilot."""

    packet_path = Path(annotation_packet_path)
    packet, packet_bytes = _load_packet(packet_path)
    clip_paths = _preflight_clips(packet, Path(clips_dir))
    legacy = _legacy_context(system_id, legacy_manifest_path)
    root = Path(output_dir)
    _assert_root_shape(root, manifest_allowed=True)
    manifest_path = root / _MANIFEST_NAME
    recognizer = _adapter_after_preflight(
        system_id,
        adapter=adapter,
        qwen_model_revision=qwen_model_revision,
        qwen_aligner_revision=qwen_aligner_revision,
        faster_model_revision=faster_model_revision,
        legacy_manifest_path=(
            Path(legacy_manifest_path) if legacy_manifest_path is not None else None
        ),
    )
    if manifest_path.exists():
        return verify_recognition_pilot(
            annotation_packet_path=packet_path,
            clips_dir=clips_dir,
            output_dir=root,
            system_id=system_id,
            adapter=recognizer,
            legacy_manifest_path=legacy_manifest_path,
            qwen_model_revision=qwen_model_revision,
            qwen_aligner_revision=qwen_aligner_revision,
            faster_model_revision=faster_model_revision,
        )

    identity = _identity_model(recognizer.identity)
    _assert_legacy_identity(identity, legacy)
    identity_hash = hash_object(identity)
    root.mkdir(parents=True, exist_ok=True)
    packet_copy = root / "inputs" / f"annotation-packet-{sha256_bytes(packet_bytes)}.json"
    _atomic_write_new(packet_copy, packet_bytes)
    legacy_copy: Path | None = None
    legacy_prompt_hash: str | None = None
    if legacy is not None:
        legacy_bytes, legacy_hash, legacy_prompt_hash = legacy
        legacy_copy = root / "inputs" / f"legacy-v1-manifest-{legacy_hash}.json"
        _atomic_write_new(legacy_copy, legacy_bytes)
    request_files = _existing_category_files(root, "requests")
    evidence_files = _existing_category_files(root, "evidence")
    raw_files = _existing_category_files(root, "raw")
    candidate_files = _existing_category_files(root, "candidate")
    input_files = _existing_category_files(root, "inputs")
    expected_input_files = {packet_copy} | ({legacy_copy} if legacy_copy is not None else set())
    if input_files != expected_input_files:
        raise ValueError("recognition pilot input snapshot file set mismatch")
    quarantine_files = {
        path for path in (root / "quarantine").rglob("*") if path.is_file()
    } if (root / "quarantine").exists() else set()
    if quarantine_files:
        raise ValueError("recognition pilot has unresolved provider quarantine residue")

    # Phase 1: validate the complete existing state before inference is allowed.
    prepared_requests: list[
        tuple[RecognitionRequestArtifactV1, RecognitionRequest, Path, bool]
    ] = []
    prepared_evidence: list[
        tuple[RecognitionEvidence, Path, Path, bytes] | None
    ] = []
    matched_request_paths: set[Path] = set()
    matched_evidence_paths: set[Path] = set()
    matched_raw_paths: set[Path] = set()
    for ordinal, (clip, clip_path) in enumerate(zip(packet.clips, clip_paths, strict=True)):
        request_artifact, request, request_path = _expected_request(
            root, packet, clip, clip_path, ordinal, system_id, identity_hash
        )
        existing_request = _single_ordinal_file(
            request_files, ordinal=ordinal, clip_id=clip.clip_id, category="request"
        )
        if existing_request is not None and existing_request != request_path:
            raise ValueError(f"existing request has a mismatched content address: {clip.clip_id}")
        if existing_request is not None:
            loaded, _raw = _read_canonical(existing_request, RecognitionRequestArtifactV1)
            if loaded != request_artifact:
                raise ValueError(
                    f"stored request differs from deterministic replay: {clip.clip_id}"
                )
            matched_request_paths.add(existing_request)
        prepared_requests.append(
            (request_artifact, request, request_path, existing_request is not None)
        )

        evidence_path = _single_ordinal_file(
            evidence_files, ordinal=ordinal, clip_id=clip.clip_id, category="evidence"
        )
        if evidence_path is None:
            prepared_evidence.append(None)
            continue
        loaded, evidence_bytes = _read_canonical(evidence_path, RecognitionEvidence)
        assert isinstance(loaded, RecognitionEvidence)
        expected_evidence_path = _artifact_path(
            root, "evidence", ordinal, clip.clip_id, sha256_bytes(evidence_bytes)
        )
        if evidence_path != expected_evidence_path:
            raise ValueError(
                f"existing Evidence has a mismatched content address: {clip.clip_id}"
            )
        _validate_evidence(loaded, request=request_artifact, identity=identity)
        raw_path = root / "raw" / f"{loaded.raw_output.sha256}.bin"
        if loaded.raw_output.uri != raw_path.resolve().as_uri():
            raise ValueError(f"stored Evidence raw URI is not pilot-owned: {clip.clip_id}")
        raw_bytes, raw_hash, raw_size = _read_measured(raw_path)
        if (raw_hash, raw_size) != (
            loaded.raw_output.sha256,
            loaded.raw_output.size_bytes,
        ):
            raise ValueError(f"stored provider raw output mismatch: {clip.clip_id}")
        recognizer.verify(loaded, request=request, raw_output=raw_bytes)
        matched_evidence_paths.add(evidence_path)
        matched_raw_paths.add(raw_path)
        prepared_evidence.append((loaded, evidence_path, raw_path, raw_bytes))

    if request_files != matched_request_paths:
        raise ValueError("recognition pilot contains extra request artifacts")
    if evidence_files != matched_evidence_paths:
        raise ValueError("recognition pilot contains extra Evidence artifacts")
    if raw_files != matched_raw_paths:
        raise ValueError("recognition pilot contains extra provider raw artifacts")
    request_presence = tuple(item[3] for item in prepared_requests)
    evidence_presence = tuple(item is not None for item in prepared_evidence)
    if request_presence != tuple(sorted(request_presence, reverse=True)):
        raise ValueError("recognition pilot request state is not a valid prefix")
    if evidence_presence != tuple(sorted(evidence_presence, reverse=True)):
        raise ValueError("recognition pilot Evidence state is not a valid prefix")
    request_count = sum(request_presence)
    evidence_count = sum(evidence_presence)
    if request_count not in {
        evidence_count,
        min(evidence_count + 1, len(packet.clips)),
    }:
        raise ValueError("recognition pilot request/Evidence prefixes are inconsistent")
    if candidate_files and evidence_count != len(packet.clips):
        raise ValueError("Candidate V3 residue exists before complete Recognition Evidence")

    # Phase 2: only a globally valid immutable prefix may reach recognize().
    requests: list[RecognitionRequestArtifactV1] = []
    evidences: list[RecognitionEvidence] = []
    clip_records: list[RecognitionPilotClipV1] = []
    expected_request_paths: set[Path] = set()
    expected_evidence_paths: set[Path] = set()
    expected_raw_paths: set[Path] = set()
    for ordinal, (clip, prepared_request, stored_evidence) in enumerate(
        zip(packet.clips, prepared_requests, prepared_evidence, strict=True)
    ):
        request_artifact, request, request_path, request_exists = prepared_request
        if not request_exists:
            _atomic_write_new(request_path, canonical_json_bytes(request_artifact))
        expected_request_paths.add(request_path)
        requests.append(request_artifact)
        if stored_evidence is None:
            produced = recognizer.recognize(request)
            owned, raw_path, raw_bytes = _snapshot_raw(root, produced)
            _validate_evidence(owned, request=request_artifact, identity=identity)
            recognizer.verify(owned, request=request, raw_output=raw_bytes)
            evidence_bytes = canonical_json_bytes(owned)
            evidence_path = _artifact_path(
                root, "evidence", ordinal, clip.clip_id, sha256_bytes(evidence_bytes)
            )
            _atomic_write_new(evidence_path, evidence_bytes)
        else:
            owned, evidence_path, raw_path, _raw_bytes = stored_evidence
        expected_evidence_paths.add(evidence_path)
        expected_raw_paths.add(raw_path)
        evidences.append(owned)
        clip_records.append(
            RecognitionPilotClipV1(
                ordinal=ordinal,
                clip=clip,
                invocation_id=request_artifact.invocation_id,
                request=_file_binding(root, request_path),
                request_content_hash=request_artifact.content_hash,
                evidence=_file_binding(root, evidence_path),
                evidence_content_hash=hash_object(owned),
                provider_raw=_file_binding(root, raw_path),
            )
        )

    candidate = build_source_bound_transcript_candidate(
        candidate_id=f"recognition-pilot-{system_id}-{packet.packet_hash}",
        system_id=system_id,
        annotation_packet=packet,
        requests=tuple(requests),
        evidence=tuple(evidences),
    )
    candidate_bytes = candidate.canonical_bytes()
    candidate_path = root / "candidate" / f"candidate-v3-{sha256_bytes(candidate_bytes)}.json"
    if candidate_files:
        if candidate_files != {candidate_path}:
            raise ValueError("candidate residue differs from deterministic Candidate V3")
        stored_candidate, stored_bytes = _read_canonical(
            candidate_path, SourceBoundTranscriptCandidateArtifactV3
        )
        if stored_candidate != candidate or stored_bytes != candidate_bytes:
            raise ValueError("candidate residue differs from deterministic Candidate V3")
    else:
        _atomic_write_new(candidate_path, candidate_bytes)
    manifest = _build_manifest(
        system_id=system_id,
        packet_file=_file_binding(root, packet_copy),
        packet=packet,
        identity=identity,
        clips=clip_records,
        candidate_file=_file_binding(root, candidate_path),
        candidate=candidate,
        legacy_manifest_file=(
            _file_binding(root, legacy_copy) if legacy_copy is not None else None
        ),
        legacy_initial_prompt_sha256=legacy_prompt_hash,
    )
    _atomic_write_new(manifest_path, manifest.canonical_bytes())
    return verify_recognition_pilot(
        annotation_packet_path=packet_path,
        clips_dir=clips_dir,
        output_dir=root,
        system_id=system_id,
        adapter=recognizer,
        legacy_manifest_path=legacy_manifest_path,
        qwen_model_revision=qwen_model_revision,
        qwen_aligner_revision=qwen_aligner_revision,
        faster_model_revision=faster_model_revision,
    )


def verify_recognition_pilot(
    *,
    annotation_packet_path: str | Path,
    clips_dir: str | Path,
    output_dir: str | Path,
    system_id: Literal[
        "qwen-primary", "faster-corroboration", "legacy-v1-whisperx"
    ],
    adapter: _Recognizer | None = None,
    legacy_manifest_path: str | Path | None = None,
    qwen_model_revision: str = QWEN_MODEL_REVISION,
    qwen_aligner_revision: str = QWEN_ALIGNER_REVISION,
    faster_model_revision: str = FASTER_MODEL_REVISION,
) -> RecognitionPilotManifestV1:
    """Fresh-process verification: replay stored raw outputs, never inference."""

    packet_path = Path(annotation_packet_path)
    packet, packet_bytes = _load_packet(packet_path)
    clip_paths = _preflight_clips(packet, Path(clips_dir))
    legacy = _legacy_context(system_id, legacy_manifest_path)
    root = Path(output_dir)
    _assert_root_shape(root, manifest_allowed=True)
    manifest_value, manifest_bytes = _read_canonical(
        root / _MANIFEST_NAME, RecognitionPilotManifestV1
    )
    assert isinstance(manifest_value, RecognitionPilotManifestV1)
    manifest = manifest_value
    if manifest.system_id != system_id:
        raise ValueError("stored recognition pilot system differs from requested system")
    recognizer = _adapter_after_preflight(
        system_id,
        adapter=adapter,
        qwen_model_revision=qwen_model_revision,
        qwen_aligner_revision=qwen_aligner_revision,
        faster_model_revision=faster_model_revision,
        legacy_manifest_path=(
            Path(legacy_manifest_path) if legacy_manifest_path is not None else None
        ),
    )
    identity = _identity_model(recognizer.identity)
    _assert_legacy_identity(identity, legacy)
    if identity != manifest.adapter_identity:
        raise ValueError("fresh adapter identity differs from immutable pilot manifest")
    packet_copy = _assert_bound_file(root, manifest.annotation_packet)
    packet_copy_bytes, _packet_hash, _packet_size = _read_measured(packet_copy)
    if (
        packet_copy_bytes != packet_bytes
        or manifest.annotation_packet_content_hash != packet.packet_hash
    ):
        raise ValueError("manifest annotation packet binding mismatch")
    if len(manifest.clips) != len(packet.clips):
        raise ValueError("manifest does not bind every annotation packet clip")

    expected_files = {root / _MANIFEST_NAME, packet_copy}
    legacy_copy: Path | None = None
    legacy_prompt_hash: str | None = None
    if legacy is not None:
        legacy_bytes, legacy_hash, legacy_prompt_hash = legacy
        if manifest.legacy_manifest is None:
            raise ValueError("stored legacy pilot omits its legacy manifest binding")
        legacy_copy = _assert_bound_file(root, manifest.legacy_manifest)
        expected_legacy_path = root / "inputs" / f"legacy-v1-manifest-{legacy_hash}.json"
        legacy_copy_bytes, _legacy_hash, _legacy_size = _read_measured(legacy_copy)
        if legacy_copy != expected_legacy_path or legacy_copy_bytes != legacy_bytes:
            raise ValueError("stored legacy manifest differs from exact external replay input")
        if manifest.legacy_initial_prompt_sha256 != legacy_prompt_hash:
            raise ValueError("stored legacy initial prompt hash differs from replay")
        expected_files.add(legacy_copy)
    requests: list[RecognitionRequestArtifactV1] = []
    evidences: list[RecognitionEvidence] = []
    replay_records: list[RecognitionPilotClipV1] = []
    identity_hash = hash_object(identity)
    for ordinal, (clip, clip_path, record) in enumerate(
        zip(packet.clips, clip_paths, manifest.clips, strict=True)
    ):
        if record.ordinal != ordinal or record.clip != clip:
            raise ValueError("manifest clip order or exact binding mismatch")
        expected_request, _request, expected_path = _expected_request(
            root, packet, clip, clip_path, ordinal, system_id, identity_hash
        )
        request_path = _assert_bound_file(root, record.request)
        if request_path != expected_path:
            raise ValueError("manifest request path is not its canonical content address")
        loaded_request, request_bytes = _read_canonical(
            request_path, RecognitionRequestArtifactV1
        )
        assert isinstance(loaded_request, RecognitionRequestArtifactV1)
        if (
            loaded_request != expected_request
            or record.request_content_hash != loaded_request.content_hash
        ):
            raise ValueError("stored request differs from deterministic replay")
        request = materialize_recognition_request(
            loaded_request, normalized_audio=clip_path, raw_output_dir=root / "raw"
        )
        evidence_path = _assert_bound_file(root, record.evidence)
        loaded_evidence, evidence_bytes = _read_canonical(
            evidence_path, RecognitionEvidence
        )
        assert isinstance(loaded_evidence, RecognitionEvidence)
        if evidence_path != _artifact_path(
            root, "evidence", ordinal, clip.clip_id, sha256_bytes(evidence_bytes)
        ):
            raise ValueError("manifest Evidence path is not its canonical content address")
        if record.evidence_content_hash != hash_object(loaded_evidence):
            raise ValueError("manifest Evidence content hash mismatch")
        _validate_evidence(loaded_evidence, request=loaded_request, identity=identity)
        raw_path = _assert_bound_file(root, record.provider_raw)
        if raw_path != root / "raw" / f"{record.provider_raw.sha256}.bin":
            raise ValueError("manifest raw path is not its canonical content address")
        raw_bytes, raw_hash, raw_size = _read_measured(raw_path)
        if (raw_hash, raw_size) != (
            record.provider_raw.sha256,
            record.provider_raw.size_bytes,
        ):
            raise ValueError("stored provider raw output changed after measurement")
        if (
            loaded_evidence.raw_output.uri != raw_path.resolve().as_uri()
            or loaded_evidence.raw_output.sha256 != record.provider_raw.sha256
            or loaded_evidence.raw_output.size_bytes != record.provider_raw.size_bytes
        ):
            raise ValueError("Evidence does not bind its generation-owned raw snapshot")
        recognizer.verify(loaded_evidence, request=request, raw_output=raw_bytes)
        requests.append(loaded_request)
        evidences.append(loaded_evidence)
        replay_records.append(record)
        expected_files.update({request_path, evidence_path, raw_path})

    candidate_path = _assert_bound_file(root, manifest.candidate)
    loaded_candidate, candidate_bytes = _read_canonical(
        candidate_path, SourceBoundTranscriptCandidateArtifactV3
    )
    assert isinstance(loaded_candidate, SourceBoundTranscriptCandidateArtifactV3)
    if candidate_path != root / "candidate" / f"candidate-v3-{sha256_bytes(candidate_bytes)}.json":
        raise ValueError("manifest Candidate V3 path is not its canonical content address")
    rebuilt = build_source_bound_transcript_candidate(
        candidate_id=f"recognition-pilot-{system_id}-{packet.packet_hash}",
        system_id=system_id,
        annotation_packet=packet,
        requests=tuple(requests),
        evidence=tuple(evidences),
    )
    verify_source_bound_transcript_candidate(loaded_candidate, packet)
    if loaded_candidate != rebuilt or candidate_bytes != rebuilt.canonical_bytes():
        raise ValueError("stored Candidate V3 differs from exact fresh replay")
    if manifest.candidate_artifact_hash != rebuilt.artifact_hash:
        raise ValueError("manifest Candidate V3 artifact hash mismatch")
    expected_files.add(candidate_path)

    actual_files = {path for path in root.rglob("*") if path.is_file()}
    if actual_files != expected_files:
        unexpected = sorted(
            path.relative_to(root).as_posix() for path in actual_files - expected_files
        )
        missing = sorted(
            path.relative_to(root).as_posix() for path in expected_files - actual_files
        )
        raise ValueError(
            f"recognition pilot file set mismatch; extra={unexpected}, missing={missing}"
        )
    rebuilt_manifest = _build_manifest(
        system_id=system_id,
        packet_file=_file_binding(root, packet_copy),
        packet=packet,
        identity=identity,
        clips=replay_records,
        candidate_file=_file_binding(root, candidate_path),
        candidate=rebuilt,
        legacy_manifest_file=(
            _file_binding(root, legacy_copy) if legacy_copy is not None else None
        ),
        legacy_initial_prompt_sha256=legacy_prompt_hash,
    )
    if manifest != rebuilt_manifest or manifest_bytes != rebuilt_manifest.canonical_bytes():
        raise ValueError("stored recognition pilot manifest differs from exact replay")
    return manifest


__all__ = [
    "FASTER_MODEL_REVISION",
    "PILOT_SYSTEMS",
    "QWEN_ALIGNER_REVISION",
    "QWEN_MODEL_REVISION",
    "RecognitionPilotAdapterIdentityV1",
    "RecognitionPilotClipV1",
    "RecognitionPilotFileV1",
    "RecognitionPilotManifestV1",
    "run_recognition_pilot",
    "verify_recognition_pilot",
]
