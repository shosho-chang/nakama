"""Book schemas: BookMetadata (extraction DTO) + TocEntry.

Book (persisted model) is added in Slice 1C.
"""

from __future__ import annotations

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
