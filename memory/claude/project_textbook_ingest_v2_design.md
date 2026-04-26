---
name: textbook-ingest v2 設計凍結
description: textbook-ingest pipeline v2 redesign — 4 凍結原則 + 12 audit findings + ADR-010-v2 大綱，Phase 1 MVP 完成 ch1，剩 10 章 hold 等 v2
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

## 當前狀態（2026-04-26）

- **Phase 1 MVP**：ch1 (Energy Sources for Muscular Activity, p.4-16) 已 ingest 進 vault
  - `KB/Wiki/Sources/Books/biochemistry-sport-exercise-2024/ch1.md`
  - `KB/Wiki/Entities/Books/biochemistry-sport-exercise-2024.md`（status: partial, chapters_ingested: 1）
  - 4 新 concept：能量連續體 / 糖解作用 / 有氧能量系統 / 無氧能量系統
  - 6 既有 concept body append `## 更新（2026-04-25）`：磷酸肌酸系統 / ATP再合成 / 肌酸激酶系統 / 磷酸肌酸能量穿梭 / 肌酸代謝 / 運動營養學
- **剩 10 章（書內 ch2-ch11）hold 等 v2 redesign**（避免重做）

## 三步 sequencing

1. **Hygiene 修補**（low-risk，1 個 PR）：A-5 config.py env 順序 bug + A-11 broken concept page migration（branch `fix/config-and-broken-pages`）
2. **ADR-010-v2 草稿**（30-60 分鐘）：把大綱寫成 ADR，建議新增 ADR-011 + ADR-010 標 superseded（保留歷史脈絡）
3. **Implementation**（1-2 週）：`shared/kb_writer.py` 共用底層 + extract_concepts 重寫 + chapter-summary 拿掉字數上限 + parse_book.py 圖片 export + Vision describe

## Critical 檔案 reference

- `agents/robin/ingest.py:472-510`（_update_wiki_page，要重寫）
- `agents/robin/ingest.py:298-339`（_get_concept_plan，要加讀既有 body）
- `agents/robin/prompts/extract_concepts.md`（要加 conflict detection）
- `.claude/skills/textbook-ingest/scripts/parse_book.py:302-319`（要加 image / table / math）
- `.claude/skills/textbook-ingest/prompts/chapter-summary.md`（拿掉字數上限）
- `shared/config.py:30-51`（env 順序 bug）
- `shared/obsidian_writer.py:16-50`（要加 update_page helper）

## 完整文件 reference

- `docs/plans/2026-04-26-ingest-v2-redesign-plan.md` — 完整 plan + ADR-010-v2 大綱
- `docs/research/2026-04-26-workflow-inventory.md` — workflow inventory（給 cross-task 上下文）
- `docs/decisions/ADR-010-textbook-ingest.md` — v1 設計依據
