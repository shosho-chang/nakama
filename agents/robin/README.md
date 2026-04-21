# Robin — 考古學家（Knowledge Base Agent）

自動將放入 Inbox 的文件攝入知識庫，產出結構化的 Wiki 頁面並同步至 Obsidian vault。

**排程：**
- `02:00 UTC` — Inbox KB ingest
- `21:30 UTC` (台北 05:30) — PubMed 每日研究 digest

**狀態：** ✅ 完成

---

## 功能

1. 掃描 `Inbox/kb/` 中的新檔案
2. 依副檔名分類並搬移至 `KB/Raw/`
3. 呼叫 Claude API 產出 Source Summary
4. 識別文件中的概念（Concept）與實體（Entity），建立或更新對應 Wiki 頁面
5. 更新 `KB/index.md` 與 `KB/log.md`
6. 在 SQLite 標記已處理，移除 Inbox 原檔

## 支援的檔案格式

| 副檔名 | 類型 | 存放位置 |
|--------|------|---------|
| `.md` | article | `KB/Raw/Articles/` |
| `.txt` | article | `KB/Raw/Articles/` |
| `.html` | article | `KB/Raw/Articles/` |
| `.pdf` | paper | `KB/Raw/Papers/` |
| `.epub` | book | `KB/Raw/Books/` |

## Vault 輸出

```
KB/
  Raw/
    Articles/   ← 原始文章檔案
    Papers/     ← 原始論文 PDF
    Books/      ← 原始書籍
  Wiki/
    Sources/    ← 每份來源的摘要頁
    Concepts/   ← 概念頁（如：間歇性斷食、端粒）
    Entities/   ← 實體頁（人物、工具、書籍、機構）
  index.md      ← 知識庫索引
  log.md        ← Append-only 操作紀錄
```

## 使用方式

把檔案放入 Obsidian vault 的 `Inbox/kb/`，Robin 會在排程時間自動處理。

手動執行：

```bash
# 預設：Inbox KB ingest
python -m agents.robin

# 互動模式（每份檔案 ingest 後暫停）
python -m agents.robin --interactive

# PubMed 每日 digest
python -m agents.robin --mode pubmed_digest

# PubMed digest dry-run（跑完 fetch + curate + score 但不寫 vault）
python -m agents.robin --mode pubmed_digest --dry-run
```

## Prompts

| 檔案 | 用途 |
|------|------|
| `prompts/summarize.md` | 產出 Source Summary |
| `prompts/extract_concepts.md` | 識別需建立/更新的 Concept & Entity |
| `prompts/write_concept.md` | 撰寫 Concept 頁內容 |
| `prompts/write_entity.md` | 撰寫 Entity 頁內容 |
| `prompts/pubmed_digest/curate.md` | 從 N 篇候選挑精選 + 分類 domain |
| `prompts/pubmed_digest/score.md` | 單篇六維度評分（NEJM 編輯 persona） |

---

## PubMed 每日 Digest（`--mode pubmed_digest`）

每天早上從 PubMed RSS 抓取新發表論文，LLM 做 curation + 評分，寫入：

```
KB/Wiki/Digests/PubMed/YYYY-MM-DD.md   ← 每日精選 digest
KB/Wiki/Sources/pubmed-{pmid}.md        ← 每篇獨立頁（精選才建）
```

### 評分面向（每項 1-5）

| 維度 | 說明 |
|------|------|
| Rigor | 研究設計嚴謹度（Meta > RCT > cohort > ...） |
| Impact | 期刊 tier + 新穎性 + 研究問題重要性 |
| Clinical Relevance | 人體 vs 動物、hard endpoint vs surrogate、effect size |
| Actionability | 結論能否轉成生活方式建議 |
| Red Flags | 反向分：5 = 無警訊，1 = 嚴重問題（COI、動物→人體過度外推等） |
| Novelty | 推進理解 vs 重複驗證 |

### 期刊 tier 資料

用 Scimago Journal Rank（SJR + Q1-Q4 quartile）當 LLM curation 訊號。
資料年度手動更新：

```bash
# 1. 到 https://www.scimagojr.com/journalrank.php 右上角 Download data
# 2. 把檔案另存為 data/_scimago_raw.csv（gitignored）
# 3. 跑 ETL 產出瘦身 CSV（~3 MB，committed）：
python -m scripts.update_scimago

# 4. git add data/scimago_journals.csv && git commit
```

### Feed 設定

`config/pubmed_feeds.yaml` 放 PubMed saved search 的 RSS URL。可放多個 feed
分領域，也可用一個廣查 query 的 feed。去重用 `state.db` 的 `scout_seen` table
（以 PMID 為 key）。

### 成本

Sonnet 4.6，120 筆候選 + 10-15 精選 + 全篇評分 ≈ **$0.35/日** ≈ **$10-11/月**。

