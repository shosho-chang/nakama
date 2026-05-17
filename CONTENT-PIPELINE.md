# Content Pipeline — 七層架構

Nakama 的內容創作工作流分七個 stage。新功能 / 規劃 / 優先序討論 **必先 anchor 在某個 stage**，避免 spontaneous 開發導致系統散亂無法串接。

跟其他兩大架構文件的關係：
- [ARCHITECTURE.md](ARCHITECTURE.md) — 元件 lens（shared modules / agents / infra）
- [CONTEXT-MAP.md](CONTEXT-MAP.md) — bounded context lens（agent 職責邊界）
- 本文件 — **內容工作流 lens**（從原料到發布的七個 stage）

---

## 七階段定義

| # | Stage | 中文 | 輸入 | 輸出 |
|---|---|---|---|---|
| 1 | Discovery | 資料收集 | 外部世界（趨勢、文獻、新聞、社群） | 原料素材（PDF / URL / 音檔 / 書 / 主題清單） |
| 2 | Reading + Annotation | 資料閱讀與註記 | 原料素材 | 劃線 + 筆記 + reading session |
| 3 | Synthesis / Ingest | 資料整合 | 原料 + annotation | KB Wiki 頁面（Source / Concept / Entity）+ Promotion Manifest |
| 4 | Atomic Content | 資料輸出（原子文章） | KB / annotation / Reading Context Package / 訪談音檔 | **Single source of truth canonical content**（手寫心得 / LLM 輔助稿 / transcribe 字幕檔） |
| 5 | Multi-channel Production | 內容製作 | 原子文章 | 4 channel：影片 / 部落格 / FB post / IG carousel |
| 6 | Publishing | 內容發布 | Multi-channel 內容 | 上線到 WP / YT / IG / FB / Newsletter / Community |
| 7 | Monitoring + Optimization | 內容監控與優化 | 已發布內容 + 平台 analytics | SEO 分數 / 排名 / 觀眾回饋 → 回灌 Stage 1 |

**閉環**：Stage 7 應 feed 回 Stage 1（內容效能 inform 下次主題選擇）。目前這條迴路斷裂 — 見下方「結構性觀察 #1」。

### Stage 4「原子文章」三種形式

Stage 4 的本質是產出一份 **canonical content 作為 Stage 5 fan-out 的 single source of truth**。三條 line 各有不同 atomic content shape：

| Line | Atomic content 形式 | 產生方式 |
|---|---|---|
| Line 1 Podcast | 字幕檔（.srt） | Transcribe pipeline 自動產出（WhisperX + Auphonic + LLM 校正 + 多模態仲裁） |
| Line 2 讀書心得 | **修修在 Project 頁面手寫的心得文** | 修修本人手寫（可用 Reading Context Package / Writing Assist Surface 降低空白頁摩擦，但不由 LLM 代寫） |
| Line 3 文獻科普 | LLM 輔助 outline + 修修加工的稿 | Robin 抽 KB → outline → Brook compose → 修修審稿 |

**關鍵設計原則**：Line 2 心得是修修自己的聲音，不可被 LLM 取代。系統角色限於「整合素材到 KB（Stage 3）」+「提供寫作前素材 scaffold（Stage 4 assist）」+「Stage 5 之後的 channel 製作」。Stage 4 assist 可以呈現 annotation、source map、evidence board、questions、outline skeleton、KB links，但**不得產生完成句、段落或第一人稱正文**。

### Stage 2→3→4 Source Promotion handoff

ADR-024 將 Reader / Ebook / Inbox document / Web document / Textbook ingest 統一到以下語言：

| 層 | Canonical term | 做什麼 | 不做什麼 |
|---|---|---|---|
| Stage 2 | **Reading Source** | ebook / web document / inbox document 進入 Reader，被閱讀與註記 | 不代表已經是正式 KB knowledge |
| Stage 2 | **Reading Overlay** | 保存 `KB/Annotations`、`digest.md`、`notes.md`、highlights、annotations、reflections、reading sessions | 不直接成為 factual claims |
| Stage 3 | **Source Promotion** | 依 source quality 將高價值來源提升為 knowledge-grade source map，抽 Source-local Concept / Global KB Concept / Entity | 不因「讀完」自動 promotion，不把全文鏡像進 Wiki |
| Stage 3 | **Promotion Review / Manifest** | LLM 產生 include / exclude / defer 建議、理由、evidence、risk、action、confidence；通過 review 後 item-level commit | 不無審核大批量改正式 KB |
| Stage 4 | **Reading Context Package** | Robin 將 annotation、notes、digest、source map、Concept links、idea clusters、questions、evidence board、outline skeletons 整理成寫作前材料包 | 不是 draft，不是 Brook compose 的正文 |
| Stage 4 | **Writing Assist Surface** | Brook-owned 或 shared UI 呈現素材包、協助插入 links / references / prompts | 不 ghostwrite Line 2 atomic content |

**Source Promotion trigger**：由 source quality 觸發，不由 reading completion 觸發。讀完是自然提示時機，但不是必要條件。

### Stage 5「多 channel 製作」path

Stage 5 從原子文章 fan out 成 4 個 channel，每個 channel 有自己的製作工具：

| Channel | 製作工具 | 適用 line |
|---|---|---|
| 影片 | **Script-Driven Video pipeline**（`agents/brook/script_video/` + `video/` Hyperframes） | Line 2 / Line 3 / Line 1 訪問新書作者 |
| 部落格 | Brook compose + Blog renderer | Line 1/2/3 |
| FB post | Brook FB renderer（4 tonal variants） | Line 1/2/3 |
| IG carousel | Brook IG renderer（5/7/5/10 卡 episode_type routing） | Line 1/2/3 |

**Script-Driven Video 不是獨立 line**，是 Stage 5 影片 channel 的製作管線。三條 line 都可走它出影片（CONTEXT-MAP.md「Line N vs script-driven video」段已凍結為 sibling）。

---

## Lines × Stages 矩陣（內容生產線 readiness）

三條 line 真正分歧在 Stage 1-4，**Stage 5 之後合流走 4 channel** + Stage 6 / 7 共用。

### 主矩陣（Stage 1-4 + 6-7）

| Line | 1 收集 | 2 閱讀 | 3 整合 | 4 原子文章 | 6 發布 | 7 監控 |
|---|---|---|---|---|---|---|
| **Line 1a Podcast 一般訪談** | n/a (有錄音) | n/a | n/a | ✅ transcribe → 字幕檔（atomic） | 🚧 Slice 10 reviewer + ❌ IG/YT/FB 自動發 | ❌ YT/IG insights |
| **Line 1b Podcast 訪問新書作者** | n/a (有錄音 + 有書) | ⬜ Line 2 Reading Source + Reading Overlay | ⬜ Line 2 Source Promotion / annotation-only sync | ✅ transcribe → 字幕檔（atomic） | 同上 | 同上 |
| **Line 2 讀書心得** | n/a (有書 / 文章 / web document) | ⬜ Reading Source + Reading Overlay（中/英/雙語閱讀） | ⬜ Source Promotion 或 annotation-only sync | ⬜ Reading Context Package → **修修 Project 頁面手寫** | ✅ WP only / ❌ 其他 channel | ❌ |
| **Line 3 文獻科普** | ⬜ Zoro topic discovery | n/a | ⬜ 主題 retrieval → outline | ⬜ synthesize outline → 修修自寫（可用 Claude.ai 對話協助），LLM 不代寫正文（ADR-027） | ✅ WP only / ❌ 其他 channel | ✅ SEO 中控台 |

圖例：✅ ship / 🚧 in flight / ⬜ 缺口 / ❌ 0 實作 / n/a 不適用

### Stage 5 sub-matrix（每 line × 4 channel）

每條 line 從 Stage 4 原子文章 fan out 成 4 channel，readiness 不一：

| Line | 影片（script-driven video） | 部落格（Brook compose） | FB post（Brook FB renderer） | IG carousel（Brook IG renderer） |
|---|---|---|---|---|
| **Line 1a 一般訪談** | n/a 或 theme video（待規劃） | ✅ ship | ✅ ship | ✅ ship |
| **Line 1b 訪問新書作者** | 🚧 Slice 2-5 | ✅ ship | ✅ ship | ✅ ship |
| **Line 2 讀書心得** | 🚧 Slice 2-5 | ✅ Brook compose | ✅ FB renderer | ✅ IG renderer |
| **Line 3 文獻科普** | 🚧 Slice 2-5（optional） | ✅ Brook compose | ✅ FB renderer | ✅ IG renderer |

**讀法**：Stage 5 製作工具基本到位（影片在 Slice 2-5 收尾），真正缺的不在 Stage 5 而在 Stage 4（原子文章不存在 → 無法 fan out）跟 Stage 6（4 channel render 完但只 WP 能自動發）。

### Line 1 兩子模式說明

**Line 1a 一般訪談**：
- Stage 1-3 跳過（直接從錄音進 Stage 4）
- Stage 4 = transcribe 字幕檔當 atomic content
- Stage 5 走部落格 / FB / IG 三 channel；影片 channel 視需要做 theme video（Line 1 補位專案）

**Line 1b 訪問新書作者**（Line 1 + Line 2 混合）：
- 訪談前修修先讀那本書 → 走 Line 2 Stage 2-3 流程（閱讀 + annotation + ingest）
- 訪談錄音 → Stage 4 transcribe
- Stage 5 可選走影片 channel（script-driven video 介紹該書）

### Line 2 Reading Source 子模式

Line 2 不再只視為「書」；ebook、inbox document、web document 都是 Reading Source。差異在 import / evidence track，不在 Stage 2→4 的核心語義。

| Source 類型 | Stage 1/2 匯入 | Stage 2 閱讀 | Stage 3 整合 |
|---|---|---|---|
| 中文 ebook | 匯入 EPUB/PDF 後轉 markdown / Reader 可讀格式 | Robin Reader 直接讀 + annotation | 依 source quality 選擇 Source Promotion 或 annotation-only sync |
| 英文 ebook | 匯入原文；可產生 bilingual display track | Robin Reader 雙語閱讀 + annotation | 原文 track 作 factual evidence；雙語 display 不當 factual evidence |
| Inbox document | 由 Obsidian / vault dropbox 進入 Inbox/kb | Robin Reader 直接讀 + annotation | 同 ebook，短文可 single Source page，長文可 section/chapter source map |
| Web document | Toast + Clipper/Defuddle-style main-content extraction 匯入 Inbox/kb | Robin Reader 直接讀或雙語讀 + annotation | 同 Inbox document；必須排除側欄、廣告、導覽等頁面雜訊 |
| Textbook-grade source | 匯入後轉 Raw markdown | 可不經個人 annotation | 直接走 Source Promotion / textbook-grade ingest |

雙語閱讀已在 PR #71 實作，但 P2A BabelDOC（學術 PDF）+ P2B Docling（掃描書）還是缺口。ADR-024 補上 Annotation / Reader source / KB integration 的 owner 語言：Robin/shared owns Source Promotion domain logic，Thousand Sunny owns review UI，Brook 只在 Stage 4/5 邊界內使用 Reading Context Package。

---

## Agents × Stages 矩陣（職責分配 readiness）

| Agent | 1 收集 | 2 閱讀 | 3 整合 | 4 輸出 | 5 製作 | 6 發布 | 7 監控 |
|---|---|---|---|---|---|---|---|
| **Robin** (KB) | ✅ PubMed digest cron + OA fulltext | ✅ Reader 雙語 (UI by Thousand Sunny) | ✅ ingest + kb_writer + textbook v2 + kb_search + RCP（ADR-024，已實作） | n/a | n/a | n/a | n/a |
| **Nami** (Secretary) | ✅ pubmed_lookup tool (Robin pass-through) + Gmail / Calendar / Vault notes | n/a | n/a | n/a | n/a | n/a | ⬜ daily briefing 接 SEO/cost data |
| **Zoro** (Scout) | ✅ keyword research + autocomplete + trends + reddit + youtube + twitter | n/a | n/a | n/a | n/a | n/a | ⬜ topic discovery 接 SEO 反向 feed |
| **Sanji** (Community) | ⬜ community FAQ discovery (從會員問題抽主題) | n/a | ⬜ member memory ingest | n/a | n/a | ❌ Fluent Community publisher | ⬜ engagement insight |
| **Brook** (Scaffold + Repurpose + SEO Audit, ADR-027) | n/a | n/a | ✅ synthesize（outline + evidence pool, ADR-021）；RCP 由 Robin own | scaffold only — outline / evidence 給修修自寫，**LLM 不代寫正文**（ADR-027 reminders not enforcement） | ✅ FB/IG/Blog renderer + repurpose engine + **script_video pipeline**；🚧 Line 1b 訪談+research_pack 2b mode（ADR-027） | n/a | ✅ SEO audit + enrich (對既有文章) |
| **Franky** (Maintenance) | ✅ AI news digest cron | n/a | n/a | n/a | n/a | n/a | ✅ probe panel + R2 backup verify + GSC daily + cost tracking |
| **Usopp** (Publisher) | n/a | n/a | n/a | n/a | n/a | ✅ WP publisher + approval queue HITL；❌ YT/IG/FB/Newsletter | n/a |

**關鍵讀法**：
- **Brook 跨 Stage 4/5/7 三層** — over-loaded，子模組已 13+，下一步要按 sub-context 切目錄（見觀察 #2）
- **Stage 6 只有 Usopp，Usopp 只有 WordPress** — 多 channel publishing 是最大缺口（見觀察 #4）
- **Sanji 全 row 空白** — 規劃 community engagement 必須補的 agent
- **Stage 2/3 owner 模糊** — Reader UI 在 Thousand Sunny、ingest 在 Robin、annotation 沒人讀（見觀察 #3）
- **Stage 4 對 Line 2 沒 agent cell** — 修修手寫心得是刻意設計，agent 不介入；只在 Stage 3（整合素材到 KB）+ Stage 5（拿手寫稿做 channel）服務

---

## 4 個結構性觀察（2026-05-04 grill 結論）

### 觀察 1：Discovery → Production → Insight 是斷的迴路

```
Zoro discovery → (人肉橋接) → Robin/Brook 寫 → (人肉發布) → SEO 中控台 audit → (回不去 Zoro)
```

**證據**：
- Zoro keyword research 結果寫進 LifeOS Project frontmatter，但 Brook compose 不自動 consume
- SEO 中控台抓出 striking-distance 關鍵字，**沒回灌 Zoro 變下次主題建議**
- IG / FB / YT 0 analytics 回收 → 不知道哪類主題真的有人看

**不做的代價**：每次選題靠修修直覺，內容效能無法 inform discovery，每集從零開始。

**建議起點**：先補 SEO 中控台 → Zoro topic discovery 反向 feed（一條 SQL view 就能起步），再補 IG/YT analytics。不需新 agent，把現有資料連起來。

### 觀察 2：Brook 已 over-loaded

ADR-001 把 Brook 定為 Composer，但實際職責橫跨 5 個子領域、`agents/brook/` 13+ Python 檔，是第二大 agent 的兩倍。

| 子職責 | 模組 | 屬於 Stage |
|---|---|---|
| 文章 compose | `compose.py` | 4 |
| SEO audit + enrich | `audit_runner.py` + `seo_block.py` + `seo_narrow.py` | 7 |
| Repurpose engine | `repurpose_engine.py` + 3 renderer | 5 |
| Script-driven video | `script_video/` 7 module | 4+5 |
| Style profile / compliance | `style_profile_loader.py` | 4 |

**不做的代價**：Brook prompt context 載入逼近 token 上限；新對話 onboarding 困難；測試 surface 越來越大。

**建議**：不急著拆 agent，但**該按 sub-context 切目錄** — `brook/compose/` + `brook/seo/` + `brook/repurpose/` + `brook/script_video/`（已切）。物理隔離先做，cognitive load 真的爆才拆 agent。

### 觀察 3：Annotation → 後續使用沒 owner

Reader UI 已支援 `==highlight==` + `> [!annotation]` markup，但這些標註寫到哪、誰讀、什麼時候用 — 完全沒設計。

**證據**：
- `agents/robin/` 沒 annotation reader
- Reading session 統整 → 心得 outline（Line 2 必須）— 0 實作
- KB/Raw 不能改寫（vault rule）、Wiki 是 Robin 寫的 → annotation 變孤兒

**不做的代價**：**Line 2 完全無法 ship**（讀書心得的 input 是 annotation）。Line 3 也受影響。

**建議**：把 annotation 當 first-class 資料 — 開 `KB/Annotations/` 目錄、Robin ingest 增「reading session 收尾」step、annotation 跟 source page 雙向 link。Line 2 critical path。

### 觀察 4：發布只有 WP，但內容主力 channel 不止 WP

| Channel | 發布實作 | Line 用到 |
|---|---|---|
| WordPress blog | ✅ Usopp full HITL | Line 1/2/3 |
| YouTube（影片） | ❌ 0 | **Script-Driven Video 主出口** |
| YouTube（podcast theme video） | ❌ 0 | Line 1 補位 |
| IG carousel | ❌ 0（render 文字 / 缺貼圖 pipeline） | Line 1 主出口之一 |
| FB post | ❌ 0 | Line 1 主出口之一 |
| Newsletter（Fluent CRM） | ❌ 0 | ADR-001 預留 |
| Community（Fluent Community） | ❌ 0（Sanji 空殼） | 讀者互動主場 |

**證據**：最緊急的 Line 1 跟 Script-Driven Video，**最終 channel 都不是 WordPress**。Line 1 IG carousel 已 render 文字、Script-Driven Video 已 emit FCPXML，但「真上 IG/YT」的 last mile 全要修修手動。

**不做的代價**：Line 1 / Script-Driven Video 的「ship」是「半成品交給修修手貼」。摩擦穩定累積。

**建議**：發布層**不要再擴 Usopp** — Usopp 寫 WP 那套已複雜。改 ADR-001 line 38 預留 + 新開 `agents/usopp/` sub-publisher（`youtube_publisher.py` / `ig_publisher.py`），共用 approval_queue HITL 但 platform-specific adapter。**先做 IG（最痛）+ YT description/title metadata**。

---

## 結構性優先序建議

如果只能挑 3 件最**結構性**的事（不是 feature，是 unblock 架構迴路）：

1. **IG 半自動發布管線**（Stage 6）— Line 1/2/3 IG carousel render 完都卡在這。不用整片 IG API upload，先解 carousel 圖檔 batch 出檔 + Buffer/Later 排程匯入。最低工自動化、最高摩擦消除
2. **Annotation → KB 整合 owner**（Stage 2→3）— Line 2 critical path。先解 annotation 寫到哪、誰讀、何時用；具體實作等修修這週手跑流程後決定（修修明確要求 Stage 4 手寫過程不介入，但 Stage 3 整合素材可動）
3. **SEO 中控台 → Zoro 反向 feed**（Stage 7→1）— 閉環 Discovery 迴路。一條 SQL view + Zoro report renderer 加 section，一週可做完

這三件**做完之後**再去推 Line 3 Stage 1 (Zoro topic discovery) / Stage 4 LLM 輔助 skill / Script-Driven Video Slice 2-5。理由：這三件不解，後面做的東西都會撞同樣的牆（4 channel 都 render 完發不出去 / Line 2 input 沒整合 / 選題無資料支撐）。

**Line 2 流程未跑過注記**：修修明確要求這週手跑 Line 2 流程一次再決定加什麼功能，所以上面第 2 條「Annotation → KB 整合」具體實作要等手跑後痛點浮現再凍結。我不該在他跑流程前就 over-design Stage 2-3 細節。

---

## 規劃原則

任何「我們來開發 X」對話必先回答三個 anchor 問題：

1. **這個 feature 屬於哪個 stage？**（1-7 之一，無法 anchor → flag）
2. **哪條 line / 哪個 agent 受益？**（對照 Lines × Stages + Agents × Stages 矩陣）
3. **跟現有 stage gap 的順序合不合？**（vs 上方 4 個結構性觀察 + 優先序）

無法 anchor 在七層內 → 屬於 infrastructure / tech debt 另一條 lens，需明確標示。
