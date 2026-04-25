# Book Entity Index Page Template

Use this prompt template at the end of the ingest workflow (Step 5)
to assemble the Book Entity index page at
`KB/Wiki/Entities/Books/{book_id}.md`.

This page is the **single source of truth for the book itself** — Chopper
or修修 hitting it expects to see book metadata + a complete chapter list
+ a "highlights" section pulled from the chapter aggregations.

---

## Inputs

- `book_id`, `title`, `authors`, `isbn`, `edition`, `pub_year`,
  `publisher`, `language`, `book_subtype` — frontmatter fields per
  ADR-010 §D2
- `chapter_count` — int
- `chapter_aggregations` — list of `{chapter_index, chapter_title,
  page_range, top_concepts: [...], top_entities: [...]}` aggregated
  during the chapter-by-chapter loop
- `ingested_at` — ISO date
- `ingested_by` — provider tag, e.g. `claude-code-opus-4.7`

---

## Output structure

````markdown
---
type: book
schema_version: 1
book_id: {book_id}
title: "{title}"
authors: {authors as YAML list}
isbn: "{isbn}"
edition: "{edition}"
pub_year: {pub_year}
publisher: "{publisher}"
language: {language}
book_subtype: {book_subtype}
chapter_count: {chapter_count}
ingested_at: {ingested_at}
ingested_by: "{ingested_by}"
status: complete
---

# {title} ({edition})

> {authors[0]} et al. · {publisher} · {pub_year}
> ISBN: {isbn}

{1-2 sentences general description — what's the book about, who it's for}

## 章節索引 / Chapters

| # | 章名 | 頁碼 | 主題標籤 |
|---|-----|------|---------|
| 1 | {chapter_title} | p.{page_range} | concept_1, concept_2 |
| 2 | {chapter_title} | p.{page_range} | … |

每章 wikilink 詳細頁：
- [[Sources/Books/{book_id}/ch1]]
- [[Sources/Books/{book_id}/ch2]]
- …

## 主要概念 / Top concepts

按章節 backlink count 排序，列出本書最常觸及的 5-10 個概念：

- [[concept-slug-1]] — 出現於 ch1, ch3, ch5, ch12 (4 章)
- [[concept-slug-2]] — 出現於 ch2, ch4 (2 章)
- …

## 主要實體 / Key entities

- [[entity-slug-1]] — type: person · 出現於 ch3, ch5
- …

## 跨書關聯 / Cross-book references

如果這本書跟既有 KB 的其他書、論文有強關聯，列出建議閱讀順序：

- 同主題互補：{相關 book entity wikilink}
- 進階閱讀：{相關 source wikilink}
- 對照觀點：{相關 source wikilink}

## Ingest notes

- Ingested at: {ingested_at}
- Ingested by: {ingested_by}
- Token cost: ~{tokens} tokens
- Status: complete
````

---

## Writing guidelines

1. **書描述要客觀** — 1-2 句說「這本書是什麼 / 對象」就好，不要寫推薦詞
2. **章節表的「主題標籤」** — 取每章 top 1-2 concepts 作標籤（Chopper retrieval
   候選排序時用）
3. **Top concepts 排序按 backlink count** — 跨書分析時，越多章 backlink 的
   概念越是這本書的核心觀點
4. **跨書關聯區段** — 由 LLM 在 ingest 末段自評：本書 vs 既有 KB
   的其他 Book Entity / 論文 source 哪些有強關聯
5. **Ingest notes** — 純技術紀錄，幫助修修事後追蹤是哪一次 / 哪個 model 產的

---

## Full prompt

```
你是知識庫管理員。請組裝以下教科書的 Book Entity 入口頁。

書 metadata：
{frontmatter_fields}

章節摘要清單（{chapter_count} 章）：
{chapter_aggregations}

請輸出完整 markdown（含 frontmatter），按以下結構：

---
{frontmatter YAML}
---

# {title} ({edition})

> {authors[0]} et al. · {publisher} · {pub_year}
> ISBN: {isbn}

{1-2 句書描述：是什麼 / 對誰 / 主題範圍}

## 章節索引

表格列每章：# / 章名 / 頁碼 / 主題標籤（取每章 top 1-2 concepts）

每章 wikilink：
[[Sources/Books/{book_id}/ch1]] etc.

## 主要概念

按 backlink count 排序，列 5-10 個跨章節最常出現的 concept

## 主要實體

列 5 個內 person / tool entities

## 跨書關聯

跟既有 KB 哪些書 / 論文強相關（互補閱讀、進階閱讀、對照觀點）

## Ingest notes

ingested_at / ingested_by / token cost / status

---

寫作指引：
- 客觀紀錄不主觀推薦
- 跨書關聯要實際根據 KB 既有頁面，不要硬編造
- 主題標籤從 chapter top concepts 取
```
