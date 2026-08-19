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
    PublishJobTransitionError,
    create_or_get_publish_job,
    list_publish_jobs,
    load_publish_job,
    publish_job_path,
    publish_release_lock,
    published_publish_platforms,
    republish_required_platforms,
    supersede_queued_publish_job,
    unfinished_publish_platforms,
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
_INSTAGRAM_MAX_CAPTION = 2200
_PUBLISH_STATUS_LABELS = {
    "queued": "等待執行者認領",
    "claimed": "執行者已認領",
    "in_progress": "發布進行中",
    "completed": "發布已完成",
    "failed": "發布未完成，可重試",
    "superseded": "發布核准已撤回",
}


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


def _publish_capabilities(
    publish_compatibility: str, asset_count: int
) -> list[CarouselPublishTarget]:
    required_meta_settings = (
        "META_GRAPH_API_VERSION",
        "META_PAGE_ID",
        "META_IG_USER_ID",
        "META_PAGE_ACCESS_TOKEN",
        "META_MEDIA_R2_ACCOUNT_ID",
        "META_MEDIA_R2_ACCESS_KEY_ID",
        "META_MEDIA_R2_SECRET_ACCESS_KEY",
        "META_MEDIA_R2_BUCKET",
    )
    missing_meta_settings = [
        name for name in required_meta_settings if not os.environ.get(name, "").strip()
    ]
    meta_configured = publish_compatibility == "api_compatible" and not missing_meta_settings
    meta_strategy = "meta_api" if meta_configured else "agent_browser"
    meta_state = "configured" if meta_configured else "agent_browser_required"
    meta_capability = "meta_api" if meta_configured else "browser_session"
    if publish_compatibility == "manual_only":
        meta_note = "這組輪播需使用已登入的瀏覽器發布；即使已設定 Meta 發布連線，也不能改走 API。"
    else:
        meta_note = (
            "已設定 Meta 發布連線；認領的 agent 必須具備對應發布權限。"
            if meta_configured
            else (
                "Meta API 尚未完整設定（缺少："
                + "、".join(missing_meta_settings)
                + "）；請使用已登入該平台的瀏覽器工作階段。"
            )
        )
    youtube_eligible = asset_count <= 10
    youtube_reason = (
        None if youtube_eligible else "YouTube Community 最多接受 10 張圖片；這組輪播已超過上限。"
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
                youtube_reason
                or "YouTube Community 沒有可用的自動發布端點；"
                "請使用已登入的瀏覽器，並在送出前人工確認。"
            ),
            eligible=youtube_eligible,
            ineligibility_reason=youtube_reason,
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


def _publish_job_payload(
    episode_slug: str,
    package_root: Path,
    job: CarouselPublishJobV1,
) -> dict:
    unfinished = set(unfinished_publish_platforms(job))
    capabilities = sorted(
        {
            capability
            for target in job.targets
            if target.platform in unfinished
            for capability in target.required_executor_capabilities
        }
    )
    job_file = publish_job_path(package_root, job.job_id)

    def claim_command(executor: str) -> str:
        capability_args = "".join(f" --capability {capability}" for capability in capabilities)
        return (
            "python scripts/podcast_carousel_publish_job.py claim "
            f'"{job_file}" --executor {executor} '
            f"--executor-id <agent-id>{capability_args}"
        )

    return {
        **job.model_dump(mode="json"),
        "status_url": f"/bridge/ig-cards/{episode_slug}/publish/jobs/{job.job_id}",
        "claim_commands": {
            "codex": claim_command("codex"),
            "claude_code": claim_command("claude_code"),
        },
    }


def _matching_publish_jobs(
    package_root: Path,
    manifest: CarouselReviewManifestV1,
    manifest_sha256: str,
) -> list[CarouselPublishJobV1]:
    return [
        job
        for job in list_publish_jobs(package_root)
        if job.source_revision == manifest.revision
        and job.source_manifest_sha256 == manifest_sha256
    ]


def _published_platforms(jobs: list[CarouselPublishJobV1]) -> list[str]:
    return sorted({platform for job in jobs for platform in published_publish_platforms(job)})


def _validated_publish_request(form, manifest: CarouselReviewManifestV1):
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
    if "instagram" in requested and len(caption) > _INSTAGRAM_MAX_CAPTION:
        raise HTTPException(
            status_code=400,
            detail="Instagram caption cannot exceed 2,200 characters",
        )
    capability_by_platform = {
        item.platform: item
        for item in _publish_capabilities(manifest.publish_compatibility, len(manifest.pages))
    }
    unknown = sorted(set(requested) - set(capability_by_platform))
    if unknown:
        raise HTTPException(status_code=400, detail=f"unsupported publish platform: {unknown[0]}")
    targets = [capability_by_platform[platform] for platform in requested]
    ineligible = next((target for target in targets if not target.eligible), None)
    if ineligible is not None:
        raise HTTPException(
            status_code=400,
            detail=ineligible.ineligibility_reason or "publish platform is not eligible",
        )
    return caption, targets


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
    matching_publish_jobs = _matching_publish_jobs(package_root, manifest, manifest_sha256)
    latest_publish_job = matching_publish_jobs[-1] if matching_publish_jobs else None
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
        "latest_publish_job": latest_publish_job,
        "latest_publish_status_label": (
            _PUBLISH_STATUS_LABELS[latest_publish_job.status]
            if latest_publish_job
            else "尚未建立發布工作"
        ),
        "publish_url": f"/bridge/ig-cards/{episode_slug}/publish",
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
    matching_jobs = _matching_publish_jobs(package_root, manifest, manifest_sha256)
    latest_job = matching_jobs[-1] if matching_jobs else None
    capabilities = _publish_capabilities(manifest.publish_compatibility, len(manifest.pages))
    republish_required = (
        republish_required_platforms(
            package_root=package_root,
            source_revision=manifest.revision,
            source_manifest_sha256=manifest_sha256,
            source_publish_compatibility=manifest.publish_compatibility,
            caption=latest_job.caption,
            targets=latest_job.targets,
        )
        if latest_job is not None
        else []
    )
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
            "republish_required_platforms": republish_required,
            "latest_job_payload": (
                _publish_job_payload(episode_slug, package_root, latest_job) if latest_job else None
            ),
            "asset_version": _SHOSHO_ASSET_VERSION,
        },
    )


@page_router.post("/{episode_slug}/publish/preflight")
async def carousel_publish_preflight(
    request: Request,
    episode_slug: str,
    nakama_auth: str | None = Cookie(None),
):
    """Revalidate the exact Stage 6 request immediately before submission."""

    if not check_auth(nakama_auth):
        raise HTTPException(status_code=401, detail="authentication required")
    form = await request.form()
    package_root = _episode_dir(episode_slug) / "ig-carousel"
    with publish_release_lock(package_root):
        package_root, manifest, manifest_sha256 = _load_manifest(episode_slug)
        _assert_current_manifest(form, manifest_sha256)
        _require_approved_revision(package_root, manifest, manifest_sha256)
        caption, targets = _validated_publish_request(form, manifest)
        required = republish_required_platforms(
            package_root=package_root,
            source_revision=manifest.revision,
            source_manifest_sha256=manifest_sha256,
            source_publish_compatibility=manifest.publish_compatibility,
            caption=caption,
            targets=targets,
        )
    return {
        "source_revision": manifest.revision,
        "source_manifest_sha256": manifest_sha256,
        "republish_required_platforms": required,
    }


@page_router.get("/{episode_slug}/publish/context")
async def carousel_publish_context(
    episode_slug: str,
    nakama_auth: str | None = Cookie(None),
):
    """Return fresh release context for client-side republish revalidation."""

    if not check_auth(nakama_auth):
        raise HTTPException(status_code=401, detail="authentication required")
    package_root, manifest, manifest_sha256 = _load_manifest(episode_slug)
    _require_approved_revision(package_root, manifest, manifest_sha256)
    matching_jobs = _matching_publish_jobs(package_root, manifest, manifest_sha256)
    latest_job = matching_jobs[-1] if matching_jobs else None
    return {
        "source_revision": manifest.revision,
        "source_manifest_sha256": manifest_sha256,
        "published_platforms": _published_platforms(matching_jobs),
        "latest_job": (
            _publish_job_payload(episode_slug, package_root, latest_job) if latest_job else None
        ),
    }


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
    form = await request.form()
    allow_republish = str(form.get("confirm_republish", "")).lower() == "true"
    package_root = _episode_dir(episode_slug) / "ig-carousel"
    with publish_release_lock(package_root):
        package_root, manifest, manifest_sha256 = _load_manifest(episode_slug)
        _assert_current_manifest(form, manifest_sha256)
        approval = _require_approved_revision(package_root, manifest, manifest_sha256)
        caption, targets = _validated_publish_request(form, manifest)
        try:
            job, created = create_or_get_publish_job(
                package_root=package_root,
                episode_id=manifest.episode_id,
                source_revision=manifest.revision,
                source_manifest_sha256=manifest_sha256,
                source_publish_compatibility=manifest.publish_compatibility,
                approval_revision_number=approval.revision_number,
                approved_at=approval.created_at,
                caption=caption,
                assets=_publish_assets(manifest),
                targets=targets,
                allow_republish=allow_republish,
            )
        except PublishJobTransitionError as error:
            raise HTTPException(status_code=409, detail=str(error)) from error
    response.status_code = 201 if created else 200
    return {
        **_publish_job_payload(episode_slug, package_root, job),
        "idempotent": not created,
    }


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

    with publish_release_lock(package_root):
        active = [
            correction_job
            for correction_job in list_jobs(package_root)
            if correction_job.source_revision == manifest.revision
            and correction_job.source_manifest_sha256 == manifest_sha256
            and correction_job.status in {"queued", "claimed", "in_progress"}
        ]
        if active:
            raise HTTPException(status_code=409, detail="correction job is still active")

        matching_publish = [
            publish_job
            for publish_job in list_publish_jobs(package_root)
            if publish_job.source_revision == manifest.revision
            and publish_job.source_manifest_sha256 == manifest_sha256
        ]
        if any(
            publish_job.status in {"claimed", "in_progress"} for publish_job in matching_publish
        ):
            raise HTTPException(
                status_code=409,
                detail="publish job is active; fail it before requesting corrections",
            )
        for publish_job in matching_publish:
            if publish_job.status == "queued":
                supersede_queued_publish_job(
                    publish_job_path(package_root, publish_job.job_id),
                    reason="new correction feedback revoked the release approval",
                    release_locked=True,
                )

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
    with publish_release_lock(package_root):
        feedback_store = _load_feedback(package_root, manifest.episode_id)
        active = [
            job
            for job in list_jobs(package_root)
            if job.source_revision == manifest.revision
            and job.source_manifest_sha256 == manifest_sha256
            and job.status in {"queued", "claimed", "in_progress"}
        ]
        if active:
            raise HTTPException(status_code=409, detail="correction job is still active")
        matching_revisions = [
            revision
            for revision in feedback_store.revisions
            if revision.carousel_revision == manifest.revision
            and revision.manifest_sha256 == manifest_sha256
        ]
        latest_matching = matching_revisions[-1] if matching_revisions else None
        if latest_matching is None or latest_matching.decision != "approved":
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
    decision = "approved" if all(item.status == "approved" for item in decisions) else "draft"
    with publish_release_lock(package_root):
        if decision == "approved":
            active_corrections = [
                job
                for job in list_jobs(package_root)
                if job.source_revision == manifest.revision
                and job.source_manifest_sha256 == manifest_sha256
                and job.status in {"queued", "claimed", "in_progress"}
            ]
            if active_corrections:
                raise HTTPException(status_code=409, detail="correction job is still active")
        else:
            matching_publish = [
                job
                for job in list_publish_jobs(package_root)
                if job.source_revision == manifest.revision
                and job.source_manifest_sha256 == manifest_sha256
            ]
            if any(job.status in {"claimed", "in_progress"} for job in matching_publish):
                raise HTTPException(
                    status_code=409,
                    detail="publish job is active; fail it before saving a review draft",
                )
            for job in matching_publish:
                if job.status == "queued":
                    supersede_queued_publish_job(
                        publish_job_path(package_root, job.job_id),
                        reason="new review draft revoked the release approval",
                        release_locked=True,
                    )
        _append_feedback_revision(
            package_root=package_root,
            manifest=manifest,
            manifest_sha256=manifest_sha256,
            decisions=decisions,
            decision=decision,
        )
    return RedirectResponse(f"/bridge/ig-cards/{episode_slug}?saved=1", status_code=303)
