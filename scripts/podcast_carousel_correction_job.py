"""Deterministic file-state mutations for Podcast Carousel correction jobs."""

from __future__ import annotations

import argparse
import json
import os
import sys
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import uuid4

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from shared.schemas.podcast_carousel import (  # noqa: E402
    CarouselCorrectionClaim,
    CarouselCorrectionItem,
    CarouselCorrectionJobV1,
    CarouselCorrectionProgress,
)


class CorrectionJobTransitionError(ValueError):
    """Raised when an executor attempts an invalid state transition."""


DEFAULT_LEASE_SECONDS = 30 * 60


def correction_jobs_dir(package_root: Path) -> Path:
    return package_root / "correction_jobs"


def correction_job_path(package_root: Path, job_id: str) -> Path:
    if not job_id.startswith("cj-") or len(job_id) != 35:
        raise ValueError("invalid correction job ID")
    try:
        int(job_id[3:], 16)
    except ValueError as error:
        raise ValueError("invalid correction job ID") from error
    return correction_jobs_dir(package_root) / f"{job_id}.json"


def load_job(path: Path) -> CarouselCorrectionJobV1:
    if not path.is_file():
        raise FileNotFoundError(f"correction job not found: {path}")
    return CarouselCorrectionJobV1.model_validate_json(path.read_text(encoding="utf-8"))


def _atomic_write(path: Path, job: CarouselCorrectionJobV1) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    pending = path.with_name(f".{path.name}.{uuid4().hex}.tmp")
    try:
        pending.write_text(job.model_dump_json(indent=2) + "\n", encoding="utf-8", newline="\n")
        pending.replace(path)
    finally:
        pending.unlink(missing_ok=True)


@contextmanager
def _job_lock(path: Path):
    lock_path = path.with_suffix(path.suffix + ".lock")
    try:
        descriptor = os.open(lock_path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
    except FileExistsError as error:
        raise CorrectionJobTransitionError("correction job is already being mutated") from error
    try:
        os.write(descriptor, f"pid={os.getpid()}\n".encode())
        yield
    finally:
        os.close(descriptor)
        lock_path.unlink(missing_ok=True)


def create_queued_job(
    *,
    package_root: Path,
    episode_id: str,
    source_revision: str,
    source_manifest_sha256: str,
    feedback_items: list[CarouselCorrectionItem],
    now: datetime | None = None,
    job_id: str | None = None,
) -> CarouselCorrectionJobV1:
    timestamp = now or datetime.now(UTC)
    identifier = job_id or f"cj-{uuid4().hex}"
    job = CarouselCorrectionJobV1(
        job_id=identifier,
        episode_id=episode_id,
        source_revision=source_revision,
        source_manifest_sha256=source_manifest_sha256,
        feedback_items=feedback_items,
        created_at=timestamp,
        updated_at=timestamp,
    )
    directory = correction_jobs_dir(package_root)
    directory.mkdir(parents=True, exist_ok=True)
    path = correction_job_path(package_root, identifier)
    with _job_lock(directory / ".create"):
        active = [
            existing
            for existing in list_jobs(package_root)
            if existing.source_revision == source_revision
            and existing.source_manifest_sha256 == source_manifest_sha256
            and existing.status in {"queued", "claimed", "in_progress"}
        ]
        if active:
            raise CorrectionJobTransitionError(
                "an active correction job already exists for this carousel revision"
            )
        if path.exists():
            raise FileExistsError(f"correction job already exists: {identifier}")
        _atomic_write(path, job)
    return job


def list_jobs(package_root: Path) -> list[CarouselCorrectionJobV1]:
    directory = correction_jobs_dir(package_root)
    if not directory.is_dir():
        return []
    jobs = [load_job(path) for path in directory.glob("cj-*.json")]
    return sorted(jobs, key=lambda job: (job.created_at, job.job_id))


def _assert_claim(
    job: CarouselCorrectionJobV1,
    claim_token: str,
    timestamp: datetime,
) -> None:
    if job.claim is None or job.claim.claim_token != claim_token:
        raise CorrectionJobTransitionError("correction job claim token mismatch")
    if timestamp >= job.claim.lease_expires_at:
        raise CorrectionJobTransitionError("correction job claim lease has expired")


def claim_job(
    path: Path,
    *,
    executor: str,
    executor_id: str,
    claim_token: str | None = None,
    lease_seconds: int = DEFAULT_LEASE_SECONDS,
    now: datetime | None = None,
) -> CarouselCorrectionJobV1:
    with _job_lock(path):
        job = load_job(path)
        timestamp = now or datetime.now(UTC)
        if job.status in {"claimed", "in_progress"}:
            if job.claim is None or timestamp < job.claim.lease_expires_at:
                raise CorrectionJobTransitionError(
                    "cannot claim correction job while its lease is still active"
                )
        elif job.status != "queued":
            raise CorrectionJobTransitionError(f"cannot claim correction job from {job.status}")
        updated = job.model_copy(
            update={
                "status": "in_progress" if job.progress else "claimed",
                "updated_at": timestamp,
                "claim": CarouselCorrectionClaim(
                    executor=executor,
                    executor_id=executor_id,
                    claim_token=claim_token or f"claim-{uuid4().hex}",
                    claimed_at=timestamp,
                    lease_seconds=lease_seconds,
                    lease_expires_at=timestamp + timedelta(seconds=lease_seconds),
                ),
            }
        )
        updated = CarouselCorrectionJobV1.model_validate(updated.model_dump())
        _atomic_write(path, updated)
        return updated


def progress_job(
    path: Path,
    *,
    claim_token: str,
    step: str,
    progress_percent: int,
    message: str = "",
    now: datetime | None = None,
) -> CarouselCorrectionJobV1:
    with _job_lock(path):
        job = load_job(path)
        if job.status not in {"claimed", "in_progress"}:
            raise CorrectionJobTransitionError(f"cannot record progress from {job.status}")
        timestamp = now or datetime.now(UTC)
        _assert_claim(job, claim_token, timestamp)
        if job.progress and progress_percent < job.progress[-1].progress_percent:
            raise CorrectionJobTransitionError("correction progress percent cannot decrease")
        assert job.claim is not None
        progress = [
            *job.progress,
            CarouselCorrectionProgress(
                sequence=len(job.progress) + 1,
                step=step,
                progress_percent=progress_percent,
                message=message,
                recorded_at=timestamp,
            ),
        ]
        renewed_claim = job.claim.model_copy(
            update={
                "lease_expires_at": timestamp + timedelta(seconds=job.claim.lease_seconds),
            }
        )
        updated = job.model_copy(
            update={
                "status": "in_progress",
                "updated_at": timestamp,
                "claim": renewed_claim,
                "progress": progress,
            }
        )
        updated = CarouselCorrectionJobV1.model_validate(updated.model_dump())
        _atomic_write(path, updated)
        return updated


def complete_job(
    path: Path,
    *,
    claim_token: str,
    result_revision: str,
    now: datetime | None = None,
) -> CarouselCorrectionJobV1:
    with _job_lock(path):
        job = load_job(path)
        if job.status != "in_progress":
            raise CorrectionJobTransitionError(f"cannot complete correction job from {job.status}")
        timestamp = now or datetime.now(UTC)
        _assert_claim(job, claim_token, timestamp)
        updated = job.model_copy(
            update={
                "status": "completed",
                "updated_at": timestamp,
                "result_revision": result_revision,
            }
        )
        updated = CarouselCorrectionJobV1.model_validate(updated.model_dump())
        _atomic_write(path, updated)
        return updated


def fail_job(
    path: Path,
    *,
    claim_token: str,
    error: str,
    now: datetime | None = None,
) -> CarouselCorrectionJobV1:
    with _job_lock(path):
        job = load_job(path)
        if job.status not in {"claimed", "in_progress"}:
            raise CorrectionJobTransitionError(f"cannot fail correction job from {job.status}")
        timestamp = now or datetime.now(UTC)
        _assert_claim(job, claim_token, timestamp)
        updated = job.model_copy(
            update={
                "status": "failed",
                "updated_at": timestamp,
                "error": error,
            }
        )
        updated = CarouselCorrectionJobV1.model_validate(updated.model_dump())
        _atomic_write(path, updated)
        return updated


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)

    claim = commands.add_parser("claim")
    claim.add_argument("job", type=Path)
    claim.add_argument("--executor", choices=("codex", "claude_code"), required=True)
    claim.add_argument("--executor-id", required=True)
    claim.add_argument("--claim-token")
    claim.add_argument("--lease-seconds", type=int, default=DEFAULT_LEASE_SECONDS)

    progress = commands.add_parser("progress")
    progress.add_argument("job", type=Path)
    progress.add_argument("--claim-token", required=True)
    progress.add_argument("--step", required=True)
    progress.add_argument("--percent", type=int, required=True)
    progress.add_argument("--message", default="")

    complete = commands.add_parser("complete")
    complete.add_argument("job", type=Path)
    complete.add_argument("--claim-token", required=True)
    complete.add_argument("--result-revision", required=True)

    fail = commands.add_parser("fail")
    fail.add_argument("job", type=Path)
    fail.add_argument("--claim-token", required=True)
    fail.add_argument("--error", required=True)
    return parser


def main() -> None:
    args = _parser().parse_args()
    if args.command == "claim":
        job = claim_job(
            args.job,
            executor=args.executor,
            executor_id=args.executor_id,
            claim_token=args.claim_token,
            lease_seconds=args.lease_seconds,
        )
    elif args.command == "progress":
        job = progress_job(
            args.job,
            claim_token=args.claim_token,
            step=args.step,
            progress_percent=args.percent,
            message=args.message,
        )
    elif args.command == "complete":
        job = complete_job(
            args.job,
            claim_token=args.claim_token,
            result_revision=args.result_revision,
        )
    else:
        job = fail_job(args.job, claim_token=args.claim_token, error=args.error)
    print(json.dumps(job.model_dump(mode="json"), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    main()
