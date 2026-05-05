"""Book schemas: TocEntry + BookMetadata (extraction DTO) + Book (persisted)."""

from __future__ import annotations

from typing import Literal

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
