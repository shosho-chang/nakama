# Concept / Entity Extraction (Textbook Chapter Variant)

Adapted from Robin's `agents/robin/prompts/extract_concepts.md` with one
explicit tweak per ADR-010 §D5: **skip `entity_type: book` extraction**.
Book Entity is created by the skill's Step 5 (book-entity.md prompt),
not via concept-extract per chapter.

The rest of the filtering criteria (Concept Page criteria, Entity Page
criteria, JSON output schema) stay aligned with Robin's behaviour so
downstream consumers (Robin's `_execute_plan`, `_create_wiki_page`,
`_update_wiki_page`) work without changes.

---

## Inputs

- `chapter_summary` — body of the just-written Chapter Source page (after
  the chapter-summary.md prompt has run)
- `chapter_source_path` — wikilink-form path of the chapter Source page,
  e.g. `[[Sources/Books/harrison-21e/ch3]]` (used for `mentioned_in:`)
- `existing_pages` — pre-scanned list of already-existing Concept / Entity
  page slugs (from `KB/Wiki/Concepts/*.md` and `KB/Wiki/Entities/*.md`)
- `book_subtype` — passed for context (`textbook_pro` vs `popular_health`
  may shift Concept/Entity threshold)

---

## Filtering criteria

### Concept Page criteria

Only build a concept page if:

- **Cross-source recurring** — a concept that will reasonably appear in
  multiple sources (other textbooks, papers, articles), not a chapter-
  specific framework
- **Standalone explanatory value** — worth its own page for definition,
  mechanism, application
- **NOT**: overly specific subsection topics, single-source jargon, a
  framework named in this book that hasn't been picked up elsewhere

**Per-chapter quota**: 3-5 new Concepts max + any number of "update existing
Concept page with new `mentioned_in:` backlink".

### Entity Page criteria

**Person** (`entity_type: person`): foundational researchers / KOLs in
your domain (longevity / health / wellness). NOT: minor researchers cited
once in this book.

**Tool / product** (`entity_type: tool`): tools you actually use or are
worth deep dive. NOT: every drug class.

**Book** (`entity_type: book`): **SKIP — handled by Book Entity
generator (Step 5)**.

**Organisation** (`entity_type: organization`): rare. Only if decisively
shapes your domain (WHO programs, NIH-funded longevity initiatives).

**Per-chapter quota**: 1-3 new Entities max.

---

## Output JSON contract

````json
{
  "create": [
    {
      "title": "頁面標題",
      "slug": "kebab-case-slug",
      "type": "concept",
      "entity_type": null,
      "reason": "為什麼符合篩選標準",
      "content_notes": "頁面應包含的重點"
    },
    {
      "title": "Eugene Braunwald",
      "slug": "eugene-braunwald",
      "type": "entity",
      "entity_type": "person",
      "reason": "心臟學奠基人物之一，現代心衰治療典範建立者，跨書多篇引用",
      "content_notes": "1929 Vienna born，Boston Brigham 心臟科主任，Braunwald 心臟學教科書原著作者"
    }
  ],
  "update": [
    {
      "title": "Beta Blocker",
      "slug": "beta-blocker",
      "file": "KB/Wiki/Concepts/beta-blocker.md",
      "reason": "本章 §3.5 補充心衰二級預防的詳細劑量規範",
      "additions": "新增章節：心衰患者 beta-blocker titration 步驟與目標心率",
      "mentioned_in_append": "[[Sources/Books/harrison-21e/ch3]]"
    }
  ]
}
````

---

## Full prompt

```
你是一位知識庫管理員。根據以下 Chapter Source Summary，判斷哪些
Concept Pages 和 Entity Pages 值得建立或更新。

書類型: {book_subtype}
本章來源頁: {chapter_source_path}

## 現有知識庫頁面

{existing_pages}

## Chapter Source Summary

{chapter_summary}

## 篩選標準（重要）

### Concept Page
建立條件：
- 跨來源 recurring（不是這本書特有的細節）
- 有獨立解釋價值
- 不要建：過於細節的章節主題、單一書框架名稱、過於 transient 的 emerging concept

每章最多 3-5 個新 Concept；既有 Concept 命中則 update（append mentioned_in）。

### Entity Page
- person：奠基人 / 你會持續追蹤的研究者
- tool：實際會用 / 值得深入了解的工具
- book：**SKIP — 不要從 chapter ingest 抽 book entity**
  （Book Entity 由 Step 5 統一產出）
- organization：幾乎不建

每章最多 1-3 個新 Entity。

## 輸出格式

JSON，每條 reason 要清楚：

{json_schema_above}

注意：
- update 條目的 `mentioned_in_append` 必須是本章 source 路徑：
  {chapter_source_path}
- create 條目不需要 mentioned_in_append，由 ingest pipeline 自動加
  （新建頁面 mentioned_in 預設只含本章來源）
- slug 用 kebab-case lowercase
```
