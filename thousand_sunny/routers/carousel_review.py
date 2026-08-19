"""Authenticated, episode-first review gate for Podcast IG Carousels."""

from __future__ import annotations

import hashlib
import json
import os
from datetime import UTC, datetime
from pathlib import Path

from fastapi import APIRouter, Cookie, HTTPException, Response
from fastapi.responses import FileResponse, HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from pydantic import ValidationError
from starlette.requests import Request

from scripts.podcast_carousel_correction_job import (
    CorrectionJobTransitionError,
    correction_job_path,
    create_queued_job,
    list_jobs,
    load_job,
)
from scripts.podcast_carousel_publish_job import (
    create_or_get_publish_job,
    list_publish_jobs,
    load_publish_job,
    publish_job_path,
)
from shared.schemas.carousel_publish import (
    CarouselPublishAsset,
    CarouselPublishJobV1,
    CarouselPublishTarget,
)
from shared.schemas.podcast_carousel import (
    CarouselCorrectionItem,
    CarouselCorrectionJobV1,
    CarouselFeedbackRevision,
    CarouselPageDecision,
    CarouselReviewFeedbackV1,
    CarouselReviewManifestV1,
    receipt_for,
)
from thousand_sunny.auth import check_auth

page_router = APIRouter(prefix="/bridge/ig-cards", tags=["bridge-ig-cards"])
_templates = Jinja2Templates(
    directory=str(Path(__file__).resolve().parent.parent / "templates" / "bridge")
)
_MAX_FEEDBACK = 1200
_MAX_CAPTION = 5000


def _shosho_asset_version() -> str:
    static_dir = Path(__file__).resolve().parent.parent / "static" / "shosho"
    digest = hashlib.sha1()
    for name in (
        "tokens.css",
        "bridge.css",
        "bridge-pages.css",
        "carousel-review.css",
        "carousel-publish.css",
    ):
        asset = static_dir / name
        if asset.is_file():
            digest.update(asset.read_bytes())
    return digest.hexdigest()[:8]


_SHOSHO_ASSET_VERSION = _shosho_asset_version()


def _episode_dir(episode_slug: str) -> Path:
    if (
        not episode_slug
        or len(episode_slug) > 120
        or episode_slug in {".", ".."}
        or any(character in episode_slug for character in ("/", "\\", ":", "\x00"))
    ):
        raise HTTPException(status_code=404, detail="invalid episode slug")
    root_value = os.environ.get("PODCAST_EPISODES_ROOT", "").strip()
    if not root_value:
        raise HTTPException(status_code=503, detail="PODCAST_EPISODES_ROOT is not configured")
    root = Path(root_value)
    if not root.is_dir():
        raise HTTPException(status_code=503, detail="PODCAST_EPISODES_ROOT is not a directory")
    candidate = root / episode_slug
    try:
        candidate.resolve().relative_to(root.resolve())
    except ValueError as error:
        raise HTTPException(status_code=404, detail="invalid episode slug") from error
    if not candidate.is_dir():
        raise HTTPException(status_code=404, detail=f"episode not found: {episode_slug}")
    return candidate


def _contained_file(path_value: str, root: Path) -> Path:
    path = Path(path_value)
    try:
        path.resolve().relative_to(root.resolve())
    except ValueError as error:
        raise HTTPException(
            status_code=422, detail="carousel artifact escapes package root"
        ) from error
    if not path.is_file():
        raise HTTPException(status_code=422, detail=f"carousel artifact missing: {path.name}")
    return path


def _load_manifest(episode_slug: str) -> tuple[Path, CarouselReviewManifestV1, str]:
    package_root = _episode_dir(episode_slug) / "ig-carousel"
    current_path = package_root / "current.json"
    if not current_path.is_file():
        raise HTTPException(status_code=404, detail="carousel has not been rendered")
    try:
        current = json.loads(current_path.read_text(encoding="utf-8"))
        manifest_path = _contained_file(str(current["manifest"]), package_root)
        manifest_receipt = receipt_for(manifest_path)
        if manifest_receipt.sha256 != current["manifest_sha256"]:
            raise HTTPException(status_code=409, detail="current carousel manifest changed")
        manifest = CarouselReviewManifestV1.model_validate_json(
            manifest_path.read_text(encoding="utf-8")
        )
    except HTTPException:
        raise
    except (OSError, KeyError, TypeError, ValueError, ValidationError) as error:
        raise HTTPException(status_code=422, detail="invalid carousel review package") from error
    for page in manifest.pages:
        image_path = _contained_file(page.image.path, package_root)
        if receipt_for(image_path) != page.image:
            raise HTTPException(status_code=409, detail=f"carousel page changed: {page.page_id}")
    return package_root, manifest, manifest_receipt.sha256


def _feedback_path(package_root: Path) -> Path:
    return package_root / "review_feedback.v1.json"


def _load_feedback(package_root: Path, episode_id: str) -> CarouselReviewFeedbackV1:
    path = _feedback_path(package_root)
    if not path.is_file():
        return CarouselReviewFeedbackV1(episode_id=episode_id)
    try:
        feedback = CarouselReviewFeedbackV1.model_validate_json(path.read_text(encoding="utf-8"))
    except (OSError, ValidationError) as error:
        raise HTTPException(status_code=422, detail="invalid carousel review feedback") from error
    if feedback.episode_id != episode_id:
        raise HTTPException(status_code=422, detail="carousel feedback episode mismatch")
    return feedback


def _write_feedback(path: Path, feedback: CarouselReviewFeedbackV1) -> None:
    pending = path.with_suffix(".tmp")
    pending.write_text(feedback.model_dump_json(indent=2) + "\n", encoding="utf-8", newline="\n")
    pending.replace(path)


def _approved_revision(
    package_root: Path,
    manifest: CarouselReviewManifestV1,
    manifest_sha256: str,
) -> CarouselFeedbackRevision | None:
    matching = [
        revision
        for revision in _load_feedback(package_root, manifest.episode_id).revisions
        if revision.carousel_revision == manifest.revision
        and revision.manifest_sha256 == manifest_sha256
    ]
    latest = matching[-1] if matching else None
    return latest if latest is not None and latest.decision == "approved" else None


def _require_approved_revision(
    package_root: Path,
    manifest: CarouselReviewManifestV1,
    manifest_sha256: str,
) -> CarouselFeedbackRevision:
    approval = _approved_revision(package_root, manifest, manifest_sha256)
    if approval is None:
        raise HTTPException(
            status_code=403,
            detail="current carousel manifest has not passed the Review Gate",
        )
    return approval


def _publish_capabilities() -> list[CarouselPublishTarget]:
    meta_configured = os.environ.get("META_CAROUSEL_PUBLISH_CONFIGURED", "").strip() == "1"
    meta_strategy = "meta_api" if meta_configured else "agent_browser"
    meta_state = "configured" if meta_configured else "agent_browser_required"
    meta_capability = "meta_api" if meta_configured else "browser_session"
    meta_note = (
        "Meta API transport is configured; executor must hold the meta_api capability."
        if meta_configured
        else (
            "Meta API credentials/media transport are not configured; "
            "use an authenticated agent browser."
        )
    )
    return [
        CarouselPublishTarget(
            platform="instagram",
            strategy=meta_strategy,
            configuration_state=meta_state,
            required_executor_capabilities=[meta_capability],
            note=meta_note,
        ),
        CarouselPublishTarget(
            platform="facebook_page",
            strategy=meta_strategy,
            configuration_state=meta_state,
            required_executor_capabilities=[meta_capability],
            note=meta_note,
        ),
        CarouselPublishTarget(
            platform="youtube_community",
            strategy="agent_browser_manual",
            configuration_state="manual_only",
            required_executor_capabilities=["browser_session"],
            note=(
                "YouTube Community has no supported Data API publish endpoint; "
                "use an authenticated agent browser with manual confirmation."
            ),
        ),
    ]


def _publish_assets(manifest: CarouselReviewManifestV1) -> list[CarouselPublishAsset]:
    return [
        CarouselPublishAsset(
            page_id=page.page_id,
            page_number=page.page_number,
            image=page.image,
        )
        for page in manifest.pages
    ]


def _publish_job_payload(episode_slug: str, job: CarouselPublishJobV1) -> dict:
    return {
        **job.model_dump(mode="json"),
        "status_url": f"/bridge/ig-cards/{episode_slug}/publish/jobs/{job.job_id}",
    }


def _context(episode_slug: str) -> dict:
    package_root, manifest, manifest_sha256 = _load_manifest(episode_slug)
    feedback = _load_feedback(package_root, manifest.episode_id)
    matching = [
        revision
        for revision in feedback.revisions
        if revision.carousel_revision == manifest.revision
        and revision.manifest_sha256 == manifest_sha256
    ]
    latest = matching[-1] if matching else None
    latest_by_id = {page.page_id: page for page in latest.pages} if latest else {}
    rows = []
    for page in manifest.pages:
        decision = latest_by_id.get(page.page_id)
        rows.append(
            {
                "page": page,
                "status": decision.status if decision else "pending",
                "feedback": decision.feedback if decision else "",
            }
        )
    return {
        "episode_slug": episode_slug,
        "manifest": manifest,
        "manifest_sha256": manifest_sha256,
        "rows": rows,
        "decision_count": len(matching),
        "approved": bool(latest and latest.decision == "approved"),
        "asset_version": _SHOSHO_ASSET_VERSION,
    }


@page_router.get("/{episode_slug}", response_class=HTMLResponse)
async def carousel_review_board(
    request: Request,
    episode_slug: str,
    saved: bool = False,
    nakama_auth: str | None = Cookie(None),
):
    if not check_auth(nakama_auth):
        return RedirectResponse(f"/login?next=/bridge/ig-cards/{episode_slug}", status_code=302)
    context = _context(episode_slug)
    context["saved"] = saved
    return _templates.TemplateResponse(request, "carousel_review.html", context)


@page_router.get("/{episode_slug}/publish", response_class=HTMLResponse)
async def carousel_publish_board(
    request: Request,
    episode_slug: str,
    nakama_auth: str | None = Cookie(None),
):
    """Render the explicit Stage 6 hand-off after Review Gate approval."""

    if not check_auth(nakama_auth):
        return RedirectResponse(
            f"/login?next=/bridge/ig-cards/{episode_slug}/publish",
            status_code=302,
        )
    package_root, manifest, manifest_sha256 = _load_manifest(episode_slug)
    approval = _require_approved_revision(package_root, manifest, manifest_sha256)
    matching_jobs = [
        job
        for job in list_publish_jobs(package_root)
        if job.source_revision == manifest.revision
        and job.source_manifest_sha256 == manifest_sha256
    ]
    latest_job = matching_jobs[-1] if matching_jobs else None
    capabilities = _publish_capabilities()
    return _templates.TemplateResponse(
        request,
        "carousel_publish.html",
        {
            "episode_slug": episode_slug,
            "manifest": manifest,
            "manifest_sha256": manifest_sha256,
            "approval": approval,
            "capabilities": capabilities,
            "latest_job": latest_job,
            "latest_job_payload": (
                _publish_job_payload(episode_slug, latest_job) if latest_job else None
            ),
            "asset_version": _SHOSHO_ASSET_VERSION,
        },
    )


@page_router.post("/{episode_slug}/publish/jobs")
async def carousel_publish_create_job(
    request: Request,
    response: Response,
    episode_slug: str,
    nakama_auth: str | None = Cookie(None),
):
    """Create a local Stage 6 job; this endpoint never publishes externally."""

    if not check_auth(nakama_auth):
        raise HTTPException(status_code=401, detail="authentication required")
    package_root, manifest, manifest_sha256 = _load_manifest(episode_slug)
    form = await request.form()
    _assert_current_manifest(form, manifest_sha256)
    approval = _require_approved_revision(package_root, manifest, manifest_sha256)
    caption = str(form.get("caption", "")).strip()
    if not caption:
        raise HTTPException(status_code=400, detail="publish caption is required")
    if len(caption) > _MAX_CAPTION:
        raise HTTPException(status_code=400, detail="publish caption is too long")
    requested = [str(value) for value in form.getlist("platforms")]
    if not requested:
        raise HTTPException(status_code=400, detail="select at least one publish platform")
    if len(requested) != len(set(requested)):
        raise HTTPException(status_code=400, detail="publish platforms must be unique")
    capability_by_platform = {item.platform: item for item in _publish_capabilities()}
    unknown = sorted(set(requested) - set(capability_by_platform))
    if unknown:
        raise HTTPException(status_code=400, detail=f"unsupported publish platform: {unknown[0]}")
    targets = [capability_by_platform[platform] for platform in requested]
    job, created = create_or_get_publish_job(
        package_root=package_root,
        episode_id=manifest.episode_id,
        source_revision=manifest.revision,
        source_manifest_sha256=manifest_sha256,
        approval_revision_number=approval.revision_number,
        approved_at=approval.created_at,
        caption=caption,
        assets=_publish_assets(manifest),
        targets=targets,
    )
    response.status_code = 201 if created else 200
    return {**_publish_job_payload(episode_slug, job), "idempotent": not created}


@page_router.get(
    "/{episode_slug}/publish/jobs/{job_id}",
    response_model=CarouselPublishJobV1,
)
async def carousel_publish_job_status(
    episode_slug: str,
    job_id: str,
    nakama_auth: str | None = Cookie(None),
):
    if not check_auth(nakama_auth):
        raise HTTPException(status_code=401, detail="authentication required")
    package_root = _episode_dir(episode_slug) / "ig-carousel"
    try:
        return load_publish_job(publish_job_path(package_root, job_id))
    except (FileNotFoundError, OSError, ValueError, ValidationError) as error:
        raise HTTPException(status_code=404, detail="publish job not found") from error


@page_router.get("/{episode_slug}/media/{page_id}")
async def carousel_review_media(
    episode_slug: str,
    page_id: str,
    nakama_auth: str | None = Cookie(None),
):
    if not check_auth(nakama_auth):
        raise HTTPException(status_code=401, detail="authentication required")
    _, manifest, _ = _load_manifest(episode_slug)
    page = next((item for item in manifest.pages if item.page_id == page_id), None)
    if page is None:
        raise HTTPException(status_code=404, detail="carousel page not found")
    return FileResponse(page.image.path, media_type="image/png")


def _assert_current_manifest(form, manifest_sha256: str) -> None:
    if str(form.get("manifest_sha256", "")) != manifest_sha256:
        raise HTTPException(
            status_code=409,
            detail="carousel revision changed; reload before saving",
        )


def _append_feedback_revision(
    *,
    package_root: Path,
    manifest: CarouselReviewManifestV1,
    manifest_sha256: str,
    decisions: list[CarouselPageDecision],
    decision: str,
) -> None:
    feedback_store = _load_feedback(package_root, manifest.episode_id)
    feedback_store.revisions.append(
        CarouselFeedbackRevision(
            revision_number=len(feedback_store.revisions) + 1,
            created_at=datetime.now(UTC),
            carousel_revision=manifest.revision,
            manifest_sha256=manifest_sha256,
            decision=decision,
            pages=decisions,
        )
    )
    _write_feedback(_feedback_path(package_root), feedback_store)


@page_router.post(
    "/{episode_slug}/feedback",
    response_model=CarouselCorrectionJobV1,
    status_code=201,
)
async def carousel_review_feedback(
    request: Request,
    episode_slug: str,
    nakama_auth: str | None = Cookie(None),
):
    """Queue revision-bound corrections without invoking an executor."""

    if not check_auth(nakama_auth):
        raise HTTPException(status_code=401, detail="authentication required")
    package_root, manifest, manifest_sha256 = _load_manifest(episode_slug)
    form = await request.form()
    _assert_current_manifest(form, manifest_sha256)
    if any(str(form.get(f"status_{page.page_id}", "")) == "approved" for page in manifest.pages):
        raise HTTPException(status_code=400, detail="approval must use the approve endpoint")

    items: list[CarouselCorrectionItem] = []
    decisions: list[CarouselPageDecision] = []
    for page in manifest.pages:
        feedback = str(form.get(f"feedback_{page.page_id}", "")).strip()
        if len(feedback) > _MAX_FEEDBACK:
            raise HTTPException(status_code=400, detail=f"feedback too long: {page.page_id}")
        status = "needs_changes" if feedback else "pending"
        decisions.append(
            CarouselPageDecision(
                page_id=page.page_id,
                status=status,
                feedback=feedback,
                artifact_sha256=page.image.sha256,
            )
        )
        if feedback:
            items.append(
                CarouselCorrectionItem(
                    page_id=page.page_id,
                    artifact_sha256=page.image.sha256,
                    feedback=feedback,
                )
            )
    if not items:
        raise HTTPException(status_code=400, detail="at least one non-empty correction is required")

    active = [
        job
        for job in list_jobs(package_root)
        if job.source_revision == manifest.revision
        and job.source_manifest_sha256 == manifest_sha256
        and job.status in {"queued", "claimed", "in_progress"}
    ]
    if active:
        raise HTTPException(status_code=409, detail="correction job is still active")
    try:
        job = create_queued_job(
            package_root=package_root,
            episode_id=manifest.episode_id,
            source_revision=manifest.revision,
            source_manifest_sha256=manifest_sha256,
            feedback_items=items,
        )
    except CorrectionJobTransitionError as error:
        raise HTTPException(status_code=409, detail="correction job is still active") from error
    _append_feedback_revision(
        package_root=package_root,
        manifest=manifest,
        manifest_sha256=manifest_sha256,
        decisions=decisions,
        decision="draft",
    )
    return job


@page_router.get(
    "/{episode_slug}/jobs/{job_id}",
    response_model=CarouselCorrectionJobV1,
)
async def carousel_correction_job_status(
    episode_slug: str,
    job_id: str,
    nakama_auth: str | None = Cookie(None),
):
    if not check_auth(nakama_auth):
        raise HTTPException(status_code=401, detail="authentication required")
    package_root = _episode_dir(episode_slug) / "ig-carousel"
    try:
        job = load_job(correction_job_path(package_root, job_id))
    except (FileNotFoundError, OSError, ValueError, ValidationError) as error:
        raise HTTPException(status_code=404, detail="correction job not found") from error
    return job


@page_router.post("/{episode_slug}/approve")
async def carousel_review_approve(
    request: Request,
    episode_slug: str,
    nakama_auth: str | None = Cookie(None),
):
    """Record a review approval only; publishing remains a separate stage."""

    if not check_auth(nakama_auth):
        raise HTTPException(status_code=401, detail="authentication required")
    package_root, manifest, manifest_sha256 = _load_manifest(episode_slug)
    form = await request.form()
    _assert_current_manifest(form, manifest_sha256)
    if any(str(form.get(f"feedback_{page.page_id}", "")).strip() for page in manifest.pages):
        raise HTTPException(status_code=400, detail="approval cannot include correction feedback")
    feedback_store = _load_feedback(package_root, manifest.episode_id)
    already_approved = any(
        revision.carousel_revision == manifest.revision
        and revision.manifest_sha256 == manifest_sha256
        and revision.decision == "approved"
        for revision in feedback_store.revisions
    )
    if already_approved:
        return {
            "approved": True,
            "revision": manifest.revision,
            "manifest_sha256": manifest_sha256,
            "published": False,
            "publish_url": f"/bridge/ig-cards/{episode_slug}/publish",
        }
    active = [
        job
        for job in list_jobs(package_root)
        if job.source_revision == manifest.revision
        and job.source_manifest_sha256 == manifest_sha256
        and job.status in {"queued", "claimed", "in_progress"}
    ]
    if active:
        raise HTTPException(status_code=409, detail="correction job is still active")
    decisions = [
        CarouselPageDecision(
            page_id=page.page_id,
            status="approved",
            feedback="",
            artifact_sha256=page.image.sha256,
        )
        for page in manifest.pages
    ]
    _append_feedback_revision(
        package_root=package_root,
        manifest=manifest,
        manifest_sha256=manifest_sha256,
        decisions=decisions,
        decision="approved",
    )
    return {
        "approved": True,
        "revision": manifest.revision,
        "manifest_sha256": manifest_sha256,
        "published": False,
        "publish_url": f"/bridge/ig-cards/{episode_slug}/publish",
    }


@page_router.post("/{episode_slug}/decide")
async def carousel_review_decide(
    request: Request,
    episode_slug: str,
    nakama_auth: str | None = Cookie(None),
):
    if not check_auth(nakama_auth):
        return RedirectResponse(f"/login?next=/bridge/ig-cards/{episode_slug}", status_code=302)
    package_root, manifest, manifest_sha256 = _load_manifest(episode_slug)
    form = await request.form()
    if str(form.get("manifest_sha256", "")) != manifest_sha256:
        raise HTTPException(
            status_code=409,
            detail="carousel revision changed; reload before saving",
        )
    decisions = []
    for page in manifest.pages:
        status = str(form.get(f"status_{page.page_id}", "pending"))
        feedback = str(form.get(f"feedback_{page.page_id}", "")).strip()
        if len(feedback) > _MAX_FEEDBACK:
            raise HTTPException(status_code=400, detail=f"feedback too long: {page.page_id}")
        try:
            decisions.append(
                CarouselPageDecision(
                    page_id=page.page_id,
                    status=status,
                    feedback=feedback,
                    artifact_sha256=page.image.sha256,
                )
            )
        except ValidationError as error:
            raise HTTPException(status_code=400, detail=str(error)) from error
    feedback_store = _load_feedback(package_root, manifest.episode_id)
    decision = "approved" if all(item.status == "approved" for item in decisions) else "draft"
    feedback_store.revisions.append(
        CarouselFeedbackRevision(
            revision_number=len(feedback_store.revisions) + 1,
            created_at=datetime.now(UTC),
            carousel_revision=manifest.revision,
            manifest_sha256=manifest_sha256,
            decision=decision,
            pages=decisions,
        )
    )
    _write_feedback(_feedback_path(package_root), feedback_store)
    return RedirectResponse(f"/bridge/ig-cards/{episode_slug}?saved=1", status_code=303)
