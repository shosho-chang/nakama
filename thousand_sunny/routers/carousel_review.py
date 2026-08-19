"""Authenticated, episode-first review gate for Podcast IG Carousels."""

from __future__ import annotations

import hashlib
import hmac
import json
import mimetypes
import os
import re
from datetime import UTC, datetime
from functools import lru_cache
from pathlib import Path
from urllib.parse import quote

from fastapi import APIRouter, Cookie, HTTPException
from fastapi.responses import FileResponse, HTMLResponse, RedirectResponse, Response
from fastapi.templating import Jinja2Templates
from pydantic import ValidationError
from starlette.requests import Request

from agents.brook.podcast_carousel_render import _digest_files
from scripts.podcast_carousel_correction_job import (
    CorrectionJobTransitionError,
    correction_job_path,
    create_queued_job,
    list_jobs,
    load_job,
)
from shared.schemas.podcast_carousel import (
    CAROUSEL_DISPLAY_COPY_FIELDS,
    CarouselCopyEdit,
    CarouselCorrectionItem,
    CarouselCorrectionJobV1,
    CarouselEditorApplyRequest,
    CarouselFeedbackRevision,
    CarouselPageDecision,
    CarouselReviewFeedbackV1,
    CarouselReviewManifestV1,
    CoverLayoutOverride,
    PodcastCarouselCopySpecV1,
    receipt_for,
)
from thousand_sunny.auth import WEB_SECRET, check_auth

page_router = APIRouter(prefix="/bridge/ig-cards", tags=["bridge-ig-cards"])
_templates = Jinja2Templates(
    directory=str(Path(__file__).resolve().parent.parent / "templates" / "bridge")
)
_ROLE_LABELS = {
    "cover": "封面",
    "hook": "開場提問",
    "point": "重點",
    "quote": "引言",
    "cta": "收尾導流",
}
_MAX_FEEDBACK = 1200
_BASE_HREF_RE = re.compile(r'<base href="[^"]*">')


def _shosho_asset_version() -> str:
    static_dir = Path(__file__).resolve().parent.parent / "static" / "shosho"
    digest = hashlib.sha1()
    for name in (
        "tokens.css",
        "bridge.css",
        "bridge-pages.css",
        "carousel-review.css",
        "carousel-preview-bridge.js",
    ):
        asset = static_dir / name
        if asset.is_file():
            digest.update(asset.read_bytes())
    return digest.hexdigest()[:8]


_SHOSHO_ASSET_VERSION = _shosho_asset_version()


def _preview_asset_token(
    episode_slug: str,
    template_sha256: str,
    manifest_sha256: str,
) -> str:
    key = (WEB_SECRET or "nakama-local-preview").encode()
    scope = f"{episode_slug}:{template_sha256}:{manifest_sha256}".encode()
    return hmac.new(key, scope, hashlib.sha256).hexdigest()


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


def _contained_directory(path_value: str, root: Path) -> Path:
    path = Path(path_value)
    try:
        path.resolve().relative_to(root.resolve())
    except ValueError as error:
        raise HTTPException(
            status_code=422,
            detail="carousel directory escapes package root",
        ) from error
    if not path.is_dir():
        raise HTTPException(status_code=422, detail="carousel directory is missing")
    return path


@lru_cache(maxsize=64)
def _verified_snapshot_receipts(
    template_root_value: str,
    expected_sha256: str,
) -> dict[str, tuple[int, str]]:
    """Verify one immutable Template Snapshot and cache its exact file receipts."""

    template_root = Path(template_root_value).resolve(strict=True)
    files = [
        (path.relative_to(template_root).as_posix(), path)
        for path in template_root.rglob("*")
        if path.is_file()
    ]
    if _digest_files(files) != expected_sha256:
        raise HTTPException(status_code=409, detail="carousel template snapshot changed")
    receipts: dict[str, tuple[int, str]] = {}
    for relative, path in files:
        receipt = receipt_for(path)
        receipts[relative] = (receipt.bytes, receipt.sha256)
    return receipts


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


def _load_copy_spec(
    package_root: Path, manifest: CarouselReviewManifestV1
) -> PodcastCarouselCopySpecV1:
    copy_path = _contained_file(manifest.copy_spec.path, package_root)
    if receipt_for(copy_path) != manifest.copy_spec:
        raise HTTPException(status_code=409, detail="carousel copy spec changed")
    try:
        spec = PodcastCarouselCopySpecV1.model_validate_json(copy_path.read_text(encoding="utf-8"))
    except (OSError, ValidationError) as error:
        raise HTTPException(status_code=422, detail="invalid carousel copy spec") from error
    if spec.episode_id != manifest.episode_id or spec.revision != manifest.revision:
        raise HTTPException(status_code=422, detail="carousel copy spec identity mismatch")
    return spec


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


def _context(episode_slug: str) -> dict:
    package_root, manifest, manifest_sha256 = _load_manifest(episode_slug)
    spec = _load_copy_spec(package_root, manifest)
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
        copy_values = page.copy_page.model_dump(mode="json")
        rows.append(
            {
                "page": page,
                "role_label": _ROLE_LABELS[page.role],
                "status": decision.status if decision else "pending",
                "feedback": decision.feedback if decision else "",
                "editor_fields": [
                    {"name": name, "value": copy_values[name]}
                    for name in CAROUSEL_DISPLAY_COPY_FIELDS[page.role]
                    if copy_values.get(name) is not None
                ],
            }
        )
    return {
        "episode_slug": episode_slug,
        "manifest": manifest,
        "manifest_sha256": manifest_sha256,
        "rows": rows,
        "editor_available": manifest.render_input is not None,
        "editor_pages": [
            {
                "page_id": row["page"].page_id,
                "page_number": row["page"].page_number,
                "role": row["page"].role,
                "artifact_sha256": row["page"].image.sha256,
                "field_order": [item["name"] for item in row["editor_fields"]],
                "fields": {item["name"]: item["value"] for item in row["editor_fields"]},
            }
            for row in rows
        ],
        "decision_count": len(matching),
        "approved": bool(latest and latest.decision == "approved"),
        "cover_layout": (spec.layout_overrides.cover or CoverLayoutOverride()).model_dump(
            mode="json"
        ),
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


@page_router.get("/{episode_slug}/preview/{page_id}", response_class=HTMLResponse)
async def carousel_editor_preview(
    episode_slug: str,
    page_id: str,
    manifest_sha256: str,
    nakama_auth: str | None = Cookie(None),
):
    """Serve a receipt-verified render DOM in an opaque-origin sandbox."""

    if not check_auth(nakama_auth):
        raise HTTPException(status_code=401, detail="authentication required")
    package_root, manifest, current_sha256 = _load_manifest(episode_slug)
    if manifest_sha256 != current_sha256:
        raise HTTPException(status_code=409, detail="carousel revision changed; reload editor")
    page = next((item for item in manifest.pages if item.page_id == page_id), None)
    if page is None:
        raise HTTPException(status_code=404, detail="carousel page not found")
    if manifest.render_input is None:
        raise HTTPException(
            status_code=409,
            detail="legacy carousel revision has no trusted editor preview; render a new revision",
        )
    render_input = _contained_file(manifest.render_input.path, package_root)
    if receipt_for(render_input) != manifest.render_input:
        raise HTTPException(status_code=409, detail="carousel render input changed")
    source = render_input.read_text(encoding="utf-8")
    if len(_BASE_HREF_RE.findall(source)) != 1:
        raise HTTPException(status_code=422, detail="carousel preview has invalid base href")
    page_index = page.page_number - 1
    base = (
        f"/bridge/ig-cards/{quote(episode_slug, safe='')}/preview-assets/"
        f"{manifest.template.sha256}/"
        f"{_preview_asset_token(episode_slug, manifest.template.sha256, current_sha256)}/"
    )
    source = _BASE_HREF_RE.sub(f'<base href="{base}">', source, count=1)
    source = source.replace(
        "</head>",
        f'<script>history.replaceState(null,"",`?page={page_index}`);</script></head>',
        1,
    )
    if "</body>" not in source:
        raise HTTPException(status_code=422, detail="carousel preview has invalid body")
    bridge_path = (
        Path(__file__).resolve().parent.parent / "static" / "shosho" / "carousel-preview-bridge.js"
    )
    bridge_source = bridge_path.read_text(encoding="utf-8")
    source = source.replace(
        "</body>",
        f"<script>{bridge_source}</script></body>",
        1,
    )
    return HTMLResponse(
        source,
        headers={
            "Cache-Control": "no-store",
            "Content-Security-Policy": (
                "default-src 'none'; img-src 'self' data:; font-src 'self' data:; "
                "style-src 'self' 'unsafe-inline'; script-src 'self' 'unsafe-inline'; "
                "connect-src 'none'; form-action 'none'; object-src 'none'; "
                "base-uri 'self'; frame-ancestors 'self'"
            ),
        },
    )


@page_router.get("/{episode_slug}/preview-assets/{template_sha256}/{asset_token}/{asset_path:path}")
async def carousel_editor_preview_asset(
    episode_slug: str,
    template_sha256: str,
    asset_token: str,
    asset_path: str,
    nakama_auth: str | None = Cookie(None),
):
    package_root, manifest, manifest_sha256 = _load_manifest(episode_slug)
    expected_token = _preview_asset_token(episode_slug, manifest.template.sha256, manifest_sha256)
    if not check_auth(nakama_auth) and not hmac.compare_digest(asset_token, expected_token):
        raise HTTPException(status_code=401, detail="authentication required")
    if template_sha256 != manifest.template.sha256:
        raise HTTPException(status_code=409, detail="carousel template changed")
    if not hmac.compare_digest(asset_token, expected_token):
        raise HTTPException(status_code=409, detail="carousel preview token changed")
    template_root = _contained_directory(manifest.template.root, package_root)
    try:
        expected_receipts = _verified_snapshot_receipts(
            str(template_root.resolve(strict=True)),
            manifest.template.sha256,
        )
    except (FileNotFoundError, OSError, ValueError) as error:
        raise HTTPException(
            status_code=409,
            detail="carousel template snapshot changed",
        ) from error
    asset = _contained_file(str(template_root / asset_path), template_root).resolve(strict=True)
    relative = asset.relative_to(template_root.resolve(strict=True)).as_posix()
    expected_receipt = expected_receipts.get(relative)
    if expected_receipt is None:
        raise HTTPException(status_code=409, detail="carousel template snapshot changed")
    try:
        payload = asset.read_bytes()
    except OSError as error:
        raise HTTPException(
            status_code=409,
            detail="carousel template snapshot changed",
        ) from error
    actual_receipt = (len(payload), hashlib.sha256(payload).hexdigest())
    if not hmac.compare_digest(actual_receipt[1], expected_receipt[1]) or (
        actual_receipt[0] != expected_receipt[0]
    ):
        raise HTTPException(status_code=409, detail="carousel template snapshot changed")
    media_type = mimetypes.guess_type(asset.name)[0] or "application/octet-stream"
    return Response(content=payload, media_type=media_type)


def _assert_current_manifest(form, manifest_sha256: str) -> None:
    if str(form.get("manifest_sha256", "")) != manifest_sha256:
        raise HTTPException(
            status_code=409,
            detail="carousel revision changed; reload before saving",
        )


def _assert_manifest_sha256(value: str, manifest_sha256: str) -> None:
    if value != manifest_sha256:
        raise HTTPException(
            status_code=409,
            detail="carousel revision changed; reload before applying edits",
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
    "/{episode_slug}/apply-edits",
    response_model=CarouselCorrectionJobV1,
    status_code=201,
)
async def carousel_review_apply_edits(
    request: Request,
    episode_slug: str,
    nakama_auth: str | None = Cookie(None),
):
    """Validate structured edits and queue them without mutating rendered artifacts."""

    if not check_auth(nakama_auth):
        raise HTTPException(status_code=401, detail="authentication required")
    package_root, manifest, manifest_sha256 = _load_manifest(episode_slug)
    spec = _load_copy_spec(package_root, manifest)
    try:
        payload = CarouselEditorApplyRequest.model_validate(await request.json())
    except (json.JSONDecodeError, TypeError, ValidationError) as error:
        raise HTTPException(status_code=422, detail="invalid structured carousel edits") from error
    _assert_manifest_sha256(payload.manifest_sha256, manifest_sha256)

    feedback_store = _load_feedback(package_root, manifest.episode_id)
    if any(
        revision.carousel_revision == manifest.revision
        and revision.manifest_sha256 == manifest_sha256
        and revision.decision == "approved"
        for revision in feedback_store.revisions
    ):
        raise HTTPException(status_code=409, detail="approved carousel revision is read-only")

    manifest_by_id = {page.page_id: page for page in manifest.pages}
    spec_by_id = {page.page_id: page for page in spec.pages}
    effective_copy_edits: list[CarouselCopyEdit] = []
    for edit in payload.copy_edits:
        review_page = manifest_by_id.get(edit.page_id)
        source_page = spec_by_id.get(edit.page_id)
        if review_page is None or source_page is None:
            raise HTTPException(status_code=422, detail=f"unknown carousel page: {edit.page_id}")
        if edit.role != review_page.role or edit.role != source_page.role:
            raise HTTPException(
                status_code=422, detail=f"carousel page role changed: {edit.page_id}"
            )
        if edit.artifact_sha256 != review_page.image.sha256:
            raise HTTPException(status_code=409, detail=f"carousel page changed: {edit.page_id}")
        changed_fields = {
            name: value
            for name, value in edit.fields.items()
            if getattr(source_page, name) != value
        }
        if not changed_fields:
            continue
        source_payload = source_page.model_dump(mode="json")
        try:
            updated_page = type(source_page).model_validate({**source_payload, **changed_fields})
        except ValidationError as error:
            raise HTTPException(
                status_code=422, detail=f"invalid display copy for {edit.page_id}"
            ) from error
        if updated_page.evidence != source_page.evidence:
            raise HTTPException(status_code=422, detail="transcript evidence is immutable")
        effective_copy_edits.append(edit.model_copy(update={"fields": changed_fields}))

    effective_layout = payload.layout_overrides
    if effective_layout is not None:
        cover = manifest_by_id.get("cover")
        if cover is None or cover.role != "cover":
            raise HTTPException(status_code=422, detail="cover page is missing")
        if effective_layout.artifact_sha256 != cover.image.sha256:
            raise HTTPException(status_code=409, detail="carousel page changed: cover")
        current_layout = spec.layout_overrides.cover or CoverLayoutOverride()
        if effective_layout.values == current_layout:
            effective_layout = None

    if not effective_copy_edits and effective_layout is None:
        raise HTTPException(status_code=400, detail="at least one changed edit is required")
    try:
        return create_queued_job(
            package_root=package_root,
            episode_id=manifest.episode_id,
            source_revision=manifest.revision,
            source_manifest_sha256=manifest_sha256,
            copy_edits=effective_copy_edits,
            layout_overrides=effective_layout,
        )
    except CorrectionJobTransitionError as error:
        raise HTTPException(status_code=409, detail="correction job is still active") from error


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
