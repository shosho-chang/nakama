"""Projects routes — Brook synthesize server-side store API (ADR-021 §4).

Exposes `/api/projects/{slug}/synthesize` with two operations:

- ``GET``  → 200 returns the persisted ``BrookSynthesizeStore`` JSON for
  ``slug``. 404 when the store has not been materialised yet (Brook
  synthesize #459 is the only writer that creates the file).
- ``POST`` → mutates the store. Body shape is a discriminated union over
  ``op``: ``append_user_action`` appends one ``UserAction`` to the audit
  trail, ``update_outline_final`` replaces the ``outline_final`` array.
  404 when the slug has not been materialised yet — the API never
  bootstraps an empty store; that is Brook synthesize's job (ADR-021 §4).

The Web UI panel route (issue #458) is intentionally out of scope here —
this module is the additive API layer only.
"""

from __future__ import annotations

from typing import Literal, Union

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, ConfigDict, Field

from shared import brook_synthesize_store
from shared.brook_synthesize_store import StoreNotFoundError
from shared.log import get_logger
from shared.schemas.brook_synthesize import (
    BrookSynthesizeStore,
    OutlineSection,
    UserAction,
)
from thousand_sunny.auth import require_auth_or_key

logger = get_logger("nakama.web.projects")
router = APIRouter(prefix="/api/projects")


# ── Request bodies ───────────────────────────────────────────────────────────


class AppendUserActionBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    op: Literal["append_user_action"]
    action: UserAction


class UpdateOutlineFinalBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    op: Literal["update_outline_final"]
    outline_final: list[OutlineSection]


SynthesizePostBody = Union[AppendUserActionBody, UpdateOutlineFinalBody]


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
        # UpdateOutlineFinalBody — exhaustive on the union.
        return brook_synthesize_store.update_outline_final(slug, body.outline_final)
    except StoreNotFoundError:
        # Race: file removed between exists() and the mutate call.
        raise HTTPException(
            status_code=404,
            detail=f"brook_synthesize store disappeared for slug={slug!r}",
        )
