"""Projects routes — Brook synthesize server-side store API (ADR-021 §4) +
the /projects/{slug} review-mode page (issue #458).

Exposes:

- ``GET /api/projects/{slug}/synthesize`` → 200 returns the persisted
  ``BrookSynthesizeStore`` JSON for ``slug``. 404 when the store has not been
  materialised yet (Brook synthesize #459 is the only writer that creates
  the file).
- ``POST /api/projects/{slug}/synthesize`` → mutates the store. Body shape
  is a discriminated union over ``op``: ``append_user_action`` appends one
  ``UserAction`` to the audit trail, ``update_outline_final`` replaces the
  ``outline_final`` array, ``finalize_outline`` (issue #462, ADR-021 §3
  Step 4) regenerates ``outline_final`` from the cached evidence pool +
  user_actions without re-running KB retrieval. 404 when the slug has not
  been materialised yet — the API never bootstraps an empty store
  (ADR-021 §4).
- ``GET /projects/{slug}`` → renders the review-mode page (issue #458).
  HMAC cookie auth, 404 when no store, 302 to /login when unauthenticated.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Literal, Union

from fastapi import APIRouter, Cookie, Depends, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel, ConfigDict, Field

from shared import brook_synthesize_store
from shared.brook_synthesize_store import StoreNotFoundError
from shared.log import get_logger
from shared.schemas.brook_synthesize import (
    BrookSynthesizeStore,
    OutlineSection,
    UserAction,
)
from thousand_sunny.auth import check_auth, require_auth_or_key

logger = get_logger("nakama.web.projects")
router = APIRouter(prefix="/api/projects")

# Page route (HTML, /projects/{slug}) is on a separate router so we can mount
# it without the /api prefix; api router stays additive-only at /api/projects.
page_router = APIRouter()

_TEMPLATE_DIR = Path(__file__).resolve().parent.parent / "templates" / "projects"
_templates = Jinja2Templates(directory=str(_TEMPLATE_DIR))


# ── Request bodies ───────────────────────────────────────────────────────────


class AppendUserActionBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    op: Literal["append_user_action"]
    action: UserAction


class UpdateOutlineFinalBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    op: Literal["update_outline_final"]
    outline_final: list[OutlineSection]


class FinalizeOutlineBody(BaseModel):
    """Trigger Brook outline regeneration from cached evidence + user_actions.

    No payload — the slug + cached store is the whole input. Issue #462,
    ADR-021 §3 Step 4: "finalize → Brook 重新 generate outline（廣搜結果
    cached，不重撈）".
    """

    model_config = ConfigDict(extra="forbid")

    op: Literal["finalize_outline"]


SynthesizePostBody = Union[
    AppendUserActionBody,
    UpdateOutlineFinalBody,
    FinalizeOutlineBody,
]


class _PostEnvelope(BaseModel):
    """Discriminated union envelope. FastAPI uses the ``op`` discriminator to
    route the body to the right inner model — invalid ``op`` values become a
    422 ``ValidationError`` automatically."""

    model_config = ConfigDict(extra="forbid")

    body: SynthesizePostBody = Field(discriminator="op")


# ── Slug helpers ─────────────────────────────────────────────────────────────


def _validate_slug(slug: str) -> None:
    """Reject path-traversal-prone slugs at the route boundary.

    `brook_synthesize_store.store_path` also raises on bad slugs, but FastAPI
    URL decoding makes it cheap to surface a clear 400 here.
    """
    if not slug or "/" in slug or "\\" in slug or slug in (".", ".."):
        raise HTTPException(status_code=400, detail=f"invalid slug: {slug!r}")


# ── Routes ───────────────────────────────────────────────────────────────────


@router.get("/{slug}/synthesize", response_model=BrookSynthesizeStore)
async def get_synthesize(
    slug: str,
    _auth=Depends(require_auth_or_key),
) -> BrookSynthesizeStore:
    """Return the persisted store for `slug`. 404 when missing."""
    _validate_slug(slug)
    try:
        return brook_synthesize_store.read(slug)
    except StoreNotFoundError:
        raise HTTPException(
            status_code=404,
            detail=f"brook_synthesize store not found for slug={slug!r}",
        )


@router.post("/{slug}/synthesize", response_model=BrookSynthesizeStore)
async def post_synthesize(
    slug: str,
    body: SynthesizePostBody,
    _auth=Depends(require_auth_or_key),
) -> BrookSynthesizeStore:
    """Mutate the store. 404 when the slug has not been materialised yet."""
    _validate_slug(slug)
    if not brook_synthesize_store.exists(slug):
        # The API never creates a fresh store — that's Brook synthesize #459.
        # ADR-021 §4: "store must be created by Brook synthesize flow".
        raise HTTPException(
            status_code=404,
            detail=(
                f"brook_synthesize store not found for slug={slug!r}; "
                "create via Brook synthesize flow first"
            ),
        )

    try:
        if isinstance(body, AppendUserActionBody):
            return brook_synthesize_store.append_user_action(slug, body.action)
        if isinstance(body, FinalizeOutlineBody):
            # ADR-021 §3 Step 4: regenerate outline from cached pool + actions
            # (no KB re-search). Late import: pulling agents.* eagerly drags
            # the LLM router into Sunny startup, which the test fixtures
            # explicitly opt out of.
            from agents.brook.synthesize import regenerate_outline_final

            try:
                return regenerate_outline_final(slug)
            except ValueError as exc:
                # Empty pool or LLM contract violation — surface as 422 so
                # the client can show a meaningful error in writing-mode UI.
                raise HTTPException(status_code=422, detail=str(exc))
        # UpdateOutlineFinalBody — exhaustive on the union.
        return brook_synthesize_store.update_outline_final(slug, body.outline_final)
    except StoreNotFoundError:
        # Race: file removed between exists() and the mutate call.
        raise HTTPException(
            status_code=404,
            detail=f"brook_synthesize store disappeared for slug={slug!r}",
        )


# ── Page route (issue #458) ──────────────────────────────────────────────────


def _is_zh(s: str) -> bool:
    """Return True iff `s` contains any non-ASCII char (proxy for CJK)."""
    return any(ord(ch) > 127 for ch in s)


def _evidence_view_model(store: BrookSynthesizeStore) -> list[dict[str, Any]]:
    """Project the raw evidence pool into the flat shape the template expects.

    Real chunks (from ``shared.kb_hybrid_search.search``) are dicts with
    ``{path, title, heading, chunk_text, chunk_id, rrf_score, source_slug}``.
    Real chunks lack the design's ``authors / journal / year / excerptZh`` —
    we omit those gracefully (no faking).
    """
    # slug → list[section_id] for the section-filter data attribute
    slug_to_sections: dict[str, list[int]] = {}
    for sec in store.outline_draft:
        for ref in sec.evidence_refs:
            slug_to_sections.setdefault(ref, []).append(sec.section)

    cards: list[dict[str, Any]] = []
    for item in store.evidence_pool:
        first: Any = item.chunks[0] if item.chunks else None
        # ``first`` is ``Any`` per schema; tolerate both dict and object access.
        if isinstance(first, dict):
            title = first.get("title")
            heading = first.get("heading")
            chunk_text = first.get("chunk_text")
            rrf = first.get("rrf_score")
        elif first is not None:
            title = getattr(first, "title", None)
            heading = getattr(first, "heading", None)
            chunk_text = getattr(first, "chunk_text", None)
            rrf = getattr(first, "rrf_score", None)
        else:
            title = heading = chunk_text = rrf = None

        excerpt = (chunk_text or "").strip()
        if len(excerpt) > 400:
            excerpt = excerpt[:400] + "…"

        relevance_pct: int | None = None
        if isinstance(rrf, (int, float)):
            relevance_pct = max(0, min(100, round(float(rrf) * 100)))

        cards.append(
            {
                "slug": item.slug,
                "paper_title": title or item.slug,
                "heading_in_paper": heading or "",
                "excerpt": excerpt,
                "relevance_pct": relevance_pct,
                "hit_reason": item.hit_reason,
                "sections_referenced": ",".join(
                    str(s) for s in slug_to_sections.get(item.slug, [])
                ),
            }
        )
    return cards


@page_router.get("/projects/{slug}", response_class=HTMLResponse)
async def project_review_page(
    request: Request,
    slug: str,
    nakama_auth: str | None = Cookie(None),
):
    """Render the /projects/{slug} review-mode page.

    Redirects to /login when the HMAC cookie is missing or invalid (mirrors
    /books/{book_id}). 404 when the BrookSynthesizeStore has not been
    materialised yet — only Brook synthesize #459 creates it.
    """
    if not check_auth(nakama_auth):
        return RedirectResponse(f"/login?next=/projects/{slug}", status_code=302)
    _validate_slug(slug)
    if not brook_synthesize_store.exists(slug):
        raise HTTPException(
            status_code=404,
            detail=f"brook_synthesize store not found for slug={slug!r}",
        )
    store = brook_synthesize_store.read(slug)
    keyword_pairs = [(k, _is_zh(k)) for k in store.keywords]
    evidence_cards = _evidence_view_model(store)
    # Issue #462: in writing mode the user reads the finalised outline; the
    # draft is stale once finalize ran. Pick the canonical outline for the
    # template loop in one place so the template doesn't branch.
    is_writing_mode = bool(store.outline_final)
    outline_for_render = (
        list(store.outline_final) if is_writing_mode else list(store.outline_draft)
    )
    return _templates.TemplateResponse(
        request,
        "review.html",
        {
            "store": store,
            "keyword_pairs": keyword_pairs,
            "evidence_cards": evidence_cards,
            "is_writing_mode": is_writing_mode,
            "outline_for_render": outline_for_render,
        },
    )
