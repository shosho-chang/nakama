"""Authenticated human selection gate for long-form highlight candidates."""

from __future__ import annotations

import hashlib
import os
from pathlib import Path

from fastapi import APIRouter, Cookie, HTTPException
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from starlette.requests import Request

from shared.highlight_shortlist import (
    HighlightDataError,
    append_review_feedback,
    collect,
    load_review_feedback,
    write_winners,
)
from thousand_sunny.auth import check_auth

page_router = APIRouter(prefix="/bridge/highlights", tags=["bridge-highlights"])
_templates = Jinja2Templates(directory=str(Path(__file__).resolve().parent.parent / "templates" / "bridge"))
_MAX_CANDIDATES = 5
_MAX_FEEDBACK = 2000


def _shosho_asset_version() -> str:
    static_dir = Path(__file__).resolve().parent.parent / "static" / "shosho"
    digest = hashlib.sha1()
    for name in ("tokens.css", "bridge.css", "bridge-pages.css"):
        asset = static_dir / name
        if asset.is_file():
            digest.update(asset.read_bytes())
    return digest.hexdigest()[:8]


_SHOSHO_ASSET_VERSION = _shosho_asset_version()


def _episode_dir(episode_slug: str) -> Path:
    """Resolve a single episode directory from the explicit Bridge env only."""
    # Episode folders are named by the editor and may include Chinese, spaces and
    # punctuation (for example ``20260721 鄭國威``).  Reject path syntax instead
    # of restricting the human-facing name to ASCII, then prove containment below.
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
    # ``resolve`` makes the single-segment rule defensible if a directory is a symlink.
    try:
        candidate.resolve().relative_to(root.resolve())
    except ValueError as exc:
        raise HTTPException(status_code=404, detail="invalid episode slug") from exc
    if not candidate.is_dir():
        raise HTTPException(status_code=404, detail=f"episode not found: {episode_slug}")
    return candidate


def _context(episode_slug: str) -> dict:
    episode_dir = _episode_dir(episode_slug)
    highlights_dir = episode_dir / "highlights"
    try:
        rows = collect(highlights_dir, "long")
        feedback_audit = load_review_feedback(highlights_dir)
    except HighlightDataError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    shown = rows[:_MAX_CANDIDATES]
    if not shown:
        raise HTTPException(status_code=422, detail="no long-form candidates available")
    latest = feedback_audit["decisions"][-1] if feedback_audit["decisions"] else {}
    latest_feedback = latest.get("feedback", {}) if isinstance(latest, dict) else {}
    if not isinstance(latest_feedback, dict):
        latest_feedback = {}
    selected_ids = latest.get("selected_ids", []) if isinstance(latest, dict) else []
    if not isinstance(selected_ids, list):
        selected_ids = []
    selected_set = {value for value in selected_ids if isinstance(value, str)}
    for row in shown:
        row["feedback"] = str(latest_feedback.get(row["id"], ""))[:_MAX_FEEDBACK]
        row["selected"] = row["id"] in selected_set
    return {
        "episode_slug": episode_slug,
        "rows": shown,
        "selected_ids": selected_set,
        "decision_count": len(feedback_audit["decisions"]),
        "asset_version": _SHOSHO_ASSET_VERSION,
    }


@page_router.get("/{episode_slug}", response_class=HTMLResponse)
async def highlight_review_board(
    request: Request,
    episode_slug: str,
    saved: bool = False,
    nakama_auth: str | None = Cookie(None),
):
    if not check_auth(nakama_auth):
        return RedirectResponse(f"/login?next=/bridge/highlights/{episode_slug}", status_code=302)
    context = _context(episode_slug)
    context["saved"] = saved
    return _templates.TemplateResponse(request, "highlight_review.html", context)


@page_router.post("/{episode_slug}/decide")
async def highlight_review_decide(
    request: Request,
    episode_slug: str,
    nakama_auth: str | None = Cookie(None),
):
    if not check_auth(nakama_auth):
        return RedirectResponse("/login?next=/bridge/highlights", status_code=302)
    context = _context(episode_slug)
    form = await request.form()
    selected_ids = [str(value) for value in form.getlist("candidate_id")]
    if len(selected_ids) != 3 or len(set(selected_ids)) != 3:
        raise HTTPException(status_code=400, detail="select exactly three distinct long-form candidates")
    by_id = {row["id"]: row for row in context["rows"]}
    unknown = [candidate_id for candidate_id in selected_ids if candidate_id not in by_id]
    if unknown:
        raise HTTPException(status_code=400, detail=f"candidate is not in this review shortlist: {unknown}")

    override_ids = {str(value) for value in form.getlist("override_veto")}
    vetoed = {candidate_id for candidate_id in selected_ids if by_id[candidate_id]["brand_severity"] == "veto"}
    if not vetoed.issubset(override_ids):
        raise HTTPException(status_code=400, detail="brand-veto candidates require an explicit override_veto")
    feedback: dict[str, str] = {}
    # Keep the editor's rejection rationale too: it is the most useful signal for
    # tuning future shortlists, and the form intentionally exposes it on all cards.
    for candidate_id in by_id:
        value = str(form.get(f"feedback_{candidate_id}", "")).strip()
        if len(value) > _MAX_FEEDBACK:
            raise HTTPException(status_code=400, detail=f"feedback for {candidate_id} exceeds {_MAX_FEEDBACK} characters")
        if value:
            feedback[candidate_id] = value

    highlights_dir = _episode_dir(episode_slug) / "highlights"
    try:
        # Validate and prepare every input before either durable write. Each write
        # itself is atomic; the audit entry preserves earlier decisions.
        write_winners(highlights_dir, context["rows"], selected_ids, picked_by="修修 (Bridge highlight review gate)")
        append_review_feedback(
            highlights_dir,
            selected_ids=selected_ids,
            feedback=feedback,
            overridden_veto_ids=sorted(vetoed),
        )
    except HighlightDataError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return RedirectResponse(f"/bridge/highlights/{episode_slug}?saved=1", status_code=303)
