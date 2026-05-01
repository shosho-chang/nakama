# Context Map

Nakama 是給單一內容創作者的 multi-agent AI 系統。每個 agent 是一個 **bounded context**，
擁有自己的領域詞彙與職責邊界；所有 agent 共用一個 shared kernel（基礎設施）與一個
presentation layer（Web UI）。

## Contexts

### Agent contexts
每個 agent 對應 `agents/<name>/` + `prompts/<name>/`，職責由 ADR-001 凍結。

- **Robin** — Knowledge Base：吸收 source（article / paper / book / podcast）→ 抽 concept / entity → 寫 wiki page
- **Nami** — Secretary：行事曆、Email triage、task 排程、daily briefing
- **Zoro** — Scout（**向外搜尋**）：keyword research、SERP / Trends / Reddit / YouTube 偵察 — 從外部世界拉情報回來
- **Sanji** — Community Manager：Fluent Community 社群營運、會員問答
- **Brook** — Composer（**對內加工**）：將素材 compose 成各平台格式（Blog / IG / YouTube / Newsletter）+ 既有部落格 SEO audit / enrich — 處理「已知/已存在」的內容
- **Franky** — System Maintenance：套件更新、CVE 掃描、health check、news digest
- **Usopp** — Publisher：發布到 WordPress / YouTube / Fluent CRM；core community publisher 待開

### Cross-cutting contexts
- **Shared kernel** (`shared/`、`agents/base.py`) — Agent / Run / Memory / Event / API call / Token cost；任何 agent 必經介面
- **Thousand Sunny** (`thousand_sunny/`) — Web presentation：所有 web UI、Bridge dashboard、各 agent router；HMAC cookie + API key auth；參見 [reference_bridge_ui_mutation_pattern](memory/claude/reference_bridge_ui_mutation_pattern.md)
- **video** (`video/`) — Script-Driven Video Production 的 Node.js + Remotion + TypeScript subproject；6 Remotion scene components + DSL parser + B-roll segment renderer；process boundary 跟 Python 主 repo 切開；Brook orchestrator (`agents/brook/script_video/`) 透過 Node.js CLI 呼叫；參見 ADR-015

## Relationships

- 每個 Agent context 透過 `shared/anthropic_client.ask_claude()` 呼叫 LLM；token cost 記入 `state.db`
- 每個 Agent context 透過 `shared/events.py` 互發事件（如 Robin → Nami briefing 注入）
- 每個 Agent context 暴露 web 表面透過 `thousand_sunny/routers/<agent>.py`
- **Brook ← Zoro** (`SEOContextV1`)：Zoro 跑 keyword-research + seo-keyword-enrich 產出 SEO context block，Brook compose 時 consume
- **Robin ← Brook** (KB lookup)：Brook compose 時可呼叫 KB search 拉素材
- **Usopp ← Brook + Sanji**：Brook 產長文輸出、Sanji 產社群素材，Usopp 排程發布；含 ADR-006 HITL approval gate
- **Brook script_video ↔ video subproject**：Brook orchestrator (`agents/brook/script_video/`) 透過 `Manifest` JSON 跟 `video/` subproject 雙向溝通；Python 端負責 ASR / mistake removal / PDF / embedding，Node.js 端負責 Remotion render B-roll segments；最終 Python 端 emit FCPXML 1.10 + 中文 SRT；DaVinci import 後修修微調 → YT
- **Brook script_video ← Robin metadata**（read-only）：textbook ingest 過的書，Brook 自動抓 title / author / book_id / pdf_path 補進 Manifest，省修修每集重填

## Per-context glossary

各 context 內部詞彙待 lazy creation —
開始 grill 該 context 時才寫 `agents/<name>/CONTEXT.md` 或 `thousand_sunny/CONTEXT.md`。

目前已凍結的 cross-context 名詞（避免歧義）：

- **「SEO solution」** = 三個用途集合：keyword research（**Zoro**，向外探索新主題）+ audit（**Brook**，對既有文章打分 + 改稿建議）+ enrich（**Brook**，為寫稿備 SEO context）。哲學分界「向外 = Zoro / 對內 = Brook」於 2026-04-29 grilling 凍結（落實 ADR-001 line 38 預留的 Brook 擴展選項）；參見 ADR-008 + ADR-009；不是新 agent
- **「approval queue」** = ADR-006 定義的 Usopp publish 前 HITL 站；不是 to-issues 的 `HITL` label
- **「surface」** = Thousand Sunny 的個別 web 頁面 / 路由（如 `/bridge/franky`、`/zoro/keyword-research`）；不是 GTM / 行銷 surface
- **「SEO 中控台」** = `/bridge/seo` surface 的別名，SEO solution 操作 hub。v1 三 section：(1) WP 文章列表 + lazy audit 分數、(2) 攻擊中目標關鍵字（讀 `config/target-keywords.yaml`）、(3) 排名變化（v1.1 等 ADR-008 Phase 2a-min 落地接 `gsc_rows` db）；2026-04-29 grilling 凍結
- **「audit review session」** = SEO 中控台底下「點進文章 → 跑新 audit → Y+ 左右對照」的單次審稿動作；以 `audit_results.suggestions_json` 落 db 持久化（resumable，無另開 session 表）；review 完成後一鍵 export 進 ADR-006 `approval_queue` 走既有 publish HITL — **不直接寫 WP**
- **「slice」** = vertical slice = 跨層（schema / API / UI / tests）的薄完整路徑；對應現有 Slice A/B/C 慣例
- **「chassis-nav」** = bridge surface 頂層 nav bar（落地後 `templates/bridge/_chassis_nav.html` partial 是 single source of truth）。Entry 兩種 lane：**agent-rooted**（ZORO、FRANKY 等對應 agent namespace，URL prefix `/bridge/<agent>/`）vs **topic-rooted**（SEO、DRAFTS、MEMORY、COST 等對應 cross-agent topic surface）。2026-04-29 grilling 凍結原則：「agent-rooted 頂層直到擠爆才 dropdown」。`aria-current` 嚴格對齊 URL，不表 user journey trail
- **「breadcrumb」** = page-header 之上一行的 user journey trail（如 `← /bridge/seo · 找新關鍵字 → ZORO · KEYWORD RESEARCH`），用來補位 chassis-nav 失去的「從哪來」資訊。Always 顯示（不 referrer-detect），用既有 `nk-caps` token
- **「script-driven video」/「腳本式影片」** = ADR-015 凍結的新 cross-cutting context。修修最高價值 content workflow：寫好中文逐字稿 → 照稿錄 A-roll → 程式自動 mistake removal + B-roll 視覺化 → 出 DaVinci FCPXML → 修修微調 ≤30 分鐘上 YT。**不是** Line 1/2/3（podcast/book/literature → 多 channel 文字）的延伸——input/output shape 不同，是 sibling 不是 extension
- **「Manifest」** = `script-driven video` workflow 的 single source of truth JSON schema（Python ↔ TypeScript shared）。modeling 場景 sequence、cut points、A-roll 時間戳、quote 視覺化指令；DSL parser 產出，pipeline / Remotion / FCPXML emitter 三方共用
- **「mistake removal」** = ADR-015 stage 1，A-roll 拍掌 marker primary + alignment fallback（修修錄錯時拍兩下手做 marker）。**不另出乾淨 mp4**——razor cut + ripple delete instructions 寫進 FCPXML，由 DaVinci 接管實際切點
- **「B-roll segment」** = ADR-015 凍結概念。Remotion **不 render 整支影片**，只 render 各 scene 為個別 mp4 clips（per-segment file），FCPXML 把它們塞進 V2-V4 軌道；A-roll 留 V1。架構反轉的核心
- **「Mode A / Mode B」** = ADR-015 引用視覺化雙模式。**Mode A** = `<DocumentQuote>` 真實 PDF 頁 + 螢光筆動畫（PyMuPDF bbox + 三變體 highlighter-sweep / ken-burns / spotlight）；**Mode B** = `<QuoteCard>` 風格化引文卡（紙質背景 + 思源宋體 + 頁碼引註）。markdown DSL `mode=` 切換
- **「per-episode 目錄」** = `data/script_video/<episode-id>/` 自包含結構：script.md + raw_recording.mp4 + refs/ (PDF) + out/ (FCPXML / SRT / b_roll mp4)。跨集共享：`_cache/embeddings/<sha256>.npy`（同 PDF 第二次用直接讀）

## ADR location

系統級決策位於 `docs/decisions/ADR-NNN-*.md`（**不是** mattpocock 預設的 `docs/adr/`）。
context 子目錄的 ADR 待第一個 context-specific decision 出現時 lazy 建立。

## Flagged ambiguities

開 grill 後發現的詞彙衝突在這裡累積。

- **「Line N」vs「script-driven video」** — Line 1/2/3 是 ADR-014 RepurposeEngine 的 source → 多 channel 文字 fan-out（Brook compose）。**script-driven video（ADR-015）不是 Line 4**——它是 single source + single output（mp4 via DaVinci），sequential pipeline 不 fan-out。grill 凍結為 sibling context，避免被誤稱「Line 4」
