"""Deterministic local state transitions for Stage 6 Carousel publish jobs.

This tool never contacts a social platform. Executors perform any browser/API work
outside this state machine and report only receipts, permalinks, or errors.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import uuid4

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from shared.schemas.carousel_publish import (  # noqa: E402
    CarouselPublishAsset,
    CarouselPublishClaim,
    CarouselPublishJobV1,
    CarouselPublishPlatformResult,
    CarouselPublishProgress,
    CarouselPublishTarget,
)

DEFAULT_LEASE_SECONDS = 30 * 60


class PublishJobTransitionError(ValueError):
    """Raised when a publish-job transition is invalid or unsafe."""


def publish_jobs_dir(package_root: Path) -> Path:
    return package_root / "publish_jobs"


def publish_job_path(package_root: Path, job_id: str) -> Path:
    if not job_id.startswith("pj-") or len(job_id) != 35:
        raise ValueError("invalid publish job ID")
    try:
        int(job_id[3:], 16)
    except ValueError as error:
        raise ValueError("invalid publish job ID") from error
    return publish_jobs_dir(package_root) / f"{job_id}.json"


def load_publish_job(path: Path) -> CarouselPublishJobV1:
    if not path.is_file():
        raise FileNotFoundError(f"publish job not found: {path}")
    return CarouselPublishJobV1.model_validate_json(path.read_text(encoding="utf-8"))


def list_publish_jobs(package_root: Path) -> list[CarouselPublishJobV1]:
    directory = publish_jobs_dir(package_root)
    if not directory.is_dir():
        return []
    jobs = [load_publish_job(path) for path in directory.glob("pj-*.json")]
    return sorted(jobs, key=lambda job: (job.created_at, job.job_id))


def request_fingerprint(
    *,
    source_revision: str,
    source_manifest_sha256: str,
    caption: str,
    targets: list[CarouselPublishTarget],
) -> str:
    payload = {
        "source_revision": source_revision,
        "source_manifest_sha256": source_manifest_sha256,
        "caption": caption.strip(),
        "platforms": sorted(target.platform for target in targets),
    }
    encoded = json.dumps(payload, ensure_ascii=False, separators=(",", ":"), sort_keys=True)
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def _atomic_write(path: Path, job: CarouselPublishJobV1) -> None:
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
        raise PublishJobTransitionError("publish job is already being mutated") from error
    try:
        os.write(descriptor, f"pid={os.getpid()}\n".encode())
        yield
    finally:
        os.close(descriptor)
        lock_path.unlink(missing_ok=True)


def create_or_get_publish_job(
    *,
    package_root: Path,
    episode_id: str,
    source_revision: str,
    source_manifest_sha256: str,
    approval_revision_number: int,
    approved_at: datetime,
    caption: str,
    assets: list[CarouselPublishAsset],
    targets: list[CarouselPublishTarget],
    now: datetime | None = None,
    job_id: str | None = None,
) -> tuple[CarouselPublishJobV1, bool]:
    ordered_targets = sorted(targets, key=lambda target: target.platform)
    fingerprint = request_fingerprint(
        source_revision=source_revision,
        source_manifest_sha256=source_manifest_sha256,
        caption=caption,
        targets=ordered_targets,
    )
    timestamp = now or datetime.now(UTC)
    identifier = job_id or f"pj-{uuid4().hex}"
    job = CarouselPublishJobV1(
        job_id=identifier,
        episode_id=episode_id,
        source_revision=source_revision,
        source_manifest_sha256=source_manifest_sha256,
        approval_revision_number=approval_revision_number,
        approved_at=approved_at,
        request_fingerprint=fingerprint,
        caption=caption,
        assets=assets,
        targets=ordered_targets,
        created_at=timestamp,
        updated_at=timestamp,
    )
    directory = publish_jobs_dir(package_root)
    directory.mkdir(parents=True, exist_ok=True)
    path = publish_job_path(package_root, identifier)
    with _job_lock(directory / ".create"):
        existing = next(
            (
                item
                for item in list_publish_jobs(package_root)
                if item.request_fingerprint == fingerprint
            ),
            None,
        )
        if existing is not None:
            return existing, False
        if path.exists():
            raise FileExistsError(f"publish job already exists: {identifier}")
        _atomic_write(path, job)
    return job, True


def _assert_claim(job: CarouselPublishJobV1, claim_token: str, timestamp: datetime) -> None:
    if job.claim is None or job.claim.claim_token != claim_token:
        raise PublishJobTransitionError("publish job claim token mismatch")
    if timestamp >= job.claim.lease_expires_at:
        raise PublishJobTransitionError("publish job claim lease has expired")


def claim_publish_job(
    path: Path,
    *,
    executor: str,
    executor_id: str,
    executor_capabilities: list[str],
    claim_token: str | None = None,
    lease_seconds: int = DEFAULT_LEASE_SECONDS,
    now: datetime | None = None,
) -> CarouselPublishJobV1:
    with _job_lock(path):
        job = load_publish_job(path)
        timestamp = now or datetime.now(UTC)
        if job.status in {"claimed", "in_progress"}:
            if job.claim is None or timestamp < job.claim.lease_expires_at:
                raise PublishJobTransitionError(
                    "cannot claim publish job while its lease is still active"
                )
        elif job.status != "queued":
            raise PublishJobTransitionError(f"cannot claim publish job from {job.status}")
        required = {
            capability
            for target in job.targets
            for capability in target.required_executor_capabilities
        }
        offered = set(executor_capabilities)
        missing = sorted(required - offered)
        if missing:
            raise PublishJobTransitionError(
                f"executor lacks required publish capabilities: {', '.join(missing)}"
            )
        claim = CarouselPublishClaim(
            executor=executor,
            executor_id=executor_id,
            executor_capabilities=sorted(offered),
            claim_token=claim_token or f"claim-{uuid4().hex}",
            claimed_at=timestamp,
            lease_seconds=lease_seconds,
            lease_expires_at=timestamp + timedelta(seconds=lease_seconds),
        )
        updated = job.model_copy(
            update={
                "status": "in_progress" if job.progress else "claimed",
                "updated_at": timestamp,
                "claim": claim,
            }
        )
        updated = CarouselPublishJobV1.model_validate(updated.model_dump())
        _atomic_write(path, updated)
        return updated


def progress_publish_job(
    path: Path,
    *,
    claim_token: str,
    step: str,
    progress_percent: int,
    message: str = "",
    now: datetime | None = None,
) -> CarouselPublishJobV1:
    with _job_lock(path):
        job = load_publish_job(path)
        if job.status not in {"claimed", "in_progress"}:
            raise PublishJobTransitionError(f"cannot record publish progress from {job.status}")
        timestamp = now or datetime.now(UTC)
        _assert_claim(job, claim_token, timestamp)
        if job.progress and progress_percent < job.progress[-1].progress_percent:
            raise PublishJobTransitionError("publish progress percent cannot decrease")
        assert job.claim is not None
        progress = [
            *job.progress,
            CarouselPublishProgress(
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
        updated = CarouselPublishJobV1.model_validate(updated.model_dump())
        _atomic_write(path, updated)
        return updated


def complete_publish_job(
    path: Path,
    *,
    claim_token: str,
    results: list[CarouselPublishPlatformResult],
    now: datetime | None = None,
) -> CarouselPublishJobV1:
    with _job_lock(path):
        job = load_publish_job(path)
        if job.status != "in_progress":
            raise PublishJobTransitionError(f"cannot complete publish job from {job.status}")
        timestamp = now or datetime.now(UTC)
        _assert_claim(job, claim_token, timestamp)
        ordered = sorted(results, key=lambda result: result.platform)
        updated = job.model_copy(
            update={
                "status": "completed",
                "updated_at": timestamp,
                "results": ordered,
            }
        )
        updated = CarouselPublishJobV1.model_validate(updated.model_dump())
        _atomic_write(path, updated)
        return updated


def fail_publish_job(
    path: Path,
    *,
    claim_token: str,
    error: str,
    now: datetime | None = None,
) -> CarouselPublishJobV1:
    with _job_lock(path):
        job = load_publish_job(path)
        if job.status not in {"claimed", "in_progress"}:
            raise PublishJobTransitionError(f"cannot fail publish job from {job.status}")
        timestamp = now or datetime.now(UTC)
        _assert_claim(job, claim_token, timestamp)
        updated = job.model_copy(
            update={"status": "failed", "updated_at": timestamp, "error": error}
        )
        updated = CarouselPublishJobV1.model_validate(updated.model_dump())
        _atomic_write(path, updated)
        return updated


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)

    listing = commands.add_parser("list")
    listing.add_argument("package_root", type=Path)

    claim = commands.add_parser("claim")
    claim.add_argument("job", type=Path)
    claim.add_argument("--executor", choices=("codex", "claude_code"), required=True)
    claim.add_argument("--executor-id", required=True)
    claim.add_argument("--capability", action="append", default=[], dest="capabilities")
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
    complete.add_argument("--results-json", type=Path, required=True)

    fail = commands.add_parser("fail")
    fail.add_argument("job", type=Path)
    fail.add_argument("--claim-token", required=True)
    fail.add_argument("--error", required=True)
    return parser


def main() -> None:
    args = _parser().parse_args()
    if args.command == "list":
        payload = [job.model_dump(mode="json") for job in list_publish_jobs(args.package_root)]
        print(json.dumps(payload, ensure_ascii=False, indent=2))
        return
    if args.command == "claim":
        job = claim_publish_job(
            args.job,
            executor=args.executor,
            executor_id=args.executor_id,
            executor_capabilities=args.capabilities,
            claim_token=args.claim_token,
            lease_seconds=args.lease_seconds,
        )
    elif args.command == "progress":
        job = progress_publish_job(
            args.job,
            claim_token=args.claim_token,
            step=args.step,
            progress_percent=args.percent,
            message=args.message,
        )
    elif args.command == "complete":
        raw = json.loads(args.results_json.read_text(encoding="utf-8"))
        items = raw.get("results", raw) if isinstance(raw, dict) else raw
        results = [CarouselPublishPlatformResult.model_validate(item) for item in items]
        job = complete_publish_job(
            args.job,
            claim_token=args.claim_token,
            results=results,
        )
    else:
        job = fail_publish_job(args.job, claim_token=args.claim_token, error=args.error)
    print(json.dumps(job.model_dump(mode="json"), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    main()
