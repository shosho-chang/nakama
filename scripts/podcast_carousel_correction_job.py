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

from pydantic import ValidationError

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from agents.brook.podcast_carousel_panel import (  # noqa: E402
    PanelResult,
    PanelReview,
    assert_panel_renderable,
)
from shared.schemas.podcast_carousel import (  # noqa: E402
    CAROUSEL_REQUIRED_REVIEWS,
    ArtifactReceipt,
    CarouselCopyEdit,
    CarouselCorrectionClaim,
    CarouselCorrectionCompletionEvidence,
    CarouselCorrectionItem,
    CarouselCorrectionJobV1,
    CarouselCorrectionProgress,
    CarouselCoverLayoutEdit,
    CarouselReviewerReceipt,
    CarouselReviewManifestV1,
    PodcastCarouselCopySpecV1,
    receipt_for,
)


class CorrectionJobTransitionError(ValueError):
    """Raised when an executor attempts an invalid state transition."""


DEFAULT_LEASE_SECONDS = 30 * 60


def _contained_file(path: Path, package_root: Path) -> Path:
    try:
        resolved = path.resolve(strict=True)
        resolved.relative_to(package_root.resolve(strict=True))
    except (FileNotFoundError, ValueError, OSError) as error:
        raise CorrectionJobTransitionError(
            f"carousel artifact is missing or outside package: {path}"
        ) from error
    if not resolved.is_file():
        raise CorrectionJobTransitionError(f"carousel artifact is not a file: {path}")
    return resolved


def _verified_receipt(path: Path, package_root: Path) -> tuple[Path, ArtifactReceipt]:
    contained = _contained_file(path, package_root)
    return contained, receipt_for(contained)


def _verify_manifest_artifacts(
    manifest: CarouselReviewManifestV1,
    *,
    package_root: Path,
    require_render_input: bool = False,
) -> PodcastCarouselCopySpecV1:
    if manifest.render_input is None:
        if require_render_input:
            raise CorrectionJobTransitionError("result manifest has no render input receipt")
    else:
        render_input_path = _contained_file(Path(manifest.render_input.path), package_root)
        if receipt_for(render_input_path) != manifest.render_input:
            raise CorrectionJobTransitionError("carousel render input receipt changed")
    copy_path = _contained_file(Path(manifest.copy_spec.path), package_root)
    if receipt_for(copy_path) != manifest.copy_spec:
        raise CorrectionJobTransitionError("carousel Copy Spec receipt changed")
    try:
        spec = PodcastCarouselCopySpecV1.model_validate_json(copy_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, ValidationError) as error:
        raise CorrectionJobTransitionError("carousel Copy Spec is invalid") from error
    if spec.episode_id != manifest.episode_id or spec.revision != manifest.revision:
        raise CorrectionJobTransitionError("carousel Copy Spec identity does not match manifest")
    if len(spec.pages) != len(manifest.pages):
        raise CorrectionJobTransitionError("carousel Copy Spec page count does not match manifest")
    for spec_page, manifest_page in zip(spec.pages, manifest.pages, strict=True):
        if spec_page != manifest_page.copy_page:
            raise CorrectionJobTransitionError(
                f"carousel Copy Spec page differs from manifest: {manifest_page.page_id}"
            )
        image_path = _contained_file(Path(manifest_page.image.path), package_root)
        if receipt_for(image_path) != manifest_page.image:
            raise CorrectionJobTransitionError(
                f"carousel page artifact receipt changed: {manifest_page.page_id}"
            )
    return spec


def _load_current_manifest(
    package_root: Path,
    *,
    require_render_input: bool = False,
) -> tuple[Path, ArtifactReceipt, CarouselReviewManifestV1, PodcastCarouselCopySpecV1]:
    current_path = package_root / "current.json"
    if not current_path.is_file():
        raise CorrectionJobTransitionError("carousel current.json is missing")
    try:
        current = json.loads(current_path.read_text(encoding="utf-8"))
        manifest_path, manifest_receipt = _verified_receipt(
            Path(str(current["manifest"])), package_root
        )
        if current["manifest_sha256"] != manifest_receipt.sha256:
            raise CorrectionJobTransitionError("current carousel manifest receipt changed")
        manifest = CarouselReviewManifestV1.model_validate_json(
            manifest_path.read_text(encoding="utf-8")
        )
    except CorrectionJobTransitionError:
        raise
    except (OSError, UnicodeDecodeError, KeyError, TypeError, ValueError, ValidationError) as error:
        raise CorrectionJobTransitionError("carousel current package is invalid") from error
    if (
        current.get("episode_id") != manifest.episode_id
        or current.get("revision") != manifest.revision
    ):
        raise CorrectionJobTransitionError("carousel current identity does not match manifest")
    spec = _verify_manifest_artifacts(
        manifest,
        package_root=package_root,
        require_render_input=require_render_input,
    )
    return manifest_path, manifest_receipt, manifest, spec


def _assert_source_integrity(job: CarouselCorrectionJobV1, package_root: Path) -> ArtifactReceipt:
    _, manifest_receipt, manifest, _ = _load_current_manifest(package_root)
    if manifest.episode_id != job.episode_id or manifest.revision != job.source_revision:
        raise CorrectionJobTransitionError("correction source is no longer the current revision")
    if manifest_receipt.sha256 != job.source_manifest_sha256:
        raise CorrectionJobTransitionError("correction source manifest receipt changed")
    page_receipts = {page.page_id: page.image.sha256 for page in manifest.pages}
    requested = [*job.feedback_items, *job.copy_edits]
    if job.layout_overrides is not None:
        requested.append(job.layout_overrides)
    for item in requested:
        if page_receipts.get(item.page_id) != item.artifact_sha256:
            raise CorrectionJobTransitionError(
                f"correction source page receipt changed: {item.page_id}"
            )
    return manifest_receipt


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
    feedback_items: list[CarouselCorrectionItem] | None = None,
    copy_edits: list[CarouselCopyEdit] | None = None,
    layout_overrides: CarouselCoverLayoutEdit | None = None,
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
        feedback_items=feedback_items or [],
        copy_edits=copy_edits or [],
        layout_overrides=layout_overrides,
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
        source_manifest_receipt = _assert_source_integrity(job, path.parent.parent)
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
                "source_manifest_receipt": source_manifest_receipt,
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


def _load_claimed_source_spec(
    job: CarouselCorrectionJobV1,
    package_root: Path,
) -> PodcastCarouselCopySpecV1:
    if job.source_manifest_receipt is None:
        raise CorrectionJobTransitionError("correction claim has no verified source receipt")
    manifest_path = _contained_file(Path(job.source_manifest_receipt.path), package_root)
    if receipt_for(manifest_path) != job.source_manifest_receipt:
        raise CorrectionJobTransitionError("claimed source manifest was changed after claim")
    if job.source_manifest_receipt.sha256 != job.source_manifest_sha256:
        raise CorrectionJobTransitionError("claimed source manifest does not match queued source")
    try:
        manifest = CarouselReviewManifestV1.model_validate_json(
            manifest_path.read_text(encoding="utf-8")
        )
    except (OSError, UnicodeDecodeError, ValidationError) as error:
        raise CorrectionJobTransitionError("claimed source manifest is invalid") from error
    if manifest.episode_id != job.episode_id or manifest.revision != job.source_revision:
        raise CorrectionJobTransitionError("claimed source manifest identity changed")
    return _verify_manifest_artifacts(manifest, package_root=package_root)


def _assert_structured_edits_applied(
    job: CarouselCorrectionJobV1,
    *,
    source_spec: PodcastCarouselCopySpecV1,
    result_spec: PodcastCarouselCopySpecV1,
) -> None:
    if not job.copy_edits and job.layout_overrides is None:
        return
    expected = source_spec.model_dump(mode="json")
    expected["revision"] = result_spec.revision
    pages = {page["page_id"]: page for page in expected["pages"]}
    for edit in job.copy_edits:
        page = pages.get(edit.page_id)
        if page is None or page["role"] != edit.role:
            raise CorrectionJobTransitionError(
                f"structured edit page identity changed: {edit.page_id}"
            )
        page.update(edit.fields)
    if job.layout_overrides is not None:
        expected["layout_overrides"]["cover"] = job.layout_overrides.values.model_dump(mode="json")
    try:
        expected_spec = PodcastCarouselCopySpecV1.model_validate(expected)
    except ValidationError as error:
        raise CorrectionJobTransitionError(
            "structured edits do not produce a valid Copy Spec"
        ) from error
    if result_spec != expected_spec:
        raise CorrectionJobTransitionError(
            "result Copy Spec does not exactly apply the requested structured edits"
        )


def _verify_completion_evidence(
    *,
    job: CarouselCorrectionJobV1,
    package_root: Path,
    result_manifest_path: Path,
    panel_result_path: Path,
    reviewer_artifacts: list[tuple[str, str, Path]],
) -> tuple[str, CarouselCorrectionCompletionEvidence]:
    source_spec = _load_claimed_source_spec(job, package_root)
    current_manifest_path, manifest_receipt, manifest, spec = _load_current_manifest(
        package_root,
        require_render_input=True,
    )
    explicit_manifest = _contained_file(result_manifest_path, package_root)
    if explicit_manifest != current_manifest_path:
        raise CorrectionJobTransitionError("result manifest is not the current carousel manifest")
    if manifest.episode_id != job.episode_id:
        raise CorrectionJobTransitionError("result manifest episode does not match correction job")
    if int(manifest.revision[1:]) <= int(job.source_revision[1:]):
        raise CorrectionJobTransitionError("result revision is not newer than correction source")
    _assert_structured_edits_applied(job, source_spec=source_spec, result_spec=spec)

    supplied_lenses = [lens for lens, _, _ in reviewer_artifacts]
    if set(supplied_lenses) != set(CAROUSEL_REQUIRED_REVIEWS) or len(supplied_lenses) != len(
        set(supplied_lenses)
    ):
        raise CorrectionJobTransitionError(
            "completion requires exactly one artifact for each canonical reviewer lens"
        )
    reviewer_ids = [reviewer_id for _, reviewer_id, _ in reviewer_artifacts]
    if len(reviewer_ids) != len(set(reviewer_ids)):
        raise CorrectionJobTransitionError("completion reviewer identities must be unique")

    parsed_reviews: dict[str, PanelReview] = {}
    reviewer_receipts: dict[str, CarouselReviewerReceipt] = {}
    review_paths: set[Path] = set()
    for lens, reviewer_id, review_path_value in reviewer_artifacts:
        review_path, review_receipt = _verified_receipt(review_path_value, package_root)
        if review_path in review_paths:
            raise CorrectionJobTransitionError("completion reviewer artifacts must be distinct")
        review_paths.add(review_path)
        try:
            review = PanelReview.model_validate_json(review_path.read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError, ValidationError) as error:
            raise CorrectionJobTransitionError(f"reviewer artifact is invalid: {lens}") from error
        if review.lens != lens:
            raise CorrectionJobTransitionError(f"reviewer artifact lens mismatch: {lens}")
        parsed_reviews[lens] = review
        reviewer_receipts[lens] = CarouselReviewerReceipt(
            lens=lens,
            reviewer_id=reviewer_id,
            review=review_receipt,
        )

    panel_path, panel_receipt = _verified_receipt(panel_result_path, package_root)
    if panel_path in review_paths:
        raise CorrectionJobTransitionError("panel result must be distinct from reviewer artifacts")
    try:
        panel = PanelResult.model_validate_json(panel_path.read_text(encoding="utf-8"))
        assert_panel_renderable(panel, spec=spec)
    except (OSError, UnicodeDecodeError, ValidationError, ValueError, RuntimeError) as error:
        raise CorrectionJobTransitionError("carousel panel has not validly converged") from error
    for lens in CAROUSEL_REQUIRED_REVIEWS:
        if panel.reviews[lens] != parsed_reviews[lens]:
            raise CorrectionJobTransitionError(f"panel review does not match receipt: {lens}")

    evidence = CarouselCorrectionCompletionEvidence(
        result_manifest=manifest_receipt,
        panel_result=panel_receipt,
        reviewers=tuple(reviewer_receipts[lens] for lens in CAROUSEL_REQUIRED_REVIEWS),
    )
    return manifest.revision, evidence


def complete_job(
    path: Path,
    *,
    claim_token: str,
    result_manifest_path: Path,
    panel_result_path: Path,
    reviewer_artifacts: list[tuple[str, str, Path]],
    now: datetime | None = None,
) -> CarouselCorrectionJobV1:
    with _job_lock(path):
        job = load_job(path)
        if job.status != "in_progress":
            raise CorrectionJobTransitionError(f"cannot complete correction job from {job.status}")
        timestamp = now or datetime.now(UTC)
        _assert_claim(job, claim_token, timestamp)
        result_revision, completion_evidence = _verify_completion_evidence(
            job=job,
            package_root=path.parent.parent,
            result_manifest_path=result_manifest_path,
            panel_result_path=panel_result_path,
            reviewer_artifacts=reviewer_artifacts,
        )
        updated = job.model_copy(
            update={
                "status": "completed",
                "updated_at": timestamp,
                "result_revision": result_revision,
                "completion_evidence": completion_evidence,
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


def _reviewer_artifact_arg(value: str) -> tuple[str, str, Path]:
    parts = value.split("=", 2)
    if len(parts) != 3 or not all(part.strip() for part in parts):
        raise argparse.ArgumentTypeError("reviewer receipt must use LENS=REVIEWER_ID=PATH format")
    return parts[0], parts[1], Path(parts[2])


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
    complete.add_argument("--result-manifest", type=Path, required=True)
    complete.add_argument("--panel-result", type=Path, required=True)
    complete.add_argument(
        "--reviewer-receipt",
        action="append",
        type=_reviewer_artifact_arg,
        required=True,
        metavar="LENS=REVIEWER_ID=PATH",
    )

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
            result_manifest_path=args.result_manifest,
            panel_result_path=args.panel_result,
            reviewer_artifacts=args.reviewer_receipt,
        )
    else:
        job = fail_job(args.job, claim_token=args.claim_token, error=args.error)
    print(json.dumps(job.model_dump(mode="json"), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    main()
