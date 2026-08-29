#!/usr/bin/env python3
"""Advance event-scoped Finished Cut Production revisions from Bridge feedback v3."""

from __future__ import annotations

import argparse
import json
import os
import sys
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Literal, Protocol

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from agents.brook.script_video.finished_cut_production import (
    RESOLVE_BINDING_SCHEMA,
    ProductionPaths,
    ProductionResolveConfiguration,
    ProductionStatusView,
    build_production_application,
    build_resolve_configuration,
)

_BINDING_FILE = "resolve-binding.v1.json"

_FEEDBACK_FILE = "finished_review_feedback.v3.json"
_FEEDBACK_SCHEMA = "nakama.finished_cut_review_feedback.v3"
_JOB_CONTRACT = "finished-cut-production-revision.v3"
_ACTIVE_STATUSES = frozenset({"queued", "registered", "pending"})
_JOB_STATUSES = frozenset(
    {
        *_ACTIVE_STATUSES,
        "registering",
        "needs_review",
        "preview_ready",
        "failed",
    }
)
_JOB_FIELDS = frozenset(
    {
        "contract",
        "request_id",
        "status",
        "command_id",
        "production_state",
        "reason_code",
        "requested_at",
        "updated_at",
        "error",
        "episode_id",
        "source_manifest_sha256",
        "release_id",
        "cut_id",
        "event_id",
        "feedback",
    }
)
_MUTABLE_JOB_FIELDS = frozenset(
    {
        "status",
        "command_id",
        "production_state",
        "reason_code",
        "updated_at",
        "error",
    }
)


class ProductionApplication(Protocol):
    """Only the Finished Cut operations this watcher is allowed to call."""

    def request_revision(
        self,
        current_release_ref: str,
        event_id: str,
        feedback: str,
    ) -> str: ...

    def advance(self, command_id: str) -> ProductionStatusView: ...


ApplicationFactory = Callable[[ProductionPaths, str], ProductionApplication]


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _sha256(value: object) -> bool:
    return (
        isinstance(value, str)
        and len(value) == 64
        and all(character in "0123456789abcdef" for character in value)
    )


def _opaque(value: object) -> bool:
    return (
        isinstance(value, str)
        and value == value.strip()
        and 0 < len(value) <= 512
        and not any(character in value for character in "/\\{}[]\r\n\t")
    )


def _command_id(value: object) -> bool:
    if not isinstance(value, str) or not value.startswith("targeted-revision:"):
        return False
    digest = value.removeprefix("targeted-revision:")
    return len(digest) == 32 and all(character in "0123456789abcdef" for character in digest)


def _validate_job(value: object, *, episode_id: str) -> dict[str, object]:
    if not isinstance(value, dict) or set(value) != _JOB_FIELDS:
        raise RuntimeError("Finished Cut revision job fields are invalid")
    if value.get("contract") != _JOB_CONTRACT:
        raise RuntimeError("Finished Cut revision job contract is invalid")
    if value.get("episode_id") != episode_id:
        raise RuntimeError("Finished Cut revision job episode differs")
    request_id = value.get("request_id")
    if (
        not isinstance(request_id, str)
        or not request_id.startswith("finished-revision:")
        or not _sha256(request_id.removeprefix("finished-revision:"))
    ):
        raise RuntimeError("Finished Cut revision request identity is invalid")
    status = value.get("status")
    if status not in _JOB_STATUSES:
        raise RuntimeError("Finished Cut revision status is invalid")
    for key in ("release_id", "cut_id", "event_id"):
        if not _opaque(value.get(key)):
            raise RuntimeError(f"Finished Cut revision {key} is invalid")
    if not _sha256(value.get("source_manifest_sha256")):
        raise RuntimeError("Finished Cut revision current-manifest identity is invalid")
    feedback = value.get("feedback")
    if not isinstance(feedback, str) or not feedback.strip() or len(feedback) > 10_000:
        raise RuntimeError("Finished Cut revision feedback is invalid")
    command_id = value.get("command_id")
    if status == "queued":
        if command_id is not None:
            raise RuntimeError("queued Finished Cut revision cannot carry a command")
    elif status in {"registered", "pending", "needs_review", "preview_ready", "failed"}:
        if command_id is not None and not _command_id(command_id):
            raise RuntimeError("Finished Cut revision command identity is invalid")
        if status in {"registered", "pending", "preview_ready"} and command_id is None:
            raise RuntimeError("advanced Finished Cut revision is missing its command")
    elif status == "registering" and command_id is not None:
        raise RuntimeError("registering Finished Cut revision cannot pre-author a command")
    return dict(value)


def _load_feedback(path: Path) -> dict[str, object]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as error:
        raise RuntimeError(f"Finished Cut feedback v3 is unreadable: {path}") from error
    if not isinstance(payload, dict) or set(payload) != {"schema", "episode_id", "revisions"}:
        raise RuntimeError("Finished Cut feedback v3 root fields are invalid")
    episode_id = payload.get("episode_id")
    revisions = payload.get("revisions")
    if (
        payload.get("schema") != _FEEDBACK_SCHEMA
        or not _opaque(episode_id)
        or not isinstance(revisions, list)
    ):
        raise RuntimeError("Finished Cut feedback v3 schema is invalid")
    for revision in revisions:
        if not isinstance(revision, dict):
            raise RuntimeError("Finished Cut feedback revision must be an object")
        jobs = revision.get("revision_jobs", [])
        if not isinstance(jobs, list):
            raise RuntimeError("Finished Cut feedback revision_jobs must be a list")
        for job in jobs:
            _validate_job(job, episode_id=str(episode_id))
    return payload


def _atomic_json(path: Path, payload: dict[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(
        "w",
        encoding="utf-8",
        dir=path.parent,
        prefix=f".{path.name}.",
        suffix=".tmp",
        delete=False,
    ) as temporary:
        json.dump(payload, temporary, ensure_ascii=False, indent=2)
        temporary.write("\n")
        temporary.flush()
        os.fsync(temporary.fileno())
        temporary_path = Path(temporary.name)
    os.replace(temporary_path, path)


def pending_revision_jobs(
    episodes_root: Path, *, statuses: frozenset[str] = _ACTIVE_STATUSES
) -> list[dict[str, object]]:
    """Return only active v3 event revisions from each episode's exact feedback file."""

    root = Path(episodes_root).resolve()
    if not root.is_dir():
        raise RuntimeError(f"episodes root is unavailable: {root}")
    pending: list[dict[str, object]] = []
    for episode_dir in sorted(path for path in root.iterdir() if path.is_dir()):
        feedback_path = episode_dir / "highlights" / "review" / _FEEDBACK_FILE
        if not feedback_path.is_file():
            continue
        payload = _load_feedback(feedback_path)
        episode_id = str(payload["episode_id"])
        if episode_id != episode_dir.name:
            raise RuntimeError("Finished Cut feedback v3 is stored under another episode")
        revisions = payload["revisions"]
        if not isinstance(revisions, list):  # pragma: no cover - validated above
            raise AssertionError("validated revisions changed type")
        for revision_index, revision in enumerate(revisions):
            jobs = revision.get("revision_jobs", [])
            for job_index, raw_job in enumerate(jobs):
                job = _validate_job(raw_job, episode_id=episode_id)
                if job["status"] not in statuses:
                    continue
                pending.append(
                    {
                        "episodes_root": root,
                        "episode_dir": episode_dir,
                        "episode_id": episode_id,
                        "feedback_path": feedback_path,
                        "revision_index": revision_index,
                        "job_index": job_index,
                        "request_id": job["request_id"],
                        "job": job,
                    }
                )
    return pending


def _current_job(work: dict[str, object]) -> tuple[dict[str, object], dict[str, object]]:
    feedback_path = Path(work["feedback_path"])
    payload = _load_feedback(feedback_path)
    try:
        revision = payload["revisions"][int(work["revision_index"])]
        raw_job = revision["revision_jobs"][int(work["job_index"])]
    except (IndexError, KeyError, TypeError) as error:
        raise RuntimeError("Finished Cut revision queue position changed") from error
    job = _validate_job(raw_job, episode_id=str(work["episode_id"]))
    if job["request_id"] != work["request_id"]:
        raise RuntimeError("Finished Cut revision request identity changed")
    return payload, job


def _update_job(
    work: dict[str, object],
    updates: dict[str, object],
    *,
    required_status: str,
) -> dict[str, object]:
    if not set(updates) <= _MUTABLE_JOB_FIELDS:
        raise RuntimeError("Finished Cut watcher attempted an unauthorized job mutation")
    payload, current = _current_job(work)
    if current["status"] != required_status:
        raise RuntimeError("Finished Cut revision status changed before update")
    updated = {**current, **updates, "updated_at": _now()}
    _validate_job(updated, episode_id=str(work["episode_id"]))
    revision = payload["revisions"][int(work["revision_index"])]
    revision["revision_jobs"][int(work["job_index"])] = updated
    _atomic_json(Path(work["feedback_path"]), payload)
    return updated


def _claim_registration(work: dict[str, object]) -> bool:
    request_id = str(work["request_id"])
    digest = request_id.removeprefix("finished-revision:")
    claim_dir = Path(work["feedback_path"]).parent / "revision-claims-v3"
    claim_dir.mkdir(parents=True, exist_ok=True)
    claim_path = claim_dir / f"{digest}.json"
    try:
        descriptor = os.open(
            claim_path,
            os.O_WRONLY | os.O_CREAT | os.O_EXCL,
            0o600,
        )
    except FileExistsError:
        return False
    with os.fdopen(descriptor, "w", encoding="utf-8") as claim:
        json.dump(
            {
                "contract": "finished-cut-production-revision-claim.v1",
                "request_id": request_id,
                "claimed_at": _now(),
            },
            claim,
            ensure_ascii=False,
            sort_keys=True,
        )
        claim.write("\n")
        claim.flush()
        os.fsync(claim.fileno())
    return True


def _production_paths(work: dict[str, object]) -> ProductionPaths:
    episode_dir = Path(work["episode_dir"]).resolve()
    return ProductionPaths(
        runtime_root=episode_dir / "highlights" / "finished-cut-production-v1" / "runtime",
        episodes_root=Path(work["episodes_root"]),
    )


def default_application_factory(paths: ProductionPaths, episode_id: str):
    """Compose the real application, Resolve authority included.

    The binding load belongs here rather than in ``run_revision_job``: a test (or
    any other caller) that injects its own factory is deliberately opting out of
    real Resolve, and forcing the file open on that path broke that seam.
    """
    path = paths.runtime_root.parent / _BINDING_FILE
    if not path.is_file():
        raise RuntimeError(f"Resolve binding is missing: {path} (schema {RESOLVE_BINDING_SCHEMA})")
    payload = json.loads(path.read_text(encoding="utf-8"))
    return build_production_application(
        paths,
        episode_id,
        resolve_configuration=build_resolve_configuration(payload, episode_id),
    )


def _resolve_configuration(work: dict[str, object]) -> ProductionResolveConfiguration:
    """Load this episode's Resolve binding; a revision that cannot finish must not start.

    Without a configuration ``build_production_application`` composes the
    dark-install lifecycle, whose transaction and probe seams raise on use.  The
    revision would then advance through its semantic stages and only die once it
    reached Resolve — burning an LLM round and leaving a half-run behind.
    """
    episode_dir = Path(work["episode_dir"]).resolve()
    path = episode_dir / "highlights" / "finished-cut-production-v1" / _BINDING_FILE
    if not path.is_file():
        raise RuntimeError(f"Resolve binding is missing: {path} (schema {RESOLVE_BINDING_SCHEMA})")
    payload = json.loads(path.read_text(encoding="utf-8"))
    return build_resolve_configuration(payload, str(work["episode_id"]))


def _durable_failure(
    work: dict[str, object],
    *,
    required_status: str,
    command_id: str | None,
    reason_code: str,
    error: Exception | str,
) -> None:
    _update_job(
        work,
        {
            "status": "needs_review",
            "command_id": command_id,
            "production_state": "needs_review",
            "reason_code": reason_code,
            "error": str(error)[-1000:],
        },
        required_status=required_status,
    )


def recover_stalled_revisions(episodes_root: Path) -> list[str]:
    """Let a revision that ran out of retries be registered again.

    A stalled revision stops at ``needs_review`` and nothing can move it:
    ``request_correction`` refuses revision runs outright, the watcher only picks
    up queued work, and the once-only registration claim means re-saving the same
    feedback just re-attaches to the same dead row — which is why the reviewer's
    second save changed nothing.

    Recovery has to do both halves together, or it does nothing: retire the claim
    that guarded the dead registration *and* put the job back in the queue.  The
    claim is renamed rather than removed, so why the retry was allowed stays on
    disk.
    """
    recovered: list[str] = []
    for work in pending_revision_jobs(episodes_root, statuses=frozenset({"needs_review"})):
        request_id = str(work["request_id"])
        digest = request_id.removeprefix("finished-revision:")
        claim = Path(work["feedback_path"]).parent / "revision-claims-v3" / f"{digest}.json"
        if claim.is_file():
            claim.rename(claim.with_suffix(f".superseded-{_now().replace(':', '')}.json"))
        _update_job(
            work,
            {
                "status": "queued",
                "command_id": None,
                "production_state": None,
                "reason_code": None,
                "error": None,
            },
            required_status="needs_review",
        )
        recovered.append(request_id)
    return recovered


def run_revision_job(
    work: dict[str, object],
    *,
    application_factory: ApplicationFactory = default_application_factory,
) -> bool:
    """Register once, then advance only the durable targeted revision command."""

    _, job = _current_job(work)
    status = str(job["status"])
    if status not in _ACTIVE_STATUSES:
        return False

    command_id = job.get("command_id")
    if status == "queued":
        if not _claim_registration(work):
            try:
                _durable_failure(
                    work,
                    required_status="queued",
                    command_id=None,
                    reason_code="revision_registration_indeterminate",
                    error="a durable registration claim already exists",
                )
            except RuntimeError:
                pass
            return False
        job = _update_job(
            work,
            {
                "status": "registering",
                "command_id": None,
                "production_state": None,
                "reason_code": None,
                "error": None,
            },
            required_status="queued",
        )
        try:
            application = application_factory(_production_paths(work), str(work["episode_id"]))
        except Exception as error:
            _durable_failure(
                work,
                required_status="registering",
                command_id=None,
                reason_code="production_composition_failed",
                error=error,
            )
            return False
        try:
            command_id = application.request_revision(
                str(job["release_id"]),
                str(job["event_id"]),
                str(job["feedback"]),
            )
        except Exception as error:
            _durable_failure(
                work,
                required_status="registering",
                command_id=None,
                reason_code="revision_registration_failed",
                error=error,
            )
            return False
        if not _command_id(command_id):
            _durable_failure(
                work,
                required_status="registering",
                command_id=None,
                reason_code="revision_command_invalid",
                error="Finished Cut Production returned an invalid command identity",
            )
            return False
        job = _update_job(
            work,
            {
                "status": "registered",
                "command_id": command_id,
                "production_state": "registered",
                "reason_code": None,
                "error": None,
            },
            required_status="registering",
        )
        status = "registered"
    else:
        try:
            application = application_factory(_production_paths(work), str(work["episode_id"]))
        except Exception as error:
            _durable_failure(
                work,
                required_status=status,
                command_id=str(command_id) if _command_id(command_id) else None,
                reason_code="production_composition_failed",
                error=error,
            )
            return False

    if not _command_id(command_id):
        _durable_failure(
            work,
            required_status=status,
            command_id=None,
            reason_code="revision_command_missing",
            error="Finished Cut revision has no durable command identity",
        )
        return False
    try:
        outcome = application.advance(str(command_id))
    except Exception as error:
        _durable_failure(
            work,
            required_status=status,
            command_id=str(command_id),
            reason_code="revision_advance_failed",
            error=error,
        )
        return False

    state = getattr(outcome, "state", None)
    if state not in {"registered", "pending", "needs_review", "preview_ready", "failed"}:
        _durable_failure(
            work,
            required_status=status,
            command_id=str(command_id),
            reason_code="revision_status_invalid",
            error="Finished Cut Production returned an invalid status",
        )
        return False
    reason_code = getattr(outcome, "reason_code", None)
    persisted_status: Literal[
        "registered", "pending", "needs_review", "preview_ready", "failed"
    ] = "needs_review" if state == "pending" and reason_code is not None else state
    _update_job(
        work,
        {
            "status": persisted_status,
            "command_id": str(command_id),
            "production_state": state,
            "reason_code": reason_code,
            "error": None,
        },
        required_status=status,
    )
    return True


def main(
    argv: list[str] | None = None,
    *,
    application_factory: ApplicationFactory = default_application_factory,
) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--episodes-root",
        type=Path,
        default=Path(os.environ.get("PODCAST_EPISODES_ROOT", r"G:\\Footages")),
    )
    parser.add_argument("--interval", type=float, default=10.0)
    parser.add_argument("--once", action="store_true")
    parser.add_argument(
        "--recover-stalled",
        action="store_true",
        help="讓停在 needs_review 的修訂可以重新註冊（退休舊 claim ＋ 放回佇列）",
    )
    args = parser.parse_args(argv)
    if args.recover_stalled:
        for request_id in recover_stalled_revisions(args.episodes_root):
            print(f"recovered {request_id}")
    if args.interval <= 0:
        parser.error("--interval must be positive")

    while True:
        for work in pending_revision_jobs(args.episodes_root):
            run_revision_job(work, application_factory=application_factory)
        if args.once:
            return 0
        time.sleep(args.interval)


if __name__ == "__main__":
    raise SystemExit(main())
