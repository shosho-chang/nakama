---
name: textbook-ingest v2 設計凍結 + Step 1/2 done + Step 3 PR A open
description: textbook-ingest pipeline v2 — 4 凍結原則 + Step 1 hygiene PR #164 + Step 2 ADR-011 PR #165 + Step 3 PR A #169 open（schemas + kb_writer + Robin 重接線 + Web UI v2 plan）
type: project
originSessionId: 27c0b340-d612-4f47-aba4-b4f3727267fd
---
2026-04-26 凍結 4 個 ingest 設計原則（修修確認）：

1. **Karpathy aggregator** — concept page = cross-source evidence aggregator，新 source 要 merge 進主體，衝突闢 `## 文獻分歧 / Discussion`
2. **LLM-readable deep extract** — ingest 用最強 model（Opus 4.7）+ 不省 token；給 agent 消化用
3. **圖表 first-class** — 圖片 export + Vision LLM domain-aware describe + table preserve as markdown + math LaTeX
4. **Conflict detection mandatory** — aliases dedup + 強制讀既有 page body 做 cross-source diff + Discussion section aggregation

**Why:** 修修要 ingest 教科書給 agent 消化，不是 RAG dump；Karpathy gist personal cross-source wiki 哲學的核心；ingest 是 one-shot deep work、retrieval 是 many-shot lightweight，職責分離。

**How to apply:** 任何動 ingest pipeline 的 PR 都要對齊 4 原則；新 concept page schema 必含 `aliases:` + `mentioned_in:` + `discussion_topics:`。

## 進度狀態（2026-04-26 EOD）

- ✅ **Step 1 (Hygiene) PR #164 merged** `c2b529b` — A-5 config.py env 順序 + A-11a obsidian_writer width=10**9 + A-11b migration script + tests
- ✅ **Step 2 (ADR-011) PR #165 merged** `694b50d` — ADR-011 完整稿（5 sub-decisions + Migration + Acceptance）+ ADR-010 標 Superseded + 修修 4 題拍板
- 🚧 **Step 3 PR A open #169** `26ec74f` — schemas + kb_writer + lifeos_writer A-11c + Robin ingest 重接線 + extract_concepts v2 prompt + Web UI v2 plan schema + 186 PR-scope tests 全綠
- ⏳ **Step 3 剩餘 PR B/C/D**：parse_book walker + Vision / 重 ingest ch1 / 批 ingest 10 章

## PR A 內容（PR #169 / branch `feat/ingest-v2-step3-schemas-kb-writer`）

**新增 4 檔**：
- `shared/schemas/kb.py` — 6 Pydantic schemas（ConceptPageV2 / FigureRef / ChapterSourcePageV2 / ConflictBlock / ConceptAction / MigrationReport）
- `shared/kb_writer.py` — 7 function + migrate（read_concept_for_diff / list_existing_concepts / upsert_concept_page / update_mentioned_in / aggregate_conflict / write_source_page / upsert_book_entity + migrate_v1_to_v2 / backfill_all_v1_pages）；idempotency + .bak retain 24h；LLM diff-merge 走 Opus 4.7
- `tests/test_kb_schemas.py` — 38 tests
- `tests/test_kb_writer.py` — 41 tests

**改 9 檔**：
- `shared/lifeos_writer.py:193` — yaml.dump 加 width=10**9（A-11c follow-up）
- `agents/robin/prompts/extract_concepts.md` — v2 4-action schema（create / update_merge / update_conflict / noop），prompt 注入既有 page aliases + body excerpt
- `agents/robin/ingest.py` — 新 `_execute_concept_action` 走 kb_writer；`_create_entity_page` 沿用 v1；移除舊 `_create_wiki_page` concept branch 與 `_update_wiki_page` todo-append
- `thousand_sunny/routers/robin.py` — Web UI 適配 v2 plan schema (concepts/entities)
- `thousand_sunny/templates/robin/review_plan.html` — UI 顯示 4-action badge + conflict topic
- 4 tests files：test_lifeos_writer regression + test_ingest 重寫 + test_robin_router/_sse 適配

## Step 3 PR B 開工順序（按 ADR-011 §6 Acceptance 排）

1. 改 `.claude/skills/textbook-ingest/scripts/parse_book.py:302-319` `_epub_html_to_text` walker — 處理 `<img>` `<table>` `<math>` + 新 dep `mathml2latex`
2. PDF 路徑改用 `pymupdf4llm.to_markdown(with_tables=True)` (A-9)
3. 拿掉 `.claude/skills/textbook-ingest/prompts/chapter-summary.md` 字數上限 (A-4)
4. 加 Vision describe step（Sonnet 4.6 + domain-aware prompt §3.4.3 skeleton）+ `prompts/vision-describe.md`
5. parse_book + Vision tests

## Step 3 PR C/D

- PR C：重 ingest ch1 + retrieval acceptance test（kb-search 對「PCr 主導時間多長」要拿到 文獻分歧 section）
- PR D：批 ingest 剩 10 章（ch2-ch11）

## Critical 檔案 reference（給後續 PR implementer）

- `agents/robin/ingest.py` — v2 已落地，PR B/C 不動
- `agents/robin/prompts/extract_concepts.md` — v2 已落地
- `shared/kb_writer.py` — PR B 寫 chapter source 時呼叫 `write_source_page()`
- `.claude/skills/textbook-ingest/scripts/parse_book.py:302-319` — `_epub_html_to_text` walker（PR B 重寫）
- `.claude/skills/textbook-ingest/prompts/chapter-summary.md` — 拿掉字數上限（PR B）
- `shared/obsidian_writer.py` — 已加 `width=10**9`（PR #164）；新 KB 寫入走 kb_writer

## 完整文件 reference

- `docs/decisions/ADR-011-textbook-ingest-v2.md` — Step 2 ADR
- `docs/plans/2026-04-26-ingest-v2-redesign-plan.md` — Step 0 plan
- `docs/plans/2026-04-26-ingest-v2-decisions.md` — 修修拍板紀錄
- `docs/research/2026-04-26-workflow-inventory.md` — workflow inventory
- `docs/decisions/ADR-010-textbook-ingest.md` — v1（Superseded）
