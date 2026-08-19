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
from contextlib import contextmanager, nullcontext
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
    CarouselPublishReleaseBundle,
    CarouselPublishTarget,
    CarouselPublishTargetState,
)
from shared.schemas.podcast_carousel import ArtifactReceipt, receipt_for  # noqa: E402

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
    source_publish_compatibility: str,
    caption: str,
    targets: list[CarouselPublishTarget],
) -> str:
    payload = {
        "source_revision": source_revision,
        "source_manifest_sha256": source_manifest_sha256,
        "source_publish_compatibility": source_publish_compatibility,
        "caption": caption.strip(),
        "platforms": sorted(target.platform for target in targets),
    }
    encoded = json.dumps(payload, ensure_ascii=False, separators=(",", ":"), sort_keys=True)
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def legacy_request_fingerprint(
    *,
    source_revision: str,
    source_manifest_sha256: str,
    caption: str,
    targets: list[CarouselPublishTarget],
) -> str:
    """Return the pre-compatibility v1 fingerprint for read-time migration."""

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


def _verify_asset_receipts(job: CarouselPublishJobV1) -> None:
    for asset in job.assets:
        path = Path(asset.image.path)
        if not path.is_absolute() or not path.is_file():
            raise PublishJobTransitionError(
                f"publish asset integrity check failed for {asset.page_id}: file missing"
            )
        try:
            size = path.stat().st_size
            if size != asset.image.bytes:
                raise PublishJobTransitionError(
                    f"publish asset integrity check failed for {asset.page_id}: size drift"
                )
            digest = hashlib.sha256()
            with path.open("rb") as handle:
                for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                    digest.update(chunk)
        except OSError as error:
            raise PublishJobTransitionError(
                f"publish asset integrity check failed for {asset.page_id}: unreadable"
            ) from error
        if digest.hexdigest() != asset.image.sha256:
            raise PublishJobTransitionError(
                f"publish asset integrity check failed for {asset.page_id}: hash drift"
            )


def _current_release(package_root: Path) -> tuple[dict, Path]:
    current_path = package_root / "current.json"
    try:
        current = json.loads(current_path.read_text(encoding="utf-8"))
        manifest_path = Path(str(current["manifest"]))
        if not manifest_path.is_absolute():
            manifest_path = package_root / manifest_path
        resolved = manifest_path.resolve()
        resolved.relative_to(package_root.resolve())
        if not resolved.is_file():
            raise FileNotFoundError(resolved)
    except (OSError, KeyError, TypeError, ValueError) as error:
        raise PublishJobTransitionError("current carousel release is unreadable") from error
    return current, resolved


def _materialize_approved_bytes(
    source: Path,
    destination: Path,
    *,
    expected_sha256: str,
    expected_bytes: int | None,
    label: str,
) -> ArtifactReceipt:
    """Read a source once, validate those bytes, then atomically publish the copy."""

    if not source.is_absolute():
        raise PublishJobTransitionError(f"{label} path must be absolute")
    try:
        payload = source.read_bytes()
    except OSError as error:
        raise PublishJobTransitionError(f"{label} is unreadable") from error
    digest = hashlib.sha256(payload).hexdigest()
    if expected_bytes is not None and len(payload) != expected_bytes:
        raise PublishJobTransitionError(f"{label} approved byte count does not match")
    if digest != expected_sha256:
        raise PublishJobTransitionError(f"{label} approved hash does not match")

    destination.parent.mkdir(parents=True, exist_ok=True)
    pending = destination.with_name(f".{destination.name}.{uuid4().hex}.tmp")
    try:
        with pending.open("xb") as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        pending.replace(destination)
    finally:
        pending.unlink(missing_ok=True)
    return ArtifactReceipt(
        path=str(destination.resolve()),
        bytes=len(payload),
        sha256=digest,
    )


def _materialize_release_bundle(
    *,
    package_root: Path,
    identifier: str,
    episode_id: str,
    source_revision: str,
    source_manifest_sha256: str,
    source_publish_compatibility: str,
    approval_revision_number: int,
    approved_at: datetime,
    caption: str,
    assets: list[CarouselPublishAsset],
    targets: list[CarouselPublishTarget],
    fingerprint: str,
    timestamp: datetime,
) -> tuple[list[CarouselPublishAsset], CarouselPublishReleaseBundle]:
    current, source_manifest_path = _current_release(package_root)
    if (
        current.get("revision") != source_revision
        or current.get("manifest_sha256") != source_manifest_sha256
    ):
        raise PublishJobTransitionError("current carousel release does not match publish source")

    bundle_root = publish_jobs_dir(package_root) / identifier / "release_bundle"
    pages_root = bundle_root / "pages"
    pages_root.mkdir(parents=True, exist_ok=False)
    manifest_path = bundle_root / "source_manifest.json"
    manifest_receipt = _materialize_approved_bytes(
        source_manifest_path,
        manifest_path,
        expected_sha256=source_manifest_sha256,
        expected_bytes=None,
        label="source manifest",
    )
    bundled_assets: list[CarouselPublishAsset] = []
    for asset in assets:
        source_path = Path(asset.image.path)
        bundled_path = pages_root / f"{asset.page_number:02d}.png"
        bundled_receipt = _materialize_approved_bytes(
            source_path,
            bundled_path,
            expected_sha256=asset.image.sha256,
            expected_bytes=asset.image.bytes,
            label=f"publish asset {asset.page_id}",
        )
        bundled_assets.append(asset.model_copy(update={"image": bundled_receipt}))

    request_path = bundle_root / "request.v1.json"
    request_payload = {
        "episode_id": episode_id,
        "source_revision": source_revision,
        "source_manifest_sha256": source_manifest_sha256,
        "source_publish_compatibility": source_publish_compatibility,
        "approval_revision_number": approval_revision_number,
        "approved_at": approved_at.isoformat(),
        "caption": caption.strip(),
        "assets": [asset.model_dump(mode="json") for asset in bundled_assets],
        "targets": [target.model_dump(mode="json") for target in targets],
        "request_fingerprint": fingerprint,
    }
    request_path.write_text(
        json.dumps(request_payload, ensure_ascii=False, separators=(",", ":"), sort_keys=True)
        + "\n",
        encoding="utf-8",
        newline="\n",
    )
    return bundled_assets, CarouselPublishReleaseBundle(
        request=receipt_for(request_path),
        source_manifest=manifest_receipt,
        created_at=timestamp,
    )


def _verify_release_bundle(job: CarouselPublishJobV1, package_root: Path) -> None:
    if job.release_bundle is None:
        raise PublishJobTransitionError(
            "legacy publish job has no immutable release bundle; create a replacement"
        )
    bundle_root = (publish_jobs_dir(package_root) / job.job_id / "release_bundle").resolve()
    expected_paths = {
        "request": bundle_root / "request.v1.json",
        "source manifest": bundle_root / "source_manifest.json",
    }
    for label, receipt in (
        ("request", job.release_bundle.request),
        ("source manifest", job.release_bundle.source_manifest),
    ):
        if Path(receipt.path).resolve() != expected_paths[label]:
            raise PublishJobTransitionError(
                f"publish release bundle {label} path does not match job"
            )
        try:
            actual = receipt_for(Path(receipt.path))
        except (OSError, ValueError) as error:
            raise PublishJobTransitionError(
                f"publish release bundle {label} is unreadable"
            ) from error
        if actual != receipt:
            raise PublishJobTransitionError(
                f"publish release bundle {label} integrity check failed"
            )
    _verify_asset_receipts(job)
    for asset in job.assets:
        expected_asset = bundle_root / "pages" / f"{asset.page_number:02d}.png"
        if Path(asset.image.path).resolve() != expected_asset:
            raise PublishJobTransitionError(
                f"publish release bundle asset path mismatch for {asset.page_id}"
            )
    try:
        request = json.loads(Path(job.release_bundle.request.path).read_text(encoding="utf-8"))
    except (OSError, ValueError, TypeError) as error:
        raise PublishJobTransitionError("publish release bundle request is unreadable") from error
    expected_fingerprint = request_fingerprint(
        source_revision=job.source_revision,
        source_manifest_sha256=job.source_manifest_sha256,
        source_publish_compatibility=_effective_publish_compatibility(job),
        caption=job.caption,
        targets=job.targets,
    )
    expected = {
        "episode_id": job.episode_id,
        "source_revision": job.source_revision,
        "source_manifest_sha256": job.source_manifest_sha256,
        "source_publish_compatibility": _effective_publish_compatibility(job),
        "approval_revision_number": job.approval_revision_number,
        "approved_at": job.approved_at.isoformat(),
        "caption": job.caption.strip(),
        "assets": [asset.model_dump(mode="json") for asset in job.assets],
        "targets": [target.model_dump(mode="json") for target in job.targets],
        "request_fingerprint": expected_fingerprint,
    }
    if request != expected or job.request_fingerprint != expected_fingerprint:
        raise PublishJobTransitionError("publish release bundle request does not match job")
    for state in job.target_states:
        if state.idempotency_key != _target_idempotency_key(expected_fingerprint, state.platform):
            raise PublishJobTransitionError("publish target idempotency key does not match request")
    current, current_manifest_path = _current_release(package_root)
    if (
        current.get("revision") != job.source_revision
        or current.get("manifest_sha256") != job.source_manifest_sha256
        or receipt_for(current_manifest_path).sha256 != job.release_bundle.source_manifest.sha256
    ):
        raise PublishJobTransitionError("current carousel release no longer matches publish source")


def _effective_publish_compatibility(job: CarouselPublishJobV1) -> str:
    if job.source_publish_compatibility is not None:
        return job.source_publish_compatibility
    return "api_compatible" if len(job.assets) <= 10 else "manual_only"


def _assert_safe_publish_strategy(job: CarouselPublishJobV1) -> None:
    if len(job.assets) > 10 and any(
        target.platform == "youtube_community" for target in job.targets
    ):
        raise PublishJobTransitionError("YouTube Community accepts at most 10 carousel images")
    if _effective_publish_compatibility(job) == "manual_only" and any(
        target.strategy == "meta_api" for target in job.targets
    ):
        legacy = "legacy " if job.source_publish_compatibility is None else ""
        raise PublishJobTransitionError(
            f"{legacy}manual_only publish job cannot use Meta API strategy"
        )


def _needs_safe_legacy_replacement(job: CarouselPublishJobV1) -> bool:
    if job.source_publish_compatibility is not None or job.status != "queued":
        return False
    return _effective_publish_compatibility(job) == "manual_only" and any(
        target.strategy == "meta_api" for target in job.targets
    )


def _has_failed_target_result(job: CarouselPublishJobV1) -> bool:
    return any(result.status == "failed" for result in job.results)


def _target_idempotency_key(request_fingerprint: str, platform: str) -> str:
    return hashlib.sha256(f"{request_fingerprint}:{platform}".encode()).hexdigest()


def _target_states_for_job(
    *,
    targets: list[CarouselPublishTarget],
    request_fingerprint: str,
    timestamp: datetime,
    previous: CarouselPublishJobV1 | None = None,
) -> list[CarouselPublishTargetState]:
    previous_published = {
        state.platform: state
        for state in (previous.target_states if previous else [])
        if state.status == "published"
    }
    if previous is not None and not previous.target_states:
        for result in previous.results:
            if result.status == "published":
                previous_published[result.platform] = CarouselPublishTargetState(
                    platform=result.platform,
                    strategy=result.strategy,
                    idempotency_key=_target_idempotency_key(request_fingerprint, result.platform),
                    status="published",
                    attempt_count=1,
                    checkpoint=result,
                    updated_at=result.completed_at,
                )
    states: list[CarouselPublishTargetState] = []
    for target in targets:
        carried = previous_published.get(target.platform)
        if carried is not None and carried.strategy == target.strategy:
            states.append(carried)
            continue
        states.append(
            CarouselPublishTargetState(
                platform=target.platform,
                strategy=target.strategy,
                idempotency_key=_target_idempotency_key(request_fingerprint, target.platform),
                updated_at=timestamp,
            )
        )
    return states


def _checkpoint_results(
    states: list[CarouselPublishTargetState],
) -> list[CarouselPublishPlatformResult]:
    return [state.checkpoint for state in states if state.checkpoint is not None]


@contextmanager
def _file_lock(lock_path: Path):
    """Use an OS lock so a crashed process cannot leave a permanent mutex."""

    lock_path.parent.mkdir(parents=True, exist_ok=True)
    descriptor = os.open(lock_path, os.O_RDWR | os.O_CREAT)
    handle = os.fdopen(descriptor, "r+b", buffering=0)
    if lock_path.stat().st_size == 0:
        handle.write(b" ")
    handle.seek(0)
    try:
        if os.name == "nt":
            import msvcrt

            msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
        else:
            import fcntl

            fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError as error:
        handle.close()
        raise PublishJobTransitionError("publish state is already being mutated") from error
    try:
        metadata = json.dumps(
            {"pid": os.getpid(), "acquired_at": datetime.now(UTC).isoformat()}
        ).encode("utf-8")
        handle.seek(0)
        handle.truncate()
        handle.write(metadata)
        handle.flush()
        handle.seek(0)
        yield
    finally:
        handle.seek(0)
        if os.name == "nt":
            import msvcrt

            msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
        else:
            import fcntl

            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
        handle.close()


@contextmanager
def _job_lock(path: Path):
    with _file_lock(path.with_suffix(path.suffix + ".lock")):
        yield


@contextmanager
def publish_release_lock(package_root: Path):
    with _file_lock(package_root / ".publish-release.lock"):
        yield


def _package_root_for_job(path: Path) -> Path:
    return path.parent.parent


def _assert_release_authority(job: CarouselPublishJobV1, package_root: Path) -> None:
    if job.status == "superseded":
        raise PublishJobTransitionError("publish job has been superseded")
    feedback_path = package_root / "review_feedback.v1.json"
    if not feedback_path.is_file():
        raise PublishJobTransitionError("publish approval audit is missing")
    try:
        feedback = json.loads(feedback_path.read_text(encoding="utf-8"))
        matching = [
            revision
            for revision in feedback.get("revisions", [])
            if revision.get("carousel_revision") == job.source_revision
            and revision.get("manifest_sha256") == job.source_manifest_sha256
        ]
    except (OSError, ValueError, TypeError) as error:
        raise PublishJobTransitionError("publish approval audit is unreadable") from error
    latest = matching[-1] if matching else None
    if (
        latest is None
        or latest.get("decision") != "approved"
        or latest.get("revision_number") != job.approval_revision_number
    ):
        raise PublishJobTransitionError("publish approval is no longer current")
    correction_dir = package_root / "correction_jobs"
    for correction_path in correction_dir.glob("cj-*.json"):
        try:
            correction = json.loads(correction_path.read_text(encoding="utf-8"))
        except (OSError, ValueError, TypeError) as error:
            raise PublishJobTransitionError("correction job audit is unreadable") from error
        if (
            correction.get("source_revision") == job.source_revision
            and correction.get("source_manifest_sha256") == job.source_manifest_sha256
            and correction.get("status") in {"queued", "claimed", "in_progress"}
        ):
            raise PublishJobTransitionError("correction job supersedes publish approval")


def create_or_get_publish_job(
    *,
    package_root: Path,
    episode_id: str,
    source_revision: str,
    source_manifest_sha256: str,
    source_publish_compatibility: str,
    approval_revision_number: int,
    approved_at: datetime,
    caption: str,
    assets: list[CarouselPublishAsset],
    targets: list[CarouselPublishTarget],
    allow_republish: bool = False,
    now: datetime | None = None,
    job_id: str | None = None,
) -> tuple[CarouselPublishJobV1, bool]:
    ordered_targets = sorted(targets, key=lambda target: target.platform)
    fingerprint = request_fingerprint(
        source_revision=source_revision,
        source_manifest_sha256=source_manifest_sha256,
        source_publish_compatibility=source_publish_compatibility,
        caption=caption,
        targets=ordered_targets,
    )
    legacy_fingerprint = legacy_request_fingerprint(
        source_revision=source_revision,
        source_manifest_sha256=source_manifest_sha256,
        caption=caption,
        targets=ordered_targets,
    )
    timestamp = now or datetime.now(UTC)
    identifier = job_id or f"pj-{uuid4().hex}"
    target_states = _target_states_for_job(
        targets=ordered_targets,
        request_fingerprint=fingerprint,
        timestamp=timestamp,
    )
    job = CarouselPublishJobV1(
        job_id=identifier,
        episode_id=episode_id,
        source_revision=source_revision,
        source_manifest_sha256=source_manifest_sha256,
        source_publish_compatibility=source_publish_compatibility,
        approval_revision_number=approval_revision_number,
        approved_at=approved_at,
        request_fingerprint=fingerprint,
        caption=caption,
        assets=assets,
        targets=ordered_targets,
        target_states=target_states,
        created_at=timestamp,
        updated_at=timestamp,
    )
    directory = publish_jobs_dir(package_root)
    directory.mkdir(parents=True, exist_ok=True)
    path = publish_job_path(package_root, identifier)
    with _job_lock(directory / ".create"):
        matching = [
            item
            for item in list_publish_jobs(package_root)
            if item.request_fingerprint in {fingerprint, legacy_fingerprint}
        ]
        existing = matching[-1] if matching else None
        needs_replacement = existing is not None and _needs_safe_legacy_replacement(existing)
        retryable_partial = (
            existing is not None
            and existing.status == "completed"
            and _has_failed_target_result(existing)
        )
        if (
            existing is not None
            and existing.status not in {"failed", "superseded"}
            and not needs_replacement
            and not retryable_partial
        ):
            return existing, False
        if existing is not None:
            published_platforms = set(published_publish_platforms(existing))
            previous_targets = {target.platform: target for target in existing.targets}
            ordered_targets = [
                previous_targets[target.platform]
                if target.platform in published_platforms
                else target
                for target in ordered_targets
            ]
            target_states = _target_states_for_job(
                targets=ordered_targets,
                request_fingerprint=fingerprint,
                timestamp=timestamp,
            )
            job = CarouselPublishJobV1.model_validate(
                job.model_copy(
                    update={"targets": ordered_targets, "target_states": target_states}
                ).model_dump()
            )
        requested_platforms = {target.platform for target in ordered_targets}
        matching_job_ids = {item.job_id for item in matching}
        other_jobs = [
            prior
            for prior in list_publish_jobs(package_root)
            if prior.job_id not in matching_job_ids
            if prior.source_revision == source_revision
            and prior.source_manifest_sha256 == source_manifest_sha256
        ]
        active_overlap = next(
            (
                prior
                for prior in other_jobs
                if prior.status in {"queued", "claimed", "in_progress"}
                and requested_platforms.intersection(target.platform for target in prior.targets)
            ),
            None,
        )
        if active_overlap is not None:
            raise PublishJobTransitionError(
                "another active publish job already owns one or more selected platforms"
            )
        published_overlap = next(
            (
                prior
                for prior in other_jobs
                if requested_platforms.intersection(published_publish_platforms(prior))
            ),
            None,
        )
        if published_overlap is not None and not allow_republish:
            raise PublishJobTransitionError(
                "republishing an already published platform requires explicit confirmation"
            )
        bundled_assets, release_bundle = _materialize_release_bundle(
            package_root=package_root,
            identifier=identifier,
            episode_id=episode_id,
            source_revision=source_revision,
            source_manifest_sha256=source_manifest_sha256,
            source_publish_compatibility=source_publish_compatibility,
            approval_revision_number=approval_revision_number,
            approved_at=approved_at,
            caption=caption,
            assets=assets,
            targets=ordered_targets,
            fingerprint=fingerprint,
            timestamp=timestamp,
        )
        job = CarouselPublishJobV1.model_validate(
            job.model_copy(
                update={"assets": bundled_assets, "release_bundle": release_bundle}
            ).model_dump()
        )
        if existing is not None:
            target_states = _target_states_for_job(
                targets=ordered_targets,
                request_fingerprint=fingerprint,
                timestamp=timestamp,
                previous=existing,
            )
            job = CarouselPublishJobV1.model_validate(
                job.model_copy(
                    update={
                        "retry_of_job_id": existing.job_id,
                        "target_states": target_states,
                        "results": _checkpoint_results(target_states),
                    }
                ).model_dump()
            )
        if path.exists():
            raise FileExistsError(f"publish job already exists: {identifier}")
        _atomic_write(path, job)
    return job, True


def _assert_claim(job: CarouselPublishJobV1, claim_token: str, timestamp: datetime) -> None:
    if job.claim is None or job.claim.claim_token != claim_token:
        raise PublishJobTransitionError("publish job claim token mismatch")
    if timestamp >= job.claim.lease_expires_at:
        raise PublishJobTransitionError("publish job claim lease has expired")


def supersede_queued_publish_job(
    path: Path,
    *,
    reason: str,
    now: datetime | None = None,
    release_locked: bool = False,
) -> CarouselPublishJobV1:
    release_context = (
        nullcontext() if release_locked else publish_release_lock(_package_root_for_job(path))
    )
    with release_context, _job_lock(path):
        job = load_publish_job(path)
        if job.status == "superseded":
            return job
        if job.status != "queued":
            raise PublishJobTransitionError(f"cannot supersede publish job from {job.status}")
        timestamp = now or datetime.now(UTC)
        updated = job.model_copy(
            update={
                "status": "superseded",
                "updated_at": timestamp,
                "superseded_at": timestamp,
                "superseded_reason": reason,
            }
        )
        updated = CarouselPublishJobV1.model_validate(updated.model_dump())
        _atomic_write(path, updated)
        return updated


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
    package_root = _package_root_for_job(path)
    with publish_release_lock(package_root), _job_lock(path):
        job = load_publish_job(path)
        _assert_release_authority(job, package_root)
        timestamp = now or datetime.now(UTC)
        expired_reclaim = False
        if job.status in {"claimed", "in_progress"}:
            if job.claim is None or timestamp < job.claim.lease_expires_at:
                raise PublishJobTransitionError(
                    "cannot claim publish job while its lease is still active"
                )
            expired_reclaim = True
        elif job.status != "queued":
            raise PublishJobTransitionError(f"cannot claim publish job from {job.status}")
        _assert_safe_publish_strategy(job)
        _verify_release_bundle(job, package_root)
        unfinished = set(unfinished_publish_platforms(job))
        required = {
            capability
            for target in job.targets
            if target.platform in unfinished
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
        target_states = job.target_states or _target_states_for_job(
            targets=job.targets,
            request_fingerprint=job.request_fingerprint,
            timestamp=timestamp,
            previous=job,
        )
        if expired_reclaim:
            target_states = [
                state.model_copy(update={"reconcile_required": True})
                if state.status == "in_progress"
                else state
                for state in target_states
            ]
        updated = job.model_copy(
            update={
                "status": (
                    "in_progress"
                    if job.progress
                    or any(state.status in {"in_progress", "failed"} for state in target_states)
                    else "claimed"
                ),
                "updated_at": timestamp,
                "claim": claim,
                "target_states": target_states,
                "results": _checkpoint_results(target_states),
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
    package_root = _package_root_for_job(path)
    with publish_release_lock(package_root), _job_lock(path):
        job = load_publish_job(path)
        if job.status not in {"claimed", "in_progress"}:
            raise PublishJobTransitionError(f"cannot record publish progress from {job.status}")
        timestamp = now or datetime.now(UTC)
        _assert_claim(job, claim_token, timestamp)
        _assert_release_authority(job, package_root)
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


def start_publish_target(
    path: Path,
    *,
    claim_token: str,
    platform: str,
    now: datetime | None = None,
) -> CarouselPublishJobV1:
    package_root = _package_root_for_job(path)
    with publish_release_lock(package_root), _job_lock(path):
        job = load_publish_job(path)
        if job.status not in {"claimed", "in_progress"}:
            raise PublishJobTransitionError(f"cannot start publish target from {job.status}")
        timestamp = now or datetime.now(UTC)
        _assert_claim(job, claim_token, timestamp)
        _assert_release_authority(job, package_root)
        _assert_safe_publish_strategy(job)
        _verify_release_bundle(job, package_root)
        states = job.target_states or _target_states_for_job(
            targets=job.targets,
            request_fingerprint=job.request_fingerprint,
            timestamp=timestamp,
            previous=job,
        )
        state_by_platform = {state.platform: state for state in states}
        state = state_by_platform.get(platform)
        if state is None:
            raise PublishJobTransitionError(f"publish target is not selected: {platform}")
        if state.status == "published":
            raise PublishJobTransitionError(f"publish target already checkpointed: {platform}")
        if state.status == "in_progress":
            raise PublishJobTransitionError(
                "publish target outcome is uncertain; reconcile and checkpoint before retry: "
                f"{platform}"
            )
        if state.status == "failed":
            raise PublishJobTransitionError(
                f"publish target has a failed checkpoint; complete and retry the job: {platform}"
            )
        state_by_platform[platform] = state.model_copy(
            update={
                "status": "in_progress",
                "attempt_count": state.attempt_count + 1,
                "attempt_id": f"pa-{uuid4().hex}",
                "reconcile_required": False,
                "checkpoint": None,
                "updated_at": timestamp,
            }
        )
        ordered_states = [state_by_platform[target.platform] for target in job.targets]
        assert job.claim is not None
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
                "target_states": ordered_states,
                "results": _checkpoint_results(ordered_states),
            }
        )
        updated = CarouselPublishJobV1.model_validate(updated.model_dump())
        _atomic_write(path, updated)
        return updated


def checkpoint_publish_target(
    path: Path,
    *,
    claim_token: str,
    result: CarouselPublishPlatformResult,
    now: datetime | None = None,
) -> CarouselPublishJobV1:
    package_root = _package_root_for_job(path)
    with publish_release_lock(package_root), _job_lock(path):
        job = load_publish_job(path)
        if job.status != "in_progress":
            raise PublishJobTransitionError(f"cannot checkpoint publish target from {job.status}")
        timestamp = now or datetime.now(UTC)
        _assert_claim(job, claim_token, timestamp)
        _assert_release_authority(job, package_root)
        _assert_safe_publish_strategy(job)
        _verify_release_bundle(job, package_root)
        states = job.target_states
        state_by_platform = {state.platform: state for state in states}
        state = state_by_platform.get(result.platform)
        if state is None or state.strategy != result.strategy:
            raise PublishJobTransitionError("publish checkpoint target identity mismatch")
        if state.status == "published":
            if state.checkpoint == result:
                return job
            raise PublishJobTransitionError(
                f"publish target already checkpointed: {result.platform}"
            )
        if state.status != "in_progress":
            raise PublishJobTransitionError(
                f"publish target must be started before checkpoint: {result.platform}"
            )
        if result.idempotency_key != state.idempotency_key or result.attempt_id != state.attempt_id:
            raise PublishJobTransitionError("publish checkpoint attempt binding mismatch")
        state_by_platform[result.platform] = state.model_copy(
            update={
                "status": result.status,
                "checkpoint": result,
                "reconcile_required": False,
                "updated_at": timestamp,
            }
        )
        ordered_states = [state_by_platform[target.platform] for target in job.targets]
        assert job.claim is not None
        renewed_claim = job.claim.model_copy(
            update={
                "lease_expires_at": timestamp + timedelta(seconds=job.claim.lease_seconds),
            }
        )
        updated = job.model_copy(
            update={
                "updated_at": timestamp,
                "claim": renewed_claim,
                "target_states": ordered_states,
                "results": _checkpoint_results(ordered_states),
            }
        )
        updated = CarouselPublishJobV1.model_validate(updated.model_dump())
        _atomic_write(path, updated)
        return updated


def unfinished_publish_platforms(job: CarouselPublishJobV1) -> list[str]:
    if job.target_states:
        return [state.platform for state in job.target_states if state.status != "published"]
    published = {result.platform for result in job.results if result.status == "published"}
    return [target.platform for target in job.targets if target.platform not in published]


def published_publish_platforms(job: CarouselPublishJobV1) -> list[str]:
    if job.target_states:
        return [state.platform for state in job.target_states if state.status == "published"]
    published = {result.platform for result in job.results if result.status == "published"}
    return [target.platform for target in job.targets if target.platform in published]


def republish_required_platforms(
    *,
    package_root: Path,
    source_revision: str,
    source_manifest_sha256: str,
    source_publish_compatibility: str,
    caption: str,
    targets: list[CarouselPublishTarget],
) -> list[str]:
    """Return only selected platforms that this exact request would post again."""

    ordered_targets = sorted(targets, key=lambda target: target.platform)
    fingerprint = request_fingerprint(
        source_revision=source_revision,
        source_manifest_sha256=source_manifest_sha256,
        source_publish_compatibility=source_publish_compatibility,
        caption=caption,
        targets=ordered_targets,
    )
    legacy_fingerprint = legacy_request_fingerprint(
        source_revision=source_revision,
        source_manifest_sha256=source_manifest_sha256,
        caption=caption,
        targets=ordered_targets,
    )
    jobs = [
        job
        for job in list_publish_jobs(package_root)
        if job.source_revision == source_revision
        and job.source_manifest_sha256 == source_manifest_sha256
    ]
    matching = [job for job in jobs if job.request_fingerprint in {fingerprint, legacy_fingerprint}]
    existing = matching[-1] if matching else None
    retryable_partial = (
        existing is not None
        and existing.status == "completed"
        and _has_failed_target_result(existing)
    )
    if (
        existing is not None
        and existing.status not in {"failed", "superseded"}
        and not _needs_safe_legacy_replacement(existing)
        and not retryable_partial
    ):
        return []

    carried = set(published_publish_platforms(existing)) if existing is not None else set()
    executable = {target.platform for target in ordered_targets} - carried
    matching_ids = {job.job_id for job in matching}
    previously_published = {
        platform
        for job in jobs
        if job.job_id not in matching_ids
        for platform in published_publish_platforms(job)
    }
    return sorted(executable.intersection(previously_published))


def complete_publish_job(
    path: Path,
    *,
    claim_token: str,
    results: list[CarouselPublishPlatformResult],
    now: datetime | None = None,
) -> CarouselPublishJobV1:
    package_root = _package_root_for_job(path)
    with publish_release_lock(package_root), _job_lock(path):
        job = load_publish_job(path)
        if job.status != "in_progress":
            raise PublishJobTransitionError(f"cannot complete publish job from {job.status}")
        timestamp = now or datetime.now(UTC)
        _assert_claim(job, claim_token, timestamp)
        _assert_release_authority(job, package_root)
        _assert_safe_publish_strategy(job)
        _verify_release_bundle(job, package_root)
        ordered = sorted(results, key=lambda result: result.platform)
        target_states = job.target_states
        if not target_states:
            raise PublishJobTransitionError(
                "publish targets must be checkpointed before completion"
            )
        state_by_platform = {state.platform: state for state in target_states}
        for result in ordered:
            state = state_by_platform.get(result.platform)
            if state is None or state.strategy != result.strategy:
                raise PublishJobTransitionError("publish completion target identity mismatch")
            if state.checkpoint != result:
                raise PublishJobTransitionError(
                    f"publish target must be checkpointed before completion: {result.platform}"
                )
        ordered = _checkpoint_results(target_states)
        if len(ordered) != len(job.targets):
            missing = unfinished_publish_platforms(
                job.model_copy(update={"target_states": target_states})
            )
            raise PublishJobTransitionError(
                f"cannot complete with unfinished publish targets: {', '.join(missing)}"
            )
        failed_platforms = [result.platform for result in ordered if result.status == "failed"]
        updated = job.model_copy(
            update={
                "status": "failed" if failed_platforms else "completed",
                "updated_at": timestamp,
                "target_states": target_states,
                "results": ordered,
                "error": (
                    "one or more publish targets failed: " + ", ".join(failed_platforms)
                    if failed_platforms
                    else None
                ),
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
    with publish_release_lock(_package_root_for_job(path)), _job_lock(path):
        job = load_publish_job(path)
        if job.status not in {"claimed", "in_progress"}:
            raise PublishJobTransitionError(f"cannot fail publish job from {job.status}")
        timestamp = now or datetime.now(UTC)
        _assert_claim(job, claim_token, timestamp)
        if any(state.status == "in_progress" for state in job.target_states):
            raise PublishJobTransitionError(
                "cannot fail while a publish target outcome is uncertain; reconcile it first"
            )
        updated = job.model_copy(
            update={"status": "failed", "updated_at": timestamp, "error": error}
        )
        updated = CarouselPublishJobV1.model_validate(updated.model_dump())
        _atomic_write(path, updated)
        return updated


def retire_unsafe_legacy_publish_job(
    path: Path,
    *,
    reason: str,
    now: datetime | None = None,
) -> CarouselPublishJobV1:
    """Administratively retire a non-executable legacy job without publishing."""

    with publish_release_lock(_package_root_for_job(path)), _job_lock(path):
        job = load_publish_job(path)
        if job.status == "superseded":
            return job
        if job.release_bundle is not None:
            raise PublishJobTransitionError(
                "only legacy jobs without release bundles can be retired"
            )
        timestamp = now or datetime.now(UTC)
        if job.status in {"claimed", "in_progress"} and (
            job.claim is None or timestamp < job.claim.lease_expires_at
        ):
            raise PublishJobTransitionError("cannot retire a legacy job with an active lease")
        if job.status not in {"queued", "claimed", "in_progress", "failed"}:
            raise PublishJobTransitionError(f"cannot retire legacy publish job from {job.status}")
        updated = job.model_copy(
            update={
                "status": "superseded",
                "updated_at": timestamp,
                "claim": None,
                "progress": [],
                "error": None,
                "superseded_at": timestamp,
                "superseded_reason": reason,
            }
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

    start_target = commands.add_parser("start-target")
    start_target.add_argument("job", type=Path)
    start_target.add_argument("--claim-token", required=True)
    start_target.add_argument("--platform", required=True)

    checkpoint = commands.add_parser("checkpoint")
    checkpoint.add_argument("job", type=Path)
    checkpoint.add_argument("--claim-token", required=True)
    checkpoint.add_argument("--result-json", type=Path, required=True)

    complete = commands.add_parser("complete")
    complete.add_argument("job", type=Path)
    complete.add_argument("--claim-token", required=True)
    complete.add_argument("--results-json", type=Path, required=True)

    fail = commands.add_parser("fail")
    fail.add_argument("job", type=Path)
    fail.add_argument("--claim-token", required=True)
    fail.add_argument("--error", required=True)

    retire = commands.add_parser("retire-legacy")
    retire.add_argument("job", type=Path)
    retire.add_argument("--reason", required=True)
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
    elif args.command == "start-target":
        job = start_publish_target(
            args.job,
            claim_token=args.claim_token,
            platform=args.platform,
        )
    elif args.command == "checkpoint":
        result = CarouselPublishPlatformResult.model_validate_json(
            args.result_json.read_text(encoding="utf-8")
        )
        job = checkpoint_publish_target(
            args.job,
            claim_token=args.claim_token,
            result=result,
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
    elif args.command == "fail":
        job = fail_publish_job(args.job, claim_token=args.claim_token, error=args.error)
    else:
        job = retire_unsafe_legacy_publish_job(args.job, reason=args.reason)
    print(json.dumps(job.model_dump(mode="json"), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    main()
