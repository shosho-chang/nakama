"""KB Wiki schemas (ADR-011 textbook ingest v2).

Schema 定義順序（依相依性）：
    FigureRef → ChapterSourcePageV2
    ConflictBlock → ConceptAction
    ConceptPageV2
    MigrationReport

所有 schema 遵守 docs/principles/schemas.md：
- extra="forbid" 強制
- 持久化 schema 含 schema_version
- value object 用 frozen=True；可變 page model 不 frozen
  （upsert 要 mutate mentioned_in / discussion_topics）
- Literal 取代 str enums

設計依據：docs/decisions/ADR-011-textbook-ingest-v2.md §3.5.3。
"""

from __future__ import annotations

from datetime import date
from typing import Literal

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    constr,
)

# 圖 / 表 / 公式 ref slug 形如 "fig-1-1" / "tab-1-1" / "eq-1-1"
FigureRefSlug = constr(pattern=r"^(fig|tab|eq)-\d+-\d+$")

# Wikilink 形如 "[[Sources/Books/foo/ch1]]"
WikilinkStr = constr(pattern=r"^\[\[[^\[\]]+\]\]$")


# ---------------------------------------------------------------------------
# Figure ref（章節 source page frontmatter 用）
# ---------------------------------------------------------------------------


class FigureRef(BaseModel):
    """章節內單一圖 / 表 / 公式的 ref（chapter source page frontmatter 用）。

    Vision describe 完成後填 `llm_description`；表 / 公式可不填。
    `path` 為 vault relative，落於 `Attachments/Books/{book_id}/ch{n}/`。
    """

    model_config = ConfigDict(extra="forbid", frozen=True)
    ref: FigureRefSlug
    path: str
    caption: str
    llm_description: str | None = None
    tied_to_section: str


# ---------------------------------------------------------------------------
# Chapter source page（KB/Wiki/Sources/Books/{book_id}/ch{n}.md frontmatter）
# ---------------------------------------------------------------------------


class ChapterSourcePageV2(BaseModel):
    """單章 source page frontmatter。

    body 不在此 schema 內；body 由 chapter-summary prompt 產出後直接寫入。
    `figures` list 寫入後可被 §3.3 Step 5 一次性更新（並非 immutable）。
    """

    model_config = ConfigDict(extra="forbid")
    schema_version: Literal[2] = 2
    type: Literal["book_chapter"] = "book_chapter"
    source_type: Literal["book"] = "book"
    content_nature: Literal["textbook"] = "textbook"
    lang: str
    book_id: str
    chapter_index: int = Field(ge=0)
    chapter_title: str
    section_anchors: list[str] = Field(default_factory=list)
    page_range: str
    figures: list[FigureRef] = Field(default_factory=list)
    ingested_at: date
    ingested_by: str


# ---------------------------------------------------------------------------
# Concept page（KB/Wiki/Concepts/{slug}.md frontmatter）
# ---------------------------------------------------------------------------


class ConceptPageV2(BaseModel):
    """Cross-source aggregator concept page frontmatter (per ADR-011 §3.1.1).

    aggregator 哲學：mentioned_in / discussion_topics 是 append-target，所以 model
    不 frozen；仍 enforce extra="forbid" 阻擋 unknown 欄位。

    `aliases` — dedup key（同義異名查找）
    `mentioned_in` — aggregator backlink wikilink list
    `source_refs` — 過渡相容欄位（v1 Robin schema），v2 寫入時平行維護
    `discussion_topics` — 對應 body `## 文獻分歧 / Discussion` 內 topic 列表，agent
        retrieval 看到此欄位代表需要讀完 Discussion 才能下結論
    """

    model_config = ConfigDict(extra="forbid")
    schema_version: Literal[2] = 2
    title: str
    type: Literal["concept"] = "concept"
    domain: str
    aliases: list[str] = Field(default_factory=list)
    mentioned_in: list[str] = Field(default_factory=list)
    source_refs: list[str] = Field(default_factory=list)
    discussion_topics: list[str] = Field(default_factory=list)
    confidence: float | None = Field(default=None, ge=0.0, le=1.0)
    tags: list[str] = Field(default_factory=list)
    created: date
    updated: date


# ---------------------------------------------------------------------------
# Conflict aggregation（給 update_conflict action 用）
# ---------------------------------------------------------------------------


class ConflictBlock(BaseModel):
    """單一 cross-source conflict 的結構化記錄（aggregate to `## 文獻分歧`）。

    寫入 page body 時對應 markdown:
        ### Topic: {topic}
        - **{existing_source_link}**: {existing_claim}
        - **{new_source_link}**: {new_claim}
        - **可能原因**: {possible_reason}
        - **共識點**: {consensus}
        - **不確定區**: {uncertainty}
    """

    model_config = ConfigDict(extra="forbid", frozen=True)
    topic: str
    existing_claim: str
    new_claim: str
    possible_reason: str | None = None
    consensus: str | None = None
    uncertainty: str | None = None


# ---------------------------------------------------------------------------
# Concept extract action（extract_concepts prompt LLM 結構化輸出）
# ---------------------------------------------------------------------------


class ConceptAction(BaseModel):
    """LLM extract_concepts prompt 的結構化輸出（每候選 concept 一個 action）。

    4 種 action 對齊 ADR-011 §3.3 Step 4：
    - create — 新 concept，無同名 / 無同義名命中
    - update_merge — 命中既有 concept，內容無衝突 → diff-merge into main body
    - update_conflict — 命中既有 concept，內容衝突 → 寫入 `## 文獻分歧 / Discussion`
    - noop — 命中既有 concept，新 source 完全沒提供新資訊（仍 append mentioned_in）

    `extracted_body` 用於 create / update_merge；`conflict` 用於 update_conflict；
    noop 兩者皆 None。
    """

    model_config = ConfigDict(extra="forbid", frozen=True)
    slug: str
    action: Literal["create", "update_merge", "update_conflict", "noop"]
    candidate_aliases: list[str] = Field(default_factory=list)
    extracted_body: str | None = None
    conflict: ConflictBlock | None = None


# ---------------------------------------------------------------------------
# Migration report（給 migrate_v1_to_v2 / backfill_all_v1_pages 用）
# ---------------------------------------------------------------------------


class MigrationReport(BaseModel):
    """單頁 v1 → v2 migration 報告（含 dry-run 模式）。

    `changes` 為 human-readable diff lines（給 review 看）。
    `skipped_reason` 非 None 時表 page 沒被 migrate（已是 v2 / 不存在 / etc.）。
    """

    model_config = ConfigDict(extra="forbid")
    slug: str
    from_version: int
    to_version: int
    dry_run: bool
    changes: list[str] = Field(default_factory=list)
    skipped_reason: str | None = None
