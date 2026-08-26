"""Run Director -> DP -> same-Director audit with trusted execution receipts.

Creative workers may write only phase-local proposal files.  This module records
the actual subscription-runtime session, binds it to the proposal bytes, and is
the only layer that calls the deterministic visual-pipeline accept functions.
It never writes a canonical plan, fulfillment, audit, recipe, or Resolve state.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Mapping, Protocol, Sequence

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from agents.brook.script_video import highlight_visual_pipeline as visual_pipeline  # noqa: E402
from agents.brook.script_video.highlight_candidate_renderer import (  # noqa: E402
    TrustedRenderError,
    hydrate_dp_proposal,
)

EXECUTION_RECEIPT_CONTRACT = "podcast-highlight-visual-worker-execution-v1"
EXECUTION_PREPARE_CONTRACT = "podcast-highlight-visual-worker-prepare-v1"
EXECUTION_FAILURE_CONTRACT = "podcast-highlight-visual-worker-failure-v1"
_JOB_ROOT = Path("highlights") / "visual-pipeline"
_SAFE_TOKEN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")
_SAFE_REVISION = re.compile(r"^r-[0-9a-f]{24}$")
_SAFE_SESSION = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{7,199}$")
_FORBIDDEN_PROPOSAL_KEYS = {
    "worker_execution",
    "worker_identity",
    "worker_id",
    "execution_id",
    "session_id",
    "role",
}
_PHASES = {
    "director",
    "director_replan",
    "dp",
    "refinement_decision",
    "semantic_audit",
}
_MAX_PROPOSAL_BYTES = 32 * 1024 * 1024


class VisualPipelineOrchestrationError(RuntimeError):
    """A worker execution or persisted orchestration receipt is not trustworthy."""


@dataclass(frozen=True, slots=True)
class DispatchRequest:
    """One isolated subscription-worker turn."""

    phase: str
    role: str
    prompt: str
    working_directory: Path
    proposal_path: Path
    proposal_contract: str
    resume_session_id: str | None = None


@dataclass(frozen=True, slots=True)
class DispatchResult:
    """Observable process result; identity comes from runtime JSONL, not the agent."""

    session_id: str
    returncode: int
    stdout: str
    stderr: str


class VisualDispatchAdapter(Protocol):
    """Injectable boundary for Codex today and other subscription runtimes later."""

    name: str

    def dispatch(self, request: DispatchRequest) -> DispatchResult: ...


def _default_codex_executable() -> str:
    configured = os.environ.get("PODCAST_CODEX_EXECUTABLE")
    if configured:
        return configured
    if os.name == "nt":
        local_appdata = os.environ.get("LOCALAPPDATA")
        if local_appdata:
            local_bin = Path(local_appdata) / "OpenAI" / "Codex" / "bin"
            app_binaries = sorted(
                (candidate for candidate in local_bin.glob("*/codex.exe") if candidate.is_file()),
                key=lambda candidate: candidate.stat().st_mtime,
                reverse=True,
            )
            if app_binaries:
                return str(app_binaries[0])
        appdata = os.environ.get("APPDATA")
        if appdata:
            npm = Path(appdata) / "npm" / "codex.cmd"
            if npm.is_file():
                return str(npm)
    return shutil.which("codex.exe") or shutil.which("codex") or "codex"


class CodexExecDispatcher:
    """Codex CLI adapter using persisted JSONL sessions and a phase-local sandbox."""

    name = "codex-exec"

    def __init__(
        self,
        *,
        executable: str | None = None,
        runner: Callable[..., object] | None = None,
        timeout_seconds: int = 7200,
    ) -> None:
        self.executable = executable or _default_codex_executable()
        self._runner = runner or subprocess.run
        self.timeout_seconds = timeout_seconds

    @staticmethod
    def _session_from_jsonl(stdout: str) -> str:
        sessions: set[str] = set()
        for line_number, line in enumerate(stdout.splitlines(), 1):
            if not line.strip():
                continue
            try:
                event = json.loads(line)
            except json.JSONDecodeError as error:
                raise VisualPipelineOrchestrationError(
                    f"Codex JSONL line {line_number} is invalid"
                ) from error
            if not isinstance(event, dict):
                raise VisualPipelineOrchestrationError(
                    f"Codex JSONL line {line_number} is not an object"
                )
            if event.get("type") == "thread.started":
                thread_id = event.get("thread_id")
                if not isinstance(thread_id, str) or not _SAFE_SESSION.fullmatch(thread_id):
                    raise VisualPipelineOrchestrationError(
                        "Codex thread.started has an unsafe session identity"
                    )
                sessions.add(thread_id)
        if len(sessions) != 1:
            raise VisualPipelineOrchestrationError(
                "Codex JSONL must identify exactly one persisted session"
            )
        return next(iter(sessions))

    def dispatch(self, request: DispatchRequest) -> DispatchResult:
        workdir = request.working_directory.resolve()
        if not workdir.is_dir():
            raise VisualPipelineOrchestrationError(f"worker directory does not exist: {workdir}")
        argv = [
            self.executable,
            "exec",
            "--json",
            "--sandbox",
            "workspace-write",
            "--skip-git-repo-check",
            "-C",
            str(workdir),
        ]
        if request.resume_session_id is not None:
            argv.extend(("resume", request.resume_session_id, "-"))
        else:
            argv.append("-")
        run_kwargs: dict[str, object] = {
            "input": request.prompt,
            "capture_output": True,
            "text": True,
            "encoding": "utf-8",
            "errors": "replace",
            "cwd": str(workdir),
            "timeout": self.timeout_seconds,
            "check": False,
        }
        if os.name == "nt" and Path(self.executable).suffix.lower() in {".cmd", ".bat"}:
            run_kwargs["shell"] = True
        try:
            completed = self._runner(argv, **run_kwargs)
        except (OSError, subprocess.SubprocessError) as error:
            raise VisualPipelineOrchestrationError(f"Codex exec could not run: {error}") from error
        returncode = getattr(completed, "returncode", None)
        stdout = getattr(completed, "stdout", None)
        stderr = getattr(completed, "stderr", None)
        if (
            not isinstance(returncode, int)
            or not isinstance(stdout, str)
            or not isinstance(stderr, str)
        ):
            raise VisualPipelineOrchestrationError(
                "Codex runner returned an invalid process result"
            )
        if returncode != 0:
            raise VisualPipelineOrchestrationError(
                f"Codex exec failed with exit {returncode}: {stderr.strip() or 'no stderr'}"
            )
        session_id = self._session_from_jsonl(stdout)
        if request.resume_session_id is not None and session_id != request.resume_session_id:
            raise VisualPipelineOrchestrationError(
                "Codex resume returned a different Director session"
            )
        return DispatchResult(
            session_id=session_id,
            returncode=returncode,
            stdout=stdout,
            stderr=stderr,
        )


def _canonical_bytes(value: Mapping[str, object]) -> bytes:
    try:
        return json.dumps(
            value,
            ensure_ascii=False,
            allow_nan=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    except (TypeError, ValueError) as error:
        raise VisualPipelineOrchestrationError("orchestration value is not strict JSON") from error


def _content_hash(value: Mapping[str, object]) -> str:
    return hashlib.sha256(_canonical_bytes(value)).hexdigest()


def _file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _safe_local_file(root: Path, value: str | Path, label: str) -> Path:
    path = Path(value).resolve()
    if not path.is_relative_to(root) or not path.is_file():
        raise VisualPipelineOrchestrationError(f"{label} must be an episode-local file")
    return path


def _safe_revision_id(value: object) -> str:
    if not isinstance(value, str) or not _SAFE_REVISION.fullmatch(value):
        raise VisualPipelineOrchestrationError(f"unsafe core revision_id: {value!r}")
    return value


def _safe_cut_id(value: str) -> str:
    if not _SAFE_TOKEN.fullmatch(value):
        raise VisualPipelineOrchestrationError(f"unsafe cut_id: {value!r}")
    return value


def _atomic_write(path: Path, payload: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        if not path.is_file() or path.read_bytes() != payload:
            raise VisualPipelineOrchestrationError(
                f"immutable orchestration artifact conflicts: {path}"
            )
        return
    descriptor, temp_name = tempfile.mkstemp(prefix=f".{path.name}.", dir=str(path.parent))
    try:
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temp_name, path)
    finally:
        temp = Path(temp_name)
        if temp.exists():
            temp.unlink()


def _write_json(path: Path, value: Mapping[str, object]) -> None:
    payload = (
        json.dumps(value, ensure_ascii=False, allow_nan=False, indent=2, sort_keys=True).encode(
            "utf-8"
        )
        + b"\n"
    )
    _atomic_write(path, payload)


def _identity(root: Path, path: Path) -> dict[str, object]:
    resolved = path.resolve()
    if not resolved.is_relative_to(root) or not resolved.is_file():
        raise VisualPipelineOrchestrationError("execution artifact escaped the episode root")
    return {
        "path": resolved.relative_to(root).as_posix(),
        "bytes": resolved.stat().st_size,
        "sha256": _file_sha256(resolved),
    }


def _path_inventory(root: Path, path: Path) -> dict[str, object]:
    resolved = path.resolve()
    if not resolved.is_relative_to(root) or path.is_symlink():
        raise VisualPipelineOrchestrationError("legacy recovery source escaped the episode root")
    if resolved.is_file():
        return {
            "kind": "file",
            "files": [
                {
                    "path": ".",
                    "bytes": resolved.stat().st_size,
                    "sha256": _file_sha256(resolved),
                }
            ],
        }
    if not resolved.is_dir():
        raise VisualPipelineOrchestrationError("legacy recovery source is missing")
    files: list[dict[str, object]] = []
    for candidate in sorted(resolved.rglob("*"), key=lambda item: item.as_posix()):
        if candidate.is_symlink():
            raise VisualPipelineOrchestrationError(
                "legacy recovery evidence contains a symbolic link"
            )
        if candidate.is_file():
            files.append(
                {
                    "path": candidate.relative_to(resolved).as_posix(),
                    "bytes": candidate.stat().st_size,
                    "sha256": _file_sha256(candidate),
                }
            )
    return {"kind": "directory", "files": files}


def _load_json(path: Path, label: str) -> dict[str, object]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as error:
        raise VisualPipelineOrchestrationError(f"{label} is missing or invalid JSON") from error
    if not isinstance(value, dict):
        raise VisualPipelineOrchestrationError(f"{label} must be a JSON object")
    return value


def _legacy_director_receipt_without_prepare(
    episode_root: Path,
    receipt_path: Path,
    *,
    cut_id: str,
    revision_id: str,
) -> bool:
    if not receipt_path.is_file():
        return False
    receipt = _load_json(receipt_path, "legacy Director execution receipt")
    expected_keys = {
        "content_hash",
        "contract",
        "cut_id",
        "episode_id",
        "phase",
        "phase_input",
        "prompt_sha256",
        "proposal",
        "revision_id",
        "role",
        "stderr",
        "stdout",
        "worker_identity",
    }
    if set(receipt) != expected_keys:
        return False
    claimed_hash = receipt.get("content_hash")
    unsigned = {key: value for key, value in receipt.items() if key != "content_hash"}
    if (
        claimed_hash != _content_hash(unsigned)
        or receipt.get("contract") != EXECUTION_RECEIPT_CONTRACT
        or receipt.get("episode_id") != episode_root.name
        or receipt.get("cut_id") != cut_id
        or receipt.get("revision_id") != revision_id
        or receipt.get("phase") != "director"
        or receipt.get("role") != "director"
    ):
        return False
    worker = receipt.get("worker_identity")
    if not isinstance(worker, dict) or set(worker) != {
        "worker_id",
        "execution_id",
        "role",
        "session_id",
    }:
        return False
    if worker.get("role") != "director" or any(
        not isinstance(value, str) or not value for value in worker.values()
    ):
        return False
    for name in ("phase_input", "proposal", "stdout", "stderr"):
        claimed = receipt.get(name)
        if not isinstance(claimed, dict) or not isinstance(claimed.get("path"), str):
            return False
        evidence_path = (episode_root / str(claimed["path"])).resolve()
        if not evidence_path.is_relative_to(episode_root) or not evidence_path.is_file():
            return False
        if _identity(episode_root, evidence_path) != claimed:
            return False
    return True


def _recover_legacy_missing_prepare_chain(
    episode_root: Path,
    job_root: Path,
    *,
    cut_id: str,
    revision_id: str,
    state: Mapping[str, object],
    editorial_master: object | None,
) -> bool:
    """Archive one fully identified pre-PREPARE chain, then restart at Director.

    This compatibility path is deliberately narrow: it runs only for a request-
    bound pending revision with no CURRENT pointer and a hash-valid legacy Director
    receipt whose sole schema difference is the absent durable PREPARE identity.
    Request-bound asset authority and acquisition evidence are never moved.
    """

    error = state.get("error")
    if (
        state.get("status") != "invalid"
        or state.get("pending_revision_id") != revision_id
        or state.get("current_revision_id") is not None
        or not isinstance(error, str)
        or "trusted execution receipt fields mismatch" not in error
        or "'prepare'" not in error
    ):
        return False
    receipt_root = job_root / "receipts"
    if not _legacy_director_receipt_without_prepare(
        episode_root,
        receipt_root / "director.json",
        cut_id=cut_id,
        revision_id=revision_id,
    ):
        return False

    revision_root = (
        episode_root
        / _JOB_ROOT
        / cut_id
        / "revisions"
        / revision_id
    ).resolve()
    director_plan = revision_root / "DIRECTOR-PLAN.json"
    if not director_plan.is_file():
        return False
    recovery_root = job_root / "legacy-recovery" / "missing-prepare-director-v1"
    prepare_path = recovery_root / "PREPARE.json"
    commit_path = recovery_root / "COMMIT.json"
    if commit_path.is_file():
        raise VisualPipelineOrchestrationError(
            "legacy missing-PREPARE recovery was already committed but state is still invalid"
        )

    if prepare_path.is_file():
        prepare = _load_json(prepare_path, "legacy recovery prepare")
        prepare_hash = prepare.pop("content_hash", None)
        if prepare_hash != _content_hash(prepare):
            raise VisualPipelineOrchestrationError("legacy recovery prepare hash mismatch")
        prepare["content_hash"] = prepare_hash
    else:
        sources = [director_plan]
        for candidate in (
            revision_root / "DP-FULFILLMENT.json",
            revision_root / "SEMANTIC-AUDIT.json",
            job_root / "workers" / "director-session",
            job_root / "workers" / "dp-session",
            job_root / "trusted",
        ):
            if candidate.exists():
                sources.append(candidate)
        for candidate in sorted(receipt_root.iterdir(), key=lambda item: item.name):
            if candidate.name.startswith(("director", "dp", "semantic_audit")):
                sources.append(candidate)
        rows: list[dict[str, object]] = []
        seen: set[Path] = set()
        for source in sources:
            resolved = source.resolve()
            if resolved in seen:
                continue
            seen.add(resolved)
            relative = resolved.relative_to(episode_root)
            destination = recovery_root / "evidence" / relative
            rows.append(
                {
                    "source": relative.as_posix(),
                    "destination": destination.relative_to(episode_root).as_posix(),
                    "inventory": _path_inventory(episode_root, resolved),
                }
            )
        prepare = {
            "contract": "podcast-highlight-legacy-execution-recovery-prepare-v1",
            "episode_id": episode_root.name,
            "cut_id": cut_id,
            "revision_id": revision_id,
            "reason": "legacy Director receipt omitted durable PREPARE identity",
            "sources": rows,
        }
        prepare["content_hash"] = _content_hash(prepare)
        _write_json(prepare_path, prepare)

    rows = prepare.get("sources")
    if not isinstance(rows, list) or not rows:
        raise VisualPipelineOrchestrationError("legacy recovery source inventory is empty")
    moved: list[tuple[Path, Path]] = []
    try:
        for row in rows:
            if not isinstance(row, dict):
                raise VisualPipelineOrchestrationError("legacy recovery source row is invalid")
            source = (episode_root / str(row.get("source") or "")).resolve()
            destination = (episode_root / str(row.get("destination") or "")).resolve()
            expected = row.get("inventory")
            if (
                not source.is_relative_to(episode_root)
                or not destination.is_relative_to(recovery_root)
                or not isinstance(expected, dict)
            ):
                raise VisualPipelineOrchestrationError("legacy recovery path escaped its root")
            source_exists = source.exists()
            destination_exists = destination.exists()
            if source_exists == destination_exists:
                raise VisualPipelineOrchestrationError(
                    "legacy recovery source/destination state is ambiguous"
                )
            active = source if source_exists else destination
            if _path_inventory(episode_root, active) != expected:
                raise VisualPipelineOrchestrationError("legacy recovery evidence hash drift")
            if source_exists:
                destination.parent.mkdir(parents=True, exist_ok=True)
                os.replace(source, destination)
                moved.append((source, destination))

        recovered = _status(episode_root, cut_id, editorial_master)
        if (
            recovered.get("status") != "awaiting_director"
            or recovered.get("pending_revision_id") != revision_id
            or recovered.get("current_revision_id") is not None
        ):
            raise VisualPipelineOrchestrationError(
                "legacy recovery did not restore the request to awaiting_director"
            )
        commit: dict[str, object] = {
            "contract": "podcast-highlight-legacy-execution-recovery-commit-v1",
            "prepare": _identity(episode_root, prepare_path),
            "status": {
                "status": recovered["status"],
                "pending_revision_id": recovered["pending_revision_id"],
                "current_revision_id": recovered["current_revision_id"],
            },
        }
        commit["content_hash"] = _content_hash(commit)
        _write_json(commit_path, commit)
        return True
    except BaseException:
        rollback_errors: list[str] = []
        for source, destination in reversed(moved):
            try:
                source.parent.mkdir(parents=True, exist_ok=True)
                os.replace(destination, source)
            except OSError as rollback_error:
                rollback_errors.append(str(rollback_error))
        if rollback_errors:
            raise VisualPipelineOrchestrationError(
                "legacy recovery rollback failed: " + "; ".join(rollback_errors)
            )
        raise


def _proposal_identity(root: Path, proposal_path: Path) -> dict[str, object]:
    if not proposal_path.is_file() or proposal_path.stat().st_size > _MAX_PROPOSAL_BYTES:
        raise VisualPipelineOrchestrationError("worker proposal is missing or too large")
    document = _load_json(proposal_path, "worker proposal")
    forged = sorted(_FORBIDDEN_PROPOSAL_KEYS.intersection(document))
    if forged:
        raise VisualPipelineOrchestrationError(
            "proposal must not self-report worker identity: " + ", ".join(forged)
        )
    return _identity(root, proposal_path)


def _worker_id(dispatcher: VisualDispatchAdapter, session_id: str) -> str:
    name = getattr(dispatcher, "name", "subscription-worker")
    safe_name = re.sub(r"[^A-Za-z0-9._-]+", "-", str(name)).strip("-")
    return f"{safe_name or 'subscription-worker'}:{session_id}"


def _execution_receipt_path(job_root: Path, phase: str) -> Path:
    return job_root / "receipts" / f"{phase}.json"


def _execution_attempt_root(job_root: Path, phase: str, attempt: int) -> Path:
    return job_root / "receipts" / f"{phase}.attempts" / f"attempt-{attempt:03d}"


def _pid_is_active(pid: int) -> bool:
    if pid <= 0:
        return False
    try:
        os.kill(pid, 0)
    except OSError:
        return False
    return True


def _prepared_attempts(job_root: Path, phase: str) -> list[tuple[int, Path]]:
    root = job_root / "receipts" / f"{phase}.attempts"
    attempts: list[tuple[int, Path]] = []
    if root.is_dir():
        for path in root.iterdir():
            match = re.fullmatch(r"attempt-(\d{3})", path.name)
            if match and path.is_dir() and (path / "PREPARE.json").is_file():
                attempts.append((int(match.group(1)), path))
    return sorted(attempts)


def _preserve_failed_attempt(
    episode_root: Path,
    attempt_root: Path,
    *,
    proposal_path: Path,
    reason: str,
    returncode: int | None = None,
) -> None:
    failure_path = attempt_root / "FAILURE.json"
    if failure_path.is_file():
        return
    evidence: dict[str, object] | None = None
    if proposal_path.is_file():
        evidence_path = attempt_root / "evidence" / "proposal.json"
        evidence_path.parent.mkdir(parents=True, exist_ok=True)
        if evidence_path.exists():
            raise VisualPipelineOrchestrationError(
                "failed execution evidence path already conflicts"
            )
        os.replace(proposal_path, evidence_path)
        evidence = _identity(episode_root, evidence_path)
    failure: dict[str, object] = {
        "contract": EXECUTION_FAILURE_CONTRACT,
        "prepare": _identity(episode_root, attempt_root / "PREPARE.json"),
        "reason": reason,
        "returncode": returncode,
        "proposal_evidence": evidence,
    }
    failure["content_hash"] = _content_hash(failure)
    _write_json(failure_path, failure)


def _reject_completed_execution(
    episode_root: Path,
    job_root: Path,
    *,
    cut_id: str,
    revision_id: str,
    phase: str,
    role: str,
    proposal_path: Path,
    reason: str,
) -> None:
    """Retire a completed receipt whose proposal failed a downstream trusted gate."""

    receipt_path = _execution_receipt_path(job_root, phase)
    receipt = _load_execution_receipt(
        episode_root,
        job_root,
        cut_id=cut_id,
        revision_id=revision_id,
        phase=phase,
        role=role,
        proposal_path=proposal_path,
    )
    if receipt is None:
        raise VisualPipelineOrchestrationError(
            f"cannot reject {phase}: canonical execution receipt is missing"
        )
    prepare = receipt.get("prepare")
    if not isinstance(prepare, dict) or not isinstance(prepare.get("path"), str):
        raise VisualPipelineOrchestrationError(
            f"cannot reject {phase}: prepare identity is invalid"
        )
    prepare_path = (episode_root / str(prepare["path"])).resolve()
    attempt_root = prepare_path.parent
    attempts_root = (job_root / "receipts" / f"{phase}.attempts").resolve()
    if (
        not attempt_root.is_relative_to(attempts_root)
        or not re.fullmatch(r"attempt-\d{3}", attempt_root.name)
        or prepare_path.name != "PREPARE.json"
        or _identity(episode_root, prepare_path) != prepare
    ):
        raise VisualPipelineOrchestrationError(
            f"cannot reject {phase}: prepare lineage escaped its attempt"
        )
    failure_path = attempt_root / "FAILURE.json"
    proposal_evidence = attempt_root / "evidence" / "proposal.json"
    receipt_evidence = attempt_root / "evidence" / "execution-receipt.json"
    if failure_path.exists() or proposal_evidence.exists() or receipt_evidence.exists():
        raise VisualPipelineOrchestrationError(
            f"cannot reject {phase}: failure evidence already conflicts"
        )
    if not proposal_path.is_file() or not receipt_path.is_file():
        raise VisualPipelineOrchestrationError(
            f"cannot reject {phase}: completed evidence is incomplete"
        )

    proposal_evidence.parent.mkdir(parents=True, exist_ok=True)
    moved: list[tuple[Path, Path]] = []
    try:
        os.replace(receipt_path, receipt_evidence)
        moved.append((receipt_path, receipt_evidence))
        os.replace(proposal_path, proposal_evidence)
        moved.append((proposal_path, proposal_evidence))
        failure: dict[str, object] = {
            "contract": EXECUTION_FAILURE_CONTRACT,
            "prepare": prepare,
            "reason": reason,
            "returncode": None,
            "proposal_evidence": _identity(episode_root, proposal_evidence),
        }
        failure["content_hash"] = _content_hash(failure)
        _write_json(failure_path, failure)
    except BaseException:
        rollback_errors: list[str] = []
        for source, destination in reversed(moved):
            try:
                os.replace(destination, source)
            except OSError as rollback_error:
                rollback_errors.append(str(rollback_error))
        if rollback_errors:
            raise VisualPipelineOrchestrationError(
                f"{phase} rejection rollback failed: " + "; ".join(rollback_errors)
            )
        raise


def _prepare_execution_attempt(
    episode_root: Path,
    job_root: Path,
    *,
    cut_id: str,
    revision_id: str,
    phase: str,
    role: str,
    prompt: str,
    phase_input_path: Path,
    proposal_path: Path,
) -> Path:
    attempts = _prepared_attempts(job_root, phase)
    if not attempts and proposal_path.is_file():
        # Older hosts could leave a proposal before any durable dispatch marker.
        # Preserve those bytes as unauthorised evidence, then start a fresh
        # execution.  The orphan is never accepted or silently overwritten.
        legacy_root = _execution_attempt_root(job_root, phase, 1)
        legacy_prepare: dict[str, object] = {
            "contract": EXECUTION_PREPARE_CONTRACT,
            "episode_id": episode_root.name,
            "cut_id": cut_id,
            "revision_id": revision_id,
            "phase": phase,
            "role": role,
            "attempt": 1,
            "orchestrator_pid": os.getpid(),
            "prompt_sha256": hashlib.sha256(prompt.encode("utf-8")).hexdigest(),
            "phase_input": _identity(episode_root, phase_input_path),
            "proposal_path": proposal_path.resolve().relative_to(episode_root).as_posix(),
        }
        legacy_prepare["content_hash"] = _content_hash(legacy_prepare)
        _write_json(legacy_root / "PREPARE.json", legacy_prepare)
        _preserve_failed_attempt(
            episode_root,
            legacy_root,
            proposal_path=proposal_path,
            reason="legacy orphan proposal had no durable dispatch marker",
        )
        attempts = [(1, legacy_root)]
    if attempts:
        _, latest = attempts[-1]
        if not (latest / "FAILURE.json").is_file():
            prepare = _load_json(latest / "PREPARE.json", f"{phase} prepare receipt")
            pid = prepare.get("orchestrator_pid")
            if not isinstance(pid, int) or isinstance(pid, bool):
                raise VisualPipelineOrchestrationError(
                    f"{phase} prepare receipt has an invalid process identity"
                )
            if _pid_is_active(pid):
                raise VisualPipelineOrchestrationError(
                    f"{phase} has an active unresolved execution attempt"
                )
            _preserve_failed_attempt(
                episode_root,
                latest,
                proposal_path=proposal_path,
                reason="orchestrator process ended before completion receipt",
            )
    next_attempt = (attempts[-1][0] + 1) if attempts else 1
    attempt_root = _execution_attempt_root(job_root, phase, next_attempt)
    prepare: dict[str, object] = {
        "contract": EXECUTION_PREPARE_CONTRACT,
        "episode_id": episode_root.name,
        "cut_id": cut_id,
        "revision_id": revision_id,
        "phase": phase,
        "role": role,
        "attempt": next_attempt,
        "orchestrator_pid": os.getpid(),
        "prompt_sha256": hashlib.sha256(prompt.encode("utf-8")).hexdigest(),
        "phase_input": _identity(episode_root, phase_input_path),
        "proposal_path": proposal_path.resolve().relative_to(episode_root).as_posix(),
    }
    prepare["content_hash"] = _content_hash(prepare)
    _write_json(attempt_root / "PREPARE.json", prepare)
    return attempt_root


def _load_execution_receipt(
    episode_root: Path,
    job_root: Path,
    *,
    cut_id: str,
    revision_id: str,
    phase: str,
    role: str,
    proposal_path: Path,
) -> dict[str, object] | None:
    path = _execution_receipt_path(job_root, phase)
    if not path.exists():
        return None
    receipt = _load_json(path, f"{phase} execution receipt")
    claimed_hash = receipt.pop("content_hash", None)
    if claimed_hash != _content_hash(receipt):
        raise VisualPipelineOrchestrationError(f"{phase} execution receipt hash mismatch")
    if (
        receipt.get("contract") != EXECUTION_RECEIPT_CONTRACT
        or receipt.get("episode_id") != episode_root.name
        or receipt.get("cut_id") != cut_id
        or receipt.get("revision_id") != revision_id
        or receipt.get("phase") != phase
        or receipt.get("role") != role
    ):
        raise VisualPipelineOrchestrationError(f"{phase} execution receipt role mismatch")
    worker = receipt.get("worker_identity")
    if not isinstance(worker, dict) or set(worker) != {
        "worker_id",
        "execution_id",
        "role",
        "session_id",
    }:
        raise VisualPipelineOrchestrationError(f"{phase} execution receipt worker is invalid")
    if worker.get("role") != role:
        raise VisualPipelineOrchestrationError(f"{phase} execution receipt worker role mismatch")
    if any(not isinstance(value, str) or not value for value in worker.values()):
        raise VisualPipelineOrchestrationError(f"{phase} execution receipt worker is incomplete")
    if not _SAFE_SESSION.fullmatch(str(worker["session_id"])):
        raise VisualPipelineOrchestrationError(f"{phase} execution receipt session is unsafe")
    expected_proposal = _identity(episode_root, proposal_path)
    if receipt.get("proposal") != expected_proposal:
        raise VisualPipelineOrchestrationError(f"{phase} execution receipt proposal hash mismatch")
    for stream_name in ("prepare", "stdout", "stderr", "phase_input"):
        identity = receipt.get(stream_name)
        if not isinstance(identity, dict) or not isinstance(identity.get("path"), str):
            raise VisualPipelineOrchestrationError(
                f"{phase} execution receipt {stream_name} identity is invalid"
            )
        stream_path = (episode_root / str(identity["path"])).resolve()
        if not stream_path.is_relative_to(episode_root) or not stream_path.is_file():
            raise VisualPipelineOrchestrationError(
                f"{phase} execution receipt {stream_name} escaped the episode"
            )
        if _identity(episode_root, stream_path) != identity:
            raise VisualPipelineOrchestrationError(
                f"{phase} execution receipt {stream_name} hash mismatch"
            )
    receipt["content_hash"] = claimed_hash
    return receipt


def _phase_contract(phase: str) -> str:
    phase_kind = phase.split("-", 1)[0]
    if phase_kind in {"director", "director_replan"}:
        return visual_pipeline.DIRECTOR_PLAN_CONTRACT
    if phase_kind == "dp":
        return visual_pipeline.DP_FULFILLMENT_CONTRACT
    if phase_kind == "refinement_decision":
        return visual_pipeline.REFINEMENT_DECISION_CONTRACT
    if phase_kind == "semantic_audit":
        return visual_pipeline.SEMANTIC_AUDIT_CONTRACT
    raise VisualPipelineOrchestrationError(f"unknown proposal phase: {phase}")


def _phase_context(
    phase: str,
    *,
    revision_id: str,
    work: object,
    director: object | None,
    dp: object | None,
    materializations: Sequence[Mapping[str, object]],
    attempt: int = 1,
    semantic_refinement: object | None = None,
    refinement_decision: object | None = None,
    asset_authority: Mapping[str, object] | None = None,
) -> dict[str, object]:
    def artifact(selection: object | None) -> object:
        if selection is None:
            return None
        identity = getattr(selection, "identity", None)
        document = getattr(selection, "document", None)
        if not callable(identity) or not isinstance(document, dict):
            raise VisualPipelineOrchestrationError("core artifact selection is invalid")
        return {"identity": identity(), "document": document}

    return {
        "contract": "podcast-highlight-visual-worker-input-v1",
        "phase": phase,
        "revision_id": revision_id,
        "attempt": attempt,
        "work_packet": artifact(work),
        "director_plan": artifact(director),
        "dp_fulfillment": artifact(dp),
        "semantic_refinement": artifact(semantic_refinement),
        "refinement_decision": artifact(refinement_decision),
        "asset_authority": dict(asset_authority) if asset_authority is not None else None,
        "materializations": list(materializations),
    }


def _skill_path(role: str) -> Path:
    repo_root = Path(__file__).resolve().parent.parent
    name = "brook-director" if role == "director" else "brook-dp"
    path = repo_root / ".claude" / "skills" / name / "SKILL.md"
    if not path.is_file():
        raise VisualPipelineOrchestrationError(f"required skill is missing: {path}")
    return path


def _phase_prompt(request: DispatchRequest, phase_input_path: Path) -> str:
    phase_kind = request.phase.split("-", 1)[0]
    action = {
        "director": "Create the Director visual-intent plan from every exact transcript cue.",
        "director_replan": (
            "Revise only the audit-failed Director events identified for replan; preserve every "
            "other immutable transcript event and do not fabricate unavailable evidence."
        ),
        "dp": "Fulfil every add_visual event with concrete candidates and selected assets/renders.",
        "refinement_decision": (
            "Triage every exact non-match finding as retry_dp or director_replan. Use "
            "director_replan whenever the requested source class is unavailable or unverifiable."
        ),
        "semantic_audit": (
            "Re-open your original Director intent, inspect every DP-selected visual, and audit "
            "exact semantic match. Mark mismatch/uncertain honestly; trusted acceptance will fail "
            "closed rather than coercing a pass."
        ),
    }[phase_kind]
    worker_intro = (
        f"You are the {request.role} subscription worker for Podcast Highlight visual production."
    )
    if phase_kind == "dp":
        media_policy = """- Never download, generate, or write media, preview files,
  provenance, or receipts.
- Stock/provided candidates may contain only candidate_id, visual_summary, and an
  authority_asset_id exposed by the phase input's immutable asset_authority inventory.
- mode=stock is mandatory for licensed_stock/official_archive/public_domain authority;
  mode=provided_asset is allowed only when that authority row's source_class starts
  with provided_. Never relabel stock as provided_asset to satisfy an event.
- If no authority asset truthfully matches an event, use a truthful HyperFrames
  abstraction or retain A-roll according to the contract; do not force an unrelated asset.
- HyperFrames candidates must be exact spec-only objects: candidate_id, visual_summary,
  component, render_params, and render_spec_sha256. Trusted orchestration renders them
  after exit."""
    else:
        media_policy = """- Do not download, generate, or write media, preview files, provenance,
  or receipts during this phase."""
    return f"""{worker_intro}

{action}

Mandatory instructions:
- Read the complete skill at: {_skill_path(request.role)}
- Read the deterministic, read-only phase input at: {phase_input_path}
- Write exactly one JSON proposal to: {request.proposal_path}
- Proposal contract: {request.proposal_contract}
- Do not include content_hash, worker_id, worker_identity, worker_execution, execution_id,
  session_id, or role. Trusted orchestration supplies execution identity after your process exits.
- Do not write canonical DIRECTOR-WORK/PLAN, DP-FULFILLMENT, SEMANTIC-AUDIT, `_broll.json`,
  `_titles.json`, review manifests, or Resolve state.
{media_policy}
- Do not request --add-dir or broader sandbox access.
- Do not call run_short_director.py or run_short_broll.py as a substitute for Director/DP work.
- Finish only after the proposal file exists and is strict JSON. Do not merely print the JSON.
"""


def _execute_phase(
    episode_root: Path,
    job_root: Path,
    *,
    cut_id: str,
    revision_id: str,
    phase: str,
    role: str,
    dispatcher: VisualDispatchAdapter,
    phase_context: Mapping[str, object],
    resume_session_id: str | None,
    forbidden_session_id: str | None,
    resume: bool,
) -> tuple[Path, dict[str, str]]:
    if phase.split("-", 1)[0] not in _PHASES:
        raise VisualPipelineOrchestrationError(f"unknown visual worker phase: {phase}")
    session_name = "director-session" if role == "director" else "dp-session"
    workdir = (job_root / "workers" / session_name).resolve()
    if not workdir.is_relative_to(episode_root):
        raise VisualPipelineOrchestrationError("worker job path escapes episode root")
    workdir.mkdir(parents=True, exist_ok=True)
    proposal_path = workdir / f"{phase}-proposal.json"
    phase_input_path = workdir / f"{phase}-input.json"
    _write_json(phase_input_path, phase_context)

    existing = _load_execution_receipt(
        episode_root,
        job_root,
        cut_id=cut_id,
        revision_id=revision_id,
        phase=phase,
        role=role,
        proposal_path=proposal_path,
    )
    if existing is not None:
        if not resume:
            raise VisualPipelineOrchestrationError(
                f"{phase} already has an immutable execution receipt; resume is disabled"
            )
        worker = existing["worker_identity"]
        assert isinstance(worker, dict)
        return proposal_path, {key: str(value) for key, value in worker.items()}
    request = DispatchRequest(
        phase=phase,
        role=role,
        prompt="",
        working_directory=workdir,
        proposal_path=proposal_path,
        proposal_contract=_phase_contract(phase),
        resume_session_id=resume_session_id,
    )
    request = DispatchRequest(
        phase=request.phase,
        role=request.role,
        prompt=_phase_prompt(request, phase_input_path),
        working_directory=request.working_directory,
        proposal_path=request.proposal_path,
        proposal_contract=request.proposal_contract,
        resume_session_id=request.resume_session_id,
    )
    phase_input_before = _identity(episode_root, phase_input_path)
    attempt_root = _prepare_execution_attempt(
        episode_root,
        job_root,
        cut_id=cut_id,
        revision_id=revision_id,
        phase=phase,
        role=role,
        prompt=request.prompt,
        phase_input_path=phase_input_path,
        proposal_path=proposal_path,
    )
    try:
        result = dispatcher.dispatch(request)
    except Exception:
        if not proposal_path.exists():
            _preserve_failed_attempt(
                episode_root,
                attempt_root,
                proposal_path=proposal_path,
                reason="dispatcher failed before producing a proposal",
            )
        raise
    if result.returncode != 0:
        _preserve_failed_attempt(
            episode_root,
            attempt_root,
            proposal_path=proposal_path,
            reason="worker process returned nonzero",
            returncode=result.returncode,
        )
        raise VisualPipelineOrchestrationError(f"{phase} worker failed with {result.returncode}")
    try:
        evidence_dir = attempt_root / "evidence"
        stdout_path = evidence_dir / "stdout.jsonl"
        stderr_path = evidence_dir / "stderr.txt"
        _atomic_write(stdout_path, result.stdout.encode("utf-8"))
        _atomic_write(stderr_path, result.stderr.encode("utf-8"))
        if not _SAFE_SESSION.fullmatch(result.session_id):
            raise VisualPipelineOrchestrationError(
                f"{phase} returned an unsafe session identity"
            )
        if resume_session_id is not None and result.session_id != resume_session_id:
            raise VisualPipelineOrchestrationError(
                "semantic audit did not resume the original Director session"
            )
        if forbidden_session_id is not None and result.session_id == forbidden_session_id:
            raise VisualPipelineOrchestrationError(
                "DP session must be distinct from Director session"
            )
        if _identity(episode_root, phase_input_path) != phase_input_before:
            raise VisualPipelineOrchestrationError(
                f"{phase} modified its deterministic phase input"
            )
        proposal_identity = _proposal_identity(episode_root, proposal_path)

        execution_id = str(uuid.uuid4())
        worker_identity = {
            "worker_id": _worker_id(dispatcher, result.session_id),
            "execution_id": execution_id,
            "role": role,
            "session_id": result.session_id,
        }
        receipt: dict[str, object] = {
            "contract": EXECUTION_RECEIPT_CONTRACT,
            "episode_id": episode_root.name,
            "cut_id": cut_id,
            "revision_id": revision_id,
            "phase": phase,
        "role": role,
        "worker_identity": worker_identity,
        "prepare": _identity(episode_root, attempt_root / "PREPARE.json"),
        "prompt_sha256": hashlib.sha256(request.prompt.encode("utf-8")).hexdigest(),
            "phase_input": phase_input_before,
            "proposal": proposal_identity,
            "stdout": _identity(episode_root, stdout_path),
            "stderr": _identity(episode_root, stderr_path),
        }
        receipt["content_hash"] = _content_hash(receipt)
        _write_json(_execution_receipt_path(job_root, phase), receipt)
        return proposal_path, worker_identity
    except Exception:
        _preserve_failed_attempt(
            episode_root,
            attempt_root,
            proposal_path=proposal_path,
            reason="completed worker output failed trusted verification",
            returncode=result.returncode,
        )
        raise


def _hydrate_dp_phase_proposal(
    episode_root: Path,
    job_root: Path,
    *,
    cut_id: str,
    revision_id: str,
    phase: str,
    attempt: int,
    raw_proposal_path: Path,
    editorial_master: object | None,
    hydrator: Callable[..., Mapping[str, object]],
    runtime_root: str | Path | None,
) -> Path:
    output_path = (job_root / "trusted" / f"{phase}-proposal.json").resolve()
    if not output_path.is_relative_to(episode_root):
        raise VisualPipelineOrchestrationError("trusted DP proposal path escapes episode root")
    try:
        hydrated = hydrator(
            episode_root,
            cut_id=cut_id,
            revision_id=revision_id,
            attempt=attempt,
            proposal_path=raw_proposal_path,
            output_path=output_path,
            editorial_master=editorial_master,
            runtime_root=runtime_root,
        )
    except (TrustedRenderError, OSError, ValueError, TypeError) as error:
        raise VisualPipelineOrchestrationError(
            f"trusted {phase} candidate hydration failed: {error}"
        ) from error
    if not isinstance(hydrated, Mapping):
        raise VisualPipelineOrchestrationError("trusted DP hydrator returned a non-object")
    persisted = _load_json(output_path, "trusted hydrated DP proposal")
    if persisted != dict(hydrated):
        raise VisualPipelineOrchestrationError("trusted DP hydrator output identity drift")
    return output_path


def _status(root: Path, cut_id: str, editorial_master: object | None) -> dict[str, object]:
    value = visual_pipeline.visual_pipeline_status(
        root, cut_id=cut_id, editorial_master=editorial_master
    )
    if not isinstance(value, dict) or not isinstance(value.get("status"), str):
        raise VisualPipelineOrchestrationError("visual pipeline returned an invalid status")
    return value


def _current_ready(status: Mapping[str, object]) -> bool:
    return (
        status.get("status") == "ready_to_materialize"
        and isinstance(status.get("current_revision_id"), str)
        and status.get("pending_revision_id") == status.get("current_revision_id")
    )


def run_visual_pipeline(
    episode_root: str | Path,
    *,
    cut_id: str,
    revision_request: str | Path | None = None,
    dispatcher: VisualDispatchAdapter | None = None,
    editorial_master: object | None = None,
    resume: bool = True,
    proposal_hydrator: Callable[..., Mapping[str, object]] | None = None,
    hyperframes_runtime_root: str | Path | None = None,
):
    """Run or resume one generation and return its freshly verified selection.

    ``revision_request`` must be an immutable episode-local request snapshot.  It
    is mandatory for a feedback-driven generation and intentionally has no
    "latest request" discovery fallback.  ``None`` is valid only for the base
    generation, as enforced by the deterministic core.
    """

    root = Path(episode_root).resolve()
    if not root.is_dir():
        raise VisualPipelineOrchestrationError(f"episode root does not exist: {root}")
    cut_id = _safe_cut_id(cut_id)
    request_path = (
        _safe_local_file(root, revision_request, "revision_request")
        if revision_request is not None
        else None
    )
    dispatch_adapter = dispatcher or CodexExecDispatcher()
    trusted_hydrator = proposal_hydrator or hydrate_dp_proposal

    initial = _status(root, cut_id, editorial_master)
    if request_path is None and _current_ready(initial):
        return visual_pipeline.verify_visual_pipeline(
            root, cut_id=cut_id, editorial_master=editorial_master
        )

    work = visual_pipeline.init_visual_work_packet(
        root,
        cut_id=cut_id,
        revision_request=request_path,
        editorial_master=editorial_master,
    )
    work_document = getattr(work, "document", None)
    if not isinstance(work_document, dict):
        raise VisualPipelineOrchestrationError("core returned an invalid work packet")
    revision_id = _safe_revision_id(work_document.get("revision_id"))
    job_root = (root / _JOB_ROOT / cut_id / "jobs" / revision_id).resolve()
    if not job_root.is_relative_to(root):
        raise VisualPipelineOrchestrationError("content-addressed job path escapes episode root")
    job_root.mkdir(parents=True, exist_ok=True)
    if request_path is not None:
        _recover_legacy_missing_prepare_chain(
            root,
            job_root,
            cut_id=cut_id,
            revision_id=revision_id,
            state=initial,
            editorial_master=editorial_master,
        )

    for _step in range(16):
        state = _status(root, cut_id, editorial_master)
        if state.get("pending_revision_id") not in (revision_id, None):
            raise VisualPipelineOrchestrationError(
                "another pending visual revision replaced this generation"
            )
        status_name = state["status"]
        if status_name == "invalid":
            raise VisualPipelineOrchestrationError(
                f"visual pipeline is invalid: {state.get('error', 'unknown contract failure')}"
            )
        if status_name == "awaiting_director":
            context = _phase_context(
                "director",
                revision_id=revision_id,
                work=work,
                director=None,
                dp=None,
                materializations=(),
            )
            proposal, identity = _execute_phase(
                root,
                job_root,
                cut_id=cut_id,
                revision_id=revision_id,
                phase="director",
                role="director",
                dispatcher=dispatch_adapter,
                phase_context=context,
                resume_session_id=None,
                forbidden_session_id=None,
                resume=resume,
            )
            visual_pipeline.accept_director_plan(
                root,
                cut_id=cut_id,
                revision_id=revision_id,
                proposal=proposal,
                worker_identity=identity,
                execution_receipt=_identity(
                    root, _execution_receipt_path(job_root, "director")
                ),
                editorial_master=editorial_master,
            )
            continue
        director_receipt = _load_execution_receipt(
            root,
            job_root,
            cut_id=cut_id,
            revision_id=revision_id,
            phase="director",
            role="director",
            proposal_path=job_root / "workers" / "director-session" / "director-proposal.json",
        )
        if director_receipt is None:
            raise VisualPipelineOrchestrationError(
                "canonical Director plan has no trusted orchestration receipt"
            )
        director_identity = director_receipt["worker_identity"]
        assert isinstance(director_identity, dict)
        director_session = str(director_identity["session_id"])
        active_attempt = int(state.get("active_dp_attempt", 1))
        director_attempt = active_attempt
        if status_name == "awaiting_dp_refinement":
            director_attempt = int(state.get("next_dp_attempt", active_attempt + 1))
        director_kwargs: dict[str, object] = {
            "cut_id": cut_id,
            "revision_id": revision_id,
            "editorial_master": editorial_master,
        }
        if director_attempt > 1:
            director_kwargs["attempt"] = director_attempt
        director = visual_pipeline.load_director_plan(root, **director_kwargs)
        if status_name == "awaiting_dp":
            asset_authority = visual_pipeline.load_asset_authority_projection(
                root,
                cut_id=cut_id,
                revision_id=revision_id,
                attempt=1,
                editorial_master=editorial_master,
            )
            context = _phase_context(
                "dp",
                revision_id=revision_id,
                work=work,
                director=director,
                dp=None,
                materializations=(),
                asset_authority=asset_authority,
            )
            reused_dp_execution = _execution_receipt_path(job_root, "dp").is_file()
            proposal, identity = _execute_phase(
                root,
                job_root,
                cut_id=cut_id,
                revision_id=revision_id,
                phase="dp",
                role="dp",
                dispatcher=dispatch_adapter,
                phase_context=context,
                resume_session_id=None,
                forbidden_session_id=director_session,
                resume=resume,
            )
            try:
                trusted_proposal = _hydrate_dp_phase_proposal(
                    root,
                    job_root,
                    cut_id=cut_id,
                    revision_id=revision_id,
                    phase="dp",
                    attempt=1,
                    raw_proposal_path=proposal,
                    editorial_master=editorial_master,
                    hydrator=trusted_hydrator,
                    runtime_root=hyperframes_runtime_root,
                )
            except VisualPipelineOrchestrationError as error:
                _reject_completed_execution(
                    root,
                    job_root,
                    cut_id=cut_id,
                    revision_id=revision_id,
                    phase="dp",
                    role="dp",
                    proposal_path=proposal,
                    reason=f"trusted DP hydration rejected completed proposal: {error}",
                )
                if reused_dp_execution:
                    continue
                raise
            visual_pipeline.accept_dp_fulfillment(
                root,
                cut_id=cut_id,
                revision_id=revision_id,
                proposal=trusted_proposal,
                worker_identity=identity,
                worker_proposal=proposal,
                execution_receipt=_identity(
                    root, _execution_receipt_path(job_root, "dp")
                ),
                editorial_master=editorial_master,
            )
            continue
        dp_phase = "dp" if active_attempt == 1 else f"dp-{active_attempt:03d}"
        dp_receipt = _load_execution_receipt(
            root,
            job_root,
            cut_id=cut_id,
            revision_id=revision_id,
            phase=dp_phase,
            role="dp",
            proposal_path=(
                job_root / "workers" / "dp-session" / f"{dp_phase}-proposal.json"
            ),
        )
        if dp_receipt is None:
            raise VisualPipelineOrchestrationError(
                "canonical DP fulfillment has no trusted orchestration receipt"
            )
        dp_identity = dp_receipt["worker_identity"]
        assert isinstance(dp_identity, dict)
        if str(dp_identity["session_id"]) == director_session:
            raise VisualPipelineOrchestrationError("DP session matches the Director session")
        dp = visual_pipeline.load_dp_fulfillment(
            root,
            cut_id=cut_id,
            revision_id=revision_id,
            editorial_master=editorial_master,
        )
        if status_name == "awaiting_refinement_decision":
            refinement = visual_pipeline.load_semantic_refinement(
                root,
                cut_id=cut_id,
                revision_id=revision_id,
                attempt=active_attempt,
                editorial_master=editorial_master,
            )
            phase = f"refinement_decision-{active_attempt:03d}"
            context = _phase_context(
                phase,
                revision_id=revision_id,
                work=work,
                director=director,
                dp=dp,
                materializations=visual_pipeline.load_visual_materializations(
                    root,
                    cut_id=cut_id,
                    revision_id=revision_id,
                    attempt=active_attempt,
                    editorial_master=editorial_master,
                ),
                attempt=active_attempt,
                semantic_refinement=refinement,
            )
            proposal, identity = _execute_phase(
                root,
                job_root,
                cut_id=cut_id,
                revision_id=revision_id,
                phase=phase,
                role="director",
                dispatcher=dispatch_adapter,
                phase_context=context,
                resume_session_id=director_session,
                forbidden_session_id=str(dp_identity["session_id"]),
                resume=resume,
            )
            visual_pipeline.accept_refinement_decision(
                root,
                cut_id=cut_id,
                revision_id=revision_id,
                attempt=active_attempt,
                proposal=proposal,
                worker_identity=identity,
                execution_receipt=_identity(
                    root, _execution_receipt_path(job_root, phase)
                ),
                editorial_master=editorial_master,
            )
            continue
        if status_name == "requires_director_replan":
            refinement = visual_pipeline.load_semantic_refinement(
                root,
                cut_id=cut_id,
                revision_id=revision_id,
                attempt=active_attempt,
                editorial_master=editorial_master,
            )
            decision = visual_pipeline.load_refinement_decision(
                root,
                cut_id=cut_id,
                revision_id=revision_id,
                attempt=active_attempt,
                editorial_master=editorial_master,
            )
            next_attempt = int(state.get("next_director_attempt", active_attempt + 1))
            phase = f"director_replan-{next_attempt:03d}"
            context = _phase_context(
                phase,
                revision_id=revision_id,
                work=work,
                director=director,
                dp=dp,
                materializations=visual_pipeline.load_visual_materializations(
                    root,
                    cut_id=cut_id,
                    revision_id=revision_id,
                    attempt=active_attempt,
                    editorial_master=editorial_master,
                ),
                attempt=active_attempt,
                semantic_refinement=refinement,
                refinement_decision=decision,
            )
            proposal, identity = _execute_phase(
                root,
                job_root,
                cut_id=cut_id,
                revision_id=revision_id,
                phase=phase,
                role="director",
                dispatcher=dispatch_adapter,
                phase_context=context,
                resume_session_id=director_session,
                forbidden_session_id=str(dp_identity["session_id"]),
                resume=resume,
            )
            visual_pipeline.accept_director_replan(
                root,
                cut_id=cut_id,
                revision_id=revision_id,
                attempt=next_attempt,
                proposal=proposal,
                worker_identity=identity,
                execution_receipt=_identity(
                    root, _execution_receipt_path(job_root, phase)
                ),
                editorial_master=editorial_master,
            )
            continue
        if status_name == "awaiting_dp_refinement":
            next_attempt = int(state["next_dp_attempt"])
            decision = visual_pipeline.load_refinement_decision(
                root,
                cut_id=cut_id,
                revision_id=revision_id,
                attempt=active_attempt,
                editorial_master=editorial_master,
            )
            phase = f"dp-{next_attempt:03d}"
            asset_authority = visual_pipeline.load_asset_authority_projection(
                root,
                cut_id=cut_id,
                revision_id=revision_id,
                attempt=next_attempt,
                editorial_master=editorial_master,
            )
            context = _phase_context(
                phase,
                revision_id=revision_id,
                work=work,
                director=director,
                dp=dp,
                materializations=visual_pipeline.load_visual_materializations(
                    root,
                    cut_id=cut_id,
                    revision_id=revision_id,
                    attempt=active_attempt,
                    editorial_master=editorial_master,
                ),
                attempt=next_attempt,
                semantic_refinement=visual_pipeline.load_semantic_refinement(
                    root,
                    cut_id=cut_id,
                    revision_id=revision_id,
                    attempt=active_attempt,
                    editorial_master=editorial_master,
                ),
                refinement_decision=decision,
                asset_authority=asset_authority,
            )
            reused_dp_execution = _execution_receipt_path(job_root, phase).is_file()
            proposal, identity = _execute_phase(
                root,
                job_root,
                cut_id=cut_id,
                revision_id=revision_id,
                phase=phase,
                role="dp",
                dispatcher=dispatch_adapter,
                phase_context=context,
                resume_session_id=None,
                forbidden_session_id=director_session,
                resume=resume,
            )
            try:
                trusted_proposal = _hydrate_dp_phase_proposal(
                    root,
                    job_root,
                    cut_id=cut_id,
                    revision_id=revision_id,
                    phase=phase,
                    attempt=next_attempt,
                    raw_proposal_path=proposal,
                    editorial_master=editorial_master,
                    hydrator=trusted_hydrator,
                    runtime_root=hyperframes_runtime_root,
                )
            except VisualPipelineOrchestrationError as error:
                _reject_completed_execution(
                    root,
                    job_root,
                    cut_id=cut_id,
                    revision_id=revision_id,
                    phase=phase,
                    role="dp",
                    proposal_path=proposal,
                    reason=f"trusted DP hydration rejected completed proposal: {error}",
                )
                if reused_dp_execution:
                    continue
                raise
            visual_pipeline.accept_dp_refinement(
                root,
                cut_id=cut_id,
                revision_id=revision_id,
                attempt=next_attempt,
                proposal=trusted_proposal,
                worker_identity=identity,
                worker_proposal=proposal,
                execution_receipt=_identity(
                    root, _execution_receipt_path(job_root, phase)
                ),
                editorial_master=editorial_master,
            )
            continue
        if status_name == "awaiting_semantic_audit":
            materializations = visual_pipeline.load_visual_materializations(
                root,
                cut_id=cut_id,
                revision_id=revision_id,
                editorial_master=editorial_master,
            )
            audit_phase = (
                "semantic_audit"
                if active_attempt == 1
                else f"semantic_audit-{active_attempt:03d}"
            )
            context = _phase_context(
                audit_phase,
                revision_id=revision_id,
                work=work,
                director=director,
                dp=dp,
                materializations=materializations,
                attempt=active_attempt,
            )
            proposal, identity = _execute_phase(
                root,
                job_root,
                cut_id=cut_id,
                revision_id=revision_id,
                phase=audit_phase,
                role="director",
                dispatcher=dispatch_adapter,
                phase_context=context,
                resume_session_id=director_session,
                forbidden_session_id=str(dp_identity["session_id"]),
                resume=resume,
            )
            director_after_audit = _load_execution_receipt(
                root,
                job_root,
                cut_id=cut_id,
                revision_id=revision_id,
                phase="director",
                role="director",
                proposal_path=(
                    job_root / "workers" / "director-session" / "director-proposal.json"
                ),
            )
            if director_after_audit is None or (
                director_after_audit["worker_identity"] != director_identity
            ):
                raise VisualPipelineOrchestrationError(
                    "Director execution receipt changed during semantic audit"
                )
            if identity["worker_id"] != str(director_identity["worker_id"]):
                raise VisualPipelineOrchestrationError(
                    "semantic audit did not preserve the Director worker identity"
                )
            visual_pipeline.accept_semantic_audit(
                root,
                cut_id=cut_id,
                revision_id=revision_id,
                proposal=proposal,
                worker_identity=identity,
                execution_receipt=_identity(
                    root, _execution_receipt_path(job_root, audit_phase)
                ),
                editorial_master=editorial_master,
            )
            continue
        if status_name == "ready_to_materialize":
            return visual_pipeline.verify_visual_pipeline(
                root,
                cut_id=cut_id,
                revision_id=revision_id,
                editorial_master=editorial_master,
            )
        raise VisualPipelineOrchestrationError(f"unsupported visual status: {status_name}")
    raise VisualPipelineOrchestrationError("visual pipeline did not reach ready state")


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Run or resume Director -> DP -> same-Director semantic audit."
    )
    parser.add_argument("episode_root", type=Path)
    parser.add_argument("--cut-id", required=True)
    parser.add_argument(
        "--revision-request",
        type=Path,
        help="Immutable episode-local request.json; omit only for the base generation.",
    )
    parser.add_argument(
        "--no-resume",
        action="store_true",
        help="Reject existing execution receipts instead of resuming them.",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        result = run_visual_pipeline(
            args.episode_root,
            cut_id=args.cut_id,
            revision_request=args.revision_request,
            resume=not args.no_resume,
        )
    except (
        VisualPipelineOrchestrationError,
        visual_pipeline.HighlightVisualContractError,
    ) as error:
        print(f"VISUAL PIPELINE FAILED: {error}", file=sys.stderr)
        return 2
    lineage = result.lineage()
    print(json.dumps(lineage, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())


__all__ = [
    "CodexExecDispatcher",
    "DispatchRequest",
    "DispatchResult",
    "VisualDispatchAdapter",
    "VisualPipelineOrchestrationError",
    "main",
    "run_visual_pipeline",
]
