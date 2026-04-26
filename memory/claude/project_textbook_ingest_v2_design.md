---
name: textbook-ingest v2 設計凍結 + Step 1/2 done + Step 3 pickup
description: textbook-ingest pipeline v2 — 4 凍結原則 + Step 1 hygiene PR #164 merged + Step 2 ADR-011 PR #165 merged + Step 3 從 schema/kb_writer 起跑
type: project
originSessionId: 27c0b340-d612-4f47-aba4-b4f3727267fd
---
2026-04-26 凍結 4 個 ingest 設計原則（修修確認）：

1. **Karpathy aggregator** — concept page 是 cross-source evidence aggregator，不是 oracle、不是 changelog；新 source 進來要把資訊真正 merge 進 concept body 主體，有衝突闢 `## 文獻分歧 / Discussion` 結構化記錄
2. **LLM-readable deep extract** — ingest 用最強 model（Opus 4.7）+ 不省 token；不為人類閱讀友善而摺扣，給 agent 消化用
3. **圖表 first-class** — 圖片 export + Vision LLM domain-aware describe + table preserve as markdown + math LaTeX；不可 drop visual / structured 內容
4. **Conflict detection mandatory** — aliases dedup + 強制讀既有 page body 做 cross-source diff + Discussion section aggregation

**Why:** 修修要 ingest 教科書給 agent 消化，不是 RAG dump；ingest 是 one-shot deep work、retrieval 是 many-shot lightweight，職責分離；Karpathy gist personal cross-source wiki 哲學的核心。

**How to apply:** 任何動 ingest pipeline 的 PR 都要對齊 4 原則；新 concept page schema 必含 `aliases:` + `mentioned_in:` + `discussion_topics:`；既有 Robin pipeline 要重構（update path 走 LLM diff-merge into main body，不是 todo-style append）。

## 進度狀態（2026-04-26 EOD）

- ✅ **Step 1 (Hygiene) PR #164 merged** `c2b529b` — A-5 config.py env 順序 + A-11a obsidian_writer width=10**9 + A-11b migration script + tests + decisions doc。dry-run 抓到 4 broken page（plan 列 2 頁，多 2 頁神經保護作用 / 膳食補充劑安全性），**修修待 apply** `python -m scripts.migrate_broken_concept_frontmatter --vault "F:/Shosho LifeOS" --apply`（會寫 .bak）
- ✅ **Step 2 (ADR-011) PR #165 merged** `694b50d` — ADR-011 完整稿（5 sub-decisions + Migration + Acceptance）+ ADR-010 標 Superseded + plan §8 → Decisions 表。修修 4 題全 A 拍板（Sequence 並行 / 6 page lazy backfill / 新 ADR-011 / Vision = Sonnet 4.6）
- 🚧 **Step 3 (Implementation) ready to start** — 從 `shared/schemas/kb.py` + `shared/kb_writer.py` 起跑，schema 與 function signatures 已在 ADR-011 §3.5.3 / §3.5.1 定義好

## ch1 vault 出口（v1 schema，待 Step 3 backfill）

- `KB/Wiki/Sources/Books/biochemistry-sport-exercise-2024/ch1.md`（chapter source）
- `KB/Wiki/Entities/Books/biochemistry-sport-exercise-2024.md`（status: partial, chapters_ingested: 1）
- 4 新 concept：能量連續體 / 糖解作用 / 有氧能量系統 / 無氧能量系統
- 6 既有 concept body append `## 更新（2026-04-25）`：磷酸肌酸系統 / ATP再合成 / 肌酸激酶系統 / 磷酸肌酸能量穿梭 / 肌酸代謝 / 運動營養學
- 剩 10 章（書內 ch2-ch11）hold 等 Step 3 落地後重 ingest ch1（lazy migrate）+ 批 ingest

## Step 3 開工順序（按 ADR-011 §6 Acceptance 排）

1. `shared/schemas/kb.py` — 6 個 Pydantic class（ConceptPageV2 / FigureRef / ChapterSourcePageV2 / ConflictBlock / ConceptAction / MigrationReport），全照 ADR-011 §3.5.3 寫；注意 frozen=True 只給 immutable value object（FigureRef / ConflictBlock / ConceptAction），ConceptPageV2 / ChapterSourcePageV2 不 frozen 因為 upsert 要 mutate mentioned_in。schema unit test round-trip
2. `shared/kb_writer.py` — 7 個 function（read_concept_for_diff / list_existing_concepts / upsert_concept_page / update_mentioned_in / aggregate_conflict / write_source_page / upsert_book_entity）+ migrate_v1_to_v2 / backfill_all_v1_pages。idempotency + .bak 寫入 `data/kb_backup/{slug}-{utc-ts}.md` retain 24h
3. 重接線 `agents/robin/ingest.py:472-510` `_update_wiki_page` → 改呼叫 `kb_writer.upsert_concept_page(action=...)`；舊 `## 更新` body append 邏輯刪除
4. 重寫 `agents/robin/prompts/extract_concepts.md` — 注入既有 page aliases + body；要求 LLM 輸出 4 種 action（create / update_merge / update_conflict / noop）
5. 拿掉 `.claude/skills/textbook-ingest/prompts/chapter-summary.md` 字數上限
6. 改 `.claude/skills/textbook-ingest/scripts/parse_book.py:302-319` `_epub_html_to_text` walker — 處理 `<img>` `<table>` `<math>` + 新 dep `mathml2latex`
7. 加 Vision describe step（Sonnet 4.6 + domain-aware prompt §3.4.3 skeleton）
8. 重 ingest ch1 + 跑 retrieval acceptance test（kb-search 對「PCr 主導時間多長」要拿到 文獻分歧 section）
9. 批 ingest 剩 10 章

## Critical 檔案 reference（給 Step 3 implementer）

- `agents/robin/ingest.py:472-510` — `_update_wiki_page` 重寫為 `kb_writer.upsert_concept_page()` 呼叫
- `agents/robin/ingest.py:298-339` — `_get_concept_plan` 加讀既有 body
- `agents/robin/prompts/extract_concepts.md` — 加 conflict detection
- `.claude/skills/textbook-ingest/scripts/parse_book.py:302-319` — `_epub_html_to_text` walker
- `.claude/skills/textbook-ingest/prompts/chapter-summary.md` — 拿掉字數上限
- `shared/obsidian_writer.py` — `write_page` 已加 `width=10**9`（PR #164）；`update_page` helper 由 kb_writer 內部用
- `shared/config.py` — env 順序 bug 已修（PR #164）

## 完整文件 reference

- `docs/decisions/ADR-011-textbook-ingest-v2.md` — Step 2 ADR 凍結（5 sub-decisions + Migration + Acceptance）
- `docs/plans/2026-04-26-ingest-v2-redesign-plan.md` — Step 0 plan（4 原則 + 12 audit findings + §8 Decisions table）
- `docs/plans/2026-04-26-ingest-v2-decisions.md` — 修修拍板紀錄（4 題全 A）
- `docs/research/2026-04-26-workflow-inventory.md` — workflow inventory（給 cross-task 上下文）
- `docs/decisions/ADR-010-textbook-ingest.md` — v1 設計（已 Superseded）
