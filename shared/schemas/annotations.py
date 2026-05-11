"""Pydantic schemas for annotation sets — v1 (paper sources), v2 (book/CFI sources),
and v3 (W3C Web Annotation shape — target + body in a single record).

ADR-017: annotation data lives in KB/Annotations/{slug}.md; v1 uses text-based highlights
and annotations; v2 uses CFI-anchored items (highlight, annotation, comment) tied to a
specific book version.

ADR-021 §1: v3 unifies paper + book stores into a single W3C Web Annotation Data
Model–shaped item where each record carries both ``target`` (cfi / text_excerpt /
chapter_ref / book_version_hash) and ``body`` (text / note / reflection body), so
indexer + retrieval can read structured items directly. ``CommentV2`` is preserved as
an alias for ``ReflectionV3`` for backward import compatibility while we migrate.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Annotated, Literal, Union

from pydantic import BaseModel, ConfigDict, Field


def _now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


# ── v1: paper-source annotation items ─────────────────────────────────────────


class HighlightV1(BaseModel):
    model_config = ConfigDict(extra="forbid")
    type: Literal["highlight"] = "highlight"
    text: str
    created_at: str = Field(default_factory=_now_iso)
    modified_at: str = Field(default_factory=_now_iso)


class AnnotationV1(BaseModel):
    model_config = ConfigDict(extra="forbid")
    type: Literal["annotation"] = "annotation"
    ref: str
    note: str
    created_at: str = Field(default_factory=_now_iso)
    modified_at: str = Field(default_factory=_now_iso)


AnnotationItemV1 = Annotated[Union[HighlightV1, AnnotationV1], Field(discriminator="type")]


class AnnotationSetV1(BaseModel):
    model_config = ConfigDict(extra="forbid")
    schema_version: Literal[1] = 1
    slug: str
    source_filename: str
    base: str = "inbox"
    items: list[AnnotationItemV1] = Field(default_factory=list)
    updated_at: str = Field(default_factory=_now_iso)
    last_synced_at: str | None = None


# ── v2: book/CFI-anchored annotation items ────────────────────────────────────


class HighlightV2(BaseModel):
    model_config = ConfigDict(extra="forbid")
    type: Literal["highlight"] = "highlight"
    cfi: str
    text_excerpt: str
    book_version_hash: str
    created_at: str
    modified_at: str


class AnnotationV2(BaseModel):
    model_config = ConfigDict(extra="forbid")
    type: Literal["annotation"] = "annotation"
    cfi: str
    text_excerpt: str
    note: str
    book_version_hash: str
    created_at: str
    modified_at: str


class CommentV2(BaseModel):
    model_config = ConfigDict(extra="forbid")
    type: Literal["comment"] = "comment"
    chapter_ref: str
    cfi_anchor: str | None
    body: str
    book_version_hash: str
    created_at: str
    modified_at: str


AnnotationItemV2 = Annotated[
    Union[HighlightV2, AnnotationV2, CommentV2], Field(discriminator="type")
]


class AnnotationSetV2(BaseModel):
    model_config = ConfigDict(extra="forbid")
    schema_version: Literal[2] = 2
    slug: str
    book_id: str
    book_version_hash: str
    base: Literal["books"] = "books"
    items: list[AnnotationItemV2] = Field(default_factory=list)
    updated_at: str = Field(default_factory=_now_iso)
    last_synced_at: str | None = None


# ── v3: W3C Web Annotation–shaped items (target + body per record) ────────────
#
# ADR-021 §1. The discriminator is ``type``; ``schema_version`` is pinned to 3 so a
# raw dict can be dispatched to the right model when loading from disk. ``modified_at``
# is kept (defaulting to ``created_at``) so the existing ``unsynced_count`` logic — which
# relies on per-item ``modified_at`` vs set-level ``last_synced_at`` — keeps working.


class HighlightV3(BaseModel):
    """Highlight: target = (cfi?, text_excerpt, book_version_hash?); body = text."""

    model_config = ConfigDict(extra="forbid")
    type: Literal["highlight"] = "highlight"
    schema_version: Literal[3] = 3
    # target
    cfi: str | None = None
    text_excerpt: str
    book_version_hash: str | None = None
    # body
    text: str
    # meta
    created_at: str = Field(default_factory=_now_iso)
    modified_at: str = Field(default_factory=_now_iso)


class AnnotationV3(BaseModel):
    """Annotation: short user note tied to a span. ``ref`` retained for paper-v1 callers."""

    model_config = ConfigDict(extra="forbid")
    type: Literal["annotation"] = "annotation"
    schema_version: Literal[3] = 3
    # target
    cfi: str | None = None
    text_excerpt: str
    ref: str | None = None  # paper backward-compat (v1 used `ref` as the anchor key)
    book_version_hash: str | None = None
    # body
    note: str
    # meta
    created_at: str = Field(default_factory=_now_iso)
    modified_at: str = Field(default_factory=_now_iso)


class ReflectionV3(BaseModel):
    """Chapter-level long-form reflection (was ``CommentV2``, renamed to align with
    user vocabulary). ``CommentV2`` alias is preserved below for backward imports."""

    model_config = ConfigDict(extra="forbid")
    type: Literal["reflection"] = "reflection"
    schema_version: Literal[3] = 3
    # target
    chapter_ref: str | None = None
    cfi_anchor: str | None = None
    book_version_hash: str | None = None
    # body
    body: str
    # meta
    created_at: str = Field(default_factory=_now_iso)
    modified_at: str = Field(default_factory=_now_iso)


AnnotationItemV3 = Annotated[
    Union[HighlightV3, AnnotationV3, ReflectionV3], Field(discriminator="type")
]


class AnnotationSetV3(BaseModel):
    """Unified annotation set. ``base`` is permissive (paper or book) — the per-item
    ``cfi`` / ``chapter_ref`` carries the lifecycle context; ``book_id`` / ``source_filename``
    are kept as optional headers for round-tripping legacy data."""

    model_config = ConfigDict(extra="forbid")
    schema_version: Literal[3] = 3
    slug: str
    base: str = "inbox"
    # Paper-side header (carried over from v1)
    source_filename: str | None = None
    # Book-side header (carried over from v2)
    book_id: str | None = None
    book_version_hash: str | None = None
    items: list[AnnotationItemV3] = Field(default_factory=list)
    updated_at: str = Field(default_factory=_now_iso)
    last_synced_at: str | None = None
