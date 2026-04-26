---
name: textbook-ingest v2 設計凍結 + Step 1/2 done + Step 3 PR A/B 都 in-flight
description: textbook-ingest pipeline v2 — 4 凍結原則 + Step 1 PR #164 + Step 2 ADR-011 PR #165 + Step 3 PR A #169 ultrareview fixes pushed + PR B #178 opened
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

- ✅ **Step 1 PR #164 merged** `c2b529b` — config env order + obsidian_writer width=10**9 + broken page migration script
- ✅ **Step 2 ADR-011 PR #165 merged** `694b50d` — ADR-011 完整稿 + ADR-010 標 Superseded + 修修 4 題拍板
- 🚧 **Step 3 PR A #169** — base `26ec74f` + ultrareview fixes `4d4ab4e` pushed；CI 綠；等 ultrareview 跑 second pass / 修修 manual E2E。9 findings 全修：5 normal-severity（含 critical prompt 目錄錯誤）+ 5 nits + 18 new tests
- 🚧 **Step 3 PR B #178** opened `d8b6e84` — parse_book walker (img/table/math) + PDF pymupdf4llm + chapter-summary v2 + vision-describe.md + SKILL.md Step 4 rewrite + truncate call removal + 22 walker tests + full suite 1843 passing
- ⏳ **Step 3 PR C / D backlog**：等 PR A + B 都 land

**詳細 in-flight 狀態 + 修修 manual todos**：見 `project_ingest_v2_step3_in_flight_2026_04_26.md`

## PR A 內容（branch `feat/ingest-v2-step3-schemas-kb-writer`）

**新增 4 檔**：
- `shared/schemas/kb.py` — 6 Pydantic schemas（ConceptPageV2 / FigureRef / ChapterSourcePageV2 / ConflictBlock / ConceptAction / MigrationReport）
- `shared/kb_writer.py` — 7 function + migrate；idempotency + .bak retain 24h；LLM diff-merge 走 Opus 4.7
- `tests/test_kb_schemas.py` — 38 tests / `tests/test_kb_writer.py` — 41 + 18 new fix tests = 59 total

**改 PR A path**（ultrareview fixes 4d4ab4e）：
- `prompts/robin/extract_concepts.md` + 5 categories — v2 prompt 從 dead `agents/robin/prompts/` 搬到 runtime path（CRITICAL：原本 merge 後 ingest 會 KeyError 炸）
- `shared/kb_writer.py` — slug validation / update_conflict idempotency / `_ensure_h2_skeleton` preserve non-canonical / noop strip legacy / chapter sort / aggregate_conflict + update_mentioned_in 補 migration + mentioned_in / confidence migration bool/unknown
- `agents/robin/ingest.py` — conflict validation gate on `action == 'update_conflict'`
- `thousand_sunny/routers/robin.py` + `templates/robin/done.html` — noop 進 progress count + done 加 referenced bucket

## PR B 內容（branch `feat/ingest-v2-step3-pr-b-parse-book`）

**新增 1 檔**：`prompts/vision-describe.md` — domain-aware system role；Sonnet 4.6 default

**改 4 檔**：
- `.claude/skills/textbook-ingest/scripts/parse_book.py` — `_walk_epub_html` walker (img/table/math) + PDF `pymupdf4llm.to_markdown` + xref dedup figures + `--attachments-base-dir` CLI flag + outline JSON figures/tables
- `.claude/skills/textbook-ingest/prompts/chapter-summary.md` — v2：拿掉字數上限 + 強制 verbatim + Section concept map + 保留 placeholders
- `.claude/skills/textbook-ingest/SKILL.md` — Step 2 + Step 4 rewrite（4b Vision describe 新 step / 4d/4e v2 4-action dispatcher）
- `agents/robin/ingest.py` — 移除 `_truncate_at_boundary(content, 30000)` no-op call (A-10)
- `tests/skills/textbook_ingest/test_parse_book.py` — +12 cases

**ADR-011 deviation**：mathml2latex PyPI 0.1.0 abandoned（empty public API）→ alttext-first path（見 `feedback_mathml2latex_abandoned.md`）

## Step 3 PR C / D backlog

- **PR C**：重 ingest ch1 + retrieval acceptance test（kb-search 對「PCr 主導時間多長」要拿到 `## 文獻分歧` section）；6 既有 concept page lazy migrate v1 → v2、body 末尾無 `## 更新` block
- **PR D**：批 ingest ch2-ch11；Book Entity status: complete + chapters_ingested: 11

## Critical 檔案 reference

- `agents/robin/ingest.py` — v2 在 PR A 落地（concept paths）+ PR B 改 truncate call
- `agents/robin/prompts/extract_concepts.md` — **dead path**，已刪；runtime 路徑是 `prompts/robin/extract_concepts.md` + 5 categories
- `shared/kb_writer.py` — PR A 主體；後續 PR 寫 chapter source 呼叫 `write_source_page()`
- `.claude/skills/textbook-ingest/scripts/parse_book.py` — PR B walker；PR C 用其 outline JSON
- `.claude/skills/textbook-ingest/prompts/chapter-summary.md` — PR B v2；PR C 跑 ingest 時用
- `.claude/skills/textbook-ingest/prompts/vision-describe.md` — PR B 新；PR C Vision describe step 用
- `shared/obsidian_writer.py` — `width=10**9`（PR #164）；KB 寫入走 kb_writer

## 完整文件 reference

- `docs/decisions/ADR-011-textbook-ingest-v2.md` — Step 2 ADR
- `docs/plans/2026-04-26-ingest-v2-redesign-plan.md` — Step 0 plan
- `docs/plans/2026-04-26-ingest-v2-decisions.md` — 修修拍板紀錄
- `docs/research/2026-04-26-workflow-inventory.md` — workflow inventory
- `docs/decisions/ADR-010-textbook-ingest.md` — v1（Superseded）
