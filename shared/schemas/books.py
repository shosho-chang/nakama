"""Book schemas: TocEntry + BookMetadata (extraction DTO) + Book (persisted)."""

from __future__ import annotations

from typing import Literal, Optional

from pydantic import BaseModel, ConfigDict


class TocEntry(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    title: str
    href: str
    children: list[TocEntry] = []


TocEntry.model_rebuild()


class BookMetadata(BaseModel):
    """Extraction DTO populated by epub_metadata.extract_metadata()."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    title: str | None
    author: str | None
    lang: str | None
    isbn: str | None
    published_year: int | None
    cover_path: str | None
    toc: list[TocEntry]


class Book(BaseModel):
    """Persisted book record stored in the ``books`` table."""

    model_config = ConfigDict(extra="forbid")

    schema_version: Literal[1] = 1
    book_id: str
    title: str
    author: str | None
    lang_pair: str
    genre: str | None
    isbn: str | None
    published_year: int | None
    has_original: bool
    book_version_hash: str
    created_at: str


class BookProgress(BaseModel):
    """Per-book reading position stored in the ``book_progress`` table."""

    model_config = ConfigDict(extra="forbid")

    book_id: str
    last_cfi: str | None
    last_chapter_ref: str | None
    last_spread_idx: int
    percent: float
    total_reading_seconds: int
    updated_at: str  # ISO-8601 + offset


class BookIngestQueueEntry(BaseModel):
    """Queue row from the ``book_ingest_queue`` table."""

    model_config = ConfigDict(extra="forbid")

    book_id: str
    status: Literal["queued", "ingesting", "ingested", "partial", "failed"]
    requested_at: str
    started_at: Optional[str]
    completed_at: Optional[str]
    chapters_done: int
    error: Optional[str]
