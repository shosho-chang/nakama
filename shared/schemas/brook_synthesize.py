"""Brook synthesize server-side store schema (ADR-021 §4).

Persisted as `data/brook_synthesize/{project_slug}.json`. The store holds the
evidence pool + outline draft + user actions produced by Brook synthesize
(issue #459) and consumed by the projects Web UI (issue #458). The vault is
intentionally not touched — see ADR-021 §4 (Fork B = U: server-side sidecar).

Schema rules (per `docs/principles/schemas.md`):
- `extra="forbid"` on every model so unknown keys fail loudly during a
  schema-version bump.
- `Literal` for the closed `UserAction.action` enum. ADR-021 §4 names two
  actions: `reject_from_section` (drop one evidence from one section, leave it
  in the pool for other sections) and `reject_evidence_entirely` (drop the
  evidence everywhere; downranked-not-deleted per the Reject paragraph).
- `schema_version: Literal[1]` on the top-level container — increment when the
  on-disk shape changes incompatibly.

The store is mutated by the Sunny `/api/projects/{slug}/synthesize` route
(GET/POST). Brook synthesize (#459) is the only writer that creates the
file; the API refuses to materialise an empty store on POST when the slug
does not exist (AC: "POST → 404/422").
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

# The two reject grains documented in ADR-021 §4 "Reject 機制":
# - reject_from_section: per-section reject (evidence stays in pool for other sections)
# - reject_evidence_entirely: pool-wide reject (downranked on next re-run, not hard-hidden)
UserActionType = Literal["reject_from_section", "reject_evidence_entirely"]


class EvidencePoolItem(BaseModel):
    """One candidate retrieval hit Brook surfaced during 廣搜.

    `chunks` is left as `list[Any]` because the chunk shape is owned by the
    retrieval layer (kb_search / kb_hybrid_search) and ADR-021 §2 explicitly
    re-shapes it; pinning the shape here would force a v2 bump every time
    that wrapper grows a field. See ADR-021 §2 amendments.
    """

    model_config = ConfigDict(extra="forbid")

    slug: str
    chunks: list[Any] = Field(default_factory=list)
    hit_reason: str = ""


class OutlineSection(BaseModel):
    """One section of the outline draft / final.

    `evidence_refs` are evidence slugs (matching `EvidencePoolItem.slug`), not
    chunk ids — the Web UI resolves slug → chunks via the parent store.
    """

    model_config = ConfigDict(extra="forbid")

    section: int
    heading: str
    evidence_refs: list[str] = Field(default_factory=list)
    # ADR-027 §Decision 4: optional Zoro trending-angle correspondence.
    # Lists which input ``trending_angles`` (passed to ``synthesize``) this
    # section actually addresses. Empty when no angles supplied OR when the
    # LLM did not match any to this section. Backwards-compatible default.
    trending_match: list[str] = Field(default_factory=list)


class UserAction(BaseModel):
    """One reject/approve action recorded for the synthesize session.

    Append-only audit trail. The Web UI (issue #458) translates these into
    the effective outline_final on POST; we keep both so the user can see
    what got dropped.

    `section` is required for `reject_from_section` and ignored (typically
    omitted / set to 0) for `reject_evidence_entirely`. We do not enforce
    that here to keep the contract permissive — the API caller is in the
    same trust boundary as the writer.
    """

    model_config = ConfigDict(extra="forbid")

    timestamp: str
    action: UserActionType
    evidence_slug: str
    section: int | None = None


class BrookSynthesizeStore(BaseModel):
    """Top-level container for `data/brook_synthesize/{project_slug}.json`."""

    model_config = ConfigDict(extra="forbid")

    project_slug: str
    topic: str = ""
    keywords: list[str] = Field(default_factory=list)
    evidence_pool: list[EvidencePoolItem] = Field(default_factory=list)
    outline_draft: list[OutlineSection] = Field(default_factory=list)
    user_actions: list[UserAction] = Field(default_factory=list)
    outline_final: list[OutlineSection] = Field(default_factory=list)
    # ADR-027 §Decision 4: Zoro trending angles handed to ``synthesize`` that
    # did NOT find a strong correspondence in any drafted section. Surfaced
    # to 修修 as a warning (and as reverse signal for Robin discovery). Always
    # present; empty list when no angles were supplied or all matched.
    unmatched_trending_angles: list[str] = Field(default_factory=list)
    schema_version: Literal[1] = 1
    updated_at: str = ""


__all__ = [
    "BrookSynthesizeStore",
    "EvidencePoolItem",
    "OutlineSection",
    "UserAction",
    "UserActionType",
]
