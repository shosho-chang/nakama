"""Annotation schemas (PRD #337 Slice 1, ADR-017).

KB/Annotations/{source-slug}.md 的 single source of truth schema。
Reader save / load / Robin sync 三條 path 共用。

Highlight = `==text==` 純重點標記
Annotation = `> [!annotation]` 重點 + 修修個人觀點

兩者透過 `type` discriminator 共住一份 marks list（Q8 凍結 flat list 時間順序）。
"""

from __future__ import annotations

from datetime import datetime
from typing import Annotated, Literal, Union

from pydantic import BaseModel, ConfigDict, Discriminator, Field


class Highlight(BaseModel):
    """純重點標記（`==text==`），無附加註解。"""

    model_config = ConfigDict(extra="forbid")
    type: Literal["hl"] = "hl"
    id: str
    reftext: str
    created_at: datetime
    modified_at: datetime


class Annotation(BaseModel):
    """重點 + 修修個人註解（`> [!annotation]` block）。"""

    model_config = ConfigDict(extra="forbid")
    type: Literal["ann"] = "ann"
    id: str
    reftext: str
    note: str
    created_at: datetime
    modified_at: datetime


# Discriminated union — Highlight 與 Annotation 共住單一 marks list（Q8 凍結 flat list 時間順序）。
Mark = Annotated[Union[Highlight, Annotation], Discriminator("type")]


class AnnotationSet(BaseModel):
    """單一 source 對應的 marks 集合。

    KB/Annotations/{source-slug}.md 是 source of truth；
    Reader 讀寫、Robin sync 進 Concept page 時都用這個 schema。
    """

    model_config = ConfigDict(extra="forbid")
    schema_version: Literal[1] = 1
    source_slug: str
    source_path: str  # backlink 到原 source 檔位置（KB/Raw/... 或 Inbox/kb/...）
    last_synced_at: datetime | None = None
    marks: list[Mark] = Field(default_factory=list)
