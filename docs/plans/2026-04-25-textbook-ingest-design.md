# Textbook Ingest Workflow — Design Proposal

**Status**: 提案中（未凍結）。修修確認 → 升級成 ADR → 實作可開工。
**Blocks**: Chopper agent ([project_chopper_community_qa.md](../../memory/claude/project_chopper_community_qa.md)) — 預設 KB 含教科書，但「整本書怎麼進 KB」沒有 workflow。
**Compute tier**: 桌機（per [feedback_compute_tier_split.md](../../memory/claude/feedback_compute_tier_split.md)）。
**Source memory**: [project_textbook_ingest_design_gap.md](../../memory/claude/project_textbook_ingest_design_gap.md)。

---

## 5 個 design 問題 × 我的提案

### Q1 — 單位：整本一個 source 還是章節拆分？

**提案**：**章節拆分為主**，一個 source-per-chapter。書本層級走 Entity 頁串連結。

**Why**：
- 一本醫學教科書 1500 頁，整本一個 source 對 retrieval 不友善（embedding 失準、citation 模糊）
- 章節是天然語意單位，跨章 retrieve 也好處理
- 對齊既有 `KB/Wiki/Sources/` 是「per content unit」的慣例

**取捨**：章節數爆炸時（某些書 100+ 章）`KB/Wiki/Sources/` 會被淹沒 — 用 `Sources/Books/{book_id}/ch{n}.md` 子資料夾隔離，不污染既有 Sources flat 命名空間。

⚠️ **修修決策點**：要不要拆到「節」（section）層級？我傾向只拆到章 — 節拆太細 retrieval 變雜訊，retriever 可以靠 chunk-level metadata 還原節的精度。

---

### Q2 — Schema：frontmatter 要哪些欄位？

**提案**：

#### Book Entity (`KB/Wiki/Entities/Books/{book_id}.md`)

```yaml
type: book
book_id: { slug }                       # e.g. harrison-internal-medicine-21e
title: { 完整書名 }
authors: [...]
isbn: { ISBN-13 }
edition: { string, e.g. "21st" }
pub_year: { int }
publisher: { string }
language: { zh-TW | en | ... }
book_subtype: { textbook_exam | textbook_pro | popular_health | clinical_protocol | reference }
chapter_count: { int }
ingested_at: { ISO date }
status: { ingesting | complete | partial }
```

#### Chapter Source (`KB/Wiki/Sources/Books/{book_id}/ch{n}.md`)

```yaml
type: book_chapter                       # 跟 paper_digest / scrape_translate 區分
source_type: book
content_nature: textbook                 # allowlist 已支援，見 thousand_sunny/routers/robin.py:242
lang: { zh-TW | en | ... }
book_id: { slug }                        # 反查 Book Entity
chapter_index: { 1-based int }
chapter_title: { 章名 }
section_anchors: [...]                   # 章內 section heading 列表（給 retrieval reranker）
page_range: { "1234-1267" }              # 原始 PDF 頁碼，給 citation
ingested_at: { ISO date }
```

⚠️ **修修決策點**：`book_subtype` 需要哪些值？我先列 5 個，之後可加（修修腦袋想到的書類型有幾種？國考用書、專業參考、科普、臨床指引 — 第五種「reference」是 catch-all，比如字典）。

---

### Q3 — Vault 落地：A / B / C 哪一個？

**提案**：**方案 C 的變體**（per-chapter Source + book-level Entity + 完整原文 Raw 備份）

```
KB/
├── Raw/Books/
│   └── {book_id}.pdf                    # 不可變原文（LifeOS Layer A）
├── Wiki/
│   ├── Entities/Books/
│   │   └── {book_id}.md                 # 書 metadata + 章節 wikilink 索引
│   └── Sources/Books/
│       └── {book_id}/
│           ├── ch1.md
│           ├── ch2.md
│           └── ...                      # 章節獨立檔
└── Attachments/Books/
    └── {book_id}/                       # 圖片 / 表格擷取（如果 Docling 抽出）
```

**Why**：
- 對齊 LifeOS CLAUDE.md Layer A (`Raw/`) vs Layer B (`Wiki/`) 規範
- Entity 層做 single source of truth（修修要找書本身的 metadata 從這裡開始）
- Source 層做 retrieval 主場（Chopper embed 與 query 都針對章節）
- `Sources/Books/{book_id}/` 子資料夾隔離爆量章節，不污染既有 `Sources/` flat 命名空間

**Ruled out**：
- 方案 A（每章扁平到 `Sources/textbook-{book_id}-ch{n}.md`）：1500 頁 × 30 章 × 多本書 → flat 目錄爆炸
- 方案 B（Raw 整本 + Wiki 章節索引）：跟 Entity 概念重複，不如直接用 Entity

---

### Q4 — Chopper Retrieval：embedding / citation / 跨章？

**提案**：

#### Embedding 切塊

- **大小**：800 token sliding window，200 token overlap（醫學教科書章節有 Q&A、表、文字混雜，800 對段落不會切碎）
- **metadata**：每 chunk 帶 `book_id` / `chapter_index` / `chapter_title` / `section_anchor` / `page` 五件
- **embedder**：先用 `BAAI/bge-m3`（中英雙語、Apache 2.0、桌機本地跑），未來 A/B 比 OpenAI text-embedding-3-large

#### Citation 形式

- Chopper 回答時引用：`(《Harrison's Internal Medicine》21e, ch3 · Cardiovascular Examination, p.245)`
- vault 內部用 `[[Books/{book_id}/ch3]]` wikilink，這樣 Obsidian graph view 自動成圖
- 不要直接給檔名（檔名是 slug-friendly 內部格式，不適合 user-facing）

#### 跨章合成

- 先 retrieve top-K chunks（K=20）跨章
- Cross-encoder rerank（`bge-reranker-v2-m3`）→ top-5
- LLM compose 時 system prompt 加「同一書多章引用要明確標出每點來自哪章」
- 修修認知：跟我跑研究時 Robin 引用多份 paper 一樣的體感

⚠️ **修修決策點**：rerank step 是 round-trip 桌機還是 VPS？我傾向 retrieve + rerank 都在 VPS（query latency 重要、bge-reranker-m3 有 ONNX 版可在 4GB RAM 跑），ingest（embedding）才在桌機。

---

### Q5 — Ingest Trigger：哪個 agent / endpoint / CLI？

**提案**：**桌機 CLI script `scripts/ingest_book.py`**

```bash
# 桌機
python -m scripts.ingest_book \
    --path "/Users/shosho/Books/harrison-21e.pdf" \
    --book-id harrison-internal-medicine-21e \
    --book-subtype textbook_pro \
    --language en
```

**Why**：
- 教科書 ingest 是長 process（PDF 解析 → 章節抽取 → LLM categorize → embedding → vault 寫入），20-60 分鐘量級
- Slack 命令會 timeout（3s 內必須回 ack）
- VPS 4GB RAM 跑不動 1500 頁 chunking + 本地 embedding model

**Pipeline 內部**：

```mermaid
flowchart LR
    PDF[PDF / EPUB] --> PARSE[Docling parse<br/>or PyMuPDF fallback]
    PARSE --> TOC[抽 TOC / outline<br/>→ 章節邊界]
    TOC --> CHUNK[每章 sliding window chunk]
    CHUNK --> EMBED[bge-m3 embed<br/>桌機本地]
    EMBED --> WRITE_SRC[寫 KB/Wiki/Sources/Books/{book_id}/ch*.md]
    WRITE_SRC --> WRITE_ENTITY[寫 KB/Wiki/Entities/Books/{book_id}.md]
    WRITE_ENTITY --> WRITE_VEC[寫 vector store<br/>data/chopper_kb.sqlite]
    WRITE_VEC --> SYNC[Obsidian Sync 自動同步]
    SYNC --> VPS_READY[VPS 端 Chopper 可查]
```

**State / 中斷重來**：每章寫一筆 `data/textbook_ingest_state.sqlite` row（book_id + chapter_index + status），失敗從未完成章節續跑，不重做已完成章節。

⚠️ **修修決策點**：vector store 要在桌機還是 VPS？
- **桌機**：embedding 寫入快、不過 SQLite + Obsidian sync 不太搭
- **VPS**：query 端就近，但桌機要每次推送 SQLite 檔（不大，幾十 MB-幾百 MB）
- **建議**：vector store 在 VPS（embedding 完桌機 scp 過去），原始章節 md 走 vault sync。

---

## 章節辨識策略（補充未列原問題）

教科書 PDF 章節邊界辨識的成敗會決定整體品質。提案：

1. **First pass — PDF outline / bookmarks**：90% 教科書 PDF 帶 outline，直接讀 TOC
2. **Second pass — heading regex**：沒 outline 就用 `r"^(Chapter|第)\s*\d+"` + font-size 偵測
3. **Third pass — LLM segmenter**：以上都失敗（掃描 PDF / 不規則排版），切 50 頁滑動視窗用 Claude Sonnet 標章節邊界
4. **Manual override**：CLI 接 `--toc-yaml` 參數讓修修手寫章節邊界（rare case）

⚠️ **修修決策點**：第 3 層 LLM 章節辨識成本可能 $1-3/書。要的話加 flag `--auto-toc`，不要的話 fallback 到 manual override。

---

## 依賴 / 前置工作

| 依賴 | 狀態 | 落點 |
|------|------|------|
| Docling 桌機本地安裝 | ⬜ 未安裝 | 桌機 |
| EPUB parser（python-ebooklib + bs4）| ⬜ 未實作 | 桌機 |
| frontmatter schema 統一（書 vs paper vs article）| ⬜ 待設計 | shared/ |
| `KB/Wiki/Sources/Books/` 目錄慣例 | ⬜ vault 內無此資料夾 | vault |
| vector store schema（chopper_kb.sqlite）| ⬜ 未設計 | shared/ |
| bge-m3 embedder + bge-reranker-v2-m3 | ⬜ 未安裝 | 桌機 + VPS |

---

## 三個未決問題（需要修修 input）

1. **章節 vs 節拆分粒度**（Q1 取捨）— 我傾向只拆到章
2. **rerank 落點 桌機 / VPS**（Q4 取捨）— 我傾向 VPS
3. **vector store 落點 桌機 / VPS**（Q5 取捨）— 我傾向 VPS（桌機 ingest，VPS query）

回答這三題之後可以升級為 ADR-010-textbook-ingest 開工。

---

## 對齊既有設計

- 對齊 [feedback_compute_tier_split.md](../../memory/claude/feedback_compute_tier_split.md)：重 ingest（解析 + chunking + embedding）在桌機，輕 query（retrieve + LLM compose）在 VPS
- 對齊 [project_vault_ingest_flow_drift_2026_04_25.md](../../memory/claude/project_vault_ingest_flow_drift_2026_04_25.md)：新 schema 直接用 `content_nature: textbook`（allowlist 已支援）
- 對齊 LifeOS CLAUDE.md：Raw/ 不改（Layer A）+ Wiki/ 主工作區（Layer B）
- 對齊 [project_chopper_community_qa.md](../../memory/claude/project_chopper_community_qa.md)：Chopper 預設 KB 來源
