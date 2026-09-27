"""Content-addressed filesystem store for immutable subtitle generations."""

from __future__ import annotations

import ctypes
import hashlib
import json
import os
import re
import shutil
import stat
import tempfile
import threading
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import TYPE_CHECKING, Any, Mapping

from .checkpoint import (
    EVIDENCE_PREFIX_MIGRATION_ARTIFACT,
    CreateCheckpoint,
    EvidencePrefixMigrationReceiptV1,
    EvidencePrefixMigrationReceiptV2,
    NativeAuditBasisMigrationReceiptV1,
    evidence_prefix_migration_artifact,
    is_evidence_prefix_migration_artifact,
    is_native_audit_basis_migration_artifact,
    native_audit_basis_migration_artifact,
)
from .errors import (
    ArtifactHashMismatchError,
    GenerationConflictError,
    GenerationIsolationError,
    GenerationNotFoundError,
    ResolutionTransactionError,
)
from .hashing import canonical_json_bytes, hash_file, hash_object, sha256_bytes
from .native_resolution import NativeResolveCheckpointV2

if TYPE_CHECKING:
    from .recognition_run import RecognitionRunRepository

_RECORD_NAME = "generation.json"
_ACTIVE_NAME = "active-generation.json"
_RESOLUTION_JOURNAL_NAME = "resolution-transaction.json"
_CREATE_CHECKPOINT_POINTER_NAME = "active-create-checkpoint.json"
_CREATE_CHECKPOINT_RECORD_NAME = "checkpoint.json"
_CREATE_EXECUTION_LOCK_NAME = "create-execution.lock"
_NATIVE_RESOLVE_CHECKPOINT_RECORD_NAME = "checkpoint.json"
_STORE_VERSION = 2
_LEGACY_STORE_VERSION = 1
_GENERATION_ID_RE = re.compile(r"^(?:[0-9a-f]{64}|generation-[0-9a-f]{64})$")
_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
_COPY_CHUNK_SIZE = 4 * 1024 * 1024


@dataclass(frozen=True)
class StoredGeneration:
    generation_id: str
    artifact_set_hash: str
    manifest: dict[str, Any]
    artifact_hashes: dict[str, str]
    directory: Path


@dataclass(frozen=True, slots=True)
class StoredAudioArtifact:
    """Verified content-addressed audio bytes owned by the episode store."""

    sha256: str
    size_bytes: int
    path: Path


@dataclass(frozen=True, slots=True)
class StoredCreateCheckpoint:
    """One fully re-hashed create checkpoint and its immutable directory."""

    checkpoint: CreateCheckpoint
    directory: Path


@dataclass(frozen=True, slots=True)
class StoredNativeResolveCheckpoint:
    """One fully re-hashed native resolution recovery checkpoint."""

    checkpoint: NativeResolveCheckpointV2
    directory: Path


class AudioSnapshot:
    """One exact streamed snapshot awaiting promotion into the audio CAS."""

    def __init__(
        self,
        store: GenerationStore,
        *,
        path: Path,
        sha256: str,
        size_bytes: int,
    ) -> None:
        self._store = store
        self.path = path
        self.sha256 = sha256
        self.size_bytes = size_bytes
        self._stored: StoredAudioArtifact | None = None

    def __enter__(self) -> AudioSnapshot:
        return self

    def __exit__(self, _exc_type: object, _exc: object, _traceback: object) -> None:
        self.discard()

    def discard(self) -> None:
        """Remove an uncommitted private staging file."""

        if self.path.exists():
            self.path.unlink()

    def verify(self) -> None:
        """Fail if any byte changed after this snapshot was captured."""

        self._store._verify_audio_file(
            self.path,
            expected_sha256=self.sha256,
            expected_size_bytes=self.size_bytes,
            label="staged audio snapshot",
        )

    def commit(self) -> StoredAudioArtifact:
        """Atomically promote this exact snapshot into the episode audio CAS."""

        if self._stored is None:
            self._stored = self._store._commit_audio_snapshot(self)
        return self._stored


def _safe_artifact_name(name: str) -> str:
    candidate = PurePosixPath(name.replace("\\", "/"))
    if (
        not name
        or ":" in name
        or candidate.is_absolute()
        or any(part in ("", ".", "..") for part in candidate.parts)
        or candidate.name == _RECORD_NAME
    ):
        raise ValueError(f"unsafe artifact name: {name!r}")
    return candidate.as_posix()


def _artifact_bytes(value: Any) -> bytes:
    if isinstance(value, bytes):
        return value
    if isinstance(value, str):
        return value.encode("utf-8")
    return canonical_json_bytes(value)


def _write_fsynced(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("wb") as stream:
        stream.write(data)
        stream.flush()
        os.fsync(stream.fileno())


def _replace_fsynced(source: Path, destination: Path) -> None:
    """Atomically replace a path and durably publish its directory entry."""

    if os.name == "nt":
        movefile_replace_existing = 0x1
        movefile_write_through = 0x8
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        move = kernel32.MoveFileExW
        move.argtypes = (ctypes.c_wchar_p, ctypes.c_wchar_p, ctypes.c_uint32)
        move.restype = ctypes.c_int
        if not move(
            str(source),
            str(destination),
            movefile_replace_existing | movefile_write_through,
        ):
            error = ctypes.get_last_error()
            raise OSError(error, f"durable atomic replace failed with Windows error {error}")
        return

    os.replace(source, destination)
    flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0)
    descriptor = os.open(destination.parent, flags)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


@contextmanager
def _exclusive_audio_lock(path: Path):
    """Serialize one digest's verify/promote transition across processes."""

    try:
        from filelock import FileLock, Timeout
    except ImportError as exc:  # pragma: no cover - declared core dependency
        raise ArtifactHashMismatchError(
            "cross-process audio CAS locking requires filelock"
        ) from exc
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        with FileLock(str(path)).acquire(timeout=30):
            yield
    except Timeout as exc:
        raise ArtifactHashMismatchError(f"timed out acquiring audio CAS lock: {path}") from exc


@contextmanager
def _exclusive_episode_lock(path: Path):
    """Serialize active-pointer and Correction Ledger transactions per episode."""

    try:
        from filelock import FileLock, Timeout
    except ImportError as exc:  # pragma: no cover - declared core dependency
        raise ResolutionTransactionError(
            "cross-process episode transactions require filelock"
        ) from exc
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        with FileLock(str(path)).acquire(timeout=30):
            yield
    except Timeout as exc:
        raise ResolutionTransactionError(
            f"timed out acquiring episode transaction lock: {path}"
        ) from exc


class GenerationStore:
    """Store immutable generations below ``episode_root/.subtitle-v2``.

    Callers never combine loose paths.  They open one generation and every read
    verifies the generation record plus the artifact's declared content hash.
    """

    def __init__(self, episode_root: str | Path) -> None:
        self.episode_root = Path(episode_root).resolve()
        self.root = self.episode_root / ".subtitle-v2"
        self.generations_dir = self.root / "generations"
        self.audio_dir = self.root / "audio" / "sha256"
        self.audio_staging_dir = self.root / "audio" / "staging"
        self.audio_locks_dir = self.root / "audio" / "locks"
        self.transactions_dir = self.root / "transactions"
        self.create_checkpoints_dir = self.root / "create-checkpoints"
        self.native_resolve_checkpoints_dir = self.root / "native-resolve-checkpoints"
        self.native_resolve_checkpoint_pointers_dir = (
            self.transactions_dir / "native-resolve-checkpoints"
        )
        self.recognition_runs_dir = self.root / "recognition-runs"
        self.create_checkpoint_pointer_path = (
            self.transactions_dir / _CREATE_CHECKPOINT_POINTER_NAME
        )
        self.resolution_journal_path = self.transactions_dir / _RESOLUTION_JOURNAL_NAME
        self.episode_lock_path = self.transactions_dir / "episode.lock"
        self._lock = threading.RLock()
        # A store instance is one verified session.  Opening a checkpoint still
        # hashes its complete immutable artifact set once; subsequent artifact
        # reads re-check the canonical record and the requested artifact only.
        # A fresh store/process has an empty cache and therefore performs the
        # complete verification again.
        self._verified_create_checkpoints: dict[str, StoredCreateCheckpoint] = {}

    @contextmanager
    def episode_transaction(self):
        """Hold the process-local and OS locks for one episode mutation."""

        with self._lock, _exclusive_episode_lock(self.episode_lock_path):
            yield

    @contextmanager
    def create_execution_lease(self, *, timeout_seconds: float = 30.0):
        """Own the episode's create execution until return or process death.

        This is an OS-backed exclusion lease, not a paid-provider idempotency
        claim.  A dead owner releases the kernel lock; a live but wedged owner is
        never stolen by wall-clock guesswork and contenders fail closed on
        timeout.  The durable started checkpoint supplies recovery identity.
        """

        try:
            from filelock import FileLock, Timeout
        except ImportError as exc:  # pragma: no cover - declared core dependency
            raise GenerationIsolationError(
                "cross-process create execution requires filelock"
            ) from exc
        path = self.transactions_dir / _CREATE_EXECUTION_LOCK_NAME
        path.parent.mkdir(parents=True, exist_ok=True)
        try:
            with FileLock(str(path)).acquire(timeout=timeout_seconds):
                yield
        except Timeout as exc:
            raise GenerationIsolationError(
                "timed out acquiring episode create execution lease"
            ) from exc

    def recognition_run_repository(self) -> RecognitionRunRepository:
        """Open the isolated, non-canonical bounded Recognition run store."""

        # Lazy import keeps the existing GenerationStore dependency direction:
        # recognition_run reuses only this module's durable filesystem primitives.
        from .recognition_run import RecognitionRunRepository

        return RecognitionRunRepository(self.root)

    def snapshot_audio(
        self,
        source: str | Path,
        *,
        expected_sha256: str | None = None,
        expected_size_bytes: int | None = None,
    ) -> AudioSnapshot:
        """Stream one descriptor into a private snapshot while hashing its bytes.

        The returned path, digest, and size all describe the same read.  Callers
        can safely give that path to an Adapter, verify it afterwards, and only
        then atomically promote it into the content-addressed store.
        """

        if expected_sha256 is not None and not _SHA256_RE.fullmatch(expected_sha256):
            raise ValueError("expected audio SHA-256 must be lowercase hexadecimal")
        if expected_size_bytes is not None and expected_size_bytes < 0:
            raise ValueError("expected audio size must be non-negative")
        source_path = Path(source)
        self.audio_staging_dir.mkdir(parents=True, exist_ok=True)
        descriptor, temporary_name = tempfile.mkstemp(
            prefix=".audio-snapshot-", dir=self.audio_staging_dir
        )
        temporary = Path(temporary_name)
        digest = hashlib.sha256()
        size_bytes = 0
        try:
            with (
                source_path.open("rb") as input_stream,
                os.fdopen(descriptor, "wb") as output_stream,
            ):
                if not stat.S_ISREG(os.fstat(input_stream.fileno()).st_mode):
                    raise ArtifactHashMismatchError(
                        f"audio source is not a regular file: {source_path}"
                    )
                while chunk := input_stream.read(_COPY_CHUNK_SIZE):
                    digest.update(chunk)
                    size_bytes += len(chunk)
                    output_stream.write(chunk)
                output_stream.flush()
                os.fsync(output_stream.fileno())
        except Exception:
            try:
                os.close(descriptor)
            except OSError:
                pass
            temporary.unlink(missing_ok=True)
            raise
        actual_sha256 = digest.hexdigest()
        if expected_sha256 is not None and actual_sha256 != expected_sha256:
            temporary.unlink(missing_ok=True)
            raise ArtifactHashMismatchError(
                "audio snapshot SHA-256 differs from its declared identity"
            )
        if expected_size_bytes is not None and size_bytes != expected_size_bytes:
            temporary.unlink(missing_ok=True)
            raise ArtifactHashMismatchError(
                "audio snapshot size differs from its declared identity"
            )
        return AudioSnapshot(
            self,
            path=temporary,
            sha256=actual_sha256,
            size_bytes=size_bytes,
        )

    def audio_path(self, sha256: str, *, size_bytes: int) -> Path:
        """Return a CAS path only after streaming its exact bytes through verification."""

        if not _SHA256_RE.fullmatch(sha256):
            raise ValueError("audio SHA-256 must be lowercase hexadecimal")
        if size_bytes < 0:
            raise ValueError("audio size must be non-negative")
        path = self.audio_dir / sha256
        self._verify_audio_file(
            path,
            expected_sha256=sha256,
            expected_size_bytes=size_bytes,
            label="stored audio artifact",
        )
        return path

    @staticmethod
    def _verify_audio_file(
        path: Path,
        *,
        expected_sha256: str,
        expected_size_bytes: int,
        label: str,
    ) -> None:
        digest = hashlib.sha256()
        size_bytes = 0
        try:
            with path.open("rb") as stream:
                if not stat.S_ISREG(os.fstat(stream.fileno()).st_mode):
                    raise ArtifactHashMismatchError(f"{label} is not a regular file")
                while chunk := stream.read(_COPY_CHUNK_SIZE):
                    digest.update(chunk)
                    size_bytes += len(chunk)
        except OSError as exc:
            raise ArtifactHashMismatchError(f"{label} is missing or unreadable") from exc
        if digest.hexdigest() != expected_sha256 or size_bytes != expected_size_bytes:
            raise ArtifactHashMismatchError(f"{label} bytes do not match their identity")

    def _commit_audio_snapshot(self, snapshot: AudioSnapshot) -> StoredAudioArtifact:
        snapshot.verify()
        destination = self.audio_dir / snapshot.sha256
        with self._lock:
            self.audio_dir.mkdir(parents=True, exist_ok=True)
            lock_path = self.audio_locks_dir / f"{snapshot.sha256}.lock"
            with _exclusive_audio_lock(lock_path):
                if destination.exists():
                    self._verify_audio_file(
                        destination,
                        expected_sha256=snapshot.sha256,
                        expected_size_bytes=snapshot.size_bytes,
                        label="stored audio artifact",
                    )
                    snapshot.path.unlink()
                else:
                    os.replace(snapshot.path, destination)
                self._verify_audio_file(
                    destination,
                    expected_sha256=snapshot.sha256,
                    expected_size_bytes=snapshot.size_bytes,
                    label="stored audio artifact",
                )
        return StoredAudioArtifact(
            sha256=snapshot.sha256,
            size_bytes=snapshot.size_bytes,
            path=destination,
        )

    def generation_dir(self, generation_id: str) -> Path:
        if not _GENERATION_ID_RE.fullmatch(generation_id):
            raise ValueError(f"invalid generation id: {generation_id!r}")
        return self.generations_dir / generation_id

    def create_checkpoint_dir(self, checkpoint_id: str) -> Path:
        """Resolve only a typed, content-addressed create checkpoint ID."""

        if not re.fullmatch(r"create-checkpoint-[0-9a-f]{64}", checkpoint_id):
            raise ValueError(f"invalid create checkpoint id: {checkpoint_id!r}")
        return self.create_checkpoints_dir / checkpoint_id

    def native_resolve_checkpoint_dir(self, checkpoint_id: str) -> Path:
        """Resolve only a typed, content-addressed native resolve checkpoint ID."""

        if not re.fullmatch(r"native-resolve-checkpoint-[0-9a-f]{64}", checkpoint_id):
            raise ValueError(f"invalid native resolve checkpoint id: {checkpoint_id!r}")
        return self.native_resolve_checkpoints_dir / checkpoint_id

    def _native_resolve_checkpoint_pointer_path(self, operation_key: str) -> Path:
        if not _SHA256_RE.fullmatch(operation_key):
            raise ValueError("native resolve operation key must be lowercase SHA-256")
        return self.native_resolve_checkpoint_pointers_dir / f"{operation_key}.json"

    def commit_native_resolve_checkpoint(
        self,
        checkpoint: NativeResolveCheckpointV2,
        *,
        artifacts: Mapping[str, Any],
    ) -> StoredNativeResolveCheckpoint:
        """Durably advance one operation-specific native child audit checkpoint.

        Each exact authorization gets an independent authenticated pointer, so
        retries for another candidate cannot overwrite or resume this process.
        No Generation, Ledger, journal, or active pointer is touched here.
        """

        encoded = {
            _safe_artifact_name(name): _artifact_bytes(value) for name, value in artifacts.items()
        }
        actual_hashes = {name: sha256_bytes(payload) for name, payload in sorted(encoded.items())}
        if actual_hashes != checkpoint.artifact_hashes:
            raise GenerationIsolationError(
                "Native resolve checkpoint artifact bytes differ from its typed manifest"
            )
        destination = self.native_resolve_checkpoint_dir(checkpoint.id)
        checkpoint_bytes = canonical_json_bytes(checkpoint)
        pointer_path = self._native_resolve_checkpoint_pointer_path(checkpoint.operation_key)

        with self.episode_transaction():
            current = self._read_native_resolve_checkpoint_pointer_locked(checkpoint.operation_key)
            current_id = current.get("checkpoint_id") if current is not None else None
            if checkpoint.stage == "audit_basis_ready":
                if current_id not in {None, checkpoint.id}:
                    raise GenerationIsolationError(
                        "Native resolve audit basis conflicts with an existing operation"
                    )
            elif current_id not in {checkpoint.id, checkpoint.previous_checkpoint_id}:
                raise GenerationIsolationError(
                    "Native resolve checkpoint pointer changed or stage lineage forked: "
                    f"expected previous={checkpoint.previous_checkpoint_id!r}, "
                    f"actual={current_id!r}"
                )
            if current is not None and current.get("operation_key") != checkpoint.operation_key:
                raise GenerationIsolationError(
                    "Native resolve checkpoint operation binding changed during commit"
                )
            if checkpoint.stage != "audit_basis_ready":
                previous_id = checkpoint.previous_checkpoint_id
                if previous_id is None:
                    raise GenerationIsolationError(
                        "post-basis Native resolve checkpoint lacks a predecessor"
                    )
                previous = self._load_native_resolve_checkpoint_directory(previous_id).checkpoint
                expected_stage = {
                    "audit_basis_ready": "text_audit_ready",
                    "text_audit_ready": "audio_audit_ready",
                }.get(previous.stage)
                if checkpoint.stage != expected_stage:
                    raise GenerationIsolationError(
                        "Native resolve checkpoint stage transition is not contiguous"
                    )
                inherited = (
                    "operation_key",
                    "episode_id",
                    "parent_generation_id",
                    "parent_manifest_hash",
                    "expected_active_generation_id",
                    "expected_ledger_head",
                    "prepared_ledger_entry_hash",
                    "decision_event_id",
                    "decision_content_hash",
                    "authorization_kind",
                    "authorization_id",
                    "authorization_hash",
                    "audit_basis_generation_id",
                    "audit_basis_transcript_hash",
                    "policy_hash",
                    "code_hash",
                    "reference_enrollment_hash",
                    "adapter_identities_hash",
                )
                if any(getattr(checkpoint, name) != getattr(previous, name) for name in inherited):
                    raise GenerationIsolationError(
                        "Native resolve checkpoint inherited identity changed"
                    )

            if destination.exists():
                stored = self._load_native_resolve_checkpoint_directory(checkpoint.id)
                if stored.checkpoint != checkpoint:
                    raise GenerationConflictError(
                        f"native resolve checkpoint {checkpoint.id} has conflicting content"
                    )
            else:
                self.native_resolve_checkpoints_dir.mkdir(parents=True, exist_ok=True)
                temporary = Path(
                    tempfile.mkdtemp(
                        prefix=f".{checkpoint.id[:32]}-",
                        dir=self.native_resolve_checkpoints_dir,
                    )
                )
                try:
                    for name, payload in encoded.items():
                        _write_fsynced(temporary / Path(name), payload)
                    _write_fsynced(
                        temporary / _NATIVE_RESOLVE_CHECKPOINT_RECORD_NAME,
                        checkpoint_bytes,
                    )
                    try:
                        _replace_fsynced(temporary, destination)
                    except OSError:
                        if not destination.exists():
                            raise
                        existing = self._load_native_resolve_checkpoint_directory(checkpoint.id)
                        shutil.rmtree(temporary, ignore_errors=True)
                        if existing.checkpoint != checkpoint:
                            raise GenerationConflictError(
                                f"native resolve checkpoint {checkpoint.id} concurrently conflicted"
                            )
                except Exception:
                    if temporary.exists():
                        shutil.rmtree(temporary, ignore_errors=True)
                    raise

            pointer_payload = {
                "checkpoint_id": checkpoint.id,
                "checkpoint_hash": sha256_bytes(checkpoint_bytes),
                "operation_key": checkpoint.operation_key,
            }
            wrapper = {
                **pointer_payload,
                "pointer_hash": hash_object(pointer_payload),
            }
            pointer_path.parent.mkdir(parents=True, exist_ok=True)
            temporary_pointer = pointer_path.with_name(f".{pointer_path.name}.tmp")
            _write_fsynced(temporary_pointer, canonical_json_bytes(wrapper))
            _replace_fsynced(temporary_pointer, pointer_path)
        return self._load_native_resolve_checkpoint_directory(checkpoint.id)

    def _read_native_resolve_checkpoint_pointer_locked(
        self, operation_key: str
    ) -> dict[str, Any] | None:
        path = self._native_resolve_checkpoint_pointer_path(operation_key)
        if not path.exists():
            return None
        if not path.is_file():
            raise GenerationIsolationError("Native resolve checkpoint pointer is not a file")
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError) as exc:
            raise GenerationIsolationError(
                "Native resolve checkpoint pointer is unreadable"
            ) from exc
        if not isinstance(payload, dict) or set(payload) != {
            "checkpoint_id",
            "checkpoint_hash",
            "operation_key",
            "pointer_hash",
        }:
            raise GenerationIsolationError("Native resolve checkpoint pointer schema is invalid")
        pointer_hash = payload.pop("pointer_hash")
        if pointer_hash != hash_object(payload):
            raise GenerationIsolationError("Native resolve checkpoint pointer hash mismatch")
        return payload

    def _load_native_resolve_checkpoint_directory(
        self, checkpoint_id: str
    ) -> StoredNativeResolveCheckpoint:
        directory = self.native_resolve_checkpoint_dir(checkpoint_id)
        path = directory / _NATIVE_RESOLVE_CHECKPOINT_RECORD_NAME
        try:
            checkpoint_bytes = path.read_bytes()
            checkpoint = NativeResolveCheckpointV2.model_validate_json(
                checkpoint_bytes, strict=True
            )
        except (OSError, ValueError) as exc:
            raise ArtifactHashMismatchError(
                f"Native resolve checkpoint is unreadable or invalid: {checkpoint_id}"
            ) from exc
        if canonical_json_bytes(checkpoint) != checkpoint_bytes:
            raise ArtifactHashMismatchError(
                "Native resolve checkpoint record is not canonical JSON"
            )
        if checkpoint.id != checkpoint_id:
            raise GenerationIsolationError(
                "Native resolve checkpoint directory crossed its content identity"
            )
        for name, expected_hash in checkpoint.artifact_hashes.items():
            safe_name = _safe_artifact_name(name)
            artifact_path = directory / Path(safe_name)
            if not artifact_path.is_file() or hash_file(artifact_path) != expected_hash:
                raise ArtifactHashMismatchError(
                    "Native resolve checkpoint artifact hash mismatch: "
                    f"checkpoint={checkpoint_id}, artifact={safe_name}"
                )
        return StoredNativeResolveCheckpoint(checkpoint=checkpoint, directory=directory)

    def load_native_resolve_checkpoint(
        self, *, expected_operation_key: str
    ) -> StoredNativeResolveCheckpoint | None:
        """Load and authenticate the current stage for one exact operation."""

        with self._lock:
            pointer = self._read_native_resolve_checkpoint_pointer_locked(expected_operation_key)
            if pointer is None:
                return None
            checkpoint_id = pointer["checkpoint_id"]
            if not isinstance(checkpoint_id, str):
                raise GenerationIsolationError("Native resolve checkpoint pointer ID is invalid")
            stored = self._load_native_resolve_checkpoint_directory(checkpoint_id)
            record_hash = hash_file(stored.directory / _NATIVE_RESOLVE_CHECKPOINT_RECORD_NAME)
            if pointer["checkpoint_hash"] != record_hash:
                raise GenerationIsolationError(
                    "Native resolve checkpoint pointer no longer binds its exact record"
                )
            if (
                pointer["operation_key"] != expected_operation_key
                or stored.checkpoint.operation_key != expected_operation_key
            ):
                raise GenerationIsolationError(
                    "Native resolve checkpoint crossed its operation binding"
                )
            return stored

    def load_native_resolve_checkpoint_id(
        self, checkpoint_id: str
    ) -> StoredNativeResolveCheckpoint:
        """Reopen one immutable native resolve predecessor."""

        with self._lock:
            return self._load_native_resolve_checkpoint_directory(checkpoint_id)

    def read_native_resolve_checkpoint_artifact(
        self,
        stored: StoredNativeResolveCheckpoint,
        name: str,
    ) -> bytes:
        """Read one artifact after revalidating the complete checkpoint."""

        verified = self._load_native_resolve_checkpoint_directory(stored.checkpoint.id)
        safe_name = _safe_artifact_name(name)
        if safe_name not in verified.checkpoint.artifact_hashes:
            raise GenerationNotFoundError(
                f"artifact not found in Native resolve checkpoint: {safe_name}"
            )
        payload = (verified.directory / Path(safe_name)).read_bytes()
        if sha256_bytes(payload) != verified.checkpoint.artifact_hashes[safe_name]:
            raise ArtifactHashMismatchError(
                f"Native resolve checkpoint artifact changed after verification: {safe_name}"
            )
        return payload

    @staticmethod
    def _validate_evidence_prefix_migration(
        *,
        checkpoint: CreateCheckpoint,
        previous: CreateCheckpoint,
        encoded: Mapping[str, bytes],
    ) -> None:
        """Validate the sole identity-changing create-checkpoint edge."""

        old_artifacts = dict(previous.artifact_hashes)
        child_artifacts = dict(checkpoint.artifact_hashes)
        added = set(child_artifacts) - set(old_artifacts)
        changed_or_missing = {
            name
            for name, digest in old_artifacts.items()
            if child_artifacts.get(name) != digest
        }
        if (
            len(added) != 1
            or changed_or_missing
            or not is_evidence_prefix_migration_artifact(next(iter(added)))
        ):
            raise GenerationIsolationError(
                "evidence-ready migration must preserve its predecessor and append one receipt"
            )
        receipt_name = next(iter(added))
        receipt_payload = encoded.get(receipt_name)
        if receipt_payload is None:
            raise GenerationIsolationError(
                "evidence-ready identity migration lacks its typed receipt"
            )
        old_migration_names = tuple(
            sorted(name for name in old_artifacts if is_evidence_prefix_migration_artifact(name))
        )
        try:
            if not old_migration_names:
                if receipt_name != EVIDENCE_PREFIX_MIGRATION_ARTIFACT:
                    raise ValueError("first migration must retain the V1 artifact name")
                receipt: EvidencePrefixMigrationReceiptV1 | EvidencePrefixMigrationReceiptV2 = (
                    EvidencePrefixMigrationReceiptV1.model_validate_json(
                        receipt_payload, strict=True
                    )
                )
            else:
                if receipt_name == EVIDENCE_PREFIX_MIGRATION_ARTIFACT:
                    raise ValueError("migration ledger receipt overwrote the V1 receipt")
                receipt = EvidencePrefixMigrationReceiptV2.model_validate_json(
                    receipt_payload, strict=True
                )
                if evidence_prefix_migration_artifact(receipt.id) != receipt_name:
                    raise ValueError("migration ledger artifact is not content addressed")
        except (TypeError, ValueError) as exc:
            raise GenerationIsolationError(
                "evidence-ready migration receipt is invalid"
            ) from exc
        if canonical_json_bytes(receipt) != receipt_payload:
            raise GenerationIsolationError(
                "evidence-ready migration receipt bytes are not canonical"
            )
        unchanged = (
            "episode_id",
            "source",
            "invocation_id",
            "normalized",
            "normalization_receipt_hash",
            "reference_enrollment_hash",
            "speaker_track_binding_hash",
            "expected_active_generation_id",
        )
        expected_receipt: dict[str, object] = {
            "episode_id": checkpoint.episode_id,
            "old_checkpoint_id": previous.id,
            "old_checkpoint_record_hash": sha256_bytes(canonical_json_bytes(previous)),
            "old_operation_key": previous.operation_key,
            "new_operation_key": checkpoint.operation_key,
            "old_input_binding_hash": previous.input_binding_hash,
            "new_input_binding_hash": checkpoint.input_binding_hash,
            "old_policy_hash": previous.policy_hash,
            "new_policy_hash": checkpoint.policy_hash,
            "old_code_hash": previous.code_hash,
            "new_code_hash": checkpoint.code_hash,
            "old_adapter_identity_set_hash": hash_object(previous.adapter_identities),
            "new_adapter_identity_set_hash": hash_object(checkpoint.adapter_identities),
        }
        if isinstance(receipt, EvidencePrefixMigrationReceiptV1):
            expected_receipt["evidence_artifact_set_hash"] = hash_object(
                tuple(sorted(old_artifacts.items()))
            )
        else:
            prior_receipts: list[
                EvidencePrefixMigrationReceiptV1 | EvidencePrefixMigrationReceiptV2
            ] = []
            for name in old_migration_names:
                payload = encoded.get(name)
                if payload is None:
                    raise GenerationIsolationError(
                        "evidence-ready migration did not preserve its receipt ledger"
                    )
                try:
                    parsed = (
                        EvidencePrefixMigrationReceiptV1.model_validate_json(payload, strict=True)
                        if name == EVIDENCE_PREFIX_MIGRATION_ARTIFACT
                        else EvidencePrefixMigrationReceiptV2.model_validate_json(
                            payload, strict=True
                        )
                    )
                except (TypeError, ValueError) as exc:
                    raise GenerationIsolationError(
                        "evidence-ready migration predecessor ledger is invalid"
                    ) from exc
                if canonical_json_bytes(parsed) != payload:
                    raise GenerationIsolationError(
                        "evidence-ready migration predecessor ledger is not canonical"
                    )
                prior_receipts.append(parsed)
            legacy = [
                item
                for item in prior_receipts
                if isinstance(item, EvidencePrefixMigrationReceiptV1)
            ]
            refreshes = sorted(
                (
                    item
                    for item in prior_receipts
                    if isinstance(item, EvidencePrefixMigrationReceiptV2)
                ),
                key=lambda item: item.sequence,
            )
            if len(legacy) != 1:
                raise GenerationIsolationError("evidence-ready migration ledger lacks its V1 root")
            head_id = legacy[0].id
            for expected_sequence, prior in enumerate(refreshes, start=2):
                if (
                    prior.sequence != expected_sequence
                    or prior.previous_migration_receipt_id != head_id
                ):
                    raise GenerationIsolationError(
                        "evidence-ready migration ledger sequence is invalid"
                    )
                head_id = prior.id
            base_artifacts = {
                name: digest
                for name, digest in old_artifacts.items()
                if not is_evidence_prefix_migration_artifact(name)
            }
            expected_receipt.update(
                {
                    "sequence": len(prior_receipts) + 1,
                    "previous_migration_receipt_id": head_id,
                    "predecessor_artifact_set_hash": hash_object(
                        tuple(sorted(old_artifacts.items()))
                    ),
                    "evidence_artifact_set_hash": hash_object(
                        tuple(sorted(base_artifacts.items()))
                    ),
                }
            )
        if (
            checkpoint.previous_checkpoint_id != previous.id
            or any(getattr(checkpoint, name) != getattr(previous, name) for name in unchanged)
            or any(getattr(receipt, name) != value for name, value in expected_receipt.items())
        ):
            raise GenerationIsolationError(
                "evidence-ready migration changed evidence or crossed its exact lineage"
            )
        for name, expected_hash in old_artifacts.items():
            payload = encoded.get(name)
            if payload is None or sha256_bytes(payload) != expected_hash:
                raise GenerationIsolationError(
                    "evidence-ready migration did not preserve every evidence byte"
                )

    @staticmethod
    def _validate_native_audit_basis_migration(
        *,
        checkpoint: CreateCheckpoint,
        previous: CreateCheckpoint,
        encoded: Mapping[str, bytes],
    ) -> None:
        old_artifacts = dict(previous.artifact_hashes)
        child_artifacts = dict(checkpoint.artifact_hashes)
        added = set(child_artifacts) - set(old_artifacts)
        changed = {
            name for name, digest in old_artifacts.items() if child_artifacts.get(name) != digest
        }
        if len(added) != 1 or changed:
            raise GenerationIsolationError(
                "native audit basis migration must preserve every predecessor artifact"
            )
        receipt_name = next(iter(added))
        if not is_native_audit_basis_migration_artifact(receipt_name):
            raise GenerationIsolationError(
                "native audit basis migration must append one content-addressed receipt"
            )
        payload = encoded.get(receipt_name)
        try:
            if payload is None:
                raise ValueError("missing receipt")
            receipt = NativeAuditBasisMigrationReceiptV1.model_validate_json(
                payload, strict=True
            )
        except (TypeError, ValueError) as exc:
            raise GenerationIsolationError(
                "native audit basis migration receipt is invalid"
            ) from exc
        if (
            canonical_json_bytes(receipt) != payload
            or native_audit_basis_migration_artifact(receipt.id) != receipt_name
        ):
            raise GenerationIsolationError(
                "native audit basis migration receipt is not canonical/content-addressed"
            )
        unchanged = (
            "episode_id",
            "source",
            "invocation_id",
            "normalized",
            "normalization_receipt_hash",
            "reference_enrollment_hash",
            "speaker_track_binding_hash",
            "expected_active_generation_id",
            "adapter_identities",
        )
        base_artifacts = {
            name: digest
            for name, digest in old_artifacts.items()
            if not is_native_audit_basis_migration_artifact(name)
        }
        expected = {
            "episode_id": checkpoint.episode_id,
            "old_checkpoint_id": previous.id,
            "old_checkpoint_record_hash": sha256_bytes(canonical_json_bytes(previous)),
            "old_operation_key": previous.operation_key,
            "new_operation_key": checkpoint.operation_key,
            "old_input_binding_hash": previous.input_binding_hash,
            "new_input_binding_hash": checkpoint.input_binding_hash,
            "old_policy_hash": previous.policy_hash,
            "new_policy_hash": checkpoint.policy_hash,
            "old_code_hash": previous.code_hash,
            "new_code_hash": checkpoint.code_hash,
            "old_adapter_identity_set_hash": hash_object(previous.adapter_identities),
            "new_adapter_identity_set_hash": hash_object(checkpoint.adapter_identities),
            "predecessor_artifact_set_hash": hash_object(tuple(sorted(old_artifacts.items()))),
            "basis_artifact_set_hash": hash_object(tuple(sorted(base_artifacts.items()))),
        }
        if (
            checkpoint.previous_checkpoint_id != previous.id
            or any(getattr(checkpoint, name) != getattr(previous, name) for name in unchanged)
            or any(getattr(receipt, name) != value for name, value in expected.items())
        ):
            raise GenerationIsolationError(
                "native audit basis migration crossed its exact code-only lineage"
            )
        for name, expected_hash in old_artifacts.items():
            artifact = encoded.get(name)
            if artifact is None or sha256_bytes(artifact) != expected_hash:
                raise GenerationIsolationError(
                    "native audit basis migration did not preserve every predecessor byte"
                )

    def commit_create_checkpoint(
        self,
        checkpoint: CreateCheckpoint,
        *,
        artifacts: Mapping[str, Any],
    ) -> StoredCreateCheckpoint:
        """Durably advance the create recovery pointer by one exact stage.

        Checkpoint directories are immutable.  The small authenticated pointer
        is the only mutable part and may advance only from the checkpoint named
        by ``previous_checkpoint_id``.  An orphan left before pointer publication
        is harmless and can be reused by an identical retry.
        """

        encoded = {
            _safe_artifact_name(name): _artifact_bytes(value) for name, value in artifacts.items()
        }
        actual_hashes = {name: sha256_bytes(payload) for name, payload in sorted(encoded.items())}
        if actual_hashes != checkpoint.artifact_hashes:
            raise GenerationIsolationError(
                "Create Checkpoint artifact bytes differ from its typed manifest"
            )
        destination = self.create_checkpoint_dir(checkpoint.id)
        checkpoint_bytes = canonical_json_bytes(checkpoint)

        with self.episode_transaction():
            if checkpoint.stage == "started":
                try:
                    actual_active = self.active_generation_id()
                except GenerationNotFoundError:
                    actual_active = None
                if actual_active != checkpoint.expected_active_generation_id:
                    raise GenerationIsolationError(
                        "active generation changed while capturing create intent: "
                        f"expected={checkpoint.expected_active_generation_id!r}, "
                        f"actual={actual_active!r}"
                    )
            current = self._read_create_checkpoint_pointer_locked()
            current_id = current.get("checkpoint_id") if current is not None else None
            starts_after_terminal = False
            if checkpoint.stage == "started" and current_id not in {None, checkpoint.id}:
                if not isinstance(current_id, str):
                    raise GenerationIsolationError("Create Checkpoint pointer ID is invalid")
                predecessor = self._load_create_checkpoint_directory(current_id)
                starts_after_terminal = predecessor.checkpoint.stage == "complete"
            if (
                current_id not in {checkpoint.id, checkpoint.previous_checkpoint_id}
                and not starts_after_terminal
            ):
                raise GenerationIsolationError(
                    "Create Checkpoint pointer changed or stage lineage forked: "
                    f"expected previous={checkpoint.previous_checkpoint_id!r}, "
                    f"actual={current_id!r}"
                )
            is_evidence_migration = False
            is_basis_migration = False
            if checkpoint.stage == "evidence_ready" and checkpoint.previous_checkpoint_id:
                previous_for_migration = self._load_create_checkpoint_directory(
                    checkpoint.previous_checkpoint_id
                ).checkpoint
                is_evidence_migration = previous_for_migration.stage == "evidence_ready"
            if checkpoint.stage == "native_audit_basis_ready" and checkpoint.previous_checkpoint_id:
                previous_for_basis_migration = self._load_create_checkpoint_directory(
                    checkpoint.previous_checkpoint_id
                ).checkpoint
                is_basis_migration = (
                    previous_for_basis_migration.stage == "native_audit_basis_ready"
                )
            if (
                current is not None
                and current.get("operation_key") != checkpoint.operation_key
                and not starts_after_terminal
                and not is_evidence_migration
                and not is_basis_migration
            ):
                raise GenerationIsolationError(
                    "Create Checkpoint operation binding changed during commit"
                )
            if checkpoint.stage != "started":
                previous_id = checkpoint.previous_checkpoint_id
                if previous_id is None:
                    raise GenerationIsolationError(
                        "post-start Create Checkpoint lacks a predecessor"
                    )
                previous = self._load_create_checkpoint_directory(previous_id).checkpoint
                expected_stages = {
                    "started": {"evidence_ready"},
                    "evidence_ready": {
                        "evidence_ready",
                        "correction_ready",
                        "native_audit_basis_ready",
                    },
                    "native_audit_basis_ready": {
                        "native_audit_basis_ready",
                        "native_text_audit_ready",
                    },
                    "native_text_audit_ready": {"native_audio_audit_ready"},
                    "native_audio_audit_ready": {"complete"},
                    "correction_ready": {"audio_audit_ready"},
                    "audio_audit_ready": {"complete"},
                }.get(previous.stage, set())
                if checkpoint.stage not in expected_stages:
                    raise GenerationIsolationError(
                        "Create Checkpoint stage transition is not contiguous"
                    )
                inherited = (
                    "operation_key",
                    "episode_id",
                    "source",
                    "input_binding_hash",
                    "policy_hash",
                    "code_hash",
                    "reference_enrollment_hash",
                    "speaker_track_binding_hash",
                    "adapter_identities",
                    "expected_active_generation_id",
                )
                if is_evidence_migration:
                    self._validate_evidence_prefix_migration(
                        checkpoint=checkpoint,
                        previous=previous,
                        encoded=encoded,
                    )
                elif is_basis_migration:
                    self._validate_native_audit_basis_migration(
                        checkpoint=checkpoint,
                        previous=previous,
                        encoded=encoded,
                    )
                elif any(
                    getattr(checkpoint, name) != getattr(previous, name) for name in inherited
                ):
                    raise GenerationIsolationError("Create Checkpoint inherited identity changed")
                if previous.stage != "started" and (
                    checkpoint.invocation_id != previous.invocation_id
                    or checkpoint.normalized != previous.normalized
                    or checkpoint.normalization_receipt_hash != previous.normalization_receipt_hash
                ):
                    raise GenerationIsolationError(
                        "Create Checkpoint normalization lineage changed"
                    )

            if destination.exists():
                stored = self._load_create_checkpoint_directory(checkpoint.id)
                if stored.checkpoint != checkpoint:
                    raise GenerationConflictError(
                        f"create checkpoint {checkpoint.id} has conflicting content"
                    )
            else:
                self.create_checkpoints_dir.mkdir(parents=True, exist_ok=True)
                temporary = Path(
                    tempfile.mkdtemp(
                        prefix=f".{checkpoint.id[:24]}-",
                        dir=self.create_checkpoints_dir,
                    )
                )
                try:
                    for name, payload in encoded.items():
                        _write_fsynced(temporary / Path(name), payload)
                    _write_fsynced(
                        temporary / _CREATE_CHECKPOINT_RECORD_NAME,
                        checkpoint_bytes,
                    )
                    try:
                        _replace_fsynced(temporary, destination)
                    except OSError:
                        if not destination.exists():
                            raise
                        existing = self._load_create_checkpoint_directory(checkpoint.id)
                        shutil.rmtree(temporary, ignore_errors=True)
                        if existing.checkpoint != checkpoint:
                            raise GenerationConflictError(
                                f"create checkpoint {checkpoint.id} concurrently conflicted"
                            )
                except Exception:
                    if temporary.exists():
                        shutil.rmtree(temporary, ignore_errors=True)
                    raise

            pointer_payload = {
                "checkpoint_id": checkpoint.id,
                "checkpoint_hash": sha256_bytes(checkpoint_bytes),
                "operation_key": checkpoint.operation_key,
            }
            wrapper = {
                **pointer_payload,
                "pointer_hash": hash_object(pointer_payload),
            }
            self.transactions_dir.mkdir(parents=True, exist_ok=True)
            temporary_pointer = self.transactions_dir / (f".{_CREATE_CHECKPOINT_POINTER_NAME}.tmp")
            _write_fsynced(temporary_pointer, canonical_json_bytes(wrapper))
            _replace_fsynced(temporary_pointer, self.create_checkpoint_pointer_path)
        return self._load_create_checkpoint_directory(checkpoint.id)

    def _read_create_checkpoint_pointer_locked(self) -> dict[str, Any] | None:
        path = self.create_checkpoint_pointer_path
        if not path.exists():
            return None
        if not path.is_file():
            raise GenerationIsolationError("Create Checkpoint pointer is not a file")
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError) as exc:
            raise GenerationIsolationError("Create Checkpoint pointer is unreadable") from exc
        if not isinstance(payload, dict) or set(payload) != {
            "checkpoint_id",
            "checkpoint_hash",
            "operation_key",
            "pointer_hash",
        }:
            raise GenerationIsolationError("Create Checkpoint pointer schema is invalid")
        pointer_hash = payload.pop("pointer_hash")
        if pointer_hash != hash_object(payload):
            raise GenerationIsolationError("Create Checkpoint pointer hash mismatch")
        return payload

    def _load_create_checkpoint_directory(self, checkpoint_id: str) -> StoredCreateCheckpoint:
        directory = self.create_checkpoint_dir(checkpoint_id)
        path = directory / _CREATE_CHECKPOINT_RECORD_NAME
        try:
            checkpoint_bytes = path.read_bytes()
            checkpoint = CreateCheckpoint.model_validate_json(checkpoint_bytes)
        except (OSError, ValueError) as exc:
            raise ArtifactHashMismatchError(
                f"Create Checkpoint is unreadable or invalid: {checkpoint_id}"
            ) from exc
        if canonical_json_bytes(checkpoint) != checkpoint_bytes:
            raise ArtifactHashMismatchError("Create Checkpoint record is not canonical JSON")
        if checkpoint.id != checkpoint_id:
            raise GenerationIsolationError(
                "Create Checkpoint directory crossed its content identity"
            )
        for name, expected_hash in checkpoint.artifact_hashes.items():
            safe_name = _safe_artifact_name(name)
            artifact_path = directory / Path(safe_name)
            if not artifact_path.is_file() or hash_file(artifact_path) != expected_hash:
                raise ArtifactHashMismatchError(
                    "Create Checkpoint artifact hash mismatch: "
                    f"checkpoint={checkpoint_id}, artifact={safe_name}"
                )
        stored = StoredCreateCheckpoint(checkpoint=checkpoint, directory=directory)
        self._verified_create_checkpoints[checkpoint_id] = stored
        return stored

    def load_create_checkpoint(
        self,
        *,
        expected_operation_key: str,
    ) -> StoredCreateCheckpoint | None:
        """Load and re-hash the current checkpoint, rejecting input drift."""

        with self._lock:
            pointer = self._read_create_checkpoint_pointer_locked()
            if pointer is None:
                return None
            if pointer["operation_key"] != expected_operation_key:
                raise GenerationIsolationError(
                    "Create Checkpoint belongs to different source, references, policy, "
                    "microphone tracks, code, or Adapter identities"
                )
            checkpoint_id = pointer["checkpoint_id"]
            if not isinstance(checkpoint_id, str):
                raise GenerationIsolationError("Create Checkpoint pointer ID is invalid")
            stored = self._load_create_checkpoint_directory(checkpoint_id)
            record_hash = hash_file(stored.directory / _CREATE_CHECKPOINT_RECORD_NAME)
            if pointer["checkpoint_hash"] != record_hash:
                raise GenerationIsolationError(
                    "Create Checkpoint pointer no longer binds its exact record"
                )
            if stored.checkpoint.operation_key != expected_operation_key:
                raise GenerationIsolationError(
                    "Create Checkpoint record crossed its operation binding"
                )
            return stored

    def load_latest_create_checkpoint(self) -> StoredCreateCheckpoint | None:
        """Load and authenticate the current create checkpoint without guessing an input.

        Read-only operators use this to report partial progress before they have
        a caller-supplied request from which an operation key could be rebuilt.
        The pointer, record, and every addressed artifact are still re-hashed.
        """

        with self._lock:
            pointer = self._read_create_checkpoint_pointer_locked()
            if pointer is None:
                return None
            checkpoint_id = pointer["checkpoint_id"]
            if not isinstance(checkpoint_id, str):
                raise GenerationIsolationError("Create Checkpoint pointer ID is invalid")
            stored = self._load_create_checkpoint_directory(checkpoint_id)
            record_hash = hash_file(stored.directory / _CREATE_CHECKPOINT_RECORD_NAME)
            if pointer["checkpoint_hash"] != record_hash:
                raise GenerationIsolationError(
                    "Create Checkpoint pointer no longer binds its exact record"
                )
            if pointer["operation_key"] != stored.checkpoint.operation_key:
                raise GenerationIsolationError(
                    "Create Checkpoint pointer crossed its operation binding"
                )
            return stored

    def load_create_checkpoint_id(self, checkpoint_id: str) -> StoredCreateCheckpoint:
        """Reopen one immutable predecessor named by a verified checkpoint."""

        with self._lock:
            return self._load_create_checkpoint_directory(checkpoint_id)

    def read_create_checkpoint_artifact(
        self,
        stored: StoredCreateCheckpoint,
        name: str,
    ) -> bytes:
        """Read one artifact inside an already fully verified store session."""

        verified = self._verified_create_checkpoints.get(stored.checkpoint.id)
        if (
            verified is None
            or verified.checkpoint != stored.checkpoint
            or verified.directory != stored.directory
        ):
            verified = self._load_create_checkpoint_directory(stored.checkpoint.id)
        record_path = verified.directory / _CREATE_CHECKPOINT_RECORD_NAME
        if hash_file(record_path) != sha256_bytes(canonical_json_bytes(verified.checkpoint)):
            self._verified_create_checkpoints.pop(stored.checkpoint.id, None)
            raise ArtifactHashMismatchError(
                f"Create Checkpoint record changed after verification: {stored.checkpoint.id}"
            )
        safe_name = _safe_artifact_name(name)
        if safe_name not in verified.checkpoint.artifact_hashes:
            raise GenerationNotFoundError(f"artifact not found in Create Checkpoint: {safe_name}")
        payload = (verified.directory / Path(safe_name)).read_bytes()
        if sha256_bytes(payload) != verified.checkpoint.artifact_hashes[safe_name]:
            self._verified_create_checkpoints.pop(stored.checkpoint.id, None)
            raise ArtifactHashMismatchError(
                f"Create Checkpoint artifact changed after verification: {safe_name}"
            )
        return payload

    def commit(
        self,
        *,
        manifest: Mapping[str, Any] | Any,
        artifacts: Mapping[str, Any],
        logical_generation_id: str | None = None,
    ) -> StoredGeneration:
        """Atomically commit a content-addressed generation.

        Repeating an identical commit is idempotent.  A pre-existing directory
        with the calculated ID but different bytes is an integrity conflict.
        """

        if not artifacts:
            raise ValueError("a generation must contain at least one artifact")
        manifest_payload = (
            manifest.model_dump(mode="json", exclude_none=False)
            if hasattr(manifest, "model_dump")
            else dict(manifest)
        )
        encoded: dict[str, bytes] = {}
        for raw_name, value in artifacts.items():
            name = _safe_artifact_name(raw_name)
            if name in encoded:
                raise ValueError(f"duplicate artifact name: {name!r}")
            encoded[name] = _artifact_bytes(value)
        artifact_hashes = {name: sha256_bytes(data) for name, data in sorted(encoded.items())}
        artifact_set_identity = {
            "store_version": _STORE_VERSION,
            "manifest": manifest_payload,
            "artifact_hashes": artifact_hashes,
        }
        artifact_set_hash = hash_object(artifact_set_identity)
        generation_id = logical_generation_id or artifact_set_hash
        # Validate before using a caller-provided identity as a path component.
        self.generation_dir(generation_id)
        manifest_generation_id = manifest_payload.get("generation_id")
        if (
            logical_generation_id is not None
            and manifest_generation_id is not None
            and manifest_generation_id != logical_generation_id
        ):
            raise GenerationIsolationError(
                "manifest generation_id does not match logical_generation_id: "
                f"manifest={manifest_generation_id!r}, logical={logical_generation_id!r}"
            )
        record = {
            **artifact_set_identity,
            "generation_id": generation_id,
            "artifact_set_hash": artifact_set_hash,
        }
        destination = self.generation_dir(generation_id)

        with self._lock:
            if destination.exists():
                try:
                    existing = self.load(generation_id)
                except (ArtifactHashMismatchError, GenerationIsolationError) as exc:
                    raise GenerationConflictError(
                        f"generation {generation_id} exists with conflicting content"
                    ) from exc
                if existing.artifact_set_hash != artifact_set_hash:
                    raise GenerationConflictError(
                        f"generation {generation_id} already addresses a different artifact set"
                    )
                return existing

            self.generations_dir.mkdir(parents=True, exist_ok=True)
            temp_dir = Path(
                tempfile.mkdtemp(prefix=f".{generation_id[:12]}-", dir=self.generations_dir)
            )
            try:
                for name, data in encoded.items():
                    _write_fsynced(temp_dir / Path(name), data)
                _write_fsynced(temp_dir / _RECORD_NAME, canonical_json_bytes(record))
                try:
                    _replace_fsynced(temp_dir, destination)
                except OSError:
                    if destination.exists():
                        existing = self.load(generation_id)
                        shutil.rmtree(temp_dir, ignore_errors=True)
                        if existing.artifact_set_hash != artifact_set_hash:
                            raise GenerationConflictError(
                                f"generation {generation_id} concurrently committed "
                                "different content"
                            )
                        return existing
                    raise
            except Exception:
                if temp_dir.exists():
                    shutil.rmtree(temp_dir, ignore_errors=True)
                raise
        return self.load(generation_id)

    def load(self, generation_id: str) -> StoredGeneration:
        directory = self.generation_dir(generation_id)
        record_path = directory / _RECORD_NAME
        if not record_path.is_file():
            raise GenerationNotFoundError(f"generation not found: {generation_id}")
        try:
            record = json.loads(record_path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError) as exc:
            raise ArtifactHashMismatchError(
                f"generation record is unreadable: {generation_id}"
            ) from exc

        declared_id = record.get("generation_id")
        store_version = record.get("store_version")
        artifact_set_identity = {
            "store_version": store_version,
            "manifest": record.get("manifest"),
            "artifact_hashes": record.get("artifact_hashes"),
        }
        actual_artifact_set_hash = hash_object(artifact_set_identity)
        if declared_id != generation_id:
            raise GenerationIsolationError(
                f"generation identity mismatch: requested={generation_id}, declared={declared_id}"
            )
        if store_version == _LEGACY_STORE_VERSION:
            declared_artifact_set_hash = declared_id
        elif store_version == _STORE_VERSION:
            declared_artifact_set_hash = record.get("artifact_set_hash")
        else:
            raise GenerationIsolationError(
                f"unsupported generation store version: {store_version!r}"
            )
        if declared_artifact_set_hash != actual_artifact_set_hash:
            raise GenerationIsolationError(
                "generation artifact-set identity mismatch: "
                f"declared={declared_artifact_set_hash}, actual={actual_artifact_set_hash}"
            )
        artifact_hashes = record.get("artifact_hashes")
        manifest = record.get("manifest")
        if not isinstance(artifact_hashes, dict) or not isinstance(manifest, dict):
            raise ArtifactHashMismatchError(f"generation record schema invalid: {generation_id}")
        for name, expected_hash in artifact_hashes.items():
            safe_name = _safe_artifact_name(name)
            path = directory / Path(safe_name)
            if not path.is_file() or hash_file(path) != expected_hash:
                raise ArtifactHashMismatchError(
                    f"artifact hash mismatch: generation={generation_id}, artifact={safe_name}"
                )
        return StoredGeneration(
            generation_id=generation_id,
            artifact_set_hash=actual_artifact_set_hash,
            manifest=manifest,
            artifact_hashes=dict(artifact_hashes),
            directory=directory,
        )

    def read_artifact(
        self,
        generation_id: str,
        name: str,
        *,
        require_active: bool = False,
    ) -> bytes:
        if require_active and self.active_generation_id() != generation_id:
            raise GenerationIsolationError(
                f"generation {generation_id} is not the active generation"
            )
        generation = self.load(generation_id)
        safe_name = _safe_artifact_name(name)
        if safe_name not in generation.artifact_hashes:
            raise GenerationNotFoundError(
                f"artifact not found in generation {generation_id}: {safe_name}"
            )
        path = generation.directory / Path(safe_name)
        try:
            payload = path.read_bytes()
        except OSError as exc:
            raise ArtifactHashMismatchError(
                f"artifact became unreadable: generation={generation_id}, artifact={safe_name}"
            ) from exc
        expected_hash = generation.artifact_hashes[safe_name]
        actual_hash = sha256_bytes(payload)
        if actual_hash != expected_hash:
            raise ArtifactHashMismatchError(
                "artifact changed after generation verification: "
                f"generation={generation_id}, artifact={safe_name}"
            )
        return payload

    def _write_active_locked(self, generation_id: str) -> None:
        generation = self.load(generation_id)
        payload = {
            "generation_id": generation_id,
            "artifact_set_hash": generation.artifact_set_hash,
            "generation_record_hash": hash_file(generation.directory / _RECORD_NAME),
        }
        payload["pointer_hash"] = hash_object(payload)
        self.root.mkdir(parents=True, exist_ok=True)
        temporary = self.root / f".{_ACTIVE_NAME}.tmp"
        _write_fsynced(temporary, canonical_json_bytes(payload))
        _replace_fsynced(temporary, self.root / _ACTIVE_NAME)

    def set_active_cas_locked(
        self,
        generation_id: str,
        *,
        expected_generation_id: str | None,
    ) -> None:
        """Set active under ``episode_transaction`` after an exact pointer CAS."""

        try:
            actual_generation_id = self.active_generation_id()
        except GenerationNotFoundError:
            actual_generation_id = None
        if actual_generation_id == generation_id:
            return
        if actual_generation_id != expected_generation_id:
            raise GenerationIsolationError(
                "active generation changed during episode transaction: "
                f"expected={expected_generation_id!r}, actual={actual_generation_id!r}"
            )
        self._write_active_locked(generation_id)

    def set_active(self, generation_id: str) -> None:
        """Unconditionally activate a stored generation under the episode lock."""

        with self.episode_transaction():
            self._write_active_locked(generation_id)

    def resolution_journal_exists(self) -> bool:
        return self.resolution_journal_path.is_file()

    def read_resolution_journal_locked(self) -> dict[str, Any] | None:
        """Read and authenticate the single durable resolution recovery journal."""

        path = self.resolution_journal_path
        if not path.exists():
            return None
        if not path.is_file():
            raise ResolutionTransactionError("resolution transaction journal is not a file")
        try:
            wrapper = json.loads(path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError) as exc:
            raise ResolutionTransactionError(
                "resolution transaction journal is unreadable"
            ) from exc
        if not isinstance(wrapper, dict) or set(wrapper) != {"payload", "journal_hash"}:
            raise ResolutionTransactionError(
                "resolution transaction journal has an invalid envelope"
            )
        payload = wrapper["payload"]
        if not isinstance(payload, dict) or wrapper["journal_hash"] != hash_object(payload):
            raise ResolutionTransactionError("resolution transaction journal hash mismatch")
        return payload

    def write_resolution_journal_locked(self, payload: Mapping[str, Any]) -> None:
        """Atomically fsync one prepared/completed transaction recovery record."""

        typed_payload = dict(payload)
        wrapper = {
            "payload": typed_payload,
            "journal_hash": hash_object(typed_payload),
        }
        self.transactions_dir.mkdir(parents=True, exist_ok=True)
        temporary = self.transactions_dir / f".{_RESOLUTION_JOURNAL_NAME}.tmp"
        _write_fsynced(temporary, canonical_json_bytes(wrapper))
        _replace_fsynced(temporary, self.resolution_journal_path)

    def active_generation_id(self) -> str:
        path = self.root / _ACTIVE_NAME
        if not path.is_file():
            raise GenerationNotFoundError("no active subtitle generation")
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError) as exc:
            raise GenerationIsolationError("active generation pointer is unreadable") from exc
        pointer_hash = payload.pop("pointer_hash", None)
        if pointer_hash != hash_object(payload):
            raise GenerationIsolationError("active generation pointer hash mismatch")
        generation_id = payload.get("generation_id")
        generation = self.load(generation_id)
        if payload.get("artifact_set_hash") != generation.artifact_set_hash:
            raise GenerationIsolationError("active generation artifact-set hash changed")
        record_hash = hash_file(generation.directory / _RECORD_NAME)
        if payload.get("generation_record_hash") != record_hash:
            raise GenerationIsolationError("active generation record changed")
        return generation.generation_id


__all__ = [
    "GenerationStore",
    "StoredAudioArtifact",
    "StoredCreateCheckpoint",
    "StoredGeneration",
]
