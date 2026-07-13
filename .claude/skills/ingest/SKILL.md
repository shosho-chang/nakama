---
name: ingest
description: >
  Ingest a finished reading into Robin's KB wiki (Centaur KB, route C —
  article / paper). Renders the reader's annotation set into a Literature Note
  (KB/Literature/{slug}), generates a Source Summary from the FULL source text,
  then proposes Concept/Entity wiki pages — STOPPING for the user to
  accept / defer / exclude before anything is written. The annotation set is an
  EMPHASIS signal layered on the full text, never a content filter. Use when the
  user finishes an article/paper (or one book chapter) and says 「ingest 這篇」
  「把這篇讀完的入庫」「/ingest <slug>」「這篇 KB 化」「幫我把這篇收進知識庫」
  「《X》第N章讀完了，幫我 ingest」. Do NOT use for: 開永久卡或寫永久卡之間的連結
  （走每日回顧，AI 不代筆）; KB 查詢/找答案（用 kb-search）; 整本書或影片逐字稿
  （route B/E 走每日迴圈、未上線，先擋並告知）; 重寫/潤稿文章（不是 ingest）.
---

# ingest — 把讀完的文章編進 Robin 的 KB Wiki（route C）

你是 Nakama `IngestPipeline`（`agents/robin/ingest.py`）的**對話介面**。你的工作是
驅動既有 pipeline、守住 HITL gate、擷取品味訊號。**你不重寫 pipeline**——確定性邏輯
（Literature render、摘要、Concept 寫入、錨點、index、紅線 tripwire）都在 Python，
你透過 `scripts/ingest_steps.py` 呼叫它。

這是「Claude 對話」這扇門；同一個 pipeline 另有 Web UI 入口（Reader 的 Ingest 鈕）。
兩扇門進同一個 Python 引擎，行為一致。

## 何時用
觸發：修修讀完一篇文章/論文（或一本書的某一章）說「ingest 這篇 / 這篇 KB 化 /
收進知識庫 / /ingest <slug> /《X》第 N 章讀完了幫我 ingest」。

不要用於：
- 開永久卡或卡間連結 → 每日回顧，**AI 不代筆**（紅線 1）。**也包含「ingest 這篇順便連到《X》那張卡」這種一句話混兩件事**——ingest 可做，但**連結是修修的決定，流程裡絕不碰 Permanent 連結**；遇到就做 ingest、明確回絕連結那半。
- **只收某段 / 只要重點 / 過濾內容**（「只要講 X 那段」「讀重點整理進去」）→ **拒絕並說明 ingest 是全文**（違反 D-A）。劃線/重點是**強調訊號**，要標重點用 `--guidance` 傳，不是只收那段。
- KB 查詢 / 找答案 → `kb-search`。
- **整本書一次收**或**邊讀邊累積** → route B 走 Centaur 每日迴圈，**未上線，先擋並告知**。
- 影片逐字稿 → route E，未上線，先擋。
- 重寫 / 潤稿 → 不是 ingest（走 Brook）。

## Centaur KB 原則（出手前讀一次）
- 你維護的是 `KB/Wiki/`（Sources / Concepts / Entities）= **candidate / 草稿層**；
  修修手寫的 `KB/Permanent/` 是**權威層**。兩層獨立，你向修修看齊。
- **ingest 全文**；修修的劃線是「他在意這裡」的**強調訊號**，**不是內容過濾器**。
  只 ingest 劃線會讓你的 Wiki 變成他注意力的鏡子，就給不了「他漏看的洞見」。
- 你**絕不**寫 `KB/Permanent/` 正文 / status / 連結。對「這 Concept 對應哪張永久卡」
  只在 Concept 頁加 defer 建議。
- 紅線與坑出手前讀 `GOTCHAS.md`；route C 分支細節讀 `references/route-c-article.md`。

## 事實（別搞錯資料流）
全文 → `_generate_summary` 產**摘要**（≤30000 字 pass-through；超過自動 map-reduce）→
**Concept 抽取吃的是摘要，不是原始全文**。Phase 1 的 Literature Note 才直接從
annotation render。把劃線當強調訊號**自動注入** concept 抽取目前**未實作**（可選增強，
先不做）；現階段要強調某段，用 `--guidance` 傳。

## 流程（兩步，HITL 卡中間）

### 0. 確認 route
文章 / 論文 / 一章 → route C，繼續。整本書 / 影片 → 告知未上線、停。

### 1. Phase 出計畫（不寫 Concept/Entity）
從 repo root 跑：
```bash
python .claude/skills/ingest/scripts/ingest_steps.py plan \
    --raw "<原始文章檔路徑>" \
    --source-type article \
    [--annotation-slug <slug>] \
    [--content-nature popular_science] \
    [--guidance "修修要你特別注意的方向（可空）"] \
    --out /tmp/ingest-plan.json
```
這一步只寫 **Source Summary 頁**（draft / candidate），**不寫任何 Concept/Entity**。
輸出 `plan.json` 含 `summary_excerpt` + `plan.concepts[]` + `plan.entities[]`。

### 2. 🚦 HITL gate（在對話裡停下來）
把 `plan` 列給修修，逐項清楚標示：
- concept：`action`（🆕 create / 🔀 update_merge / ⚠️ update_conflict / 🟢 noop）+ 標題 + 理由；
  conflict 要顯示「既有 vs 新」主張。
- entity：類型 + 標題 + 理由。

請修修 **accept / defer / exclude**（例：「1、3 要，2 之後再說，4 不要」）。
**不可自動放行。** 出手前再讀一次 `GOTCHAS.md` 確認沒踩紅線。

### 3. 寫回過濾後的 plan → execute
把修修**要執行**的項目留下、defer/exclude 的移除，寫回 `plan.json`（保留 `summary_path`/
`title`/`slug`/`source_type`），然後：
```bash
python .claude/skills/ingest/scripts/ingest_steps.py execute --plan-file /tmp/ingest-plan.json
```
pipeline 寫 `KB/Wiki/Concepts` / `Entities`（過紅線 5 citation lint）+ 更新 `index.md` / `log.md`。

### 4. 擷取品味訊號（taste loop）
把修修的 accept / defer / exclude **+ 一句理由**記下來：
- 追加一行到 `style/taste-profile.md` 的「raw 觀察」區（先只記，不改規則）。
- promote 成正式 gotchas / examples / style **由修修點頭**，是之後的人工蒸餾步驟（先不自動）。

## 細節
route C 分支（錨點、plan schema、action 語意）見 `references/route-c-article.md`；
五條紅線與 route C 專屬坑見 `GOTCHAS.md`。
