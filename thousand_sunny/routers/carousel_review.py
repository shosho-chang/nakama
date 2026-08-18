"""Authenticated, episode-first review gate for Podcast IG Carousels."""

from __future__ import annotations

import hashlib
import json
import os
from datetime import UTC, datetime
from pathlib import Path

from fastapi import APIRouter, Cookie, HTTPException
from fastapi.responses import FileResponse, HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from pydantic import ValidationError
from starlette.requests import Request

from shared.schemas.podcast_carousel import (
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


def _shosho_asset_version() -> str:
    static_dir = Path(__file__).resolve().parent.parent / "static" / "shosho"
    digest = hashlib.sha1()
    for name in ("tokens.css", "bridge.css", "bridge-pages.css", "carousel-review.css"):
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


def _context(episode_slug: str) -> dict:
    package_root, manifest, manifest_sha256 = _load_manifest(episode_slug)
    feedback = _load_feedback(package_root, manifest.episode_id)
    latest = feedback.revisions[-1] if feedback.revisions else None
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
        "decision_count": len(feedback.revisions),
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
