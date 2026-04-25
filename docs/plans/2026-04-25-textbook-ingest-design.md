# Textbook Ingest Workflow — Design Proposal

**Status**: ✅ RESOLVED 2026-04-25 — 升級為 [ADR-010-textbook-ingest.md](../decisions/ADR-010-textbook-ingest.md)。本檔保留為設計過程歷史紀錄。
**Blocks**: Chopper agent ([project_chopper_community_qa.md](../../memory/claude/project_chopper_community_qa.md)) — 預設 KB 含教科書，但「整本書怎麼進 KB」沒有 workflow（已解決）
**Compute tier**: Mac / 桌機（Claude Code，per [feedback_compute_tier_split.md](../../memory/claude/feedback_compute_tier_split.md)）
**Source memory**: [project_textbook_ingest_design_gap.md](../../memory/claude/project_textbook_ingest_design_gap.md)

---

## 設計決策摘要（最終版）

對話過程拍板的 5 題決策，最終版本與最初提案有重大轉折，特別是 Q4 / Q5 因為兩個關鍵 insight 整個簡化：

1. **Karpathy KB 哲學**（修修提醒）— 教科書 ingest 不是只有「章 → Source」這條線，**抽出來的 Concept / Entity 要長進跨書共享 Wiki 池**，每個 concept 頁帶 `mentioned_in:` backlink 串起所有來源。
2. **Claude Code Opus 4.7 1M context**（修修提案）— 教科書數量少、絕大多英文 → 直接用 Claude Code + Max 200 quota 走 Opus 4.7 整章 in-context 處理。**完全不需要 embedding / vector store / 桌機 GPU**。

---

## Q1 — 切到「章」還是「節」？

**Decided: 章為單位，但 Source Summary 內部走 section-by-section 結構**

- 每章 1 個 Source 檔（`KB/Wiki/Sources/Books/{book_id}/ch{n}.md`）
- 一本書約 30 個檔（不是節級的 150-300 檔），vault 整潔
- 章內 summary 不是一坨大摘要，而是按節獨立寫 2-3 段重點：

  ```markdown
  ## 3.1 Inspection
  （這節 2-3 段重點）

  ## 3.2 Auscultation
  （這節 2-3 段重點）
  ```

- 為什麼不切到節：retrieval 是 chunk 層 + concept backlink 層做的，summary 只是人類肉眼瀏覽 + LLM 排序候選的入口。檔案爆炸沒必要。

## Q2 — Frontmatter schema

**Decided: 接受提案 schema、5 個 book_subtype、book_id 走 slug**

詳細欄位定義移到 ADR-010 §D2。

## Q3 — Vault 落地

**Decided: 方案 C+ — 分層子資料夾 + 跨書共享的 Concept/Entity Wiki 池**

```
KB/
├── Raw/Books/
│   └── harrison-21e.pdf                     ← 原檔備份（Layer A）
├── Wiki/
│   ├── Entities/Books/
│   │   └── harrison-21e.md                  ← 書本身入口頁
│   ├── Sources/Books/
│   │   └── harrison-21e/
│   │       ├── ch1.md
│   │       └── ...
│   ├── Concepts/                            ← ★ 跨書/論文共享
│   │   ├── frank-starling-law.md
│   │   └── ...
│   └── Entities/                            ← ★ 跨書/論文共享
│       └── beta-blocker.md
└── Attachments/Books/
    └── harrison-21e/
```

每個 Concept / Entity 頁的 frontmatter 帶 `mentioned_in:` 列所有來源（書章 + 論文 + 文章）：

```yaml
title: Beta Blocker
type: concept
mentioned_in:
  - "[[Sources/Books/harrison-21e/ch5]]"
  - "[[Sources/pubmed-12345]]"
  - "[[Sources/popular-health-article-xyz]]"
```

這是 Karpathy 的「rich companion wiki」精神 — Chopper 答題時從一個 concept 反查所有來源，**一次拿到立體多角度資訊**。

## Q4 — Retrieval（embedding / rerank）

**Decided: 完全不需要 embedding / vector store / reranker**

Robin 既有的 `/kb/research`（kb-search skill）就是 LLM-based ranking + symbolic backlink expansion：

1. 掃 `KB/Wiki/Concepts/`、`Entities/`、`Sources/` 標題
2. 給 Claude 排序「哪幾頁跟問題最相關」
3. 拿 top-K，follow `mentioned_in:` 抓所有源頭
4. compose 答案

Chopper 直接重用 kb-search skill 即可。**幹掉的東西**：

- bge-m3 embedding model
- bge-reranker-v2-m3 cross-encoder
- sqlite-vec / pgvector vector store
- 桌機 GPU 跑 embedding 的依賴
- 桌機 vs VPS rerank 落點討論
- chunk size / overlap 參數調

## Q5 — Ingest trigger / pipeline

**Decided: Claude Code skill 走 Opus 4.7 1M context，Mac 本機跑**

教科書 ingest 包成 `.claude/skills/textbook-ingest/`：

1. 修修開 Claude Code → 「ingest /Users/shosho/Books/harrison.pdf 這本教科書」
2. Skill 接到 → bash 跑 PDF parse helper 抽出 outline + 章節邊界 → 給修修確認
3. **每章一個 turn**：Opus 讀整章 in-context → 產 section-by-section summary → 抽 concept/entity → Write tool 寫入 vault（重用 Robin 的 prompt template + obsidian_writer）
4. 全部完成 → 產 Book Entity 入口頁列章節 wikilink
5. vault → Obsidian Sync → VPS → Chopper kb-search 可查

**為什麼 Claude Code 走 Opus 4.7 而不是 API**：

| 維度 | API 路徑 | Claude Code Opus 4.7 |
|------|---------|---------------------|
| 成本 | 30 章 × map-reduce ≈ 一本書 $3-5 | 走 Max 200 subscription quota（已付） |
| Context 模型 | 200k Sonnet（要 map-reduce）| 1M Opus（中型書整本可一次塞、大書整章一次塞）|
| 互動 | 純 batch，失敗要排查 log | interactive 可中斷可介入 |
| 品質 | Sonnet map-reduce reduce 階段會掉 context | Opus 整章 in-context，品質高 |
| 桌機 / VPS | 都可跑 | Mac Claude Code，自然落點 |

**Token 數學**（英文書）：

| 書類 | 頁數 | 整本 token | 1M 塞得下 |
|------|------|-----------|----------|
| 一般醫學參考書 | 800 | ~520k | ✅ |
| 中型教科書 | 1500 | ~975k | 🟡 邊緣 |
| Harrison's | 4000 | ~2.6M | ❌ 必須分章 |

大書分章 ingest，每章 30-100 頁約 20-65k token，**Opus 4.7 一次吃整章 + 寫 summary + 抽 concept/entity 綽綽有餘**。

---

## 章節辨識策略

教科書 PDF 章節邊界辨識：

1. **First pass — PDF outline / bookmarks**：90% 教科書 PDF 帶 outline，直接讀
2. **Second pass — heading regex**：沒 outline 就用 `r"^(Chapter|第)\s*\d+"` + font-size 偵測
3. **Third pass — Opus 自己看 50 頁滑動視窗判斷章節邊界**（最後 fallback）
4. **Manual override**：CLI 接 `--toc-yaml` 讓修修手寫章節邊界（rare case）

## 依賴 / 重用既有 code

- ✅ `shared/pdf_parser.py`（既有，pymupdf4llm）
- ✅ `shared/obsidian_writer.py`（既有，write_page）
- ✅ `agents/robin/chunker.py`（既有，按 heading 切；Opus 1M context 多半用不到）
- ✅ Robin 既有 prompt template（summarize_chunk / concept-extract）— 直接重用，不重造輪子
- ✅ Robin `/kb/research` retrieval pipeline — 不動
- ❌ ~~Docling / EPUB parser~~ — 不需要（PDF outline + pymupdf4llm 涵蓋 90%）
- ❌ ~~bge-m3 embedding~~ — 不需要
- ❌ ~~vector store~~ — 不需要
- ❌ ~~桌機 GPU~~ — 不需要

---

## Future backlog（之後再優化）

修修提到的兩條優化方向，Phase 2+ 處理：

### 1. 網頁 UI 介面

- (a) Nakama 首頁（Bridge Hub）加「教科書 ingest」入口
- (b) Ingest 過程中加互動 UI / 進度條

實作思路：FastAPI route 接 PDF upload → background task 呼叫 Claude SDK（不是 Claude Code，是程式化呼叫）→ SSE stream 進度 → 寫 vault。重用 Bridge UI mutation pattern（[reference_bridge_ui_mutation_pattern.md](../../memory/claude/reference_bridge_ui_mutation_pattern.md)）。

**前置依賴**：Phase 1 Claude Code skill 流程穩定運作後，prompt / pipeline 邏輯已經沉澱成可程式化呼叫的單元。

### 2. Multi-provider subscription model 選擇

- (a) Ingest 時可選擇用哪家 subscription（Anthropic Max / OpenAI Pro / Google AI Ultra ...）
- (b) 各家模型強項不同：Opus 強推理 / GPT-5 強多語 / Gemini 強長 context

實作思路：抽象 `IngestProvider` interface，各家實作 adapter；ingest 命令加 `--provider claude|openai|google` flag；對應 prompt 微調。

**前置依賴**：Phase 1 把 prompt / vault writer / schema 等抽象介面凍結（Claude Code 是最直白的呼叫端，反而最容易抽出 interface）。

---

## 升級為 ADR

本提案已升級為 [ADR-010-textbook-ingest.md](../decisions/ADR-010-textbook-ingest.md)，詳細決策、schema 凍結、實作 phase 切分以那邊為準。
