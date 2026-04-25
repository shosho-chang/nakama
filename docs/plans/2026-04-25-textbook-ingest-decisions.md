# Textbook Ingest — Decision Questionnaire

**Status**: ✅ RESOLVED 2026-04-25 — 透過對話拍板（不是 checkbox），升級為 [ADR-010-textbook-ingest.md](../decisions/ADR-010-textbook-ingest.md)。本檔保留為歷史紀錄。

對應提案：[2026-04-25-textbook-ingest-design.md](./2026-04-25-textbook-ingest-design.md)
背景：[project_textbook_ingest_design_gap.md](../../memory/claude/project_textbook_ingest_design_gap.md)

---

## 拍板過程

原本 questionnaire 列了 3 個未決問題（章/節粒度、rerank 落點、vector store 落點）。實際對話過程因為兩個關鍵 insight 把問題場域整個重塑：

1. **修修提醒 Karpathy 的 KB 設計哲學** — 教科書 ingest 的核心不是「把書切完存檔」，是「filing each chapter as you go, building out pages for characters, themes, plot threads, and how they connect」。每章吃完都會增建 / 更新 vault 內的 Concept / Entity 頁，跨書共享。
2. **修修提案用 Claude Code Opus 4.7 1M context + Max 200 subscription** — 教科書數量少、絕大多英文，沒必要建 embedding pipeline。直接用 Claude Code 互動式跑，整章 in-context 處理。

這兩個 insight 把原 Q4（embedding / rerank）+ Q5 部分（vector store 落點）整個刪掉。剩下的決策透過對話直接拍板。

---

## Q1 — 章節拆分粒度

**Decided: 章為單位**，但 Source Summary 內部走 section-by-section 結構（每節 2-3 段重點，而非整章一坨大摘要）。

**Why**：retrieval 是 chunk + concept backlink 兩層做的，summary 只是入口；vault 整潔（一本書 ~30 檔，不是節級的 150-300 檔）。

## Q2 — Rerank 落點桌機 / VPS

**Decided: 不需要 rerank**（連 embedding 都不要了）。

**Why**：Robin `/kb/research` 已經是 LLM-based ranking + symbolic backlink expansion，不靠 vector similarity。

## Q3 — Vector store 落點桌機 / VPS

**Decided: 不需要 vector store**。

**Why**：同 Q2。Wiki 的 `mentioned_in:` backlink 就是 retrieval 的「索引」— 由 LLM 在 query time 動態打分，不需要預先 embed。

---

## 補充新增拍板（對話過程衍生）

### Q4 — Vault 落地

**Decided: 方案 C+** — 分層子資料夾（`Sources/Books/{book_id}/ch*.md`、`Entities/Books/{book_id}.md`、`Raw/Books/{book_id}.pdf`） **加** 跨書共享的 `Concepts/` + `Entities/` Wiki 池（重用 Robin 既有抽 concept/entity 機制）。

### Q5 — Ingest trigger / pipeline

**Decided: Claude Code skill** — `.claude/skills/textbook-ingest/`，Mac 本機跑 Opus 4.7（Max 200 quota），互動式每章一個 turn。

### Q6 — Future backlog

**Decided（不在 Phase 1 scope）**：

1. 網頁 UI 介面（Bridge Hub 入口 + 進度條）
2. Multi-provider subscription 選擇（Anthropic Max / OpenAI Pro / Google AI Ultra）

---

## 接下來

詳細決策、schema 凍結、實作 phase 切分以 [ADR-010-textbook-ingest.md](../decisions/ADR-010-textbook-ingest.md) 為準。
