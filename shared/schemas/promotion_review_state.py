"""Promotion Review UI state schema (ADR-024 Slice 8 / issue #516).

Pure pydantic value-object describing one entry in the Thousand Sunny
"pending review" list view. Derived from #511 ``PreflightReport`` plus a
boolean indicating whether a ``PromotionManifest`` already exists on disk.

Slice 8 is UI + service composition. The state object lives here (in
``shared/schemas``) so the service facade can construct it without
importing FastAPI / template packages — keeps domain pure per ADR-024
ownership boundary.

Closed-set extension protocol mirrors #509 / #511 / #512: every ``Literal``
enum is frozen for ``schema_version=1``. Silent extension is forbidden.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict

from shared.schemas.preflight_report import PreflightAction

ManifestUiStatus = Literal["needs_review", "partial", "complete", "failed"]
"""Closed for ``schema_version=1``. Mirrors
``shared.schemas.promotion_manifest.ManifestStatus``; duplicated here as a
display-side closed enum so the UI surface does not import the manifest
module's status enum directly (the service decouples them).
"""


class PromotionReviewState(BaseModel):
    """One entry in the Thousand Sunny "pending review" list view.

    Built from a ``PreflightReport`` (#511) plus a peek at whether a
    ``PromotionManifest`` already exists on disk for the same source.

    Frozen value-object — emit a new state on re-build; do not mutate.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal[1] = 1
    """First field — closed-set extension protocol marker. Adding any new
    Literal member or invariant requires bumping this and updating downstream
    consumers (templates, list view)."""

    source_id: str
    """Stable namespace-qualified Reading Source id from #509 (e.g.
    ``ebook:abc123`` or ``inbox:Inbox/kb/foo.md``). Schema does NOT parse or
    validate format; transport string only. Routes base64url-encode this
    for the URL path per Brief §3."""

    primary_lang: str
    """BCP-47 short tag mirrored from ``ReadingSource.primary_lang``."""

    preflight_action: PreflightAction
    """Mirrors ``PreflightReport.recommended_action``. The list view filters
    by ``action ∈ {proceed_full_promotion, proceed_with_warnings}``; the
    schema permits all five values so callers can build state for inspection
    surfaces too."""

    preflight_summary: str
    """Short synopsis of the preflight result (≤ 200 chars). Plain text,
    no markup. Caller composes from ``PreflightReport`` size + reasons."""

    has_existing_manifest: bool
    """True when a ``PromotionManifest`` already exists on disk for this
    source. Drives the "Start review" vs "Resume review" affordance in the
    list view."""

    manifest_status: ManifestUiStatus | None = None
    """``None`` when no manifest exists yet; otherwise mirrors
    ``PromotionManifest.status``. The list view renders this as a status
    pill; ``None`` means the source hasn't been reviewed."""
