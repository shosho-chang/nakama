# ADR-010: Textbook Ingest — Claude Code skill + Karpathy-style cross-book Wiki

**Date:** 2026-04-25
**Status:** Accepted

---

## Context

Chopper agent（社群健康問答，[project_chopper_community_qa.md](../../memory/claude/project_chopper_community_qa.md)）的設計前提是 Robin KB 內含教科書 + 研究文獻供查詢。但 Robin 既有 ingest（[agents/robin/ingest.py](../../agents/robin/ingest.py)）是「per document = 1 Source Summary」單位 — 一本 1500 頁的醫學教科書硬塞會被壓縮成「整本書講什麼」的幾百字大綱，retrieval 失準。**「整本書怎麼進 KB」沒有獨立 workflow**，是 Chopper 開發前必須解決的 blocker。

設計過程透過對話拍板，最終版本與最初的提案有重大轉折，特別是 Q4 / Q5：

1. **Karpathy 的 KB 哲學**（[karpathy gist](https://gist.github.com/karpathy/442a6bf555914893e9891c11519de94f)）— 教科書 ingest 核心是 "filing each chapter as you go, building out pages for characters, themes, plot threads, and how they connect" — 每章吃完都增建 vault 內的概念頁、實體頁，跨書互相 backlink，最後產出「rich companion wiki」。
2. **Claude Code Opus 4.7 1M context** — 教科書數量少、絕大多英文 → 用 Claude Code + Max 200 quota 走 Opus 整章 in-context 處理，**完全跳過 embedding / vector store / 桌機 GPU**。

詳細決策過程見 [docs/plans/2026-04-25-textbook-ingest-design.md](../plans/2026-04-25-textbook-ingest-design.md) + [docs/plans/2026-04-25-textbook-ingest-decisions.md](../plans/2026-04-25-textbook-ingest-decisions.md)。

**援引原則**：
- Schema：`docs/principles/schemas.md` §1-§4（contract 先寫、schema_version、extra="forbid"、Literal 取代 enums）
- Skill 三層架構：[feedback_skill_design_principle.md](../../memory/claude/feedback_skill_design_principle.md)
- KB Wiki 哲學：Karpathy gist（filing-as-you-go + cross-page wiki + LLM 處理 bookkeeping）
- Compute tier：[feedback_compute_tier_split.md](../../memory/claude/feedback_compute_tier_split.md)（修正：embedding 不再強制桌機，因為已棄用 embedding）

---

## Decision

### D1. Ingest trigger 與 pipeline 落點

**Decided: Claude Code skill 走 Opus 4.7 1M context，Mac 本機跑。**

- Skill 路徑：`.claude/skills/textbook-ingest/`
- 觸發詞：「ingest 這本書 <path>」、「把這本教科書加進 KB」、「textbook ingest <path>」
- 執行端：Mac 本機 Claude Code（修修開 IDE → 觸發 skill → interactive 一章一章跑）
- LLM：Opus 4.7（Max 200 subscription，1M context window）
- 輸出端：直接 Write 工具寫進 vault，Obsidian Sync 自動帶到 VPS → Chopper kb-search 可查

**為什麼選 Claude Code 而非 API**：

| 維度 | API（程式化） | Claude Code |
|------|------------|-------------|
| 成本 | Sonnet ~$3-5 / 書 | Max 200 subscription（已付） |
| Context | 200k 強制 map-reduce | 1M Opus 整章 in-context |
| 互動 | batch only | interactive，可中斷可介入修正 |
| 品質 | reduce 階段掉 context | 整章 in-context，品質高 |
| 落點 | 都可 | Mac 自然落點 |
| 失敗復原 | 重跑整批 | 從失敗章續跑 |

**為什麼不在 VPS 跑**：VPS 沒 Claude Code，要在 VPS 跑就退回程式化 API → 失去 1M context 優勢。

### D2. Frontmatter schema 凍結

#### Book Entity（`KB/Wiki/Entities/Books/{book_id}.md`）

```yaml
type: book
book_id: harrison-internal-medicine-21e   # slug, 唯一識別
title: "Harrison's Principles of Internal Medicine"
authors: ["Loscalzo J", "Fauci AS", "Kasper DL", "Hauser SL", "Longo DL", "Jameson JL"]
isbn: 9781265060190
edition: "21st"
pub_year: 2022
publisher: "McGraw-Hill"
language: en              # zh-TW / en / 其他
book_subtype: textbook_pro
chapter_count: 30
ingested_at: 2026-04-25
ingested_by: "claude-code-opus-4.7"   # 紀錄誰 ingest 的（之後 multi-provider 用）
status: complete          # complete / partial（中斷重來時用）
```

`book_subtype` allowlist（5 個值）：

- `textbook_exam` — 國考用書
- `textbook_pro` — 專業參考書
- `popular_health` — 科普健康書
- `clinical_protocol` — 臨床指引
- `reference` — 參考資料 / 手冊 / 字典

#### Chapter Source（`KB/Wiki/Sources/Books/{book_id}/ch{n}.md`）

```yaml
type: book_chapter
source_type: book               # Reader UI pill
content_nature: textbook        # 已在 allowlist
lang: en
book_id: harrison-internal-medicine-21e   # 反查 Book Entity
chapter_index: 3                # 1-based
chapter_title: "Cardiovascular Examination"
section_anchors:
  - "3.1 Inspection"
  - "3.2 Auscultation"
  - "3.3 Murmur Differentiation"
page_range: "142-187"
ingested_at: 2026-04-25
```

#### Concept / Entity 頁（既有 Robin schema 加一欄）

既有 Robin Concept / Entity 頁已有自己的 schema（`type: concept` / `type: entity` + `domain` + `confidence` + `tags` 等）。**新增 `mentioned_in:` 欄位** 串起所有來源：

```yaml
title: Beta Blocker
type: concept
domain: cardiovascular
mentioned_in:
  - "[[Sources/Books/harrison-21e/ch5]]"
  - "[[Sources/pubmed-12345]]"
  - "[[Sources/popular-health-article-xyz]]"
```

每次 ingest 命中既有 concept 時，append 新 source 到 `mentioned_in:`（不覆寫）。

### D3. Vault 落地結構

```
KB/
├── Raw/Books/
│   └── {book_id}.pdf                    ← 原檔備份（Layer A，不可改）
│
├── Wiki/
│   ├── Entities/Books/
│   │   └── {book_id}.md                 ← 書本身入口（metadata + 章節 wikilink）
│   │
│   ├── Sources/Books/
│   │   └── {book_id}/
│   │       ├── ch1.md                   ← 章 source 頁（section-by-section summary）
│   │       └── ...
│   │
│   ├── Concepts/                        ← ★ 跨書 / 論文 / 文章 共享概念池
│   │   └── {concept-slug}.md
│   │
│   └── Entities/                        ← ★ 跨書 / 論文 / 文章 共享實體池
│       └── {entity-slug}.md
│
└── Attachments/Books/
    └── {book_id}/                       ← 章內圖表擷取（如果 PDF 抽出）
```

**為什麼方案 C+ 而非 A / B**：

- A（全部攤平到 `Sources/textbook-{book_id}-ch{n}.md`）：5 本書 × 30 章 = 150 個檔淹沒既有 Sources/ flat 命名空間，跟論文 source 頁混雜
- B（整本 Raw + Wiki 索引）：跟 Layer A `Raw/` 不可改 + Entity 入口頁概念衝突
- C+：分層子資料夾 + 跨書共享 Concept/Entity 池，對齊 LifeOS Layer A/B + Karpathy 跨檔互聯哲學

### D4. Retrieval — 不建 embedding，重用 Robin kb-search

**Decided: 完全不需要 embedding / vector store / reranker。**

Chopper 答題 retrieval 流程：

1. Chopper 接到問題 → 呼叫 Robin `/kb/research`（kb-search skill 已 production）
2. kb-search：掃 `KB/Wiki/Concepts/` + `Entities/` + `Sources/` 標題 → 給 Claude 排序「哪幾頁跟問題最相關」 → 拿 top-K
3. follow `mentioned_in:` backlink 抓所有源頭（書章 + 論文 + 文章）
4. Chopper compose：拿到多角度資料合成答案，citation 標到具體章節 / 論文

**幹掉的依賴**：

- ❌ bge-m3 / bge-reranker-v2-m3
- ❌ sqlite-vec / pgvector
- ❌ chunk size / overlap 參數調
- ❌ 桌機 GPU 跑 embedding
- ❌ 桌機 vs VPS rerank 落點討論

**Citation 格式**（寫進 Chopper system prompt）：

```
《Harrison's Principles of Internal Medicine》21e · ch3 · Cardiovascular Examination · p.142
```

組裝邏輯：從 chunk 反查 chapter source frontmatter（`book_id` + `chapter_index` + `chapter_title` + `page_range`）→ 反查 Book Entity（title + edition）→ 串字串。

### D5. Skill 內部 workflow

`.claude/skills/textbook-ingest/` 互動流程：

```
1. 修修：「ingest /Users/shosho/Books/harrison-21e.pdf 這本教科書」
2. Skill 啟動：
   a. bash 呼叫 helper：parse PDF outline + 列章節邊界
   b. 顯示給修修確認章節辨識結果（hand-off check）
3. 確認後，每章一個 turn：
   a. 用 Read tool 讀該章內容（in-context 整章）
   b. Opus 產 section-by-section Source Summary
   c. Opus 抽該章的 Concept / Entity 候選清單
   d. 對每個 concept：檢查既有 wiki 頁 → append `mentioned_in:` 或建新頁
   e. Write tool 寫 vault：Source 頁、新 Concept 頁、更新既有 Concept 頁
   f. 顯示進度 + cost 給修修（cost = Max 200 quota 用了多少）
4. 全部章節完成後，產 Book Entity 入口頁列章節 wikilink
5. 通知修修 ingest 完成、提示 Obsidian Sync 把 vault 同步到 VPS
```

**章節辨識策略**（按優先序 fallback）：

1. PDF outline / bookmarks（90% 教科書 PDF 帶 outline）
2. heading regex（`r"^(Chapter|第)\s*\d+"` + font-size 偵測）
3. Opus 自己看 50 頁滑動視窗判斷邊界（最後 fallback）
4. Manual override：CLI 接 `--toc-yaml` 修修手寫章節邊界

**重用既有 code**：

- `shared/pdf_parser.py` — pymupdf4llm，既有
- `shared/obsidian_writer.py` — write_page，既有
- `agents/robin/chunker.py` — 按 heading 切（Opus 1M context 多半用不到）
- Robin prompt template — `summarize_chunk` / concept-extract / entity-extract（直接重用）
- Robin `/kb/research` — Chopper 端用，不動

---

## Phase 切分

### Phase 1（本 ADR scope，Claude Code skill MVP）

- ✅ Frontmatter schema 凍結（D2）
- ✅ Vault 落地結構凍結（D3）
- ⬜ Skill 骨架（`.claude/skills/textbook-ingest/SKILL.md` + prompts）
- ⬜ PDF parse helper（`scripts/parse_book.py`：抽 outline、列章節邊界）
- ⬜ 寫 chapter Source 頁的 prompt template
- ⬜ Concept/Entity append 邏輯（既有頁 update / 新頁 create）
- ⬜ Book Entity 入口頁產生器
- ⬜ MVP 驗收：用一本中型英文教科書（800 頁）跑完整流程

### Phase 2（Future backlog，本 ADR 不 scope）

修修提到的兩條優化：

#### B1. 網頁 UI 介面

- (a) Nakama Bridge Hub 加「教科書 ingest」入口
- (b) Ingest 過程互動 UI / 進度條

實作思路：FastAPI route 接 PDF upload → background task 呼叫 Anthropic SDK（程式化呼叫 Claude，不是 Claude Code）→ SSE stream 進度 → 寫 vault。重用 [reference_bridge_ui_mutation_pattern.md](../../memory/claude/reference_bridge_ui_mutation_pattern.md)（PR #140 reference 實作）。

**前置依賴**：Phase 1 prompt / pipeline 邏輯穩定，能抽出可程式化呼叫的單元。

#### B2. Multi-provider subscription model 選擇

- (a) Ingest 時選 provider（Anthropic Max / OpenAI Pro / Google AI Ultra）
- (b) 各家強項不同：Opus 強推理、GPT-5 強多語、Gemini 強長 context

實作思路：抽象 `IngestProvider` interface，各家實作 adapter；ingest 命令加 `--provider claude|openai|google`。

**前置依賴**：Phase 1 prompt / vault writer / schema 介面凍結。Claude Code 是最直白的呼叫端，反而最容易抽 interface。

#### 其他 Phase 2 候選（暫存）

- 中文教科書支援（簡 / 繁體都要 — translator 介入點要設計）
- 掃描 PDF 支援（Docling 或 OCR fallback）
- EPUB / Word parser（per [project_vault_ingest_flow_drift_2026_04_25.md](../../memory/claude/project_vault_ingest_flow_drift_2026_04_25.md)）

---

## Alternatives Considered

### Alt 1: 完整 RAG pipeline（embedding + vector store + reranker）

**Rejected**：複雜度高、依賴重（bge-m3 / bge-reranker / sqlite-vec / 桌機 GPU），對教科書這種**數量少 + 靜態內容**的場景過度工程。Robin `/kb/research` 已經證明 LLM ranking 在 KB 規模 < 1000 頁時夠用，且 Karpathy KB 哲學本來就是「人類 curated wiki + LLM 動態排序」，不需要預先 embed。

### Alt 2: 整本書一份 Source Summary

**Rejected**：1500 頁壓縮成幾百字會失準到無法 retrieve。

### Alt 3: 切到節（150-300 個檔 / 書）

**Rejected**：vault 檔案數爆炸，retrieval 是 chunk + concept backlink 兩層做的，summary 切太細沒必要。

### Alt 4: API 程式化 ingest（Anthropic SDK + Sonnet map-reduce）

**Rejected**：成本（一本書 $3-5 vs Max 200 已付）+ context limit（200k 強制 map-reduce vs 1M Opus 整章 in-context）+ 互動性（batch vs interactive）三個維度都輸。

### Alt 5: 桌機本地 LLM（Qwen 3.6 / Llama）

**Rejected**：本地 LLM 對醫學內容品質不如 Opus 4.7，Concept/Entity 抽取會掉落很多細節。Phase 2 multi-provider 可以再評。

---

## Risks & Mitigations

| Risk | Mitigation |
|------|-----------|
| Max 200 quota 用完 | Phase 1 driver 是修修，自己節制；ingest 一本書估 ~1.8M token，一週 5-7 本書內 Max 200 應該夠 |
| Opus 1M context 對某些超大書（Harrison's 4000 頁）裝不下 | 已預期，按章 ingest，每章 30-100 頁綽綽有餘 |
| 章節辨識錯誤 | 4 層 fallback（outline → heading regex → Opus 自判 → manual override）；前段 hand-off 給修修確認再跑 |
| Concept/Entity 抽取重複 / 不一致 | 重用 Robin 既有抽取 prompt（已 production 驗）；既有 wiki 頁存在則 append `mentioned_in:` 不重建 |
| Obsidian Sync 同步 vault 到 VPS 延遲 | 修修肉眼確認 vault 寫入完成 → 等 Obsidian Sync → 才在 VPS 端用 Chopper |
| Ingest 中斷 / 失敗 | Book Entity `status: partial` + 章節獨立檔已寫的不重做，續跑只處理未完成章 |
| Citation 跨書衝突（同 concept 不同書互相牴觸）| Concept 頁是「集合所有來源」，內文由 LLM 整合時提示「同一 concept 多來源要保留各自觀點」 |

---

## Acceptance Criteria（Phase 1 MVP）

- [ ] `.claude/skills/textbook-ingest/SKILL.md` 文件就位、觸發詞清楚
- [ ] PDF parse helper 能正確抽 outline + 列章節邊界（用一本帶 outline 的書驗）
- [ ] 一本中型英文教科書（800 頁）能跑完整流程：
  - [ ] Book Entity 入口頁產生 + 章節 wikilink 完整
  - [ ] 30 個章 Source 頁產生 + section-by-section summary 結構正確
  - [ ] Concept / Entity 頁產生 / append 正確（既有頁不重建）
  - [ ] 章與書的 frontmatter 符合 D2 schema
- [ ] vault 寫入 → Obsidian Sync 到 VPS → Chopper 用 kb-search 能查到

## References

- [docs/plans/2026-04-25-textbook-ingest-design.md](../plans/2026-04-25-textbook-ingest-design.md) — 設計過程紀錄
- [docs/plans/2026-04-25-textbook-ingest-decisions.md](../plans/2026-04-25-textbook-ingest-decisions.md) — 拍板過程
- [Karpathy KB gist](https://gist.github.com/karpathy/442a6bf555914893e9891c11519de94f) — KB Wiki 哲學來源
- [project_textbook_ingest_design_gap.md](../../memory/claude/project_textbook_ingest_design_gap.md) — 原始 gap 描述（已 RESOLVED）
- [project_vault_ingest_flow_drift_2026_04_25.md](../../memory/claude/project_vault_ingest_flow_drift_2026_04_25.md) — 整體 ingest 流程 baseline
- [docs/diagrams/vault-ingest-flow.md](../diagrams/vault-ingest-flow.md) — Vault ingest 全景 IA diagram
- [agents/robin/ingest.py](../../agents/robin/ingest.py) — Robin 既有 ingest（重用 prompt template + chunker）
- [agents/robin/kb_search.py](../../agents/robin/kb_search.py) — Chopper retrieval 入口
- [feedback_skill_design_principle.md](../../memory/claude/feedback_skill_design_principle.md) — Skill 三層架構
