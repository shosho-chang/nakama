"""Pydantic schemas for annotation sets — v1 (paper sources) and v2 (book/CFI sources).

ADR-017: annotation data lives in KB/Annotations/{slug}.md; v1 uses text-based highlights
and annotations; v2 uses CFI-anchored items (highlight, annotation, comment) tied to a
specific book version.
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
