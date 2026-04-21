---
name: Robin PubMed 每日 digest
description: Robin 第二個子流程上線 — 每日台北 05:30 自動產出 PubMed 研究精選到 Obsidian vault
type: project
---

# Robin PubMed 每日 Digest

**狀態**：✅ 已上線 VPS（2026-04-21 部署，首次 cron 觸發 2026-04-22 05:30 台北）

## 做什麼

每日台北 05:30 從 PubMed RSS 抓最新論文 → LLM curation 挑 10-15 篇 → NEJM 編輯 persona 六維度評分 → 寫進 Obsidian vault：
- `KB/Wiki/Digests/PubMed/YYYY-MM-DD.md` — 每日精選頁
- `KB/Wiki/Sources/pubmed-{pmid}.md` — 每篇獨立 source 頁

取代原本的 n8n RSS → BigQuery → Google Sheets 流程。把資產搬進 Obsidian 讓 KB search / Brook compose 之後能引用。

## 關鍵檔案

- `agents/robin/pubmed_digest.py` — pipeline 主體
- `shared/journal_metrics.py` — Scimago SJR lookup（30,542 期刊）
- `scripts/update_scimago.py` — 年度 ETL（`data/_scimago_raw.csv` 原始 → `data/scimago_journals.csv` 瘦身 committed）
- `prompts/robin/pubmed_digest/curate.md` + `score.md` — LLM prompts
- `config/pubmed_feeds.yaml` — feed URL 清單（目前單一廣領域 saved search）
- 入口：`python -m agents.robin --mode pubmed_digest [--dry-run]`
- Cron：`30 5 * * *` 於 VPS（Asia/Taipei TZ）

## 成本

Sonnet 4.6，120 候選 → 12 精選 ≈ **$0.35/日 ≈ $10/月**。已走 BaseAgent 成本追蹤，Bridge `/bridge/cost` 頁可看。

## 評分六維度

Rigor / Impact / Clinical Relevance / Actionability / Red Flags / Novelty（每項 1-5，5 最好；Red Flags 反向分 5=無警訊）。`editor_pick` 判準：overall ≥ 3.5 且 Rigor ≥ 3 且 Red Flags ≥ 3。Persona = NEJM 資深編輯 + 3 個 few-shot 防分數膨脹。

## 相關 PR

- #66 feature
- #67 filename timezone fix（Asia/Taipei 而非 UTC）
- #68 cron TZ fix（VPS 本機 Asia/Taipei 時區）

## 下個迭代

週報 / 社群電子報由 Brook 消化這些 vault digest 產出，依賴 Brook style extraction（見 `project_brook_style_extraction_todo.md`，2026-04-22 待辦）。
