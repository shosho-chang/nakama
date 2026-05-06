# ADR-020: Textbook Ingest v3 — Raw Layer + Lossless Source + Sync Concept Aggregation + Bilingual RAG

**Status:** Accepted
**Date:** 2026-05-06
**Deciders:** shosho-chang
**Related:** ADR-010, ADR-011 (this rewrite supersedes both for textbook path), ADR-016 (this rewrite supersedes), CONTENT-PIPELINE.md Stage 3, [project_kb_corpus_stub_crisis_2026_05_06.md](../../memory/claude/project_kb_corpus_stub_crisis_2026_05_06.md)

**Audit trail (multi-agent panel):**
- v1 draft: Claude (Opus 4.7) authored 2026-05-06
- Audit 1: Codex (GPT-5 via ChatGPT auth, task-motib9le-isaj8u) — verified 87.5% stub claim by hard count, identified `kb_writer.py` impl exists but pipeline bypasses it, pushed back on pure verbatim Option D, proposed coverage manifest + 4-action dispatcher revival → [docs/research/2026-05-06-codex-adr-020-audit.md](../research/2026-05-06-codex-adr-020-audit.md)
- Audit 2: Gemini 2.5 Pro — caught multilingual blind spot Claude+Codex both missed, mandated concrete RAG architecture spec, refined Vision triage from 3-class to 6-class, proposed Concept Maturity Model replacing binary promotion threshold → [docs/research/2026-05-06-gemini-adr-020-audit.md](../research/2026-05-06-gemini-adr-020-audit.md)
- v2 integration: 2026-05-06, all 5 Gemini-mandated modifications + Codex 4-action revival + Claude verbatim body kept

---

## Context

2026-05-06 發現 KB Concept corpus 87.5% (544/622) 是 phase-b-reconciliation 自動生成的 stub 空殼，body 只有一行「Will be enriched as Robin processes future ingests or as 修修 fills in body」。ch5 source page spot-check 顯示 ~75-85% 資訊密度（primary fact 高保留 / secondary nuance + clinical edge case 低保留），且**沒有任何 acceptance gate 量過資訊密度**。

修修拍板「現在不要再談什麼修修補補了，整個 review 重寫一次」。本 ADR supersedes textbook ingest 路徑的 ADR-010 / ADR-011 / ADR-016 三份疊代設計，並回應四件 root cause:

1. **ADR-016 沉默違反 ADR-011 P1**: Phase A 禁碰 Concept + Phase B 只生 stub 收尾 wikilink → ADR-011 §3.3 規範的 4-action dispatcher (`create` / `update_merge` / `update_conflict` / `noop`) 在並行路徑**完全 unreachable**。`shared/kb_writer.upsert_concept_page` (line 591-786) 實作其實在，bug 是 **pipeline bypass 不是 code 缺失**（Codex audit §2 驗證）。
2. **沒有「品質」column 的 metrics**：ADR-016 §3 metrics 用 wall time 5x speedup 當 KPI，無 concept body completeness / source page 資訊密度 measure。
3. **「Quality > speed」變裝飾性 trailer**：phase-b-reconciliation.md line 171 寫了，但 Step 3 stub template 本身違反它，沒人發現衝突。
4. **「Will be enriched later」是 deferred TODO 寫進 data**，沒 trigger / owner / deadline / mechanism — 累積 544 個永不 enriched 的空殼。

**Multilingual context（Gemini audit §3 揭露的關鍵維度）**: 系統 fundamentally 雙語 — 教科書原文是英文（Sport Nutrition 4E / Biochemistry of Sport & Exercise），但 KB Wiki 的 concept page 用繁體中文（`[[腸道菌群]]`、`[[微絨毛]]`、`[[肌酸]]`）。先前 ADR-010/011/016 跟 Claude 草稿都假設「同語言」忽略此事實。

修修最高指導原則：**品質 > 速度 > 成本**。Phase 1 全文 copy 進個人 KB 在著作權法 §51 個人非營利合理重製範圍內合法，對外輸出走 RAG transformation 已是衍生作品（不是 verbatim distribution），市場替代風險低（Health & Wellness 內容不替代教科書市場）。

---

## Decision

Four-phase ingest pipeline replacing ADR-016 Phase A/B 拆分，加上配套的 RAG architecture + multilingual discipline + maturity model. Phase 0 lossless EPUB→Raw 對齊 vault 分層原則（Raw = immutable source of truth, Wiki = LLM-加工成品，per CLAUDE.md vault 規則）:

### Phase 0 — Lossless EPUB Conversion

EPUB / PDF / HTML → `KB/Raw/Books/{book-title}.md` 整本 raw markdown，**無 LLM 補述、無切 chapter**。對齊 CLAUDE.md vault 分層: Raw = immutable source of truth, Wiki = LLM-加工成品。

工具選型 spike（S0 slice 決定）:

| 工具 | 強項 | 弱項 |
|---|---|---|
| **pandoc** | mature document converter；markdown 輸出結構清楚 | table 輸出 space-aligned grid（非 GFM pipe）；需外部 binary（~34 MB）；anchor stub 雜訊 |
| **ebooklib + markdownify (Python)** | 純 Python；GFM pipe table；spine order explicit；programmatic path rewrite | markdownify 不轉換 MathML（Phase 0 可接受） |
| **Calibre `ebook-convert`** | 圖檔抽取最完整 | ~100 MB system install；非 pip；subprocess only |

**選型決定（S0 spike 完成）：ebooklib 0.20 + markdownify 1.2.2**

選型優先順序: **lossless > 結構簡潔 > 圖檔 path 一致性**。Spike 詳細比對見 [`docs/spike/2026-05-06-epub-converter.md`](../spike/2026-05-06-epub-converter.md)。

選型理由摘要:
1. **GFM pipe table** — pandoc space-aligned grid 非標準，Obsidian/GitHub 皆支援 pipe table
2. **In-process path rewrite** — BeautifulSoup 前處理直接改寫 `<img src>` 為 vault-relative path，無需 subprocess round-trip
3. **Pure Python, 零 binary dep** — ebooklib + markdownify 無 binary；已在 requirements.txt（ebooklib）
4. **Explicit spine order** — `book.spine` 以 `[(idref, linear)]` 回傳讀取順序，chapter boundary 偵測確定性高

Output 規範:
- 純 markdown — heading / paragraph / list / image link / inline code / inline math 保留
- 圖檔抽到 `Attachments/Books/{book-id}/{fig-name}.{ext}`（per-book 子目錄，命名 spike 階段定）
- Frontmatter minimal（`title` / `source_epub_path` / `converted_date` / `converter_tool` / `converter_version` / `book_id`）
- 不切 chapter（chapter boundary 由 Phase 1 LLM 識別 — 因為 EPUB internal chapter division 不一定符合教科書真章節邏輯）

Why Phase 0 layer:
- **Vault 分層**：Raw immutable，Wiki 才是 LLM-加工成品 — 既有 CLAUDE.md 規範
- **Phase 1 從 Raw 讀**：未來 LLM prompt 改 / Sonnet 升級 / RAG 重 index，都不需重 parse EPUB；Raw 是穩定 substrate
- **可 diff / backup / sync**：純文字 markdown 跨 device 同步無 EPUB binary 痛點，git 也能 diff verbatim 改動
- **跨 agent reuse**：其他 agent（Robin paper digest cross-reference / Brook reference page generator）讀 Raw 不必經 LLM

### Phase 1 — Lossless Source Ingest（per chapter, parallelizable）

從 `KB/Raw/Books/{book-title}.md` 讀 → LLM 切 chapter boundary + Source page `KB/Wiki/Sources/Books/{book_id}/ch{n}.md` 結構：

```
[YAML frontmatter — 完整 metadata + figures[].llm_description (triaged) + tables[]]

# Chapter {N} — {Title}

## Section 1.1 Title

[原書原文 verbatim — paragraph-level，不 paraphrase 不 abstract 不刪 detail]

[圖：![[Attachments/.../fig-X-Y.ext]]
*caption*]

[表：bold caption + inline markdown table content]

### Section concept map (LLM-written wrapper)
[mermaid / bullet — 本節 concept 結構]

### Wikilinks introduced (LLM-written wrapper)
[[concept-1]] [[concept-2]] ...

## Section 1.2 ...
```

**Body 走 verbatim**（不做 paraphrase）。LLM **不重寫 body**，只**外加** structured wrapper:
- Section concept map（mermaid 或 bullet 形式的概念結構）
- Wikilinks introduced（每節抽出該節新引入的 concept seed）
- Cross-reference 建議（指向同書其他章 / 跨書）
- Figure / Table inline markdown swap（占位符 → embed + caption）

**Vision describe 6-class triage**（取代 ADR-011 「每張圖必跑 Vision」+ Claude v1 草稿的 3-class 過粗分類，per Gemini §1）:

| 類型 | 啟發式判別（walker / 圖 metadata 階段）| Vision action |
|---|---|---|
| **Quantitative** | 有 axes / legends / error bars / data points | Full Vision: 抽 data series / trends / 軸標 / key values；transcribe equations |
| **Structural** | 解剖圖 / molecular structure / histology / microscopy | Full Vision: 標所有 significant component + spatial relationship；別只 transcribe caption |
| **Process** | flowchart / metabolic pathway / multi-panel sequence | Full Vision: input/output sequence + 階段 relationship；**multi-panel (a)(b)(c) 視為單一 conceptual unit 一次描述** |
| **Comparative** | side-by-side / before-after / healthy-vs-pathology | Full Vision: focus on **differences and similarities** between compared elements，不要各自描述 |
| **Tabular** | 圖中是 grid of text/numbers（image-as-table） | Full Vision: 轉 markdown table，preserve merged cell + footnote symbols (*, †, §) + nested structure |
| **Decorative** | stock photo / portrait / 情境照 / clipart | **No Vision call**，caption only + `[decorative]` tag |

分類由 walker 啟發式（看 alt-text 長度 / 圖檔比例 / file size / sibling files of same fig prefix indicate multi-panel）+ LLM 自判 fallback（uncertain → fallback to Full Vision，不 fallback to skip）。Multi-panel 圖（`fig-5-1a.png`, `fig-5-1b.png`）必須 grouped 成一個 description 而非各自獨立描述（Gemini §1）。

### Phase 2 — Per-Chapter Concept Aggregation（sync, in-chapter）

章節寫完 source page 後**立即**跑 concept extraction + dispatch，**不 deferred 到 cross-source 收尾步驟**。走既有 `shared/kb_writer.upsert_concept_page` 4-action dispatcher (`shared/kb_writer.py:591-786`)：

| Action | When | Body 寫入內容 |
|---|---|---|
| `create` | concept slug 不存在 | v3 schema 8 H2 skeleton + 從**本章**抽出的 body content（不只是 stub）+ `en_source_terms` 欄位填入該章節原文中對應的英文術語 list |
| `update_merge` | concept 已存在 + 新 extract 跟 existing body 互補 | LLM diff-merge new extract 進 existing body + extend `en_source_terms` (dedup) + extend `mentioned_in:` |
| `update_conflict` | concept 已存在 + 新 extract 跟 existing body 數據衝突 | append 至該頁 `## 文獻分歧 / Discussion` 段，保留兩 source 各自 claim + extend `en_source_terms` + `mentioned_in:` |
| `noop` | concept 已存在 + 新章節無新增 substantive content | 僅 append source link 到 `mentioned_in:`，並 extend `en_source_terms`（如本章節原文有新英文同義詞）|

**Hard invariants**:
- **L3-active stub == ingest fail**: Phase 2 為 ≥2 sources concept 寫 L3 active page 時，body word count < 200 字 = 整章 ingest fail，回報具體 concept slug 給人類介入。L2 stub（單 source high-value，見 Phase 3）走另一路徑，是 productive workflow state 不是 failure。
- **0 phase-b-reconciliation-style stub tolerance**: 不再生「Will be enriched later」一行 placeholder。所有 stub 必走 L2 Maturity Model 規範（含初步 body + 升級條件 prompt）。

**Bilingual term mapping discipline（Gemini §3 必修）**:
- Phase 2 任何 action（create / update_merge / update_conflict / noop）都必須抽當前章節原文中對應該 concept 的所有英文表達 → 寫入 `en_source_terms` frontmatter list（dedup）
- 例：concept `[[腸道菌群]]` 的 `en_source_terms` 應含 `["gut microbiota", "intestinal flora", "gut microbiome"]`，每次 ingest 新章節若見新同義詞就 append
- 此 list 對應的 RAG query expansion 跟 cross-lingual reranker training 用，是讓「英文教科書 → 繁中 KB」雙語對齊 measurable 的 ground truth

並行性處理：
- Phase A subagent 仍 per-chapter parallel（保留 ADR-016 5x wall time 部分價值）
- 但 subagent 寫**自己章節的** Source page **+ Phase 2 concept dispatch**（不再 HARD-CONSTRAINT 禁碰 Concept）
- Concept page race condition 走 **per-concept advisory lock**（用既有 `shared/locks.py` 或 SQLite `BEGIN IMMEDIATE`）— 同 concept 多章 simultaneous update 走序列化排隊
- 並行 chapter 不會互相 lock（不同 concept 互不衝突）；同 concept 高頻 hit 走小範圍序列化，wall-time impact 預估 < 20%

### Phase 3 — Concept Maturity Model（取代 binary promotion threshold, per Gemini §5）

不採 v1 草稿的「promote OR alias」二元 threshold（過於 blunt instrument）。改用三層 Maturity Model:

| Level | 條件 | 形式 | 升級條件 |
|---|---|---|---|
| **L1: Alias** | 出現 1 次 + LLM classifier 判定 low-value（無 section heading / 無 bolded definition / passing mention） | 留在 `KB/Wiki/_alias_map.md` lightweight 索引：`slug → first-seen source` | 下次 ingest 觸發 re-evaluate；若見第二 source 或 high-value 訊號 → 升 L2/L3 |
| **L2: Stub** | 出現 1 次但 LLM classifier 判定 high-value（含 section heading 為該 term / 有 bolded define / 多次提及含 cross-section ref / 教科書 explicit 強調） | 生 concept page，body 含當前章節抽出的初步內容 + frontmatter `status: stub` + body 末加 prompt：「需要：另一 source 的 cross-reference 或人工 review 後升 L3 active」。**這是 productive workflow state**，配合 hard min 200 字 body，**跟 phase-b-reconciliation 一行 placeholder 完全不同概念** | 下次 ingest 見第二 source → 自動升 L3；或 修修 手動 review 升 L3 |
| **L3: Active** | ≥ 2 sources 出現（chapter / paper / article 跨來源計數）OR L2 stub 經人工 review | 完整 aggregator concept page，body word count ≥ 200，frontmatter `status: active`，含 `mentioned_in:` 多條 + `en_source_terms` 多條 | — |

**LLM classifier「high-value」判定規則**（取代 v1 草稿的「多次提及 + 跨節 cross-ref」vague rule, per Gemini §5 「quantifiable rule needed」）:

從本章節抽 concept 候選時，對每個 candidate 跑下列檢查（任 1 條成立 → high-value，promote 到 L2）:
1. 該 term 是某 section heading 或 sub-section heading
2. 該 term 在原文中以 `**bold**` / 教科書 `*italic-define*` 形式定義（教科書定義 marker）
3. 該 term 出現 ≥ 3 次跨 ≥ 2 sub-section
4. 該 term 後接「is defined as」/「is referred to as」/「is the process by which」/ 中文「稱為」/「定義為」等定義語句

**False-Consensus guard（Gemini §5）**: L3 promotion 不純看「2 sources」count。merge 前必須 LLM 比對兩 source 的 concept scope:
- 若兩 source 是同 concept 不同 facet（例：textbook ch3 講 creatine phosphate 機制 + ch12 講 supplementation protocol）→ `update_merge` 兩段 body 各自保留 + 加 `## Different Facets` section
- 若兩 source 名稱相同但實是不同 concept（罕見但例：textbook 「creatine」vs 民間誤用「肌酸 = 能量飲料」）→ split into two concept pages with explicit disambiguation note，不 merge

### Cross-source aggregation post-pass（取代 ADR-016 Phase B housekeeping）

Phase B subagent 重新定位為「跨書收尾 housekeeping」（不再生 stub）:
- 所有 chapters Phase A + Phase 2 完成後跑一次
- `mentioned_in:` backlink 統一性檢查 / dedupe（per-chapter Phase 2 已 incremental write，post-pass 只檢查一致性）
- `KB/Wiki/Entities/Books/{book_id}.md` Book Entity 完成度更新 `chapters_ingested: N` + `status: complete`
- `KB/index.md` 補入新 book entry（既有 5/6 發現 index.md 0 條 Concepts 的 gap）
- `KB/log.md` append milestone（停在 4/25 的 gap）
- **Cross-chapter concept clustering pass**（Gemini §4 per-chapter atomicity 議題）: 單一 chapter 內可能同時涵蓋多 disparate topic（例 Carb chapter = digestion + glycolysis），單一概念可能跨 chapter（Krebs Cycle 在 ch3 介紹 + ch7 臨床應用）。post-pass 跑 LLM cluster：對 active concepts 看是否需要 split（單 concept 在 KB 變兩頁的反向）或 cross-chapter merge（補 `mentioned_in:` 缺漏）

---

### RAG Architecture Specification（new section, Gemini §2 必修）

ADR-020 commits to verbatim body 為主，而非 paraphrase。這個選擇**只在配套 RAG 架構到位時 viable**。本 ADR 直接 spec RAG baseline，不 punt to「implementation detail」（Gemini 警告留白會讓 verbatim 選擇失敗）：

#### Chunking — Parent-Child Semantic Hierarchical（取代 sliding window fixed-size）

每個 source page 在 indexing 時拆兩層 chunk:

| Chunk type | 內容 | 大小 |
|---|---|---|
| **Parent chunk** | 每 `## Section` 一個 parent — 含 section title + LLM-generated `Section concept map` + `Wikilinks introduced` list（不含 verbatim body）| ~200-400 字（精簡語義摘要）|
| **Child chunks** | section 內的 verbatim 段落，每 chunk 含 3-5 連續段落 + 1 段落 overlap 跟前後 chunk | ~500-800 字（含 verbatim）|

每個 child chunk metadata 含:
- `parent_id` → 指向 section parent chunk
- `book_id` / `chapter_index` / `section_anchor` / `paragraph_range`
- `figures_referenced` / `tables_referenced` (若該段落 reference 某圖表)
- `concepts_introduced` (該段落首次提到的 wikilink targets)

#### Embedding — BGE-M3（取代既有 hybrid retrieval Phase 1a 的 model2vec potion-base-8M 256d）

選 BGE-M3 理由:
- **Native cross-lingual alignment** — 對英文教科書 verbatim + 繁中 wikilink target 同一 vector space embedding（model2vec 256d 對中文支持較弱）
- **Multi-granularity** — 同 model 同時 produce dense vector + sparse score + multi-vector，配合下方 hybrid retrieval
- 1024d output，比 model2vec 256d 表達能力強約 4x（textbook scientific corpus 高密度語義必要）

Phase 1a `shared/kb_embedder.py` 已實作 model2vec lazy-load 介面（PR #436），v3 新加 BGE-M3 backend，flag 切換: `embed_model: "bge-m3" | "potion-base-8m"`。

#### Retrieval — Hybrid + Reranker（**non-negotiable** per Gemini §2）

```
user query
   │
   ├──▶ Stage 1: 並行 hybrid search（top-K=20）
   │     ├── BM25 sparse retrieval (FTS5 / BGE-M3 sparse score)
   │     │   ↑ 數字 / 專名 / 罕見 keyword 強項（例 "97% fat absorption"）
   │     └── Dense vector retrieval (BGE-M3 dense embedding)
   │         ↑ 語義相關 + 跨語言 alignment 強項
   │     合併 with Reciprocal Rank Fusion (RRF, k=60，已實作於 PR #436)
   │
   ├──▶ Stage 2: 對 top-20 candidates 跑 cross-encoder reranker
   │     ├── Model: bge-reranker-large
   │     │   ↑ 對 query+chunk 一起 score，比 dot-product similarity 精準
   │     └── 篩選出 top-N=5 進 final context
   │
   └──▶ Stage 3: Small-to-big retrieval pattern
         ├── 上述 retrieval 對象是 parent chunks（精簡語義摘要）
         ├── 命中 parent 後，pull 該 parent 的 child chunks（verbatim 段落）進 LLM context
         └── LLM 看到的是 verbatim 原文 + parent 提供的語義 framing
```

**Reranker 是 non-negotiable**（Gemini §2 明確 mandate）— 沒 reranker 直接讓 verbatim chunks 進 LLM，retrieval noise 會把 verbatim 的 lossless 優勢完全抵銷。

#### Query expansion — Bilingual via en_source_terms（new, Gemini §3）

當 user query 有 wikilink target（例「腸道菌群」）時，retrieval 階段 augment query:
1. 讀 concept page `[[腸道菌群]]` 的 frontmatter `en_source_terms: ["gut microbiota", "intestinal flora"]`
2. 並行 retrieve 三個 query：原 query「腸道菌群」+ 每個英文同義詞
3. 合併 retrieval results 走 RRF

這把「英文教科書 verbatim 內文 vs 繁中 query」這個 cross-lingual gap 從 unknown risk 變 measurable 工程問題。

---

### Coverage Manifest Acceptance Gate（refined per Gemini §4）

每章 ingest 結束時，產出 `KB/Wiki/Sources/Books/{book_id}/ch{n}.coverage.json`:

```json
{
  "chapter_index": 5,
  "walker_raw_path": "E:/textbook-ingest/.../ch5.md",
  "section_count_walker": 11,
  "section_count_ingested": 11,
  "claims_extracted_by_llm": [
    {"claim": "small intestine surface area ~250 m²", "category": "primary", "found_in_page": true, "source_section_walker": "Anatomy"},
    {"claim": "achlorhydria patients can have 10,000 to 100M microorganisms/ml", "category": "primary", "found_in_page": false, "source_section_walker": "Gut Microbiota"},
    {"claim": "cellulose possibly up to 30% digested by colon microbiota", "category": "secondary", "found_in_page": false, "source_section_walker": "Carbohydrate Digestion"},
    ...
  ],
  "primary_claims_missing_pct": 8.3,
  "figures_count": 24,
  "figures_embedded": 24,
  "tables_count": 7,
  "tables_inline_markdown": 7,
  "tables_transcluded": 0,
  "verbatim_paragraph_match_pct": 97.2,
  "wikilinks_introduced": ["[[消化道]]", "[[腸道菌群]]", ...],
  "concept_dispatch_log": [
    {"slug": "腸道菌群", "action": "create", "level": "L3", "body_word_count": 487, "en_source_terms": ["gut microbiota", "intestinal flora"]},
    {"slug": "微絨毛", "action": "update_merge", "level": "L3", "merge_diff_size": 234, "en_source_terms_added": ["microvilli", "brush border"]},
    {"slug": "Cori-cycle", "action": "create", "level": "L2", "body_word_count": 312, "high_value_signals": ["section_heading", "bolded_define"]},
    ...
  ],
  "acceptance_status": "pass" | "fail",
  "acceptance_reasons": ["primary_claims_missing_pct > 5%: 8.3%"]
}
```

**「Primary」definition — LLM-classified, NOT regex-extracted**（Gemini §4 必修）:

「primary number / primary claim」是語義決定（content-based subjective），regex 抽 `\d+%` 之類保證失敗。改用 LLM classifier：

acceptance gate 工作流:
1. 走 walker raw chapter，LLM (Sonnet 4.6) 分類抽出本章所有「claim units」（事實、數字、機轉、臨床判讀）— 這個分類是 ingest pipeline 的 ground truth，先跑一次存 `walker_claims.json`
2. 每個 claim unit 標 `category: primary | secondary | nuance`:
   - **primary**: 該主題核心事實（教科書若刪這條讀者就學不會該主題）
   - **secondary**: 次級 detail（背景、額外 example）
   - **nuance**: 邊緣 case / 統計細節 / 罕見 example
3. ingest 完 vault page 後，LLM 讀 vault page，對每個 walker claim 判 `found_in_page: true | false`
4. coverage manifest gate fail conditions（**hierarchical**, primary 比 secondary 嚴）:
   - `primary_claims_missing_pct > 5%` → fail
   - `secondary_claims_missing_pct > 25%` → warn (not fail，但 log)
   - `nuance_claims_missing_pct > 50%` → not gate (acceptable loss)
   - `figures_count != figures_embedded` → fail
   - `tables_transcluded > 0` → fail（spec drift detected — Codex audit ref ch5 violation）
   - `concept_dispatch_log` 任一條 `action: phase-b-style-stub` → fail（0 一行 placeholder tolerance）
   - `concept_dispatch_log` 任一條 L3 active 的 `body_word_count < 200` → fail

Acceptance fail = ingest fail = 不寫進 vault（或寫進 staging dir 等人類介入）。

---

### Multilingual Considerations（new section, Gemini §3 揭露）

系統 fundamentally 雙語。設計層面三件 baked-in:

1. **Concept page 用繁中 title**（既有約定）+ frontmatter `aliases: [English equivalents]` + `en_source_terms: [each occurrence in source]`
2. **Source page body 保英文 verbatim**（不翻譯，§Phase 1 verbatim invariant）
3. **RAG retrieval bilingual-aware**（§RAG Architecture / Query expansion via en_source_terms）

**Cross-lingual retrieval ground truth dataset**（Gemini §3 提案的 measurable risk 路徑）:
- ingest 過程累積的 `en_source_terms` 是天然 bilingual term mapping dataset
- 後續可用於:
  - 評估 BGE-M3 對該 corpus 的 cross-lingual alignment 表現（量化指標：給中文 query「腸道菌群」，能 retrieve 含「intestinal flora」段落的 recall@10 是多少）
  - Fine-tune cross-lingual reranker（若 BGE 預設表現不夠 → 用此 dataset fine-tune）
  - 給 修修 review 「LLM 抽的英文 source term 是否 canonical」

---

## Considered Options

### (A) 修修原 Option D：Pure verbatim body + LLM wrapper only

修修 5/6 提案。Body 100% walker verbatim blockquote，LLM 只外加 wrapper（圖、wikilink、concept map），不加 RAG architecture spec。

**Rejected** — Codex audit 4 條 push-back（中 3 條成立）+ Gemini §2 警告:
- Token retrieval payload bloat
- Wikilink graph 弱化（教科書原文不會自然含修修要的中文 `[[wikilink]]` 密度，需 LLM wrapper 補）
- Maintenance: 書再版時 verbatim 區塊只能 text-diff 不能 claim-diff
- Concept aggregator P1 哲學受影響
- **Gemini decisive**: 「leaving RAG as 'implementation detail' is a critical flaw — verbatim necessitates more sophisticated RAG architecture than paraphrased」

但「verbatim body」這個方向**修修直覺仍對**：信息密度 > 格式偏好，前提是 RAG 架構到位。本 ADR Phase 1 採納 verbatim body **在 source page**，但靠 §RAG Architecture Specification 整套配套（Parent-child chunking + BGE-M3 + Hybrid + Reranker + bilingual query expansion）讓 verbatim 選擇 viable。

### (B) 維持 ADR-011 Pure structured paraphrase

LLM 重寫 body 為 markdown bullet/numbered list，no verbatim。

**Rejected** — 5/6 ch5 spot check 75-85% 資訊密度 + 沒 enforcement = 繼續累積 GIGO。修修 explicit 拒絕「修修補補」路線。

### (C) Codex 提的 claim-dense + short verbatim anchors + coverage manifest

Codex audit §5 提案。Body 仍 paraphrased（claim-dense）+ 每節 short verbatim quote anchor + coverage manifest gate。

**Partially adopted** — 採納:
- ✅ Coverage manifest gate（refined: LLM-classified primary/secondary/nuance hierarchical instead of regex）
- ✅ Per-chapter sync Phase 2
- ✅ 4-action dispatcher（既有實作活化）
- ✅ Cross-source promotion threshold（refined: replaced binary with Concept Maturity Model）

不採:
- ❌ Body claim-dense paraphrased — 採用修修 verbatim 直覺。理由：修修「品質 > 速度 > 成本」的「品質」第一義是**信息完整保留**。verbatim body 是 lossless 的硬保證，paraphrased（即使 claim-dense）仍是 paraphrase drift 風險源。Gemini RAG architecture spec 解掉 Codex 講的 retrieval 弱點。

### (D, chosen) Hybrid: Verbatim body + LLM wrapper + sync Phase 2 + coverage manifest gate + Concept Maturity Model + RAG architecture spec + Bilingual term mapping

組合修修直覺（verbatim body）+ Codex 工程提案（sync Phase 2 + coverage manifest + 4-action dispatcher）+ Gemini 必修五件（RAG arch spec + bilingual term mapping + Maturity Model + 6-class Vision triage + LLM-classified coverage）+ Vision triage 6-class。

---

## Consequences

### Positive

- **0% information loss** in source page body（lossless guarantee）
- **0 phase-b-style stub** in Concept corpus（hard invariant 取代「Will be enriched later」TODO）
- **L2 stub 變 productive workflow state**（Cori cycle 這類 niche-but-critical concept 有歸宿）
- **544 既有 stub cleanup**: 一次性 re-ingest Sport Nutrition 4E + BSE 兩本（預估 ~3-5 hr/book on desktop, Opus 4.7 1M, 含 sync Phase 2）
- **資訊密度有 enforcement** (LLM-classified hierarchical coverage manifest gate)
- **Concept page 變少更深**: 從「每 wikilink 一頁」變成「Maturity-Model 篩選」，預估從 622 降到 ~150-250 真正 aggregator pages（active L3）+ ~50-100 L2 stubs（workflow state）+ 大量 L1 alias 在 `_alias_map.md`
- **既有 implementation 活化**: `shared/kb_writer.upsert_concept_page` 4-action dispatcher 從「unused code」變回 hot path
- **Bilingual KB 真實工程化**: `en_source_terms` 把雙語 alignment 從 unknown 變 measurable，配合 BGE-M3 + reranker 解中文 query 撈英文 verbatim corpus 問題
- **Vision token cost 降**: 6-class triage 後 ~30-50% 圖跳過 Vision call（decorative + caption-rich tabular），Sonnet 4.6 vision call 數從 ~24/章降到 ~12-16/章
- **著作權合規**: §51 個人非營利重製 + 對外輸出走 RAG transformation（非 verbatim distribution）+ 不公開 raw KB endpoint

### Negative / Cost

- **Token cost 升 1.5-2x** vs ADR-011 現狀:
  - +verbatim body（vs paraphrase）：~1.3x output tokens
  - +sync Phase 2 concept body write（vs deferred stub）：~0.3x extra LLM turn per chapter
  - +LLM classifier for high-value detection + claim extraction for coverage manifest：~0.2x extra
  - 修修 explicit「品質 > 速度 > 成本」原則 cover
- **Wall time 退化部分**：ADR-016 並行 5x speedup 部分讓步給 Phase 2 sync，但 chapter-level 並行仍保留（per-concept lock 解 race），預估 wall-time impact < 20%
- **Vault size 增 ~1.5x**: source page verbatim body 比 paraphrase 大；對個人 KB 規模 negligible
- **RAG infrastructure cost**: BGE-M3 model 1.7GB + bge-reranker-large 1.1GB 載入 RAM；本機桌機（64GB RAM）無壓力，但 VPS（4GB RAM）跑不動 — 確認 retrieval 走桌機 / Mac / R2 blob，VPS 只跑 query intake（既有 reference_compute_tier_split.md 分工）
- **Implementation cost**: 8 個 slice（從 v1 的 6 個擴），預估 2-3 週完成 + 1-2 天 cleanup re-ingest

### Risks & open questions

1. **BGE-M3 + bge-reranker-large 對該 corpus 的真實表現** — 需 implementation 階段對 Sport Nutrition + BSE 跑 evaluation set（用 `en_source_terms` 當 ground truth：給中文 query，Recall@10 對含對應英文段的 child chunk）
2. **LLM-classified primary/secondary/nuance** — Sonnet 4.6 對「primary claim」分類的一致性需要 spike test：同一段跑 3 次看 inter-run consistency；若不穩走 self-consistency voting (3-of-5 majority)
3. **Concept Maturity Model L1/L2/L3 的 false-positive rate** — high-value classifier 誤判 L2 → 後續累積 stub 過多。需要 50 個既有非 stub concept page 當 L2/L3 ground truth set 跑 classifier 看 precision/recall
4. **Concept body 200 字 hard min** — 既有 78 個 non-stub concept page 的 body word count distribution baseline 需先量
5. **False-Consensus guard 的 LLM scope-comparison 成本** — 每次 update_merge 前都跑 scope 比對 LLM call，可能成本可觀；考慮只在「concept 已有 ≥2 sources 且 update_merge 又見新 source」時才跑
6. **Per-chapter atomicity 的 cross-chapter clustering post-pass** — Krebs Cycle 這類概念，post-pass cluster LLM 怎麼判「該 split 還是該 merge」？需要實作前 prototype + 修修 review 規則
7. **既有 622 stub cleanup 順序**：建 v3 pipeline → re-ingest 是 sequential dependency

---

## Implementation slices

| Slice | What | Replaces | Effort |
|---|---|---|---|
| **S0** | EPUB → Raw markdown converter (pandoc / ebooklib / Calibre 三家 spike + 選型 + writer 寫 `KB/Raw/Books/`) | new feature (Phase 0 補 vault 分層) | 1 day |
| **S1** | Phase 1 walker (**改從 `KB/Raw/Books/` 讀**) → verbatim body + LLM wrapper prompt | `chapter-summary.md` v2 prompt | 1-2 days |
| **S2** | Phase 2 in-chapter sync concept dispatch + per-concept lock + 4-action revival | `phase-a-subagent.md` 「禁碰 Concept」HARD CONSTRAINT | 2-3 days |
| **S3** | Concept Maturity Model classifier (high-value detection) + L1/L2/L3 routing | `phase-b-reconciliation.md` Step 3 stub generation | 2-3 days |
| **S4** | Coverage manifest LLM classifier (primary/secondary/nuance) + acceptance gate | ADR-011 §6 placeholder/figures/concepts acceptance | 2-3 days |
| **S5** | Vision 6-class triage classifier + multi-panel grouping | `vision-describe.md` + `phase-a-subagent.md` 「every fig」rule | 2 days |
| **S6** | RAG infrastructure: BGE-M3 backend + bge-reranker-large + Parent-Child chunking + Hybrid retrieval pipeline | 既有 PR #436 hybrid retrieval Phase 1a (model2vec backend) | 3-4 days |
| **S7** | Bilingual term mapping (`en_source_terms` extraction + populate) + query expansion in retrieval | new feature | 1-2 days |
| **S8** | Cleanup re-ingest Sport Nutrition 4E + BSE (含 Phase 0 EPUB→Raw + Phase 1-3 完整流程) | one-shot operation | 1 day |

S0-S7 sequential（每 slice merge 才開下一個 slice 避免 context drift；S0 → S1 dependency 強，S1 必須有 Raw producer 才能讀）。S8 是 cleanup，S0-S7 全 ship 後一次性跑。

---

## Concept page body schema（v3, hard min enforced）

```yaml
---
title: 過度訓練症候群
aliases: [Overtraining Syndrome, OTS]
en_source_terms:                        # NEW v3 — bilingual term mapping
  - "overtraining syndrome"
  - "overreaching"                      # NFOR / functional overreaching 相關
  - "overtraining"
type: concept
domain: sport-nutrition
schema_version: 3
status: active                          # active | stub (L2 only — phase-b-style stub forbidden)
maturity_level: L3                      # L1/L2/L3 per Maturity Model
mentioned_in:
  - "[[Sources/Books/sport-nutrition-jeukendrup-2024/ch12]]"
  - "[[Sources/Books/biochemistry-sport-exercise-2024/ch7]]"
  - "[[Sources/pubmed-12345]]"
created: 2026-05-08
created_by: phase-2-concept-dispatcher
---

# 過度訓練症候群（Overtraining Syndrome）

## Definition
（從第一個 source merge 進來的 1-2 段 definition，verbatim quote where useful）

## Core Mechanism
（生理機制描述 — 從多 source aggregate）

## Different Facets                     # 若 update_merge 兩 source 是不同 facet
（textbook ch3 講機制 + supplementation chapter 講 protocol — 各保留）

## Diagnostic Markers
（早晨 HRV / 心率變化 / 情緒問卷 / 訓練表現下降，含 reference value）

## Differential Diagnosis
（區別 functional overreaching / non-functional overreaching / OTS）

## Recovery Protocol
（休息策略 / 漸進回場 / 監控指標）

## 文獻分歧 / Discussion
（若多 source 數值衝突，列在這裡並標 source — `update_conflict` action 寫入這段）

## See also
- [[HRV]]
- [[訓練負荷監控]]
- [[Cortisol-Testosterone Ratio]]
```

L3 active 的 hard min: body 字數 ≥ 200（不含 frontmatter）。低於閾值 = ingest fail。

L2 stub 的 schema 略簡:
```yaml
---
title: Cori-cycle
aliases: [乳酸循環, 寇里循環]
en_source_terms:
  - "Cori cycle"
  - "lactic acid cycle"
type: concept
status: stub
maturity_level: L2
high_value_signals:                     # 為什麼 L2 不只是 L1 alias
  - "section_heading"                   # 該 term 是 section heading
  - "bolded_define"                     # 教科書 bold 定義
mentioned_in:
  - "[[Sources/Books/biochemistry-sport-exercise-2024/ch5]]"
created: 2026-05-08
created_by: phase-2-concept-dispatcher
---

# Cori-cycle

[Phase 2 抽出的初步 body — 至少 200 字]

---

> **Stub status**: 此 concept 目前僅在單一 source 出現，已含初步定義+機制。
> 升級條件：
> - 自動：下次 ingest 見第二 source 提及此 concept → 自動升 L3 active
> - 手動：修修 review 後手動 set `status: active` + `maturity_level: L3`
```

---

## Multi-agent panel sign-off

本 ADR 已過三家 panel:

| 階段 | Auditor | 結論 | 採納 |
|---|---|---|---|
| Draft | Claude (Opus 4.7) | v1 草稿 | base |
| Audit 1 | Codex (GPT-5) | 6-section, push-back on pure D, propose coverage manifest + 4-action revival | 4-action 重啟 / coverage manifest gate / pipeline bypass diagnosis |
| Audit 2 | Gemini 2.5 Pro | 6-section, "Approve with modifications", 5 必修 | RAG architecture spec / bilingual term mapping / Concept Maturity Model / Vision 6-class / LLM-classified coverage |
| Final | 修修 | sign-off 2026-05-06 | Status: Accepted |

panel pattern 之後用 skill-creator 凍結成 `multi-agent-panel` skill（見 [project_multi_agent_panel_skill_todo.md](../../memory/claude/project_multi_agent_panel_skill_todo.md)）。

---

## References

- [project_kb_corpus_stub_crisis_2026_05_06.md](../../memory/claude/project_kb_corpus_stub_crisis_2026_05_06.md) — 5/6 戰略發現 + root cause trace
- [feedback_quality_over_speed_cost.md](../../memory/claude/feedback_quality_over_speed_cost.md) — 修修最高指導原則
- [feedback_kb_concept_aggregator_principle.md](../../memory/claude/feedback_kb_concept_aggregator_principle.md) — Karpathy aggregator 哲學
- [feedback_adr_principle_conflict_check.md](../../memory/claude/feedback_adr_principle_conflict_check.md) — 寫新 ADR 必 cross-check 既有 P-level invariants
- [feedback_subagent_prompt_must_inline_principles.md](../../memory/claude/feedback_subagent_prompt_must_inline_principles.md) — subagent dispatch 必 inline 最高指導原則
- ADR-010 (superseded for textbook path) — Karpathy v1 design
- ADR-011 (superseded for textbook path) — v2 with 4-action dispatcher (本 ADR 重啟)
- ADR-016 (superseded) — Phase A/B parallel architecture（contract violation 源頭）
- [docs/research/2026-05-06-codex-adr-020-audit.md](../research/2026-05-06-codex-adr-020-audit.md) — Codex (GPT-5) audit verbatim
- [docs/research/2026-05-06-gemini-adr-020-audit.md](../research/2026-05-06-gemini-adr-020-audit.md) — Gemini 2.5 Pro audit verbatim
- [docs/research/2026-05-06-gemini-adr-020-audit-dispatch.py](../research/2026-05-06-gemini-adr-020-audit-dispatch.py) — Gemini dispatch script (multi-agent panel pattern reference)
- TIPO Article 65 fair-use framework — https://www.tipo.gov.tw/tw/copyright/694-17672.html
- 台灣著作權法 §51 — 個人非營利合理重製
- BGE-M3 paper — https://arxiv.org/abs/2402.03216 (multilingual + multi-granularity embedding)
- bge-reranker-large — https://huggingface.co/BAAI/bge-reranker-large
